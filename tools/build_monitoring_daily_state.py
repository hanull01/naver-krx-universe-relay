#!/usr/bin/env python3
"""Build compact, deterministic 301-observation production daily state.

Research canonical history remains untouched.  This file is intentionally a
bootstrap/validation tool; publishing it is a separate, reviewed operation.
"""
from __future__ import annotations
import argparse, json, os, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CANONICAL=ROOT/'data/naver-history/canonical/daily'
UNIVERSE=ROOT/'data/market-universe/all-assets.json'
# Keep the same raw lookback used by the canonical baseline reader.  This is
# intentionally raw-row based: invalid rows are retained, never synthesized.
KEEP=301

def atomic(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f: json.dump(value,f,ensure_ascii=False,separators=(',',':'),sort_keys=True); f.write('\n')
        os.replace(tmp,path)
    except Exception:
        os.unlink(tmp); raise

def valid(row):
    def positive(key): return isinstance(row.get(key),(int,float)) and not isinstance(row.get(key),bool) and row[key]>0
    close=positive('close'); ohlc=bool(row.get('feature_valid',True)) and close and positive('high') and row['high']>=row['close']
    return close,ohlc,positive('volume')

def compact_rows(path):
    rows=[json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]
    dates=[str(r.get('date')) for r in rows]
    if len(dates)!=len(set(dates)): raise ValueError(f'duplicate date: {path.name}')
    out=[]
    for row in sorted(rows,key=lambda r:str(r['date']))[-KEEP:]:
        cv,hv,vv=valid(row); out.append({'date':str(row['date']),'close':row.get('close'),'high':row.get('high'),'volume':row.get('volume'),'closeValid':cv,'ohlcValid':hv,'volumeValid':vv})
    return out


def validity(row):
    return (0 if row['closeValid'] else 1) | (0 if row['ohlcValid'] else 2) | (0 if row['volumeValid'] else 4)


def column_stock(code, rows):
    """Stable production representation; no synthetic missing observations."""
    return {'code': code, 'dates': [row['date'] for row in rows],
            'close': [row['close'] for row in rows], 'high': [row['high'] for row in rows],
            'volume': [row['volume'] for row in rows], 'validity': [validity(row) for row in rows]}

def build(canonical=CANONICAL, universe=UNIVERSE):
    assets=json.loads(Path(universe).read_text(encoding='utf-8')).get('assets',[])
    stocks=[a for a in assets if a.get('assetType','STOCK')=='STOCK']
    seen=set(); groups={'KOSPI':[],'KOSDAQ':[]}
    for a in sorted(stocks,key=lambda x:str(x['code'])):
        code=str(a['code']); market=a.get('market')
        if code in seen or market not in groups: raise ValueError('invalid authoritative stock universe')
        seen.add(code); path=Path(canonical)/f'{code}.jsonl'
        if not path.is_file(): raise ValueError(f'missing canonical code: {code}')
        rows=compact_rows(path)
        if not rows: raise ValueError(f'empty canonical code: {code}')
        groups[market].append(column_stock(code, rows))
    return {'schemaVersion':1,'retainedObservations':KEEP,'authoritativeCount':len(stocks),'markets':groups}

def main():
    p=argparse.ArgumentParser()
    p.add_argument('command', nargs='?', default='bootstrap', choices=('bootstrap','update-latest'))
    p.add_argument('--output-dir',default=str(ROOT/'data/monitoring-daily'))
    p.add_argument('--canonical-dir',default=str(CANONICAL)); p.add_argument('--universe-file',default=str(UNIVERSE))
    p.add_argument('--daily-input', help='complete validated whole-market daily rows JSON for update-latest')
    p.add_argument('--fetch-today', action='store_true', help='collect one current daily row per authoritative code from NAVER')
    p.add_argument('--date', help='target KRX trading day (YYYY-MM-DD) for update-latest')
    p.add_argument('--baseline-output', default=str(ROOT/'data/monitoring-baseline/latest.json'))
    p.add_argument('--no-write',action='store_true'); a=p.parse_args()
    if a.command == 'update-latest':
        if not a.date or bool(a.daily_input) == bool(a.fetch_today):
            p.error('update-latest requires --date and exactly one of --daily-input or --fetch-today')
        from monitoring_daily_state_update import (collect_today_rows, load_state, update_from_file, update_from_rows)
        if a.fetch_today:
            outcome = update_from_rows(load_state(a.output_dir, a.universe_file),
                                       collect_today_rows(a.date, a.universe_file), a.output_dir, a.date,
                                       a.universe_file, a.baseline_output, no_write=a.no_write)
        else:
            outcome = update_from_file(a.output_dir, a.daily_input, a.date, a.universe_file,
                                       a.baseline_output, no_write=a.no_write)
        print(json.dumps({key:value for key,value in outcome.items() if key != 'baseline'},ensure_ascii=False))
        return
    state=build(a.canonical_dir,a.universe_file)
    if not a.no_write:
        for market,stocks in state['markets'].items():
            as_of=max((stock['dates'][-1] for stock in stocks if stock['dates']),default=None)
            atomic(Path(a.output_dir)/f'{market.lower()}.json',{'schemaVersion':1,'format':'COLUMN_ARRAY_V1','market':market,'retainedObservations':KEEP,'asOfDate':as_of,'publicationStatus':'FINAL','stocks':stocks})
        atomic(Path(a.output_dir)/'quality.json',{'schemaVersion':1,'authoritativeCount':state['authoritativeCount'],'marketCounts':{k:len(v) for k,v in state['markets'].items()}})
    print(json.dumps({'authoritativeCount':state['authoritativeCount'],'marketCounts':{k:len(v) for k,v in state['markets'].items()}},ensure_ascii=False))
if __name__=='__main__': main()
