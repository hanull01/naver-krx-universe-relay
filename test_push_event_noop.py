from pathlib import Path
import unittest


ROOT = Path(__file__).parent
WORKFLOWS = (
    ROOT / ".github/workflows/refresh.yml",
)


class PushEventNoopContractTests(unittest.TestCase):
    def test_push_event_has_successful_noop_job(self):
        for workflow_path in WORKFLOWS:
            text = workflow_path.read_text(encoding="utf-8")
            self.assertIn("push-event-noop:", text, workflow_path.name)
            self.assertIn("if: github.event_name == 'push'", text, workflow_path.name)
            self.assertIn("NOOP_PUSH_EVENT", text, workflow_path.name)
            self.assertIn(
                "No market data collection or publication was performed.",
                text,
                workflow_path.name,
            )
            self.assertIn("GITHUB_STEP_SUMMARY", text, workflow_path.name)

    def test_collection_job_is_dispatch_only(self):
        expected_jobs = {
            "refresh.yml": "  refresh:\n    if: github.event_name == 'workflow_dispatch'",
        }
        for workflow_path in WORKFLOWS:
            text = workflow_path.read_text(encoding="utf-8")
            self.assertIn(expected_jobs[workflow_path.name], text)

    def test_dispatch_contract_remains_present(self):
        for workflow_path in WORKFLOWS:
            text = workflow_path.read_text(encoding="utf-8")
            self.assertIn("  workflow_dispatch:", text, workflow_path.name)
            self.assertIn("krx_expected_date:", text, workflow_path.name)
            self.assertIn("krx_ref:", text, workflow_path.name)
            self.assertIn("krx_generation_id:", text, workflow_path.name)
            self.assertIn("publish:", text, workflow_path.name)
            self.assertIn("resolve-krx-generation", text, workflow_path.name)
            self.assertIn("inputs.publish == false", text, workflow_path.name)

    def test_noop_job_does_not_contain_operational_actions(self):
        for workflow_path in WORKFLOWS:
            text = workflow_path.read_text(encoding="utf-8")
            noop = text.split("  push-event-noop:", 1)[1].split("\n  ", 1)[0]
            self.assertNotIn("resolve-krx-generation", noop, workflow_path.name)
            self.assertNotIn("git push", noop, workflow_path.name)
            self.assertNotIn("MARKET_DATA_READY", noop, workflow_path.name)
            self.assertNotIn("naver_market.py", noop, workflow_path.name)


if __name__ == "__main__":
    unittest.main()
