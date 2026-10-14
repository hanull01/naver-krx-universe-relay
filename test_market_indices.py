import unittest

import market_indices


class MarketIndicesTests(unittest.TestCase):
    def test_collects_official_index_fields(self):
        payloads = {
            "KOSPI": {"stockName": "코스피", "closePrice": "2,500.10", "compareToPreviousClosePrice": "12.34", "fluctuationsRatio": "0.50", "marketStatus": "CLOSE", "localTradedAt": "2026-10-14T20:15:00+09:00"},
            "KOSDAQ": {"stockName": "코스닥", "closePrice": "700.20", "compareToPreviousClosePrice": "-3.40", "fluctuationsRatio": "-0.48", "marketStatus": "CLOSE", "localTradedAt": "2026-10-14T20:15:00+09:00"},
        }
        result = market_indices.collect(lambda url: payloads[url.rsplit("/", 2)[-2]])
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["indices"][0]["changePoints"], 12.34)
        self.assertEqual(result["indices"][1]["session"], "POST_MARKET_OR_CLOSED")

    def test_missing_index_is_partial_without_fabricated_value(self):
        def fetcher(url):
            if "KOSDAQ" in url:
                raise TimeoutError("fixture")
            return {"stockName": "코스피", "closePrice": "2500", "compareToPreviousClosePrice": "1", "fluctuationsRatio": "0.1", "marketStatus": "OPEN", "localTradedAt": "2026-10-14T10:00:00+09:00"}
        result = market_indices.collect(fetcher)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(len(result["indices"]), 1)
        self.assertEqual(result["errors"][0]["code"], "KOSDAQ")


if __name__ == "__main__":
    unittest.main()
