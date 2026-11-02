import json
import tempfile
import unittest
from pathlib import Path

from tools.derived_input_resolver import (
    DerivedInputError,
    RollingHistoryDailyLoader,
    filesystem_date,
    resolve_derived_input,
)


class DerivedInputResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "data" / "monitoring-derived"
        self.date_dir = self.root / "2026-10-30"
        self.run = self.date_dir / "41355451393"
        (self.run / "rolling-history" / "STOCK").mkdir(parents=True)
        generated = "2026-11-02T07:01:00+09:00"
        provenance = {
            "sourceStaging": "/tmp/backfill",
            "sourceCanonicalPath": "/tmp/backfill/canonical/daily/005930.jsonl",
            "sourceCanonicalSha256": "a" * 64,
        }
        tech = {
            "targetDate": "20261030", "generatedAt": generated,
            "fallbackUsed": False, "datas": [{
                "itemCode": "005930", "asOfDate": "20261030",
                "availabilityStatus": "AVAILABLE", "provenance": provenance,
            }],
        }
        state = {
            "targetDate": "20261030", "generatedAt": generated,
            "fallbackUsed": False, "datas": [{
                "itemCode": "005930", "asOfDate": "20261030",
                "availabilityStatus": "AVAILABLE", "provenance": provenance,
            }],
        }
        group = {"targetDate": "20261030", "generatedAt": generated, "groups": []}
        quality = {"targetDate": "20261030", "generatedAt": generated, "fallbackUsed": False}
        rolling = {
            "targetDate": "20261030", "generatedAt": generated, "window": 120,
            "fallbackUsed": False, "byCode": {"005930": {
                "path": "rolling-history/STOCK/005930.jsonl", "rowCount": 1,
            }},
        }
        manifest = {
            "targetDate": "20261030", "generatedAt": generated,
            "source": {"targetDate": "20261030"},
        }
        projection = {"asOf": "20261030", "observedAt": generated, "provenance": {"fallbackUsed": False}}
        for name, payload in {
            "technicals.json": tech, "states.json": state,
            "group-states.json": group, "quality.json": quality,
            "rolling-history-manifest.json": rolling, "manifest.json": manifest,
            "report-contract-projection.json": projection,
        }.items():
            (self.run / name).write_text(json.dumps(payload), encoding="utf-8")
        (self.run / "rolling-history" / "STOCK" / "005930.jsonl").write_text(
            json.dumps({"code": "005930", "date": "20261030", "close": 70000,
                        "high": 71000, "open": 69000, "low": 68000, "volume": 1000}) + "\n",
            encoding="utf-8",
        )
        (self.date_dir / "latest.json").write_text(json.dumps({
            "targetDate": "20261030", "runId": "41355451393",
            "path": "41355451393/manifest.json",
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolves_pointer_and_generation(self):
        result = resolve_derived_input(self.root, "20261030")
        self.assertEqual(result["runId"], "41355451393")
        self.assertEqual(result["targetDate"], "20261030")
        self.assertEqual(result["runDir"], self.run.resolve())

    def test_compact_and_dashed_target_dates_select_same_pointer(self):
        self.assertEqual(filesystem_date("20261030"), "2026-10-30")
        self.assertEqual(filesystem_date("2026-10-30"), "2026-10-30")
        compact = resolve_derived_input(self.root, "20261030")
        dashed = resolve_derived_input(self.root, "2026-10-30")
        self.assertEqual(compact["pointerPath"], dashed["pointerPath"])
        self.assertEqual(compact["runId"], dashed["runId"])

    def test_invalid_date_and_missing_pointer_fail(self):
        with self.assertRaisesRegex(DerivedInputError, "INVALID_TARGETDATE"):
            resolve_derived_input(self.root, "2026/10/30")
        with self.assertRaisesRegex(DerivedInputError, "DERIVED_JSON_READ_FAILED"):
            resolve_derived_input(self.root, "20261029")

    def test_pointer_run_id_mismatch_is_rejected(self):
        pointer = self.date_dir / "latest.json"
        pointer.write_text(json.dumps({
            "targetDate": "20261030", "runId": "wrong-run",
            "path": "41355451393/manifest.json",
        }), encoding="utf-8")
        with self.assertRaisesRegex(DerivedInputError, "DERIVED_POINTER_RUN_DIRECTORY_MISMATCH"):
            resolve_derived_input(self.root, "20261030")

    def test_missing_artifact_and_fallback_are_rejected(self):
        (self.run / "states.json").unlink()
        with self.assertRaisesRegex(DerivedInputError, "DERIVED_ARTIFACT_MISSING"):
            resolve_derived_input(self.root, "20261030")

    def test_loader_never_reads_legacy_daily_and_rejects_future_rows(self):
        resolved = resolve_derived_input(self.root, "20261030")
        loader = RollingHistoryDailyLoader(resolved)
        daily = loader.load("005930")
        self.assertEqual(daily["datas"][0]["barType"], "DERIVED_ROLLING_HISTORY")
        self.assertEqual(daily["datas"][0]["date"], "2026-10-30")
        self.assertIsNone(loader.load("000660"))


if __name__ == "__main__":
    unittest.main()
