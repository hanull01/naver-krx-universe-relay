import unittest

from tools.build_dynamic_universe import input_evidence, usable_industry_rows, verified_trading_date
from dynamic_universe_manager import build_management_result


class IndustryEvidenceTests(unittest.TestCase):
    def row(self, **changes):
        row = {
            "id": "261", "memberCount": 10, "featureValidMemberCount": 10,
            "closeValidMemberCount": 10, "advancePct": 60.0,
            "relativeStrengthVsMarket": 1.0, "pctAboveMA20": 60.0,
            "pctVolumeAbove20DayAverage": 70.0, "leaders": [],
        }
        row.update(changes)
        return row

    def test_partial_quality_with_valid_rows_is_not_full(self):
        rows, status = usable_industry_rows({
            "quality": {"sourceStatus": "PARTIAL", "industryEvidenceUsable": "UNUSABLE"},
            "industries": [self.row()],
        })
        self.assertEqual(status, "PARTIAL_WITH_COVERAGE")
        self.assertEqual(len(rows), 1)

    def test_current_observation_rows_survive_without_krx_derived_metrics(self):
        row = self.row(
            relativeStrengthVsMarket=None,
            officialChangeRateRelativeStrength=2.5,
            pctAboveMA20=None,
            pctVolumeAbove20DayAverage=None,
            leaders=[{"code": "005930", "name": "삼성전자", "sourceDate": "20261109"}],
        )
        rows, status = usable_industry_rows({
            "quality": {"sourceStatus": "OK", "industryEvidenceUsable": "FULL"},
            "industries": [row],
        })
        self.assertEqual(status, "FULL")
        self.assertEqual([item["id"] for item in rows], ["261"])

    def test_operating_sector_evaluation_receives_industry_rows(self):
        row = self.row(
            officialChangeRateRelativeStrength=2.5,
            leaders=[{"code": "005930", "name": "삼성전자", "assetType": "STOCK", "sourceDate": "20261109"}],
        )
        result = build_management_result(
            {"tradingDate": "20261109", "industries": {
                "qualityStatus": "FULL",
                "quality": {"sourceStatus": "OK", "industryEvidenceUsable": "FULL"},
                "industries": [row],
            }, "themes": {"status": "UNAVAILABLE"}},
            {}, policy={
                "activationConfirmations": 2, "removalConfirmations": 2,
                "watchConfirmations": 1, "leaderLimitPerGroup": 3,
                "collectionLimit": 120,
                "sector": {"minimumScore": 60, "minimumRelativeStrength": 0,
                           "minimumAdvancerPct": 50, "minimumTurnoverConcentrationPct": 0,
                           "minimumPctAboveMA20": 50, "minimumLeaderCount": 1},
                "theme": {"minimumScore": 60, "requireVerifiedMembership": True},
                "leader": {"minimumScore": 60, "minimumPersistence": 1},
            }, asset_registry={"assets": [{"code": "005930", "assetType": "STOCK"}]},
        )
        self.assertEqual(len(result["sectors"]), 1)
        self.assertEqual(result["sectors"][0]["id"], "261")
        self.assertEqual(result["industryEvidence"]["analyzedIndustryCount"], 1)

    def test_incomplete_rows_are_rejected(self):
        rows, status = usable_industry_rows({
            "quality": {"sourceStatus": "PARTIAL", "industryEvidenceUsable": "UNUSABLE"},
            "industries": [self.row(closeValidMemberCount=9), self.row(featureValidMemberCount=0)],
        })
        self.assertEqual(status, "UNUSABLE")
        self.assertEqual(rows, [])

    def test_verified_trading_date_is_explicit_and_validated(self):
        self.assertEqual(verified_trading_date({"tradingDate": "2026-10-23"}), "20261023")
        self.assertEqual(verified_trading_date({"tradingDate": "20261023"}), "20261023")
        self.assertIsNone(verified_trading_date({"tradingDate": "2026-10-2"}))
        self.assertIsNone(verified_trading_date({"tradingDate": "2026-02-30"}))
        self.assertIsNone(verified_trading_date({"generatedAt": "2026-10-23T20:59:00+09:00"}))

    def test_operational_latest_industries_without_date_does_not_fabricate_one(self):
        payload = {"generatedAt": "2026-10-23T14:41:53+00:00", "quality": {"sourceStatus": "OK"}, "industries": []}
        self.assertIsNone(verified_trading_date(payload))

    def test_input_evidence_preserves_date_quality_and_unknown_commit(self):
        evidence = input_evidence(
            {"tradingDate": "20261026"},
            {"sourceQualityStatus": "SOURCE_OBSERVED"},
        )
        self.assertEqual(evidence["tradingDate"], "20261026")
        self.assertEqual(evidence["sourceQualityStatus"], "SOURCE_OBSERVED")
        self.assertIsNone(evidence["sourceCommit"])
        self.assertEqual(evidence["sourceCommitStatus"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
