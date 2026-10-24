import json
import tempfile
import unittest
from pathlib import Path

from tools.trading_session_diagnostic import write_local_date_diagnostic


class TradingSessionDiagnosticTests(unittest.TestCase):
    def test_writes_matching_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local-date.json"
            result = write_local_date_diagnostic(path, "20261023", {"A": "20261023"}, {"A"})
            self.assertEqual(result["status"], "VERIFIED")
            self.assertEqual(json.loads(path.read_text())["source"], "NAVER_LOCAL_DATE")

    def test_code_set_mismatch_is_unverified(self):
        result = write_local_date_diagnostic("/tmp/local-date.json", "20261023", {"A": "20261023"}, {"A", "B"})
        self.assertEqual(result["status"], "UNVERIFIED")
        self.assertEqual(result["reason"], "CODE_SET_MISMATCH")


if __name__ == "__main__":
    unittest.main()
