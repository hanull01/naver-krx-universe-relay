import hashlib
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
            "generatedAt": generated,
            "source": {"targetDate": "20261030"},
        }
        projection = {"asOf": "20261030", "observedAt": generated, "provenance": {"fallbackUsed": False}}
        registered_technicals = {"targetDate": "20261030", "generatedAt": generated, "fallbackUsed": False}
        registered_states = {"targetDate": "20261030", "generatedAt": generated, "fallbackUsed": False}
        provenance_payload = {"targetDate": "20261030", "fallbackUsed": False, "byCode": {}}
        for name, payload in {
            "technicals.json": tech, "states.json": state,
            "group-states.json": group, "quality.json": quality,
            "rolling-history-manifest.json": rolling,
            "report-contract-projection.json": projection,
            "registered-universe-technicals.json": registered_technicals,
            "registered-universe-states.json": registered_states,
            "provenance.json": provenance_payload,
        }.items():
            (self.run / name).write_text(json.dumps(payload), encoding="utf-8")
        rolling_path = self.run / "rolling-history" / "STOCK" / "005930.jsonl"
        rolling_path.write_text(
            json.dumps({"code": "005930", "date": "20261030", "close": 70000,
                        "high": 71000, "open": 69000, "low": 68000, "volume": 1000}) + "\n",
            encoding="utf-8",
        )
        rolling["byCode"]["005930"].update({
            "bytes": rolling_path.stat().st_size,
            "sha256": self._sha256(rolling_path),
            "latest": "20261030",
        })
        (self.run / "rolling-history-manifest.json").write_text(
            json.dumps(rolling), encoding="utf-8"
        )
        manifest["files"] = {
            name: self._file_meta(name)
            for name in (
                "technicals.json", "states.json", "registered-universe-technicals.json",
                "registered-universe-states.json", "group-states.json", "provenance.json",
                "quality.json",
            )
        }
        (self.run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        source_artifacts = {
            name: self._file_meta(name)
            for name in (
                "technicals.json", "states.json", "registered-universe-technicals.json",
                "registered-universe-states.json", "group-states.json", "provenance.json",
                "quality.json", "manifest.json", "rolling-history-manifest.json",
            )
        }
        report_manifest = {
            "schemaVersion": 1,
            "contract": self._file_meta("report-contract-projection.json"),
            "sourceArtifacts": source_artifacts,
            "targetDate": "20261030",
            "status": "PARTIAL",
        }
        (self.run / "report-contract-manifest.json").write_text(
            json.dumps(report_manifest), encoding="utf-8"
        )
        (self.date_dir / "latest.json").write_text(json.dumps({
            "targetDate": "20261030", "runId": "41355451393",
            "path": "41355451393/report-contract-manifest.json",
        }), encoding="utf-8")

    @staticmethod
    def _sha256(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _file_meta(self, name):
        path = self.run / name
        return {"path": name, "bytes": path.stat().st_size, "sha256": self._sha256(path)}

    def _refresh_report_artifact_meta(self, name):
        manifest_path = self.run / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if name in manifest.get("files", {}):
            manifest["files"][name] = self._file_meta(name)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        report_path = self.run / "report-contract-manifest.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["sourceArtifacts"][name] = self._file_meta(name)
        report["sourceArtifacts"]["manifest.json"] = self._file_meta("manifest.json")
        report_path.write_text(json.dumps(report), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolves_pointer_and_generation(self):
        result = resolve_derived_input(self.root, "20261030")
        self.assertEqual(result["runId"], "41355451393")
        self.assertEqual(result["targetDate"], "20261030")
        self.assertEqual(result["runDir"], self.run.resolve())
        self.assertFalse(result["generationDiagnostic"]["generatedAtMismatch"])

    def test_same_manifest_hashes_are_accepted(self):
        result = resolve_derived_input(self.root, "20261030")
        report_manifest = result["artifacts"]["report-contract-manifest.json"]
        self.assertEqual(
            report_manifest["sourceArtifacts"]["technicals.json"]["sha256"],
            self._sha256(self.run / "technicals.json"),
        )
        self.assertEqual(result["runId"], "41355451393")

    def test_microsecond_generated_at_difference_is_diagnostic_only(self):
        group = self.run / "group-states.json"
        payload = json.loads(group.read_text(encoding="utf-8"))
        payload["generatedAt"] = "2026-11-02T07:01:00.000121+09:00"
        group.write_text(json.dumps(payload), encoding="utf-8")
        self._refresh_report_artifact_meta("group-states.json")

        result = resolve_derived_input(self.root, "20261030")
        self.assertTrue(result["generationDiagnostic"]["generatedAtMismatch"])
        self.assertEqual(
            result["generationDiagnostic"]["generatedAtByArtifact"]["group-states.json"],
            "2026-11-02T07:01:00.000121+09:00",
        )

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

    def test_manifest_source_date_is_required(self):
        manifest = self.run / "manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["source"].pop("targetDate")
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(DerivedInputError, "DERIVED_MANIFEST_SOURCE_DATE_MISSING"):
            resolve_derived_input(self.root, "20261030")

    def test_manifest_source_date_mismatch_is_rejected(self):
        manifest = self.run / "manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["source"]["targetDate"] = "20261029"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(DerivedInputError, "DERIVED_TARGET_DATE_MISMATCH:manifest.json"):
            resolve_derived_input(self.root, "20261030")

    def test_manifest_run_id_mismatch_is_rejected(self):
        manifest = self.run / "manifest.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["runId"] = "different-run"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(DerivedInputError, "DERIVED_MANIFEST_RUN_ID_MISMATCH"):
            resolve_derived_input(self.root, "20261030")

    def test_mixed_run_id_artifact_is_rejected(self):
        report_path = self.run / "report-contract-manifest.json"
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        payload["sourceArtifacts"]["technicals.json"]["runId"] = "other-run"
        report_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(DerivedInputError, "DERIVED_ARTIFACT_RUN_ID_MISMATCH"):
            resolve_derived_input(self.root, "20261030")

    def test_manifest_sha_mismatch_is_rejected(self):
        technicals = self.run / "technicals.json"
        payload = json.loads(technicals.read_text(encoding="utf-8"))
        payload["tampered"] = True
        technicals.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(DerivedInputError, "DERIVED_ARTIFACT_SHA256_MISMATCH:technicals.json"):
            resolve_derived_input(self.root, "20261030")

    def test_fallback_used_is_rejected(self):
        states = self.run / "states.json"
        payload = json.loads(states.read_text(encoding="utf-8"))
        payload["fallbackUsed"] = True
        states.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(DerivedInputError, "DERIVED_FALLBACK_USED:states.json"):
            resolve_derived_input(self.root, "20261030")

    def test_future_rolling_history_is_rejected(self):
        rolling_path = self.run / "rolling-history-manifest.json"
        payload = json.loads(rolling_path.read_text(encoding="utf-8"))
        payload["byCode"]["005930"]["latest"] = "20261031"
        rolling_path.write_text(json.dumps(payload), encoding="utf-8")
        self._refresh_report_artifact_meta("rolling-history-manifest.json")
        with self.assertRaisesRegex(DerivedInputError, "ROLLING_HISTORY_FUTURE_DATA:005930"):
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
