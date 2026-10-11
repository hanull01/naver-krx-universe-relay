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
then compact full-market signals. A bounded helper can request only remaining
symbols in one batch when an operator explicitly enables that step. VWAP and
intraday high/low remain `UNAVAILABLE` unless an audited source supplies them.

Production activation still requires two reviewed operations that are not
part of this change: publishing the validated discovery files to GitHub and
updating the ChatGPT Work instructions to consume this contract. The existing
`MARKET_DATA_READY` email and its send conditions remain unchanged.
