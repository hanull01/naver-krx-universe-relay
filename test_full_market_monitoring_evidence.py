import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "evidence", Path(__file__).parent / "tools" / "full_market_monitoring_evidence.py"
)
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


def history(day, close, volume=100):
    return {"date": day, "open": close - 1, "high": close + 1,
            "low": close - 2, "close": close, "volume": volume,
            "feature_valid": True}


def current(code="005930", market="KOSPI", price=130, **extra):
    row = {
        "code": code, "name": code, "market": market,
        "currentPrice": price, "change": 2, "changeRate": 1.56,
        "openPrice": price - 1, "highPrice": price + 2,
        "lowPrice": price - 3, "accumulatedTradingVolume": 200,
        "accumulatedTradingValue": 1000, "snapshotGeneratedAt": "2026-10-01T10:00:00+09:00",
        "localDate": "20261001",
    }
    row.update(extra)
    return row


class EvidenceTests(unittest.TestCase):
    def snapshot(self, *rows, **extra):
        rows = list(rows or (current(),))
        return {
            "status": "SUCCESS", "collectionStatus": "SUCCESS",
            "generatedAt": "2026-10-01T10:00:00+09:00",
            "expectedCount": len(rows), "coverageCount": len(rows),
            "attemptCount": 1, "duplicateCodes": [], "missingCodes": [],
            "stocks": rows, **extra,
        }

    def context(self, date="20261001"):
        return {
            "intradayTradingDateEvidence": {
                "tradingDate": date, "status": "VERIFIED",
                "reason": "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT",
                "evidenceClass": "INTRADAY_SESSION",
                "collectionGeneratedAt": "2026-10-01T10:00:00+09:00",
            }
        }

    def test_verified_current_date_is_published_without_final_lifecycle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "005930.jsonl").write_text(
                json.dumps(history("20260930", 129)) + "\n", encoding="utf-8"
            )
            built, _ = evidence.build_evidence(
                self.snapshot(), root, today="20261001",
                publication_context=self.context(),
            )
        full_market = built["latest-summary.json"]["fullMarket"]
        self.assertEqual(full_market["tradingDate"], "20261001")
        self.assertEqual(full_market["tradingDateEvidence"]["status"], "VERIFIED")
        self.assertNotIn("finalConfirmed", full_market["tradingDateEvidence"])
        self.assertNotIn("baselineFreshnessStatus", built["latest-quality.json"])

    def test_missing_date_evidence_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            built, _ = evidence.build_evidence(
                self.snapshot(localDate=None), Path(directory), today="20261001",
                publication_context={},
            )
        full_market = built["latest-summary.json"]["fullMarket"]
        self.assertIsNone(full_market["tradingDate"])
        self.assertEqual(full_market["tradingDateEvidence"]["reason"],
                         "CANONICAL_SESSION_DATE_MISMATCH")

    def test_history_merge_is_read_only_and_does_not_duplicate_current_bar(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "005930.jsonl"
            rows = [history(f"202609{day:02d}", 100 + day) for day in range(1, 22)]
            original = "".join(json.dumps(row) + "\n" for row in rows)
            path.write_text(original, encoding="utf-8")
            feature = evidence.feature_row(
                current(), evidence.read_history("005930", root, "20261001"), "20261001"
            )
            self.assertIsNotNone(feature["ma20"])
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_industry_aggregation_uses_current_membership_and_limits_leaders(self):
        rows = [
            evidence.feature_row(current("000001", price=120), [history(f"202609{d:02d}", 100 + d) for d in range(1, 22)], "20261001"),
            evidence.feature_row(current("000002", price=110), [history(f"202609{d:02d}", 100 + d) for d in range(1, 22)], "20261001"),
        ]
        industries = {"status": "OK", "sourceDate": "20261001",
                      "industries": [{"id": "i1", "name": "테스트업종"}]}
        membership = {"status": "OK", "industries": [{
            "id": "i1", "name": "테스트업종", "status": "OK",
            "members": [{"code": "000001"}, {"code": "000002"}],
        }]}
        quality, result = evidence.aggregate_industries(rows, industries, membership,
                                                         observation_date="20261001")
        self.assertEqual(quality["sourceStatus"], "OK")
        self.assertEqual(len(result), 1)
        self.assertLessEqual(len(result[0]["leaders"]), 3)

    def test_theme_without_membership_is_explicitly_unavailable(self):
        row = evidence.build_theme_signals(
            "2026-10-01T10:00:00+09:00", [], {"status": "DISABLED", "themes": []}
        )
        self.assertEqual(row["status"], "UNAVAILABLE")
        self.assertEqual(row["sourceStatus"], "DISABLED")


if __name__ == "__main__":
    unittest.main()
