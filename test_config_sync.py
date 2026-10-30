import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import config_sync


def config(stocks=None, watchlists=None):
    return {
        "stocks": stocks or [], "sectors": {}, "themes": {},
        "watchlists": watchlists or {},
        "leaders": {"sector": {}, "theme": {}, "watchlist": {}},
    }


class ConfigSyncTests(unittest.TestCase):
    def test_status_uses_json_baseline_not_worktree_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            data = config([{"itemCode": "005930", "stockName": "삼성전자", "enabled": True}])
            (root / "config" / "universe.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

            def runner(args, **_):
                if args[1:] == ["diff", "--cached", "--name-only"]:
                    return subprocess.CompletedProcess(args, 0, "", "")
                if args[1:] == ["show", "origin/main:config/universe.json"]:
                    return subprocess.CompletedProcess(args, 0, json.dumps(data, ensure_ascii=False), "")
                return subprocess.CompletedProcess(args, 0, "", "")

            result = config_sync.status(root, runner)
            self.assertEqual(result["state"], "SYNCED")
            self.assertFalse(result["dirty"])

    def test_summary_uses_actual_stock_and_group_changes(self):
        before = config(
            [{"itemCode": "005930", "stockName": "삼성전자", "enabled": True}],
            {"관심종목": ["005930"]},
        )
        after = config(
            [{"itemCode": "005930", "stockName": "삼성전자", "enabled": False}],
            {"관심종목": []},
        )
        summary = config_sync.comparison_summary(before, after)
        self.assertEqual(summary["state"], "CHANGED")
        self.assertEqual(summary["stocks"]["changed"][0]["itemCode"], "005930")
        self.assertEqual(summary["groups"][0]["name"], "관심종목")
        self.assertEqual(summary["groups"][0]["removed"], ["005930"])
        self.assertNotIn("006800", str(summary))

    def test_summary_distinguishes_no_change_and_empty_source(self):
        populated = config(
            [{"itemCode": "005930", "stockName": "삼성전자", "enabled": True}]
        )
        self.assertEqual(config_sync.comparison_summary(populated, populated)["state"], "NO_CHANGES")
        empty = config()
        self.assertEqual(config_sync.comparison_summary(empty, empty)["state"], "EMPTY_SOURCE")

    def test_summary_reports_group_creation_and_removal(self):
        before = config([{"itemCode": "005930", "stockName": "삼성전자", "enabled": True}])
        after = config(
            [{"itemCode": "005930", "stockName": "삼성전자", "enabled": True}],
            {"관심종목": ["005930"]},
        )
        added = config_sync.comparison_summary(before, after)["groups"]
        self.assertEqual(added[0]["change"], "ADDED")
        removed = config_sync.comparison_summary(after, before)["groups"]
        self.assertEqual(removed[0]["change"], "REMOVED")


if __name__ == "__main__":
    unittest.main()
