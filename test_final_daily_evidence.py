import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tools import final_daily_evidence
from tools.final_daily_evidence import validate_snapshot


def row(code="000001", **extra):
    value = {"code": code, "openPrice": 10, "highPrice": 12, "lowPrice": 9,
             "currentPrice": 11, "accumulatedTradingVolume": 100,
             "accumulatedTradingValue": 1000, "marketStatus": "CLOSE",
             "tradingSessionType": "AFTER_MARKET"}
    value.update(extra)
    return value


class FinalDailyEvidenceTests(unittest.TestCase):
    def test_after_20_without_terminal_marker_is_official_candidate(self):
        result = validate_snapshot({"source": "NAVER_STOCKLIST_CURRENT", "sourceMarket": "KRX", "tradingDate": "20261019",
                                    "generatedAt": "2026-10-19T20:30:00+09:00",
                                    "stocks": [row()]},
                                   {"asOfDate": "20261019", "historyStatus": "OK"})
        self.assertEqual(result["status"], "PASS_WITH_UNKNOWN_FINALITY")
        self.assertTrue(result["finalEligible"])
        self.assertEqual(result["sessionStatus"], "KRX_AFTER_MARKET_CLOSE_20")
        self.assertEqual(result["finalityPolicy"], "KRX_AFTER_MARKET_CLOSE_20_CANDIDATE")
        self.assertEqual(result["networkCalls"], 0)

    def test_terminal_marker_can_be_reported_but_not_inferred(self):
        result = validate_snapshot({"source": "NAVER_STOCKLIST_CURRENT", "sourceMarket": "KRX", "tradingDate": "20261019",
                                    "generatedAt": "2026-10-19T20:30:00+09:00",
                                    "stocks": [row(marketStatus="CLOSED")]},
                                   {"asOfDate": "20261019", "historyStatus": "OK"})
        self.assertTrue(result["finalEligible"])
        self.assertEqual(result["sessionStatus"], "EXPLICIT_TERMINAL")

    def test_before_close_date_mismatch_and_duplicates_are_rejected(self):
        result = validate_snapshot({"source": "NAVER_STOCKLIST_CURRENT", "sourceMarket": "KRX", "tradingDate": "20261019",
                                    "generatedAt": "2026-10-19T19:59:00+09:00",
                                    "stocks": [row(), row()]},
                                   {"asOfDate": "20261018", "historyStatus": "OK"},
                                   expected_codes=["000001", "000002"])
        self.assertEqual(result["status"], "UNUSABLE")
        self.assertIn("SNAPSHOT_BEFORE_20_KST", result["errors"])
        self.assertNotIn("BASELINE_DATE_MISMATCH", result["errors"])
        self.assertIn("DUPLICATE_CODES", result["errors"])
        self.assertIn("MISSING_CODES", result["errors"])

    def test_future_baseline_date_is_rejected(self):
        result = validate_snapshot({"source": "NAVER_STOCKLIST_CURRENT", "sourceMarket": "KRX", "tradingDate": "20261019",
                                    "generatedAt": "2026-10-19T20:30:00+09:00",
                                    "stocks": [row()]},
                                   {"asOfDate": "20261020", "historyStatus": "OK"})
        self.assertIn("BASELINE_DATE_MISMATCH", result["errors"])

    def test_invalid_ohlcv_is_not_final(self):
        result = validate_snapshot({"source": "NAVER_STOCKLIST_CURRENT", "sourceMarket": "KRX", "tradingDate": "20261019",
                                    "generatedAt": "2026-10-19T20:30:00+09:00",
                                    "stocks": [row(highPrice=None, marketStatus="CLOSED")]})
        self.assertFalse(result["finalEligible"])
        self.assertIn("OHLCV_PRICE_INVALID:000001", result["errors"])

    def test_etf_and_etn_use_regular_session_policy_without_after_market_marker(self):
        result = validate_snapshot({"source": "NAVER_STOCKLIST_CURRENT", "sourceMarket": "KRX", "tradingDate": "20261019",
                                    "generatedAt": "2026-10-19T20:30:00+09:00",
                                    "stocks": [row("ETF1", assetType="ETF"),
                                               row("ETN1", assetType="ETN")]},
                                   {"asOfDate": "20261019", "historyStatus": "OK"})
        self.assertTrue(result["finalEligible"])
        self.assertEqual(result["finalityPolicy"], "KRX_AFTER_MARKET_CLOSE_20_CANDIDATE")

    def test_cli_uses_explicit_snapshot_date_not_prior_baseline(self):
        snapshot = {"source": "NAVER_STOCKLIST_CURRENT", "sourceMarket": "KRX", "tradingDate": "20261026",
                    "generatedAt": "2026-10-26T20:30:00+09:00",
                    "stocks": [row()]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_path = root / "snapshot.json"
            baseline_path = root / "baseline.json"
            snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
            baseline_path.write_text(json.dumps({"asOfDate": "20261016", "historyStatus": "OK"}), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(final_daily_evidence.main([
                    "--snapshot", str(snapshot_path), "--baseline", str(baseline_path)
                ]), 0)
            self.assertEqual(json.loads(output.getvalue())["tradingDate"], "20261026")

    def test_as_of_date_is_not_a_trading_date_source(self):
        result = validate_snapshot({"source": "NAVER_STOCKLIST_CURRENT", "sourceMarket": "KRX", "asOfDate": "20261016",
                                    "generatedAt": "2026-10-26T20:30:00+09:00",
                                    "stocks": [row()]},
                                   {"asOfDate": "20261016", "historyStatus": "OK"})
        self.assertIn("TRADING_DATE_MISSING", result["errors"])
        self.assertFalse(result["finalEligible"])


if __name__ == "__main__":
    unittest.main()
