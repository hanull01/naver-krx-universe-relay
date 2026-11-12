import hashlib
import html as html_lib
import json
import tempfile
import unittest
from pathlib import Path

from test_market_dashboard_renderer import report_fixture
from tools.market_dashboard_renderer import TemplateUnavailable, render_contract_views
from tools.render_current_observation import render_current_observation


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
            self.assertEqual(result["markdownSha256"], hashlib.sha256(markdown_text.encode("utf-8")).hexdigest())
            self.assertIn(result["contractId"], markdown_text)
            self.assertIn(f'name="report-contract-id"', html_text)
            self.assertIn(f'content="{result["contractId"]}"', html_text)
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

    def test_current_observation_and_latest_views_share_one_contract_and_replace_stale_markdown(self):
        contract = report_fixture()["reportContract"]
        contract["asOf"] = contract["observationDate"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_path = root / "latest-report-contract.json"
            contract_path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
            latest_dir = root / "latest"
            latest_dir.mkdir()
            (latest_dir / "latest.md").write_text("STALE_MARKDOWN", encoding="utf-8")

            latest = render_contract_views(
                contract, latest_dir, allow_current_observation=True
            )
            observation = render_current_observation(
                contract_path,
                root / "current-observation" / "run-1",
                run_id="run-1",
                head_sha="a" * 40,
            )

            self.assertEqual(
                Path(latest["markdown"]).read_bytes(),
                root.joinpath("current-observation", "run-1", "report.md").read_bytes(),
            )
            self.assertEqual(
                Path(latest["html"]).read_bytes(),
                root.joinpath("current-observation", "run-1", "report.html").read_bytes(),
            )
            self.assertEqual(
                Path(latest["markdown"]).read_bytes(),
                Path(latest["datedMarkdown"]).read_bytes(),
            )
            self.assertNotIn(
                "STALE_MARKDOWN", Path(latest["markdown"]).read_text(encoding="utf-8")
            )
            self.assertEqual(observation["status"], "SUCCESS")

    def test_failed_contract_view_does_not_consume_or_replace_stale_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            latest = output / "latest.md"
            latest.write_text("STALE_MARKDOWN", encoding="utf-8")
            with self.assertRaises(TemplateUnavailable):
                render_contract_views({}, output)
            self.assertEqual(latest.read_text(encoding="utf-8"), "STALE_MARKDOWN")


if __name__ == "__main__":
    unittest.main()
