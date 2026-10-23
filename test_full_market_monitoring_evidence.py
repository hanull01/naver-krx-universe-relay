import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "evidence", Path(__file__).parent / "tools" / "full_market_monitoring_evidence.py"
)
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


def history(date, close, volume=100, feature_valid=True, high=None, low=None):
    return {"date": date, "open": close - 1, "high": high if high is not None else close + 1,
            "low": low if low is not None else close - 2, "close": close, "volume": volume,
            "feature_valid": feature_valid}


def current(code="005930", market="KOSPI", price=130, volume=200):
    return {"code": code, "name": code, "market": market, "currentPrice": price, "change": 2,
            "changeRate": 1.56, "openPrice": price - 1, "highPrice": price + 2, "lowPrice": price - 3,
            "accumulatedTradingVolume": volume, "accumulatedTradingValue": 1000, "marketCap": 1000,
            "high52Week": 132, "low52Week": 50, "snapshotGeneratedAt": "2026-10-01T10:00:00+09:00"}


class EvidenceTests(unittest.TestCase):
    def test_required_discovery_codes_are_retained_in_signal_subset(self):
        rows = [evidence.feature_row(current("005930"), [history("20260930", 100)] * 20, "20261015"),
                evidence.feature_row(current("006400"), [history("20260930", 100)] * 20, "20261015")]
        selected = evidence.signal_subset(rows, ["006400"])
        self.assertIn("006400", [row["code"] for row in selected])

    def test_verified_new_listing_without_baseline_is_history_insufficient(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "t", "expectedCount": 2,
                    "coverageCount": 2, "attemptCount": 1, "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current("005930"), current("179880", "KOSDAQ")]}
        baseline = {"asOfDate": "20261014", "count": 1, "authoritativeCount": 1,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": "005930", "returnBases": {}, "rollingHighCounts": {},
                                "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None}]}
        with tempfile.TemporaryDirectory() as directory:
            original = evidence.UNIVERSE_FILE
            evidence.UNIVERSE_FILE = Path(directory) / "universe.json"
            evidence.UNIVERSE_FILE.write_text(json.dumps({"metadata": {"lifecycle": {"events": [
                {"type": "NEW_LISTING", "code": "179880", "effectiveDate": "20261015",
                 "sourceVerified": True}]}}}), encoding="utf-8")
            try:
                context = {"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"}
                built, _ = evidence.build_evidence(snapshot, Path(directory), baseline=baseline,
                                                    publication_context=context, today="20261015")
                self.assertEqual(context["baselineDiagnostics"]["newListingHistoryInsufficient"], ["179880"])
                row = next(item for item in built["latest-stock-signals.json"]["stocks"]
                           if item["code"] == "179880")
                self.assertEqual(row["historyStatus"], "HISTORY_INSUFFICIENT")
            finally:
                evidence.UNIVERSE_FILE = original

    def test_existing_baseline_gap_remains_fatal(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "t", "expectedCount": 1,
                    "coverageCount": 1, "attemptCount": 1, "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current("005930")]}
        baseline = {"asOfDate": "20261014", "count": 0, "authoritativeCount": 0,
                    "coveragePct": 0.0, "historyStatus": "OK", "stocks": []}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "baseline missing 1 current codes"):
                evidence.build_evidence(snapshot, Path(directory), baseline=baseline,
                                        publication_context={"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"},
                                        today="20261015")

    def test_complete_snapshot_allows_unverified_composition_change(self):
        snapshot = {"status": "SUCCESS", "collectionStatus": "SUCCESS", "generatedAt": "t",
                    "expectedCount": 2, "coverageCount": 2, "attemptCount": 1,
                    "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current("005930"), current("0158S0", "KOSDAQ")]}
        baseline = {"asOfDate": "20261014", "count": 1, "authoritativeCount": 1,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": "005930", "returnBases": {},
                                "rollingHighCounts": {}, "rollingHighs": {},
                                "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None}]}
        with tempfile.TemporaryDirectory() as directory:
            context = {"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"}
            built, _ = evidence.build_evidence(snapshot, Path(directory), baseline=baseline,
                                                publication_context=context, today="20261015")
            self.assertEqual(built["latest-quality.json"]["publicationStatus"], "SUCCESS")
            self.assertEqual(context["baselineDiagnostics"]["unverifiedCompositionChangeCodes"], ["0158S0"])
            row = next(item for item in built["latest-stock-signals.json"]["stocks"]
                       if item["code"] == "0158S0")
            self.assertEqual(row["historyStatus"], "HISTORY_INSUFFICIENT")

    def test_composition_change_with_single_no_quote_keeps_evidence_publishable(self):
        snapshot = {"status": "COMPLETE_RECONCILIATION_PENDING", "collectionStatus": "SUCCESS",
                    "qualityStatus": "WARNING", "generatedAt": "t", "expectedCount": 2,
                    "coverageCount": 1, "attemptCount": 3, "duplicateCodes": [],
                    "missingCodes": [], "stocks": [current("005930"), current("0158S0", "KOSDAQ")]}
        baseline = {"asOfDate": "20261014", "count": 1, "authoritativeCount": 1,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": "005930", "returnBases": {},
                                "rollingHighCounts": {}, "rollingHighs": {},
                                "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None}]}
        with tempfile.TemporaryDirectory() as directory:
            context = {"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"}
            built, _ = evidence.build_evidence(snapshot, Path(directory), baseline=baseline,
                                                publication_context=context, today="20261015")
            self.assertEqual(built["latest-quality.json"]["publicationStatus"], "COMPLETE_RECONCILIATION_PENDING")
            self.assertIn("0158S0", context["baselineDiagnostics"]["unverifiedCompositionChangeCodes"])

    def test_reconciliation_pending_snapshot_allows_composition_change(self):
        snapshot = {"status": "COMPLETE_RECONCILIATION_PENDING",
                    "collectionStatus": "SUCCESS",
                    "qualityStatus": "WARNING", "generatedAt": "t",
                    "expectedCount": 2, "coverageCount": 1, "attemptCount": 2,
                    "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current("005930"), current("0158S0", "KOSDAQ")]}
        baseline = {"asOfDate": "20261020", "count": 1,
                    "authoritativeCount": 1, "coveragePct": 100.0,
                    "historyStatus": "OK", "stocks": [{
                        "code": "005930", "returnBases": {}, "rollingHighCounts": {},
                        "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                        "previousMa60": None}]}
        with tempfile.TemporaryDirectory() as directory:
            context = {"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"}
            built, _ = evidence.build_evidence(
                snapshot, Path(directory), baseline=baseline,
                publication_context=context, today="20261021")
        self.assertEqual(built["latest-quality.json"]["publicationStatus"],
                         "COMPLETE_RECONCILIATION_PENDING")

    def test_run_39538406973_missing_quote_is_publishable(self):
        snapshot = {"status": "COMPLETE_RECONCILIATION_PENDING",
                    "collectionStatus": "SUCCESS", "qualityStatus": "WARNING",
                    "generatedAt": "20261021T1224", "expectedCount": 2764,
                    "count": 2764, "coverageCount": 2763, "attemptCount": 2,
                    "duplicateCodes": [], "missingCodes": ["0158S0"],
                    "stocks": [current("005930"), current("0158S0", "KOSDAQ")]}
        baseline = {"asOfDate": "20261020", "count": 1,
                    "authoritativeCount": 1, "coveragePct": 100.0,
                    "historyStatus": "OK", "stocks": [{
                        "code": "005930", "returnBases": {}, "rollingHighCounts": {},
                        "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                        "previousMa60": None}]}
        with tempfile.TemporaryDirectory() as directory:
            context = {"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"}
            built, _ = evidence.build_evidence(
                snapshot, Path(directory), baseline=baseline,
                publication_context=context, today="20261021")
        self.assertEqual(built["latest-quality.json"]["publicationStatus"],
                         "COMPLETE_RECONCILIATION_PENDING")
        self.assertIn("0158S0", context["baselineDiagnostics"]["unverifiedCompositionChangeCodes"])

    def test_structural_page_missing_remains_blocked(self):
        snapshot = {"status": "COMPLETE_RECONCILIATION_PENDING",
                    "collectionStatus": "SUCCESS", "qualityStatus": "WARNING",
                    "generatedAt": "t", "expectedCount": 2, "count": 1,
                    "coverageCount": 1, "attemptCount": 2, "duplicateCodes": [],
                    "missingCodes": ["0158S0"], "pageMissingCodes": ["0158S0"],
                    "stocks": [current("005930"), current("0158S0", "KOSDAQ")]}
        baseline = {"asOfDate": "20261020", "count": 1,
                    "authoritativeCount": 1, "coveragePct": 100.0,
                    "historyStatus": "OK", "stocks": [{"code": "005930"}]}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                evidence.build_evidence(snapshot, Path(directory), baseline=baseline,
                                        publication_context={"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"},
                                        today="20261021")

    def test_current_history_merge_no_duplicate_and_no_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / "005930.jsonl"
            rows = [history(f"202609{day:02d}", 100 + day) for day in range(1, 22)] + [history("20261001", 999)]
            original = "".join(json.dumps(row) + "\n" for row in rows)
            path.write_text(original, encoding="utf-8")
            feature = evidence.feature_row(current(), evidence.read_history("005930", root, "20261001"), "20261001")
            self.assertNotEqual(feature["ma20"], 999)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_ma_return_rolling_high_breakout_and_volume_ratio(self):
        history_rows = [history(f"202609{day:02d}", 100 + day, 100) for day in range(1, 21)]
        item = current(price=130, volume=200)
        item["highPrice"] = 140
        feature = evidence.feature_row(item, history_rows, "20261001")
        self.assertIsNotNone(feature["ma20"])
        self.assertIsNotNone(feature["ret_5d"])
        self.assertEqual(feature["rolling_high_20"], 121.0)
        self.assertTrue(feature["breakout_20_intraday"])
        self.assertEqual(feature["current_volume_ratio_20"], 2.0)

    def test_invalid_ohlc_and_zero_volume_remain_unfixed(self):
        item = current(price=100, volume=0)
        item.update(openPrice=0, highPrice=0, lowPrice=0)
        feature = evidence.feature_row(item, [history("20260930", 99)], "20261001")
        self.assertTrue(feature["close_valid"])
        self.assertFalse(feature["ohlc_valid"])
        self.assertFalse(feature["volume_valid"])
        self.assertIsNone(feature["breakout_20_intraday"])

    def test_breadth_has_market_splits(self):
        rows = []
        for item in (current("A", "KOSPI", 110), current("B", "KOSDAQ", 90)):
            item["changeRate"] = 1 if item["market"] == "KOSPI" else -1
            rows.append(evidence.feature_row(item, [history("20260930", 100)] * 20, "20261001"))
        self.assertEqual(evidence.breadth(rows, "TOTAL")["stockCount"], 2)
        self.assertEqual(evidence.breadth(rows, "KOSPI")["advancers"], 1)
        self.assertEqual(evidence.breadth(rows, "KOSDAQ")["decliners"], 1)
        total = evidence.breadth(rows, "TOTAL")
        self.assertIn("aboveMA20Count", total)
        self.assertIn("breakout20Count", total)
        for prefix in ("aboveMA120", "near52WeekHigh", "volumeAbove20DayAverage"):
            self.assertIn(prefix + "Count", total)
            self.assertIn(prefix + "EligibleCount", total)
            eligible = total[prefix + "EligibleCount"]
            self.assertEqual(total["pct" + prefix[0].upper() + prefix[1:]],
                             round(total[prefix + "Count"] * 100 / eligible, 4) if eligible else None)

    def test_baseline_and_canonical_history_coverage_are_distinct(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "t", "expectedCount": 1,
                    "coverageCount": 1, "attemptCount": 1, "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current()]}
        baseline_stock = {"code": "005930", "lastTradingDate": "20261001",
                          "returnBases": {}, "rollingHighCounts": {}, "rollingHighs": {},
                          "volumeState20": {}, "previousMa20": None, "previousMa60": None}
        baseline = {"asOfDate": "20261001", "count": 1, "authoritativeCount": 1,
                    "coveragePct": 100.0, "historyStatus": "OK", "stocks": [baseline_stock]}
        with tempfile.TemporaryDirectory() as directory:
            built, _ = evidence.build_evidence(
                snapshot, Path(directory), baseline=baseline,
                publication_context={"baselineFreshnessStatus": "VALID_CURRENT_DAY_FINAL"},
                today="20261001")
        quality = built["latest-quality.json"]
        self.assertEqual(quality["productionBaselineCoveragePct"], 100.0)
        self.assertEqual(quality["productionBaselineCount"], 1)
        self.assertEqual(quality["canonicalHistoryFileCoveragePct"], 0.0)
        self.assertEqual(quality["missingCanonicalHistoryCodes"], ["005930"])
        self.assertEqual(quality["historyCoverageSource"], "CANONICAL_HISTORY_FILES_DEPRECATED_ALIAS")

    def test_incomplete_baseline_reports_degraded_production_coverage(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "t", "expectedCount": 1,
                    "coverageCount": 1, "attemptCount": 1, "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current()]}
        baseline = {"asOfDate": "20261001", "count": 0, "authoritativeCount": 1,
                    "coveragePct": 0.0, "historyStatus": "PARTIAL", "stocks": []}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                evidence.build_evidence(snapshot, Path(directory), baseline=baseline,
                                        publication_context={"baselineFreshnessStatus": "VALID_CURRENT_DAY_FINAL"},
                                        today="20261001")

    def test_explicit_full_market_scope_does_not_share_monitored_universe_metrics(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "t", "expectedCount": 2, "coverageCount": 2,
                    "attemptCount": 1, "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current("A", "KOSPI"), current("B", "KOSDAQ")]}
        context = {"monitoredUniverse": {"scope": "MONITORED_UNIVERSE", "configuredCount": 42,
                                             "current": {"stockCount": 42, "advancers": 35}},
                   "monitoredUniverseLeaders": [{"code": "M", "name": "관심"}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for code in ("A", "B"):
                (root / f"{code}.jsonl").write_text("\n".join(json.dumps(history(f"202609{d:02d}", 100 + d)) for d in range(1, 22)), encoding="utf-8")
            built, _ = evidence.build_evidence(snapshot, root, publication_context=context, today="20261001")
        summary = built["latest-summary.json"]
        self.assertEqual(summary["fullMarket"]["scope"], "AUTHORITATIVE_FULL_MARKET")
        self.assertEqual(summary["fullMarket"]["markets"]["TOTAL"]["stockCount"], 2)
        self.assertEqual(summary["monitoredUniverse"]["configuredCount"], 42)
        self.assertEqual(built["latest-leaders.json"]["scope"], "AUTHORITATIVE_FULL_MARKET")
        self.assertEqual(built["latest-leaders.json"]["monitoredUniverseLeaders"][0]["code"], "M")

    def test_previous_changes_and_industry_partial_propagate(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-01T10:00:00+09:00", "expectedCount": 2,
                    "coverageCount": 2, "attemptCount": 1, "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current("A", "KOSPI"), current("B", "KOSDAQ")]}
        industries = {"status": "PARTIAL", "industries": [{"id": "1", "name": "업종"}], "diagnostics": {
            "pagesFetched": 1, "failedPage": 1, "currentCoveragePct": 66.67,
            "missingCategoryIds": ["2"], "partialReason": "page failure",
            "comparisonSource": "previous_successful_snapshot"}}
        membership = {"status": "OK", "industries": [{"id": "1", "name": "업종", "status": "OK", "members": [{"code": "A"}, {"code": "B"}]}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for code in ("A", "B"):
                (root / f"{code}.jsonl").write_text("\n".join(json.dumps(history(f"202609{d:02d}", 100 + d)) for d in range(1, 22)), encoding="utf-8")
            previous = {"generatedAt": "2026-10-01T09:00:00+09:00", "breadth": {"TOTAL": {"advancerPct": 20, "pctAboveMA20": 30, "pctAboveMA60": 40, "pctBreakout20": 0, "pctNear52WeekHigh": 1, "medianReturn1D": 2, "pctVolumeAbove20DayAverage": 5}}}
            previous_good = {"generatedAt": "2026-10-01T08:00:00+09:00"}
            built, _ = evidence.build_evidence(
                snapshot, root, industries, membership, previous, "20261001",
                previous_good_industries=previous_good,
            )
        quality = built["latest-industries.json"]["quality"]
        self.assertEqual(quality["industryEvidenceUsable"], "PARTIAL_WITH_COVERAGE")
        self.assertEqual(quality["missingCount"], 1)
        self.assertEqual(quality["previousGoodGeneratedAt"], previous_good["generatedAt"])
        self.assertTrue(built["latest-changes.json"]["comparisonAvailable"])

    def test_theme_disabled_and_quality_summary(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "t", "expectedCount": 1, "coverageCount": 1,
                    "attemptCount": 1, "duplicateCodes": [], "missingCodes": [], "stocks": [current()]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "005930.jsonl").write_text(json.dumps(history("20260930", 100)) + "\n", encoding="utf-8")
            built, _ = evidence.build_evidence(snapshot, root, None, None, None, "20261001")
        self.assertEqual(built["latest-summary.json"]["themeEvidenceStatus"], "DISABLED_PENDING_RESEARCH_GATE")

    def test_verified_theme_membership_is_reflected_in_summary(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "t", "expectedCount": 1, "coverageCount": 1,
                    "attemptCount": 1, "duplicateCodes": [], "missingCodes": [], "stocks": [current()]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "005930.jsonl").write_text(json.dumps(history("20260930", 100)) + "\n", encoding="utf-8")
            membership = {"status": "OK", "generatedAt": "2026-10-14T17:00:00+09:00", "themes": [{"id": "520", "members": [{"code": "005930"}]}]}
            built, _ = evidence.build_evidence(snapshot, root, None, None, None, "20261001", theme_membership=membership)
        summary = built["latest-summary.json"]
        self.assertEqual(summary["themeEvidenceStatus"], "AVAILABLE_RESEARCH_MEMBERSHIP")
        self.assertEqual(summary["availableThemeCount"], 1)
        self.assertEqual(built["latest-quality.json"]["historyCoveragePct"], 100.0)

    def test_reconciliation_pending_snapshot_remains_analyzable_with_quality_status(self):
        snapshot = {"status": "COMPLETE_RECONCILIATION_PENDING", "generatedAt": "t", "expectedCount": 1,
                    "coverageCount": 1, "attemptCount": 1, "duplicateCodes": [], "missingCodes": [],
                    "universeDelta": {"added": ["005930"], "verificationStatus": "PENDING_EXTERNAL_VERIFICATION"},
                    "stocks": [current()]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "005930.jsonl").write_text(json.dumps(history("20260930", 100)) + "\n", encoding="utf-8")
            built, _ = evidence.build_evidence(snapshot, root, None, None, None, "20261001")
        self.assertEqual(built["latest-quality.json"]["collectionQualityStatus"], "COMPLETE_RECONCILIATION_PENDING")
        self.assertEqual(built["latest-summary.json"]["fullMarket"]["qualityStatus"], "COMPLETE_RECONCILIATION_PENDING")

    def test_leaders_include_evidence_backed_reasons_and_technical_checks(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-01T10:00:00+09:00", "expectedCount": 2,
                    "coverageCount": 2, "attemptCount": 1, "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current("A", price=130, volume=200), current("B", price=90, volume=200)]}
        snapshot["stocks"][0]["changeRate"] = 5
        snapshot["stocks"][1]["changeRate"] = -2
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for code in ("A", "B"):
                (root / f"{code}.jsonl").write_text("\n".join(json.dumps(history(f"202609{d:02d}", 100 + d)) for d in range(1, 22)), encoding="utf-8")
            previous = {"previousLeaders": [{"code": "B"}]}
            built, _ = evidence.build_evidence(snapshot, root, previous_summary=previous, today="20261001")
        leaders = built["latest-leaders.json"]["fullMarketLeaders"]
        self.assertEqual(leaders[0]["code"], "A")
        self.assertIn("POSITIVE_RETURN", leaders[0]["selectionReasons"])
        self.assertIn("above_ma20", leaders[0]["technicalChecks"])
        self.assertEqual(built["latest-leaders.json"]["leaderChanges"]["status"], "AVAILABLE")
        self.assertIn("A", built["latest-leaders.json"]["leaderChanges"]["new"])

    def test_industry_reports_breadth_concentration_and_relative_strength(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "t", "expectedCount": 2,
                    "coverageCount": 2, "attemptCount": 1, "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current("A"), current("B")]}
        snapshot["stocks"][0]["changeRate"] = 4
        snapshot["stocks"][1]["changeRate"] = -1
        industries = {"status": "OK", "industries": [{"id": "1", "name": "업종"}], "diagnostics": {}}
        membership = {"status": "OK", "industries": [{"id": "1", "name": "업종", "status": "OK", "members": [{"code": "A"}, {"code": "B"}]}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for code in ("A", "B"):
                (root / f"{code}.jsonl").write_text("\n".join(json.dumps(history(f"202609{d:02d}", 100 + d)) for d in range(1, 22)), encoding="utf-8")
            built, _ = evidence.build_evidence(snapshot, root, industries, membership, today="20261001")
        row = built["latest-industries.json"]["industries"][0]
        self.assertIn(row["breadthConcentration"], ("BROAD", "NARROW", "WEAK"))
        self.assertIn("relativeStrengthVsMarket", row)

    def test_empty_industry_aggregation_is_not_full(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "t", "expectedCount": 1,
                    "coverageCount": 1, "attemptCount": 1, "duplicateCodes": [],
                    "missingCodes": [], "stocks": [current("A")]}
        industries = {"status": "OK", "industries": [{"id": "1", "name": "업종"}],
                      "diagnostics": {}}
        membership = {"status": "PARTIAL", "industries": [{"id": "1", "name": "업종",
                       "status": "ERROR", "members": []}]}
        with tempfile.TemporaryDirectory() as directory:
            built, _ = evidence.build_evidence(snapshot, Path(directory), industries,
                                                membership, today="20261001")
        quality = built["latest-industries.json"]["quality"]
        self.assertEqual(quality["sourceIndustryCount"], 1)
        self.assertEqual(quality["membershipIndustryCount"], 1)
        self.assertEqual(quality["aggregationUsableIndustryCount"], 0)
        self.assertEqual(quality["industryEvidenceUsable"], "UNUSABLE")
        self.assertEqual(quality["partialReason"], "INDUSTRY_MEMBERSHIP_UNUSABLE")

    def test_complete_reconciliation_pending_allows_verified_structural_composition_delta(self):
        snapshot = {"status": "COMPLETE_RECONCILIATION_PENDING",
                    "collectionStatus": "SUCCESS", "qualityStatus": "WARNING",
                    "generatedAt": "20261023T082000", "expectedCount": 4,
                    "count": 3, "coverageCount": 1, "attemptCount": 2,
                    "duplicateCodes": [], "pageMissingCodes": [],
                    "structuralErrors": [], "corruptCodes": [],
                    "missingCodes": ["999999"],
                    "attemptDiagnostics": [{"structurallyValid": True}],
                    "stocks": [current("005930"), current("0013Y0", "KOSDAQ"),
                               current("0158S0", "KOSDAQ")]}
        baseline = {"asOfDate": "20261022", "count": 1,
                    "authoritativeCount": 1, "coveragePct": 100.0,
                    "historyStatus": "OK", "stocks": [{
                        "code": "005930", "returnBases": {}, "rollingHighCounts": {},
                        "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                        "previousMa60": None}]}
        with tempfile.TemporaryDirectory() as directory:
            context = {"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"}
            built, _ = evidence.build_evidence(
                snapshot, Path(directory), baseline=baseline,
                publication_context=context, today="20261023")
        self.assertEqual(built["latest-quality.json"]["publicationStatus"],
                         "COMPLETE_RECONCILIATION_PENDING")
        self.assertEqual(
            context["baselineDiagnostics"]["unverifiedCompositionChangeCodes"],
            ["0013Y0", "0158S0"],
        )



    def test_complete_pending_snapshot_accepts_missing_codes_outside_current_set(self):
        snapshot = {"status": "COMPLETE_RECONCILIATION_PENDING", "collectionStatus": "SUCCESS",
                    "qualityStatus": "WARNING", "generatedAt": "t", "expectedCount": 4,
                    "count": 3, "coverageCount": 3, "attemptCount": 2,
                    "missingCodes": ["0158S0"], "duplicateCodes": [], "pageMissingCodes": [],
                    "structuralErrors": [], "corruptCodes": [],
                    "attemptDiagnostics": [{"structurallyValid": True}],
                    "stocks": [current("005930"), current("0013Y0", "KOSDAQ"),
                               current("0158S0", "KOSDAQ")]}
        baseline = {"asOfDate": "20261022", "count": 1, "authoritativeCount": 1,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": "005930", "returnBases": {}, "rollingHighCounts": {},
                                "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None}]}
        with tempfile.TemporaryDirectory() as directory:
            context = {"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"}
            built, _ = evidence.build_evidence(snapshot, Path(directory), baseline=baseline,
                                                publication_context=context, today="20261023")
            self.assertEqual(context["baselineDiagnostics"]["newListingHistoryInsufficient"],
                             ["0013Y0", "0158S0"])
            self.assertEqual(built["latest-summary.json"]["qualityStatus"],
                             "COMPLETE_RECONCILIATION_PENDING")

    def test_missing_codes_present_in_current_set_remain_structural_failure(self):
        snapshot = {"status": "COMPLETE_RECONCILIATION_PENDING", "collectionStatus": "SUCCESS",
                    "qualityStatus": "WARNING", "generatedAt": "t", "expectedCount": 2,
                    "count": 2, "coverageCount": 2, "attemptCount": 2,
                    "missingCodes": ["0158S0"], "duplicateCodes": [], "pageMissingCodes": [],
                    "structuralErrors": [], "corruptCodes": [],
                    "attemptDiagnostics": [{"structurallyValid": True}],
                    "stocks": [current("005930"), current("0158S0", "KOSDAQ")]}
        baseline = {"asOfDate": "20261022", "count": 1, "authoritativeCount": 1,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": "005930", "returnBases": {}, "rollingHighCounts": {},
                                "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None}]}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "baseline missing 1 current codes"):
                evidence.build_evidence(snapshot, Path(directory), baseline=baseline,
                                        publication_context={"baselineFreshnessStatus":
                                                             "VALID_PREVIOUS_TRADING_DAY"},
                                        today="20261023")


if __name__ == "__main__":
    unittest.main()


