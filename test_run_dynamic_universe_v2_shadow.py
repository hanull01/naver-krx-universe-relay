import json
import tempfile
import unittest
from pathlib import Path

from tools.run_dynamic_universe_v2_shadow import _enrich_persistence, _history_context, run_once


class V2RunnerIsolationTests(unittest.TestCase):
    def test_failure_is_recorded_without_raising(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/dynamic-universe").mkdir(parents=True)
            outcome = run_once(root, "2026-10-15", "2026-10-15T01:00:00Z")
            self.assertEqual(outcome["status"], "FAILED")
            self.assertEqual(json.loads((root / "data/dynamic-universe/v2-shadow-run-status.json").read_text())["status"], "FAILED")
            self.assertFalse((root / "data/dynamic-universe/latest.json").exists())

    def test_existing_history_is_connected_to_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history_path = root / "data/dynamic-universe/v2-shadow-history.json"
            history_path.parent.mkdir(parents=True)
            history_path.write_text(json.dumps({"observations": [
                {"tradingDate": "2026-10-27", "industries": [{"id": "100"}], "themes": []},
                {"tradingDate": "2026-10-28", "industries": [{"id": "100"}], "themes": []},
            ]}))
            context = _history_context(root, "2026-10-28")
            payload = _enrich_persistence(
                {"industries": [{"id": "100", "persistence": {"availableTradingDays": 1}}]},
                "industries", context)
            row = payload["industries"][0]
            self.assertEqual(row["persistence"]["availableTradingDays"], 2)
            self.assertEqual(row["persistence"]["status"], "AVAILABLE")
            self.assertEqual(row["historyPreviousTradingDate"], "2026-10-27")
