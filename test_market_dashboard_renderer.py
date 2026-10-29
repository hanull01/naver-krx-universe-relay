import re
import unittest
import html as html_lib
from pathlib import Path

from tools.market_dashboard_renderer import (
    TEMPLATE_DIR,
    TemplateUnavailable,
    build_dashboard_model,
    load_approved_template,
    render_dashboard_html,
    render_dashboard_markdown,
    validate_renderer_models,
)


def report_fixture():
    generated = "2026-10-29T12:10:00+09:00"
    return {
        "generatedAt": generated,
        "writtenAt": generated,
        "asOf": "2026-10-29",
        "reportContract": {
            "schemaVersion": "market-report-contract-v1",
            "status": "AVAILABLE",
            "asOf": "2026-10-29",
            "observedAt": generated,
            "tradingDateStatus": "VERIFIED",
            "sourceQualityStatus": "SOURCE_OBSERVED",
            "tradingDateEvidence": {"tradingDate": "20261029", "status": "VERIFIED"},
            "sourceGenerations": {"market": generated, "v2": generated},
            "fullMarket": {
                "totalCount": 2767,
                "breadth": {"advancers": 400, "decliners": 2205, "unchanged": 162},
                "marketTradingValue": {"KOSPI": {"value": 100}, "KOSDAQ": {"value": 200}},
                "metrics": {
                    "aboveMA20": {"count": 10, "eligibleCount": 100, "pct": 10.0},
                    "aboveMA60": {"count": 20, "eligibleCount": 100, "pct": 20.0},
                    "aboveMA120": {"count": 30, "eligibleCount": 100, "pct": 30.0},
                    "breakout20": {"count": 4, "eligibleCount": 100, "pct": 4.0},
                    "breakout60": {"count": 2, "eligibleCount": 100, "pct": 2.0},
                    "volumeAbove20DayAverage": {"count": 5, "eligibleCount": 100, "pct": 5.0},
                },
            },
            "indices": [
                {"name": "KOSPI", "close": 3000.12, "changePoints": 12.3, "changeRate": 0.41},
                {"name": "KOSDAQ", "close": 900.34, "changePoints": -3.2, "changeRate": -0.35},
            ],
            "industries": [{"name": "반도체와반도체장비", "memberCount": 10, "advancers": 8, "avgReturn1D": 2.1, "advancePct": 80.0}],
            "themes": [],
            "leaders": [{"code": "000660", "name": "SK하이닉스"}],
            "v2": {
                "tradingDate": "2026-10-29",
                "sourceCommit": "abc123",
                "sourceQualityStatus": "SOURCE_OBSERVED",
                "history": {"uniqueTradingDates": ["2026-10-28", "2026-10-29"]},
                "industries": [{"id": "i1", "name": "반도체와반도체장비", "v2Status": "ACTIVE", "score": 91.2, "selectionReason": "상대강도"}],
                "themes": [{"id": "t1", "name": "S7", "v2Status": "WATCH", "score": 88.4, "selectionReason": "지속성"}],
            },
            "independentAnalysis": {"research": {"status": "AVAILABLE"}},
            "kiwoomCandidates": {
                "stocks": [
                    {"itemCode": "028050", "itemName": "삼성E&A", "price": 59800, "changeRate": 0.0, "conditionStatus": "확인 대기"},
                    {"itemCode": "126340", "itemName": "비나텍", "price": 103200, "changeRate": -13.64, "conditionStatus": "미충족"},
                    {"itemCode": "207940", "itemName": "삼성바이오로직스", "price": 1452000, "changeRate": -0.95, "conditionStatus": "확인 대기"},
                    {"itemCode": "300120", "itemName": "라온피플", "price": 12000, "changeRate": 1.2, "conditionStatus": "충족"},
                ]
            },
            "rendering": {"htmlAllowed": True, "reason": "CONTRACT_VERIFIED"},
        },
    }


class MarketDashboardRendererTests(unittest.TestCase):
    def test_approved_template_hashes_and_layout_are_frozen(self):
        assets = load_approved_template()
        self.assertEqual(assets["manifest"]["version"], "A-1.1.0")
        self.assertEqual(assets["manifest"]["source_sha256"], "cba54525bbabe1f7a2b100d9696181bac1e49b76e772301007abfdcdb5fef0c3")
        self.assertEqual(assets["manifest"]["css_sha256"], "bbe55c39024cbf4705baece7d9ba135c9723dcce182bc2af6652b3275e8ea941")
        self.assertTrue((TEMPLATE_DIR / "market-dashboard-A-v1.1.reference.html").exists())

    def test_decline_ratio_is_arithmetic_not_a_fixed_display_value(self):
        model = build_dashboard_model(report_fixture())
        self.assertEqual(model["fullMarket"]["declineRatio"], 79.69)
        market = model["fullMarket"]
        self.assertEqual(market["advancers"] + market["decliners"] + market["unchanged"], market["total"])

    def test_html_and_markdown_share_traces_values_and_three_parts(self):
        report = report_fixture()
        html = render_dashboard_html(report)
        markdown = render_dashboard_markdown(report)
        html_text = html_lib.unescape(html)
        for marker in ("CONTRACT_TRACE", "V2_DATA_TRACE", "TEMPLATE_TRACE", "2026-10-29T12:10:00+09:00", "79.69%", "SK하이닉스", "000660", "삼성E&A", "028050", "59,800원", "확인 대기"):
            self.assertIn(marker, html_text)
            self.assertIn(marker, markdown)
        self.assertNotIn("2026년 10월 28일 · 11:08 KST", html_text)
        self.assertNotIn("과거 예시 데이터", html_text)
        self.assertEqual(["part1", "part2", "part3"], re.findall(r'<section[^>]+id="(part\d)"', html))
        self.assertIn("## 1. 시장 대시보드", markdown)
        self.assertIn("## 2. 업종·테마·주도주·리서치", markdown)
        self.assertIn("## 3. 키움 후보 추적·조건별 대응", markdown)

    def test_html_classes_are_from_approved_structure(self):
        html = render_dashboard_html(report_fixture())
        required = ("wrap", "topbar", "nav", "hero", "section", "card", "index-card", "kpis", "breadth", "industry-cards", "stock-list", "decision-grid")
        for class_name in required:
            self.assertRegex(html, rf'class="[^"]*\b{class_name}\b[^"]*"')
            self.assertIn(f".{class_name}", load_approved_template()["css"])

    def test_model_mismatch_is_detected_before_render_delivery(self):
        left = report_fixture()
        right = report_fixture()
        right["reportContract"]["kiwoomCandidates"]["stocks"][0]["price"] = 59900
        self.assertIn("kiwoom", validate_renderer_models(left, right))

    def test_contract_not_allowed_fails_closed_without_alternative_html(self):
        report = report_fixture()
        report["reportContract"]["rendering"]["htmlAllowed"] = False
        with self.assertRaisesRegex(TemplateUnavailable, "CONTRACT_HTML_NOT_ALLOWED"):
            render_dashboard_html(report)


if __name__ == "__main__":
    unittest.main()
