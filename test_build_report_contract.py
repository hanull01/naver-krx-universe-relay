import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tools.build_report_contract as builder


class BuildReportContractTests(unittest.TestCase):
    def test_main_writes_contract_from_report_model(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "latest-report-contract.json"
            with patch.object(builder, "load_json", return_value={"tradingDate": "20261028"}), \
                 patch.object(builder, "build_report", return_value={"reportContract": {"status": "AVAILABLE", "asOf": "20261028"}}):
                with patch("sys.argv", ["build_report_contract.py", "--output", str(output)]):
                    self.assertEqual(builder.main(), 0)
            self.assertEqual(json.loads(output.read_text())["status"], "AVAILABLE")
