import unittest
from pathlib import Path


ROOT = Path(__file__).parent / "ops" / "external-scheduler"


class ExternalSchedulerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = (ROOT / "worker.js").read_text(encoding="utf-8")
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        cls.wrangler = (ROOT / "wrangler.toml").read_text(encoding="utf-8")
        cls.refresh_workflow = (ROOT.parent.parent / ".github" / "workflows" / "refresh.yml").read_text(encoding="utf-8")

    def test_final_slot_dispatches_explicit_recovery_mode(self):
        self.assertIn('cron === "30 11 * * MON-FRI"', self.worker)
        self.assertIn('run_mode: "final_recovery"', self.worker)

    def test_intraday_slot_keeps_intraday_mode(self):
        self.assertIn('run_mode: "intraday"', self.worker)
        self.assertIn('cron === "7 0-10 * * MON-FRI"', self.worker)

    def test_duplicate_claim_and_secret_protection_are_explicit(self):
        self.assertIn("SLOT_GATE", self.worker)
        self.assertIn("GITHUB_TOKEN", self.worker)
        self.assertIn('"User-Agent": GITHUB_USER_AGENT', self.worker)
        self.assertIn('GITHUB_USER_AGENT = "naver-krx-external-scheduler"', self.worker)
        self.assertIn("DISPATCH_FAILED", self.worker)
        self.assertIn("UNVERIFIED", self.worker)
        self.assertIn("SLOT_GRACE_BEFORE_MS", self.worker)
        self.assertIn("SLOT_GRACE_AFTER_MS", self.worker)
        self.assertIn("Secret 등록·실제 Cron 활성화는 별도 승인", self.readme)

    def test_delivery_is_not_claimed_from_run_success(self):
        self.assertIn('runResult.deliveryStatus = "UNVERIFIED"', self.worker)
        self.assertIn("게시 커밋 SHA", self.readme)

    def test_final_publication_contract_is_explicit(self):
        for value in ("FINAL_DATE_MISMATCH", "FINAL_DIAGNOSTIC_UNVERIFIED", "FINAL_BASELINE_INVALID", "FINAL_PUBLICATION_VERIFIED"):
            self.assertIn(value, self.worker)

    def test_free_plan_and_provenance_contract_are_explicit(self):
        self.assertIn('CONSOLIDATED_CRON', self.worker)
        self.assertIn('production-publication-', self.worker)
        self.assertIn('production_commit_sha', self.readme)
        self.assertEqual(self.wrangler.count('crons ='), 1)
        self.assertIn('10,17,20,30,50 * * * SUN-FRI', self.wrangler)
        self.assertIn('new_sqlite_classes = ["SlotGate"]', self.wrangler)
        self.assertIn("production-publication-${{ github.run_id }}-${{ steps.publish.outputs.production_commit_sha }}-${{ steps.publish.outputs.publication_changed }}", self.refresh_workflow)
        self.assertIn("Upload production publication provenance", self.refresh_workflow)


if __name__ == "__main__":
    unittest.main()
