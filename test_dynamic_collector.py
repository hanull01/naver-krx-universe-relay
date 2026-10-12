import unittest

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


if __name__ == '__main__':
    unittest.main()
