import unittest

import dynamic_monitoring as monitor


def theme_row(code, name, ranking):
    return {'id': code, 'name': name, 'ranking': ranking, 'changeRate': 2.0,
            'risingCount': 2, 'fallingCount': 1, 'unchangedCount': 0,
            'totalTradingValue': 10, 'totalTradingVolume': 20, 'totalMarketCap': 30,
            'naverTopByChangeRate': [], 'naverTopByTradingValue': [],
            'naverTopByTradingVolume': [], 'updatedAt': 't', 'source': {}}


class DynamicMonitoringTests(unittest.TestCase):
    def snapshot(self):
        period = {'status': 'OK', 'count': 2, 'uniqueThemeCount': 2,
                  'pagination': {'endCondition': 'HAS_NEXT_FALSE'},
                  'quality': {'snapshotConsistency': 'NON_ATOMIC'},
                  'updatedAt': ['t'], 'themes': [theme_row('1', 'AI', 1), theme_row('2', '로봇', 12)]}
        return {'collection': {'status': 'OK', 'researchGate': 'DISABLED_PENDING_OPERATIONAL_APPROVAL',
                               'periods': {name: dict(period, period=name) for name in monitor.THEME_PERIODS}}}

    def test_reuses_data_without_network_and_deduplicates_related_stock(self):
        result = monitor.build_context(
            self.snapshot(), {'status': 'AVAILABLE', 'industries': [{'name': '반도체', 'changeRate': 3.0}]},
            {'watchlists': {'관심': ['000001']}, 'sectors': {}, 'themes': {}},
            {'datas': [{'code': '000001', 'changeRate': 1}]},
            {'datas': [{'itemCode': '000001', 'ma20': 10}]},
            {'datas': [{'itemCode': '000001', 'breakout20': 'confirmed'}]},
            {'status': 'AVAILABLE', 'symbols': ['000001']},
            {'themes': [{'id': '1', 'name': 'AI', 'members': [{'code': '000001', 'name': 'A'}]}]},
        )
        self.assertEqual(result['networkCalls'], 0)
        self.assertEqual(len(result['relatedStocks']), 1)
        self.assertTrue(result['relatedStocks']['000001']['kiwoomCandidate'])
        self.assertIn('KIWOOM_CANDIDATE', result['relatedStocks']['000001']['selectionReasons'])
        self.assertEqual(result['relatedStocks']['000001']['themeMemberships'][0]['id'], '1')
        self.assertEqual(result['themes']['commonStrongThemes'][0]['id'], '1')
        self.assertEqual(result['themes']['periods']['daily']['themes'][0]['systemLeaders'][0]['code'], '000001')

    def test_invalid_theme_is_isolated_and_existing_market_context_survives(self):
        result = monitor.build_context(
            {}, {'status': 'AVAILABLE', 'industries': [{'name': '반도체', 'changeRate': 1.0}]},
            {}, {'datas': []}, {'datas': []}, {'datas': []})
        self.assertEqual(result['themes']['status'], 'UNAVAILABLE')
        self.assertEqual(result['industries']['status'], 'AVAILABLE')
        self.assertEqual(result['networkCalls'], 0)

    def test_published_industry_quality_contract_is_consumable_without_theme_data(self):
        result = monitor.build_context(
            {}, {'quality': {'sourceStatus': 'OK', 'industryEvidenceUsable': 'FULL'},
                 'industries': [{'name': '반도체', 'changeRate': 1.0}]},
            {}, {'datas': []}, {'datas': []}, {'datas': []})
        self.assertEqual(result['themes']['status'], 'UNAVAILABLE')
        self.assertEqual(result['industries']['status'], 'AVAILABLE')
        self.assertEqual(result['industries']['validation'], 'QUALITY_FULL')
        self.assertEqual(result['networkCalls'], 0)

    def test_published_industry_avg_return_drives_strong_selection(self):
        result = monitor.build_context(
            {}, {'quality': {'sourceStatus': 'OK', 'industryEvidenceUsable': 'FULL'},
                 'industries': [{'name': '반도체', 'avgReturn1D': 4.0, 'leaders': []},
                                {'name': '약세', 'avgReturn1D': -1.0, 'leaders': []}]},
            {}, {'datas': []}, {'datas': []}, {'datas': []})
        self.assertEqual([row['name'] for row in result['industries']['strong']], ['반도체'])


if __name__ == '__main__':
    unittest.main()
