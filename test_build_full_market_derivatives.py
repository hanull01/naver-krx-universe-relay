import hashlib
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tools.build_full_market_derivatives import (
    FullMarketDerivativeError,
    build_derivatives,
    resolve_rolling_window,
)


class BuildFullMarketDerivativesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.canonical = self.root / "canonical" / "daily"
        self.canonical.mkdir(parents=True)
        self.output = self.root / "derived"
        self.universe = self.root / "universe.json"
        self.universe.write_text(json.dumps({
            "stocks": [{"itemCode": "005930", "stockName": "삼성전자", "enabled": True}],
            "watchlists": {"관심종목": ["005930"]},
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_rows(self, code="005930", count=25, start=100.0):
        rows = []
        for index in range(count):
            date = 20261005 + index
            close = start + index
            rows.append({
                "code": code,
                "date": str(date),
                "open": close - 1,
                "high": close + 2,
                "low": close - 2,
                "close": close,
                "volume": 1000 + index,
                "assetType": "STOCK",
                "feature_valid": True,
                "source_valid": True,
            })
        path = self.canonical / f"{code}.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return path

    def _write_history_rows(self, end="20261028", count=120, code="005930"):
        cursor = date.fromisoformat(f"{end[:4]}-{end[4:6]}-{end[6:]}")
        dates = []
        while len(dates) < count:
            if cursor.weekday() < 5:
                dates.append(cursor.strftime("%Y%m%d"))
            cursor -= timedelta(days=1)
        dates.reverse()
        history = self.root / "restored-history" / "STOCK" / f"{code}.jsonl"
        history.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for index, day in enumerate(dates):
            close = 100.0 + index
            rows.append({
                "code": code, "date": day, "open": close - 1, "high": close + 2,
                "low": close - 2, "close": close, "volume": 1000 + index,
                "assetType": "STOCK", "feature_valid": True, "source_valid": True,
            })
        history.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        return history

    def test_builds_compact_derivatives_and_registered_projection(self):
        source = self._write_rows()
        (self.root / "provenance").mkdir()
        (self.root / "provenance" / "coverage.jsonl").write_text(
            json.dumps({"code": "000660", "missingReason": "NO_DAILY_BAR"}) + "\n",
            encoding="utf-8",
        )

        result = build_derivatives(self.canonical, self.universe, self.output)
        technicals = json.loads((self.output / "technicals.json").read_text())
        states = json.loads((self.output / "states.json").read_text())
        registered = json.loads((self.output / "registered-universe-technicals.json").read_text())
        groups = json.loads((self.output / "group-states.json").read_text())
        provenance = json.loads((self.output / "provenance.json").read_text())

        self.assertEqual(result["targetDate"], "20261029")
        self.assertEqual(technicals["targetDate"], "20261029")
        self.assertEqual(technicals["count"], 1)
        self.assertNotIn('"asOfDate":"20261030"', (self.output / "technicals.json").read_text())
        self.assertEqual(technicals["datas"][0]["itemCode"], "005930")
        self.assertIn("ma20", technicals["datas"][0])
        self.assertEqual(states["datas"][0]["itemCode"], "005930")
        self.assertEqual(registered["count"], 1)
        self.assertEqual(groups["groups"][0]["groupName"], "관심종목")
        self.assertFalse(technicals["fallbackUsed"])
        self.assertEqual(technicals["noDailyBar"], ["000660"])

        expected_sha = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(
            provenance["byCode"]["005930"]["sourceCanonicalSha256"], expected_sha
        )
        self.assertEqual(
            provenance["byCode"]["005930"]["sourceAsOfDate"], "20261029"
        )
        self.assertLess(
            (self.output / "technicals.json").stat().st_size,
            source.stat().st_size * 3,
        )
        rolling = json.loads((self.output / "rolling-history-manifest.json").read_text())
        self.assertEqual(rolling["window"], 120)
        self.assertEqual(rolling["byCode"]["005930"]["rowCount"], 25)

    def test_empty_canonical_is_unavailable_without_fallback(self):
        self._write_rows()
        (self.canonical / "000660.jsonl").write_text("", encoding="utf-8")
        build_derivatives(self.canonical, self.universe, self.output)
        technicals = json.loads((self.output / "technicals.json").read_text())
        row = next(row for row in technicals["datas"] if row["itemCode"] == "000660")
        self.assertEqual(row["status"], "UNAVAILABLE")
        self.assertEqual(row["asOfDate"], None)
        self.assertFalse(technicals["fallbackUsed"])

    def test_zero_canonical_files_fails(self):
        with self.assertRaises(FullMarketDerivativeError):
            build_derivatives(self.canonical, self.universe, self.output)

    def test_row_after_dynamic_target_fails(self):
        path = self._write_rows(count=1)
        row = json.loads(path.read_text().splitlines()[0])
        row["date"] = "20261030"
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        with self.assertRaises(FullMarketDerivativeError):
            build_derivatives(self.canonical, self.universe, self.output, target_date="20261029")

    def test_dynamic_target_20261030_is_allowed(self):
        path = self._write_rows(count=1)
        row = json.loads(path.read_text().splitlines()[0])
        row["date"] = "20261030"
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        result = build_derivatives(
            self.canonical, self.universe, self.output, target_date="20261030"
        )
        technicals = json.loads((self.output / "technicals.json").read_text())
        self.assertEqual(result["targetDate"], "20261030")
        self.assertEqual(technicals["targetDate"], "20261030")
        self.assertEqual(technicals["datas"][0]["asOfDate"], "20261030")
        self.assertEqual(technicals["date20261030Rows"], 1)

    def test_restored_history_after_target_fails(self):
        history = self._write_history_rows(count=1)
        row = json.loads(history.read_text().splitlines()[0])
        row["date"] = "20261030"
        history.write_text(json.dumps(row) + "\n", encoding="utf-8")
        with self.assertRaises(FullMarketDerivativeError):
            build_derivatives(
                self.canonical,
                self.universe,
                self.output,
                history_dir=history.parent.parent,
                target_date="20261029",
            )

    def test_restored_history_and_new_bar_keep_rolling_window_and_enable_ma60(self):
        history = self._write_history_rows()
        current = self._write_rows(count=1, start=500.0)
        row = json.loads(current.read_text().splitlines()[0])
        row["date"] = "20261029"
        current.write_text(json.dumps(row) + "\n", encoding="utf-8")

        build_derivatives(
            self.canonical,
            self.universe,
            self.output,
            history_dir=history.parent.parent,
        )
        technicals = json.loads((self.output / "technicals.json").read_text())
        technical = technicals["datas"][0]
        rolling = json.loads((self.output / "rolling-history-manifest.json").read_text())
        rolling_file = self.output / rolling["byCode"]["005930"]["path"]
        rolling_rows = [json.loads(line) for line in rolling_file.read_text().splitlines()]

        self.assertEqual(technical["asOfDate"], "20261029")
        self.assertEqual(technical["historyCount"], 120)
        self.assertIsNotNone(technical["ma20"])
        self.assertIsNotNone(technical["ma60"])
        self.assertEqual(rolling["byCode"]["005930"]["rowCount"], 120)
        self.assertEqual(rolling_rows[-1]["date"], "20261029")
        self.assertNotEqual(rolling_rows[0]["date"], "20260101")
        self.assertEqual(rolling["byCode"]["005930"]["sourceKind"], "MERGED_CURRENT_AND_ROLLING_HISTORY")
        self.assertEqual(rolling["byCode"]["005930"]["restoredHistorySha256"], hashlib.sha256(history.read_bytes()).hexdigest())

    def test_rolling_window_bootstrap_incremental_and_gap_fill(self):
        history_dir = self.root / "window-history"
        history_dir.mkdir()
        is_weekday = lambda day: day.weekday() < 5

        bootstrap = resolve_rolling_window(history_dir, "20261029", is_weekday)
        self.assertEqual(bootstrap["mode"], "BOOTSTRAP")
        self.assertEqual(bootstrap["endDate"], "2026-10-29")
        self.assertEqual(bootstrap["historyCountMin"], 0)

        (history_dir / "rolling-history-manifest.json").write_text(json.dumps({
            "byCode": {"005930": {"rowCount": 120, "latest": "20261028"}}
        }), encoding="utf-8")
        incremental = resolve_rolling_window(history_dir, "20261029", is_weekday)
        self.assertEqual(incremental["mode"], "INCREMENTAL")
        self.assertEqual(incremental["startDate"], "2026-10-29")

        (history_dir / "rolling-history-manifest.json").write_text(json.dumps({
            "byCode": {"005930": {"rowCount": 120, "latest": "20261008"}}
        }), encoding="utf-8")
        gap = resolve_rolling_window(history_dir, "20261012", is_weekday)
        self.assertEqual(gap["mode"], "GAP_FILL")
        self.assertEqual(gap["startDate"], "2026-10-09")


if __name__ == "__main__":
    unittest.main()
