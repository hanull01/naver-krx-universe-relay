import json
import tempfile
import unittest
from pathlib import Path

from tools.run_dynamic_universe_v2_shadow import (
    _enrich_persistence,
    _history_context,
    normalize_run_status,
    run_once,
    v2_observation_id,
)


def write_json(root: Path, relative: str, payload: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class V2RunnerLineageTests(unittest.TestCase):
    def test_observation_id_is_deterministic_and_does_not_use_krx_generation(self):
        first = v2_observation_id(source_date="20261113", observation_date="20261113", source_hash="a" * 64)
        second = v2_observation_id(source_date="20261113", observation_date="20261113", source_hash="a" * 64)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("v2obs-"))
        self.assertNotIn("gen_", first)

    def test_success_requires_naver_lineage_and_is_consumed(self):
        payload = {"status": "SUCCESS", "v2Lineage": {
            "v2ObservationId": "v2obs-1", "source": "NAVER_CURRENT_SNAPSHOT",
            "sourceDate": "20261113", "observationDate": "20261113",
            "sourceHash": "a" * 64, "selectionPolicyVersion": "policy",
        }}
        result = normalize_run_status(payload, run_id="run-1")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["consumptionStatus"], "CONSUMED")
        self.assertEqual(result["v2Lineage"]["producerRunId"], "run-1")

    def test_incomplete_lineage_is_not_consumed(self):
        result = normalize_run_status({"status": "SUCCESS"}, run_id="run-missing")
        self.assertEqual(result["status"], "INPUT_UNAVAILABLE")
        self.assertEqual(result["consumptionStatus"], "NOT_CONSUMED")

    def test_failure_statuses_are_not_consumed(self):
        for status in ("FAILED", "INPUT_UNAVAILABLE", "INPUT_MISMATCH", "ARTIFACT_INVALID"):
            result = normalize_run_status({"status": status, "failureReason": "fixture"}, run_id=status)
            self.assertEqual(result["status"], status)
            self.assertEqual(result["consumptionStatus"], "NOT_CONSUMED")

    def _write_inputs(self, root: Path, *, date="20261113", source_hash=None):
        (root / "data/monitoring").mkdir(parents=True)
        (root / "data/dynamic-universe").mkdir(parents=True, exist_ok=True)
        breadth = {"source": "NAVER_CURRENT_SNAPSHOT", "sourceDate": date, "observationDate": date,
                   "generatedAt": f"{date[:4]}-{date[4:6]}-{date[6:]}T01:00:00Z"}
        industries = {"source": "NAVER", "sourceDate": date, "observationDate": date,
                      "generatedAt": breadth["generatedAt"], "industries": []}
        themes = {"generatedAt": breadth["generatedAt"], "themes": [{"id": "1", "stocks": [{"temporaryCurrentBarDate": date}]}]}
        if source_hash:
            breadth["sourceHash"] = source_hash
            industries["sourceHash"] = source_hash
            themes["sourceHash"] = source_hash
        for relative, payload in (("data/monitoring/latest-breadth.json", breadth),
                                  ("data/monitoring/latest-industries.json", industries),
                                  ("data/monitoring/latest-theme-signals.json", themes),
                                  ("data/dynamic-universe/latest.json", {"sectors": [], "themes": []})):
            write_json(root, relative, payload)

    def test_run_uses_naver_files_and_writes_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root, source_hash="a" * 64)
            outcome = run_once(root, "20261113", "2026-11-13T01:00:00Z")
            self.assertEqual(outcome["status"], "SUCCESS")
            latest = json.loads((root / "data/dynamic-universe/v2-shadow-latest.json").read_text())
            lineage = latest["v2Lineage"]
            self.assertEqual(lineage["source"], "NAVER_CURRENT_SNAPSHOT")
            self.assertEqual(lineage["sourceDate"], "20261113")
            self.assertEqual(lineage["observationDate"], "20261113")
            self.assertEqual(lineage["sourceHash"], "a" * 64)
            self.assertNotIn("generationId", lineage)
            status = json.loads((root / "data/dynamic-universe/v2-shadow-run-status.json").read_text())
            self.assertEqual(status["consumptionStatus"], "CONSUMED")

    def test_missing_input_does_not_consume_stale_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/dynamic-universe").mkdir(parents=True)
            latest = root / "data/dynamic-universe/v2-shadow-latest.json"
            latest.write_text(json.dumps({"tradingDate": "20261112", "status": "SUCCESS"}), encoding="utf-8")
            outcome = run_once(root, "20261113", "2026-11-13T01:00:00Z")
            self.assertEqual(outcome["status"], "INPUT_UNAVAILABLE")
            self.assertEqual(json.loads(latest.read_text())["tradingDate"], "20261112")

    def test_date_mismatch_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root, date="20261112")
            outcome = run_once(root, "20261113", "2026-11-13T01:00:00Z")
            self.assertEqual(outcome["status"], "INPUT_MISMATCH")
            self.assertEqual(outcome["consumptionStatus"], "NOT_CONSUMED")

    def test_hash_mismatch_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_inputs(root, source_hash="a" * 64)
            path = root / "data/monitoring/latest-theme-signals.json"
            payload = json.loads(path.read_text())
            payload["sourceHash"] = "b" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
            outcome = run_once(root, "20261113", "2026-11-13T01:00:00Z")
            self.assertEqual(outcome["status"], "INPUT_MISMATCH")

    def test_history_is_diagnostic_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history_path = root / "data/dynamic-universe/v2-shadow-history.json"
            history_path.parent.mkdir(parents=True)
            history_path.write_text(json.dumps({"observations": [{"tradingDate": "20261112", "industries": [{"id": "100"}], "themes": []}]}))
            context = _history_context(root, "20261113")
            payload = _enrich_persistence({"industries": [{"id": "100"}]}, "industries", context)
            self.assertEqual(payload["industries"][0]["historyPreviousTradingDate"], "20261112")


if __name__ == "__main__":
    unittest.main()
