import unittest
from pathlib import Path


WORKFLOW = Path('.github/workflows/refresh-monitoring-daily.yml').read_text()


class BaselineWorkflowOrderTests(unittest.TestCase):
    def test_baseline_refresh_follows_daily_candles_success(self):
        self.assertIn('workflow_run:', WORKFLOW)
        self.assertIn('workflows: ["Refresh daily candles"]', WORKFLOW)
        self.assertIn('types: [completed]', WORKFLOW)
        self.assertNotIn('cron: "45 6 * * 1-5"', WORKFLOW)

    def test_manual_dispatch_remains_available(self):
        self.assertIn('workflow_dispatch:', WORKFLOW)
        self.assertIn("github.event_name == 'workflow_dispatch' || github.event.workflow_run.conclusion == 'success'", WORKFLOW)


if __name__ == '__main__':
    unittest.main()
