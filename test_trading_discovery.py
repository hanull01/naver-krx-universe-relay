import json
import tempfile
import unittest
from pathlib import Path

import trading_discovery as td


def snapshot(symbols=None, monitoring="2026-10-12"):
    symbols = ["005930", "000660"] if symbols is None else symbols
    return {
        "schema_version": 1, "status": "AVAILABLE" if symbols else "AVAILABLE_EMPTY",
        "selection_date": "2026-10-09", "monitoring_date": monitoring,
        "source": "KIWOOM_WATCHLIST", "source_group": "전일후보군", "group_id": "001",
        "synced_at": "2026-10-09T16:10:00+09:00", "candidate_count": len(symbols),
        "symbols": symbols, "source_sha256": "a" * 64,
        "validation": {"status": "VALID", "calendar_source": "KRX_OFFICIAL", "immutable": True},
    }


class TradingDiscoveryTests(unittest.TestCase):
    def test_publish_minimal_schema_and_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(json.dumps(snapshot()), encoding="utf-8")
            first = td.publish_snapshot(source, root / "out")
            second = td.publish_snapshot(source, root / "out")
            self.assertEqual(first["result"], "CREATED")
            self.assertEqual(second["result"], "IDEMPOTENT")
            payload = json.loads((root / "out/latest.json").read_text())
            self.assertEqual(payload["symbols"], ["005930", "000660"])
            self.assertNotIn("token", payload)
            self.assertNotIn("account", payload)

    def test_publish_conflict_preserves_existing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            one, two = root / "one.json", root / "two.json"
            one.write_text(json.dumps(snapshot()), encoding="utf-8")
            changed = snapshot(["035420"]); changed["source_sha256"] = "b" * 64
            two.write_text(json.dumps(changed), encoding="utf-8")
            td.publish_snapshot(one, root / "out")
            before = (root / "out/2026-10-12.json").read_bytes()
            with self.assertRaisesRegex(td.DiscoveryError, "CONFLICT"):
                td.publish_snapshot(two, root / "out")
            self.assertEqual((root / "out/2026-10-12.json").read_bytes(), before)

    def test_empty_is_available_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.json"
            source.write_text(json.dumps(snapshot([])), encoding="utf-8")
            td.publish_snapshot(source, root / "out")
            got = td.discovery_status("2026-10-12", root / "out/latest.json")
            self.assertEqual(got["status"], "AVAILABLE_EMPTY")
            self.assertEqual(got["candidateCount"], 0)

    def test_missing_stale_and_corrupt_are_section_local(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / "latest.json"
            self.assertEqual(td.discovery_status("2026-10-12", path)["status"], "UNAVAILABLE")
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(td.discovery_status("2026-10-12", path)["status"], "INVALID")
            path.write_text(json.dumps(td.published_payload(snapshot())), encoding="utf-8")
            self.assertEqual(td.discovery_status("2026-10-13", path)["status"], "STALE")

    def test_analysis_reuses_core_then_full_market_and_isolates_missing(self):
        discovery = td.published_payload(snapshot(["005930", "035420", "999999"]))
        quotes = {"datas": [{"itemCode": "005930", "stockName": "삼성전자", "closePrice": 100,
                              "fluctuationsRatio": 1, "accumulatedTradingVolume": 10,
                              "accumulatedTradingValue": 1000}]}
        technicals = {"datas": [{"itemCode": "005930", "ma5": 90, "ma20": 80, "ma60": 70}]}
        states = {"datas": [{"itemCode": "005930", "breakout20": "confirmed"}]}
        signals = {"stocks": [{"code": "035420", "name": "NAVER", "currentPrice": 200,
                                 "changeRate": 2, "current_volume_ratio_20": 1.5,
                                 "breakout_20_intraday": True}]}
        result = td.analyze("2026-10-12", discovery, quotes, technicals, states, signals)
        self.assertEqual([row["source"] for row in result["stocks"]],
                         ["MONITORED_UNIVERSE", "FULL_MARKET_SIGNAL", None])
        self.assertEqual(result["stocks"][-1]["dataStatus"], "UNAVAILABLE")
        self.assertEqual(result["metrics"]["vwap"], "UNAVAILABLE")

    def test_previous_report_change(self):
        discovery = td.published_payload(snapshot(["005930"]))
        quote = {"datas": [{"itemCode": "005930", "closePrice": 110}]}
        previous = {"stocks": [{"itemCode": "005930", "price": 100,
                                  "dataStatus": "PARTIAL", "breakout20": "none"}]}
        row = td.analyze("2026-10-12", discovery, quote, {}, {}, previous=previous)["stocks"][0]
        self.assertEqual(row["previousReportChange"]["price"], 10)

    def test_bounded_fetch_requests_only_missing_once(self):
        calls = []
        def fetcher(codes):
            calls.append(codes)
            return {"datas": [{"itemCode": code} for code in codes]}
        result = td.fetch_missing_quotes(
            ["005930", "035420", "035420"], {"005930"}, fetcher,
            lambda row, now: {**row, "sourceTime": now}, lambda: "now")
        self.assertEqual(calls, [["035420"]])
        self.assertEqual(result["networkCalls"], 1)
        self.assertEqual(len(result["datas"]), 1)

    def test_no_missing_symbols_has_zero_network_calls(self):
        result = td.fetch_missing_quotes(["005930"], {"005930"},
                                         lambda _: self.fail("network called"),
                                         lambda row, now: row, lambda: "now")
        self.assertEqual(result["networkCalls"], 0)


if __name__ == "__main__":
    unittest.main()
