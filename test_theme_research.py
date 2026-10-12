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


if __name__ == '__main__':
    unittest.main()
