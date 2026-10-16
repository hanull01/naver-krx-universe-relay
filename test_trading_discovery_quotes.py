import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import trading_discovery as td

SPEC = importlib.util.spec_from_file_location(
    "collect_trading_discovery_quotes",
    Path(__file__).parent / "tools/collect_trading_discovery_quotes.py")
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)
KST = ZoneInfo("Asia/Seoul")


def discovery(symbols=None):
    symbols = symbols if symbols is not None else [
        "007660", "0197X0", "043260", "051910", "058470",
        "353200", "364980", "373220", "455850",
    ]
    payload = {
        "schemaVersion": 1, "status": "AVAILABLE" if symbols else "AVAILABLE_EMPTY",
        "monitoringDate": "2026-10-12", "selectionDate": "2026-10-08",
        "source": "KIWOOM_WATCHLIST", "sourceGroup": "전일후보군", "groupId": "001",
        "syncedAt": "2026-10-11T17:05:13+09:00", "candidateCount": len(symbols),
        "symbols": symbols, "sourceSha256": "0" * 64,
        "validation": {"status": "VALID", "calendarSource": "KRX_OFFICIAL", "immutable": True},
    }
    payload["sourceSha256"] = td.source_hash({
        "source": payload["source"], "source_group": payload["sourceGroup"],
        "group_id": payload["groupId"], "selection_date": payload["selectionDate"],
        "symbols": payload["symbols"],
    })
    return payload


def quote(code):
    return {
        "itemCode": code, "stockName": code, "closePrice": 100,
        "fluctuationsRatio": 1.0, "accumulatedTradingVolume": 10,
        "accumulatedTradingValue": 1000, "source": "NAVER_KRX",
        "sourceTime": "2026-10-12T10:00:00+09:00", "marketStatus": "OPEN",
        "session": "REGULAR", "priceBasis": "REALTIME", "fresh": True, "status": "ok",
    }


def quotes_payload(rows, source_time="2026-10-12T10:00:00+09:00"):
    return {"sourceTime": source_time, "status": "ok", "fresh": True, "datas": rows}


class SupplementalDiscoveryQuoteTests(unittest.TestCase):
    def test_production_hash_contract_recomputes_expected_value(self):
        payload = json.loads((Path(__file__).parent / "data/trading-discovery/2026-10-12.json").read_text())
        material = {
            "source": payload["source"], "source_group": payload["sourceGroup"],
            "group_id": payload["groupId"], "selection_date": payload["selectionDate"],
            "symbols": payload["symbols"],
        }
        self.assertEqual(td.source_hash(material),
                         "09616de26156b84977af7c1f7c4b1710364ddc5a12a43a1efe1235da3d141aa8")

    def test_reuses_four_core_one_signal_and_fetches_only_four_once(self):
        core = ["007660", "058470", "353200", "373220"]
        quotes = quotes_payload([quote(code) for code in core])
        signals = {"generatedAt": "2026-10-12T01:00:01+00:00",
                   "stocks": [{"code": "043260"}]}
        calls = []
        def fetcher(codes):
            calls.append(codes)
            return {"datas": [quote(code) for code in codes]}
        result = td.build_supplemental_quotes(
            discovery(), quotes, signals, fetcher=fetcher,
            normalizer=lambda row, _now: row,
            clock=lambda: datetime(2026, 10, 12, 10, 1, tzinfo=KST))
        self.assertEqual(calls, [["0197X0", "051910", "364980", "455850"]])
        self.assertEqual(result["networkCalls"], 1)
        self.assertEqual(result["status"], "AVAILABLE")
        self.assertEqual(result["quoteCount"], 4)

    def test_partial_and_total_failure_are_explicit(self):
        quotes = quotes_payload([])
        partial = td.build_supplemental_quotes(
            discovery(["0197X0", "051910"]), quotes, {},
            fetcher=lambda _codes: {"datas": [quote("0197X0")]},
            normalizer=lambda row, _now: row,
            clock=lambda: datetime(2026, 10, 12, 10, 1, tzinfo=KST))
        self.assertEqual(partial["status"], "PARTIAL")
        self.assertEqual(partial["missingSymbols"], ["051910"])
        failed = td.build_supplemental_quotes(
            discovery(["0197X0"]), quotes, {},
            fetcher=lambda _codes: (_ for _ in ()).throw(OSError("offline")),
            normalizer=lambda row, _now: row,
            clock=lambda: datetime(2026, 10, 12, 10, 1, tzinfo=KST))
        self.assertEqual(failed["status"], "UNAVAILABLE")

    def test_zero_candidates_and_all_reused_make_zero_network_calls(self):
        now = lambda: datetime(2026, 10, 12, 10, 1, tzinfo=KST)
        for candidate, rows in ((discovery([]), []), (discovery(["007660"]), [quote("007660")])):
            result = td.build_supplemental_quotes(
                candidate, quotes_payload(rows), {},
                fetcher=lambda _: self.fail("network called"),
                normalizer=lambda row, _now: row, clock=now)
            self.assertEqual(result["networkCalls"], 0)
            self.assertEqual(result["status"], "AVAILABLE_EMPTY")

    def test_stale_signal_is_not_reused(self):
        calls = []
        result = td.build_supplemental_quotes(
            discovery(["043260"]),
            quotes_payload([]),
            {"generatedAt": "2026-10-08T01:00:00+00:00", "stocks": [{"code": "043260"}]},
            fetcher=lambda codes: calls.append(codes) or {"datas": [quote("043260")]},
            normalizer=lambda row, _now: row,
            clock=lambda: datetime(2026, 10, 12, 10, 1, tzinfo=KST))
        self.assertEqual(calls, [["043260"]])
        self.assertEqual(result["status"], "AVAILABLE")

    def test_previous_day_market_quotes_are_rejected_after_open(self):
        candidate = discovery(["251340"]); candidate["monitoringDate"] = "2026-10-16"
        with self.assertRaisesRegex(td.DiscoveryError, "MARKET_QUOTES_DATE_MISMATCH"):
            td.build_supplemental_quotes(candidate,
                quotes_payload([quote("251340")], "2026-10-15T15:30:00+09:00"), {},
                fetcher=lambda _: self.fail("stale source must not be reused"),
                normalizer=lambda row, _now: row,
                clock=lambda: datetime(2026, 10, 16, 9, 25, tzinfo=KST))

    def test_supplemental_quote_date_mismatch_is_stale(self):
        candidate = discovery(["251340"]); candidate["monitoringDate"] = "2026-10-16"
        payload = {"schemaVersion": 1, "status": "AVAILABLE", "monitoringDate": "2026-10-16",
            "candidateSourceSha256": candidate["sourceSha256"],
            "marketSourceTime": "2026-10-16T09:25:00+09:00", "generatedAt": "2026-10-16T09:25:01+09:00",
            "source": "NAVER_KRX", "requestedCount": 1, "quoteCount": 1,
            "missingSymbols": [], "errors": [],
            "datas": [{**quote("251340"), "sourceTime": "2026-10-15T15:30:00+09:00"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "latest.json"; path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual("STALE", td.supplemental_quotes_status(candidate, payload["marketSourceTime"], path=path)["status"])

    def test_fresh_premarket_previous_close_is_preserved_as_market_source_time(self):
        result = td.build_supplemental_quotes(
            discovery(["0197X0"]),
            quotes_payload([], "2026-10-08T20:00:00+09:00"), {},
            fetcher=lambda codes: {"datas": [quote(code) for code in codes]},
            normalizer=lambda row, _now: row,
            clock=lambda: datetime(2026, 10, 12, 8, 1, tzinfo=KST))
        self.assertEqual(result["marketSourceTime"], "2026-10-08T20:00:00+09:00")
        self.assertEqual(result["status"], "AVAILABLE")

    def test_collector_same_market_time_is_noop_and_does_not_refetch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = root / "out"
            d = root / "discovery.json"; d.write_text(json.dumps(discovery()))
            q = root / "quotes.json"; q.write_text(json.dumps(quotes_payload(
                [quote(code) for code in ["007660", "058470", "353200", "373220"]])))
            s = root / "signals.json"; s.write_text(json.dumps({
                "generatedAt": "2026-10-12T01:00:00+00:00", "stocks": [{"code": "043260"}]}))
            calls = []
            fetcher = lambda codes: calls.append(codes) or {"datas": [quote(code) for code in codes]}
            first = collector.collect(d, q, s, output, fetcher=fetcher,
                                      normalizer=lambda row, _now: row,
                                      clock=lambda: datetime(2026, 10, 12, 10, 1, tzinfo=KST))
            second = collector.collect(d, q, s, output,
                                       fetcher=lambda _: self.fail("duplicate network call"),
                                       normalizer=lambda row, _now: row)
            self.assertEqual(first["result"], "PUBLISHED")
            self.assertEqual(second["result"], "NOOP")
            self.assertEqual(len(calls), 1)

    def test_total_failure_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = root / "out"; output.mkdir()
            latest = output / "latest.json"; latest.write_text('{"old":true}\n')
            before = latest.read_bytes()
            d = root / "discovery.json"; d.write_text(json.dumps(discovery(["0197X0"])))
            q = root / "quotes.json"; q.write_text(json.dumps(quotes_payload([])))
            s = root / "signals.json"; s.write_text("{}")
            with self.assertRaisesRegex(td.DiscoveryError, "COLLECTION_UNAVAILABLE"):
                collector.collect(d, q, s, output,
                                  fetcher=lambda _: (_ for _ in ()).throw(OSError("offline")),
                                  normalizer=lambda row, _now: row)
            self.assertEqual(latest.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
