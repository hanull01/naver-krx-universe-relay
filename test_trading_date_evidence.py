import json
import tempfile
import unittest
from pathlib import Path

from tools.trading_date_evidence import verify_trading_date


class TradingDateEvidenceTests(unittest.TestCase):
    def write_history(self, root, code, day):
        (Path(root) / f"{code}.jsonl").write_text(
            json.dumps({"date": day, "close": 100, "volume": 10}) + "\n",
            encoding="utf-8",
        )

    def snapshot(self, day="20261023", codes=("A", "B")):
        return {"stocks": [{"code": code, "tradingDate": day} for code in codes]}

    def test_verified_canonical_session_date(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_history(directory, "A", "20261023")
            self.write_history(directory, "B", "20261023")
            result = verify_trading_date(
                directory, ["A", "B"], current_snapshot=self.snapshot()
            )
        self.assertEqual(result["tradingDate"], "20261023")
        self.assertEqual(result["reason"], "VERIFIED_CANONICAL_SESSION")

    def test_missing_or_stale_history_is_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_history(directory, "A", "20261022")
            result = verify_trading_date(
                directory, ["A", "B"], current_snapshot=self.snapshot()
            )
        self.assertIsNone(result["tradingDate"])
        self.assertEqual(result["reason"], "CANONICAL_SESSION_DATE_MISMATCH")

    def test_mixed_explicit_snapshot_dates_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            self.write_history(directory, "A", "20261023")
            result = verify_trading_date(
                directory, ["A"],
                current_snapshot={"stocks": [{"code": "A", "tradingDate": "20261024"}]},
            )
        self.assertIsNone(result["tradingDate"])
        self.assertEqual(result["reason"], "CANONICAL_SESSION_DATE_MISMATCH")


if __name__ == "__main__":
    unittest.main()
