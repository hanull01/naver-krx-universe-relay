import importlib.util
import json
import tempfile
import unittest
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
