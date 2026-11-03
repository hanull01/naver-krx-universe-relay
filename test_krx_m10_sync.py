import hashlib
import json
import fcntl
import tempfile
import unittest
from pathlib import Path

from tools.krx_m10_sync import (
    deliver_pending_once,
    inspect_current_generation,
    stage_current_generation,
    validate_authenticated_envelope,
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

    def test_past_trading_date_is_rejected_without_current_regression(self):
        self.write_generation("gen_20261103_a", "20261103")
        stage_current_generation(self.source, self.outbox, execution_id="exec-new")
        self.assertEqual(deliver_pending_once(self.outbox, self.receiver)["status"], "ACCEPTED")
        self.write_generation("gen_20261102_b", "20261102")
        stage_current_generation(self.source, self.outbox, execution_id="exec-old")
        result = deliver_pending_once(self.outbox, self.receiver)
        self.assertEqual(result["status"], "REJECTED_STALE")
        self.assertEqual((self.receiver / "CURRENT").read_text().strip(), "gen_20261103_a")

    def test_generation_copy_interruption_recovers_from_staging(self):
        stage_current_generation(self.source, self.outbox, execution_id="exec-1")
        failed = deliver_pending_once(self.outbox, self.receiver, failure_point="generation_copy")
        self.assertEqual(failed["status"], "FAILED")
        self.assertFalse((self.receiver / "CURRENT").exists())
        recovered = deliver_pending_once(self.outbox, self.receiver)
        self.assertEqual(recovered["status"], "ACCEPTED")

    def test_generation_promotion_interruption_recovers_without_deleting_generation(self):
        stage_current_generation(self.source, self.outbox, execution_id="exec-1")
        failed = deliver_pending_once(self.outbox, self.receiver, failure_point="generation_promote")
        self.assertEqual(failed["status"], "FAILED")
        self.assertTrue((self.receiver / "generations" / "gen_20261102_a").is_dir())
        self.assertFalse((self.receiver / "CURRENT").exists())
        recovered = deliver_pending_once(self.outbox, self.receiver)
        self.assertEqual(recovered["status"], "NO_CHANGES")
        self.assertEqual((self.receiver / "CURRENT").read_text().strip(), "gen_20261102_a")

    def test_current_write_interruption_recovers_from_pending_ledger(self):
        stage_current_generation(self.source, self.outbox, execution_id="exec-1")
        failed = deliver_pending_once(self.outbox, self.receiver, failure_point="current_write")
        self.assertEqual(failed["status"], "FAILED")
        self.assertFalse((self.receiver / "CURRENT").exists())
        recovered = deliver_pending_once(self.outbox, self.receiver)
        self.assertEqual(recovered["status"], "NO_CHANGES")
        self.assertEqual((self.receiver / "CURRENT").read_text().strip(), "gen_20261102_a")

    def test_current_write_after_commit_interruption_is_idempotent(self):
        stage_current_generation(self.source, self.outbox, execution_id="exec-1")
        failed = deliver_pending_once(self.outbox, self.receiver, failure_point="after_current")
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual((self.receiver / "CURRENT").read_text().strip(), "gen_20261102_a")
        recovered = deliver_pending_once(self.outbox, self.receiver)
        self.assertEqual(recovered["status"], "NO_CHANGES")

    def test_receiver_ledger_save_failure_preserves_previous_current(self):
        self.write_generation("gen_20261101_a", "20261101")
        stage_current_generation(self.source, self.outbox, execution_id="exec-old")
        self.assertEqual(deliver_pending_once(self.outbox, self.receiver)["status"], "ACCEPTED")
        previous = (self.receiver / "CURRENT").read_text()
        self.write_generation("gen_20261102_a", "20261102")
        stage_current_generation(self.source, self.outbox, execution_id="exec-new")
        failed = deliver_pending_once(self.outbox, self.receiver, failure_point="receiver_ledger_save")
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual((self.receiver / "CURRENT").read_text(), previous)
        recovered = deliver_pending_once(self.outbox, self.receiver)
        self.assertEqual(recovered["status"], "NO_CHANGES")
        self.assertEqual((self.receiver / "CURRENT").read_text().strip(), "gen_20261102_a")

    def test_same_package_concurrent_receive_is_single_instance(self):
        stage_current_generation(self.source, self.outbox, execution_id="exec-1")
        self.receiver.mkdir(parents=True, exist_ok=True)
        with (self.receiver / ".sync.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = deliver_pending_once(self.outbox, self.receiver)
        self.assertEqual(result["status"], "ALREADY_RUNNING")
        self.assertEqual(deliver_pending_once(self.outbox, self.receiver)["status"], "ACCEPTED")

    def test_restart_reconciles_ledger_current_mismatch_forward_only(self):
        stage_current_generation(self.source, self.outbox, execution_id="exec-1")
        self.assertEqual(deliver_pending_once(self.outbox, self.receiver, failure_point="current_write")["status"], "FAILED")
        state = json.loads((self.receiver / "received_state.json").read_text())
        self.assertIsNotNone(state["pendingPromotion"])
        recovered = deliver_pending_once(self.outbox, self.receiver)
        self.assertEqual(recovered["status"], "NO_CHANGES")
        state = json.loads((self.receiver / "received_state.json").read_text())
        self.assertIsNone(state["pendingPromotion"])

    def test_existing_generation_is_immutable_when_new_generation_arrives(self):
        stage_current_generation(self.source, self.outbox, execution_id="exec-1")
        self.assertEqual(deliver_pending_once(self.outbox, self.receiver)["status"], "ACCEPTED")
        old_monitoring = (self.receiver / "generations" / "gen_20261102_a" / "monitoring_interchange.json").read_bytes()
        self.write_generation("gen_20261103_a", "20261103")
        stage_current_generation(self.source, self.outbox, execution_id="exec-2")
        self.assertEqual(deliver_pending_once(self.outbox, self.receiver)["status"], "ACCEPTED")
        self.assertEqual(
            old_monitoring,
            (self.receiver / "generations" / "gen_20261102_a" / "monitoring_interchange.json").read_bytes(),
        )

    def test_tampered_current_generation_fails_closed(self):
        stage_current_generation(self.source, self.outbox, execution_id="exec-1")
        self.assertEqual(deliver_pending_once(self.outbox, self.receiver)["status"], "ACCEPTED")
        current_file = self.receiver / "generations" / "gen_20261102_a" / "monitoring_interchange.json"
        current_file.write_text("tampered", encoding="utf-8")
        self.write_generation("gen_20261103_a", "20261103")
        stage_current_generation(self.source, self.outbox, execution_id="exec-2")
        result = deliver_pending_once(self.outbox, self.receiver)
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("RECEIVER_CURRENT_INTEGRITY_MISMATCH", result["error"])
        self.assertEqual((self.receiver / "CURRENT").read_text().strip(), "gen_20261102_a")

    def test_authenticated_transport_requires_sender_signature_and_replay_protection(self):
        envelope = {"sender": "krx-private-producer", "transferId": "t-1", "expiresAt": 200}
        result = validate_authenticated_envelope(
            envelope,
            allowed_senders={"krx-private-producer"},
            replay_ids=set(),
            verifier=lambda value: value is envelope,
            now_epoch=100,
        )
        self.assertTrue(result["authenticated"])
        with self.assertRaisesRegex(ValueError, "UNAUTHORIZED_SENDER"):
            validate_authenticated_envelope(
                {**envelope, "sender": "unknown"},
                allowed_senders={"krx-private-producer"},
                replay_ids=set(),
                verifier=lambda value: True,
                now_epoch=100,
            )
        with self.assertRaisesRegex(ValueError, "REPLAYED_TRANSFER"):
            validate_authenticated_envelope(
                envelope,
                allowed_senders={"krx-private-producer"},
                replay_ids={"t-1"},
                verifier=lambda value: True,
                now_epoch=100,
            )
        with self.assertRaisesRegex(ValueError, "AUTHENTICATION_FAILED"):
            validate_authenticated_envelope(
                envelope,
                allowed_senders={"krx-private-producer"},
                replay_ids=set(),
                verifier=lambda value: False,
                now_epoch=100,
            )


if __name__ == "__main__":
    unittest.main()
