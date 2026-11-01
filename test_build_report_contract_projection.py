import json
import tempfile
import unittest
from pathlib import Path

from tools.build_full_market_derivatives import build_derivatives
from tools.build_report_contract_projection import build_report_contract_projection


class BuildReportContractProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.canonical = self.root / "canonical" / "daily"
        self.canonical.mkdir(parents=True)
        self.universe = self.root / "universe.json"
        self.universe.write_text(json.dumps({
            "stocks": [{"itemCode": "005930", "stockName": "삼성전자", "enabled": True}],
            "watchlists": {"관심종목": ["005930"]},
        }), encoding="utf-8")
        rows = []
        for index in range(25):
            close = 100 + index
            rows.append({
                "code": "005930", "date": str(20261005 + index),
                "open": close - 1, "high": close + 2, "low": close - 2,
                "close": close, "volume": 1000 + index,
                "assetType": "STOCK", "feature_valid": True, "source_valid": True,
            })
        (self.canonical / "005930.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        self.derived = self.root / "derived"
        build_derivatives(self.canonical, self.universe, self.derived)

    def tearDown(self):
        self.tmp.cleanup()

    def test_projection_is_small_and_keeps_artifact_references(self):
        output = self.derived / "report-contract-projection.json"
        result = build_report_contract_projection(self.derived, self.universe, output)
        contract = result["contract"]
        self.assertLessEqual(result["bytes"], 900_000)
        self.assertEqual(contract["asOf"], "20261029")
        self.assertEqual(contract["status"], "AVAILABLE")
        self.assertFalse(contract["htmlAllowed"])
        self.assertEqual(contract["fullMarket"]["totalCount"], 1)
        self.assertEqual(contract["registeredUniverse"]["items"][0]["itemCode"], "005930")
        self.assertEqual(contract["registeredUniverse"]["items"][0]["metrics"]["ma20"], 114.5)
        self.assertEqual(contract["provenance"]["fullMarketRows"], 1)
        self.assertIn("technicals.json", contract["fullMarket"]["artifacts"])
        self.assertIn("rolling-history-manifest.json", contract["fullMarket"]["artifacts"])
        self.assertNotIn('"asOfDate":"20261030"', output.read_text(encoding="utf-8"))

    def test_projection_rejects_mixed_full_market_coverage(self):
        states_path = self.derived / "states.json"
        states = json.loads(states_path.read_text(encoding="utf-8"))
        states["datas"].append(dict(states["datas"][0], itemCode="000660"))
        states_path.write_text(json.dumps(states), encoding="utf-8")
        with self.assertRaises(ValueError):
            build_report_contract_projection(self.derived, self.universe)

    def test_projection_accepts_dynamic_target_20261030(self):
        build_derivatives(
            self.canonical,
            self.universe,
            self.derived,
            target_date="20261030",
        )
        result = build_report_contract_projection(
            self.derived,
            self.universe,
            expected_target_date="20261030",
        )
        self.assertEqual(result["contract"]["asOf"], "20261030")


if __name__ == "__main__":
    unittest.main()
