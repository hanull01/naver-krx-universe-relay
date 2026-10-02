import unittest
import json
import tempfile
from pathlib import Path

import daily_report


class DailyReportTests(unittest.TestCase):
    def test_full_market_evidence_requires_final_same_day_complete_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = daily_report.FULL_MARKET_FILES
            for name in names:
                (root / name).write_text(json.dumps({"generatedAt": "2026-10-02T16:20:00+09:00"}), encoding="utf-8")
            quality = {
                "generatedAt": "2026-10-02T16:20:00+09:00", "baselineAsOfDate": "20261002",
                "publicationStatus": "FINAL", "authoritativeCount": 2768,
                "currentCount": 2768, "currentCoveragePct": 100.0,
                "duplicateCodes": [], "missingCurrentCodes": [],
            }
            (root / "latest-quality.json").write_text(json.dumps(quality), encoding="utf-8")
            breadth = {"TOTAL": {"advancers": 1, "decliners": 2, "unchanged": 3}}
            (root / "latest-breadth.json").write_text(json.dumps({"generatedAt": quality["generatedAt"], "breadth": breadth}), encoding="utf-8")
            got = daily_report.full_market_evidence("2026-10-02", root)
            self.assertEqual(got["status"], "AVAILABLE")
            self.assertEqual(got["breadth"]["advancers"], 1)

    def test_full_market_evidence_rejects_stale_or_incomplete_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in daily_report.FULL_MARKET_FILES:
                (root / name).write_text(json.dumps({"generatedAt": "t"}), encoding="utf-8")
            (root / "latest-quality.json").write_text(json.dumps({
                "publicationStatus": "FINAL", "baselineAsOfDate": "20261001",
                "authoritativeCount": 2768, "currentCount": 2767,
                "currentCoveragePct": 99.96,
            }), encoding="utf-8")
            got = daily_report.full_market_evidence("2026-10-02", root)
            self.assertEqual(got["status"], "UNAVAILABLE")
            self.assertIn(got["reason"], {"BASELINE_DATE_MISMATCH", "CURRENT_COVERAGE_INVALID"})

    def test_quote_summary(self):
        row = {
            "price": 100,
            "changeRate": 2.5,
            "volume": 1234,
            "tradingValue": 9999,
            "sourceTime": "2026-09-26T15:30:00+09:00",
            "fresh": True,
        }
        got = daily_report.quote_summary(row)
        self.assertEqual(got["price"], 100)
        self.assertEqual(got["changeRate"], 2.5)
        self.assertEqual(got["volume"], 1234)

    def test_enabled_universe(self):
        payload = {
            "stocks": [
                {
                    "itemCode": "000001",
                    "stockName": "A",
                    "enabled": True,
                },
                {
                    "itemCode": "000002",
                    "stockName": "B",
                    "enabled": False,
                },
            ]
        }
        got = daily_report.enabled_universe(payload)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["itemCode"], "000001")

    def test_memberships(self):
        payload = {
            "sectors": {"섹터A": ["000001"]},
            "themes": {"테마A": ["000001"]},
            "watchlists": {"관심": ["000002"]},
        }
        got = daily_report.memberships(payload)
        self.assertEqual(len(got["000001"]), 2)
        self.assertEqual(got["000002"][0]["name"], "관심")

    def test_coverage_status_complete(self):
        self.assertEqual(
            daily_report.coverage_status(
                41, 41, 41, 41, "ok", True
            ),
            "OK",
        )

    def test_coverage_status_incomplete(self):
        self.assertEqual(
            daily_report.coverage_status(41, 36, 36, 36),
            "INCOMPLETE_MARKET_COVERAGE",
        )

    def test_coverage_status_stale(self):
        self.assertEqual(
            daily_report.coverage_status(
                41, 41, 41, 41, "stale", False
            ),
            "STALE_MARKET_DATA",
        )

    def test_coverage_status_fresh(self):
        self.assertEqual(
            daily_report.coverage_status(
                41, 41, 41, 41, "ok", True
            ),
            "OK",
        )

    def test_research_index(self):
        payload = {
            "stocks": [
                {
                    "itemCode": "005930",
                    "status": "OK",
                }
            ]
        }
        got = daily_report.research_index(payload)
        self.assertEqual(got["005930"]["status"], "OK")

    def test_market_breadth_excludes_missing_market_values(self):
        rows = [
            {
                "marketObserved": True,
                "market": {"changeRate": 2.0},
                "state": {
                    "priceVsMA20": "above", "priceVsMA60": "above",
                    "breakout20": "confirmed", "breakout60": "none",
                    "volumeState": "surge", "pullbackState": "near_breakout20",
                },
            },
            {
                "marketObserved": False,
                "market": {"changeRate": None},
                "state": {},
            },
        ]
        got = daily_report.market_breadth(rows)
        self.assertEqual(got["observedCount"], 1)
        self.assertEqual(got["upCount"], 1)
        self.assertEqual(got["breakout20ConfirmedCount"], 1)
        self.assertEqual(got["nearBreakoutCount"], 1)

    def test_market_breadth_zero_denominator_is_none(self):
        got = daily_report.market_breadth([])
        self.assertIsNone(got["upRatio"])
        self.assertIsNone(got["ma20AboveRatio"])

    def test_market_breadth_direction_ratio_excludes_missing_change_rate(self):
        rows = [
            {
                "marketObserved": True, "market": {"changeRate": 1.0},
                "state": {},
            },
            {
                "marketObserved": True, "market": {"changeRate": None},
                "state": {},
            },
        ]
        got = daily_report.market_breadth(rows)
        self.assertEqual(got["observedCount"], 2)
        self.assertEqual(got["directionObservedCount"], 1)
        self.assertEqual(got["upRatio"], 1.0)

    def test_market_breadth_ma20_ratio_excludes_missing_state_value(self):
        rows = [
            {
                "marketObserved": True, "market": {"changeRate": 0.0},
                "state": {"priceVsMA20": "above"},
            },
            {
                "marketObserved": True, "market": {"changeRate": 0.0},
                "state": {},
            },
        ]
        got = daily_report.market_breadth(rows)
        self.assertEqual(got["ma20ObservedCount"], 1)
        self.assertEqual(got["ma20AboveRatio"], 1.0)

    def test_normalized_group_summary_uses_dynamic_payload(self):
        got = daily_report.normalized_group_summary({"groups": [{
            "groupType": "theme", "groupName": "테마A", "enabledMembers": 2,
            "upCount": 1, "downCount": 1, "breakout20ConfirmedCount": 1,
            "volumeSurgeCount": 0, "leaderUpCount": 1,
            "averageChangePct": 0.2, "diffusionState": "mixed", "status": "ok",
        }]})
        self.assertEqual(got[0]["groupType"], "theme")
        self.assertEqual(got[0]["groupName"], "테마A")
        self.assertEqual(got[0]["enabledMembers"], 2)

    def test_technical_events_use_existing_state_labels(self):
        row = {
            "itemCode": "000001", "itemName": "A",
            "market": {"changeRate": 1.0},
            "state": {
                "breakout20": "confirmed", "breakout60": "attempt",
                "volumeState": "elevated", "pullbackState": "near_breakout20",
                "priceVsMA20": "above", "priceVsMA60": "below",
            },
        }
        got = daily_report.technical_events([row])
        self.assertEqual(len(got["breakout20Confirmed"]), 1)
        self.assertEqual(len(got["breakout60Attempt"]), 1)
        self.assertEqual(len(got["volumeElevated"]), 1)
        self.assertEqual(len(got["nearBreakout"]), 1)

    def test_research_summary_requires_ranking_eligible_for_active_stocks(self):
        rows = [
            {
                "itemCode": "000001", "itemName": "A",
                "research": {
                    "rankingEligible": True, "recentReportCount": 2,
                    "targetMeanChangePct": 1.0, "revisionUp": 1,
                    "revisionDown": 0, "risingTopics": [{"topic": "AI"}],
                },
            },
            {
                "itemCode": "000002", "itemName": "B",
                "research": {
                    "rankingEligible": False, "recentReportCount": 5,
                    "targetMeanChangePct": 3.0, "revisionUp": 1,
                    "revisionDown": 0, "risingTopics": [],
                },
            },
        ]
        got = daily_report.research_summary(rows, {"statusCounts": {"OK": 1}})
        self.assertEqual([x["itemCode"] for x in got["activeStocks"]], ["000001"])
        self.assertEqual(got["recentCoverageActiveCount"], 1)

    def test_market_research_cross_uses_boolean_buckets(self):
        rows = [
            {
                "itemCode": "000001", "itemName": "A", "marketObserved": True,
                "market": {"changeRate": 1.0},
                "state": {"priceVsMA20": "above"},
                "research": {"rankingEligible": True, "recentReportCount": 1},
            },
            {
                "itemCode": "000002", "itemName": "B", "marketObserved": False,
                "market": {"changeRate": None}, "state": {},
                "research": {"rankingEligible": False, "recentReportCount": 0},
            },
        ]
        got = daily_report.market_research_cross(rows)
        self.assertEqual(got["MARKET_ACTIVE_RESEARCH_ACTIVE"]["count"], 1)
        self.assertEqual(got["INSUFFICIENT_DATA"]["count"], 1)

    def test_market_research_cross_quiet_and_weak_limited_are_descriptive(self):
        rows = [
            {
                "itemCode": "000001", "itemName": "Quiet", "marketObserved": True,
                "market": {"changeRate": 0.0}, "state": {},
                "research": {"rankingEligible": False, "recentReportCount": 0},
            },
            {
                "itemCode": "000002", "itemName": "Weak", "marketObserved": True,
                "market": {"changeRate": -1.0}, "state": {},
                "research": {"rankingEligible": False, "recentReportCount": 0},
            },
        ]
        got = daily_report.market_research_cross(rows)
        self.assertEqual(got["MARKET_QUIET_RESEARCH_LIMITED"]["count"], 1)
        self.assertEqual(got["MARKET_WEAK_RESEARCH_LIMITED"]["count"], 1)
        self.assertEqual(got["INSUFFICIENT_DATA"]["count"], 0)

    def test_market_research_cross_insufficient_requires_missing_market_value(self):
        rows = [
            {
                "itemCode": "000001", "itemName": "Observed", "marketObserved": True,
                "market": {"changeRate": 0.0}, "state": {},
                "research": {"rankingEligible": False, "recentReportCount": 0},
            },
            {
                "itemCode": "000002", "itemName": "Missing", "marketObserved": False,
                "market": {"changeRate": None}, "state": {},
                "research": {"rankingEligible": False, "recentReportCount": 0},
            },
        ]
        got = daily_report.market_research_cross(rows)
        self.assertEqual(got["MARKET_QUIET_RESEARCH_LIMITED"]["count"], 1)
        self.assertEqual(got["INSUFFICIENT_DATA"]["count"], 1)


if __name__ == "__main__":
    unittest.main()
