import json
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path

from tools.monitoring_daily_state_update import TodayRowsNotReady, collect_today_rows


class LocalDateCollectionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.universe = self.root / "universe.json"
        self.universe.write_text(json.dumps({"assets": [
            {"code": "000001", "market": "KOSPI", "assetType": "STOCK"},
            {"code": "000002", "market": "KOSDAQ", "assetType": "STOCK"},
        ]}), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def response(local_date="2026-10-23", close=11, rows=None):
        rows = rows if rows is not None else [{
            "localDate": local_date, "openPrice": 10, "highPrice": 12,
            "lowPrice": 9, "closePrice": close, "accumulatedTradingVolume": 3,
        }]
        return {"raw": json.dumps({"priceInfos": rows}).encode("utf-8")}

    def test_collect_preserves_source_local_date_in_atomic_sidecar(self):
        diagnostic = self.root / "diagnostics" / "trading-session.json"

        def fetcher(_code, _start, _end):
            return self.response("2026-10-23")

        rows = collect_today_rows("2026-10-23", self.universe, fetcher=fetcher,
                                  diagnostic_output=diagnostic, retry_sleep=0)
        # The public collection shape remains normalized to the requested day.
        self.assertEqual("20261023", rows["000001"]["date"])
        saved = json.loads(diagnostic.read_text(encoding="utf-8"))
        self.assertEqual(["000001", "000002"], saved["expectedCodes"])
        self.assertEqual(["000001", "000002"], saved["observedCodes"])
        self.assertEqual({"000001": "2026-10-23", "000002": "2026-10-23"}, saved["observedDates"])
        self.assertEqual("VERIFIED", saved["status"])

    def test_collect_returns_source_ohlcv_and_trading_value_for_final_adapter(self):
        def fetcher(_code, _start, _end):
            return self.response(rows=[{
                "localDate": "2026-10-23", "openPrice": 10, "highPrice": 12,
                "lowPrice": 9, "closePrice": 11, "accumulatedTradingVolume": 3,
                "accumulatedTradingValue": 1000,
            }])

        rows = collect_today_rows("2026-10-23", self.universe, fetcher=fetcher,
                                  retry_sleep=0)
        self.assertEqual(rows["000001"]["date"], "20261023")
        self.assertEqual(rows["000001"]["open"], 10)
        self.assertEqual(rows["000001"]["high"], 12)
        self.assertEqual(rows["000001"]["low"], 9)
        self.assertEqual(rows["000001"]["close"], 11)
        self.assertEqual(rows["000001"]["volume"], 3)
        self.assertEqual(rows["000001"]["value"], 1000)

    def test_observed_2766_set_is_used_without_mutating_previous_2764_universe(self):
        previous = self.root / "previous-universe.json"
        previous_payload = {"assets": [
            {"code": f"{index:06d}", "market": "KOSPI" if index % 2 else "KOSDAQ",
             "assetType": "STOCK"}
            for index in range(2764)
        ]}
        previous.write_text(json.dumps(previous_payload), encoding="utf-8")
        observed = self.root / "observed-universe.json"
        observed_payload = {"assets": previous_payload["assets"] + [
            {"code": "990001", "market": "KOSPI", "assetType": "STOCK"},
            {"code": "990002", "market": "KOSDAQ", "assetType": "STOCK"},
        ]}
        observed.write_text(json.dumps(observed_payload), encoding="utf-8")

        rows = collect_today_rows(
            "2026-10-23", observed,
            fetcher=lambda *_args: self.response("2026-10-23"),
            max_workers=8, retry_sleep=0,
        )
        self.assertEqual(len(rows), 2766)
        self.assertEqual(json.loads(previous.read_text(encoding="utf-8")), previous_payload)

    def test_invalid_ohlcv_is_flagged_and_not_final_quality(self):
        def fetcher(_code, _start, _end):
            return self.response(rows=[{
                "localDate": "2026-10-23", "openPrice": 10, "highPrice": 8,
                "lowPrice": 9, "closePrice": 11, "accumulatedTradingVolume": 3,
            }])

        rows = collect_today_rows("2026-10-23", self.universe, fetcher=fetcher,
                                  retry_sleep=0)
        self.assertFalse(rows["000001"]["ohlcValid"])
        self.assertTrue(rows["000001"]["closeValid"])

    def test_date_mismatch_is_recorded_before_existing_not_ready_failure(self):
        diagnostic = self.root / "diagnostic.json"

        def fetcher(code, _start, _end):
            return self.response("2026-10-22" if code == "000001" else "2026-10-23")

        with self.assertRaises(TodayRowsNotReady) as caught:
            collect_today_rows("2026-10-23", self.universe, fetcher=fetcher,
                               diagnostic_output=diagnostic, retries=1, retry_sleep=0)
        self.assertEqual(("000001",), caught.exception.missing_codes)
        saved = json.loads(diagnostic.read_text(encoding="utf-8"))
        self.assertEqual(["000001"], saved["mismatchCodes"])
        self.assertEqual([], saved["missingCodes"])
        self.assertEqual("LOCAL_DATE_MISMATCH", saved["reason"])

    def test_duplicate_target_rows_are_recorded_and_keep_value_error_contract(self):
        diagnostic = self.root / "diagnostic.json"
        duplicate = [{
            "localDate": "2026-10-23", "openPrice": 10, "highPrice": 12,
            "lowPrice": 9, "closePrice": 11, "accumulatedTradingVolume": 3,
        }] * 2

        with self.assertRaisesRegex(ValueError, "duplicate today daily row"):
            collect_today_rows("2026-10-23", self.universe,
                               fetcher=lambda *_args: self.response(rows=duplicate),
                               diagnostic_output=diagnostic, retries=1, retry_sleep=0)
        saved = json.loads(diagnostic.read_text(encoding="utf-8"))
        self.assertEqual(["000001", "000002"], saved["duplicateCodes"])
        self.assertEqual("DUPLICATE_LOCAL_DATE_OBSERVATION", saved["reason"])

    def test_missing_response_is_recorded_without_changing_not_ready_rule(self):
        diagnostic = self.root / "diagnostic.json"

        with self.assertRaises(TodayRowsNotReady):
            collect_today_rows("2026-10-23", self.universe,
                               fetcher=lambda *_args: self.response(rows=[]),
                               diagnostic_output=diagnostic, retries=1, retry_sleep=0)
        saved = json.loads(diagnostic.read_text(encoding="utf-8"))
        self.assertEqual([], saved["observedCodes"])
        self.assertEqual(["000001", "000002"], saved["missingCodes"])
        self.assertEqual("CODE_SET_MISMATCH", saved["reason"])

    def test_partial_worker_failure_collects_other_observation_without_shared_state(self):
        diagnostic = self.root / "diagnostic.json"
        calls = Counter()

        def fetcher(code, _start, _end):
            calls[code] += 1
            if code == "000001":
                raise OSError("permanent")
            time.sleep(0.01)
            return self.response("2026-10-23")

        with self.assertRaisesRegex(RuntimeError, "000001"):
            collect_today_rows("2026-10-23", self.universe, fetcher=fetcher,
                               diagnostic_output=diagnostic, retries=1,
                               max_workers=2, retry_sleep=0)
        saved = json.loads(diagnostic.read_text(encoding="utf-8"))
        self.assertEqual(["000002"], saved["observedCodes"])
        self.assertEqual(["000001"], saved["missingCodes"])
        self.assertEqual({"000001": 1, "000002": 1}, dict(calls))

    def test_parallel_results_are_sorted_and_observed_dates_are_code_bound(self):
        diagnostic = self.root / "diagnostic.json"

        def fetcher(code, _start, _end):
            time.sleep(0.02 if code == "000001" else 0.001)
            return self.response("2026-10-23", close=int(code))

        rows = collect_today_rows("2026-10-23", self.universe, fetcher=fetcher,
                                  diagnostic_output=diagnostic, max_workers=2,
                                  retry_sleep=0)
        self.assertEqual(["000001", "000002"], list(rows))
        saved = json.loads(diagnostic.read_text(encoding="utf-8"))
        self.assertEqual(["000001", "000002"], saved["observedCodes"])

    def test_diagnostic_write_failure_is_reported_for_successful_collection(self):
        parent = self.root / "not-a-directory"
        parent.write_text("occupied", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            collect_today_rows("2026-10-23", self.universe,
                               fetcher=lambda *_args: self.response(),
                               diagnostic_output=parent / "diagnostic.json",
                               retry_sleep=0)


if __name__ == "__main__":
    unittest.main()
