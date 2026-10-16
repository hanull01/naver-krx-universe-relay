import json
import tempfile
import unittest
from pathlib import Path

from tools.run_dynamic_universe_v2_shadow import run_once


class V2RunnerIsolationTests(unittest.TestCase):
    def test_failure_is_recorded_without_raising(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/dynamic-universe").mkdir(parents=True)
            outcome = run_once(root, "2026-10-15", "2026-10-15T01:00:00Z")
            self.assertEqual(outcome["status"], "FAILED")
            self.assertEqual(json.loads((root / "data/dynamic-universe/v2-shadow-run-status.json").read_text())["status"], "FAILED")
            self.assertFalse((root / "data/dynamic-universe/latest.json").exists())
