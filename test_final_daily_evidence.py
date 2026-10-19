import unittest

from tools.final_daily_evidence import validate_snapshot


def row(code="000001", **extra):
    value = {"code": code, "openPrice": 10, "highPrice": 12, "lowPrice": 9,
             "currentPrice": 11, "accumulatedTradingVolume": 100,
             "accumulatedTradingValue": 1000}
    value.update(extra)
    return value


class FinalDailyEvidenceTests(unittest.TestCase):
    def test_after_20_without_terminal_marker_is_official_candidate(self):
        result = validate_snapshot({"tradingDate": "20261019",
                                    "generatedAt": "2026-10-19T20:30:00+09:00",
                                    "stocks": [row()]},
                                   {"asOfDate": "20261019", "historyStatus": "OK"})
        self.assertEqual(result["status"], "PASS_WITH_UNKNOWN_FINALITY")
        self.assertTrue(result["finalEligible"])
        self.assertEqual(result["sessionStatus"], "NAVER_OFFICIAL_POST_20")
        self.assertEqual(result["finalityPolicy"], "NAVER_OFFICIAL_POST_20_CANDIDATE")
        self.assertEqual(result["networkCalls"], 0)

    def test_terminal_marker_can_be_reported_but_not_inferred(self):
        result = validate_snapshot({"tradingDate": "20261019",
                                    "generatedAt": "2026-10-19T20:30:00+09:00",
                                    "stocks": [row(marketStatus="CLOSED")]},
                                   {"asOfDate": "20261019", "historyStatus": "OK"})
        self.assertTrue(result["finalEligible"])
        self.assertEqual(result["sessionStatus"], "EXPLICIT_TERMINAL")

    def test_before_close_date_mismatch_and_duplicates_are_rejected(self):
        result = validate_snapshot({"tradingDate": "20261019",
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
        result = validate_snapshot({"tradingDate": "20261019",
                                    "generatedAt": "2026-10-19T20:30:00+09:00",
                                    "stocks": [row()]},
                                   {"asOfDate": "20261020", "historyStatus": "OK"})
        self.assertIn("BASELINE_DATE_MISMATCH", result["errors"])

    def test_invalid_ohlcv_is_not_final(self):
        result = validate_snapshot({"tradingDate": "20261019",
                                    "generatedAt": "2026-10-19T20:30:00+09:00",
                                    "stocks": [row(highPrice=None, marketStatus="CLOSED")]})
        self.assertFalse(result["finalEligible"])
        self.assertIn("OHLCV_PRICE_INVALID:000001", result["errors"])

    def test_etf_and_etn_use_regular_session_policy_without_after_market_marker(self):
        result = validate_snapshot({"tradingDate": "20261019",
                                    "generatedAt": "2026-10-19T20:30:00+09:00",
                                    "stocks": [row("ETF1", assetType="ETF"),
                                               row("ETN1", assetType="ETN")]},
                                   {"asOfDate": "20261019", "historyStatus": "OK"})
        self.assertTrue(result["finalEligible"])
        self.assertEqual(result["finalityPolicy"], "NAVER_OFFICIAL_POST_20_CANDIDATE")


if __name__ == "__main__":
    unittest.main()
