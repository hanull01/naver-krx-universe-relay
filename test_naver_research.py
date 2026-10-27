import tempfile
import unittest
from pathlib import Path

import naver_research as nr


class NaverResearchTest(unittest.TestCase):
    def test_normalize_opinion(self):
        self.assertEqual(nr.normalize_opinion("Buy"), "BUY")
        self.assertEqual(nr.normalize_opinion("매수"), "BUY")
        self.assertEqual(nr.normalize_opinion("StrongBuy"), "BUY")
        self.assertEqual(nr.normalize_opinion("Hold"), "HOLD")
        self.assertEqual(nr.normalize_opinion("중립"), "HOLD")
        self.assertEqual(nr.normalize_opinion("Neutral"), "HOLD")
        self.assertEqual(nr.normalize_opinion("매도"), "SELL")
        self.assertEqual(nr.normalize_opinion("OutPerform"), "OUTPERFORM")
        self.assertEqual(nr.normalize_opinion("MarketPerform"), "MARKETPERFORM")
        self.assertEqual(nr.normalize_opinion("없음"), "NONE")

    def test_goal_price(self):
        self.assertEqual(nr.parse_goal_price("500000"), 500000)
        self.assertEqual(nr.parse_goal_price("500,000"), 500000)
        self.assertIsNone(nr.parse_goal_price(""))
        self.assertIsNone(nr.parse_goal_price(None))
        self.assertIsNone(nr.parse_goal_price("0"))

    def test_strip_html(self):
        text = nr.strip_html("<p>목표가 <strong>상향</strong></p><p>실적 개선</p>")
        self.assertIn("목표가 상향", text)
        self.assertIn("실적 개선", text)

    def test_analysis_direction_and_topics(self):
        content = """
        <p>실적 개선과 영업이익 증가가 기대된다.</p>
        <p>AI와 로봇 사업의 성장 가능성이 높다.</p>
        <p>목표주가는 기존 대비 상향한다.</p>
        """
        a = nr.analyze_content(content)

        self.assertEqual(a["earningsTextDirection"], "UP")
        self.assertEqual(a["targetPriceMentionDirection"], "UP")
        self.assertIn("AI", a["topics"])
        self.assertIn("로봇", a["topics"])

    def test_analysis_negative_earnings(self):
        content = """
        <p>원화 강세와 파업 영향으로 실적 둔화가 예상된다.</p>
        <p>영업이익 감소를 반영해 목표주가를 하향한다.</p>
        """
        a = nr.analyze_content(content)

        self.assertEqual(a["earningsTextDirection"], "DOWN")
        self.assertEqual(a["targetPriceMentionDirection"], "DOWN")
        self.assertIn("환율", a["topics"])
        self.assertIn("파업", a["topics"])

    def test_realistic_target_price_down_phrase(self):
        content = """
        <p>목표주가 500,000원으로 16.7% 하향:
        3분기 파업과 원화 강세 영향으로 실적 둔화 영향 반영.</p>
        """
        a = nr.analyze_content(content)

        self.assertEqual(a["targetPriceMentionDirection"], "DOWN")
        self.assertEqual(a["earningsTextDirection"], "DOWN")
        self.assertIn("환율", a["topics"])
        self.assertIn("파업", a["topics"])

    def test_target_price_unchanged_phrase(self):
        content = """
        <p>목표주가 120,000원과 투자의견 Buy를 유지한다.</p>
        """
        a = nr.analyze_content(content)
        self.assertEqual(
            a["targetPriceMentionDirection"],
            "UNCHANGED",
        )

    def test_same_broker_change(self):
        rows = [
            {
                "nid": "1",
                "writeDate": "2026-01-01",
                "itemCode": "005930",
                "brokerCode": "78",
                "goalPrice": 100000,
                "normalizedOpinion": "BUY",
            },
            {
                "nid": "2",
                "writeDate": "2026-02-01",
                "itemCode": "005930",
                "brokerCode": "78",
                "goalPrice": 120000,
                "normalizedOpinion": "HOLD",
            },
        ]
        result = nr.apply_same_broker_changes(rows)
        latest = result[0]

        self.assertEqual(latest["nid"], "2")
        self.assertEqual(latest["previousSameBrokerNid"], "1")
        self.assertEqual(latest["goalPriceChange"]["amount"], 20000)
        self.assertEqual(latest["goalPriceChange"]["pct"], 20.0)
        self.assertEqual(
            latest["opinionChange"],
            {"from": "BUY", "to": "HOLD"},
        )

    def test_same_day_reports_compare_to_previous_date(self):
        rows = [
            {
                "nid": "1",
                "writeDate": "2026-01-01",
                "itemCode": "005930",
                "brokerCode": "78",
                "goalPrice": 100000,
                "normalizedOpinion": "BUY",
            },
            {
                "nid": "2",
                "writeDate": "2026-02-01",
                "itemCode": "005930",
                "brokerCode": "78",
                "goalPrice": 120000,
                "normalizedOpinion": "BUY",
            },
            {
                "nid": "3",
                "writeDate": "2026-02-01",
                "itemCode": "005930",
                "brokerCode": "78",
                "goalPrice": 130000,
                "normalizedOpinion": "BUY",
            },
        ]

        result = nr.apply_same_broker_changes(rows)
        by_nid = {r["nid"]: r for r in result}

        self.assertEqual(
            by_nid["2"]["previousSameBrokerNid"],
            "1",
        )
        self.assertEqual(
            by_nid["3"]["previousSameBrokerNid"],
            "1",
        )

    def test_large_goal_price_change_flag(self):
        rows = [
            {
                "nid": "1",
                "writeDate": "2026-01-01",
                "itemCode": "005930",
                "brokerCode": "78",
                "goalPrice": 100000,
                "normalizedOpinion": "BUY",
            },
            {
                "nid": "2",
                "writeDate": "2026-02-01",
                "itemCode": "005930",
                "brokerCode": "78",
                "goalPrice": 160000,
                "normalizedOpinion": "BUY",
            },
        ]

        result = nr.apply_same_broker_changes(rows)
        latest = result[0]

        self.assertTrue(latest["goalPriceChangeLarge"])
        self.assertEqual(
            latest["goalPriceChange"]["pct"],
            60.0,
        )

    def test_no_new_nid_is_true_noop(self):
        raw = [{
            "nid": "10",
            "writeDate": "2026-09-20",
            "itemCode": "005930",
            "itemName": "Samsung",
            "title": "Test",
            "brokerCode": "1",
            "brokerName": "Broker",
            "goalPrice": "100000",
            "opinionText": "Buy",
            "opinionType": "buy",
            "readCount": "10",
            "content": "<p>test body</p>",
        }]

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            nr.build_outputs(
                raw, root,
                "2026-09-20",
                "2026-09-20",
            )

            before = {
                p.relative_to(root): p.read_bytes()
                for p in root.rglob("*.json")
            }

            result = nr.build_outputs(
                raw, root,
                "2026-09-20",
                "2026-09-20",
            )

            after = {
                p.relative_to(root): p.read_bytes()
                for p in root.rglob("*.json")
            }

            self.assertEqual(result["newReportCount"], 0)
            self.assertTrue(result["unchanged"])
            self.assertEqual(before, after)

    def test_identical_page_boundary_overlap_is_deduplicated(self):
        pages = {
            0: {"totalCount": 2, "items": [{"nid": "1", "title": "A"}], "hasNext": True},
            1: {"totalCount": 2, "items": [{"nid": "1", "title": "A"}, {"nid": "2", "title": "B"}], "hasNext": False},
        }

        original = nr.fetch_page
        try:
            nr.fetch_page = lambda page, start_date, end_date: pages[page]
            reports, total = nr.collect("2026-10-01", "2026-10-28", sleep_seconds=0)
        finally:
            nr.fetch_page = original

        self.assertEqual(total, 2)
        self.assertEqual([row["nid"] for row in reports], ["1", "2"])

    def test_conflicting_page_boundary_overlap_fails_closed(self):
        pages = {
            0: {"totalCount": 2, "items": [{"nid": "1", "title": "A"}], "hasNext": True},
            1: {"totalCount": 2, "items": [{"nid": "1", "title": "changed"}, {"nid": "2", "title": "B"}], "hasNext": False},
        }

        original = nr.fetch_page
        try:
            nr.fetch_page = lambda page, start_date, end_date: pages[page]
            with self.assertRaisesRegex(RuntimeError, "conflicting duplicate nid"):
                nr.collect("2026-10-01", "2026-10-28", sleep_seconds=0)
        finally:
            nr.fetch_page = original

    def test_same_page_duplicate_fails_closed(self):
        original = nr.fetch_page
        try:
            nr.fetch_page = lambda page, start_date, end_date: {
                "totalCount": 1,
                "items": [{"nid": "1"}, {"nid": "1"}],
                "hasNext": False,
            }
            with self.assertRaisesRegex(RuntimeError, "duplicate nid within page"):
                nr.collect("2026-10-01", "2026-10-28", sleep_seconds=0)
        finally:
            nr.fetch_page = original

    def test_build_outputs_does_not_store_content(self):
        raw = [{
            "nid": "10",
            "writeDate": "2026-09-20",
            "itemCode": "005930",
            "itemName": "삼성전자",
            "title": "테스트",
            "brokerCode": "1",
            "brokerName": "테스트증권",
            "goalPrice": "100000",
            "opinionText": "Buy",
            "opinionType": "buy",
            "readCount": "10",
            "content": "<p>이 본문은 저장되면 안 됩니다.</p>",
        }]

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            nr.build_outputs(
                raw,
                root,
                "2026-09-20",
                "2026-09-20",
            )
            saved = (root / "reports" / "2026-09.json").read_text()
            self.assertNotIn("이 본문은 저장되면 안 됩니다.", saved)
            self.assertIn("contentSha256", saved)
            self.assertIn("contentLength", saved)


if __name__ == "__main__":
    unittest.main()
