import unittest
from pathlib import Path


WORKFLOW = Path(__file__).parent / ".github" / "workflows" / "daily-report.yml"


class DailyReportWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.market_day = cls.text[cls.text.index("      - name: Check KRX trading day"):]
        cls.build = cls.text[cls.text.index("      - name: Build daily report"):
                             cls.text.index("      - name: Validate daily report")]

    def test_scheduled_gate_records_calendar_target_but_manual_date_stays_unrestricted(self):
        self.assertIn('python krx_market_day.py --date "$scheduled_date"', self.market_day)
        self.assertIn('echo "expected_date=" >> "$GITHUB_OUTPUT"', self.market_day)
        self.assertIn('echo "expected_date=$scheduled_date" >> "$GITHUB_OUTPUT"', self.market_day)

    def test_published_evidence_must_match_scheduled_krx_target(self):
        self.assertIn("EXPECTED_KRX_TRADING_DATE", self.build)
        self.assertIn("does not match KRX scheduled target", self.build)
        self.assertIn("steps.market_day.outputs.expected_date", self.text)

    def test_manual_historical_validation_is_preserved(self):
        self.assertIn("REPORT_AS_OF: ${{ inputs.as_of || '' }}", self.build)
        self.assertIn('[ "${{ github.event_name }}" = "workflow_dispatch" ]', self.market_day)

    def test_23h_publishes_json_history_without_duplicate_document_views(self):
        self.assertIn("daily_report.py --as-of \"$as_of\" --output-dir data/reports --json-only", self.build)
        self.assertIn("git add data/reports/*.json", self.text)


if __name__ == "__main__":
    unittest.main()
