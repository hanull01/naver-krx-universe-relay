import unittest

from tools.final_daily_history import (moving_averages, monitoring_daily_payload,
                                       monitoring_daily_rows, reconcile_rows, snapshot_rows)


def row(code="000001", assetType="STOCK", **extra):
    result = {"code": code, "assetType": assetType, "openPrice": 10,
              "highPrice": 12, "lowPrice": 9, "currentPrice": 11,
              "accumulatedTradingVolume": 100, "accumulatedTradingValue": 1000,
              "marketStatus": "CLOSE", "tradingSessionType": "AFTER_MARKET",
              "sourceMarketCode": "0", "tradeStopYn": "N", "tradableStatus": "TRADABLE"}
    result.update(extra)
    return result


def snapshot(rows, date="20261019"):
    return {"source": "NAVER_STOCKLIST_CURRENT", "sourceMarket": "KRX",
            "sourceMarketEvidence": {"provider": "NAVER", "requestTradeType": "KRX",
                                      "responseField": "sosok", "markets": ["KOSPI", "KOSDAQ"]},
            "tradingDate": date, "generatedAt": "2026-10-19T20:30:00+09:00",
            "stocks": rows}


class FinalDailyHistoryTests(unittest.TestCase):
    def test_monitoring_payload_is_dry_run_and_requires_exact_stock_coverage(self):
        rows = [row("S"), row("E", "ETF")]
        result = monitoring_daily_payload(snapshot(rows), {"asOfDate": "20261019", "historyStatus": "OK"}, ["S", "E"])
        self.assertEqual(result["status"], "READY")
        self.assertEqual([item["code"] for item in result["rows"]], ["S"])
        self.assertEqual(result["excluded"][0]["assetType"], "ETF")
        self.assertEqual(result["diagnostic"]["networkCalls"], 0)
        self.assertNotIn("write", result)

    def test_monitoring_adapter_reuses_stock_schema_and_excludes_etf_etn(self):
        proposed = snapshot_rows(snapshot([row("S"), row("E", "ETF"), row("N", "ETN")]),
                                 {"asOfDate": "20261019", "historyStatus": "OK"})["rows"]
        result = monitoring_daily_rows(proposed)
        self.assertEqual(result["status"], "READY")
        self.assertEqual([item["code"] for item in result["rows"]], ["S"])
        self.assertEqual({item["assetType"] for item in result["excluded"]}, {"ETF", "ETN"})
        self.assertEqual(result["rows"][0]["open"], 10)
        self.assertEqual(result["rows"][0]["low"], 9)
        self.assertEqual(result["rows"][0]["value"], 1000)

    def test_stock_etf_etn_are_preserved_and_no_network_is_used(self):
        result = snapshot_rows(snapshot([row("S"), row("E", "ETF"), row("N", "ETN")]),
                               {"asOfDate": "20261019", "historyStatus": "OK"})
        self.assertEqual(result["status"], "READY")
        self.assertEqual({item["assetType"] for item in result["rows"]}, {"STOCK", "ETF", "ETN"})
        self.assertEqual(result["diagnostic"]["networkCalls"], 0)

    def test_missing_asset_type_or_value_is_rejected(self):
        missing_type = dict(row()); missing_type.pop("assetType")
        self.assertEqual(snapshot_rows(snapshot([missing_type]))["status"], "REJECTED")
        self.assertEqual(snapshot_rows(snapshot([row(accumulatedTradingValue=None)]))["status"], "REJECTED")

    def test_collector_snapshot_can_use_verified_run_date_but_missing_code_still_blocks(self):
        payload = {"generatedAt": "2026-10-19T20:30:00+09:00", "expectedCount": 2,
                   "stocks": [row("000001")]}
        result = monitoring_daily_payload(payload, {"asOfDate": "20261018", "historyStatus": "OK"},
                                          ["000001", "028080"], target_date="20261019")
        self.assertEqual(result["status"], "WAIT")
        self.assertIn("MISSING_CODES", result["reason"])
        self.assertIn("COVERAGE_MISMATCH", result["reason"])
        self.assertNotIn("TRADING_DATE_MISSING", result["reason"])

    def test_missing_snapshot_date_is_not_replaced_by_execution_date(self):
        payload = {"generatedAt": "2026-10-19T20:30:00+09:00",
                   "stocks": [row("000001")]}
        result = monitoring_daily_payload(payload,
                                          {"asOfDate": "20261018", "historyStatus": "OK"},
                                          ["000001"])
        self.assertEqual(result["status"], "WAIT")
        self.assertIn("TRADING_DATE_MISSING_OR_INCONSISTENT", result["reason"])

    def test_row_level_explicit_local_date_can_drive_final_candidate(self):
        payload = {"source": "NAVER_STOCKLIST_CURRENT", "sourceMarket": "KRX",
                   "sourceMarketEvidence": {"provider": "NAVER", "requestTradeType": "KRX",
                                             "responseField": "sosok", "markets": ["KOSPI", "KOSDAQ"]},
                   "generatedAt": "2026-10-19T20:30:00+09:00",
                   "stocks": [row("000001", localDate="20261019")]}
        result = monitoring_daily_payload(payload,
                                          {"asOfDate": "20261018", "historyStatus": "OK"},
                                          ["000001"])
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["rows"][0]["date"], "20261019")

    def test_complete_reconciliation_pending_uses_observed_active_set(self):
        payload = snapshot([row("000001"), row("000002")])
        payload.update({"status": "COMPLETE_RECONCILIATION_PENDING",
                        "collectionStatus": "SUCCESS", "qualityStatus": "WARNING",
                        "expectedCount": 3})
        result = monitoring_daily_payload(
            payload, {"asOfDate": "20261018", "historyStatus": "OK"},
            ["000001", "000003"], target_date="20261019")
        self.assertEqual(result["status"], "READY")
        self.assertEqual([row["code"] for row in result["rows"]], ["000001", "000002"])
        self.assertIn("UNIVERSE_COMPOSITION_CHANGED", result["diagnostic"]["warnings"])

    def test_ohlc_relationship_is_rejected(self):
        result = snapshot_rows(snapshot([row(highPrice=8)]))
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("HIGH_BELOW_OPEN_OR_CLOSE:000001", result["reason"])

    def test_same_date_same_values_are_noop(self):
        current = snapshot_rows(snapshot([row()]))["rows"]
        self.assertEqual(reconcile_rows(current, current)["status"], "NOOP")

    def test_same_date_changed_values_are_conflict(self):
        current = snapshot_rows(snapshot([row()]))["rows"]
        changed = snapshot_rows(snapshot([row(currentPrice=13, highPrice=14)]))["rows"]
        result = reconcile_rows(current, changed)
        self.assertEqual(result["status"], "CONFLICT")
        self.assertEqual(result["conflicts"], ["000001"])

    def test_new_code_is_ready_without_mutating_input(self):
        current = snapshot_rows(snapshot([row()]))["rows"]
        incoming = snapshot_rows(snapshot([row(), row("000002")]))["rows"]
        result = reconcile_rows(current, incoming)
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["added"], ["000002"])
        self.assertEqual(len(current), 1)

    def test_ma5_20_60_waits_until_enough_history(self):
        rows = [{"date": f"2026-01-{day:02d}", "close": day} for day in range(1, 61)]
        result = moving_averages(rows)
        self.assertEqual(result["ma5"], 58.0)
        self.assertEqual(result["ma20"], 50.5)
        self.assertEqual(result["ma60"], 30.5)
        self.assertIsNone(moving_averages(rows[:4])["ma5"])


if __name__ == "__main__":
    unittest.main()
