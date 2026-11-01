# Dynamic Universe Manager

The dynamic manager is a staged, offline consumer of the existing full-market
evidence. It never changes `config/universe.json`, never calls a market API,
and never sends orders.

## Data ownership

| Area | Owner | Published representation |
|---|---|---|
| `AUTO_SECTOR` | full-market rules | `data/dynamic-universe/latest.json.sectors` |
| `AUTO_THEME` | verified theme evidence only | `themes`, or an explicit `PENDING` reason |
| `AUTO_LEADER` | qualified sector/theme members | `leaders` |
| `KIWOOM_DISCOVERY` | validated discovery snapshot | `kiwoomDiscovery` |
| `USER_WATCHLIST` | user actions only | configured groups in `config/universe.json` |

One code may have several memberships. `collectionCodes` is deduplicated by
code while `memberships.sources` and `memberships.reasons` retain provenance.
The fixed baseline remains untouched until a separate operations approval.

## Selection and persistence

Thresholds are stored in `config/dynamic_universe_policy.json`. A group must
pass every configured hard threshold and the minimum score; the engine does
not fill a fixed top-N quota. Activation and removal confirmations provide
hysteresis. The lifecycle is `DISCOVERED`, `ACTIVE`, `WATCH`, `REMOVED`, and
`REENTERED`. Leaders are capped at three per active group, but zero is valid.

Run locally without writing:

```bash
python tools/build_dynamic_universe.py --no-write
```

The GitHub market refresh calls the same builder after successful full-market
evidence generation and stages only the two reviewed dynamic JSON artifacts.
No verified GitHub-to-Mac transport currently exists. `config_sync.py` is a
Mac-to-GitHub configuration backup, not a receive path. Therefore the Mac UI
must report a missing local `latest.json` as unpublished/PENDING; it must not
pretend that GitHub Actions can reach localhost.

## Safe migration

1. Preserve `config/universe.json`, `config/history`, local authentication,
   report history, ports, and Trading Console settings.
2. Publish and compare the staged dynamic files beside the fixed baseline.
3. Verify collection, reporting, Gmail/Work contracts, security, latency, and
   artifact size.
4. Obtain explicit user approval before changing the fixed list or any
   production configuration. No operational deletion is part of this change.
