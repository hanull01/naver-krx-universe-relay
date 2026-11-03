import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from test_market_dashboard_renderer import report_fixture
from tools.render_current_observation import render_current_observation


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
            "finalConfirmed": False,
        }
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
            self.assertTrue(manifest["finalPending"])
            self.assertNotIn("contractHtmlAllowed", manifest)
            for field, filename in (("markdownSha256", "report.md"), ("htmlSha256", "report.html")):
                self.assertEqual(
                    manifest[field],
                    hashlib.sha256(
                        (root / "current-observation" / "123" / filename).read_bytes()
                    ).hexdigest(),
                )


if __name__ == "__main__":
    unittest.main()
