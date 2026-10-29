import unittest

from tools.dynamic_universe_v2_history import append_observation, evaluate_horizons, make_observation, unique_trading_dates
from tools.v2_change_analysis import compare_v2_changes


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

    def test_source_date_mismatch_is_excluded_from_horizons(self):
        history = {}
        bad = make_observation(result("2026-10-16T12:00:00+00:00"), "2026-10-15")
        good = make_observation(result("2026-10-17T12:00:00+00:00"), "2026-10-17")
        history = append_observation(history, bad)
        history = append_observation(history, good)
        self.assertEqual(bad["dateQuality"], "MISMATCH")
        self.assertEqual(unique_trading_dates(history), ["2026-10-17"])
        self.assertEqual(evaluate_horizons(history), [])

    def test_provenance_hash_becomes_history_snapshot_identity(self):
        provenance = {"inputSnapshotSha256": "a" * 64, "status": "PARTIAL"}
        observation = make_observation(result("2026-10-15T12:00:00+00:00"), "2026-10-15",
                                       source_provenance=provenance)
        self.assertEqual(observation["sourceSnapshotHash"], "a" * 64)
        self.assertEqual(observation["sourceSnapshotHashType"], "INPUT_FILES_SHA256")
        self.assertEqual(observation["sourceProvenance"]["status"], "PARTIAL")

    def test_source_change_same_day_is_distinct_but_not_a_second_trading_day(self):
        first = make_observation(result("same"), "2026-10-15", source_provenance={"inputSnapshotSha256": "a" * 64})
        second = make_observation(result("same"), "2026-10-15", source_provenance={"inputSnapshotSha256": "b" * 64})
        history = append_observation({}, first)
        history = append_observation(history, second)
        self.assertEqual(len(history["observations"]), 2)
        self.assertEqual(unique_trading_dates(history), ["2026-10-15"])

    def test_v2_change_keys_include_kind_and_id_and_same_day_uses_latest_observation(self):
        history = {"observations": [
            {"tradingDate": "2026-10-28", "observedAt": "2026-10-28T01:00:00Z", "sourceSnapshotHash": "a",
             "sourceProvenance": {"sourceCommit": "c"},
             "industries": [{"id": "1", "name": "업종", "v2Status": "WATCH", "score": 70}],
             "themes": [{"id": "1", "name": "테마", "v2Status": "WATCH", "score": 60}]},
            {"tradingDate": "2026-10-28", "observedAt": "2026-10-28T02:00:00Z", "sourceSnapshotHash": "b",
             "sourceProvenance": {"sourceCommit": "c"},
             "industries": [{"id": "1", "name": "업종", "v2Status": "WATCH", "score": 72}],
             "themes": [{"id": "1", "name": "테마", "v2Status": "WATCH", "score": 60}]},
            {"tradingDate": "2026-10-29", "observedAt": "2026-10-29T01:00:00Z", "sourceSnapshotHash": "d",
             "sourceProvenance": {"sourceCommit": "c"},
             "industries": [{"id": "1", "name": "업종", "v2Status": "ACTIVE", "score": 80}],
             "themes": [{"id": "1", "name": "테마", "v2Status": "WATCH", "score": 55}]},
        ]}
        changes = compare_v2_changes(history)
        self.assertEqual({(row["kind"], row["id"]) for row in changes}, {("industry", "1"), ("theme", "1")})
        industry = next(row for row in changes if row["kind"] == "industry")
        self.assertEqual(industry["baseTradingDate"], "2026-10-28")
        self.assertEqual(industry["officialChangeClass"], "STATUS_CHANGED")

    def test_v2_missing_source_commit_blocks_official_transition(self):
        history = {"observations": [
            {"tradingDate": "2026-10-28", "observedAt": "a", "industries": [{"id": "1", "v2Status": "WATCH", "score": 70}], "themes": []},
            {"tradingDate": "2026-10-29", "observedAt": "b", "industries": [{"id": "1", "v2Status": "ACTIVE", "score": 80}], "themes": []},
        ]}
        row = compare_v2_changes(history)[0]
        self.assertEqual(row["status"], "UNVERIFIED_SOURCE")
        self.assertEqual(row["officialChangeClass"], "COMPARISON_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
