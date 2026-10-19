import unittest
from pathlib import Path


WORKFLOW = Path('.github/workflows/refresh-monitoring-daily.yml').read_text()


class BaselineWorkflowOrderTests(unittest.TestCase):
    def test_baseline_refresh_is_no_longer_chained_to_daily_candles(self):
        self.assertNotIn('workflow_run:', WORKFLOW)
        self.assertNotIn('workflows: ["Refresh daily candles"]', WORKFLOW)
        self.assertNotIn('types: [completed]', WORKFLOW)
        self.assertNotIn('cron: "45 6 * * 1-5"', WORKFLOW)

    def test_manual_dispatch_remains_available(self):
        self.assertIn('workflow_dispatch:', WORKFLOW)
        self.assertIn("github.event_name == 'workflow_dispatch'", WORKFLOW)


if __name__ == '__main__':
    unittest.main()
