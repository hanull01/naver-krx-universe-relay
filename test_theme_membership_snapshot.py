import unittest

import theme_research as theme
import dynamic_universe_manager as manager
from test_naver_market import stock


class ThemeMembershipSnapshotTests(unittest.TestCase):
    def test_validated_theme_list_is_reused_and_membership_is_complete(self):
        calls = []

        def fetch(path, params, expected):
            calls.append((path, params['startIdx']))
            return [stock('000001')] if params['startIdx'] == 0 else []

        result = theme.collect_theme_membership_for_list([{'id': '520', 'name': '테스트'}], fetch)
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(result['themeCount'], 1)
        self.assertEqual(result['membershipCount'], 1)
        self.assertEqual(result['byCode']['000001'][0]['id'], '520')
        self.assertEqual(len(calls), 2)

    def test_incomplete_membership_is_not_publishable(self):
        def fetch(path, params, expected):
            return [stock('000001')]

        result = theme.collect_theme_membership_for_list([{'id': '520', 'name': '테스트'}], fetch)
        self.assertIn(result['status'], {'PARTIAL', 'ERROR'})

    def test_failed_group_does_not_become_removed(self):
        context = {'themes': {'status': 'AVAILABLE', 'periods': {'daily': {'themes': []}}}}
        previous = {'themes': [{'id': '520', 'name': '기존 테마', 'status': 'ACTIVE', 'score': 80}]}
        result = manager.build_management_result(context, {}, previous=previous)
        row = next(row for row in result['themes'] if row['id'] == '520')
        self.assertEqual(row['status'], 'ACTIVE')
        self.assertTrue(row['selectionDeferred'])
        self.assertEqual(row['dataQuality'], 'LIST_NOT_FOUND')


if __name__ == '__main__':
    unittest.main()
