import importlib.util
import json
import tempfile
import threading
import time
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / filename)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value)
    return value

update = module("monitoring_daily_state_update", "monitoring_daily_state_update.py")
baseline = module("daily_monitoring_baseline", "daily_monitoring_baseline.py")


class MonitoringDailyStateUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.universe = self.root / "universe.json"
        self.universe.write_text(json.dumps({"assets": [
            {"code": "000001", "name": "A", "market": "KOSPI", "assetType": "STOCK"},
            {"code": "000002", "name": "B", "market": "KOSDAQ", "assetType": "STOCK"},
        ]}), encoding="utf-8")
        self.state_dir = self.root / "state"; self.state_dir.mkdir()
        self._write_state("KOSPI", "000001"); self._write_state("KOSDAQ", "000002")

    def tearDown(self): self.temp.cleanup()

    def _write_state(self, market, code, count=301, status="FINAL"):
        rows = [{"date": f"2025{index // 28 + 1:02d}{index % 28 + 1:02d}", "close": 100 + index,
                 "high": 101 + index, "volume": 10 + index, "closeValid": True,
                 "ohlcValid": True, "volumeValid": True} for index in range(count)]
        # The synthetic fixture uses a monotonically valid sequence rather
        # than pretending all stocks share calendar dates.
        rows = [{**row, "date": f"{20250001 + index:08d}"} for index, row in enumerate(rows)]
        payload = {"schemaVersion": 1, "format": update.FORMAT, "market": market,
                   "retainedObservations": 301, "publicationStatus": status,
                   "stocks": [update.encode_stock(code, rows)]}
        (self.state_dir / f"{market.lower()}.json").write_text(json.dumps(payload), encoding="utf-8")

    def _rows(self, date="20260101", close=500, high=510, volume=30):
        return {"rows": [{"code": "000001", "date": date, "close": close, "high": high, "volume": volume},
                         {"code": "000002", "date": date, "close": close, "high": high, "volume": volume}]}

    def test_append_cap_and_validity_preservation(self):
        state = update.load_state(self.state_dir, self.universe)
        incoming = update.normalize_daily_rows(self._rows(high=None, volume=0), "2026-01-01")
        result, changed = update.update_state(state, incoming, "2026-01-01", self.universe)
        self.assertTrue(changed)
        rows = update.decode_stock(result["KOSPI"]["stocks"][0])
        self.assertEqual(301, len(rows)); self.assertEqual("20260101", rows[-1]["date"])
        self.assertFalse(rows[-1]["ohlcValid"]); self.assertFalse(rows[-1]["volumeValid"])

    def test_same_date_noop_and_final_conflict(self):
        state = update.load_state(self.state_dir, self.universe)
        row = update.decode_stock(state["KOSPI"]["stocks"][0])[-1]
        payload = self._rows(row["date"], row["close"], row["high"], row["volume"])
        same = update.normalize_daily_rows(payload, row["date"])
        _, changed = update.update_state(state, same, row["date"], self.universe)
        self.assertFalse(changed)
        changed_payload = update.normalize_daily_rows(self._rows(row["date"], 999, 1000, 1), row["date"])
        with self.assertRaisesRegex(ValueError, "final daily conflict"):
            update.update_state(state, changed_payload, row["date"], self.universe)

    def test_provisional_can_be_promoted(self):
        self._write_state("KOSPI", "000001", status="PROVISIONAL")
        self._write_state("KOSDAQ", "000002", status="PROVISIONAL")
        state = update.load_state(self.state_dir, self.universe)
        target = update.decode_stock(state["KOSPI"]["stocks"][0])[-1]["date"]
        result, changed = update.update_state(state, update.normalize_daily_rows(self._rows(target, 999, 1000, 1), target), target, self.universe)
        self.assertTrue(changed); self.assertEqual("FINAL", result["KOSPI"]["publicationStatus"])

    def test_missing_and_duplicate_are_blocked(self):
        state = update.load_state(self.state_dir, self.universe)
        with self.assertRaisesRegex(ValueError, "duplicate daily code"):
            update.normalize_daily_rows({"rows": [self._rows()["rows"][0], self._rows()["rows"][0]]}, "2026-01-01")
        with self.assertRaisesRegex(ValueError, "authoritative code mismatch"):
            update.update_state(state, {"000001": update.normalize_daily_rows(self._rows(), "2026-01-01")["000001"]}, "2026-01-01", self.universe)

    def test_collect_today_rows_requires_exact_observed_today_row(self):
        def fetcher(code, _start, _end):
            return {"raw": json.dumps({"priceInfos": [{"localDate": "20260101", "openPrice": 10,
                    "highPrice": 12, "lowPrice": 9, "closePrice": 11, "accumulatedTradingVolume": 3}]}).encode()}
        rows = update.collect_today_rows("2026-01-01", self.universe, fetcher=fetcher)
        self.assertEqual({"000001", "000002"}, set(rows))
        self.assertTrue(rows["000001"]["ohlcValid"])

    def test_collect_today_rows_reports_complete_missing_diagnostic(self):
        def fetcher(_code, _start, _end):
            return {"raw": json.dumps({"priceInfos": []}).encode()}
        with self.assertRaises(update.TodayRowsNotReady) as caught:
            update.collect_today_rows("2026-01-01", self.universe, fetcher=fetcher)
        self.assertEqual(2, caught.exception.diagnostics["missingTodayCount"])
        self.assertEqual(["000001", "000002"], caught.exception.diagnostics["firstMissingCodes"])

    @staticmethod
    def _daily_response(date="20260101", close=11):
        return {"raw": json.dumps({"priceInfos": [{"localDate": date, "openPrice": 10,
                "highPrice": 12, "lowPrice": 9, "closePrice": close,
                "accumulatedTradingVolume": 3}]}).encode()}

    def test_concurrent_collection_is_deterministic_and_fetches_each_code_once(self):
        calls = Counter()
        delays = {"000001": 0.02, "000002": 0.001}

        def fetcher(code, _start, _end):
            calls[code] += 1
            time.sleep(delays[code])
            return self._daily_response(close=int(code))

        first = update.collect_today_rows("2026-01-01", self.universe, fetcher=fetcher,
                                          max_workers=2, retry_sleep=0)
        second = update.collect_today_rows("2026-01-01", self.universe, fetcher=fetcher,
                                           max_workers=2, retry_sleep=0)
        self.assertEqual(first, second)
        self.assertEqual(["000001", "000002"], list(first))
        self.assertEqual({"000001": 2, "000002": 2}, dict(calls))

    def test_concurrent_collection_retries_only_the_failing_code(self):
        calls = Counter()

        def fetcher(code, _start, _end):
            calls[code] += 1
            if code == "000001" and calls[code] == 1:
                raise OSError("temporary")
            return self._daily_response()

        rows = update.collect_today_rows("2026-01-01", self.universe, fetcher=fetcher,
                                         max_workers=2, retries=2, retry_sleep=0)
        self.assertEqual({"000001", "000002"}, set(rows))
        self.assertEqual(2, calls["000001"])
        self.assertEqual(1, calls["000002"])

    def test_concurrent_collection_propagates_permanent_worker_failure(self):
        calls = Counter()

        def fetcher(code, _start, _end):
            calls[code] += 1
            if code == "000001":
                raise OSError("permanent")
            return self._daily_response()

        with self.assertRaisesRegex(RuntimeError, "000001"):
            update.collect_today_rows("2026-01-01", self.universe, fetcher=fetcher,
                                      max_workers=2, retries=2, retry_sleep=0)
        self.assertEqual(2, calls["000001"])
        self.assertEqual(1, calls["000002"])

    def test_concurrent_collection_retries_missing_then_fails_closed(self):
        calls = Counter()

        def fetcher(code, _start, _end):
            calls[code] += 1
            if code == "000001":
                return {"raw": b'{"priceInfos":[]}'}
            return self._daily_response()

        with self.assertRaises(update.TodayRowsNotReady) as caught:
            update.collect_today_rows("2026-01-01", self.universe, fetcher=fetcher,
                                      max_workers=2, retries=2, retry_sleep=0)
        self.assertEqual(("000001",), caught.exception.missing_codes)
        self.assertEqual(2, calls["000001"])
        self.assertEqual(1, calls["000002"])

    def test_concurrent_collection_rejects_duplicate_response_rows(self):
        row = json.loads(self._daily_response()["raw"])["priceInfos"][0]

        def fetcher(_code, _start, _end):
            return {"raw": json.dumps({"priceInfos": [row, row]}).encode()}

        with self.assertRaisesRegex(ValueError, "duplicate today daily row"):
            update.collect_today_rows("2026-01-01", self.universe, fetcher=fetcher,
                                      max_workers=2, retry_sleep=0)

    def test_concurrent_collection_enforces_bounded_worker_count_for_full_universe(self):
        assets = [{"code": f"{index:06d}", "name": str(index),
                   "market": "KOSPI" if index % 2 else "KOSDAQ", "assetType": "STOCK"}
                  for index in range(1, 2769)]
        full_universe = self.root / "full-universe.json"
        full_universe.write_text(json.dumps({"assets": assets}), encoding="utf-8")
        lock = threading.Lock(); active = 0; peak = 0; calls = Counter()

        def fetcher(code, _start, _end):
            nonlocal active, peak
            with lock:
                calls[code] += 1; active += 1; peak = max(peak, active)
            time.sleep(0.0002)
            with lock:
                active -= 1
            return self._daily_response(close=int(code) + 1)

        rows = update.collect_today_rows("2026-01-01", full_universe, fetcher=fetcher,
                                         max_workers=4, retry_sleep=0)
        self.assertEqual(2768, len(rows))
        self.assertEqual(2768, len(calls))
        self.assertTrue(all(count == 1 for count in calls.values()))
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, 4)
        self.assertEqual(sorted(rows), list(rows))

    def test_concurrent_collection_rejects_unbounded_worker_configuration(self):
        with self.assertRaisesRegex(ValueError, "max_workers"):
            update.collect_today_rows("2026-01-01", self.universe,
                                      fetcher=lambda *_args: self._daily_response(),
                                      max_workers=9, retry_sleep=0)

    def test_closing_window_uses_explicit_kst_boundary(self):
        midnight = update.closing_window(datetime(2026, 10, 1, 15, 22, tzinfo=timezone.utc))
        self.assertEqual("BEFORE_REGULAR_CLOSE", midnight["status"])
        self.assertEqual("2026-10-02", midnight["targetDate"])
        before = update.closing_window(datetime(2026, 10, 2, 15, 29, tzinfo=update.KST))
        after = update.closing_window(datetime(2026, 10, 2, 15, 30, tzinfo=update.KST))
        self.assertFalse(before["eligible"])
        self.assertTrue(after["eligible"])

    def test_reader_preserves_rows_and_shared_baseline_calculation(self):
        state = update.load_state(self.state_dir, self.universe)
        reader = update.state_history_reader(state)
        rows = reader("000001")
        self.assertEqual(rows, update.decode_stock(state["KOSPI"]["stocks"][0]))
        asset = {"code": "000001", "name": "A", "market": "KOSPI"}
        direct = baseline.baseline_stock(asset, rows)
        built = baseline.build_baseline(universe_file=self.universe, history_reader=reader, before_date="99999999")
        self.assertEqual(direct, next(item for item in built["stocks"] if item["code"] == "000001"))

    def test_transaction_failure_does_not_publish_first_market(self):
        first = self.root / "first.json"; second = self.root / "missing" / "second.json"
        first.write_text('{"old":true}\n', encoding="utf-8")
        # A file parent for second cannot be created by this fixture after
        # temp creation failure is injected; verify validation/write happens
        # before caller use instead through a malformed unserialisable object.
        with self.assertRaises(TypeError):
            update.transactional_publish({first: {"ok": True}, second: {"bad": {1, 2}}})
        self.assertEqual('{"old":true}\n', first.read_text(encoding="utf-8"))


if __name__ == "__main__": unittest.main()
