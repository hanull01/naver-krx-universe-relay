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
        current = datetime(2026, 9, 29, 10, 0, tzinfo=relay.KST)
        with patch.object(relay, 'universe_state', return_value=(universe, codes, legacy, sectors)), \
             patch.object(relay, 'now', return_value=current), \
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
             patch.object(relay, 'collect_regular_daily', side_effect=lambda code: {'itemCode': code, 'regularDailyStatus': 'ok'}) as regular, \
             patch.object(relay, 'save'):
            relay.collect_all_daily()
        self.assertEqual(sorted(call.args[0] for call in daily.call_args_list), codes)
        self.assertEqual(sorted(call.args[0] for call in regular.call_args_list), codes)

    def test_previous_krx_business_day_uses_verified_calendar_not_weekday_guess(self):
        no_holidays = lambda year: set()
        self.assertEqual(relay.previous_krx_business_day(datetime(2026, 9, 29).date(), no_holidays).isoformat(),
                         '2026-09-28')
        self.assertEqual(relay.previous_krx_business_day(datetime(2026, 9, 28).date(), no_holidays).isoformat(),
                         '2026-09-25')
        holidays = {'2026-09-28'}
        loader = lambda year: holidays if year == 2026 else set()
        self.assertEqual(relay.previous_krx_business_day(datetime(2026, 9, 29).date(), loader).isoformat(),
                         '2026-09-25')

    def test_preclose_uses_exact_completed_previous_business_day_without_realtime(self):
        universe = self.expanded_universe(); codes = relay.universe_codes(universe)
        current = datetime(2026, 9, 29, 8, 40, tzinfo=relay.KST)
        daily = {'datas': [
            {'date': '2026-09-28', 'complete': True, 'close': 70000},
            {'date': '2026-09-29', 'complete': False, 'close': 99999},
        ]}
        with patch.object(relay, 'universe_state', return_value=(universe, codes, codes, {})), \
             patch.object(relay, 'now', return_value=current), \
             patch.object(relay, 'previous_krx_business_day', return_value=datetime(2026, 9, 28).date()), \
             patch.object(relay, 'load_daily_for_technical', return_value=daily), \
             patch.object(relay, 'fetch') as fetch, patch.object(relay, 'save') as save:
            result = relay.collect_quotes()
        fetch.assert_not_called()
        self.assertEqual(result['count'], 3)
        self.assertTrue(all(row['closePrice'] == 70000 for row in result['datas']))
        self.assertTrue(all(row['priceBasis'] == 'PREVIOUS_KRX_CLOSE' for row in result['datas']))
        self.assertTrue(all(row['freshnessReason'] == 'previous_business_day_close' for row in result['datas']))
        self.assertIn('data/quotes-lite.json', [call.args[0] for call in save.call_args_list])

    def test_preclose_technical_cache_uses_quote_business_day_not_conservative_weekday(self):
        daily = self.daily_fixture(20)
        daily.update({'status': 'ok', 'sourceTime': '2026-09-25'})
        quote = {'accumulatedTradingVolume': 1000, 'previousBusinessDay': '2026-09-25'}
        result = relay.calculate_technicals('000001', 'A', daily, quote)
        self.assertEqual(result['status'], 'ok')

    def test_preclose_missing_exact_completed_bar_preserves_production_and_records_diagnostic(self):
        universe = self.expanded_universe(); codes = relay.universe_codes(universe)
        current = datetime(2026, 9, 29, 8, 40, tzinfo=relay.KST)
        daily = {'datas': [{'date': '2026-09-25', 'complete': True, 'close': 70000}]}
        with patch.object(relay, 'universe_state', return_value=(universe, codes, codes, {})), \
             patch.object(relay, 'now', return_value=current), \
             patch.object(relay, 'previous_krx_business_day', return_value=datetime(2026, 9, 28).date()), \
             patch.object(relay, 'load_daily_for_technical', return_value=daily), \
             patch.object(relay, 'save') as save:
            result = relay.collect_quotes()
        self.assertEqual(result['status'], 'error')
        self.assertEqual({call.args[0] for call in save.call_args_list}, {'data/status/quotes-error.json'})
        self.assertTrue(save.call_args.args[1]['preservedProductionSnapshot'])

    def test_realtime_complete_failure_preserves_production_and_pipeline_skips_derived_outputs(self):
        universe = self.expanded_universe(); codes = relay.universe_codes(universe)
        current = datetime(2026, 9, 29, 10, 0, tzinfo=relay.KST)
        def broken_quote(url):
            requested = url.rsplit('/', 1)[-1].split(',')
            return {'datas': [{'itemCode': code, 'stockName': code,
                               'localTradedAt': current.isoformat(), 'closePriceRaw': '100',
                               'compareToPreviousClosePriceRaw': '0', 'fluctuationsRatioRaw': '0',
                               'openPriceRaw': '', 'highPriceRaw': '100', 'lowPriceRaw': '100',
                               'accumulatedTradingVolumeRaw': '1', 'accumulatedTradingValueRaw': '1',
                               'stockExchangeType': {'delayTime': 0}} for code in requested]}
        with patch.object(relay, 'universe_state', return_value=(universe, codes, codes, {})), \
             patch.object(relay, 'now', return_value=current), patch.object(relay, 'fetch', broken_quote), \
             patch.object(relay, 'save') as save:
            result = relay.collect_quotes()
        self.assertEqual((result['count'], result['freshCount'], result['status']), (0, 0, 'error'))
        self.assertEqual({call.args[0] for call in save.call_args_list}, {'data/status/quotes-error.json'})
        self.assertTrue(any("field=openPrice, value=''" in error['error'] for error in result['errors']))
        unusable = {'count': 0, 'expectedCount': 3, 'freshCount': 0, 'status': 'error', 'datas': []}
        with patch.object(relay, 'collect_quotes', return_value=unusable), \
             patch.object(relay, 'build_technicals') as technicals, \
             patch.object(relay, 'universe_state', return_value=(universe, codes, codes, {})):
            self.assertTrue(relay.run_quote_pipeline())
        technicals.assert_not_called()

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

    def test_daily_cache_uses_completed_source_time_before_provisional_latest_date(self):
        current = datetime(2026, 9, 28, 15, 32, tzinfo=relay.KST)
        daily = self.daily_fixture(60)
        daily.update({'status': 'ok', 'latestDate': '2026-09-28', 'sourceTime': '2026-09-25T15:30:00+09:00'})
        with patch.object(relay, 'previous_krx_business_day', return_value=datetime(2026, 9, 25).date()):
            self.assertTrue(relay.daily_cache_is_current(daily, current))

    def test_daily_cache_uses_krx_business_day_across_holiday_gap(self):
        current = datetime(2026, 10, 6, 11, 5, tzinfo=relay.KST)
        daily = self.daily_fixture(60)
        daily.update({'status': 'ok', 'latestDate': '2026-10-02',
                      'sourceTime': '2026-10-02T15:30:00+09:00'})
        with patch.object(relay, 'previous_krx_business_day', return_value=datetime(2026, 10, 2).date()):
            self.assertTrue(relay.daily_cache_is_current(daily, current))
            stale = dict(daily, sourceTime='2026-10-01T15:30:00+09:00')
            self.assertFalse(relay.daily_cache_is_current(stale, current))

    def test_daily_cache_fails_closed_when_krx_business_day_unavailable(self):
        current = datetime(2026, 10, 6, 11, 5, tzinfo=relay.KST)
        daily = self.daily_fixture(60)
        daily.update({'status': 'ok', 'sourceTime': '2026-10-02T15:30:00+09:00'})
        with patch.object(relay, 'previous_krx_business_day', side_effect=relay.CalendarUnavailable('unavailable')):
            self.assertFalse(relay.daily_cache_is_current(daily, current))

    def test_daily_cache_keeps_today_provisional_after_regular_close(self):
        current = datetime(2026, 9, 28, 16, 31, tzinfo=relay.KST)
        current_daily = self.daily_fixture(60)
        current_daily.update({'status': 'ok', 'latestDate': '2026-09-28', 'sourceTime': '2026-09-28T15:30:00+09:00'})
        stale_daily = dict(current_daily, sourceTime='2026-09-25T15:30:00+09:00')
        self.assertFalse(relay.daily_cache_is_current(current_daily, current))
        with patch.object(relay, 'previous_krx_business_day', return_value=datetime(2026, 9, 25).date()):
            self.assertTrue(relay.daily_cache_is_current(stale_daily, current))

    def test_legacy_raw_today_complete_is_runtime_provisional_and_previous_cache_stays_valid(self):
        current = datetime(2026, 9, 29, 10, 0, tzinfo=relay.KST)
        raw = {'status': 'ok', 'sourceTime': '2026-09-29', 'datas': [
            {'date': '2026-09-28', 'close': 100, 'high': 100, 'volume': 10,
             'complete': True, 'noTrading': False},
            {'date': '2026-09-29', 'close': 999, 'high': 999, 'volume': 999,
             'complete': True, 'noTrading': False},
        ]}
        normalized = relay.normalize_raw_daily_for_technical(raw, current)
        self.assertTrue(raw['datas'][1]['complete'])  # no production rewrite
        self.assertFalse(normalized['datas'][1]['complete'])
        self.assertEqual(normalized['sourceTime'], '2026-09-28')
        with patch.object(relay, 'previous_krx_business_day', return_value=datetime(2026, 9, 28).date()):
            self.assertTrue(relay.daily_cache_is_current(normalized, current))

    def test_legacy_raw_today_falls_back_to_previous_history_without_regular_daily(self):
        current = datetime(2026, 9, 29, 10, 0, tzinfo=relay.KST)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'data/daily').mkdir(parents=True)
            completed = [{'date': '2026-09-28', 'close': 100, 'high': 100, 'volume': 10,
                          'complete': True, 'noTrading': False} for _ in range(60)]
            raw = {'status': 'ok', 'sourceTime': '2026-09-29', 'datas': completed + [
                {'date': '2026-09-29', 'close': 999, 'high': 999, 'volume': 999,
                 'complete': True, 'noTrading': False}]}
            (root / 'data/daily/000001.json').write_text(json.dumps(raw), encoding='utf-8')
            with patch.object(relay, 'ROOT', root), patch.object(relay, 'now', return_value=current), \
                 patch.object(relay, 'previous_krx_business_day', return_value=datetime(2026, 9, 28).date()):
                daily = relay.load_daily_for_technical('000001')
                technical = relay.calculate_technicals('000001', 'A', daily,
                                                       {'accumulatedTradingVolume': 10})
        self.assertEqual(len([bar for bar in daily['datas'] if bar['complete']]), 60)
        self.assertEqual(technical['asOf'], '2026-09-28')
        self.assertEqual(technical['status'], 'ok')

    def test_daily_cache_rejects_missing_or_corrupt_metadata(self):
        current = datetime(2026, 9, 28, 16, 31, tzinfo=relay.KST)
        self.assertFalse(relay.daily_cache_is_current({'status': 'ok', 'sourceTime': 'not-a-date'}, current))
        self.assertFalse(relay.daily_cache_is_current({'status': 'error'}, current))

    def test_run_quote_pipeline_fails_when_all_technical_rows_are_errors(self):
        quotes = {'count': 3, 'expectedCount': 3, 'datas': []}
        technicals = {'count': 3, 'expectedCount': 3, 'status': 'error',
                      'missingCodes': ['000001', '000002', '000003'], 'datas': []}
        states = {'count': 3, 'datas': []}
        legacy = [f'{index:06d}' for index in range(33)]
        with patch.object(relay, 'collect_quotes', return_value=quotes), \
             patch.object(relay, 'build_technicals', return_value=technicals), \
             patch.object(relay, 'build_states', return_value=states), \
             patch.object(relay, 'build_group_states'), \
             patch.object(relay, 'universe_state', return_value=({}, [], legacy, {})):
            self.assertTrue(relay.run_quote_pipeline())

    def test_intraday_pipeline_succeeds_when_legacy_cache_normalizes_to_previous_completed_day(self):
        quotes = {'count': 3, 'expectedCount': 3, 'coverageCount': 3,
                  'status': 'ok', 'datas': []}
        technicals = {'count': 3, 'expectedCount': 3, 'status': 'ok',
                      'missingCodes': [], 'datas': []}
        states = {'count': 3, 'datas': []}; legacy = [f'{index:06d}' for index in range(33)]
        with patch.object(relay, 'collect_quotes', return_value=quotes), \
             patch.object(relay, 'build_technicals', return_value=technicals), \
             patch.object(relay, 'build_states', return_value=states), \
             patch.object(relay, 'build_group_states'), \
             patch.object(relay, 'universe_state', return_value=({}, [], legacy, {})):
            self.assertFalse(relay.run_quote_pipeline())

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
        payload = {'generatedAt': 'now', 'sourceTime': 'now', 'sourceTimeLatest': 'now',
                   'expectedCount': 1, 'count': 1, 'freshCount': 1,
                   'missingCodes': [], 'status': 'ok', 'fresh': True,
                   'datas': [{'itemCode': '051600', 'stockName': '한전KPS', 'closePrice': 1,
                              'fluctuationsRatio': 0, 'accumulatedTradingVolume': 2,
                              'sourceTime': 'now', 'marketStatus': 'CLOSE', 'delayTime': 0,
                              'fresh': True, 'freshnessReason': 'after_market_live', 'session': 'AFTER',
                              'priceBasis': 'AFTER_MARKET', 'marketSessionType': 'afterMarket',
                              'status': 'ok', 'unwanted': 'x'}]}
        result = relay.lite_payload(payload)
        self.assertEqual(set(result['datas'][0]), set(relay.LITE_FIELDS))
        self.assertEqual(result['sourceTime'], 'now')
        self.assertEqual(result['sourceTimeLatest'], 'now')
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
        self.assertTrue(relay.quote_freshness(friday, monday, 'OPEN', 0)[0])

    def test_first_report_uses_only_previous_krx_close(self):
        current = datetime(2026, 9, 21, 8, 40, tzinfo=relay.KST)
        previous_close = datetime(2026, 9, 18, 15, 30, tzinfo=relay.KST)
        premarket = datetime(2026, 9, 21, 8, 35, tzinfo=relay.KST)
        self.assertTrue(relay.quote_freshness(previous_close, current, 'UNKNOWN', 0)[0])
        self.assertFalse(relay.quote_freshness(premarket, current, 'OPEN', 0)[0])

    def test_2026_market_sessions_and_freshness(self):
        day = datetime(2026, 9, 23, tzinfo=relay.KST)
        cases = [(14, 0, 13, 55, 'REGULAR', True), (15, 40, 15, 30, 'AFTER', True),
                 (15, 39, 15, 30, 'REGULAR_CLOSED', True),
                 (16, 40, 16, 35, 'AFTER', True), (19, 40, 19, 35, 'AFTER', True),
                 (20, 40, 19, 30, 'CLOSED', True), (14, 0, 13, 40, 'REGULAR', False),
                 (16, 40, 16, 20, 'AFTER', False)]
        for hour, minute, source_hour, source_minute, session, fresh in cases:
            current = day.replace(hour=hour, minute=minute)
            traded = day.replace(hour=source_hour, minute=source_minute)
            self.assertEqual(relay.detect_market_session(current), session)
            self.assertEqual(relay.quote_freshness(traded, current, 'OPEN', 0)[0], fresh)
        self.assertTrue(relay.quote_freshness(day.replace(hour=19, minute=59), day.replace(hour=20, minute=40), 'OPEN', 0)[0])
        self.assertFalse(relay.quote_freshness(day.replace(day=22, hour=15, minute=30), day.replace(hour=15, minute=40), 'OPEN', 0)[0])
        self.assertFalse(relay.quote_freshness(day.replace(hour=14, minute=5), day.replace(hour=14), 'OPEN', 0)[0])

    def test_regular_close_hold_ignores_age_only_during_short_gap(self):
        current = datetime(2026, 9, 23, 15, 39, 30, tzinfo=relay.KST)
        regular_close = datetime(2026, 9, 23, 15, 30, tzinfo=relay.KST)
        for status in ('OPEN', 'CLOSE', 'UNKNOWN'):
            self.assertEqual(relay.quote_freshness(regular_close, current, status, 0),
                             (True, 'krx_close_hold'))
        for source in (datetime(2026, 9, 23, 15, 29, 59, tzinfo=relay.KST),
                       datetime(2026, 9, 23, 15, 0, tzinfo=relay.KST)):
            self.assertEqual(relay.quote_freshness(source, current, 'UNKNOWN', 0),
                             (False, 'stale_krx_close_hold'))

    def test_after_market_final_accepts_same_day_but_rejects_previous_day(self):
        current = datetime(2026, 9, 23, 20, 40, tzinfo=relay.KST)
        for minute in (50, 30):
            self.assertTrue(relay.quote_freshness(
                datetime(2026, 9, 23, 19, minute, tzinfo=relay.KST), current, 'UNKNOWN', 0)[0])
        self.assertFalse(relay.quote_freshness(
            datetime(2026, 9, 22, 19, 30, tzinfo=relay.KST), current, 'UNKNOWN', 0)[0])

    def test_quote_json_includes_session(self):
        current = datetime(2026, 9, 23, 16, 40, tzinfo=relay.KST)
        row = dict(itemCode='005930', stockName='삼성전자', localTradedAt=current.isoformat(), marketStatus='OPEN', stockExchangeType={'delayTime': 0}, closePrice='1', compareToPreviousClosePrice='0', compareToPreviousPrice={}, fluctuationsRatio='0', openPrice='1', highPrice='1', lowPrice='1', accumulatedTradingVolume='1', accumulatedTradingValue='1')
        self.assertEqual(relay.normalize_quote(row, current)['session'], 'AFTER')

    def test_latest_valid_quote_does_not_depend_on_market_status_text(self):
        current = datetime(2026, 9, 23, 16, 50, tzinfo=relay.KST)
        row = dict(itemCode='005930', stockName='삼성전자',
                   localTradedAt='2026-09-23T16:47:00+09:00', marketStatus='OPEN',
                   stockExchangeType={'delayTime': 0}, closePrice='1',
                   compareToPreviousClosePrice='0', compareToPreviousPrice={}, fluctuationsRatio='0',
                   openPrice='1', highPrice='1', lowPrice='1', accumulatedTradingVolume='1',
                   accumulatedTradingValue='1')
        quote = relay.normalize_quote(row, current)
        self.assertTrue(quote['fresh'])
        self.assertEqual(quote['freshnessReason'], 'after_market_live')

    def test_market_status_text_is_not_a_freshness_input(self):
        current = datetime(2026, 9, 23, 16, 50, tzinfo=relay.KST)
        traded = datetime(2026, 9, 23, 16, 47, tzinfo=relay.KST)
        results = {relay.quote_freshness(traded, current, status, 0) for status in ('OPEN', 'CLOSE', 'UNKNOWN')}
        self.assertEqual(results, {(True, 'after_market_live')})

    def test_after_market_metadata_and_price_basis_are_preserved(self):
        current = datetime(2026, 9, 23, 16, 50, tzinfo=relay.KST)
        row = dict(itemCode='005930', stockName='삼성전자',
                   localTradedAt='2026-09-23T16:47:00+09:00', marketStatus='OPEN',
                   marketSessionType='afterMarket', stockExchangeType={'delayTime': 0, 'code': 'KRX'},
                   overMarketPriceInfo={'tradingSessionType': 'AFTER_MARKET', 'overPrice': '100500',
                                        'localTradedAt': '2026-09-23T16:47:00+09:00'},
                   closePrice='100500', compareToPreviousClosePrice='0', compareToPreviousPrice={},
                   fluctuationsRatio='0', openPrice='1', highPrice='1', lowPrice='1',
                   accumulatedTradingVolume='1', accumulatedTradingValue='1')
        quote = relay.normalize_quote(row, current)
        self.assertEqual(quote['closePrice'], 100500)
        self.assertEqual(quote['priceBasis'], 'AFTER_MARKET')
        self.assertEqual(quote['marketSessionType'], 'afterMarket')
        self.assertEqual(quote['stockExchangeType']['code'], 'KRX')
        self.assertEqual(quote['overMarketPriceInfo']['tradingSessionType'], 'AFTER_MARKET')
        self.assertEqual(quote['overMarketPriceInfo']['overPrice'], '100500')

    def test_after_quote_uses_top_level_price_without_over_market_and_marks_no_trade(self):
        current = datetime(2026, 9, 29, 16, 10, tzinfo=relay.KST)
        row = dict(itemCode='097800', stockName='윈팩', localTradedAt='2026-09-29T15:30:00+09:00',
                   marketSessionType='afterMarket', marketStatus='CLOSE',
                   stockExchangeType={'delayTime': 0}, closePrice='2335',
                   compareToPreviousClosePrice='0', compareToPreviousPrice={}, fluctuationsRatio='0',
                   openPrice='1', highPrice='1', lowPrice='1', accumulatedTradingVolume='1',
                   accumulatedTradingValue='1', overMarketPriceInfo=None)
        quote = relay.normalize_quote(row, current)
        self.assertEqual(quote['sessionPrice'], 2335)
        self.assertEqual(quote['sessionPriceBasis'], 'AFTER_MARKET')
        self.assertEqual(quote['sessionStatus'], 'NO_AFTER_TRADE')
        self.assertEqual(quote['overMarketPriceInfo'], {})

    def test_after_reference_uses_exact_1530_minute_and_failure_is_nonfatal(self):
        current = datetime(2026, 9, 29, 16, 10, tzinfo=relay.KST)
        row = {'itemCode': '201490', 'session': 'AFTER', 'sessionPrice': 3420}
        def minute(url):
            self.assertIn('startDateTime=202609290900&endDateTime=202609291530', url)
            return [{'localDateTime': '20260929152900', 'currentPrice': 3300},
                    {'localDateTime': '20260929153000', 'currentPrice': 3385}]
        with patch.object(relay, 'fetch', minute):
            relay.attach_after_references({'201490': row}, current)
        self.assertEqual(row['referencePrice'], 3385)
        self.assertEqual(row['referenceSourceTime'], '20260929153000')
        self.assertEqual(row['sessionChange'], 35)
        self.assertEqual(row['referenceStatus'], 'ok')
        with patch.object(relay, 'fetch', side_effect=ValueError('minute down')):
            relay.attach_after_references({'201490': row}, current)
        self.assertIsNone(row['referencePrice'])
        self.assertEqual(row['referenceStatus'], 'unavailable')

    def test_after_no_trade_is_covered_and_does_not_make_ready_payload_stale(self):
        rows = {'000001': {'fresh': False, 'sourceTime': '2026-09-29T15:30:00+09:00',
                           'sessionStatus': 'NO_AFTER_TRADE'}}
        payload = relay.quote_payload(rows, ['000001'], 1, [], datetime.now(relay.KST))
        self.assertEqual((payload['status'], payload['coverageCount'], payload['liveCount'],
                          payload['noAfterTradeCount']), ('ok', 1, 0, 1))
        self.assertFalse(relay.quote_snapshot_unusable(payload))

    def test_today_daily_row_remains_provisional_after_1540(self):
        current = datetime(2026, 9, 29, 15, 40, tzinfo=relay.KST)
        response = [{'localDate': '20260928', 'openPrice': 1, 'highPrice': 2, 'lowPrice': 1,
                     'closePrice': 2, 'accumulatedTradingVolume': 10},
                    {'localDate': '20260929', 'openPrice': 1, 'highPrice': 3, 'lowPrice': 1,
                     'closePrice': 3, 'accumulatedTradingVolume': 20}]
        with patch.object(relay, 'now', return_value=current), patch.object(relay, 'fetch', return_value=response), \
             patch.object(relay, 'save') as save:
            result = relay.collect_daily('201490')
        saved = save.call_args.args[1]
        self.assertEqual(result['status'], 'insufficient')
        self.assertTrue(saved['datas'][0]['complete'])
        self.assertFalse(saved['datas'][1]['complete'])

    def test_regular_daily_reconstructs_ohlcv_from_minutes_and_uses_me2on_1530_close(self):
        current = datetime(2026, 9, 29, 15, 40, tzinfo=relay.KST)
        rows = [
            {'localDateTime': '20260929090000', 'openPrice': 3665, 'highPrice': 3680,
             'lowPrice': 3620, 'currentPrice': 3640, 'accumulatedTradingVolume': 43446},
            {'localDateTime': '20260929090100', 'openPrice': 3630, 'highPrice': 3700,
             'lowPrice': 3595, 'currentPrice': 3605, 'accumulatedTradingVolume': 62435},
            {'localDateTime': '20260929153000', 'openPrice': 3385, 'highPrice': 3385,
             'lowPrice': 3385, 'currentPrice': 3385, 'accumulatedTradingVolume': 34378},
        ]
        bar = relay.build_regular_daily_bar(rows, current)
        self.assertEqual(bar['open'], 3665)
        self.assertEqual(bar['high'], 3700)
        self.assertEqual(bar['low'], 3385)
        self.assertEqual(bar['close'], 3385)  # Me2on verified regular close
        self.assertEqual(bar['volume'], 140259)  # minute volumes are per-minute, so sum them
        self.assertTrue(bar['complete'])
        self.assertEqual((bar['session'], bar['source'], bar['sourceTime']),
                         ('REGULAR', 'NAVER_MINUTE', '20260929153000'))

    def test_regular_daily_requires_exact_1530_and_keeps_raw_daily_independent_on_failure(self):
        current = datetime(2026, 9, 29, 15, 40, tzinfo=relay.KST)
        rows = [{'localDateTime': '20260929152900', 'openPrice': 1, 'highPrice': 1,
                 'lowPrice': 1, 'currentPrice': 1, 'accumulatedTradingVolume': 1}]
        with self.assertRaisesRegex(ValueError, '15:30'):
            relay.build_regular_daily_bar(rows, current)
        with patch.object(relay, 'fetch', side_effect=ValueError('minute down')), \
             patch.object(relay, 'save') as save:
            result = relay.collect_regular_daily('201490', current)
        self.assertEqual(result['regularDailyStatus'], 'unavailable')
        self.assertEqual(save.call_args.args[0], 'data/daily-regular/201490.json')
        self.assertEqual(save.call_args.args[1]['status'], 'unavailable')

    def test_preclose_can_merge_previous_day_regular_bar(self):
        current = datetime(2026, 9, 30, 8, 10, tzinfo=relay.KST)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data/daily').mkdir(parents=True)
            (root / 'data/daily-regular').mkdir(parents=True)

            raw = {
                'status': 'ok',
                'sourceTime': '2026-09-28',
                'datas': [
                    {'date': '2026-09-28', 'close': 270000, 'high': 285500,
                     'volume': 21346064, 'complete': True, 'noTrading': False},
                    {'date': '2026-09-29', 'close': 275000, 'high': 276000,
                     'volume': 15653425, 'complete': False, 'noTrading': False},
                ],
            }

            regular = {
                'regularDailyStatus': 'ok',
                'datas': [{
                    'date': '2026-09-29',
                    'open': 266000,
                    'high': 276000,
                    'low': 266000,
                    'close': 272500,
                    'volume': 14945615,
                    'complete': True,
                    'noTrading': False,
                    'session': 'REGULAR',
                    'source': 'NAVER_MINUTE',
                    'sourceTime': '20260929153000',
                    'barType': 'REGULAR_SESSION',
                }],
            }

            (root / 'data/daily/005930.json').write_text(
                json.dumps(raw), encoding='utf-8'
            )
            (root / 'data/daily-regular/005930.json').write_text(
                json.dumps(regular), encoding='utf-8'
            )

            with patch.object(relay, 'ROOT', root), \
                 patch.object(relay, 'now', return_value=current):
                daily = relay.load_daily_for_technical('005930')
                close = relay.load_previous_business_day_close(
                    '005930', datetime(2026, 9, 29).date()
                )

        self.assertEqual(daily['regularSessionDate'], '2026-09-29')
        self.assertEqual(daily['sourceTime'], '20260929153000')
        self.assertEqual(daily['datas'][-1]['date'], '2026-09-29')
        self.assertTrue(daily['datas'][-1]['complete'])
        self.assertEqual(daily['datas'][-1]['close'], 272500)
        self.assertEqual(close, 272500)

    def test_technicals_merge_regular_bar_but_never_raw_provisional_today(self):
        current = datetime(2026, 9, 29, 16, 0, tzinfo=relay.KST)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data/daily').mkdir(parents=True)
            (root / 'data/daily-regular').mkdir(parents=True)
            raw = {'status': 'ok', 'sourceTime': '2026-09-28', 'datas': [
                {'date': '2026-09-28', 'close': 100, 'high': 100, 'volume': 10,
                 'complete': True, 'noTrading': False},
                {'date': '2026-09-29', 'close': 999, 'high': 999, 'volume': 999,
                 'complete': False, 'noTrading': False},
            ]}
            regular = {'regularDailyStatus': 'ok', 'datas': [{
                'date': '2026-09-29', 'open': 101, 'high': 110, 'low': 100, 'close': 105,
                'volume': 20, 'complete': True, 'noTrading': False, 'session': 'REGULAR',
                'source': 'NAVER_MINUTE', 'sourceTime': '20260929153000',
                'barType': 'REGULAR_SESSION'}]}
            (root / 'data/daily/201490.json').write_text(json.dumps(raw), encoding='utf-8')
            (root / 'data/daily-regular/201490.json').write_text(json.dumps(regular), encoding='utf-8')
            with patch.object(relay, 'ROOT', root), patch.object(relay, 'now', return_value=current):
                daily = relay.load_daily_for_technical('201490')
                technical = relay.calculate_technicals('201490', '미투온', daily,
                                                       {'accumulatedTradingVolume': 20})
        self.assertEqual([bar['close'] for bar in daily['datas']], [100, 105])
        self.assertEqual(technical['asOf'], '2026-09-29')
        self.assertEqual(technical['status'], 'insufficient_history')

    def test_daily_workflow_runs_at_1540_kst_and_documents_provisional_policy(self):
        workflow = (Path(__file__).parent / '.github/workflows/refresh-daily.yml').read_text(encoding='utf-8')
        self.assertIn('cron: "40 6 * * 1-5"', workflow)
        self.assertIn("today's provisional row", workflow)

    def test_breakout_close_confirmation_uses_source_timestamp_not_market_status(self):
        config = {'nearPct': 2, 'volumeElevated': 1.2, 'volumeSurge': 1.5}
        daily = self.daily_fixture(21)
        technical = {'ma20': 100, 'ma60': 90, 'volumeRatio20': 1.0, 'status': 'ok'}
        closed = {'closePrice': 123, 'highPrice': 124, 'marketStatus': 'OPEN',
                  'sourceTime': '2026-09-23T15:30:00+09:00'}
        before_close = dict(closed, sourceTime='2026-09-23T15:29:00+09:00')
        with patch.object(relay, 'now', return_value=datetime(2026, 9, 23, 16, 0, tzinfo=relay.KST)):
            self.assertEqual(relay.calculate_state('000001', 'A', technical, daily, closed, config)['breakout20'], 'confirmed')
            self.assertEqual(relay.calculate_state('000001', 'A', technical, daily, before_close, config)['breakout20'], 'attempt')

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
        current = datetime(2026, 9, 29, 10, 0, tzinfo=relay.KST)
        with patch.object(relay, 'now', return_value=current), \
             patch.object(relay, 'fetch', fetch), patch.object(relay, 'normalize_quote', normalize), \
             patch.object(relay, 'save') as save:
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
