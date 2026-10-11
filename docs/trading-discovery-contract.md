# TC-KIWOOM-REPORT-01 data contract

`data/trading-discovery/YYYY-MM-DD.json` is an optional input to the domestic
market report. `latest.json` points to the newest validated monitoring date.
Neither file participates in `MARKET_DATA_READY` eligibility.

The local source remains Trading Console's `data/today_candidates.json`.
Trading Console freezes that source into an immutable next-session snapshot;
`trading_discovery.py` validates and publishes only the monitoring date,
selection date, group identity, synchronization time, symbols, count,
validation metadata, and source hash. Broker credentials, tokens, account
data, names, and order state are not accepted by the schema.

Consumer states are `AVAILABLE`, `AVAILABLE_EMPTY`, `UNAVAILABLE`, `STALE`,
and `INVALID`. Every state is section-local. Missing or invalid discovery data
must not alter market coverage, full-market validation, regular-close policy,
email eligibility, or report delivery.

The report reuses `quotes.json`, `technicals.json`, and `states.json` first,
then compact full-market signals. `daily_report.py --fetch-discovery-missing`
requests only remaining symbols in one batch (maximum 50); it never refreshes
the full market, and failures remain in the optional section. VWAP and
intraday high/low remain `UNAVAILABLE` unless an audited source supplies them.

Production activation still requires two reviewed operations that are not
part of this change: publishing the validated discovery files to GitHub and
updating the ChatGPT Work instructions to consume this contract. The existing
`MARKET_DATA_READY` email and its send conditions remain unchanged.

After Trading Console has created a finalized immutable snapshot, the one-shot
event entrypoint is `tools/on_trading_discovery_snapshot.py SNAPSHOT --calendar
CALENDAR --repository RELAY`. It accepts the exact paths supplied by the
caller, performs no polling or date calculation, serializes concurrent runs,
and delegates validation, idempotency, isolated commit, non-force push, and
remote hash verification to `tools/publish_trading_discovery.py`. Connecting
this command to Trading Console's successful post-sync event remains an
external deployment step; no scheduler or background watcher is installed.

Production URLs after deployment:

- `https://raw.githubusercontent.com/hanull01/naver-krx-universe-relay/main/data/trading-discovery/latest.json`
- `https://raw.githubusercontent.com/hanull01/naver-krx-universe-relay/main/data/trading-discovery/YYYY-MM-DD.json`

Work must select the dated file whose `monitoringDate` equals the current KRX
trading date. It verifies `sourceSha256` by canonical JSON hashing of
`source`, `sourceGroup`, `groupId`, `selectionDate`, and `symbols`; keys are
sorted, UTF-8 is preserved, and separators are `,` and `:` without spaces.
`AVAILABLE_EMPTY` is a valid zero-candidate state. Missing, malformed,
hash-mismatched, or wrong-date data maps to `UNAVAILABLE`, `INVALID`, or
`STALE` only within the Kiwoom section.

The market-data email commit SHA and the discovery publisher commit SHA are
independent. Work must validate market artifacts against the email SHA and
discovery against its dated path plus `sourceSha256`; it must not require both
artifacts to come from one commit. Repeated emails or Work runs may occur, so
the report comparison key is `(monitoringDate, sourceSha256, market source
time)`, not email count.
