import copy
import hashlib
import json
import unittest

from tools.krx_m10_sync import deliver_pending_once
from tools.krx_private_repo_source import (
    PrivateSourceError,
    load_verified_private_generation,
    materialize_private_generation_to_outbox,
)


class FixtureFetcher:
    def __init__(self, files):
        self.files = files
        self.paths = []

    def fetch(self, repository, ref, path):
        self.paths.append((repository, ref, path))
        return self.files[path]


def make_fixture():
    root = "data/krx_sync"
    generation = "gen_20261102_test"
    base = f"{root}/generations/{generation}"
    monitoring = {
        "schema": "krx_monitoring_interchange_v1",
        "source": "KRX_OPEN_API",
        "date": "20261102",
        "entity_count": 1,
        "entities": [{"identity": "stk_bydd_trd:005930", "code": "005930", "category": "stock"}],
    }
    report = {
        "schema": "krx_private_report_input_v1",
        "source": "KRX_OPEN_API",
        "generationId": generation,
        "tradingDate": "20261102",
        "entityCount": 1,
        "historicalIndicatorSource": "KRX",
        "historicalPriority": "PRIMARY",
        "historicalFallbackAllowed": False,
        "entities": [{"identity": "stk_bydd_trd:005930", "code": "005930", "category": "stock"}],
    }
    manifest = {"schema": "krx_m9_generation_manifest_v1", "generation": generation, "date": "20261102", "metrics_date": "20261102"}
    files = {
        f"{root}/CURRENT": json.dumps({"generationId": generation, "tradingDate": "20261102"}).encode(),
        f"{base}/generation_manifest.json": json.dumps(manifest).encode(),
        f"{base}/monitoring_interchange.json": json.dumps(monitoring).encode(),
        f"{base}/report_input.json": json.dumps(report).encode(),
    }
    files[f"{base}/private_sync_manifest.json"] = json.dumps({
        "schema": "krx_private_sync_manifest_v1", "generationId": generation, "tradingDate": "20261102",
        "entityCount": 1,
        "artifacts": {
            "generation_manifest": {"sha256": hashlib.sha256(files[f"{base}/generation_manifest.json"]).hexdigest(), "bytes": len(files[f"{base}/generation_manifest.json"])},
            "report_input": {"sha256": hashlib.sha256(files[f"{base}/report_input.json"]).hexdigest(), "bytes": len(files[f"{base}/report_input.json"])},
            "monitoring_interchange": {"sha256": hashlib.sha256(files[f"{base}/monitoring_interchange.json"]).hexdigest(), "bytes": len(files[f"{base}/monitoring_interchange.json"])},
        },
    }).encode()
    private = json.loads(files[f"{base}/private_sync_manifest.json"])
    transfer = {
        "schemaVersion": "krx_m10_private_transfer_v1", "publication": "PRIVATE_KIS_TRADING_ONLY",
        "transferId": f"{generation}:20261102",
        "generationId": generation, "tradingDate": "20261102", "rawIncluded": False,
        "entityCount": 1,
        "metricsIncluded": False, "aftermarketIncluded": False,
        "includedArtifacts": ["generation_manifest.json", "monitoring_interchange.json", "private_sync_manifest.json", "report_input.json"],
        "manifestSha256": hashlib.sha256(files[f"{base}/generation_manifest.json"]).hexdigest(), "manifestBytes": len(files[f"{base}/generation_manifest.json"]),
        "privateManifestSha256": hashlib.sha256(files[f"{base}/private_sync_manifest.json"]).hexdigest(), "privateManifestBytes": len(files[f"{base}/private_sync_manifest.json"]),
        "reportInputSha256": private["artifacts"]["report_input"]["sha256"], "reportInputBytes": private["artifacts"]["report_input"]["bytes"],
        "monitoringSha256": private["artifacts"]["monitoring_interchange"]["sha256"], "monitoringBytes": private["artifacts"]["monitoring_interchange"]["bytes"],
    }
    files[f"{base}/transfer_manifest.json"] = json.dumps(transfer).encode()
    return files


class PrivateRepoSourceTests(unittest.TestCase):
    def test_reads_only_allowlisted_private_files_and_verifies_contract(self):
        fetcher = FixtureFetcher(make_fixture())
        result = load_verified_private_generation(fetcher, repository="hanull01/KIS_Trading", ref="main")
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["generationId"], "gen_20261102_test")
        self.assertEqual(len(fetcher.paths), 6)
        self.assertTrue(all(path.startswith("data/krx_sync/") for _, _, path in fetcher.paths))

    def test_tampering_or_policy_violation_is_rejected(self):
        files = make_fixture()
        tampered = copy.deepcopy(files)
        base = "data/krx_sync/generations/gen_20261102_test"
        tampered[f"{base}/report_input.json"] = b"tampered"
        with self.assertRaisesRegex(PrivateSourceError, "JSON_INVALID|REPORT_INPUT_INTEGRITY"):
            load_verified_private_generation(FixtureFetcher(tampered), repository="hanull01/KIS_Trading")

        forbidden = copy.deepcopy(files)
        transfer_path = f"{base}/transfer_manifest.json"
        transfer = json.loads(forbidden[transfer_path])
        transfer["rawIncluded"] = True
        forbidden[transfer_path] = json.dumps(transfer).encode()
        with self.assertRaisesRegex(PrivateSourceError, "FORBIDDEN_ARTIFACT"):
            load_verified_private_generation(FixtureFetcher(forbidden), repository="hanull01/KIS_Trading")

    def test_path_traversal_is_rejected(self):
        files = make_fixture()
        with self.assertRaisesRegex(PrivateSourceError, "PATH_TRAVERSAL"):
            load_verified_private_generation(FixtureFetcher(files), repository="hanull01/KIS_Trading", root="data/krx_sync/../private")

    def test_private_source_materializes_into_existing_receiver(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            verified = load_verified_private_generation(FixtureFetcher(make_fixture()), repository="hanull01/KIS_Trading")
            staged = materialize_private_generation_to_outbox(verified, Path(tmp) / "outbox")
            result = deliver_pending_once(Path(tmp) / "outbox", Path(tmp) / "receiver")
            self.assertEqual(staged["status"], "STAGED_NOT_SENT")
            self.assertEqual(result["status"], "ACCEPTED")
            self.assertEqual((Path(tmp) / "receiver" / "CURRENT").read_text().strip(), "gen_20261102_test")


if __name__ == "__main__":
    unittest.main()
