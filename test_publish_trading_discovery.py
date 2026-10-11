import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import trading_discovery as td

SPEC = importlib.util.spec_from_file_location(
    "publish_trading_discovery", Path(__file__).parent / "tools/publish_trading_discovery.py")
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


class SafePublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.repo = self.root / "repo"
        subprocess.run(["git", "init", "--bare", str(self.remote)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(["git", "clone", str(self.remote), str(self.repo)], check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        git(self.repo, "config", "user.name", "Fixture")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        (self.repo / "README.md").write_text("fixture\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "fixture")
        git(self.repo, "branch", "-M", "main")
        git(self.repo, "push", "-u", "origin", "main")
        self.calendar = self.root / "calendar.json"
        self.calendar.write_text(json.dumps({"source": "KRX_OFFICIAL",
                                             "dates": ["2026-10-09", "2026-10-12", "2026-10-13"]}), encoding="utf-8")
        payload = {
            "schema_version": 1, "status": "AVAILABLE", "selection_date": "2026-10-09",
            "monitoring_date": "2026-10-12", "source": "KIWOOM_WATCHLIST",
            "source_group": "전일후보군", "group_id": "001",
            "synced_at": "2026-10-09T16:00:00+09:00", "candidate_count": 1,
            "symbols": ["005930"], "source_sha256": "0" * 64,
            "validation": {"status": "VALID", "calendar_source": "KRX_OFFICIAL", "immutable": True},
        }
        payload["source_sha256"] = td.source_hash(payload)
        self.snapshot = self.root / "snapshot.json"
        self.snapshot.write_text(json.dumps(payload), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_success_remote_verification_and_repeat_noop(self):
        first = publisher.safe_publish(self.snapshot, self.calendar, self.repo)
        self.assertEqual(first["publishStatus"], "PUSHED")
        self.assertEqual(set(first["stagedFiles"]), {
            "data/trading-discovery/2026-10-12.json", "data/trading-discovery/latest.json"})
        second = publisher.safe_publish(self.snapshot, self.calendar, self.repo)
        self.assertEqual(second["publishStatus"], "NOOP")
        self.assertIsNone(second["commitSha"])
        raw = git(self.repo, "show", "origin/main:data/trading-discovery/2026-10-12.json")
        self.assertEqual(json.loads(raw)["sourceSha256"], json.loads(self.snapshot.read_text())["source_sha256"])

    def test_condition_search_success_and_repeat_noop(self):
        payload = json.loads(self.snapshot.read_text())
        payload.update({"source": "KIWOOM_CONDITION_SEARCH",
                        "source_group": "장종료후 단타후보군 탐색",
                        "group_id": "CONDITION:0",
                        "symbols": ["0197X0"], "candidate_count": 1})
        payload["source_sha256"] = td.source_hash(payload)
        self.snapshot.write_text(json.dumps(payload), encoding="utf-8")
        first = publisher.safe_publish(self.snapshot, self.calendar, self.repo)
        second = publisher.safe_publish(self.snapshot, self.calendar, self.repo)
        self.assertEqual(first["publishStatus"], "PUSHED")
        self.assertEqual(second["publishStatus"], "NOOP")
        raw = git(self.repo, "show", "origin/main:data/trading-discovery/2026-10-12.json")
        self.assertEqual(json.loads(raw)["symbols"], ["0197X0"])

    def test_remote_advance_aborts_without_overwrite(self):
        competitor = self.root / "competitor"
        def advance(_base):
            subprocess.run(["git", "clone", "--branch", "main", str(self.remote), str(competitor)],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            git(competitor, "config", "user.name", "Competitor")
            git(competitor, "config", "user.email", "competitor@example.invalid")
            (competitor / "OTHER.md").write_text("race\n", encoding="utf-8")
            git(competitor, "add", "OTHER.md")
            git(competitor, "commit", "-m", "competing writer")
            git(competitor, "push", "origin", "main")
        with self.assertRaisesRegex(publisher.PublishError, "REMOTE_ADVANCED"):
            publisher.safe_publish(self.snapshot, self.calendar, self.repo,
                                   before_final_fetch=advance)
        tree = git(self.repo, "ls-tree", "-r", "--name-only", "origin/main")
        self.assertNotIn("data/trading-discovery/2026-10-12.json", tree)

    def test_invalid_snapshot_never_touches_remote(self):
        before = git(self.repo, "rev-parse", "origin/main")
        payload = json.loads(self.snapshot.read_text()); payload["candidate_count"] = 2
        self.snapshot.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(td.DiscoveryError):
            publisher.safe_publish(self.snapshot, self.calendar, self.repo)
        self.assertEqual(git(self.repo, "rev-parse", "origin/main"), before)

    def test_discovery_push_does_not_match_existing_refresh_path_filters(self):
        root = Path(__file__).parent
        for name in ("refresh.yml", "refresh-market-snapshots.yml"):
            workflow = (root / ".github/workflows" / name).read_text(encoding="utf-8")
            trigger_paths = workflow.split("schedule:", 1)[0]
            self.assertNotIn("data/trading-discovery", trigger_paths)
            self.assertNotIn("trading_discovery.py", trigger_paths)
            self.assertNotIn("publish_trading_discovery.py", workflow)
        mail = (root / "send_market_data_ready.py").read_text(encoding="utf-8")
        self.assertIn("[MARKET_DATA_READY]", mail)
        self.assertNotIn("trading-discovery", mail)

    def test_supplemental_collection_is_optional_and_does_not_change_mail_gate(self):
        workflow = (Path(__file__).parent / ".github/workflows/refresh.yml").read_text(encoding="utf-8")
        self.assertIn("id: discovery_quotes", workflow)
        self.assertIn("exit 0", workflow[workflow.index("id: discovery_quotes"):])
        mail = workflow[workflow.index("id: ready_email"):]
        self.assertIn("steps.collect.outputs.exit_code == '0'", mail[:600])
        self.assertIn("steps.full_market.outputs.exit_code == '0'", mail[:600])
        self.assertIn("steps.publish.outputs.published == 'true'", mail[:600])
        self.assertNotIn("steps.discovery_quotes", mail[:600])


if __name__ == "__main__":
    unittest.main()
