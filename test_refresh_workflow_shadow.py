import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/refresh.yml").read_text()


class RefreshWorkflowShadowSafetyTests(unittest.TestCase):
    def test_email_is_main_only(self):
        block = WORKFLOW.split("- name: Send MARKET_DATA_READY signal", 1)[1].split("- name: Warn", 1)[0]
        self.assertIn("github.ref == 'refs/heads/main'", block)

    def test_branch_local_sync_and_push(self):
        self.assertIn('target_ref="${GITHUB_REF_NAME:-main}"', WORKFLOW)
        self.assertIn('git pull --ff-only origin "$target_ref"', WORKFLOW)
        self.assertIn('git fetch origin "$target_ref"', WORKFLOW)
        self.assertIn('git rebase "origin/${target_ref}"', WORKFLOW)
        self.assertIn('git push origin "HEAD:${target_ref}"', WORKFLOW)
        self.assertNotIn("git rebase origin/main", WORKFLOW)

    def test_v2_status_is_separate_from_v1_gate(self):
        block = WORKFLOW.split("- name: Run V2 shadow observation", 1)[1].split("- name: Collect optional", 1)[0]
        self.assertIn("continue-on-error: true", block)
        self.assertIn("v2-shadow-run-status.json", WORKFLOW)
        self.assertIn("PARTIAL_NON_BLOCKING", block)
        self.assertIn("GITHUB_STEP_SUMMARY", block)
        self.assertIn("normalize_run_status", block)
        self.assertIn("expected_generation_id", block)
        self.assertIn('consumptionStatus', block)

    def test_v2_publish_is_atomic_and_failure_preserves_prior_results(self):
        self.assertIn("restore_generated_path data/dynamic-universe/v2-shadow-latest.json", WORKFLOW)
        self.assertIn("restore_generated_path data/dynamic-universe/v2-shadow-history.json", WORKFLOW)
        self.assertIn("v2_paths=", WORKFLOW)
        self.assertIn('git diff --name-only -- "$path"', WORKFLOW)
        self.assertIn('git diff-tree --no-commit-id --name-only -r HEAD', WORKFLOW)
        self.assertIn("expected-publish-files.txt", WORKFLOW)
        self.assertIn("actual-commit-files.txt", WORKFLOW)
        success_block = WORKFLOW.split('if [ "$v2_status" = "OK" ]; then', 1)[1].split("else", 1)[0]
        self.assertIn("v2-shadow-latest.json", success_block)
        self.assertIn("v2-shadow-history.json", success_block)

    def test_v2_restore_function_precedes_publish_decision(self):
        self.assertLess(
            WORKFLOW.index("restore_generated_path()"),
            WORKFLOW.index("if [ -f data/dynamic-universe/v2-shadow-run-status.json ]"),
        )


if __name__ == "__main__":
    unittest.main()
