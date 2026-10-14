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
from urllib.parse import parse_qs

import universe_manager as manager
import dynamic_universe_manager as dynamic


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config" / "universe.json"
DEFAULT_DYNAMIC_RESULT = ROOT / "data" / "dynamic-universe" / "latest.json"
DEFAULT_DYNAMIC_HISTORY = ROOT / "data" / "dynamic-universe" / "history.json"
DEFAULT_POLICY = ROOT / "config" / "dynamic_universe_policy.json"
DEFAULT_ASSET_REGISTRY = ROOT / "data" / "market-universe" / "all-assets.json"


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
    def __init__(self, config_path=DEFAULT_CONFIG, dynamic_result=DEFAULT_DYNAMIC_RESULT,
                 dynamic_history=DEFAULT_DYNAMIC_HISTORY, policy_path=DEFAULT_POLICY,
                 asset_registry=DEFAULT_ASSET_REGISTRY):
        self.config_path = Path(config_path)
        self.dynamic_result = Path(dynamic_result)
        self.dynamic_history = Path(dynamic_history)
        self.policy_path = Path(policy_path)
        self.asset_registry = Path(asset_registry)

    def current(self):
        return manager.load(self.config_path)

    def dashboard(self):
        data = self.current()
        errors, warnings = manager.validation(data)
        stocks = data.get("stocks", []) if isinstance(data, dict) else []
        auto = self.auto_management()
        return {
            "enabledCount": sum(stock.get("enabled") is True for stock in stocks if isinstance(stock, dict)),
            "disabledCount": sum(stock.get("enabled") is False for stock in stocks if isinstance(stock, dict)),
            "sectorCount": len(data.get("sectors", {})),
            "themeCount": len(data.get("themes", {})),
            "watchlistCount": len(data.get("watchlists", {})),
            "validation": {"status": "ERROR" if errors else "WARNING" if warnings else "OK", "errors": errors, "warnings": warnings},
            "configPath": str(self.config_path),
            "modifiedAt": self.config_path.stat().st_mtime,
            "autoSectorCount": sum(row.get("status") in {"ACTIVE", "WATCH", "REENTERED"} for row in auto["sectors"]),
            "autoThemeCount": sum(row.get("status") in {"ACTIVE", "WATCH", "REENTERED"} for row in auto["themes"]),
            "autoLeaderCount": sum(row.get("status") in {"ACTIVE", "WATCH", "REENTERED"} for row in auto["leaders"]),
            "themeStatus": "PENDING" if auto["pending"] else "AVAILABLE",
        }

    def auto_management(self):
        payload = dynamic.load_json(self.dynamic_result, {}) or {}
        return {"version": payload.get("version", "dynamic-universe-management-v1"),
                "generatedAt": payload.get("generatedAt"), "sectors": payload.get("sectors", []),
                "themes": payload.get("themes", []), "leaders": payload.get("leaders", []),
                "initialObservationCandidates": payload.get("initialObservationCandidates", []),
                "kiwoomDiscovery": payload.get("kiwoomDiscovery", []),
                "pending": payload.get("pending", [{"type": "AUTO_THEME", "status": "PENDING", "reason": "DYNAMIC_RESULT_NOT_PUBLISHED"}])}

    def watchlists(self):
        data = self.current(); names = {row["itemCode"]: row["stockName"] for row in data.get("stocks", [])}
        return {name: [{"code": code, "name": names.get(code)} for code in codes]
                for name, codes in manager.user_watchlists(data).items()}

    def history(self):
        payload = dynamic.load_json(self.dynamic_history, {"events": []}) or {"events": []}
        backups = sorted(self.config_path.parent.glob("history/universe-*.json"), reverse=True)
        return {"automatic": payload.get("events", []),
                "userBackups": [{"file": path.name, "modifiedAt": path.stat().st_mtime} for path in backups[:100]]}

    def settings(self):
        return dynamic.load_json(self.policy_path, {}) or {}

    def search(self, query):
        query = str(query or "").strip().lower()
        if not query: return []
        registry = dynamic.load_json(self.asset_registry, {}) or {}
        rows = registry if isinstance(registry, list) else registry.get("stocks") or registry.get("assets") or registry.get("items") or []
        results = []
        for row in rows:
            code = str(row.get("code") or row.get("itemCode") or "")
            name = str(row.get("name") or row.get("stockName") or "")
            if query in code.lower() or query in name.lower():
                results.append({"code": code, "name": name, "market": row.get("market")})
            if len(results) >= 20: break
        return results

    def _watchlist_mutation(self, data, operation):
        mapping = {"watchlist_add": "add", "watchlist_remove": "remove",
                   "watchlist_group_create": "group_create", "watchlist_group_delete": "group_delete"}
        action = mapping.get(operation.get("op"))
        if not action: raise ValueError("지원하지 않는 관심종목 작업")
        manager.mutate_user_watchlist(data, action, operation.get("name", ""), operation.get("code"), operation.get("stockName"))

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
                if str(operation.get("op", "")).startswith("watchlist_"):
                    self._watchlist_mutation(after, operation)
                else:
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
        if str(operation.get("op", "")).startswith("watchlist_"):
            self._watchlist_mutation(after, operation)
        else:
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
            if method == "GET" and route == "/api/automatic": return HTTPStatus.OK, self.auto_management()
            if method == "GET" and route == "/api/user-watchlists": return HTTPStatus.OK, {"groups": self.watchlists()}
            if method == "GET" and route == "/api/history": return HTTPStatus.OK, self.history()
            if method == "GET" and route == "/api/settings": return HTTPStatus.OK, self.settings()
            if method == "GET" and route == "/api/search/stocks":
                return HTTPStatus.OK, {"results": self.search((parse_qs(urlparse(path).query).get("q") or [""])[0])}
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
body{font:15px system-ui,sans-serif;max-width:1120px;margin:auto;padding:16px;color:#17202a;background:#f7f8fa}nav{display:flex;gap:6px;flex-wrap:wrap;position:sticky;top:0;background:#f7f8fa;padding:8px 0}.view{display:none}.view.active{display:block}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.card,section{background:white;border:1px solid #d8dee4;border-radius:10px;padding:13px;margin:9px 0}.metric b{font-size:26px;display:block}button,input,select{padding:9px;margin:3px;border:1px solid #afb8c1;border-radius:7px}button{cursor:pointer}.muted{color:#667085}.badge{display:inline-block;padding:2px 7px;border-radius:10px;background:#eaeef2;font-size:12px}.ACTIVE,.REENTERED{background:#dafbe1}.WATCH,.PENDING,.DISCOVERED{background:#fff8c5}.REMOVED{background:#ffebe9}table{width:100%;border-collapse:collapse}th,td{text-align:left;border-bottom:1px solid #e7eaed;padding:7px}pre{white-space:pre-wrap;background:#f6f8fa;padding:10px;overflow:auto}@media(max-width:650px){table{font-size:12px}}</style>
<h1>Universe Manager</h1><p class="muted">전체시장 분석과 관리목록은 분리됩니다. 자동 데이터는 읽기 전용이고 사용자 관심종목만 직접 편집됩니다.</p>
<nav><button data-tab="auto">자동 관리</button><button data-tab="watch">사용자 관심종목</button><button data-tab="history">변경 이력</button><button data-tab="settings">관리 설정</button></nav>
<main><section id="auto" class="view active"><div id="dashboard"></div><h2>동적 섹터</h2><div id="sectors"></div><h2>동적 테마</h2><div id="themes"></div><h2>정식 주도주</h2><div id="leaders"></div><h2>초기 관찰 후보</h2><div id="initialObservationCandidates"></div><h2>키움 발굴종목</h2><div id="kiwoom"></div></section>
<section id="watch" class="view"><h2>사용자 관심종목</h2><input id="query" placeholder="종목명 또는 코드"><button onclick="searchStocks()">검색</button><div id="search"></div><div id="watchlists"></div><h3>변경 미리보기</h3><select id="op"><option value="watchlist_add">추가</option><option value="watchlist_remove">삭제</option><option value="watchlist_group_create">그룹 생성</option><option value="watchlist_group_delete">그룹 삭제</option></select><input id="code" placeholder="종목코드"><input id="stockName" placeholder="종목명"><input id="name" placeholder="관심 그룹"><button onclick="preview()">미리보기</button><button id="apply" disabled onclick="applyChange()">저장</button><pre id="result"></pre></section>
<section id="history" class="view"><h2>변경 이력</h2><div id="events"></div></section><section id="settings" class="view"><h2>관리 설정</h2><pre id="policy"></pre></section></main>
<script>let pending=null;const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));async function api(url,body){let r=await fetch(url,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{}),x=await r.json();if(!r.ok)throw x;return x}const status=s=>'<span class="badge '+esc(s)+'">'+esc(s)+'</span>';function rows(items,extra=''){return(items||[]).map(x=>'<div class="card"><b>'+esc(x.name||x.code||x.id)+'</b> '+status(x.status||'')+' <span class="muted">점수 '+esc(x.score??'-')+'</span>'+(x.currentPrice!=null?'<span class="muted"> · 현재가 '+esc(x.currentPrice)+' · 등락률 '+esc(x.changeRate??'-')+'% · 시세 기준 '+esc(x.sourceTime||'확인 불가')+'</span>':'')+extra.replaceAll('{code}',esc(x.code||'')).replaceAll('{name}',esc(x.name||''))+'</div>').join('')||'<p class="muted">현재 항목 없음</p>'}async function load(){let[d,a,w,h,p]=await Promise.all(['/api/dashboard','/api/automatic','/api/user-watchlists','/api/history','/api/settings'].map(api));$('dashboard').innerHTML='<div class="cards">'+[['자동 섹터',d.autoSectorCount],['자동 테마',d.autoThemeCount],['주도주',d.autoLeaderCount],['테마 상태',d.themeStatus],['설정 검증',d.validation.status]].map(x=>'<div class="card metric"><span class="muted">'+x[0]+'</span><b>'+x[1]+'</b></div>').join('')+'</div>';$('sectors').innerHTML=rows(a.sectors);$('themes').innerHTML=rows(a.themes)+rows(a.pending);$('leaders').innerHTML=rows(a.leaders);$('initialObservationCandidates').innerHTML=rows(a.initialObservationCandidates);$('kiwoom').innerHTML=rows(a.kiwoomDiscovery);$('watchlists').innerHTML=Object.entries(w.groups).map(([n,v])=>'<div class="card"><b>'+esc(n)+'</b><p>'+v.map(x=>'<span class="badge">'+esc(x.code)+' '+esc(x.name)+'</span>').join(' ')+'</p></div>').join('');$('events').innerHTML=rows(h.automatic)+ '<h3>사용자 변경 백업</h3>'+rows(h.userBackups);$('policy').textContent=JSON.stringify(p,null,2)}document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));$(b.dataset.tab).classList.add('active')});function operation(){return{op:$(\'op\').value,code:$(\'code\').value,stockName:$(\'stockName\').value,name:$(\'name\').value}}async function preview(){try{pending=await api('/api/preview',{operation:operation()});$(\'result\').textContent=(pending.summary||'')+'\\n'+(pending.diff||'');$(\'apply\').disabled=!pending.ok||!pending.changed}catch(e){$(\'result\').textContent=(e.errors||['미리보기 실패']).join('\\n')}}async function applyChange(){let r=await api('/api/apply',{operation:operation(),baselineHash:pending.baselineHash});$(\'result\').textContent=JSON.stringify(r,null,2);if(r.ok)load()}async function searchStocks(){let x=await api('/api/search/stocks?q='+encodeURIComponent($(\'query\').value));$(\'search\').innerHTML=rows(x.results,'<button onclick="choose(\'{code}\',\'{name}\')">선택</button>')}function choose(c,n){$(\'code\').value=c;$(\'stockName\').value=n}load()</script>'''


if __name__ == "__main__":
    main()
