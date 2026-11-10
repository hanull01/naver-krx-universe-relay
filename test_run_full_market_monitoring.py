import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from tools import run_full_market_monitoring as runner


KST = ZoneInfo("Asia/Seoul")


def open_day(_date):
    return True, "KRX_OPEN"


class IntradayMonitoringTests(unittest.TestCase):
    def snapshot(self):
        return {
            "status": "SUCCESS",
            "collectionStatus": "SUCCESS",
            "generatedAt": "2026-10-28T10:07:03+09:00",
            "expectedCount": 2,
            "coverageCount": 2,
            "count": 2,
            "stocks": [
                {"code": "000001", "snapshotGeneratedAt": "2026-10-28T10:07:00+09:00"},
                {"code": "000002", "snapshotGeneratedAt": "2026-10-28T10:07:00+09:00"},
            ],
        }

    def test_current_snapshot_date_evidence_has_no_final_lifecycle_field(self):
        result = runner.intraday_trading_date_evidence(
            self.snapshot(), datetime(2026, 10, 28, 10, 7, tzinfo=KST), open_day
        )
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["tradingDate"], "20261028")
        self.assertEqual(result["evidenceClass"], "INTRADAY_SESSION")
        self.assertNotIn("finalConfirmed", result)

    def test_missing_krx_input_fails_explicitly_without_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            result = runner.run(
                now=datetime(2026, 10, 28, 10, 7, tzinfo=KST),
                output_dir=Path(directory),
                krx_input_dir=Path(directory) / "missing-input",
            )
        self.assertEqual(result, {
            "status": "NOT_PUBLISHED",
            "reason": "KRX_INPUT_UNAVAILABLE",
            "missing": [
                "technicals.json", "states.json", "group-states.json",
                "report-contract-projection.json", "krx-resolution.json",
            ],
        })


if __name__ == "__main__":
    unittest.main()
