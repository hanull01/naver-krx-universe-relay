import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import dynamic_universe_sync as sync


def payload(generated="2026-10-13T05:00:00+00:00"):
    return {"version": "dynamic-universe-management-v1", "generatedAt": generated,
            "collectionCodes": ["005930"], "memberships": [{"code": "005930", "sources": ["USER_WATCHLIST"]}]}


class DynamicUniverseSyncTests(unittest.TestCase):
    def files(self, commit="a" * 40, generated="2026-10-13T05:00:00+00:00"):
        latest = json.dumps(payload(generated)).encode(); history = json.dumps({"version": "dynamic-universe-history-v1", "events": []}).encode()
        return {"data/dynamic-universe/latest.json": (latest, commit), "data/dynamic-universe/history.json": (history, commit)}

    def test_apply_and_same_commit_noop(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); f = self.files(); fetch = lambda p: f[p]
            first = sync.sync(fetch, root / "latest.json", root / "history.json", root / "state.json", now="2026-10-13T06:00:00+00:00")
            second = sync.sync(fetch, root / "latest.json", root / "history.json", root / "state.json", now="2026-10-13T06:00:00+00:00")
            self.assertEqual(first["status"], "APPLIED"); self.assertEqual(second["status"], "NOOP")

    def test_mismatched_commit_is_rejected_without_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); f = self.files(); f["data/dynamic-universe/history.json"] = (f["data/dynamic-universe/history.json"][0], "b" * 40)
            with self.assertRaisesRegex(sync.SyncError, "ARTIFACT_COMMITS_MISMATCH"):
                sync.sync(lambda p: f[p], root / "latest.json", root / "history.json", root / "state.json", now="2026-10-13T06:00:00+00:00")
            self.assertFalse((root / "latest.json").exists())

    def test_old_result_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); f = self.files(); state = root / "state.json"
            state.write_text(json.dumps({"commit": "b" * 40, "generatedAt": "2026-10-13T06:00:00+00:00"}))
            with self.assertRaisesRegex(sync.SyncError, "PUBLISHED_RESULT_NOT_NEWER"):
                sync.sync(lambda p: f[p], root / "latest.json", root / "history.json", state, now="2026-10-13T07:00:00+00:00")

    def test_commit_changed_but_payload_same_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); f = self.files(commit="a" * 40)
            sync.sync(lambda p: f[p], root / "latest.json", root / "history.json", root / "state.json", now="2026-10-13T06:00:00+00:00")
            f["data/dynamic-universe/latest.json"] = (f["data/dynamic-universe/latest.json"][0], "b" * 40)
            f["data/dynamic-universe/history.json"] = (f["data/dynamic-universe/history.json"][0], "b" * 40)
            result = sync.sync(lambda p: f[p], root / "latest.json", root / "history.json", root / "state.json", now="2026-10-13T06:00:00+00:00")
            self.assertEqual(result["status"], "NOOP")
            self.assertEqual(result["appliedCommit"], "a" * 40)
            self.assertEqual(json.loads((root / "state.json").read_text())["commit"], "a" * 40)

    def test_same_generated_at_with_different_payload_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); f = self.files(commit="a" * 40)
            sync.sync(lambda p: f[p], root / "latest.json", root / "history.json", root / "state.json", now="2026-10-13T06:00:00+00:00")
            changed = payload(); changed["collectionCodes"] = ["000660"]
            changed["memberships"] = [{"code": "000660"}]
            f["data/dynamic-universe/latest.json"] = (json.dumps(changed).encode(), "b" * 40)
            f["data/dynamic-universe/history.json"] = (f["data/dynamic-universe/history.json"][0], "b" * 40)
            with self.assertRaisesRegex(sync.SyncError, "PUBLISHED_RESULT_HASH_MISMATCH"):
                sync.sync(lambda p: f[p], root / "latest.json", root / "history.json", root / "state.json", now="2026-10-13T06:00:00+00:00")

    def test_stale_corrupt_and_invalid_codes_are_rejected(self):
        for mutate, error in ((lambda x: x.update(generatedAt="2026-10-01T00:00:00+00:00"), "PUBLISHED_RESULT_STALE"),
                              (lambda x: x.update(collectionCodes=["bad"]), "COLLECTION_CODE_INVALID")):
            with self.subTest(error=error):
                with tempfile.TemporaryDirectory() as d:
                    root = Path(d); f = self.files(); item = payload(); mutate(item)
                    f["data/dynamic-universe/latest.json"] = (json.dumps(item).encode(), "a" * 40)
                    with self.assertRaisesRegex(sync.SyncError, error):
                        sync.sync(lambda p: f[p], root / "latest.json", root / "history.json", root / "state.json", now="2026-10-13T06:00:00+00:00")

    def test_alphanumeric_krx_code_is_accepted(self):
        latest = payload()
        latest["collectionCodes"] = ["005930", "0197X0"]
        latest["memberships"] = [{"code": code} for code in latest["collectionCodes"]]
        history = {"version": "dynamic-universe-history-v1", "events": []}
        result = sync.validate(latest, history, "a" * 40, now="2026-10-13T06:00:00+00:00")
        self.assertEqual(result["collectionCount"], 2)


if __name__ == "__main__": unittest.main()
