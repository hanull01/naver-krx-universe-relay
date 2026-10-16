import json
import tempfile
import unittest
from pathlib import Path

import naver_market as market


def stock(code='005930', name='삼성전자', **extra):
    row = {
        'itemcode': code, 'itemname': name, 'nowPrice': '100', 'prevChangePrice': '2',
        'prevChangeRate': '2.04', 'tradeVolume': '1000', 'tradeAmount': '100000',
        'marketSum': '999999', 'week52HighPrice': '120', 'quantDiff': '300',
        'tradableStatusUpdatedAt': '2026-09-23T10:00:00+09:00',
    }
    row.update(extra)
    return row


def category(no, name=None):
    return {
        'no': str(no), 'name': name or f'업종{no}', 'totalCnt': '3', 'riseCnt': '2',
        'fallCnt': '1', 'changeRate': '1.2', 'totalAccQuant': '100',
        'totalAccAmount': '200', 'totalMarketSum': '300', 'leadingItem': '2,005930,삼성전자',
        'thistime': '20260923100000',
    }


def investor_row(code='005930', name='삼성전자', amount='1000'):
    return {
        'itemcode': code, 'itemname': name, 'bizdateFrom': '20260923',
        'bizdateTo': '20260923', 'accTradeVolume': '10', 'accTradeAmount': amount,
        'dailyTradeVolume': '100', 'nowPrice': '100', 'prevChangePrice': '2',
        'prevChangeRate': '2.04', 'type': 'ST', 'estimated': False,
        'toRankingAt': '2026-09-23T00:00:00+09:00',
    }


class MarketTests(unittest.TestCase):
    CLOCK = staticmethod(lambda: '2026-09-23T12:00:00+09:00')

    def test_ranking_mapping_normalization_and_source_preservation(self):
        calls = []
        def fetch(path, params, expected):
            calls.append((path, params['orderType']))
            return [stock(code=f'{index:06d}') for index in range(1, 3)]
        payload = market.collect_rankings(fetch, self.CLOCK)
        self.assertEqual(payload['status'], 'OK')
        self.assertEqual([entry['name'] for entry in payload['rankings']], list(market.RANKINGS))
        self.assertEqual(len(calls), 7)
        row = payload['rankings'][0]['rows'][0]
        self.assertEqual(row['code'], '000001')
        self.assertEqual(row['tradingValue'], 100000)
        self.assertEqual(row['source']['tradeAmount'], '100000')

    def test_ranking_empty_invalid_and_duplicate_are_errors(self):
        def empty(path, params, expected): return []
        self.assertEqual(market.collect_rankings(empty, self.CLOCK)['status'], 'ERROR')
        def duplicate(path, params, expected): return [stock(), stock()]
        self.assertIn('duplicate', market.collect_rankings(duplicate, self.CLOCK)['rankings'][0]['error']['message'])
        def wrong(path, params, expected): return {'not': 'an array'}
        self.assertEqual(market.collect_rankings(wrong, self.CLOCK)['rankings'][0]['status'], 'ERROR')

    def test_one_ranking_failure_produces_partial(self):
        def fetch(path, params, expected):
            if params['orderType'] == 'down':
                raise market.MarketApiError('offline')
            return [stock()]
        payload = market.collect_rankings(fetch, self.CLOCK)
        self.assertEqual(payload['status'], 'PARTIAL')
        self.assertEqual(payload['rankings'][-1]['status'], 'ERROR')

    def test_industry_page_index_empty_termination_and_primary_no(self):
        pages = {
            0: [category(1), category(2)], 1: [category(3)], 2: [],
        }
        def fetch(path, params, expected): return pages[params['startIdx']]
        payload = market.collect_industries(fetch, self.CLOCK)
        self.assertEqual(payload['status'], 'OK')
        self.assertEqual(payload['count'], 3)
        self.assertEqual([row['id'] for row in payload['industries']], ['1', '2', '3'])
        self.assertEqual(payload['pagination']['endCondition'], 'EMPTY_ARRAY')
        self.assertEqual(payload['industries'][0]['source']['no'], '1')

    def test_industry_duplicate_and_max_page_are_not_silent(self):
        def duplicate(path, params, expected):
            return [category(1), category(2)] if params['startIdx'] == 0 else [category(1), category(3)]
        payload = market.collect_industries(duplicate, self.CLOCK, max_pages=3)
        self.assertEqual(payload['status'], 'PARTIAL')
        self.assertIn('duplicate', payload['error']['message'])
        def endless(path, params, expected): return [category(params['startIdx'] + 100)]
        payload = market.collect_industries(endless, self.CLOCK, max_pages=2)
        self.assertEqual(payload['status'], 'PARTIAL')
        self.assertIn('max pages', payload['error']['message'])

    def test_member_page_index_pagination(self):
        pages = {0: [stock('000001')], 1: [stock('000002')], 2: []}
        def fetch(path, params, expected): return pages[params['startIdx']]
        payload = market.collect_industry_members('297', fetch, self.CLOCK)
        self.assertEqual(payload['status'], 'OK')
        self.assertEqual([item['code'] for item in payload['members']], ['000001', '000002'])

    def test_investor_types_day_buy_sell_and_raw_fields(self):
        calls = []
        def fetch(path, params, expected):
            calls.append(params['investorType'])
            return {'sections': {'buyRankList': [investor_row()],
                                 'sellRankList': [investor_row('000660', 'SK하이닉스', '-1000')]}}
        payload = market.collect_investors(fetch, self.CLOCK)
        self.assertEqual(payload['status'], 'OK')
        self.assertEqual(calls, ['FOREIGNER', 'ORGANIZATION'])
        sell = payload['investors'][0]['sell'][0]
        self.assertEqual(sell['accTradeAmount'], -1000)
        self.assertEqual(sell['source']['accTradeAmount'], '-1000')
        self.assertNotIn('netBuyAmount', sell)

    def test_investor_missing_sections_and_partial_failure(self):
        def fetch(path, params, expected):
            if params['investorType'] == 'FOREIGNER':
                return {'sections': {'buyRankList': []}}
            return {'sections': {'buyRankList': [investor_row()], 'sellRankList': []}}
        payload = market.collect_investors(fetch, self.CLOCK)
        self.assertEqual(payload['status'], 'PARTIAL')
        self.assertEqual(payload['investors'][0]['status'], 'ERROR')

    def test_multi_period_contract_pagination_and_day_compatibility(self):
        def fetch(path, params, expected):
            page = params['startIdx']
            if params['periodType'] == 'DAY':
                return {'sections': {'buyRankList': [investor_row()], 'sellRankList': []}}
            if page == 0:
                return {'sections': {'buyRankList': [investor_row('000001')],
                                     'sellRankList': [investor_row('000002', amount='-2')]}}
            if page == 1:
                return {'sections': {'buyRankList': [investor_row('000003')], 'sellRankList': []}}
            return {'sections': {'buyRankList': [], 'sellRankList': []}}
        day = market.collect_investors(fetch, self.CLOCK)
        multi = market.collect_multi_period_investors(fetch, self.CLOCK)
        self.assertEqual(day['periodType'], 'DAY')
        self.assertEqual([x['investorType'] for x in day['investors']], list(market.INVESTOR_TYPES))
        week = multi['periods']['WEEK']['investors'][0]
        self.assertEqual(week['pagination']['endCondition'], 'EMPTY_ARRAY')
        self.assertEqual(week['pagination']['pagesFetched'], 3)
        self.assertEqual([x['code'] for x in week['buy']], ['000001', '000003'])
        self.assertEqual(week['sell'][0]['accTradeAmount'], -2)
        self.assertNotIn('netBuy', week['sell'][0])

    def test_membership_aggregates_and_retains_partial_error(self):
        def fetch(path, params, expected):
            if path.endswith('/upjong/list'):
                return [category(1), category(2)] if params['startIdx'] == 0 else []
            category_id = path.split('/')[-2]
            if category_id == '1':
                if params['marketType'] == 'KOSPI':
                    return {'hasNext': False, 'items': [{'itemCode': '000001', 'itemName': 'A', 'marketType': 'KOSPI'}]}
                return {'hasNext': False, 'items': []}
            raise market.MarketApiError('temporary failure')
        payload = market.collect_industry_membership(fetch, self.CLOCK)
        self.assertEqual(payload['status'], 'PARTIAL')
        self.assertEqual(payload['byCode']['000001'], [{'id': '1', 'name': '업종1'}])
        self.assertEqual(payload['industries'][1]['status'], 'ERROR')

    def test_cursor_industry_membership_merges_markets_and_pages(self):
        calls = []
        def fetch(path, params, expected):
            calls.append((path, params))
            market_type = params.get('marketType')
            cursor = params.get('cursor')
            if market_type == 'KOSPI':
                return {'hasNext': True, 'cursor': 'k1',
                        'items': [{'itemCode': '000001', 'itemName': 'A', 'marketType': 'KOSPI'}]} if not cursor else {
                            'hasNext': False, 'items': [{'itemCode': '000002', 'itemName': 'B', 'marketType': 'KOSPI'}]}
            return {'hasNext': False, 'items': [{'itemCode': '000003', 'itemName': 'C', 'marketType': 'KOSDAQ'}]}
        payload = market.collect_industry_members('1', fetch, self.CLOCK,
                                                  market_types=('KOSPI', 'KOSDAQ'))
        self.assertEqual(payload['status'], 'OK')
        self.assertEqual([row['code'] for row in payload['members']], ['000001', '000002', '000003'])
        self.assertEqual(payload['pagination']['type'], 'CURSOR')
        self.assertEqual(calls[1][1]['cursor'], 'k1')

    def test_manifest_and_atomic_current_error_write(self):
        rankings = {'generatedAt': 'x', 'source': 'NAVER', 'status': 'ERROR', 'rankings': []}
        industries = {'generatedAt': 'x', 'source': 'NAVER', 'status': 'OK', 'industries': []}
        investors = {'generatedAt': 'x', 'source': 'NAVER', 'status': 'OK', 'investors': []}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / 'rankings.json').write_text('{"status":"OK","old":true}', encoding='utf-8')
            manifest = market.write_all(rankings, industries, investors, output, self.CLOCK)
            self.assertEqual(manifest['status'], 'PARTIAL')
            stored = json.loads((output / 'rankings.json').read_text(encoding='utf-8'))
            self.assertEqual(stored['status'], 'ERROR')
            self.assertNotIn('old', stored)
            self.assertEqual(manifest['datasets']['themes']['status'], 'DISABLED_PENDING_RESEARCH_GATE')

    def test_all_never_calls_theme_and_no_write_creates_no_files(self):
        calls = []
        def fetch(path, params, expected):
            calls.append(path)
            if path.endswith('/stock/default'):
                return [stock()]
            if path.endswith('/upjong/list'):
                return []
            if path.endswith('trendForeignOrg'):
                return {'sections': {'buyRankList': [investor_row()], 'sellRankList': []}}
            raise AssertionError(path)
        with tempfile.TemporaryDirectory() as directory:
            result = market.collect_all(fetch, self.CLOCK, Path(directory), no_write=True)
            self.assertEqual(result['manifest']['datasets']['themes']['status'], 'DISABLED_PENDING_RESEARCH_GATE')
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertFalse(any('/theme/' in path for path in calls))
        self.assertEqual(result['investors']['periodType'], 'DAY')
        self.assertEqual(len(result['investors']['investors']), 2)
        self.assertIn('multiPeriod', result['investors'])

    def test_fetch_json_rejects_non_json_and_wrong_root(self):
        class Response:
            status = 200
            headers = {'Content-Type': 'text/html'}
            def read(self): return b'{}'
            def __enter__(self): return self
            def __exit__(self, *args): return False
        with self.assertRaises(market.MarketApiError):
            market.fetch_json('/x', opener=lambda request, timeout: Response(), sleep=lambda _: None)

    def test_no_write_parser_accepts_prefix_and_postfix(self):
        self.assertTrue(market.parse_args(['--no-write', 'all']).no_write)
        self.assertTrue(market.parse_args(['all', '--no-write']).no_write)


if __name__ == '__main__':
    unittest.main()
