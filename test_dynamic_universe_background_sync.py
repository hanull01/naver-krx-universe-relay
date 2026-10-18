import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dynamic_universe_background_sync as background_sync


class DynamicUniverseBackgroundSyncTests(unittest.TestCase):
    def test_same_commit_skips_artifact_download(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "data" / "dynamic-universe"
            target.mkdir(parents=True)
            (target / "latest.json").write_text("{}", encoding="utf-8")
            (target / "history.json").write_text("{}", encoding="utf-8")
            (target / ".sync-state.json").write_text(
                json.dumps({"commit": "abc123", "generatedAt": "2026-10-18T00:00:00Z"}),
                encoding="utf-8",
            )

            def fetch_factory(*args, **kwargs):
                def fetch(path):
                    raise AssertionError(f"artifact fetch must be skipped: {path}")

                fetch.commit = "abc123"
                return fetch

            with patch.object(background_sync, "fetch_factory", side_effect=fetch_factory):
                result = background_sync.run_result(["--root", str(root)])

            self.assertEqual(result["status"], "NOOP")
            self.assertEqual(result["sourceCommit"], "abc123")
            self.assertEqual(result["appliedCommit"], "abc123")


if __name__ == "__main__":
    unittest.main()
