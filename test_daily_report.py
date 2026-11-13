import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import daily_report


def verified_full_market():
    return {
        "status": "AVAILABLE",
        "asOfDate": "20261015",
        "snapshotDate": "20261016",
        "snapshotDateStatus": "VERIFIED_EXPLICIT",
        "sourceTime": "2026-10-16T20:00:00+09:00",
        "generatedAt": "2026-10-16T20:05:00+09:00",
        "qualityStatus": "SUCCESS",
        "quality": {"currentCount": 2766, "authoritativeCount": 2766},
        "breadth": {
            "advancers": 1200, "decliners": 1300, "unchanged": 266,
            "aboveMA20": {"count": 900, "eligibleCount": 2700, "pct": 33.3333},
            "aboveMA60": {"count": 1000, "eligibleCount": 2680, "pct": 37.3134},
            "aboveMA120": {"count": 800, "eligibleCount": 2650, "pct": 30.1887},
            "breakout20": {"count": 300, "eligibleCount": 2600, "pct": 11.5385},
            "breakout60": {"count": 200, "eligibleCount": 2500, "pct": 8.0},
            "near52wHigh": {"count": 100, "eligibleCount": 2400, "pct": 4.1667},
            "volumeAbove20DayAverage": {"count": 700, "eligibleCount": 2600, "pct": 26.9231},
        },
        "marketTradingValue": {
            "KOSPI": {"value": 123456789, "sourceTime": "2026-10-16T20:00:00+09:00",
                       "availableCount": 1800, "observedCount": 1800},
            "KOSDAQ": {"value": 98765432, "sourceTime": "2026-10-16T20:00:00+09:00",
                        "availableCount": 966, "observedCount": 966},
        },
        "industries": [{"id": "1", "name": "검증 업종", "avgReturn1D": 1.8,
                        "advancePct": 62.5, "relativeStrengthVsMarket": 2.5,
                        "tradingValue": {"total": 1234567, "availableCount": 10},
                        "persistence": {"status": "AVAILABLE"}}],
        "leaders": [{"code": "005930", "name": "검증 주도주", "changeRate": 3.2,
                     "current_volume_ratio_20": 1.8, "above_ma20": True, "above_ma60": False,
                     "selectionReasons": ["POSITIVE_RETURN", "VOLUME_ABOVE_20D_AVERAGE"],
                     "technicalEvidence": {"status": "AVAILABLE"}}],
    }


class DailyReportTests(unittest.TestCase):
    def test_configured_interest_groups_do_not_promote_dynamic_selection_history(self):
        projection = daily_report.configured_interest_group_projection({
            "sectors": {},
            "themes": {},
            "generatedAt": "2026-11-12T13:30:00+09:00",
        })
        self.assertEqual(projection["configurationStatus"], "NOT_CONFIGURED")
        self.assertEqual(projection["sectors"], [])
        self.assertEqual(projection["themes"], [])
        self.assertEqual(projection["artifactPath"], "config/universe.json#sectors,themes")

    def test_dynamic_v1_artifact_is_the_source_when_config_is_empty(self):
        projection = daily_report.configured_interest_group_projection(
            {"sectors": {}, "themes": {}},
            {
                "status": "MODEL_INPUT_UNAVAILABLE",
                "producerStatus": "MODEL_INPUT_UNAVAILABLE",
                "generatedAt": "2026-11-13T04:31:52+00:00",
                "source": "NAVER_CURRENT_SNAPSHOT",
                "sourceDate": "20261113",
                "observationDate": "20261113",
                "scope": "STOCK_ONLY",
                "industryEvidence": {"missingFields": ["pctAboveMA20"]},
                "sectors": [{
                    "id": "261", "name": "제약", "status": "MODEL_INPUT_UNAVAILABLE",
                    "lifecycleStatus": "DISCOVERED", "failureReason": "MODEL_INPUT_MISSING",
                }],
                "themes": [],
            },
        )
        self.assertEqual(projection["configurationStatus"], "PRODUCER_ARTIFACT")
        self.assertEqual(projection["status"], "MODEL_INPUT_UNAVAILABLE")
        self.assertEqual(projection["sourceDate"], "20261113")
        self.assertEqual(projection["sectors"][0]["failureReason"], "MODEL_INPUT_MISSING")

    def test_registered_projection_calculates_trend_from_current_and_krx_inputs(self):
        projection = daily_report.registered_universe_projection(
            [{"itemCode": "005930", "stockName": "삼성전자"}],
            [{
                "itemCode": "005930",
                "market": {"price": 70000, "sourceTime": "2026-11-12T10:00:00+09:00"},
                "technical": {"ma20": 68000, "ma60": 72000, "sourceDate": "20261111"},
                "marketObserved": True,
            }],
        )
        state = projection["items"][0]["state"]
        self.assertEqual(state["priceVsMA20"], "above")
        self.assertEqual(state["priceVsMA60"], "below")
        self.assertEqual(state["trend"], "MIXED_MA20_MA60")
        self.assertEqual(state["trendStatus"], "AVAILABLE")
        self.assertEqual(state["trendSourceDate"], "20261111")

    def test_registered_projection_reports_missing_trend_inputs_without_fallback(self):
        projection = daily_report.registered_universe_projection(
            [{"itemCode": "005930", "stockName": "삼성전자"}],
            [{
                "itemCode": "005930",
                "market": {"price": 70000, "sourceTime": "2026-11-12T10:00:00+09:00"},
                "technical": {"ma20": None, "ma60": 72000, "sourceDate": "20261111"},
                "marketObserved": True,
            }],
        )
        state = projection["items"][0]["state"]
        self.assertEqual(state["trend"], "UNAVAILABLE_TREND_INPUT")
        self.assertEqual(state["failureReason"], "UNAVAILABLE_TREND_INPUT")

    def test_research_requires_matching_date_and_source_generation(self):
        payload = {
            "status": "OK", "asOf": "2026-10-29", "sourceGeneration": "gen-1",
            "stocks": [{"itemCode": "005930", "rankingEligible": True}],
        }
        self.assertEqual(
            daily_report.validated_research_for_report(payload, "2026-10-29", "gen-1"),
            payload,
        )
        self.assertEqual(
            daily_report.validated_research_for_report(payload, "2026-10-30", "gen-1")["status"],
            "ARTIFACT_INVALID",
        )
        self.assertEqual(
            daily_report.validated_research_for_report(payload, "2026-10-29", "gen-2")["status"],
            "ARTIFACT_INVALID",
        )
        self.assertEqual(
            daily_report.validated_research_for_report({**payload, "sourceGeneration": None}, "2026-10-29")["status"],
            "ARTIFACT_INVALID",
        )
        self.assertEqual(
            daily_report.validated_research_for_report(
                {**payload, "sourceGeneration": None}, "2026-10-29"
            )["unavailableReason"],
            "RESEARCH_GENERATION_MISSING",
        )
        self.assertEqual(
            daily_report.validated_research_for_report(None, "2026-10-29")["unavailableReason"],
            "RESEARCH_SOURCE_MISSING",
        )

    def test_research_slot_loader_rejects_generic_latest_and_accepts_exact_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            slot_dir = Path(directory)
            payload = {
                "status": "OK", "asOf": "2026-10-29", "sourceGeneration": "gen-1",
                "stocks": [], "slotLineage": {
                    "researchSlot": "07:20", "artifactStatus": "AVAILABLE",
                    "producerStatus": "SUCCESS", "freshnessStatus": "FRESH",
                },
            }
            (slot_dir / "20261029-0720.json").write_text(json.dumps(payload), encoding="utf-8")
            with patch.object(daily_report, "RESEARCH_SLOTS_PATH", slot_dir), patch.dict("os.environ", {"REPORT_RESEARCH_SLOT": "07:20", "REPORT_SLOT": "08:30"}, clear=False):
                loaded = daily_report.load_research_slot_for_report("2026-10-29", "gen-1")
                self.assertEqual(loaded["status"], "CONSUMED")
                self.assertEqual(loaded["slotLineage"]["researchSlot"], "07:20")
                self.assertEqual(loaded["slotLineage"]["reportSlot"], "08:30")
                self.assertTrue(loaded["slotLineage"]["consumedByReport"])
                with patch.dict("os.environ", {"REPORT_RESEARCH_SLOT": "12:20"}, clear=False):
                    missing = daily_report.load_research_slot_for_report("2026-10-29", "gen-1")
                    self.assertEqual(missing["unavailableReason"], "RESEARCH_SLOT_ARTIFACT_MISSING")
                    self.assertEqual(missing["slotLineage"]["researchSlot"], "12:20")
                    self.assertFalse(missing["slotLineage"]["consumedByReport"])

    def test_research_slot_diagnostics_cover_not_due_failure_invalid_and_recheck(self):
        with tempfile.TemporaryDirectory() as directory:
            slot_dir = Path(directory)
            manifest = slot_dir / "manifest.json"
            manifest.write_text(json.dumps({"slotLineage": []}), encoding="utf-8")
            with patch.object(daily_report, "RESEARCH_SLOTS_PATH", slot_dir), \
                 patch.object(daily_report, "RESEARCH_MANIFEST_PATH", manifest), \
                 patch.dict("os.environ", {"REPORT_RESEARCH_SLOT": "07:20", "REPORT_SLOT": "08:30"}, clear=False):
                not_due = daily_report.load_research_slot_for_report("2099-11-13", "gen-1")
                self.assertEqual(not_due["status"], "NOT_DUE")
                self.assertEqual(not_due["unavailableReason"], "RESEARCH_SLOT_NOT_DUE")

                (slot_dir / "20261113-0720.json").write_text("{broken", encoding="utf-8")
                invalid = daily_report.load_research_slot_for_report("2026-11-13", "gen-1")
                self.assertEqual(invalid["status"], "ARTIFACT_INVALID")

                mismatch_payload = {
                    "status": "OK", "asOf": "2026-11-13", "sourceGeneration": "gen-old",
                    "stocks": [], "slotLineage": {
                        "researchSlot": "07:20", "artifactStatus": "AVAILABLE",
                        "producerStatus": "SUCCESS", "freshnessStatus": "FRESH",
                    },
                }
                (slot_dir / "20261113-0720.json").write_text(json.dumps(mismatch_payload), encoding="utf-8")
                mismatch = daily_report.load_research_slot_for_report("2026-11-13", "gen-1")
                self.assertEqual(mismatch["status"], "ARTIFACT_INVALID")
                self.assertEqual(mismatch["unavailableReason"], "RESEARCH_GENERATION_MISMATCH")

                failed_payload = {
                    "status": "OK", "asOf": "2026-11-13", "sourceGeneration": "gen-1",
                    "stocks": [], "slotLineage": {
                        "researchSlot": "07:20", "artifactStatus": "AVAILABLE",
                        "producerStatus": "FAILED", "freshnessStatus": "FRESH",
                    },
                }
                (slot_dir / "20261113-0720.json").write_text(json.dumps(failed_payload), encoding="utf-8")
                failed = daily_report.load_research_slot_for_report("2026-11-13", "gen-1")
                self.assertEqual(failed["status"], "PRODUCER_FAILED")

                recheck_payload = {
                    "status": "OK", "asOf": "2026-11-12", "sourceGeneration": "gen-1",
                    "stocks": [], "slotLineage": {
                        "researchSlot": "12:20", "artifactStatus": "AVAILABLE",
                        "producerStatus": "SUCCESS", "freshnessStatus": "FRESH",
                    },
                }
                (slot_dir / "20261112-1220.json").write_text(json.dumps(recheck_payload), encoding="utf-8")
                with patch.dict("os.environ", {"REPORT_RESEARCH_SLOT": "", "REPORT_SLOT": "13:30"}, clear=False):
                    recheck = daily_report.load_research_slot_for_report("2026-11-12", "gen-1")
                self.assertEqual(recheck["status"], "CONSUMED")
                self.assertEqual(recheck["slotLineage"]["researchSlot"], "12:20")
                self.assertEqual(recheck["slotLineage"]["reportSlot"], "13:30")

    def test_unavailable_research_does_not_fake_coverage_or_block_report(self):
        report = {
            "coverage": {"enabledStockCount": 1, "researchStockCount": 0},
            "sources": {"researchIntelligence": {
                "status": "UNAVAILABLE",
                "unavailableReason": "RESEARCH_DATE_MISMATCH",
            }},
        }
        got = daily_report.validate_research_coverage(report)
        self.assertTrue(got["ok"])
        self.assertFalse(got["blocking"])
        self.assertEqual(got["status"], "OPTIONAL_RESEARCH_UNAVAILABLE")
        self.assertEqual(got["actual"], 0)
        self.assertEqual(got["reason"], "RESEARCH_DATE_MISMATCH")

    def test_research_coverage_one_of_one_passes(self):
        report = {
            "coverage": {"enabledStockCount": 1, "researchStockCount": 1},
            "sources": {"researchIntelligence": {"status": "OK"}},
        }
        got = daily_report.validate_research_coverage(report)
        self.assertTrue(got["ok"])
        self.assertEqual(got["status"], "OK")

    def test_available_but_incomplete_research_blocks(self):
        report = {
            "coverage": {"enabledStockCount": 1, "researchStockCount": 0},
            "sources": {"researchIntelligence": {"status": "OK"}},
        }
        got = daily_report.validate_research_coverage(report)
        self.assertFalse(got["ok"])
        self.assertTrue(got["blocking"])
        self.assertEqual(got["status"], "RESEARCH_COVERAGE_MISMATCH")

    def test_current_group_source_requires_same_date_and_generation(self):
        industries = {
            "tradingDate": "20261029", "generatedAt": "g1",
            "industries": [{"name": "업종", "officialChangeRateRelativeStrength": 2.0}],
        }
        themes = {
            "generatedAt": "g1", "themes": [{"name": "테마", "stocks": [{"temporaryCurrentBarDate": "20261029"}]}],
        }
        self.assertEqual(len(daily_report.validated_group_source(industries, "industry", "2026-10-29")["industries"]), 1)
        self.assertEqual(len(daily_report.validated_group_source(themes, "theme", "2026-10-29", "g1")["themes"]), 1)
        self.assertEqual(daily_report.validated_group_source(industries, "industry", "2026-10-30")["industries"], [])
        self.assertEqual(daily_report.validated_group_source(themes, "theme", "2026-10-29", "g2")["themes"], [])
    def test_preopen_context_separates_current_observation_from_prior_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = root / "2026-10-29.json"
            previous.write_text(json.dumps({
                "asOf": "2026-10-29",
                "generatedAt": "2026-10-29T23:01:34+09:00",
                "dateAlignment": {"status": "OK"},
                "marketSummary": {"indices": {"indices": [
                    {"name": "KOSPI", "close": 5937.66, "changePoints": -74.78,
                     "changeRate": -1.24, "marketStatus": "CLOSE",
                     "sourceTime": "2026-10-29T20:15:00+09:00"},
                    {"name": "KOSDAQ", "close": 753.23, "changePoints": -18.05,
                     "changeRate": -2.34, "marketStatus": "CLOSE",
                     "sourceTime": "2026-10-29T20:15:00+09:00"}
                ]}},
                "reportContract": {
                    "fullMarket": {"totalCount": 2767,
                                   "breadth": {"advancers": 717, "decliners": 1813, "unchanged": 237},
                                   "metrics": {"aboveMA20": {"count": 10}}},
                    "v2": {"tradingDate": "2026-10-29", "sourceQualityStatus": "SOURCE_OBSERVED",
                           "sourceCommitStatus": "UNAVAILABLE"}
                }
            }), encoding="utf-8")
            context = daily_report.build_preopen_session_context(
                "2026-10-30",
                {"generatedAt": "2026-10-30T08:01:46+09:00", "source": "NAVER_MOBILE_INDEX_BASIC",
                 "indices": [
                     {"name": "KOSPI", "close": 5937.66, "changePoints": 0.0,
                      "changeRate": 0.0, "marketStatus": "PREOPEN", "sourceTime": "2026-10-30T08:01:46+09:00"},
                     {"name": "KOSDAQ", "close": 753.23, "changePoints": 0.0,
                      "changeRate": 0.0, "marketStatus": "PREOPEN", "sourceTime": "2026-10-30T08:01:46+09:00"},
                 ]},
                {"asOfDate": "20261029"}, root
            )
            self.assertEqual(context["mode"], "PREOPEN")
            self.assertEqual(context["status"], "AVAILABLE")
            self.assertEqual(context["observation"]["tradingDate"], "20261030")
            self.assertEqual(context["previousTradingDay"]["tradingDate"], "20261029")
            self.assertEqual(context["previousTradingDay"]["indices"][0]["changeRate"], -1.24)
            self.assertEqual(context["previousTradingDay"]["fullMarket"]["breadth"]["decliners"], 1813)

    def test_preopen_context_rejects_stale_generation_without_losing_prior_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "2026-10-29.json").write_text(json.dumps({
                "asOf": "2026-10-29", "dateAlignment": {"status": "OK"},
                "marketSummary": {"indices": {"indices": [
                    {"name": "KOSPI", "marketStatus": "CLOSE", "sourceTime": "2026-10-29T20:15:00+09:00"},
                    {"name": "KOSDAQ", "marketStatus": "CLOSE", "sourceTime": "2026-10-29T20:15:00+09:00"},
                ]}}
            }), encoding="utf-8")
            context = daily_report.build_preopen_session_context(
                "2026-10-30",
                {"generatedAt": "2026-10-29T23:01:46+09:00", "indices": [
                    {"name": "KOSPI", "marketStatus": "PREOPEN", "sourceTime": "2026-10-30T08:01:46+09:00"},
                    {"name": "KOSDAQ", "marketStatus": "PREOPEN", "sourceTime": "2026-10-30T08:01:46+09:00"},
                ]},
                {"asOfDate": "20261029"}, root
            )
            self.assertEqual(context["status"], "PARTIAL")
            self.assertEqual(context["observation"]["reason"], "PREOPEN_GENERATION_DATE_MISMATCH")
            self.assertEqual(context["previousTradingDay"]["status"], "AVAILABLE")

    def test_full_market_summary_requires_verified_alignment(self):
        available = daily_report.full_market_summary(
            verified_full_market(), {"status": "OK"}
        )
        self.assertEqual(available["status"], "AVAILABLE")
        self.assertEqual(available["breadth"]["advancers"], 1200)
        self.assertNotIn("marketTradingValue", available)

        partial = daily_report.full_market_summary(
            verified_full_market(),
            {"status": "PARTIAL", "mismatches": ["SOURCE_DATE_MISMATCH"]},
        )
        self.assertEqual(partial["status"], "PARTIAL")
        self.assertIsNone(partial["breadth"])
        self.assertNotIn("marketTradingValue", partial)

    def test_full_market_summary_preserves_missing_industry_and_leader_evidence(self):
        payload = verified_full_market()
        payload["industries"][0].update({
            "avgReturn1D": None,
            "advancePct": None,
            "relativeStrengthVsMarket": None,
            "tradingValue": {"total": None},
        })
        payload["leaders"][0] = {
            "code": "005930", "name": "검증 주도주",
            "selectionReasons": [], "technicalEvidence": {"status": "UNAVAILABLE"},
        }
        summary = daily_report.full_market_summary(payload, {"status": "OK"})
        self.assertIsNone(summary["industries"][0]["avgReturn1D"])
        self.assertIsNone(summary["industries"][0]["tradingValue"]["total"])
        self.assertEqual(summary["leaders"][0]["technicalEvidence"]["status"], "UNAVAILABLE")

    def test_shadow_comparison_keeps_v1_and_v2_separate(self):
        dynamic = {
            "sectors": [{"id": "s1", "status": "ACTIVE"}],
            "themes": [{"id": "t1", "status": "ACTIVE"}, {"id": "t2", "status": "REMOVED"}],
            "leaders": [{"code": "005930"}],
        }
        shadow = {"generatedAt": "2026-10-16T12:00:00+09:00", "industries": [], "themes": [{
            "id": "t1", "name": "테마", "v1Status": "ACTIVE", "v2Status": "WATCH",
            "score": 61.5, "components": {"breadth": 80},
            "componentStatus": {"breadth": "VALID", "persistence": "PARTIAL"},
            "persistenceDays": 1, "dataQuality": "PARTIAL",
        }]}
        got = daily_report.shadow_v1_v2_analysis(dynamic, shadow, {"observations": []}, {"tradingDate": "2026-10-16"})
        self.assertEqual(got["v1"]["themes"]["statusCounts"], {"ACTIVE": 1, "REMOVED": 1})
        self.assertEqual(got["v2Shadow"]["themes"]["statusCounts"], {"WATCH": 1})
        self.assertEqual(got["v2Shadow"]["themes"]["rows"][0]["criteria"]["persistence"]["status"], "PARTIAL")

    def test_shadow_history_without_source_dates_stays_partial(self):
        got = daily_report.shadow_v1_v2_analysis({}, {"industries": [], "themes": []}, {
            "observations": [{"tradingDate": "2026-10-16"}]
        }, {"status": "OK", "tradingDate": "2026-10-16"})
        self.assertEqual(got["v2Shadow"]["history"]["uniqueTradingDates"], ["2026-10-16"])
        self.assertEqual(got["observationSources"]["uiObservationCount"], None)

    def test_failed_v2_status_blocks_stale_latest_and_history(self):
        latest, history = daily_report._usable_v2_shadow_inputs(
            {"tradingDate": "2026-10-16", "industries": [{"id": "stale"}]},
            {"observations": [{"tradingDate": "2026-10-16"}]},
            {"status": "FAILED", "tradingDate": "2026-10-17", "errorType": "NameError"},
        )
        self.assertEqual(latest, {})
        self.assertEqual(history, {})

    def test_v2_shadow_failure_is_explicit_and_non_blocking(self):
        got = daily_report.shadow_v1_v2_analysis(
            {}, {}, {}, {
                "status": "GENERATION_MISMATCH",
                "producerStatus": "OK",
                "runId": "run-1",
                "tradingDate": "20261112",
                "generationId": "gen-old",
                "expectedGenerationId": "gen-new",
                "failureReason": "V2_GENERATION_ID_MISMATCH",
            },
        )
        shadow = got["v2Shadow"]
        self.assertEqual(shadow["status"], "GENERATION_MISMATCH")
        self.assertEqual(shadow["consumptionStatus"], "NOT_CONSUMED")
        self.assertEqual(shadow["publicationPolicy"], "PARTIAL_NON_BLOCKING")
        self.assertEqual(shadow["failureReason"], "V2_GENERATION_ID_MISMATCH")

    def test_quote_summary(self):
        row = {
            "price": 100,
            "changeRate": 2.5,
            "volume": 1234,
            "tradingValue": 9999,
            "sourceTime": "2026-09-26T15:30:00+09:00",
            "fresh": True,
            "referencePrice": 99,
            "referenceSourceTime": "20260926153000",
            "referenceStatus": "ok",
            "sessionChange": 1,
            "sessionChangeRate": 1.010101,
        }
        got = daily_report.quote_summary(row)
        self.assertEqual(got["price"], 100)
        self.assertEqual(got["changeRate"], 2.5)
        self.assertEqual(got["volume"], 1234)
        self.assertEqual(got["referencePrice"], 99)
        self.assertEqual(got["sessionChange"], 1)

    def test_enabled_universe(self):
        payload = {
            "stocks": [
                {
                    "itemCode": "000001",
                    "stockName": "A",
                    "enabled": True,
                },
                {
                    "itemCode": "000002",
                    "stockName": "B",
                    "enabled": False,
                },
            ]
        }
        got = daily_report.enabled_universe(payload)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["itemCode"], "000001")

    def test_memberships(self):
        payload = {
            "sectors": {"섹터A": ["000001"]},
            "themes": {"테마A": ["000001"]},
            "watchlists": {"관심": ["000002"]},
        }
        got = daily_report.memberships(payload)
        self.assertEqual(len(got["000001"]), 2)
        self.assertEqual(got["000002"][0]["name"], "관심")

    def test_coverage_status_complete(self):
        self.assertEqual(
            daily_report.coverage_status(
                41, 41, 41, 41, "ok", True
            ),
            "OK",
        )

    def test_coverage_status_incomplete(self):
        self.assertEqual(
            daily_report.coverage_status(41, 36, 36, 36),
            "INCOMPLETE_MARKET_COVERAGE",
        )

    def test_coverage_status_stale(self):
        self.assertEqual(
            daily_report.coverage_status(
                41, 41, 41, 41, "stale", False
            ),
            "STALE_MARKET_DATA",
        )

    def test_coverage_status_fresh(self):
        self.assertEqual(
            daily_report.coverage_status(
                41, 41, 41, 41, "ok", True
            ),
            "OK",
        )

    def test_krx_previous_day_and_naver_current_observation_are_not_stale(self):
        self.assertEqual(
            daily_report.coverage_status(
                42, 42, 42, 42, "ok", False,
                historical_as_of_date="20261103",
                observed_date="20261104",
                current_snapshot_source="NAVER_SEPARATE",
            ),
            "OK",
        )

    def test_old_naver_current_observation_remains_stale(self):
        self.assertEqual(
            daily_report.coverage_status(
                42, 42, 42, 42, "ok", False,
                historical_as_of_date="20261103",
                observed_date="20261101",
                current_snapshot_source="NAVER_SEPARATE",
            ),
            "STALE_MARKET_DATA",
        )

    def test_research_index(self):
        payload = {
            "stocks": [
                {
                    "itemCode": "005930",
                    "status": "OK",
                }
            ]
        }
        got = daily_report.research_index(payload)
        self.assertEqual(got["005930"]["status"], "OK")

    def test_market_breadth_excludes_missing_market_values(self):
        rows = [
            {
                "marketObserved": True,
                "market": {"changeRate": 2.0},
                "state": {
                    "priceVsMA20": "above", "priceVsMA60": "above",
                    "breakout20": "confirmed", "breakout60": "none",
                    "volumeState": "surge", "pullbackState": "near_breakout20",
                },
            },
            {
                "marketObserved": False,
                "market": {"changeRate": None},
                "state": {},
            },
        ]
        got = daily_report.market_breadth(rows)
        self.assertEqual(got["observedCount"], 1)
        self.assertEqual(got["upCount"], 1)
        self.assertEqual(got["breakout20ConfirmedCount"], 1)
        self.assertEqual(got["nearBreakoutCount"], 1)

    def test_market_breadth_zero_denominator_is_none(self):
        got = daily_report.market_breadth([])
        self.assertIsNone(got["upRatio"])
        self.assertIsNone(got["ma20AboveRatio"])

    def test_market_breadth_direction_ratio_excludes_missing_change_rate(self):
        rows = [
            {
                "marketObserved": True, "market": {"changeRate": 1.0},
                "state": {},
            },
            {
                "marketObserved": True, "market": {"changeRate": None},
                "state": {},
            },
        ]
        got = daily_report.market_breadth(rows)
        self.assertEqual(got["observedCount"], 2)
        self.assertEqual(got["directionObservedCount"], 1)
        self.assertEqual(got["upRatio"], 1.0)

    def test_market_breadth_ma20_ratio_excludes_missing_state_value(self):
        rows = [
            {
                "marketObserved": True, "market": {"changeRate": 0.0},
                "state": {"priceVsMA20": "above"},
            },
            {
                "marketObserved": True, "market": {"changeRate": 0.0},
                "state": {},
            },
        ]
        got = daily_report.market_breadth(rows)
        self.assertEqual(got["ma20ObservedCount"], 1)
        self.assertEqual(got["ma20AboveRatio"], 1.0)

    def test_normalized_group_summary_uses_dynamic_payload(self):
        got = daily_report.normalized_group_summary({"groups": [{
            "groupType": "theme", "groupName": "테마A", "enabledMembers": 2,
            "upCount": 1, "downCount": 1, "breakout20ConfirmedCount": 1,
            "volumeSurgeCount": 0, "leaderUpCount": 1,
            "averageChangePct": 0.2, "diffusionState": "mixed", "status": "ok",
        }]})
        self.assertEqual(got[0]["groupType"], "theme")
        self.assertEqual(got[0]["groupName"], "테마A")
        self.assertEqual(got[0]["enabledMembers"], 2)

    def test_technical_events_use_existing_state_labels(self):
        row = {
            "itemCode": "000001", "itemName": "A",
            "market": {"changeRate": 1.0},
            "state": {
                "breakout20": "confirmed", "breakout60": "attempt",
                "volumeState": "elevated", "pullbackState": "near_breakout20",
                "priceVsMA20": "above", "priceVsMA60": "below",
            },
        }
        got = daily_report.technical_events([row])
        self.assertEqual(len(got["breakout20Confirmed"]), 1)
        self.assertEqual(len(got["breakout60Attempt"]), 1)
        self.assertEqual(len(got["volumeElevated"]), 1)
        self.assertEqual(len(got["nearBreakout"]), 1)

    def test_regular_events_do_not_fallback_to_current(self):
        rows = [
            {
                "itemCode": "000001", "itemName": "Confirmed",
                "market": {"changeRate": 1.0},
                "currentState": {"breakout20": "confirmed"},
                "regularSessionState": {"breakout20": "failed"},
            },
            {
                "itemCode": "000002", "itemName": "Unavailable",
                "market": {"changeRate": 2.0},
                "currentState": {"breakout20": "confirmed"},
                "regularSessionState": {},
            },
        ]
        current = daily_report.technical_events(rows, "currentState")
        regular = daily_report.technical_events(rows, "regularSessionState")
        self.assertEqual(len(current["breakout20Confirmed"]), 2)
        self.assertEqual(len(regular["breakout20Confirmed"]), 0)
        self.assertEqual([row["itemCode"] for row in regular["breakout20Failed"]], ["000001"])

    def test_regular_breadth_is_independent_of_current_state(self):
        row = {
            "marketObserved": True, "market": {"changeRate": 3.0},
            "currentState": {"priceVsMA20": "above", "breakout20": "confirmed"},
            "regularSessionState": {"priceVsMA20": "below", "breakout20": "failed"},
        }
        before = daily_report.market_breadth([row], "regularSessionState", False)
        row["currentState"] = {"priceVsMA20": "below", "breakout20": "none"}
        after = daily_report.market_breadth([row], "regularSessionState", False)
        self.assertEqual(before, after)
        self.assertEqual(after["breakout20FailedCount"], 1)
        self.assertEqual(after["directionObservedCount"], 0)

    @staticmethod
    def candidate_row(code, regular=None, current=None, session_rate=0, change_rate=0):
        return {
            "itemCode": code,
            "itemName": f"Stock {code}",
            "regularSessionState": regular or {},
            "currentState": current or {},
            "market": {
                "changeRate": change_rate,
                "referencePrice": 100,
                "referenceSourceTime": "20261006153000",
                "sessionChange": session_rate,
                "sessionChangeRate": session_rate,
            },
        }

    def test_next_candidates_confirmed_breakout_is_top_bucket(self):
        row = self.candidate_row("000001", {
            "status": "CONFIRMED", "breakout20": "confirmed",
            "breakout60": "none", "priceVsMA20": "above",
            "priceVsMA60": "above",
        })
        got = daily_report.next_session_candidates([row], "2026-10-06")
        self.assertEqual(got["priority"][0]["primaryBucket"], "REGULAR_BREAKOUT_STRONG")
        self.assertEqual(got["priority"][0]["breakoutStrength"], "20")
        self.assertIn("REGULAR_ABOVE_MA_MOMENTUM", got["priority"][0]["allBuckets"])

    def test_next_candidates_failed_after_up_is_recovery_not_confirmed(self):
        row = self.candidate_row("000001", {
            "status": "CONFIRMED", "breakout20": "failed", "breakout60": "failed",
        }, session_rate=0.5)
        got = daily_report.next_session_candidates([row], "2026-10-06")["priority"][0]
        self.assertEqual(got["primaryBucket"], "REGULAR_BREAKOUT_FAILED_AFTER_RECOVERY")
        self.assertNotIn("REGULAR_BREAKOUT_STRONG", got["allBuckets"])
        self.assertEqual(got["regularSession"]["breakout20"], "failed")

    def test_next_candidates_above_both_ma_is_momentum(self):
        row = self.candidate_row("000001", {
            "status": "CONFIRMED", "breakout20": "none", "breakout60": "none",
            "priceVsMA20": "above", "priceVsMA60": "above",
        })
        got = daily_report.next_session_candidates([row], "2026-10-06")["priority"][0]
        self.assertEqual(got["primaryBucket"], "REGULAR_ABOVE_MA_MOMENTUM")
        self.assertIn("ABOVE_MA20_MA60", got["tags"])

    def test_next_candidates_excludes_unavailable_regular_state(self):
        row = self.candidate_row("000001", {}, {
            "breakout20": "confirmed", "pullbackState": "near_breakout20",
        })
        got = daily_report.next_session_candidates([row], "2026-10-06")
        self.assertEqual(got["status"], "UNAVAILABLE")
        self.assertEqual(got["candidateCount"], 0)
        self.assertEqual(got["excluded"][0]["reason"], "REGULAR_SESSION_UNAVAILABLE")

    def test_next_candidates_volume_then_after_ordering(self):
        regular = {"status": "CONFIRMED", "breakout20": "confirmed", "breakout60": "none"}
        rows = [
            self.candidate_row("000003", regular, {"volumeState": "normal"}, 10),
            self.candidate_row("000002", regular, {"volumeState": "surge"}, 1),
            self.candidate_row("000001", regular, {"volumeState": "elevated"}, 20),
        ]
        got = daily_report.next_session_candidates(rows, "2026-10-06")["priority"]
        self.assertEqual([row["itemCode"] for row in got], ["000002", "000001", "000003"])

    def test_next_candidates_after_then_market_then_code_ordering_is_deterministic(self):
        regular = {"status": "CONFIRMED", "priceVsMA20": "above", "priceVsMA60": "above"}
        current = {"volumeState": "normal"}
        rows = [
            self.candidate_row("000003", regular, current, 1, 9),
            self.candidate_row("000002", regular, current, 2, 1),
            self.candidate_row("000001", regular, current, 1, 9),
        ]
        first = daily_report.next_session_candidates(rows, "2026-10-06")["priority"]
        second = daily_report.next_session_candidates(list(reversed(rows)), "2026-10-06")["priority"]
        expected = ["000002", "000001", "000003"]
        self.assertEqual([row["itemCode"] for row in first], expected)
        self.assertEqual([row["itemCode"] for row in second], expected)

    def test_next_candidates_current_price_does_not_change_regular_bucket(self):
        regular = {"status": "CONFIRMED", "breakout20": "failed", "breakout60": "none"}
        row = self.candidate_row("000001", regular, {"price": 100}, session_rate=1)
        before = daily_report.next_session_candidates([row], "2026-10-06")["priority"][0]
        row["currentState"]["price"] = 999
        after = daily_report.next_session_candidates([row], "2026-10-06")["priority"][0]
        self.assertEqual(before["primaryBucket"], after["primaryBucket"])
        self.assertEqual(after["regularSession"]["breakout20"], "failed")

    def test_next_candidates_regular_breakout_change_changes_bucket(self):
        regular = {"status": "CONFIRMED", "breakout20": "failed", "breakout60": "none"}
        row = self.candidate_row("000001", regular, session_rate=1)
        before = daily_report.next_session_candidates([row], "2026-10-06")["priority"][0]
        row["regularSessionState"]["breakout20"] = "confirmed"
        after = daily_report.next_session_candidates([row], "2026-10-06")["priority"][0]
        self.assertEqual(before["primaryBucket"], "REGULAR_BREAKOUT_FAILED_AFTER_RECOVERY")
        self.assertEqual(after["primaryBucket"], "REGULAR_BREAKOUT_STRONG")

    def test_next_candidates_current_observation_buckets_use_existing_labels_only(self):
        rows = [
            self.candidate_row("000001", {"status": "CONFIRMED"}, {
                "pullbackState": "near_breakout60", "volumeState": "normal",
            }),
            self.candidate_row("000002", {"status": "CONFIRMED"}, {
                "pullbackState": "pullback_ma20", "volumeState": "normal",
            }),
            self.candidate_row("000003", {"status": "CONFIRMED"}, {
                "pullbackState": "near_ma20", "volumeState": "normal",
            }),
        ]
        got = daily_report.next_session_candidates(rows, "2026-10-06")
        self.assertEqual(got["buckets"]["NEAR_BREAKOUT_WATCH"]["count"], 1)
        self.assertEqual(got["buckets"]["PULLBACK_WATCH"]["count"], 1)
        self.assertEqual(got["candidateCount"], 2)
        self.assertEqual(
            got["buckets"]["NEAR_BREAKOUT_WATCH"]["basis"], "CURRENT_OBSERVATION"
        )

    def test_build_report_preserves_flat_current_and_regular_views(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "universe.json": {"stocks": [{"itemCode": "000001", "stockName": "A", "enabled": True}],
                                  "sectors": {}, "themes": {}, "watchlists": {}},
                "quotes.json": {"status": "ok", "fresh": True, "datas": [{
                    "itemCode": "000001", "closePrice": 103, "fluctuationsRatio": 3,
                    "referencePrice": 99, "referenceSourceTime": "20261006153000",
                    "referenceStatus": "ok", "sessionChange": 4,
                    "sessionChangeRate": 4.040404,
                }]},
                "technicals.json": {"datas": [{"itemCode": "000001", "ma20": 100}]},
                "states.json": {"datas": [{
                    "itemCode": "000001", "priceVsMA20": "above", "breakout20": "confirmed",
                    "current": {"status": "ok", "price": 103, "priceVsMA20": "above",
                                "breakout20": "confirmed"},
                    "regularSession": {"status": "CONFIRMED", "price": 99,
                                       "priceVsMA20": "below", "breakout20": "failed",
                                       "sourceDate": "2026-10-06"},
                }]},
                "research.json": {"stocks": [{"itemCode": "000999", "status": "OK", "rankingEligible": True}]},
            }
            for name, payload in files.items():
                (root / name).write_text(json.dumps(payload), encoding="utf-8")
            with patch.multiple(
                    daily_report,
                    UNIVERSE_PATH=root / "universe.json", QUOTES_PATH=root / "quotes.json",
                    TECHNICALS_PATH=root / "technicals.json", STATES_PATH=root / "states.json",
                    RESEARCH_PATH=root / "research.json", GROUP_STATES_PATH=root / "missing-groups.json",
                    MONITORING_PATH=root / "missing-monitoring"):
                report = daily_report.build_report("2026-10-06")
            stock = report["stocks"][0]
            self.assertEqual(stock["state"]["breakout20"], "confirmed")
            self.assertEqual(stock["currentState"]["breakout20"], "confirmed")
            self.assertEqual(stock["regularSessionState"]["breakout20"], "failed")
            self.assertEqual(len(report["technicalEventsCurrent"]["breakout20Confirmed"]), 1)
            self.assertEqual(len(report["technicalEventsRegularSession"]["breakout20Failed"]), 1)
            self.assertEqual(
                report["nextSessionCandidates"]["priority"][0]["primaryBucket"],
                "REGULAR_BREAKOUT_FAILED_AFTER_RECOVERY",
            )
            self.assertEqual(report["status"], "PARTIAL")
            self.assertEqual(report["dateAlignment"]["status"], "UNAVAILABLE")
            self.assertEqual(report["marketSummary"]["fullMarket"]["status"], "UNAVAILABLE")
            self.assertIsNone(report["marketSummary"]["fullMarket"]["breadth"])
            self.assertEqual(report["dateAlignment"]["fullMarketReason"],
                             "DERIVED_PROJECTION_MISSING")
            report["dateAlignment"] = {
                "status": "UNAVAILABLE", "fullMarketStatus": "UNAVAILABLE",
                "fullMarketReason": "MISSING_EVIDENCE_FILE", "reportTradingDate": "2026-10-06",
                "indexAlignmentStatus": "MISMATCH", "indexSourceDates": ["2026-10-07"],
                "mismatches": ["REPORT_VS_INDEX_SOURCE_DATE"],
                "missingEvidence": ["FULL_MARKET_EVIDENCE"],
            }
            markdown = root / "report.md"
            daily_report.write_markdown(report, markdown)
            rendered = markdown.read_text(encoding="utf-8")
            self.assertIn("Full-market 상태: UNAVAILABLE", rendered)
            self.assertIn("전체시장 수치는 기준일 또는 원천 증거가 검증되지 않아", rendered)
            self.assertIn("설정 종목 수: 1종목", rendered)
            self.assertIn("사유: MISSING_EVIDENCE_FILE", rendered)
            self.assertIn("확인된 지수 날짜: 2026-10-07", rendered)
            self.assertIn("REPORT_VS_INDEX_SOURCE_DATE", rendered)
            self.assertIn("다음 거래일 관찰 후보", rendered)
            self.assertIn("후보 분석 기준일: 2026-10-06", rendered)
            self.assertNotIn("대상 거래일: 2026-10-06", rendered)
            self.assertIn("정규장 돌파 실패 후 회복 관찰", rendered)
            self.assertIn("현재 관찰값 차이", rendered)
            self.assertIn("위험·주의 요인", rendered)
            self.assertIn("정규장 돌파 실패 관찰", rendered)
            self.assertEqual(report["nextSessionCandidates"]["asOf"], "2026-10-06")
            self.assertEqual(report["coverage"]["researchStockCount"], 0)
            self.assertEqual(report["monitoredUniverse"]["scope"], "MONITORED_UNIVERSE_CONFIGURED")

    def test_build_report_separates_empty_configured_universe_from_full_market(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "universe.json": {"stocks": [], "sectors": {}, "themes": {}, "watchlists": {}},
                "quotes.json": {"status": "ok", "fresh": True, "datas": []},
                "technicals.json": {"datas": []},
                "states.json": {"datas": []},
                "research.json": {"stocks": []},
            }
            for name, payload in files.items():
                (root / name).write_text(json.dumps(payload), encoding="utf-8")
            with patch.multiple(
                    daily_report,
                    UNIVERSE_PATH=root / "universe.json", QUOTES_PATH=root / "quotes.json",
                    TECHNICALS_PATH=root / "technicals.json", STATES_PATH=root / "states.json",
                    RESEARCH_PATH=root / "research.json", GROUP_STATES_PATH=root / "missing-groups.json",
                    MONITORING_PATH=root / "missing-monitoring"), \
                    patch.object(daily_report, "full_market_from_derived_projection", return_value=verified_full_market()), \
                    patch.object(daily_report, "date_alignment", return_value={"status": "OK"}):
                report = daily_report.build_report("2026-10-06")
            self.assertEqual(report["coverageStatus"], "EMPTY_CONFIGURED_UNIVERSE")
            self.assertEqual(report["status"], "PARTIAL")
            self.assertEqual(report["coverage"]["enabledStockCount"], 0)
            self.assertEqual(report["monitoredUniverse"]["scope"], "MONITORED_UNIVERSE_EMPTY")
            self.assertEqual(report["monitoredUniverse"]["configuredCount"], 0)
            self.assertEqual(report["marketSummary"]["fullMarket"]["status"], "AVAILABLE")
            self.assertEqual(report["marketSummary"]["fullMarket"]["breadth"]["advancers"], 1200)
            self.assertEqual(report["marketSummary"]["monitoredUniverse"]["configuredCount"], 0)
            markdown = root / "report.md"
            daily_report.write_markdown(report, markdown)
            rendered = markdown.read_text(encoding="utf-8")
            self.assertIn("전체시장 종목: 2766/2766종목", rendered)
            self.assertIn("설정 종목 수: 0종목", rendered)
            self.assertIn("이는 전체시장 종목 수 0을 의미하지 않습니다", rendered)
            self.assertIn("관심종목 설정이 없어 후보를 생성하지 않았습니다", rendered)

    def test_markdown_excludes_candidate_with_mismatched_regular_source_date(self):
        report = {
            "asOf": "2026-10-16",
            "generatedAt": "2026-10-23T20:00:00+09:00",
            "status": "PARTIAL",
            "coverageStatus": "OK",
            "dateAlignment": {"status": "PARTIAL", "reportTradingDate": "2026-10-16"},
            "coverage": {
                "enabledStockCount": 1, "missingMarketCount": 0,
                "missingMarketStocks": [],
            },
            "marketSummary": {
                "fullMarket": {"status": "UNAVAILABLE", "reason": "DATE_MISMATCH"},
                "monitoredUniverse": {"configuredCount": 1, "coverageStatus": "OK"},
                "breadth": {
                    "observedCount": 0, "upCount": 0, "downCount": 0, "flatCount": 0,
                    "ma20AboveCount": 0, "ma20BelowCount": 0, "ma60AboveCount": 0,
                    "ma60BelowCount": 0, "breakout20ConfirmedCount": 0,
                    "breakout20AttemptCount": 0, "breakout20FailedCount": 0,
                    "volumeSurgeCount": 0, "volumeElevatedCount": 0,
                }, "indices": {"status": "UNAVAILABLE"},
            },
            "monitoredUniverse": {
                "scope": "MONITORED_UNIVERSE_CONFIGURED", "configuredCount": 1,
            },
            "groupSummary": [],
            "technicalEventsRegularSession": {
                key: [] for key in (
                    "breakout20Confirmed", "breakout20Attempt", "breakout20Failed",
                    "breakout60Confirmed", "breakout60Attempt", "breakout60Failed",
                    "volumeSurge", "volumeElevated", "nearBreakout", "pullback",
                )
            },
            "researchSummary": {
                "statusCounts": {}, "recentCoverageActiveCount": 0,
                "targetRevisionObservedCount": 0, "risingTopicObservedCount": 0,
                "activeStocks": [],
            },
            "sources": {
                "quotes": {"fresh": False, "status": "UNAVAILABLE", "sourceTime": None, "generatedAt": None},
                "researchIntelligence": {"asOf": None}, "themeSignals": {},
            },
            "marketResearchCross": {}, "fullMarket": {},
            "nextSessionCandidates": {
                "asOf": "2026-10-16", "status": "PARTIAL", "candidateCount": 1,
                "excludedCount": 0, "priority": [{
                    "rank": 1, "itemCode": "000001", "itemName": "혼합 날짜 종목",
                    "allBuckets": ["REGULAR_BREAKOUT_STRONG"],
                    "regularSession": {"sourceDate": "2026-10-23"},
                }], "excluded": [],
            },
            "stocks": [], "dynamicUniverse": {}, "research": {},
            "themeResearch": {}, "shadowAnalysis": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            markdown = Path(directory) / "report.md"
            daily_report.write_markdown(report, markdown)
            rendered = markdown.read_text(encoding="utf-8")
        self.assertIn("정규장 날짜 일치 검증: 0종목", rendered)
        self.assertIn("정규장 원천 날짜 불일치", rendered)
        self.assertNotIn("- 1. 혼합 날짜 종목", rendered)

    def test_markdown_excludes_candidate_with_missing_regular_source_date(self):
        report = {
            "asOf": "2026-10-16", "generatedAt": "2026-10-16T20:00:00+09:00",
            "status": "PARTIAL", "coverageStatus": "OK",
            "dateAlignment": {"status": "PARTIAL", "reportTradingDate": "2026-10-16"},
            "coverage": {
                "enabledStockCount": 1, "missingMarketCount": 0,
                "missingMarketStocks": [],
            },
            "marketSummary": {
                "fullMarket": {"status": "UNAVAILABLE", "reason": "MISSING_EVIDENCE"},
                "monitoredUniverse": {"configuredCount": 1, "coverageStatus": "OK"},
                "breadth": {
                    "observedCount": 0, "upCount": 0, "downCount": 0, "flatCount": 0,
                    "ma20AboveCount": 0, "ma20BelowCount": 0, "ma60AboveCount": 0,
                    "ma60BelowCount": 0, "breakout20ConfirmedCount": 0,
                    "breakout20AttemptCount": 0, "breakout20FailedCount": 0,
                    "volumeSurgeCount": 0, "volumeElevatedCount": 0,
                }, "indices": {"status": "UNAVAILABLE"},
            },
            "monitoredUniverse": {
                "scope": "MONITORED_UNIVERSE_CONFIGURED", "configuredCount": 1,
            },
            "groupSummary": [],
            "technicalEventsRegularSession": {
                key: [] for key in (
                    "breakout20Confirmed", "breakout20Attempt", "breakout20Failed",
                    "breakout60Confirmed", "breakout60Attempt", "breakout60Failed",
                    "volumeSurge", "volumeElevated", "nearBreakout", "pullback",
                )
            },
            "researchSummary": {
                "statusCounts": {}, "recentCoverageActiveCount": 0,
                "targetRevisionObservedCount": 0, "risingTopicObservedCount": 0,
                "activeStocks": [],
            },
            "sources": {
                "quotes": {"fresh": False, "status": "UNAVAILABLE", "sourceTime": None, "generatedAt": None},
                "researchIntelligence": {"asOf": None}, "themeSignals": {},
            },
            "marketResearchCross": {}, "fullMarket": {},
            "nextSessionCandidates": {
                "asOf": "2026-10-16", "status": "UNAVAILABLE", "candidateCount": 1,
                "excludedCount": 0, "priority": [{
                    "rank": 1, "itemCode": "000002", "itemName": "날짜 미확인 종목",
                    "allBuckets": ["REGULAR_BREAKOUT_STRONG"],
                    "regularSession": {},
                }], "excluded": [],
            },
            "stocks": [], "dynamicUniverse": {}, "research": {},
            "themeResearch": {}, "shadowAnalysis": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            markdown = Path(directory) / "report.md"
            daily_report.write_markdown(report, markdown)
            rendered = markdown.read_text(encoding="utf-8")
        self.assertIn("정규장 원천 날짜 미확인", rendered)
        self.assertNotIn("- 1. 날짜 미확인 종목", rendered)

    def test_build_report_keeps_normal_ok_when_date_alignment_is_ok(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "universe.json": {"stocks": [{"itemCode": "000001", "stockName": "A", "enabled": True}],
                                  "sectors": {}, "themes": {}, "watchlists": {}},
                "quotes.json": {"status": "ok", "fresh": True, "datas": [{
                    "itemCode": "000001", "closePrice": 103, "fluctuationsRatio": 3,
                }]},
                "technicals.json": {"datas": [{"itemCode": "000001", "ma20": 100}]},
                "states.json": {"datas": [{"itemCode": "000001", "priceVsMA20": "above"}]},
                "research.json": {"stocks": []},
            }
            for name, payload in files.items():
                (root / name).write_text(json.dumps(payload), encoding="utf-8")
            with patch.multiple(
                    daily_report,
                    UNIVERSE_PATH=root / "universe.json", QUOTES_PATH=root / "quotes.json",
                    TECHNICALS_PATH=root / "technicals.json", STATES_PATH=root / "states.json",
                    RESEARCH_PATH=root / "research.json", GROUP_STATES_PATH=root / "missing-groups.json",
                    MONITORING_PATH=root / "missing-monitoring"), \
                    patch.object(daily_report, "date_alignment", return_value={"status": "OK"}), \
                    patch.object(daily_report, "full_market_from_derived_projection", return_value=verified_full_market()):
                report = daily_report.build_report("2026-10-06")
            self.assertEqual(report["status"], "OK")
            self.assertEqual(report["marketSummary"]["fullMarket"]["status"], "AVAILABLE")
            self.assertEqual(report["marketSummary"]["monitoredUniverse"]["configuredCount"], 1)
            self.assertEqual(report["marketSummary"]["breadth"]["observedCount"], 1)
            self.assertEqual(report["marketSummary"]["topGainersScope"], "MONITORED_UNIVERSE_CONFIGURED")
            self.assertEqual(report["marketSummary"]["monitoredUniverse"]["rankingBasis"],
                             "MONITORED_UNIVERSE_CHANGE_RATE")
            markdown = root / "report.md"
            daily_report.write_markdown(report, markdown)
            rendered = markdown.read_text(encoding="utf-8")
            self.assertIn("전체시장 주도주 근거", rendered)
            self.assertIn("관심종목 등락 상위", rendered)
            self.assertIn("업종 정렬: 1일 평균 수익률 내림차순", rendered)
            self.assertNotIn("전체시장 거래대금", rendered)
            self.assertIn("연구용 테마 자료(운영 선정 아님)", rendered)

            misaligned_full_market = verified_full_market()
            misaligned_full_market.update({
                "asOfDate": "20261016",
                "snapshotDate": "20261023",
                "sourceTime": "2026-10-23T20:00:00+09:00",
            })
            misaligned_alignment = daily_report.date_alignment(
                "2026-10-16",
                {"indices": [{"sourceTime": "2026-10-23T20:15:00+09:00"}]},
                misaligned_full_market,
            )
            report["asOf"] = "2026-10-16"
            report["status"] = "PARTIAL"
            report["dateAlignment"] = misaligned_alignment
            report["marketSummary"]["fullMarket"] = daily_report.full_market_summary(
                misaligned_full_market, misaligned_alignment
            )
            daily_report.write_markdown(report, markdown)
            rendered = markdown.read_text(encoding="utf-8")
            self.assertIn("기준일 정합성 경고", rendered)
            self.assertIn("REPORT_VS_INDEX_SOURCE_DATE", rendered)
            self.assertIn("전체시장 수치는 기준일 또는 원천 증거가 검증되지 않아", rendered)
            self.assertNotIn("- 분석 상태: 검증된 전체시장 자료 사용", rendered)

            regular = {
                "status": "CONFIRMED", "breakout20": "confirmed", "breakout60": "none",
                "price": 100, "ma20": 99, "ma60": 98,
                "priceVsMA20": "above", "priceVsMA60": "above",
                "sourceDate": "2026-10-06", "sourceTime": "2026-10-06T15:30:00+09:00",
            }
            current = {
                "price": 101, "sourceTime": "2026-10-06T16:10:00+09:00",
                "pullbackState": "none", "volumeState": "elevated", "volumeRatio20": 1.4,
            }
            rows = [
                self.candidate_row(f"{index:06d}", regular, current, session_rate=1)
                for index in range(1, 13)
            ]
            report["nextSessionCandidates"] = daily_report.next_session_candidates(rows, "2026-10-06")
            report["nextSessionCandidates"]["excluded"] = [{
                "itemCode": "999999", "itemName": "제외 종목",
                "reason": "REGULAR_SESSION_UNAVAILABLE",
            }]
            report["nextSessionCandidates"]["excludedCount"] = 1
            report["nextSessionCandidates"]["status"] = "PARTIAL"
            daily_report.write_markdown(report, markdown)
            rendered = markdown.read_text(encoding="utf-8")
            self.assertIn("Stock 000001", rendered)
            self.assertIn("Stock 000010", rendered)
            self.assertNotIn("Stock 000011", rendered)
            self.assertIn("제외 종목", rendered)
            self.assertIn("정규장 확정 증거 미확인", rendered)

    def test_coverage_status_empty_configured_universe_is_distinct(self):
        self.assertEqual(
            daily_report.coverage_status(0, 42, 42, 42, "ok", True),
            "EMPTY_CONFIGURED_UNIVERSE",
        )

    def test_date_alignment_keeps_historical_and_current_sources_separate(self):
        got = daily_report.date_alignment(
            "2026-10-16",
            {"indices": [{"sourceTime": "2026-10-23T20:15:00+09:00"}]},
            {
                "status": "AVAILABLE", "asOfDate": "20261016",
                "snapshotDate": None, "snapshotDateStatus": "MISSING",
                "maBasisDates": ["20261023"],
                "marketTradingValue": {
                    "KOSPI": {"sourceTime": "2026-10-23T14:41:53+00:00"},
                },
            },
        )
        self.assertEqual(got["status"], "PARTIAL")
        self.assertIn("FULL_MARKET_TRADING_DATE", got["missingEvidence"])
        self.assertIn("REPORT_VS_INDEX_SOURCE_DATE", got["mismatches"])
        self.assertIn("REPORT_VS_MA_BASIS_DATE", got["mismatches"])

    def test_date_alignment_accepts_explicit_same_date_evidence(self):
        got = daily_report.date_alignment(
            "2026-10-16",
            {"indices": [{"sourceTime": "2026-10-16T20:15:00+09:00"}]},
            {
                "status": "AVAILABLE", "asOfDate": "20261016",
                "snapshotDate": "20261016", "snapshotDateStatus": "VERIFIED_EXPLICIT",
                "maBasisDates": ["20261016"],
                "marketTradingValue": {
                    "KOSPI": {"sourceTime": "2026-10-16T14:41:53+00:00"},
                },
            },
        )
        self.assertEqual(got["status"], "OK")
        self.assertEqual(got["mismatches"], [])

    def test_date_alignment_accepts_krx_historical_and_naver_current_dates_separately(self):
        got = daily_report.date_alignment(
            "2026-11-03",
            {"indices": [{"sourceTime": "2026-11-04T08:05:00+09:00"}]},
            {
                "status": "AVAILABLE", "asOfDate": "20261103",
                "snapshotDate": "20261103", "snapshotDateStatus": "DERIVED_TARGET_DATE",
                "maBasisDates": ["20261103"],
                "marketTradingValue": {
                    "KOSPI": {"sourceTime": "2026-11-03T15:30:00+09:00"},
                },
            },
            observed_date="20261104",
            historical_source="KRX",
            current_snapshot_source="NAVER_SEPARATE",
        )
        self.assertEqual(got["status"], "SEPARATE_SOURCES_ALIGNED")
        self.assertEqual(got["dateAlignmentMode"], "KRX_HISTORICAL_NAVER_CURRENT_SEPARATE")
        self.assertEqual(got["historicalAsOfDate"], "20261103")
        self.assertEqual(got["observedDate"], "20261104")
        self.assertEqual(got["indexAlignmentStatus"], "ALIGNED_TO_OBSERVED_DATE")
        self.assertEqual(got["mismatches"], [])

    def test_krx_projection_keeps_missing_trading_value_explicit_but_accepts_ma_basis_evidence(self):
        got = daily_report.date_alignment(
            "2026-11-03",
            {"indices": [{"sourceTime": "2026-11-04T08:05:00+09:00"}]},
            {
                "status": "AVAILABLE", "asOfDate": "20261103",
                "snapshotDate": "20261103", "snapshotDateStatus": "DERIVED_TARGET_DATE",
                "maBasisDates": ["20261103"],
                "maBasisDateEvidence": {
                    "status": "VERIFIED", "source": "KRX_M10_GENERATION",
                    "sourceDate": "20261103",
                },
                "marketTradingValue": {},
                "marketTradingValueEvidence": {
                    "status": "UNAVAILABLE",
                    "reason": "UNAVAILABLE_M10_FIELD_MISSING",
                    "source": "KRX_M10_GENERATION",
                    "sourceDate": None,
                },
            },
            observed_date="2026-11-04",
            historical_source="KRX",
            current_snapshot_source="NAVER_SEPARATE",
        )
        self.assertEqual(got["status"], "SEPARATE_SOURCES_ALIGNED")
        self.assertEqual(got["missingEvidence"], [])
        self.assertEqual(got["dateAlignmentMissingEvidence"], [])

    def test_market_trading_value_missing_does_not_affect_date_alignment(self):
        got = daily_report.date_alignment(
            "2026-11-03",
            {"indices": [{"sourceTime": "2026-11-04T08:05:00+09:00"}]},
            {
                "status": "AVAILABLE", "asOfDate": "20261103",
                "snapshotDate": "20261103", "snapshotDateStatus": "DERIVED_TARGET_DATE",
                "maBasisDates": ["20261103"],
                "marketTradingValue": {},
                "marketTradingValueEvidence": {
                    "status": "UNAVAILABLE", "reason": "EVIDENCE_NOT_PROVIDED",
                },
            },
            observed_date="2026-11-04",
            historical_source="KRX",
            current_snapshot_source="NAVER_SEPARATE",
        )
        self.assertEqual(got["status"], "SEPARATE_SOURCES_ALIGNED")
        self.assertEqual(got["missingEvidence"], [])
        self.assertEqual(got["dateAlignmentMissingEvidence"], [])

    def test_daily_report_json_keeps_stage_partial_without_market_trading_value(self):
        full_market = {
            "status": "AVAILABLE",
            "asOfDate": "20261104",
            "snapshotDate": "20261104",
            "snapshotDateStatus": "DERIVED_TARGET_DATE",
            "breadth": {"advancers": 1, "decliners": 0, "unchanged": 0},
            "marketTradingValue": {},
            "marketTradingValueEvidence": {
                "status": "UNAVAILABLE",
                "reason": "UNAVAILABLE_M10_FIELD_MISSING",
            },
            "maBasisDates": ["20261104"],
        }
        alignment = daily_report.date_alignment(
            "20261104",
            {"indices": [{"sourceTime": "2026-11-05T08:05:00+09:00"}]},
            full_market,
            observed_date="20261105",
            historical_source="KRX",
            current_snapshot_source="NAVER_SEPARATE",
        )
        report = {
            "status": "PARTIAL",
            "statusReason": "KRX_STAGED_PROJECTION_NOT_FINAL",
            "asOf": "20261104",
            "dateAlignment": alignment,
            "marketSummary": {"fullMarket": daily_report.full_market_summary(full_market, alignment)},
        }
        payload = json.dumps(report, ensure_ascii=False)
        loaded = json.loads(payload)
        self.assertEqual(loaded["status"], "PARTIAL")
        self.assertEqual(loaded["statusReason"], "KRX_STAGED_PROJECTION_NOT_FINAL")
        self.assertEqual(loaded["dateAlignment"]["status"], "SEPARATE_SOURCES_ALIGNED")
        self.assertNotIn("MARKET_TRADING_VALUE_SOURCE_DATE", loaded["dateAlignment"]["missingEvidence"])
        self.assertNotIn("marketTradingValue", loaded["marketSummary"]["fullMarket"])

    def test_date_alignment_rejects_old_naver_current_index_for_separate_sources(self):
        got = daily_report.date_alignment(
            "2026-11-03",
            {"indices": [{"sourceTime": "2026-11-02T15:30:00+09:00"}]},
            {
                "status": "AVAILABLE", "asOfDate": "20261103",
                "snapshotDate": "20261103", "snapshotDateStatus": "DERIVED_TARGET_DATE",
                "maBasisDates": ["20261103"],
                "marketTradingValue": {
                    "KOSPI": {"sourceTime": "2026-11-03T15:30:00+09:00"},
                },
            },
            observed_date="20261104",
            historical_source="KRX",
            current_snapshot_source="NAVER_SEPARATE",
        )
        self.assertEqual(got["status"], "PARTIAL")
        self.assertIn("OBSERVED_VS_INDEX_SOURCE_DATE", got["mismatches"])

    def test_date_alignment_reports_unavailable_full_market_and_index_mismatch(self):
        got = daily_report.date_alignment(
            "2026-10-16",
            {"indices": [{"sourceTime": "2026-10-23T20:15:00+09:00"}]},
            {"status": "UNAVAILABLE", "reason": "MISSING_EVIDENCE_FILE"},
        )
        self.assertEqual(got["status"], "UNAVAILABLE")
        self.assertEqual(got["fullMarketReason"], "MISSING_EVIDENCE_FILE")
        self.assertEqual(got["indexAlignmentStatus"], "MISMATCH")
        self.assertIn("REPORT_VS_INDEX_SOURCE_DATE", got["mismatches"])
        self.assertIn("FULL_MARKET_EVIDENCE", got["missingEvidence"])

    def test_date_alignment_reports_unavailable_full_market_and_index_match(self):
        got = daily_report.date_alignment(
            "2026-10-16",
            {"indices": [{"sourceTime": "2026-10-16T20:15:00+09:00"}]},
            {"status": "UNAVAILABLE", "reason": "QUALITY_NOT_FINAL"},
        )
        self.assertEqual(got["status"], "UNAVAILABLE")
        self.assertEqual(got["indexAlignmentStatus"], "ALIGNED")
        self.assertEqual(got["mismatches"], [])
        self.assertIn("FULL_MARKET_EVIDENCE", got["missingEvidence"])

    def test_research_summary_requires_ranking_eligible_for_active_stocks(self):
        rows = [
            {
                "itemCode": "000001", "itemName": "A",
                "research": {
                    "rankingEligible": True, "recentReportCount": 2,
                    "targetMeanChangePct": 1.0, "revisionUp": 1,
                    "revisionDown": 0, "risingTopics": [{"topic": "AI"}],
                },
            },
            {
                "itemCode": "000002", "itemName": "B",
                "research": {
                    "rankingEligible": False, "recentReportCount": 5,
                    "targetMeanChangePct": 3.0, "revisionUp": 1,
                    "revisionDown": 0, "risingTopics": [],
                },
            },
        ]
        got = daily_report.research_summary(rows, {"statusCounts": {"OK": 1}})
        self.assertEqual([x["itemCode"] for x in got["activeStocks"]], ["000001"])
        self.assertEqual(got["recentCoverageActiveCount"], 1)

    def test_market_research_cross_uses_boolean_buckets(self):
        rows = [
            {
                "itemCode": "000001", "itemName": "A", "marketObserved": True,
                "market": {"changeRate": 1.0},
                "state": {"priceVsMA20": "above"},
                "research": {"rankingEligible": True, "recentReportCount": 1},
            },
            {
                "itemCode": "000002", "itemName": "B", "marketObserved": False,
                "market": {"changeRate": None}, "state": {},
                "research": {"rankingEligible": False, "recentReportCount": 0},
            },
        ]
        got = daily_report.market_research_cross(rows)
        self.assertEqual(got["MARKET_ACTIVE_RESEARCH_ACTIVE"]["count"], 1)
        self.assertEqual(got["INSUFFICIENT_DATA"]["count"], 1)

    def test_market_research_cross_quiet_and_weak_limited_are_descriptive(self):
        rows = [
            {
                "itemCode": "000001", "itemName": "Quiet", "marketObserved": True,
                "market": {"changeRate": 0.0}, "state": {},
                "research": {"rankingEligible": False, "recentReportCount": 0},
            },
            {
                "itemCode": "000002", "itemName": "Weak", "marketObserved": True,
                "market": {"changeRate": -1.0}, "state": {},
                "research": {"rankingEligible": False, "recentReportCount": 0},
            },
        ]
        got = daily_report.market_research_cross(rows)
        self.assertEqual(got["MARKET_QUIET_RESEARCH_LIMITED"]["count"], 1)
        self.assertEqual(got["MARKET_WEAK_RESEARCH_LIMITED"]["count"], 1)
        self.assertEqual(got["INSUFFICIENT_DATA"]["count"], 0)

    def test_market_research_cross_insufficient_requires_missing_market_value(self):
        rows = [
            {
                "itemCode": "000001", "itemName": "Observed", "marketObserved": True,
                "market": {"changeRate": 0.0}, "state": {},
                "research": {"rankingEligible": False, "recentReportCount": 0},
            },
            {
                "itemCode": "000002", "itemName": "Missing", "marketObserved": False,
                "market": {"changeRate": None}, "state": {},
                "research": {"rankingEligible": False, "recentReportCount": 0},
            },
        ]
        got = daily_report.market_research_cross(rows)
        self.assertEqual(got["MARKET_QUIET_RESEARCH_LIMITED"]["count"], 1)
        self.assertEqual(got["INSUFFICIENT_DATA"]["count"], 1)

    def test_report_writer_does_not_create_json_history(self):
        report = {
            "asOf": "2026-10-16",
            "status": "OK",
            "coverageStatus": "OK",
            "coverage": {"enabledStockCount": 0},
            "researchSummary": {"rankingEligibleCount": 0},
        }
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(daily_report, "build_report", return_value=report), \
                 patch.object(daily_report, "write_markdown"), \
                 patch.object(daily_report, "write_work_markdown"), \
                 patch.object(daily_report, "render_contract_views", return_value={"markdown": str(Path(directory) / "latest.md")}), \
                 patch.object(daily_report, "render_dashboard_html", return_value="<html></html>"), patch(
                     "sys.argv", ["daily_report.py", "--as-of", "2026-10-16", "--output-dir", directory]
                 ):
                daily_report.main()
            output = Path(directory)
            self.assertFalse((output / "latest.json").exists())
            self.assertFalse((output / "2026-10-16.json").exists())
            self.assertFalse((output / "latest.html").exists())
            self.assertFalse((output / "2026-10-16.html").exists())


if __name__ == "__main__":
    unittest.main()
