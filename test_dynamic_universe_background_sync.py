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

    def test_same_payload_different_commit_records_verified_source_then_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "data" / "dynamic-universe"
            target.mkdir(parents=True)
            latest = {"version": "dynamic-universe-management-v1", "generatedAt": "2026-10-18T00:00:00+00:00",
                      "collectionCodes": [], "memberships": []}
            history = {"version": "dynamic-universe-history-v1", "events": []}
            (target / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
            (target / "history.json").write_text(json.dumps(history), encoding="utf-8")
            latest_raw = (target / "latest.json").read_bytes()
            (target / ".sync-state.json").write_text(json.dumps({
                "commit": "a" * 40, "generatedAt": latest["generatedAt"],
                "latestSha256": __import__("hashlib").sha256(latest_raw).hexdigest(),
            }), encoding="utf-8")
            calls = []

            def fetch_factory(*args, **kwargs):
                def fetch(path):
                    calls.append(path)
                    raw = ((target / "latest.json").read_bytes() if path.endswith("latest.json")
                           else (target / "history.json").read_bytes())
                    return raw, "b" * 40
                fetch.commit = "b" * 40
                return fetch

            with patch.object(background_sync, "fetch_factory", side_effect=fetch_factory):
                first = background_sync.run_result(["--root", str(root), "--now", "2026-10-18T00:01:00+00:00"])
                state = json.loads((target / ".sync-state.json").read_text(encoding="utf-8"))
                second = background_sync.run_result(["--root", str(root), "--now", "2026-10-18T00:02:00+00:00"])

            self.assertEqual(first["status"], "NOOP")
            self.assertEqual(second["status"], "NOOP")
            self.assertEqual(state["lastVerifiedSourceCommit"], "b" * 40)
            self.assertEqual(calls, ["data/dynamic-universe/latest.json", "data/dynamic-universe/history.json"])


if __name__ == "__main__":
    unittest.main()
