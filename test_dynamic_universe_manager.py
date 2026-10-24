import copy
import tempfile
import unittest
from pathlib import Path

import dynamic_universe_manager as manager
import universe_manager


POLICY = {
    "activationConfirmations": 2, "removalConfirmations": 2,
    "watchConfirmations": 1, "leaderLimitPerGroup": 3, "collectionLimit": 120,
    "sector": {"minimumScore": 60, "minimumRelativeStrength": 0,
               "minimumAdvancerPct": 50, "minimumTurnoverConcentrationPct": 0,
               "minimumPctAboveMA20": 50, "minimumLeaderCount": 1},
    "theme": {"minimumScore": 60, "requireVerifiedMembership": True},
    "leader": {"minimumScore": 60, "minimumPersistence": 1},
}


def sector(name="반도체", score=80, leaders=None):
    return {"name": name, "score": score, "relativeStrength": 1,
            "advancerPct": 70, "turnoverConcentrationPct": 10,
            "pctAboveMA20": 70, "leaderCount": len(leaders or [{"code": "005930", "score": 80}]),
            "leaders": leaders or [{"code": "005930", "score": 80}]}


class DynamicUniverseManagerTests(unittest.TestCase):
    def context(self, sectors=None, themes=None):
        return {"generatedAt": "t", "industries": {"status": "AVAILABLE", "industries": sectors or []},
                "themes": themes or {"status": "UNAVAILABLE", "reason": "RESEARCH_GATE"}}

    def universe(self):
        return {"stocks": [{"itemCode": "005930", "stockName": "삼성전자", "enabled": True},
                           {"itemCode": "000660", "stockName": "SK하이닉스", "enabled": True}],
                "sectors": {}, "themes": {},
                "watchlists": {"legacy33": ["005930"], "관심종목": ["005930", "000660"]},
                "leaders": {"sector": {}, "theme": {}, "watchlist": {}}}

    def test_sector_requires_persistence_then_activates(self):
        first = manager.build_management_result(self._dated_context([sector()], "20261023"), self.universe(), policy=POLICY)
        self.assertEqual(first["sectors"][0]["status"], "DISCOVERED")
        second = manager.build_management_result(self._dated_context([sector()], "20261024"), self.universe(), previous=first, policy=POLICY)
        self.assertEqual(second["sectors"][0]["status"], "ACTIVE")
        self.assertEqual(second["leaders"][0]["status"], "DISCOVERED")
        third = manager.build_management_result(self._dated_context([sector()], "20261025"), self.universe(), previous=second, policy=POLICY)
        self.assertEqual(third["leaders"][0]["status"], "ACTIVE")

    def test_weakening_moves_watch_then_removed(self):
        active = manager.build_management_result(self.context([sector()]), self.universe(), policy=POLICY)
        active = manager.build_management_result(self.context([sector()]), self.universe(), previous=active, policy=POLICY)
        weak = sector(score=10); weak["advancerPct"] = 0
        watch = manager.build_management_result(self._dated_context([weak], "20261024"), self.universe(), previous=active, policy=POLICY)
        self.assertEqual(watch["sectors"][0]["status"], "WATCH")
        removed = manager.build_management_result(self._dated_context([weak], "20261025"), self.universe(), previous=watch, policy=POLICY)
        self.assertEqual(removed["sectors"][0]["status"], "REMOVED")

    def test_verified_theme_and_missing_theme_handling(self):
        unavailable = manager.build_management_result(self.context([sector()]), self.universe(), policy=POLICY)
        self.assertEqual(unavailable["pending"][0]["status"], "PENDING")
        themes = {"status": "AVAILABLE", "periods": {"daily": {"themes": [
            {"id": "T1", "name": "AI", "score": 90, "verifiedMembership": True,
             "systemLeaders": [{"code": "000660", "score": 90}]},
            {"id": "T2", "name": "미검증", "score": 90, "systemLeaders": []},
        ]}}}
        result = manager.build_management_result(self.context([], themes), self.universe(), policy=POLICY)
        self.assertEqual([row["status"] for row in result["themes"]], ["DISCOVERED", "REMOVED"])

    def test_deduplicates_collection_but_preserves_memberships(self):
        discovery = {"status": "AVAILABLE", "source": "KIWOOM_CONDITION_SEARCH",
                     "source_group": "조건A", "symbols": ["005930"]}
        result = manager.build_management_result(self.context([sector()]), self.universe(), discovery, policy=POLICY)
        row = next(row for row in result["memberships"] if row["code"] == "005930")
        self.assertEqual(result["collectionCodes"].count("005930"), 1)
        self.assertIn("KIWOOM_DISCOVERY", row["sources"])
        self.assertIn("USER_WATCHLIST", row["sources"])

    def test_automatic_run_cannot_mutate_user_watchlist(self):
        universe = self.universe(); before = copy.deepcopy(universe["watchlists"])
        manager.build_management_result(self.context([sector()]), universe, policy=POLICY)
        self.assertEqual(universe["watchlists"], before)

    def test_manual_watchlist_add_remove_and_legacy_guard(self):
        universe = self.universe()
        universe_manager.mutate_user_watchlist(universe, "add", "관심종목", "123456", "테스트")
        self.assertIn("123456", universe["watchlists"]["관심종목"])
        universe_manager.mutate_user_watchlist(universe, "remove", "관심종목", "123456")
        self.assertNotIn("123456", universe["watchlists"]["관심종목"])
        with self.assertRaises(ValueError):
            universe_manager.mutate_user_watchlist(universe, "remove", "legacy33", "005930")

    def test_atomic_publish_and_restore_source_state(self):
        result = manager.build_management_result(self.context([sector()]), self.universe(), policy=POLICY)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"; state = Path(directory) / "state.json"; history = Path(directory) / "history.json"
            manager.publish_result(result, output, state, history)
            self.assertEqual(manager.load_json(output)["version"], "dynamic-universe-management-v1")
            self.assertEqual(manager.load_json(state), manager.load_json(output))
            self.assertTrue(manager.load_json(history)["events"])

    def test_load_limit_never_truncates_user_or_discovery(self):
        policy = {**POLICY, "collectionLimit": 1}
        discovery = {"status": "AVAILABLE", "symbols": ["777777"]}
        result = manager.build_management_result(self.context([sector()]), self.universe(), discovery, policy=policy)
        self.assertIn("005930", result["collectionCodes"])
        self.assertIn("000660", result["collectionCodes"])
        self.assertIn("777777", result["collectionCodes"])
        self.assertEqual(result["load"]["limitExceededByProtectedSources"], 2)

    def _dated_context(self, sectors, trading_date):
        context = self.context(sectors)
        context["tradingDate"] = trading_date
        return context

    def test_same_trading_date_repeated_does_not_increment_streak(self):
        first = manager.build_management_result(self._dated_context([sector()], "20261023"), self.universe(), policy=POLICY)
        repeated = first
        for _ in range(10):
            repeated = manager.build_management_result(self._dated_context([sector()], "20261023"), self.universe(), previous=repeated, policy=POLICY)
        self.assertEqual(repeated["sectors"][0]["qualifyStreak"], first["sectors"][0]["qualifyStreak"])
        self.assertEqual(repeated["sectors"][0]["lastObservedTradingDate"], "20261023")

    def test_same_day_condition_change_does_not_add_persistence(self):
        first = manager.build_management_result(self._dated_context([sector()], "20261023"), self.universe(), policy=POLICY)
        weak = sector(score=0, leaders=[])
        changed = manager.build_management_result(self._dated_context([weak], "20261023"), self.universe(), previous=first, policy=POLICY)
        self.assertEqual(changed["sectors"][0]["qualifyStreak"], first["sectors"][0]["qualifyStreak"])
        restored = manager.build_management_result(self._dated_context([sector()], "20261023"), self.universe(), previous=changed, policy=POLICY)
        self.assertEqual(restored["sectors"][0]["qualifyStreak"], first["sectors"][0]["qualifyStreak"])

    def test_new_trading_date_advances_and_undated_preserves(self):
        first = manager.build_management_result(self._dated_context([sector()], "20261023"), self.universe(), policy=POLICY)
        second = manager.build_management_result(self._dated_context([sector()], "20261024"), self.universe(), previous=first, policy=POLICY)
        self.assertEqual(second["sectors"][0]["qualifyStreak"], first["sectors"][0]["qualifyStreak"] + 1)
        undated = manager.build_management_result(self.context([sector()]), self.universe(), previous=second, policy=POLICY)
        self.assertEqual(undated["sectors"][0]["qualifyStreak"], second["sectors"][0]["qualifyStreak"])

    def test_theme_and_leader_use_same_trading_date_guard(self):
        theme = {"status": "AVAILABLE", "periods": {"daily": {"themes": [{"id": "theme-1", "name": "테마", "score": 80, "verifiedMembership": True, "systemLeaders": [{"code": "005930", "score": 80}]}]}}}
        context = self.context([sector()])
        context["tradingDate"] = "20261023"
        context["themes"] = theme
        first = manager.build_management_result(context, self.universe(), policy=POLICY)
        repeated_context = copy.deepcopy(context)
        repeated = manager.build_management_result(repeated_context, self.universe(), previous=first, policy=POLICY)
        self.assertEqual(repeated["themes"][0]["qualifyStreak"], first["themes"][0]["qualifyStreak"])
        self.assertEqual(len(repeated["leaders"]), len(first["leaders"]))

    def test_undated_legacy_state_is_frozen(self):
        previous = {"sectors": [{"id": "261", "status": "ACTIVE", "qualifyStreak": 7, "weakenStreak": 0}], "themes": [], "leaders": []}
        result = manager.build_management_result(self.context([sector()]), self.universe(), previous=previous, policy=POLICY)
        row = result["sectors"][0]
        self.assertEqual((row["status"], row["qualifyStreak"], row["weakenStreak"]), ("ACTIVE", 7, 0))
        self.assertNotIn("lastObservedTradingDate", row)

    def test_historical_rerun_preserves_last_observed_date(self):
        first = manager.build_management_result(self._dated_context([sector()], "20261024"), self.universe(), policy=POLICY)
        older = manager.build_management_result(self._dated_context([sector()], "20261023"), self.universe(), previous=first, policy=POLICY)
        self.assertEqual(older["sectors"][0]["lastObservedTradingDate"], "20261024")
        self.assertEqual(older["sectors"][0]["qualifyStreak"], first["sectors"][0]["qualifyStreak"])


if __name__ == "__main__":
    unittest.main()
