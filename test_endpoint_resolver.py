import unittest

import naver_market as market


def pc_row(code, updated='2026-11-09T12:30:00+09:00'):
    return {
        'code': str(code), 'name': f'업종{code}', 'ranking': '1',
        'changeRate': '2.1', 'risingCount': '2', 'fallingCount': '1',
        'unchangedCount': '0', 'totalTradingVolume': '100',
        'totalTradingValue': '200', 'totalMarketCap': '300',
        'updatedAt': updated,
    }


def mobile_row(code):
    return {
        'code': str(code), 'name': f'모바일{code}', 'changeRate': 1.2,
        'risingCount': 2, 'fallingCount': 1, 'unchangedCount': 0,
        'topItems': [],
    }


class EndpointResolverTests(unittest.TestCase):
    CLOCK = staticmethod(lambda: '2026-11-09T12:40:00+09:00')

    def test_registry_contains_browser_verified_pc_and_mobile_candidates(self):
        registry = market.endpoint_registry()
        self.assertEqual(registry['industry'][0]['name'], 'pc_v2_industry_cursor')
        self.assertEqual(registry['theme'][0]['name'], 'pc_v2_theme_cursor')
        self.assertEqual(registry['industry'][1]['adapter'], 'MOBILE_SECTORS')
        self.assertEqual(registry['theme'][1]['params']['sectorType'], 'theme')

    def test_404_primary_falls_back_to_validated_mobile_endpoint(self):
        def fetch(path, params, expected):
            if path.startswith('/api/stockSecurity'):
                raise market.MarketApiError('HTTP 404: primary')
            if path == '/front-api/stock/sectors/all':
                return {'isSuccess': True, 'result': {
                    'hasNext': bool(params.get('cursor')) is False,
                    'cursor': 'next' if not params.get('cursor') else None,
                    'sectors': [mobile_row('1')] if not params.get('cursor') else [mobile_row('2')],
                }}
            raise AssertionError(path)

        payload = market.collect_sector_rankings('industry', fetch, self.CLOCK, max_pages=3)
        self.assertEqual(payload['status'], 'OK')
        self.assertEqual(payload['endpoint'], 'mobile_front_api_industry_cursor')
        self.assertEqual(payload['count'], 2)
        self.assertEqual(payload['pagination']['endCondition'], 'HAS_NEXT_FALSE')
        self.assertEqual(payload['endpointDiagnostics'][0]['error']['message'], 'HTTP 404: primary')

    def test_schema_error_falls_back_and_does_not_reuse_stale_rows(self):
        def fetch(path, params, expected):
            if path.startswith('/api/stockSecurity'):
                return {'items': [{'bad': 'schema'}], 'hasNext': False, 'cursor': None}
            raise market.MarketApiError('HTTP 503: fallback unavailable')

        payload = market.collect_sector_rankings('theme', fetch, self.CLOCK, max_pages=2)
        self.assertEqual(payload['status'], 'ERROR')
        self.assertEqual(payload['count'], 0)
        self.assertEqual(payload['themes'], [])
        self.assertEqual(payload['endpoint'], None)
        self.assertEqual(len(payload['endpointDiagnostics']), 3)

    def test_duplicate_page_and_cursor_are_rejected(self):
        def duplicate_page(path, params, expected):
            code = '1' if not params.get('cursor') else '2'
            return {'items': [pc_row(code)], 'hasNext': True, 'cursor': 'same'}

        payload = market.collect_sector_rankings('industry', duplicate_page, self.CLOCK, max_pages=3)
        self.assertEqual(payload['status'], 'ERROR')
        self.assertIn('cursor repeated', payload['endpointDiagnostics'][0]['error']['message'])

    def test_source_date_mismatch_is_rejected(self):
        def stale(path, params, expected):
            if path.startswith('/api/stockSecurity'):
                return {'items': [pc_row('1', '2026-11-08T15:30:00+09:00')], 'hasNext': False, 'cursor': None}
            raise market.MarketApiError('fallback rejected')

        payload = market.collect_sector_rankings('industry', stale, self.CLOCK, max_pages=2)
        self.assertEqual(payload['status'], 'ERROR')
        self.assertIn('source date mismatch', payload['endpointDiagnostics'][0]['error']['message'])


if __name__ == '__main__':
    unittest.main()
