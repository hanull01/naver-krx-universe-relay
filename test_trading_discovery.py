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
    payload["candidate_details"] = [
        {"symbol": code, "conditionDetails": {"matched": True},
         "selectionValue": 100, "technicalIndicators": {"ma20": 100},
         "currentPrice": None, "detailStatus": "PARTIAL"}
        for code in symbols
    ]
    return payload


def calendar(path):
    path.write_text(json.dumps({"source": "KRX_OFFICIAL",
                                "dates": ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-15"]}), encoding="utf-8")
    return path


def legacy(payload):
    result = json.loads(json.dumps(payload))
    result.pop("candidate_details", None)
    return result


def detailed_nine_candidate_snapshot():
    symbols = [
        "007660", "0197X0", "043260", "051910", "058470",
        "353200", "364980", "373220", "455850",
    ]
    payload = snapshot(symbols)
    payload["generated_at"] = "2026-10-11T21:01:03+09:00"
    payload["producer_commit"] = "producer-commit-20261011"
    payload["candidate_completeness"] = {
        "selection": {"status": "AVAILABLE", "available": 9, "total": 9},
        "technicalIndicators": {"status": "AVAILABLE", "available": 9, "total": 9},
        "conditionDetails": {"status": "AVAILABLE", "available": 9, "total": 9},
        "quote": {"status": "PARTIAL", "available": 7, "total": 9},
    }
    payload["candidate_details"] = []
    for index, symbol in enumerate(symbols):
        quoted = index < 7
        payload["candidate_details"].append({
            "symbol": symbol,
            "name": f"candidate-{index}",
            "selectionValue": 100 + index,
            "technicalIndicators": {"ma5": 95 + index, "ma20": 90 + index},
            "conditionDetails": {"matched": True, "conditionId": "CONDITION:0"},
            "currentPrice": 200 + index if quoted else None,
            "quoteStatus": "AVAILABLE" if quoted else "UNAVAILABLE",
            "failureReason": None if quoted else "HTTP_429",
            "detailStatus": "AVAILABLE",
        })
    return payload


class TradingDiscoveryTests(unittest.TestCase):
    def test_candidate_details_are_preserved_and_aligned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = snapshot(["005930"])
            payload["candidate_details"] = [{
                "symbol": "005930", "conditionName": "장종료후 단타후보군 탐색",
                "conditionResult": True, "discoveredAt": "2026-10-09T21:00:00+09:00",
                "sourceValues": {}, "sourceValueStatus": "NOT_PROVIDED",
            }]
            source = root / "source.json"; source.write_text(json.dumps(payload), encoding="utf-8")
            published = td.published_payload(payload, calendar(root / "calendar.json"))
            self.assertEqual(published["candidateDetails"][0]["conditionResult"], True)
            out = root / "out"; out.mkdir()
            (out / "latest.json").write_text(json.dumps(published), encoding="utf-8")
            got = td.discovery_status("2026-10-12", out / "latest.json")
            self.assertEqual(got["status"], "AVAILABLE")

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

    def test_symbol_only_artifact_is_safely_enriched_and_latest_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = legacy(detailed_nine_candidate_snapshot())
            new = detailed_nine_candidate_snapshot()
            old["synced_at"] = "2026-10-11T21:00:57+09:00"
            old["source_sha256"] = td.source_hash(old)
            new["source_sha256"] = td.source_hash(new)
            old_path, new_path = root / "old.json", root / "new.json"
            old_path.write_text(json.dumps(old), encoding="utf-8")
            new_path.write_text(json.dumps(new), encoding="utf-8")
            out = root / "out"
            cal = calendar(root / "calendar.json")

            td.publish_snapshot(old_path, out, cal)
            result = td.publish_snapshot(new_path, out, cal)
            dated = json.loads((out / "2026-10-12.json").read_text())
            latest = json.loads((out / "latest.json").read_text())
            self.assertEqual(result["result"], "ENRICHED")
            self.assertEqual(dated, latest)
            self.assertEqual(len(dated["candidateDetails"]), 9)
            self.assertEqual(dated["sourceSha256"], old["source_sha256"])
            self.assertEqual(dated["syncedAt"], old["synced_at"])
            self.assertEqual(dated["generatedAt"], new["generated_at"])
            self.assertEqual(dated["originalGeneratedAt"], old["generated_at"])
            self.assertEqual(dated["producerCommit"], new["producer_commit"])
            self.assertEqual(dated["enrichmentStatus"], "ENRICHED")
            self.assertEqual(dated["schemaTransition"]["from"], "SYMBOL_ONLY")
            self.assertEqual(dated["schemaTransition"]["to"], "CANDIDATE_DETAILS")
            self.assertEqual(dated["candidateDetails"][-1]["failureReason"], "HTTP_429")
            self.assertEqual(dated["candidateCompleteness"]["quote"],
                             {"status": "PARTIAL", "available": 7, "total": 9})
            self.assertEqual(sum(row["quoteStatus"] == "AVAILABLE"
                                 for row in dated["candidateDetails"]), 7)
            self.assertEqual(sum(row["failureReason"] == "HTTP_429"
                                 for row in dated["candidateDetails"]), 2)
            self.assertEqual(td.discovery_status("2026-10-12", out / "latest.json")["status"], "AVAILABLE")

    def test_enrichment_blocks_symbol_set_change_even_if_hash_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cal = calendar(root / "calendar.json")
            old = legacy(snapshot(["005930", "000660"]))
            new = snapshot(["005930", "000660"])
            old_path, new_path = root / "old.json", root / "new.json"
            old_path.write_text(json.dumps(old), encoding="utf-8")
            new_path.write_text(json.dumps(new), encoding="utf-8")
            out = root / "out"; td.publish_snapshot(old_path, out, cal)
            existing = json.loads((out / "2026-10-12.json").read_text())
            existing["symbols"] = ["005930", "035420"]
            (out / "2026-10-12.json").write_text(json.dumps(existing), encoding="utf-8")
            with self.assertRaisesRegex(td.DiscoveryError, "SYMBOL_SET_CONFLICT"):
                td.publish_snapshot(new_path, out, cal)

    def test_enrichment_blocks_source_or_date_identity_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cal = calendar(root / "calendar.json")
            old = legacy(snapshot(["005930"]))
            new = snapshot(["005930"])
            old_path, new_path = root / "old.json", root / "new.json"
            old_path.write_text(json.dumps(old), encoding="utf-8")
            new_path.write_text(json.dumps(new), encoding="utf-8")
            out = root / "out"; td.publish_snapshot(old_path, out, cal)
            existing = json.loads((out / "2026-10-12.json").read_text())
            existing["source"] = "KIWOOM_CONDITION_SEARCH"
            (out / "2026-10-12.json").write_text(json.dumps(existing), encoding="utf-8")
            with self.assertRaisesRegex(td.DiscoveryError, "IDENTITY_CONFLICT"):
                td.publish_snapshot(new_path, out, cal)

            existing["source"] = "KIWOOM_WATCHLIST"
            existing["selectionDate"] = "2026-10-08"
            (out / "2026-10-12.json").write_text(json.dumps(existing), encoding="utf-8")
            with self.assertRaisesRegex(td.DiscoveryError, "IDENTITY_CONFLICT"):
                td.publish_snapshot(new_path, out, cal)

    def test_enrichment_blocks_detail_value_conflict_and_symbol_only_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); cal = calendar(root / "calendar.json")
            old = legacy(snapshot(["005930"]))
            new = snapshot(["005930"])
            old_path, new_path = root / "old.json", root / "new.json"
            old_path.write_text(json.dumps(old), encoding="utf-8")
            new_path.write_text(json.dumps(new), encoding="utf-8")
            out = root / "out"; td.publish_snapshot(old_path, out, cal)
            with self.assertRaisesRegex(td.DiscoveryError, "DETAILS_REQUIRED"):
                td.publish_snapshot(old_path, out, cal)

            detailed = snapshot(["005930"])
            detailed["candidate_details"][0]["selectionValue"] = 101
            detailed_path = root / "detailed.json"
            detailed_path.write_text(json.dumps(detailed), encoding="utf-8")
            td.publish_snapshot(detailed_path, out, cal)
            conflict = snapshot(["005930"])
            conflict["candidate_details"][0]["selectionValue"] = 999
            conflict_path = root / "conflict.json"
            conflict_path.write_text(json.dumps(conflict), encoding="utf-8")
            with self.assertRaisesRegex(td.DiscoveryError, "VALUE_CONFLICT"):
                td.publish_snapshot(conflict_path, out, cal)

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

    def test_analysis_keeps_current_quote_but_blocks_non_krx_technical_fallback(self):
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
                         ["MONITORED_UNIVERSE", None, None])
        self.assertIsNone(result["stocks"][1]["price"])
        self.assertEqual(result["stocks"][1]["technicalStatus"], "AVAILABLE")
        self.assertEqual(result["stocks"][-1]["dataStatus"], "UNAVAILABLE")
        self.assertEqual(result["metrics"]["vwap"], "UNAVAILABLE")

    def test_verified_name_lookup_does_not_supply_price_or_technical_values(self):
        discovery = td.published_payload(snapshot(["000810"]))
        result = td.analyze(
            "2026-10-30", discovery, {}, {}, {},
            name_lookup={"000810": "삼성화재"},
        )
        row = result["stocks"][0]
        self.assertEqual(row["itemName"], "삼성화재")
        self.assertEqual(row["itemNameSource"], "VERIFIED_NAME_LOOKUP")
        self.assertIsNone(row["price"])
        self.assertEqual(row["dataStatus"], "UNAVAILABLE")

    def test_krx_input_never_becomes_kiwoom_candidate_technical_provenance(self):
        discovery = td.published_payload(snapshot(["005930"]))
        technicals = {"generationId": "g1", "targetDate": "20261009", "datas": [{"itemCode": "005930", "ma5": 1, "ma20": 2, "ma60": 3}]}
        states = {"generationId": "g1", "targetDate": "20261009", "datas": [{"itemCode": "005930", "state": {"breakout20": "confirmed"}}]}
        source = {"status": "VERIFIED", "generationId": "g2", "targetDate": "20261009", "sourcePath": "data/technicals.json", "manifestSha256": "a" * 64, "receiptStatus": "VERIFIED"}
        result = td.analyze("2026-10-12", discovery, {}, technicals, states, full_market_signals={"stocks": [{"code": "005930", "ma20": 99}]}, krx_source=source)
        row = result["stocks"][0]
        self.assertNotIn("technicalProvenance", result)
        self.assertEqual(row["candidateTechnicalIndicators"], {"ma20": 100})
        self.assertIsNone(row["ma20"])
        self.assertIsNone(row["technicalSourceDate"])

    def test_previous_report_change(self):
        discovery = td.published_payload(snapshot(["005930"]))
        quote = {"datas": [{"itemCode": "005930", "closePrice": 110}]}
        previous = {"stocks": [{"itemCode": "005930", "price": 100,
                                  "dataStatus": "PARTIAL", "breakout20": "none"}]}
        row = td.analyze("2026-10-12", discovery, quote, {}, {}, previous=previous)["stocks"][0]
        self.assertEqual(row["previousReportChange"]["price"], 10)

    def test_nested_candidate_indicators_win_over_krx_input(self):
        discovery = td.published_payload(snapshot(["003490"]))
        technicals = {
            "generationId": "krx-20261009", "asOfDate": "20261009",
            "datas": [{"itemCode": "003490", "stockName": "대한항공", "close": 29050,
                        "technicals": {"ma5": 28070, "ma20": 27952.5, "ma60": 28420,
                                       "volumeRatio20": 1.1}}],
        }
        states = {
            "generationId": "krx-20261009", "asOfDate": "20261009",
            "datas": [{"itemCode": "003490", "state": {"breakout20": "confirmed", "pullbackState": "none"}}],
        }
        result = td.analyze("2026-10-12", discovery, {}, technicals, states,
                            historical_technicals=technicals, historical_states=states)
        row = result["stocks"][0]
        self.assertIsNone(row["selectionPrice"])
        self.assertEqual(row["candidateTechnicalIndicators"], {"ma20": 100})
        self.assertIsNone(row["ma20"])
        self.assertIsNone(row["ma60"])
        self.assertIsNone(row["volumeRatio20"])
        self.assertIsNone(row["breakout20"])
        self.assertIsNone(row["technicalSourceDate"])

    def test_krx_generation_mismatch_does_not_leak_indicators(self):
        discovery = td.published_payload(snapshot(["003490"]))
        technicals = {"generationId": "g1", "asOfDate": "20261009",
                      "datas": [{"itemCode": "003490", "ma20": 100, "ma60": 90, "close": 110}]}
        states = {"generationId": "g2", "asOfDate": "20261009",
                  "datas": [{"itemCode": "003490", "state": {"breakout20": "confirmed"}}]}
        result = td.analyze("2026-10-12", discovery, {}, {}, {},
                            historical_technicals=technicals, historical_states=states)
        self.assertEqual(result["stocks"][0]["technicalStatus"], "AVAILABLE")
        self.assertEqual(result["stocks"][0]["candidateTechnicalIndicators"], {"ma20": 100})
        self.assertIsNone(result["stocks"][0]["ma20"])
        self.assertIsNone(result["stocks"][0]["selectionPrice"])

    def test_missing_candidate_details_does_not_fallback_to_krx_technicals(self):
        discovery = td.published_payload(snapshot(["003490"]))
        discovery.pop("candidateDetails", None)
        result = td.analyze(
            "2026-10-12", discovery, {},
            {"datas": [{"itemCode": "003490", "ma20": 999}]},
            {"datas": [{"itemCode": "003490", "state": {"breakout20": "confirmed"}}]},
        )
        row = result["stocks"][0]
        self.assertEqual(row["technicalStatus"], "UNAVAILABLE")
        self.assertEqual(row["candidateTechnicalIndicators"], {})
        self.assertIn("CANDIDATE_DETAILS_UNAVAILABLE", row["candidateFailureReason"])
        self.assertIn("TECHNICAL_INDICATORS_UNAVAILABLE", row["candidateFailureReason"])
        self.assertIsNone(row["ma20"])

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
        self.assertEqual(result["errors"], [{"itemCode": "999999", "reason": "OSError"}])

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
