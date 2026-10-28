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


def current(code="005930", market="KOSPI", price=130, volume=200, **extra):
    row = {"code": code, "name": code, "market": market, "currentPrice": price, "change": 2,
            "changeRate": 1.56, "openPrice": price - 1, "highPrice": price + 2, "lowPrice": price - 3,
            "accumulatedTradingVolume": volume, "accumulatedTradingValue": 1000, "marketCap": 1000,
            "high52Week": 132, "low52Week": 50, "snapshotGeneratedAt": "2026-10-01T10:00:00+09:00"}
    row.update(extra)
    return row


class EvidenceTests(unittest.TestCase):
    def test_verified_full_market_date_is_published_with_evidence(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-15T20:00:00+09:00",
                    "expectedCount": 1, "coverageCount": 1, "attemptCount": 1,
                    "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current(localDate="20261015")]}
        baseline = {"asOfDate": "20261014", "count": 1, "authoritativeCount": 1,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": "005930", "returnBases": {}, "rollingHighCounts": {},
                                "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "005930.jsonl").write_text(json.dumps(history("20261015", 129)) + "\n",
                                                   encoding="utf-8")
            built, _ = evidence.build_evidence(
                snapshot, root, baseline=baseline,
                publication_context={"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"},
                today="20261015",
            )
        full_market = built["latest-breadth.json"]["fullMarket"]
        self.assertEqual(full_market["tradingDate"], "20261015")
        self.assertEqual(full_market["tradingDateEvidence"]["reason"],
                         "VERIFIED_CANONICAL_SESSION")
        self.assertEqual(built["latest-summary.json"]["fullMarket"]["tradingDate"], "20261015")

    def test_missing_full_market_date_is_published_as_unavailable_evidence(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-15T20:00:00+09:00",
                    "expectedCount": 1, "coverageCount": 1, "attemptCount": 1,
                    "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current()]}
        baseline = {"asOfDate": "20261014", "count": 1, "authoritativeCount": 1,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": "005930", "returnBases": {}, "rollingHighCounts": {},
                                "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "005930.jsonl").write_text(json.dumps(history("20261015", 129)) + "\n",
                                                   encoding="utf-8")
            built, _ = evidence.build_evidence(
                snapshot, root, baseline=baseline,
                publication_context={"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"},
                today="20261015",
            )
        full_market = built["latest-breadth.json"]["fullMarket"]
        self.assertIsNone(full_market["tradingDate"])
        self.assertEqual(full_market["tradingDateEvidence"]["reason"],
                         "INSUFFICIENT_TRADING_DATE_EVIDENCE")

    def test_complete_local_date_diagnostic_is_connected_to_full_market_evidence(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-28T09:07:00+09:00",
                    "expectedCount": 2, "coverageCount": 2, "attemptCount": 1,
                    "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current("A", localDate=None), current("B", "KOSDAQ", localDate=None)]}
        baseline = {"asOfDate": "20261027", "count": 2, "authoritativeCount": 2,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": code, "returnBases": {}, "rollingHighCounts": {},
                                "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None} for code in ("A", "B")]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "trading-session-diagnostic.json").write_text(json.dumps({
                "status": "VERIFIED", "source": "NAVER_LOCAL_DATE", "targetDate": "20261028",
                "expectedCount": 2, "observedCount": 2, "expectedCodes": ["A", "B"],
                "observedCodes": ["A", "B"], "observedDates": {"A": "20261028", "B": "20261028"},
                "missingCodes": [], "unexpectedCodes": [], "duplicateCodes": [],
                "mismatchCodes": [], "dateMismatchCodes": []}), encoding="utf-8")
            built, _ = evidence.build_evidence(
                snapshot, root / "canonical", baseline=baseline,
                publication_context={"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"},
                today="20261028", monitoring_daily_dir=root,
            )
        full_market = built["latest-breadth.json"]["fullMarket"]
        self.assertEqual(full_market["tradingDate"], "20261028")
        self.assertEqual(full_market["tradingDateEvidence"]["reason"],
                         "VERIFIED_NAVER_LOCAL_DATE_DIAGNOSTIC")

    def test_intraday_evidence_is_not_daily_confirmation(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-28T10:07:03+09:00",
                    "expectedCount": 1, "coverageCount": 1, "attemptCount": 1,
                    "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current(snapshotGeneratedAt="2026-10-28T10:07:00+09:00")]}
        baseline = {"asOfDate": "20261027", "count": 1, "authoritativeCount": 1,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": "005930", "returnBases": {}, "rollingHighCounts": {},
                                "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None}]}
        with tempfile.TemporaryDirectory() as directory:
            built, _ = evidence.build_evidence(
                snapshot, directory, baseline=baseline,
                today="20261028", publication_context={
                    "intradayTradingDateEvidence": {
                        "tradingDate": "20261028", "status": "VERIFIED",
                        "reason": "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT",
                        "evidenceClass": "INTRADAY_SESSION", "finalConfirmed": False,
                    }
                })
        result = built["latest-summary.json"]["fullMarket"]["tradingDateEvidence"]
        self.assertEqual(result["evidenceClass"], "INTRADAY_SESSION")
        self.assertFalse(result["finalConfirmed"])

    def test_source_observed_final_connects_without_claiming_local_date_verification(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-26T20:30:00+09:00",
                    "expectedCount": 1, "coverageCount": 1, "attemptCount": 1,
                    "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current()]}
        baseline = {"asOfDate": "20261026", "count": 1, "authoritativeCount": 1,
                    "coveragePct": 100.0, "historyStatus": "SOURCE_OBSERVED",
                    "sourceQualityStatus": "SOURCE_OBSERVED",
                    "stocks": [{"code": "005930", "returnBases": {}, "rollingHighCounts": {},
                                "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); final = root / "final"; final.mkdir()
            state = {"schemaVersion": 1, "format": "COLUMN_ARRAY_V1", "market": "KOSPI",
                     "publicationStatus": "FINAL", "sourceQualityStatus": "SOURCE_OBSERVED",
                     "asOfDate": "20261026",
                     "stocks": [{"code": "005930", "dates": ["20261026"],
                                 "close": [129], "high": [130], "volume": [200], "validity": [0],
                                 "source_valid": [True], "source_status": ["OBSERVED"]}]}
            (final / "kospi.json").write_text(json.dumps(state), encoding="utf-8")
            (final / "kosdaq.json").write_text(json.dumps({**state, "market": "KOSDAQ", "stocks": []}), encoding="utf-8")
            built, _ = evidence.build_evidence(
                snapshot, root / "canonical", baseline=baseline,
                publication_context={"baselineFreshnessStatus": "VALID_CURRENT_DAY_FINAL_SOURCE_OBSERVED"},
                today="20261026", monitoring_daily_dir=final,
                allow_source_observed_final=True,
            )
        full_market = built["latest-breadth.json"]["fullMarket"]
        self.assertEqual(full_market["tradingDate"], "20261026")
        self.assertEqual(full_market["sourceQualityStatus"], "SOURCE_OBSERVED")
        self.assertEqual(full_market["tradingDateEvidence"]["status"], "SOURCE_OBSERVED")
        self.assertEqual(full_market["tradingDateEvidence"]["reason"],
                         "SOURCE_OBSERVED_MONITORING_DAILY_FINAL_DATE")

    def test_source_observed_previous_baseline_is_not_promoted_to_next_day(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-27T09:07:00+09:00",
                    "expectedCount": 1, "coverageCount": 1, "attemptCount": 1,
                    "duplicateCodes": [], "missingCodes": [], "stocks": [current()]}
        baseline = {"asOfDate": "20261026", "count": 1, "authoritativeCount": 1,
                    "coveragePct": 100.0, "historyStatus": "SOURCE_OBSERVED",
                    "sourceQualityStatus": "SOURCE_OBSERVED",
                    "stocks": [{"code": "005930", "returnBases": {}, "rollingHighCounts": {},
                                "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            built, _ = evidence.build_evidence(
                snapshot, root / "canonical", baseline=baseline,
                publication_context={
                    "baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY_SOURCE_OBSERVED"
                },
                today="20261027", allow_source_observed_final=False,
            )
        full_market = built["latest-breadth.json"]["fullMarket"]
        self.assertIsNone(full_market["tradingDate"])
        self.assertEqual(full_market["tradingDateEvidence"]["reason"],
                         "INSUFFICIENT_TRADING_DATE_EVIDENCE")

    def test_rolling_final_is_used_only_as_named_full_market_date_source(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-15T20:00:00+09:00",
                    "expectedCount": 1, "coverageCount": 1, "attemptCount": 1,
                    "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current(localDate="20261015")]}
        baseline = {"asOfDate": "20261014", "count": 1, "authoritativeCount": 1,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": "005930", "returnBases": {}, "rollingHighCounts": {},
                                "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            final = root / "final"
            final.mkdir()
            state = {
                "schemaVersion": 1, "format": "COLUMN_ARRAY_V1", "market": "KOSPI",
                "publicationStatus": "FINAL", "asOfDate": "20261015",
                "stocks": [{"code": "005930", "dates": ["20261014", "20261015"],
                            "close": [128, 129], "high": [129, 130],
                            "volume": [100, 200], "validity": [0, 0]}],
            }
            (final / "kospi.json").write_text(json.dumps(state), encoding="utf-8")
            (final / "kosdaq.json").write_text(json.dumps({
                "schemaVersion": 1, "format": "COLUMN_ARRAY_V1", "market": "KOSDAQ",
                "publicationStatus": "FINAL", "asOfDate": "20261015", "stocks": [],
            }), encoding="utf-8")
            built, _ = evidence.build_evidence(
                snapshot, root / "canonical", baseline=baseline,
                publication_context={"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"},
                today="20261015", monitoring_daily_dir=final,
            )
        full_market = built["latest-breadth.json"]["fullMarket"]
        self.assertEqual(full_market["tradingDate"], "20261015")
        self.assertEqual(full_market["tradingDateEvidence"]["reason"],
                         "VERIFIED_MONITORING_DAILY_FINAL_SESSION")
        self.assertEqual(full_market["tradingDateEvidence"]["source"], "MONITORING_DAILY_FINAL")

    def test_rolling_final_cannot_bypass_partial_canonical_history(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-15T20:00:00+09:00",
                    "expectedCount": 2, "coverageCount": 2, "attemptCount": 1,
                    "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current(localDate="20261015"),
                               current("006400", "KOSDAQ", localDate="20261015")]}
        baseline = {"asOfDate": "20261014", "count": 2, "authoritativeCount": 2,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": code, "returnBases": {}, "rollingHighCounts": {},
                                "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None} for code in ("005930", "006400")]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); final = root / "final"; final.mkdir()
            (root / "005930.jsonl").write_text(json.dumps(history("20261015", 129)) + "\n", encoding="utf-8")
            state = {"schemaVersion": 1, "format": "COLUMN_ARRAY_V1", "market": "KOSPI",
                     "publicationStatus": "FINAL", "asOfDate": "20261015",
                     "stocks": [{"code": "005930", "dates": ["20261015"],
                                 "close": [129], "high": [130], "volume": [200], "validity": [0]}]}
            (final / "kospi.json").write_text(json.dumps(state), encoding="utf-8")
            (final / "kosdaq.json").write_text(json.dumps({**state, "market": "KOSDAQ", "stocks": []}), encoding="utf-8")
            built, _ = evidence.build_evidence(snapshot, root, baseline=baseline,
                publication_context={"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"},
                today="20261015", monitoring_daily_dir=final)
        result = built["latest-breadth.json"]["fullMarket"]["tradingDateEvidence"]
        self.assertIsNone(result["tradingDate"])
        self.assertEqual(result["reason"], "CANONICAL_COVERAGE_INCOMPLETE")

    def test_rolling_final_cannot_bypass_damaged_canonical_history(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-15T20:00:00+09:00",
                    "expectedCount": 2, "coverageCount": 2, "attemptCount": 1,
                    "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current(localDate="20261015"),
                               current("006400", "KOSDAQ", localDate="20261015")]}
        baseline = {"asOfDate": "20261014", "count": 2, "authoritativeCount": 2,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": code, "returnBases": {}, "rollingHighCounts": {},
                                "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None} for code in ("005930", "006400")]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); final = root / "final"; final.mkdir()
            (root / "005930.jsonl").write_text(json.dumps(history("20261015", 129)) + "\n", encoding="utf-8")
            (root / "006400.jsonl").write_text("not-json\n", encoding="utf-8")
            for market, stocks in (("KOSPI", [{"code": "005930", "dates": ["20261015"], "close": [129], "high": [130], "volume": [200], "validity": [0]}]), ("KOSDAQ", [])):
                (final / f"{market.lower()}.json").write_text(json.dumps({"format": "COLUMN_ARRAY_V1", "market": market, "publicationStatus": "FINAL", "asOfDate": "20261015", "stocks": stocks}), encoding="utf-8")
            built, _ = evidence.build_evidence(snapshot, root, baseline=baseline,
                publication_context={"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"},
                today="20261015", monitoring_daily_dir=final)
        result = built["latest-breadth.json"]["fullMarket"]["tradingDateEvidence"]
        self.assertIsNone(result["tradingDate"])
        self.assertEqual(result["reason"], "CANONICAL_COVERAGE_INCOMPLETE")

    def test_rolling_final_cannot_bypass_mixed_canonical_history(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-15T20:00:00+09:00",
                    "expectedCount": 2, "coverageCount": 2, "attemptCount": 1,
                    "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current(localDate="20261015"),
                               current("006400", "KOSDAQ", localDate="20261015")]}
        baseline = {"asOfDate": "20261014", "count": 2, "authoritativeCount": 2,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": code, "returnBases": {}, "rollingHighCounts": {},
                                "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None} for code in ("005930", "006400")]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); final = root / "final"; final.mkdir()
            for code, day in (("005930", "20261015"), ("006400", "20261014")):
                (root / f"{code}.jsonl").write_text(json.dumps(history(day, 129)) + "\n", encoding="utf-8")
            for market, stocks in (("KOSPI", [{"code": "005930", "dates": ["20261015"], "close": [129], "high": [130], "volume": [200], "validity": [0]}]), ("KOSDAQ", [])):
                (final / f"{market.lower()}.json").write_text(json.dumps({"format": "COLUMN_ARRAY_V1", "market": market, "publicationStatus": "FINAL", "asOfDate": "20261015", "stocks": stocks}), encoding="utf-8")
            built, _ = evidence.build_evidence(snapshot, root, baseline=baseline,
                publication_context={"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"},
                today="20261015", monitoring_daily_dir=final)
        result = built["latest-breadth.json"]["fullMarket"]["tradingDateEvidence"]
        self.assertIsNone(result["tradingDate"])
        self.assertEqual(result["reason"], "MIXED_CANONICAL_TRADING_DATES")

    def test_mixed_full_market_date_evidence_is_not_verified(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-15T20:00:00+09:00",
                    "expectedCount": 2, "coverageCount": 2, "attemptCount": 1,
                    "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current(localDate="20261015"), current("006400", "KOSDAQ", localDate="20261016")]}
        baseline = {"asOfDate": "20261014", "count": 2, "authoritativeCount": 2,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": code, "returnBases": {}, "rollingHighCounts": {},
                                "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None} for code in ("005930", "006400")]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for code, date in (("005930", "20261015"), ("006400", "20261016")):
                (root / f"{code}.jsonl").write_text(json.dumps(history(date, 129)) + "\n",
                                                       encoding="utf-8")
            built, _ = evidence.build_evidence(
                snapshot, root, baseline=baseline,
                publication_context={"baselineFreshnessStatus": "VALID_PREVIOUS_TRADING_DAY"},
                today="20261015",
            )
        full_market = built["latest-breadth.json"]["fullMarket"]
        self.assertIsNone(full_market["tradingDate"])
        self.assertEqual(full_market["tradingDateEvidence"]["reason"],
                         "INSUFFICIENT_TRADING_DATE_EVIDENCE")

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
                    "count": 2763, "coverageCount": 2763, "attemptCount": 2,
                    "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current("005930")]}
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

    def test_stale_baseline_keeps_quote_and_quarantines_derived_metrics(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-29T09:07:00+09:00",
                    "expectedCount": 1, "coverageCount": 1, "attemptCount": 1,
                    "duplicateCodes": [], "missingCodes": [],
                    "stocks": [current("005930", localDate="20261029", currentPrice=130,
                                        change=2, changeRate=1.56)]}
        baseline = {"asOfDate": "20261027", "count": 1, "authoritativeCount": 1,
                    "coveragePct": 100.0, "historyStatus": "OK",
                    "stocks": [{"code": "005930", "lastClose": 128,
                                "returnBases": {"1": 128, "5": 120, "20": 100},
                                "rollingHighCounts": {}, "rollingHighs": {},
                                "volumeState20": {}, "previousMa20": None,
                                "previousMa60": None}]}
        industries = {"status": "OK", "industries": [{"id": "1", "name": "테스트"}]}
        membership = {"status": "OK", "industries": [{"id": "1", "name": "테스트",
                                                        "status": "OK", "members": [{"code": "005930"}]}]}
        with tempfile.TemporaryDirectory() as directory:
            built, features = evidence.build_evidence(
                snapshot, Path(directory), baseline=baseline, industries=industries,
                membership=membership, theme_membership={"status": "UNUSABLE", "themes": []},
                publication_context={"baselineFreshnessStatus": "BASELINE_STALE",
                                     "baselineAsOfDate": "20261027",
                                     "expectedPreviousTradingDate": "20261028"},
                today="20261029",
            )
        feature = features[0]
        self.assertEqual(feature["currentPrice"], 130.0)
        self.assertEqual(feature["changeRate"], 1.56)
        self.assertEqual(feature["baselineFreshnessStatus"], "BASELINE_STALE")
        self.assertFalse(feature["derivedMetricsUsable"])
        self.assertIsNone(feature["ret_1d"])
        self.assertIsNone(feature["ma20"])
        self.assertIsNone(feature["breakout_20_intraday"])
        self.assertIsNone(feature["current_volume_ratio_20"])
        quality = built["latest-quality.json"]
        self.assertEqual(quality["baselineFreshnessStatus"], "BASELINE_STALE")
        self.assertEqual(quality["derivedMetricsStatus"], "UNAVAILABLE")
        industry = built["latest-industries.json"]["industries"][0]
        self.assertIsNone(industry["avgReturn1D"])
        self.assertEqual(industry["officialChangeRateRelativeStrength"], 0.0)
        self.assertEqual(industry["derivedMetricsStatus"], "UNAVAILABLE")

    def test_incomplete_previous_bar_does_not_become_one_day_return(self):
        snapshot = {"status": "SUCCESS", "generatedAt": "2026-10-29T09:07:00+09:00",
                    "expectedCount": 3, "coverageCount": 3, "attemptCount": 1,
                    "duplicateCodes": [], "missingCodes": [],
                    "stocks": [
                        current("032685", price=307, localDate="20261029"),
                        current("000300", price=4300, localDate="20261029"),
                        current("005930", price=110, localDate="20261029"),
                    ]}
        valid = {"closeValid": True, "ohlcValid": True, "volumeValid": True}
        invalid = {"closeValid": False, "ohlcValid": False, "volumeValid": False}
        baseline = {"asOfDate": "20261028", "count": 3, "authoritativeCount": 3,
                    "coveragePct": 100.0, "historyStatus": "SOURCE_OBSERVED",
                    "sourceQualityStatus": "SOURCE_OBSERVED", "stocks": [
                        {"code": "032685", "lastTradingDate": "20261027", "lastClose": 297,
                         **valid, "returnBases": {"1": 297}, "rollingHighCounts": {},
                         "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                         "previousMa60": None},
                        {"code": "000300", "lastTradingDate": "20261028", "lastClose": 4200,
                         **invalid, "returnBases": {"1": 4200}, "rollingHighCounts": {},
                         "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                         "previousMa60": None},
                        {"code": "005930", "lastTradingDate": "20261028", "lastClose": 100,
                         **valid, "returnBases": {"1": 100}, "rollingHighCounts": {},
                         "rollingHighs": {}, "volumeState20": {}, "previousMa20": None,
                         "previousMa60": None},
                    ]}
        industries = {"status": "OK", "industries": [{"id": "1", "name": "테스트"}]}
        membership = {"status": "OK", "industries": [{"id": "1", "name": "테스트",
                                                        "status": "OK", "members": [
                                                            {"code": "032685"},
                                                            {"code": "000300"},
                                                            {"code": "005930"},
                                                        ]}]}
        with tempfile.TemporaryDirectory() as directory:
            _, features = evidence.build_evidence(
                snapshot, Path(directory), baseline=baseline, industries=industries,
                membership=membership, theme_membership={"status": "UNUSABLE", "themes": []},
                publication_context={"baselineFreshnessStatus":
                                     "VALID_PREVIOUS_TRADING_DAY_SOURCE_OBSERVED"},
                today="20261029",
            )
        by_code = {row["code"]: row for row in features}
        for code in ("032685", "000300"):
            self.assertEqual(by_code[code]["currentPrice"],
                             307.0 if code == "032685" else 4300.0)
            self.assertFalse(by_code[code]["derivedMetricsUsable"])
            self.assertEqual(by_code[code]["derivedMetricsReason"],
                             "BASELINE_DAILY_BAR_INCOMPLETE")
            self.assertIsNone(by_code[code]["ret_1d"])
        self.assertEqual(by_code["032685"]["baselineDailyBarReason"],
                         "LATEST_VALID_BAR_DATE_MISMATCH")
        self.assertEqual(by_code["000300"]["baselineDailyBarReason"],
                         "LATEST_BAR_INVALID")
        self.assertTrue(by_code["005930"]["derivedMetricsUsable"])
        self.assertEqual(by_code["005930"]["ret_1d"], 10.0)

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
                    "qualityStatus": "WARNING",
                    "qualityWarnings": ["UNIVERSE_COMPOSITION_CHANGED"],
                    "universeDelta": {"added": ["005930"], "verificationStatus": "PENDING_EXTERNAL_VERIFICATION"},
                    "stocks": [current()]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "005930.jsonl").write_text(json.dumps(history("20260930", 100)) + "\n", encoding="utf-8")
            built, _ = evidence.build_evidence(snapshot, root, None, None, None, "20261001")
        self.assertEqual(built["latest-quality.json"]["collectionQualityStatus"], "COMPLETE_RECONCILIATION_PENDING")
        self.assertEqual(built["latest-summary.json"]["fullMarket"]["qualityStatus"], "WARNING")
        self.assertEqual(
            built["latest-summary.json"]["fullMarket"]["qualityWarnings"],
            ["UNIVERSE_COMPOSITION_CHANGED"],
        )

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
        self.assertEqual(leaders[0]["technicalEvidence"]["status"], "UNAVAILABLE")
        self.assertEqual(leaders[0]["technicalEvidence"]["reason"], "TRADING_DATE_EVIDENCE_UNAVAILABLE")
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
                    "missingCodes": [], "duplicateCodes": [], "pageMissingCodes": [],
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
            self.assertEqual(built["latest-quality.json"]["publicationStatus"],
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
# missingCodes fixture follows expected minus current collector contract.


