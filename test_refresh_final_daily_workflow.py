import unittest
from pathlib import Path


WORKFLOW = Path(__file__).parent / ".github" / "workflows" / "refresh.yml"


class RefreshFinalDailyWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.start = cls.text.index("      - name: Prepare final daily monitoring update")
        cls.end = cls.text.index("      - name: Collect market indices", cls.start)
        cls.final_step = cls.text[cls.start:cls.end]
        cls.publish_start = cls.text.index("      - name: Commit refreshed production data")
        cls.publish = cls.text[cls.publish_start:]

    def test_final_history_is_opt_in_and_schedule_gated(self):
        self.assertIn("vars.FINAL_DAILY_HISTORY_WRITE == 'true'", self.final_step)
        self.assertIn("github.event.schedule == '30 11 * * 1-5'", self.final_step)

    def test_opt_in_path_writes_and_no_longer_forces_no_write(self):
        self.assertIn("build_monitoring_daily_state.py update-latest", self.final_step)
        self.assertNotIn("--no-write", self.final_step)
        self.assertIn('echo "write=true" >> "$GITHUB_OUTPUT"', self.final_step)

    def test_only_successful_opt_in_update_is_publishable(self):
        gate = (
            '[ "${{ vars.FINAL_DAILY_HISTORY_WRITE }}" = "true" ] && '
            '[ "${{ steps.final_daily_history.outcome }}" = "success" ]'
        )
        self.assertIn(gate, self.publish)
        self.assertIn("data/monitoring-daily/kospi.json", self.publish)
        self.assertIn("data/monitoring-daily/kosdaq.json", self.publish)
        self.assertIn("data/monitoring-baseline/latest.json", self.publish)


if __name__ == "__main__":
    unittest.main()
