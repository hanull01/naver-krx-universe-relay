import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import trading_discovery as td
from tools import report_contract


KST = ZoneInfo("Asia/Seoul")
SYMBOLS = ["003230", "003490", "017670", "018260"]


def write_calendar(root):
    path = Path(root) / "calendar.json"
    path.write_text(json.dumps({
        "source": "KRX_OFFICIAL",
        "dates": ["2026-10-12", "2026-10-13"],
    }), encoding="utf-8")
    return path


def official_snapshot(root, *, symbols=None, monitoring="2026-10-13"):
    symbols = list(symbols or SYMBOLS)
    payload = {
        "schema_version": 1,
        "status": "AVAILABLE" if symbols else "AVAILABLE_EMPTY",
        "selection_date": "2026-10-12",
        "monitoring_date": monitoring,
        "source": "KIWOOM_CONDITION_SEARCH",
        "source_group": "장종료후 단타후보군 탐색",
        "group_id": "CONDITION:0",
        "synced_at": "2026-10-12T21:00:01+09:00",
        "candidate_count": len(symbols),
        "symbols": symbols,
        "source_sha256": "",
        "validation": {"status": "VALID", "calendar_source": "KRX_OFFICIAL", "immutable": True},
    }
    payload["source_sha256"] = td.source_hash(payload)
    path = Path(root) / f"{monitoring}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path, payload


def market_quotes():
    return {
        "sourceTime": "2026-10-13T10:00:00+09:00",
        "status": "ok",
        "fresh": True,
        "datas": [],
    }


def quote(symbol):
    return {
        "itemCode": symbol,
        "stockName": f"종목-{symbol}",
        "closePrice": 1000,
        "fluctuationsRatio": 1.0,
        "accumulatedTradingVolume": 10,
        "accumulatedTradingValue": 10000,
        "source": "NAVER_KRX",
        "sourceTime": "2026-10-13T10:00:00+09:00",
        "marketStatus": "OPEN",
        "session": "REGULAR",
        "priceBasis": "REALTIME",
        "fresh": True,
        "status": "ok",
    }


class KISCandidateDeliveryTests(unittest.TestCase):
    def test_exact_date_reader_does_not_fallback_to_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous, _ = official_snapshot(root)
            self.assertTrue(previous.exists())
            with self.assertRaisesRegex(td.DiscoveryError, "SNAPSHOT_MISSING"):
                td.load_official_snapshot_for_date(
                    "2026-10-14", root=root, calendar_path=write_calendar(root)
                )

    def test_official_snapshot_rejects_path_date_hash_and_duplicate_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, payload = official_snapshot(root)
            calendar = write_calendar(root)
            with self.assertRaisesRegex(td.DiscoveryError, "SNAPSHOT_PATH_DATE_MISMATCH"):
                td.load_official_snapshot(path, expected_monitoring_date="2026-10-12", calendar_path=calendar)

            tampered = copy.deepcopy(payload)
            tampered["source_sha256"] = "0" * 64
            path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(td.DiscoveryError, "SNAPSHOT_HASH_MISMATCH"):
                td.load_official_snapshot(path, calendar_path=calendar)

            duplicate = copy.deepcopy(payload)
            duplicate["symbols"] = [SYMBOLS[0], SYMBOLS[0]]
            duplicate["candidate_count"] = 2
            duplicate["source_sha256"] = td.source_hash(duplicate)
            path.write_text(json.dumps(duplicate), encoding="utf-8")
            with self.assertRaisesRegex(td.DiscoveryError, "SNAPSHOT_COUNT_INVALID"):
                td.load_official_snapshot(path, calendar_path=calendar)

    def _prepared(self, root, returned):
        snapshot_path, _ = official_snapshot(root)
        calendar = write_calendar(root)
        snapshot = td.load_official_snapshot(snapshot_path, calendar_path=calendar)
        discovery = td.published_payload(snapshot, calendar)
        calls = []

        def fetcher(codes):
            calls.append(list(codes))
            return {"datas": [quote(code) for code in returned]}

        payload = td.build_supplemental_quotes(
            discovery, market_quotes(), {}, fetcher=fetcher,
            normalizer=lambda row, _now: row,
            clock=lambda: datetime(2026, 10, 13, 10, 1, tzinfo=KST),
        )
        return snapshot_path, calendar, discovery, payload, calls

    def test_quote_zero_partial_and_all_requests_are_lineage_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for returned, expected_status, expected_count in (
                ([], "PARTIAL", 0),
                (SYMBOLS[:2], "PARTIAL", 2),
                (SYMBOLS, "AVAILABLE", 4),
            ):
                _, _, discovery, payload, calls = self._prepared(root, returned)
                self.assertEqual(calls, [SYMBOLS])
                self.assertEqual(payload["requestedCount"], len(SYMBOLS))
                self.assertEqual(payload["quoteCount"], expected_count)
                self.assertEqual(payload["status"], expected_status)
                self.assertEqual(payload["sourceHash"], discovery["sourceSha256"])
                self.assertTrue(payload["paper_only"])
                self.assertEqual(
                    {row["symbol"] for row in payload["candidateStatuses"]},
                    set(SYMBOLS),
                )
                self.assertTrue(all(row["quoteRequested"] is True for row in payload["candidateStatuses"]))
                self.assertTrue(all(row["paper_only"] is True for row in payload["candidateStatuses"]))

    def test_consumer_writes_candidate_and_quote_artifacts_without_touching_kis_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_path, _ = official_snapshot(root)
            calendar = write_calendar(root)
            before = snapshot_path.read_bytes()
            result = td.consume_official_snapshot(
                snapshot_path, market_quotes(), calendar,
                discovery_output_dir=root / "relay",
                quote_output_dir=root / "relay" / "quotes",
                expected_monitoring_date="2026-10-13",
                fetcher=lambda codes: {"datas": [quote(code) for code in codes[:2]]},
                normalizer=lambda row, _now: row,
                clock=lambda: datetime(2026, 10, 13, 10, 1, tzinfo=KST),
            )
            self.assertEqual(result["status"], "PARTIAL")
            self.assertTrue((root / "relay/2026-10-13.json").exists())
            self.assertTrue((root / "relay/latest.json").exists())
            self.assertTrue((root / "relay/quotes/2026-10-13.json").exists())
            self.assertTrue((root / "relay/quotes/latest.json").exists())
            self.assertEqual(snapshot_path.read_bytes(), before)

    def test_quote_publication_is_idempotent_and_protects_other_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, discovery, payload, _ = self._prepared(root, SYMBOLS[:2])
            output = root / "quotes"
            first = td.publish_supplemental_quote_artifact(payload, discovery, output)
            second = td.publish_supplemental_quote_artifact(payload, discovery, output)
            self.assertEqual(first["result"], "CREATED")
            self.assertEqual(second["result"], "IDEMPOTENT")
            other_discovery = copy.deepcopy(discovery)
            other_discovery["symbols"] = SYMBOLS[:-1] + ["035420"]
            other_discovery["candidateCount"] = len(other_discovery["symbols"])
            other_discovery["sourceSha256"] = td.source_hash({
                "source": other_discovery["source"],
                "source_group": other_discovery["sourceGroup"],
                "group_id": other_discovery["groupId"],
                "selection_date": other_discovery["selectionDate"],
                "symbols": other_discovery["symbols"],
            })
            other_payload = td.build_supplemental_quotes(
                other_discovery, market_quotes(), {},
                fetcher=lambda codes: {"datas": [quote(code) for code in codes]},
                normalizer=lambda row, _now: row,
                clock=lambda: datetime(2026, 10, 13, 10, 1, tzinfo=KST),
            )
            protected = td.publish_supplemental_quote_artifact(other_payload, other_discovery, output)
            self.assertEqual(protected["result"], "REJECTED_IDENTITY_CONFLICT")

    def test_report_projection_keeps_candidate_values_and_quote_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_path, calendar, discovery, quotes, _ = self._prepared(root, SYMBOLS)
            details = [
                {"symbol": code, "selectionValue": 10,
                 "technicalIndicators": {"ma20": 9},
                 "conditionDetails": {"matched": True},
                 "selectionStatus": "AVAILABLE", "source": "KIWOOM_CONDITION_SEARCH",
                 "generatedAt": "2026-10-12T21:00:01+09:00"}
                for code in SYMBOLS
            ]
            discovery["candidateDetails"] = details
            analyzed = td.analyze(
                "2026-10-13", discovery, quotes, {}, {}, {}, {},
            )
            projected = report_contract.compact_kiwoom_candidate_rows(
                analyzed["stocks"], analyzed["candidateDetails"]
            )
            self.assertEqual(len(projected), len(SYMBOLS))
            self.assertEqual(
                [row["candidateSelectionValue"] for row in projected], [10] * len(SYMBOLS)
            )
            self.assertTrue(all(row["candidateTechnicalIndicators"] == {"ma20": 9} for row in projected))
            self.assertTrue(all(row["candidateSourceHash"] == quotes["sourceHash"] for row in projected))
            self.assertTrue(all(row["candidatePaperOnly"] is True for row in projected))
            self.assertNotIn("ma60", projected[0]["candidateTechnicalIndicators"])


if __name__ == "__main__":
    unittest.main()
