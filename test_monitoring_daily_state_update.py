import importlib.util
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
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

    def test_provisional_write_keeps_baseline_unchanged(self):
        baseline_path = self.root / "baseline.json"
        baseline_path.write_text('{"asOfDate":"20260101","historyStatus":"OK"}\n', encoding="utf-8")
        state = update.load_state(self.state_dir, self.universe)
        incoming = update.normalize_daily_rows(self._rows("20260102"), "20260102")
        with patch.object(update, "baseline_from_state", return_value={"asOfDate": "20260102"}):
            result = update.update_from_rows(state, incoming, self.state_dir, "20260102",
                                             self.universe, baseline_path, provisional=True)
        self.assertEqual("SUCCESS", result["status"])
        self.assertEqual("PROVISIONAL", json.loads((self.state_dir / "kospi.json").read_text())["publicationStatus"])
        self.assertEqual('{"asOfDate":"20260101","historyStatus":"OK"}\n', baseline_path.read_text())

    def test_missing_and_duplicate_are_blocked(self):
        state = update.load_state(self.state_dir, self.universe)
        with self.assertRaisesRegex(ValueError, "duplicate daily code"):
            update.normalize_daily_rows({"rows": [self._rows()["rows"][0], self._rows()["rows"][0]]}, "2026-01-01")
        with self.assertRaisesRegex(ValueError, "authoritative code mismatch"):
            update.update_state(state, {"000001": update.normalize_daily_rows(self._rows(), "2026-01-01")["000001"]}, "2026-01-01", self.universe)

    def test_source_observed_allows_missing_code_without_synthesizing_row(self):
        state = update.load_state(self.state_dir, self.universe)
        incoming = update.normalize_daily_rows({"rows": [self._rows()["rows"][0]]}, "2026-01-01")
        result, changed = update.update_state(state, incoming, "2026-01-01", self.universe,
                                              allow_source_observed=True)
        self.assertTrue(changed)
        self.assertEqual(result["KOSDAQ"]["sourceMissingCodes"], ["000002"])
        self.assertNotEqual(update.decode_stock(result["KOSDAQ"]["stocks"][0])[-1]["date"], "20260101")

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

    def test_source_observed_collection_preserves_normal_empty_and_feature_invalid_rows(self):
        def fetcher(code, _start, _end):
            if code == "000002":
                return {"raw": b'{"priceInfos": []}'}
            return {"raw": json.dumps({"priceInfos": [{
                "localDate": "20260101", "openPrice": 10, "highPrice": 8,
                "lowPrice": 9, "closePrice": 11, "accumulatedTradingVolume": 0,
            }]}).encode()}

        result = update.collect_today_rows_source_observed(
            "2026-01-01", self.universe, fetcher=fetcher, retries=1, retry_sleep=0)
        self.assertEqual("SUCCESS", result["status"])
        self.assertEqual(["000002"], result["sourceMissingCodes"])
        self.assertEqual("NORMAL_EMPTY_DAILY", result["sourceMissingReasons"]["000002"])
        self.assertFalse(result["rows"]["000001"]["ohlcValid"])
        self.assertTrue(result["rows"]["000001"]["source_valid"])
        self.assertEqual("OBSERVED", result["rows"]["000001"]["source_status"])

    def test_daily_input_source_observed_requires_explicit_missing_reason(self):
        daily_input = self.root / "daily-input.json"
        daily_input.write_text(json.dumps({"rows": [self._rows()["rows"][0]]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SOURCE_OBSERVED_MISSING_REASONS_REQUIRED"):
            update.update_from_file(
                self.state_dir, daily_input, "2026-01-01", self.universe,
                self.root / "baseline.json", no_write=True, allow_source_observed=True,
            )
    def test_source_observed_collection_blocks_api_failure(self):
        result = update.collect_today_rows_source_observed(
            "2026-01-01", self.universe,
            fetcher=lambda *_args: (_ for _ in ()).throw(OSError("offline")),
            retries=1, retry_sleep=0)
        self.assertEqual("FAILED", result["status"])
        self.assertIn("000001", result["errors"])
        self.assertEqual([], result["sourceMissingCodes"])

    def test_source_observed_rejects_error_object_as_normal_empty(self):
        result = update.collect_today_rows_source_observed(
            "2026-01-01", self.universe,
            fetcher=lambda *_args: {"raw": b'{"error":"temporarily unavailable"}'},
            retries=1, retry_sleep=0)
        self.assertEqual("FAILED", result["status"])
        self.assertTrue(all("missing priceInfos" in reason for reason in result["errors"].values()))

    def test_source_observed_rejects_missing_or_non_array_price_infos(self):
        for raw in (b'{}', b'{"priceInfos": null}', b'[]'):
            with self.subTest(raw=raw):
                result = update.collect_today_rows_source_observed(
                    "2026-01-01", self.universe,
                    fetcher=lambda *_args, raw=raw: {"raw": raw},
                    retries=1, retry_sleep=0)
                self.assertEqual("FAILED", result["status"])
                self.assertEqual([], result["sourceMissingCodes"])

    def test_source_observed_rejects_all_normal_empty_responses(self):
        result = update.collect_today_rows_source_observed(
            "2026-01-01", self.universe,
            fetcher=lambda *_args: {"raw": b'{"priceInfos": []}'},
            retries=1, retry_sleep=0)
        self.assertEqual("FAILED", result["status"])
        self.assertEqual("ALL_NORMAL_EMPTY_DAILY", result["errors"]["__all__"])

    def test_source_observed_refresh_builds_final_baseline_without_synthesizing_empty_day(self):
        previous, stock = self._lifecycle_fixture()
        universe = self.root / "source-observed-universe.json"
        universe.write_text(json.dumps(previous), encoding="utf-8")
        state_dir = self.root / "source-observed-state"; state_dir.mkdir()
        state = self._state_for_payload(previous)
        for market, payload in state.items():
            payload["asOfDate"] = "20261007"
            (state_dir / f"{market.lower()}.json").write_text(json.dumps(payload), encoding="utf-8")
        baseline_path = self.root / "source-observed-baseline.json"
        baseline_path.write_text(json.dumps({"asOfDate": "20261007", "historyStatus": "SOURCE_OBSERVED",
            "sourceQualityStatus": "SOURCE_OBSERVED", "authoritativeCount": 3, "count": 3,
            "coveragePct": 100.0, "stocks": []}), encoding="utf-8")
        lifecycle_path = self.root / "source-observed-lifecycle.json"
        stocks = [row for row in previous["assets"] if row.get("assetType") == "STOCK"]
        source = {"stocks": stocks, "metadata": {"failedCount": 0,
            "markets": {market: sum(row["market"] == market for row in stocks)
                        for market in update.MARKETS}}}

        def fetcher(code, _start, _end):
            if code == "000002":
                return {"raw": b'{"priceInfos": []}'}
            return self._daily_response("20261008")

        result = update.update_with_universe_refresh(
            state_dir, universe, baseline_path, lifecycle_path, "2026-10-08", source,
            fetcher=fetcher, retry_sleep=0, allow_source_observed=True,
            market_day_check=lambda _target: (True, "OPEN"),
            enforce_lifecycle_verification=True)
        self.assertEqual("SUCCESS", result["status"])
        self.assertEqual("SOURCE_OBSERVED", result["baseline"]["historyStatus"])
        self.assertEqual(["000002"], result["baseline"]["missingLatestCodes"])
        saved = json.loads((state_dir / "kosdaq.json").read_text(encoding="utf-8"))
        self.assertEqual("SOURCE_OBSERVED", saved["sourceQualityStatus"])
        self.assertEqual(["000002"], saved["sourceMissingCodes"])
        self.assertEqual([], json.loads((state_dir / "kospi.json").read_text(encoding="utf-8"))["sourceMissingCodes"])

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

    def test_closing_window_separates_regular_and_final_close(self):
        midnight = update.closing_window(datetime(2026, 10, 1, 15, 22, tzinfo=timezone.utc))
        self.assertEqual("BEFORE_FINAL_CLOSE", midnight["status"])
        self.assertEqual("2026-10-02", midnight["targetDate"])
        regular_close = update.closing_window(datetime(2026, 10, 2, 15, 30, tzinfo=update.KST))
        before_final = update.closing_window(datetime(2026, 10, 2, 19, 59, 59, tzinfo=update.KST))
        final_close = update.closing_window(datetime(2026, 10, 2, 20, 0, tzinfo=update.KST))
        self.assertFalse(regular_close["eligible"])
        self.assertEqual(regular_close["finalCloseKst"], "20:00")
        self.assertFalse(before_final["eligible"])
        self.assertTrue(final_close["eligible"])
        self.assertEqual(final_close["status"], "AFTER_FINAL_CLOSE")

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

    def _lifecycle_fixture(self):
        previous = {"metadata": {"counts": {"STOCK": 3}}, "assets": [
            {"code": "000001", "name": "A", "market": "KOSPI", "assetType": "STOCK"},
            {"code": "000003", "name": "C", "market": "KOSPI", "assetType": "STOCK"},
            {"code": "000002", "name": "B", "market": "KOSDAQ", "assetType": "STOCK"},
            {"code": "ETF001", "name": "ETF", "market": "KOSPI", "assetType": "ETF"},
        ]}
        def stock(code, name, market):
            return {"code": code, "name": name, "market": market, "assetType": "STOCK",
                    "source": "naver_stocklist"}
        return previous, stock

    def _state_for_payload(self, payload):
        groups = update.authoritative_groups(payload); state = {}
        for market in update.MARKETS:
            stocks = []
            for index, code in enumerate(sorted(groups[market])):
                row = {"date": "20261007", "close": 100 + index, "high": 110 + index,
                       "volume": 10 + index, "closeValid": True, "ohlcValid": True,
                       "volumeValid": True}
                stocks.append(update.encode_stock(code, [row]))
            state[market] = {"schemaVersion": 1, "format": update.FORMAT, "market": market,
                             "retainedObservations": 301, "publicationStatus": "FINAL", "stocks": stocks}
        return state

    def test_removed_stock_reconciles_without_mutating_remaining_history(self):
        previous, stock = self._lifecycle_fixture()
        source = {"stocks": [stock("000001", "A", "KOSPI"), stock("000002", "B", "KOSDAQ")],
                  "metadata": {"failedCount": 0, "markets": {"KOSPI": 1, "KOSDAQ": 1},
                               "generatedAt": "t"}}
        current, diagnostics = update.refresh_stock_universe(previous, source, "2026-10-08")
        before = self._state_for_payload(previous)
        result = update.reconcile_authoritative_universe(before, previous, current, diagnostics)
        self.assertEqual(["000003"], [item["code"] for item in diagnostics["removed"]])
        self.assertEqual("REMOVED_FROM_AUTHORITATIVE_SOURCE", diagnostics["removed"][0]["reason"])
        self.assertEqual(2, diagnostics["currentCount"])
        self.assertEqual(before["KOSPI"]["stocks"][0], result["KOSPI"]["stocks"][0])
        self.assertEqual({"000001", "000002"}, {s["code"] for m in update.MARKETS for s in result[m]["stocks"]})
        self.assertIn("ETF001", {item["code"] for item in current["assets"]})

    def test_added_and_mixed_universe_create_empty_observed_only_state(self):
        previous, stock = self._lifecycle_fixture()
        added = stock("000004", "D", "KOSDAQ")
        for stocks in (
            [stock("000001", "A", "KOSPI"), stock("000003", "C", "KOSPI"),
             stock("000002", "B", "KOSDAQ"), added],
            [stock("000001", "A", "KOSPI"), stock("000002", "B", "KOSDAQ"), added],
        ):
            source = {"stocks": stocks, "metadata": {"failedCount": 0,
                "markets": {m: sum(row["market"] == m for row in stocks) for m in update.MARKETS}}}
            current, diagnostics = update.refresh_stock_universe(previous, source, "2026-10-08")
            reconciled = update.reconcile_authoritative_universe(
                self._state_for_payload(previous), previous, current, diagnostics)
            self.assertEqual("READY", diagnostics["status"])
            self.assertEqual(["000004"], [item["code"] for item in diagnostics["added"]])
            added_state = next(item for item in reconciled["KOSDAQ"]["stocks"] if item["code"] == "000004")
            self.assertEqual([], update.decode_stock(added_state))

    def test_universe_source_failure_is_rejected_before_transition(self):
        previous, stock = self._lifecycle_fixture()
        source = {"stocks": [stock("000001", "A", "KOSPI"), stock("000002", "B", "KOSDAQ")],
                  "metadata": {"failedCount": 1, "markets": {"KOSPI": 1, "KOSDAQ": 1}}}
        original = json.dumps(previous, sort_keys=True)
        with self.assertRaisesRegex(ValueError, "source incomplete"):
            update.refresh_stock_universe(previous, source, "2026-10-08")
        self.assertEqual(original, json.dumps(previous, sort_keys=True))

    def test_lifecycle_publish_is_atomic_and_exact_against_new_set(self):
        previous, stock = self._lifecycle_fixture()
        universe = self.root / "lifecycle-universe.json"
        universe.write_text(json.dumps(previous), encoding="utf-8")
        state_dir = self.root / "lifecycle-state"; state_dir.mkdir()
        for market, payload in self._state_for_payload(previous).items():
            (state_dir / f"{market.lower()}.json").write_text(json.dumps(payload), encoding="utf-8")
        baseline_path = self.root / "baseline.json"; baseline_path.write_text('{"old":true}', encoding="utf-8")
        lifecycle_path = self.root / "lifecycle.json"
        source = {"stocks": [stock("000001", "A", "KOSPI"), stock("000002", "B", "KOSDAQ")],
                  "metadata": {"failedCount": 0, "markets": {"KOSPI": 1, "KOSDAQ": 1}}}

        def fetcher(_code, _start, _end): return self._daily_response("20261008")
        def fake_baseline(state, target, current, _output):
            assets = update.authoritative_groups(current)
            return {"asOfDate": update.compact_date(target), "authoritativeCount": 2, "count": 2,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": code, "market": market} for market in update.MARKETS for code in sorted(assets[market])],
                    "refreshDiagnostics": {}}

        with patch.object(update, "baseline_from_state", side_effect=fake_baseline):
            outcome = update.update_with_universe_refresh(
                state_dir, universe, baseline_path, lifecycle_path, "2026-10-08", source,
                fetcher=fetcher, retry_sleep=0)
        self.assertEqual(2, outcome["coverage"])
        self.assertEqual(2, json.loads(baseline_path.read_text())["authoritativeCount"])
        self.assertEqual({"000001", "000002"}, set().union(*(
            set(update.authoritative_groups(universe)[market]) for market in update.MARKETS)))

    def test_added_stock_publishes_with_only_observed_target_row(self):
        previous, stock = self._lifecycle_fixture()
        universe = self.root / "added-universe.json"; universe.write_text(json.dumps(previous), encoding="utf-8")
        state_dir = self.root / "added-state"; state_dir.mkdir()
        for market, payload in self._state_for_payload(previous).items():
            (state_dir / f"{market.lower()}.json").write_text(json.dumps(payload), encoding="utf-8")
        baseline_path = self.root / "added-baseline.json"; baseline_path.write_text('{"old":true}', encoding="utf-8")
        lifecycle_path = self.root / "added-lifecycle.json"
        stocks = [row for row in previous["assets"] if row.get("assetType") == "STOCK"]
        stocks.append(stock("000004", "D", "KOSDAQ"))
        source = {"stocks": stocks, "metadata": {"failedCount": 0,
                  "markets": {m: sum(row["market"] == m for row in stocks) for m in update.MARKETS}}}
        captured = {}
        def fake_baseline(state, target, current, _output):
            captured["addedRows"] = update.decode_stock(next(
                item for item in state["KOSDAQ"]["stocks"] if item["code"] == "000004"))
            assets = update.authoritative_groups(current)
            rows = [{"code": code, "market": market} for market in update.MARKETS for code in sorted(assets[market])]
            return {"asOfDate": update.compact_date(target), "authoritativeCount": 4, "count": 4,
                    "coveragePct": 100.0, "historyStatus": "OK", "stocks": rows,
                    "refreshDiagnostics": {}}
        with patch.object(update, "baseline_from_state", side_effect=fake_baseline):
            outcome = update.update_with_universe_refresh(
                state_dir, universe, baseline_path, lifecycle_path, "2026-10-08", source,
                fetcher=lambda *_args: self._daily_response("20261008"), retry_sleep=0)
        self.assertEqual(4, outcome["coverage"])
        self.assertEqual(1, len(captured["addedRows"]))
        self.assertEqual("20261008", captured["addedRows"][0]["date"])
        self.assertEqual(4, json.loads(baseline_path.read_text())["count"])
        compact = baseline.baseline_stock(
            {"code": "000004", "name": "D", "market": "KOSDAQ"}, captured["addedRows"])
        self.assertEqual(1, compact["closeState"]["20"]["count"])
        self.assertLess(compact["closeState"]["20"]["count"], 19)

    def test_same_day_final_generation_is_noop_without_daily_fetch(self):
        previous, _stock = self._lifecycle_fixture()
        universe = self.root / "noop-universe.json"; universe.write_text(json.dumps(previous), encoding="utf-8")
        state_dir = self.root / "noop-state"; state_dir.mkdir()
        state = self._state_for_payload(previous)
        for market, payload in state.items():
            payload["asOfDate"] = "20261008"
            (state_dir / f"{market.lower()}.json").write_text(json.dumps(payload), encoding="utf-8")
        codes = [row["code"] for row in previous["assets"] if row.get("assetType") == "STOCK"]
        baseline_path = self.root / "noop-baseline.json"
        baseline_path.write_text(json.dumps({"asOfDate": "20261008", "authoritativeCount": 3,
            "count": 3, "coveragePct": 100.0, "historyStatus": "OK",
            "stocks": [{"code": code} for code in codes]}), encoding="utf-8")
        calls = Counter()
        def forbidden_fetch(code, *_args): calls[code] += 1; raise AssertionError("must not fetch")
        source = {"stocks": [row for row in previous["assets"] if row.get("assetType") == "STOCK"],
                  "metadata": {"failedCount": 0, "markets": {"KOSPI": 2, "KOSDAQ": 1}}}
        outcome = update.update_with_universe_refresh(
            state_dir, universe, baseline_path, self.root / "noop-lifecycle.json",
            "2026-10-08", source, fetcher=forbidden_fetch, retry_sleep=0)
        self.assertFalse(outcome["changed"])
        self.assertEqual({}, dict(calls))

    def test_refresh_reconciles_state_membership_drift_before_today_collection(self):
        previous, stock = self._lifecycle_fixture()
        universe = self.root / "drift-universe.json"; universe.write_text(json.dumps(previous), encoding="utf-8")
        state_dir = self.root / "drift-state"; state_dir.mkdir()
        state = self._state_for_payload(previous)
        # Simulate the production shape: the authoritative universe already
        # moved on, while rolling state still contains the prior membership.
        state["KOSPI"]["stocks"] = [s for s in state["KOSPI"]["stocks"] if s["code"] != "000003"]
        state["KOSDAQ"]["stocks"].append(update.encode_stock("000004", []))
        for market, payload in state.items():
            payload["asOfDate"] = "20261007"
            (state_dir / f"{market.lower()}.json").write_text(json.dumps(payload), encoding="utf-8")
        baseline_path = self.root / "drift-baseline.json"; baseline_path.write_text("{}", encoding="utf-8")
        lifecycle_path = self.root / "drift-lifecycle.json"
        stocks = [stock("000001", "A", "KOSPI"), stock("000002", "B", "KOSDAQ"),
                  stock("000004", "D", "KOSDAQ")]
        source = {"stocks": stocks, "metadata": {"failedCount": 0,
                  "markets": {"KOSPI": 1, "KOSDAQ": 2}}}
        def fake_baseline(state_value, target, current, _output):
            groups = update.authoritative_groups(current)
            rows = [{"code": code, "market": market} for market in update.MARKETS for code in sorted(groups[market])]
            return {"asOfDate": update.compact_date(target), "authoritativeCount": 3, "count": 3,
                    "coveragePct": 100.0, "historyStatus": "OK", "stocks": rows,
                    "refreshDiagnostics": {}}
        with patch.object(update, "baseline_from_state", side_effect=fake_baseline):
            result = update.update_with_universe_refresh(
                state_dir, universe, baseline_path, lifecycle_path, "2026-10-08",
                source, fetcher=lambda *_args: self._daily_response("20261008"), retry_sleep=0)
        self.assertEqual(3, result["coverage"])
        self.assertEqual({"000001", "000002", "000004"},
                         {s["code"] for market in update.MARKETS for s in
                          json.loads((state_dir / f"{market.lower()}.json").read_text())["stocks"]})

    def test_lifecycle_validation_failure_leaves_every_artifact_unchanged(self):
        previous, stock = self._lifecycle_fixture()
        universe = self.root / "atomic-universe.json"; universe.write_text(json.dumps(previous), encoding="utf-8")
        state_dir = self.root / "atomic-state"; state_dir.mkdir()
        for market, payload in self._state_for_payload(previous).items():
            (state_dir / f"{market.lower()}.json").write_text(json.dumps(payload), encoding="utf-8")
        baseline_path = self.root / "atomic-baseline.json"; baseline_path.write_text('{"old":true}', encoding="utf-8")
        lifecycle_path = self.root / "atomic-lifecycle.json"
        before = {path: path.read_bytes() for path in (universe, baseline_path, state_dir / "kospi.json", state_dir / "kosdaq.json")}
        source = {"stocks": [stock("000001", "A", "KOSPI"), stock("000002", "B", "KOSDAQ")],
                  "metadata": {"failedCount": 0, "markets": {"KOSPI": 1, "KOSDAQ": 1}}}
        with self.assertRaises(update.TodayRowsNotReady):
            update.update_with_universe_refresh(
                state_dir, universe, baseline_path, lifecycle_path, "2026-10-08", source,
                fetcher=lambda *_args: {"raw": b'{"priceInfos":[]}'}, retries=1, retry_sleep=0)
        self.assertFalse(lifecycle_path.exists())
        self.assertTrue(all(path.read_bytes() == content for path, content in before.items()))

    def test_refresh_path_uses_complete_naver_set_without_legal_delisting_claim(self):
        previous, stock = self._lifecycle_fixture()
        universe = self.root / "verified-universe.json"; universe.write_text(json.dumps(previous), encoding="utf-8")
        state_dir = self.root / "verified-state"; state_dir.mkdir()
        for market, payload in self._state_for_payload(previous).items():
            (state_dir / f"{market.lower()}.json").write_text(json.dumps(payload), encoding="utf-8")
        baseline_path = self.root / "verified-baseline.json"; baseline_path.write_text('{"old":true}', encoding="utf-8")
        lifecycle_path = self.root / "verified-lifecycle.json"
        before = {p: p.read_bytes() for p in (universe, baseline_path, state_dir / "kospi.json", state_dir / "kosdaq.json")}
        source = {"stocks": [stock("000001", "A", "KOSPI"), stock("000002", "B", "KOSDAQ")],
                  "metadata": {"failedCount": 0, "markets": {"KOSPI": 1, "KOSDAQ": 1}}}
        with patch.object(update, "baseline_from_state", return_value={"asOfDate":"20261008", "authoritativeCount":2, "count":2, "coveragePct":100.0, "historyStatus":"OK", "stocks":[{"code":"000001"},{"code":"000002"}]}):
            result = update.update_with_universe_refresh(
                state_dir, universe, baseline_path, lifecycle_path, "2026-10-08", source,
                enforce_lifecycle_verification=True, no_write=True,
                fetcher=lambda *_args: self._daily_response("20261008"), retry_sleep=0)
        self.assertEqual("SUCCESS", result["status"])
        self.assertEqual("NAVER_OBSERVED", result["lifecycle"]["lifecycle"]["status"])
        self.assertTrue(all(p.read_bytes() == value for p, value in before.items()))

    def test_observed_snapshot_reconciles_active_set_without_refetching_daily_rows(self):
        previous, stock = self._lifecycle_fixture()
        universe = self.root / "snapshot-universe.json"; universe.write_text(json.dumps(previous), encoding="utf-8")
        state_dir = self.root / "snapshot-state"; state_dir.mkdir()
        for market, payload in self._state_for_payload(previous).items():
            (state_dir / f"{market.lower()}.json").write_text(json.dumps(payload), encoding="utf-8")
        baseline_path = self.root / "snapshot-baseline.json"; baseline_path.write_text("{}", encoding="utf-8")
        lifecycle_path = self.root / "snapshot-lifecycle.json"
        observed = [stock("000001", "A", "KOSPI"), stock("000002", "B", "KOSDAQ"),
                    stock("000004", "D", "KOSDAQ")]
        source = {"stocks": observed, "metadata": {"failedCount": 0,
                  "markets": {market: sum(row["market"] == market for row in observed)
                              for market in update.MARKETS}}}
        daily_rows = [{"code": row["code"], "date": "20261008", "close": 11,
                       "high": 12, "volume": 3} for row in observed]
        def fake_baseline(state, target, current, _output):
            groups = update.authoritative_groups(current)
            return {"asOfDate": update.compact_date(target), "authoritativeCount": 3,
                    "count": 3, "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": code, "market": market}
                               for market in update.MARKETS for code in sorted(groups[market])],
                    "refreshDiagnostics": {}}
        def forbidden_fetch(*_args, **_kwargs):
            raise AssertionError("observed snapshot path must not refetch NAVER daily rows")
        with patch.object(update, "baseline_from_state", side_effect=fake_baseline):
            result = update.update_with_universe_refresh(
                state_dir, universe, baseline_path, lifecycle_path, "2026-10-08", source,
                fetcher=forbidden_fetch, daily_rows=daily_rows,
                enforce_lifecycle_verification=True, retry_sleep=0)
        self.assertEqual("SUCCESS", result["status"])
        lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
        self.assertEqual("NAVER_OBSERVED", lifecycle["lifecycle"]["status"])
        self.assertEqual("UNVERIFIED", lifecycle["lifecycle"]["legalStatus"])
        self.assertEqual({event["type"] for event in lifecycle["lifecycle"]["events"]},
                         {"NAVER_ADDED", "NAVER_REMOVED"})
        archived = json.loads((state_dir / "retired.json").read_text(encoding="utf-8"))
        self.assertEqual("UNVERIFIED", archived["stocks"]["000003"]["legalStatus"])
        self.assertEqual({"000001", "000002", "000004"},
                         {stock["code"] for market in update.MARKETS
                          for stock in json.loads((state_dir / f"{market.lower()}.json").read_text())["stocks"]})

    def test_2764_to_2766_observed_snapshot_is_current_authority(self):
        old_assets = [{"code": f"{index:06d}", "name": f"old-{index}",
                       "market": "KOSPI" if index % 2 else "KOSDAQ", "assetType": "STOCK"}
                      for index in range(1, 2765)]
        new_assets = old_assets + [
            {"code": "0013Y0", "name": "new-a", "market": "KOSDAQ", "assetType": "STOCK"},
            {"code": "0015M0", "name": "new-b", "market": "KOSDAQ", "assetType": "STOCK"},
        ]
        universe = self.root / "full-universe.json"; universe.write_text(
            json.dumps({"assets": old_assets}), encoding="utf-8")
        state_dir = self.root / "full-state"; state_dir.mkdir()
        for market in update.MARKETS:
            stocks = [update.encode_stock(row["code"], [{
                "date": "20261023", "close": 100, "high": 101, "volume": 10,
                "closeValid": True, "ohlcValid": True, "volumeValid": True,
            }]) for row in old_assets if row["market"] == market]
            (state_dir / f"{market.lower()}.json").write_text(json.dumps({
                "schemaVersion": 1, "format": update.FORMAT, "market": market,
                "retainedObservations": update.KEEP, "publicationStatus": "FINAL", "stocks": stocks,
            }), encoding="utf-8")
        baseline_path = self.root / "full-baseline.json"; baseline_path.write_text("{}", encoding="utf-8")
        lifecycle_path = self.root / "full-lifecycle.json"
        source = {"stocks": new_assets, "metadata": {"failedCount": 0,
                  "markets": {market: sum(row["market"] == market for row in new_assets)
                              for market in update.MARKETS}}}
        daily_rows = [{"code": row["code"], "date": "20261026", "close": 101,
                       "high": 102, "volume": 11} for row in new_assets]
        def fake_baseline(state, target, current, _output):
            groups = update.authoritative_groups(current)
            return {"asOfDate": update.compact_date(target), "authoritativeCount": 2766,
                    "count": 2766, "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": code, "market": market}
                               for market in update.MARKETS for code in sorted(groups[market])],
                    "refreshDiagnostics": {}}
        with patch.object(update, "baseline_from_state", side_effect=fake_baseline):
            result = update.update_with_universe_refresh(
                state_dir, universe, baseline_path, lifecycle_path, "2026-10-26", source,
                daily_rows=daily_rows, enforce_lifecycle_verification=True)
        self.assertEqual("SUCCESS", result["status"])
        self.assertEqual(2766, result["coverage"])
        lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))["lifecycle"]
        self.assertEqual({event["type"] for event in lifecycle["events"]}, {"NAVER_ADDED"})
        self.assertEqual("UNVERIFIED", lifecycle["legalStatus"])

    def test_refresh_path_applies_only_explicit_verified_event(self):
        previous, stock = self._lifecycle_fixture()
        universe = self.root / "verified-add-universe.json"; universe.write_text(json.dumps(previous), encoding="utf-8")
        state_dir = self.root / "verified-add-state"; state_dir.mkdir()
        for market, payload in self._state_for_payload(previous).items():
            (state_dir / f"{market.lower()}.json").write_text(json.dumps(payload), encoding="utf-8")
        baseline_path = self.root / "verified-add-baseline.json"; baseline_path.write_text('{"old":true}', encoding="utf-8")
        lifecycle_path = self.root / "verified-add-lifecycle.json"
        added = stock("000004", "D", "KOSDAQ")
        stocks = [row for row in previous["assets"] if row.get("assetType") == "STOCK"] + [added]
        source = {"stocks": stocks, "metadata": {"failedCount": 0,
                  "markets": {m: sum(row["market"] == m for row in stocks) for m in update.MARKETS}}}
        with patch.object(update, "baseline_from_state", return_value={"asOfDate":"20261008", "authoritativeCount":4, "count":4, "coveragePct":100.0, "historyStatus":"OK", "stocks":[{"code": code} for code in ("000001", "000002", "000003", "000004")]}) as baseline_mock:
            result = update.update_with_universe_refresh(
                state_dir, universe, baseline_path, lifecycle_path, "2026-10-08", source,
                fetcher=lambda *_args: self._daily_response("20261008"), retry_sleep=0,
                enforce_lifecycle_verification=True,
                lifecycle_candidates=[{"code":"000004", "deltaType":"NEW_LISTING",
                    "evidence":{"sourceVerified":True, "effectiveDate":"2026-10-08",
                                "sourceUrl":"fixture://listing"}}])
        self.assertEqual("SUCCESS", result["status"])
        self.assertTrue(baseline_mock.called)

    def test_partial_collection_reports_missing_without_marking_complete(self):
        calls = []
        def fetcher(code, _start, _end):
            calls.append(code)
            if code == "000002":
                return {"raw": b'{"priceInfos": []}'}
            return self._daily_response("20261008")
        result = update.collect_today_rows_partial(
            "2026-10-08", self.universe, fetcher=fetcher, retries=1, retry_sleep=0)
        self.assertEqual("PARTIAL", result["status"])
        self.assertEqual(["000002"], result["missingCodes"])
        self.assertEqual(1, result["collectedCount"])
        self.assertEqual(2, len(calls))

    def test_partial_update_does_not_touch_baseline_and_preserves_missing_history(self):
        state = update.load_state(self.state_dir, self.universe)
        incoming = update.normalize_daily_rows({"rows": [{"code": "000001", "date": "20261008",
            "close": 11, "high": 12, "volume": 3}]}, "20261008")
        result = update.update_partial_state(
            state, incoming, "2026-10-08", self.universe,
            errors={"000002": "missing"}, no_write=True)
        self.assertEqual("PARTIAL", result["status"])
        self.assertFalse(result["baselineUpdated"])
        self.assertEqual(1, result["coverage"])
        self.assertEqual(301, len(update.decode_stock(result_state := update.load_state(self.state_dir, self.universe)["KOSDAQ"]["stocks"][0])))

    def test_retired_archive_preserves_rows_and_restores_on_reappearance(self):
        previous, _stock = self._lifecycle_fixture()
        state = self._state_for_payload(previous)
        removed = next(stock for stock in state["KOSDAQ"]["stocks"] if stock["code"] == "000002")
        observed = {"assets": [row for row in previous["assets"] if row["code"] != "000002"]}
        archive = update.build_retired_archive(state, observed, {"schemaVersion": 1, "stocks": {}}, "20261008")
        self.assertEqual(len(update.decode_stock(removed)), len(update.decode_stock(archive["stocks"]["000002"]["stock"])))
        restored = update.restore_retired_states(
            {"KOSPI": state["KOSPI"], "KOSDAQ": {**state["KOSDAQ"], "stocks": []}},
            previous, archive)
        self.assertEqual(len(update.decode_stock(removed)), len(update.decode_stock(next(
            stock for stock in restored["KOSDAQ"]["stocks"] if stock["code"] == "000002"))))
        self.assertEqual("UNVERIFIED", archive["stocks"]["000002"]["legalStatus"])

    def test_real_regression_shape_removes_196490_without_special_case(self):
        previous, stock = self._lifecycle_fixture()
        previous["assets"].append(stock("196490", "디에이테크놀로지", "KOSDAQ"))
        stocks = [row for row in previous["assets"] if row.get("assetType") == "STOCK" and row["code"] != "196490"]
        source = {"stocks": stocks, "metadata": {"failedCount": 0,
                  "markets": {m: sum(row["market"] == m for row in stocks) for m in update.MARKETS}}}
        _current, diagnostics = update.refresh_stock_universe(previous, source, "2026-10-08")
        self.assertEqual(["196490"], [item["code"] for item in diagnostics["removed"]])

    def test_universe_transition_is_deterministic_across_source_order(self):
        previous, stock = self._lifecycle_fixture()
        stocks = [stock("000001", "A", "KOSPI"), stock("000002", "B", "KOSDAQ")]
        metadata = {"failedCount": 0, "markets": {"KOSPI": 1, "KOSDAQ": 1}, "generatedAt": "t"}
        first = update.refresh_stock_universe(previous, {"stocks": stocks, "metadata": metadata}, "2026-10-08")
        second = update.refresh_stock_universe(previous, {"stocks": list(reversed(stocks)), "metadata": metadata}, "2026-10-08")
        self.assertEqual(first, second)

    def test_same_set_universe_refresh_is_metadata_noop(self):
        previous, _stock = self._lifecycle_fixture()
        stocks = [row for row in previous["assets"] if row.get("assetType") == "STOCK"]
        source = {"stocks": list(reversed(stocks)), "metadata": {"failedCount": 0,
                  "markets": {m: sum(row["market"] == m for row in stocks) for m in update.MARKETS},
                  "generatedAt": "new-runtime-time"}}
        current, diagnostics = update.refresh_stock_universe(previous, source, "2026-10-08")
        self.assertEqual(previous, current)
        self.assertEqual([], diagnostics["removed"])
        self.assertEqual([], diagnostics["added"])

    def test_concurrent_collection_2767_calls_each_active_code_once(self):
        assets = [{"code": f"{index:06d}", "name": str(index),
                   "market": "KOSPI" if index % 2 else "KOSDAQ", "assetType": "STOCK"}
                  for index in range(1, 2768)]
        universe = self.root / "active-2767.json"
        universe.write_text(json.dumps({"assets": assets}), encoding="utf-8")
        calls = Counter()
        def fetcher(code, _start, _end):
            calls[code] += 1
            return self._daily_response(close=int(code) + 1)
        started = time.perf_counter()
        rows = update.collect_today_rows("2026-01-01", universe, fetcher=fetcher,
                                         max_workers=4, retry_sleep=0)
        self.performance_elapsed = time.perf_counter() - started
        self.assertEqual(2767, len(rows))
        self.assertEqual(2767, sum(calls.values()))
        self.assertTrue(all(count == 1 for count in calls.values()))

    def test_optional_ohlcv_columns_are_additive_and_v1_remains_readable(self):
        legacy = update.encode_stock("000001", [{"date": "20261017", "close": 10,
            "high": 11, "volume": 100, "closeValid": True, "ohlcValid": True,
            "volumeValid": True}])
        self.assertNotIn("open", legacy)
        self.assertEqual(update.decode_stock(legacy)[0]["close"], 10)
        extended = update.encode_stock("000001", [{"date": "20261018", "open": 9,
            "low": 8, "close": 10, "high": 11, "volume": 100, "value": 1000,
            "closeValid": True, "ohlcValid": True, "volumeValid": True}])
        self.assertEqual(extended["open"], [9])
        self.assertEqual(extended["low"], [8])
        self.assertEqual(extended["value"], [1000])
        self.assertEqual(update.decode_stock(extended)[0]["value"], 1000)


if __name__ == "__main__": unittest.main()
