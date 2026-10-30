import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import tools.build_report_contract as builder


class BuildReportContractTests(unittest.TestCase):
    def test_refresh_workflow_runs_contract_as_module(self):
        workflow = Path(__file__).parent / ".github/workflows/refresh.yml"
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("python -m tools.build_report_contract", text)
        self.assertNotIn("python tools/build_report_contract.py", text)

    def test_main_writes_contract_from_report_model(self):
        evidence = {
            "tradingDate": "20261028",
            "status": "VERIFIED",
            "reason": "VERIFIED_MONITORING_DAILY_FINAL_SESSION",
            "finalConfirmed": True,
            "collectionGeneratedAt": "2026-10-28T10:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "latest-report-contract.json"
            with patch.object(builder, "load_json", side_effect=[
                {"generatedAt": evidence["collectionGeneratedAt"], "tradingDateEvidence": evidence},
                {"generatedAt": evidence["collectionGeneratedAt"], "fullMarket": {"tradingDateEvidence": evidence}},
            ]), \
                 patch.object(builder, "build_report", return_value={"reportContract": {"schemaVersion": "market-report-contract-v1", "status": "AVAILABLE", "asOf": "20261028"}}):
                with patch("sys.argv", ["build_report_contract.py", "--output", str(output)]):
                    self.assertEqual(builder.main(), 0)
            self.assertEqual(json.loads(output.read_text())["status"], "AVAILABLE")

    def test_main_writes_atomically_and_reports_size_and_sha(self):
        evidence = {
            "tradingDate": "20261028",
            "status": "VERIFIED",
            "reason": "VERIFIED_MONITORING_DAILY_FINAL_SESSION",
            "finalConfirmed": True,
            "collectionGeneratedAt": "2026-10-28T10:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "latest-report-contract.json"
            with patch.object(builder, "load_json", side_effect=[
                {"generatedAt": evidence["collectionGeneratedAt"], "tradingDateEvidence": evidence},
                {"generatedAt": evidence["collectionGeneratedAt"], "fullMarket": {"tradingDateEvidence": evidence}},
            ]), patch.object(builder, "build_report", return_value={
                "reportContract": {
                    "schemaVersion": "market-report-contract-v1",
                    "status": "AVAILABLE",
                    "asOf": "20261028",
                    "observedAt": evidence["collectionGeneratedAt"],
                }
            }), patch("sys.argv", ["build_report_contract.py", "--output", str(output)]):
                captured = io.StringIO()
                with redirect_stdout(captured):
                    self.assertEqual(builder.main(), 0)
            result = json.loads(captured.getvalue())
            self.assertEqual(result["bytes"], output.stat().st_size)
            self.assertEqual(len(result["sha256"]), 64)
            self.assertEqual(json.loads(output.read_text())["asOf"], "20261028")

    def test_large_2769_stock_diagnostics_are_not_copied_into_contract(self):
        generated = "2026-10-30T00:09:00+09:00"
        evidence = {
            "tradingDate": "20261030",
            "status": "VERIFIED",
            "reason": "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT",
            "evidenceClass": "INTRADAY_SESSION",
            "finalConfirmed": False,
            "collectionGeneratedAt": generated,
            "missingCanonicalCodes": [f"{index:06d}" for index in range(2769)],
            "invalidCanonicalCodes": {f"{index:06d}": "INVALID" for index in range(116)},
        }
        quality = {"generatedAt": generated, "tradingDateEvidence": evidence}
        summary = {"generatedAt": generated, "fullMarket": {"tradingDateEvidence": evidence}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "latest-report-contract.json"
            with patch.object(builder, "load_json", side_effect=[quality, summary]), patch.object(
                builder,
                "build_report",
                return_value={"reportContract": {
                    "schemaVersion": "market-report-contract-v1",
                    "status": "PARTIAL",
                    "asOf": "2026-10-30",
                    "observedAt": generated,
                    "tradingDateStatus": "VERIFIED",
                }},
            ), patch("sys.argv", ["build_report_contract.py", "--output", str(output)]):
                captured = io.StringIO()
                with redirect_stdout(captured):
                    self.assertEqual(builder.main(), 0)
            result = json.loads(captured.getvalue())
            self.assertLess(result["bytes"], builder.MAX_WORK_CONTRACT_BYTES)
            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertNotIn("missingCanonicalCodes", written["tradingDateEvidence"])
            self.assertEqual(
                written["tradingDateEvidence"]["detailCounts"]["missingCanonicalCodes"],
                2769,
            )

    def test_oversized_contract_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "latest-report-contract.json"
            output.write_text('{"status":"AVAILABLE","asOf":"20261027"}\n', encoding="utf-8")
            with patch.object(builder, "load_json", side_effect=[
                {"generatedAt": "2026-10-28T10:00:00+00:00", "tradingDateEvidence": {
                    "tradingDate": "20261028", "status": "VERIFIED",
                    "reason": "VERIFIED_MONITORING_DAILY_FINAL_SESSION",
                }},
                {"generatedAt": "2026-10-28T10:00:00+00:00", "fullMarket": {"tradingDateEvidence": {
                    "tradingDate": "20261028", "status": "VERIFIED",
                    "reason": "VERIFIED_MONITORING_DAILY_FINAL_SESSION",
                }}},
            ]), patch.object(builder, "build_report", return_value={
                "reportContract": {
                    "schemaVersion": "market-report-contract-v1",
                    "status": "AVAILABLE",
                    "asOf": "20261028",
                    "observedAt": "2026-10-28T10:00:00+00:00",
                    "oversized": "x" * builder.MAX_WORK_CONTRACT_BYTES,
                }
            }), patch("sys.argv", ["build_report_contract.py", "--output", str(output)]):
                with self.assertRaises(SystemExit) as error:
                    builder.main()
            self.assertEqual(str(error.exception), "REPORT_CONTRACT_TOO_LARGE")
            self.assertEqual(json.loads(output.read_text())["asOf"], "20261027")

    def test_intraday_evidence_allows_contract_without_final_daily_bar(self):
        evidence = {
            "tradingDate": "20261028",
            "status": "VERIFIED",
            "reason": "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT",
            "evidenceClass": "INTRADAY_SESSION",
            "finalConfirmed": False,
            "collectionGeneratedAt": "2026-10-28T10:09:19.432387+00:00",
        }
        quality = {"generatedAt": evidence["collectionGeneratedAt"], "tradingDateEvidence": evidence}
        summary = {
            "generatedAt": evidence["collectionGeneratedAt"],
            "fullMarket": {"tradingDateEvidence": evidence},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "latest-report-contract.json"
            def load(path):
                return quality if str(path).endswith("latest-quality.json") else summary
            with patch.object(builder, "load_json", side_effect=load), \
                 patch.object(builder, "build_report", return_value={"reportContract": {"schemaVersion": "market-report-contract-v1", "status": "AVAILABLE", "asOf": "20261028"}}), \
                 patch("sys.argv", ["build_report_contract.py", "--output", str(output)]):
                self.assertEqual(builder.main(), 0)
            contract = json.loads(output.read_text())
            self.assertEqual(contract["asOf"], "20261028")
            self.assertFalse(contract["tradingDateEvidence"]["finalConfirmed"])

    def test_post_market_snapshot_evidence_allows_non_final_contract(self):
        evidence = {
            "tradingDate": "20261028",
            "status": "VERIFIED",
            "reason": "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT",
            "evidenceClass": "POST_MARKET_SNAPSHOT",
            "finalConfirmed": False,
            "collectionGeneratedAt": "2026-10-28T11:40:00+00:00",
        }
        quality = {"generatedAt": evidence["collectionGeneratedAt"], "tradingDateEvidence": evidence}
        summary = {
            "generatedAt": evidence["collectionGeneratedAt"],
            "fullMarket": {"tradingDateEvidence": evidence},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "latest-report-contract.json"
            def load(path):
                return quality if str(path).endswith("latest-quality.json") else summary
            with patch.object(builder, "load_json", side_effect=load), \
                 patch.object(builder, "build_report", return_value={"reportContract": {"schemaVersion": "market-report-contract-v1", "status": "AVAILABLE", "asOf": "20261028"}}), \
                 patch("sys.argv", ["build_report_contract.py", "--output", str(output)]):
                self.assertEqual(builder.main(), 0)
            contract = json.loads(output.read_text())
            self.assertEqual(contract["asOf"], "20261028")
            self.assertEqual(contract["tradingDateEvidence"]["evidenceClass"], "POST_MARKET_SNAPSHOT")

    def test_source_observed_final_date_builds_partial_contract_without_html(self):
        evidence = {
            "tradingDate": "20261028",
            "status": "SOURCE_OBSERVED",
            "reason": "SOURCE_OBSERVED_MONITORING_DAILY_FINAL_DATE",
            "evidenceClass": "SOURCE_OBSERVED",
        }
        quality = {"generatedAt": "2026-10-28T11:40:00+00:00", "tradingDateEvidence": evidence}
        summary = {
            "generatedAt": "2026-10-28T11:40:00+00:00",
            "fullMarket": {"tradingDateEvidence": evidence},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "latest-report-contract.json"
            def load(path):
                return quality if str(path).endswith("latest-quality.json") else summary
            with patch.object(builder, "load_json", side_effect=load), \
                 patch.object(builder, "build_report", return_value={
                     "reportContract": {
                         "schemaVersion": "market-report-contract-v1",
                         "status": "PARTIAL",
                         "asOf": "20261028",
                         "observedAt": "2026-10-28T11:40:00+00:00",
                         "tradingDateStatus": "SOURCE_OBSERVED",
                         "rendering": {"htmlAllowed": False},
                     }
                 }), \
                 patch("sys.argv", ["build_report_contract.py", "--output", str(output)]):
                self.assertEqual(builder.main(), 0)
            contract = json.loads(output.read_text())
            self.assertEqual(contract["tradingDateStatus"], "SOURCE_OBSERVED")

    def test_intraday_evidence_requires_matching_collection_timestamp(self):
        evidence = {
            "tradingDate": "20261028",
            "status": "VERIFIED",
            "reason": "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT",
            "evidenceClass": "INTRADAY_SESSION",
            "finalConfirmed": False,
            "collectionGeneratedAt": "2026-10-28T10:08:00+00:00",
        }
        quality = {"generatedAt": "2026-10-28T10:09:00+00:00", "tradingDateEvidence": evidence}
        summary = {"generatedAt": "2026-10-28T10:09:00+00:00", "fullMarket": {"tradingDateEvidence": evidence}}
        with patch.object(builder, "load_json", side_effect=[quality, summary]), \
             patch("sys.argv", ["build_report_contract.py"]):
            with self.assertRaises(SystemExit) as error:
                builder.main()
        self.assertEqual(str(error.exception), "TRADING_DATE_EVIDENCE_STALE")

    def test_missing_date_does_not_replace_existing_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "latest-report-contract.json"
            output.write_text('{"status":"AVAILABLE","asOf":"20261027"}\n', encoding="utf-8")
            with patch.object(builder, "load_json", side_effect=[{}, {}]), \
                 patch("sys.argv", ["build_report_contract.py", "--output", str(output)]):
                with self.assertRaises(SystemExit) as error:
                    builder.main()
            self.assertEqual(str(error.exception), "TRADING_DATE_UNAVAILABLE")
            self.assertEqual(json.loads(output.read_text())["asOf"], "20261027")

    def test_mismatched_same_run_dates_are_rejected(self):
        quality = {"generatedAt": "2026-10-28T10:00:00+00:00", "tradingDateEvidence": {"tradingDate": "20261028", "status": "VERIFIED", "reason": "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT", "evidenceClass": "INTRADAY_SESSION", "finalConfirmed": False}}
        summary = {"generatedAt": "2026-10-28T10:00:00+00:00", "fullMarket": {"tradingDateEvidence": {"tradingDate": "20261027", "status": "VERIFIED", "reason": "VERIFIED_MONITORING_DAILY_FINAL_SESSION"}}}
        with patch.object(builder, "load_json", side_effect=[quality, summary]), \
             patch("sys.argv", ["build_report_contract.py"]):
            with self.assertRaises(SystemExit) as error:
                builder.main()
        self.assertEqual(str(error.exception), "TRADING_DATE_EVIDENCE_MISMATCH")

    def test_contract_model_failure_does_not_replace_existing_contract(self):
        evidence = {
            "tradingDate": "20261028",
            "status": "VERIFIED",
            "reason": "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT",
            "evidenceClass": "INTRADAY_SESSION",
            "finalConfirmed": False,
            "collectionGeneratedAt": "2026-10-28T10:00:00+00:00",
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "latest-report-contract.json"
            output.write_text('{"status":"AVAILABLE","asOf":"20261027"}\n', encoding="utf-8")
            with patch.object(builder, "load_json", side_effect=[
                {"generatedAt": evidence["collectionGeneratedAt"], "tradingDateEvidence": evidence},
                {"generatedAt": evidence["collectionGeneratedAt"], "fullMarket": {"tradingDateEvidence": evidence}},
            ]), patch.object(builder, "build_report", return_value={}), \
                 patch("sys.argv", ["build_report_contract.py", "--output", str(output)]):
                with self.assertRaises(SystemExit) as error:
                    builder.main()
            self.assertEqual(str(error.exception), "REPORT_CONTRACT_UNAVAILABLE")
            self.assertEqual(json.loads(output.read_text())["asOf"], "20261027")

    def test_workflow_records_contract_failure_without_gating_publication(self):
        workflow = Path(__file__).parent / ".github/workflows/refresh.yml"
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("id: report_contract", text)
        self.assertIn("status=FAILED", text)
        self.assertIn("Report contract unavailable; preserving the previous contract artifact.", text)
        self.assertIn("steps.publish.outputs.publication_changed == 'true'", text)

    def test_successful_contract_is_staged_with_market_publication(self):
        workflow = Path(__file__).parent / ".github/workflows/refresh.yml"
        text = workflow.read_text(encoding="utf-8")
        self.assertIn(
            'git add data/monitoring/latest-report-contract.json',
            text,
        )
        self.assertIn(
            '"${{ steps.report_contract.outputs.status }}" = "SUCCESS"',
            text,
        )
