import unittest

from tools.krx_m10_market_aggregates import build_m10_market_aggregates, safe_number


def entity(code, category, close, ma20, ma60, ma120, ret, volume, high20=False, high60=False):
    return {
        "code": code, "name": code, "category": category,
        "identity": f"test:{code}", "asof": "20261102", "source_api": "test",
        "close_regular": close, "MA": {"MA20": ma20, "MA60": ma60, "MA120": ma120},
        "returns_pct": {"return_1d_pct": ret},
        "high_breakouts": {"high_breakout_prev20": high20, "high_breakout_prev60": high60},
        "volume_ratio_prev20": volume,
    }


class KrxM10AggregateTests(unittest.TestCase):
    def test_safe_number_preserves_null_and_numeric_precision(self):
        self.assertIsNone(safe_number(None))
        self.assertEqual(safe_number("210000"), 210000)
        self.assertEqual(safe_number("210000.125"), 210000.125)
        self.assertIsNone(safe_number("not-a-number"))

    def test_market_counts_are_separated_and_denominators_are_valid_rows(self):
        payload = {
            "schema": "krx_monitoring_interchange_v1", "date": "20261102",
            "entities": [
                entity("000001", "stock", "110", "100", "90", "80", "2.5", "1.5", True, True),
                entity("000002", "stock", "90", "100", None, "80", "-1.0", "0.5"),
                entity("ETF001", "etf", "100", "100", "100", "100", "0", "2.0"),
                {"code": "KOSPI", "name": "KOSPI", "category": "index", "identity": "kospi:KOSPI", "source_api": "kospi_dd_trd", "asof": "20261102"},
            ],
        }
        result = build_m10_market_aggregates(payload)
        stock = result["markets"]["STOCK"]
        etf = result["markets"]["ETF"]
        total = result["markets"]["TOTAL"]
        self.assertEqual(stock["entityCount"], 2)
        self.assertEqual(stock["advancers"], 1)
        self.assertEqual(stock["decliners"], 1)
        self.assertEqual(stock["unchanged"], 0)
        self.assertEqual(stock["aboveMA20"], {"count": 1, "eligibleCount": 2, "pct": 50.0})
        self.assertEqual(stock["volumeAbove20DayAverage"], {"count": 1, "eligibleCount": 2, "pct": 50.0})
        self.assertEqual(etf["entityCount"], 1)
        self.assertEqual(etf["unchanged"], 1)
        self.assertEqual(total["entityCount"], 3)
        self.assertEqual(result["indexCount"], 1)
        self.assertEqual(result["indicatorAvailability"]["relativeStrength"], "UNAVAILABLE_M10_FIELD_MISSING")
        self.assertEqual(result["indicatorAvailability"]["newLow"], "UNAVAILABLE_M10_FIELD_MISSING")
        self.assertEqual(result["markets"]["STOCK"]["leaderCandidates"][0]["code"], "000001")


if __name__ == "__main__":
    unittest.main()
