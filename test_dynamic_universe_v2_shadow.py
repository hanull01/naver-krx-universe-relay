import unittest

from tools.dynamic_universe_v2_shadow import evaluate_snapshot, summarize, _evaluate, _trading_value_summary


class DynamicUniverseV2ShadowTests(unittest.TestCase):
    def test_missing_data_is_wait_not_zero(self):
        result = evaluate_snapshot(
            {"generatedAt": "t", "industries": [{"id": "1", "name": "x", "memberCount": 2}]},
            {"generatedAt": "t", "themes": []}, {"sectors": [], "themes": []})
        row = result["industries"][0]
        self.assertEqual(row["v2Status"], "WAIT")
        self.assertEqual(row["dataQuality"], "UNAVAILABLE")
        self.assertIsNone(row["score"])

    def test_small_group_is_not_automatically_active(self):
        row = {"id": "1", "name": "x", "memberCount": 2, "featureValidMemberCount": 2,
               "advancers": 2, "decliners": 0, "avgReturn1D": 3, "medianReturn1D": 3,
               "advancePct": 100, "relativeStrengthVsMarket": 5, "pctAboveMA20": 100,
               "tradingValue": {"coveragePct": 100}, "persistence": {"availableTradingDays": 5},
               "leaders": [{"code": "1"}]}
        result = evaluate_snapshot({"industries": [row]}, {"themes": []}, {"sectors": [], "themes": []})
        self.assertEqual(result["industries"][0]["smallGroupCorrection"], 0.75)
        self.assertNotEqual(result["industries"][0]["v2Status"], "ACTIVE")

    def test_relative_defense_is_separate_from_absolute_strength(self):
        row = {"id": "1", "name": "x", "memberCount": 10, "featureValidMemberCount": 10,
               "avgReturn1D": -1, "medianReturn1D": -1, "advancePct": 20,
               "relativeStrengthVsMarket": 4, "pctAboveMA20": 20,
               "tradingValue": {"coveragePct": 100}, "persistence": {"availableTradingDays": 5},
               "leaders": [{"code": "1"}, {"code": "2"}]}
        result = evaluate_snapshot({"industries": [row]}, {"themes": []}, {"sectors": [], "themes": []})
        got = result["industries"][0]
        self.assertEqual(got["v2Status"], "DEFENSIVE")
        self.assertFalse(got["absoluteStrength"])
        self.assertTrue(got["relativeDefense"])

    def test_current_weakness_without_prior_is_weak_not_weakening(self):
        row = {"id": "1", "name": "x", "memberCount": 10, "featureValidMemberCount": 10,
               "avgReturn1D": -1, "medianReturn1D": -1, "advancePct": 20,
               "relativeStrengthVsMarket": -1, "pctAboveMA20": 20,
               "tradingValue": {"coveragePct": 100}, "persistence": {"availableTradingDays": 5},
               "leaders": [{"code": "1"}, {"code": "2"}]}
        got = evaluate_snapshot({"industries": [row]}, {"themes": []}, {"sectors": [], "themes": []})["industries"][0]
        self.assertEqual(got["v2Status"], "WEAK")
        self.assertEqual(got["trendEvidence"], "NO_COMPARABLE_PRIOR")

    def test_weakening_requires_prior_comparison(self):
        row = {"id": "1", "name": "x", "memberCount": 10, "featureValidMemberCount": 10,
               "avgReturn1D": -2, "medianReturn1D": -1, "advancePct": 20,
               "relativeStrengthVsMarket": -1, "pctAboveMA20": 20,
               "tradingValue": {"coveragePct": 100}, "persistence": {"availableTradingDays": 5},
               "leaders": [{"code": "1"}], "previous": {"avgReturn1D": 1}}
        got = evaluate_snapshot({"industries": [row]}, {"themes": []}, {"sectors": [], "themes": []})["industries"][0]
        self.assertEqual(got["v2Status"], "WEAKENING")
        self.assertTrue(got["watchEligible"] is False)

    def test_counts_and_v1_comparison(self):
        result = evaluate_snapshot({"industries": [{"id": "1", "memberCount": 5}]},
                                   {"themes": [{"id": "2", "membershipStatus": "OK", "memberCount": 5}]},
                                   {"sectors": [{"id": "1", "status": "ACTIVE"}], "themes": [{"id": "2", "status": "WATCH"}]})
        summary = summarize(result)
        self.assertEqual(summary["industries"]["count"], 1)
        self.assertEqual(summary["themes"]["count"], 1)
        self.assertEqual(result["industries"][0]["v1Status"], "ACTIVE")
        self.assertEqual(result["themes"][0]["v1Status"], "WATCH")

    def test_zero_trading_value_total_does_not_divide_by_zero(self):
        row = {"id": "theme-1", "name": "테스트", "memberCount": 2, "metrics": {
            "relativeStrengthVsMarket": 1, "persistence": {"availableTradingDays": 5}},
            "stocks": [{"changeRate": 1, "above_ma20": True, "accumulatedTradingValue": 0},
                       {"changeRate": 1, "above_ma20": True, "accumulatedTradingValue": 0}]}
        result = _evaluate(row, "theme")
        self.assertEqual(result["components"]["turnoverParticipation"], 100.0)
        summary = _trading_value_summary([0.0, 0.0], 2)
        self.assertEqual(summary["total"], 0.0)
        self.assertIsNone(summary["top3ConcentrationPct"])

    def test_positive_trading_value_total_keeps_concentration_calculation(self):
        row = {"id": "theme-1", "name": "테스트", "memberCount": 2, "metrics": {
            "relativeStrengthVsMarket": 1, "persistence": {"availableTradingDays": 5}},
            "stocks": [{"changeRate": 1, "above_ma20": True, "accumulatedTradingValue": 100},
                       {"changeRate": 1, "above_ma20": True, "accumulatedTradingValue": 200}]}
        result = _evaluate(row, "theme")
        self.assertEqual(result["components"]["turnoverParticipation"], 100.0)
        summary = _trading_value_summary([100.0, 200.0], 2)
        self.assertEqual(summary["total"], 300.0)
        self.assertEqual(summary["top3ConcentrationPct"], 100.0)


if __name__ == "__main__":
    unittest.main()
