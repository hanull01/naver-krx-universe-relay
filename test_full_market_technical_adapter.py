import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import daily_report

from tools.full_market_technical_adapter import (
    FullMarketTechnicalAdapterError,
    build_report_input,
)


class FullMarketTechnicalAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = "/tmp/naver-krx-backfill-final-recollected-20261101-v2"
        self.technicals = self.root / "technicals.json"
        self.states = self.root / "states.json"
        self.groups = self.root / "group-states.json"
        self.universe = self.root / "universe.json"
        provenance = {
            "sourceStaging": self.source,
            "sourceCanonicalPath": self.source + "/canonical/daily/005930.jsonl",
            "sourceCanonicalSha256": "a" * 64,
        }
        technical_row = {
            "itemCode": "005930", "assetType": "STOCK", "asOfDate": "20261029",
            "availabilityStatus": "AVAILABLE", "ma20": 238350, "provenance": provenance,
        }
        state_row = {
            "itemCode": "005930", "assetType": "STOCK", "asOfDate": "20261029",
            "availabilityStatus": "AVAILABLE", "price": 230500, "provenance": provenance,
        }
        self.technicals.write_text(json.dumps({
            "targetDate": "20261029", "counts": {"STOCK_AVAILABLE": 1}, "datas": [technical_row]
        }), encoding="utf-8")
        self.states.write_text(json.dumps({"targetDate": "20261029", "datas": [state_row]}), encoding="utf-8")
        self.groups.write_text(json.dumps({
            "targetDate": "20261029", "availabilityStatus": "UNAVAILABLE", "groups": []
        }), encoding="utf-8")
        (self.root / "report-contract-projection.json").write_text(json.dumps({
            "asOf": "20261029", "status": "PARTIAL", "observedAt": "2026-11-01T07:00:00+09:00",
            "fullMarket": {"breadth": {}, "leaders": [], "quality": {}},
        }), encoding="utf-8")
        self.universe.write_text(json.dumps({
            "stocks": [{"itemCode": "005930", "stockName": "삼성전자", "enabled": True}],
            "watchlists": {"관심종목": ["005930"]},
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_market_and_registered_projection_are_separate(self):
        current = {"status": "AVAILABLE", "tradingDate": "20261029", "industries": [{"name": "현재 업종"}]}
        result = build_report_input(self.technicals, self.states, self.groups, self.universe, current)
        self.assertEqual(len(result["fullMarket"]["technicals"]), 1)
        self.assertEqual(result["registeredUniverse"]["registeredCount"], 1)
        self.assertEqual(result["registeredUniverse"]["items"][0]["itemCode"], "005930")
        self.assertEqual(result["currentSnapshot"], current)
        self.assertIs(result["currentSnapshot"], result["currentSnapshot"])
        self.assertTrue(result["currentSnapshotSeparated"])
        self.assertFalse(result["fallbackUsed"])

    def test_partial_etf_date_is_preserved_without_promotion(self):
        payload = json.loads(self.technicals.read_text())
        row = payload["datas"][0]
        row.update({"itemCode": "261920", "assetType": "ETF", "asOfDate": "20261028", "availabilityStatus": "PARTIAL"})
        self.technicals.write_text(json.dumps(payload), encoding="utf-8")
        states = json.loads(self.states.read_text())
        states["datas"][0].update({"itemCode": "261920", "assetType": "ETF", "asOfDate": "20261028", "availabilityStatus": "PARTIAL"})
        self.states.write_text(json.dumps(states), encoding="utf-8")
        result = build_report_input(self.technicals, self.states, self.groups, self.universe)
        self.assertEqual(result["fullMarket"]["technicals"]["261920"]["asOfDate"], "20261028")
        self.assertEqual(result["fullMarket"]["technicals"]["261920"]["availabilityStatus"], "PARTIAL")

    def test_forbidden_fallback_provenance_is_rejected(self):
        payload = json.loads(self.technicals.read_text())
        payload["datas"][0]["provenance"]["sourceCanonicalPath"] = "/repo/data/technicals.json"
        self.technicals.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(FullMarketTechnicalAdapterError):
            build_report_input(self.technicals, self.states, self.groups, self.universe)

    def test_external_provenance_file_is_accepted(self):
        technicals = json.loads(self.technicals.read_text())
        states = json.loads(self.states.read_text())
        provenance = technicals["datas"][0].pop("provenance")
        states["datas"][0].pop("provenance")
        self.technicals.write_text(json.dumps(technicals), encoding="utf-8")
        self.states.write_text(json.dumps(states), encoding="utf-8")
        (self.root / "provenance.json").write_text(json.dumps({
            "schemaVersion": 1,
            "sourceStaging": self.source,
            "targetDate": "20261029",
            "byCode": {"005930": provenance},
        }), encoding="utf-8")
        result = build_report_input(self.technicals, self.states, self.groups, self.universe)
        self.assertEqual(result["sourceStaging"], self.source)
        self.assertEqual(result["fullMarket"]["technicals"]["005930"]["provenance"], provenance)

    def test_missing_registered_symbol_is_not_filled_from_legacy_data(self):
        universe = json.loads(self.universe.read_text())
        universe["stocks"].append({
            "itemCode": "000660", "stockName": "SK하이닉스", "enabled": True,
        })
        self.universe.write_text(json.dumps(universe), encoding="utf-8")
        result = build_report_input(self.technicals, self.states, self.groups, self.universe)
        self.assertEqual(result["registeredUniverse"]["registeredCount"], 2)
        self.assertEqual(result["registeredUniverse"]["missingCodes"], ["000660"])
        self.assertFalse(result["fallbackUsed"])

    def test_future_row_after_target_is_rejected(self):
        payload = json.loads(self.technicals.read_text())
        payload["datas"][0]["asOfDate"] = "20261030"
        self.technicals.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(FullMarketTechnicalAdapterError):
            build_report_input(
                self.technicals,
                self.states,
                self.groups,
                self.universe,
                expected_target_date="20261029",
            )

    def test_target_20261030_is_allowed(self):
        technicals = json.loads(self.technicals.read_text())
        states = json.loads(self.states.read_text())
        groups = json.loads(self.groups.read_text())
        technicals["targetDate"] = "20261030"
        states["targetDate"] = "20261030"
        groups["targetDate"] = "20261030"
        technicals["datas"][0]["asOfDate"] = "20261030"
        states["datas"][0]["asOfDate"] = "20261030"
        self.technicals.write_text(json.dumps(technicals), encoding="utf-8")
        self.states.write_text(json.dumps(states), encoding="utf-8")
        self.groups.write_text(json.dumps(groups), encoding="utf-8")
        result = build_report_input(
            self.technicals,
            self.states,
            self.groups,
            self.universe,
            expected_target_date="20261030",
        )
        self.assertEqual(result["targetDate"], "20261030")

    def test_daily_report_loader_keeps_full_market_and_registered_inputs_separate(self):
        current = {"status": "AVAILABLE", "tradingDate": "20261029"}
        with patch.object(daily_report, "UNIVERSE_PATH", self.universe):
            result = daily_report.load_report_technical_inputs(
                self.root, current_snapshot=current
            )
        self.assertEqual(result["mode"], "STAGING")
        self.assertEqual(len(result["analysisTechnicals"]["datas"]), 1)
        self.assertEqual(len(result["technicals"]["datas"]), 1)
        self.assertEqual(result["technicals"]["datas"][0]["itemCode"], "005930")
        self.assertEqual(result["metadata"]["fullMarketTechnicalCount"], 1)
        self.assertEqual(result["metadata"]["registeredObservedCount"], 1)
        self.assertEqual(result["metadata"]["currentSnapshotSeparated"], True)
        self.assertFalse(result["metadata"]["fallbackUsed"])

    def test_staging_current_quotes_remain_visible_as_a_separate_generation(self):
        quotes = {
            "status": "AVAILABLE",
            "sourceTime": "2026-10-30T20:00:00+09:00",
            "datas": [{"itemCode": "005930", "sourceTime": "2026-10-30T20:00:00+09:00"}],
        }
        restricted = daily_report.restrict_staging_current_quotes(quotes, "20261029")
        self.assertEqual(restricted["status"], "AVAILABLE")
        self.assertEqual(len(restricted["datas"]), 1)
        self.assertEqual(restricted["observedSourceDates"], ["20261030"])
        self.assertTrue(restricted["currentSnapshotSeparated"])
        self.assertEqual(restricted["currentSnapshotDateStatus"], "CURRENT_OBSERVATION_SEPARATE")

    def test_krx_staging_is_primary_and_never_falls_back_to_naver_history(self):
        technicals = json.loads(self.technicals.read_text())
        states = json.loads(self.states.read_text())
        for payload in (technicals, states):
            payload["source"] = "KRX_M10_GENERATION"
            payload["historicalIndicatorSource"] = "KRX"
            payload["historicalFallbackAllowed"] = False
        provenance = {
            "sourceStaging": "KRX_M10_GENERATION",
            "sourceCanonicalPath": "m10/monitoring_interchange.json",
            "sourceCanonicalSha256": "b" * 64,
        }
        for payload in (technicals, states):
            payload["datas"][0]["provenance"] = provenance
        self.technicals.write_text(json.dumps(technicals), encoding="utf-8")
        self.states.write_text(json.dumps(states), encoding="utf-8")
        result = build_report_input(
            self.technicals, self.states, self.groups, self.universe,
            expected_source_staging="KRX_M10_GENERATION",
        )
        self.assertEqual(result["historicalIndicatorSource"], "KRX")
        self.assertEqual(result["historicalPriority"], "PRIMARY")
        self.assertFalse(result["historicalFallbackAllowed"])
        self.assertEqual(result["currentObservationPolicy"], "SEPARATE")

    def test_krx_source_mismatch_fails_closed(self):
        with self.assertRaisesRegex(FullMarketTechnicalAdapterError, "staged source mismatch"):
            build_report_input(
                self.technicals, self.states, self.groups, self.universe,
                expected_source_staging="KRX_M10_GENERATION",
            )

    def test_krx_lineage_uses_verified_receipt_and_provenance(self):
        technicals = json.loads(self.technicals.read_text())
        states = json.loads(self.states.read_text())
        generation = "gen_20261029_krx"
        provenance = {
            "sourceStaging": "KRX_M10_GENERATION",
            "sourceCanonicalPath": "m10/monitoring_interchange.json",
            "sourceCanonicalSha256": "b" * 64,
            "sourceGenerationId": generation,
        }
        for payload in (technicals, states):
            payload.update({
                "source": "KRX_M10_GENERATION",
                "generationId": generation,
                "historicalIndicatorSource": "KRX",
                "historicalFallbackAllowed": False,
                "receiptStatus": "VERIFIED",
                "counts": {"entityCount": 1, "instrumentCount": 1},
            })
            payload["datas"][0]["provenance"] = provenance
        technicals["datas"][0]["regularSession"] = {"status": "UNAVAILABLE"}
        states["datas"][0]["regularSession"] = {"status": "UNAVAILABLE"}
        self.technicals.write_text(json.dumps(technicals), encoding="utf-8")
        self.states.write_text(json.dumps(states), encoding="utf-8")
        (self.root / "provenance.json").write_text(json.dumps({
            "source": "KRX_OPEN_API", "generationId": generation,
            "byCode": {"005930": provenance},
        }), encoding="utf-8")
        (self.root / "receipt.json").write_text(json.dumps({
            "schemaVersion": 2, "source": "KRX_OPEN_API",
            "tradingDate": "20261029", "generationId": generation,
            "generationStatus": "VERIFIED", "publicationAuthorized": True,
        }), encoding="utf-8")

        result = build_report_input(
            self.technicals, self.states, self.groups, self.universe,
            expected_source_staging="KRX_M10_GENERATION",
        )
        lineage = result["historicalLineage"]
        self.assertEqual(lineage["status"], "VERIFIED")
        self.assertEqual(lineage["generationId"], generation)
        self.assertEqual(lineage["targetDate"], "20261029")
        self.assertEqual(lineage["receiptStatus"], "VERIFIED")
        self.assertEqual(lineage["instrumentCount"], 1)
        self.assertFalse(lineage["fallbackAllowed"])
        self.assertEqual(result["registeredUniverse"]["missingCodes"], [])
        self.assertEqual(result["registeredUniverse"]["missingHistoryCodes"], [])
        self.assertEqual(result["registeredUniverse"]["regularSessionUnavailableCodes"], ["005930"])

    def test_krx_lineage_without_verified_receipt_is_unavailable(self):
        technicals = json.loads(self.technicals.read_text())
        states = json.loads(self.states.read_text())
        for payload in (technicals, states):
            payload.update({
                "source": "KRX_M10_GENERATION",
                "generationId": "gen_missing_receipt",
                "historicalIndicatorSource": "KRX",
                "historicalFallbackAllowed": False,
            })
            payload["datas"][0]["provenance"].update({
                "sourceStaging": "KRX_M10_GENERATION",
                "sourceGenerationId": "gen_missing_receipt",
            })
        self.technicals.write_text(json.dumps(technicals), encoding="utf-8")
        self.states.write_text(json.dumps(states), encoding="utf-8")
        result = build_report_input(
            self.technicals, self.states, self.groups, self.universe,
            expected_source_staging="KRX_M10_GENERATION",
        )
        self.assertEqual(result["historicalLineage"]["status"], "UNAVAILABLE")
        self.assertEqual(result["historicalLineage"]["receiptStatus"], "UNAVAILABLE")

    def test_derived_projection_is_reported_as_partial_not_final(self):
        projection = {
            "asOf": "20261029", "status": "AVAILABLE",
            "observedAt": "2026-11-01T07:00:00+09:00",
            "fullMarket": {"breadth": {"advancers": 1}, "leaders": [], "quality": {}},
        }
        result = daily_report.full_market_from_derived_projection(projection, "2026-10-29")
        summary = daily_report.full_market_summary(
            result, {"status": "OK", "mismatches": [], "missingEvidence": []}
        )
        self.assertEqual(summary["status"], "PARTIAL")
        self.assertEqual(summary["snapshotDateStatus"], "DERIVED_TARGET_DATE")
        self.assertEqual(summary["breadth"]["advancers"], 1)


if __name__ == "__main__":
    unittest.main()
