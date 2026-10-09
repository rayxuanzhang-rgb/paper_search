import copy
import json
import tempfile
import unittest
from pathlib import Path

from processing.codex_review import make_packet, store_review, validate_review
from output.review_html import render_review


class CodexReviewTests(unittest.TestCase):
    def setUp(self):
        self.profile = {"version": "test", "daily_max": 5, "topics": {"data": "数据"}}
        self.packet = make_packet([{"id": "2601.00001", "title": "<script>bad()</script>",
                                    "abstract": "source text"}], self.profile, label="历史样例")
        self.result = {
            "packet_id": self.packet["packet_id"], "profile_hash": self.packet["profile_hash"],
            "reviewer": {"engine": "codex", "model": "test-only", "reviewed_at": "2026-10-09"},
            "highlight_ids": ["2601.00001"],
            "reviews": [{"id": "2601.00001", "fingerprint": self.packet["candidates"][0]["fingerprint"],
                         "topics": ["data"], "decision": "read", "industry_importance": 4,
                         "transfer_value": 3, "evidence_level": "full_text", "headline": "测试样例",
                         "what_changed": "<img src=x onerror=alert(1)>", "why_care": "数据利用",
                         "transfer": "需验证", "caveat": "未复现",
                         "sources": [{"url": "https://arxiv.org/abs/2601.00001", "locator": "§3",
                                      "claim": "test-only evidence"}]}]
        }

    def test_no_legacy_scores_leak_into_packet(self):
        packet = make_packet([{"id": "x", "score": 9, "reason": "mechanical"}], self.profile, label="x")
        self.assertNotIn("score", packet["candidates"][0])
        self.assertNotIn("reason", packet["candidates"][0])

    def test_missing_review_is_rejected(self):
        self.result["reviews"] = []
        with self.assertRaises(ValueError):
            validate_review(self.packet, self.result)

    def test_duplicate_review_is_rejected(self):
        self.result["reviews"] *= 2
        with self.assertRaises(ValueError):
            validate_review(self.packet, self.result)

    def test_stale_source_or_profile_is_rejected(self):
        for mutate in (lambda r: r.update(profile_hash="stale"),
                       lambda r: r["reviews"][0].update(fingerprint="stale")):
            result = copy.deepcopy(self.result)
            mutate(result)
            with self.assertRaises(ValueError):
                validate_review(self.packet, result)

    def test_abstract_only_cannot_be_highlight(self):
        self.result["reviews"][0]["evidence_level"] = "abstract"
        with self.assertRaises(ValueError):
            validate_review(self.packet, self.result)
        self.result["highlight_ids"] = []
        validate_review(self.packet, self.result)

    def test_bad_score_and_unsafe_url_rejected(self):
        for score in (float("nan"), True, 8, -1):
            result = copy.deepcopy(self.result)
            result["reviews"][0]["transfer_value"] = score
            with self.assertRaises(ValueError):
                validate_review(self.packet, result)
        self.result["reviews"][0]["sources"][0]["url"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            validate_review(self.packet, self.result)

    def test_import_is_idempotent_and_preserves_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reviews.json"
            store_review(self.packet, self.result, path)
            store_review(self.packet, self.result, path)
            self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["runs"]), 1)
            self.result["reviews"][0]["caveat"] = "新增限制"
            store_review(self.packet, self.result, path)
            self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))["runs"]), 2)

    def test_html_escapes_source_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.html"
            render_review(self.packet, self.result, path)
            html = path.read_text(encoding="utf-8")
            self.assertNotIn("<script>bad()", html)
            self.assertNotIn("<img src=x", html)
            self.assertIn("&lt;script&gt;", html)
            self.assertIn("历史样例", html)


if __name__ == "__main__":
    unittest.main()
