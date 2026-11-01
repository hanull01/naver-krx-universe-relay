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

    def test_20261030_is_rejected(self):
        payload = json.loads(self.technicals.read_text())
        payload["targetDate"] = "20261030"
        self.technicals.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(FullMarketTechnicalAdapterError):
            build_report_input(self.technicals, self.states, self.groups, self.universe)

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

    def test_staging_current_quotes_do_not_use_a_newer_generation(self):
        quotes = {
            "status": "AVAILABLE",
            "sourceTime": "2026-10-30T20:00:00+09:00",
            "datas": [{"itemCode": "005930", "sourceTime": "2026-10-30T20:00:00+09:00"}],
        }
        restricted = daily_report.restrict_staging_current_quotes(quotes, "20261029")
        self.assertEqual(restricted["status"], "STALE")
        self.assertEqual(restricted["datas"], [])
        self.assertEqual(restricted["observedSourceDates"], ["20261030"])


if __name__ == "__main__":
    unittest.main()
