"""Offline integration contract for dynamic market, theme and candidate analysis.

This module consumes already-published payloads only.  It must never trigger a
market, theme membership, or candidate quote request.
"""
from datetime import datetime

import theme_research


THEME_PERIODS = ('daily', 'weekly', 'monthly')


def _code(row):
    if not isinstance(row, dict):
        return None
    return str(row.get('code') or row.get('itemCode') or row.get('symbol') or '') or None


def _rows(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ('datas', 'stocks', 'items', 'quotes', 'technicals', 'states'):
        if isinstance(payload.get(key), list):
            return payload[key]
    return []


def _index(payload):
    return {code: row for row in _rows(payload) if (code := _code(row))}


def _universe_memberships(universe):
    result = {}
    for group_type in ('sectors', 'themes', 'watchlists'):
        for name, codes in (universe.get(group_type, {}) if isinstance(universe, dict) else {}).items():
            for code in codes:
                result.setdefault(str(code), []).append({'type': group_type, 'name': name})
    return result


def _kiwoom_codes(discovery):
    if not isinstance(discovery, dict) or discovery.get('status') not in ('AVAILABLE', 'AVAILABLE_EMPTY'):
        return set()
    return {str(item.get('code') if isinstance(item, dict) else item)
            for item in discovery.get('symbols', []) if item}


def _published_industries(payload):
    """Accept the published industry contract without weakening validation."""
    if not isinstance(payload, dict):
        return [], None
    if payload.get('status') == 'AVAILABLE':
        return list(payload.get('industries') or []), 'STATUS_AVAILABLE'
    quality = payload.get('quality') or {}
    if quality.get('sourceStatus') == 'OK' and quality.get('industryEvidenceUsable') == 'FULL':
        return list(payload.get('industries') or []), 'QUALITY_FULL'
    return [], None


def _theme_payload(snapshot):
    try:
        collection = snapshot['collection']
        theme_research.validate_pc_theme_snapshot(collection)
        return collection
    except (KeyError, TypeError, theme_research.market.MarketApiError):
        return None


def _rank_changes(current, previous):
    if not isinstance(previous, dict):
        return {'status': 'UNAVAILABLE', 'reason': 'NO_PREVIOUS_CONTEXT', 'changes': []}
    old = {str(row.get('id')): row for row in previous.get('themes', []) if isinstance(row, dict)}
    changes = []
    for row in current:
        prior = old.get(str(row['id']))
        if prior is None:
            changes.append({'id': row['id'], 'status': 'NEW_IN_AVAILABLE_CONTEXT'})
        elif prior.get('ranking') != row['ranking'] or prior.get('changeRate') != row['changeRate']:
            changes.append({'id': row['id'], 'previousRanking': prior.get('ranking'),
                            'ranking': row['ranking'], 'previousChangeRate': prior.get('changeRate'),
                            'changeRate': row['changeRate']})
    return {'status': 'AVAILABLE', 'changes': changes}


def build_context(theme_snapshot, full_market, universe, quotes, technicals, states,
                  discovery=None, memberships=None, previous=None, strong_rank_limit=10):
    """Create a descriptive Work input payload without changing existing reports."""
    theme_collection = _theme_payload(theme_snapshot)
    quote_idx, tech_idx, state_idx = _index(quotes), _index(technicals), _index(states)
    universe_members = _universe_memberships(universe)
    kiwoom_codes = _kiwoom_codes(discovery)
    memberships = memberships or {'themes': []}
    theme_members_by_code = {}
    for membership in memberships.get('themes', []):
        for member in membership.get('members', []):
            code = str(member.get('code') or '')
            if code:
                theme_members_by_code.setdefault(code, []).append({
                    'id': str(membership.get('id')), 'name': membership.get('name'),
                })
    themes = {'status': 'UNAVAILABLE', 'reason': 'MISSING_OR_INVALID_THEME_SNAPSHOT', 'periods': {}}
    if theme_collection:
        themes = {'status': 'AVAILABLE', 'researchGate': theme_collection.get('researchGate'),
                  'periods': {}, 'quality': {}}
        ranked_sets = []
        for period in THEME_PERIODS:
            ranking = theme_collection['periods'][period]
            joined = theme_research.attach_pc_theme_analysis(
                ranking, memberships, quote_idx, tech_idx, set(universe_members))
            rows = joined['themes']
            themes['periods'][period] = {
                'status': ranking['status'], 'count': len(rows),
                'quality': ranking['quality'], 'updatedAt': ranking['updatedAt'],
                'rankChanges': _rank_changes(rows, (previous or {}).get('themes', {}).get(period)),
                'themes': rows,
            }
            ranked_sets.append({row['id'] for row in rows if row['ranking'] <= strong_rank_limit})
        common = set.intersection(*ranked_sets) if ranked_sets else set()
        daily = {row['id']: row for row in themes['periods']['daily']['themes']}
        themes['commonStrongThemes'] = [daily[code] for code in sorted(common) if code in daily]
        themes['strongRankLimit'] = strong_rank_limit
        themes['quality'] = {period: themes['periods'][period]['quality'] for period in THEME_PERIODS}

    industries, industry_validation = _published_industries(full_market)
    strong_industries = sorted(
        [row for row in industries if isinstance(row.get('changeRate'), (int, float)) and row['changeRate'] > 0],
        key=lambda row: row['changeRate'], reverse=True)

    related = {}
    for code in set(universe_members) | kiwoom_codes:
        reasons = [f"UNIVERSE_{group['type'].upper()}:{group['name']}"
                   for group in universe_members.get(code, [])]
        if code in kiwoom_codes:
            reasons.append('KIWOOM_CANDIDATE')
        if code in quote_idx:
            reasons.append('REUSED_MARKET_QUOTE')
        related[code] = {
            'code': code, 'groups': universe_members.get(code, []),
            'kiwoomCandidate': code in kiwoom_codes,
            'themeMemberships': theme_members_by_code.get(code, []),
            'selectionReasons': reasons,
            'quote': quote_idx.get(code), 'technical': tech_idx.get(code), 'state': state_idx.get(code),
            'marketDataStatus': ('AVAILABLE' if code in quote_idx and code in tech_idx and code in state_idx
                                 else 'PARTIAL' if code in quote_idx else 'UNKNOWN'),
        }
    return {
        'version': 'dynamic-monitoring-context-v1',
        'generatedAt': datetime.now().astimezone().isoformat(),
        'networkCalls': 0,
        'reuse': {'quotes': len(quote_idx), 'technicals': len(tech_idx), 'states': len(state_idx),
                  'membershipThemes': len(memberships.get('themes', []))},
        'industries': {'status': 'AVAILABLE' if industries else 'UNAVAILABLE',
                       'strong': strong_industries, 'source': 'FULL_MARKET_EVIDENCE',
                       'validation': industry_validation},
        'themes': themes,
        'relatedStocks': related,
        'policy': {'membership': 'CACHED_OR_TOP_THEMES_ON_DEMAND_ONLY',
                   'systemLeaders': 'UNKNOWN_WITHOUT_VERIFIED_MEMBERSHIP_AND_MARKET_DATA',
                   'themeFailureIsolation': 'THEME_SECTION_ONLY'},
    }
