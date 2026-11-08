import unittest
from pathlib import Path


class BackfillDerivedWorkflowTests(unittest.TestCase):
    WORKFLOW = Path(__file__).parent / ".github" / "workflows" / "backfill-derived.yml"

    @classmethod
    def setUpClass(cls):
        cls.text = cls.WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_manual_and_checks_out_selected_ref(self):
        self.assertIn("workflow_dispatch:", self.text)
        self.assertNotIn("\n  schedule:", self.text)
        self.assertNotIn("\n  push:", self.text)
        self.assertNotIn("ref: main", self.text)
        self.assertIn("fetch-depth: 0", self.text)

    def test_publish_modes_and_rolling_history_are_explicit(self):
        self.assertIn("      publish:", self.text)
        self.assertIn("default: false", self.text)
        self.assertIn("Restore latest rolling history", self.text)
        self.assertIn("Resolve rolling collection window", self.text)
        self.assertIn('--history-dir "$HISTORY_DIR"', self.text)
        self.assertIn('"rolling-history-manifest.json"', self.text)
        self.assertIn("Upload non-publishing derived artifact", self.text)
        self.assertIn("inputs.publish == true && github.ref != 'refs/heads/main'", self.text)
        self.assertIn("inputs.publish == true && github.ref == 'refs/heads/main'", self.text)

    def test_safety_gates_and_no_report_generation(self):
        self.assertNotIn("actions: write", self.text)
        self.assertNotIn("gh workflow run daily-report.yml", self.text)
        self.assertNotIn("Dispatch daily report after successful backfill", self.text)
        self.assertIn("FULL_MARKET_COLLECTION_ZERO_ROWS", self.text)
        self.assertIn("REPORT_CONTRACT_TOO_LARGE", self.text)
        self.assertIn("DERIVED_FALLBACK_USED", self.text)
        self.assertIn('TARGET_DATE: ${{ steps.previous_day.outputs.target_date }}', self.text)
        self.assertIn("FUTURE_DATA", self.text)
        self.assertIn("ROLLING_HISTORY_TARGET_DATE_MISMATCH", self.text)
        self.assertNotIn("20261030_DATA_MIXED_IN", self.text)
        self.assertNotIn("20261030_ROLLING_HISTORY_MIXED_IN", self.text)
        self.assertIn("RAW_OR_REPORT_RENDER_OUTPUT_PRESENT", self.text)
        publish_start = self.text.index("- name: Publish derived artifacts atomically")
        publish_body = self.text[publish_start:]
        self.assertIn("inputs.publish == true", publish_body)
        self.assertIn("git push origin HEAD:main", publish_body)

    def test_validation_keeps_paths_separate_from_loaded_payloads(self):
        self.assertIn('projection_path = require_path(root / "report-contract-projection.json"', self.text)
        self.assertIn('projection_payload = json.loads(projection_path.read_text', self.text)
        self.assertIn('projection_path.stat().st_size', self.text)
        self.assertNotIn('projection = json.loads((root / "report-contract-projection.json")', self.text)
        self.assertIn('rolling_manifest_path = require_path(root / "rolling-history-manifest.json"', self.text)
        self.assertIn('rolling_history_root = require_path(root / "rolling-history"', self.text)
        self.assertIn('for path in rolling_history_root.rglob("*.jsonl")', self.text)
        self.assertIn('def require_path(value, name):', self.text)


if __name__ == "__main__":
    unittest.main()
