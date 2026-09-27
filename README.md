# NAVER KRX Universe Relay

Public KRX quote snapshots and daily OHLCV for the dynamic `config/universe.json` monitoring Universe. All `enabled: true` stocks are collected; sectors, themes, and watchlists are supported. The original 33-stock set remains available only through legacy compatibility outputs.
No accounts, order information, KIS keys, personal data, or custom secrets are used.

## Public data

- [Universe quotes](https://raw.githubusercontent.com/hanull01/naver-krx-universe-relay/main/data/quotes.json)
- [Samsung daily OHLCV](https://raw.githubusercontent.com/hanull01/naver-krx-universe-relay/main/data/daily/005930.json)
- Replace `005930` with another enabled Universe code for other daily files.

`data/core33.json` and `data/core33-lite.json` are legacy compatibility files for the original 33-stock set. New Universe stocks are published through `data/quotes*.json`, `data/technicals*.json`, and `data/states*.json`, not core33.

`relay.py` uses Python 3.12+ standard libraries. Run `python relay.py all`.
It collects daily data first and quotes last to minimize quote age at publication.
Quotes try a Universe-wide batch, then sector batches, then missing individual codes.
Only validated rows are counted; missing codes are explicit, and stale quotes remain labeled stale.
Trading value uses the exact `accumulatedTradingValueRaw` in KRW, not rounded Korean text.

Daily endpoint: `https://api.stock.naver.com/chart/domestic/item/{code}/day?startDateTime=YYYYMMDD&endDateTime=YYYYMMDD`.
The collector requests 550 calendar days. At least 60 completed trading sessions are required.
Today's candle remains provisional until 16:30 KST; consumers must exclude incomplete candles.
NAVER rows with zero open/high/low/volume and a carried close are retained with `noTrading: true`.
They must not be treated as actual zero-price trades when computing highs/lows.
One-KRW discrepancies in adjusted OHLC are preserved and marked `roundingMismatch`.
Failed retrieval overwrites that file with explicit error status, never a newly timestamped old success.

## Universe Management

Use [`universe_manager.py`](docs/universe-manager.md) for normal local management of
`config/universe.json`. It supports safe previews, validation, atomic writes, backups,
stocks, groups, and leaders. Discovery candidates are never added automatically: review
them first, then add them explicitly with the manager.

## Schedule and permissions

- Monday–Friday 08:35 KST: Sunday–Thursday 23:35 UTC.
- Monday–Friday 09:35–20:35 KST: Monday–Friday 00:35–11:35 UTC.
- `workflow_dispatch` permits manual execution.
- Only the refresh job receives `contents: write` using the built-in `GITHUB_TOKEN`.
- Collection failures are committed as error files and then make the workflow fail.

## Consumer validation and limitations

Always check `generatedAt` against the current time (maximum 10 minutes, reject future timestamps),
the expected enabled Universe set, `count`, `fresh`, `status`, `delayTime`, and each `sourceTime`.
`sourceTime` is the original NAVER `localTradedAt`; root `sourceTime` is the oldest row.
An old snapshot does not become current merely because its saved `fresh` flag is true.
Daily files expose date-only `sourceTime`, `completedCount`, and per-candle `complete`.

The collector uses conservative weekday checks, not a KRX holiday calendar. Holiday-adjacent
closes may be marked stale. The report must independently verify actual KRX trading days and
the requested previous/final close. A close snapshot alone cannot prove exchange finalization.
Schedules attempt collection on weekdays, including holidays, and do not guarantee execution
before the :40 report. GitHub Actions may delay or drop scheduled runs, and Raw caching or
the ChatGPT web reader may return old content. Stale/failed rows require verified fallback.
NAVER public endpoints may change without notice. No third-party market-data rights are granted.

Tests: `python -m unittest discover -s . -v`.
