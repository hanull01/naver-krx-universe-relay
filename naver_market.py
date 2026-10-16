"""Read-only NAVER domestic market snapshots.

This collector is independent of relay.py and the configured stock Universe.
It deliberately excludes theme APIs until their pagination contract is revalidated.
"""
import argparse
import json
import os
import ssl
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

try:
    import certifi
except ImportError:  # pragma: no cover - production environments may provide system roots.
    certifi = None


ROOT = Path(__file__).resolve().parent
MARKET_DIR = ROOT / 'data' / 'market'
BASE_URL = 'https://stock.naver.com'
KST = ZoneInfo('Asia/Seoul')
USER_AGENT = 'Mozilla/5.0 (compatible; naver-krx-universe-relay/1.0)'
TIMEOUT_SECONDS = 20
MAX_RETRIES = 1
PAGE_SIZE = 20
MAX_CATEGORY_PAGES = 20
MAX_MEMBER_PAGES = 100
MEMBERSHIP_TTL_HOURS = 24
TLS_CONTEXT = ssl.create_default_context(cafile=certifi.where()) if certifi else None

RANKINGS = {
    'market_cap': 'marketSum',
    'trading_value': 'priceTop',
    'volume': 'quantTop',
    'volume_surge': 'upperQuantTop',
    'high_52week': 'high52week',
    'gainers': 'up',
    'losers': 'down',
}
INVESTOR_TYPES = ('FOREIGNER', 'ORGANIZATION')
PERIOD_TYPES = ('DAY', 'WEEK', 'MONTH', 'THREE_MONTH')


class MarketApiError(RuntimeError):
    """A public API response did not satisfy the confirmed contract."""


def generated_at():
    return datetime.now(KST).isoformat()


def error_payload(exc):
    return {'type': type(exc).__name__, 'message': str(exc)}


def build_url(path, params=None):
    query = urlencode(params or {})
    return BASE_URL + path + (('?' + query) if query else '')


def fetch_json(path, params=None, expected_type=None, opener=urlopen, sleep=time.sleep):
    """Fetch one public JSON response, with one bounded retry."""
    url = build_url(path, params)
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            request = Request(url, headers={
                'User-Agent': USER_AGENT,
                'Accept': 'application/json, text/plain, */*',
                'Referer': 'https://stock.naver.com/',
            })
            open_kwargs = {'timeout': TIMEOUT_SECONDS}
            # Keep injected test openers backward-compatible; only the real
            # urllib opener receives the explicit, verified CA bundle.
            if opener is urlopen and TLS_CONTEXT is not None:
                open_kwargs['context'] = TLS_CONTEXT
            with opener(request, **open_kwargs) as response:
                status = getattr(response, 'status', response.getcode())
                if status < 200 or status >= 300:
                    raise MarketApiError(f'HTTP {status}: {path}')
                content_type = response.headers.get('Content-Type', '')
                if 'json' not in content_type.lower():
                    raise MarketApiError(f'non-JSON content-type: {content_type or "missing"}')
                try:
                    payload = json.loads(response.read().decode('utf-8'))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise MarketApiError(f'invalid JSON: {exc}') from exc
                if expected_type is not None and not isinstance(payload, expected_type):
                    raise MarketApiError(
                        f'expected {expected_type.__name__} root, got {type(payload).__name__}'
                    )
                return payload
        except Exception as exc:  # urllib errors carry useful messages too.
            last_error = exc
            if attempt == MAX_RETRIES:
                break
            sleep(1)
    if isinstance(last_error, MarketApiError):
        raise last_error
    raise MarketApiError(f'request failed: {last_error}') from last_error


def numeric(value):
    """Parse NAVER's number strings without applying unit conversions."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).replace(',', '').strip()
    if not text:
        return None
    try:
        result = float(text)
    except ValueError as exc:
        raise MarketApiError(f'invalid numeric field: {value!r}') from exc
    return int(result) if result.is_integer() else result


def require_fields(row, fields, label):
    if not isinstance(row, dict):
        raise MarketApiError(f'{label} row is not an object')
    missing = [field for field in fields if row.get(field) in (None, '')]
    if missing:
        raise MarketApiError(f'{label} missing required fields: {", ".join(missing)}')


def validate_stock_row(row):
    require_fields(row, ('itemcode', 'itemname'), 'stock')


def validate_category_row(row):
    require_fields(row, ('no', 'name'), 'category')


def validate_investor_response(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('sections'), dict):
        raise MarketApiError('investor response missing sections object')
    sections = payload['sections']
    for name in ('buyRankList', 'sellRankList'):
        if not isinstance(sections.get(name), list):
            raise MarketApiError(f'investor response missing {name} array')
        for row in sections[name]:
            validate_stock_row(row)
    return sections


def normalize_stock(row, investor=False):
    validate_stock_row(row)
    result = {
        'code': row['itemcode'],
        'name': row['itemname'],
        'price': numeric(row.get('nowPrice')),
        'change': numeric(row.get('prevChangePrice')),
        'changeRate': numeric(row.get('prevChangeRate')),
        'source': dict(row),
    }
    if investor:
        result.update({
            'dailyVolume': numeric(row.get('dailyTradeVolume')),
            'accTradeVolume': numeric(row.get('accTradeVolume')),
            'accTradeAmount': numeric(row.get('accTradeAmount')),
            'bizdateFrom': row.get('bizdateFrom'),
            'bizdateTo': row.get('bizdateTo'),
            'toRankingAt': row.get('toRankingAt'),
            'estimated': row.get('estimated'),
        })
    else:
        result.update({
            'volume': numeric(row.get('tradeVolume')),
            'tradingValue': numeric(row.get('tradeAmount')),
            'marketCap': numeric(row.get('marketSum')),
            'high52Week': numeric(row.get('week52HighPrice')),
            'volumeDiff': numeric(row.get('quantDiff')),
        })
    return result


def normalize_industry_member(row, market_type):
    """Normalize the current browser/API stocklist contract."""
    require_fields(row, ('itemCode', 'itemName'), 'industry member')
    return {'code': str(row['itemCode']), 'name': row['itemName'],
            'marketType': row.get('marketType') or market_type, 'source': dict(row)}


def normalize_category(row):
    validate_category_row(row)
    return {
        'id': row['no'],
        'name': row['name'],
        'totalStocks': numeric(row.get('totalCnt')),
        'risingStocks': numeric(row.get('riseCnt')),
        'fallingStocks': numeric(row.get('fallCnt')),
        'changeRate': numeric(row.get('changeRate')),
        'totalVolume': numeric(row.get('totalAccQuant')),
        'totalTradingValue': numeric(row.get('totalAccAmount')),
        'totalMarketCap': numeric(row.get('totalMarketSum')),
        'leadingItem': row.get('leadingItem'),
        'sourceTime': row.get('thistime'),
        'source': dict(row),
    }


def status_from(entries):
    statuses = [entry.get('status') for entry in entries]
    if statuses and all(status == 'OK' for status in statuses):
        return 'OK'
    if any(status == 'OK' for status in statuses):
        return 'PARTIAL'
    return 'ERROR'


def collect_rankings(fetcher=fetch_json, clock=generated_at):
    results = []
    for name, order_type in RANKINGS.items():
        entry = {'name': name, 'orderType': order_type, 'status': 'ERROR', 'rows': []}
        try:
            rows = fetcher('/api/domestic/market/stock/default', {
                'tradeType': 'KRX', 'marketType': 'ALL', 'orderType': order_type,
                'startIdx': 0, 'pageSize': PAGE_SIZE,
            }, list)
            if not rows:
                raise MarketApiError('ranking returned an empty array')
            normalized = [normalize_stock(row) for row in rows]
            codes = [row['code'] for row in normalized]
            if len(codes) != len(set(codes)):
                raise MarketApiError('ranking returned duplicate itemcode')
            entry.update(status='OK', rows=normalized, rowCount=len(normalized))
        except Exception as exc:
            entry['error'] = error_payload(exc)
            entry['rowCount'] = 0
        results.append(entry)
    return {'generatedAt': clock(), 'source': 'NAVER', 'status': status_from(results),
            'rankings': results}


def collect_industries(fetcher=fetch_json, clock=generated_at, max_pages=MAX_CATEGORY_PAGES):
    categories, seen_ids, seen_pages = [], set(), set()
    error = None
    finished = False
    for page_index in range(max_pages):
        try:
            rows = fetcher('/api/domestic/market/upjong/list', {
                'startIdx': page_index, 'pageSize': PAGE_SIZE, 'sortType': 'changeRate',
            }, list)
            signature = tuple(row.get('no') for row in rows if isinstance(row, dict))
            if signature in seen_pages:
                raise MarketApiError(f'industry repeated page at startIdx={page_index}')
            seen_pages.add(signature)
            if not rows:
                finished = True
                break
            normalized = [normalize_category(row) for row in rows]
            ids = [row['id'] for row in normalized]
            duplicate = seen_ids.intersection(ids)
            if duplicate or len(ids) != len(set(ids)):
                raise MarketApiError(f'industry duplicate no: {sorted(duplicate or set(ids))[:3]}')
            seen_ids.update(ids)
            categories.extend(normalized)
        except Exception as exc:
            error = error_payload(exc)
            break
    if not finished and error is None:
        error = error_payload(MarketApiError(f'industry max pages reached: {max_pages}'))
    status = 'OK' if finished else ('PARTIAL' if categories else 'ERROR')
    payload = {'generatedAt': clock(), 'source': 'NAVER', 'status': status,
               'pagination': {'type': 'PAGE_INDEX', 'parameter': 'startIdx',
                              'endCondition': 'EMPTY_ARRAY' if finished else 'UNCONFIRMED',
                              'pagesFetched': len(seen_pages)},
               'count': len(categories), 'industries': categories}
    if error:
        payload['error'] = error
    return payload


def fetch_industry_info(category_no, fetcher=fetch_json):
    return fetcher(f'/api/domestic/market/upjong/{category_no}/info', {'marketType': 'ALL'}, dict)


def collect_industry_members(category_no, fetcher=fetch_json, clock=generated_at,
                             max_pages=MAX_MEMBER_PAGES, market_types=None):
    members, seen_codes = [], set()
    error = None
    finished = False
    if market_types is not None:
        cursors = {market: None for market in market_types}
        for market_type in market_types:
            for page_index in range(max_pages):
                try:
                    params = {'marketType': market_type, 'sortType': 'tradingVolume',
                              'sortDirection': 'desc', 'size': min(PAGE_SIZE, 100)}
                    if cursors[market_type]:
                        params['cursor'] = cursors[market_type]
                    response = fetcher(f'/api/domestic/market/upjong/{category_no}/stocklist', params, dict)
                    rows = response.get('items')
                    if not isinstance(rows, list):
                        raise MarketApiError('industry member response missing items array')
                    normalized = [normalize_industry_member(row, market_type) for row in rows]
                    codes = [row['code'] for row in normalized]
                    duplicate = seen_codes.intersection(codes)
                    if duplicate or len(codes) != len(set(codes)):
                        raise MarketApiError(f'industry member duplicate itemcode: {sorted(duplicate or set(codes))[:3]}')
                    seen_codes.update(codes); members.extend(normalized)
                    has_next = response.get('hasNext') is True
                    next_cursor = response.get('cursor')
                    if not has_next:
                        break
                    if not next_cursor or next_cursor == cursors[market_type]:
                        raise MarketApiError('industry member pagination missing or repeated cursor')
                    cursors[market_type] = next_cursor
                except Exception as exc:
                    error = error_payload(exc); break
            else:
                error = error_payload(MarketApiError(f'industry member max pages reached: {max_pages}'))
            if error:
                break
        finished = error is None
        pagination = {'type': 'CURSOR', 'parameter': 'cursor', 'endCondition': 'HAS_NEXT_FALSE' if finished else 'UNCONFIRMED',
                      'markets': list(market_types), 'pagesFetched': len(members)}
    else:
      for page_index in range(max_pages):
        try:
            rows = fetcher(f'/api/domestic/market/upjong/{category_no}/stocklist', {
                'marketType': 'ALL', 'orderType': 'quantTop', 'startIdx': page_index,
                'pageSize': PAGE_SIZE,
            }, list)
            if not rows:
                finished = True
                break
            normalized = [normalize_stock(row) for row in rows]
            codes = [row['code'] for row in normalized]
            duplicate = seen_codes.intersection(codes)
            if duplicate or len(codes) != len(set(codes)):
                raise MarketApiError(f'industry member duplicate itemcode: {sorted(duplicate or set(codes))[:3]}')
            seen_codes.update(codes)
            members.extend(normalized)
        except Exception as exc:
            error = error_payload(exc)
            break
      if not finished and error is None:
          error = error_payload(MarketApiError(f'industry member max pages reached: {max_pages}'))
      pagination = {'type': 'PAGE_INDEX', 'parameter': 'startIdx',
                    'endCondition': 'EMPTY_ARRAY' if finished else 'UNCONFIRMED',
                    'pagesFetched': (len(members) + PAGE_SIZE - 1) // PAGE_SIZE + 1 if finished else max_pages}
    payload = {'generatedAt': clock(), 'source': 'NAVER',
               'status': 'OK' if finished else ('PARTIAL' if members else 'ERROR'),
               'categoryId': str(category_no), 'count': len(members),
               'pagination': pagination,
               'members': members}
    if error:
        payload['error'] = error
    return payload


def collect_investors(fetcher=fetch_json, clock=generated_at, period_type='DAY', paginate=False,
                      max_pages=MAX_MEMBER_PAGES):
    if period_type not in PERIOD_TYPES:
        raise ValueError(f'unsupported periodType: {period_type}')
    entries = []
    for investor_type in INVESTOR_TYPES:
        entry = {'investorType': investor_type, 'periodType': period_type,
                 'status': 'ERROR', 'buy': [], 'sell': []}
        try:
            buy, sell, seen_buy, seen_sell, pages = [], [], set(), set(), 0
            for page_index in range(max_pages):
                payload = fetcher('/api/domestic/market/trend/trendForeignOrg', {
                    'investorType': investor_type, 'tradeType': 'KRX', 'marketType': 'ALL',
                    'startIdx': page_index, 'pageSize': PAGE_SIZE, 'periodType': period_type,
                }, dict)
                sections = validate_investor_response(payload); pages += 1
                page_buy = [normalize_stock(row, investor=True) for row in sections['buyRankList']]
                page_sell = [normalize_stock(row, investor=True) for row in sections['sellRankList']]
                if not paginate or (not page_buy and not page_sell):
                    if paginate and (page_buy or page_sell):
                        raise MarketApiError('investor pagination did not terminate')
                    break
                for label, rows, seen in (('buy', page_buy, seen_buy), ('sell', page_sell, seen_sell)):
                    codes = [row['code'] for row in rows]
                    duplicate = seen.intersection(codes)
                    if duplicate or len(codes) != len(set(codes)):
                        raise MarketApiError(f'investor {label} duplicate itemcode: {sorted(duplicate or set(codes))[:3]}')
                    seen.update(codes)
                buy.extend(page_buy); sell.extend(page_sell)
            else:
                raise MarketApiError(f'investor max pages reached: {max_pages}')
            entry.update(status='OK', buy=buy if paginate else page_buy,
                         sell=sell if paginate else page_sell,
                         pagination={'type': 'PAGE_INDEX', 'parameter': 'startIdx',
                                     'endCondition': 'EMPTY_ARRAY' if paginate else 'NOT_REQUESTED',
                                     'pagesFetched': pages})
        except Exception as exc:
            entry['error'] = error_payload(exc)
        entries.append(entry)
    return {'generatedAt': clock(), 'source': 'NAVER', 'status': status_from(entries),
            'periodType': period_type, 'investors': entries}


def collect_multi_period_investors(fetcher=fetch_json, clock=generated_at):
    periods = {}
    for period_type in ('WEEK', 'MONTH', 'THREE_MONTH'):
        periods[period_type] = collect_investors(fetcher, clock, period_type, paginate=True)
    return {'status': status_from(periods.values()), 'supportedPeriodTypes': list(periods),
            'periods': periods}


def build_manifest(rankings, industries, investors, clock=generated_at):
    entries = [rankings, industries, investors]
    return {'generatedAt': clock(), 'source': 'NAVER', 'status': status_from(entries),
            'datasets': {
                'rankings': {'status': rankings['status'], 'path': 'data/market/rankings.json'},
                'industries': {'status': industries['status'], 'path': 'data/market/industries.json'},
                'investorFlow': {'status': investors['status'], 'path': 'data/market/investor-flow.json'},
                'themes': {'status': 'DISABLED_PENDING_RESEARCH_GATE'},
            }}


def atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(temp, path)


def write_all(rankings, industries, investors, output_dir=MARKET_DIR, clock=generated_at):
    output_dir = Path(output_dir)
    manifest = build_manifest(rankings, industries, investors, clock)
    # Write every current result even when it failed: old success must not be re-labelled as current.
    atomic_write(output_dir / 'rankings.json', rankings)
    atomic_write(output_dir / 'industries.json', industries)
    atomic_write(output_dir / 'investor-flow.json', investors)
    atomic_write(output_dir / 'manifest.json', manifest)
    return manifest


def collect_all(fetcher=fetch_json, clock=generated_at, output_dir=MARKET_DIR, no_write=False):
    rankings = collect_rankings(fetcher, clock)
    industries = collect_industries(fetcher, clock)
    investors = collect_investors(fetcher, clock)
    investors['multiPeriod'] = collect_multi_period_investors(fetcher, clock)
    investors['status'] = status_from([investors, investors['multiPeriod']])
    manifest = build_manifest(rankings, industries, investors, clock)
    if not no_write:
        manifest = write_all(rankings, industries, investors, output_dir, clock)
    return {'manifest': manifest, 'rankings': rankings, 'industries': industries, 'investors': investors}


def collect_industry_membership(fetcher=fetch_json, clock=generated_at):
    industries = collect_industries(fetcher, clock)
    rows, by_code = [], {}
    for industry in industries['industries']:
        members = collect_industry_members(industry['id'], fetcher, clock,
                                           market_types=('KOSPI', 'KOSDAQ'))
        rows.append({'id': industry['id'], 'name': industry['name'], 'status': members['status'],
                     'memberCount': members['count'], 'pagination': members['pagination'],
                     'members': [{'code': row['code'], 'name': row['name']} for row in members['members']]})
        for member in members['members']:
            by_code.setdefault(member['code'], []).append({'id': industry['id'], 'name': industry['name']})
    status = status_from([industries] + rows)
    return {'generatedAt': clock(), 'source': 'NAVER', 'status': status,
            'cachePolicy': {'ttlHours': MEMBERSHIP_TTL_HOURS, 'refreshMode': 'IF_STALE'},
            'industryCount': len(rows), 'membershipCount': sum(row['memberCount'] for row in rows),
            'industries': rows, 'byCode': by_code}


def membership_is_fresh(path, clock=generated_at):
    try:
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
        created = datetime.fromisoformat(payload['generatedAt'])
        current = datetime.fromisoformat(clock())
        return payload.get('status') == 'OK' and (current - created).total_seconds() < MEMBERSHIP_TTL_HOURS * 3600
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def emit(payload, no_write=False):
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def parse_args(argv=None):
    """Accept --no-write/--stdout before or after the subcommand."""
    argv = list(sys.argv[1:] if argv is None else argv)
    for option in ('--no-write', '--stdout'):
        if option in argv:
            argv.remove(option)
            argv.insert(0, option)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-write', '--stdout', action='store_true', dest='no_write',
                        help='Fetch and print without writing data/market files.')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('rankings')
    sub.add_parser('industries')
    sub.add_parser('investors')
    members = sub.add_parser('industry-members'); members.add_argument('category_no')
    membership = sub.add_parser('industry-membership'); membership.add_argument('--if-stale', action='store_true')
    info = sub.add_parser('industry-info'); info.add_argument('category_no')
    sub.add_parser('all')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if args.command == 'rankings':
        payload = collect_rankings()
        if not args.no_write:
            atomic_write(MARKET_DIR / 'rankings.json', payload)
    elif args.command == 'industries':
        payload = collect_industries()
        if not args.no_write:
            atomic_write(MARKET_DIR / 'industries.json', payload)
    elif args.command == 'investors':
        payload = collect_investors()
        if not args.no_write:
            atomic_write(MARKET_DIR / 'investor-flow.json', payload)
    elif args.command == 'industry-members':
        payload = collect_industry_members(args.category_no)
        if not args.no_write:
            atomic_write(MARKET_DIR / f'industry-members-{args.category_no}.json', payload)
    elif args.command == 'industry-membership':
        target = MARKET_DIR / 'industry-membership.json'
        if args.if_stale and membership_is_fresh(target):
            payload = json.loads(target.read_text(encoding='utf-8')); payload['cacheStatus'] = 'FRESH_REUSED'
        else:
            payload = collect_industry_membership()
            if not args.no_write:
                atomic_write(target, payload)
    elif args.command == 'industry-info':
        payload = {'generatedAt': generated_at(), 'source': 'NAVER', 'status': 'OK',
                   'categoryId': args.category_no, 'info': fetch_industry_info(args.category_no)}
    else:
        payload = collect_all(no_write=args.no_write)
    emit(payload)


if __name__ == '__main__':
    main()
