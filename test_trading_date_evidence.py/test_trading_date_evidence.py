import json
import tempfile
import unittest
from pathlib import Path

from tools.trading_date_evidence import verify_trading_date

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

if __name__ == '__main__':
    unittest.main()
