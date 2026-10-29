import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import tools.work_qa_preflight as preflight


class WorkQaPreflightTests(unittest.TestCase):
    def test_repository_check_rejects_non_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(preflight.PreflightError, "NOT_A_GIT_REPOSITORY"):
                preflight.find_repository(Path(directory))

    def test_repository_check_requires_expected_origin(self):
        completed = [
            type("Result", (), {"returncode": 0, "stdout": "/tmp/repo\n", "stderr": ""})(),
            type("Result", (), {"returncode": 0, "stdout": "https://example.invalid/repo.git\n", "stderr": ""})(),
        ]
        with patch.object(preflight, "run", side_effect=completed):
            with self.assertRaisesRegex(preflight.PreflightError, "UNEXPECTED_ORIGIN"):
                preflight.find_repository(Path("/tmp"))

    def test_required_import_contract_is_explicit(self):
        self.assertEqual(preflight.REQUIRED_IMPORTS, ("certifi", "webauthn"))
        self.assertTrue((Path(__file__).parent / "requirements-qa.txt").read_text().find("webauthn") >= 0)

    def test_remote_logging_redacts_userinfo_and_ssh_user(self):
        self.assertEqual(
            preflight.redact_remote_url("https://alice:token@github.com/hanull01/naver-krx-universe-relay.git"),
            "https://github.com/hanull01/naver-krx-universe-relay.git",
        )
        self.assertEqual(
            preflight.redact_remote_url("https://github.com/hanull01/naver-krx-universe-relay.git?token=secret#secret"),
            "https://github.com/hanull01/naver-krx-universe-relay.git",
        )
        self.assertEqual(
            preflight.redact_remote_url("git@github.com:hanull01/naver-krx-universe-relay.git"),
            "ssh://github.com/hanull01/naver-krx-universe-relay.git",
        )

    def test_check_requires_a_qa_venv(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(preflight.PreflightError, "QA_VENV_MISSING"):
                preflight.require_qa_venv(Path(directory) / ".qa-venv")

    def test_check_rejects_incomplete_qa_venv_with_repair_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".qa-venv"
            env_path.mkdir()
            with self.assertRaisesRegex(preflight.PreflightError, "QA_VENV_INCOMPLETE.*--repair"):
                preflight.require_qa_venv(env_path)

    def test_github_cli_auth_is_not_inferred_from_git_remote_read(self):
        with patch.object(preflight.shutil, "which", return_value="/usr/bin/gh"), patch.object(
            preflight,
            "run",
            side_effect=[
                SimpleNamespace(returncode=1, stdout="", stderr="not logged in"),
                SimpleNamespace(returncode=0, stdout="remote-head", stderr=""),
            ],
        ):
            result = preflight.github_read_check(Path("/tmp"))
        self.assertIn("github_cli_auth=UNVERIFIED", result)
        self.assertIn("git_remote_read=PASS", result)
        self.assertIn("github_write=UNVERIFIED", result)


if __name__ == "__main__":
    unittest.main()
