import json
import tempfile
import unittest
from pathlib import Path

import universe_web
from test_universe_manager import fixture


class UniverseWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config" / "universe.json"
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(fixture(), ensure_ascii=False), encoding="utf-8")
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

    def test_stock_list_load(self):
        status, payload = self.request("GET", "/api/stocks")
        self.assertEqual(status, 200)
        self.assertEqual(payload["stocks"][0]["sector"], ["기존"])
        self.assertEqual(payload["stocks"][0]["leaders"], ["sector:기존"])

    def test_group_list_load(self):
        status, payload = self.request("GET", "/api/groups")
        self.assertEqual(status, 200)
        self.assertEqual(payload["groups"]["sector"][0]["name"], "기존")

    def test_dry_run_preview_never_writes(self):
        before = self.path.read_text(encoding="utf-8")
        preview = self.app.preview({"op": "stock_add", "code": "005930", "stockName": "삼성전자"})
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
        result = self.app.apply({"op": "member_add", "kind": "sector", "name": "기존", "code": "999999"}, "wrong")
        self.assertFalse(result["ok"])
        self.assertEqual(before, self.path.read_text(encoding="utf-8"))

    def test_successful_apply_on_temp_fixture(self):
        operation = {"op": "stock_add", "code": "005930", "stockName": "삼성전자"}
        preview = self.app.preview(operation)
        result = self.app.apply(operation, preview["baselineHash"])
        self.assertTrue(result["ok"])
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["stocks"][-1]["itemCode"], "005930")
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


if __name__ == "__main__":
    unittest.main()
