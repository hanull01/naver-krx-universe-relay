import importlib.util
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "market_universe_builder", Path(__file__).parent / "tools" / "naver_market_universe_builder.py"
)
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def stock(code, name, sosok, stock_type="ST"):
    return {"itemcode": code, "itemname": name, "sosok": sosok, "type": stock_type,
            "tradableStatus": "tradable", "tradableStatusCode": "ok", "tradeStopYn": "N"}


def etf(code, name, market="KOSPI"):
    return {"itemCode": code, "itemName": name, "marketType": market, "etfType": "국내주식형",
            "krx": {"tradableStatus": "tradable", "tradableStatusCode": "ok"}}


def etn(code, name):
    return {"itemcode": code, "itemname": name, "tradableStatus": "tradable"}


class UniverseBuilderTests(unittest.TestCase):
    def test_stock_page_index_market_split_alphanumeric_spac_and_preferred(self):
        pages = {
            ("KOSPI", 0): [stock("005930", "삼성전자", "0"), stock("0088M0", "테스트스팩", "0")],
            ("KOSPI", 1): [],
            ("KOSDAQ", 0): [stock("33626K", "영문코드", "1"), stock("123456", "테스트우", "1")],
            ("KOSDAQ", 1): [],
        }
        calls = []
        def fetch(market, page, size, timeout, retries):
            calls.append((market, page, size))
            return pages[(market, page)]
        payload = builder.collect_stocklist(fetch, page_size=2, sleep_seconds=0)
        self.assertEqual(calls, [("KOSPI", 0, 2), ("KOSPI", 1, 2),
                                 ("KOSDAQ", 0, 2), ("KOSDAQ", 1, 2)])
        self.assertEqual(payload["metadata"]["markets"], {"KOSPI": 2, "KOSDAQ": 2})
        rows = {item["code"]: item for item in payload["stocks"]}
        self.assertEqual(rows["0088M0"]["assetType"], "STOCK")
        self.assertIn("SPAC", rows["0088M0"]["tags"])
        self.assertIn("PREFERRED_LIKE", rows["123456"]["tags"])

    def test_stock_failure_duplicate_missing_and_market_mismatch_are_visible(self):
        pages = {
            ("KOSPI", 0): [stock("000001", "정상", "0"), stock("000002", "불일치", "1"),
                            {"itemcode": "000003", "sosok": "0"}],
            ("KOSPI", 1): [],
            ("KOSDAQ", 0): [stock("000001", "중복", "1"), stock("000004", "다른종목", "1"),
                             stock("000005", "또다른종목", "1")],
        }
        def fetch(market, page, *_):
            if market == "KOSDAQ" and page == 1:
                raise RuntimeError("network down")
            return pages[(market, page)]
        payload = builder.collect_stocklist(fetch, page_size=3, sleep_seconds=0)
        failures = payload["metadata"]["failures"]
        self.assertEqual(payload["metadata"]["failedCount"], 4)
        self.assertIn("KOSPI:000002", failures)
        self.assertIn("KOSPI:000003", failures)
        self.assertIn("KOSDAQ:000001", failures)
        self.assertIn("KOSDAQ:startIdx=1", failures)

    def test_etf_and_etn_are_preserved_in_separate_authoritative_collections(self):
        etf_pages = {
            0: {"items": [etf("102110", "TIGER 200"), etf("123450", "ETF 두번째")]},
            1: {"items": []},
        }
        etn_pages = {0: [etn("530107", "삼성 ETN"), etn("530108", "다른 ETN")], 1: []}
        etfs = builder.collect_etfs(lambda page, *_: etf_pages[page], page_size=2, sleep_seconds=0)
        etns = builder.collect_etns(lambda page, *_: etn_pages[page], page_size=2, sleep_seconds=0)
        self.assertEqual([item["assetType"] for item in etfs["etfs"]], ["ETF", "ETF"])
        self.assertEqual([item["assetType"] for item in etns["etns"]], ["ETN", "ETN"])
        self.assertEqual(etfs["metadata"]["sourceApiPath"], builder.ETF_PATH)
        self.assertEqual(etns["metadata"]["sourceApiPath"], builder.ETN_PATH)

    def test_authoritative_result_fails_closed_when_stock_list_is_incomplete(self):
        payload = {"metadata": {"failedCount": 0, "markets": {"KOSPI": 10, "KOSDAQ": 10}}}
        with self.assertRaises(builder.UniverseSourceError):
            builder.ensure_complete(payload)
        payload["metadata"]["markets"] = {"KOSPI": 600, "KOSDAQ": 1100}
        builder.ensure_complete(payload)
        with self.assertRaises(builder.UniverseSourceError):
            builder.ensure_assets_complete({"metadata": {"source": "NAVER_ETF_LIST", "failedCount": 1,
                                                            "resolvedCount": 20}})

    def test_industry_membership_remains_explicit_non_authoritative_fallback(self):
        candidates = builder.discover_candidates({"items": [
            {"code": "005930", "name": "삼성전자"}, {"itemCode": "000660", "stockName": "SK하이닉스"},
        ]})
        self.assertEqual(candidates[0]["code"], "000660")


if __name__ == "__main__":
    unittest.main()
