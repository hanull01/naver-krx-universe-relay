# Universe Management

`config/universe.json` is the operating source of truth. Use `universe_manager.py`
for normal local management; it does not collect data, change scoring, or add Discovery
candidates automatically.

## Operating rule

Run `--dry-run` first for every change. It validates the prospective configuration and
prints a unified diff without writing `config/universe.json`.

```bash
python3 universe_manager.py show
python3 universe_manager.py validate

python3 universe_manager.py stock add \
  --code 005930 \
  --name 삼성전자 \
  --dry-run

python3 universe_manager.py stock disable --code 005930 --dry-run
python3 universe_manager.py stock enable --code 005930 --dry-run
```

Use `disable` as the normal removal method. It preserves historical market and research
links. Hard deletion is exceptional and is explicitly guarded; it refuses to run while
the stock is referenced by a sector, theme, watchlist, or leader entry.

```bash
python3 universe_manager.py stock delete --code 005930 --hard --dry-run
```

## Groups

The same commands apply to `sector`, `theme`, and `watchlist` kinds.

```bash
python3 universe_manager.py group create --kind theme --name AI --dry-run
python3 universe_manager.py group delete --kind theme --name AI --dry-run
python3 universe_manager.py group rename --kind theme --name AI --new-name AI플랫폼 --dry-run

python3 universe_manager.py group member --kind theme --name AI add --code 005930 --dry-run
python3 universe_manager.py group member --kind theme --name AI remove --code 005930 --dry-run
```

## Leaders

A leader must already be a member of the selected group.

```bash
python3 universe_manager.py leader set --kind sector --name 반도체\ 대형주 --code 005930 --dry-run
python3 universe_manager.py leader remove --kind sector --name 반도체\ 대형주 --code 005930 --dry-run
```

## Validation, atomic writes, and backups

Before every save, the manager validates stock codes, duplicate codes, stock names,
group references, leaders, enabled flags, and JSON structure. Duplicate stock names and
empty groups are reported as warnings so a newly created group can be populated in a
later explicit command; invalid references and invalid leaders are errors.

For a real change, the manager saves the previous configuration to
`config/history/universe-YYYYMMDD-HHMMSS.json`, then writes the validated replacement
through a temporary file and atomic replace. No backup is made for dry-runs or no-op
commands.

## Discovery boundary

Discovery output is a candidate list only. It never promotes a stock into the Universe.
After review, a user explicitly adds the stock with `universe_manager.py`; disabling is
the default removal method, and hard delete is reserved for exceptional cleanup.
