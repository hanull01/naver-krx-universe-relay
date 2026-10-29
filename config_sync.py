"""Safe GitHub backup of the Universe config only."""
import json
import subprocess

SYNC_ALLOWED_FILES = {"config/universe.json"}
FORBIDDEN_KEYS = {"password", "token", "secret", "credential", "privatekey", "private_key", "pin"}

def run(args, root, runner=subprocess.run):
    return runner(["git", *args], cwd=root, text=True, capture_output=True, timeout=15, check=False)

def sensitive(value):
    if isinstance(value, dict):
        return any(str(k).lower() in FORBIDDEN_KEYS or sensitive(v) for k, v in value.items())
    if isinstance(value, list): return any(sensitive(v) for v in value)
    return False

def status(root, runner=subprocess.run):
    changed = run(["diff", "--", "config/universe.json"], root, runner).stdout
    cached = run(["diff", "--cached", "--", "config/universe.json"], root, runner).stdout
    staged = [x for x in run(["diff", "--cached", "--name-only"], root, runner).stdout.splitlines() if x]
    blocked = [x for x in staged if x not in SYNC_ALLOWED_FILES]
    state = "LOCAL_CONFIG_CHANGES" if changed or cached else "SYNCED"
    return {"state": state, "dirty": bool(changed or cached), "stagedBlocked": blocked, "message": "저장하지 않은 설정 변경 있음" if state != "SYNCED" else "GitHub에 저장된 설정과 동일"}

def _empty_config(data):
    leaders = data.get("leaders") or {}
    return (not data.get("stocks")
            and not any(data.get(key) for key in ("sectors", "themes", "watchlists"))
            and not any(leaders.values()))


def _config_from_git(root, runner):
    result = run(["show", "HEAD:config/universe.json"], root, runner)
    if result.returncode:
        raise ValueError("GitHub 기준 설정을 읽을 수 없습니다.")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("GitHub 기준 설정 JSON이 올바르지 않습니다.") from exc


def _config_from_worktree(root):
    try:
        return json.loads((root / "config" / "universe.json").read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError("현재 Universe 설정 파일을 읽을 수 없습니다.") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("현재 Universe 설정 JSON이 올바르지 않습니다.") from exc


def comparison_summary(before, after):
    """Return a data-backed, user-safe summary for the exact config diff."""
    before_stocks = {str(row.get("itemCode")): row for row in before.get("stocks", [])}
    after_stocks = {str(row.get("itemCode")): row for row in after.get("stocks", [])}
    added_codes = sorted(set(after_stocks) - set(before_stocks))
    removed_codes = sorted(set(before_stocks) - set(after_stocks))
    changed = []
    for code in sorted(set(before_stocks) & set(after_stocks)):
        old, new = before_stocks[code], after_stocks[code]
        fields = {}
        for field in ("stockName", "enabled"):
            if old.get(field) != new.get(field):
                fields[field] = {"before": old.get(field), "after": new.get(field)}
        if fields:
            changed.append({"itemCode": code, "stockName": new.get("stockName") or old.get("stockName"), "fields": fields})

    group_changes = []
    for kind, key in (("sector", "sectors"), ("theme", "themes"), ("watchlist", "watchlists")):
        old_groups = before.get(key) or {}
        new_groups = after.get(key) or {}
        for name in sorted(set(old_groups) | set(new_groups)):
            if name not in old_groups:
                group_changes.append({"kind": kind, "name": name, "change": "ADDED",
                                      "added": sorted(str(code) for code in new_groups[name]), "removed": []})
                continue
            if name not in new_groups:
                group_changes.append({"kind": kind, "name": name, "change": "REMOVED",
                                      "added": [], "removed": sorted(str(code) for code in old_groups[name])})
                continue
            old_members = sorted(str(code) for code in old_groups.get(name, []))
            new_members = sorted(str(code) for code in new_groups.get(name, []))
            added_members = sorted(set(new_members) - set(old_members))
            removed_members = sorted(set(old_members) - set(new_members))
            if added_members or removed_members:
                group_changes.append({"kind": kind, "name": name, "change": "CHANGED",
                                      "added": added_members, "removed": removed_members})

    state = "EMPTY_SOURCE" if _empty_config(after) else "NO_CHANGES" if before == after else "CHANGED"
    return {
        "state": state,
        "stocks": {
            "added": [after_stocks[code] for code in added_codes],
            "removed": [before_stocks[code] for code in removed_codes],
            "changed": changed,
        },
        "groups": group_changes,
        "beforeStockCount": len(before_stocks),
        "afterStockCount": len(after_stocks),
    }


def preview(root, runner=subprocess.run):
    result = status(root, runner)
    result["diff"] = run(["diff", "HEAD", "--", "config/universe.json"], root, runner).stdout
    result["summary"] = comparison_summary(_config_from_git(root, runner), _config_from_worktree(root))
    return result

def remote_config_changed(root, runner=subprocess.run):
    """True only when the remote changed the one file this feature owns.

    Remote source-code commits must not block a Universe-settings backup, but
    a remote edit to config/universe.json requires an explicit human decision.
    """
    fetched = run(["fetch", "origin"], root, runner)
    if fetched.returncode:
        raise ValueError("GitHub 상태 확인에 실패했습니다.")
    changed = run(
        ["diff", "--name-only", "HEAD..origin/main", "--", "config/universe.json"],
        root,
        runner,
    )
    if changed.returncode:
        raise ValueError("GitHub 설정 변경 확인에 실패했습니다.")
    return bool(changed.stdout.strip())

def push(root, runner=subprocess.run):
    current = status(root, runner)
    if current["stagedBlocked"]: raise ValueError("다른 개발 파일이 Git staging 상태입니다. 설정만 안전하게 저장하려면 먼저 staged 상태를 정리해야 합니다.")
    with open(root / "config" / "universe.json", encoding="utf-8") as f:
        if sensitive(json.load(f)): raise ValueError("설정에 민감정보로 보이는 필드가 있어 GitHub 저장을 차단했습니다.")
    if current["state"] == "SYNCED": return {"ok": True, "noop": True}
    if remote_config_changed(root, runner):
        raise ValueError("GitHub에도 새로운 Universe 설정 변경이 있습니다. 자동 저장을 중단했습니다.")
    if run(["add", "--", "config/universe.json"], root, runner).returncode: raise ValueError("설정 staging에 실패했습니다.")
    names = run(["diff", "--cached", "--name-only"], root, runner).stdout.splitlines()
    if names != ["config/universe.json"]: raise ValueError("설정 외 파일이 staging되어 동기화를 중단했습니다.")
    if run(["commit", "-m", "Update universe configuration"], root, runner).returncode: raise ValueError("로컬 설정 commit에 실패했습니다.")
    result = run(["push", "origin", "main"], root, runner)
    return {"ok": result.returncode == 0, "pushed": result.returncode == 0, "message": "설정은 로컬 Git에 저장됨; GitHub 전송 실패" if result.returncode else "GitHub에 설정 저장됨"}
