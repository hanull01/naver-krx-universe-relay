"""Collect and stage the verified NAVER theme-to-stock membership snapshot."""
import argparse
import json
import sys
import time
import statistics
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import theme_research
import naver_market


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default=str(ROOT / 'data/research/theme-membership/latest.json'))
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--ranking', default=str(ROOT / 'data/research/theme-ranking/latest.json'))
    parser.add_argument('--previous', default=str(ROOT / 'data/research/theme-membership/latest.json'))
    args = parser.parse_args(argv)
    output = Path(args.output)
    if output.exists() and not args.force:
        try:
            previous = json.loads(output.read_text(encoding='utf-8'))
            generated = datetime.fromisoformat(previous['generatedAt'])
            if generated.astimezone(ZoneInfo('Asia/Seoul')).date() == datetime.now(ZoneInfo('Asia/Seoul')).date():
                print(json.dumps({'status': 'SKIPPED_ALREADY_CURRENT', 'themeCount': previous.get('themeCount', 0)}))
                return 0
        except (OSError, ValueError, KeyError, TypeError):
            pass
    ranking = json.loads(Path(args.ranking).read_text(encoding='utf-8'))
    themes = (((ranking.get('workContract') or {}).get('periods') or {}).get('daily') or [])
    if ranking.get('status') != 'OK' or not themes:
        print('THEME_RANKING_NOT_USABLE')
        return 1
    calls = 0
    def counted_fetcher(path, params, expected):
        nonlocal calls
        calls += 1
        return naver_market.fetch_json(path, params, expected)
    started = time.monotonic()
    snapshot = theme_research.collect_theme_membership_for_list(themes, fetcher=counted_fetcher)
    try:
        previous = json.loads(Path(args.previous).read_text(encoding='utf-8'))
    except (OSError, ValueError, json.JSONDecodeError):
        previous = {}
    current_ids = {str(row.get('id')) for row in snapshot.get('themes', [])}
    for old in previous.get('themes', []):
        if str(old.get('id')) not in current_ids:
            snapshot['themes'].append({"id": str(old.get('id')), "name": old.get('name'),
                                       "status": "LIST_NOT_FOUND", "deletionConfirmed": False,
                                       "memberCount": old.get('memberCount', 0), "members": [],
                                       "lastVerifiedAt": old.get('generatedAt') or previous.get('generatedAt')})
    snapshot['themeCount'] = len(snapshot['themes'])
    snapshot['qualityStatusCounts'] = {status: sum(row.get('status') == status for row in snapshot['themes'])
                                       for status in ('OK', 'PARTIAL', 'ERROR', 'LIST_NOT_FOUND')}
    if snapshot.get('status') != 'OK':
        print('THEME_MEMBERSHIP_NOT_PUBLISHED ' + json.dumps({
            'status': snapshot.get('status'), 'themeCount': snapshot.get('themeCount'),
            'membershipCount': snapshot.get('membershipCount')}, ensure_ascii=False))
        return 1
    snapshot = {'version': 'naver-theme-membership-v1', **snapshot,
                'quality': {'sourceStatus': 'OK', 'paginationValidated': True,
                            'duplicateMemberships': 0, 'requestCount': calls,
                            'elapsedSeconds': round(time.monotonic() - started, 3)}}
    latencies = snapshot.get('collectionMetrics', {}).get('latencies', [])
    if latencies:
        ordered = sorted(latencies)
        def percentile(p):
            return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * p))]
        snapshot['quality']['requestLatencySeconds'] = {
            'average': round(statistics.fmean(latencies), 4),
            'p50': round(percentile(0.50), 4),
            'p95': round(percentile(0.95), 4),
            'max': round(max(latencies), 4),
        }
    snapshot['quality']['marketRequestCounts'] = (
        snapshot.get('collectionMetrics', {}).get('marketRequests', {}))
    snapshot['quality']['themeRequestCounts'] = (
        snapshot.get('collectionMetrics', {}).get('themeRequests', {}))
    naver_market.atomic_write(output, snapshot)
    print(json.dumps({'status': 'OK', 'themeCount': snapshot['themeCount'],
                      'membershipCount': snapshot['membershipCount'],
                      'quality': snapshot['quality']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
