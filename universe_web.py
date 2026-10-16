#!/usr/bin/env python3
"""Local UI; every Universe mutation delegates to universe_manager."""
import argparse, contextlib, copy, difflib, hashlib, io, json, os, secrets, time, threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import stock_search
import config_sync
import universe_manager as manager
from universe_auth import AuthError, WebAuthnService
import dynamic_universe_manager as dynamic
import dynamic_universe_background_sync as background_sync
import dynamic_universe_sync as sync_contract

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config" / "universe.json"
DEFAULT_DYNAMIC_RESULT = ROOT / "data" / "dynamic-universe" / "latest.json"
DEFAULT_DYNAMIC_HISTORY = ROOT / "data" / "dynamic-universe" / "history.json"
_sync_lock = threading.Lock()
def _saved_sync_state():
    try:
        value = json.loads((ROOT / "data" / "dynamic-universe" / ".sync-state.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


_last_sync = _saved_sync_state()
_sync_state = {"status": "NOT_STARTED", "result": None,
               "lastAppliedCommit": _last_sync.get("commit"),
               "lastAppliedGeneratedAt": _last_sync.get("generatedAt"),
               "lastAppliedSha256": _last_sync.get("latestSha256")}

def tailscale_ipv4(runner=None):
    """Legacy helper retained for diagnostics; Serve is the recommended path."""
    import subprocess
    try:
        result = (runner or subprocess.run)(["tailscale", "ip", "-4"], text=True, capture_output=True, check=True)
        return result.stdout.strip().splitlines()[0]
    except (OSError, subprocess.SubprocessError, IndexError): return None

def safe_config_path(value):
    path = Path(value).resolve()
    if path.parent != (ROOT / "config").resolve() or path.name != "universe.json":
        raise argparse.ArgumentTypeError("config path must be config/universe.json inside this repository")
    return path

def payload_hash(data): return hashlib.sha256(manager.serialized(data).encode()).hexdigest()

def memberships(data):
    result={s["itemCode"]:{k:[] for k in manager.GROUP_KEYS} for s in data["stocks"]}
    for kind,key in manager.GROUP_KEYS.items():
        for name,codes in data.get(key,{}).items():
            if kind == "watchlist" and name == "legacy33":
                continue
            for code in codes:
                if code in result: result[code][kind].append(name)
    return result

def action_args(op):
    name,code,kind=op.get("name"),op.get("code"),op.get("kind")
    if op["op"] == "stock_add": return argparse.Namespace(command="stock",action="add",code=code,name=op.get("stockName", ""))
    if op["op"] in ("stock_enable","stock_disable"): return argparse.Namespace(command="stock",action=op["op"][6:],code=code)
    if op["op"] == "stock_delete": return argparse.Namespace(command="stock",action="delete",code=code,hard=True)
    if op["op"] in ("group_create","group_delete"): return argparse.Namespace(command="group",action=op["op"][6:],kind=kind,name=name)
    if op["op"] == "group_rename": return argparse.Namespace(command="group",action="rename",kind=kind,name=name,new_name=op.get("newName", ""))
    if op["op"] in ("member_add","member_remove"): return argparse.Namespace(command="group",action="member",kind=kind,name=name,member_action=op["op"][7:],code=code)
    if op["op"] in ("leader_set","leader_remove"): return argparse.Namespace(command="leader",action=op["op"][7:],kind=kind,name=name,code=code)
    raise ValueError("unknown operation")

def mutate_operation(data,op):
    if op.get("op")=="stock_add_with_groups":
        manager.mutate(data,action_args({"op":"stock_add","code":op.get("code"),"stockName":op.get("stockName","")}))
        for kind,names in op.get("groups",{}).items():
            for name in names: manager.mutate(data,action_args({"op":"member_add","kind":kind,"name":name,"code":op.get("code")}))
    else: manager.mutate(data,action_args(op))

def operation_summary(op): return {"stock_add":"신규 종목 추가","stock_add_with_groups":"신규 종목 추가","stock_enable":"종목 활성화","stock_disable":"종목 비활성화","stock_delete":"종목 완전 삭제","group_create":"그룹 생성","group_delete":"그룹 삭제","group_rename":"그룹 이름 변경","member_add":"구성종목 추가","member_remove":"구성종목 제거","leader_set":"주도주 지정","leader_remove":"주도주 해제"}.get(op.get("op"),"변경")

def preview_summary(before, after, operation):
    """Describe the same snapshots used for the raw diff, keyed only by itemCode."""
    before_codes = {stock.get("itemCode") for stock in before.get("stocks", [])}
    added = [stock for stock in after.get("stocks", []) if stock.get("itemCode") not in before_codes]
    if added:
        group_lines = []
        labels = {"sector": "섹터", "theme": "테마", "watchlist": "관심종목"}
        for kind, label in labels.items():
            names = operation.get("groups", {}).get(kind, [])
            group_lines.append(f"{label}: {', '.join(names) if names else '지정 안 함'}")
        return "\n".join([*(f"신규 종목: {stock.get('stockName', '')} {stock['itemCode']}" for stock in added), *group_lines])
    return operation_summary(operation)

class UniverseWebApp:
    def __init__(self,config_path=DEFAULT_CONFIG): self.config_path=Path(config_path)
    def current(self): return manager.load(self.config_path)
    def dynamic_payload(self):
        payload = dynamic.load_json(DEFAULT_DYNAMIC_RESULT, {}) or {}
        industries = dynamic.load_json(ROOT / "data" / "monitoring" / "latest-industries.json", {}) or {}
        themes = dynamic.load_json(ROOT / "data" / "monitoring" / "latest-theme-signals.json", {}) or {}
        industry_by_id = {str(v.get("id")): v for v in industries.get("industries", []) if isinstance(v, dict)}
        industry_by_name = {str(v.get("name")): v for v in industries.get("industries", []) if isinstance(v, dict)}
        theme_by_id = {str(v.get("id")): v for v in themes.get("themes", []) if isinstance(v, dict)}
        sectors = []
        for item in payload.get("sectors", []):
            if not isinstance(item, dict):
                continue
            evidence = industry_by_id.get(str(item.get("id"))) or industry_by_name.get(str(item.get("name")))
            merged = dict(item)
            if evidence:
                merged["metrics"] = evidence
                merged["leaders"] = evidence.get("leaders", [])
                merged["evidenceGeneratedAt"] = industries.get("generatedAt")
            sectors.append(merged)
        selected_themes = []
        for item in payload.get("themes", []):
            if not isinstance(item, dict):
                continue
            evidence = theme_by_id.get(str(item.get("id")))
            merged = dict(item)
            if evidence:
                merged["metrics"] = evidence.get("metrics", {})
                merged["stocks"] = evidence.get("stocks", [])
                merged["evidenceGeneratedAt"] = themes.get("generatedAt")
            selected_themes.append(merged)
        name_by_code = {}
        try:
            assets = dynamic.load_json(ROOT / "data" / "market-universe" / "all-assets.json", {}).get("assets", [])
            name_by_code.update({str(v.get("code")): v.get("name") for v in assets if v.get("code") and v.get("name")})
        except (AttributeError, TypeError):
            pass
        kiwoom = []
        for item in payload.get("kiwoomDiscovery", []):
            if isinstance(item, dict) and item.get("code"):
                item = dict(item)
                item["name"] = item.get("name") or name_by_code.get(str(item["code"]))
                item["nameSource"] = "NAVER_MARKET_UNIVERSE" if item.get("name") else None
            kiwoom.append(item)
        return {**payload, "sectors": sectors, "themes": selected_themes,
                "kiwoomDiscovery": kiwoom,
                "analysisGeneratedAt": {"industries": industries.get("generatedAt"), "themes": themes.get("generatedAt")}}
    def dynamic_history(self): return dynamic.load_json(DEFAULT_DYNAMIC_HISTORY, {"events": []}) or {"events": []}
    def dashboard(self):
        data=self.current(); errors,warnings=manager.validation(data); stocks=data.get("stocks",[])
        return {"enabledCount":sum(x.get("enabled") is True for x in stocks),"disabledCount":sum(x.get("enabled") is False for x in stocks),"sectorCount":len(data.get("sectors",{})),"themeCount":len(data.get("themes",{})),"watchlistCount":len(data.get("watchlists",{})),"validation":{"status":"ERROR" if errors else "WARNING" if warnings else "OK","errors":errors,"warnings":warnings},"configPath":str(self.config_path),"modifiedAt":self.config_path.stat().st_mtime}
    def stocks(self):
        data=self.current(); ms=memberships(data); markets={r["code"]:r.get("market") for r in stock_search.local_stocks(self.config_path)}
        return [{**s,"market":markets.get(s["itemCode"]),**ms.get(s["itemCode"],{"leaders":[]})} for s in data.get("stocks",[])]
    def groups(self):
        data=self.current(); names={s["itemCode"]:s["stockName"] for s in data.get("stocks",[])}; out={}
        for kind,key in manager.GROUP_KEYS.items(): out[kind]=[{"name":n,"members":[{"code":c,"name":names.get(c)} for c in codes],"leaders":[]} for n,codes in data.get(key,{}).items() if not (kind == "watchlist" and n == "legacy33")]
        return out
    def search(self,q): return stock_search.search_stocks(q,universe_path=self.config_path)
    def preview(self,op):
        before=self.current(); after=copy.deepcopy(before); output=io.StringIO()
        try:
            with contextlib.redirect_stdout(output): mutate_operation(after,op)
            errors,warnings=manager.validation(after)
        except (ValueError,KeyError,TypeError) as exc: return {"ok":False,"errors":[str(exc)],"warnings":[],"summary":operation_summary(op)}
        a,b=manager.serialized(before),manager.serialized(after)
        return {"ok":not errors,"errors":errors,"warnings":warnings,"summary":preview_summary(before,after,op),"messages":output.getvalue().strip(),"diff":"".join(difflib.unified_diff(a.splitlines(True),b.splitlines(True),fromfile="before/universe.json",tofile="after/universe.json")),"baselineHash":payload_hash(before),"changed":a!=b}
    def apply(self,op,baseline,confirm_hard_delete=False):
        if op.get("op")=="stock_delete" and not confirm_hard_delete: return {"ok":False,"errors":["hard delete requires explicit confirmation"]}
        before=self.current()
        if baseline != payload_hash(before): return {"ok":False,"errors":["configuration changed after preview; preview again"]}
        preview=self.preview(op)
        if not preview["ok"] or not preview["changed"]: return {**preview,"applied":False}
        after=copy.deepcopy(before); mutate_operation(after,op); manager.save(self.config_path,before,after,dry_run=False)
        return {"ok":True,"applied":True,"summary":preview["summary"]}
    def handle(self,method,path,body=None):
        try:
            route=urlparse(path).path
            if method=="GET" and route=="/healthz": return HTTPStatus.OK,{"status":"ok"}
            if method=="GET" and route=="/api/dashboard": return HTTPStatus.OK,self.dashboard()
            if method=="GET" and route=="/api/automatic":
                p=self.dynamic_payload(); sync=dict(_sync_state); return HTTPStatus.OK,{"generatedAt":p.get("generatedAt"),"commit":sync.get("lastAppliedCommit") or p.get("commit"),"sectors":p.get("sectors",[]),"themes":p.get("themes",[]),"leaders":p.get("leaders",[]),"initialObservationCandidates":p.get("initialObservationCandidates",[]),"kiwoomDiscovery":p.get("kiwoomDiscovery",[]),"pending":p.get("pending",[]),"industryEvidence":p.get("industryEvidence"),"sync":sync}
            if method=="GET" and route=="/api/history": return HTTPStatus.OK,self.dynamic_history()
            if method=="GET" and route=="/api/settings": return HTTPStatus.OK,dynamic.load_json(ROOT/"config"/"dynamic_universe_policy.json",{}) or {}
            if method=="GET" and route=="/api/dynamic-sync/status": return HTTPStatus.OK,dict(_sync_state)
            if method=="GET" and route=="/api/config-sync/status": return HTTPStatus.OK,config_sync.status(ROOT)
            if method=="GET" and route=="/api/config-sync/preview": return HTTPStatus.OK,config_sync.preview(ROOT)
            if method=="GET" and route=="/api/stocks": return HTTPStatus.OK,{"stocks":self.stocks()}
            if method=="GET" and route=="/api/groups": return HTTPStatus.OK,{"groups":self.groups()}
            if method=="GET" and route=="/api/search/stocks": return HTTPStatus.OK,self.search(parse_qs(urlparse(path).query).get("q",[""])[0])
            if method=="GET" and route in ("/api/validate","/api/validation"):
                e,w=manager.validation(self.current()); return HTTPStatus.OK,{"errors":e,"warnings":w}
            if method=="POST" and route in ("/api/preview","/api/apply"):
                request=body or {}; result=self.preview(request.get("operation",{})) if route.endswith("preview") else self.apply(request.get("operation",{}),request.get("baselineHash"),request.get("confirmHardDelete",False)); return (HTTPStatus.OK if result["ok"] else HTTPStatus.UNPROCESSABLE_ENTITY),result
            return HTTPStatus.NOT_FOUND,{"errors":["not found"]}
        except (ValueError,OSError,json.JSONDecodeError) as exc: return HTTPStatus.UNPROCESSABLE_ENTITY,{"errors":[str(exc)]}

class SessionAuth:
    SESSION_SECONDS=28800
    def __init__(self,pin=None,required=False,now=time.time): self.pin=pin; self.required=required; self.now=now; self.sessions={}
    def issue(self):
        t=secrets.token_urlsafe(32); self.sessions[t]={"csrf":secrets.token_urlsafe(24),"expires":self.now()+self.SESSION_SECONDS}; return t
    def login(self,pin): return self.issue() if self.required and self.pin and secrets.compare_digest(str(pin or ""),self.pin) else None
    def record(self,t):
        x=self.sessions.get(t or "")
        if x and x["expires"]>self.now(): return x
        self.logout(t); return None
    def allowed(self,t): return not self.required or bool(self.record(t))
    def csrf(self,t):
        x=self.record(t); return x["csrf"] if x else None
    def csrf_valid(self,t,v): return bool(self.csrf(t) and v and secrets.compare_digest(self.csrf(t),str(v)))
    def logout(self,t): self.sessions.pop(t or "",None)

def sync_after_login():
    """Start one non-blocking pull per process at the successful login boundary."""
    global _sync_state
    with _sync_lock:
        if _sync_state.get("status") == "RUNNING": return
        _sync_state = {"status": "RUNNING", "result": None,
                       "lastAppliedCommit": _sync_state.get("lastAppliedCommit"),
                       "lastAppliedGeneratedAt": _sync_state.get("lastAppliedGeneratedAt"),
                       "lastAppliedSha256": _sync_state.get("lastAppliedSha256")}
    def worker():
        global _sync_state
        try:
            result = background_sync.run_result(["--root", str(ROOT), "--ref", "main"])
            saved = _saved_sync_state()
            _sync_state = {"status": result.get("status", "COMPLETED"), "result": result,
                           "lastAppliedCommit": result.get("commit") or saved.get("commit") or _last_sync.get("commit"),
                           "lastAppliedGeneratedAt": result.get("generatedAt") or saved.get("generatedAt") or _last_sync.get("generatedAt"),
                           "lastAppliedSha256": saved.get("latestSha256") or _last_sync.get("latestSha256")}
        except Exception as exc:
            safe_error = str(exc) if isinstance(exc, sync_contract.SyncError) else type(exc).__name__
            _sync_state = {"status": "FAILED", "result": {"error": safe_error, "errorType": type(exc).__name__},
                           "lastAppliedCommit": _last_sync.get("commit"),
                           "lastAppliedGeneratedAt": _last_sync.get("generatedAt"),
                           "lastAppliedSha256": _last_sync.get("latestSha256")}
    threading.Thread(target=worker, name="dynamic-universe-login-sync", daemon=True).start()

def login_page(has_credential, recovery=False):
    if recovery:
        return LOGIN_HTML
    start = LOGIN_HTML.index('<details>')
    end = LOGIN_HTML.index('</details>', start) + len('</details>')
    page = LOGIN_HTML[:start] + LOGIN_HTML[end:]
    page = page.replace('<button onclick="login()">Face ID로 로그인</button>', '<div id="authActions"></div>')
    page = page.replace('<p id=e>등록된 Passkey가 없거나 이 주소가 HTTPS가 아니면 Face ID 로그인을 사용할 수 없습니다.</p>', '<p id=e></p>')
    script = r'''<script>(async()=>{
const hasCredential=__HAS_CREDENTIAL__,key='universePasskeyFailureCount',actions=document.querySelector('#authActions'),error=document.querySelector('#e');
let failures=Number(sessionStorage.getItem(key)||0),attempted=false;
function render(){actions.replaceChildren();if(!hasCredential){let recovery=document.createElement('a');recovery.href='/?recovery=1';recovery.textContent='로그인 복구';actions.append(recovery);return}let retry=document.createElement('button');retry.textContent='다시 로그인';retry.onclick=attempt;actions.append(retry);if(failures>=3){let recovery=document.createElement('a');recovery.href='/?recovery=1';recovery.textContent='로그인 복구';recovery.style.marginLeft='12px';actions.append(recovery)}}
async function attempt(){if(attempted)return;attempted=true;error.textContent='로그인 중…';try{let x=await post('/api/auth/passkey/options',{}),c=await navigator.credentials.get({publicKey:prep(x.publicKey)});await post('/api/auth/passkey/verify',{ceremonyId:x.ceremonyId,credential:cred(c)});sessionStorage.removeItem(key);location='/'}catch(x){failures+=1;sessionStorage.setItem(key,String(failures));attempted=false;error.textContent='로그인에 실패했습니다.';render()}}
if(!hasCredential){render()}else if(failures===0){attempt()}else{render()}
})()</script>'''.replace('__HAS_CREDENTIAL__', json.dumps(bool(has_credential)))
    return page + script

class Handler(BaseHTTPRequestHandler):
    app=None; auth=SessionAuth(); webauthn=WebAuthnService(); secure_cookies=False; bootstrap={}
    def session(self):
        for x in self.headers.get("Cookie","").split(";"):
            k,_,v=x.strip().partition("=")
            if k=="universe_session": return v
        return None
    def send_json(self,status,payload,cookies=()):
        raw=json.dumps(payload,ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.send_header("Cache-Control","no-store, max-age=0"); self.send_header("Pragma","no-cache")
        for c in cookies:self.send_header("Set-Cookie",c)
        self.end_headers(); self.wfile.write(raw)
    def send_html(self,text):
        raw=text.encode(); self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Content-Length",str(len(raw)));self.send_header("Cache-Control","no-store, max-age=0");self.send_header("Pragma","no-cache");self.end_headers();self.wfile.write(raw)
    def cookie_policy(self, http_only=False):
        secure="; Secure" if self.secure_cookies else ""
        return f"; Path=/; SameSite=Strict{secure}" + ("; HttpOnly" if http_only else "")
    def cookies(self,t):
        c=self.auth.csrf(t)
        return [f"universe_session={t}; Max-Age=28800"+self.cookie_policy(http_only=True),f"universe_csrf={c}; Max-Age=28800"+self.cookie_policy()]
    def expired_cookies(self):
        expired="; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
        return ["universe_session="+expired+self.cookie_policy(http_only=True),"universe_csrf="+expired+self.cookie_policy()]
    def do_GET(self):
        route=urlparse(self.path).path
        if route=="/healthz":self.send_json(200,{"status":"ok"});return
        if route=="/help":self.send_html(HELP_HTML);return
        if route=="/api/auth/status":
            x=self.webauthn.status();x.update({"authenticated":self.auth.allowed(self.session()),"csrfToken":self.auth.csrf(self.session())});self.send_json(200,x);return
        if route in ("/api/config-sync/status","/api/config-sync/preview") and not self.auth.record(self.session()):
            self.send_json(401,{"errors":["설정 GitHub 상태 확인에는 로그인이 필요합니다"]});return
        if route=="/":
            if self.auth.allowed(self.session()):
                self.send_html(HTML + ADD_UI_JS)
            else:
                recovery = parse_qs(urlparse(self.path).query).get("recovery", [""])[0] == "1"
                self.send_html(login_page(self.webauthn.status().get("hasCredential", False), recovery))
            return
        if not self.auth.allowed(self.session()):self.send_json(401,{"errors":["로그인이 필요합니다"]});return
        s,p=self.app.handle("GET",self.path);self.send_json(s,p)
    def do_POST(self):
        try:body=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))).decode())
        except (ValueError,json.JSONDecodeError):self.send_json(400,{"errors":["잘못된 요청입니다"]});return
        route=urlparse(self.path).path
        if route=="/api/login":
            t=self.auth.login(body.get("pin"))
            if not t:self.send_json(401,{"errors":["PIN 코드가 올바르지 않습니다"]});return
            self.send_json(200,{"ok":True},self.cookies(t));sync_after_login();return
        if route=="/api/logout":
            token=self.session()
            if self.auth.record(token):
                if not self.auth.csrf_valid(token,self.headers.get("X-CSRF-Token")):
                    self.send_json(403,{"errors":["보안 확인이 만료되었습니다. 다시 로그인하세요."]});return
                self.auth.logout(token)
                self.send_json(200,{"ok":True},self.expired_cookies());return
            # A repeated logout has no server-side session to alter, but still
            # expires any stale browser cookies so logout remains idempotent.
            self.send_json(200,{"ok":True,"alreadyLoggedOut":True},self.expired_cookies());return
        if route=="/api/auth/register/options":
            if not self.auth.pin or not secrets.compare_digest(str(body.get("pin") or ""),self.auth.pin):self.send_json(401,{"errors":["최초 등록 또는 복구에는 관리자 PIN 승인이 필요합니다."]});return
            try:x=self.webauthn.registration_options();b=secrets.token_urlsafe(24);self.bootstrap[b]=time.time()+300;x["bootstrapId"]=b;self.send_json(200,x)
            except AuthError as e:self.send_json(422,{"errors":[str(e)]})
            return
        if route=="/api/auth/register/verify":
            if self.bootstrap.pop(body.get("bootstrapId"),0)<time.time():self.send_json(401,{"errors":["관리자 등록 승인이 만료되었습니다."]});return
            try:self.send_json(200,self.webauthn.finish_registration(body.get("ceremonyId"),body.get("credential")))
            except AuthError as e:self.send_json(422,{"errors":[str(e)]})
            return
        if route=="/api/auth/passkey/options":
            try:self.send_json(200,self.webauthn.authentication_options())
            except AuthError as e:self.send_json(422,{"errors":[str(e)]})
            return
        if route=="/api/auth/passkey/verify":
            try:self.webauthn.finish_authentication(body.get("ceremonyId"),body.get("credential"));t=self.auth.issue();self.send_json(200,{"ok":True},self.cookies(t));sync_after_login()
            except AuthError as e:self.send_json(401,{"errors":[str(e)]})
            return
        if not self.auth.allowed(self.session()):self.send_json(401,{"errors":["로그인이 필요합니다"]});return
        if route=="/api/config-sync/push":
            if not self.auth.record(self.session()):self.send_json(401,{"errors":["설정 GitHub 저장에는 로그인이 필요합니다"]});return
            if not self.auth.csrf_valid(self.session(),self.headers.get("X-CSRF-Token")):self.send_json(403,{"errors":["보안 확인이 만료되었습니다. 다시 로그인하세요."]});return
            try:self.send_json(200,config_sync.push(ROOT))
            except ValueError as e:self.send_json(422,{"errors":[str(e)]})
            return
        if route in ("/api/preview","/api/apply") and self.auth.required and not self.auth.csrf_valid(self.session(),self.headers.get("X-CSRF-Token")):self.send_json(403,{"errors":["보안 확인이 만료되었습니다. 다시 로그인하세요."]});return
        s,p=self.app.handle("POST",self.path,body);self.send_json(s,p)
    def log_message(self,*_):pass

def create_server(host,port,config_path=DEFAULT_CONFIG,pin=None,require_auth=False,webauthn=None):
    Handler.app=UniverseWebApp(config_path);Handler.webauthn=webauthn or WebAuthnService();Handler.auth=SessionAuth(pin,require_auth or host!="127.0.0.1" or Handler.webauthn.configured);Handler.secure_cookies=Handler.webauthn.configured;Handler.bootstrap={};return ThreadingHTTPServer((host,port),Handler)

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument("--host",default="127.0.0.1");p.add_argument("--port",type=int,default=8765);p.add_argument("--require-auth",action="store_true");p.add_argument("--show-tailscale-help",action="store_true");p.add_argument("--tailscale",action="store_true",help="호환용: Tailscale Serve 안내");p.add_argument("--config",type=safe_config_path,default=DEFAULT_CONFIG,help=argparse.SUPPRESS);a=p.parse_args(argv)
    if a.show_tailscale_help or a.tailscale:print("Tailscale Serve HTTPS: .venv-web/bin/python universe_web.py ; tailscale serve --https=443 http://127.0.0.1:8765 ; tailscale serve status로 HTTPS 주소 확인 후 RP ID/Origin 환경변수 설정");return
    service=WebAuthnService();required=a.require_auth or a.host!="127.0.0.1" or service.configured;pin=os.environ.get("UNIVERSE_WEB_PIN")
    if required and not pin:raise SystemExit("오류: 인증 운영에는 UNIVERSE_WEB_PIN이 bootstrap/복구용으로 필요합니다.")
    s=create_server(a.host,a.port,a.config,pin,required,service);print(f"로컬 접속: http://127.0.0.1:{a.port}");print("Passkey 운영: --show-tailscale-help")
    try:s.serve_forever()
    except KeyboardInterrupt:pass
    finally:s.server_close()

HTML = r'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Universe Manager</title><style>
:root{color-scheme:light dark;--bg:#f5f7fb;--card:#fff;--ink:#152033;--mute:#64748b;--line:#e4e8f0;--brand:#5267ee;--good:#087d5a;--warn:#9a6500;--bad:#bc3030}@media(prefers-color-scheme:dark){:root{--bg:#101624;--card:#192235;--ink:#edf2ff;--mute:#a8b2c6;--line:#303c52;--brand:#8b99ff}}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",sans-serif;padding-bottom:90px}.app{max-width:820px;margin:auto;padding:max(16px,env(safe-area-inset-top)) 16px 16px}.head{display:flex;justify-content:space-between;align-items:center}.tabs,.actions{display:flex;gap:8px;overflow:auto}.tabs{position:sticky;top:0;background:var(--bg);padding:14px 0;z-index:2}.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:15px;margin:10px 0}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:9px}.metric b{display:block;font-size:26px;margin-top:5px}.muted{color:var(--mute);font-size:13px}.chip,.badge{display:inline-block;background:#e8edff;color:#4052bd;border-radius:999px;padding:4px 8px;margin:3px;font-size:12px}.ok{background:#dff6ed;color:var(--good)}.warn{background:#fff0d4;color:var(--warn)}.bad{background:#ffe1e1;color:var(--bad)}button,input{font:inherit;border-radius:12px;padding:11px 13px;min-height:44px;border:1px solid var(--line)}button{background:var(--brand);color:#fff;border:0;font-weight:700}.secondary{background:var(--card);color:var(--ink);border:1px solid var(--line)}.danger{background:var(--bad)}input{width:100%;background:var(--card);color:var(--ink)}.view{display:none}.view.active{display:block}.bottom{position:fixed;bottom:0;left:0;right:0;padding:10px max(16px,env(safe-area-inset-right)) calc(10px + env(safe-area-inset-bottom));background:color-mix(in srgb,var(--card) 93%,transparent);display:flex;justify-content:center;gap:8px;border-top:1px solid var(--line)}.sheet{display:none;position:fixed;inset:0;background:#0008;align-items:end;z-index:10}.sheet.on{display:flex}.panel{background:var(--card);width:min(820px,100%);margin:auto;padding:20px;border-radius:22px 22px 0 0;max-height:88vh;overflow:auto}pre{white-space:pre-wrap;overflow:auto;background:#101624;color:#e8efff;border-radius:12px;padding:12px}.toast{position:fixed;top:18px;left:50%;transform:translateX(-50%);background:#152033;color:#fff;padding:11px 15px;border-radius:12px;display:none;z-index:20}@media(min-width:650px){.grid{grid-template-columns:repeat(3,1fr)}.stocks{display:grid;grid-template-columns:1fr 1fr;gap:10px}.stocks .card{margin:0}.sheet{align-items:center}.panel{border-radius:22px}}</style><body><main class="app"><header class="head"><div><h1>Universe Manager</h1><span class="muted">안전한 Universe 관리</span></div><a href="/help">사용방법</a></header><nav class="tabs"><button data-v="dash">대시보드</button><button class="secondary" data-v="stocks">종목 관리</button><button class="secondary" data-v="groups">그룹 관리</button><button class="secondary" data-v="valid">설정 검증</button><button class="secondary" onclick="logout()">로그아웃</button></nav><section id="dash" class="view active"></section><section id="stocks" class="view"><input id="filter" placeholder="종목명 · 종목코드 · 그룹 검색"><div id="stockList" class="stocks"></div></section><section id="groups" class="view"></section><section id="valid" class="view"></section></main><div class="bottom"><button onclick="addStock()">＋ 종목 추가</button><button class="secondary" onclick="load(true)">설정 검증</button></div><div id="sheet" class="sheet"><div class="panel"><button class="secondary" onclick="closeSheet()">닫기</button><div id="sheetBody"></div></div></div><div id="toast" class="toast"></div><script>
let dash,stocks,groups,pending;const $=x=>document.getElementById(x),esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));function csrf(){return document.cookie.split('; ').find(x=>x.startsWith('universe_csrf='))?.split('=')[1]}async function api(url,body){let h={'Content-Type':'application/json'};if(body&&csrf())h['X-CSRF-Token']=csrf();let r=await fetch(url,body?{method:'POST',headers:h,body:JSON.stringify(body)}:{}),x=await r.json();if(!r.ok)throw x;return x}function toast(x){$('toast').textContent=x;$('toast').style.display='block';setTimeout(()=>$('toast').style.display='none',2400)}function badge(x){let c=x==='OK'?'ok':x==='ERROR'?'bad':'warn';return '<span class="badge '+c+'">'+esc(x==='OK'?'정상':x==='ERROR'?'오류':x==='WARNING'?'경고':x)+'</span>'}function tab(){document.querySelectorAll('[data-v]').forEach(b=>b.onclick=()=>{document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));$((b.dataset.v)).classList.add('active')})}function render(){let v=dash.validation;$('dash').innerHTML='<div class="grid">'+[['활성 종목',dash.enabledCount],['비활성 종목',dash.disabledCount],['섹터',dash.sectorCount],['테마',dash.themeCount],['관심종목',dash.watchlistCount],['설정 검증',v.status]].map(x=>'<article class="card metric"><span class="muted">'+x[0]+'</span><b>'+x[1]+'</b></article>').join('')+'</div><article class="card">현재 설정: '+esc(dash.configPath)+'<br><span class="muted">최근 변경: '+new Date(dash.modifiedAt*1000).toLocaleString()+'</span></article>';$('valid').innerHTML='<article class="card"><h2>설정 검증 '+badge(v.status)+'</h2>'+[...v.errors.map(x=>'오류: '+esc(x)),...v.warnings.map(x=>'경고: '+esc(x))].join('<br>')+(v.errors.length||v.warnings.length?'':'현재 설정은 정상입니다.')+'</article>';renderStocks();$('groups').innerHTML=Object.entries(groups).map(([kind,gs])=>'<article class="card"><h2>'+({sector:'섹터',theme:'테마',watchlist:'관심종목'}[kind])+'</h2>'+gs.map(g=>'<p><b>'+esc(g.name)+'</b> <span class="badge">'+g.members.length+' 종목</span><br>'+g.members.map(m=>'<span class="chip">'+esc(m.name||m.code)+'</span>').join('')+'<br><span class="muted">주도주: '+(g.leaders.join(', ')||'없음')+'</span></p>').join('')+'</article>').join('')}function renderStocks(){let q=$('filter').value?.toLowerCase()||'';$('stockList').innerHTML=stocks.filter(s=>[s.itemCode,s.stockName,...s.sector,...s.theme,...s.watchlist].join(' ').toLowerCase().includes(q)).map(s=>'<article class="card"><b>'+esc(s.stockName)+'</b> <span class="badge">'+s.itemCode+'</span><p>'+badge(s.enabled?'활성':'비활성')+' '+[...s.sector.map(x=>'섹터 '+x),...s.theme.map(x=>'테마 '+x),...s.watchlist.map(x=>'관심 '+x)].map(esc).join(' · ')+'</p><button class="secondary" onclick="preview({op:\'stock_'+(s.enabled?'disable':'enable')+'\',code:\''+s.itemCode+'\'})">'+(s.enabled?'비활성화':'활성화')+'</button> <button class="danger" onclick="hard(\''+s.itemCode+'\')">완전 삭제</button></article>').join('')||'<article class="card">검색 결과가 없습니다.</article>'}async function load(note){try{[dash,stocks,groups]=await Promise.all([api('/api/dashboard'),api('/api/stocks').then(x=>x.stocks),api('/api/groups').then(x=>x.groups)]);render();if(note)toast('설정 검증: '+dash.validation.status)}catch(e){toast((e.errors||['불러오기 실패']).join(' '))}}function sheet(x){$('sheetBody').innerHTML=x;$('sheet').classList.add('on')}function closeSheet(){$('sheet').classList.remove('on')}async function addStock(){let q=prompt('추가할 종목명을 입력하세요');if(!q)return;try{let x=await api('/api/search/stocks?q='+encodeURIComponent(q));let r=x.results?.[0];if(!r)throw {errors:[x.message||'검색 결과가 없습니다.']};preview({op:'stock_add',code:r.code,stockName:r.name})}catch(e){toast((e.errors||['종목 검색 서비스를 사용할 수 없습니다.']).join(' '))}}async function preview(op){try{let x=await api('/api/preview',{operation:op});pending={op,x};sheet('<h2>변경 미리보기</h2><p>'+x.summary+'</p><pre>'+esc(x.diff||'변경 없음')+'</pre><button onclick="apply()" '+(!x.ok||!x.changed?'disabled':'')+'>변경 적용</button>')}catch(e){toast((e.errors||['미리보기 실패']).join(' '))}}async function apply(){try{let x=await api('/api/apply',{operation:pending.op,baselineHash:pending.x.baselineHash,confirmHardDelete:pending.op.op==='stock_delete'});toast(x.applied?'변경을 안전하게 적용했습니다.':'변경 없음');closeSheet();load()}catch(e){toast((e.errors||['적용이 차단되었습니다']).join(' '))}}function hard(c){if(confirm('과거 데이터 연결에 영향을 줄 수 있습니다. 완전 삭제할까요?'))preview({op:'stock_delete',code:c})}async function logout(){await api('/api/logout',{});location='/'}$('filter').oninput=renderStocks;tab();load();</script></body></html>'''
HELP_HTML='''<!doctype html><meta charset="utf-8"><title>Universe Manager 사용방법</title><h1>Universe Manager 사용방법</h1><p>조회만으로 설정은 바뀌지 않습니다. 변경은 미리보기·검증·적용 순서로 처리되며 기존 설정은 백업 후 안전하게 교체됩니다.</p><p>Discovery 후보는 자동으로 Universe에 들어오지 않습니다. 제거는 기본적으로 비활성화를 사용하세요.</p><h2>iPhone Face ID 로그인</h2><p>Mac과 iPhone에서 Tailscale을 켠 뒤 UI는 localhost로 실행하고 <code>tailscale serve --https=443 http://127.0.0.1:8765</code>를 사용합니다. HTTPS 주소를 RP ID/Origin으로 설정하고, 최초에는 관리자 PIN 승인으로 Passkey를 등록합니다.</p>'''
LOGIN_HTML='''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>로그인</title><h1>Universe Manager</h1><button onclick="login()">Face ID로 로그인</button><p id=e>등록된 Passkey가 없거나 이 주소가 HTTPS가 아니면 Face ID 로그인을 사용할 수 없습니다.</p><details><summary>최초 등록·복구용 PIN</summary><input id=pin type=password placeholder="관리자 PIN"><button onclick="register()">이 PIN으로 Face ID 등록</button></details><script>const e=document.querySelector('#e'),b=s=>Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/')),x=>x.charCodeAt(0)),q=a=>btoa(String.fromCharCode(...new Uint8Array(a))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');function prep(o){o.challenge=b(o.challenge);if(o.user)o.user.id=b(o.user.id);for(let k of ['allowCredentials','excludeCredentials'])if(o[k])o[k]=o[k].map(x=>({...x,id:b(x.id)}));return o}function cred(c){let r=c.response;return{id:c.id,rawId:q(c.rawId),type:c.type,response:{clientDataJSON:q(r.clientDataJSON),attestationObject:r.attestationObject&&q(r.attestationObject),authenticatorData:r.authenticatorData&&q(r.authenticatorData),signature:r.signature&&q(r.signature),userHandle:r.userHandle&&q(r.userHandle)}}}async function post(u,x){let r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(x)}),v=await r.json();if(!r.ok)throw v;return v}async function login(){try{let x=await post('/api/auth/passkey/options',{}),c=await navigator.credentials.get({publicKey:prep(x.publicKey)});await post('/api/auth/passkey/verify',{ceremonyId:x.ceremonyId,credential:cred(c)});location='/'}catch(x){e.textContent=(x.errors||['인증이 취소되었습니다.']).join(' ')}}async function register(){try{let x=await post('/api/auth/register/options',{pin:pin.value}),c=await navigator.credentials.create({publicKey:prep(x.publicKey)});await post('/api/auth/register/verify',{bootstrapId:x.bootstrapId,ceremonyId:x.ceremonyId,credential:cred(c)});e.textContent='Face ID 등록이 완료되었습니다.'}catch(x){e.textContent=(x.errors||['등록에 실패했습니다.']).join(' ')}}</script>'''

ADD_UI_JS = r'''<style>.choices{display:flex;flex-wrap:wrap;gap:8px}.choices .chip{display:flex;align-items:center;gap:7px;margin:0;min-height:42px;padding:8px 12px;cursor:pointer}.choices input{appearance:none!important;width:20px!important;height:20px!important;min-height:0!important;padding:0!important;margin:0!important;border:2px solid #7180a8!important;border-radius:50%!important;background:transparent!important}.choices input[type=checkbox]{border-radius:6px!important}.choices input:checked{border-color:var(--brand)!important;background:var(--brand)!important;box-shadow:inset 0 0 0 4px var(--card)}</style><script>
let baseRender=render;render=function(){baseRender();let g=$('groups');g.insertAdjacentHTML('afterbegin','<button onclick="newGroup()">＋ 그룹 추가</button>');let v=dash.validation;if(v.warnings?.length||v.errors?.length){$('dash').insertAdjacentHTML('beforeend','<article class="card '+(v.errors?.length?'bad':'warn')+'"><b>'+(v.errors?.length?'설정 오류':'설정 경고')+'</b><br>'+[...(v.errors||[]),...(v.warnings||[])].map(esc).join('<br>')+'</article>')}};
function newGroup(){sheet('<h2>그룹 추가</h2><h3>그룹 종류</h3><div class="choices"><label class="chip"><input type="radio" name="groupKind" value="sector" checked> 섹터</label><label class="chip"><input type="radio" name="groupKind" value="theme"> 테마</label><label class="chip"><input type="radio" name="groupKind" value="watchlist"> 관심종목</label></div><h3>그룹 이름</h3><input id="groupName" placeholder="예: AI 소프트웨어"><p class="muted">구성종목은 그룹 생성 후 종목 편집에서 추가할 수 있습니다.</p><button onclick="previewNewGroup()">변경 미리보기</button>')}
function previewNewGroup(){let kind=document.querySelector('#sheetBody input[name=groupKind]:checked').value,name=$('groupName').value.trim();if(!name){toast('그룹 이름을 입력하세요.');return}preview({op:'group_create',kind,name})}
function groupEditor(stock){let html='<h2>'+ (stock?'종목 편집':'종목 추가')+'</h2><div class="card"><b>'+stock.name+'</b><br>'+stock.code+' · '+(stock.market||'시장 정보 없음')+'</div>';for(let [kind,label,multi] of [['sector','섹터',false],['theme','테마',true],['watchlist','관심종목',true]]){let list=groups[kind]||[];html+='<h3>'+label+'</h3><div class="choices" data-kind="'+kind+'">'+(kind==='sector'?'<label class="chip"><input type="radio" name="sector" value=""> 지정 안 함</label>':'')+list.map(g=>'<label class="chip"><input type="'+(multi?'checkbox':'radio')+'" name="'+kind+'" value="'+esc(g.name)+'"> '+esc(g.name)+'</label>').join('')+'</div>'}html+='<button onclick="previewGroups('+JSON.stringify(stock).replace(/"/g,'&quot;')+')">변경 미리보기</button>';sheet(html)}
addStock=async function(){let q=prompt('종목명을 입력하세요');if(!q)return;try{let x=await api('/api/search/stocks?q='+encodeURIComponent(q)),r=x.results?.[0];if(!r)throw {errors:[x.message||'검색 결과가 없습니다.']};groupEditor(r)}catch(e){toast((e.errors||['종목 검색 실패']).join(' '))}}
function previewGroups(stock){let groupsOut={sector:[],theme:[],watchlist:[]};document.querySelectorAll('#sheetBody .choices input:checked').forEach(x=>{if(x.value)groupsOut[x.closest('.choices').dataset.kind].push(x.value)});preview({op:'stock_add_with_groups',code:stock.code,stockName:stock.name,groups:groupsOut})}
let oldPreview=preview;preview=async function(op){try{let x=await api('/api/preview',{operation:op});pending={op,x};let lines=(x.summary||'').split('\n').map(esc).join('<br>');sheet('<h2>변경 미리보기</h2><div class="card">'+lines+'</div><button class="secondary" onclick="groupEditor({code:\''+(op.code||'')+'\',name:\''+(op.stockName||'')+'\'})">수정하기</button> <button onclick="apply()" '+(!x.ok||!x.changed?'disabled':'')+'>변경 적용</button><details><summary>상세 변경 내용 보기</summary><pre>'+esc(x.diff||'변경 없음')+'</pre></details>')}catch(e){toast((e.errors||['미리보기 실패']).join(' '))}}
function configSummary(diff){let rows=[];if(diff.includes('"itemCode": "006800"'))rows.push('신규 종목<br><b>미래에셋증권</b><br><span class="badge">006800</span>');if(diff.includes('"관심2"'))rows.push('관심종목<br><b>관심2</b><br>＋ 미래에셋증권');return rows.join('<hr>')||'저장할 Universe 설정 변경을 확인하세요.'}
async function refreshConfigBackup(){let card=$('configBackup');if(!card)return;try{let x=await api('/api/config-sync/status');let changed=x.state==='LOCAL_CONFIG_CHANGES';card.querySelector('[data-sync-status]').innerHTML=changed?'🟠 저장하지 않은 설정 변경 있음':'✅ GitHub에 저장된 설정과 동일';card.querySelector('[data-sync-actions]').style.display='flex'}catch(e){card.querySelector('[data-sync-status]').textContent='로그인 필요 — 설정 백업 상태는 로그인 후 확인할 수 있습니다.';card.querySelector('[data-sync-actions]').style.display='none'}}
async function previewConfigBackup(){try{let x=await api('/api/config-sync/preview');sheet('<h2>GitHub에 저장할 설정 변경</h2><div class="card">'+configSummary(x.diff||'')+'</div><button onclick="pushConfigBackup()">GitHub에 설정 저장</button><details><summary>상세 변경 내용 보기</summary><pre>'+esc(x.diff||'변경 없음')+'</pre></details>')}catch(e){toast((e.errors||['로그인이 필요합니다.']).join(' '))}}
async function pushConfigBackup(){try{let x=await api('/api/config-sync/push',{});toast(x.message||'설정을 GitHub에 저장했습니다.');closeSheet();refreshConfigBackup()}catch(e){toast((e.errors||['설정 저장이 차단되었습니다.']).join(' '))}}
let renderWithConfigBackup=render;render=function(){renderWithConfigBackup();let v=$('valid');if(!$('configBackup'))v.insertAdjacentHTML('beforeend','<article id="configBackup" class="card"><h2>Universe 설정 백업</h2><p class="muted">종목·섹터·테마·관심종목 설정만 GitHub에 저장합니다.<br>프로그램 소스코드는 포함되지 않습니다.</p><p data-sync-status>상태 확인 중…</p><div class="actions" data-sync-actions style="display:none"><button class="secondary" onclick="refreshConfigBackup()">상태 새로고침</button><button class="secondary" onclick="previewConfigBackup()">변경 미리보기</button><button onclick="previewConfigBackup()">GitHub에 설정 저장</button></div></article>');refreshConfigBackup()}
</script>'''
HTML = HTML.replace("$('filter').oninput=renderStocks;tab();load();", "$('filter').oninput=renderStocks;")
HTML = HTML.replace('<button data-v="dash">대시보드</button><button class="secondary" data-v="stocks">종목 관리</button><button class="secondary" data-v="groups">그룹 관리</button><button class="secondary" data-v="valid">설정 검증</button><button class="secondary" onclick="logout()">로그아웃</button>', '<button data-v="dash">시장 현황</button><button class="secondary" data-v="groups">내 관심종목</button><button class="secondary" data-v="valid">시스템 관리</button><button class="secondary" onclick="logout()">로그아웃</button>')
HTML = HTML.replace('<section id="dash" class="view active"></section>', '<section id="dash" class="view active"><div id="dynamicUniverseContent"></div></section>')
HTML = HTML.replace('<section id="groups" class="view"></section>', '<section id="groups" class="view"><h2>내 관심종목 · 종목 관리</h2><div id="myStocks"><h3>등록 종목</h3><input id="filterMine" placeholder="종목명 · 종목코드 검색"><div id="stockListMine"></div></div></section>')

ADD_UI_JS += r'''<style>
body{overflow-x:hidden}.app{padding-bottom:120px}.tabs{overflow-x:auto;scrollbar-width:thin}.tabs button{flex:0 0 auto;min-width:88px;white-space:nowrap;word-break:keep-all;background:var(--card);color:var(--ink);border:1px solid var(--line)}.tabs button.tab-selected{background:var(--brand);color:#fff;border-color:var(--brand)}.dynamic-summary{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.dynamic-summary .card{margin:0;min-width:0}.dynamic-summary b{font-size:22px;line-height:1.15;overflow-wrap:anywhere}.market-toggle{width:100%;text-align:left;background:var(--card);color:var(--ink);border:1px solid var(--line);margin-top:8px}.market-toggle[aria-expanded="true"]{border-color:var(--brand)}.market-detail{display:none;padding:10px 0}.market-detail.open{display:block}.bottom{padding-bottom:calc(14px + env(safe-area-inset-bottom))}@media(min-width:650px){.dynamic-summary{grid-template-columns:repeat(4,minmax(0,1fr))}}
</style><script>
(function(){
function kst(v){if(!v)return '기준시각 확인 불가';try{return new Date(v).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'numeric',day:'numeric',hour:'numeric',minute:'2-digit',hour12:true})}catch(_){return '기준시각 확인 불가'}}
function label(s){return ({ACTIVE:'주목 중',WATCH:'관찰 중',DISCOVERED:'선정 검토 중',REENTERED:'다시 주목',REMOVED:'관찰 종료',PENDING:'확인 대기'}[s]||s||'확인 대기')}
function value(v){return v===null||v===undefined||v===''?'확인 불가':esc(v)}
function metrics(v){let m=v&&v.metrics||{},keys=[['avgReturn','평균 등락률','%'],['medianReturn','중앙값 등락률','%'],['advancerPct','상승 종목 비율','%'],['pctAboveMA20','20일선 상회율','%'],['turnoverValue','거래대금',''],['turnoverConcentrationPct','상위 3종목 거래대금 비중','%']];return '<div class="muted">'+keys.map(k=>'<span class="chip">'+k[1]+': '+value(m[k[0]])+(m[k[0]]==null?'':k[2])+'</span>').join('')+'</div>'}
function row(v){let name=v.name||v.code||'이름 확인 불가',reason=v.reason||v.criteria||'선정 근거 자료 미제공',stocks=v.stocks||v.leaders||v.members||[],details='<div class="market-detail"><p class="muted">선정 이유: '+esc(reason)+'</p>'+metrics(v)+'<p class="muted">데이터 기준시각: '+kst(v.sourceTime||v.evidenceGeneratedAt)+'</p>'+((stocks||[]).slice(0,3).map(s=>'<p><b>'+esc(s.name||s.code||'종목명 확인 불가')+'</b> <span class="badge">'+esc(s.code||'코드 확인 불가')+'</span></p>').join('')||'<p class="muted">연결된 주목 종목 없음</p>')+'</div>';return '<article class="card"><button class="market-toggle" aria-expanded="false"><b>'+esc(name)+'</b> <span class="badge">'+esc(label(v.status))+'</span></button>'+details+'</article>'}
function section(title,items){return '<h2>'+title+'</h2>'+((items||[]).filter(v=>v.status!=='REMOVED').map(row).join('')||'<p class="muted">현재 선정 없음</p>')}
function renderMarket(x){let sectors=x?.sectors||[],themes=x?.themes||[],leaders=x?.leaders||[],kiwoom=x?.kiwoomDiscovery||[],active=sectors.filter(v=>['ACTIVE','WATCH','DISCOVERED','REENTERED'].includes(v.status)).length,observed=new Set([...leaders,...kiwoom].map(v=>v.code).filter(Boolean)),host=$('dynamicUniverseContent');if(!host){$('dash').innerHTML='<div id="dynamicUniverseContent"></div>';host=$('dynamicUniverseContent')}host.innerHTML='<article class="card"><h1>시장 현황</h1><p class="muted">선정 자료: '+kst(x.generatedAt)+' · 동기화: '+esc(({APPLIED:'갱신 완료',NOOP:'최신 상태',FAILED:'갱신 실패',RUNNING:'확인 중'}[x.sync?.status]||'확인 대기'))+'</p><div class="dynamic-summary"><article class="card metric"><span class="muted">주목 업종</span><b>'+active+'</b></article><article class="card metric"><span class="muted">주목 테마</span><b>'+themes.filter(v=>v.status!=='REMOVED').length+'</b></article><article class="card metric"><span class="muted">관찰 종목</span><b>'+observed.size+'</b></article><article class="card metric"><span class="muted">키움 후보</span><b>'+kiwoom.length+'</b></article></div>'+section('주목 업종',sectors)+section('주목 테마',themes)+section('주도주·대표 관찰 종목',leaders)+section('키움 발굴 후보',kiwoom)+'</article>'}
function renderMine(){let host=$('myStocks');if(!host){$('groups').insertAdjacentHTML('beforeend','<div id="myStocks"><h3>등록 종목</h3><input id="filterMine" placeholder="종목명 · 종목코드 검색"><div id="stockListMine"></div></div>');host=$('myStocks')}if($('filterMine'))$('filterMine').oninput=function(){let q=this.value.toLowerCase();$('stockListMine').innerHTML=stocks.filter(s=>(s.itemCode+' '+s.stockName).toLowerCase().includes(q)).map(s=>'<p><b>'+esc(s.stockName)+'</b> <span class="badge">'+esc(s.itemCode)+'</span> '+badge(s.enabled?'활성':'비활성')+'</p>').join('')||'<p class="muted">등록 종목이 없습니다.</p>'};$('filterMine').oninput()}
function select(id){let bs=[...document.querySelectorAll('.tabs [data-v]')];document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id===id));bs.forEach(b=>b.classList.toggle('tab-selected',b.dataset.v===id));let b=bs.find(x=>x.dataset.v===id);if(b)b.scrollIntoView({block:'nearest',inline:'center'})}
document.querySelectorAll('.tabs [data-v]').forEach(b=>b.onclick=()=>select(b.dataset.v));document.addEventListener('click',e=>{let b=e.target.closest('.market-toggle');if(!b)return;let d=b.nextElementSibling,open=b.getAttribute('aria-expanded')==='true';b.setAttribute('aria-expanded',String(!open));d.classList.toggle('open',!open)});select('dash');
let initialized=false,serial=0,syncPollTimer=null,syncPollStopped=false,syncPollCount=0,lastSyncView=null;
function stopSyncPolling(){syncPollStopped=true;if(syncPollTimer){clearTimeout(syncPollTimer);syncPollTimer=null}}
function terminalSyncStatus(status){return ['APPLIED','NOOP','FAILED','SUCCESS','COMPLETED'].includes(status)}
async function refreshAutomaticView(){let next=await api('/api/automatic');let before=lastSyncView||{};let changed=next.commit!==before.commit||next.generatedAt!==before.generatedAt;if(changed){lastSyncView=next;renderMarket(next)}return next}
async function pollSyncStatus(){
  if(syncPollStopped||syncPollCount>=15)return stopSyncPolling();
  syncPollCount+=1;
  try{
    let status=await api('/api/dynamic-sync/status');
    if(status.status==='RUNNING'){
      syncPollTimer=setTimeout(pollSyncStatus,1000);return;
    }
    if(status.status==='APPLIED')await refreshAutomaticView();
    else if(terminalSyncStatus(status.status)){lastSyncView={...(lastSyncView||{}),sync:{...(lastSyncView?.sync||{}),status:status.status}};renderMarket(lastSyncView)}
    stopSyncPolling();
  }catch(e){stopSyncPolling()}
}
function startSyncPolling(view){
  stopSyncPolling();syncPollStopped=false;syncPollCount=0;lastSyncView=view;
  if(view?.sync?.status==='RUNNING')syncPollTimer=setTimeout(pollSyncStatus,250);
}
window.addEventListener('pagehide',stopSyncPolling,{once:true});
async function initialize(note){if(initialized)return;initialized=true;let n=++serial;try{let r=await Promise.all([api('/api/dashboard'),api('/api/stocks').then(x=>x.stocks),api('/api/groups').then(x=>x.groups),api('/api/automatic')]);if(n!==serial)return;[dash,stocks,groups]=r;render();renderMine();renderMarket(r[3]);startSyncPolling(r[3]);if(note)toast('설정 검증: '+dash.validation.status)}catch(e){initialized=false;stopSyncPolling();toast('시장 자료를 불러오지 못했습니다. 마지막 정상 화면을 유지합니다.')}}
window.loadUser=initialize;select('dash');initialize();
})();
</script>'''

if __name__=="__main__":main()
