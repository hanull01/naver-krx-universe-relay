import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import universe_manager


def fixture():
    return {
        "stocks": [
            {"itemCode": "000001", "stockName": "A", "enabled": True},
            {"itemCode": "000002", "stockName": "B", "enabled": True},
        ],
        "sectors": {"기존": ["000001"]},
        "themes": {},
        "watchlists": {},
        "leaders": {"sector": {"기존": ["000001"]}, "theme": {}, "watchlist": {}},
    }


class UniverseManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config" / "universe.json"
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps(fixture(), ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            universe_manager.main(["--file", str(self.path), *args])
        return output.getvalue()

    def data(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_add_stock_and_duplicate_add(self):
        self.invoke("stock", "add", "--code", "005930", "--name", "삼성전자")
        self.assertIn("005930", universe_manager.stock_map(self.data()))
        output = self.invoke("stock", "add", "--code", "005930", "--name", "다른이름")
        self.assertIn("ALREADY EXISTS", output)
        self.assertEqual(len(self.data()["stocks"]), 3)

    def test_disable_and_enable_stock(self):
        self.invoke("stock", "disable", "--code", "000001")
        self.assertFalse(universe_manager.stock_map(self.data())["000001"]["enabled"])
        self.invoke("stock", "enable", "--code", "000001")
        self.assertTrue(universe_manager.stock_map(self.data())["000001"]["enabled"])

    def test_group_create_and_delete(self):
        self.invoke("group", "create", "--kind", "theme", "--name", "AI")
        self.assertIn("AI", self.data()["themes"])
        self.invoke("group", "delete", "--kind", "theme", "--name", "AI")
        self.assertNotIn("AI", self.data()["themes"])

    def test_member_add_and_remove(self):
        self.invoke("group", "member", "--kind", "sector", "--name", "기존", "add", "--code", "000002")
        self.assertEqual(self.data()["sectors"]["기존"], ["000001", "000002"])
        self.invoke("group", "member", "--kind", "sector", "--name", "기존", "remove", "--code", "000002")
        self.assertEqual(self.data()["sectors"]["기존"], ["000001"])

    def test_leader_must_be_group_member(self):
        with self.assertRaisesRegex(ValueError, "leader는 해당 group member"):
            self.invoke("leader", "set", "--kind", "sector", "--name", "기존", "--code", "000002")
        self.invoke("leader", "set", "--kind", "sector", "--name", "기존", "--code", "000001")
        self.assertEqual(self.data()["leaders"]["sector"]["기존"], ["000001"])

    def test_unknown_stock_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "존재하지 않는 종목"):
            self.invoke("group", "member", "--kind", "sector", "--name", "기존", "add", "--code", "999999")

    def test_hard_delete_protects_references(self):
        with self.assertRaisesRegex(ValueError, "hard delete blocked; references: sector/기존, leader/sector/기존"):
            self.invoke("stock", "delete", "--code", "000001", "--hard")

    def test_dry_run_does_not_write(self):
        before = self.path.read_text(encoding="utf-8")
        output = self.invoke("stock", "add", "--code", "005930", "--name", "삼성전자", "--dry-run")
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertIn("+", output)
        self.assertFalse((self.path.parent / "history").exists())

    def test_atomic_save_creates_backup(self):
        self.invoke("stock", "add", "--code", "005930", "--name", "삼성전자")
        backups = list((self.path.parent / "history").glob("universe-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), fixture())
        self.assertIn("005930", universe_manager.stock_map(self.data()))

    def test_malformed_config_handling(self):
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "잘못된 JSON 구조"):
            self.invoke("show")


if __name__ == "__main__":
    unittest.main()
