import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
