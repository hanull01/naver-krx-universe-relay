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
    def test_full_market_summary_requires_verified_alignment(self):
        available = daily_report.full_market_summary(
            verified_full_market(), {"status": "OK"}
        )
        self.assertEqual(available["status"], "AVAILABLE")
        self.assertEqual(available["breadth"]["advancers"], 1200)
        self.assertEqual(available["marketTradingValue"]["KOSPI"]["value"], 123456789)

        partial = daily_report.full_market_summary(
            verified_full_market(),
            {"status": "PARTIAL", "mismatches": ["SOURCE_DATE_MISMATCH"]},
        )
        self.assertEqual(partial["status"], "PARTIAL")
        self.assertIsNone(partial["breadth"])
        self.assertEqual(partial["marketTradingValue"], {})

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

    def test_resolve_report_trading_date_requires_matching_published_material(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            monitoring = root / "monitoring"
            monitoring.mkdir()
            (monitoring / "latest-quality.json").write_text(json.dumps({
                "baselineAsOfDate": "20261016", "publicationStatus": "SUCCESS",
                "generatedAt": "q",
            }), encoding="utf-8")
            baseline = root / "baseline.json"
            baseline.write_text(json.dumps({
                "asOfDate": "20261016", "historyStatus": "OK", "generatedAt": "b",
            }), encoding="utf-8")
            got = daily_report.resolve_report_trading_date(monitoring, baseline)
            self.assertEqual(got["status"], "READY")
            self.assertEqual(got["tradingDate"], "2026-10-16")
            self.assertEqual(daily_report.resolve_report_trading_date(
                monitoring, baseline, "2026-10-17")["reason"], "REQUESTED_DATE_MISMATCH")

    def test_resolve_report_trading_date_rejects_mismatch_or_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            monitoring = root / "monitoring"
            monitoring.mkdir()
            (monitoring / "latest-quality.json").write_text(json.dumps({
                "baselineAsOfDate": "20261016", "publicationStatus": "SUCCESS",
            }), encoding="utf-8")
            baseline = root / "baseline.json"
            baseline.write_text(json.dumps({"asOfDate": "20261015", "historyStatus": "OK"}), encoding="utf-8")
            self.assertEqual(daily_report.resolve_report_trading_date(monitoring, baseline)["reason"], "TRADING_DATE_MISMATCH")
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

    def test_full_market_evidence_requires_final_same_day_complete_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = daily_report.FULL_MARKET_FILES
            for name in names:
                (root / name).write_text(json.dumps({"generatedAt": "2026-10-02T16:20:00+09:00"}), encoding="utf-8")
            quality = {
                "generatedAt": "2026-10-02T16:20:00+09:00", "baselineAsOfDate": "20261002",
                "publicationStatus": "FINAL", "authoritativeCount": 2768,
                "currentCount": 2768, "currentCoveragePct": 100.0,
                "duplicateCodes": [], "missingCurrentCodes": [],
            }
            (root / "latest-quality.json").write_text(json.dumps(quality), encoding="utf-8")
            breadth = {"TOTAL": {"advancers": 1, "decliners": 2, "unchanged": 3}}
            (root / "latest-breadth.json").write_text(json.dumps({"generatedAt": quality["generatedAt"], "breadth": breadth}), encoding="utf-8")
            (root / "latest-summary.json").write_text(json.dumps({
                "generatedAt": quality["generatedAt"],
                "monitoredUniverse": {
                    "current": {"aboveMA20Count": 22},
                    "regularSession": {"status": "AVAILABLE", "aboveMA20Count": 21},
                },
            }), encoding="utf-8")
            got = daily_report.full_market_evidence("2026-10-02", root)
            self.assertEqual(got["status"], "AVAILABLE")
            self.assertEqual(got["breadth"]["advancers"], 1)
            self.assertEqual(got["monitoredUniverse"]["current"]["aboveMA20Count"], 22)
            self.assertEqual(got["monitoredUniverse"]["regularSession"]["aboveMA20Count"], 21)

    def test_full_market_evidence_rejects_stale_or_incomplete_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in daily_report.FULL_MARKET_FILES:
                (root / name).write_text(json.dumps({"generatedAt": "t"}), encoding="utf-8")
            (root / "latest-quality.json").write_text(json.dumps({
                "publicationStatus": "FINAL", "baselineAsOfDate": "20261001",
                "authoritativeCount": 2768, "currentCount": 2767,
                "currentCoveragePct": 99.96,
            }), encoding="utf-8")
            got = daily_report.full_market_evidence("2026-10-02", root)
            self.assertEqual(got["status"], "UNAVAILABLE")
            self.assertIn(got["reason"], {"BASELINE_DATE_MISMATCH", "CURRENT_COVERAGE_INVALID"})

    def test_full_market_evidence_accepts_previous_session_baseline_with_current_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = "2026-10-12T10:00:00+09:00"
            for name in daily_report.FULL_MARKET_FILES:
                (root / name).write_text(json.dumps({"generatedAt": generated}), encoding="utf-8")
            quality = {
                "generatedAt": generated, "baselineAsOfDate": "20261008",
                "tradingDate": "20261012",
                "baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY",
                "currentSnapshotGeneratedAt": generated, "publicationStatus": "SUCCESS",
                "authoritativeCount": 2767, "currentCount": 2767,
                "currentCoveragePct": 100.0, "duplicateCodes": [], "missingCurrentCodes": [],
            }
            (root / "latest-quality.json").write_text(json.dumps(quality), encoding="utf-8")
            (root / "latest-breadth.json").write_text(json.dumps({
                "generatedAt": generated,
                "breadth": {"TOTAL": {"advancers": 1, "decliners": 1, "unchanged": 0}},
            }), encoding="utf-8")
            self.assertEqual(daily_report.full_market_evidence("2026-10-12", root)["status"],
                             "AVAILABLE")
            quality["currentSnapshotGeneratedAt"] = "2026-10-08T10:00:00+09:00"
            quality["tradingDate"] = "20261008"
            (root / "latest-quality.json").write_text(json.dumps(quality), encoding="utf-8")
            self.assertEqual(daily_report.full_market_evidence("2026-10-12", root)["status"],
                             "UNAVAILABLE")

    def test_full_market_evidence_does_not_infer_trading_date_from_generated_at(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in daily_report.FULL_MARKET_FILES:
                (root / name).write_text(json.dumps({"generatedAt": "2026-10-12T10:00:00+09:00"}), encoding="utf-8")
            (root / "latest-quality.json").write_text(json.dumps({
                "baselineAsOfDate": "20261008",
                "baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY",
                "currentSnapshotGeneratedAt": "2026-10-12T10:00:00+09:00",
                "publicationStatus": "SUCCESS", "authoritativeCount": 1,
                "currentCount": 1, "currentCoveragePct": 100.0,
                "duplicateCodes": [], "missingCurrentCodes": [],
            }), encoding="utf-8")
            self.assertEqual(
                daily_report.full_market_evidence("2026-10-12", root)["reason"],
                "BASELINE_DATE_MISMATCH",
            )

    def test_full_market_evidence_reads_verified_date_from_published_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = "2026-10-15T20:00:00+09:00"
            for name in daily_report.FULL_MARKET_FILES:
                (root / name).write_text(json.dumps({"generatedAt": generated}), encoding="utf-8")
            (root / "latest-quality.json").write_text(json.dumps({
                "generatedAt": generated, "baselineAsOfDate": "20261014",
                "baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY",
                "publicationStatus": "SUCCESS", "authoritativeCount": 1,
                "currentCount": 1, "currentCoveragePct": 100.0,
                "duplicateCodes": [], "missingCurrentCodes": [],
            }), encoding="utf-8")
            (root / "latest-breadth.json").write_text(json.dumps({
                "generatedAt": generated,
                "breadth": {"TOTAL": {"advancers": 1, "decliners": 0, "unchanged": 0}},
                "fullMarket": {
                    "tradingDate": "20261015",
                    "tradingDateEvidence": {"reason": "VERIFIED_CANONICAL_SESSION"},
                },
            }), encoding="utf-8")
            got = daily_report.full_market_evidence("2026-10-15", root)
            self.assertEqual(got["status"], "AVAILABLE")
            self.assertEqual(got["snapshotDate"], "20261015")
            self.assertEqual(got["snapshotDateStatus"], "VERIFIED_EXPLICIT")

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
                                       "priceVsMA20": "below", "breakout20": "failed"},
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
            self.assertIn(report["dateAlignment"]["fullMarketReason"],
                          {"MISSING_EVIDENCE_FILE", "BASELINE_DATE_MISMATCH"})
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
                    patch.object(daily_report, "full_market_evidence", return_value=verified_full_market()), \
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
                    patch.object(daily_report, "full_market_evidence", return_value=verified_full_market()):
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
            self.assertIn("KOSPI 전체시장 거래대금: 123,456,789원", rendered)
            self.assertEqual(rendered.count("KOSPI 전체시장 거래대금"), 1)
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

    def test_date_alignment_keeps_baseline_and_current_sources_separate(self):
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


if __name__ == "__main__":
    unittest.main()
