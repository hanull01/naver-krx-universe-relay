import hashlib
import html as html_lib
import tempfile
import unittest
from pathlib import Path

from test_market_dashboard_renderer import report_fixture
from tools.market_dashboard_renderer import TemplateUnavailable, render_contract_views


class ContractOnlyReportRenderingTests(unittest.TestCase):
    def test_published_contract_generates_and_reopens_both_views(self):
        contract = report_fixture()["reportContract"]
        with tempfile.TemporaryDirectory() as directory:
            result = render_contract_views(contract, Path(directory))
            html_path = Path(result["html"])
            markdown_path = Path(result["markdown"])
            html_text = html_path.read_text(encoding="utf-8")
            markdown_text = markdown_path.read_text(encoding="utf-8")

            self.assertTrue(html_path.exists())
            self.assertTrue(markdown_path.exists())
            self.assertEqual(result["htmlBytes"], len(html_text.encode("utf-8")))
            self.assertEqual(
                result["htmlSha256"],
                hashlib.sha256(html_text.encode("utf-8")).hexdigest(),
            )
            self.assertEqual(html_text, Path(result["datedHtml"]).read_text(encoding="utf-8"))
            self.assertNotIn("<pre", html_text.lower())
            self.assertNotIn("2026년 10월 28일 · 11:08 KST", html_lib.unescape(html_text))
            self.assertNotIn("2,205/2,767", html_lib.unescape(html_text))
            for token in ("삼성E&A", "키움 후보"):
                self.assertIn(token, html_lib.unescape(html_text))
                self.assertIn(token, markdown_text)
            self.assertNotIn("005930", html_lib.unescape(html_text))
            self.assertNotIn("005930", markdown_text)
            for token in ("CONTRACT_TRACE", "V2_DATA_TRACE", "TEMPLATE_TRACE", "historyDates", "sourceSha256"):
                self.assertNotIn(token, html_text)
                self.assertNotIn(token, markdown_text)
            for section_id in ("part1", "part2", "part3"):
                self.assertIn(f'id="{section_id}"', html_text)
            for heading in ("## 1. 시장 대시보드", "## 2. 주도 업종·테마", "## 3. 관심종목·키움 후보"):
                self.assertIn(heading, markdown_text)

    def test_partial_contract_is_rendered_without_html_gate(self):
        contract = report_fixture()["reportContract"]
        contract["status"] = "PARTIAL"
        contract["rendering"] = {
            "status": "RENDERABLE",
            "reason": "PARTIAL_DATA_RETAINED",
        }
        with tempfile.TemporaryDirectory() as directory:
            result = render_contract_views(contract, Path(directory))
            self.assertEqual(result["htmlStatus"], "AVAILABLE")
            self.assertTrue(Path(result["datedHtml"]).exists())

    def test_current_observation_mode_allows_validated_partial_contract(self):
        contract = report_fixture()["reportContract"]
        contract["status"] = "PARTIAL"
        contract["rendering"] = {
            "status": "RENDERABLE",
            "reason": "PARTIAL_DATA_RETAINED",
        }
        with tempfile.TemporaryDirectory() as directory:
            result = render_contract_views(
                contract, Path(directory), allow_current_observation=True
            )
            self.assertEqual(result["htmlStatus"], "AVAILABLE")
            self.assertTrue(Path(result["datedHtml"]).exists())


if __name__ == "__main__":
    unittest.main()
