# KRX → monitoring receipt integration (isolated, not active)

This branch adds a read-only validator and field mapper for the actual M10
consumer contract. It is not wired to a GitHub Actions workflow and does not
publish KRX data.

## Evidence used

The local M10 generation was inspected directly:

- generation manifest schema: `krx_m9_generation_manifest_v1`
- consumer schema: `krx_monitoring_interchange_v1`
- consumer date field: `date`
- generation identifier: `generation_manifest.json.generation` and `CURRENT`
- monitoring count: `entity_count == len(entities)`
- completed-session basis: `price_reference=KRX_OFFICIAL_1530_CLOSE`
- after-hours policy: `aftermarket_used=false`
- source metrics integrity: `source_metrics_gzip_sha256`
- actual M10 entity categories: `stock`, `etf`, `index`
- actual 2026-11-02 counts: 2,767 stocks, 1,183 ETFs, 132 indexes

Stock and ETF identifiers are six-character alphanumeric codes. Index
identifiers are descriptive KRX labels such as `KOSPI`, so the adapter does
not incorrectly impose the stock-code rule on indexes. Index display labels
can repeat across KOSPI/KOSDAQ; the source-qualified `identity` is the unique
key. Stock/ETF codes remain unique.

## Mapping

`tools/krx_sync_receipt.py::map_m10_entity` maps verified M10 entities to a
neutral report adapter shape:

| M10 field | Adapter field | Rule |
| --- | --- | --- |
| `code`, `name`, `category`, `asof` | `itemCode`, `name`, `assetType`, `asOfDate` | identity preserved |
| `MA.MA5..MA240` | `technicals.ma5..ma240` | KRX values preserved, nullable |
| `returns_pct.*` | `technicals.return*Pct` | KRX values preserved |
| `high_breakouts.*` | `technicals.highBreakout*` | KRX state preserved |
| `open_regular`, `close_regular`, `official_daily_volume` | `regularSession.*` | KRX regular-session contract |
| `source_api`, `identity`, `asof` | `provenance.*` | source lineage preserved |
| absent from M10 | `currentSnapshot` | remains `null`; NAVER is a separate layer |

Completed KRX moving averages are not recomputed from NAVER current prices.
The adapter does not include raw KRX bars, after-hours prices, or a public
publication path.

## Receipt and publication gate

The v2 receipt must include SHA/byte-size entries for both
`monitoring_interchange.json` and `generation_manifest.json`, and must bind
the same `tradingDate`, `generationId`, entity count, and M10 schema. The
validator rejects the staging directory unless `publicationAuthorized=true`.
That flag is an explicit license/policy decision; it is not inferred from
technical validation.

Successful validation still returns `reportInputReady=false` in this branch.
Transport, legal redistribution approval, report wiring, Cloudflare changes,
and public publication remain separate deployment gates.

`stage_m10_report_inputs` materializes a local-only adapter directory for the
existing `full_market_technical_adapter` contract. It emits
`technicals.json`, `states.json`, `group-states.json`, `provenance.json`, and a
`conflict-audit.json` plus a `PARTIAL` report projection. For the inspected generation, all 4,082 entities
are validated, 3,950 stock/ETF rows are staged for the existing instrument
adapter, and 132 index rows remain in the separate index metadata section.
Per-indicator null counts are reported; nulls are not synthesized.

When the staging source is `KRX_M10_GENERATION`, KRX is the primary and only
historical indicator source. The adapter records
`KRX_PRIMARY_NO_MERGE` and
`MISSING_OR_LAGGED_NO_NAVER_HISTORICAL_FALLBACK`; a missing KRX row remains
missing or lagged instead of being filled from NAVER history. NAVER current
and after-hours observations remain a separate current-snapshot input. The
report renderer prints unavailable breadth values as `확인 불가` when the M10
generation does not provide a verified aggregate, rather than fabricating
zeros or failing the report.

The staged report projection also contains a deterministic KRX aggregate:
stocks and ETFs are counted separately, each indicator uses its own valid-row
denominator, and direction breadth uses only rows with a valid `return_1d_pct`.
`MA.MA20`, `MA.MA60`, and `MA.MA120` are exposed as numeric `ma20`, `ma60`,
and `ma120`; `volume_ratio_prev20` is exposed as `volumeRatio20`. The 132
index entities remain source-qualified by `identity`. Relative strength,
new-low, and trading-value fields are marked unavailable because they are not
present in this M10 interchange; they are never inferred from NAVER.

The report consumer receives KRX historical values in the legacy `technical`
shape while NAVER current observations remain in the separate `market` and
current-snapshot fields. The isolated 2026-11-02 report QA produced matching
JSON, Markdown, and HTML values for the KRX breadth and Samsung Electronics
MA20/MA60 fields.

## Private delivery bridge

`tools/krx_m10_sync.py` resolves `krx_m9_runtime_data/CURRENT`, verifies the
matching generation manifest and consumer SHA/byte counts, and stages only
`generation_manifest.json` plus `monitoring_interchange.json` in a local
durable outbox. Raw files, the metrics bundle, credentials, and GitHub
contents are excluded. A receiver rechecks the generation, date, entity
count, schema, and SHA before atomic promotion. It records accepted
generation/date/SHA tuples, returns `NO_CHANGES` for repeats, rejects a
different generation for the same date, rejects older trading dates without
moving `CURRENT`, and leaves failed attempts in the queue for the next
invocation after restart. Receiver promotion uses a durable pending state,
an atomic generation directory move, and a final atomic `CURRENT` update;
ambiguous ledger/current state is fail-closed for manual review. A receiver
lock makes concurrent delivery return `ALREADY_RUNNING`, and existing
generations are never deleted or overwritten. The module also exposes an
authentication/replay-protection interface for a future approved transport;
SHA-256 alone is not treated as sender authentication. This bridge is
local-only and does not activate an external transport.

`derived_input_resolver.py` is intentionally not changed: its
`monitoring-derived` pointer is the existing NAVER/derived generation
contract, while M10 KRX staging is an independent source that must not be
silently mixed into that resolver.

## Opt-in legacy-engine bridge

`relay.py` now accepts `--krx-staging <verified-staging-dir>` as an explicit,
isolated input mode.  In that mode `tools/krx_operational_bridge.py` projects
the existing M10 technical rows into the legacy `technicals.json` shape,
`relay.build_states` compares the NAVER current quote with those KRX values,
and existing group/V1/V2 consumers can read the same generated payloads.  No
NAVER daily history is opened and no KRX indicator is recalculated.  The
default relay command remains unchanged, so this branch does not activate the
operational switch or alter any workflow schedule.

The two former NAVER historical producers remain available for compatibility
and recovery: `relay.calculate_technicals` and
`tools.build_full_market_derivatives._technical_row`.  The opt-in KRX path
does not call either producer.  `current`, `regularSession`, and after-hours
observations stay separate from KRX historical indicators.

## V1/V2 impact comparison

`tools/krx_v1v2_impact.py` is a read-only, in-memory comparison tool. It
reuses `aggregate_industries`, `build_theme_signals`,
`dynamic_universe_manager.build_management_result`, and the existing V2
evaluator without changing their formulas. It overlays only KRX historical
MA/return/prior-high fields onto the existing feature scope and keeps NAVER
current price, change rate, and trading value as current observations.

For impact analysis, the analysis trading date is the NAVER current snapshot
date and the KRX technical date is retained separately. The legacy engine's
`ret_1d` input is supplied from NAVER's current `changeRate`; KRX's confirmed
one-day return is retained as `krx_ret_1d_pct` and is not used as today's
relative strength. KRX historical close/volume validity and NAVER current
quote validity are separate fields.

The KRX `volume_ratio_prev20` field is retained as
`krx_volume_ratio_prev20`; it is never copied into
`current_volume_ratio_20`. Missing KRX fields remain nullable and there is no
NAVER historical fallback. Stock/ETF filtering, date mismatch, duplicate
identity, and missing-code diagnostics are emitted before comparison.

The tool can be run against a local M10 interchange and existing monitoring
snapshots to compare group metrics, V1 status/leader changes, and V2 status
changes. The output is an audit artifact selected by the caller; no canonical
monitoring file, workflow, or external transport is written by the tool.

## Operational boundaries

- no M10 LaunchAgent change
- no KRX API call
- no KRX raw or derived data committed
- no Cloudflare schedule change
- no workflow/main change
- existing NAVER current snapshots remain separate
