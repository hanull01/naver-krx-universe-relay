import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools.market_dashboard_renderer import render_contract_views


class KrxOperationalReportSampleTests(unittest.TestCase):
    def test_existing_report_contract_keeps_all_four_time_views(self):
        source = Path("data/monitoring/latest-report-contract.json")
        payload = json.loads(source.read_text(encoding="utf-8"))
        contract = payload.get("reportContract", payload)
        for time_text in ("08:00", "09:07", "20:30", "23:00"):
            with self.subTest(time_text=time_text), tempfile.TemporaryDirectory() as directory:
                sample = copy.deepcopy(contract)
                sample["observedAt"] = f"2026-11-03T{time_text}:00+09:00"
                sample.setdefault("relativeStrengthTop5", {})
                sample.setdefault("v2", {})
                sample.setdefault("independentAnalysis", {})
                result = render_contract_views(
                    sample, Path(directory), allow_current_observation=True
                )
                markdown = Path(result["markdown"]).read_text(encoding="utf-8")
                html = Path(result["html"]).read_text(encoding="utf-8")
                self.assertIn("## 1. 시장 대시보드", markdown)
                self.assertIn("## 2. 주도 업종·테마", markdown)
                self.assertIn("## 3. 관심종목·키움 후보", markdown)
                self.assertIn('id="part1"', html)
                self.assertIn('id="part2"', html)
                self.assertIn('id="part3"', html)
                self.assertEqual(result["htmlStatus"], "AVAILABLE")


if __name__ == "__main__":
    unittest.main()
