# KRX → monitoring receipt integration (isolated, not active)

This branch adds a read-only validator and field mapper for the actual M10
consumer contract. It is not wired to a GitHub Actions workflow and does not
publish KRX data.

## Evidence used

The local M10 generation was inspected directly:

- generation manifest schema: `krx_generation_v1`
- consumer schema: `krx_monitoring_interchange_v1`
- consumer date field: `date`
- generation identifier: `generation_manifest.json.generation` and `CURRENT`
- monitoring count: `entity_count == len(entities)`
- completed-session basis: `price_reference=KRX_OFFICIAL_1530_CLOSE`
- after-hours policy: `aftermarket_used=false`
- source metrics integrity: `source_metrics_gzip_sha256`
- actual M10 entity categories: `stock`, `etf`, `index`

Stock and ETF identifiers are six-character alphanumeric codes. Index
identifiers are descriptive KRX labels such as `KOSPI`, so the adapter does
not incorrectly impose the stock-code rule on indexes.

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

## Operational boundaries

- no M10 LaunchAgent change
- no KRX API call
- no KRX raw or derived data committed
- no Cloudflare schedule change
- no workflow/main change
- existing NAVER current snapshots remain separate
