import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = load("history_adjustment_guard")
evidence = load("full_market_monitoring_evidence")
baseline = load("daily_monitoring_baseline")
v2 = load("dynamic_universe_v2_shadow")
CASES = json.loads((ROOT / "tests" / "fixtures" / "corporate_action_price_cases.json").read_text())


def history(date, close, volume=100):
    return {"date": date, "open": close - 1, "high": close + 1,
            "low": close - 2, "close": close, "volume": volume,
            "feature_valid": True}


def current(case, market="KOSPI", name=None):
    return {
        "code": case["code"], "name": name or case["code"], "market": market,
        "currentPrice": case["currentPrice"], "change": case.get("change"),
        "changeRate": case["changeRate"], "openPrice": case["currentPrice"] - 1,
        "highPrice": case["currentPrice"] + 2, "lowPrice": case["currentPrice"] - 3,
        "accumulatedTradingVolume": 200, "accumulatedTradingValue": 1000,
        "high52Week": case["currentPrice"] + 3,
    }


class HistoryAdjustmentGuardTests(unittest.TestCase):
    def test_es_energy_is_quarantined_without_confirming_corporate_action(self):
        case = CASES["es_energy"]
        assessment = guard.detect(case, case["previousClose"])
        self.assertEqual(assessment["status"], guard.STATUS_UNVERIFIED)
        self.assertEqual(assessment["reason"], "PRICE_SCALE_MISMATCH")
        self.assertEqual(assessment["scaleRatio"], 0.1)
        self.assertFalse(assessment["corporateActionConfirmed"])

        row = {"currentPrice": 5290, "changeRate": -9.26, "ret_1d": 807.375643,
               "ma20": 600, "breakout_20_intraday": True,
               "current_volume_ratio_20": 3.0}
        guarded = guard.apply_to_feature(row, assessment)
        self.assertEqual(guarded["currentPrice"], 5290)
        self.assertEqual(guarded["changeRate"], -9.26)
        self.assertIsNone(guarded["ret_1d"])
        self.assertIsNone(guarded["ma20"])
        self.assertIsNone(guarded["breakout_20_intraday"])
        self.assertIsNone(guarded["current_volume_ratio_20"])

    def test_sk_hynix_and_normal_stock_keep_existing_features(self):
        for name in ("sk_hynix", "normal"):
            case = CASES[name]
            assessment = guard.detect(case, case["previousClose"])
            self.assertEqual(assessment["status"], guard.STATUS_NOT_DETECTED)
            self.assertTrue(assessment["derivedMetricsUsable"])

    def test_missing_official_reference_is_unverified(self):
        case = CASES["unverified_reference"]
        assessment = guard.detect(case, case["previousClose"])
        self.assertEqual(assessment["status"], guard.STATUS_UNVERIFIED)
        self.assertEqual(assessment["reason"], "OFFICIAL_REFERENCE_PRICE_UNAVAILABLE")
        self.assertFalse(assessment["derivedMetricsUsable"])

    def test_feature_row_masks_es_energy_and_preserves_quote(self):
        case = CASES["es_energy"]
        feature = evidence.feature_row(
            current(case, "KOSDAQ", "에스에너지"),
            [history(f"202610{day:02d}", 583) for day in range(1, 22)],
            "20261028",
        )
        self.assertEqual(feature["historyAdjustmentStatus"], guard.STATUS_UNVERIFIED)
        self.assertEqual(feature["currentPrice"], 5290.0)
        self.assertEqual(feature["changeRate"], -9.26)
        self.assertIsNone(feature["ret_1d"])
        self.assertIsNone(feature["ma20"])

    def test_compact_baseline_path_applies_the_same_guard(self):
        case = CASES["es_energy"]
        rows = [history(f"202610{day:02d}", 583) for day in range(1, 22)]
        stock = baseline.baseline_stock({"code": case["code"], "name": "에스에너지",
                                         "market": "KOSDAQ"}, rows)
        feature = baseline.feature_row_from_baseline(
            current(case, "KOSDAQ", "에스에너지"), stock, "20261028"
        )
        self.assertEqual(feature["historyAdjustmentStatus"], guard.STATUS_UNVERIFIED)
        self.assertFalse(feature["derivedMetricsUsable"])
        self.assertIsNone(feature["ret_1d"])

    def test_industry_and_theme_a_b_exclude_quarantined_return_but_keep_official_rate(self):
        es = evidence.feature_row(current(CASES["es_energy"], "KOSDAQ", "에스에너지"),
                                  [history(f"202610{day:02d}", 583) for day in range(1, 22)], "20261028")
        normal = evidence.feature_row(current(CASES["normal"], "KOSDAQ", "정상종목"),
                                      [history(f"202610{day:02d}", 128) for day in range(1, 22)], "20261028")
        features = [es, normal]
        industries = {"status": "OK", "industries": [{"id": "1", "name": "테스트업종"}]}
        membership = {"status": "OK", "industries": [{"id": "1", "name": "테스트업종",
                                                        "status": "OK", "members": [
                                                            {"code": es["code"]}, {"code": normal["code"]}]}]}
        _, rows = evidence.aggregate_industries(features, industries, membership)
        row = rows[0]
        self.assertEqual(row["historyAdjustmentUnverifiedCount"], 1)
        self.assertEqual(row["performanceComparison"]["existingCanonicalReturn"]["eligibleCount"], 1)
        self.assertEqual(row["performanceComparison"]["officialChangeRate"]["eligibleCount"], 2)
        self.assertEqual([leader["code"] for leader in row["leaders"]], [normal["code"]])

        themes = {"status": "OK", "themes": [{"id": "1", "name": "테스트테마",
                                                 "status": "OK", "members": [
                                                     {"code": es["code"]}, {"code": normal["code"]}]}]}
        payload = evidence.build_theme_signals("2026-10-28T10:00:00+09:00", features, themes)
        metrics = payload["themes"][0]["metrics"]
        self.assertEqual(metrics["historyAdjustmentUnverifiedCount"], 1)
        self.assertEqual(metrics["performanceComparison"]["existingCanonicalReturn"]["eligibleCount"], 1)
        self.assertEqual(metrics["performanceComparison"]["officialChangeRate"]["eligibleCount"], 2)

    def test_v2_separates_zero_relative_strength_and_filters_quarantine(self):
        result = v2._evaluate({
            "id": "theme-1", "name": "테스트", "memberCount": 2,
            "metrics": {"relativeStrengthVsMarket": 0, "avgReturn1D": 99,
                        "medianReturn1D": 99, "advancerPct": 50,
                        "pctAboveMA20": 50,
                        "tradingValue": {"coveragePct": 100},
                        "persistence": {"availableTradingDays": 5}},
            "stocks": [{"changeRate": 9, "ret_1d": 9, "derivedMetricsUsable": True,
                        "above_ma20": True, "accumulatedTradingValue": 100},
                       {"changeRate": 807, "ret_1d": None, "derivedMetricsUsable": False,
                        "above_ma20": None, "accumulatedTradingValue": 1000}]}, "theme")
        self.assertEqual(result["priceRelativeStrengthBasis"], "RELATIVE_STRENGTH_VS_MARKET")
        self.assertEqual(result["components"]["priceRelativeStrength"], 50.0)
        self.assertEqual(result["relativeStrengthVsMarket"], 0.0)


if __name__ == "__main__":
    unittest.main()
