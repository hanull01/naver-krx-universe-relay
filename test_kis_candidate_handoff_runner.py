import json
import os
import tempfile
import unittest
from pathlib import Path

import trading_discovery as td
from tools import run_kis_candidate_handoff as runner


def make_fixture(root, monitoring_date="2026-10-13"):
    root = Path(root)
    snapshot_root = root / "snapshots"
    snapshot_root.mkdir()
    calendar = root / "calendar.json"
    calendar.write_text(json.dumps({
        "source": "KRX_OFFICIAL",
        "dates": ["2026-10-12", monitoring_date],
    }), encoding="utf-8")
    payload = {
        "schema_version": 1, "status": "AVAILABLE",
        "selection_date": "2026-10-12", "monitoring_date": monitoring_date,
        "source": "KIWOOM_CONDITION_SEARCH",
        "source_group": "장종료후 단타후보군 탐색", "group_id": "CONDITION:0",
        "synced_at": "2026-10-12T21:00:01+09:00", "candidate_count": 1,
        "symbols": ["005930"], "source_sha256": "",
        "validation": {"status": "VALID", "calendar_source": "KRX_OFFICIAL", "immutable": True},
    }
    payload["source_sha256"] = td.source_hash(payload)
    snapshot = snapshot_root / f"{monitoring_date}.json"
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    quotes = root / "quotes.json"
    quotes.write_text("{}", encoding="utf-8")
    return snapshot_root, snapshot, calendar, quotes, payload


class HandoffRunnerTests(unittest.TestCase):
    def test_snapshot_not_detected_does_not_call_publisher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            result = runner.run_once(
                root / "snapshots", root / "calendar.json", root / "quotes.json", root,
                state_dir=root / "state", stability_seconds=0,
                publisher_fn=lambda *args, **kwargs: calls.append(args),
            )
            self.assertEqual(result["status"], "WAITING_SNAPSHOT")
            self.assertEqual(calls, [])

    def test_explicit_date_never_falls_back_to_an_older_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_root, _, calendar, quotes, _ = make_fixture(root, "2026-10-13")
            calls = []
            result = runner.run_once(
                snapshot_root, calendar, quotes, root,
                state_dir=root / "state", monitoring_date="2026-10-14",
                stability_seconds=0,
                publisher_fn=lambda *args, **kwargs: calls.append(args),
            )
            self.assertEqual(result["status"], "WAITING_SNAPSHOT")
            self.assertEqual(calls, [])

    def test_detected_snapshot_runs_once_then_same_hash_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_root, snapshot, calendar, quotes, payload = make_fixture(root)
            original = snapshot.read_text(encoding="utf-8")
            calls = []

            def fake_publish(*args, **kwargs):
                calls.append((args, kwargs))
                return {"publishStatus": "PUSHED", "lineage": {"sourceHash": payload["source_sha256"]}}

            first = runner.run_once(
                snapshot_root, calendar, quotes, root,
                state_dir=root / "state", stability_seconds=0,
                publisher_fn=fake_publish,
            )
            second = runner.run_once(
                snapshot_root, calendar, quotes, root,
                state_dir=root / "state", stability_seconds=0,
                publisher_fn=fake_publish,
            )
            self.assertEqual(first["status"], "PUSHED")
            self.assertEqual(second["status"], "IDEMPOTENT")
            self.assertEqual(len(calls), 1)
            self.assertEqual(snapshot.read_text(encoding="utf-8"), original)

    def test_stale_dated_file_is_not_selected_without_explicit_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_root, snapshot, calendar, quotes, _ = make_fixture(root)
            old = snapshot.stat().st_mtime - 7200
            os.utime(snapshot, (old, old))
            result = runner.run_once(
                snapshot_root, calendar, quotes, root,
                state_dir=root / "state", max_age_seconds=60,
                stability_seconds=0,
                publisher_fn=lambda *args, **kwargs: self.fail("stale snapshot selected"),
            )
            self.assertEqual(result["status"], "WAITING_SNAPSHOT")

    def test_publisher_failure_is_recorded_and_not_retried_for_same_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_root, _, calendar, quotes, _ = make_fixture(root)
            calls = []

            def failing_publish(*args, **kwargs):
                calls.append(True)
                raise RuntimeError("PUBLISH_FAILED_FIXTURE")

            first = runner.run_once(
                snapshot_root, calendar, quotes, root,
                state_dir=root / "state", stability_seconds=0,
                publisher_fn=failing_publish,
            )
            second = runner.run_once(
                snapshot_root, calendar, quotes, root,
                state_dir=root / "state", stability_seconds=0,
                publisher_fn=failing_publish,
            )
            self.assertEqual(first["status"], "FAILED")
            self.assertEqual(first["failureReason"], "RuntimeError")
            self.assertEqual(second["status"], "IDEMPOTENT")
            self.assertEqual(second["previousStatus"], "FAILED")
            self.assertEqual(len(calls), 1)

    def test_launchagent_is_watch_based_and_not_fixed_schedule(self):
        plist = Path(__file__).parent / "ops/launchd/com.hanull.monitoring-kis-candidate-handoff.plist"
        raw = plist.read_text(encoding="utf-8")
        self.assertIn("com.hanull.monitoring-kis-candidate-handoff", raw)
        self.assertIn("<key>WatchPaths</key>", raw)
        self.assertNotIn("StartInterval", raw)
        self.assertNotIn("StartCalendarInterval", raw)
        self.assertNotIn("trading-console-candidates", raw)
        self.assertIn("--allow-network", raw)


if __name__ == "__main__":
    unittest.main()
