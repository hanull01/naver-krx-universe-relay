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

The source contract is deliberately allow-listed. `KIWOOM_WATCHLIST` requires
`source_group=전일후보군`. `KIWOOM_CONDITION_SEARCH` requires
`source_group=장종료후 단타후보군 탐색`, a `group_id` of
`CONDITION:<numeric sequence>`, and a KST synchronization timestamp. Both
sources use the same immutable file and hash contract. Because the source,
group, and group id are hash inputs, two sources with identical symbols have
different hashes and therefore conflict instead of overwriting one another on
the same monitoring date.

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
remote hash verification to `tools/publish_trading_discovery.py`. Trading
Console invokes this command only after the 21:00 KST condition search has
successfully finalized the next-session snapshot. Manual `전일후보군` sync may
update the console's mutable candidates but does not create or publish an
official snapshot. Publication remains disabled until explicitly activated.
No scheduler or background watcher is installed.

Production URLs after deployment:

- `https://raw.githubusercontent.com/hanull01/naver-krx-universe-relay/main/data/trading-discovery/latest.json`
- `https://raw.githubusercontent.com/hanull01/naver-krx-universe-relay/main/data/trading-discovery/YYYY-MM-DD.json`

Work must select the dated file whose `monitoringDate` equals the current KRX
trading date. It verifies `sourceSha256` by first mapping the published
camelCase values to the exact snake_case object keys `source`, `source_group`,
`group_id`, `selection_date`, and `symbols`. Serialize with
`ensure_ascii=False`, `sort_keys=True`, and `separators=(",", ":")`, encode
as UTF-8, then calculate SHA-256. The 2026-10-12 production snapshot recomputes
to `09616de26156b84977af7c1f7c4b1710364ddc5a12a43a1efe1235da3d141aa8`;
this example is not a substitute for recomputing each payload.
`AVAILABLE_EMPTY` is a valid zero-candidate state. Missing, malformed,
hash-mismatched, or wrong-date data maps to `UNAVAILABLE`, `INVALID`, or
`STALE` only within the Kiwoom section.

The market-data email commit SHA and the discovery publisher commit SHA are
independent. Work must validate market artifacts against the email SHA and
discovery against its dated path plus `sourceSha256`; it must not require both
artifacts to come from one commit. Repeated emails or Work runs may occur, so
the report comparison key is `(monitoringDate, sourceSha256, market source
time)`, not email count.

## Supplemental candidate quotes

The hourly market workflow may publish only candidate symbols absent from both
the 42-stock quotes and current compact full-market signals:

- `data/trading-discovery/quotes/latest.json`
- `data/trading-discovery/quotes/YYYY-MM-DD.json`

The payload binds its rows to `monitoringDate`, `candidateSourceSha256`, and
the core quote file's `marketSourceTime`. Consumers must require all three to
match their current candidate and market inputs. Rows retain NAVER source time,
session, price basis, price, change rate, volume, and trading value. Technical
indicators are not fabricated. `PARTIAL` may contain verified rows plus explicit
missing symbols; `UNAVAILABLE` is not published over a prior valid file.

Collection runs after core and full-market collection, requests at most 50
remaining candidates in one batch, and performs no call when all candidates
are already covered or the same candidate hash and market source time were
previously published. Its failure is optional: core market publication and the
existing `MARKET_DATA_READY` condition remain unchanged.
