import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import daily_report
import trading_discovery as td


class ReportTradingDiscoveryIntegrationTests(unittest.TestCase):
    def core(self, report):
        value = copy.deepcopy(report)
        value.pop("kiwoomPreviousCandidates", None)
        value.pop("generatedAt", None)
        return value

    def published(self, symbols=None, monitoring="2026-10-08"):
        symbols = ["005930"] if symbols is None else symbols
        payload = {
            "schemaVersion": 1, "status": "AVAILABLE" if symbols else "AVAILABLE_EMPTY",
            "monitoringDate": monitoring, "selectionDate": "2026-10-07",
            "source": "KIWOOM_WATCHLIST", "sourceGroup": "전일후보군", "groupId": "001",
            "syncedAt": "2026-10-07T16:00:00+09:00", "candidateCount": len(symbols),
            "symbols": symbols, "sourceSha256": "0" * 64,
            "validation": {"status": "VALID", "calendarSource": "KRX_OFFICIAL", "immutable": True},
        }
        payload["sourceSha256"] = td.source_hash({
            "source": payload["source"], "source_group": payload["sourceGroup"],
            "group_id": payload["groupId"], "selection_date": payload["selectionDate"],
            "symbols": payload["symbols"],
        })
        return payload

    def build(self, discovery_path, previous_path):
        with patch.multiple(daily_report, TRADING_DISCOVERY_PATH=discovery_path,
                            PREVIOUS_REPORT_PATH=previous_path):
            return daily_report.build_report("2026-10-08")

    def test_missing_corrupt_stale_and_empty_do_not_change_core_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); discovery = root / "latest.json"; previous = root / "previous.json"
            missing = self.build(discovery, previous)
            discovery.write_text("{broken", encoding="utf-8")
            corrupt = self.build(discovery, previous)
            discovery.write_text(json.dumps(self.published(monitoring="2026-10-07")), encoding="utf-8")
            stale = self.build(discovery, previous)
            discovery.write_text(json.dumps(self.published([])), encoding="utf-8")
            empty = self.build(discovery, previous)
            baseline = self.core(missing)
            self.assertEqual(self.core(corrupt), baseline)
            self.assertEqual(self.core(stale), baseline)
            self.assertEqual(self.core(empty), baseline)
            self.assertEqual(missing["kiwoomPreviousCandidates"]["status"], "UNAVAILABLE")
            self.assertEqual(corrupt["kiwoomPreviousCandidates"]["status"], "INVALID")
            self.assertEqual(stale["kiwoomPreviousCandidates"]["status"], "STALE")
            self.assertEqual(empty["kiwoomPreviousCandidates"]["status"], "AVAILABLE_EMPTY")

    def test_partial_candidate_data_does_not_degrade_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); discovery = root / "latest.json"; previous = root / "previous.json"
            discovery.write_text(json.dumps(self.published(["005930", "999999"])), encoding="utf-8")
            report = self.build(discovery, previous)
            self.assertEqual(report["status"], "OK")
            section = report["kiwoomPreviousCandidates"]
            self.assertEqual(section["status"], "AVAILABLE")
            self.assertEqual(section["missingCount"], 1)
            self.assertEqual(section["stocks"][1]["dataStatus"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
