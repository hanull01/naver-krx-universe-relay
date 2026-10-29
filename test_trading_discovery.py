import json
import tempfile
import unittest
from pathlib import Path

import trading_discovery as td


def snapshot(symbols=None, monitoring="2026-10-12", source="KIWOOM_WATCHLIST"):
    symbols = ["005930", "000660"] if symbols is None else symbols
    payload = {
        "schema_version": 1, "status": "AVAILABLE" if symbols else "AVAILABLE_EMPTY",
        "selection_date": "2026-10-09", "monitoring_date": monitoring,
        "source": source,
        "source_group": ("전일후보군" if source == "KIWOOM_WATCHLIST"
                         else "장종료후 단타후보군 탐색"),
        "group_id": "001" if source == "KIWOOM_WATCHLIST" else "CONDITION:0",
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

    def test_condition_search_contract_preserves_alphanumeric_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cal = calendar(root / "calendar.json")
            payload = snapshot(["0197X0", "005930"], source="KIWOOM_CONDITION_SEARCH")
            original_hash = payload["source_sha256"]
            td.validate_snapshot(payload, cal)
            published = td.published_payload(payload, cal)
            self.assertEqual(published["source"], "KIWOOM_CONDITION_SEARCH")
            self.assertEqual(published["sourceGroup"], "장종료후 단타후보군 탐색")
            self.assertEqual(published["groupId"], "CONDITION:0")
            self.assertEqual(published["symbols"], ["0197X0", "005930"])
            self.assertEqual(published["sourceSha256"], original_hash)

    def test_condition_search_rejects_wrong_group_sequence_and_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cal = calendar(root / "calendar.json")
            cases = []
            wrong_group = snapshot(source="KIWOOM_CONDITION_SEARCH")
            wrong_group["source_group"] = "전일후보군"; cases.append(wrong_group)
            wrong_sequence = snapshot(source="KIWOOM_CONDITION_SEARCH")
            wrong_sequence["group_id"] = "001"; cases.append(wrong_sequence)
            wrong_time = snapshot(source="KIWOOM_CONDITION_SEARCH")
            wrong_time["synced_at"] = "2026-10-09T07:10:00+00:00"; cases.append(wrong_time)
            for payload in cases:
                payload["source_sha256"] = td.source_hash(payload)
                with self.assertRaises(td.DiscoveryError):
                    td.validate_snapshot(payload, cal)

    def test_sources_with_same_membership_have_distinct_hashes_and_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cal = calendar(root / "calendar.json")
            watchlist = snapshot(["005930"])
            condition = snapshot(["005930"], source="KIWOOM_CONDITION_SEARCH")
            self.assertNotEqual(watchlist["source_sha256"], condition["source_sha256"])
            one = root / "watchlist.json"; one.write_text(json.dumps(watchlist))
            two = root / "condition.json"; two.write_text(json.dumps(condition))
            td.publish_snapshot(one, root / "out", cal)
            before = (root / "out/2026-10-12.json").read_bytes()
            with self.assertRaisesRegex(td.DiscoveryError, "CONFLICT"):
                td.publish_snapshot(two, root / "out", cal)
            self.assertEqual((root / "out/2026-10-12.json").read_bytes(), before)

    def test_condition_search_empty_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cal = calendar(root / "calendar.json")
            payload = snapshot([], source="KIWOOM_CONDITION_SEARCH")
            td.validate_snapshot(payload, cal)
            self.assertEqual(payload["status"], "AVAILABLE_EMPTY")

    def test_missing_stale_and_corrupt_are_section_local(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / "latest.json"
            self.assertEqual(td.discovery_status("2026-10-12", path)["status"], "UNAVAILABLE")
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(td.discovery_status("2026-10-12", path)["status"], "INVALID")
            path.write_text(json.dumps(td.published_payload(snapshot())), encoding="utf-8")
            self.assertEqual(td.discovery_status("2026-10-13", path)["status"], "STALE")

    def test_default_reader_uses_report_date_and_never_falls_back_to_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dated = td.published_payload(snapshot(["042510", "064400", "253590"], monitoring="2026-10-29"))
            future = td.published_payload(snapshot(["000810"], monitoring="2026-10-30"))
            (root / "2026-10-29.json").write_text(json.dumps(dated), encoding="utf-8")
            (root / "latest.json").write_text(json.dumps(future), encoding="utf-8")
            original = td.DISCOVERY_DIR
            td.DISCOVERY_DIR = root
            try:
                selected = td.discovery_status("2026-10-29")
                self.assertEqual(selected["status"], "AVAILABLE")
                self.assertEqual(selected["symbols"], ["042510", "064400", "253590"])
                self.assertTrue(selected["sourcePath"].endswith("2026-10-29.json"))

                (root / "2026-10-29.json").unlink()
                missing = td.discovery_status("2026-10-29")
                self.assertEqual(missing["status"], "UNAVAILABLE")
                self.assertTrue(missing["sourcePath"].endswith("2026-10-29.json"))
            finally:
                td.DISCOVERY_DIR = original

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

    def test_final_baseline_replaces_stale_after_quote_and_leaves_etf_unavailable(self):
        discovery = td.published_payload(snapshot(["051910", "0197X0"]))
        stale_rows = [{
            "itemCode": code, "stockName": code, "closePrice": price,
            "sourceTime": "2026-10-09T20:00:00+09:00",
            "fresh": False, "status": "stale", "priceBasis": "PREVIOUS_KRX_CLOSE",
            "_discoveryAdditionalLookup": True,
        } for code, price in (("051910", 281500), ("0197X0", 6515))]
        baseline = {
            "asOfDate": "20261009", "historyStatus": "OK", "count": 1,
            "authoritativeCount": 1, "coveragePct": 100.0,
            "refreshDiagnostics": {"missingCodes": [], "extraCodes": []},
            "stocks": [{
                "code": "051910", "name": "LG화학", "lastTradingDate": "20261009",
                "lastClose": 282500, "closeValid": True, "ohlcValid": True,
                "ma5": 265700, "ma20": 267475, "ma60": 265191.67,
            }],
        }
        result = td.analyze("2026-10-12", discovery, {"datas": stale_rows}, {}, {},
                            final_baseline=baseline)
        stock, etf = result["stocks"]
        self.assertEqual(stock["source"], "FULL_MARKET_FINAL")
        self.assertEqual(stock["price"], 282500)
        self.assertEqual(stock["priceSourceDate"], "2026-10-09")
        self.assertEqual(stock["priceBasis"], "VERIFIED_FULL_MARKET_FINAL")
        self.assertEqual(stock["priceStatus"], "AVAILABLE")
        self.assertEqual(stock["technicalStatus"], "AVAILABLE")
        self.assertEqual(stock["analysisStatus"], "PARTIAL")
        self.assertEqual(etf["dataStatus"], "UNAVAILABLE")
        self.assertEqual(etf["rejectedQuoteReason"], "STALE_OR_UNVERIFIED_QUOTE")
        self.assertIsNone(etf["price"])

    def test_invalid_or_wrong_date_final_baseline_is_not_used(self):
        discovery = td.published_payload(snapshot(["051910"]))
        baseline = {
            "asOfDate": "20261008", "historyStatus": "OK", "count": 1,
            "authoritativeCount": 1, "coveragePct": 100.0,
            "refreshDiagnostics": {"missingCodes": [], "extraCodes": []},
            "stocks": [{"code": "051910", "lastTradingDate": "20261008",
                         "lastClose": 282500, "closeValid": True, "ohlcValid": True}],
        }
        result = td.analyze("2026-10-12", discovery, {}, {}, {}, final_baseline=baseline)
        self.assertEqual(result["finalBaseline"]["status"], "INVALID")
        self.assertEqual(result["stocks"][0]["dataStatus"], "UNAVAILABLE")

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
