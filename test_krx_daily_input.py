import json
import tempfile
import unittest
from pathlib import Path

from tools.validate_krx_daily_input import DailyInputError, validate_daily_input


class KrxDailyInputTests(unittest.TestCase):
    def write_fixture(self, root: Path, *, source="KRX_OPEN_API", date="20261103"):
        metadata = {
            "sourceGeneration": "gen_test",
            "targetDate": date,
            "manifestSha256": "a" * 64,
            "monitoringSha256": "b" * 64,
        }
        payload = {
            "source": source,
            "generationId": "gen_test",
            "targetDate": date,
            "receiptStatus": "VERIFIED",
            "publicationAuthorized": True,
            "historicalIndicatorSource": "KRX",
            "historicalFallbackAllowed": False,
            "manifestSha256": metadata["manifestSha256"],
            "monitoringSha256": metadata["monitoringSha256"],
            "expectedRowCount": 2,
            "rows": [
                {"code": "000001", "date": date, "close": 100, "high": 101, "volume": 10},
                {"code": "000002", "date": date, "close": 200, "high": 201, "volume": 20},
            ],
        }
        input_path = root / "daily-input.json"
        metadata_path = root / "krx-resolution.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        return input_path, metadata_path

    def test_verified_krx_daily_input_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path, metadata_path = self.write_fixture(Path(directory))
            result = validate_daily_input(
                input_path, metadata_path, expected_date="20261103", expected_generation="gen_test"
            )
            self.assertEqual(result["status"], "VERIFIED")
            self.assertEqual(result["rowCount"], 2)
            self.assertEqual(result["historicalSource"], "KRX")
            self.assertFalse(result["historicalFallbackAllowed"])

    def test_unverified_or_incomplete_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path, metadata_path = self.write_fixture(root, source="NAVER")
            with self.assertRaisesRegex(DailyInputError, "SOURCE_NOT_KRX"):
                validate_daily_input(
                    input_path, metadata_path, expected_date="20261103", expected_generation="gen_test"
                )
            input_path, metadata_path = self.write_fixture(root)
            payload = json.loads(input_path.read_text(encoding="utf-8"))
            payload["expectedRowCount"] = 3
            input_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(DailyInputError, "COVERAGE_UNVERIFIED"):
                validate_daily_input(
                    input_path, metadata_path, expected_date="20261103", expected_generation="gen_test"
                )

    def test_naver_historical_path_is_not_present_in_operational_workflow(self):
        workflow = Path(__file__).parent / ".github/workflows/refresh-monitoring-daily.yml"
        text = workflow.read_text(encoding="utf-8")
        self.assertNotIn("--fetch-today", text)
        self.assertIn("--daily-input", text)
        self.assertIn("KRX_ROLLING_STATE_UNAVAILABLE", text)


if __name__ == "__main__":
    unittest.main()
