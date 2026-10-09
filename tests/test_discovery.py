import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx

from searchers.discovery import collect_query, collect_queries, merge_catalog, parse_feed


def paper(i, date="2026-10-08T00:00:00Z"):
    return {"id": f"2610.{i:05}", "updated": date, "version": "v1", "abstract": "x" * 1200, "title": str(i)}


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_paging_does_not_stop_at_old_top15(self):
        pages = [(list(map(paper, range(100))), 103), (list(map(paper, range(100, 103))), 103)]
        with patch("searchers.discovery.request_feed", new=AsyncMock(side_effect=pages)) as request:
            found, complete = await collect_query(None, "cat:cs.RO", datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.assertTrue(complete)
        self.assertEqual(len(found), 103)
        self.assertEqual(len(found[0]["abstract"]), 1200)
        self.assertEqual(request.call_args_list[1].args[1]["start"], 100)

    async def test_failure_only_advances_successful_query(self):
        state = {"query_success": {"bad": "2026-10-01T00:00:00Z"}}
        with patch("searchers.discovery.collect_query", new=AsyncMock(side_effect=[httpx.ConnectError("offline"), ([paper(1)], True)])):
            found, new_state, health = await collect_queries(None, {"bad": "x", "good": "y"}, state)
        self.assertEqual(new_state["query_success"]["bad"], state["query_success"]["bad"])
        self.assertIn("good", new_state["query_success"])
        self.assertEqual(health["bad"]["status"], "error")
        self.assertEqual(len(found), 1)

    async def test_page_cap_keeps_candidates_but_not_cursor(self):
        with patch("searchers.discovery.request_feed", new=AsyncMock(return_value=([paper(1)], 999))):
            found, state, health = await collect_queries(None, {"cap": "x"}, {}, max_pages=1,
                started=datetime(2026, 10, 9, tzinfo=timezone.utc))
        self.assertEqual(len(found), 1)
        self.assertEqual(state["query_success"], {})
        self.assertEqual(health["cap"]["status"], "truncated")

    def test_revision_reenters_review_and_preserves_discovery(self):
        old = dict(paper(1), date_found="2026-10-01", review_status="reviewed", hit_dimensions=["a"])
        new = dict(paper(1), version="v2", hit_dimensions=["b"])
        merged = merge_catalog([old], [new, new], "2026-10-09T00:00:00Z")
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["date_found"], "2026-10-01")
        self.assertEqual(merged[0]["review_status"], "pending")
        self.assertEqual(merged[0]["hit_dimensions"], ["a", "b"])

    def test_xml_preserves_full_abstract_and_canonical_id(self):
        abstract = "long " * 300
        xml = f'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/">
        <o:totalResults>1</o:totalResults><entry><id>http://arxiv.org/abs/2610.00001v2</id>
        <title>Test</title><summary>{abstract}</summary><published>2026-10-01T00:00:00Z</published>
        <updated>2026-10-08T00:00:00Z</updated></entry></feed>'''
        found, total = parse_feed(xml)
        self.assertEqual(total, 1)
        self.assertEqual(found[0]["id"], "2610.00001")
        self.assertEqual(found[0]["version"], "v2")
        self.assertEqual(found[0]["abstract"], abstract.strip())
        with self.assertRaises(ValueError):
            parse_feed(xml.replace("<o:totalResults>1</o:totalResults>", ""))


if __name__ == "__main__":
    unittest.main()
