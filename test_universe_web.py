import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import stock_search
import universe_web
import universe_auth
from test_universe_manager import fixture


class UniverseWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config" / "universe.json"
        self.path.parent.mkdir(parents=True)
        data = fixture()
        data["stocks"].append({"itemCode": "005930", "stockName": "삼성전자", "enabled": False})
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.app = universe_web.UniverseWebApp(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def request(self, method, path, body=None):
        status, payload = self.app.handle(method, path, body)
        return int(status), payload

    def test_dashboard_load(self):
        status, payload = self.request("GET", "/api/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(payload["enabledCount"], 2)
        self.assertEqual(payload["validation"]["status"], "OK")

    def test_healthz_is_read_only_and_has_no_config_details(self):
        status, payload = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})

    def test_stock_list_load(self):
        status, payload = self.request("GET", "/api/stocks")
        self.assertEqual(status, 200)
        self.assertEqual(payload["stocks"][0]["sector"], ["기존"])
        self.assertNotIn("leaders", payload["stocks"][0])

    def test_group_list_load(self):
        status, payload = self.request("GET", "/api/groups")
        self.assertEqual(status, 200)
        self.assertEqual(payload["groups"]["sector"][0]["name"], "기존")

    def test_legacy33_and_manual_leaders_are_hidden_from_web_data(self):
        data = self.app.current()
        data["watchlists"]["legacy33"] = ["000001"]
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        _, groups = self.request("GET", "/api/groups")
        _, stocks = self.request("GET", "/api/stocks")
        self.assertNotIn("legacy33", [group["name"] for group in groups["groups"]["watchlist"]])
        self.assertNotIn("legacy33", stocks["stocks"][0]["watchlist"])
        self.assertNotIn("leaders", stocks["stocks"][0])

    def test_dry_run_preview_never_writes(self):
        before = self.path.read_text(encoding="utf-8")
        preview = self.app.preview({"op": "stock_add", "code": "005932", "stockName": "미리보기종목"})
        self.assertTrue(preview["ok"])
        self.assertTrue(preview["changed"])
        self.assertIn("+", preview["diff"])
        self.assertEqual(before, self.path.read_text(encoding="utf-8"))
        self.assertFalse((self.path.parent / "history").exists())

    def test_invalid_mutation_rejected(self):
        preview = self.app.preview({"op": "member_add", "kind": "sector", "name": "기존", "code": "999999"})
        self.assertFalse(preview["ok"])
        self.assertIn("존재하지 않는 종목", preview["errors"][0])

    def test_validation_error_blocks_apply(self):
        before = self.path.read_text(encoding="utf-8")
        result = self.app.apply({"op": "member_add", "kind": "sector", "name": "기존", "code": "999999"}, universe_web.payload_hash(self.app.current()))
        self.assertFalse(result["ok"])
        self.assertEqual(before, self.path.read_text(encoding="utf-8"))

    def test_successful_apply_on_temp_fixture(self):
        operation = {"op": "stock_add", "code": "005931", "stockName": "테스트종목"}
        preview = self.app.preview(operation)
        result = self.app.apply(operation, preview["baselineHash"])
        self.assertTrue(result["ok"])
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["stocks"][-1]["itemCode"], "005931")
        self.assertTrue(list((self.path.parent / "history").glob("universe-*.json")))

    def test_hard_delete_requires_confirmation_and_reference_protection(self):
        operation = {"op": "stock_delete", "code": "000001"}
        preview = self.app.preview(operation)
        self.assertFalse(preview["ok"])
        result = self.app.apply(operation, universe_web.payload_hash(self.app.current()), False)
        self.assertFalse(result["ok"])
        self.assertIn("explicit confirmation", result["errors"][0])

    def test_config_path_safety(self):
        self.assertEqual(universe_web.safe_config_path(universe_web.DEFAULT_CONFIG), universe_web.DEFAULT_CONFIG)
        with self.assertRaises(Exception):
            universe_web.safe_config_path("/tmp/universe.json")

    def test_malformed_config_handling(self):
        self.path.write_text("{broken", encoding="utf-8")
        status, payload = self.request("GET", "/api/dashboard")
        self.assertEqual(status, 422)
        self.assertIn("잘못된 JSON 구조", payload["errors"][0])

    def test_help_and_korean_labels_exist(self):
        self.assertIn("Universe Manager 사용방법", universe_web.HELP_HTML)
        self.assertIn("종목 관리", universe_web.HTML)
        self.assertIn("변경 미리보기", universe_web.HTML)

    def test_stock_search_exact_prefix_partial_and_code(self):
        for query in ("삼성전자", "삼성", "전자", "005930"):
            result = self.app.search(query)
            self.assertEqual(result["results"][0]["code"], "005930")
            self.assertTrue(result["results"][0]["inUniverse"])
            self.assertFalse(result["results"][0]["enabled"])

    def test_search_endpoint_and_code_mapping(self):
        status, payload = self.request("GET", "/api/search/stocks?q=%EC%82%BC%EC%84%B1%EC%A0%84%EC%9E%90")
        self.assertEqual(status, 200)
        self.assertEqual(payload["results"][0]["code"], "005930")
        self.assertEqual(payload["results"][0]["name"], "삼성전자")

    def test_search_provider_failure_has_korean_message(self):
        result = stock_search.search_stocks("없는검색어", universe_path=self.path, fetch_json=lambda _: (_ for _ in ()).throw(OSError("offline")))
        self.assertEqual(result["results"], [])
        self.assertIn("종목 검색 서비스를", result["message"])

    def test_preview_hash_mismatch_blocks_apply(self):
        operation = {"op": "stock_enable", "code": "005930"}
        preview = self.app.preview(operation)
        data = self.app.current()
        data["stocks"][0]["stockName"] = "변경됨"
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = self.app.apply(operation, preview["baselineHash"])
        self.assertFalse(result["ok"])
        self.assertIn("preview", result["errors"][0])

    def test_batch_stock_add_with_selected_groups(self):
        operation = {"op": "stock_add_with_groups", "code": "005935", "stockName": "테스트", "groups": {"sector": ["기존"], "theme": [], "watchlist": []}}
        preview = self.app.preview(operation)
        self.assertTrue(preview["ok"])
        result = self.app.apply(operation, preview["baselineHash"])
        self.assertTrue(result["ok"])
        self.assertIn("005935", self.app.current()["sectors"]["기존"])

    def test_preview_only_calls_mirae_asset_new_when_spg_already_exists(self):
        data = self.app.current()
        data["stocks"].append({"itemCode": "058610", "stockName": "에스피지", "enabled": True})
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        preview = self.app.preview({"op": "stock_add", "code": "006800", "stockName": "미래에셋증권"})
        self.assertTrue(preview["ok"])
        self.assertIn("신규 종목: 미래에셋증권 006800", preview["summary"])
        self.assertIn("006800", preview["diff"])
        self.assertNotIn('+      "itemCode": "058610"', preview["diff"])
        self.assertNotIn('-      "itemCode": "058610"', preview["diff"])

    def test_stock_add_preview_and_apply_selected_groups(self):
        data = self.app.current()
        data["themes"] = {"증권": [], "밸류업": []}
        data["watchlists"] = {"신규관심": []}
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        op = {"op": "stock_add_with_groups", "code": "006800", "stockName": "미래에셋증권", "groups": {"sector": ["기존"], "theme": ["증권", "밸류업"], "watchlist": ["신규관심"]}}
        before = self.path.read_text(encoding="utf-8")
        preview = self.app.preview(op)
        self.assertIn("섹터: 기존", preview["summary"])
        self.assertIn("테마: 증권, 밸류업", preview["summary"])
        self.assertEqual(before, self.path.read_text(encoding="utf-8"))
        self.assertTrue(self.app.apply(op, preview["baselineHash"])["ok"])
        updated = self.app.current()
        self.assertIn("006800", updated["sectors"]["기존"])
        self.assertEqual(updated["themes"]["증권"], ["006800"])
        self.assertEqual(updated["themes"]["밸류업"], ["006800"])
        self.assertEqual(updated["watchlists"]["신규관심"], ["006800"])

    def test_non_localhost_requires_pin_and_session(self):
        auth = universe_web.SessionAuth(pin="1234", required=True)
        self.assertIsNone(auth.login("wrong"))
        token = auth.login("1234")
        self.assertTrue(auth.allowed(token))
        auth.logout(token)
        self.assertFalse(auth.allowed(token))

    def test_non_localhost_without_pin_is_blocked_before_bind(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SystemExit):
                universe_web.main(["--host", "0.0.0.0"])

    def test_tailscale_detection(self):
        class Result:
            stdout = "100.64.0.2\n"
        self.assertEqual(universe_web.tailscale_ipv4(lambda *args, **kwargs: Result()), "100.64.0.2")

    def test_passkey_requires_https_rp_configuration(self):
        with patch.dict("os.environ", {}, clear=True):
            service = universe_auth.WebAuthnService(rp_id=None, origin=None)
            self.assertFalse(service.status()["configured"])
            with self.assertRaises(universe_auth.AuthError):
                service.authentication_options()

    def test_credential_store_is_separate_from_universe(self):
        auth_path = Path(self.temp.name) / "data" / "local" / "universe-auth.json"
        store = universe_auth.CredentialStore(auth_path)
        self.assertEqual(store.credentials(), [])
        self.assertNotEqual(auth_path, self.path)
        self.assertFalse(auth_path.exists())

    def test_session_csrf_and_logout(self):
        auth = universe_web.SessionAuth(pin="1234", required=True)
        token = auth.login("1234")
        self.assertTrue(auth.csrf_valid(token, auth.csrf(token)))
        self.assertFalse(auth.csrf_valid(token, "wrong"))
        auth.logout(token)
        self.assertFalse(auth.allowed(token))


class LogoutHttpTests(unittest.TestCase):
    """Exercise the real HTTP handler's logout policy without a browser."""
    def setUp(self):
        self.old_auth = universe_web.Handler.auth
        self.old_secure = universe_web.Handler.secure_cookies
        self.auth = universe_web.SessionAuth(pin="1234", required=True)
        universe_web.Handler.auth = self.auth
        universe_web.Handler.secure_cookies = True

    def tearDown(self):
        universe_web.Handler.auth = self.old_auth
        universe_web.Handler.secure_cookies = self.old_secure

    def handler(self, path, token=None, csrf=None, body=None):
        handler = object.__new__(universe_web.Handler)
        handler.path = path
        raw = json.dumps(body or {}).encode()
        handler.headers = {
            "Cookie": f"universe_session={token}" if token else "",
            "Content-Length": str(len(raw)),
        }
        if csrf is not None:
            handler.headers["X-CSRF-Token"] = csrf
        handler.rfile = io.BytesIO(raw)
        handler.app = Mock()
        handler.result = None
        handler.send_json = lambda status, payload, cookies=(): setattr(
            handler, "result", (status, payload, list(cookies))
        )
        return handler

    def test_logout_revokes_server_session_and_expires_matching_secure_cookies(self):
        token = self.auth.login("1234")
        handler = self.handler("/api/logout", token, self.auth.csrf(token))
        universe_web.Handler.do_POST(handler)
        self.assertEqual(handler.result[:2], (200, {"ok": True}))
        self.assertIsNone(self.auth.record(token))
        session_cookie, csrf_cookie = handler.result[2]
        for value in (session_cookie, csrf_cookie):
            self.assertIn("Max-Age=0", value)
            self.assertIn("Expires=Thu, 01 Jan 1970 00:00:00 GMT", value)
            self.assertIn("Path=/", value)
            self.assertIn("SameSite=Strict", value)
            self.assertIn("Secure", value)
        self.assertIn("HttpOnly", session_cookie)
        self.assertNotIn("HttpOnly", csrf_cookie)

        protected = self.handler("/api/dashboard", token)
        universe_web.Handler.do_GET(protected)
        self.assertEqual(protected.result[0], 401)

    def test_logout_requires_csrf_for_live_session_and_is_idempotent_after_revoke(self):
        token = self.auth.login("1234")
        for csrf in (None, "invalid"):
            handler = self.handler("/api/logout", token, csrf)
            universe_web.Handler.do_POST(handler)
            self.assertEqual(handler.result[0], 403)
            self.assertIsNotNone(self.auth.record(token))

        handler = self.handler("/api/logout", token, self.auth.csrf(token))
        universe_web.Handler.do_POST(handler)
        repeated = self.handler("/api/logout", token)
        universe_web.Handler.do_POST(repeated)
        self.assertEqual(repeated.result[:2], (200, {"ok": True, "alreadyLoggedOut": True}))
        self.assertTrue(all("Max-Age=0" in value for value in repeated.result[2]))

    def test_login_and_logout_cookie_scope_match_and_authenticated_responses_are_no_store(self):
        login = self.handler("/api/login", body={"pin": "1234"})
        universe_web.Handler.do_POST(login)
        self.assertEqual(login.result[:2], (200, {"ok": True}))
        login_cookies = login.result[2]
        token = next(iter(self.auth.sessions))
        handler = self.handler("/api/logout", token, self.auth.csrf(token))
        universe_web.Handler.do_POST(handler)
        for login, expired in zip(login_cookies, handler.result[2]):
            for attribute in ("Path=/", "SameSite=Strict", "Secure"):
                self.assertIn(attribute, login)
                self.assertIn(attribute, expired)

        response = object.__new__(universe_web.Handler)
        response.wfile = io.BytesIO()
        response.send_response = Mock()
        response.send_header = Mock()
        response.end_headers = Mock()
        universe_web.Handler.send_json(response, 200, {"ok": True})
        headers = [call.args for call in response.send_header.call_args_list]
        self.assertIn(("Cache-Control", "no-store, max-age=0"), headers)
        self.assertIn(("Pragma", "no-cache"), headers)


if __name__ == "__main__":
    unittest.main()
