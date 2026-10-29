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
        result = build_kiwoom_performance(discovery(), {"AAA": rows(*dates)}, dates, official_trading_dates=dates)
        stock = result["stocks"][0]
        self.assertEqual(stock["horizons"]["D+1"]["date"], "2026-10-29")
        self.assertEqual(stock["horizons"]["D+3"]["date"], "2026-11-02")
        self.assertEqual(stock["horizons"]["D+5"]["status"], "PENDING")
        self.assertAlmostEqual(stock["horizons"]["D+1"]["returnPct"], 10.0)

    def test_source_observed_is_not_treated_as_confirmed_close(self):
        dates = ["2026-10-28", "2026-10-29"]
        result = build_kiwoom_performance(discovery(), {"AAA": rows(*dates, quality="SOURCE_OBSERVED")}, dates, official_trading_dates=dates)
        stock = result["stocks"][0]
        self.assertEqual(stock["selectionCloseStatus"], "UNAVAILABLE")
        self.assertEqual(stock["horizons"]["D+1"]["status"], "UNAVAILABLE")
        self.assertIsNone(stock["horizons"]["D+1"]["returnPct"])

    def test_missing_measurements_and_invalid_prices_are_retained(self):
        dates = ["2026-10-28", "2026-10-29"]
        price_rows = {"AAA": rows(*dates)}
        price_rows["AAA"]["2026-10-29"]["close"] = 0
        result = build_kiwoom_performance(discovery(("AAA", "BBB")), price_rows, dates, official_trading_dates=dates)
        self.assertEqual(len(result["stocks"]), 2)
        self.assertEqual(result["stocks"][0]["horizons"]["D+1"]["status"], "UNAVAILABLE")
        self.assertEqual(result["stocks"][1]["horizons"]["D+1"]["status"], "UNAVAILABLE")
        self.assertEqual(result["stocks"][1]["horizons"]["D+1"]["reason"], "SELECTION_CLOSE_UNAVAILABLE")

    def test_unverified_corporate_action_blocks_returns(self):
        dates = ["2026-10-28", "2026-10-29"]
        data = rows(*dates)
        data["2026-10-28"]["corporateActionStatus"] = "UNVERIFIED"
        result = build_kiwoom_performance(discovery(), {"AAA": data}, dates, official_trading_dates=dates)
        self.assertEqual(result["stocks"][0]["selectionCloseReason"], "CORPORATE_ACTION_UNVERIFIED")

    def test_zero_candidates_is_explicit(self):
        result = build_kiwoom_performance(discovery(()), {}, [])
        self.assertEqual(result["status"], "AVAILABLE_EMPTY")
        self.assertEqual(result["candidateCount"], 0)

    def test_observed_price_dates_are_not_used_as_official_calendar(self):
        dates = ["2026-10-28", "2026-10-29"]
        result = build_kiwoom_performance(discovery(), {"AAA": rows(*dates)}, dates)
        self.assertEqual(result["status"], "PENDING")
        self.assertEqual(result["stocks"][0]["horizons"]["D+1"]["reason"], "OFFICIAL_KRX_TRADING_DATES_UNAVAILABLE")

    def test_missing_past_measurement_is_unavailable_not_pending(self):
        dates = ["2026-10-28", "2026-10-29"]
        result = build_kiwoom_performance(discovery(), {"AAA": rows("2026-10-28")}, dates, official_trading_dates=dates)
        self.assertEqual(result["stocks"][0]["horizons"]["D+1"]["status"], "UNAVAILABLE")
        self.assertEqual(result["stocks"][0]["horizons"]["D+1"]["reason"], "PRICE_ROW_MISSING")

    def test_future_measurement_is_pending_even_when_calendar_contains_it(self):
        dates = ["2026-10-28", "2026-10-29"]
        result = build_kiwoom_performance(discovery(), {"AAA": rows(*dates)}, dates, official_trading_dates=dates, now_date="2026-10-28")
        self.assertEqual(result["stocks"][0]["horizons"]["D+1"]["status"], "PENDING")
        self.assertEqual(result["stocks"][0]["horizons"]["D+1"]["reason"], "MEASUREMENT_DATE_IN_FUTURE")

    def test_file_header_cannot_promote_observed_row(self):
        dates = ["2026-10-28", "2026-10-29"]
        data = rows(*dates)
        for row in data.values():
            row.pop("qualityStatus")
            row["source_status"] = "OBSERVED"
            row["fileSourceQualityStatus"] = "VALIDATED"
        result = build_kiwoom_performance(discovery(), {"AAA": data}, dates, official_trading_dates=dates)
        self.assertEqual(result["stocks"][0]["selectionCloseReason"], "SOURCE_NOT_VERIFIED_FINAL")

    def test_missing_corporate_action_evidence_blocks_comparison(self):
        dates = ["2026-10-28", "2026-10-29"]
        data = rows(*dates)
        data["2026-10-29"].pop("corporateActionStatus")
        result = build_kiwoom_performance(discovery(), {"AAA": data}, dates, official_trading_dates=dates)
        self.assertEqual(result["stocks"][0]["horizons"]["D+1"]["reason"], "CORPORATE_ACTION_STATUS_UNVERIFIED")

    def test_selection_date_outside_official_calendar_is_explicit(self):
        dates = ["2026-10-29", "2026-10-30"]
        result = build_kiwoom_performance(discovery(), {"AAA": rows("2026-10-28", "2026-10-29")}, dates, official_trading_dates=dates)
        self.assertEqual(result["stocks"][0]["horizons"]["D+1"]["reason"], "SELECTION_DATE_NOT_IN_KRX_TRADING_DATES")


if __name__ == "__main__":
    unittest.main()
