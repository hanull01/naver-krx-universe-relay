import unittest
from pathlib import Path


ROOT = Path(__file__).parent / "ops" / "external-scheduler"


class ExternalSchedulerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worker = (ROOT / "worker.js").read_text(encoding="utf-8")
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_final_slot_dispatches_explicit_recovery_mode(self):
        self.assertIn('cron === "30 11 * * 1-5"', self.worker)
        self.assertIn('run_mode: "final_recovery"', self.worker)

    def test_intraday_slot_keeps_intraday_mode(self):
        self.assertIn('run_mode: "intraday"', self.worker)
        self.assertIn('cron === "7 0-10 * * 1-5"', self.worker)

    def test_duplicate_claim_and_secret_protection_are_explicit(self):
        self.assertIn("SLOT_GATE", self.worker)
        self.assertIn("GITHUB_TOKEN", self.worker)
        self.assertIn("DISPATCH_FAILED", self.worker)
        self.assertIn("UNVERIFIED", self.worker)
        self.assertIn("Secret 등록·실제 Cron 활성화는 별도 승인", self.readme)

    def test_delivery_is_not_claimed_from_run_success(self):
        self.assertIn('runResult.deliveryStatus = "UNVERIFIED"', self.worker)
        self.assertIn("게시 커밋 SHA", self.readme)


if __name__ == "__main__":
    unittest.main()
