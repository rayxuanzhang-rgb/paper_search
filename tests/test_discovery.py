import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx

from searchers.discovery import collect_query, collect_queries, merge_catalog, parse_feed, collect_backfill, reconcile_announcements


def paper(i, date="2026-10-08T00:00:00Z"):
    return {"id": f"2610.{i:05}", "updated": date, "version": "v1", "abstract": "x" * 1200, "title": str(i)}


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_initial_window_includes_early_hours_and_preserves_history(self):
        with patch("searchers.discovery.collect_query", new=AsyncMock(return_value=([], True))) as request:
            _, state, _ = await collect_queries(None, {"a": "x"}, {"backfill": {"saved": True}},
                started=datetime(2026, 10, 9, 5, 51, tzinfo=timezone.utc))
        self.assertEqual(request.call_args.args[2], datetime(2026, 10, 2, tzinfo=timezone.utc))
        self.assertEqual(state["backfill"], {"saved": True})

    async def test_backfill_resume_failure_and_query_change(self):
        started = datetime(2026, 10, 9, tzinfo=timezone.utc)
        with patch("searchers.discovery.collect_query", new=AsyncMock(side_effect=[([paper(1)], True), ([], False)])):
            found, state, health = await collect_backfill(None, {"a": "cat:cs.RO"}, {}, started=started, windows=2)
        self.assertEqual(state["backfill"]["a"]["next_end"], "2026-10-03T00:00:00+00:00")
        self.assertFalse(health["a"]["complete"])
        with patch("searchers.discovery.collect_query", new=AsyncMock(return_value=([], True))) as request:
            _, resumed, health = await collect_backfill(None, {"a": "cat:cs.RO"}, state, started=started, windows=5)
        self.assertIn("202609260000 TO 202610022359", request.call_args_list[0].args[1])
        self.assertTrue(health["a"]["complete"])
        with patch("searchers.discovery.collect_query", new=AsyncMock(side_effect=httpx.ConnectError("offline"))):
            _, failed, health = await collect_backfill(None, {"a": "changed"}, resumed, started=started)
        self.assertEqual(failed["backfill"]["a"]["next_end"], "2026-10-10T00:00:00+00:00")
        self.assertEqual(health["a"]["status"], "error")

    async def test_announcements_recover_old_submission_and_reject_partial_list(self):
        client = AsyncMock()
        client.get.return_value = httpx.Response(200, text='Total of 1 entries <a href ="/abs/2610.00001">abs</a>',
                                                request=httpx.Request("GET", "https://arxiv.org"))
        old = paper(1, "2026-09-16T00:00:00Z")
        with patch("searchers.discovery.request_feed", new=AsyncMock(return_value=([old], 1))):
            found, health = await reconcile_announcements(client, [])
        self.assertEqual(found[0]["updated"], old["updated"])
        self.assertEqual(health["remaining"], 0)
        client.get.return_value = httpx.Response(200, text='Total of 2 entries <a href="/abs/2610.00001">abs</a>',
                                                request=httpx.Request("GET", "https://arxiv.org"))
        found, health = await reconcile_announcements(client, [])
        self.assertEqual(health["status"], "error")

    async def test_announcements_missing_metadata_is_failure(self):
        client = AsyncMock()
        client.get.return_value = httpx.Response(200, text='Total of 1 entries <a href="/abs/2610.00001">abs</a>',
                                                request=httpx.Request("GET", "https://arxiv.org"))
        with patch("searchers.discovery.request_feed", new=AsyncMock(return_value=([], 0))):
            _, health = await reconcile_announcements(client, [])
        self.assertEqual(health["status"], "error")
        self.assertEqual(health["remaining"], 1)

    def test_backfill_cannot_downgrade_a_new_revision(self):
        new = dict(paper(1), version="v2", hit_dimensions=None, review_status="reviewed")
        old = dict(paper(1, "2026-09-16T00:00:00Z"), hit_dimensions=["history"])
        merged = merge_catalog([new], [old], "2026-10-09T00:00:00Z")[0]
        self.assertEqual(merged["version"], "v2")
        self.assertEqual(merged["review_status"], "reviewed")
        self.assertEqual(merged["hit_dimensions"], ["history"])

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
