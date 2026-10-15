import unittest

from tools.dynamic_universe_v2_history import append_observation, evaluate_horizons, make_observation, unique_trading_dates


def result(generated, score=75, eligible=True):
    row = {"id": "1", "kind": "industry", "v2Status": "WATCH", "score": score,
           "watchEligible": eligible, "actual": {"avgReturn1D": 1.0}, "criteria": {"scoreMin": 70}}
    return {"schemaVersion": "dynamic-universe-v2-shadow-2", "generatedAt": generated,
            "source": {"industries": generated}, "industries": [row], "themes": []}


class DynamicUniverseV2HistoryTests(unittest.TestCase):
    def test_same_day_repeated_observations_are_retained_but_one_trading_day(self):
        history = {}
        history = append_observation(history, make_observation(result("a"), "2026-10-15", "2026-10-15T01:00:00Z"))
        history = append_observation(history, make_observation(result("b"), "2026-10-15", "2026-10-15T02:00:00Z"))
        history = append_observation(history, make_observation(result("a"), "2026-10-15", "2026-10-15T03:00:00Z"))
        self.assertEqual(len(history["observations"]), 2)
        self.assertEqual(unique_trading_dates(history), ["2026-10-15"])
        self.assertEqual(evaluate_horizons(history), [])

    def test_one_three_five_horizon_requires_distinct_trading_dates(self):
        history = {}
        for i, day in enumerate(("2026-10-15", "2026-10-16", "2026-10-17", "2026-10-20", "2026-10-21", "2026-10-22")):
            history = append_observation(history, make_observation(result(str(i)), day, f"2026-10-{15+i:02d}T01:00:00Z"))
        evaluations = evaluate_horizons(history)
        self.assertTrue(any(item["horizonTradingDays"] == 1 for item in evaluations))
        self.assertTrue(any(item["horizonTradingDays"] == 3 for item in evaluations))
        self.assertTrue(any(item["horizonTradingDays"] == 5 for item in evaluations))

    def test_missing_future_or_ineligible_base_is_not_estimated(self):
        history = {}
        history = append_observation(history, make_observation(result("a", eligible=False), "2026-10-15", "2026-10-15T01:00:00Z"))
        history = append_observation(history, make_observation(result("b"), "2026-10-16", "2026-10-16T01:00:00Z"))
        self.assertEqual(evaluate_horizons(history)[0]["status"], "NO_ELIGIBLE_BASE_ITEMS")


if __name__ == "__main__":
    unittest.main()
