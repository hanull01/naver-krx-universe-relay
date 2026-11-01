import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.build_full_market_derivatives import (
    FullMarketDerivativeError,
    build_derivatives,
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

    def test_forbidden_20261030_row_fails(self):
        path = self._write_rows(count=1)
        row = json.loads(path.read_text().splitlines()[0])
        row["date"] = "20261030"
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        with self.assertRaises(FullMarketDerivativeError):
            build_derivatives(self.canonical, self.universe, self.output)


if __name__ == "__main__":
    unittest.main()
