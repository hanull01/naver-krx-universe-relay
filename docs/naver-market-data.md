# NAVER Market Data Layer

`naver_market.py` is an independent, read-only collector for validated NAVER domestic-market APIs. It does not use or modify the existing Universe, quote, technical, state, or legacy compatibility outputs.

## Supported datasets

- Rankings: seven top-20 KRX/ALL snapshots.
- Industries: the full category list selected by the validated endpoint resolver.
  The PC cursor endpoint is primary, the mobile sectors endpoint is a fallback,
  and the legacy PAGE_INDEX endpoint is diagnostic-only. HTTP, schema, date,
  duplicate, pagination, and completeness failures remain explicit `ERROR`.
- Investor flow: `FOREIGNER` and `ORGANIZATION`, `DAY` period, top-20 buy and sell lists.

Theme collection is **DISABLED_PENDING_RESEARCH_GATE**. The observed theme-list duplicate means production code must not call a theme endpoint yet.

## Outputs

- `data/market/manifest.json`
- `data/market/rankings.json`
- `data/market/industries.json`
- `data/market/investor-flow.json`

Every root records `generatedAt` (collector time in KST), `source: "NAVER"`, and `status`. `generatedAt` is not a source market timestamp. NAVER fields such as `thistime`, `tradableStatusUpdatedAt`, `bizdateFrom`, `bizdateTo`, and `toRankingAt` are preserved in `source` without timezone interpretation.

## Normalization and preservation

Normalized rows use `code`/`name` and parsed numeric values without unit conversion. Each row also retains the complete NAVER source row in `source`. Investor `accTradeVolume` and `accTradeAmount` retain their original names: buy/sell direction is represented only by the parent container, because field semantics remain partial.

Industry identifiers use NAVER `code`/`no`, never list position. The current
industry and membership endpoints use a cursor with `hasNext=false` termination;
the legacy fallback retains `startIdx`/empty-array termination. Ranking `startIdx`
is an offset. Investor `startIdx` is a PAGE_INDEX; its full end condition is
not currently available because the endpoint returns no total metadata.

## CLI

```bash
python3 naver_market.py rankings
python3 naver_market.py industries
python3 naver_market.py investors
python3 naver_market.py industry-members 297
python3 naver_market.py industry-info 297
python3 naver_market.py all
python3 naver_market.py all --no-write
```

`all` preserves the existing DAY `investors` array and adds a backward-compatible `multiPeriod` object for WEEK, MONTH, and THREE_MONTH. Each period is fully paged and retains NAVER's `accTradeVolume` and `accTradeAmount` names; it does not calculate or label a net buy amount.

`industry-membership --if-stale` writes `data/market/industry-membership.json` only when its successful cache is older than 24 hours. The 15-minute workflow invokes it, but normal runs make no membership API calls. A failed or partial refresh is written as current status rather than silently retaining an old success.

Live contract probe (2026-09-24 KST): `trendForeignOrg` accepted both investor types and all three multi-period values. Each response had `sections.buyRankList`/`sellRankList`; both lists were 20 rows on `startIdx` 0–4 and empty on 5. Buy `accTradeAmount` values were positive and sell values negative; `bizdateFrom`/`bizdateTo` were present, while `toRankingAt` was null for the multi-period samples. `/upjong/list` returned 79 categories in 20/20/20/19/0 rows. `/upjong/{no}/stocklist` used the same zero-based page index and empty-array termination. Category 25 required 78 calls (1,542 rows), so the member cap is 100 rather than the category-list cap of 20. The complete membership pass made 344 requests (5 category-list + 339 member-list), returned 4,412 unique codes, and observed no multi-industry memberships. The alternative `/domestic/sector/item/list?...` returned HTTP 404 HTML and is excluded.

## External Integration Research Gate

Before adding an external service or endpoint: research existing public implementations and official material, compare candidates, make small current read-only validation requests, establish the response/pagination contract, then design and implement. A newly proposed theme collector must restart this gate.
