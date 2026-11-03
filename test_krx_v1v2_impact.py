import json
import tempfile
import unittest
from pathlib import Path

from tools.krx_v1v2_impact import (
    KrxImpactError,
    build_krx_overlay_features,
    load_m10_entities,
)


def _entity(code="005930", category="stock"):
    return {
        "identity": f"stk_bydd_trd:{code}", "category": category, "code": code,
        "name": "삼성전자", "asof": "20261102", "close_regular": "225000",
        "official_daily_volume": "19668075", "source_api": "stk_bydd_trd",
        "MA": {"MA5": "230000", "MA20": "233225", "MA60": "250808.333333",
                "MA120": "273125", "MA240": "216006.666667"},
        "returns_pct": {f"return_{n}d_pct": "1.5" for n in (1, 5, 20, 60, 120, 240)},
        "high_breakouts": {"high_breakout_prev20": False, "high_breakout_prev60": False},
        "volume_ratio_prev20": "0.8037",
    }


class KrxV1V2ImpactTests(unittest.TestCase):
    def test_overlay_keeps_current_observation_and_separates_volume_ratio(self):
        base = [{
            "code": "005930", "name": "삼성전자", "market": "KOSPI",
            "currentPrice": 219500, "change": -3500, "changeRate": -1.57,
            "accumulatedTradingValue": 123, "snapshotGeneratedAt": "2026-11-03T08:00:00+09:00",
        }]
        m10 = {"date": "20261102", "entities": [_entity()], "source": "KRX_OPEN_API"}
        features, diagnostics = build_krx_overlay_features(
            base, m10, expected_date="20261102", current_observation_date="20261103",
            registry={"assets": [{"code": "005930", "assetType": "STOCK"}]},
        )
        row = features[0]
        self.assertEqual(row["currentPrice"], 219500)
        self.assertEqual(row["changeRate"], -1.57)
        self.assertEqual(row["ma20"], 233225)
        self.assertEqual(row["ret_1d"], 1.5)
        self.assertEqual(row["krx_volume_ratio_prev20"], 0.8037)
        self.assertIsNone(row["current_volume_ratio_20"])
        self.assertEqual(row["technicalAsOfDate"], "20261102")
        self.assertEqual(row["currentObservationDate"], "20261103")
        self.assertEqual(diagnostics["overlayCount"], 1)

    def test_etf_is_not_mixed_into_stock_overlay(self):
        base = [
            {"code": "005930", "name": "삼성전자", "market": "KOSPI"},
            {"code": "0000D0", "name": "ETF", "market": "KOSPI"},
        ]
        m10 = {"date": "20261102", "entities": [_entity(), _entity("0000D0", "etf")], "source": "KRX_OPEN_API"}
        features, diagnostics = build_krx_overlay_features(
            base, m10, expected_date="20261102", current_observation_date="20261103",
            registry={"assets": [{"code": "005930", "assetType": "STOCK"}, {"code": "0000D0", "assetType": "ETF"}]},
        )
        self.assertEqual([row["code"] for row in features], ["005930"])
        self.assertEqual(diagnostics["mixedAssetCodes"], ["0000D0"])

    def test_date_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitoring_interchange.json"
            path.write_text(json.dumps({"date": "20261103", "entities": [{"asof": "20261103", "category": "stock", "code": "005930", "identity": "x"}], "source": "KRX_OPEN_API"}), encoding="utf-8")
            with self.assertRaisesRegex(KrxImpactError, "TARGET_DATE"):
                load_m10_entities(path, expected_date="20261102")

    def test_missing_m10_code_is_reported_without_fallback(self):
        base = [{"code": "005930", "name": "삼성전자", "market": "KOSPI"}]
        m10 = {"date": "20261102", "entities": [], "source": "KRX_OPEN_API"}
        features, diagnostics = build_krx_overlay_features(
            base, m10, expected_date="20261102", current_observation_date="20261103"
        )
        self.assertEqual(features, [])
        self.assertEqual(diagnostics["missingCodes"], ["005930"])


if __name__ == "__main__":
    unittest.main()
