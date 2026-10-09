"""Collect public paper metadata without calling a model or creating scores."""
import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx

from processing.codex_review import read_json, write_json
from searchers.discovery import collect_queries, merge_catalog

ROOT = Path(__file__).resolve().parent


async def run(args):
    queries = read_json(ROOT / "discovery_queries.json")
    if args.only:
        selected = set(args.only.split(","))
        if selected - queries.keys():
            raise ValueError("Unknown query name")
        queries = {k: v for k, v in queries.items() if k in selected}
    state_path = args.data / "collection_state.json"
    catalog_path = args.data / "catalog.json"
    state = read_json(state_path) if state_path.exists() else {}
    existing = read_json(catalog_path)["papers"] if catalog_path.exists() else []
    started = datetime.now(timezone.utc)
    async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"User-Agent": "WAMResearchTracker/1.0"}) as client:
        papers, state, health = await collect_queries(client, queries, state, started=started,
                                                      initial_days=args.days, max_pages=args.max_pages)
    catalog = merge_catalog(existing, papers, started.isoformat())
    # Write data before cursors: a crash can cause a repeat fetch, but cannot skip unsaved results.
    write_json(catalog_path, {"schema_version": 1, "papers": catalog})
    write_json(state_path, state)
    ok = all(h["status"] == "ok" for h in health.values())
    write_json(args.data / "collection_health.json", {"at": started.isoformat(), "ok": ok, "sources": health})
    print(f"Collected {len(papers)} items; catalog={len(catalog)}; complete={ok}")
    return 0 if ok else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    parser.add_argument("--days", type=int, default=7, help="Initial window; existing query cursors take precedence")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--only")
    args = parser.parse_args()
    if args.days < 1 or args.max_pages < 1:
        parser.error("days and max-pages must be positive")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
