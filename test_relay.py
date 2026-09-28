import unittest
import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import relay
import universe_cli


class RelayTests(unittest.TestCase):
    @staticmethod
    def daily_fixture(count, incomplete=False, no_trading=False):
        bars = [{'date': f'2026-01-{index:03d}', 'close': index, 'high': 100 + index,
                 'volume': 1000 + index, 'complete': True, 'noTrading': False}
                for index in range(1, count + 1)]
        if incomplete:
            bars.append({'date': '2026-12-30', 'close': 9999, 'high': 9999, 'volume': 9999,
                         'complete': False, 'noTrading': False})
        if no_trading:
            bars.append({'date': '2026-12-31', 'close': 9998, 'high': 9998, 'volume': 9998,
                         'complete': True, 'noTrading': True})
        return {'datas': bars}

    def test_technical_indicators_from_completed_bars(self):
        result = relay.calculate_technicals('000001', 'A', self.daily_fixture(260),
                                            {'accumulatedTradingVolume': 2501})
        self.assertEqual(result['historyCount'], 260)
        self.assertEqual(result['ma5'], 258)
        self.assertEqual(result['ma20'], 250.5)
        self.assertEqual(result['ma60'], 230.5)
        self.assertEqual(result['high20'], 360)
        self.assertEqual(result['high60'], 360)
        self.assertEqual(result['high52w'], 360)
        self.assertTrue(result['high52wComplete'])
        self.assertEqual(result['avgVolume20'], 1250.5)
        self.assertEqual(result['volumeRatio20'], 2)

    def test_technical_excludes_incomplete_and_no_trading_and_handles_short_history(self):
        result = relay.calculate_technicals('000001', 'A', self.daily_fixture(19, incomplete=True,
                                            no_trading=True), {'accumulatedTradingVolume': 9999})
        self.assertEqual(result['historyCount'], 19)
        self.assertEqual(result['ma5'], 17)
        self.assertIsNone(result['ma20'])
        self.assertIsNone(result['high20'])
        self.assertIsNone(result['avgVolume20'])
        self.assertIsNone(result['volumeRatio20'])
        self.assertEqual(result['high52w'], 119)
        self.assertFalse(result['high52wComplete'])
        self.assertEqual(result['status'], 'insufficient_history')

    def test_build_technicals_uses_enabled_universe_only(self):
        universe = self.expanded_universe()
        codes = relay.universe_codes(universe)
        quotes = {'datas': [{'itemCode': code, 'accumulatedTradingVolume': 1000} for code in codes]}
        with patch.object(relay, 'universe_state', return_value=(universe, codes, codes, {})), \
             patch.object(relay, 'load_daily_for_technical', return_value=self.daily_fixture(20)) as daily, \
             patch.object(relay, 'save') as save:
            result = relay.build_technicals(quotes)
        self.assertEqual(result['count'], 3)
        self.assertEqual([row['itemCode'] for row in result['datas']], codes)
        self.assertEqual(daily.call_count, 3)
        self.assertEqual([call.args[0] for call in save.call_args_list],
                         ['data/technicals.json', 'data/technicals-lite.json'])

    def test_prior_highs_exclude_evaluation_bar_and_short_history(self):
        daily = self.daily_fixture(61)
        daily['datas'][-1]['high'] = 999
        self.assertEqual(relay.prior_highs(daily), (160, 160))
        self.assertEqual(relay.prior_highs(self.daily_fixture(20)), (None, None))

    def test_breakout_states(self):
        self.assertEqual(relay.breakout(103, 105, 100, False), 'attempt')
        self.assertEqual(relay.breakout(103, 105, 100, True), 'confirmed')
        self.assertEqual(relay.breakout(99, 105, 100, True), 'failed')
        self.assertEqual(relay.breakout(99, 100, 100, True), 'none')

    def test_state_volume_and_pullback(self):
        config = {'nearPct': 2, 'volumeElevated': 1.2, 'volumeSurge': 1.5}
        self.assertEqual(relay.volume_state(1.0, config), 'normal')
        self.assertEqual(relay.volume_state(1.2, config), 'elevated')
        self.assertEqual(relay.volume_state(1.5, config), 'surge')
        daily = self.daily_fixture(21)
        technical = {'ma20': 100, 'ma60': 90, 'volumeRatio20': 1.0, 'status': 'ok'}
        quote = {'closePrice': 101, 'highPrice': 101, 'marketStatus': 'OPEN', 'sourceTime': 'now'}
        self.assertEqual(relay.calculate_state('000001', 'A', technical, daily, quote, config)['pullbackState'], 'near_ma20')

    def test_build_states_lite_and_enabled_only(self):
        universe = self.expanded_universe(); codes = relay.universe_codes(universe)
        tech = {'datas': [{'itemCode': code, 'ma20': 10, 'ma60': 9, 'volumeRatio20': 1.0, 'status': 'ok'} for code in codes]}
        quotes = {'datas': [{'itemCode': code, 'closePrice': 10, 'highPrice': 10, 'marketStatus': 'OPEN'} for code in codes]}
        with patch.object(relay, 'universe_state', return_value=(universe, codes, codes, {})), patch.object(relay, 'load_analysis_config', return_value={'nearPct': 2, 'volumeElevated': 1.2, 'volumeSurge': 1.5}), patch.object(relay, 'load_daily_for_technical', return_value=self.daily_fixture(21)), patch.object(relay, 'save') as save:
            result = relay.build_states(quotes, tech)
        self.assertEqual(result['count'], 3)
        self.assertEqual(save.call_args_list[-1].args[0], 'data/states-lite.json')

    def test_diffusion_classification(self):
        config={'groupBroadRatio':.67,'groupModerateRatio':.5}
        base={'enabledMembers':3,'unknownCount':0,'upRatio':1,'downRatio':0,'aboveMA20CountRatio':1,'breakout20AttemptCount':0,'breakout20ConfirmedCount':0,'volumeSurgeCount':0,'leaderUpCount':0}
        self.assertEqual(relay.classify_diffusion(base,config),'broad')
        base.update(upRatio=.33,downRatio=.67,aboveMA20CountRatio=.33)
        self.assertEqual(relay.classify_diffusion(base,config),'weak')
        base.update(upRatio=.33,downRatio=.33,aboveMA20CountRatio=.33,breakout20AttemptCount=1)
        self.assertEqual(relay.classify_diffusion(base,config),'narrow')

    @staticmethod
    def expanded_universe():
        return {
            'stocks': [
                {'itemCode': '000001', 'stockName': 'A', 'enabled': True},
                {'itemCode': '000002', 'stockName': 'B', 'enabled': True},
                {'itemCode': '000003', 'stockName': 'C', 'enabled': True},
                {'itemCode': '000004', 'stockName': 'Disabled', 'enabled': False},
            ],
            'sectors': {'대형섹터': ['000001', '000002', '000003']},
            'themes': {'테마A': ['000001', '000002'], '테마B': ['000001']},
            'watchlists': {'관심': ['000001', '000003'], 'legacy33': ['000001', '000002', '000003']},
            'leaders': {'sector': {'대형섹터': ['000001', '000002']},
                        'theme': {'테마A': ['000001', '000002', '000003', '000001', '000002']},
                        'watchlist': {'관심': ['000001', '000002', '000003', '000001', '000002']}},
        }

    def test_expanded_universe_groups_and_quotes_are_deduplicated(self):
        universe = self.expanded_universe()
        _, codes, legacy, sectors = (universe, relay.universe_codes(universe),
                                     universe['watchlists']['legacy33'],
                                     {'000001': '대형섹터', '000002': '대형섹터', '000003': '대형섹터'})
        calls = []
        def fetch(url):
            requested = url.rsplit('/', 1)[-1].split(',')
            calls.append(requested)
            return {'datas': [{'itemCode': item_code} for item_code in requested]}
        def normalize(row, current):
            return {'itemCode': row['itemCode'], 'stockName': row['itemCode'], 'fresh': True,
                    'sourceTime': current.isoformat(), 'closePrice': 1, 'fluctuationsRatio': 0,
                    'accumulatedTradingVolume': 1, 'marketStatus': 'OPEN', 'delayTime': 0,
                    'status': 'ok'}
        with patch.object(relay, 'universe_state', return_value=(universe, codes, legacy, sectors)), \
             patch.object(relay, 'fetch', fetch), patch.object(relay, 'normalize_quote', normalize), \
             patch.object(relay, 'save') as save:
            result = relay.collect_quotes()
        self.assertEqual(codes, ['000001', '000002', '000003'])
        self.assertEqual(result['count'], 3)
        self.assertEqual(calls, [['000001', '000002', '000003']])
        written = {call.args[0]: call.args[1] for call in save.call_args_list}
        self.assertIn('data/groups/테마A.json', written)
        self.assertIn('data/groups/관심.json', written)
        self.assertEqual([row['itemCode'] for row in written['data/groups/테마A.json']['datas']], ['000001', '000002'])

    def test_expanded_universe_daily_is_deduplicated(self):
        universe = self.expanded_universe()
        codes = relay.universe_codes(universe)
        with patch.object(relay, 'universe_state', return_value=(universe, codes, codes, {})), \
             patch.object(relay, 'collect_daily', side_effect=lambda code: {'itemCode': code, 'status': 'ok', 'completedCount': 1}) as daily, \
             patch.object(relay, 'save'):
            relay.collect_all_daily()
        self.assertEqual(sorted(call.args[0] for call in daily.call_args_list), codes)

    def test_intraday_mode_skips_daily_network_collection_and_runs_derived_steps(self):
        quotes = {'count': 3, 'expectedCount': 3, 'datas': []}
        technicals = {'count': 3, 'datas': []}
        states = {'count': 3, 'datas': []}
        legacy = [f'{index:06d}' for index in range(33)]
        with patch.object(relay, 'collect_all_daily') as daily, \
             patch.object(relay, 'collect_quotes', return_value=quotes) as collect_quotes, \
             patch.object(relay, 'build_technicals', return_value=technicals) as build_technicals, \
             patch.object(relay, 'build_states', return_value=states) as build_states, \
             patch.object(relay, 'build_group_states') as build_group_states, \
             patch.object(relay, 'universe_state', return_value=({}, [], legacy, {})):
            self.assertEqual(relay.main(['intraday']), 0)
        daily.assert_not_called()
        collect_quotes.assert_called_once_with()
        build_technicals.assert_called_once_with(quotes)
        build_states.assert_called_once_with(quotes, technicals)
        build_group_states.assert_called_once_with(quotes, technicals, states)

    def test_daily_and_all_modes_keep_their_existing_pipeline_contracts(self):
        daily_rows = [{'itemCode': '000001', 'status': 'ok', 'completedCount': 60}]
        quotes = {'count': 1, 'expectedCount': 1, 'datas': []}
        technicals = {'count': 1, 'datas': []}
        states = {'count': 1, 'datas': []}
        legacy = [f'{index:06d}' for index in range(33)]
        with patch.object(relay, 'collect_all_daily', return_value=daily_rows) as daily, \
             patch.object(relay, 'collect_quotes', return_value=quotes) as collect_quotes, \
             patch.object(relay, 'build_technicals', return_value=technicals), \
             patch.object(relay, 'build_states', return_value=states), \
             patch.object(relay, 'build_group_states'), \
             patch.object(relay, 'universe_state', return_value=({}, [], legacy, {})):
            self.assertEqual(relay.main(['daily']), 0)
            collect_quotes.assert_not_called()
            self.assertEqual(relay.main(['all']), 0)
        self.assertEqual(daily.call_count, 2)
        collect_quotes.assert_called_once_with()

    def test_missing_or_corrupt_daily_cache_remains_visible_in_technicals(self):
        universe = self.expanded_universe()
        codes = relay.universe_codes(universe)
        quotes = {'datas': [{'itemCode': code, 'accumulatedTradingVolume': 1000} for code in codes]}
        with patch.object(relay, 'universe_state', return_value=(universe, codes, codes, {})), \
             patch.object(relay, 'load_daily_for_technical', return_value=None), \
             patch.object(relay, 'save'):
            payload = relay.build_technicals(quotes)
        self.assertEqual(payload['status'], 'error')
        self.assertEqual(payload['missingCodes'], codes)
        self.assertTrue(all(row['status'] == 'error' for row in payload['datas']))

    def test_corrupt_daily_cache_is_not_treated_as_valid_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            daily = root / 'data' / 'daily'
            daily.mkdir(parents=True)
            (daily / '000001.json').write_text('{not json', encoding='utf-8')
            with patch.object(relay, 'ROOT', root):
                self.assertIsNone(relay.load_daily_for_technical('000001'))

    def test_stale_saved_daily_cache_is_not_silently_technical_ok(self):
        daily = self.daily_fixture(60)
        daily.update({'status': 'ok', 'latestDate': '2026-09-21'})
        current = datetime(2026, 9, 23, 14, 35, tzinfo=relay.KST)
        with patch.object(relay, 'now', return_value=current):
            result = relay.calculate_technicals('000001', 'A', daily, {'accumulatedTradingVolume': 1000})
        self.assertEqual(result['status'], 'error')

    def test_universe_legacy_and_validation(self):
        universe = relay.load_universe()
        self.assertEqual(len(universe['watchlists']['legacy33']), 33)
        self.assertGreaterEqual(len(relay.universe_codes(universe)), 33)
        self.assertEqual(universe['watchlists']['legacy33'][-1], '051600')
        broken = json.loads(json.dumps(universe))
        broken['leaders']['sector']['원전·발전설비'] = ['999999']
        with self.assertRaises(ValueError):
            relay.validate_universe(broken)

    def test_lite_payload_is_minimal(self):
        payload = {'generatedAt': 'now', 'expectedCount': 1, 'count': 1, 'freshCount': 1,
                   'missingCodes': [], 'status': 'ok', 'fresh': True,
                   'datas': [{'itemCode': '051600', 'stockName': '한전KPS', 'closePrice': 1,
                              'fluctuationsRatio': 0, 'accumulatedTradingVolume': 2,
                              'sourceTime': 'now', 'marketStatus': 'CLOSE', 'delayTime': 0,
                              'fresh': True, 'status': 'ok', 'unwanted': 'x'}]}
        result = relay.lite_payload(payload)
        self.assertEqual(set(result['datas'][0]), set(relay.LITE_FIELDS))
        self.assertNotIn('unwanted', result['datas'][0])

    def test_legacy_fresh_count_uses_legacy_codes_only(self):
        rows = {'000001': {'fresh': True, 'sourceTime': 'a'},
                '000002': {'fresh': True, 'sourceTime': 'b'}}
        result = relay.quote_payload(rows, ['000001'], 1, [], datetime.now(relay.KST))
        self.assertEqual(result['freshCount'], 1)
    def test_freshness(self):
        current = datetime(2026, 9, 23, 10, 40, tzinfo=relay.KST)
        for minute, expected in [(35, True), (29, False), (42, False)]:
            traded = current.replace(minute=minute)
            self.assertEqual(relay.quote_freshness(traded, current, 'OPEN', 0)[0], expected)
        self.assertFalse(relay.quote_freshness(current, current, 'OPEN', 20)[0])
        self.assertFalse(relay.quote_freshness(current, current, 'OPEN', None)[0])
        monday = datetime(2026, 9, 21, 8, 35, tzinfo=relay.KST)
        friday = datetime(2026, 9, 18, 15, 30, tzinfo=relay.KST)
        self.assertTrue(relay.quote_freshness(friday, monday, 'CLOSE', 0)[0])
        self.assertFalse(relay.quote_freshness(friday, monday, 'OPEN', 0)[0])

    def test_2026_market_sessions_and_freshness(self):
        day = datetime(2026, 9, 23, tzinfo=relay.KST)
        cases = [(14, 0, 13, 55, 'REGULAR', True), (15, 40, 15, 30, 'REGULAR_CLOSED', True),
                 (16, 40, 16, 35, 'AFTER', True), (19, 40, 19, 35, 'AFTER', True),
                 (20, 40, 20, 0, 'CLOSED', True), (14, 0, 13, 40, 'REGULAR', False),
                 (16, 40, 16, 20, 'AFTER', False)]
        for hour, minute, source_hour, source_minute, session, fresh in cases:
            current = day.replace(hour=hour, minute=minute)
            traded = day.replace(hour=source_hour, minute=source_minute)
            self.assertEqual(relay.detect_market_session(current), session)
            self.assertEqual(relay.quote_freshness(traded, current, 'OPEN', 0)[0], fresh)
        self.assertFalse(relay.quote_freshness(day.replace(hour=19, minute=59), day.replace(hour=20, minute=40), 'OPEN', 0)[0])
        self.assertFalse(relay.quote_freshness(day.replace(day=22, hour=15, minute=30), day.replace(hour=15, minute=40), 'OPEN', 0)[0])
        self.assertFalse(relay.quote_freshness(day.replace(hour=14, minute=5), day.replace(hour=14), 'OPEN', 0)[0])

    def test_quote_json_includes_session(self):
        current = datetime(2026, 9, 23, 16, 40, tzinfo=relay.KST)
        row = dict(itemCode='005930', stockName='삼성전자', localTradedAt=current.isoformat(), marketStatus='OPEN', stockExchangeType={'delayTime': 0}, closePrice='1', compareToPreviousClosePrice='0', compareToPreviousPrice={}, fluctuationsRatio='0', openPrice='1', highPrice='1', lowPrice='1', accumulatedTradingVolume='1', accumulatedTradingValue='1')
        self.assertEqual(relay.normalize_quote(row, current)['session'], 'AFTER')

    def test_numeric_units_and_direction(self):
        current = datetime(2026, 9, 23, 10, 35, tzinfo=relay.KST)
        row = dict(itemCode='005930', stockName='삼성전자', localTradedAt=current.isoformat(),
                   marketStatus='OPEN', stockExchangeType={'delayTime': 0},
                   closePrice='100,000', compareToPreviousClosePrice='1,000',
                   compareToPreviousPrice={'code': '5'}, fluctuationsRatio='-1.0',
                   openPrice='101,000', highPrice='102,000', lowPrice='99,000',
                   accumulatedTradingVolume='1,234', accumulatedTradingValue='1조 2,345억',
                   accumulatedTradingValueRaw='1234500000000')
        result = relay.normalize_quote(row, current)
        self.assertEqual(result['compareToPreviousClosePrice'], -1000)
        self.assertEqual(result['accumulatedTradingValue'], 1234500000000)
        self.assertEqual(result['accumulatedTradingVolume'], 1234)
        self.assertTrue(result['fresh'])

    def test_numeric_parser_keeps_required_fields_strict_and_reports_field_value(self):
        self.assertEqual(relay.number('1,234', 'closePrice'), 1234)
        self.assertEqual(relay.number('+1,234', 'closePrice'), 1234)
        self.assertEqual(relay.number('-1,234.50', 'fluctuationsRatio'), -1234.5)
        with self.assertRaisesRegex(ValueError, r"field=closePrice, value='--'"):
            relay.number('--', 'closePrice')
        with self.assertRaisesRegex(ValueError, r"field=accumulatedTradingValue, value='1조 1,110억'"):
            relay.number('1조 1,110억', 'accumulatedTradingValue')
        for value in ('', '-', 'N/A', None, '-2.10%'):
            with self.assertRaisesRegex(ValueError, r"field=openPrice"):
                relay.number(value, 'openPrice')

    def test_sector_then_individual_fallback(self):
        calls = []
        def fetch(url):
            codes = url.rsplit('/', 1)[-1].split(',')
            calls.append(codes)
            if len(codes) > 1:
                raise ValueError('batch rejected')
            return {'datas': [{'itemCode': codes[0]}]}
        def normalize(row, current):
            return dict(itemCode=row['itemCode'], fresh=True, sourceTime=current.isoformat())
        with patch.object(relay, 'fetch', fetch), patch.object(relay, 'normalize_quote', normalize), patch.object(relay, 'save') as save:
            result = relay.collect_quotes()
        self.assertEqual(result['count'], result['expectedCount'])
        self.assertEqual(len(set(r['itemCode'] for r in result['datas'])), result['expectedCount'])
        self.assertEqual(len(calls[0]), result['expectedCount'])
        written = {call.args[0]: call.args[1] for call in save.call_args_list}
        self.assertEqual(written['data/core33-lite.json']['count'], 33)
        self.assertEqual(written['data/core33-lite.json']['datas'][-1]['itemCode'], '051600')
        self.assertEqual(set(written['data/core33-lite.json']['datas'][0]), set(relay.LITE_FIELDS))
        self.assertIn('data/quotes.json', written)
        self.assertIn('data/quotes-lite.json', written)
        self.assertIn('data/groups/원전·발전설비.json', written)

    def test_failure_replaces_old_data_with_error(self):
        with patch.object(relay, 'fetch', side_effect=ValueError('upstream unavailable')), patch.object(relay, 'save') as save:
            result = relay.collect_daily('005930')
        self.assertEqual(result['status'], 'error')
        payload = save.call_args.args[1]
        self.assertFalse(payload['fresh'])
        self.assertEqual(payload['datas'], [])
        self.assertTrue(payload['errors'])


class UniverseCliTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / 'universe.json'
        self.path.write_text(json.dumps(RelayTests.expanded_universe(), ensure_ascii=False), encoding='utf-8')
        self.path_patch = patch.object(universe_cli, 'UNIVERSE_PATH', self.path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.tempdir.cleanup()

    def run_cli(self, *args):
        universe_cli.run(universe_cli.parser().parse_args(list(args)))

    def universe(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def test_add_stock(self):
        self.run_cli('add-stock', '000005', '--name', '신규', '--theme', '테마A')
        data = self.universe()
        self.assertIn('000005', [stock['itemCode'] for stock in data['stocks']])
        self.assertIn('000005', data['themes']['테마A'])
        self.assertTrue(next(stock for stock in data['stocks'] if stock['itemCode'] == '000005')['enabled'])

    def test_add_stock_disabled_and_existing_state_preserved(self):
        self.run_cli('add-stock', '000005', '비활성', '--disabled')
        self.assertFalse(next(stock for stock in self.universe()['stocks'] if stock['itemCode'] == '000005')['enabled'])
        self.run_cli('add-stock', '000005', '비활성', '--theme', '테마A')
        self.assertFalse(next(stock for stock in self.universe()['stocks'] if stock['itemCode'] == '000005')['enabled'])

    def test_remove_stock(self):
        self.run_cli('remove-stock', '000001')
        data = self.universe()
        self.assertNotIn('000001', [stock['itemCode'] for stock in data['stocks']])
        self.assertNotIn('000001', data['themes']['테마A'])

    def test_disable_and_enable_stock(self):
        self.run_cli('disable-stock', '000001')
        self.assertFalse(self.universe()['stocks'][0]['enabled'])
        self.run_cli('enable-stock', '000001')
        self.assertTrue(self.universe()['stocks'][0]['enabled'])

    def test_add_theme_and_add_to_theme(self):
        self.run_cli('add-theme', '새테마')
        self.run_cli('add-to-theme', '새테마', '000001')
        self.assertEqual(self.universe()['themes']['새테마'], ['000001'])

    def test_set_leaders_accepts_variable_lengths(self):
        self.run_cli('set-leaders', 'sector', '대형섹터', '000001', '000002')
        self.assertEqual(len(self.universe()['leaders']['sector']['대형섹터']), 2)
        self.run_cli('set-leaders', 'theme', '테마A', '000001', '000002', '000003', '000001', '000002')
        self.assertEqual(len(self.universe()['leaders']['theme']['테마A']), 5)

    def test_set_leaders_rejects_unknown_stock(self):
        with self.assertRaises(ValueError):
            self.run_cli('set-leaders', 'theme', '테마A', '999999')

    def test_dry_run_does_not_modify_file(self):
        before = self.path.read_text(encoding='utf-8')
        self.run_cli('--dry-run', 'add-theme', '저장안됨')
        self.assertEqual(self.path.read_text(encoding='utf-8'), before)

    def test_validate(self):
        self.run_cli('validate')

    def test_publish_prefix_and_postfix_parser_forms(self):
        prefix = universe_cli.parser().parse_args(['--publish', 'add-stock', '272210', '한화시스템'])
        postfix = universe_cli.parser().parse_args(['add-stock', '272210', '한화시스템', '--publish', '--message', 'custom'])
        self.assertTrue(prefix.publish)
        self.assertTrue(postfix.publish)
        self.assertEqual(postfix.message, 'custom')

    def test_local_only_does_not_publish_and_dry_run_publish_does_not_call(self):
        with patch.object(universe_cli, 'publish_universe') as publish:
            self.run_cli('add-stock', '272210', '한화시스템')
            self.assertFalse(publish.called)
            self.run_cli('add-stock', '272211', '테스트', '--publish', '--dry-run')
            self.assertFalse(publish.called)

    def test_rename_stock_preserves_memberships_and_enabled(self):
        before = self.universe()
        self.run_cli('rename-stock', '000001', '새이름')
        after = self.universe()
        self.assertEqual(after['stocks'][0]['stockName'], '새이름')
        self.assertEqual(after['stocks'][0]['enabled'], before['stocks'][0]['enabled'])
        self.assertEqual(after['sectors'], before['sectors'])
        self.assertEqual(after['themes'], before['themes'])
        self.assertEqual(after['watchlists'], before['watchlists'])
        self.assertEqual(after['leaders'], before['leaders'])

    def test_chat_friendly_add_stock_creates_multiple_groups(self):
        self.run_cli('add-stock', '272210', '한화시스템', '--enabled', '--sector', '방산',
                     '--theme', '우주항공', '--theme', '방산전자', '--watchlist', '관심종목', '--create-groups')
        data = self.universe()
        self.assertIn('272210', data['sectors']['방산'])
        self.assertIn('272210', data['themes']['우주항공'])
        self.assertIn('272210', data['themes']['방산전자'])

    def test_add_stock_requires_existing_group_without_create_flag(self):
        with self.assertRaises(ValueError):
            self.run_cli('add-stock', '272210', '한화시스템', '--theme', '우주항공')

    def test_apply_is_atomic_and_dry_run_is_unchanged(self):
        before = self.path.read_text(encoding='utf-8')
        payload = '{"stock":{"itemCode":"272210","stockName":"한화시스템","enabled":true},"themes":["우주항공"],"createGroups":true}'
        self.run_cli('apply', payload, '--dry-run')
        self.assertEqual(self.path.read_text(encoding='utf-8'), before)
        self.run_cli('apply', payload)
        self.assertIn('272210', [s['itemCode'] for s in self.universe()['stocks']])


if __name__ == '__main__':
    unittest.main()
