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
            "leaders": leaders or [{"code": "005930", "score": 80, "assetType": "STOCK"}]}


class DynamicUniverseManagerTests(unittest.TestCase):
    def context(self, sectors=None, themes=None):
        return {"generatedAt": "t", "industries": {"status": "AVAILABLE", "industries": sectors or []},
                "themes": themes or {"status": "UNAVAILABLE", "reason": "RESEARCH_GATE"}}

    def universe(self):
        return {"stocks": [{"itemCode": "005930", "stockName": "삼성전자", "enabled": True},
                           {"itemCode": "000660", "stockName": "SK하이닉스", "enabled": True}],
                "sectors": {}, "themes": {},
                "watchlists": {"관심종목": ["005930", "000660"]},
                "leaders": {"sector": {}, "theme": {}, "watchlist": {}}}

    def test_sector_requires_persistence_then_activates(self):
        first = manager.build_management_result(self._dated_context([sector()], "20261021"), self.universe(), policy=POLICY)
        self.assertEqual(first["sectors"][0]["status"], "DISCOVERED")
        second = manager.build_management_result(self._dated_context([sector()], "20261022"), self.universe(), previous=first, policy=POLICY)
        self.assertEqual(second["sectors"][0]["status"], "ACTIVE")
        self.assertEqual(second["leaders"][0]["status"], "DISCOVERED")
        third = manager.build_management_result(self._dated_context([sector()], "20261023"), self.universe(), previous=second, policy=POLICY)
        self.assertEqual(third["leaders"][0]["status"], "ACTIVE")

    def test_weakening_moves_watch_then_removed(self):
        active = manager.build_management_result(self._dated_context([sector()], "20261019"), self.universe(), policy=POLICY)
        active = manager.build_management_result(self._dated_context([sector()], "20261020"), self.universe(), previous=active, policy=POLICY)
        weak = sector(score=10); weak["advancerPct"] = 0
        watch = manager.build_management_result(self._dated_context([weak], "20261021"), self.universe(), previous=active, policy=POLICY)
        self.assertEqual(watch["sectors"][0]["status"], "WATCH")
        removed = manager.build_management_result(self._dated_context([weak], "20261022"), self.universe(), previous=watch, policy=POLICY)
        self.assertEqual(removed["sectors"][0]["status"], "REMOVED")

    def test_verified_theme_and_missing_theme_handling(self):
        unavailable = manager.build_management_result(self._dated_context([sector()], "20261023"), self.universe(), policy=POLICY)
        self.assertEqual(unavailable["pending"][0]["status"], "PENDING")
        themes = {"status": "AVAILABLE", "periods": {"daily": {"themes": [
            {"id": "T1", "name": "AI", "score": 90, "verifiedMembership": True,
             "systemLeaders": [{"code": "000660", "score": 90, "assetType": "STOCK"}]},
            {"id": "T2", "name": "미검증", "score": 90, "systemLeaders": []},
        ]}}}
        result = manager.build_management_result({**self._dated_context([], "20261023"), "themes": themes}, self.universe(), policy=POLICY)
        self.assertEqual([row["status"] for row in result["themes"]], ["DISCOVERED", "REMOVED"])

    def test_deduplicates_collection_but_preserves_memberships(self):
        discovery = {"status": "AVAILABLE", "source": "KIWOOM_CONDITION_SEARCH",
                     "source_group": "조건A", "symbols": ["005930"]}
        result = manager.build_management_result(self._dated_context([sector()], "20261023"), self.universe(), discovery, policy=POLICY)
        row = next(row for row in result["memberships"] if row["code"] == "005930")
        self.assertEqual(result["collectionCodes"].count("005930"), 1)
        self.assertIn("KIWOOM_DISCOVERY", row["sources"])
        self.assertIn("USER_WATCHLIST", row["sources"])

    def test_automatic_run_cannot_mutate_user_watchlist(self):
        universe = self.universe(); before = copy.deepcopy(universe["watchlists"])
        manager.build_management_result(self._dated_context([sector()], "20261023"), universe, policy=POLICY)
        self.assertEqual(universe["watchlists"], before)

    def test_manual_watchlist_add_remove(self):
        universe = self.universe()
        universe_manager.mutate_user_watchlist(universe, "add", "관심종목", "123456", "테스트")
        self.assertIn("123456", universe["watchlists"]["관심종목"])
        universe_manager.mutate_user_watchlist(universe, "remove", "관심종목", "123456")
        self.assertNotIn("123456", universe["watchlists"]["관심종목"])
        universe_manager.mutate_user_watchlist(universe, "remove", "관심종목", "005930")
        self.assertNotIn("005930", universe["watchlists"]["관심종목"])

    def test_atomic_publish_and_restore_source_state(self):
        result = manager.build_management_result(self._dated_context([sector()], "20261023"), self.universe(), policy=POLICY)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"; state = Path(directory) / "state.json"; history = Path(directory) / "history.json"
            manager.publish_result(result, output, state, history)
            self.assertEqual(manager.load_json(output)["version"], "dynamic-universe-management-v1")
            self.assertEqual(manager.load_json(state), manager.load_json(output))
            self.assertTrue(manager.load_json(history)["events"])

    def test_load_limit_never_truncates_user_or_discovery(self):
        policy = {**POLICY, "collectionLimit": 1}
        discovery = {"status": "AVAILABLE", "symbols": ["777777"]}
        result = manager.build_management_result(self._dated_context([sector()], "20261023"), self.universe(), discovery, policy=policy)
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
        undated = manager.build_management_result(self._dated_context([sector()], "20261023"), self.universe(), previous=second, policy=POLICY)
        self.assertEqual(undated["sectors"][0]["qualifyStreak"], second["sectors"][0]["qualifyStreak"])

    def test_theme_and_leader_use_same_trading_date_guard(self):
        theme = {"status": "AVAILABLE", "periods": {"daily": {"themes": [{"id": "theme-1", "name": "테마", "score": 80, "verifiedMembership": True, "systemLeaders": [{"code": "005930", "score": 80, "assetType": "STOCK"}]}]}}}
        context = self._dated_context([sector()], "20261023")
        context["tradingDate"] = "20261023"
        context["themes"] = theme
        first = manager.build_management_result(context, self.universe(), policy=POLICY)
        repeated_context = copy.deepcopy(context)
        repeated = manager.build_management_result(repeated_context, self.universe(), previous=first, policy=POLICY)
        self.assertEqual(repeated["themes"][0]["qualifyStreak"], first["themes"][0]["qualifyStreak"])
        self.assertEqual(len(repeated["leaders"]), len(first["leaders"]))

    def test_undated_legacy_state_is_frozen(self):
        previous = {"sectors": [{"id": "반도체", "status": "ACTIVE", "qualifyStreak": 7, "weakenStreak": 0}], "themes": [], "leaders": []}
        result = manager.build_management_result(self.context([sector()]), self.universe(), previous=previous, policy=POLICY)
        row = result["sectors"][0]
        self.assertEqual((row["status"], row["qualifyStreak"], row["weakenStreak"]), ("ACTIVE", 7, 0))
        self.assertNotIn("lastObservedTradingDate", row)

    def test_historical_rerun_preserves_last_observed_date(self):
        first = manager.build_management_result(self._dated_context([sector()], "20261024"), self.universe(), policy=POLICY)
        older = manager.build_management_result(self._dated_context([sector()], "20261023"), self.universe(), previous=first, policy=POLICY)
        self.assertEqual(older["sectors"][0]["lastObservedTradingDate"], "20261024")
        self.assertEqual(older["sectors"][0]["qualifyStreak"], first["sectors"][0]["qualifyStreak"])

    def test_structured_asset_types_preserve_stock_and_block_non_equity(self):
        self.assertEqual(manager.leader_asset_type({"code": "005930", "assetType": "STOCK"}), "STOCK")
        self.assertEqual(manager.leader_asset_type({"code": "33626K", "assetType": "STOCK"}), "STOCK")
        self.assertEqual(manager.leader_asset_type({"code": "123456", "assetType": "STOCK", "tags": ["PREFERRED_LIKE"]}), "STOCK")
        self.assertEqual(manager.leader_asset_type({"code": "102110", "assetType": "ETF"}), "ETF")
        self.assertEqual(manager.leader_asset_type({"code": "530107", "assetType": "ETN"}), "ETN")
        self.assertEqual(manager.leader_asset_type({"code": "0030R0", "assetType": "STOCK", "stockType": "RT"}), "REIT")
        self.assertEqual(manager.leader_asset_type({"code": "0088M0", "assetType": "STOCK", "tags": ["SPAC"]}), "SPAC")
        self.assertEqual(manager.leader_asset_type({"code": "0120X0", "assetType": "STOCK", "stockType": "PF"}), "FUND")
        self.assertEqual(manager.leader_asset_type({"code": "999999"}), "UNKNOWN")

    def test_score_is_preserved_but_fund_candidate_is_not_eligible(self):
        ok, score, checks = manager._leader_decision(
            {"code": "0120X0", "score": 100, "persistence": 2}, POLICY["leader"],
            [{"code": "0120X0", "assetType": "STOCK", "stockType": "PF"}], "20261026")
        self.assertFalse(ok)
        self.assertEqual(score, 100)
        self.assertEqual(checks["assetType"], "FUND")
        self.assertFalse(checks["assetTypeEligible"])

    def test_unverified_asset_type_and_trading_date_are_pending(self):
        unknown_context = self._dated_context([sector(leaders=[{"code": "999999", "score": 100}])], "20261026")
        unknown_context["industries"].update({"qualityStatus": "FULL", "quality": {"industryEvidenceUsable": "FULL"}})
        unknown = manager.build_management_result(unknown_context, self.universe(), policy=POLICY)
        self.assertTrue(any(item.get("reason") == "LEADER_ASSET_TYPE_UNVERIFIED" for item in unknown["pending"]))
        mismatch = {"code": "005930", "score": 100, "assetType": "STOCK",
                    "technicalEvidence": {"lastConfirmedDailyDate": "20261025"}}
        ok, _, checks = manager._leader_decision(mismatch, POLICY["leader"], None, "20261026")
        self.assertFalse(ok)
        self.assertFalse(checks["tradingDateVerified"])
        undated = manager._leader_decision({"code": "005930", "score": 100, "assetType": "STOCK"},
                                           POLICY["leader"], None, None)
        self.assertFalse(undated[0])

    def test_known_non_equity_previous_leader_is_removed_and_not_admitted(self):
        previous = {
            "sectors": [{"id": "반도체", "status": "ACTIVE", "qualifyStreak": 2, "weakenStreak": 0}],
            "leaders": [{"id": "sector:반도체:0120X0", "code": "0120X0", "status": "ACTIVE",
                         "qualifyStreak": 2, "weakenStreak": 0, "collectionAdmitted": True}],
        }
        result = manager.build_management_result(
            self._dated_context([sector(leaders=[{"code": "0120X0", "score": 100}])], "20261026"),
            self.universe(), previous=previous, policy=POLICY,
            asset_registry={"assets": [{"code": "0120X0", "assetType": "STOCK", "stockType": "PF"}]})
        row = next(item for item in result["leaders"] if item["id"] == "sector:반도체:0120X0")
        self.assertEqual(row["status"], "REMOVED")
        self.assertFalse(row["collectionAdmitted"])
        self.assertNotIn("0120X0", result["collectionCodes"])

    def test_asset_registry_does_not_change_full_market_asset_codes(self):
        import json
        assets = json.loads((Path(__file__).parent / "data/market-universe/all-assets.json").read_text())['assets']
        before = {row["code"] for row in assets}
        result = manager.build_management_result(
            self._dated_context([sector(leaders=[{"code": "0120X0", "score": 100}])], "20261026"),
            self.universe(), policy=POLICY, asset_registry={"assets": assets})
        self.assertNotIn("0120X0", result["collectionCodes"])
        self.assertEqual(before, {row["code"] for row in assets})


if __name__ == "__main__":
    unittest.main()
