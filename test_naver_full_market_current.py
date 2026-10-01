import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "full_current", Path(__file__).parent / "tools" / "naver_full_market_current.py"
)
current = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(current)


def raw(code, name=None, **extra):
    row = {
        "itemcode": code, "itemname": name or f"종목{code}", "nowPrice": "100",
        "prevChangePrice": "2", "prevChangeRate": "2.04", "openPrice": "99",
        "highPrice": "101", "lowPrice": "98", "tradeVolume": "1000",
        "tradeAmount": "100000", "marketSum": "999999", "week52HighPrice": "120",
        "week52LowPrice": "80", "marketStatus": "OPEN", "tradingSessionType": "REGULAR",
        "tradeStopYn": "N", "tradableStatus": "TRADABLE",
        "tradableStatusUpdatedAt": "2026-10-01T10:00:00+09:00",
    }
    row.update(extra)
    return row


def paged(pages):
    def fetch(market, index, _size):
        return pages[market][index]
    return fetch


class FullMarketCurrentTests(unittest.TestCase):
    expected = {"KOSPI": {"005930", "0088M0"}, "KOSDAQ": {"000660", "123456"}}

    def test_normal_two_market_pagination_and_schema(self):
        fetch = paged({
            "KOSPI": [[raw("005930"), raw("0088M0")], []],
            "KOSDAQ": [[raw("000660"), raw("123456")], []],
        })
        result = current.collect_attempt(self.expected, fetch, page_size=2, sleep_seconds=0,
                                         snapshot_generated_at="2026-10-01T10:00:00+09:00")
        self.assertTrue(result["valid"])
        self.assertEqual(result["count"], 4)
        self.assertEqual(result["stocks"][0]["snapshotGeneratedAt"], "2026-10-01T10:00:00+09:00")
        self.assertNotIn("sourceTime", result["stocks"][0])
        self.assertEqual(result["stocks"][0]["code"], "000660")

    def test_same_count_missing_and_extra_is_failure(self):
        fetch = paged({
            "KOSPI": [[raw("005930"), raw("0088M0")], []],
            "KOSDAQ": [[raw("000660"), raw("999999")], []],
        })
        result = current.collect_attempt(self.expected, fetch, 2, 0)
        self.assertFalse(result["valid"])
        self.assertEqual(result["count"], 4)
        self.assertEqual(result["missingCodes"], ["123456"])
        self.assertEqual(result["extraCodes"], ["999999"])

    def test_duplicate_and_page_boundary_duplicate_fail(self):
        fetch = paged({
            "KOSPI": [[raw("005930"), raw("0088M0")], []],
            "KOSDAQ": [[raw("000660"), raw("000660")], []],
        })
        result = current.collect_attempt(self.expected, fetch, 2, 0)
        self.assertFalse(result["valid"])
        self.assertEqual(result["duplicateCodes"], ["000660"])

    def test_whole_snapshot_retry_discards_bad_first_attempt(self):
        calls = {"attempt": 0}
        def fetch(market, index, size):
            if market == "KOSPI" and index == 0:
                calls["attempt"] += 1
            if calls["attempt"] == 1:
                pages = {"KOSPI": [[raw("005930"), raw("0088M0")], []],
                         "KOSDAQ": [[raw("000660"), raw("999999")], []]}
            else:
                pages = {"KOSPI": [[raw("005930"), raw("0088M0")], []],
                         "KOSDAQ": [[raw("000660"), raw("123456")], []]}
            return pages[market][index]
        with tempfile.TemporaryDirectory() as directory:
            universe = Path(directory) / "universe.json"
            universe.write_text(json.dumps({"assets": [
                {"code": code, "assetType": "STOCK", "market": market}
                for market, codes in self.expected.items() for code in codes]}), encoding="utf-8")
            snapshot = current.collect_snapshot(universe, fetch, 2, 3, 0,
                                                lambda: "2026-10-01T10:00:00+09:00")
        self.assertEqual(snapshot["status"], "SUCCESS")
        self.assertEqual(snapshot["attemptCount"], 2)
        self.assertEqual(len(snapshot["attemptDiagnostics"]), 2)

    def test_all_retries_fail_and_publish_preserves_existing_snapshot(self):
        def fetch(market, index, size):
            pages = {"KOSPI": [[raw("005930"), raw("0088M0")], []],
                     "KOSDAQ": [[raw("000660"), raw("999999")], []]}
            return pages[market][index]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            universe = root / "universe.json"
            universe.write_text(json.dumps({"assets": [
                {"code": code, "assetType": "STOCK", "market": market}
                for market, codes in self.expected.items() for code in codes]}), encoding="utf-8")
            snapshot = current.collect_snapshot(universe, fetch, 2, 3, 0,
                                                lambda: "2026-10-01T10:00:00+09:00")
            output = root / "stocks-current.json"
            output.write_text('{"status":"SUCCESS","sentinel":true}\n', encoding="utf-8")
            diagnostic = root / "stocks-current-error.json"
            current.publish(snapshot, output, diagnostic)
            self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["sentinel"])
            self.assertEqual(json.loads(diagnostic.read_text(encoding="utf-8"))["status"], "FAILURE")

    def test_halted_and_zero_volume_are_preserved(self):
        row = raw("005930", tradeStopYn="Y", tradableStatus="HALTED", openPrice="0",
                  highPrice="0", lowPrice="0", tradeVolume="0")
        item, malformed = current.normalize(row, "KOSPI", "2026-10-01T10:00:00+09:00")
        self.assertEqual(malformed, [])
        self.assertEqual(item["tradeStopYn"], "Y")
        self.assertEqual(item["accumulatedTradingVolume"], 0)
        self.assertEqual(item["openPrice"], 0)


if __name__ == "__main__":
    unittest.main()
