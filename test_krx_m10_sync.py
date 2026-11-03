import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.krx_m10_sync import (
    deliver_pending_once,
    inspect_current_generation,
    stage_current_generation,
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class KrxM10SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.outbox = self.root / "outbox"
        self.receiver = self.root / "receiver"
        self.write_generation("gen_20261102_a", "20261102")

    def tearDown(self):
        self.tmp.cleanup()

    def write_generation(self, generation, date, *, metrics_extra=None):
        runtime = self.source / "krx_m9_runtime_data"
        gen = runtime / "generations" / generation
        consumers = gen / "consumers"
        engine = gen / "engine"
        consumers.mkdir(parents=True, exist_ok=True)
        engine.mkdir(parents=True, exist_ok=True)
        monitoring = {
            "schema": "krx_monitoring_interchange_v1", "source": "KRX_OPEN_API",
            "date": date, "aftermarket_used": False, "entity_count": 2,
            "entities": [
                {"identity": "stk_bydd_trd:005930", "code": "005930", "category": "stock", "returns_pct": {"return_1d_pct": "1.2"}},
                {"identity": "stk_bydd_trd:000660", "code": "000660", "category": "stock", "returns_pct": {"return_1d_pct": None}},
            ],
        }
        trading = {"schema": "krx_trading_interchange_v1", "date": date, "entity_count": 2}
        metrics = {"schema": "krx_metrics_v1", "date": date, "metrics": {"stk_bydd_trd:005930": {"relative_strength_v1_pct": "1.2", **(metrics_extra or {})}}}
        monitoring_path = consumers / "monitoring_interchange.json"
        trading_path = consumers / "trading_interchange.json"
        metrics_path = engine / "latest_metrics.json"
        monitoring_path.write_text(json.dumps(monitoring), encoding="utf-8")
        trading_path.write_text(json.dumps(trading), encoding="utf-8")
        metrics_path.write_text(json.dumps(metrics), encoding="utf-8")
        manifest = {
            "schema": "krx_m9_generation_manifest_v1", "generation": generation,
            "date": date, "metrics_date": date, "entity_count": 2,
            "consumer_counts": {"monitoring": 2, "trading": 2},
            "artifacts": {
                "monitoring": {"bytes": monitoring_path.stat().st_size, "sha256": digest(monitoring_path)},
                "trading": {"bytes": trading_path.stat().st_size, "sha256": digest(trading_path)},
                "metrics": {"bytes": metrics_path.stat().st_size, "sha256": digest(metrics_path)},
            },
        }
        (gen / "generation_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "CURRENT").write_text(generation + "\n", encoding="utf-8")

    def test_inspection_reviews_internal_metrics_without_transmitting_them(self):
        result = inspect_current_generation(self.source)
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["entityCount"], 2)
        self.assertEqual(result["extensionReview"]["status"], "REVIEW_ONLY_NOT_TRANSMITTED")
        self.assertEqual(result["directionExcluded"][0]["reason"], "HISTORY_BARS_LT_2_NO_PREVIOUS_AVAILABLE_CLOSE")

    def test_private_stage_and_receiver_are_idempotent(self):
        staged = stage_current_generation(self.source, self.outbox, execution_id="exec-1")
        package = Path(staged["package"])
        self.assertEqual(staged["status"], "STAGED_NOT_SENT")
        self.assertEqual(
            sorted(path.name for path in package.iterdir()),
            ["generation_manifest.json", "monitoring_interchange.json", "transfer_manifest.json"],
        )
        accepted = deliver_pending_once(self.outbox, self.receiver)
        self.assertEqual(accepted["status"], "ACCEPTED")
        self.assertEqual((self.receiver / "CURRENT").read_text().strip(), "gen_20261102_a")
        self.assertEqual(deliver_pending_once(self.outbox, self.receiver)["status"], "NO_CHANGES")
        staged_again = stage_current_generation(self.source, self.outbox, execution_id="exec-2")
        self.assertEqual(staged_again["status"], "STAGED_NOT_SENT")
        self.assertEqual(deliver_pending_once(self.outbox, self.receiver)["status"], "NO_CHANGES")

    def test_failed_delivery_is_durable_and_recovers_on_next_run(self):
        stage_current_generation(self.source, self.outbox, execution_id="exec-1")
        failed = deliver_pending_once(self.outbox, self.receiver, failure_injection=True)
        self.assertEqual(failed["status"], "FAILED")
        self.assertFalse((self.receiver / "CURRENT").exists())
        recovered = deliver_pending_once(self.outbox, self.receiver)
        self.assertEqual(recovered["status"], "ACCEPTED")

    def test_tampered_package_is_rejected_without_promotion(self):
        staged = stage_current_generation(self.source, self.outbox, execution_id="exec-1")
        package = Path(staged["package"])
        (package / "monitoring_interchange.json").write_text("{}", encoding="utf-8")
        result = deliver_pending_once(self.outbox, self.receiver)
        self.assertEqual(result["status"], "FAILED")
        self.assertFalse((self.receiver / "CURRENT").exists())

    def test_same_date_different_generation_requires_review(self):
        stage_current_generation(self.source, self.outbox, execution_id="exec-1")
        self.assertEqual(deliver_pending_once(self.outbox, self.receiver)["status"], "ACCEPTED")
        self.write_generation("gen_20261102_b", "20261102")
        stage_current_generation(self.source, self.outbox, execution_id="exec-2")
        result = deliver_pending_once(self.outbox, self.receiver)
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("SAME_DATE_DIFFERENT_GENERATION", result["error"])


if __name__ == "__main__":
    unittest.main()
