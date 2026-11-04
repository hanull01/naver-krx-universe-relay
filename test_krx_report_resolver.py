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


if __name__ == "__main__":
    unittest.main()
