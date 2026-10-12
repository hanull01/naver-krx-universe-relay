import tempfile
import unittest
import ssl
from pathlib import Path

import dynamic_collector


class DynamicCollectorTests(unittest.TestCase):
    def plan(self):
        return {'baselineCodes': ['B1'], 'selectedCodes': ['B1', 'O1', 'O2'],
                'collectionCodes': ['B1', 'O1', 'O2'],
                'candidates': [{'code': 'O1', 'sources': ['INDUSTRY_LEADER']},
                               {'code': 'O2', 'sources': ['KIWOOM_CANDIDATE']}]}

    def test_splits_baseline_and_optional_targets(self):
        result = dynamic_collector.split_collection_targets(self.plan(), ['B1'])
        self.assertEqual(result['baselineTargets'], ['B1'])
        self.assertEqual(result['optionalTargets'], ['O1', 'O2'])
        self.assertEqual(result['networkCalls'], 0)

    def test_merges_optional_rows_without_duplicate_lookup_or_baseline_change(self):
        base = {'status': 'ok', 'datas': [{'itemCode': 'B1', 'value': 1}]}
        optional = {'datas': [
            {'itemCode': 'O1', 'sourceTime': 't', 'value': 2},
            {'itemCode': 'B1', 'sourceTime': 't', 'value': 9},
            {'itemCode': 'X', 'sourceTime': 't', 'value': 3},
        ]}
        result = dynamic_collector.merge_optional_dataset(base, optional, ['O1', 'O2'], 't')
        self.assertEqual([row['itemCode'] for row in result['datas']], ['B1', 'O1'])
        self.assertEqual(result['datas'][0]['value'], 1)
        self.assertEqual(result['optionalAcceptedCount'], 1)
        self.assertEqual(result['optionalStatus'], 'PARTIAL')

    def test_prepares_bounded_batches_for_existing_fetch_contract(self):
        result = dynamic_collector.prepare_optional_stage(
            {'selectedCodes': [f'O{index:02d}' for index in range(78)]},
            ['B1'] + [f'O{index:02d}' for index in range(3)],
            max_batch_size=50, timeout_seconds=10, max_retries=1)
        self.assertEqual([len(batch) for batch in result['optionalBatches']], [50, 25])
        self.assertEqual(result['requestUpperBound'], 2)
        self.assertEqual(result['networkCalls'], 0)
        self.assertEqual(result['fetchContract'], 'relay.fetch_existing_quote_endpoint')

    def test_existing_fetch_connection_uses_batches_and_records_attempts(self):
        stage = dynamic_collector.prepare_optional_stage(
            {'selectedCodes': [f'O{index:02d}' for index in range(3)]}, ['B1'],
            max_batch_size=2, max_retries=1)
        calls = []
        def fetch(batch):
            calls.append(list(batch))
            return {'datas': [{'itemCode': code, 'sourceTime': 't'} for code in batch]}
        result = dynamic_collector.collect_optional_with_existing_fetch(stage, fetch, allow_network=True)
        self.assertEqual(calls, [['O00', 'O01'], ['O02']])
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(result['networkCalls'], 2)

    def test_existing_fetch_connection_isolates_failed_batch(self):
        stage = dynamic_collector.prepare_optional_stage(
            {'selectedCodes': ['O1', 'O2']}, [], max_batch_size=1, max_retries=1)
        def fetch(batch):
            if batch == ['O1']:
                raise RuntimeError('fixture failure')
            return {'datas': [{'itemCode': 'O2', 'sourceTime': 't'}]}
        result = dynamic_collector.collect_optional_with_existing_fetch(stage, fetch, allow_network=True)
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(result['errors'][0]['codes'], ['O1'])
        self.assertEqual(result['networkCalls'], 3)

    def test_optional_fetch_is_disabled_without_explicit_network_opt_in(self):
        stage = dynamic_collector.prepare_optional_stage(self.plan(), ['B1'])
        result = dynamic_collector.collect_optional_with_existing_fetch(
            stage, lambda batch: {'datas': []})
        self.assertEqual(result['status'], 'DISABLED')
        self.assertEqual(result['networkCalls'], 0)

    def test_relay_pilot_adapter_uses_cert_context_and_zero_inner_retry(self):
        calls = []
        class Relay:
            QUOTE_URL = 'https://example/'
            @staticmethod
            def fetch(url, retries=2, context=None):
                calls.append((url, retries, context))
                return {'datas': []}
        adapter = dynamic_collector.make_relay_pilot_fetcher(Relay, 'CERTIFI_CONTEXT')
        self.assertEqual(adapter(['O1'])['datas'], [])
        self.assertEqual(calls, [('https://example/O1', 0, 'CERTIFI_CONTEXT')])

    def test_tls_certificate_failure_is_not_retried_by_optional_stage(self):
        stage = dynamic_collector.prepare_optional_stage(
            {'selectedCodes': ['O1', 'O2', 'O3']}, [], max_retries=1)
        calls = []

        def fetch(batch):
            calls.append(list(batch))
            raise ssl.SSLCertVerificationError('CERTIFICATE_VERIFY_FAILED')

        result = dynamic_collector.collect_optional_with_existing_fetch(
            stage, fetch, allow_network=True)
        self.assertEqual(result['status'], 'ERROR')
        self.assertEqual(result['networkCalls'], 1)
        self.assertEqual(len(result['attempts']), 1)
        self.assertEqual(calls, [['O1', 'O2', 'O3']])

    def test_pilot_requires_three_to_five_explicit_optional_codes(self):
        stage = dynamic_collector.prepare_pilot_stage(
            {'selectedCodes': ['O1', 'O2', 'O3', 'O4'],
             'collectionCodes': ['O1', 'O2', 'O3', 'O4']}, [],
            pilot_codes=['O1', 'O2', 'O3'])
        self.assertTrue(stage['pilot'])
        self.assertFalse(stage['networkEnabled'])
        self.assertEqual(stage['optionalBatches'], [['O1', 'O2', 'O3']])

    def test_source_time_mismatch_isolated(self):
        result = dynamic_collector.merge_optional_dataset(
            {'datas': [{'itemCode': 'B1'}]},
            {'datas': [{'itemCode': 'O1', 'sourceTime': 'old'}]}, ['O1'], 'current')
        self.assertEqual(len(result['datas']), 1)
        self.assertEqual(result['optionalRejected'][0]['reason'], 'SOURCE_TIME_MISMATCH')

    def test_all_optional_failure_preserves_baseline(self):
        base = {'status': 'ok', 'datas': [{'itemCode': 'B1', 'value': 1}]}
        result = dynamic_collector.preserve_baseline_on_failure(base, ['O1', 'O2'], 'FETCH_FAILED')
        self.assertEqual(result['datas'], base['datas'])
        self.assertEqual(result['optionalStatus'], 'ERROR')
        self.assertEqual(result['optionalRequestedCount'], 2)

    def test_optional_error_row_is_not_published(self):
        result = dynamic_collector.merge_optional_dataset(
            {'datas': [{'itemCode': 'B1'}]},
            {'datas': [{'itemCode': 'O1', 'status': 'stale'}]}, ['O1'])
        self.assertEqual(result['optionalAcceptedCount'], 0)
        self.assertEqual(result['optionalRejected'][0]['reason'], 'ROW_NOT_USABLE')

    def test_optional_snapshot_is_disabled_by_default_and_atomic_when_enabled(self):
        stage = dynamic_collector.prepare_optional_stage(self.plan(), ['B1'])
        merged = dynamic_collector.merge_optional_dataset(
            {'datas': [{'itemCode': 'B1'}]},
            {'datas': [{'itemCode': 'O1', 'sourceTime': 't'},
                       {'itemCode': 'O2', 'sourceTime': 't'}]},
            stage['optionalTargets'], 't')
        self.assertEqual(dynamic_collector.build_optional_snapshot(merged, stage, 't')['status'], 'DISABLED')
        enabled = dynamic_collector.build_optional_snapshot(merged, stage, 't', enabled=True)
        self.assertEqual(enabled['status'], 'AVAILABLE')
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(dynamic_collector.write_optional_snapshot(
                enabled, Path(directory) / 'latest.json').exists())

    def test_incomplete_optional_snapshot_is_not_publishable(self):
        stage = dynamic_collector.prepare_optional_stage(self.plan(), ['B1'])
        merged = dynamic_collector.preserve_baseline_on_failure(
            {'datas': [{'itemCode': 'B1'}]}, stage['optionalTargets'], 'FETCH_FAILED')
        result = dynamic_collector.build_optional_snapshot(merged, stage, 't', enabled=True)
        self.assertEqual(result['status'], 'INVALID')
        with self.assertRaises(ValueError):
            dynamic_collector.write_optional_snapshot(result, '/tmp/should-not-publish.json')


if __name__ == '__main__':
    unittest.main()
