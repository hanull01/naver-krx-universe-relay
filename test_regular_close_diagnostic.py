import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import relay
from tools.regular_close_diagnostic import (
    build_record,
    extract_integration_last_close,
    save_record,
)


class RegularCloseDiagnosticTests(unittest.TestCase):
    def test_explicit_integration_field_is_last_close_price(self):
        payload = {"totalInfos": [{"code": "lastClosePrice", "key": "전일", "value": "222,000"}]}
        self.assertEqual(extract_integration_last_close(payload), 222000)

    def test_day_close_difference_is_observed_not_used_as_regular_close(self):
        minute = [{"localDateTime": "20261028153000", "currentPrice": 242000}]
        day = [{"localDate": "20261028", "closePrice": 245000}]
        record = build_record("005930", "20261028", minute, day,
                              "2026-10-28T20:10:00+09:00")
        self.assertEqual(record["regularClose"], 242000)
        self.assertEqual(record["dayClose"], 245000)
        self.assertEqual(record["dayCloseDifference"], 3000)
        self.assertEqual(record["dayCloseComparison"], "DAY_CLOSE_MAY_INCLUDE_AFTER_MARKET")
        self.assertEqual(record["verificationStatus"], "VERIFIED")

    def test_missing_1530_is_unavailable_without_imputation(self):
        record = build_record(
            "005930", "20261028",
            [{"localDateTime": "20261028152900", "currentPrice": 241900}],
            [{"localDate": "20261028", "closePrice": 245000}],
        )
        self.assertIsNone(record["regularClose"])
        self.assertEqual(record["verificationStatus"], "UNAVAILABLE")
        self.assertEqual(record["failureReason"], "EXACT_1530_UNAVAILABLE")

    def test_existing_builder_reproduces_after_market_false_promotion(self):
        current = datetime(2026, 10, 28, 20, 1, tzinfo=relay.KST)
        daily = {"datas": [{"date": "2026-10-28", "open": 231000,
                            "high": 247000, "low": 228000, "close": 245000,
                            "volume": 17720841, "complete": False,
                            "noTrading": False}]}
        realtime = {"datas": [{"itemCode": "005930", "closePrice": "245,000",
                                "marketStatus": "CLOSE",
                                "marketStatusDetailType": "close",
                                "localTradedAt": "2026-10-28T20:00:00+09:00",
                                "marketSessionType": "afterMarket",
                                "stockExchangeType": {"delayTime": 0, "endTime": "1530",
                                                       "closePriceSendTime": "1630"}}]}
        bar = relay.build_verified_regular_daily_bar(daily, realtime, current, "005930")
        self.assertEqual(bar["close"], 245000)
        self.assertEqual(bar["session"], "REGULAR")
        self.assertEqual(bar["sourceTime"], "2026-10-28T20:00:00+09:00")
        self.assertEqual(realtime["datas"][0]["marketSessionType"], "afterMarket")

    def test_save_isolated_diagnostic_area(self):
        record = build_record(
            "005930", "20261028",
            [{"localDateTime": "20261028153000", "currentPrice": 242000}],
            collected_at="2026-10-28T20:10:00+09:00",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = save_record(directory, record)
            self.assertEqual(path, Path(directory) / "data/daily-regular-diagnostic/20261028_005930.json")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["regularCloseBasisTimestamp"], "2026-10-28T15:30:00+09:00")
            self.assertFalse((Path(directory) / "data/daily").exists())
            self.assertFalse((Path(directory) / "data/monitoring-daily").exists())


if __name__ == "__main__":
    unittest.main()
