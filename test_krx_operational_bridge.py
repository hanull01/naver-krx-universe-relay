import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import relay
from tools.krx_operational_bridge import KrxOperationalBridgeError, load_krx_technicals


def _row(code="005930"):
    return {
        "itemCode": code,
        "stockName": "삼성전자",
        "asOfDate": "2026-11-02",
        "technicals": {
            "ma5": "230000", "ma20": "233225", "ma60": "250808.33333333334",
            "ma120": None, "ma240": None, "return1dPct": "-3.0172",
            "return5dPct": None, "return20dPct": "1.2", "return60dPct": None,
            "return120dPct": None, "return240dPct": None,
            "highBreakoutPrev20": False, "highBreakoutPrev60": None,
            "volumeRatio20": "1.45",
        },
        "regularSession": {"close": 225000, "volume": 19668075},
        "provenance": {"sourceStaging": "KRX_M10_GENERATION", "sourceCanonicalSha256": "a" * 64},
    }


class KrxOperationalBridgeTests(unittest.TestCase):
    def _write(self, payload):
        root = Path(tempfile.mkdtemp())
        (root / "technicals.json").write_text(json.dumps(payload), encoding="utf-8")
        return root

    def test_projects_krx_rows_without_recomputing_or_fallback(self):
        root = self._write({
            "source": "KRX_M10_GENERATION", "targetDate": "20261102",
            "historicalIndicatorSource": "KRX", "historicalFallbackAllowed": False,
            "counts": {"instrumentCount": 1}, "datas": [_row()],
        })
        result = load_krx_technicals(root)
        row = result["datas"][0]
        self.assertEqual(row["ma20"], 233225)
        self.assertEqual(row["ma60"], 250808.33333333334)
        self.assertEqual(row["return1dPct"], -3.0172)
        self.assertIsNone(row["ma120"])
        self.assertEqual(row["regularClose"], 225000)
        self.assertEqual(result["historicalIndicatorSource"], "KRX")

    def test_naver_fallback_and_duplicate_rows_are_rejected(self):
        base = {
            "source": "KRX_M10_GENERATION", "targetDate": "20261102",
            "historicalIndicatorSource": "KRX", "historicalFallbackAllowed": False,
            "counts": {"instrumentCount": 2}, "datas": [_row(), _row()],
        }
        root = self._write(base)
        with self.assertRaisesRegex(KrxOperationalBridgeError, "DUPLICATE"):
            load_krx_technicals(root)
        base["historicalFallbackAllowed"] = True
        base["counts"]["instrumentCount"] = 1
        base["datas"] = [_row()]
        root = self._write(base)
        with self.assertRaisesRegex(KrxOperationalBridgeError, "FALLBACK"):
            load_krx_technicals(root)

    def test_explicit_relay_path_uses_krx_and_never_reads_naver_history(self):
        root = self._write({
            "source": "KRX_M10_GENERATION", "targetDate": "20261102",
            "historicalIndicatorSource": "KRX", "historicalFallbackAllowed": False,
            "counts": {"instrumentCount": 1}, "datas": [_row()],
        })
        universe = {
            "stocks": [{"itemCode": "005930", "stockName": "삼성전자", "enabled": True}],
            "sectors": {}, "themes": {}, "watchlists": {}, "leaders": {},
        }
        quotes = {"status": "ok", "count": 1, "expectedCount": 1, "datas": [{
            "itemCode": "005930", "closePrice": 230500, "highPrice": 231000,
            "fluctuationsRatio": 1.0, "sourceTime": "2026-11-03T08:00:00+09:00",
            "accumulatedTradingVolume": 100, "accumulatedTradingValue": 200,
        }]}
        def adapter_only(code, verified_bar=None, daily_adapter=None):
            self.assertIsNotNone(daily_adapter)
            return daily_adapter(code)

        with patch.object(relay, "collect_quotes", return_value=quotes), \
             patch.object(relay, "universe_state", return_value=(universe, ["005930"], {})), \
             patch.object(relay, "load_analysis_config", return_value={
                 "nearPct": 2, "volumeElevated": 1.2, "volumeSurge": 1.5,
                 "groupBroadRatio": 0.7, "groupModerateRatio": 0.4,
             }), \
             patch.object(relay, "save"), \
             patch.object(relay, "load_daily_for_technical", side_effect=adapter_only):
            failed = relay.run_quote_pipeline(root)
        self.assertFalse(failed)


if __name__ == "__main__":
    unittest.main()
