import unittest

import theme_research as theme
from test_naver_market import category, stock


class ThemeResearchTests(unittest.TestCase):
    CLOCK = staticmethod(lambda: '2026-09-23T12:00:00+09:00')

    def test_multi_page_empty_termination_and_normalization(self):
        pages = {0: [category(1, 'AI')], 1: [category(2, '로봇')], 2: []}
        calls = []
        def fetch(path, params, expected):
            calls.append((path, params['startIdx']))
            return pages[params['startIdx']]
        result = theme.collect_themes(fetch, self.CLOCK)
        self.assertEqual(result['status'], 'OK')
        self.assertEqual([x['id'] for x in result['themes']], ['1', '2'])
        self.assertEqual(result['pagination']['endCondition'], 'EMPTY_ARRAY')
        self.assertEqual(calls[0][0], theme.THEME_LIST_PATH)

    def test_repeated_page_is_partial_and_not_success(self):
        def fetch(path, params, expected):
            return [category(1)]
        result = theme.collect_themes(fetch, self.CLOCK, max_pages=4)
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertIn('repeated page', result['error']['message'])
        self.assertEqual(result['pagination']['endCondition'], 'UNCONFIRMED')

    def test_partial_page_overlap_is_recorded_and_not_success(self):
        def fetch(path, params, expected):
            return ([category(1), category(2)] if params['startIdx'] == 0
                    else [category(2), category(3)] if params['startIdx'] == 1
                    else [])
        result = theme.collect_themes(fetch, self.CLOCK)
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(result['pageDiagnostics'][1]['duplicateCount'], 1)
        self.assertIn('duplicate', result['error']['message'])

    def test_member_duplicates_and_missing_fields_fail_closed(self):
        def duplicate(path, params, expected):
            return [stock('000001'), stock('000001')] if params['startIdx'] == 0 else []
        result = theme.collect_theme_members('1', duplicate, self.CLOCK)
        self.assertEqual(result['status'], 'ERROR')
        self.assertIn('duplicate', result['error']['message'])

    def test_membership_and_analysis_preserve_unknowns_and_limit_universe(self):
        def fetch(path, params, expected):
            if path == theme.THEME_LIST_PATH:
                return [category(1, 'AI')] if params['startIdx'] == 0 else []
            if params['startIdx'] == 0:
                return [stock('000001'), stock('000002')]
            return []
        membership = theme.collect_theme_membership(fetch, self.CLOCK)
        result = theme.analyze_dynamic_themes(
            membership,
            quotes={'000001': {'code': '000001', 'name': 'A', 'changeRate': 3.0,
                               'tradingValue': 20, 'sourceTime': 't'}},
            technicals={'000001': {'breakout20': 'confirmed'}},
            universe_codes={'000001'},
        )
        group = result['groups'][0]
        self.assertEqual(group['memberCount'], 1)
        self.assertEqual(group['knownQuoteCount'], 1)
        self.assertEqual(group['status'], 'OK')
        self.assertEqual(group['leaders'][0]['technical']['breakout20'], 'confirmed')

    def test_no_previous_report_does_not_invent_changes(self):
        result = theme.analyze_dynamic_themes({'themes': []})
        self.assertIsNone(result['changes'])

    def test_pc_cursor_periods_complete_and_preserve_metrics(self):
        def row(code, name, updated='2026-10-12T12:00:00+09:00'):
            return {'code': code, 'name': name, 'ranking': '1', 'changeRate': '2.5',
                    'risingCount': '2', 'fallingCount': '1', 'unchangedCount': '0',
                    'totalTradingValue': '10', 'totalTradingVolume': '20', 'totalMarketCap': '30',
                    'topByChangeRate': [], 'topByTradingValue': [], 'topByTradingVolume': [],
                    'updatedAt': updated}
        def fetch(path, params, expected):
            self.assertEqual(path, theme.PC_THEME_RANKING_PATH)
            return ({'items': [row('1', 'A')], 'hasNext': True, 'cursor': 'next'}
                    if 'cursor' not in params else {'items': [row('2', 'B')], 'hasNext': False})
        result = theme.collect_pc_theme_ranking('daily', fetch, self.CLOCK)
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(result['uniqueThemeCount'], 2)
        self.assertEqual(result['themes'][0]['totalTradingValue'], 10)
        self.assertEqual(result['pagination']['endCondition'], 'HAS_NEXT_FALSE')

    def test_pc_cursor_missing_repeated_duplicate_and_time_change_fail_closed(self):
        base = {'code': '1', 'name': 'A', 'ranking': '1', 'changeRate': '1',
                'updatedAt': 't'}
        def missing(path, params, expected): return {'items': [base], 'hasNext': True}
        self.assertEqual(theme.collect_pc_theme_ranking('daily', missing, self.CLOCK)['status'], 'PARTIAL')
        def repeated(path, params, expected): return {'items': [base], 'hasNext': True, 'cursor': 'same'}
        self.assertEqual(theme.collect_pc_theme_ranking('daily', repeated, self.CLOCK)['status'], 'PARTIAL')
        def duplicate(path, params, expected):
            return ({'items': [base], 'hasNext': True, 'cursor': 'next'} if 'cursor' not in params
                    else {'items': [base], 'hasNext': False})
        self.assertEqual(theme.collect_pc_theme_ranking('daily', duplicate, self.CLOCK)['status'], 'PARTIAL')

        def time_change(path, params, expected):
            row = dict(base)
            row['code'] = '1' if 'cursor' not in params else '2'
            row['updatedAt'] = 'first' if 'cursor' not in params else 'second'
            return ({'items': [row], 'hasNext': True, 'cursor': 'next'} if 'cursor' not in params
                    else {'items': [row], 'hasNext': False})
        changed = theme.collect_pc_theme_ranking('daily', time_change, self.CLOCK)
        self.assertEqual(changed['status'], 'OK')
        self.assertFalse(changed['updatedAtConsistent'])
        self.assertEqual(changed['quality']['snapshotConsistency'], 'NON_ATOMIC')

    def test_pc_analysis_keeps_naver_and_system_leaders_separate(self):
        ranking = {'period': 'daily', 'status': 'OK', 'themes': [{
            'id': '1', 'name': 'AI', 'ranking': 1, 'changeRate': 2.0,
            'updatedAt': 't', 'naverTopByChangeRate': [{'code': '000001'}],
            'naverTopByTradingValue': [{'code': '000001'}]}]}
        membership = {'themes': [{'id': '1', 'name': 'AI',
                                  'members': [{'code': '000001', 'name': 'A'}]}]}
        result = theme.attach_pc_theme_analysis(
            ranking, membership,
            quotes={'000001': {'code': '000001', 'name': 'A', 'changeRate': 1.0,
                               'tradingValue': 100}},
        )
        row = result['themes'][0]
        self.assertEqual(row['naverTopByChangeRate'], [{'code': '000001'}])
        self.assertEqual(row['systemLeaders'][0]['code'], '000001')
        self.assertEqual(row['membershipStatus'], 'OK')

    def test_period_comparison_and_work_contract_do_not_treat_difference_as_removal(self):
        row = {'id': '1', 'name': 'A', 'ranking': 1, 'changeRate': 1, 'risingCount': 1,
               'fallingCount': 0, 'totalTradingValue': 1, 'totalTradingVolume': 1,
               'naverTopByChangeRate': [], 'updatedAt': 't'}
        periods = {
            'daily': {'themes': [row], 'quality': {'snapshotConsistency': 'NON_ATOMIC'}},
            'weekly': {'themes': [dict(row, id='2')], 'quality': {'snapshotConsistency': 'CONSISTENT'}},
        }
        contract = theme.build_work_theme_contract(periods)
        self.assertEqual(contract['comparison']['commonCodeCount'], 0)
        self.assertEqual(contract['membershipPolicy'], 'TOP_THEMES_CACHED_OR_ON_DEMAND_ONLY')


if __name__ == '__main__':
    unittest.main()
