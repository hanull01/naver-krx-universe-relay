# Legacy Universe CLI

For normal local Universe management, use [Universe Manager](universe-manager.md).
`universe_cli.py` remains supported for backward compatibility and its still-unique
`apply`, `rename-stock`, and isolated-worktree `--publish` flows. No production
workflow or collector currently invokes it directly.

`config/universe.json` is the operating source of truth. The `legacy33` watchlist preserves the original 33 stocks; `data/core33*.json` remains compatibility-only, and new stocks use Universe-wide `data/quotes*.json` and related outputs.

```bash
# A. 단순 종목 추가 (기본 enabled=true)
python3 universe_cli.py add-stock 272210 한화시스템 --enabled
python3 universe_cli.py add-stock 272210 한화시스템 --disabled
# B. 그룹을 함께 추가
python3 universe_cli.py add-stock 272210 한화시스템 --sector 방산 --theme 우주항공 --watchlist 관심종목 --create-groups --enabled
# C. ChatGPT apply JSON
python3 universe_cli.py apply '{"stock":{"itemCode":"272210","stockName":"한화시스템","enabled":true},"sectors":["방산"],"createGroups":true}'
# D. dry-run / E. validate / F. disable/remove
python3 universe_cli.py apply '{"stock":{"itemCode":"272210","stockName":"한화시스템"}}' --dry-run
python3 universe_cli.py validate
python3 universe_cli.py disable-stock 272210
python3 universe_cli.py remove-stock 272210

# 종목명 변경: 코드·enabled·그룹 membership은 유지
python3 universe_cli.py rename-stock 079550 LIG디펜스앤에어로스페이스
python3 universe_cli.py rename-stock 079550 LIG디펜스앤에어로스페이스 --publish

# GitHub publish (권장: 서브커맨드 뒤 옵션)
python3 universe_cli.py add-stock 201490 미투온 --watchlist 관심종목 --publish
python3 universe_cli.py add-stock 201490 미투온 --publish --dry-run
# publish는 isolated origin/main worktree에서 config/universe.json만 commit/push한다.
# 실패 시 로컬 설정은 보존되며 force push는 사용하지 않는다.
```
