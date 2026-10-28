import unittest

from tools.report_contract import build_report_contract, validate_rendered_models


class ReportContractTests(unittest.TestCase):
    def test_contract_uses_one_generation_and_keeps_v2_names_scores_and_history(self):
        common = {"generatedAt": "2026-10-28T10:09:19+00:00"}
        contract = build_report_contract(
            indices={**common, "indices": [{"name": "KOSPI", "close": 6012.44}]},
            summary={**common, "fullMarket": {"tradingDate": "20261028", "authoritativeCount": 2766,
                "markets": {"TOTAL": {"advancers": 1850, "decliners": 713, "unchanged": 203,
                    "breakout20Count": 81, "breakout20EligibleCount": 2642, "pctBreakout20": 3.0659}}}},
            breadth={**common, "fullMarket": {"marketTradingValue": {"KOSPI": {"value": 1}, "KOSDAQ": {"value": 2}}}},
            quality={**common, "collectionQualityStatus": "COMPLETE_RECONCILIATION_PENDING"},
            industries={**common, "industries": [{"name": "생명보험", "avgReturn1D": 6.35}]},
            themes={**common, "themes": [{"name": "S7", "membershipStatus": "ACTIVE"}]},
            v2_latest={"tradingDate": "2026-10-28", "industries": [{"id": "1", "name": "생명보험", "v2Status": "ACTIVE", "score": 69.46}],
                "themes": [{"id": "2", "name": "S7", "v2Status": "ACTIVE", "score": 88.84}]},
            v2_history={"observations": [{"tradingDate": "2026-10-27"}, {"tradingDate": "2026-10-28"}]},
            leaders={"fullMarketLeaders": []},
            candidates=[{"itemCode": "037950", "price": 14730}],
        )
        self.assertEqual(contract["status"], "AVAILABLE")
        self.assertEqual(contract["fullMarket"]["metrics"]["breakout20"]["count"], 81)
        self.assertEqual(contract["v2"]["industries"][0]["score"], 69.46)
        self.assertEqual(contract["v2"]["history"]["uniqueTradingDates"], ["2026-10-27", "2026-10-28"])

    def test_renderer_mismatch_catches_breakout_disagreement(self):
        good = {"asOf": "2026-10-28", "observedAt": "t", "fullMarket": {"breadth": {"advancers": 1, "decliners": 2, "unchanged": 3}, "metrics": {"breakout20": {"count": 81}}, "marketTradingValue": {"KOSPI": {"value": 1}, "KOSDAQ": {"value": 2}}}, "v2": {"industries": [], "themes": []}, "candidates": []}
        bad = {**good, "fullMarket": {**good["fullMarket"], "metrics": {"breakout20": {"count": 80}}}}
        self.assertEqual(validate_rendered_models(good, bad), ["fullMarket.metrics.breakout20.count"])
