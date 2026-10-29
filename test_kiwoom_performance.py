import unittest

from tools.kiwoom_performance import build_kiwoom_performance


def discovery(symbols=("AAA",), selection="2026-10-28", monitoring="2026-10-29"):
    return {
        "status": "AVAILABLE", "selectionDate": selection, "monitoringDate": monitoring,
        "sourcePath": f"data/trading-discovery/{monitoring}.json", "sourceSha256": "a" * 64,
        "symbols": list(symbols), "validation": {"codeNameBindings": {code: code for code in symbols}},
    }


def rows(*dates, quality="VERIFIED_FINAL"):
    return {date: {"date": date, "close": close, "qualityStatus": quality,
                   "corporateActionStatus": "VERIFIED_NONE"}
            for date, close in zip(dates, (100, 110, 120, 130, 140, 150))}


class KiwoomPerformanceTests(unittest.TestCase):
    def test_d1_d3_d5_use_the_supplied_krx_order_not_calendar_days(self):
        dates = ["2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03"]
        result = build_kiwoom_performance(discovery(), {"AAA": rows(*dates)}, dates)
        stock = result["stocks"][0]
        self.assertEqual(stock["horizons"]["D+1"]["date"], "2026-10-29")
        self.assertEqual(stock["horizons"]["D+3"]["date"], "2026-11-02")
        self.assertEqual(stock["horizons"]["D+5"]["status"], "PENDING")
        self.assertAlmostEqual(stock["horizons"]["D+1"]["returnPct"], 10.0)

    def test_source_observed_is_not_treated_as_confirmed_close(self):
        dates = ["2026-10-28", "2026-10-29"]
        result = build_kiwoom_performance(discovery(), {"AAA": rows(*dates, quality="SOURCE_OBSERVED")}, dates)
        stock = result["stocks"][0]
        self.assertEqual(stock["selectionCloseStatus"], "UNAVAILABLE")
        self.assertEqual(stock["horizons"]["D+1"]["status"], "UNAVAILABLE")
        self.assertIsNone(stock["horizons"]["D+1"]["returnPct"])

    def test_missing_measurements_and_invalid_prices_are_retained(self):
        dates = ["2026-10-28", "2026-10-29"]
        price_rows = {"AAA": rows(*dates)}
        price_rows["AAA"]["2026-10-29"]["close"] = 0
        result = build_kiwoom_performance(discovery(("AAA", "BBB")), price_rows, dates)
        self.assertEqual(len(result["stocks"]), 2)
        self.assertEqual(result["stocks"][0]["horizons"]["D+1"]["status"], "UNAVAILABLE")
        self.assertEqual(result["stocks"][1]["horizons"]["D+1"]["status"], "UNAVAILABLE")
        self.assertEqual(result["stocks"][1]["horizons"]["D+1"]["reason"], "SELECTION_CLOSE_UNAVAILABLE")

    def test_unverified_corporate_action_blocks_returns(self):
        dates = ["2026-10-28", "2026-10-29"]
        data = rows(*dates)
        data["2026-10-28"]["corporateActionStatus"] = "UNVERIFIED"
        result = build_kiwoom_performance(discovery(), {"AAA": data}, dates)
        self.assertEqual(result["stocks"][0]["selectionCloseReason"], "CORPORATE_ACTION_UNVERIFIED")

    def test_zero_candidates_is_explicit(self):
        result = build_kiwoom_performance(discovery(()), {}, [])
        self.assertEqual(result["status"], "AVAILABLE_EMPTY")
        self.assertEqual(result["candidateCount"], 0)


if __name__ == "__main__":
    unittest.main()
