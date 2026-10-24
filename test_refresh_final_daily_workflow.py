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
        self.assertIn('--observed-snapshot "$RUNNER_TEMP/full-market-current.json"', self.final_step)
        self.assertIn("--refresh-universe", self.final_step)
        self.assertNotIn("--no-write", self.final_step)
        self.assertIn('echo "write=true" >> "$GITHUB_OUTPUT"', self.final_step)

    def test_final_success_rebuilds_evidence_from_same_snapshot_without_refetch(self):
        update = self.final_step.index("build_monitoring_daily_state.py update-latest")
        rebuild = self.final_step.index("tools/run_full_market_monitoring.py", update)
        self.assertLess(update, rebuild)
        self.assertIn('--current-file "$RUNNER_TEMP/full-market-current.json"', self.final_step[rebuild:])
        self.assertNotIn("naver_full_market_current.py", self.final_step[rebuild:])

    def test_final_update_requires_explicit_snapshot_session_date(self):
        self.assertIn("from tools.trading_date_evidence import explicit_session_date", self.final_step)
        self.assertIn("TRADING_DATE_MISSING_OR_INCONSISTENT", self.final_step)
        self.assertNotIn("datetime.now(ZoneInfo(\"Asia/Seoul\"))", self.final_step)

    def test_only_successful_opt_in_update_is_publishable(self):
        gate = (
            '[ "${{ vars.FINAL_DAILY_HISTORY_WRITE }}" = "true" ] && '
            '[ "${{ steps.final_daily_history.outcome }}" = "success" ]'
        )
        self.assertIn(gate, self.publish)
        self.assertIn("data/monitoring-daily/kospi.json", self.publish)
        self.assertIn("data/monitoring-daily/kosdaq.json", self.publish)
        self.assertIn("data/market-universe/all-assets.json", self.publish)
        self.assertIn("data/monitoring-daily/retired.json", self.publish)
        self.assertIn("data/monitoring-daily/universe-lifecycle.json", self.publish)
        self.assertIn("data/monitoring-daily/trading-session-diagnostic.json", self.publish)
        self.assertIn("data/monitoring-baseline/latest.json", self.publish)
        self.assertIn("final_daily_gate_required", self.publish)
        self.assertIn('preserving prior evidence', self.publish)

    def test_final_update_writes_and_validates_local_date_diagnostic(self):
        self.assertIn("observed_stocks = snapshot.get(\"stocks\") or []", self.final_step)
        self.assertIn("OBSERVED_STOCK_UNIVERSE_INVALID", self.final_step)
        self.assertIn("write_local_date_diagnostic", self.final_step)
        self.assertIn("TRADING_DATE_DIAGNOSTIC_UNVERIFIED", self.final_step)

    def test_ready_signal_requires_a_new_publication_commit(self):
        mail = self.text[self.text.index("id: ready_email"):]
        self.assertIn("steps.publish.outputs.publication_changed == 'true'", mail[:1200])


if __name__ == "__main__":
    unittest.main()
