import json
import tempfile
import unittest
from pathlib import Path

from tools.recover_monitoring_daily_optional import recover


class OptionalRecoveryTests(unittest.TestCase):
    def test_restores_only_matching_valid_rows_and_preserves_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); monitoring = root / "monitoring"; canonical = root / "canonical"; output = root / "out"
            monitoring.mkdir(); canonical.mkdir()
            payload = {"publicationStatus": "FINAL", "stocks": [{
                "code": "000001", "dates": ["20261001", "20261002", "20261003"],
                "close": [10, 20, 30], "high": [11, 21, 31], "volume": [100, 200, 300], "validity": [0, 1, 0]
            }]}
            (monitoring / "kospi.json").write_text(json.dumps(payload), encoding="utf-8")
            (monitoring / "kosdaq.json").write_text(json.dumps({"publicationStatus": "FINAL", "stocks": []}), encoding="utf-8")
            (canonical / "000001.jsonl").write_text(
                '{"date":"20261001","open":9,"low":8,"close":10,"high":11,"volume":100}\n'
                '{"date":"20261002","open":19,"low":18,"close":20,"high":21,"volume":200}\n'
                '{"date":"20261003","open":29,"low":28,"close":999,"high":31,"volume":300}\n', encoding="utf-8")
            before = (monitoring / "kospi.json").read_text()
            report = recover(monitoring, canonical, output)
            result = json.loads((output / "kospi.json").read_text())
            self.assertEqual(report["restored"], 1)
            self.assertEqual(report["conflict"], 1)
            self.assertEqual(report["invalid"], 1)
            self.assertEqual(result["stocks"][0]["open"], [9, None, None])
            self.assertEqual((monitoring / "kospi.json").read_text(), before)


if __name__ == "__main__":
    unittest.main()
