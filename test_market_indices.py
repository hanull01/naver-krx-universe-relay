import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import market_indices


class MarketIndicesTests(unittest.TestCase):
    @staticmethod
    def final_window_payload(session="POST_MARKET_OR_CLOSED", generated="2026-10-26T20:30:02+09:00",
                             kosdaq_date="2026-10-26T20:15:00+09:00"):
        return {
            "status": "OK", "source": "NAVER_MOBILE_INDEX_BASIC",
            "generatedAt": generated,
            "indices": [
                {"code": "KOSPI", "marketStatus": "CLOSE", "session": session,
                 "sourceTime": "2026-10-26T20:15:00+09:00"},
                {"code": "KOSDAQ", "marketStatus": "CLOSE", "session": session,
                 "sourceTime": kosdaq_date},
            ],
        }

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

    def test_final_payload_accepts_current_closed_same_day_indices(self):
        result = market_indices.validate_final_payload(
            self.final_window_payload(),
            "2026-10-26T20:30:00+09:00", "2026-10-26T20:30:05+09:00")
        self.assertEqual(result["targetDate"], "20261026")

    def test_final_payload_accepts_microsecond_generation_with_precise_window(self):
        result = market_indices.validate_final_payload(
            self.final_window_payload(generated="2026-10-26T20:30:05.123456+09:00"),
            "2026-10-26T20:30:00.000000+09:00",
            "2026-10-26T20:30:05.654321+09:00")
        self.assertEqual(result["targetDate"], "20261026")

    def test_final_payload_rejects_intraday_index_even_when_date_is_today(self):
        payload = self.final_window_payload(session="REGULAR")
        with self.assertRaisesRegex(ValueError, "INDEX_SESSION_NOT_CLOSED"):
            market_indices.validate_final_payload(
                payload, "2026-10-26T20:30:00+09:00", "2026-10-26T20:30:05+09:00")

    def test_final_payload_rejects_stale_previous_indices_file(self):
        payload = self.final_window_payload(generated="2026-10-25T20:30:02+09:00")
        with self.assertRaisesRegex(ValueError, "INDEX_GENERATED_AT_STALE"):
            market_indices.validate_final_payload(
                payload, "2026-10-26T20:30:00+09:00", "2026-10-26T20:30:05+09:00")

    def test_final_payload_rejects_mixed_index_source_dates(self):
        payload = self.final_window_payload(kosdaq_date="2026-10-25T20:15:00+09:00")
        with self.assertRaisesRegex(ValueError, "INDEX_SOURCE_DATE_MISMATCH"):
            market_indices.validate_final_payload(
                payload, "2026-10-26T20:30:00+09:00", "2026-10-26T20:30:05+09:00")

    def test_final_payload_rejects_previous_trading_date_in_current_window(self):
        payload = self.final_window_payload(
            generated="2026-10-26T20:30:02+09:00",
            kosdaq_date="2026-10-25T20:15:00+09:00")
        payload["indices"][0]["sourceTime"] = "2026-10-25T20:15:00+09:00"
        with self.assertRaisesRegex(ValueError, "INDEX_SOURCE_DATE_GENERATED_DATE_MISMATCH"):
            market_indices.validate_final_payload(
                payload, "2026-10-26T20:30:00+09:00", "2026-10-26T20:30:05+09:00")

    def test_cli_returns_failure_for_partial_collection(self):
        payload = {"status": "PARTIAL", "indices": [], "errors": [{"code": "KOSDAQ"}]}
        with patch.object(market_indices, "collect", return_value=payload), \
             patch.object(market_indices, "write"), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(market_indices.main(), 1)

    def test_cli_returns_success_only_for_complete_collection(self):
        payload = {"status": "OK", "indices": [{"code": "KOSPI"}, {"code": "KOSDAQ"}], "errors": []}
        with patch.object(market_indices, "collect", return_value=payload), \
             patch.object(market_indices, "write"), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(market_indices.main(), 0)


if __name__ == "__main__":
    unittest.main()
