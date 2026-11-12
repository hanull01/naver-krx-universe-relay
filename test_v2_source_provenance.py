import json
import tempfile
import unittest
from pathlib import Path

from tools.v2_source_provenance import build_source_provenance


def write_json(root: Path, relative: str, payload: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class V2SourceProvenanceTests(unittest.TestCase):
    def base_files(self, root: Path, *, generation="2026-10-29T05:00:00+00:00", date="20261029"):
        write_json(root, "data/monitoring/latest-breadth.json",
                   {"generatedAt": generation, "observationDate": date, "fullMarket": {}})
        write_json(root, "data/monitoring/latest-industries.json",
                   {"generatedAt": generation, "tradingDate": date, "industries": []})
        write_json(root, "data/monitoring/latest-theme-signals.json",
                   {"generatedAt": generation, "themes": []})
        write_json(root, "data/dynamic-universe/latest.json",
                   {"generatedAt": "2026-10-29T05:00:01+00:00", "sectors": [], "themes": []})
        return {"generatedAt": generation, "inputEvidence": {"tradingDate": date}}

    def test_all_input_bytes_are_hashed_but_missing_source_commit_is_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.base_files(root)
            provenance = build_source_provenance(root, result=result)
            self.assertEqual(provenance["status"], "PARTIAL")
            self.assertIsNone(provenance["sourceCommit"])
            self.assertEqual(provenance["sourceCommitStatus"], "UNAVAILABLE")
            self.assertEqual(len(provenance["inputSnapshotSha256"]), 64)
            self.assertTrue(all(item.get("sha256") for item in provenance["files"]))

    def test_missing_source_is_not_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.base_files(root)
            (root / "data/monitoring/latest-theme-signals.json").unlink()
            provenance = build_source_provenance(root, result=result)
            self.assertEqual(provenance["status"], "PARTIAL")
            theme = next(item for item in provenance["files"] if item["kind"] == "themes")
            self.assertEqual(theme["status"], "UNVERIFIED")

    def test_generation_or_date_mismatch_is_not_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.base_files(root, generation="2026-10-29T05:00:00+00:00")
            write_json(root, "data/monitoring/latest-industries.json",
                       {"generatedAt": "old", "tradingDate": "20261028", "industries": []})
            provenance = build_source_provenance(root, result=result)
            self.assertEqual(provenance["status"], "MISMATCH")
            industry = next(item for item in provenance["files"] if item["kind"] == "industries")
            self.assertEqual(industry["generationStatus"], "MISMATCH")
            self.assertEqual(industry["dateStatus"], "MISMATCH")

    def test_output_hashes_are_separate_from_input_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.base_files(root)
            write_json(root, "data/dynamic-universe/v2-shadow-latest.json", result)
            write_json(root, "data/dynamic-universe/v2-shadow-history.json", {"observations": []})
            provenance = build_source_provenance(root, result=result, include_outputs=True)
            self.assertEqual(len(provenance["resultSha256"]), 64)
            self.assertEqual(len(provenance["historySha256"]), 64)
            self.assertNotEqual(provenance["resultSha256"], provenance["inputSnapshotSha256"])

    def test_naver_lineage_is_carried_without_krx_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.base_files(root, generation="2026-11-13T00:00:00+00:00", date="20261113")
            result.update({
                "generatedAt": "2026-11-13T00:00:00+00:00",
                "observationDate": "20261113",
                "v2Lineage": {
                    "v2ObservationId": "v2obs-test",
                    "source": "NAVER_CURRENT_SNAPSHOT",
                    "sourceDate": "20261113",
                    "observationDate": "20261113",
                    "sourceHash": "b" * 64,
                    "selectionPolicyVersion": "v2-current-observation-20261113",
                    "producerStatus": "SUCCESS",
                    "consumptionStatus": "CONSUMED",
                },
            })
            provenance = build_source_provenance(root, result=result)
            self.assertEqual(provenance["lineage"]["source"], "NAVER_CURRENT_SNAPSHOT")
            self.assertEqual(provenance["lineage"]["v2ObservationId"], "v2obs-test")
            self.assertIsNone(provenance["calculationGeneration"])
            self.assertEqual(provenance["files"][0]["kind"], "breadth")


if __name__ == "__main__":
    unittest.main()
