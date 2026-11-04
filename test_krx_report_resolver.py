import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.krx_report_resolver import ResolverError, resolve


class KrxReportResolverTests(unittest.TestCase):
    def test_exact_date_is_required_for_automatic_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ResolverError, "EXPECTED_DATE_REQUIRED"):
                resolve(output_dir=tmp, require_expected_date=True)

    def test_missing_secret_is_blocked_without_network_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ResolverError, "SECRET_MISSING:KRX_PRIVATE_REPO_TOKEN"):
                    resolve(output_dir=tmp, expected_date="20261103")

    def test_non_immutable_ref_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"KRX_PRIVATE_REPO_TOKEN": "test-only"}, clear=True):
                with self.assertRaisesRegex(ResolverError, "IMMUTABLE_REF_REQUIRED"):
                    resolve(output_dir=tmp, expected_date="20261103", ref="codex/other-branch")

    def test_generation_mismatch_is_blocked_before_payload_use(self):
        verified = {"tradingDate": "20261103", "generationId": "gen_actual"}
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"KRX_PRIVATE_REPO_TOKEN": "test-only"}, clear=True):
                with patch("tools.krx_report_resolver.load_verified_private_generation", return_value=verified):
                    with self.assertRaisesRegex(ResolverError, "GENERATION_ID_MISMATCH"):
                        resolve(
                            output_dir=tmp,
                            expected_date="20261103",
                            expected_generation_id="gen_expected",
                        )

    def test_workflows_use_the_common_resolver_and_private_secret(self):
        root = Path(__file__).parent / ".github" / "workflows"
        names = (
            "refresh.yml",
            "refresh-monitoring-daily.yml",
            "refresh-market-snapshots.yml",
            "refresh-research.yml",
            "daily-report.yml",
        )
        for name in names:
            text = (root / name).read_text(encoding="utf-8")
            self.assertIn("./.github/actions/resolve-krx-generation", text, name)
            self.assertIn("secrets.KRX_PRIVATE_REPO_TOKEN", text, name)
            self.assertIn("krx_expected_date", text, name)
            self.assertIn("krx_ref", text, name)
            self.assertIn("krx_generation_id", text, name)
            self.assertIn("publish", text, name)
            self.assertIn("inputs.publish == true", text, name)
            self.assertIn("NO_PUBLISH", text, name)

    def test_fanout_is_exact_input_and_idempotent(self):
        text = (
            Path(__file__).parent / ".github" / "workflows" / "krx-m10-fanout.yml"
        ).read_text(encoding="utf-8")
        for target in (
            "refresh.yml",
            "refresh-monitoring-daily.yml",
            "refresh-market-snapshots.yml",
            "refresh-research.yml",
            "daily-report.yml",
        ):
            self.assertIn(target, text)
        self.assertIn("NO_CHANGES", text)
        self.assertIn("gh workflow run", text)
        self.assertIn("krx_expected_date", text)
        self.assertIn("krx_ref", text)
        self.assertIn("krx_generation_id", text)
        self.assertLess(
            text.index("Resolve and verify private KRX generation"),
            text.index("Dispatch report workflows"),
        )


if __name__ == "__main__":
    unittest.main()
