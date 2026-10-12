"""Offline dynamic monitoring target selection.

This module prepares a deduplicated collection plan from already-published
context.  It does not change the production Universe or perform requests.
"""


USER_WATCHLIST_NAMES = {'legacy33'}


def _code(row):
    if isinstance(row, dict):
        value = row.get('code') or row.get('itemCode') or row.get('symbol')
    else:
        value = row
    return str(value) if value else None


def _add(candidates, code, source, reason, source_time=None, **extra):
    if not code:
        return
    row = candidates.setdefault(code, {
        'code': code, 'sources': [], 'reasons': [], 'sourceTimes': [],
    })
    if source not in row['sources']:
        row['sources'].append(source)
    if reason not in row['reasons']:
        row['reasons'].append(reason)
    if source_time and source_time not in row['sourceTimes']:
        row['sourceTimes'].append(source_time)
    for key, value in extra.items():
        if value is not None and key not in row:
            row[key] = value


def _user_codes(universe):
    watchlists = universe.get('watchlists', {}) if isinstance(universe, dict) else {}
    return {
        _code(code): name
        for name, codes in watchlists.items()
        if name not in USER_WATCHLIST_NAMES
        for code in codes
        if _code(code)
    }


def build_dynamic_universe(dynamic_context, universe, discovery=None,
                           baseline_codes=(), previous=None, top_n=3):
    """Build a staged target plan while preserving the fixed baseline.

    ``selectedCodes`` are new dynamic candidates; ``collectionCodes`` also
    contains the existing enabled baseline so the production 42-stock path is
    unchanged until a separate operations approval.
    """
    if top_n < 1:
        raise ValueError('top_n must be positive')
    dynamic_context = dynamic_context if isinstance(dynamic_context, dict) else {}
    candidates = {}
    insufficient = []

    industries = dynamic_context.get('industries') or {}
    for industry in industries.get('strong', []) if isinstance(industries, dict) else []:
        industry_name = industry.get('name')
        for leader in (industry.get('leaders') or [])[:top_n]:
            code = _code(leader)
            if not code:
                continue
            _add(candidates, code, 'INDUSTRY_LEADER',
                 f"industry:{industry_name or 'UNKNOWN'}",
                 industry.get('sourceTime') or industry.get('generatedAt'),
                 industry=industry_name)

    themes = dynamic_context.get('themes') or {}
    if themes.get('status') == 'AVAILABLE':
        daily = themes.get('periods', {}).get('daily', {})
        for theme in daily.get('themes', []) if isinstance(daily, dict) else []:
            leaders = theme.get('systemLeaders') or []
            if not leaders:
                insufficient.append({'type': 'THEME_LEADER', 'theme': theme.get('name'),
                                     'reason': 'VERIFIED_SYSTEM_LEADER_DATA_MISSING'})
                continue
            for leader in leaders[:top_n]:
                code = _code(leader)
                if code:
                    _add(candidates, code, 'THEME_LEADER',
                         f"theme:{theme.get('name') or 'UNKNOWN'}",
                         theme.get('updatedAt'), theme=theme.get('name'))
    else:
        insufficient.append({'type': 'THEME_LEADER',
                             'reason': 'THEME_SNAPSHOT_UNAVAILABLE'})

    for item in (discovery or {}).get('symbols', []) if isinstance(discovery, dict) else []:
        code = _code(item)
        if code:
            _add(candidates, code, 'KIWOOM_CANDIDATE', 'kiwoom:confirmed',
                 (discovery or {}).get('syncedAt'))

    user_codes = _user_codes(universe)
    for code, group_name in user_codes.items():
        _add(candidates, code, 'USER_WATCHLIST', f'watchlist:{group_name}',
             (universe or {}).get('generatedAt'))

    baseline = {str(code) for code in baseline_codes if code}
    selected = set(candidates)
    collection_codes = sorted(baseline | selected)
    previous_codes = set((previous or {}).get('selectedCodes', []))
    return {
        'version': 'dynamic-universe-plan-v1',
        'status': 'OK',
        'networkCalls': 0,
        'baselineCodes': sorted(baseline),
        'selectedCodes': sorted(selected),
        'collectionCodes': collection_codes,
        'candidateCount': len(selected),
        'collectionCount': len(collection_codes),
        'candidates': [candidates[code] for code in sorted(candidates)],
        'addedCodes': sorted(selected - previous_codes) if previous else sorted(selected),
        'exitedCodes': sorted(previous_codes - selected) if previous else [],
        'preservedUserCodes': sorted(user_codes),
        'insufficientData': insufficient,
        'plannedAdditionalQuoteTargets': sorted(selected - baseline),
        'estimatedAdditionalQuoteLookups': len(selected - baseline),
        'policy': {
            'baselinePreservedUntilOperationsApproval': True,
            'userWatchlistsNeverAutoDeleted': True,
            'kiwoomSourcePreserved': True,
            'deduplicateBy': 'itemCode',
            'failureAction': 'KEEP_LAST_GOOD_PLAN',
        },
    }
