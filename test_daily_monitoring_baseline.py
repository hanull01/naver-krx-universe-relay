import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baseline = load("daily_monitoring_baseline")
evidence = load("full_market_monitoring_evidence")


def history(day, close, volume=100, valid=True):
    return {"date": f"202609{day:02d}", "open": close - 1 if valid else 0,
            "high": close + 1 if valid else 0, "low": close - 2 if valid else 0,
            "close": close, "volume": volume, "feature_valid": valid}


def current(price=150, volume=200):
    return {"code": "000001", "name": "테스트", "market": "KOSPI", "currentPrice": price,
            "change": 1, "changeRate": 1.0, "openPrice": price - 1, "highPrice": price + 2,
            "lowPrice": price - 3, "accumulatedTradingVolume": volume, "high52Week": price + 3}


class DailyBaselineTests(unittest.TestCase):
    def _asset(self):
        return {"code": "000001", "name": "테스트", "market": "KOSPI"}

    def test_scalar_state_matches_canonical_reference_with_expiring_windows(self):
        rows = [history(day, 100 + day, 100 + day) for day in range(1, 241)]
        for day, row in enumerate(rows, start=1):
            row["date"] = f"{day:08d}"  # lexicographically ordered synthetic trading dates
        stock = baseline.baseline_stock(self._asset(), rows)
        direct = evidence.feature_row(current(400, 500), rows, "20261001")
        compact = baseline.feature_row_from_baseline(current(400, 500), stock, "20261001")
        keys = ("ma5", "ma20", "ma60", "ma120", "ma240", "ret_1d", "ret_5d", "ret_20d",
                "ret_60d", "ret_120d", "ret_240d", "rolling_high_20", "rolling_high_60",
                "rolling_high_120", "rolling_high_240", "breakout_20_intraday", "breakout_60_intraday",
                "current_volume_ratio_20", "ma20_rising", "ma60_rising")
        self.assertEqual({key: compact[key] for key in keys}, {key: direct[key] for key in keys})

    def test_short_history_zero_volume_and_invalid_ohlc_are_not_synthesized(self):
        rows = [history(1, 100, 0), history(2, 101, valid=False)]
        stock = baseline.baseline_stock(self._asset(), rows)
        item = baseline.feature_row_from_baseline(current(102, 0), stock, "20261001")
        self.assertIsNone(item["ma20"])
        self.assertFalse(item["volume_valid"])
        self.assertTrue(item["close_valid"])
        self.assertIsNone(item["breakout_20_intraday"])

    def test_build_is_read_only_and_has_authoritative_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); history_dir = root / "history"; history_dir.mkdir()
            universe = root / "universe.json"
            universe.write_text(json.dumps({"assets": [dict(self._asset(), assetType="STOCK")]}), encoding="utf-8")
            original = json.dumps(history(1, 100)) + "\n"
            (history_dir / "000001.jsonl").write_text(original, encoding="utf-8")
            built = baseline.build_baseline(history_dir, universe, "t")
            self.assertEqual(built["count"], 1)
            self.assertEqual(built["coveragePct"], 100.0)
            self.assertEqual((history_dir / "000001.jsonl").read_text(encoding="utf-8"), original)

    def test_evidence_rejects_same_day_baseline_without_final_session_proof(self):
        baseline_payload = {"historyStatus": "OK", "asOfDate": "20261001", "stocks": []}
        snapshot = {"status": "SUCCESS", "stocks": [], "generatedAt": "t", "expectedCount": 0,
                    "duplicateCodes": [], "missingCodes": [], "attemptCount": 1}
        with self.assertRaisesRegex(ValueError, "validated final-session"):
            evidence.build_evidence(snapshot, baseline=baseline_payload, today="20261001")

    def test_same_day_after_overlay_matches_reference_without_duplicate_day(self):
        rows = [history(day, 100 + day, 100 + day) for day in range(1, 242)]
        for day, row in enumerate(rows, start=1):
            row["date"] = f"{day:08d}"
        today = rows[-1]["date"]
        stock = baseline.baseline_stock(self._asset(), rows)
        after = current(500, 700)
        direct = evidence.feature_row(after, rows, today, overlay=True)
        compact = baseline.feature_row_from_baseline(after, stock, today, overlay=True)
        keys = ("ma5", "ma20", "ma60", "ma120", "ma240", "ret_1d", "ret_5d", "ret_20d",
                "ret_60d", "ret_120d", "ret_240d", "rolling_high_20", "rolling_high_60",
                "rolling_high_120", "rolling_high_240", "breakout_20_intraday", "breakout_60_intraday",
                "current_volume_ratio_20", "ma20_rising", "ma60_rising")
        self.assertEqual({key: compact[key] for key in keys}, {key: direct[key] for key in keys})
        self.assertTrue(compact["sameDayOverlay"])

    def test_evidence_allows_same_day_overlay_only_after_final_baseline_validation(self):
        rows = [history(day, 100 + day, 100 + day) for day in range(1, 25)]
        today = rows[-1]["date"]
        stock = baseline.baseline_stock(self._asset(), rows)
        snapshot = {"status": "SUCCESS", "stocks": [current(200, 300)], "generatedAt": "t",
                    "expectedCount": 1, "duplicateCodes": [], "missingCodes": [], "attemptCount": 1}
        payloads, features = evidence.build_evidence(
            snapshot, baseline={"historyStatus": "OK", "asOfDate": today, "stocks": [stock]}, today=today,
            publication_context={"baselineFreshnessStatus": "VALID_CURRENT_DAY_FINAL"},
        )
        self.assertTrue(features[0]["sameDayOverlay"])
        self.assertEqual(payloads["latest-quality.json"]["baselineFreshnessStatus"], "VALID_CURRENT_DAY_FINAL")


if __name__ == "__main__":
    unittest.main()
