import unittest
from pathlib import Path


WORKFLOW = Path(__file__).parent / ".github" / "workflows" / "refresh.yml"


class RefreshIntradayWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_refresh_is_intraday_only(self):
        for forbidden in (
            "run_mode:", "final_recovery", "source_mode:",
            "Resolve refresh mode", "steps.mode", "final_daily_history",
            "Diagnose final daily-bar evidence", "Prepare final daily monitoring update",
            "FINAL_DAILY_HISTORY_WRITE", "--allow-source-observed-final",
            "--source-observed-date",
        ):
            self.assertNotIn(forbidden, self.text)

    def test_krx_primary_input_is_passed_to_relay_and_full_market(self):
        self.assertIn("--krx-input-dir", self.text)
        self.assertIn("KRX_INPUT=AVAILABLE historicalSource=KRX fallbackUsed=false", self.text)
        self.assertIn('"${{ steps.krx.outputs.krx_report_input_dir }}"', self.text)

    def test_current_indices_are_collected_without_final_close_validation(self):
        self.assertIn("python market_indices.py", self.text)
        self.assertIn("INTRADAY_INDEX_SNAPSHOT", self.text)
        self.assertNotIn("validate_final_payload", self.text)
        self.assertNotIn("POST_MARKET_INDEX_SNAPSHOT", self.text)

    def test_contract_failure_stops_publication(self):
        self.assertIn("no report or market publication is eligible", self.text)
        self.assertIn('exit "$code"', self.text)
        self.assertIn("steps.report_contract.outputs.status == 'SUCCESS'", self.text)

    def test_push_event_remains_noop(self):
        self.assertIn("NOOP_PUSH_EVENT", self.text)
        self.assertIn("No market data collection or publication was performed.", self.text)

    def test_publish_split_and_report_delivery_gate_remain(self):
        self.assertIn("inputs.publish == true", self.text)
        self.assertIn("Commit refreshed production data", self.text)
        self.assertIn("MARKET_DATA_READY", self.text)
        self.assertIn("data/monitoring/latest-report-contract.json", self.text)


if __name__ == "__main__":
    unittest.main()
