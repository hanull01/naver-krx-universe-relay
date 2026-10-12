"""Prepare optional dynamic collection without changing the production path.

The existing relay continues to collect the fixed baseline.  This module only
builds an input plan and merges validated optional rows in memory; callers may
publish the optional result only after a separate operations approval.
"""

import json
import os
import time
from pathlib import Path


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


def prepare_optional_stage(plan, baseline_codes, max_batch_size=50,
                           timeout_seconds=10, max_retries=1):
    """Prepare bounded optional batches for the existing relay fetch contract."""
    if max_batch_size < 1 or timeout_seconds <= 0 or max_retries < 0:
        raise ValueError('invalid optional collection limits')
    split = split_collection_targets(plan, baseline_codes)
    targets = split['optionalTargets']
    batches = [targets[index:index + max_batch_size]
               for index in range(0, len(targets), max_batch_size)]
    return {
        **split,
        'optionalBatches': batches,
        'requestUpperBound': len(batches),
        'timeoutSeconds': timeout_seconds,
        'maxRetries': max_retries,
        'fetchContract': 'relay.fetch_existing_quote_endpoint',
        'publishPolicy': 'OPTIONAL_ONLY_AFTER_ALL_BATCHES_VALID',
    }


def prepare_pilot_stage(plan, baseline_codes, pilot_codes=None, max_symbols=5):
    """Prepare an explicit 3-5 symbol pilot without enabling network access."""
    if not 3 <= max_symbols <= 5:
        raise ValueError('pilot size must be between 3 and 5')
    stage = prepare_optional_stage(plan, baseline_codes, max_batch_size=max_symbols)
    allowed = set(stage['optionalTargets'])
    requested = [str(code) for code in (pilot_codes or []) if str(code) in allowed]
    if len(requested) > max_symbols:
        raise ValueError('pilot exceeds max_symbols')
    if len(requested) < 3:
        raise ValueError('pilot requires at least 3 optional targets')
    stage['optionalTargets'] = sorted(set(requested))
    stage['optionalBatches'] = [stage['optionalTargets']]
    stage['collectionTargets'] = sorted(set(stage['baselineTargets']) | set(requested))
    stage['pilot'] = True
    stage['networkEnabled'] = False
    stage['publishPolicy'] = 'PILOT_DISABLED_UNTIL_EXPLICIT_APPROVAL'
    return stage


def collect_optional_with_existing_fetch(stage_plan, fetch_batch, allow_network=False):
    """Call an injected existing relay fetch once per bounded optional batch.

    ``fetch_batch`` must be the existing relay fetch adapter.  No network
    client or endpoint is created here, and callers must not enable this stage
    without an operations approval.
    """
    if not allow_network:
        return {'status': 'DISABLED', 'reason': 'EXPLICIT_NETWORK_OPT_IN_REQUIRED',
                'datas': [], 'errors': [], 'attempts': [], 'networkCalls': 0}
    if not callable(fetch_batch):
        raise TypeError('fetch_batch must be callable')
    rows, errors, attempts = [], [], []
    for batch in stage_plan.get('optionalBatches', []):
        batch_error = None
        for attempt in range(stage_plan.get('maxRetries', 0) + 1):
            started = time.monotonic()
            try:
                payload = fetch_batch(batch)
                if not isinstance(payload, dict) or not isinstance(payload.get('datas'), list):
                    raise ValueError('missing datas array')
                rows.extend(payload['datas'])
                attempts.append({'codes': list(batch), 'attempt': attempt + 1,
                                 'elapsedSeconds': round(time.monotonic() - started, 6),
                                 'status': 'OK'})
                batch_error = None
                break
            except Exception as exc:
                batch_error = str(exc)
                attempts.append({'codes': list(batch), 'attempt': attempt + 1,
                                 'elapsedSeconds': round(time.monotonic() - started, 6),
                                 'status': 'ERROR'})
        if batch_error is not None:
            errors.append({'codes': list(batch), 'error': batch_error})
    status = 'OK' if not errors else ('PARTIAL' if rows else 'ERROR')
    return {'status': status, 'datas': rows, 'errors': errors,
            'requestedCount': len(stage_plan.get('optionalTargets', [])),
            'attempts': attempts, 'networkCalls': len(attempts)}


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


def build_optional_snapshot(merged_payload, stage_plan, market_source_time,
                            enabled=False):
    """Build a separately publishable snapshot; disabled is the default."""
    optional_status = merged_payload.get('optionalStatus')
    requested = merged_payload.get('optionalRequestedCount', 0)
    accepted = merged_payload.get('optionalAcceptedCount', 0)
    if not enabled:
        return {'status': 'DISABLED', 'reason': 'OPERATIONS_APPROVAL_REQUIRED',
                'networkCalls': 0}
    if optional_status != 'OK' or requested != accepted:
        return {'status': 'INVALID', 'reason': 'OPTIONAL_DATA_INCOMPLETE',
                'networkCalls': 0}
    return {
        'schemaVersion': 'dynamic-optional-quotes-v1',
        'status': 'AVAILABLE', 'marketSourceTime': market_source_time,
        'collectionPlanVersion': stage_plan.get('version'),
        'targets': stage_plan.get('optionalTargets', []),
        'datas': merged_payload.get('datas', []), 'networkCalls': 0,
    }


def write_optional_snapshot(snapshot, path):
    """Atomically write only a validated AVAILABLE optional snapshot."""
    if not isinstance(snapshot, dict) or snapshot.get('status') != 'AVAILABLE':
        raise ValueError('optional snapshot is not publishable')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                         encoding='utf-8')
    os.replace(temporary, path)
    return path
