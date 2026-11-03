import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.full_market_technical_adapter import build_report_input, load_full_market_staging
from tools.krx_sync_receipt import KrxReceiptError, map_m10_entity, stage_m10_report_inputs, validate_m10_staging


class KrxSyncReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.monitoring = {
            "schema": "krx_monitoring_interchange_v1",
            "source": "KRX_OPEN_API",
            "date": "20261102",
            "price_reference": "KRX_OFFICIAL_1530_CLOSE",
            "aftermarket_used": False,
            "entity_count": 1,
            "source_metrics_gzip_sha256": "a" * 64,
            "entities": [self.entity()],
        }
        self.write_stage()

    @staticmethod
    def entity():
        return {
            "code": "005930", "name": "삼성전자", "category": "stock",
            "identity": "stk_bydd_trd:005930", "asof": "20261102",
            "source_api": "stk_bydd_trd", "open_regular": "225000",
            "close_regular": "225000", "official_daily_volume": "19668075",
            "MA": {"MA5": "220000", "MA20": "210000", "MA60": None, "MA120": None, "MA240": None},
            "returns_pct": {"return_1d_pct": "1.2", "return_5d_pct": None, "return_20d_pct": None,
                            "return_60d_pct": None, "return_120d_pct": None, "return_240d_pct": None},
            "high_breakouts": {"high_breakout_prev20": False, "high_breakout_prev60": False},
            "volume_ratio_prev20": "0.8",
        }

    def write_stage(self):
        self.root.mkdir(exist_ok=True)
        monitoring_path = self.root / "monitoring_interchange.json"
        manifest_path = self.root / "generation_manifest.json"
        monitoring_path.write_text(json.dumps(self.monitoring, ensure_ascii=False), encoding="utf-8")
        manifest = {"schema": "krx_m9_generation_manifest_v1", "generation": "gen_20261102_test",
                    "date": "20261102", "entity_count": len(self.monitoring["entities"]), "artifacts": {}}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.receipt = {
            "schemaVersion": 2, "source": "KRX_OPEN_API", "tradingDate": "20261102",
            "generationId": "gen_20261102_test", "generationStatus": "VERIFIED",
            "publicationAuthorized": True,
            "artifacts": {
                "monitoring": {"name": "monitoring_interchange.json", "sha256": hashlib.sha256(monitoring_path.read_bytes()).hexdigest(), "bytes": monitoring_path.stat().st_size},
                "generationManifest": {"name": "generation_manifest.json", "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(), "bytes": manifest_path.stat().st_size},
            },
        }
        manifest["artifacts"] = {"monitoring": {"sha256": hashlib.sha256(monitoring_path.read_bytes()).hexdigest(), "bytes": monitoring_path.stat().st_size}}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.receipt["artifacts"]["generationManifest"] = {"name": "generation_manifest.json", "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(), "bytes": manifest_path.stat().st_size}
        (self.root / "receipt.json").write_text(json.dumps(self.receipt), encoding="utf-8")

    def test_actual_m10_shape_is_validated(self):
        result = validate_m10_staging(self.root, expected_date="2026-11-02")
        self.assertEqual(result["status"], "VALIDATED_NOT_CONNECTED")
        self.assertEqual(result["entityCount"], 1)
        self.assertFalse(result["reportInputReady"])

    def test_mapping_keeps_krx_regular_session_and_no_current_quote(self):
        mapped = map_m10_entity(self.entity())
        self.assertEqual(mapped["regularSession"]["basis"], "KRX_OFFICIAL_1530_CLOSE")
        self.assertFalse(mapped["regularSession"]["aftermarketUsed"])
        self.assertIsNone(mapped["currentSnapshot"])
        self.assertEqual(mapped["technicals"]["ma20"], "210000")

    def test_publication_interlock_is_fail_closed(self):
        self.receipt["publicationAuthorized"] = False
        (self.root / "receipt.json").write_text(json.dumps(self.receipt), encoding="utf-8")
        with self.assertRaisesRegex(KrxReceiptError, "REDISTRIBUTION_NOT_AUTHORIZED"):
            validate_m10_staging(self.root)

    def test_generation_mismatch_rejected(self):
        self.receipt["generationId"] = "another_generation"
        (self.root / "receipt.json").write_text(json.dumps(self.receipt), encoding="utf-8")
        with self.assertRaisesRegex(KrxReceiptError, "GENERATION_MANIFEST_MISMATCH"):
            validate_m10_staging(self.root)

    def test_index_identifier_is_not_forced_to_six_characters(self):
        self.monitoring["entities"] = [dict(self.entity(), code="KOSPI", name="KOSPI", category="index", identity="kospi_dd_trd:KOSPI", source_api="kospi_dd_trd")]
        self.write_stage()
        self.assertEqual(validate_m10_staging(self.root)["entityCount"], 1)

    def test_verified_stage_feeds_existing_report_adapter_without_current_quotes(self):
        output = self.root / "report-stage"
        result = stage_m10_report_inputs(self.root, output)
        self.assertEqual(result["status"], "STAGED_NOT_CONNECTED")
        loaded = load_full_market_staging(
            output / "technicals.json", output / "states.json", output / "group-states.json",
            expected_target_date="20261102")
        self.assertEqual(loaded["targetDate"], "20261102")
        self.assertEqual(len(loaded["technicals"]), 1)
        universe = self.root / "universe.json"
        universe.write_text(json.dumps({"stocks": [{"enabled": True, "itemCode": "005930", "stockName": "삼성전자"}], "watchlists": {}}), encoding="utf-8")
        report_input = build_report_input(
            output / "technicals.json", output / "states.json", output / "group-states.json",
            universe, expected_target_date="20261102")
        self.assertTrue(report_input["currentSnapshotSeparated"])
        self.assertEqual(report_input["registeredUniverse"]["registeredCount"], 1)

    def test_duplicate_entity_code_is_rejected(self):
        self.monitoring["entities"] = [self.entity(), dict(self.entity(), identity="stk_bydd_trd:005931")]
        self.monitoring["entity_count"] = 2
        self.write_stage()
        with self.assertRaisesRegex(KrxReceiptError, "DUPLICATE_ENTITY_CODE"):
            validate_m10_staging(self.root)

    def test_duplicate_index_display_code_uses_source_identity(self):
        first = dict(self.entity(), code="IT 서비스", name="IT 서비스", category="index", identity="kospi_dd_trd:IT 서비스", source_api="kospi_dd_trd")
        second = dict(first, identity="kosdaq_dd_trd:IT 서비스", source_api="kosdaq_dd_trd")
        self.monitoring["entities"] = [first, second]
        self.monitoring["entity_count"] = 2
        self.write_stage()
        self.assertEqual(validate_m10_staging(self.root)["entityCount"], 2)


if __name__ == "__main__":
    unittest.main()
