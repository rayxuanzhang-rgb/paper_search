"""Prepare Codex reading packets, validate actual reviews, and render HTML.

No model API or ChatGPT credential is needed by this command. Codex performs
the reading and reasoning in its own session, between prepare and accept.
"""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from processing.codex_review import make_packet, read_json, store_review, write_json, now, validate_review, industry_watch
from output.review_html import render_review

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--days", type=int, default=7)
    prepare.add_argument("--ids", help="Comma-separated explicit IDs for historical review")
    prepare.add_argument("--batch-size", type=int, default=30)
    prepare.add_argument("--label", default="研究简报")
    prepare.add_argument("--profile", type=Path, default=ROOT / ".review/profile.json")
    prepare.add_argument("--output", type=Path, default=ROOT / ".review/packets")
    prepare.add_argument("--catalog", type=Path, default=ROOT / "data/catalog.json")
    prepare.add_argument("--store", type=Path, default=ROOT / ".review/reviews.json")
    prepare.add_argument("--include-reviewed", action="store_true")
    prepare.add_argument("--backlog", action="store_true", help="Include all unreviewed dates, including offline days")
    accept = sub.add_parser("accept")
    accept.add_argument("packet", type=Path)
    accept.add_argument("result", type=Path)
    accept.add_argument("--output", type=Path, required=True)
    accept.add_argument("--store", type=Path, default=ROOT / ".review/reviews.json")
    compose = sub.add_parser("compose", help="Merge validated batches using an explicit editorial selection")
    compose.add_argument("selection", type=Path, help="JSON: packet_ids, highlight_ids, label, reviewer, overview")
    compose.add_argument("--store", type=Path, default=ROOT / ".review/reviews.json")
    compose.add_argument("--output", type=Path, required=True)
    feedback = sub.add_parser("feedback")
    feedback.add_argument("id")
    feedback.add_argument("action", choices=["useful", "try", "not_useful", "already_known"])
    feedback.add_argument("--reason", default="")
    watch = sub.add_parser("watch", help="Export all high-industry-value papers awaiting a second look")
    watch.add_argument("--catalog", type=Path, default=ROOT / "data/catalog.json")
    watch.add_argument("--profile", type=Path, default=ROOT / ".review/profile.json")
    watch.add_argument("--store", type=Path, default=ROOT / ".review/reviews.json")
    watch.add_argument("--output", type=Path, default=ROOT / ".review/industry-watch.json")
    args = parser.parse_args()
    if args.command == "prepare":
        if args.days < 1 or not 1 <= args.batch_size <= 100:
            parser.error("days must be positive and batch-size must be 1–100")
        profile_path = args.profile
        if not profile_path.exists():
            profile_path = ROOT / "review_profile.example.json"
        profile = read_json(profile_path)
        catalog = args.catalog
        if not catalog.exists() and (ROOT / "database/papers.json").exists():
            catalog = ROOT / "database/papers.json"
        papers = read_json(catalog)["papers"] if catalog.exists() else []
        if args.ids:
            wanted = set(args.ids.split(","))
            papers = [p for p in papers if p["id"] in wanted]
            missing = wanted - {p["id"] for p in papers}
            if missing:
                parser.error(f"Requested IDs not in database: {', '.join(sorted(missing))}")
        elif not args.backlog:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).date().isoformat()
            papers = [p for p in papers if (p.get("review_changed_at") or p.get("date_found") or p.get("date") or "") >= cutoff]
        if not args.include_reviewed and not args.ids and args.store.exists():
            history = read_json(args.store)["runs"]
            probe = make_packet(papers, profile, label=args.label)
            reviewed = {r["fingerprint"] for run in history
                        if run["packet"]["profile_hash"] == probe["profile_hash"]
                        for r in run["result"]["reviews"] if r["decision"] != "needs_evidence"}
            pending_ids = {p["id"] for p in probe["candidates"] if p["fingerprint"] not in reviewed}
            papers = [p for p in papers if p["id"] in pending_ids]
        papers.sort(key=lambda p: (p.get("date_found") or "", p.get("date") or "", p["id"]), reverse=True)
        # No score threshold and no hidden top-N truncation. Every candidate is exported.
        packets = []
        for offset in range(0, len(papers), args.batch_size):
            packet = make_packet(papers[offset:offset + args.batch_size], profile, label=args.label)
            path = args.output / f"{packet['packet_id'][:16]}.json"
            write_json(path, packet)
            packets.append({"path": str(path), "packet_id": packet["packet_id"], "count": len(packet["candidates"])})
        write_json(args.output / "manifest.json", {"created_at": now(), "total": len(papers), "packets": packets})
        print(f"Prepared {len(papers)} candidates in {len(packets)} packets: {args.output}")
    elif args.command == "accept":
        packet, result = read_json(args.packet), read_json(args.result)
        store_review(packet, result, args.store)
        render_review(packet, result, args.output)
        print(f"Validated review and wrote HTML: {args.output}")
    elif args.command == "compose":
        selection = read_json(args.selection)
        wanted = selection["packet_ids"]
        runs = {run["packet"]["packet_id"]: run for run in read_json(args.store)["runs"]}
        selected = [runs[pid] for pid in wanted]
        if not selected or len({r["packet"]["profile_hash"] for r in selected}) != 1:
            raise ValueError("Select batches with the same research profile")
        candidates, reviews = {}, {}
        for run in selected:
            validate_review(run["packet"], run["result"])
            for paper in run["packet"]["candidates"]:
                if paper["id"] in candidates and candidates[paper["id"]]["fingerprint"] != paper["fingerprint"]:
                    raise ValueError("Conflicting revisions; select only the intended revision")
                candidates[paper["id"]] = paper
            reviews.update({r["id"]: r for r in run["result"]["reviews"]})
        packet = make_packet(list(candidates.values()), selected[0]["packet"]["profile"], label=selection["label"])
        result = {"packet_id": packet["packet_id"], "profile_hash": packet["profile_hash"],
                  "reviewer": selection["reviewer"], "highlight_ids": selection["highlight_ids"],
                  "reviews": list(reviews.values())}
        if "overview" in selection:
            result["overview"] = selection["overview"]
        store_review(packet, result, args.store)
        render_review(packet, result, args.output)
        write_json(args.output.with_suffix(".json"), {"packet": packet, "result": result})
        print(f"Composed {len(result['highlight_ids'])} highlights across {len(selected)} batches: {args.output}")
    elif args.command == "watch":
        profile = read_json(args.profile if args.profile.exists() else ROOT / "review_profile.example.json")
        store = read_json(args.store) if args.store.exists() else {"runs": []}
        queue = industry_watch(read_json(args.catalog)["papers"], profile, store)
        write_json(args.output, queue)
        print(f"Industry watch: {queue['total']} candidates; {args.output}")
    else:
        path = ROOT / ".review/feedback.json"
        data = read_json(path) if path.exists() else {"events": []}
        data["events"].append({"id": args.id, "action": args.action, "reason": args.reason, "at": now()})
        write_json(path, data)
        print(f"Saved feedback for {args.id}; no automatic topic exclusion was applied.")


if __name__ == "__main__":
    main()
