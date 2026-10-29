import unittest

from tools.report_contract import (
    build_report_contract,
    classify_contract_transport_response,
    validate_contract_payload,
    validate_kiwoom_candidate_binding,
    validate_rendered_models,
)


class ReportContractTests(unittest.TestCase):
    def evidence(self, date="20261028"):
        return {
            "tradingDate": date,
            "status": "VERIFIED",
            "reason": "VERIFIED_MONITORING_DAILY_FINAL_SESSION",
            "finalConfirmed": True,
        }

    def test_contract_transport_empty_inline_content_is_not_called_empty_file(self):
        result = classify_contract_transport_response({
            "encoding": "none",
            "content": "",
            "size": 14673922,
            "download_url": "https://raw.example.test/contract.json",
        })
        self.assertEqual(result["status"], "INLINE_CONTENT_UNAVAILABLE")
        self.assertEqual(result["size"], 14673922)

    def test_contract_payload_validation_covers_empty_invalid_missing_date_and_sha(self):
        self.assertEqual(validate_contract_payload(b"")["status"], "EMPTY_FILE")
        self.assertEqual(validate_contract_payload(b"not-json")["status"], "INVALID_JSON")
        valid = b'{"schemaVersion":"market-report-contract-v1","asOf":"20261029","observedAt":"t"}'
        self.assertEqual(validate_contract_payload(valid, expected_as_of="20261028")["status"], "TRADING_DATE_MISMATCH")
        self.assertEqual(validate_contract_payload(valid, expected_sha256="0" * 64)["status"], "SHA_MISMATCH")
        self.assertEqual(validate_contract_payload(b"{}", expected_as_of=None)["status"], "REQUIRED_FIELDS_MISSING")
        self.assertEqual(validate_contract_payload(valid, expected_as_of="20261029", expected_observed_at="t")["status"], "VALID")

    def test_contract_uses_one_generation_and_keeps_v2_names_scores_and_history(self):
        common = {"generatedAt": "2026-10-28T10:09:19+00:00"}
        contract = build_report_contract(
            indices={**common, "indices": [{"name": "KOSPI", "close": 6012.44}]},
            summary={**common, "fullMarket": {"tradingDate": "20261028", "tradingDateEvidence": self.evidence(), "authoritativeCount": 2766,
                "markets": {"TOTAL": {"advancers": 1850, "decliners": 713, "unchanged": 203,
                    "breakout20Count": 81, "breakout20EligibleCount": 2642, "pctBreakout20": 3.0659}}}},
            breadth={**common, "fullMarket": {"marketTradingValue": {"KOSPI": {"value": 1}, "KOSDAQ": {"value": 2}}}},
            quality={**common, "collectionQualityStatus": "COMPLETE_RECONCILIATION_PENDING", "tradingDateEvidence": self.evidence()},
            industries={**common, "industries": [{"name": "생명보험", "avgReturn1D": 6.35}]},
            themes={**common, "themes": [{"name": "S7", "membershipStatus": "ACTIVE"}]},
            v2_latest={"tradingDate": "2026-10-28", "industries": [{"id": "1", "name": "생명보험", "v2Status": "ACTIVE", "score": 69.46}],
                "themes": [{"id": "2", "name": "S7", "v2Status": "ACTIVE", "score": 88.84}]},
            v2_history={"observations": [{"tradingDate": "2026-10-27"}, {"tradingDate": "2026-10-28"}]},
            leaders={"fullMarketLeaders": []},
            candidates=[{"itemCode": "037950", "price": 14730}],
        )
        self.assertEqual(contract["status"], "AVAILABLE")
        self.assertEqual(contract["fullMarket"]["metrics"]["breakout20"]["count"], 81)
        self.assertEqual(contract["v2"]["industries"][0]["score"], 69.46)
        self.assertEqual(contract["v2"]["history"]["uniqueTradingDates"], ["2026-10-27", "2026-10-28"])

    def test_v2_real_names_leaders_and_recent_history_are_same_generation(self):
        generated = "2026-10-29T05:08:56+00:00"
        evidence = {
            "tradingDate": "20261029", "status": "VERIFIED",
            "reason": "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT",
            "evidenceClass": "INTRADAY_SESSION", "finalConfirmed": False,
            "collectionGeneratedAt": generated,
        }
        industry_source = {
            "id": "i1", "name": "인터넷과카탈로그소매", "leaders": [
                {"code": "100001", "name": "상위업종주", "changeRate": 12.0},
                {"code": "100002", "name": "두번째주", "changeRate": 8.0},
                {"code": "100003", "name": "세번째주", "changeRate": 4.0},
                {"code": "100004", "name": "제외주", "changeRate": 2.0},
            ],
        }
        theme_source = {
            "id": "t1", "name": "S7", "stocks": [
                {"code": "200001", "name": "테마상위", "changeRate": 20.0, "derivedMetricsUsable": True, "currentPrice": 1000},
                {"code": "200002", "name": "테마차상위", "changeRate": 10.0, "derivedMetricsUsable": True, "currentPrice": 2000},
                {"code": "200003", "name": "테마하위", "changeRate": 1.0, "derivedMetricsUsable": True, "currentPrice": 3000},
                {"code": "200004", "name": "격리", "changeRate": 99.0, "derivedMetricsUsable": False, "currentPrice": 4000},
            ],
        }
        observations = []
        for index, date in enumerate(("2026-10-22", "2026-10-23", "2026-10-24", "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29")):
            observations.append({
                "tradingDate": date, "observedAt": f"{date}T10:00:00+00:00",
                "sourceSnapshotHash": f"hash-{date}",
                "industries": [{"id": "i1", "kind": "industry", "v2Status": "WATCH", "score": 70 + index}],
                "themes": [{"id": "t1", "kind": "theme", "v2Status": "WATCH", "score": 60 + index}],
            })
        observations.append({
            "tradingDate": "2026-10-29", "observedAt": "2026-10-29T11:00:00+00:00",
            "sourceSnapshotHash": "hash-2026-10-29-later",
            "industries": [{"id": "i1", "kind": "industry", "v2Status": "ACTIVE", "score": 90}],
            "themes": [{"id": "t1", "kind": "theme", "v2Status": "WATCH", "score": 66}],
        })
        contract = build_report_contract(
            indices={"generatedAt": generated, "indices": []},
            summary={"generatedAt": generated, "fullMarket": {
                "tradingDate": "20261029", "tradingDateEvidence": evidence,
                "authoritativeCount": 1, "markets": {"TOTAL": {"advancers": 1, "decliners": 0, "unchanged": 0}},
            }},
            breadth={"generatedAt": generated, "fullMarket": {"marketTradingValue": {}}},
            quality={"generatedAt": generated, "tradingDateEvidence": evidence},
            industries={"generatedAt": generated, "industries": [industry_source]},
            themes={"generatedAt": generated, "themes": [theme_source]},
            v2_latest={"generatedAt": generated, "tradingDate": "20261029",
                        "inputEvidence": {"sourceQualityStatus": "SOURCE_OBSERVED", "sourceCommit": None, "sourceCommitStatus": "UNAVAILABLE"},
                        "industries": [{"id": "i1", "name": "인터넷과카탈로그소매", "kind": "industry", "v2Status": "ACTIVE", "score": 91.2, "persistenceDays": 7}],
                        "themes": [{"id": "t1", "name": "S7", "kind": "theme", "v2Status": "WATCH", "score": 82.4, "persistenceDays": 3}]},
            v2_history={"observations": observations}, leaders={},
        )
        industry = contract["v2"]["industries"][0]
        theme = contract["v2"]["themes"][0]
        self.assertEqual([row["name"] for row in industry["leaderCandidates"]], ["상위업종주", "두번째주", "세번째주"])
        self.assertEqual(industry["leaders"], industry["leaderCandidates"])
        self.assertEqual(industry["leaderEvidenceStatus"], "AVAILABLE_UNVERIFIED_SOURCE_COMMIT")
        self.assertEqual([row["name"] for row in theme["leaderCandidates"]], ["테마상위", "테마차상위", "테마하위"])
        self.assertEqual(contract["v2"]["history"]["availableTradingDays"], 7)
        self.assertTrue(contract["v2"]["history"]["sameDayDeduplicated"])
        self.assertEqual(industry["historySummary"]["changeClass"], "STATUS_CHANGED")
        self.assertEqual(industry["historySummary"]["scoreDelta"], 15)
        self.assertTrue(industry["historySummary"]["currentObserved"])

    def test_v2_leaders_are_blocked_when_source_generation_does_not_match(self):
        generated = "2026-10-29T05:08:56+00:00"
        contract = build_report_contract(
            indices={"generatedAt": generated, "indices": []},
            summary={"generatedAt": generated, "fullMarket": {"tradingDate": "20261029", "authoritativeCount": 1, "markets": {"TOTAL": {}}}},
            breadth={"generatedAt": generated, "fullMarket": {"marketTradingValue": {}}},
            quality={"generatedAt": generated},
            industries={"generatedAt": "old", "industries": [{"id": "i1", "leaders": [{"code": "1", "name": "오래된주"}]}]},
            themes={"generatedAt": generated, "themes": []},
            v2_latest={"generatedAt": generated, "industries": [{"id": "i1", "kind": "industry", "v2Status": "WATCH"}], "themes": []},
            v2_history={"observations": []}, leaders={},
        )
        row = contract["v2"]["industries"][0]
        self.assertEqual(row["leaderEvidenceStatus"], "UNAVAILABLE_SOURCE_GENERATION_MISMATCH")
        self.assertEqual(row["leaderCandidates"], [])

    def test_v2_source_provenance_is_explicit_and_never_promotes_missing_commit(self):
        generated = "2026-10-29T05:08:56+00:00"
        provenance = {
            "schemaVersion": "v2-source-provenance-v1",
            "status": "PARTIAL",
            "sourceCommit": None,
            "sourceCommitStatus": "UNAVAILABLE",
            "inputSnapshotSha256": "a" * 64,
            "reason": "SOURCE_COMMIT_UNAVAILABLE_PRE_PUBLISH",
        }
        contract = build_report_contract(
            indices={"generatedAt": generated, "indices": []},
            summary={"generatedAt": generated, "fullMarket": {
                "tradingDate": "20261029", "markets": {"TOTAL": {}}
            }},
            breadth={"generatedAt": generated, "fullMarket": {"marketTradingValue": {}}},
            quality={"generatedAt": generated},
            industries={"generatedAt": generated, "industries": []},
            themes={"generatedAt": generated, "themes": []},
            v2_latest={"generatedAt": generated, "tradingDate": "20261029",
                        "inputEvidence": {"sourceCommit": None, "sourceCommitStatus": "UNAVAILABLE"},
                        "industries": [], "themes": []},
            v2_history={"observations": []}, leaders={}, v2_provenance=provenance,
        )
        self.assertEqual(contract["v2"]["sourceProvenance"]["status"], "PARTIAL")
        self.assertIsNone(contract["v2"]["sourceProvenance"]["sourceCommit"])
        self.assertNotEqual(contract["v2"]["sourceProvenance"]["status"], "VERIFIED")

    def test_post_market_observation_is_available_but_not_final_daily_evidence(self):
        generated = "2026-10-28T20:40:03+09:00"
        evidence = {
            "tradingDate": "20261028",
            "status": "VERIFIED",
            "reason": "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT",
            "evidenceClass": "POST_MARKET_SNAPSHOT",
            "finalConfirmed": False,
            "collectionGeneratedAt": generated,
        }
        common = {"generatedAt": generated}
        contract = build_report_contract(
            indices={**common, "indices": []},
            summary={**common, "fullMarket": {"tradingDate": "20261028", "tradingDateEvidence": evidence, "markets": {"TOTAL": {}}}},
            breadth={**common, "fullMarket": {"marketTradingValue": {}}},
            quality={**common, "tradingDateEvidence": evidence},
            industries={**common, "industries": []}, themes={**common, "themes": []},
            v2_latest={**common, "tradingDate": "20261028", "industries": [], "themes": []},
            v2_history={"observations": []}, leaders={},
        )
        self.assertEqual(contract["status"], "AVAILABLE")
        self.assertEqual(contract["tradingDateStatus"], "VERIFIED")
        self.assertTrue(contract["rendering"]["htmlAllowed"])
        self.assertFalse(contract["tradingDateEvidence"]["finalConfirmed"])

    def test_kiwoom_candidates_are_separate_from_technical_candidates(self):
        discovery = {
            "status": "AVAILABLE",
            "selectionDate": "2026-10-28",
            "monitoringDate": "2026-10-29",
            "sourceSha256": "a" * 64,
            "symbols": ["042510", "064400", "253590"],
            "validation": {"status": "VALID", "calendarSource": "KRX_OFFICIAL", "immutable": True, "codeNameBindings": {"042510": "라온시큐어", "064400": "LG씨엔에스", "253590": "네오셈"}},
        }
        kiwoom = {
            **discovery,
            "candidateCount": 3,
            "stocks": [
                {"itemCode": "042510", "itemName": "라온시큐어"},
                {"itemCode": "064400", "itemName": "LG씨엔에스"},
                {"itemCode": "253590", "itemName": "네오셈"},
            ],
        }
        contract = build_report_contract(
            indices={"generatedAt": "g", "indices": []},
            summary={"generatedAt": "g", "fullMarket": {"tradingDate": "20261028", "markets": {"TOTAL": {}}}},
            breadth={"generatedAt": "g", "fullMarket": {"marketTradingValue": {}}},
            quality={"generatedAt": "g"}, industries={"generatedAt": "g", "industries": []},
            themes={"generatedAt": "g", "themes": []},
            v2_latest={"generatedAt": "g", "industries": [], "themes": []},
            v2_history={"observations": []}, leaders={},
            technical_candidates=[{"itemCode": "028050", "itemName": "삼성E&A"}],
            kiwoom_candidates=kiwoom,
        )
        self.assertEqual(contract["technicalCandidates"][0]["itemCode"], "028050")
        self.assertEqual(contract["kiwoomCandidates"]["symbols"], discovery["symbols"])
        self.assertEqual(validate_kiwoom_candidate_binding(discovery, contract), [])

    def test_kiwoom_candidate_outside_source_fails_binding(self):
        discovery = {
            "status": "AVAILABLE", "selectionDate": "2026-10-28",
            "monitoringDate": "2026-10-29", "sourceSha256": "a" * 64,
            "symbols": ["042510", "064400", "253590"],
        }
        contract = {"kiwoomCandidates": {
            "selectionDate": "2026-10-28", "monitoringDate": "2026-10-29",
            "sourceSha256": "a" * 64, "sourceCandidateCount": 3,
            "symbols": ["028050", "064400", "253590"],
            "stocks": [
                {"itemCode": "028050", "itemName": "삼성E&A"},
                {"itemCode": "064400", "itemName": "LG씨엔에스"},
                {"itemCode": "253590", "itemName": "네오셈"},
            ],
        }}
        errors = validate_kiwoom_candidate_binding(discovery, contract)
        self.assertIn("KIWOOM_SYMBOLS_MISMATCH", errors)
        self.assertIn("KIWOOM_STOCK_CODES_MISMATCH", errors)

    def test_code_name_mapping_is_not_swapped(self):
        discovery = {
            "status": "AVAILABLE",
            "symbols": ["042510", "300120"],
            "sourceSha256": "a" * 64,
            "selectionDate": "2026-10-28",
            "monitoringDate": "2026-10-29",
            "candidateCount": 2,
            "validation": {"status": "VALID", "calendarSource": "KRX_OFFICIAL", "immutable": True, "codeNameBindings": {"042510": "라온시큐어", "300120": "라온피플"}},
        }
        contract = {"kiwoomCandidates": {
            "symbols": ["042510", "300120"],
            "stocks": [
                {"itemCode": "042510", "itemName": "라온시큐어"},
                {"itemCode": "300120", "itemName": "라온피플"},
            ],
            "sourceSha256": "a" * 64,
            "selectionDate": "2026-10-28", "monitoringDate": "2026-10-29",
            "sourceCandidateCount": 2,
        }}
        self.assertEqual(validate_kiwoom_candidate_binding(discovery, contract), [])
        contract["kiwoomCandidates"]["stocks"][0]["itemName"] = "라온피플"
        self.assertIn("KIWOOM_CODE_NAME_MISMATCH", validate_kiwoom_candidate_binding(discovery, contract))

    def test_trading_date_evidence_is_required_for_verified_status(self):
        contract = build_report_contract(
            indices={"generatedAt": "g", "indices": []},
            summary={"generatedAt": "g", "fullMarket": {"tradingDate": "20261028", "markets": {"TOTAL": {}}}},
            breadth={"generatedAt": "g", "fullMarket": {"marketTradingValue": {}}},
            quality={"generatedAt": "g"}, industries={"generatedAt": "g", "industries": []},
            themes={"generatedAt": "g", "themes": []},
            v2_latest={"generatedAt": "g", "industries": [], "themes": []},
            v2_history={"observations": []}, leaders={},
        )
        self.assertEqual(contract["tradingDateStatus"], "UNAVAILABLE_TRADING_DATE_EVIDENCE_INVALID")
        self.assertFalse(contract["rendering"]["htmlAllowed"])

    def test_source_observed_final_date_is_partial_and_html_blocked(self):
        evidence = {
            "tradingDate": "20261028",
            "status": "SOURCE_OBSERVED",
            "reason": "SOURCE_OBSERVED_MONITORING_DAILY_FINAL_DATE",
            "evidenceClass": "SOURCE_OBSERVED",
        }
        contract = build_report_contract(
            indices={"generatedAt": "g", "indices": []},
            summary={"generatedAt": "g", "fullMarket": {
                "tradingDate": "20261028", "tradingDateEvidence": evidence,
                "markets": {"TOTAL": {}},
            }},
            breadth={"generatedAt": "g", "fullMarket": {"marketTradingValue": {}}},
            quality={"generatedAt": "g", "tradingDateEvidence": evidence},
            industries={"generatedAt": "g", "industries": []},
            themes={"generatedAt": "g", "themes": []},
            v2_latest={"generatedAt": "g", "industries": [], "themes": []},
            v2_history={"observations": []}, leaders={},
        )
        self.assertEqual(contract["status"], "PARTIAL")
        self.assertEqual(contract["tradingDateStatus"], "SOURCE_OBSERVED")
        self.assertFalse(contract["rendering"]["htmlAllowed"])

    def test_null_trading_date_disables_html(self):
        contract = build_report_contract(
            indices={"generatedAt": "g", "indices": []},
            summary={"generatedAt": "g", "fullMarket": {"tradingDate": None, "markets": {"TOTAL": {}}}},
            breadth={"generatedAt": "g", "fullMarket": {"marketTradingValue": {}}},
            quality={"generatedAt": "g"}, industries={"generatedAt": "g", "industries": []},
            themes={"generatedAt": "g", "themes": []},
            v2_latest={"generatedAt": "g", "industries": [], "themes": []},
            v2_history={"observations": []}, leaders={},
        )
        self.assertEqual(contract["status"], "PARTIAL")
        self.assertEqual(contract["tradingDateStatus"], "UNAVAILABLE_TRADING_DATE_NULL")
        self.assertFalse(contract["rendering"]["htmlAllowed"])

    def test_industry_breadth_preserves_authoritative_155_of_169(self):
        contract = build_report_contract(
            indices={"generatedAt": "g", "indices": []},
            summary={"generatedAt": "g", "fullMarket": {"tradingDate": "20261028", "markets": {"TOTAL": {}}}},
            breadth={"generatedAt": "g", "fullMarket": {"marketTradingValue": {}}},
            quality={"generatedAt": "g"},
            industries={"generatedAt": "g", "industries": [{
                "name": "반도체와반도체장비", "memberCount": 169, "advancers": 155,
            }]},
            themes={"generatedAt": "g", "themes": []},
            v2_latest={"generatedAt": "g", "industries": [], "themes": []},
            v2_history={"observations": []}, leaders={},
        )
        row = contract["industries"][0]
        self.assertEqual((row["advancers"], row["memberCount"]), (155, 169))

    def test_mixed_market_generations_are_not_available(self):
        contract = build_report_contract(
            indices={"generatedAt": "g1", "indices": []},
            summary={"generatedAt": "g1", "fullMarket": {"tradingDate": "20261028", "markets": {"TOTAL": {}}}},
            breadth={"generatedAt": "g2", "fullMarket": {"marketTradingValue": {}}},
            quality={"generatedAt": "g1"}, industries={"generatedAt": "g1", "industries": []},
            themes={"generatedAt": "g1", "themes": []},
            v2_latest={"generatedAt": "g1", "industries": [], "themes": []},
            v2_history={"observations": []}, leaders={},
        )
        self.assertEqual(contract["status"], "PARTIAL")
        self.assertFalse(contract["rendering"]["htmlAllowed"])

    def test_renderer_mismatch_catches_breakout_disagreement(self):
        good = {"asOf": "2026-10-28", "observedAt": "t", "fullMarket": {"breadth": {"advancers": 1, "decliners": 2, "unchanged": 3}, "metrics": {"breakout20": {"count": 81}}, "marketTradingValue": {"KOSPI": {"value": 1}, "KOSDAQ": {"value": 2}}}, "v2": {"industries": [], "themes": []}, "candidates": [], "technicalCandidates": [], "kiwoomCandidates": {"symbols": [], "stocks": []}}
        bad = {**good, "fullMarket": {**good["fullMarket"], "metrics": {"breakout20": {"count": 80}}}}
        self.assertEqual(validate_rendered_models(good, bad), ["fullMarket.metrics.breakout20.count"])

    def test_stale_baseline_is_explicit_and_contract_is_partial(self):
        contract = build_report_contract(
            indices={"generatedAt": "t", "indices": []},
            summary={"generatedAt": "t", "baselineFreshnessStatus": "BASELINE_STALE",
                     "baselineAsOfDate": "20261027", "fullMarket": {"tradingDate": "20261029"}},
            breadth={"generatedAt": "t", "fullMarket": {"marketTradingValue": {}}},
            quality={"generatedAt": "t", "baselineFreshnessStatus": "BASELINE_STALE",
                     "baselineAsOfDate": "20261027"},
            industries={"generatedAt": "t", "industries": []},
            themes={"generatedAt": "t", "themes": []},
            v2_latest={"industries": [], "themes": []}, v2_history={"observations": []},
            leaders={},
        )
        self.assertEqual(contract["status"], "PARTIAL")
        self.assertEqual(contract["baseline"]["freshnessStatus"], "BASELINE_STALE")
        self.assertTrue(contract["baseline"]["dateMismatch"])
        self.assertEqual(contract["baseline"]["derivedMetricsStatus"], "UNAVAILABLE")

    def test_theme_member_rows_are_compacted_for_github_contents_transport(self):
        generated = "2026-10-29T04:09:16+00:00"
        evidence = {
            "tradingDate": "20261029",
            "status": "VERIFIED",
            "reason": "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT",
            "evidenceClass": "INTRADAY_SESSION",
            "finalConfirmed": False,
            "collectionGeneratedAt": generated,
        }
        contract = build_report_contract(
            indices={"generatedAt": generated, "indices": []},
            summary={"generatedAt": generated, "fullMarket": {
                "tradingDate": "20261029", "tradingDateEvidence": evidence,
                "authoritativeCount": 1, "markets": {"TOTAL": {}}
            }},
            breadth={"generatedAt": generated, "fullMarket": {"marketTradingValue": {}}},
            quality={"generatedAt": generated, "tradingDateEvidence": evidence},
            industries={"generatedAt": generated, "industries": []},
            themes={"generatedAt": generated, "themes": [{
                "id": "1", "name": "테마", "metrics": {"score": 88.2},
                "stocks": [{"code": "000001", "name": "A"}, {"code": "000002", "name": "B"}],
            }]},
            v2_latest={"generatedAt": generated, "tradingDate": "2026-10-29", "industries": [], "themes": []},
            v2_history={"observations": []}, leaders={},
        )
        row = contract["themes"][0]
        self.assertNotIn("stocks", row)
        self.assertEqual(row["stockDetailCount"], 2)
        self.assertIn("latest-theme-signals.json", row["stockDetailSource"])
        self.assertIn("themeStockDetails", contract["sourceBindings"])
