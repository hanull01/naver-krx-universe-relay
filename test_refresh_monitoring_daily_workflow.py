import unittest
from pathlib import Path


WORKFLOW = Path(__file__).parent / ".github/workflows/refresh-monitoring-daily.yml"


class ClosingRollingWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_uses_1545_kst_queue_and_manual_dry_run(self):
        self.assertIn('cron: "45 6 * * 1-5"', self.text)
        self.assertIn('group: generated-data-write', self.text)
        self.assertIn('workflow_dispatch:', self.text)
        self.assertIn('dry_run:', self.text)

    def test_retry_calendar_and_fetch_today_are_fail_closed(self):
        self.assertIn('for attempt in 1 2 3', self.text)
        self.assertIn('sleep 300', self.text)
        self.assertIn('python krx_market_day.py', self.text)
        self.assertIn('--fetch-today --date', self.text)
        self.assertIn('preserving prior rolling state and baseline', self.text)

    def test_before_close_skips_fetch_and_only_incomplete_rows_retry(self):
        self.assertIn('Check KST regular-close window', self.text)
        self.assertIn('BEFORE_REGULAR_CLOSE: skipping NAVER fetch, retry, and publish', self.text)
        self.assertIn("steps.closing_window.outputs.eligible == 'true'", self.text)
        self.assertIn('if [ "$code" -ne 2 ]', self.text)
        self.assertIn('nowKst=', self.text)

    def test_publish_stages_only_three_artifacts_without_force_push(self):
        for path in ('data/monitoring-daily/kospi.json', 'data/monitoring-daily/kosdaq.json',
                     'data/monitoring-baseline/latest.json'):
            self.assertIn(path, self.text)
        self.assertNotIn('git add .', self.text)
        self.assertNotIn('git add -A', self.text)
        self.assertNotIn('git push --force', self.text)
        self.assertIn('git diff --cached --name-only', self.text)


if __name__ == '__main__':
    unittest.main()
