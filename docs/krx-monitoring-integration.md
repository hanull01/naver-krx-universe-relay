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
- verified 2026-11-03 generation: `gen_20261103_1793748502562386000`
- verified 2026-11-03 counts: 2,768 stocks, 1,185 ETFs, 132 indexes
- monitoring consumer count: 4,085; trading consumer count: 3,953

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
`conflict-audit.json` plus a `PARTIAL` report projection. For the inspected
2026-11-03 generation, all 4,085 entities are validated, 3,953 stock/ETF rows
are staged for the existing instrument adapter, and 132 index rows remain in
the separate index metadata section. The 20 repeated display codes are index
labels shared across markets; source-qualified identities remain unique.
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
current-snapshot fields. The isolated 2026-11-03 report QA produced JSON,
Markdown, and HTML outputs from the staged KRX input. It preserved `KRX` as
the historical indicator source, kept NAVER current observations separate,
and reported the staged projection as `PARTIAL` because it is not a final
production generation.

The local outbox/receiver QA accepted the generation once, reproduced both
artifact SHA-256 values on receiver readback, and returned `NO_CHANGES` on the
second delivery attempt. This is a local receiver test, not a GitHub data
publication.

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

## Operational boundaries

- no M10 LaunchAgent change
- no KRX API call
- no KRX raw or derived data committed
- no Cloudflare schedule change
- no workflow/main change
- existing NAVER current snapshots remain separate

## Trigger status

The current `daily-report.yml` has `workflow_dispatch` only; it has no fixed
23:00 schedule. `backfill-derived.yml` still contains its existing manual
dispatch of `daily-report.yml` after a successful derived backfill. The
un-deployed `ops/external-scheduler/worker.js` still contains legacy 23:00
and 08:00 slot mappings; this branch does not activate or change that
external scheduler. No KRX 08:05 → sync → report chain is active in this
branch. The safe production sequence remains a future gated integration:
verify the KRX generation, use an approved private transport, re-read and
verify the receiver SHA, then invoke the report and pre-open stages. Until
those gates are approved, the existing NAVER paths remain in place and are
not deleted.
