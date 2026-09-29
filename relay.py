"""Public NAVER KRX quotes and daily candles. No credentials required."""
import argparse
import json
import math
import re
import time
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from krx_market_day import CalendarUnavailable, krx_holidays

KST = ZoneInfo('Asia/Seoul')
ROOT = Path(__file__).resolve().parent
UNIVERSE_PATH = ROOT / 'config/universe.json'
QUOTE_URL = 'https://polling.finance.naver.com/api/realtime/domestic/stock/'
DAILY_URL = 'https://api.stock.naver.com/chart/domestic/item/{code}/day'


def validate_universe(universe):
    stocks = universe.get('stocks')
    if not isinstance(stocks, list):
        raise ValueError('stocks must be an array')
    codes = []
    for stock in stocks:
        code = stock.get('itemCode') if isinstance(stock, dict) else None
        if not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
            raise ValueError('stock itemCode must be six digits')
        if not isinstance(stock.get('stockName'), str) or not stock['stockName']:
            raise ValueError(f'{code}: stockName is required')
        if not isinstance(stock.get('enabled'), bool):
            raise ValueError(f'{code}: enabled must be boolean')
        codes.append(code)
    if len(codes) != len(set(codes)):
        raise ValueError('duplicate stock itemCode')
    known = set(codes)
    for kind in ('sectors', 'themes', 'watchlists'):
        groups = universe.get(kind, {})
        if not isinstance(groups, dict):
            raise ValueError(f'{kind} must be an object')
        for name, members in groups.items():
            if not isinstance(name, str) or not isinstance(members, list):
                raise ValueError(f'invalid {kind} group')
            if len(members) != len(set(members)) or any(code not in known for code in members):
                raise ValueError(f'{kind}/{name} has invalid member')
    for kind, groups in universe.get('leaders', {}).items():
        if kind not in ('sector', 'theme', 'watchlist') or not isinstance(groups, dict):
            raise ValueError('invalid leaders')
        source = universe.get(kind + 's', {}) if kind != 'watchlist' else universe.get('watchlists', {})
        for name, members in groups.items():
            if name not in source or not isinstance(members, list) or any(code not in known for code in members):
                raise ValueError(f'leaders/{kind}/{name} has invalid member')
    return universe


def load_universe(path=UNIVERSE_PATH):
    return validate_universe(json.loads(Path(path).read_text(encoding='utf-8')))


def universe_codes(universe, enabled_only=True):
    return [s['itemCode'] for s in universe['stocks'] if not enabled_only or s['enabled']]


def universe_state():
    universe = load_universe()
    codes = universe_codes(universe)
    legacy = [c for c in universe['watchlists'].get('legacy33', []) if c in set(codes)]
    sector = {}
    for name, members in universe['sectors'].items():
        for code in members:
            sector.setdefault(code, name)
    return universe, codes, legacy, sector


def now():
    return datetime.now(KST)


def fetch(url):
    for attempt in range(3):
        try:
            req = Request(url, headers={'User-Agent': 'Mozilla/5.0',
                                        'Accept': 'application/json',
                                        'Referer': 'https://stock.naver.com/'})
            with urlopen(req, timeout=20) as response:
                return json.load(response)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def save(path, payload, compact=False):
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.tmp')
    content = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                         separators=(',', ':') if compact else None,
                         indent=None if compact else 2)
    temp.write_text(content + '\n', encoding='utf-8')
    temp.replace(target)


def number(value, field='unknown'):
    """Parse NAVER numeric text without silently coercing bad required data.

    The endpoint exposes display strings as well as *Raw values.  Raw values
    remain mandatory for quote fields.  The error deliberately
    reports just field/value, never an entire response payload.
    """
    if value is None or isinstance(value, bool):
        raise ValueError(f'missing numeric field: field={field}, value={value!r}')
    text = str(value).strip()
    text = text.replace(',', '')
    if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', text):
        raise ValueError(f'invalid numeric field: field={field}, value={value!r}')
    result = float(text)
    if not math.isfinite(result):
        raise ValueError(f'invalid numeric field: field={field}, value={value!r}')
    return int(result) if result.is_integer() else result


def previous_weekday(day):
    day -= timedelta(days=1)
    while day.weekday() > 4:
        day -= timedelta(days=1)
    return day


def previous_krx_business_day(day, holiday_loader=krx_holidays):
    """Return the preceding verified KRX business day, never a weekday guess.

    PRE quotes deliberately use a completed daily bar for this exact date. A
    KRX calendar lookup failure is allowed to fail collection rather than
    silently selecting an older candle.
    """
    holiday_cache = {}
    candidate = day - timedelta(days=1)
    for _ in range(370):
        if candidate.weekday() < 5:
            holidays = holiday_cache.setdefault(candidate.year, holiday_loader(candidate.year))
            if candidate.isoformat() not in holidays:
                return candidate
        candidate -= timedelta(days=1)
    raise CalendarUnavailable('could not determine previous KRX business day')


def detect_market_session(current):
    clock = current.time()
    if clock < dtime(9): return 'PRE'
    if clock < dtime(15, 30): return 'REGULAR'
    if clock < dtime(15, 40): return 'REGULAR_CLOSED'
    if clock < dtime(20): return 'AFTER'
    return 'CLOSED'


def price_basis(session):
    return {
        'PRE': 'PREVIOUS_KRX_CLOSE',
        'REGULAR': 'KRX_REGULAR',
        'REGULAR_CLOSED': 'KRX_CLOSE_HOLD',
        'AFTER': 'AFTER_MARKET',
        'CLOSED': 'AFTER_MARKET_FINAL',
    }[session]


def quote_freshness(traded, current, market, delay):
    """Apply the timestamp-based KRX/after-market price policy.

    ``market`` is retained for the stable call signature but intentionally is
    not a freshness input: NAVER's OPEN/CLOSE text is raw reference metadata.
    """
    age = (current - traded).total_seconds()
    if age < -60:
        return False, 'future_source_time'
    if delay != 0:
        return False, 'delayed_or_unknown_delay'
    if current.weekday() > 4:
        return False, 'non_weekday'
    session = detect_market_session(current)
    if session == 'PRE':
        valid = (traded.date() == previous_weekday(current.date())
                 and traded.time() >= dtime(15, 30))
        return valid, 'previous_close' if valid else 'stale_previous_close'
    if session == 'REGULAR':
        valid = traded.date() == current.date() and -60 <= age <= 600
        return valid, 'krx_regular_live' if valid else 'stale_krx_regular'
    if session == 'REGULAR_CLOSED':
        # Preserve the same-day regular-session close during the short gap
        # before the after-market opens; only the 15:30 close window is valid
        # and freshness age is irrelevant inside this short hold interval.
        valid = (traded.date() == current.date()
                 and dtime(15, 30) <= traded.time() < dtime(15, 40))
        return valid, 'krx_close_hold' if valid else 'stale_krx_close_hold'
    if session == 'AFTER':
        valid = traded.date() == current.date() and -60 <= age <= 600
        return valid, 'after_market_live' if valid else 'stale_after_market'
    # After 20:00 NAVER may keep the last after-market trade at 19:xx.  It is
    # still today's final valid after-market price, not a stale live quote.
    valid = traded.date() == current.date() and traded.time() >= dtime(15, 40)
    return valid, 'after_market_final' if valid else 'stale_after_market_final'


def optional_market_metadata(row, delay):
    """Keep selected public NAVER session metadata without parsing it as price."""
    exchange = row.get('stockExchangeType')
    exchange = exchange if isinstance(exchange, dict) else {}
    over_market = row.get('overMarketPriceInfo')
    over_market = over_market if isinstance(over_market, dict) else {}
    return (
        {key: exchange[key] for key in ('code', 'name') if key in exchange} | {'delayTime': delay},
        {key: over_market[key] for key in ('tradingSessionType', 'overPrice', 'localTradedAt')
         if key in over_market},
    )


def normalize_quote(row, current, sector=None):
    code = row['itemCode']
    traded = datetime.fromisoformat(row['localTradedAt'])
    if traded.tzinfo is None:
        traded = traded.replace(tzinfo=KST)
    traded = traded.astimezone(KST)
    result = {'itemCode': code, 'stockName': row['stockName']}
    if sector:
        result['sector'] = sector
    fields = ['closePrice', 'compareToPreviousClosePrice', 'fluctuationsRatio',
              'openPrice', 'highPrice', 'lowPrice', 'accumulatedTradingVolume',
              'accumulatedTradingValue']
    for field in fields:
        result[field] = number(row.get(field + 'Raw', row.get(field)), field)
    # NAVER sometimes gives an unsigned change plus a separate direction enum.
    direction = str(row.get('compareToPreviousPrice', {}).get('code', ''))
    if direction in ('4', '5') or result['fluctuationsRatio'] < 0:
        result['compareToPreviousClosePrice'] = -abs(result['compareToPreviousClosePrice'])
    if result['closePrice'] <= 0 or result['accumulatedTradingVolume'] < 0:
        raise ValueError('invalid price or volume')
    delay = row.get('stockExchangeType', {}).get('delayTime')
    delay = number(delay, 'stockExchangeType.delayTime') if delay is not None else None
    fresh, reason = quote_freshness(traded, current, row.get('marketStatus'), delay)
    session = detect_market_session(current)
    exchange_metadata, over_market_metadata = optional_market_metadata(row, delay)
    result.update(source='NAVER_KRX', sourceTime=traded.isoformat(),
                  localTradedAt=traded.isoformat(), marketStatus=row.get('marketStatus'),
                  delayTime=delay, stockExchangeType=exchange_metadata,
                  marketSessionType=row.get('marketSessionType'),
                  overMarketPriceInfo=over_market_metadata,
                  ageSeconds=round((current - traded).total_seconds()), fresh=fresh,
                  status='ok' if fresh else 'stale', freshnessReason=reason,
                  session=session, priceBasis=price_basis(session))
    return result


LITE_FIELDS = ('itemCode', 'stockName', 'closePrice', 'fluctuationsRatio',
               'accumulatedTradingVolume', 'sourceTime', 'marketStatus', 'delayTime',
               'fresh', 'freshnessReason', 'session', 'priceBasis', 'marketSessionType', 'status')


def lite_payload(payload):
    keys = ('generatedAt', 'expectedCount', 'count', 'freshCount', 'missingCodes', 'status', 'fresh')
    result = {key: payload[key] for key in keys}
    result['datas'] = [{key: row.get(key) for key in LITE_FIELDS} for row in payload['datas']]
    return result


def quote_payload(rows, codes, expected, errors, started):
    missing = [c for c in codes if c not in rows]
    fresh_count = sum(rows[c]['fresh'] for c in codes if c in rows)
    status = 'error' if not rows else 'partial' if missing else 'ok' if fresh_count == expected else 'stale'
    times = [row['sourceTime'] for row in rows.values()]
    return {'schemaVersion': 1, 'generatedAt': now().isoformat(),
            'collectionStartedAt': started.isoformat(), 'source': 'NAVER_KRX',
            'count': len([c for c in codes if c in rows]), 'expectedCount': expected,
            'freshCount': fresh_count, 'status': status, 'fresh': status == 'ok', 'errors': errors,
            'missingCodes': missing, 'sourceTime': min(times) if times else None,
            'sourceTimeLatest': max(times) if times else None,
            'freshnessPolicy': 'previous KRX close before 09:00; 600 seconds during regular/after live sessions; same-day KRX close hold 15:30-15:40; same-day after-market final after 20:00',
            'datas': [rows[c] for c in codes if c in rows]}


def quote_snapshot_unusable(payload):
    """A zero-usable collection must not replace the last production snapshot."""
    return (payload.get('status') == 'error' or payload.get('count', 0) == 0
            or ('freshCount' in payload and payload.get('freshCount', 0) == 0))


def quote_error_diagnostic(payload, current):
    return {
        'generatedAt': now().isoformat(), 'status': 'error',
        'expectedCount': payload.get('expectedCount'), 'count': payload.get('count'),
        'freshCount': payload.get('freshCount'), 'missingCodes': payload.get('missingCodes', []),
        'errors': payload.get('errors', []), 'collectionStartedAt': payload.get('collectionStartedAt'),
        'session': detect_market_session(current), 'preservedProductionSnapshot': True,
    }


def load_previous_business_day_close(code, business_day):
    """Load only the requested completed daily close; no stale-date fallback."""
    daily = load_daily_for_technical(code)
    for bar in (daily or {}).get('datas', []):
        if (bar.get('date') == business_day.isoformat() and bar.get('complete') is True):
            close = bar.get('close')
            if isinstance(close, (int, float)) and not isinstance(close, bool) and close > 0:
                return close
            raise ValueError(f'invalid completed daily close for {business_day.isoformat()}')
    raise ValueError(f'missing completed daily close for {business_day.isoformat()}')


def build_preclose_quote(code, stock_name, business_day, close, current, sector=None):
    """Build the 08:40 PRE quote from the prior KRX regular-session close."""
    source_time = datetime.combine(business_day, dtime(15, 30), tzinfo=KST)
    result = {
        'itemCode': code, 'stockName': stock_name, 'closePrice': close,
        'compareToPreviousClosePrice': None, 'fluctuationsRatio': None,
        'openPrice': None, 'highPrice': None, 'lowPrice': None,
        'accumulatedTradingVolume': None, 'accumulatedTradingValue': None,
        'source': 'NAVER_KRX', 'sourceTime': source_time.isoformat(),
        'localTradedAt': source_time.isoformat(), 'marketStatus': None, 'delayTime': 0,
        'stockExchangeType': {'delayTime': 0}, 'marketSessionType': None,
        'overMarketPriceInfo': {}, 'ageSeconds': round((current - source_time).total_seconds()),
        'fresh': True, 'status': 'ok', 'freshnessReason': 'previous_business_day_close',
        'session': 'PRE', 'priceBasis': 'PREVIOUS_KRX_CLOSE',
        'previousBusinessDay': business_day.isoformat(),
    }
    if sector:
        result['sector'] = sector
    return result


def collect_preclose_quotes(universe, codes, legacy_codes, sectors, current):
    rows, errors = {}, []
    try:
        business_day = previous_krx_business_day(current.date())
    except Exception as exc:
        errors.append({'stage': 'previous_krx_business_day', 'error': str(exc)})
        business_day = None
    names = {stock['itemCode']: stock['stockName'] for stock in universe['stocks']}
    if business_day:
        for code in codes:
            try:
                close = load_previous_business_day_close(code, business_day)
                rows[code] = build_preclose_quote(code, names[code], business_day, close, current,
                                                   sectors.get(code))
            except Exception as exc:
                errors.append({'stage': 'previous_business_day_close', 'code': code, 'error': str(exc)})
    for code in codes:
        if code not in rows:
            errors.append({'code': code, 'error': 'no_valid_quote'})
    payload = quote_payload(rows, codes, len(codes), errors, current)
    legacy = quote_payload(rows, legacy_codes, len(legacy_codes), errors, current)
    if quote_snapshot_unusable(payload):
        save('data/status/quotes-error.json', quote_error_diagnostic(payload, current))
        print('Collector failed; preserving last successful production snapshot.')
    else:
        save('data/quotes.json', payload)
        save('data/quotes-lite.json', lite_payload(payload), compact=True)
        save('data/core33.json', legacy)
        save('data/core33-lite.json', lite_payload(legacy), compact=True)
        write_group_files(universe, rows)
    print(json.dumps({k: payload[k] for k in ('count', 'freshCount', 'status', 'sourceTime')}))
    return payload


def collect_quotes():
    universe, codes, legacy_codes, sectors = universe_state()
    current = now()
    if detect_market_session(current) == 'PRE':
        return collect_preclose_quotes(universe, codes, legacy_codes, sectors, current)
    rows, errors = {}, []

    def batch(codes, stage):
        try:
            payload = fetch(QUOTE_URL + ','.join(codes))
            if not isinstance(payload.get('datas'), list):
                raise ValueError('missing datas array')
            for row in payload['datas']:
                code = row.get('itemCode')
                if code not in codes:
                    continue
                try:
                    normalized = normalize_quote(row, now())
                    if sectors.get(code):
                        normalized['sector'] = sectors[code]
                    rows[code] = normalized
                except Exception as exc:
                    errors.append({'stage': stage, 'code': code, 'error': str(exc)})
        except Exception as exc:
            errors.append({'stage': stage, 'codes': codes, 'error': str(exc)})

    batch(codes, 'all_batch')
    seen_groups = set()
    for group in universe['sectors'].values():
        missing = [c for c in group if c in codes and c not in rows and c not in seen_groups]
        seen_groups.update(group)
        if missing:
            batch(missing, 'sector_batch')
    for code in codes:
        if code not in rows:
            batch([code], 'individual')
    missing = [c for c in codes if c not in rows]
    for code in missing:
        errors.append({'code': code, 'error': 'no_valid_quote'})
    payload = quote_payload(rows, codes, len(codes), errors, current)
    # Legacy 33-stock compatibility output; data/quotes*.json is the Universe-wide source.
    legacy = quote_payload(rows, legacy_codes, len(legacy_codes), errors, current)
    if quote_snapshot_unusable(payload):
        save('data/status/quotes-error.json', quote_error_diagnostic(payload, current))
        print('Collector failed; preserving last successful production snapshot.')
    else:
        save('data/quotes.json', payload)
        save('data/quotes-lite.json', lite_payload(payload), compact=True)
        save('data/core33.json', legacy)
        save('data/core33-lite.json', lite_payload(legacy), compact=True)
        write_group_files(universe, rows)
    print(json.dumps({k: payload[k] for k in ('count', 'freshCount', 'status', 'sourceTime')}))
    return payload


def safe_group_name(name):
    return re.sub(r'[^\w가-힣· .-]+', '_', name, flags=re.UNICODE).strip(' .') or 'group'


def write_group_files(universe, rows):
    for kind, label in (('sectors', 'sector'), ('themes', 'theme'), ('watchlists', 'watchlist')):
        leaders = universe.get('leaders', {}).get(label, {})
        for name, codes in universe.get(kind, {}).items():
            datas = [{key: rows[c].get(key) for key in LITE_FIELDS} for c in codes if c in rows]
            payload = {'generatedAt': now().isoformat(), 'groupType': label, 'groupName': name,
                       'expectedCount': len(codes), 'count': len(datas), 'leaders': leaders.get(name, []),
                       'datas': datas}
            save(f'data/groups/{safe_group_name(name)}.json', payload, compact=True)


def collect_daily(code):
    current = now()
    start = (current - timedelta(days=550)).strftime('%Y%m%d')
    url = DAILY_URL.format(code=code) + f'?startDateTime={start}&endDateTime={current:%Y%m%d}'
    payload = {'schemaVersion': 1, 'itemCode': code, 'generatedAt': current.isoformat(),
               'source': 'NAVER_KRX', 'sourceUrl': url, 'status': 'error',
               'fresh': False, 'errors': [], 'count': 0, 'completedCount': 0, 'datas': []}
    try:
        response = fetch(url)
        if not isinstance(response, list):
            raise ValueError('daily endpoint did not return array')
        bars = {}
        for row in response:
            date = datetime.strptime(row['localDate'], '%Y%m%d').date()
            if date > current.date():
                raise ValueError('future candle date')
            bar = {'date': date.isoformat()}
            for out, field in [('open', 'openPrice'), ('high', 'highPrice'),
                               ('low', 'lowPrice'), ('close', 'closePrice'),
                               ('volume', 'accumulatedTradingVolume')]:
                bar[out] = number(row[field], field)
            bar['noTrading'] = (bar['open'] == bar['high'] == bar['low'] == bar['volume'] == 0
                                and bar['close'] > 0)
            # Adjusted historical prices can differ by one KRW from rounding.
            bar['roundingMismatch'] = not bar['noTrading'] and (
                bar['low'] > min(bar['open'], bar['close']) or
                max(bar['open'], bar['close']) > bar['high'])
            if not bar['noTrading'] and (not (0 < bar['low'] <= bar['high']
                    and bar['low'] - 1 <= min(bar['open'], bar['close'])
                    <= max(bar['open'], bar['close']) <= bar['high'] + 1) or bar['volume'] < 0):
                raise ValueError('invalid OHLCV')
            # Conservatively keep today's candle provisional until 16:30 KST.
            bar['complete'] = date < current.date() or current.time() >= dtime(16, 30)
            bars[bar['date']] = bar
        ordered = [bars[d] for d in sorted(bars)]
        completed = [b for b in ordered if b['complete']]
        latest = completed[-1]['date'] if completed else None
        expected = current.date() if current.weekday() < 5 and current.time() >= dtime(16, 30) else previous_weekday(current.date())
        fresh = latest == expected.isoformat()
        status = 'insufficient' if len(completed) < 60 else 'ok' if fresh else 'stale'
        payload.update(count=len(ordered), completedCount=len(completed), datas=ordered,
                       sourceTime=latest, latestDate=ordered[-1]['date'] if ordered else None,
                       fresh=fresh and len(completed) >= 60, status=status,
                       freshnessPolicy='latest completed date must equal conservative expected weekday; consumer must verify actual KRX calendar')
        if status != 'ok':
            payload['errors'].append({'error': status, 'expectedCompletedDate': expected.isoformat()})
    except Exception as exc:
        payload['errors'].append({'error': str(exc)})
    save(f'data/daily/{code}.json', payload)
    return {k: payload[k] for k in ('itemCode', 'status', 'completedCount')}


def collect_all_daily():
    _, codes, _, _ = universe_state()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(collect_daily, codes))
    save('data/daily-status.json', {'generatedAt': now().isoformat(), 'results': results})
    print(json.dumps(results, ensure_ascii=False))
    return results


TECHNICAL_LITE_FIELDS = ('itemCode', 'ma5', 'ma20', 'ma60', 'high20', 'high60',
                         'high52w', 'volumeRatio20', 'historyCount', 'status')


def load_daily_for_technical(code):
    try:
        return json.loads((ROOT / f'data/daily/{code}.json').read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def daily_cache_is_current(daily, current=None, expected_completed_date=None):
    """Accept legacy fixtures, but fail closed for stale saved collector data."""
    if not daily:
        return False
    if daily.get('status') in ('error', 'insufficient', 'stale'):
        return False
    # ``latestDate`` may include a provisional current-day candle.  Technical
    # indicators must instead be keyed to the latest completed candle.
    source_date = daily.get('sourceTime') or daily.get('latestDate')
    # Older cache fixtures predate source-date metadata. Their history checks
    # still determine technical status; saved collector payloads have metadata.
    if source_date is None:
        return True
    try:
        actual = datetime.fromisoformat(str(source_date)).date()
    except ValueError:
        return False
    current = current or now()
    if expected_completed_date is not None:
        try:
            expected = datetime.fromisoformat(str(expected_completed_date)).date()
        except ValueError:
            return False
    else:
        expected = (current.date() if current.weekday() < 5 and current.time() >= dtime(16, 30)
                    else previous_weekday(current.date()))
    return actual == expected


def technical_average(values):
    return round(sum(values) / len(values), 2)


def calculate_technicals(code, stock_name, daily, quote):
    bars = [] if not daily else [bar for bar in daily.get('datas', [])
                                 if bar.get('complete') is True and not bar.get('noTrading')]
    history_count = len(bars)
    closes = [bar['close'] for bar in bars]
    highs = [bar['high'] for bar in bars]
    volumes = [bar['volume'] for bar in bars]
    enough20 = history_count >= 20
    avg_volume20 = technical_average(volumes[-20:]) if enough20 else None
    current_volume = quote.get('accumulatedTradingVolume') if quote else None
    return {
        'itemCode': code, 'stockName': stock_name, 'asOf': bars[-1]['date'] if bars else None,
        'historyCount': history_count,
        'ma5': technical_average(closes[-5:]) if history_count >= 5 else None,
        'ma20': technical_average(closes[-20:]) if enough20 else None,
        'ma60': technical_average(closes[-60:]) if history_count >= 60 else None,
        'high20': max(highs[-20:]) if enough20 else None,
        'high60': max(highs[-60:]) if history_count >= 60 else None,
        'high52w': max(highs[-252:]) if highs else None,
        'high52wComplete': history_count >= 252,
        'avgVolume20': avg_volume20,
        'volumeRatio20': round(current_volume / avg_volume20, 2)
                         if avg_volume20 and current_volume is not None else None,
        'status': 'error' if not daily_cache_is_current(
            daily, expected_completed_date=(quote or {}).get('previousBusinessDay'))
        else 'ok' if enough20 else 'insufficient_history',
    }


def build_technicals(quote_payload=None):
    universe, codes, _, _ = universe_state()
    if quote_payload is None:
        try:
            quote_payload = json.loads((ROOT / 'data/quotes.json').read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            quote_payload = {'datas': []}
    quotes = {row['itemCode']: row for row in quote_payload.get('datas', [])}
    names = {stock['itemCode']: stock['stockName'] for stock in universe['stocks']}
    datas = [calculate_technicals(code, names[code], load_daily_for_technical(code), quotes.get(code))
             for code in codes]
    missing = [row['itemCode'] for row in datas if row['status'] == 'error']
    payload = {'generatedAt': now().isoformat(), 'expectedCount': len(codes), 'count': len(datas),
               'missingCodes': missing, 'status': 'error' if missing and len(missing) == len(codes)
               else 'partial' if missing else 'ok', 'datas': datas}
    save('data/technicals.json', payload)
    lite = {key: payload[key] for key in ('generatedAt', 'expectedCount', 'count', 'missingCodes', 'status')}
    lite['datas'] = [{key: row[key] for key in TECHNICAL_LITE_FIELDS} for row in datas]
    save('data/technicals-lite.json', lite, compact=True)
    return payload


def load_analysis_config():
    config = json.loads((ROOT / 'config/analysis.json').read_text(encoding='utf-8'))
    keys = ('nearPct', 'volumeElevated', 'volumeSurge')
    if any(not isinstance(config.get(key), (int, float)) for key in keys):
        raise ValueError('invalid analysis config')
    return config


def prior_highs(daily):
    bars = [] if not daily else [bar for bar in daily.get('datas', [])
                                 if bar.get('complete') is True and not bar.get('noTrading')]
    previous = bars[:-1]
    return (max((bar['high'] for bar in previous[-20:]), default=None) if len(previous) >= 20 else None,
            max((bar['high'] for bar in previous[-60:]), default=None) if len(previous) >= 60 else None)


def compare(value, reference):
    if value is None or reference is None:
        return 'unknown'
    return 'above' if value > reference else 'below' if value < reference else 'equal'


def breakout(price, session_high, prior_high, final):
    if prior_high is None or price is None or session_high is None:
        return 'unknown'
    if final:
        return 'confirmed' if price > prior_high else 'failed' if session_high > prior_high else 'none'
    return 'attempt' if session_high > prior_high else 'none'


def volume_state(ratio, config):
    if ratio is None: return 'unknown'
    if ratio >= config['volumeSurge']: return 'surge'
    if ratio >= config['volumeElevated']: return 'elevated'
    return 'normal'


def calculate_state(code, stock_name, technical, daily, quote, config):
    prior20, prior60 = prior_highs(daily)
    price = quote.get('closePrice') if quote else None
    session_high = quote.get('highPrice') if quote else None
    final = regular_close_confirmed(quote)
    ma20, ma60 = technical.get('ma20'), technical.get('ma60')
    def distance(value): return round((price - value) / value * 100, 2) if price is not None and value else None
    def near(value): return value is not None and price is not None and abs((price-value)/value*100) <= config['nearPct']
    pullback = 'near_breakout20' if near(prior20) else 'near_breakout60' if near(prior60) else 'near_ma20' if near(ma20) else 'none' if price is not None else 'unknown'
    return {'itemCode': code, 'stockName': stock_name, 'sourceTime': quote.get('sourceTime') if quote else None,
            'price': price, 'ma20': ma20, 'ma60': ma60,
            'priceVsMA20': compare(price, ma20), 'priceVsMA60': compare(price, ma60),
            'maAlignment': 'unknown' if ma20 is None or ma60 is None else 'ma20_above_ma60' if ma20 > ma60 else 'ma20_below_ma60' if ma20 < ma60 else 'equal',
            'distanceMA20Pct': distance(ma20), 'distanceMA60Pct': distance(ma60),
            'priorHigh20': prior20, 'priorHigh60': prior60,
            'distancePriorHigh20Pct': distance(prior20), 'distancePriorHigh60Pct': distance(prior60),
            'breakout20': breakout(price, session_high, prior20, final), 'breakout60': breakout(price, session_high, prior60, final),
            'volumeRatio20': technical.get('volumeRatio20'), 'volumeState': volume_state(technical.get('volumeRatio20'), config),
            'pullbackState': pullback, 'status': 'ok' if technical.get('status') == 'ok' and quote else 'partial'}


def regular_close_confirmed(quote):
    """Confirm a regular-session close from the quote timestamp, not NAVER text."""
    if not quote or not quote.get('sourceTime'):
        return False
    try:
        source_time = datetime.fromisoformat(str(quote['sourceTime']))
    except ValueError:
        return False
    if source_time.tzinfo is None:
        source_time = source_time.replace(tzinfo=KST)
    source_time = source_time.astimezone(KST)
    return source_time.date() == now().astimezone(KST).date() and source_time.time() >= dtime(15, 30)


def build_states(quote_payload=None, technical_payload=None):
    universe, codes, _, _ = universe_state(); config = load_analysis_config()
    if quote_payload is None: quote_payload = json.loads((ROOT / 'data/quotes.json').read_text(encoding='utf-8'))
    if technical_payload is None: technical_payload = json.loads((ROOT / 'data/technicals.json').read_text(encoding='utf-8'))
    quotes = {row['itemCode']: row for row in quote_payload.get('datas', [])}; technicals = {row['itemCode']: row for row in technical_payload.get('datas', [])}
    names = {stock['itemCode']: stock['stockName'] for stock in universe['stocks']}
    datas = [calculate_state(code, names[code], technicals.get(code, {}), load_daily_for_technical(code), quotes.get(code), config) for code in codes]
    missing = [row['itemCode'] for row in datas if row['status'] != 'ok']
    payload = {'generatedAt': now().isoformat(), 'expectedCount': len(codes), 'count': len(datas), 'missingCodes': missing, 'status': 'ok' if not missing else 'partial', 'datas': datas}
    save('data/states.json', payload)
    fields = ('itemCode','price','priceVsMA20','priceVsMA60','maAlignment','priorHigh20','priorHigh60','breakout20','breakout60','volumeRatio20','volumeState','pullbackState','status')
    save('data/states-lite.json', {**{key: payload[key] for key in ('generatedAt','expectedCount','count','missingCodes','status')}, 'datas': [{key: row[key] for key in fields} for row in datas]}, compact=True)
    return payload


def classify_diffusion(m, config):
    if not m['enabledMembers'] or m['unknownCount'] == m['enabledMembers']: return 'unknown'
    broad, moderate = config['groupBroadRatio'], config['groupModerateRatio']
    if m['upRatio'] >= broad and m['aboveMA20CountRatio'] >= broad: return 'broad'
    if m['upRatio'] >= moderate and m['aboveMA20CountRatio'] >= moderate: return 'moderate'
    if m['downRatio'] >= broad and m['aboveMA20CountRatio'] < moderate: return 'weak'
    if m['upRatio'] < moderate and (m['breakout20AttemptCount'] + m['breakout20ConfirmedCount'] or m['volumeSurgeCount'] or m['leaderUpCount']): return 'narrow'
    return 'mixed'


def calculate_group_state(kind, name, members, leaders, enabled, quotes, technicals, states, config):
    codes = [c for c in members if c in enabled]; rows = [(c, quotes.get(c), technicals.get(c), states.get(c)) for c in codes]
    changes = [q.get('fluctuationsRatio') for _, q, _, _ in rows if q and q.get('fluctuationsRatio') is not None]
    up = sum(1 for x in changes if x > 0); down = sum(1 for x in changes if x < 0); flat = sum(1 for x in changes if x == 0); n=len(codes)
    state_rows = [s for _,_,_,s in rows if s]; leader_rows=[r for r in rows if r[0] in leaders]
    ratio=lambda x: round(x/n,2) if n else None
    m={'groupType':kind,'groupName':name,'members':len(members),'enabledMembers':n,'upCount':up,'downCount':down,'flatCount':flat,'upRatio':ratio(up),'downRatio':ratio(down),'aboveMA20Count':sum(s.get('priceVsMA20')=='above' for s in state_rows),'aboveMA60Count':sum(s.get('priceVsMA60')=='above' for s in state_rows),'ma20AboveMa60Count':sum(s.get('maAlignment')=='ma20_above_ma60' for s in state_rows),'breakout20AttemptCount':sum(s.get('breakout20')=='attempt' for s in state_rows),'breakout20ConfirmedCount':sum(s.get('breakout20')=='confirmed' for s in state_rows),'breakout20FailedCount':sum(s.get('breakout20')=='failed' for s in state_rows),'volumeElevatedCount':sum(s.get('volumeState')=='elevated' for s in state_rows),'volumeSurgeCount':sum(s.get('volumeState')=='surge' for s in state_rows),'unknownCount':n-len(state_rows),'leaderCount':len(leader_rows),'leaderUpCount':sum(q and q.get('fluctuationsRatio',0)>0 for _,q,_,_ in leader_rows),'leaderAboveMA20Count':sum(s and s.get('priceVsMA20')=='above' for _,_,_,s in leader_rows),'leaderBreakoutCount':sum(s and s.get('breakout20') in ('attempt','confirmed') for _,_,_,s in leader_rows),'averageChangePct':round(sum(changes)/len(changes),2) if changes else None,'medianChangePct':round(statistics.median(changes),2) if changes else None,'maxChangePct':max(changes) if changes else None,'minChangePct':min(changes) if changes else None}
    for key in ('aboveMA20Count','aboveMA60Count','ma20AboveMa60Count'): m[key+'Ratio']=ratio(m[key])
    m['changeSpreadPct']=round(m['maxChangePct']-m['minChangePct'],2) if changes else None; m['diffusionState']=classify_diffusion(m,config); m['evidence']={'upRatio':m['upRatio'],'aboveMA20Ratio':m['aboveMA20CountRatio'],'breakout20Count':m['breakout20AttemptCount']+m['breakout20ConfirmedCount'],'volumeSurgeCount':m['volumeSurgeCount'],'leaderUpCount':m['leaderUpCount']}; m['status']='ok' if n else 'partial'; return m


def build_group_states(quote_payload, technical_payload, state_payload):
    universe,codes,_,_=universe_state(); config=load_analysis_config(); enabled=set(codes); q={x['itemCode']:x for x in quote_payload['datas']}; t={x['itemCode']:x for x in technical_payload['datas']}; s={x['itemCode']:x for x in state_payload['datas']}; groups=[]
    for plural,kind in (('sectors','sector'),('themes','theme'),('watchlists','watchlist')):
        for name,members in universe[plural].items(): groups.append(calculate_group_state(kind,name,members,universe.get('leaders',{}).get(kind,{}).get(name,[]),enabled,q,t,s,config))
    payload={'generatedAt':now().isoformat(),'groupCount':len(groups),'status':'ok','groups':groups}; save('data/group-states.json',payload); fields=('groupType','groupName','enabledMembers','upRatio','aboveMA20CountRatio','aboveMA60CountRatio','breakout20AttemptCount','breakout20ConfirmedCount','volumeSurgeCount','leaderUpCount','averageChangePct','diffusionState','status'); save('data/group-states-lite.json',{**{k:payload[k] for k in ('generatedAt','groupCount','status')},'groups':[{k:g[k] for k in fields} for g in groups]},compact=True); return payload


def timed_step(name, fn, *args):
    """Emit small operational timing logs without changing output schemas."""
    started = time.perf_counter()
    result = fn(*args)
    print(f'{name} elapsedSeconds={time.perf_counter() - started:.2f}')
    return result


def run_quote_pipeline():
    """Refresh quotes and derive intraday outputs from the local daily cache."""
    failed = False
    result = timed_step('quotes', collect_quotes)
    _, _, legacy_codes, _ = universe_state()
    failed |= result['count'] != result['expectedCount'] or len(legacy_codes) != 33
    if quote_snapshot_unusable(result):
        print('Collector failed; preserving last successful production snapshot.')
        return True
    technicals = timed_step('technicals', build_technicals, result)
    failed |= technicals['count'] != result['expectedCount']
    # A complete-looking row count is not healthy if every daily-derived row
    # carries an error (for example, a stale completed-candle cache).
    failed |= technicals.get('status') == 'error'
    if technicals.get('status') == 'partial':
        print(f"WARNING technicals partial missingCodes={len(technicals.get('missingCodes', []))}")
    states = timed_step('states', build_states, result, technicals)
    timed_step('group-states', build_group_states, result, technicals, states)
    return failed


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['quotes', 'daily', 'intraday', 'all'], default='all', nargs='?')
    args = parser.parse_args(argv)
    failed = False
    if args.mode in ('daily', 'all'):
        failed |= any(r['status'] in ('error', 'insufficient') for r in timed_step('daily', collect_all_daily))
    if args.mode in ('quotes', 'intraday', 'all'):
        failed |= run_quote_pipeline()
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
