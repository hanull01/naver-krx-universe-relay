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


if __name__ == "__main__":
    unittest.main()
