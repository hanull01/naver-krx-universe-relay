import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from research_report_linkage import build_research_linkage


class ResearchReportLinkageTests(unittest.TestCase):
    def write_reports(self, root: Path, rows):
        (root / "2026-10.json").write_text(
            json.dumps({"reports": rows}, ensure_ascii=False),
            encoding="utf-8",
        )

    def row(self, nid, code="000001", broker="A증권", date="2026-10-29", goal=100, opinion="BUY"):
        return {
            "nid": str(nid),
            "itemCode": code,
            "itemName": "테스트종목",
            "writeDate": date,
            "brokerName": broker,
            "title": f"보고서 {nid}",
            "goalPrice": goal,
            "normalizedOpinion": opinion,
            "previousSameBrokerNid": None,
            "goalPriceChange": None,
            "opinionChange": None,
            "analysis": {
                "earningsTextDirection": "UP",
                "targetPriceMentionDirection": "UNKNOWN",
                "topics": {"AI": 1},
            },
        }

    def base(self, rows, universe=None):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_reports(root, rows)
            result = build_research_linkage(
                universe=universe if universe is not None else {"watchlists": {"fixture": ["000001"]}},
                v2_latest={
                    "generatedAt": "g",
                    "inputEvidence": {"sourceCommit": None, "sourceCommitStatus": "UNAVAILABLE"},
                    "industries": [], "themes": [],
                },
                v2_history={"observations": []},
                industries={"generatedAt": "g", "industries": []},
                themes={"generatedAt": "g", "themes": []},
                reports_dir=root,
                manifest={"generatedAt": "m", "storedReportCount": len(rows)},
                as_of="2026-10-29",
            )
            return result

    def test_same_broker_revision_and_opinion_are_preserved(self):
        first = self.row(1, date="2026-10-01", goal=100, opinion="BUY")
        second = self.row(2, date="2026-10-29", goal=120, opinion="HOLD")
        second["previousSameBrokerNid"] = "1"
        second["goalPriceChange"] = {"from": 100, "to": 120, "amount": 20, "pct": 20.0}
        second["opinionChange"] = {"from": "BUY", "to": "HOLD"}
        result = self.base([first, second])
        stock = result["stocks"][0]
        self.assertEqual(stock["status"], "AVAILABLE")
        self.assertEqual(stock["brokers"][0]["latest"]["goalPriceChange"]["pct"], 20.0)
        self.assertEqual(stock["brokers"][0]["latest"]["opinionChange"]["to"], "HOLD")
        self.assertEqual(stock["forecastSignals"]["status"], "UNAVAILABLE")

    def test_cross_broker_dispersion_is_not_same_broker_revision(self):
        result = self.base([
            self.row(1, broker="A증권", goal=100),
            self.row(2, broker="B증권", goal=130),
        ])
        stock = result["stocks"][0]
        self.assertEqual(stock["brokerDispersion"]["status"], "AVAILABLE")
        self.assertEqual(stock["brokerDispersion"]["range"], 30)
        self.assertEqual(stock["recentVsPrevious"]["revisionUp"], 0)

    def test_empty_watchlist_is_explicit_and_unknown_stock_is_unavailable(self):
        result = self.base([], {"watchlists": {}})
        self.assertEqual(result["manualWatchlists"]["status"], "AVAILABLE_EMPTY")
        self.assertEqual(result["manualWatchlists"]["codeCount"], 0)
        self.assertEqual(result["stocks"], [])

        result = self.base([], {"watchlists": {"내 관심": ["999999"]}})
        self.assertEqual(result["manualWatchlists"]["groups"]["내 관심"], ["999999"])
        self.assertEqual(result["stocks"][0]["status"], "UNAVAILABLE")

    def test_v2_theme_membership_and_industry_leader_scope_are_explicit(self):
        result = build_research_linkage(
            universe={"watchlists": {}},
            v2_latest={
                "generatedAt": "g",
                "inputEvidence": {"sourceCommit": None, "sourceCommitStatus": "UNAVAILABLE"},
                "industries": [{"id": "i", "kind": "industry", "name": "산업", "v2Status": "WATCH", "score": 61, "persistenceDays": 2}],
                "themes": [{"id": "t", "kind": "theme", "name": "테마", "v2Status": "ACTIVE", "score": 82, "persistenceDays": 3}],
            },
            v2_history={"observations": []},
            industries={"generatedAt": "g", "industries": [{"id": "i", "name": "산업", "leaders": [{"code": "000001", "name": "테스트종목"}]}]},
            themes={"generatedAt": "g", "themes": [{"id": "t", "name": "테마", "membershipStatus": "OK", "memberCount": 1, "stocks": [{"code": "000002", "name": "테마종목"}]}]},
            reports_dir=Path("data/research/reports"),
            manifest={},
            as_of="2026-10-29",
        )
        by_id = {row["id"]: row for row in result["v2GroupLinks"]}
        self.assertEqual(by_id["t"]["membershipScope"], "full_theme_membership")
        self.assertEqual(by_id["i"]["membershipScope"], "leader_candidates_only")
        self.assertEqual(by_id["t"]["sourceCommitStatus"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
