import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_market_dashboard_renderer import report_fixture
from tools.render_current_observation import render_current_observation
from tools.report_lineage import lineage_from_contract


class CurrentObservationReportTests(unittest.TestCase):
    def test_partial_contract_renders_same_generation_markdown_html_and_manifest(self):
        report = report_fixture()
        contract = report["reportContract"]
        contract["status"] = "PARTIAL"
        contract["tradingDateStatus"] = "SOURCE_OBSERVED"
        contract["tradingDateEvidence"] = {
            "tradingDate": "20261029",
            "status": "SOURCE_OBSERVED",
            "evidenceClass": "SOURCE_OBSERVED",

        }
        contract["observationDate"] = "20261030"
        contract["observationDateStatus"] = "VERIFIED"
        contract["observationDateEvidence"] = {
            "tradingDate": "20261030",
            "status": "VERIFIED",
            "evidenceClass": "INTRADAY_SESSION",

        }
        for row in contract["indices"]:
            row["sourceTime"] = "2026-10-30T10:00:00+09:00"
        contract["rendering"] = {
            "status": "RENDERABLE",
            "reason": "PARTIAL_DATA_RETAINED",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_path = root / "latest-report-contract.json"
            contract_path.write_text(
                json.dumps(contract, ensure_ascii=False), encoding="utf-8"
            )
            output = render_current_observation(
                contract_path,
                root / "current-observation" / "123",
                run_id="123",
                head_sha="a" * 40,
            )
            manifest = json.loads(
                (root / "current-observation" / "123" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(output["status"], "SUCCESS")
            self.assertEqual(manifest["session"], "POST_MARKET")
            self.assertEqual(manifest["tradingDate"], "20261030")
            self.assertEqual(manifest["tradingDateStatus"], "SOURCE_OBSERVED")
            self.assertEqual(manifest["observationDateStatus"], "VERIFIED")
            expected_lineage = lineage_from_contract(contract)
            self.assertEqual(manifest["contractId"], expected_lineage["contractId"])
            self.assertEqual(manifest["reportLineage"], expected_lineage)
            self.assertIn(expected_lineage["contractId"], (root / "current-observation" / "123" / "report.md").read_text(encoding="utf-8"))
            self.assertIn(expected_lineage["contractId"], (root / "current-observation" / "123" / "report.html").read_text(encoding="utf-8"))
            self.assertNotIn("finalPending", manifest)
            self.assertNotIn("baselineStale", manifest)
            self.assertNotIn("contractHtmlAllowed", manifest)
            for field, filename in (("markdownSha256", "report.md"), ("htmlSha256", "report.html")):
                self.assertEqual(
                    manifest[field],
                    hashlib.sha256(
                        (root / "current-observation" / "123" / filename).read_bytes()
                    ).hexdigest(),
                )

    def test_module_and_script_entrypoints_work_from_different_working_directories(self):
        contract = report_fixture()["reportContract"]
        contract["status"] = "PARTIAL"
        contract["tradingDateStatus"] = "SOURCE_OBSERVED"
        contract["tradingDateEvidence"] = {
            "tradingDate": "20261029",
            "status": "SOURCE_OBSERVED",
            "evidenceClass": "SOURCE_OBSERVED",

        }
        contract["observationDate"] = "20261030"
        contract["observationDateStatus"] = "VERIFIED"
        contract["observationDateEvidence"] = {
            "tradingDate": "20261030",
            "status": "VERIFIED",
            "evidenceClass": "INTRADAY_SESSION",

        }
        for row in contract["indices"]:
            row["sourceTime"] = "2026-10-30T10:00:00+09:00"
        contract["rendering"] = {"status": "RENDERABLE", "reason": "PARTIAL_DATA_RETAINED"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_path = root / "latest-report-contract.json"
            contract_path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
            cases = (
                ("module", [sys.executable, "-m", "tools.render_current_observation"], Path(__file__).parent),
                ("script", [sys.executable, str(Path(__file__).parent / "tools/render_current_observation.py")], root),
            )
            for label, command, cwd in cases:
                output_dir = root / label
                completed = subprocess.run(
                    command + [
                        "--contract", str(contract_path),
                        "--output-dir", str(output_dir),
                        "--run-id", f"{label}-run",
                        "--head-sha", "a" * 40,
                    ],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
                self.assertTrue((output_dir / "report.md").is_file())
                self.assertTrue((output_dir / "report.html").is_file())
                self.assertEqual(manifest["workflowRunId"], f"{label}-run")
                self.assertEqual(manifest["executionHead"], "a" * 40)
                self.assertEqual(manifest["tradingDate"], "20261030")
                self.assertEqual(manifest["tradingDateStatus"], "SOURCE_OBSERVED")
                self.assertEqual(manifest["observationDateStatus"], "VERIFIED")
                expected_lineage = lineage_from_contract(contract)
                self.assertEqual(manifest["contractId"], expected_lineage["contractId"])
                self.assertEqual(manifest["reportLineage"], expected_lineage)
                self.assertEqual(manifest["markdownSha256"], hashlib.sha256((output_dir / "report.md").read_bytes()).hexdigest())
                self.assertEqual(manifest["htmlSha256"], hashlib.sha256((output_dir / "report.html").read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
