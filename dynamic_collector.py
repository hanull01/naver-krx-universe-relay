"""Prepare optional dynamic collection without changing the production path.

The existing relay continues to collect the fixed baseline.  This module only
builds an input plan and merges validated optional rows in memory; callers may
publish the optional result only after a separate operations approval.
"""


DATASET_KEYS = ('quotes', 'technicals', 'states')


def _code(row):
    if not isinstance(row, dict):
        return None
    return str(row.get('itemCode') or row.get('code') or row.get('symbol') or '') or None


def _rows(payload):
    return list(payload.get('datas') or []) if isinstance(payload, dict) else []


def _source_time(row):
    if not isinstance(row, dict):
        return None
    return row.get('sourceTime') or row.get('asOf') or row.get('sourceDate')


def split_collection_targets(plan, baseline_codes):
    """Split a dynamic plan while retaining provenance and deduplicating codes."""
    baseline = {str(code) for code in baseline_codes if code}
    plan_codes = {str(code) for code in (plan or {}).get('collectionCodes', []) if code}
    selected = {str(code) for code in (plan or {}).get('selectedCodes', []) if code}
    optional = (plan_codes | selected) - baseline
    candidates = {str(row.get('code')): row for row in (plan or {}).get('candidates', [])
                  if isinstance(row, dict) and row.get('code')}
    return {
        'version': 'dynamic-collector-plan-v1',
        'status': 'OK',
        'baselineTargets': sorted(baseline),
        'optionalTargets': sorted(optional),
        'collectionTargets': sorted(baseline | optional),
        'provenance': {code: candidates.get(code, {}) for code in sorted(optional)},
        'networkCalls': 0,
        'failurePolicy': 'OPTIONAL_FAILURE_DOES_NOT_BLOCK_BASELINE',
    }


def merge_optional_dataset(base_payload, optional_payload, optional_targets,
                           reference_time=None):
    """Merge only compatible optional rows, leaving baseline rows untouched."""
    base_payload = dict(base_payload or {})
    base_rows = _rows(base_payload)
    base_codes = {_code(row) for row in base_rows}
    optional_targets = {str(code) for code in optional_targets if code}
    accepted, rejected, seen = [], [], set(base_codes)
    optional_payload = optional_payload if isinstance(optional_payload, dict) else {}
    for row in _rows(optional_payload):
        code = _code(row)
        reason = None
        if not code or code not in optional_targets:
            reason = 'NOT_REQUESTED'
        elif code in seen:
            reason = 'DUPLICATE_CODE'
        elif reference_time and _source_time(row) != reference_time:
            reason = 'SOURCE_TIME_MISMATCH'
        elif row.get('status') in ('error', 'stale', 'unavailable'):
            reason = 'ROW_NOT_USABLE'
        if reason:
            rejected.append({'code': code, 'reason': reason})
            continue
        accepted.append(row)
        seen.add(code)
    merged = dict(base_payload)
    merged['datas'] = base_rows + accepted
    merged['baselineCount'] = len(base_rows)
    merged['optionalRequestedCount'] = len(optional_targets)
    merged['optionalAcceptedCount'] = len(accepted)
    merged['optionalRejected'] = rejected
    merged['optionalStatus'] = 'OK' if not rejected else ('PARTIAL' if accepted else 'ERROR')
    merged['dynamicCollection'] = True
    return merged


def preserve_baseline_on_failure(base_payload, optional_targets, reason):
    """Represent an optional failure without mutating the baseline dataset."""
    merged = dict(base_payload or {})
    merged['datas'] = list(_rows(base_payload))
    merged['baselineCount'] = len(merged['datas'])
    merged['optionalRequestedCount'] = len(set(str(x) for x in optional_targets if x))
    merged['optionalAcceptedCount'] = 0
    merged['optionalRejected'] = [{'code': None, 'reason': reason}]
    merged['optionalStatus'] = 'ERROR'
    merged['dynamicCollection'] = True
    return merged
