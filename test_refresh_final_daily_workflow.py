import unittest
from pathlib import Path


WORKFLOW = Path(__file__).parent / ".github" / "workflows" / "refresh.yml"


class RefreshFinalDailyWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.start = cls.text.index("      - name: Prepare final daily monitoring update")
        cls.end = cls.text.index("      - name: Run V2 shadow observation", cls.start)
        cls.final_step = cls.text[cls.start:cls.end]
        cls.indices_start = cls.text.index("      - name: Collect market indices")
        cls.indices_step = cls.text[cls.indices_start:cls.start]
        cls.publish_start = cls.text.index("      - name: Commit refreshed production data")
        cls.publish = cls.text[cls.publish_start:]

    def test_final_history_is_opt_in_and_schedule_gated(self):
        self.assertIn("vars.FINAL_DAILY_HISTORY_WRITE == 'true'", self.final_step)
        self.assertIn("steps.mode.outputs.final_run == 'true'", self.final_step)
        self.assertIn("run_mode:", self.text)
        self.assertIn("final_recovery", self.text)

    def test_final_recovery_mode_is_explicit_and_intraday_dispatch_remains_default(self):
        mode_start = self.text.index("      - name: Resolve refresh mode")
        mode_end = self.text.index("      - name: Set up Python", mode_start)
        mode = self.text[mode_start:mode_end]
        self.assertIn('RUN_MODE: ${{ inputs.run_mode || \'intraday\' }}', mode)
        self.assertIn('EVENT_SCHEDULE" = "30 11 * * 1-5"', mode)
        self.assertIn('RUN_MODE" = "final_recovery"', mode)
        self.assertIn('final_run=false', mode)

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

    def test_final_success_rebuilds_v1_after_final_baseline(self):
        rebuild = self.final_step.index("tools/run_full_market_monitoring.py")
        v1 = self.final_step.index("tools/build_dynamic_universe.py", rebuild)
        self.assertLess(rebuild, v1)

    def test_final_update_requires_explicit_snapshot_session_date(self):
        self.assertIn("data/market/indices.json", self.final_step)
        self.assertIn("validate_final_payload", self.final_step)
        self.assertIn("from market_indices import validate_final_payload", self.final_step)
        self.assertIn("collection_started_at", self.final_step)
        self.assertIn("collection_finished_at", self.final_step)
        self.assertNotIn("datetime.now(ZoneInfo(\"Asia/Seoul\"))", self.final_step)

    def test_final_daily_evidence_is_a_publication_gate(self):
        self.assertIn("from tools.final_daily_evidence import validate_snapshot", self.final_step)
        self.assertIn("final_evidence = validate_snapshot", self.final_step)
        self.assertIn('if not final_evidence["finalEligible"]:', self.final_step)
        self.assertIn("FINAL_DAILY_HISTORY_NOT_READY:FINAL_DAILY_EVIDENCE_UNUSABLE", self.final_step)

    def test_index_collection_records_a_fresh_execution_window(self):
        self.assertIn("started_at=$(TZ=Asia/Seoul date --iso-8601=seconds)", self.indices_step)
        self.assertIn("finished_at=$(TZ=Asia/Seoul date --iso-8601=seconds)", self.indices_step)
        self.assertIn("collection_started_at", self.indices_step)
        self.assertIn("collection_finished_at", self.indices_step)

    def test_final_requires_closed_indices_before_local_date_collection(self):
        self.assertIn("validate_final_payload", self.final_step)
        self.assertIn("--fetch-today", self.final_step)
        self.assertIn("TRADING_DATE_DIAGNOSTIC_UNVERIFIED", self.final_step)

    def test_final_update_collects_naver_daily_local_dates_for_observed_set(self):
        self.assertIn("--observed-snapshot", self.final_step)
        self.assertIn("--fetch-today", self.final_step)
        self.assertIn("--allow-source-observed", self.final_step)
        self.assertIn("--session-diagnostic-output", self.final_step)
        self.assertIn("final-daily-target.txt", self.final_step)
        self.assertNotIn("from tools.monitoring_daily_state_update import collect_today_rows", self.final_step)
        self.assertNotIn("monitoring_daily_payload", self.final_step)

    def test_duplicate_final_run_reuses_same_day_state_without_daily_refetch_contract(self):
        self.assertIn("--fetch-today", self.final_step)
        self.assertIn("update_with_universe_refresh", Path(__file__).parent.joinpath(
            "tools", "monitoring_daily_state_update.py").read_text(encoding="utf-8"))
        self.assertIn("same-day final generation", Path(__file__).parent.joinpath(
            "tools", "monitoring_daily_state_update.py").read_text(encoding="utf-8"))

    def test_indices_are_collected_before_final_date_resolution(self):
        self.assertIn("id: indices", self.indices_step)
        self.assertLess(self.indices_start, self.start)

    def test_only_successful_opt_in_update_is_publishable(self):
        self.assertIn('[ "${{ vars.FINAL_DAILY_HISTORY_WRITE }}" = "true" ]', self.publish)
        self.assertIn('[ "${{ steps.mode.outputs.final_run }}" = "true" ]', self.publish)
        self.assertIn('[ "${{ steps.final_daily_history.outcome }}" = "success" ]', self.publish)
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
        self.assertIn("--session-diagnostic-output", self.final_step)
        self.assertIn("diagnostic_path=\"$RUNNER_TEMP/trading-session-diagnostic.json\"", self.final_step)
        self.assertIn("TRADING_DATE_DIAGNOSTIC_UNVERIFIED", self.final_step)

    def test_ready_signal_requires_a_new_publication_commit(self):
        mail = self.text[self.text.index("id: ready_email"):]
        self.assertIn("steps.publish.outputs.publication_changed == 'true'", mail[:1200])

    def test_full_market_entrypoint_opts_into_source_observed_only_with_explicit_date(self):
        calls = self.text.count("--allow-source-observed-final")
        self.assertEqual(calls, 2)
        self.assertEqual(self.text.count('--source-observed-date "$target_date"'), 2)
        self.assertIn("baselineFreshnessStatus", Path(__file__).parent.joinpath(
            "tools", "run_full_market_monitoring.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
