"""Merge the public GitHub catalog into this local workspace (no credentials)."""
import argparse
from pathlib import Path

import httpx

from processing.codex_review import read_json, write_json
from searchers.discovery import merge_catalog

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="rayxuanzhang-rgb/paper_search")
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    if len(args.repository.split("/")) != 2 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_/ ." for c in args.repository) or ".." in args.repository:
        parser.error("Expected owner/repository")
    base = f"https://raw.githubusercontent.com/{args.repository}/main/data"
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        response = client.get(f"{base}/catalog.json")
        response.raise_for_status()
        cloud = response.json()
        response = client.get(f"{base}/collection_health.json")
        response.raise_for_status()
        health = response.json()
    path = args.data / "catalog.json"
    existing = read_json(path)["papers"] if path.exists() else []
    # Never downgrade a locally discovered revision to an older cloud copy.
    local = {p["id"]: p for p in existing}
    incoming = [p for p in cloud["papers"] if p.get("updated", "") >= local.get(p["id"], {}).get("updated", "")]
    merged = merge_catalog(existing, incoming, health["at"])
    write_json(path, {"schema_version": 1, "papers": merged})
    write_json(args.data / "cloud_health.json", health)
    print(f"Synced {len(cloud['papers'])} public entries; local catalog={len(merged)}; cloud complete={health['ok']}")


if __name__ == "__main__":
    main()
