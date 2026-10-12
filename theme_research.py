"""Research-only dynamic NAVER theme collection and analysis.

This module intentionally is not called by :mod:`naver_market`.  The theme
list pagination contract remains under research until a live sample proves a
complete, non-repeating termination condition.
"""
from collections import defaultdict

import naver_market as market


THEME_LIST_PATH = '/api/domestic/market/theme/list'
THEME_MEMBER_PATH = '/api/domestic/market/theme/{theme_id}/stocklist'


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
