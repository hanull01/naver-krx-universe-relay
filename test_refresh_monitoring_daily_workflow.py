import json
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


WORKFLOW = Path(__file__).parent / ".github/workflows/refresh-monitoring-daily.yml"


class ClosingRollingWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_is_manual_only_after_final_path_owns_daily_state(self):
        self.assertNotIn('workflow_run:', self.text)
        self.assertNotIn('workflows: ["Refresh daily candles"]', self.text)
        self.assertNotIn('cron: "45 6 * * 1-5"', self.text)
        self.assertIn('group: generated-data-write', self.text)
        self.assertIn('workflow_dispatch:', self.text)
        self.assertIn('dry_run:', self.text)

    def test_retry_calendar_and_fetch_today_are_fail_closed(self):
        self.assertNotIn('sleep 300', self.text)
        self.assertNotIn('Full-market daily readiness attempt', self.text)
        self.assertIn('python krx_market_day.py', self.text)
        self.assertIn('--fetch-today --refresh-universe --date', self.text)
        self.assertIn('per-code bounded retries', self.text)
        self.assertIn('preserving all production artifacts', self.text)

    def test_before_close_skips_fetch_and_only_incomplete_rows_retry(self):
        self.assertIn('Check KST regular-close window', self.text)
        self.assertIn('BEFORE_REGULAR_CLOSE: skipping NAVER fetch, retry, and publish', self.text)
        self.assertIn("steps.closing_window.outputs.eligible == 'true'", self.text)
        self.assertIn('if [ "$code" -eq 2 ]', self.text)
        self.assertIn('UNIVERSE_HISTORY_REQUIRED', self.text)
        self.assertIn('nowKst=', self.text)

    def test_no_workflow_run_provisional_path_remains(self):
        self.assertNotIn('workflow_run', self.text)
        self.assertNotIn('extra+=(--provisional)', self.text)

    def test_publish_stages_only_atomic_lifecycle_artifacts_without_force_push(self):
        for path in ('data/market-universe/all-assets.json', 'data/monitoring-daily/kospi.json',
                     'data/monitoring-daily/kosdaq.json', 'data/monitoring-daily/universe-lifecycle.json',
                     'data/monitoring-baseline/latest.json'):
            self.assertIn(path, self.text)
        self.assertNotIn('git add .', self.text)
        self.assertNotIn('git add -A', self.text)
        self.assertNotIn('git push --force', self.text)
        self.assertIn('git diff --cached --name-only', self.text)

    def run_update_step(self, payload, exit_code=0, dry_run=True, tee_exit=0):
        # Execute the actual workflow shell with a fake collector. JSON parsing,
        # tee, PIPESTATUS and the output gate are real; no network is used.
        step = self.text.split('      - name: Collect, validate, and update rolling state', 1)[1]
        body = textwrap.dedent(step.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0])
        body = body.replace('${{ steps.closing_window.outputs.target_date }}', '2026-10-26')
        body = body.replace('${{ vars.PARTIAL_FINAL_ENABLED }}', 'true')
        stub = '''python() {
          if [ "$1" = "tools/build_monitoring_daily_state.py" ]; then
            printf '%s\\n' "$FAKE_RESULT"
            return "$FAKE_EXIT"
          fi
          PYTHON_EXECUTABLE "$@"
        }
        tee() { command tee "$@"; return "$FAKE_TEE_EXIT"; }
        '''.replace('PYTHON_EXECUTABLE', shlex.quote(sys.executable))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'outputs'
            result = subprocess.run(['bash', '--noprofile', '--norc', '-e', '-o', 'pipefail', '-c', stub + body],
                                    env={**os.environ, 'DRY_RUN': str(dry_run).lower(),
                                         'RUNNER_TEMP': directory, 'GITHUB_OUTPUT': str(output),
                                         'FAKE_RESULT': payload, 'FAKE_EXIT': str(exit_code),
                                         'FAKE_TEE_EXIT': str(tee_exit)},
                                    text=True, capture_output=True, timeout=15)
            values = dict(line.split('=', 1) for line in output.read_text().splitlines())
            return result, values

    def test_only_explicit_success_allows_ready_for_dry_run_and_publish(self):
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                result, values = self.run_update_step('{"status":"SUCCESS"}', dry_run=dry_run)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(values['ready'], 'true')
                self.assertEqual(values['python_exit_code'], '0')
                self.assertEqual(values['final_status'], 'SUCCESS')
        for step in ('Commit final rolling state and baseline', 'Report dry-run success'):
            block = self.text.split('      - name: ' + step, 1)[1]
            condition = next(line for line in block.splitlines() if line.strip().startswith('if:'))
            self.assertIn("steps.update.outputs.final_status == 'SUCCESS'", condition)
            self.assertIn("steps.update.outcome == 'success'", condition)

    def test_zero_exit_non_success_json_never_becomes_ready(self):
        for dry_run in (True, False):
            for status in ('PARTIAL', 'ERROR', 'NOT_READY', 'FAILED', 'UNIVERSE_HISTORY_REQUIRED', 'UNKNOWN'):
                with self.subTest(status=status, dry_run=dry_run):
                    result, values = self.run_update_step(json.dumps({'status': status}), dry_run=dry_run)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(values['ready'], 'false')
                    self.assertEqual(values['final_status'], status)
                    self.assertEqual(values['partial'], str(status == 'PARTIAL').lower())

    def test_python_exit_is_preserved_even_with_success_json_and_successful_tee(self):
        for code in (1, 2, 3, 4, 17):
            with self.subTest(code=code):
                result, values = self.run_update_step('{"status":"SUCCESS"}', code)
                self.assertEqual(result.returncode, code)
                self.assertEqual(values['python_exit_code'], str(code))
                self.assertEqual(values['ready'], 'false')

    def test_missing_or_invalid_final_json_fails_closed(self):
        for payload in ('', '{}', 'null', '[]', 'not-json', '{"status":"SUCCESS"}\ntrailing garbage'):
            with self.subTest(payload=payload):
                result, values = self.run_update_step(payload)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(values['ready'], 'false')
                self.assertEqual(values['final_status'], 'FAILED')

    def test_logging_failure_blocks_ready_and_preserves_python_failure(self):
        for code, expected in ((0, 7), (4, 4)):
            result, values = self.run_update_step('{"status":"SUCCESS"}', code, tee_exit=7)
            self.assertEqual(result.returncode, expected)
            self.assertEqual(values['ready'], 'false')
            self.assertEqual(values['log_exit_code'], '7')

    def test_closing_window_cli_works_without_pythonpath(self):
        env = {key: value for key, value in os.environ.items() if key != 'PYTHONPATH'}
        result = subprocess.run([sys.executable, 'tools/build_monitoring_daily_state.py', '--closing-window'],
                                cwd=WORKFLOW.parents[2], env=env, text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIn(payload['status'], ('BEFORE_REGULAR_CLOSE', 'AFTER_REGULAR_CLOSE'))
        self.assertIn('targetDate', payload)


if __name__ == '__main__':
    unittest.main()
