import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import trading_session_diagnostic
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

    def test_atomic_replace_failure_preserves_previous_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local-date.json"
            path.write_text('{"previous":true}\n', encoding="utf-8")
            with patch.object(trading_session_diagnostic.os, "replace", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    write_local_date_diagnostic(path, "20261023", {"A": "20261023"}, {"A"})
            self.assertEqual('{"previous":true}\n', path.read_text(encoding="utf-8"))
            self.assertEqual([], list(Path(directory).glob("local-date.json.*.tmp")))


if __name__ == "__main__":
    unittest.main()
