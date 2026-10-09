"""Loss-aware arXiv collection: paging, complete abstracts, per-query cursors."""
import asyncio
import hashlib
from html.parser import HTMLParser
from datetime import datetime, timedelta, timezone
import re
import xml.etree.ElementTree as ET

import httpx

API = "https://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom", "o": "http://a9.com/-/spec/opensearch/1.1/"}


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_feed(text):
    root = ET.fromstring(text)
    if root.tag != "{http://www.w3.org/2005/Atom}feed":
        raise ValueError("Not an Atom feed")
    papers = []
    for entry in root.findall("a:entry", NS):
        raw_id = entry.findtext("a:id", "", NS)
        if "/abs/" not in raw_id:
            raise ValueError("arXiv returned an error or invalid entry")
        versioned = raw_id.split("/abs/", 1)[1]
        match = re.fullmatch(r"((?:\d{4}\.\d{4,5})|(?:[a-z.-]+/\d{7}))(v\d+)?", versioned)
        if not match:
            raise ValueError("Invalid arXiv identifier")
        published = entry.findtext("a:published", "", NS)
        updated = entry.findtext("a:updated", "", NS) or published
        timestamp(updated)
        papers.append({
            "id": match[1], "version": (match[2] or "v1"),
            "title": " ".join(entry.findtext("a:title", "", NS).split()),
            "abstract": " ".join(entry.findtext("a:summary", "", NS).split()),
            "authors": [a.findtext("a:name", "", NS) for a in entry.findall("a:author", NS)],
            "date": published[:10], "updated": updated,
            "url": f"https://arxiv.org/abs/{versioned}", "source": "arxiv",
            "categories": [c.attrib.get("term", "") for c in entry.findall("a:category", NS)]
        })
    total = root.findtext("o:totalResults", None, NS)
    if total is None:
        raise ValueError("Missing totalResults; cannot verify coverage")
    return papers, int(total)


async def request_feed(client, params, *, delay=3.5):
    retry_wait = 0
    for attempt in range(3):
        await asyncio.sleep(max(delay * (attempt + 1), retry_wait))
        try:
            response = await client.get(API, params=params)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    try:
                        retry_wait = max(30 * (attempt + 1), float(response.headers.get("Retry-After", "0")))
                    except ValueError:
                        retry_wait = 60
                    if retry_wait > 120:
                        response.raise_for_status()
                    continue
            response.raise_for_status()
            return parse_feed(response.text)
        except httpx.TransportError:
            if attempt == 2:
                raise
    raise RuntimeError("arXiv retries exhausted")


async def collect_query(client, query, since, *, page_size=100, max_pages=20, delay=3.5):
    found = {}
    offset = 0
    for _ in range(max_pages):
        papers, total = await request_feed(client, {
            "search_query": query, "start": offset, "max_results": page_size,
            "sortBy": "lastUpdatedDate", "sortOrder": "descending"
        }, delay=delay)
        if not papers and offset < total:
            raise ValueError("Empty page before reported result count")
        for p in papers:
            if timestamp(p["updated"]) >= since:
                found[p["id"]] = p
        offset += len(papers)
        if offset >= total or (papers and min(timestamp(p["updated"]) for p in papers) < since):
            return list(found.values()), True
    # Keep recovered papers, but never advance the success cursor past an incomplete query.
    return list(found.values()), False


async def collect_queries(client, queries, state, *, started=None, overlap_days=3,
                          initial_days=7, max_pages=20, delay=3.5):
    started = started or datetime.now(timezone.utc)
    cursors = dict(state.get("query_success", {}))
    merged, health = {}, {}
    for name, query in queries.items():
        previous = cursors.get(name)
        since = timestamp(previous) - timedelta(days=overlap_days) if previous else started - timedelta(days=initial_days)
        since = since.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            papers, complete = await collect_query(client, query, since, max_pages=max_pages, delay=delay)
            for p in papers:
                if p["id"] in merged:
                    merged[p["id"]]["hit_dimensions"].append(name)
                else:
                    p["hit_dimensions"] = [name]
                    merged[p["id"]] = p
            health[name] = {"status": "ok" if complete else "truncated", "count": len(papers), "since": since.isoformat()}
            if complete:
                cursors[name] = started.isoformat()
        except (httpx.HTTPError, ET.ParseError, ValueError, RuntimeError) as exc:
            # Do not include credentials, proxy URLs, or request headers in persisted errors.
            health[name] = {"status": "error", "error_type": type(exc).__name__, "since": since.isoformat()}
            if isinstance(exc, httpx.HTTPStatusError):
                health[name]["http_status"] = exc.response.status_code
        print(f"[Collect] {name}: {health[name]['status']} ({health[name].get('count', 0)})", flush=True)
    return list(merged.values()), {**state, "query_success": cursors, "last_attempt": started.isoformat()}, health


async def collect_backfill(client, queries, state, *, started, days=30, windows=1, max_pages=20, delay=3.5):
    """Resume bounded submission-date windows, independently of incremental cursors."""
    histories = dict(state.get("backfill", {}))
    found, health = [], {}
    end = started.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    for name, query in queries.items():
        signature = hashlib.sha256(query.encode()).hexdigest()
        record = dict(histories.get(name, {}))
        if record.get("query_hash") != signature or record.get("days") != days:
            record = {"query_hash": signature, "days": days, "start": (end - timedelta(days=days)).isoformat(),
                      "end": end.isoformat(), "next_end": end.isoformat()}
        lower, cursor = timestamp(record["start"]), timestamp(record["next_end"])
        h = {"status": "ok", "count": 0, "windows_completed": 0, "start": record["start"], "end": record["end"]}
        for _ in range(windows):
            if cursor <= lower:
                break
            start = max(lower, cursor - timedelta(days=7))
            bounded = f'({query}) AND submittedDate:[{start:%Y%m%d%H%M} TO {cursor - timedelta(minutes=1):%Y%m%d%H%M}]'
            try:
                papers, complete = await collect_query(client, bounded, start, max_pages=max_pages, delay=delay)
                found.extend(dict(p, hit_dimensions=[name]) for p in papers)
                h["count"] += len(papers)
                if not complete:
                    h["status"] = "truncated"
                    break
                cursor = start
                record["next_end"] = cursor.isoformat()
                h["windows_completed"] += 1
            except (httpx.HTTPError, ET.ParseError, ValueError, RuntimeError) as exc:
                h.update(status="error", error_type=type(exc).__name__)
                break
        record["complete"] = cursor <= lower
        histories[name] = record
        health[name] = {**h, "complete": record["complete"], "next_end": record["next_end"]}
        print(f"[Backfill] {name}: {h['status']} ({h['count']}); complete={record['complete']}", flush=True)
    return found, {**state, "backfill": histories}, health


class AnnouncementParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.text = set(), []

    def handle_starttag(self, tag, attrs):
        href = dict(attrs).get("href", "")
        if tag == "a" and re.fullmatch(r"/abs/\d{4}\.\d{4,5}", href):
            self.ids.add(href.removeprefix("/abs/"))

    def handle_data(self, data):
        self.text.append(data)


async def reconcile_announcements(client, existing, *, delay=3.5):
    """Reconcile the complete official recent cs.RO list, irrespective of submission age."""
    health = {"status": "error", "scope": "arxiv cs.RO recent announcement list"}
    found = []
    try:
        response = await client.get("https://arxiv.org/list/cs.RO/recent", params={"skip": 0, "show": 2000})
        response.raise_for_status()
        parser = AnnouncementParser()
        parser.feed(response.text)
        total = re.search(r"Total of\s+([\d,]+)\s+entries", " ".join(parser.text))
        if not total or len(parser.ids) != int(total[1].replace(",", "")):
            raise ValueError("Announcement list incomplete or unrecognized")
        # Re-fetch incomplete legacy metadata too; a bare ID is not a usable candidate.
        present = {p["id"] for p in existing if p.get("version") and p.get("updated") and p.get("abstract")}
        missing = sorted(parser.ids - present)
        health.update(reference_count=len(parser.ids), missing_before=len(missing))
        for offset in range(0, len(missing), 50):
            ids = missing[offset:offset + 50]
            papers, _ = await request_feed(client, {"id_list": ",".join(ids), "max_results": len(ids)}, delay=delay)
            found.extend(dict(p, hit_dimensions=["robotics_all"]) for p in papers if p["id"] in ids)
            if {p["id"] for p in papers} != set(ids):
                raise ValueError("Missing announcement metadata")
        health["status"] = "ok"
    except (httpx.HTTPError, ET.ParseError, ValueError, RuntimeError) as exc:
        health["error_type"] = type(exc).__name__
    health["recovered"] = len({p["id"] for p in found})
    if "missing_before" in health:
        health["remaining"] = health["missing_before"] - health["recovered"]
    return found, health


def merge_catalog(existing, incoming, discovered_at):
    result = {p["id"]: dict(p) for p in existing}
    for paper in incoming:
        old = result.get(paper["id"], {})
        dimensions = sorted(set(old.get("hit_dimensions") or []) | set(paper.get("hit_dimensions") or []))
        if old.get("updated") and paper.get("updated") and timestamp(old["updated"]) > timestamp(paper["updated"]):
            result[paper["id"]] = {**old, "hit_dimensions": dimensions}
            continue
        merged = dict(old)
        merged.update(paper)
        merged["date_found"] = old.get("date_found") or paper.get("date_found") or discovered_at[:10]
        merged["hit_dimensions"] = dimensions
        if not old or any(old.get(k) != paper.get(k) for k in ("version", "abstract", "title")):
            merged["review_status"] = "pending"
            merged["review_changed_at"] = discovered_at
        result[paper["id"]] = merged
    return list(result.values())
