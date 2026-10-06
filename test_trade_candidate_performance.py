import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import trade_candidate_performance as subject


KST = ZoneInfo("Asia/Seoul")


def candidate(code="000001", primary="A", all_buckets=None):
    return {
        "rank": 1, "itemCode": code, "itemName": code,
        "primaryBucket": primary, "allBuckets": all_buckets or [primary],
        "breakoutStrength": "20", "regularSession": {"price": 100},
        "current": {"price": 101}, "afterSession": {"sessionChangeRate": 1},
        "volume": {"volumeState": "surge"}, "tags": ["AFTER_UP"],
    }


def snapshot(rows=None):
    rows = rows or [candidate()]
    return {"schemaVersion": 1, "asOf": "2026-10-06", "candidateCount": len(rows),
            "candidates": rows}


def minute(stamp, open_price, close, high=None, low=None):
    return {"localDateTime": stamp, "openPrice": open_price,
            "currentPrice": close, "highPrice": high if high is not None else close,
            "lowPrice": low if low is not None else close}


class TradeCandidatePerformanceTests(unittest.TestCase):
    def test_candidate_snapshot_is_immutable_and_idempotent(self):
        report = {"generatedAt": "t", "nextSessionCandidates": {
            "status": "AVAILABLE", "asOf": "2026-10-06", "candidateCount": 1,
            "priority": [candidate()],
        }}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = subject.persist_candidate_snapshot(report, root)
            second = subject.persist_candidate_snapshot(report, root)
            self.assertEqual(first, second)
            changed = deepcopy(report)
            changed["nextSessionCandidates"]["priority"][0]["rank"] = 2
            with self.assertRaisesRegex(ValueError, "immutable"):
                subject.persist_candidate_snapshot(changed, root)

    def test_next_krx_trading_day_skips_weekend_and_holiday(self):
        loader = lambda year: {"2026-10-05", "2026-10-07"}
        self.assertEqual(
            subject.next_krx_trading_day("2026-10-06", loader).isoformat(),
            "2026-10-08",
        )

    def test_exact_minute_timestamp_contract_and_no_fallback(self):
        rows = [
            minute("20261007090000", 100, 101),
            minute("20261007090100", 101, 999),
            minute("20261007090200", 102, 103),
            minute("20261007090400", 104, 105),
            minute("20261007090900", 106, 107),
            minute("20261007092900", 108, 109),
        ]
        got = subject.evaluate_candidate(
            {**candidate(), "candidateAsOf": "2026-10-06"}, "2026-10-07", rows
        )["performance"]
        self.assertEqual(got["priceAt1m"], 101)
        self.assertEqual(got["priceAt3m"], 103)
        self.assertEqual(got["priceAt5m"], 105)
        self.assertEqual(got["priceAt10m"], 107)
        self.assertEqual(got["priceAt30m"], 109)
        rows = [row for row in rows if row["localDateTime"] != "20261007090200"]
        rows.append(minute("20261007090300", 103, 777))
        got = subject.evaluate_candidate(
            {**candidate(), "candidateAsOf": "2026-10-06"}, "2026-10-07", rows
        )["performance"]
        self.assertIsNone(got["priceAt3m"])

    def test_returns_gap_mfe_mae_and_complete_status(self):
        rows = [
            minute("20261007090000", 110, 111, 112, 109),
            minute("20261007090200", 111, 112, 114, 110),
            minute("20261007090400", 112, 108, 113, 107),
            minute("20261007090900", 108, 115, 116, 108),
            minute("20261007092900", 115, 117, 120, 114),
            minute("20261007153000", 117, 118, 119, 106),
        ]
        got = subject.evaluate_candidate(
            {**candidate(), "candidateAsOf": "2026-10-06"}, "2026-10-07", rows
        )
        self.assertEqual(got["status"], "COMPLETE")
        self.assertEqual(got["performance"]["gapFromRegularClosePct"], 10)
        self.assertEqual(got["performance"]["return1mFromOpenPct"], 0.909091)
        self.assertEqual(got["performance"]["mfeFromOpenPct"], 9.090909)
        self.assertEqual(got["performance"]["maeFromOpenPct"], -3.636364)
        self.assertEqual(got["performance"]["closeReturnFromOpenPct"], 7.272727)

    def test_partial_pending_and_unavailable_states(self):
        partial = subject.evaluate_candidate(
            {**candidate(), "candidateAsOf": "2026-10-06"}, "2026-10-07",
            [minute("20261007090000", 100, 101)],
        )
        self.assertEqual(partial["status"], "PARTIAL")
        self.assertEqual(subject.evaluate_candidate(
            {**candidate(), "candidateAsOf": "2026-10-06"}, "2026-10-07", [], True
        )["status"], "PENDING")
        self.assertEqual(subject.evaluate_candidate(
            {**candidate(), "candidateAsOf": "2026-10-06"}, "2026-10-07", []
        )["status"], "UNAVAILABLE")

    def test_evaluation_before_next_session_does_not_call_loader(self):
        calls = []
        got = subject.evaluate_snapshot(
            snapshot(), lambda code, day: calls.append((code, day)), "2026-10-07",
            datetime(2026, 10, 6, 23, 0, tzinfo=KST),
        )
        self.assertEqual(got["status"], "PENDING")
        self.assertEqual(calls, [])

    def test_minute_source_failure_is_unavailable_not_fabricated(self):
        def failed_loader(code, day):
            raise RuntimeError("down")

        got = subject.evaluate_snapshot(
            snapshot(), failed_loader, "2026-10-07",
            datetime(2026, 10, 7, 16, 0, tzinfo=KST),
        )
        self.assertEqual(got["status"], "UNAVAILABLE")
        self.assertEqual(got["results"][0]["status"], "UNAVAILABLE")
        self.assertEqual(got["results"][0]["sourceError"], "RuntimeError")
        self.assertIsNone(got["results"][0]["performance"]["openPrice"])

    def test_primary_and_membership_summaries_are_separate(self):
        rows = [candidate("1", "A", ["A", "B"]), candidate("2", "B", ["B"])]
        artifact = subject.evaluate_snapshot(
            snapshot(rows),
            lambda code, day: [
                minute("20261007090000", 100, 101 if code == "1" else 100),
                minute("20261007090200", 100, 100),
                minute("20261007090400", 100, 100),
                minute("20261007090900", 100, 100),
                minute("20261007092900", 100, 100),
                minute("20261007153000", 100, 100),
            ],
            "2026-10-07", datetime(2026, 10, 7, 16, 0, tzinfo=KST),
        )
        summary = subject.build_summary(artifact)
        self.assertEqual(summary["summaryByPrimaryBucket"]["A"]["sampleCount"], 1)
        self.assertEqual(summary["summaryByPrimaryBucket"]["B"]["sampleCount"], 1)
        self.assertEqual(summary["summaryByAllBuckets"]["B"]["sampleCount"], 2)
        self.assertEqual(summary["summaryByAllBuckets"]["B"]["1m"]["positiveCount"], 1)
        self.assertEqual(summary["summaryByAllBuckets"]["B"]["1m"]["winRate"], 50)

    def test_zero_return_is_not_win_and_summary_is_deterministic(self):
        metric = subject.metric_summary([0, 1, -1])
        self.assertEqual(metric["positiveCount"], 1)
        self.assertEqual(metric["winRate"], 33.333333)
        rows = [candidate("2", "B"), candidate("1", "A")]
        artifact = subject.evaluate_snapshot(
            snapshot(rows), lambda code, day: [], "2026-10-07",
            datetime(2026, 10, 7, 16, 0, tzinfo=KST),
        )
        first = json.dumps(subject.build_summary(artifact), sort_keys=False)
        second = json.dumps(subject.build_summary(deepcopy(artifact)), sort_keys=False)
        self.assertEqual(first, second)

    def test_naver_minute_loader_reuses_existing_endpoint(self):
        seen = []
        rows = subject.naver_minute_rows(
            "005930", "2026-10-07", lambda url: seen.append(url) or {"datas": []}
        )
        self.assertEqual(rows, [])
        self.assertEqual(
            seen[0],
            subject.relay.MINUTE_URL.format(code="005930")
            + "?startDateTime=202610070900&endDateTime=202610071530",
        )


if __name__ == "__main__":
    unittest.main()
