import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "daily_backfill", Path(__file__).parent / "tools" / "naver_daily_backfill.py"
)
backfill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backfill)


class DailyBackfillUniverseTests(unittest.TestCase):
    def test_codes_keep_alphanumeric_and_reject_non_naver_characters(self):
        self.assertEqual(backfill.parse_codes("005930,0088M0,33626K,005930"),
                         ["005930", "0088M0", "33626K"])
        with self.assertRaises(ValueError):
            backfill.parse_codes("005930,00-001")

    def test_authoritative_universe_filters_asset_type_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "all-assets.json"
            path.write_text(json.dumps({"assets": [
                {"code": "005930", "assetType": "STOCK", "market": "KOSPI"},
                {"code": "005930", "assetType": "STOCK", "market": "KOSPI"},
                {"code": "102110", "assetType": "ETF", "market": "KOSPI"},
                {"code": "530107", "assetType": "ETN", "market": "KOSPI"},
                {"code": "0088M0", "assetType": "STOCK", "market": "KOSPI"},
            ]}), encoding="utf-8")
            stock = backfill.load_universe_targets(path, "stock")
            self.assertEqual([item["code"] for item in stock], ["005930", "0088M0"])
            self.assertEqual([item["code"] for item in backfill.load_universe_targets(path, "etf")], ["102110"])
            self.assertEqual(len(backfill.load_universe_targets(path, "all")), 4)

    def test_invalid_universe_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "universe.json"
            path.write_text(json.dumps({"assets": [{"code": "bad-code", "assetType": "STOCK"}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                backfill.load_universe_targets(path)

    def test_checkpoint_uses_historical_dates_not_run_clock_time(self):
        start_a = datetime(2023, 10, 1, 9, 1, tzinfo=timezone.utc)
        end_a = datetime(2026, 10, 1, 9, 1, tzinfo=timezone.utc)
        start_b = datetime(2023, 10, 1, 22, 59, tzinfo=timezone.utc)
        end_b = datetime(2026, 10, 1, 22, 59, tzinfo=timezone.utc)
        self.assertEqual(backfill.checkpoint_key("0088M0", start_a, end_a),
                         backfill.checkpoint_key("0088M0", start_b, end_b))
        self.assertNotEqual(backfill.checkpoint_key("0088M0", start_a, end_a, "ETF"),
                            backfill.checkpoint_key("0088M0", start_a, end_a, "STOCK"))

    def test_canonical_rows_preserve_asset_market_and_observed_dates_only(self):
        rows = [{"localDate": "20260928", "openPrice": "10", "highPrice": "12", "lowPrice": "9",
                 "closePrice": "11", "accumulatedTradingVolume": "100", "foreignRetentionRate": "2.3"}]
        canonical = backfill.canonicalize(rows, "0088M0", "STOCK", "KOSPI")
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0]["assetType"], "STOCK")
        self.assertEqual(canonical[0]["market"], "KOSPI")
        self.assertEqual(canonical[0]["date"], "20260928")

    def test_zero_volume_zero_ohl_close_is_preserved_but_feature_invalid(self):
        row = {"localDate": "20260907", "openPrice": 0, "highPrice": 0, "lowPrice": 0,
               "closePrice": 1310, "accumulatedTradingVolume": 0}
        canonical = backfill.canonicalize([row], "000040")
        self.assertEqual(canonical[0]["close"], 1310)
        self.assertEqual(canonical[0]["open"], 0)
        self.assertFalse(canonical[0]["feature_valid"])
        self.assertIn("zero_volume_zero_ohl_with_close", canonical[0]["validation_reasons"])

    def test_low_inconsistency_is_preserved_but_feature_invalid(self):
        row = {"localDate": "20250827", "openPrice": 1542, "highPrice": 1558, "lowPrice": 1540,
               "closePrice": 1539, "accumulatedTradingVolume": 13543}
        canonical = backfill.canonicalize([row], "050760")
        self.assertEqual(canonical[0]["low"], 1540)
        self.assertFalse(canonical[0]["feature_valid"])
        self.assertEqual(canonical[0]["validation_reasons"], ["lowPrice inconsistent"])

    def test_mixed_quality_rows_remain_collection_valid(self):
        rows = [
            {"localDate": "20260901", "openPrice": 10, "highPrice": 12, "lowPrice": 9,
             "closePrice": 11, "accumulatedTradingVolume": 100},
            {"localDate": "20260902", "openPrice": 0, "highPrice": 0, "lowPrice": 0,
             "closePrice": 12, "accumulatedTradingVolume": 0},
        ]
        validation = backfill.validate_rows(rows)
        self.assertTrue(validation["collection_valid"])
        self.assertEqual(validation["invalid_row_count"], 1)
        self.assertEqual(len(backfill.canonicalize(rows, "005930")), 2)

    def test_success_checkpoint_without_canonical_is_not_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(backfill.checkpoint_is_resumable(
                {"status": "SUCCESS"}, Path(directory) / "missing.jsonl"))

    def test_parseable_nonempty_canonical_is_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "005930.jsonl"
            path.write_text('{"code":"005930","date":"20260901"}\n', encoding="utf-8")
            self.assertTrue(backfill.canonical_file_is_usable(path))
            self.assertTrue(backfill.checkpoint_is_resumable({"status": "SUCCESS"}, path))

    def test_collection_writes_canonical_when_one_row_is_feature_invalid(self):
        payload = {"priceInfos": [
            {"localDate": "20260901", "openPrice": 10, "highPrice": 12, "lowPrice": 9,
             "closePrice": 11, "accumulatedTradingVolume": 100},
            {"localDate": "20260902", "openPrice": 0, "highPrice": 0, "lowPrice": 0,
             "closePrice": 12, "accumulatedTradingVolume": 0},
        ]}
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(backfill, "fetch", return_value={
                "url": "https://example.invalid", "status": 200,
                "raw": json.dumps(payload).encode("utf-8"), "elapsed_ms": 1.0, "attempt": 1,
            }):
                result = backfill.collect_one(
                    "005930", datetime(2023, 1, 1, tzinfo=timezone.utc),
                    datetime(2026, 1, 1, tzinfo=timezone.utc), Path(directory),
                    timeout=1, retries=1, request_sleep=0, dry_run=False,
                )
            self.assertEqual(result["collection_status"], "SUCCESS")
            self.assertTrue(result["canonical_written"])
            self.assertEqual(result["invalid_row_count"], 1)
            rows = [json.loads(line) for line in (Path(directory) / "canonical" / "daily" / "005930.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertFalse(rows[1]["feature_valid"])

    def test_recovery_uses_existing_raw_without_fetching(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_path = root / "raw" / "20261001" / "000040" / "snapshot.json"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_text(json.dumps({"metadata": {"collection_status": "SUCCESS", "collected_at": "2026-10-01T10:00:00+09:00"}, "response": {"priceInfos": [{"localDate": "20260907", "openPrice": 0, "highPrice": 0, "lowPrice": 0, "closePrice": 1310, "accumulatedTradingVolume": 0}]}}), encoding="utf-8")
            with patch.object(backfill, "fetch") as fetch:
                result = backfill.recover_missing_canonical(root, [{"code": "000040", "assetType": "STOCK", "market": "KOSPI"}])
            self.assertEqual(len(result["recovered"]), 1)
            fetch.assert_not_called()
            row = json.loads((root / "canonical" / "daily" / "000040.jsonl").read_text(encoding="utf-8"))
            self.assertFalse(row["feature_valid"])


if __name__ == "__main__":
    unittest.main()
