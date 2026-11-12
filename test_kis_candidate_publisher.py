import json
import tempfile
import unittest
from pathlib import Path

import trading_discovery as td
from tools import publish_kis_candidate_delivery as publisher


SYMBOLS = ["003230", "003490", "017670"]


def snapshot(root):
    payload = {
        "schema_version": 1,
        "status": "AVAILABLE",
        "selection_date": "2026-10-12",
        "monitoring_date": "2026-10-13",
        "source": "KIWOOM_CONDITION_SEARCH",
        "source_group": "장종료후 단타후보군 탐색",
        "group_id": "CONDITION:0",
        "synced_at": "2026-10-12T21:00:01+09:00",
        "candidate_count": len(SYMBOLS),
        "symbols": SYMBOLS,
        "source_sha256": "",
        "validation": {"status": "VALID", "calendar_source": "KRX_OFFICIAL", "immutable": True},
    }
    payload["source_sha256"] = td.source_hash(payload)
    path = Path(root) / "2026-10-13.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path, payload


def empty_snapshot(root):
    path, payload = snapshot(root)
    payload["status"] = "AVAILABLE_EMPTY"
    payload["candidate_count"] = 0
    payload["symbols"] = []
    payload["source_sha256"] = td.source_hash(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path, payload


def calendar(root):
    path = Path(root) / "calendar.json"
    path.write_text(json.dumps({"source": "KRX_OFFICIAL", "dates": ["2026-10-12", "2026-10-13"]}), encoding="utf-8")
    return path


def quotes(root):
    path = Path(root) / "quotes.json"
    path.write_text(json.dumps({
        "sourceTime": "2026-10-13T10:00:00+09:00",
        "status": "ok",
        "fresh": True,
        "datas": [],
    }), encoding="utf-8")
    return path


class CandidatePublisherTests(unittest.TestCase):
    def test_refresh_consumes_published_artifacts_without_a_candidate_writer(self):
        workflow = (Path(__file__).parent / ".github/workflows/refresh.yml").read_text(encoding="utf-8")
        self.assertIn("pre-published by the monitoring-owned local handoff", workflow)
        self.assertNotIn("tools/consume_kis_candidate_snapshot.py", workflow.split("Build canonical market report contract", 1)[1].split("Build current observation", 1)[0])
        self.assertNotIn("tools/collect_trading_discovery_quotes.py", workflow)
        self.assertNotIn("steps.discovery_quotes", workflow)

    def test_local_handoff_writes_four_lineage_bound_files_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, payload = snapshot(root)
            result = publisher.safe_publish(
                source, calendar(root), quotes(root), root,
                monitoring_date="2026-10-13", dry_run=True,
            )
            self.assertEqual(result["publishStatus"], "DRY_RUN")
            self.assertEqual(result["lineage"]["candidateCount"], len(SYMBOLS))
            self.assertEqual(result["lineage"]["requestedCount"], len(SYMBOLS))
            self.assertEqual(result["lineage"]["quoteStatus"], "PARTIAL")
            self.assertEqual(payload["source_sha256"], result["sourceHash"])
            self.assertEqual(
                set(result["lineage"]),
                {"monitoringDate", "sourceHash", "candidateCount", "requestedCount", "quoteCount", "quoteStatus"},
            )

    def test_lineage_rejects_a_different_latest_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = snapshot(root)
            discovery = td.published_payload(td.load_official_snapshot(source, calendar_path=calendar(root)))
            out = root / "out"
            td.consume_official_snapshot(
                source, td.load_json(quotes(root)), calendar(root),
                discovery_output_dir=out / "data" / "trading-discovery",
                quote_output_dir=out / "data" / "trading-discovery" / "quotes",
                expected_monitoring_date="2026-10-13",
                fetcher=lambda _codes: {"datas": []},
                normalizer=lambda row, _now: row,
            )
            latest = out / "data/trading-discovery/quotes/latest.json"
            changed = json.loads(latest.read_text(encoding="utf-8"))
            changed["sourceHash"] = "f" * 64
            latest.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(publisher.PublishError, "QUOTE_LINEAGE_MISMATCH"):
                publisher.validate_delivery_lineage(out, discovery)

    def test_real_network_publish_requires_explicit_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = snapshot(root)
            with self.assertRaisesRegex(publisher.PublishError, "NETWORK_PERMISSION_REQUIRED"):
                publisher.safe_publish(
                    source, calendar(root), quotes(root), root,
                    monitoring_date="2026-10-13",
                )

    def test_empty_snapshot_does_not_require_or_read_previous_quotes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, payload = empty_snapshot(root)
            result = publisher.safe_publish(
                source, calendar(root), root / "missing-previous-quotes.json", root,
                monitoring_date="2026-10-13", dry_run=True,
            )
            self.assertEqual(result["publishStatus"], "DRY_RUN")
            self.assertEqual(result["lineage"]["candidateCount"], 0)
            self.assertEqual(result["lineage"]["requestedCount"], 0)
            self.assertEqual(result["lineage"]["quoteCount"], 0)
            self.assertEqual(result["quotes"]["status"], "AVAILABLE_EMPTY")


if __name__ == "__main__":
    unittest.main()
