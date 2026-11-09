import json
import tempfile
import unittest
from pathlib import Path

import naver_market
from tools import full_market_monitoring_evidence as evidence
from tools.market_snapshot_projection import build_projection


def _stock(code, name, change_rate):
    return {
        "code": code,
        "name": name,
        "market": "KOSPI",
        "currentPrice": 100.0,
        "openPrice": 99.0,
        "highPrice": 101.0,
        "lowPrice": 98.0,
        "accumulatedTradingVolume": 1000,
        "accumulatedTradingValue": 100000,
        "change": change_rate,
        "changeRate": change_rate,
        "snapshotGeneratedAt": "2026-11-09T10:00:00+09:00",
    }


def _current_snapshot():
    stocks = [_stock("000001", "A", 3.0), _stock("000002", "B", -1.0)]
    return {
        "status": "SUCCESS",
        "collectionStatus": "SUCCESS",
        "generatedAt": "2026-11-09T10:00:00+09:00",
        "tradingDate": "2026-11-09",
        "expectedCount": len(stocks),
        "coverageCount": len(stocks),
        "count": len(stocks),
        "attemptCount": 1,
        "missingCodes": [],
        "duplicateCodes": [],
        "qualityWarnings": [],
        "stocks": stocks,
    }


def _source(status="OK", rows=None):
    rows = rows if rows is not None else [
        {"id": "1", "name": "반도체", "risingStocks": 1, "fallingStocks": 1,
         "unchangedStocks": 0, "changeRate": 1.0, "totalStocks": 2},
        {"id": "2", "name": "보험", "risingStocks": 1, "fallingStocks": 1,
         "unchangedStocks": 0, "changeRate": -0.2, "totalStocks": 2},
    ]
    return {
        "generatedAt": "2026-11-09T10:00:00+09:00",
        "source": "NAVER",
        "status": status,
        "endpoint": "pc_v2_industry_cursor" if status == "OK" else None,
        "sourceDate": "2026-11-09" if status == "OK" else None,
        "count": len(rows),
        "pagination": {"type": "CURSOR", "parameter": "cursor",
                        "endCondition": "HAS_NEXT_FALSE", "pagesFetched": 1},
        "industries": rows,
    }


def _membership(status="OK"):
    rows = [
        {"id": "1", "name": "반도체", "status": status, "memberCount": 1,
         "members": [{"code": "000001", "name": "A"}]},
        {"id": "2", "name": "보험", "status": status, "memberCount": 1,
         "members": [{"code": "000002", "name": "B"}]},
    ] if status == "OK" else []
    return {
        "generatedAt": "2026-11-09T10:00:00+09:00",
        "source": "NAVER",
        "status": status,
        "sourceDate": "2026-11-09" if status == "OK" else None,
        "sourceIndustryCount": len(rows),
        "industryCount": len(rows),
        "membershipCount": sum(row["memberCount"] for row in rows),
        "industries": rows,
    }


class IndustryOperatingPathTests(unittest.TestCase):
    def test_manifest_and_projection_preserve_source_contract(self):
        source = _source()
        rankings = {"generatedAt": source["generatedAt"], "status": "OK", "rankings": []}
        investors = {"generatedAt": source["generatedAt"], "status": "OK", "investors": []}
        manifest = naver_market.build_manifest(rankings, source, investors)
        self.assertEqual(manifest["datasets"]["industries"]["sourceDate"], "2026-11-09")
        self.assertEqual(manifest["datasets"]["industries"]["count"], 2)
        projection = build_projection(
            rankings=rankings, investor_flow=investors,
            industries=source, membership=_membership(), manifest=manifest,
        )
        self.assertEqual(projection["industries"]["status"], "OK")
        self.assertEqual(projection["industries"]["count"], 2)
        self.assertEqual(projection["industries"]["top"][0]["name"], "반도체")

    def test_latest_industries_writer_records_success_and_date(self):
        with tempfile.TemporaryDirectory() as directory:
            payloads, _ = evidence.build_evidence(
                _current_snapshot(), history_dir=Path(directory) / "history",
                industries=_source(), membership=_membership(), today="20261109",
                previous_summary={}, theme_membership={"status": "UNUSABLE", "themes": []},
            )
            latest = payloads["latest-industries.json"]
            self.assertEqual(latest["status"], "OK")
            self.assertEqual(latest["source"], "NAVER")
            self.assertEqual(latest["sourceDate"], "2026-11-09")
            self.assertEqual(latest["observationDate"], "20261109")
            self.assertEqual(latest["sourceCount"], 2)
            self.assertEqual(len(latest["industries"]), 2)
            output = Path(directory) / "monitoring"
            evidence.write_evidence(payloads, output)
            stored = json.loads((output / "latest-industries.json").read_text())
            self.assertEqual(stored["status"], "OK")
            self.assertEqual(stored["sourceDate"], "2026-11-09")

    def test_empty_or_failed_source_is_explicit_error(self):
        with tempfile.TemporaryDirectory() as directory:
            payloads, _ = evidence.build_evidence(
                _current_snapshot(), history_dir=Path(directory) / "history",
                industries=_source("ERROR", []), membership=_membership("ERROR"),
                today="20261109", previous_summary={},
                theme_membership={"status": "UNUSABLE", "themes": []},
            )
            latest = payloads["latest-industries.json"]
            self.assertEqual(latest["status"], "ERROR")
            self.assertEqual(latest["sourceCount"], 0)
            self.assertEqual(latest["industries"], [])


if __name__ == "__main__":
    unittest.main()
