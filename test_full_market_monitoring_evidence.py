import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "evidence", Path(__file__).parent / "tools" / "full_market_monitoring_evidence.py"
)
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


def history(date, close, volume=100, feature_valid=True, high=None, low=None):
    return {"date": date, "open": close - 1, "high": high if high is not None else close + 1,
            "low": low if low is not None else close - 2, "close": close, "volume": volume,
            "feature_valid": feature_valid}


def current(code="005930", market="KOSPI", price=130, volume=200):
    return {"code": code, "name": code, "market": market, "currentPrice": price, "change": 2,
            "changeRate": 1.56, "openPrice": price - 1, "highPrice": price + 2, "lowPrice": price - 3,
            "accumulatedTradingVolume": volume, "accumulatedTradingValue": 1000, "marketCap": 1000,
            "high52Week": 132, "low52Week": 50, "snapshotGeneratedAt": "2026-10-01T10:00:00+09:00"}


class EvidenceTests(unittest.TestCase):
    def test_current_history_merge_no_duplicate_and_no_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / "005930.jsonl"
            rows = [history(f"202609{day:02d}", 100 + day) for day in range(1, 22)] + [history("20261001", 999)]
            original = "".join(json.dumps(row) + "\n" for row in rows)
            path.write_text(original, encoding="utf-8")
            feature = evidence.feature_row(current(), evidence.read_history("005930", root, "20261001"), "20261001")
            self.assertNotEqual(feature["ma20"], 999)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_ma_return_rolling_high_breakout_and_volume_ratio(self):
        history_rows = [history(f"202609{day:02d}", 100 + day, 100) for day in range(1, 21)]
        item = current(price=130, volume=200)
        item["highPrice"] = 140
        feature = evidence.feature_row(item, history_rows, "20261001")
        self.assertIsNotNone(feature["ma20"])
        self.assertIsNotNone(feature["ret_5d"])
        self.assertEqual(feature["rolling_high_20"], 121.0)
        self.assertTrue(feature["breakout_20_intraday"])
        self.assertEqual(feature["current_volume_ratio_20"], 2.0)

    def test_invalid_ohlc_and_zero_volume_remain_unfixed(self):
        item = current(price=100, volume=0)
        item.update(openPrice=0, highPrice=0, lowPrice=0)
        feature = evidence.feature_row(item, [history("20260930", 99)], "20261001")
        self.assertTrue(feature["close_valid"])
        self.assertFalse(feature["ohlc_valid"])
        self.assertFalse(feature["volume_valid"])
        self.assertIsNone(feature["breakout_20_intraday"])

    def test_breadth_has_market_splits(self):
        rows = []
        for item in (current("A", "KOSPI", 110), current("B", "KOSDAQ", 90)):
            item["changeRate"] = 1 if item["market"] == "KOSPI" else -1
            rows.append(evidence.feature_row(item, [history("20260930", 100)] * 20, "20261001"))
        self.assertEqual(evidence.breadth(rows, "TOTAL")["stockCount"], 2)
        self.assertEqual(evidence.breadth(rows, "KOSPI")["advancers"], 1)
        self.assertEqual(evidence.breadth(rows, "KOSDAQ")["decliners"], 1)
        total = evidence.breadth(rows, "TOTAL")
        self.assertIn("aboveMA20Count", total)
        self.assertIn("breakout20Count", total)

    def test_explicit_full_market_scope_does_not_share_monitored_universe_metrics(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "t", "expectedCount": 2, "coverageCount": 2,
                    "attemptCount": 1, "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current("A", "KOSPI"), current("B", "KOSDAQ")]}
        context = {"monitoredUniverse": {"scope": "MONITORED_UNIVERSE", "configuredCount": 42,
                                             "current": {"stockCount": 42, "advancers": 35}},
                   "monitoredUniverseLeaders": [{"code": "M", "name": "관심"}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for code in ("A", "B"):
                (root / f"{code}.jsonl").write_text("\n".join(json.dumps(history(f"202609{d:02d}", 100 + d)) for d in range(1, 22)), encoding="utf-8")
            built, _ = evidence.build_evidence(snapshot, root, publication_context=context, today="20261001")
        summary = built["latest-summary.json"]
        self.assertEqual(summary["fullMarket"]["scope"], "AUTHORITATIVE_FULL_MARKET")
        self.assertEqual(summary["fullMarket"]["markets"]["TOTAL"]["stockCount"], 2)
        self.assertEqual(summary["monitoredUniverse"]["configuredCount"], 42)
        self.assertEqual(built["latest-leaders.json"]["scope"], "AUTHORITATIVE_FULL_MARKET")
        self.assertEqual(built["latest-leaders.json"]["monitoredUniverseLeaders"][0]["code"], "M")

    def test_previous_changes_and_industry_partial_propagate(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-01T10:00:00+09:00", "expectedCount": 2,
                    "coverageCount": 2, "attemptCount": 1, "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current("A", "KOSPI"), current("B", "KOSDAQ")]}
        industries = {"status": "PARTIAL", "industries": [{"id": "1", "name": "업종"}], "diagnostics": {
            "pagesFetched": 1, "failedPage": 1, "currentCoveragePct": 66.67,
            "missingCategoryIds": ["2"], "partialReason": "page failure",
            "comparisonSource": "previous_successful_snapshot"}}
        membership = {"status": "OK", "industries": [{"id": "1", "name": "업종", "status": "OK", "members": [{"code": "A"}, {"code": "B"}]}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for code in ("A", "B"):
                (root / f"{code}.jsonl").write_text("\n".join(json.dumps(history(f"202609{d:02d}", 100 + d)) for d in range(1, 22)), encoding="utf-8")
            previous = {"generatedAt": "2026-10-01T09:00:00+09:00", "breadth": {"TOTAL": {"advancerPct": 20, "pctAboveMA20": 30, "pctAboveMA60": 40, "pctBreakout20": 0, "pctNear52WeekHigh": 1, "medianReturn1D": 2, "pctVolumeAbove20DayAverage": 5}}}
            previous_good = {"generatedAt": "2026-10-01T08:00:00+09:00"}
            built, _ = evidence.build_evidence(
                snapshot, root, industries, membership, previous, "20261001",
                previous_good_industries=previous_good,
            )
        quality = built["latest-industries.json"]["quality"]
        self.assertEqual(quality["industryEvidenceUsable"], "PARTIAL_WITH_COVERAGE")
        self.assertEqual(quality["missingCount"], 1)
        self.assertEqual(quality["previousGoodGeneratedAt"], previous_good["generatedAt"])
        self.assertTrue(built["latest-changes.json"]["comparisonAvailable"])

    def test_theme_disabled_and_quality_summary(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "t", "expectedCount": 1, "coverageCount": 1,
                    "attemptCount": 1, "duplicateCodes": [], "missingCodes": [], "stocks": [current()]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "005930.jsonl").write_text(json.dumps(history("20260930", 100)) + "\n", encoding="utf-8")
            built, _ = evidence.build_evidence(snapshot, root, None, None, None, "20261001")
        self.assertEqual(built["latest-summary.json"]["themeEvidenceStatus"], "DISABLED_PENDING_RESEARCH_GATE")
        self.assertEqual(built["latest-quality.json"]["historyCoveragePct"], 100.0)


if __name__ == "__main__":
    unittest.main()
