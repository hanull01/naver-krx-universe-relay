#!/usr/bin/env python3
"""Safe, local CLI manager for config/universe.json.

This tool only changes the Universe after explicit user commands.  It does not
collect market data, promote discovery candidates, or make recommendations.
"""

import argparse
import copy
import difflib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_PATH = ROOT / "config" / "universe.json"
GROUP_KEYS = {"sector": "sectors", "theme": "themes", "watchlist": "watchlists"}


def code(value):
    if not re.fullmatch(r"\d{6}", value):
        raise argparse.ArgumentTypeError("종목코드는 6자리 숫자여야 합니다")
    return value


def load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"잘못된 JSON 구조: {exc.msg}") from exc


def validation(data):
    """Return (errors, warnings); warnings never silently change the data."""
    errors, warnings = [], []
    if not isinstance(data, dict):
        return ["최상위 JSON은 object여야 합니다"], warnings
    stocks = data.get("stocks")
    if not isinstance(stocks, list):
        return ["stocks는 배열이어야 합니다"], warnings
    codes, names = [], []
    for stock in stocks:
        if not isinstance(stock, dict):
            errors.append("stock 항목은 object여야 합니다")
            continue
        item_code = stock.get("itemCode")
        name = stock.get("stockName")
        if not isinstance(item_code, str) or not re.fullmatch(r"\d{6}", item_code):
            errors.append("stock itemCode는 6자리 숫자여야 합니다")
        else:
            codes.append(item_code)
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{item_code}: stockName이 필요합니다")
        else:
            names.append(name.strip())
        if not isinstance(stock.get("enabled"), bool):
            errors.append(f"{item_code}: enabled는 boolean이어야 합니다")
    if len(codes) != len(set(codes)):
        errors.append("중복 stock itemCode")
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        warnings.append("중복 stockName 경고: " + ", ".join(duplicates))
    known = set(codes)
    for kind, key in GROUP_KEYS.items():
        groups = data.get(key)
        if not isinstance(groups, dict):
            errors.append(f"{key}는 object여야 합니다")
            continue
        for name, members in groups.items():
            if not isinstance(name, str) or not name.strip() or not isinstance(members, list):
                errors.append(f"잘못된 {kind} group: {name}")
                continue
            if not members:
                warnings.append(f"빈 {kind} group: {name}")
            if len(members) != len(set(members)):
                errors.append(f"{kind}/{name}: 중복 member")
            unknown = [member for member in members if member not in known]
            if unknown:
                errors.append(f"{kind}/{name}: 존재하지 않는 종목 참조: {', '.join(unknown)}")
    leaders = data.get("leaders", {})
    if not isinstance(leaders, dict):
        errors.append("leaders는 object여야 합니다")
    else:
        for kind, entries in leaders.items():
            if kind not in GROUP_KEYS or not isinstance(entries, dict):
                errors.append(f"잘못된 leaders kind: {kind}")
                continue
            groups = data.get(GROUP_KEYS[kind], {})
            for name, members in entries.items():
                if name not in groups or not isinstance(members, list):
                    errors.append(f"leaders/{kind}/{name}: 존재하지 않는 group")
                    continue
                invalid = [member for member in members if member not in groups[name]]
                if invalid:
                    errors.append(f"leaders/{kind}/{name}: group member가 아닌 leader: {', '.join(invalid)}")
    return errors, warnings


def validate_or_raise(data):
    errors, warnings = validation(data)
    if errors:
        raise ValueError("; ".join(errors))
    return warnings


def stock_map(data):
    return {stock["itemCode"]: stock for stock in data["stocks"]}


def group(data, kind, name):
    groups = data[GROUP_KEYS[kind]]
    if name not in groups:
        raise ValueError(f"존재하지 않는 {kind} group: {name}")
    return groups[name]


def require_stock(data, item_code):
    try:
        return stock_map(data)[item_code]
    except KeyError as exc:
        raise ValueError(f"존재하지 않는 종목: {item_code}") from exc


def references(data, item_code):
    found = []
    for kind, key in GROUP_KEYS.items():
        for name, members in data[key].items():
            if item_code in members:
                found.append(f"{kind}/{name}")
    for kind, entries in data.get("leaders", {}).items():
        for name, members in entries.items():
            if item_code in members:
                found.append(f"leader/{kind}/{name}")
    return found


def render(data):
    stocks = data["stocks"]
    names = {stock["itemCode"]: stock["stockName"] for stock in stocks}
    lines = [f"Enabled stocks: {sum(stock['enabled'] for stock in stocks)}"]
    for kind, key in GROUP_KEYS.items():
        lines.append(f"\n[{kind}]")
        leaders = data.get("leaders", {}).get(kind, {})
        for name, members in data[key].items():
            member_text = ", ".join(
                f"{item} {names.get(item, '?')}" for item in members
            )
            leader_text = (
                " leaders=" + ", ".join(leaders.get(name, []))
                if leaders.get(name) else ""
            )
            lines.append(f"- {name}: {member_text or '(empty)'}{leader_text}")
    disabled = [f"{stock['itemCode']} {stock['stockName']}" for stock in stocks if not stock["enabled"]]
    lines.append("\n[disabled]")
    lines.extend(f"- {item}" for item in disabled) if disabled else lines.append("- none")
    return "\n".join(lines)


def serialized(data):
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def save(path, original, updated, dry_run=False, now=None):
    warnings = validate_or_raise(updated)
    before, after = serialized(original), serialized(updated)
    diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=str(path), tofile=str(path)))
    for warning in warnings:
        print(f"경고: {warning}")
    if before == after:
        print("UNCHANGED")
        return False
    if dry_run:
        print(diff)
        return False
    path = Path(path)
    history = path.parent / "history"
    history.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup = history / f"universe-{stamp}.json"
    suffix = 1
    while backup.exists():
        backup = history / f"universe-{stamp}-{suffix}.json"
        suffix += 1
    backup.write_text(before, encoding="utf-8")
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".universe-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(after)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"SAVED backup={backup}")
    return True


def mutate(data, args):
    if args.command == "stock":
        if args.action == "add":
            existing = stock_map(data).get(args.code)
            if existing:
                print(f"ALREADY EXISTS: {args.code} {existing['stockName']}")
            else:
                data["stocks"].append({"itemCode": args.code, "stockName": args.name.strip(), "enabled": True})
        elif args.action in ("enable", "disable"):
            require_stock(data, args.code)["enabled"] = args.action == "enable"
        else:
            require_stock(data, args.code)
            refs = references(data, args.code)
            if refs:
                raise ValueError("hard delete blocked; references: " + ", ".join(refs))
            data["stocks"] = [stock for stock in data["stocks"] if stock["itemCode"] != args.code]
    elif args.command == "group":
        groups = data[GROUP_KEYS[args.kind]]
        leaders = data.setdefault("leaders", {}).setdefault(args.kind, {})
        if args.action == "create":
            if args.name in groups:
                raise ValueError(f"이미 존재하는 {args.kind} group: {args.name}")
            groups[args.name] = []
        elif args.action == "delete":
            group(data, args.kind, args.name)
            del groups[args.name]
            leaders.pop(args.name, None)
        elif args.action == "rename":
            members = group(data, args.kind, args.name)
            if args.new_name in groups:
                raise ValueError(f"이미 존재하는 {args.kind} group: {args.new_name}")
            groups[args.new_name] = members
            del groups[args.name]
            if args.name in leaders:
                leaders[args.new_name] = leaders.pop(args.name)
        elif args.action == "member":
            members = group(data, args.kind, args.name)
            require_stock(data, args.code)
            if args.member_action == "add" and args.code not in members:
                members.append(args.code)
            elif args.member_action == "remove" and args.code in members:
                members.remove(args.code)
                leaders[args.name] = [item for item in leaders.get(args.name, []) if item != args.code]
                if not leaders[args.name]:
                    leaders.pop(args.name, None)
    else:  # leader
        members = group(data, args.kind, args.name)
        leaders = data.setdefault("leaders", {}).setdefault(args.kind, {})
        if args.action == "set":
            require_stock(data, args.code)
            if args.code not in members:
                raise ValueError("leader는 해당 group member여야 합니다")
            if args.code not in leaders.setdefault(args.name, []):
                leaders[args.name].append(args.code)
        else:
            if args.code in leaders.get(args.name, []):
                leaders[args.name].remove(args.code)
            if not leaders.get(args.name):
                leaders.pop(args.name, None)


def parser():
    p = argparse.ArgumentParser(description="Safe Universe Manager")
    p.add_argument("--file", type=Path, default=DEFAULT_PATH, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("show")
    sub.add_parser("validate")
    stock = sub.add_parser("stock"); stock_sub = stock.add_subparsers(dest="action", required=True)
    add = stock_sub.add_parser("add"); add.add_argument("--code", type=code, required=True); add.add_argument("--name", required=True)
    for action in ("enable", "disable"):
        q = stock_sub.add_parser(action); q.add_argument("--code", type=code, required=True)
    delete = stock_sub.add_parser("delete"); delete.add_argument("--code", type=code, required=True); delete.add_argument("--hard", action="store_true", required=True)
    group_parser = sub.add_parser("group"); group_sub = group_parser.add_subparsers(dest="action", required=True)
    for action in ("create", "delete"):
        q = group_sub.add_parser(action); q.add_argument("--kind", choices=GROUP_KEYS, required=True); q.add_argument("--name", required=True)
    rename = group_sub.add_parser("rename"); rename.add_argument("--kind", choices=GROUP_KEYS, required=True); rename.add_argument("--name", required=True); rename.add_argument("--new-name", required=True)
    member = group_sub.add_parser("member"); member.add_argument("--kind", choices=GROUP_KEYS, required=True); member.add_argument("--name", required=True); member.add_argument("member_action", choices=("add", "remove")); member.add_argument("--code", type=code, required=True)
    leader = sub.add_parser("leader"); leader_sub = leader.add_subparsers(dest="action", required=True)
    for action in ("set", "remove"):
        q = leader_sub.add_parser(action); q.add_argument("--kind", choices=GROUP_KEYS, required=True); q.add_argument("--name", required=True); q.add_argument("--code", type=code, required=True)
    for command in (*stock_sub.choices.values(), *group_sub.choices.values(), *leader_sub.choices.values()):
        command.add_argument("--dry-run", action="store_true")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    data = load(args.file)
    if args.command == "show":
        validate_or_raise(data)
        print(render(data))
        return
    if args.command == "validate":
        warnings = validate_or_raise(data)
        for warning in warnings:
            print(f"경고: {warning}")
        print("유효합니다")
        return
    updated = copy.deepcopy(data)
    mutate(updated, args)
    save(args.file, data, updated, args.dry_run)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(f"오류: {exc}")
