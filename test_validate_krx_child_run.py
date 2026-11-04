import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.validate_krx_child_run import validate


def payload(*steps, conclusion="success"):
    return {"conclusion": conclusion, "jobs": [{"steps": list(steps)}]}


def step(name, conclusion):
    return {"name": name, "conclusion": conclusion}


class ValidateKrxChildRunTests(unittest.TestCase):
    def test_publish_false_success(self):
        self.assertIsNone(validate(payload(
            step("Commit refreshed production data", "skipped"),
            step("Record production publication provenance", "skipped"),
            step("Upload production publication provenance", "skipped"),
            step("Report NO_PUBLISH validation result", "success"),
        ), "false"))

    def test_publish_false_commit_is_rejected(self):
        self.assertEqual(validate(payload(
            step("Commit refreshed production data", "success"),
            step("Report NO_PUBLISH validation result", "success"),
        ), "false"), "PUBLISH_FALSE_COMMIT_STEP_RAN")

    def test_publish_false_missing_no_publish_is_rejected(self):
        self.assertEqual(validate(payload(
            step("Commit refreshed production data", "skipped"),
        ), "false"), "NO_PUBLISH_RESULT_MISSING")

    def test_publish_true_success(self):
        self.assertIsNone(validate(payload(
            step("Commit refreshed production data", "success"),
            step("Record production publication provenance", "success"),
            step("Upload production publication provenance", "success"),
        ), "true"))

    def test_publish_true_does_not_require_no_publish(self):
        self.assertIsNone(validate(payload(
            step("Commit refreshed production data", "success"),
            step("Record production publication provenance", "success"),
        ), "true"))

    def test_publish_true_commit_failure_is_rejected(self):
        self.assertEqual(validate(payload(
            step("Commit refreshed production data", "failure"),
            step("Record production publication provenance", "success"),
        ), "true"), "PUBLISH_TRUE_COMMIT_STEP_FAILED")

    def test_child_failure_is_rejected(self):
        self.assertEqual(validate(payload(
            step("Commit refreshed production data", "success"),
            conclusion="failure",
        ), "true"), "CHILD_WORKFLOW_NOT_SUCCESS")

    def test_cli_reads_json_path_and_publish_state(self):
        data = payload(
            step("Commit refreshed production data", "success"),
            step("Record production publication provenance", "success"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "child-run.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "tools/validate_krx_child_run.py", str(path), "true"],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
