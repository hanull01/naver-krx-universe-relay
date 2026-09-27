#!/usr/bin/env python3
"""Local-only browser UI layered on top of universe_manager.py."""

import argparse
import contextlib
import copy
import difflib
import hashlib
import io
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import universe_manager as manager


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config" / "universe.json"


def safe_config_path(value):
    """CLI configuration is deliberately constrained to this repository config dir."""
    path = Path(value).resolve()
    config_dir = (ROOT / "config").resolve()
    if path.parent != config_dir or path.name != "universe.json":
        raise argparse.ArgumentTypeError("config path must be config/universe.json inside this repository")
    return path


def payload_hash(data):
    return hashlib.sha256(manager.serialized(data).encode("utf-8")).hexdigest()


def memberships(data):
    result = {stock["itemCode"]: {kind: [] for kind in manager.GROUP_KEYS} for stock in data["stocks"]}
    leaders = data.get("leaders", {})
    for kind, key in manager.GROUP_KEYS.items():
        for name, codes in data.get(key, {}).items():
            for code in codes:
                if code in result:
                    result[code][kind].append(name)
        for name, codes in leaders.get(kind, {}).items():
            for code in codes:
                if code in result:
                    result[code].setdefault("leaders", []).append(f"{kind}:{name}")
    return result


def action_args(operation):
    """Translate a UI operation into the canonical manager mutation arguments."""
    op = operation.get("op")
    code = operation.get("code")
    kind = operation.get("kind")
    name = operation.get("name")
    if op == "stock_add":
        return argparse.Namespace(command="stock", action="add", code=code, name=operation.get("stockName", ""))
    if op in ("stock_enable", "stock_disable"):
        return argparse.Namespace(command="stock", action=op.removeprefix("stock_"), code=code)
    if op == "stock_delete":
        return argparse.Namespace(command="stock", action="delete", code=code, hard=True)
    if op in ("group_create", "group_delete"):
        return argparse.Namespace(command="group", action=op.removeprefix("group_"), kind=kind, name=name)
    if op == "group_rename":
        return argparse.Namespace(command="group", action="rename", kind=kind, name=name, new_name=operation.get("newName", ""))
    if op in ("member_add", "member_remove"):
        return argparse.Namespace(command="group", action="member", kind=kind, name=name, member_action=op.removeprefix("member_"), code=code)
    if op in ("leader_set", "leader_remove"):
        return argparse.Namespace(command="leader", action=op.removeprefix("leader_"), kind=kind, name=name, code=code)
    raise ValueError("unknown operation")


def operation_summary(operation):
    op = operation.get("op", "unknown")
    code = operation.get("code", "")
    name = operation.get("name", "")
    if op == "stock_add": return f"+ stock {code} {operation.get('stockName', '')}"
    if op == "stock_disable": return f"disable {code}"
    if op == "stock_enable": return f"enable {code}"
    if op == "stock_delete": return f"hard delete {code}"
    if op.startswith("member_"): return f"{op.removeprefix('member_')} member {code} -> {operation.get('kind')}:{name}"
    if op.startswith("leader_"): return f"{op.removeprefix('leader_')} leader {code} -> {operation.get('kind')}:{name}"
    return f"{op} {operation.get('kind', '')}:{name}"


class UniverseWebApp:
    def __init__(self, config_path=DEFAULT_CONFIG):
        self.config_path = Path(config_path)

    def current(self):
        return manager.load(self.config_path)

    def dashboard(self):
        data = self.current()
        errors, warnings = manager.validation(data)
        stocks = data.get("stocks", []) if isinstance(data, dict) else []
        return {
            "enabledCount": sum(stock.get("enabled") is True for stock in stocks if isinstance(stock, dict)),
            "disabledCount": sum(stock.get("enabled") is False for stock in stocks if isinstance(stock, dict)),
            "sectorCount": len(data.get("sectors", {})),
            "themeCount": len(data.get("themes", {})),
            "watchlistCount": len(data.get("watchlists", {})),
            "validation": {"status": "ERROR" if errors else "WARNING" if warnings else "OK", "errors": errors, "warnings": warnings},
            "configPath": str(self.config_path),
            "modifiedAt": self.config_path.stat().st_mtime,
        }

    def stocks(self):
        data = self.current()
        member_map = memberships(data)
        return [
            {**stock, **member_map.get(stock["itemCode"], {"leaders": []})}
            for stock in data.get("stocks", [])
        ]

    def groups(self):
        data = self.current()
        names = {stock["itemCode"]: stock["stockName"] for stock in data.get("stocks", [])}
        result = {}
        for kind, key in manager.GROUP_KEYS.items():
            leader_map = data.get("leaders", {}).get(kind, {})
            result[kind] = [
                {"name": name, "members": [{"code": code, "name": names.get(code)} for code in codes], "leaders": leader_map.get(name, [])}
                for name, codes in data.get(key, {}).items()
            ]
        return result

    def preview(self, operation):
        before = self.current()
        after = copy.deepcopy(before)
        messages = io.StringIO()
        try:
            with contextlib.redirect_stdout(messages):
                manager.mutate(after, action_args(operation))
            errors, warnings = manager.validation(after)
        except (ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "errors": [str(exc)], "warnings": [], "summary": operation_summary(operation)}
        before_text, after_text = manager.serialized(before), manager.serialized(after)
        diff = "".join(difflib.unified_diff(before_text.splitlines(True), after_text.splitlines(True), fromfile="before/universe.json", tofile="after/universe.json"))
        return {
            "ok": not errors,
            "errors": errors,
            "warnings": warnings,
            "summary": operation_summary(operation),
            "messages": messages.getvalue().strip(),
            "diff": diff,
            "baselineHash": payload_hash(before),
            "changed": before_text != after_text,
        }

    def apply(self, operation, baseline_hash, confirm_hard_delete=False):
        if operation.get("op") == "stock_delete" and not confirm_hard_delete:
            return {"ok": False, "errors": ["hard delete requires explicit confirmation"]}
        before = self.current()
        if baseline_hash != payload_hash(before):
            return {"ok": False, "errors": ["configuration changed after preview; preview again"]}
        preview = self.preview(operation)
        if not preview["ok"]:
            return preview
        if not preview["changed"]:
            return {**preview, "applied": False}
        after = copy.deepcopy(before)
        manager.mutate(after, action_args(operation))
        messages = io.StringIO()
        with contextlib.redirect_stdout(messages):
            manager.save(self.config_path, before, after, dry_run=False)
        return {"ok": True, "applied": True, "summary": preview["summary"], "messages": messages.getvalue().strip()}

    def handle(self, method, path, body=None):
        try:
            route = urlparse(path).path
            if method == "GET" and route == "/api/dashboard": return HTTPStatus.OK, self.dashboard()
            if method == "GET" and route == "/api/stocks": return HTTPStatus.OK, {"stocks": self.stocks()}
            if method == "GET" and route == "/api/groups": return HTTPStatus.OK, {"groups": self.groups()}
            if method == "GET" and route == "/api/validation":
                errors, warnings = manager.validation(self.current())
                return HTTPStatus.OK, {"errors": errors, "warnings": warnings}
            if method == "POST" and route in ("/api/preview", "/api/apply"):
                request = body if isinstance(body, dict) else {}
                if route.endswith("preview"):
                    result = self.preview(request.get("operation", {}))
                    return (HTTPStatus.OK if result["ok"] else HTTPStatus.UNPROCESSABLE_ENTITY), result
                result = self.apply(request.get("operation", {}), request.get("baselineHash"), request.get("confirmHardDelete", False))
                return (HTTPStatus.OK if result["ok"] else HTTPStatus.UNPROCESSABLE_ENTITY), result
            return HTTPStatus.NOT_FOUND, {"errors": ["not found"]}
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            return HTTPStatus.UNPROCESSABLE_ENTITY, {"errors": [str(exc)]}


class Handler(BaseHTTPRequestHandler):
    app = None

    def send_json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if urlparse(self.path).path == "/":
            raw = HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        status, payload = self.app.handle("GET", self.path)
        self.send_json(status, payload)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            self.send_json(HTTPStatus.BAD_REQUEST, {"errors": ["invalid JSON request"]})
            return
        status, payload = self.app.handle("POST", self.path, body)
        self.send_json(status, payload)

    def log_message(self, *_):
        pass


def create_server(host, port, config_path=DEFAULT_CONFIG):
    Handler.app = UniverseWebApp(config_path)
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local-only Universe Manager Web UI")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1",), help="local bind address")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", type=safe_config_path, default=DEFAULT_CONFIG, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    server = create_server(args.host, args.port, args.config)
    print(f"Universe Web UI: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


HTML = r'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Universe Manager</title><style>
body{font:15px system-ui,sans-serif;max-width:1100px;margin:auto;padding:16px;color:#15202b}h1,h2{margin-top:28px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:9px}.card,section{border:1px solid #d8dee4;border-radius:8px;padding:12px}.card b{font-size:24px;display:block}table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid #e7eaed;padding:7px;vertical-align:top}button,input,select{padding:7px;margin:3px}pre{white-space:pre-wrap;background:#f6f8fa;padding:10px;overflow:auto}.warn{color:#9a6700}.error{color:#cf222e}.ok{color:#1a7f37}@media(max-width:650px){table{font-size:12px}th:nth-child(5),td:nth-child(5){display:none}}</style>
<h1>Universe Manager</h1><p>로컬 전용 관리 화면입니다. Discovery 후보는 자동 추가되지 않으며, disable이 기본 제거 방식입니다.</p><section id="dashboard"></section><section><h2>Stocks</h2><div id="stocks"></div></section><section><h2>Groups</h2><div id="groups"></div></section><section><h2>변경 Preview</h2><p>모든 변경은 먼저 Preview로 검증하고, 오류가 없을 때만 Apply합니다.</p><select id="op"><option value="stock_add">종목 추가</option><option value="stock_disable">종목 비활성화</option><option value="stock_enable">종목 활성화</option><option value="stock_delete">종목 hard delete</option><option value="group_create">그룹 생성</option><option value="group_delete">그룹 삭제</option><option value="group_rename">그룹 이름 변경</option><option value="member_add">그룹 구성원 추가</option><option value="member_remove">그룹 구성원 제거</option><option value="leader_set">leader 지정</option><option value="leader_remove">leader 해제</option></select><input id="code" placeholder="종목코드"><input id="stockName" placeholder="종목명"><select id="kind"><option>sector</option><option>theme</option><option>watchlist</option></select><input id="name" placeholder="그룹명"><input id="newName" placeholder="새 그룹명"><button onclick="preview()">Preview</button><label><input type="checkbox" id="confirm">hard delete 확인</label><button id="apply" disabled onclick="applyChange()">Apply</button><pre id="result"></pre></section>
<script>let pending=null;const $=id=>document.getElementById(id);async function api(url,body){let r=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});return await r.json()}function esc(s){return String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}async function load(){let d=await api('/api/dashboard');$('dashboard').innerHTML='<h2>Dashboard</h2><div class="cards">'+[['Enabled',d.enabledCount],['Disabled',d.disabledCount],['Sectors',d.sectorCount],['Themes',d.themeCount],['Watchlists',d.watchlistCount],['Validation',d.validation.status]].map(x=>'<div class="card"><small>'+x[0]+'</small><b>'+x[1]+'</b></div>').join('')+'<p>파일: '+esc(d.configPath)+'<br>수정시각: '+new Date(d.modifiedAt*1000).toLocaleString()+'</p>';let s=await api('/api/stocks');$('stocks').innerHTML='<table><tr><th>Code</th><th>Name</th><th>Status</th><th>Sector</th><th>Theme</th><th>Watchlist</th><th>Leader</th></tr>'+s.stocks.map(x=>'<tr><td>'+x.itemCode+'</td><td>'+esc(x.stockName)+'</td><td>'+ (x.enabled?'enabled':'disabled')+'</td><td>'+esc(x.sector.join(', '))+'</td><td>'+esc(x.theme.join(', '))+'</td><td>'+esc(x.watchlist.join(', '))+'</td><td>'+esc((x.leaders||[]).join(', '))+'</td></tr>').join('')+'</table>';let g=await api('/api/groups');$('groups').innerHTML=Object.entries(g.groups).map(([k,v])=>'<h3>'+k+'</h3>'+v.map(x=>'<div class="card"><b>'+esc(x.name)+'</b>members: '+esc(x.members.map(m=>m.code+' '+m.name).join(', '))+'<br>leaders: '+esc(x.leaders.join(', '))+'</div>').join('')).join('')}function operation(){return{op:$('op').value,code:$('code').value,stockName:$('stockName').value,kind:$('kind').value,name:$('name').value,newName:$('newName').value}}async function preview(){pending=await api('/api/preview',{operation:operation()});$('result').textContent=(pending.errors||[]).join('\n')+(pending.warnings||[]).join('\n')+'\n'+(pending.summary||'')+'\n'+(pending.diff||'');$('apply').disabled=!pending.ok||!pending.changed}async function applyChange(){let r=await api('/api/apply',{operation:operation(),baselineHash:pending.baselineHash,confirmHardDelete:$('confirm').checked});$('result').textContent=JSON.stringify(r,null,2);if(r.ok)load()}load()</script>'''


if __name__ == "__main__":
    main()
