"""Research-only dynamic NAVER theme collection and analysis.

This module intentionally is not called by :mod:`naver_market`.  The theme
list pagination contract remains under research until a live sample proves a
complete, non-repeating termination condition.
"""
from collections import defaultdict
import json
from pathlib import Path

import naver_market as market


THEME_LIST_PATH = '/api/domestic/market/theme/list'
THEME_MEMBER_PATH = '/api/domestic/market/theme/{theme_id}/stocklist'
PC_THEME_RANKING_PATH = '/api/stockSecurity/rankings/v2/domestic/themes'
PC_THEME_PERIODS = ('daily', 'weekly', 'monthly')
PC_THEME_PAGE_SIZE = 20
PC_THEME_MAX_PAGES = 50


def _normalize_pc_theme(row):
    market.require_fields(row, ('code', 'name', 'ranking', 'changeRate', 'updatedAt'), 'pc theme')
    return {
        'id': str(row['code']), 'name': row['name'], 'ranking': market.numeric(row['ranking']),
        'changeRate': market.numeric(row['changeRate']),
        'risingCount': market.numeric(row.get('risingCount')),
        'fallingCount': market.numeric(row.get('fallingCount')),
        'unchangedCount': market.numeric(row.get('unchangedCount')),
        'totalTradingValue': market.numeric(row.get('totalTradingValue')),
        'totalTradingVolume': market.numeric(row.get('totalTradingVolume')),
        'totalMarketCap': market.numeric(row.get('totalMarketCap')),
        'naverTopByChangeRate': list(row.get('topByChangeRate') or []),
        'naverTopByTradingValue': list(row.get('topByTradingValue') or []),
        'naverTopByTradingVolume': list(row.get('topByTradingVolume') or []),
        'updatedAt': row['updatedAt'], 'source': dict(row),
    }


def collect_pc_theme_ranking(period, fetcher=market.fetch_json, clock=market.generated_at,
                             max_pages=PC_THEME_MAX_PAGES):
    """Read the PC cursor ranking, failing closed on cursor/ID/time anomalies."""
    if period not in PC_THEME_PERIODS:
        raise ValueError(f'unsupported PC theme period: {period}')
    started_at = clock()
    themes, seen_ids, seen_cursors, diagnostics, source_times = [], set(), set(), [], set()
    cursor = None
    error = None
    completed = False
    for page in range(max_pages):
        params = {'sortType': 'changeRate', 'size': PC_THEME_PAGE_SIZE,
                  'excludeCodes': '25', 'period': period}
        if cursor is not None:
            params['cursor'] = cursor
        try:
            payload = fetcher(PC_THEME_RANKING_PATH, params, dict)
            if not isinstance(payload.get('items'), list) or not isinstance(payload.get('hasNext'), bool):
                raise market.MarketApiError('pc theme response missing items/hasNext contract')
            rows = [_normalize_pc_theme(row) for row in payload['items']]
            ids = [row['id'] for row in rows]
            duplicate = seen_ids.intersection(ids)
            source_times.update(row['updatedAt'] for row in rows)
            next_cursor = payload.get('cursor')
            diagnostics.append({'page': page, 'count': len(rows),
                                'duplicateCount': len(duplicate),
                                'hasNext': payload['hasNext'],
                                'cursorPresent': bool(next_cursor)})
            if duplicate or len(ids) != len(set(ids)):
                raise market.MarketApiError(f'pc theme duplicate code: {sorted(duplicate or set(ids))[:3]}')
            seen_ids.update(ids)
            themes.extend(rows)
            if not payload['hasNext']:
                completed = True
                break
            if not isinstance(next_cursor, str) or not next_cursor:
                raise market.MarketApiError('pc theme hasNext without cursor')
            if next_cursor in seen_cursors:
                raise market.MarketApiError('pc theme cursor repeated')
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        except Exception as exc:
            error = market.error_payload(exc)
            break
    if not completed and error is None:
        error = market.error_payload(market.MarketApiError(
            f'pc theme max pages reached: {max_pages}'))
    time_consistent = len(source_times) <= 1
    status = 'OK' if completed else ('PARTIAL' if themes else 'ERROR')
    payload = {'generatedAt': clock(), 'collectionStartedAt': started_at,
               'collectionCompletedAt': clock(), 'source': 'NAVER', 'status': status,
               'researchGate': 'DISABLED_PENDING_OPERATIONAL_APPROVAL',
               'period': period, 'count': len(themes), 'uniqueThemeCount': len(seen_ids),
               'updatedAt': sorted(source_times),
               'updatedAtConsistent': time_consistent,
               'updatedAtMeaning': 'UNKNOWN',
               'quality': {
                   'pageCompletion': 'COMPLETE' if completed else 'INCOMPLETE',
                   'duplicateCodes': len(themes) - len(seen_ids),
                   'cursorIntegrity': 'OK' if completed else 'FAILED',
                   'snapshotConsistency': 'CONSISTENT' if time_consistent else 'NON_ATOMIC',
               },
               'pagination': {'type': 'CURSOR', 'parameter': 'cursor', 'pagesFetched': len(diagnostics),
                              'endCondition': 'HAS_NEXT_FALSE' if completed else 'UNCONFIRMED'},
               'pageDiagnostics': diagnostics, 'themes': themes}
    if error:
        payload['error'] = error
    return payload


def collect_pc_theme_periods(fetcher=market.fetch_json, clock=market.generated_at,
                             max_pages=PC_THEME_MAX_PAGES):
    """Collect periods independently; no failing period is relabelled as success."""
    periods = {period: collect_pc_theme_ranking(period, fetcher, clock, max_pages)
               for period in PC_THEME_PERIODS}
    return {'generatedAt': clock(), 'source': 'NAVER', 'status': market.status_from(periods.values()),
            'researchGate': 'DISABLED_PENDING_OPERATIONAL_APPROVAL', 'periods': periods}


def compare_pc_theme_periods(periods):
    """Describe period set differences without treating absence as deletion."""
    codes = {period: {row['id'] for row in payload.get('themes', [])}
             for period, payload in periods.items()}
    common = set.intersection(*codes.values()) if codes else set()
    return {'commonCodeCount': len(common),
            'periodCodeCounts': {period: len(values) for period, values in codes.items()},
            'onlyByPeriod': {period: sorted(values - common) for period, values in codes.items()}}


def build_work_theme_contract(periods):
    """Return only the fields Work may present, with source-quality context."""
    comparison = compare_pc_theme_periods(periods)
    return {'source': 'NAVER_PC_THEME_RANKING',
            'quality': {period: payload['quality'] for period, payload in periods.items()},
            'periods': {period: [{'id': row['id'], 'name': row['name'], 'ranking': row['ranking'],
                                  'changeRate': row['changeRate'], 'risingCount': row['risingCount'],
                                  'fallingCount': row['fallingCount'],
                                  'totalTradingValue': row['totalTradingValue'],
                                  'totalTradingVolume': row['totalTradingVolume'],
                                  'naverTopByChangeRate': row['naverTopByChangeRate'],
                                  'updatedAt': row['updatedAt']}
                                 for row in payload.get('themes', [])]
                        for period, payload in periods.items()},
            'comparison': comparison,
            'membershipPolicy': 'TOP_THEMES_CACHED_OR_ON_DEMAND_ONLY',
            'systemLeaderPolicy': 'UNKNOWN_WITHOUT_VERIFIED_MEMBERSHIP_AND_QUOTES'}


def validate_pc_theme_snapshot(payload):
    """Validate staged PC rankings before they may replace a prior good file."""
    periods = payload.get('periods')
    if not isinstance(periods, dict) or set(periods) != set(PC_THEME_PERIODS):
        raise market.MarketApiError('pc theme periods are incomplete')
    if payload.get('status') != 'OK':
        raise market.MarketApiError(
            'pc theme periods are not all complete: ' +
            json.dumps(pc_theme_period_diagnostics(periods), ensure_ascii=False, sort_keys=True))
    for period, row in periods.items():
        if row.get('status') != 'OK':
            raise market.MarketApiError(f'pc theme {period} is not valid')
        if row.get('pagination', {}).get('endCondition') != 'HAS_NEXT_FALSE':
            raise market.MarketApiError(f'pc theme {period} did not terminate')
        if row.get('count') != row.get('uniqueThemeCount'):
            raise market.MarketApiError(f'pc theme {period} has duplicate codes')
    return True


def pc_theme_period_diagnostics(periods):
    """Return compact per-period failure evidence safe for Actions logs.

    This intentionally exposes only pagination counters and sanitized collector
    errors.  It neither retries requests nor includes source payload rows.
    """
    diagnostics = {}
    for period in PC_THEME_PERIODS:
        row = periods.get(period, {})
        pages = row.get('pageDiagnostics') or []
        last = pages[-1] if pages else {}
        error = row.get('error') or {}
        message = str(error.get('message', ''))
        diagnostics[period] = {
            'status': row.get('status'),
            'pagesFetched': row.get('pagination', {}).get('pagesFetched', len(pages)),
            'uniqueThemeCount': row.get('uniqueThemeCount'),
            'lastHasNext': last.get('hasNext'),
            'lastCursorPresent': last.get('cursorPresent'),
            'cursorRepeated': 'cursor repeated' in message,
            'duplicateCodeCount': row.get('quality', {}).get('duplicateCodes'),
            'errorType': error.get('type'),
            'errorMessage': error.get('message'),
        }
    return diagnostics


def write_pc_theme_snapshot(payload, output_dir):
    """Atomically publish only a validated staged research snapshot."""
    validate_pc_theme_snapshot(payload)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {'version': 'naver-pc-theme-research-v1',
                'source': 'NAVER_PC_THEME_RANKING',
                'status': 'OK', 'researchGate': 'DISABLED_PENDING_OPERATIONAL_APPROVAL',
                'generatedAt': market.generated_at(), 'collection': payload,
                'workContract': build_work_theme_contract(payload['periods'])}
    market.atomic_write(output_dir / 'latest.json', snapshot)
    return snapshot


def attach_pc_theme_analysis(ranking, membership, quotes=None, technicals=None,
                             universe_codes=None):
    """Join PC rankings to explicitly supplied memberships, without new requests.

    NAVER's top-three lists remain a separate source from system-selected leaders.
    Themes without a verified supplied membership stay UNKNOWN rather than being
    treated as empty or removed.
    """
    by_id = {str(row.get('id')): row for row in membership.get('themes', [])}
    analyzed = analyze_dynamic_themes({'themes': list(by_id.values())}, quotes, technicals,
                                      universe_codes)
    systems = {str(row['id']): row for row in analyzed['groups']}
    rows = []
    for theme in ranking.get('themes', []):
        system = systems.get(str(theme['id']))
        rows.append({
            'id': theme['id'], 'name': theme['name'], 'ranking': theme['ranking'],
            'changeRate': theme['changeRate'], 'updatedAt': theme['updatedAt'],
            'naverTopByChangeRate': theme['naverTopByChangeRate'],
            'naverTopByTradingValue': theme['naverTopByTradingValue'],
            'systemLeaders': system['leaders'] if system else [],
            'membershipStatus': system['status'] if system else 'UNKNOWN',
            'qualityStatus': 'OK' if ranking['status'] == 'OK' and system else 'PARTIAL',
        })
    return {'period': ranking['period'], 'rankingStatus': ranking['status'],
            'count': len(rows), 'themes': rows,
            'unlinkedThemeCount': sum(row['membershipStatus'] == 'UNKNOWN' for row in rows)}


def collect_themes(fetcher=market.fetch_json, clock=market.generated_at,
                   max_pages=market.MAX_CATEGORY_PAGES):
    """Collect theme pages, failing closed on repeats or unconfirmed end."""
    themes, seen_ids, seen_pages = [], set(), set()
    error = None
    page_diagnostics = []
    finished = False
    for page_index in range(max_pages):
        try:
            rows = fetcher(THEME_LIST_PATH, {
                'startIdx': page_index, 'pageSize': market.PAGE_SIZE,
                'sortType': 'changeRate',
            }, list)
            signature = tuple(row.get('no') for row in rows if isinstance(row, dict))
            if signature in seen_pages:
                page_diagnostics.append({'startIdx': page_index, 'count': len(rows),
                                         'repeatedPage': True, 'duplicateCount': len(signature)})
                raise market.MarketApiError(
                    f'theme repeated page at startIdx={page_index}')
            seen_pages.add(signature)
            if not rows:
                finished = True
                break
            normalized = [market.normalize_category(row) for row in rows]
            ids = [row['id'] for row in normalized]
            duplicate = seen_ids.intersection(ids)
            page_diagnostics.append({'startIdx': page_index, 'count': len(ids),
                                     'repeatedPage': False, 'duplicateCount': len(duplicate)})
            if duplicate or len(ids) != len(set(ids)):
                raise market.MarketApiError(
                    f'theme duplicate no: {sorted(duplicate or set(ids))[:3]}')
            seen_ids.update(ids)
            themes.extend(normalized)
        except Exception as exc:
            error = market.error_payload(exc)
            break
    if not finished and error is None:
        error = market.error_payload(
            market.MarketApiError(f'theme max pages reached: {max_pages}'))
    payload = {
        'generatedAt': clock(), 'source': 'NAVER',
        'status': 'OK' if finished else ('PARTIAL' if themes else 'ERROR'),
        'researchGate': 'PENDING_LIVE_PAGINATION_CONFIRMATION',
        'pagination': {
            'type': 'PAGE_INDEX', 'parameter': 'startIdx',
            'endCondition': 'EMPTY_ARRAY' if finished else 'UNCONFIRMED',
            'pagesFetched': len(seen_pages),
        },
        'pageDiagnostics': page_diagnostics,
        'count': len(themes), 'themes': themes,
    }
    if error:
        payload['error'] = error
    return payload


def collect_theme_members(theme_id, fetcher=market.fetch_json,
                          clock=market.generated_at,
                          max_pages=market.MAX_MEMBER_PAGES):
    """Collect one theme's members with duplicate and incomplete detection."""
    members, seen_codes = [], set()
    error = None
    finished = False
    for page_index in range(max_pages):
        try:
            rows = fetcher(THEME_MEMBER_PATH.format(theme_id=theme_id), {
                'marketType': 'ALL', 'orderType': 'quantTop',
                'startIdx': page_index, 'pageSize': market.PAGE_SIZE,
            }, list)
            if not rows:
                finished = True
                break
            normalized = [market.normalize_stock(row) for row in rows]
            codes = [row['code'] for row in normalized]
            duplicate = seen_codes.intersection(codes)
            if duplicate or len(codes) != len(set(codes)):
                raise market.MarketApiError(
                    f'theme member duplicate itemcode: {sorted(duplicate or set(codes))[:3]}')
            seen_codes.update(codes)
            members.extend(normalized)
        except Exception as exc:
            error = market.error_payload(exc)
            break
    if not finished and error is None:
        error = market.error_payload(
            market.MarketApiError(f'theme member max pages reached: {max_pages}'))
    payload = {
        'generatedAt': clock(), 'source': 'NAVER',
        'status': 'OK' if finished else ('PARTIAL' if members else 'ERROR'),
        'themeId': str(theme_id), 'count': len(members),
        'pagination': {
            'type': 'PAGE_INDEX', 'parameter': 'startIdx',
            'endCondition': 'EMPTY_ARRAY' if finished else 'UNCONFIRMED',
            'pagesFetched': page_index + 1,
        },
        'members': members,
    }
    if error:
        payload['error'] = error
    return payload


def collect_theme_membership(fetcher=market.fetch_json, clock=market.generated_at,
                             max_pages=market.MAX_CATEGORY_PAGES):
    """Build an auditable theme-to-code mapping without writing production data."""
    themes = collect_themes(fetcher, clock, max_pages)
    rows, by_code = [], defaultdict(list)
    for theme in themes['themes']:
        members = collect_theme_members(theme['id'], fetcher, clock)
        row = {
            'id': theme['id'], 'name': theme['name'],
            'status': members['status'], 'memberCount': members['count'],
            'pagination': members['pagination'],
            'members': [{'code': x['code'], 'name': x['name']} for x in members['members']],
        }
        rows.append(row)
        for member in members['members']:
            by_code[member['code']].append({'id': theme['id'], 'name': theme['name']})
    status = market.status_from([themes] + rows)
    return {
        'generatedAt': clock(), 'source': 'NAVER', 'status': status,
        'researchGate': 'PENDING_LIVE_PAGINATION_CONFIRMATION',
        'themeCount': len(rows),
        'membershipCount': sum(row['memberCount'] for row in rows),
        'themes': rows, 'byCode': dict(by_code),
    }


def collect_theme_membership_for_list(themes, fetcher=market.fetch_json,
                                      clock=market.generated_at):
    """Collect members for a previously validated, stable theme list.

    The legacy list endpoint is live-sorted and can overlap while it changes.
    A validated cursor ranking snapshot supplies stable IDs; this function only
    fetches each membership page and fails closed on incomplete membership.
    """
    rows, by_code = [], defaultdict(list)
    for theme in themes:
        members = collect_theme_members(theme['id'], fetcher, clock)
        row = {'id': str(theme['id']), 'name': theme['name'],
               'status': members['status'], 'memberCount': members['count'],
               'pagination': members['pagination'],
               'members': [{'code': x['code'], 'name': x['name']} for x in members['members']]}
        if members.get('error'):
            row['error'] = members['error']
        rows.append(row)
        for member in members['members']:
            by_code[member['code']].append({'id': str(theme['id']), 'name': theme['name']})
    status = market.status_from(rows)
    return {'generatedAt': clock(), 'source': 'NAVER', 'status': status,
            'researchGate': 'PENDING_LIVE_MEMBERSHIP_CONFIRMATION',
            'themeCount': len(rows),
            'membershipCount': sum(row['memberCount'] for row in rows),
            'themes': rows, 'byCode': dict(by_code)}


def analyze_dynamic_themes(membership, quotes=None, technicals=None,
                           universe_codes=None, previous=None):
    """Aggregate verified member data; unknown inputs remain unknown."""
    quotes = quotes or {}
    technicals = technicals or {}
    allowed = set(universe_codes) if universe_codes is not None else None
    groups = []
    for theme in membership.get('themes', []):
        members = [m for m in theme.get('members', [])
                   if allowed is None or m['code'] in allowed]
        rows = [quotes.get(m['code']) for m in members]
        known = [r for r in rows if isinstance(r, dict) and isinstance(r.get('changeRate'), (int, float))]
        rising = sum(r['changeRate'] > 0 for r in known)
        falling = sum(r['changeRate'] < 0 for r in known)
        avg = sum(r['changeRate'] for r in known) / len(known) if known else None
        ranked = sorted(known, key=lambda r: (r.get('tradingValue') is not None,
                                                r.get('tradingValue') or 0,
                                                r.get('changeRate') or 0), reverse=True)
        leaders = [{'code': r.get('code'), 'name': r.get('name'),
                    'changeRate': r.get('changeRate'),
                    'technical': technicals.get(r.get('code'))}
                   for r in ranked[:3]]
        groups.append({
            'id': theme['id'], 'name': theme['name'],
            'memberCount': len(members), 'knownQuoteCount': len(known),
            'risingCount': rising, 'fallingCount': falling,
            'averageChangeRate': avg, 'leaders': leaders,
            'status': 'OK' if len(known) == len(members) else ('UNKNOWN' if not known else 'PARTIAL'),
            'sourceTime': [r.get('sourceTime') for r in known if r.get('sourceTime')],
        })
    names = {g['name'] for g in groups}
    old_names = set((previous or {}).get('groups', []))
    return {'status': 'OK', 'groups': groups,
            'changes': {'new': sorted(names - old_names),
                        'removed': sorted(old_names - names)} if previous else None}
