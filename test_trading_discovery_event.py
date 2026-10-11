import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "on_trading_discovery_snapshot",
    Path(__file__).parent / "tools/on_trading_discovery_snapshot.py")
event = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(event)


class TradingDiscoveryEventTests(unittest.TestCase):
    def test_exact_snapshot_is_forwarded_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "2026-10-12.json"; snapshot.write_text("{}", encoding="utf-8")
            calendar = root / "calendar.json"; calendar.write_text("{}", encoding="utf-8")
            repository = root / "repo"; repository.mkdir()
            before = snapshot.read_bytes(); calls = []
            def publisher(*args, **kwargs):
                calls.append((args, kwargs)); return {"publishStatus": "NOOP"}
            result = event.handle(snapshot, calendar, repository,
                                  publisher=publisher, lock_root=root)
            self.assertEqual(result["publishStatus"], "NOOP")
            self.assertEqual(calls[0][0], (snapshot.resolve(), calendar.resolve(), repository.resolve()))
            self.assertEqual(snapshot.read_bytes(), before)

    def test_missing_input_and_concurrent_event_fail_before_publisher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); calendar = root / "calendar.json"; calendar.write_text("{}")
            repository = root / "repo"; repository.mkdir()
            with self.assertRaisesRegex(event.EventError, "SNAPSHOT_MISSING"):
                event.handle(root / "missing.json", calendar, repository, lock_root=root)
            snapshot = root / "snapshot.json"; snapshot.write_text(json.dumps({}))
            (root / "tc-kiwoom-discovery-publisher.lock").mkdir()
            with self.assertRaisesRegex(event.EventError, "ALREADY_RUNNING"):
                event.handle(snapshot, calendar, repository, lock_root=root)


if __name__ == "__main__":
    unittest.main()
