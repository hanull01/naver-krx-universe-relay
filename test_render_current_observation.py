import unittest


class ReportAsOfNormalizationTests(unittest.TestCase):
    def test_accepts_compact_dashed_and_iso_dates(self):
        from tools.report_lineage import normalize_report_as_of
        self.assertEqual(normalize_report_as_of("20261112"), "20261112")
        self.assertEqual(normalize_report_as_of("2026-11-12"), "20261112")
        self.assertEqual(normalize_report_as_of("2026-11-12T21:13:38+09:00"), "20261112")

    def test_rejects_invalid_date(self):
        from tools.report_lineage import normalize_report_as_of
        with self.assertRaisesRegex(ValueError, "REPORT_ASOF_INVALID"):
            normalize_report_as_of("2026-11-13-not-a-date")

    def test_renderer_display_date_is_accepted(self):
        from tools.report_lineage import validate_rendered_as_of
        self.assertEqual(
            validate_rendered_as_of(
                {"reportLineage": {"asOf": "20261112"}},
                {"asOf": "2026-11-12", "lineage": {"asOf": "20261112"}},
                {"asOf": "20261112"},
            ),
            "20261112",
        )

    def test_contract_date_is_preferred_when_manifest_is_display_formatted(self):
        from tools.report_lineage import validate_rendered_as_of
        self.assertEqual(
            validate_rendered_as_of(
                {"reportLineage": {"asOf": "2026-11-12"}},
                {"lineage": {"asOf": "2026-11-12"}},
                {"asOf": "20261112"},
            ),
            "20261112",
        )

    def test_missing_top_level_renderer_date_is_allowed(self):
        from tools.report_lineage import validate_rendered_as_of
        self.assertEqual(
            validate_rendered_as_of(
                {"reportLineage": {"asOf": "20261112"}},
                {"lineage": {"asOf": "20261112"}},
                {"asOf": "20261112"},
            ),
            "20261112",
        )

    def test_mismatched_lineage_or_auxiliary_date_is_rejected(self):
        from tools.report_lineage import validate_rendered_as_of
        with self.assertRaisesRegex(ValueError, "REPORT_AS_OF_MISMATCH"):
            validate_rendered_as_of(
                {"reportLineage": {"asOf": "20261112"}},
                {"lineage": {"asOf": "20261112"}},
                {"asOf": "20261111"},
            )
        with self.assertRaisesRegex(ValueError, "REPORT_RENDERED_LINEAGE_ASOF_MISMATCH"):
            validate_rendered_as_of(
                {"reportLineage": {"asOf": "20261112"}},
                {"lineage": {"asOf": "20261111"}},
                {"asOf": "20261112"},
            )
        with self.assertRaisesRegex(ValueError, "REPORT_RENDER_RESULT_ASOF_MISMATCH"):
            validate_rendered_as_of(
                {"reportLineage": {"asOf": "20261112"}},
                {"asOf": "20261111", "lineage": {"asOf": "20261112"}},
                {"asOf": "20261112"},
            )
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_market_dashboard_renderer import report_fixture
from tools.render_current_observation import render_current_observation
from tools.report_lineage import (
    lineage_from_contract,
    validate_current_observation_publish,
    validate_rendered_as_of,
)


class CurrentObservationReportTests(unittest.TestCase):
    def _publish_fixture(self):
        lineage = {
            "contractId": "mrc-test",
            "asOf": "20261112",
            "current": {"source": "NAVER_CURRENT_SNAPSHOT"},
            "historical": {"source": "KRX"},
        }
        return (
            {"contractId": "mrc-test", "asOf": "20261112", "reportLineage": lineage},
            {"contractId": "mrc-test", "lineage": lineage},
            {"contractId": "mrc-test", "reportLineage": lineage, "reportContractSha256": "sha"},
        )

    def test_publish_validation_allows_user_views_without_internal_lineage(self):
        contract, rendered, manifest = self._publish_fixture()
        validate_current_observation_publish(
            manifest, rendered, contract, "자료 수신됨", "<p>자료 수신됨</p>", "sha"
        )

    def test_publish_validation_rejects_manifest_contract_id_mismatch(self):
        contract, rendered, manifest = self._publish_fixture()
        manifest["contractId"] = "mrc-other"
        with self.assertRaisesRegex(ValueError, "CURRENT_OBSERVATION_CONTRACT_ID_MISMATCH"):
            validate_current_observation_publish(
                manifest, rendered, contract, "자료 수신됨", "<p>자료 수신됨</p>", "sha"
            )

    def test_publish_validation_rejects_contract_digest_mismatch(self):
        contract, rendered, manifest = self._publish_fixture()
        with self.assertRaisesRegex(ValueError, "CURRENT_OBSERVATION_CONTRACT_DIGEST_MISMATCH"):
            validate_current_observation_publish(
                manifest, rendered, contract, "자료 수신됨", "<p>자료 수신됨</p>", "wrong-sha"
            )

    def test_publish_validation_rejects_internal_token_in_user_view(self):
        contract, rendered, manifest = self._publish_fixture()
        with self.assertRaisesRegex(ValueError, "CURRENT_OBSERVATION_INTERNAL_METADATA_RENDERED"):
            validate_current_observation_publish(
                manifest, rendered, contract, "contractId=mrc-test", "<p>자료</p>", "sha"
            )

    def test_manifest_lineage_as_of_is_canonical_without_top_level_rendered_as_of(self):
        manifest = {"reportLineage": {"asOf": "20261112"}}
        rendered = {"lineage": {"asOf": "20261112"}}
        contract = {"asOf": "20261112"}

        self.assertEqual(validate_rendered_as_of(manifest, rendered, contract), "20261112")

    def test_manifest_and_contract_as_of_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "REPORT_AS_OF_MISMATCH"):
            validate_rendered_as_of(
                {"reportLineage": {"asOf": "20261112"}},
                {"lineage": {"asOf": "20261112"}},
                {"asOf": "20261111"},
            )

    def test_optional_top_level_rendered_as_of_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "REPORT_RENDER_RESULT_ASOF_MISMATCH"):
            validate_rendered_as_of(
                {"reportLineage": {"asOf": "20261112"}},
                {"asOf": "20261111", "lineage": {"asOf": "20261112"}},
                {"asOf": "20261112"},
            )

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
            report_md = (root / "current-observation" / "123" / "report.md").read_text(encoding="utf-8")
            report_html = (root / "current-observation" / "123" / "report.html").read_text(encoding="utf-8")
            self.assertNotIn(expected_lineage["contractId"], report_md)
            self.assertNotIn(expected_lineage["contractId"], report_html)
            self.assertNotIn("report-lineage", report_html)
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
