import importlib.util
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baseline_tool = load("daily_monitoring_baseline")
runner = load("run_full_market_monitoring")
KST = ZoneInfo("Asia/Seoul")


def open_day(_): return True, "KRX_OPEN"
def holiday(_): return False, "KRX_HOLIDAY"


def history(day="20261001", close=100):
    return {"date": day, "open": 99, "high": 101, "low": 98, "close": close, "volume": 100, "feature_valid": True}


def asset(): return {"code": "000001", "name": "테스트", "market": "KOSPI", "assetType": "STOCK"}


class MonitoringProductionTests(unittest.TestCase):
    def _history_fixture(self, root, row=history()):
        hist = root / "history"; hist.mkdir()
        (hist / "000001.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        universe = root / "universe.json"; universe.write_text(json.dumps({"assets": [asset()]}), encoding="utf-8")
        return hist, universe

    def test_refresh_publishes_confirmed_exact_baseline_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); hist, universe = self._history_fixture(root)
            output = root / "latest.json"
            result = baseline_tool.refresh_baseline("2026-10-01", output, hist, universe, open_day, "t")
            self.assertEqual(result["status"], "SUCCESS")
            saved = json.loads(output.read_text())
            self.assertEqual(saved["asOfDate"], "20261001")
            self.assertEqual(saved["refreshDiagnostics"]["todayRowCount"], 1)

    def test_not_ready_and_holiday_preserve_previous_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); hist, universe = self._history_fixture(root, history("20260930"))
            output = root / "latest.json"; output.write_text('{"preserve":true}', encoding="utf-8")
            result = baseline_tool.refresh_baseline("2026-10-01", output, hist, universe, open_day)
            self.assertEqual(result["status"], "NOT_READY")
            self.assertEqual(json.loads(output.read_text()), {"preserve": True})
            result = baseline_tool.refresh_baseline("2026-10-01", output, hist, universe, holiday)
            self.assertEqual(result["reason"], "KRX_HOLIDAY")
            self.assertEqual(json.loads(output.read_text()), {"preserve": True})

    def test_refresh_no_write_validates_without_replacing_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); hist, universe = self._history_fixture(root)
            output = root / "latest.json"; output.write_text('{"preserve":true}', encoding="utf-8")
            result = baseline_tool.refresh_baseline("2026-10-01", output, hist, universe, open_day, no_write=True)
            self.assertEqual(result["status"], "SUCCESS")
            self.assertTrue(result["noWrite"])
            self.assertEqual(json.loads(output.read_text()), {"preserve": True})

    def test_freshness_allows_prior_open_day_and_current_final_only(self):
        prior = {"historyStatus": "OK", "asOfDate": "20260930", "count": 1, "authoritativeCount": 1, "coveragePct": 100.0}
        current = dict(prior, asOfDate="20261001")
        regular = datetime(2026, 10, 1, 10, 0, tzinfo=KST)
        after = datetime(2026, 10, 1, 16, 0, tzinfo=KST)
        self.assertEqual(runner.baseline_freshness(prior, regular, open_day)[1], "VALID_PREVIOUS_TRADING_DAY")
        self.assertFalse(runner.baseline_freshness(current, regular, open_day)[0])
        self.assertEqual(runner.baseline_freshness(current, after, open_day)[1], "VALID_CURRENT_DAY_FINAL")

    def test_scalar_baseline_readiness_accepts_valid_without_publication_status(self):
        baseline = {"asOfDate": "20261002", "historyStatus": "OK", "count": 2,
                    "authoritativeCount": 2, "coveragePct": 100.0,
                    "refreshDiagnostics": {"targetDate": "2026-10-02",
                                           "missingCodes": [], "extraCodes": []}}
        self.assertEqual(runner.baseline_readiness(baseline, "2026-10-02"), (True, "READY"))

    def test_scalar_baseline_readiness_rejects_invalid_contract(self):
        valid = {"asOfDate": "20261002", "historyStatus": "OK", "count": 2,
                 "authoritativeCount": 2, "coveragePct": 100.0,
                 "refreshDiagnostics": {"missingCodes": [], "extraCodes": []}}
        cases = [
            (dict(valid, historyStatus="ERROR"), "BASELINE_HISTORY_INVALID"),
            (dict(valid, asOfDate="20261001"), "BASELINE_DATE_MISMATCH"),
            (dict(valid, coveragePct=99.0), "BASELINE_COVERAGE_INVALID"),
            (dict(valid, refreshDiagnostics={"missingCodes": ["000001"], "extraCodes": []}), "BASELINE_MISSING_CODES"),
            (dict(valid, refreshDiagnostics={"missingCodes": [], "extraCodes": ["000002"]}), "BASELINE_EXTRA_CODES"),
        ]
        for baseline, reason in cases:
            self.assertEqual(runner.baseline_readiness(baseline, "2026-10-02"), (False, reason))

    def test_incomplete_current_never_publishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); baseline = root / "baseline.json"; output = root / "evidence"
            baseline.write_text(json.dumps({"historyStatus": "OK", "asOfDate": "20260930", "count": 1,
                                             "authoritativeCount": 1, "coveragePct": 100.0, "stocks": []}), encoding="utf-8")
            output.mkdir(); preserved = output / "latest-quality.json"; preserved.write_text('{"preserve":true}', encoding="utf-8")
            current = root / "current.json"; current.write_text(json.dumps({"status": "FAILURE", "coverageCount": 0, "expectedCount": 1}), encoding="utf-8")
            result = runner.run(datetime(2026, 10, 1, 10, tzinfo=KST), baseline, output, current, market_day_check=open_day)
            self.assertEqual(result["reason"], "CURRENT_SNAPSHOT_INCOMPLETE")
            self.assertEqual(json.loads(preserved.read_text()), {"preserve": True})

    def test_extracts_enabled_universe_from_the_validated_full_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            universe = root / "universe.json"
            universe.write_text(json.dumps({"stocks": [
                {"itemCode": "000001", "enabled": True},
                {"itemCode": "000002", "enabled": False},
                {"itemCode": "000003", "enabled": True},
            ]}), encoding="utf-8")
            snapshot = {"stocks": [{"code": "000001"}, {"code": "000002"}, {"code": "000003"}]}
            self.assertEqual([row["code"] for row in runner.extract_universe_snapshot(snapshot, universe)], ["000001", "000003"])
            with self.assertRaisesRegex(ValueError, "missing 1"):
                runner.extract_universe_snapshot({"stocks": [{"code": "000001"}]}, universe)

    def test_cross_check_records_time_delay_and_comparable_fields_without_price_tolerance(self):
        with tempfile.TemporaryDirectory() as directory:
            quotes = Path(directory) / "quotes.json"
            quotes.write_text(json.dumps({
                "generatedAt": "relay-generated", "sourceTime": "relay-source",
                "sourceTimeLatest": "relay-latest", "datas": [{
                    "itemCode": "000001", "closePrice": 101, "compareToPreviousClosePrice": 1,
                    "fluctuationsRatio": 1.0, "openPrice": 99, "highPrice": 102, "lowPrice": 98,
                    "accumulatedTradingVolume": 100, "session": "REGULAR", "delayTime": 0,
                }],
            }), encoding="utf-8")
            result = runner.cross_check_universe_snapshot([{
                "code": "000001", "currentPrice": 102, "change": 1, "changeRate": 1.0,
                "openPrice": 99, "highPrice": 102, "lowPrice": 98, "accumulatedTradingVolume": 100,
            }], quotes)
            self.assertEqual(result["status"], "AVAILABLE")
            self.assertEqual(result["relaySourceTime"], "relay-source")
            self.assertEqual(result["relayDelayTimes"], [0])
            self.assertEqual(result["fieldComparisons"]["currentPrice"]["differentCount"], 1)

    def test_monitored_universe_is_separate_from_full_market_and_keeps_regular_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); quotes = root / "quotes.json"; states = root / "states.json"
            quotes.write_text(json.dumps({"generatedAt": "relay-generated", "sourceTime": "20:00",
                "datas": [{"itemCode": "000001", "stockName": "관심종목", "closePrice": 105,
                           "fluctuationsRatio": 2.0}]}), encoding="utf-8")
            states.write_text(json.dumps({"datas": [{"itemCode": "000001",
                "current": {"aboveMA20": True, "aboveMA60": True, "breakout20": "attempt"},
                "regularSession": {"status": "CONFIRMED", "aboveMA20": False,
                                   "aboveMA60": False, "breakout20": "failed"}}]}), encoding="utf-8")
            # An unverified nested object cannot certify a regular close.
            result = runner.monitored_universe_summary([{"code": "000001"}], quotes, states)
        self.assertEqual(result["scope"], "MONITORED_UNIVERSE")
        self.assertEqual(result["current"]["advancers"], 1)
        self.assertEqual(result["current"]["aboveMA20Count"], 1)
        self.assertEqual(result["regularSession"]["confirmedCount"], 0)
        self.assertEqual(result["regularSession"]["aboveMA20Count"], 0)
        self.assertEqual(result["regularSession"]["breakout20Count"], 0)

    def test_flat_current_and_strict_regular_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); regular = root / "daily-regular"; regular.mkdir()
            daily = root / "daily"; daily.mkdir()
            quotes = root / "quotes.json"; states = root / "states.json"
            codes = [f"{i:06d}" for i in range(42)]
            quotes.write_text(json.dumps({"datas": [{"itemCode": code,
                "sourceTime": "2026-10-06T17:35:00+09:00", "closePrice": 200,
                "fluctuationsRatio": 10} for code in codes]}))
            states.write_text(json.dumps({"datas": [{"itemCode": code,
                "status": "ok", "sourceTime": "2026-10-06T17:35:00+09:00",
                "priceVsMA20": "above", "priceVsMA60": "below", "breakout20": "confirmed",
                "ma20": 100, "ma60": 120, "priorHigh20": 115} for code in codes]}))
            bar = {"date": "2026-10-06", "complete": True, "session": "REGULAR",
                   "barType": "REGULAR_SESSION",
                   "source": "NAVER_VERIFIED_REGULAR_CLOSE",
                   "verification": "DAILY_REALTIME_MATCH",
                   "sourceDate": "2026-10-06",
                   "sourceTime": "2026-10-06T20:00:00+09:00",
                   "sessionCloseTime": "15:30", "marketStatus": "CLOSE",
                   "marketStatusDetailType": "close", "delayTime": 0,
                   "close": 110, "high": 116}
            previous_closes = [100, 120, 110, None] + [100] * 38
            for code, previous_close in zip(codes, previous_closes):
                (regular / f"{code}.json").write_text(json.dumps({"regularDailyStatus": "ok", "datas": [bar]}))
                rows = [] if previous_close is None else [
                    {"date": "2026-10-02", "close": previous_close,
                     "complete": True, "noTrading": False},
                    {"date": "2026-10-01", "close": 999,
                     "complete": True, "noTrading": False},
                ]
                (daily / f"{code}.json").write_text(json.dumps({"datas": rows}))
            subset = [{"code": code} for code in codes]
            result = runner.monitored_universe_summary(subset, quotes, states)
            self.assertEqual(result["current"]["aboveMA20EligibleCount"], 42)
            self.assertEqual(result["current"]["aboveMA20Count"], 42)
            self.assertEqual(result["current"]["aboveMA60Count"], 0)
            self.assertEqual(result["current"]["breakout20Count"], 42)
            self.assertEqual(result["regularSession"]["status"], "AVAILABLE")
            self.assertEqual(result["regularSession"]["confirmedCount"], 42)
            self.assertEqual(result["regularSession"]["breakout20Count"], 0)
            self.assertEqual(result["regularSession"]["changeEligibleCount"], 41)
            self.assertEqual(result["regularSession"]["advancers"], 39)
            self.assertEqual(result["regularSession"]["decliners"], 1)
            self.assertEqual(result["regularSession"]["unchanged"], 1)
            self.assertEqual(result["regularSession"]["advancerPct"], round(39 / 41 * 100, 4))
            self.assertEqual(result["regularSession"]["advancers"]
                             + result["regularSession"]["decliners"]
                             + result["regularSession"]["unchanged"], 41)
            for change in ({"sourceTime": "bad"}, {"date": "2026-10-05"},
                           {"complete": False}, {"session": "AFTER"},
                           {"barType": "RAW"}, {"source": "OTHER"}):
                with self.subTest(change=change):
                    (regular / f"{codes[0]}.json").write_text(json.dumps({"regularDailyStatus": "ok", "datas": [dict(bar, **change)]}))
                    result = runner.monitored_universe_summary(subset, quotes, states)
                    self.assertEqual(result["regularSession"]["confirmedCount"], 41)
                    self.assertEqual(result["regularSession"]["changeEligibleCount"], 40)
            (regular / f"{codes[0]}.json").write_text(json.dumps({"regularDailyStatus": "unavailable", "datas": [bar]}))
            unavailable = runner.monitored_universe_summary(subset, quotes, states)["regularSession"]
            self.assertEqual(unavailable["confirmedCount"], 41)
            self.assertEqual(unavailable["changeEligibleCount"], 40)

    def test_hourly_workflow_collects_full_snapshot_once_and_publishes_compact_evidence(self):
        workflow = (ROOT / ".github/workflows/refresh.yml").read_text(encoding="utf-8")
        self.assertIn('- cron: "0 23 * * 0-4"', workflow)
        self.assertIn('- cron: "0 0-11 * * 1-5"', workflow)
        self.assertNotIn('cron: "32 ', workflow)
        self.assertEqual(workflow.count("python tools/naver_full_market_current.py"), 1)
        self.assertIn('--current-file "$RUNNER_TEMP/full-market-current.json"', workflow)
        self.assertIn('--output "$RUNNER_TEMP/full-market-current.json"', workflow)
        self.assertNotIn("data/market/stocks-current.json", workflow)
        self.assertIn("data/monitoring/latest-quality.json", workflow)
        self.assertIn("steps.full_market.outputs.exit_code == '0'", workflow)
        self.assertIn("steps.collect.outputs.exit_code == '0' && steps.full_market.outputs.exit_code == '0' && steps.publish.outputs.published == 'true'", workflow)
        self.assertFalse((ROOT / ".github/workflows/refresh-full-market-monitoring.yml").exists())

    def test_market_schedules_match_main_hourly_daily_and_snapshot_contract(self):
        snapshots = (ROOT / ".github/workflows/refresh-market-snapshots.yml").read_text(encoding="utf-8")
        daily = (ROOT / ".github/workflows/refresh-daily.yml").read_text(encoding="utf-8")
        report = (ROOT / ".github/workflows/daily-report.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "10,30,50 23 * * 0-4"', snapshots)
        self.assertIn('cron: "10,30,50 0-10 * * 1-5"', snapshots)
        self.assertIn('cron: "35 7 * * 1-5"', daily)
        self.assertIn("cron: '40 11 * * 1-5'", report)


if __name__ == "__main__":
    unittest.main()
