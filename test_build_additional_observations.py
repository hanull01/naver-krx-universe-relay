import json
import tempfile
import unittest
from pathlib import Path

from tools.build_additional_observations import build_artifact, main


class AdditionalObservationProducerTests(unittest.TestCase):
    def setUp(self):
        self.config = {"industries": [{"id": "i1", "name": "업종1"}], "themes": [{"id": "t1", "name": "테마1"}]}
        self.industry = {"generatedAt": "2026-11-12T10:00:00+09:00", "sourceDate": "20261112", "observationDate": "20261112", "industries": [{"id": "i1", "name": "업종1", "memberCount": 4, "leaders": [{"code": "1", "name": "A"}]}]}
        self.theme = {"generatedAt": "2026-11-12T10:01:00+09:00", "themes": [{"id": "t1", "name": "테마1", "memberCount": 3, "stocks": [{"code": "2", "name": "B", "changeRate": 2.0}, {"code": "3", "name": "C", "changeRate": 1.0}]}]}

    def build(self, **overrides):
        values = dict(config=self.config, industry_input=self.industry, theme_input=self.theme, observation_date="20261112", run_id="run-1", generated_at="2026-11-12T10:02:00+09:00", config_path="config.json", industry_path="industry.json", theme_path="theme.json")
        values.update(overrides)
        return build_artifact(**values)

    def test_configured_inputs_create_available_rows(self):
        result = self.build()
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["counts"]["available"], 2)
        self.assertEqual(result["themes"][0]["leaders"][0]["code"], "2")

    def test_missing_leader_is_unavailable_without_fallback(self):
        self.industry["industries"][0]["leaders"] = []
        result = self.build()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["industries"][0]["failureReason"], "NO_LEADER_DATA")

    def test_stale_input_is_not_used(self):
        self.industry["sourceDate"] = "20261111"
        result = self.build()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["industries"][0]["status"], "UNAVAILABLE")
        self.assertEqual(result["industries"][0]["failureReason"], "INDUSTRY_INPUT_DATE_MISMATCH")

    def test_no_configuration_is_explicit(self):
        result = self.build(config={"industries": [], "themes": []})
        self.assertEqual(result["status"], "PRODUCER_NOT_CONFIGURED")

    def test_missing_configured_input_is_producer_failed(self):
        result = self.build(config={"industries": [], "themes": [{"id": "t1", "name": "테마1"}]}, theme_input=None)
        self.assertEqual(result["status"], "PRODUCER_FAILED")
        self.assertEqual(result["themes"][0]["failureReason"], "THEME_INPUT_MISSING")

    def test_refresh_workflow_runs_and_publishes_the_producer_artifact(self):
        workflow = Path(".github/workflows/refresh.yml").read_text(encoding="utf-8")
        self.assertIn("Build additional observation supply artifact", workflow)
        self.assertIn("python -m tools.build_additional_observations", workflow)
        self.assertIn("data/monitoring/additional-observations.json", workflow)

    def test_cli_writes_status_artifact_without_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(json.dumps({"industries": [], "themes": []}), encoding="utf-8")
            (root / "industry.json").write_text(json.dumps(self.industry), encoding="utf-8")
            (root / "theme.json").write_text(json.dumps(self.theme), encoding="utf-8")
            output = root / "out.json"
            code = main(["--config", str(root / "config.json"), "--industry-input", str(root / "industry.json"), "--theme-input", str(root / "theme.json"), "--output", str(output), "--observation-date", "20261112", "--run-id", "run-cli"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.read_text())["status"], "PRODUCER_NOT_CONFIGURED")


if __name__ == "__main__":
    unittest.main()
