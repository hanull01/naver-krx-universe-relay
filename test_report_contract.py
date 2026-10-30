import json
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

    def test_batch2_performance_and_v2_changes_are_compact_and_backward_compatible(self):
        contract = build_report_contract(
            indices={"generatedAt": "g", "indices": []},
            summary={"generatedAt": "g", "fullMarket": {"tradingDate": "20261029", "markets": {"TOTAL": {}}}},
            breadth={"generatedAt": "g", "fullMarket": {"marketTradingValue": {}}},
            quality={"generatedAt": "g"}, industries={"generatedAt": "g", "industries": []},
            themes={"generatedAt": "g", "themes": []},
            v2_latest={"generatedAt": "g", "industries": [], "themes": []},
            v2_history={"observations": []}, leaders={},
            kiwoom_performance={"status": "PENDING", "source": "data/trading-discovery/2026-10-29.json", "stocks": [{"itemCode": "042510", "horizons": {"D+1": {"status": "PENDING"}}}]},
            v2_changes=[{"kind": "industry", "id": "i1", "name": "업종", "officialChangeClass": "COMPARISON_UNAVAILABLE", "status": "UNVERIFIED_SOURCE"}],
        )
        self.assertEqual(contract["kiwoomPerformance"]["status"], "PENDING")
        self.assertEqual(contract["v2Changes"][0]["officialChangeClass"], "COMPARISON_UNAVAILABLE")
        self.assertEqual(contract["v2"]["changesSource"], "reportContract.v2Changes")

    def test_full_market_date_diagnostics_are_compacted_with_reference_counts(self):
        generated = "2026-10-30T00:09:00+09:00"
        evidence = {
            "tradingDate": "20261030",
            "status": "VERIFIED",
            "reason": "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT",
            "evidenceClass": "INTRADAY_SESSION",
            "finalConfirmed": False,
            "collectionGeneratedAt": generated,
            "missingCanonicalCodes": [f"{index:06d}" for index in range(2769)],
            "invalidCanonicalCodes": {f"{index:06d}": "NORMAL_EMPTY_DAILY" for index in range(116)},
            "secondaryEvidence": {
                "reason": "CANONICAL_COVERAGE_INCOMPLETE",
                "missingCanonicalCodes": [f"{index:06d}" for index in range(2769)],
            },
        }
        contract = build_report_contract(
            indices={"generatedAt": generated, "indices": []},
            summary={"generatedAt": generated, "fullMarket": {
                "tradingDate": "20261030", "tradingDateEvidence": evidence,
                "authoritativeCount": 2769, "markets": {"TOTAL": {
                    "advancers": 1, "decliners": 2767, "unchanged": 1,
                }},
            }},
            breadth={"generatedAt": generated, "fullMarket": {"marketTradingValue": {}}},
            quality={"generatedAt": generated, "tradingDateEvidence": evidence},
            industries={"generatedAt": generated, "industries": []},
            themes={"generatedAt": generated, "themes": []},
            v2_latest={"generatedAt": generated, "tradingDate": "20261030", "industries": [], "themes": []},
            v2_history={"observations": []},
            leaders={},
        )
        evidence_out = contract["tradingDateEvidence"]
        self.assertNotIn("missingCanonicalCodes", evidence_out)
        self.assertEqual(evidence_out["detailCounts"]["missingCanonicalCodes"], 2769)
        self.assertEqual(evidence_out["detailCounts"]["invalidCanonicalCodes"], 116)
        self.assertEqual(evidence_out["secondaryEvidence"]["detailCounts"]["missingCanonicalCodes"], 2769)

    def test_relative_strength_top5_is_separate_from_v1_v2_status_and_deterministic(self):
        common = {"generatedAt": "2026-10-30T10:00:00+09:00"}
        industries = {
            **common,
            "industries": [
                {"id": "2", "name": "B", "memberCount": 10, "featureValidMemberCount": 10,
                 "relativeStrengthVsMarket": 3.0, "avgReturn1D": 2.0, "advancePct": 70,
                 "leaders": [{"code": "000002", "name": "B주도"}]},
                {"id": "1", "name": "A", "memberCount": 10, "featureValidMemberCount": 10,
                 "relativeStrengthVsMarket": 3.0, "avgReturn1D": 2.0, "advancePct": 70,
                 "leaders": [{"code": "000001", "name": "A주도"}]},
            ],
        }
        themes = {**common, "themes": [{
            "id": "T1", "name": "테마A", "memberCount": 4, "coveragePct": 100,
            "membershipStatus": "OK", "metrics": {
                "officialChangeRateRelativeStrength": 5.0, "averageReturn1D": 4.0,
                "advancerPct": 75, "derivedValidQuoteCount": 4,
            }, "stocks": [{"code": "000003", "name": "테마주"}],
        }]}
        v2_latest = {**common, "inputEvidence": {"sourceCommit": None}, "industries": [
            {"id": "2", "kind": "industry", "v1Status": "ACTIVE", "v2Status": "WATCH", "score": 71.0, "dataQuality": "OK"},
            {"id": "1", "kind": "industry", "v1Status": "WAIT", "v2Status": "ACTIVE", "score": 80.0, "dataQuality": "OK"},
        ], "themes": [{"id": "T1", "kind": "theme", "v1Status": "ACTIVE", "v2Status": "WEAK", "score": 40.0, "dataQuality": "OK"}]}
        history = {"observations": [{
            "tradingDate": "2026-10-29", "observedAt": "2026-10-29T10:00:00+09:00",
            "dateQuality": "VERIFIED", "industries": [
                {"id": "1", "kind": "industry", "relativeStrengthVsMarket": 1.0, "averageReturn1D": 1.0, "memberCount": 10},
                {"id": "2", "kind": "industry", "relativeStrengthVsMarket": 4.0, "averageReturn1D": 1.0, "memberCount": 10},
            ], "themes": [],
        }]}
        contract = build_report_contract(
            indices={**common, "indices": []},
            summary={**common, "fullMarket": {"tradingDate": "20261030", "tradingDateEvidence": self.evidence("20261030"), "markets": {"TOTAL": {}}}},
            breadth={**common, "fullMarket": {"marketTradingValue": {}}}, quality={**common},
            industries=industries, themes=themes, v2_latest=v2_latest, v2_history=history, leaders={},
        )
        tracking = contract["relativeStrengthTop5"]
        self.assertEqual([row["id"] for row in tracking["industries"]["top5"]], ["1", "2"])
        self.assertEqual(tracking["industries"]["top5"][0]["changeClass"], "RANK_UP")
        self.assertEqual(tracking["industries"]["active"][0]["id"], "1")
        self.assertEqual(tracking["sourceCommitStatus"], "UNVERIFIED")
        self.assertNotIn("stocks", contract["themes"][0])
        self.assertNotIn("missingCodes", contract["themes"][0])

    def test_relative_strength_zero_is_eligible_not_missing(self):
        common = {"generatedAt": "g"}
        contract = build_report_contract(
            indices={**common, "indices": []},
            summary={**common, "fullMarket": {"tradingDate": "20261030", "markets": {"TOTAL": {}}}},
            breadth={**common, "fullMarket": {"marketTradingValue": {}}}, quality={**common},
            industries={**common, "industries": [{"id": "1", "name": "중립업종", "memberCount": 5, "featureValidMemberCount": 5, "relativeStrengthVsMarket": 0.0}]},
            themes={**common, "themes": []}, v2_latest={**common, "industries": [], "themes": []},
            v2_history={"observations": []}, leaders={},
        )
        row = contract["relativeStrengthTop5"]["industries"]["top5"][0]
        self.assertEqual(row["relativeStrength"], 0.0)
        self.assertEqual(row["eligibility"], "ELIGIBLE")

    def test_2769_scale_group_contract_stays_below_target_without_member_duplication(self):
        common = {"generatedAt": "2026-10-30T10:00:00+09:00"}
        industries = []
        for index in range(79):
            industries.append({
                "id": f"I{index:03d}", "name": f"업종{index:03d}",
                "memberCount": 35, "featureValidMemberCount": 35,
                "relativeStrengthVsMarket": float(index) / 10,
                "avgReturn1D": float(index) / 20, "advancePct": 55.0,
                "leaders": [{"code": f"{index:03d}001", "name": "주도주", "changeRate": 3.0}],
            })
        themes = []
        for index in range(264):
            themes.append({
                "id": f"T{index:03d}", "name": f"테마{index:03d}",
                "memberCount": 10, "coveragePct": 100.0,
                "membershipStatus": "OK", "metrics": {
                    "officialChangeRateRelativeStrength": float(index) / 10,
                    "averageReturn1D": float(index) / 20, "advancerPct": 50.0,
                    "derivedValidQuoteCount": 10,
                },
                "stocks": [{"code": f"{index:03d}{stock:03d}", "name": "구성종목", "changeRate": 1.0}
                           for stock in range(10)],
            })
        contract = build_report_contract(
            indices={**common, "indices": []},
            summary={**common, "fullMarket": {
                "tradingDate": "20261030", "tradingDateEvidence": self.evidence("20261030"),
                "authoritativeCount": 2769, "markets": {"TOTAL": {
                    "advancers": 1200, "decliners": 1300, "unchanged": 269,
                }},
            }},
            breadth={**common, "fullMarket": {"marketTradingValue": {}}},
            quality={**common, "tradingDateEvidence": self.evidence("20261030")},
            industries={**common, "industries": industries},
            themes={**common, "themes": themes},
            v2_latest={**common, "tradingDate": "20261030", "industries": [], "themes": []},
            v2_history={"observations": []}, leaders={},
        )
        payload = json.dumps(contract, ensure_ascii=False).encode("utf-8")
        self.assertEqual(len(contract["industries"]), 79)
        self.assertEqual(len(contract["themes"]), 264)
        self.assertLess(len(payload), 600_000)
        self.assertNotIn("stocks", contract["themes"][0])
        self.assertEqual(contract["themes"][0]["stockDetailCount"], 10)

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
        self.assertEqual(contract["rendering"]["reason"], "SOURCE_OBSERVED_PARTIAL_HTML_BLOCKED")

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
        self.assertEqual(contract["relativeStrengthTop5"]["status"], "PARTIAL")
        self.assertEqual(contract["relativeStrengthTop5"]["reason"], "TRADING_DATE_UNAVAILABLE")

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
        self.assertEqual(contract["rendering"]["reason"], "BASELINE_STALE_HTML_BLOCKED")

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
