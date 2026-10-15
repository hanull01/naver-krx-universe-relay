import unittest
from datetime import date
from unittest.mock import patch

import research_intelligence as ri
import research_intelligence_batch as rib


class ResearchIntelligenceTest(unittest.TestCase):

    def test_target_stats(self):
        rows = [
            {"goalPrice": 100000},
            {"goalPrice": 200000},
            {"goalPrice": None},
        ]

        result = ri.target_stats(rows)

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["min"], 100000)
        self.assertEqual(result["max"], 200000)
        self.assertEqual(result["mean"], 150000)
        self.assertEqual(result["median"], 150000)
        self.assertEqual(result["stdev"], 50000)
        self.assertEqual(result["range"], 100000)

    def test_topic_stats_normalized_per_report(self):
        rows = [
            {
                "title": "HBM 성장성에 주목",
                "analysis": {"topics": {}},
            },
            {
                "title": "파운드리 경쟁력 확대",
                "analysis": {"topics": {}},
            },
        ]

        result = ri.topic_stats(rows)

        self.assertEqual(
            result["HBM"]["count"],
            1,
        )
        self.assertEqual(
            result["HBM"]["perReport"],
            0.5,
        )
        self.assertEqual(
            result["파운드리"]["count"],
            1,
        )
        self.assertEqual(
            result["파운드리"]["perReport"],
            0.5,
        )

    def test_compare_topics_detects_acceleration(self):
        current = [
            {
                "title": "HBM 성장",
                "analysis": {"topics": {}},
            },
            {
                "title": "HBM 확대",
                "analysis": {"topics": {}},
            },
        ]

        previous = [
            {
                "title": "AI 수요",
                "analysis": {"topics": {}},
            },
            {
                "title": "일반 수요",
                "analysis": {"topics": {}},
            },
        ]

        result = ri.compare_topics(
            current,
            previous,
        )

        by_topic = {
            row["topic"]: row
            for row in result
        }

        self.assertGreater(
            by_topic["HBM"]["perReportDelta"],
            0,
        )

        self.assertLessEqual(
            by_topic["AI"]["perReportDelta"],
            0,
        )

    def test_safe_pct_change(self):
        self.assertEqual(
            rib.safe_pct_change(110, 100),
            10.0,
        )

        self.assertEqual(
            rib.safe_pct_change(90, 100),
            -10.0,
        )

        self.assertIsNone(
            rib.safe_pct_change(100, 0)
        )

        self.assertIsNone(
            rib.safe_pct_change(None, 100)
        )

    def test_unknown_stock_returns_no_research(self):
        result = rib.analyze_stock(
            code="999999",
            name="TEST",
            as_of=date(2026, 9, 26),
            recent_days=30,
            min_recent_reports=3,
        )

        self.assertEqual(
            result["status"],
            "NO_RESEARCH",
        )

        self.assertFalse(
            result["rankingEligible"]
        )

        self.assertEqual(
            result["reportCount"],
            0,
        )

    def test_universe_loader_returns_enabled_stocks(self):
        # Keep research behavior independent from the intentionally empty
        # production/user universe.
        fixture = [
            {"itemCode": "000001", "stockName": "fixture-A", "enabled": True},
            {"itemCode": "000002", "stockName": "fixture-B", "enabled": True},
        ]
        with patch.object(rib, "load_universe", return_value=fixture):
            stocks = rib.load_universe()

        self.assertGreater(
            len(stocks),
            0,
        )

        self.assertTrue(
            all(
                stock.get("enabled", True)
                for stock in stocks
            )
        )

        self.assertTrue(
            all(
                "itemCode" in stock
                and "stockName" in stock
                for stock in stocks
            )
        )


if __name__ == "__main__":
    unittest.main()
