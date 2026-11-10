import unittest

import daily_report
from tools.report_contract import build_report_contract


class TradingDateContractFixTests(unittest.TestCase):
    def test_krx_historical_and_naver_current_dates_are_aligned_by_role(self):
        alignment = daily_report.date_alignment(
            "2026-11-09",
            {
                "indices": [
                    {"code": "KOSPI", "close": 1, "sourceTime": "2026-11-09T10:30:00+09:00"},
                    {"code": "KOSDAQ", "close": 1, "sourceTime": "2026-11-09T10:30:00+09:00"},
                ]
            },
            {
                "status": "AVAILABLE",
                "snapshotDate": "20261106",
                "snapshotDateStatus": "DERIVED_TARGET_DATE",
                "maBasisDates": ["20261106"],
            },
            observed_date="20261109",
            historical_source="KRX",
            current_snapshot_source="NAVER_SEPARATE",
        )
        self.assertEqual(alignment["status"], "SEPARATE_SOURCES_ALIGNED")
        self.assertEqual(alignment["mismatches"], [])
        self.assertEqual(alignment["fullMarketSnapshotDate"], "20261106")

    def test_contract_keeps_current_evidence_and_historical_technicals_separate(self):
        generated = "2026-11-09T01:30:00+00:00"
        evidence = {
            "tradingDate": "20261109",
            "status": "VERIFIED",
            "reason": "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT",
            "evidenceClass": "INTRADAY_SESSION",

            "collectionGeneratedAt": generated,
        }
        total = {"stockCount": 2771, "advancers": 1291, "decliners": 1270, "unchanged": 210}
        contract = build_report_contract(
            indices={"indices": [
                {"code": "KOSPI", "close": 1, "sourceTime": "2026-11-09T10:30:00+09:00"},
                {"code": "KOSDAQ", "close": 1, "sourceTime": "2026-11-09T10:30:00+09:00"},
            ]},
            summary={"generatedAt": generated, "fullMarket": {
                "tradingDate": "20261109",
                "tradingDateEvidence": evidence,
                "markets": {"TOTAL": total},
            }},
            breadth={"generatedAt": generated, "breadth": {"TOTAL": total}, "fullMarket": {
                "tradingDate": "20261109", "authoritativeCount": 2771,
                "qualityStatus": "WARNING",
            }},
            quality={
                "generatedAt": generated,
                "tradingDate": "20261109",
                "tradingDateEvidence": evidence,
                "observedDate": "20261109",
                "historicalIndicatorSource": "KRX",
                "historicalGenerationId": "gen_20261106_1794185533300691000",
                "historicalTargetDate": "20261106",
                "historicalReceiptStatus": "VERIFIED",
                "historicalLineageStatus": "VERIFIED",
                "historicalInstrumentCount": 3953,
                "historicalEntityCount": 4085,
                "historicalObservedInstrumentCount": 3953,
                "currentSnapshotSource": "NAVER_SEPARATE",
            },
            industries={"generatedAt": generated, "industries": []},
            themes={"generatedAt": generated, "themes": []},
            v2_latest={},
            v2_history={},
            leaders={},
            current_market_observation={"generatedAt": generated, "breadth": {"TOTAL": total}, "fullMarket": {
                "tradingDate": "20261109", "authoritativeCount": 2771, "qualityStatus": "WARNING",
            }},
            historical_technical_market={
                "source": "KRX",
                "targetDate": "20261106",
                "generationId": "gen_20261106_1794185533300691000",
                "instrumentCount": 3953,
                "metrics": {
                    "aboveMA20": {"count": 744, "eligibleCount": 3923, "pct": 18.97},
                    "aboveMA60": {"count": 680, "eligibleCount": 3885, "pct": 17.5},
                    "aboveMA120": {"count": 697, "eligibleCount": 3806, "pct": 18.31},
                },
            },
        )
        self.assertEqual(contract["tradingDateStatus"], "VERIFIED")
        self.assertEqual(contract["currentMarketObservation"]["observedDate"], "20261109")
        self.assertEqual(contract["currentMarketObservation"]["totalCount"], 2771)
        self.assertEqual(contract["historicalTechnicalMarket"]["targetDate"], "20261106")
        self.assertEqual(contract["historicalTechnicalMarket"]["generationId"], "gen_20261106_1794185533300691000")
        self.assertEqual(contract["historicalTechnicalMarket"]["metrics"]["aboveMA20"]["count"], 744)


if __name__ == "__main__":
    unittest.main()
