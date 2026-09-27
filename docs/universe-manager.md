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

## Local Web UI

The CLI remains the canonical management backend. `universe_web.py` is a local GUI layer
that reuses its validation, mutation, backup, and atomic-write behavior.

```bash
python3 universe_web.py
# Open http://127.0.0.1:8765
```

The server binds only to `127.0.0.1`; it has no deployment or external-publication mode.
Use the UI in this order:

1. Dashboard to inspect current status and validation.
2. Stocks or Groups to review the current configuration.
3. Enter a management action and select **Preview**.
4. Review validation messages and the diff, then select **Apply**.

The UI never changes the configuration on dashboard/list requests or Preview. Apply is
blocked on validation errors, rejects configuration changes made after Preview, and asks
for explicit confirmation before hard delete. Warnings remain visible but can be applied.
Discovery candidates remain manual review items; they are not shown or promoted by v1.
