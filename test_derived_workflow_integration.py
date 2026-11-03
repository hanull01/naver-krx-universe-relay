import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class DerivedWorkflowIntegrationTests(unittest.TestCase):
    def test_daily_report_passes_the_resolved_generation_to_staging_mode(self):
        text = (ROOT / ".github/workflows/daily-report.yml").read_text(encoding="utf-8")
        self.assertIn("Resolve report and derived generation", text)
        self.assertIn("resolve_derived_input", text)
        self.assertIn("--staging-input-dir", text)
        self.assertIn("steps.inputs.outputs.derived_dir", text)
        self.assertIn("--json-only", text)

    def test_refresh_requires_derived_generation_before_intraday_collection(self):
        text = (ROOT / ".github/workflows/refresh.yml").read_text(encoding="utf-8")
        self.assertIn("Resolve derived generation for refresh", text)
        self.assertIn("DERIVED_RUN_DIR", text)
        self.assertIn("DERIVED_TARGET_DATE", text)
        self.assertIn("Intraday collection or derived technical validation failed", text)
        self.assertNotIn("data/daily/", text[text.index("- name: Collect NAVER data"):text.index("- name: Collect full-market monitoring evidence")])

    def test_refresh_and_daily_report_select_the_same_resolved_generation(self):
        refresh = (ROOT / ".github/workflows/refresh.yml").read_text(encoding="utf-8")
        daily = (ROOT / ".github/workflows/daily-report.yml").read_text(encoding="utf-8")
        for text in (refresh, daily):
            self.assertIn("resolve_derived_input", text)
            self.assertRegex(text, r"(?:derived|result)\[['\"]runId['\"]\]")
            self.assertRegex(text, r"(?:derived|result)\[['\"]runDir['\"]\]")
        self.assertIn('resolve_derived_input("data/monitoring-derived", sys.argv[1])', refresh)
        self.assertIn('resolve_derived_input("data/monitoring-derived", as_of)', daily)

    def test_legacy_fallback_is_not_enabled_by_staging_adapter(self):
        text = (ROOT / "daily_report.py").read_text(encoding="utf-8")
        start = text.index("def load_report_technical_inputs")
        end = text.index("def restrict_staging_current_quotes", start)
        block = text[start:end]
        self.assertIn("derivedProjection", block)
        self.assertIn("build_staged_technical_input", block)
        staging_block = block.split("    root = Path(staging_input_dir)", 1)[1]
        self.assertNotIn("TECHNICALS_PATH", staging_block)
        self.assertNotIn("STATES_PATH", staging_block)

    def test_krx_staging_enforces_krx_primary_policy(self):
        text = (ROOT / "daily_report.py").read_text(encoding="utf-8")
        block = text[text.index("def load_report_technical_inputs"):text.index("def restrict_staging_current_quotes")]
        self.assertIn('technicals_payload.get("source") == "KRX_M10_GENERATION"', block)
        self.assertIn('expected_source_staging=expected_source_staging', block)
        self.assertIn('"KRX_PRIMARY_NO_MERGE"', block)
        self.assertIn('"MISSING_OR_LAGGED_NO_NAVER_HISTORICAL_FALLBACK"', block)


if __name__ == "__main__":
    unittest.main()
