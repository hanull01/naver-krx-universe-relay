import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import daily_report
from tools import daily_monitoring_baseline
from tools import final_daily_history
from tools import full_market_monitoring_evidence
from tools import monitoring_daily_state_update
from tools import naver_full_market_current
from tools import trading_session_diagnostic


TARGET = "20261026"
PREVIOUS = "20261023"
ASSETS = [
    {"code": "000001", "name": "테스트KOSPI", "market": "KOSPI", "assetType": "STOCK"},
    {"code": "000002", "name": "테스트KOSDAQ", "market": "KOSDAQ", "assetType": "STOCK"},
]
EXPECTED = {"KOSPI": {"000001"}, "KOSDAQ": {"000002"}}


def raw(code, market_status="CLOSED", **changes):
    row = {
        "itemcode": code,
        "itemname": f"테스트{code}",
        "sosok": "1" if code == "000002" else "0",
        "nowPrice": "100",
        "prevChangePrice": "2",
        "prevChangeRate": "2.04",
        "openPrice": "99",
        "highPrice": "101",
        "lowPrice": "98",
        "tradeVolume": "1000",
        "tradeAmount": "100000",
        "marketSum": "999999",
        "week52HighPrice": "120",
        "week52LowPrice": "80",
        "marketStatus": market_status,
        "tradingSessionType": "AFTER_MARKET",
        "tradeStopYn": "N",
        "tradableStatus": "TRADABLE",
        "localDate": TARGET,
    }
    row.update(changes)
    return row


def fetch_pages(rows):
    pages = {"KOSPI": [[rows["000001"]], []], "KOSDAQ": [[rows["000002"]], []]}

    def fetch(market, index, _size):
        return pages[market][index]

    return fetch


def initial_state(market, code):
    return {
        "schemaVersion": 1,
        "format": "COLUMN_ARRAY_V1",
        "market": market,
        "retainedObservations": 301,
        "publicationStatus": "FINAL",
        "asOfDate": PREVIOUS,
        "stocks": [{
            "code": code,
            "dates": [PREVIOUS],
            "close": [90],
            "high": [91],
            "volume": [900],
            "validity": [0],
        }],
    }


class OperationalSnapshotPipelineTests(unittest.TestCase):
    @contextmanager
    def _collect(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            universe = Path(directory) / "universe.json"
            universe.write_text(json.dumps({"assets": ASSETS}), encoding="utf-8")
            snapshot = naver_full_market_current.collect_snapshot(
                universe, fetch_pages(rows), page_size=1, max_attempts=1,
                sleep_seconds=0, clock=lambda: "2026-10-26T20:30:00+09:00",
            )
            yield Path(directory), universe, snapshot

    def test_normalized_naver_snapshot_reaches_final_evidence_and_report_ready(self):
        rows = {"000001": raw("000001"), "000002": raw("000002")}
        with self._collect(rows) as (root, universe, snapshot):
            self.assertEqual(snapshot["status"], "SUCCESS")
            self.assertEqual({row["assetType"] for row in snapshot["stocks"]}, {"STOCK"})

            candidate = final_daily_history.monitoring_daily_payload(
                snapshot, {"asOfDate": PREVIOUS, "historyStatus": "OK"},
                expected_codes=[asset["code"] for asset in ASSETS],
            )
            self.assertEqual(candidate["status"], "READY")

            state_dir = root / "monitoring-daily"
            state_dir.mkdir()
            (state_dir / "kospi.json").write_text(
                json.dumps(initial_state("KOSPI", "000001")), encoding="utf-8")
            (state_dir / "kosdaq.json").write_text(
                json.dumps(initial_state("KOSDAQ", "000002")), encoding="utf-8")
            incoming = monitoring_daily_state_update.normalize_daily_rows(
                {"rows": candidate["rows"]}, TARGET)
            existing = monitoring_daily_state_update.load_state(state_dir, universe)
            updated, changed = monitoring_daily_state_update.update_state(
                existing, incoming, TARGET, universe)
            self.assertTrue(changed)
            for market in ("KOSPI", "KOSDAQ"):
                (state_dir / f"{market.lower()}.json").write_text(
                    json.dumps(updated[market]), encoding="utf-8")

            baseline = daily_monitoring_baseline.build_baseline(
                universe_file=universe, before_date=TARGET + "z",
                generated_at="2026-10-26T20:31:00+09:00",
                history_reader=monitoring_daily_state_update.state_history_reader(updated),
            )
            baseline_path = root / "baseline.json"
            baseline_path.write_text(json.dumps(baseline), encoding="utf-8")
            trading_session_diagnostic.write_local_date_diagnostic(
                state_dir / "trading-session-diagnostic.json", TARGET,
                {code: TARGET for code in ("000001", "000002")},
                {"000001", "000002"},
            )

            monitoring_dir = root / "monitoring"
            canonical_dir = root / "canonical"
            canonical_dir.mkdir()
            built, _ = full_market_monitoring_evidence.build_evidence(
                snapshot, history_dir=canonical_dir, baseline=baseline,
                today=TARGET,
                publication_context={"baselineFreshnessStatus": "VALID_CURRENT_DAY_FINAL"},
                monitoring_daily_dir=state_dir,
            )
            full_market_monitoring_evidence.write_evidence(built, monitoring_dir)
            evidence = built["latest-summary.json"]["fullMarket"]["tradingDateEvidence"]
            self.assertEqual(evidence["reason"], "VERIFIED_MONITORING_DAILY_FINAL_SESSION")
            self.assertEqual(
                daily_report.resolve_report_trading_date(monitoring_dir, baseline_path)["status"],
                "READY",
            )

    def test_missing_date_blocks_before_final_state(self):
        rows = {"000001": raw("000001"), "000002": raw("000002", localDate=None)}
        with self._collect(rows) as (_, _, snapshot):
            result = final_daily_history.monitoring_daily_payload(
                snapshot, {"asOfDate": PREVIOUS, "historyStatus": "OK"},
                expected_codes=[asset["code"] for asset in ASSETS],
            )
        self.assertEqual(result["status"], "WAIT")
        self.assertTrue(any("TRADING_DATE_ROW_MISSING_OR_INCONSISTENT:000002" in reason
                            for reason in result["reason"]))

    def test_date_mismatch_blocks_before_final_state(self):
        rows = {"000001": raw("000001"), "000002": raw("000002", localDate="20261025")}
        with self._collect(rows) as (_, _, snapshot):
            result = final_daily_history.monitoring_daily_payload(
                snapshot, {"asOfDate": PREVIOUS, "historyStatus": "OK"},
                expected_codes=[asset["code"] for asset in ASSETS],
            )
        self.assertEqual(result["status"], "WAIT")
        self.assertIn("TRADING_DATE_MISSING_OR_INCONSISTENT", result["reason"])

    def test_composition_mismatch_uses_complete_naver_set_without_legal_claim(self):
        rows = {"000001": raw("000001"), "000002": raw("000002")}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            universe = root / "universe.json"
            universe.write_text(json.dumps({"assets": ASSETS}), encoding="utf-8")

            def fetch(market, index, _size):
                pages = {"KOSPI": [[rows["000001"]], []],
                         "KOSDAQ": [[raw("999999", marketStatus="CLOSED", sosok="1")], []]}
                return pages[market][index]

            snapshot = naver_full_market_current.collect_snapshot(
                universe, fetch, page_size=1, max_attempts=1, sleep_seconds=0,
                clock=lambda: "2026-10-26T20:30:00+09:00",
            )
            result = final_daily_history.monitoring_daily_payload(
                snapshot, {"asOfDate": PREVIOUS, "historyStatus": "OK"},
                expected_codes=[asset["code"] for asset in ASSETS],
            )
        self.assertEqual(snapshot["status"], "COMPLETE_RECONCILIATION_PENDING")
        self.assertEqual(result["status"], "READY")
        self.assertEqual({row["code"] for row in result["rows"]}, {"000001", "999999"})
        self.assertNotIn("NEW_LISTING", json.dumps(result, ensure_ascii=False))

    def test_missing_ohlcv_blocks_final_candidate(self):
        rows = {"000001": raw("000001", highPrice=None), "000002": raw("000002")}
        with self._collect(rows) as (_, _, snapshot):
            result = final_daily_history.monitoring_daily_payload(
                snapshot, {"asOfDate": PREVIOUS, "historyStatus": "OK"},
                expected_codes=[asset["code"] for asset in ASSETS],
            )
        self.assertEqual(result["status"], "WAIT")
        self.assertTrue(any("OHLCV_PRICE_INVALID:000001" in reason
                            for reason in result["reason"]))


if __name__ == "__main__":
    unittest.main()
