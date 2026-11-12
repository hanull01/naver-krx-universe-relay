import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.run_dynamic_universe_v2_shadow import (
    _enrich_persistence,
    _history_context,
    normalize_run_status,
    run_once,
)


class V2RunnerIsolationTests(unittest.TestCase):
    def test_status_normalization_preserves_success_only_for_matching_generation(self):
        success = normalize_run_status(
            {"status": "OK", "generationId": "gen-1"},
            expected_generation_id="gen-1", run_id="run-1",
        )
        mismatch = normalize_run_status(
            {"status": "OK", "generationId": "gen-old"},
            expected_generation_id="gen-new", run_id="run-2",
        )
        failed = normalize_run_status(
            {"status": "FAILED", "errorType": "NameError", "error": "quality"},
            expected_generation_id="gen-new", run_id="run-3",
        )
        self.assertEqual(success["status"], "OK")
        self.assertEqual(success["consumptionStatus"], "CONSUMED")
        self.assertEqual(mismatch["status"], "GENERATION_MISMATCH")
        self.assertEqual(mismatch["consumptionStatus"], "NOT_CONSUMED")
        self.assertEqual(mismatch["failureReason"], "V2_GENERATION_ID_MISMATCH")
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(failed["producerStatus"], "FAILED")
        self.assertEqual(failed["consumptionStatus"], "NOT_CONSUMED")

    def test_success_without_generation_is_not_consumable(self):
        normalized = normalize_run_status(
            {"status": "OK"}, expected_generation_id=None, run_id="run-missing-generation",
        )
        self.assertEqual(normalized["status"], "GENERATION_UNAVAILABLE")
        self.assertEqual(normalized["consumptionStatus"], "NOT_CONSUMED")
        self.assertEqual(normalized["failureReason"], "V2_GENERATION_ID_MISSING")

    def test_failure_is_recorded_without_raising(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/dynamic-universe").mkdir(parents=True)
            outcome = run_once(root, "2026-10-15", "2026-10-15T01:00:00Z")
            self.assertEqual(outcome["status"], "FAILED")
            status = json.loads((root / "data/dynamic-universe/v2-shadow-run-status.json").read_text())
            self.assertEqual(status["status"], "FAILED")
            self.assertIn("runId", status)
            self.assertIn("generatedAt", status)
            self.assertTrue(status["failureReason"])
            self.assertFalse((root / "data/dynamic-universe/latest.json").exists())

    def _write_inputs(self, root: Path) -> None:
        (root / "data/monitoring").mkdir(parents=True)
        (root / "data/dynamic-universe").mkdir(parents=True, exist_ok=True)
        for path, payload in (
            (root / "data/monitoring/latest-industries.json", {"generatedAt": "2026-11-12T01:00:00Z", "industries": []}),
            (root / "data/monitoring/latest-theme-signals.json", {"generatedAt": "2026-11-12T01:00:00Z", "themes": []}),
            (root / "data/dynamic-universe/latest.json", {"status": "AVAILABLE", "sectors": [], "themes": [], "leaders": []}),
        ):
            path.write_text(json.dumps(payload), encoding="utf-8")

    def test_quality_input_is_consumed_without_name_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            (root / "data/monitoring/latest-quality.json").write_text(json.dumps({
                "sourceQualityStatus": "OK",
                "sourceCommit": "abc123",
                "historicalGenerationId": "gen-test",
            }), encoding="utf-8")
            with patch("tools.run_dynamic_universe_v2_shadow.evaluate_snapshot", return_value={"industries": [], "themes": []}), \
                 patch("tools.run_dynamic_universe_v2_shadow.summarize", return_value={}), \
                 patch("tools.run_dynamic_universe_v2_shadow.build_source_provenance", return_value={}), \
                 patch("tools.run_dynamic_universe_v2_shadow.record_current", return_value={"observations": []}):
                outcome = run_once(root, "2026-11-12", "2026-11-12T01:00:00Z")
            self.assertEqual(outcome["status"], "OK")
            evidence = json.loads((root / "data/dynamic-universe/v2-shadow-latest.json").read_text())["inputEvidence"]
            self.assertEqual(evidence["sourceQualityStatus"], "OK")
            self.assertEqual(evidence["generationId"], "gen-test")

    def test_missing_quality_is_explicitly_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            with patch("tools.run_dynamic_universe_v2_shadow.evaluate_snapshot", return_value={"industries": [], "themes": []}), \
                 patch("tools.run_dynamic_universe_v2_shadow.summarize", return_value={}), \
                 patch("tools.run_dynamic_universe_v2_shadow.build_source_provenance", return_value={}), \
                 patch("tools.run_dynamic_universe_v2_shadow.record_current", return_value={"observations": []}):
                outcome = run_once(root, "2026-11-12", "2026-11-12T01:00:00Z")
            self.assertEqual(outcome["status"], "OK")
            evidence = json.loads((root / "data/dynamic-universe/v2-shadow-latest.json").read_text())["inputEvidence"]
            self.assertEqual(evidence["sourceQualityStatus"], "UNAVAILABLE")
            self.assertIsNone(evidence["generationId"])

    def test_failed_run_does_not_reuse_stale_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            latest = root / "data/dynamic-universe/v2-shadow-latest.json"
            latest.write_text(json.dumps({"tradingDate": "2026-11-11", "status": "OK"}), encoding="utf-8")
            before = latest.read_text(encoding="utf-8")
            with patch("tools.run_dynamic_universe_v2_shadow.evaluate_snapshot", side_effect=RuntimeError("quality missing")):
                outcome = run_once(root, "2026-11-12", "2026-11-12T01:00:00Z")
            self.assertEqual(outcome["status"], "FAILED")
            self.assertEqual(latest.read_text(encoding="utf-8"), before)
            status = json.loads((root / "data/dynamic-universe/v2-shadow-run-status.json").read_text())
            self.assertEqual(status["errorType"], "RuntimeError")
            self.assertEqual(status["tradingDate"], "2026-11-12")

    def test_write_failure_restores_latest_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root)
            latest = root / "data/dynamic-universe/v2-shadow-latest.json"
            history = root / "data/dynamic-universe/v2-shadow-history.json"
            prior_latest = json.dumps({"tradingDate": "2026-11-11", "status": "OK"})
            prior_history = json.dumps({"observations": [{"tradingDate": "2026-11-11"}]})
            latest.write_text(prior_latest, encoding="utf-8")
            history.write_text(prior_history, encoding="utf-8")
            with patch("tools.run_dynamic_universe_v2_shadow.evaluate_snapshot", return_value={"industries": [], "themes": []}), \
                 patch("tools.run_dynamic_universe_v2_shadow.summarize", return_value={}), \
                 patch("tools.run_dynamic_universe_v2_shadow.build_source_provenance", return_value={}), \
                 patch("tools.run_dynamic_universe_v2_shadow.record_current", side_effect=RuntimeError("history write")):
                outcome = run_once(root, "2026-11-12", "2026-11-12T01:00:00Z")
            self.assertEqual(outcome["status"], "FAILED")
            self.assertEqual(latest.read_text(encoding="utf-8"), prior_latest)
            self.assertEqual(history.read_text(encoding="utf-8"), prior_history)

    def test_existing_history_is_connected_to_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history_path = root / "data/dynamic-universe/v2-shadow-history.json"
            history_path.parent.mkdir(parents=True)
            history_path.write_text(json.dumps({"observations": [
                {"tradingDate": "2026-10-27", "industries": [{"id": "100"}], "themes": []},
                {"tradingDate": "2026-10-28", "industries": [{"id": "100"}], "themes": []},
            ]}))
            context = _history_context(root, "2026-10-28")
            payload = _enrich_persistence(
                {"industries": [{"id": "100", "persistence": {"availableTradingDays": 1}}]},
                "industries", context)
            row = payload["industries"][0]
            self.assertEqual(row["persistence"]["availableTradingDays"], 2)
            self.assertEqual(row["persistence"]["status"], "AVAILABLE")
            self.assertEqual(row["historyPreviousTradingDate"], "2026-10-27")
