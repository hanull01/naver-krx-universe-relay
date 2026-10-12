import unittest

import dynamic_universe


class DynamicUniverseTests(unittest.TestCase):
    def context(self):
        return {
            'industries': {'strong': [{'name': '반도체', 'leaders': [
                {'code': '000001', 'changeRate': 10}, {'code': '000002'},
                {'code': '000003'}, {'code': '000004'}]}]},
            'themes': {'status': 'UNAVAILABLE'},
        }

    def universe(self):
        return {'generatedAt': 't', 'watchlists': {
            'legacy33': ['000002'], '관심종목': ['000010', '000001'],
        }}

    def test_deduplicates_sources_and_preserves_baseline(self):
        result = dynamic_universe.build_dynamic_universe(
            self.context(), self.universe(),
            {'status': 'AVAILABLE', 'syncedAt': 's', 'symbols': ['000020', '000001']},
            baseline_codes=['000001', '000099'])
        self.assertEqual(result['networkCalls'], 0)
        self.assertEqual(result['collectionCodes'], ['000001', '000002', '000003', '000010', '000020', '000099'])
        row = next(row for row in result['candidates'] if row['code'] == '000001')
        self.assertIn('INDUSTRY_LEADER', row['sources'])
        self.assertIn('USER_WATCHLIST', row['sources'])
        self.assertIn('KIWOOM_CANDIDATE', row['sources'])

    def test_theme_leaders_require_verified_system_data(self):
        context = {'industries': {'strong': []}, 'themes': {
            'status': 'AVAILABLE', 'periods': {'daily': {'themes': [
                {'id': '1', 'name': 'AI', 'systemLeaders': [], 'updatedAt': ['t']}
            ]}}
        }}
        result = dynamic_universe.build_dynamic_universe(context, {}, baseline_codes=[])
        self.assertEqual(result['selectedCodes'], [])
        self.assertEqual(result['insufficientData'][0]['reason'], 'VERIFIED_SYSTEM_LEADER_DATA_MISSING')

    def test_records_added_and_exited_without_deleting_user_codes(self):
        result = dynamic_universe.build_dynamic_universe(
            self.context(), self.universe(), baseline_codes=['000001'],
            previous={'selectedCodes': ['000001', '000777']})
        self.assertIn('000777', result['exitedCodes'])
        self.assertIn('000010', result['preservedUserCodes'])
        self.assertTrue(result['policy']['userWatchlistsNeverAutoDeleted'])


if __name__ == '__main__':
    unittest.main()
