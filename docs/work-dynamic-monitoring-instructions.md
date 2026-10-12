# Dynamic monitoring input contract (draft)

This is a staged Relay contract. It does not modify Work configuration.

Work may use `dynamic-monitoring-context-v1` only when its theme section is
`AVAILABLE`. It must present each period's `quality`, including `NON_ATOMIC`
and `updatedAtMeaning: UNKNOWN`, rather than claiming one common snapshot
time. `systemLeaders` are `UNKNOWN` without verified cached membership and
market data. NAVER top-three rows and system leaders are different sources.

If the theme section is unavailable, continue market, industry, watchlist and
Kiwoom reporting unchanged. Do not request all theme memberships: use cached
or selected top-theme memberships only. Treat period code differences as a
source comparison, not theme deletion.
