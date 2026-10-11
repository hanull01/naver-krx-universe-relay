import json
import tempfile
import unittest
from pathlib import Path

import trading_discovery as td


def snapshot(symbols=None, monitoring="2026-10-12"):
    symbols = ["005930", "000660"] if symbols is None else symbols
    payload = {
        "schema_version": 1, "status": "AVAILABLE" if symbols else "AVAILABLE_EMPTY",
        "selection_date": "2026-10-09", "monitoring_date": monitoring,
        "source": "KIWOOM_WATCHLIST", "source_group": "전일후보군", "group_id": "001",
        "synced_at": "2026-10-09T16:10:00+09:00", "candidate_count": len(symbols),
        "symbols": symbols, "source_sha256": "0" * 64,
        "validation": {"status": "VALID", "calendar_source": "KRX_OFFICIAL", "immutable": True},
    }
    payload["source_sha256"] = td.source_hash(payload)
    return payload


def calendar(path):
    path.write_text(json.dumps({"source": "KRX_OFFICIAL",
                                "dates": ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-15"]}), encoding="utf-8")
    return path


class TradingDiscoveryTests(unittest.TestCase):
    def test_publish_minimal_schema_and_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(json.dumps(snapshot()), encoding="utf-8")
            cal = calendar(root / "calendar.json")
            first = td.publish_snapshot(source, root / "out", cal)
            second = td.publish_snapshot(source, root / "out", cal)
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
            changed = snapshot(["035420"])
            two.write_text(json.dumps(changed), encoding="utf-8")
            cal = calendar(root / "calendar.json")
            td.publish_snapshot(one, root / "out", cal)
            before = (root / "out/2026-10-12.json").read_bytes()
            with self.assertRaisesRegex(td.DiscoveryError, "CONFLICT"):
                td.publish_snapshot(two, root / "out", cal)
            self.assertEqual((root / "out/2026-10-12.json").read_bytes(), before)

    def test_empty_is_available_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / "source.json"
            source.write_text(json.dumps(snapshot([])), encoding="utf-8")
            td.publish_snapshot(source, root / "out", calendar(root / "calendar.json"))
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

    def test_missing_quote_network_failure_is_returned_not_raised(self):
        def fail(_codes):
            raise OSError("offline")
        result = td.fetch_missing_quotes(
            ["999999"], set(), fail, lambda row, now: row, lambda: "now")
        self.assertEqual(result["networkCalls"], 1)
        self.assertEqual(result["requestedSymbols"], ["999999"])
        self.assertEqual(result["errors"], [{"reason": "OSError"}])

    def test_hash_count_duplicate_date_and_sensitive_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cal = calendar(root / "calendar.json")
            cases = []
            bad_hash = snapshot(); bad_hash["source_sha256"] = "f" * 64; cases.append(bad_hash)
            bad_count = snapshot(); bad_count["candidate_count"] = 99; cases.append(bad_count)
            duplicate = snapshot(["005930", "005930"]); cases.append(duplicate)
            bad_date = snapshot(monitoring="2026-10-13"); cases.append(bad_date)
            sensitive = snapshot(); sensitive["accountToken"] = "do-not-publish"; cases.append(sensitive)
            for index, payload in enumerate(cases):
                source = root / f"bad-{index}.json"
                source.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(td.DiscoveryError):
                    td.publish_snapshot(source, root / f"out-{index}", cal)

    def test_friday_monday_and_holiday_next_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cal = calendar(root / "calendar.json")
            td.validate_snapshot(snapshot(), cal)
            holiday = snapshot(monitoring="2026-10-15")
            holiday["selection_date"] = "2026-10-13"
            holiday["source_sha256"] = td.source_hash(holiday)
            td.validate_snapshot(holiday, cal)


if __name__ == "__main__":
    unittest.main()
