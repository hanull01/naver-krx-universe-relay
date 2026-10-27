import json
import tempfile
import unittest
from pathlib import Path

from tools.trading_date_evidence import (verify_monitoring_daily_final,
                                         verify_source_observed_monitoring_daily_final,
                                         verify_trading_date)

class TradingDateEvidenceTests(unittest.TestCase):
    def write(self, root, code, day):
        (root / (code + '.jsonl')).write_text(json.dumps({'code': code, 'date': day, 'close': 100}) + '\n', encoding='utf-8')

    def test_verified_canonical_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, 'A', '20261023'); self.write(root, 'B', '20261023')
            result = verify_trading_date(root, ['A', 'B'], '20261023')
            self.assertEqual(result['tradingDate'], '20261023')
            self.assertEqual(result['reason'], 'VERIFIED_CANONICAL_SESSION')

    def test_mixed_dates_are_not_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, 'A', '20261023'); self.write(root, 'B', '20261022')
            result = verify_trading_date(root, ['A', 'B'], '20261023')
            self.assertIsNone(result['tradingDate'])
            self.assertEqual(result['reason'], 'MIXED_CANONICAL_TRADING_DATES')

    def test_missing_and_invalid_evidence_is_not_published(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, 'A', '20261023')
            result = verify_trading_date(root, ['A', 'B'], '20261023')
            self.assertIsNone(result['tradingDate'])
            self.assertEqual(result['reason'], 'CANONICAL_COVERAGE_INCOMPLETE')

    def test_execution_date_is_not_a_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.write(root, 'A', '20261023')
            result = verify_trading_date(root, ['A'], None)
            self.assertIsNone(result['tradingDate'])
            self.assertEqual(result['reason'], 'INSUFFICIENT_TRADING_DATE_EVIDENCE')

    def write_final(self, root, market, codes, day='20261023', validity=0):
        stocks = []
        for code in codes:
            stocks.append({
                'code': code,
                'dates': ['20261022', day],
                'close': [99, 100],
                'high': [100, 101],
                'volume': [10, 20],
                'validity': [0, validity],
                'source_valid': [True, True],
                'source_status': ['OBSERVED', 'OBSERVED'],
            })
        (root / f'{market.lower()}.json').write_text(json.dumps({
            'schemaVersion': 1, 'format': 'COLUMN_ARRAY_V1', 'market': market,
            'publicationStatus': 'FINAL', 'asOfDate': day, 'stocks': stocks,
        }), encoding='utf-8')

    def test_verified_monitoring_daily_final_session_is_separate_from_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_final(root, 'KOSPI', ['A'])
            self.write_final(root, 'KOSDAQ', ['B'])
            result = verify_monitoring_daily_final(
                root, ['A', 'B'], current_snapshot={
                    'stocks': [{'code': 'A', 'localDate': '20261023'},
                               {'code': 'B', 'localDate': '20261023'}]
                })
            self.assertEqual(result['tradingDate'], '20261023')
            self.assertEqual(result['reason'], 'VERIFIED_MONITORING_DAILY_FINAL_SESSION')
            self.assertEqual(result['source'], 'MONITORING_DAILY_FINAL')

    def test_source_observed_final_is_explicitly_separate_from_local_date_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_final(root, 'KOSPI', ['A'])
            self.write_final(root, 'KOSDAQ', ['B'])
            for market in ('KOSPI', 'KOSDAQ'):
                path = root / f'{market.lower()}.json'
                payload = json.loads(path.read_text())
                payload['asOfDate'] = '20261023'
                payload['sourceQualityStatus'] = 'SOURCE_OBSERVED'
                path.write_text(json.dumps(payload), encoding='utf-8')
            result = verify_source_observed_monitoring_daily_final(
                root, ['A', 'B'], session_date='20261023')
            self.assertEqual(result['status'], 'SOURCE_OBSERVED')
            self.assertEqual(result['tradingDate'], '20261023')
            self.assertEqual(result['reason'], 'SOURCE_OBSERVED_MONITORING_DAILY_FINAL_DATE')

    def test_source_observed_final_allows_declared_normal_empty_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_final(root, 'KOSPI', ['A'])
            self.write_final(root, 'KOSDAQ', ['B'])
            for market in ('KOSPI', 'KOSDAQ'):
                path = root / f'{market.lower()}.json'
                payload = json.loads(path.read_text())
                payload['sourceQualityStatus'] = 'SOURCE_OBSERVED'
                payload['sourceMissingCodes'] = ['M']
                path.write_text(json.dumps(payload), encoding='utf-8')
            result = verify_source_observed_monitoring_daily_final(
                root, ['A', 'B', 'M'], session_date='20261023')
            self.assertEqual(result['status'], 'SOURCE_OBSERVED')
            self.assertEqual(result['sourceMissingCodes'], ['M'])

    def test_source_observed_final_rejects_missing_code_declared_in_wrong_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_final(root, 'KOSPI', ['A'])
            self.write_final(root, 'KOSDAQ', ['B'])
            for market in ('KOSPI', 'KOSDAQ'):
                path = root / f'{market.lower()}.json'
                payload = json.loads(path.read_text())
                payload['sourceQualityStatus'] = 'SOURCE_OBSERVED'
                payload['sourceMissingCodes'] = ['M'] if market == 'KOSPI' else []
                path.write_text(json.dumps(payload), encoding='utf-8')
            result = verify_source_observed_monitoring_daily_final(
                root, ['A', 'B', 'M'], session_date='20261023',
                expected_by_market={'KOSPI': ['A'], 'KOSDAQ': ['B', 'M']})
            self.assertEqual(result['status'], 'UNAVAILABLE')
            self.assertIn('KOSPI', result['invalidFinalCodes'])

    def test_monitoring_daily_final_requires_current_exact_code_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_final(root, 'KOSPI', ['A'])
            self.write_final(root, 'KOSDAQ', [])
            result = verify_monitoring_daily_final(
                root, ['A', 'B'], session_date='20261023',
                current_snapshot={'stocks': [{'code': 'A'}, {'code': 'B'}]})
            self.assertIsNone(result['tradingDate'])
            self.assertEqual(result['reason'], 'MONITORING_DAILY_FINAL_COVERAGE_INCOMPLETE')
            self.assertEqual(result['missingFinalCodes'], ['B'])

    def test_monitoring_daily_final_requires_every_snapshot_local_date_without_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_final(root, 'KOSPI', ['A'])
            self.write_final(root, 'KOSDAQ', ['B'])
            result = verify_monitoring_daily_final(
                root, ['A', 'B'], session_date='20261023',
                current_snapshot={'stocks': [{'code': 'A', 'localDate': '20261023'},
                                             {'code': 'B'}]})
            self.assertIsNone(result['tradingDate'])
            self.assertEqual(result['reason'], 'SNAPSHOT_TRADING_DATE_EVIDENCE_INCOMPLETE')

    def test_verified_complete_local_date_diagnostic_can_supply_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_final(root, 'KOSPI', ['A'])
            self.write_final(root, 'KOSDAQ', ['B'])
            (root / 'trading-session-diagnostic.json').write_text(json.dumps({
                'status': 'VERIFIED', 'source': 'NAVER_LOCAL_DATE',
                'targetDate': '20261023', 'expectedCount': 2, 'observedCount': 2,
                'expectedCodes': ['A', 'B'], 'observedCodes': ['A', 'B'],
                'observedDates': {'A': '20261023', 'B': '20261023'},
                'missingCodes': [], 'unexpectedCodes': [], 'duplicateCodes': [],
                'mismatchCodes': [], 'dateMismatchCodes': [],
            }), encoding='utf-8')
            result = verify_monitoring_daily_final(
                root, ['A', 'B'], current_snapshot={'stocks': [{'code': 'A'}, {'code': 'B'}]})
            self.assertEqual(result['tradingDate'], '20261023')
            self.assertEqual(result['diagnosticStatus'], 'VERIFIED')

    def test_monitoring_daily_final_rejects_duplicate_snapshot_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_final(root, 'KOSPI', ['A'])
            self.write_final(root, 'KOSDAQ', ['B'])
            result = verify_monitoring_daily_final(
                root, ['A', 'B'], session_date='20261023',
                current_snapshot={'stocks': [
                    {'code': 'A', 'localDate': '20261023'},
                    {'code': 'A', 'localDate': '20261023'},
                    {'code': 'B', 'localDate': '20261023'},
                ]})
            self.assertIsNone(result['tradingDate'])
            self.assertEqual(result['reason'], 'SNAPSHOT_TRADING_DATE_EVIDENCE_INCOMPLETE')

    def test_monitoring_daily_final_rejects_unexpected_final_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_final(root, 'KOSPI', ['A'])
            self.write_final(root, 'KOSDAQ', ['B', 'C'])
            result = verify_monitoring_daily_final(
                root, ['A', 'B'], session_date='20261023',
                current_snapshot={'stocks': [
                    {'code': 'A', 'localDate': '20261023'},
                    {'code': 'B', 'localDate': '20261023'},
                ]})
            self.assertIsNone(result['tradingDate'])
            self.assertEqual(result['reason'], 'MONITORING_DAILY_FINAL_COVERAGE_INCOMPLETE')
            self.assertEqual(result['unexpectedFinalCodes'], ['C'])

    def test_monitoring_daily_final_rejects_duplicate_final_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_final(root, 'KOSPI', ['A'])
            self.write_final(root, 'KOSDAQ', ['A'])
            result = verify_monitoring_daily_final(
                root, ['A'], session_date='20261023',
                current_snapshot={'stocks': [{'code': 'A', 'localDate': '20261023'}]})
            self.assertIsNone(result['tradingDate'])
            self.assertEqual(result['reason'], 'MONITORING_DAILY_FINAL_COVERAGE_INCOMPLETE')

    def test_verified_diagnostic_cannot_rescue_duplicate_snapshot_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_final(root, 'KOSPI', ['A'])
            self.write_final(root, 'KOSDAQ', ['B'])
            (root / 'trading-session-diagnostic.json').write_text(json.dumps({
                'status': 'VERIFIED', 'source': 'NAVER_LOCAL_DATE',
                'targetDate': '20261023', 'expectedCount': 2, 'observedCount': 2,
                'expectedCodes': ['A', 'B'], 'observedCodes': ['A', 'B'],
                'observedDates': {'A': '20261023', 'B': '20261023'},
                'missingCodes': [], 'unexpectedCodes': [], 'duplicateCodes': [],
                'mismatchCodes': [], 'dateMismatchCodes': [],
            }), encoding='utf-8')
            result = verify_monitoring_daily_final(
                root, ['A', 'B'], current_snapshot={'stocks': [
                    {'code': 'A'}, {'code': 'A'}, {'code': 'B'}]})
            self.assertIsNone(result['tradingDate'])
            self.assertEqual(result['reason'], 'SNAPSHOT_TRADING_DATE_EVIDENCE_INCOMPLETE')

    def test_monitoring_daily_final_rejects_empty_authoritative_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_final(root, 'KOSPI', [])
            self.write_final(root, 'KOSDAQ', [])
            result = verify_monitoring_daily_final(
                root, [], session_date='20261023',
                current_snapshot={'stocks': []})
            self.assertIsNone(result['tradingDate'])
            self.assertEqual(result['reason'], 'AUTHORITATIVE_CODE_SET_EMPTY')

    def test_snapshot_top_level_and_row_dates_must_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write(root, 'A', '20261023')
            result = verify_trading_date(
                root, ['A'], session_date='20261023',
                current_snapshot={'stocks': [{'code': 'A', 'localDate': '20261022'}]})
            self.assertIsNone(result['tradingDate'])
            self.assertEqual(result['reason'], 'INSUFFICIENT_TRADING_DATE_EVIDENCE')

    def test_monitoring_daily_final_rejects_stale_or_invalid_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_final(root, 'KOSPI', ['A'], day='20261022')
            self.write_final(root, 'KOSDAQ', ['B'], validity=1)
            result = verify_monitoring_daily_final(
                root, ['A', 'B'], session_date='20261023',
                current_snapshot={'stocks': [{'code': 'A'}, {'code': 'B'}]})
            self.assertIsNone(result['tradingDate'])
            self.assertEqual(result['reason'], 'MONITORING_DAILY_FINAL_COVERAGE_INCOMPLETE')

    def test_monitoring_daily_final_does_not_infer_from_execution_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_final(root, 'KOSPI', ['A'])
            self.write_final(root, 'KOSDAQ', ['B'])
            result = verify_monitoring_daily_final(root, ['A', 'B'], current_snapshot={
                'generatedAt': '2026-10-23T20:30:00+09:00',
                'stocks': [{'code': 'A'}, {'code': 'B'}],
            })
            self.assertIsNone(result['tradingDate'])
            self.assertEqual(result['reason'], 'INSUFFICIENT_TRADING_DATE_EVIDENCE')

    def test_monitoring_daily_final_rejects_unverified_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_final(root, 'KOSPI', ['A'])
            self.write_final(root, 'KOSDAQ', ['B'])
            (root / 'trading-session-diagnostic.json').write_text(json.dumps({
                'status': 'UNVERIFIED', 'source': 'NAVER_LOCAL_DATE',
                'targetDate': '20261023', 'expectedCount': 2, 'observedCount': 1,
                'observedDates': {'A': '20261023'},
            }), encoding='utf-8')
            result = verify_monitoring_daily_final(
                root, ['A', 'B'], session_date='20261023',
                current_snapshot={'stocks': [{'code': 'A'}, {'code': 'B'}]})
            self.assertIsNone(result['tradingDate'])
            self.assertEqual(result['reason'], 'MONITORING_DAILY_DIAGNOSTIC_UNVERIFIED')

if __name__ == '__main__':
    unittest.main()
