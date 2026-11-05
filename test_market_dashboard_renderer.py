import re
import tempfile
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
    validate_rendered_files,
    validate_rendered_outputs,
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
                {"name": "KOSPI", "close": 3000.12, "changePoints": 12.3, "changeRate": 0.41, "marketStatus": "CLOSE", "session": "POST_MARKET_SNAPSHOT", "sourceTime": "2026-10-29T20:15:00+09:00"},
                {"name": "KOSDAQ", "close": 900.34, "changePoints": -3.2, "changeRate": -0.35, "marketStatus": "CLOSE", "session": "POST_MARKET_SNAPSHOT", "sourceTime": "2026-10-29T20:15:00+09:00"},
            ],
            "industries": [{"name": "반도체와반도체장비", "memberCount": 10, "advancers": 8, "avgReturn1D": 2.1, "advancePct": 80.0}],
            "themes": [],
            "leaders": [{"code": "000660", "name": "SK하이닉스"}],
            "v2": {
                "tradingDate": "2026-10-29",
                "sourceCommit": "abc123",
                "sourceQualityStatus": "SOURCE_OBSERVED",
                "history": {"uniqueTradingDates": ["2026-10-28", "2026-10-29"]},
                "industries": [{"id": "i1", "name": "반도체와반도체장비", "v1Status": "WATCH", "v2Status": "ACTIVE", "score": 91.2, "selectionReason": "상대강도", "persistenceDays": 4, "leaderCandidates": [{"code": "000660", "name": "SK하이닉스", "changeRate": 3.77}], "leaderEvidenceStatus": "AVAILABLE", "sourceMetricsStatus": "AVAILABLE", "sourceMetrics": {"advancers": 8, "advancePct": 80.0, "medianReturn1D": 2.2, "relativeStrengthVsMarket": 4.5, "tradingValue": {"total": 1000000000}}, "historySummary": {"changeClass": "STRENGTHENING", "scoreDelta": 2.1, "availableTradingDays": 4}}],
                "themes": [{"id": "t1", "name": "S7", "v1Status": "WATCH", "v2Status": "WATCH", "score": 88.4, "selectionReason": "지속성", "persistenceDays": 3, "leaderCandidates": [{"code": "005930", "name": "삼성전자", "changeRate": 1.2}], "leaderEvidenceStatus": "AVAILABLE_UNVERIFIED_SOURCE_COMMIT", "sourceMetricsStatus": "AVAILABLE_UNVERIFIED_SOURCE_COMMIT", "sourceMetrics": {"advancers": 5, "advancePct": 60.0, "medianReturn1D": 1.1, "relativeStrengthVsMarket": 2.2, "tradingValue": {"total": 2000000000}}, "historySummary": {"changeClass": "PERSISTING", "scoreDelta": 0, "availableTradingDays": 3}}],
                "history": {"status": "AVAILABLE", "recentTradingDates": ["2026-10-25", "2026-10-26", "2026-10-27", "2026-10-28"], "availableTradingDays": 4, "sameDayDeduplicated": True, "uniqueTradingDates": ["2026-10-25", "2026-10-26", "2026-10-27", "2026-10-28"]},
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
            "rendering": {"status": "RENDERABLE", "reason": "CONFIRMED_CONTRACT"},
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

    def test_html_and_markdown_share_user_values_without_internal_traces(self):
        report = report_fixture()
        html = render_dashboard_html(report)
        markdown = render_dashboard_markdown(report)
        html_text = html_lib.unescape(html)
        for marker in ("2026-10-29 12:10 KST", "79.69%", "삼성전자", "005930", "삼성E&A", "028050", "59,800원", "확인 대기"):
            self.assertIn(marker, html_text)
            self.assertIn(marker, markdown)
        for marker in ("CONTRACT_TRACE", "V2_DATA_TRACE", "TEMPLATE_TRACE", "historyDates", "sourceSha256", "htmlAllowed", "contractHtmlAllowed", "HTML_BLOCKED", "AVAILABLE_UNVERIFIED_SOURCE_COMMIT"):
            self.assertNotIn(marker, html_text)
            self.assertNotIn(marker, markdown)
        for output in (html_text, markdown):
            self.assertNotIn(" V1 ", output)
            self.assertNotIn(" V2 ", output)
        self.assertNotIn("2026년 10월 28일 · 11:08 KST", html_text)
        self.assertNotIn("과거 예시 데이터", html_text)
        self.assertEqual(["part1", "part2", "part3"], re.findall(r'<section[^>]+id="(part\d)"', html))
        self.assertIn("## 1. 시장 대시보드", markdown)
        self.assertIn("## 2. 주도 업종·테마", markdown)
        self.assertIn("## 3. 관심종목·키움 후보", markdown)
        self.assertIn("| 지수 | 현재값 | 전일 대비 | 등락률 |", markdown)
        self.assertIn("| 순위 | 업종 | 상대강도 | 상승 비율 | 대표 주도주 | 상태 |", markdown)
        self.assertNotIn("\n-\n", markdown)
        self.assertNotIn("1)\n", markdown)
        self.assertNotIn("2)\n", markdown)
        self.assertIn("장후 관측", html_text)
        self.assertNotIn("정규장 장중 관측", html_text)
        self.assertNotIn("2,205/2,767 산술 검증", html_text)
        self.assertEqual(validate_rendered_outputs(markdown, html, build_dashboard_model(report)), [])

    def test_market_snapshot_rankings_and_investor_flow_are_rendered(self):
        report = report_fixture()
        report["reportContract"]["marketSnapshot"] = {
            "status": "OK",
            "rankings": {"status": "OK", "items": {
                "gainers": {"status": "OK", "rows": [{"code": "005930", "name": "삼성전자", "changeRate": 4.2}]},
            }},
            "investorFlow": {"status": "OK", "periods": {"DAY": [
                {"investorType": "FOREIGNER", "status": "OK", "buy": [{"code": "000660", "name": "SK하이닉스", "amount": 123456}], "sell": []},
            ]}},
        }
        html = html_lib.unescape(render_dashboard_html(report))
        markdown = render_dashboard_markdown(report)
        self.assertNotIn("시장 거래대금", html)
        self.assertNotIn("시장 거래대금", markdown)
        for text in (html, markdown):
            self.assertIn("현재 스냅샷 순위·수급", text)
            self.assertIn("삼성전자", text)
            self.assertIn("SK하이닉스", text)
            self.assertIn("123,456", text)
        self.assertEqual(validate_rendered_outputs(markdown, html, build_dashboard_model(report)), [])

    def test_ambiguous_zero_investor_amount_is_not_rendered_as_zero(self):
        report = report_fixture()
        report["reportContract"]["marketSnapshot"] = {
            "status": "OK", "rankings": {"items": {}},
            "investorFlow": {"periods": {"DAY": [{
                "investorType": "FOREIGNER",
                "buy": [{"name": "삼성전자", "code": "005930", "amount": 0}],
                "sell": [],
            }]}}
        }
        html = html_lib.unescape(render_dashboard_html(report))
        markdown = render_dashboard_markdown(report)
        self.assertNotIn(">0</td>", html)
        self.assertIn("확인 불가", html)
        self.assertIn("확인 불가", markdown)

    def test_saved_output_validation_detects_missing_dynamic_information(self):
        report = report_fixture()
        report["reportContract"]["v2"]["sourceCommitStatus"] = "VERIFIED"
        markdown = render_dashboard_markdown(report)
        rendered = render_dashboard_html(report)
        broken = rendered.replace("SK하이닉스", "")
        issues = validate_rendered_outputs(markdown, broken, build_dashboard_model(report))
        self.assertTrue(any(issue.startswith("PART2_V2_LEADER_MISSING") for issue in issues), issues)

    def test_saved_output_validation_requires_values_in_their_own_section(self):
        report = report_fixture()
        report["reportContract"]["v2"]["sourceCommitStatus"] = "VERIFIED"
        markdown = render_dashboard_markdown(report)
        rendered = render_dashboard_html(report)
        broken = re.sub(
            r'(<section[^>]+id="part2"[^>]*>[\s\S]*?</section>)',
            lambda match: match.group(1).replace("반도체와반도체장비", ""),
            rendered,
            count=1,
        )
        issues = validate_rendered_outputs(markdown, broken, build_dashboard_model(report))
        self.assertTrue(any(issue.startswith("PART2_V2_MISSING") for issue in issues), issues)

    def test_v2_rows_beyond_visible_card_limit_do_not_block_html(self):
        report = report_fixture()
        hidden = {
            "id": "hidden-theme",
            "name": "표시범위 밖 테마",
            "v1Status": "WATCH",
            "v2Status": "WATCH",
            "score": 77.7,
            "leaderCandidates": [{"code": "999999", "name": "표시범위 밖 주도주"}],
            "persistenceDays": 2,
            "historySummary": {"changeClass": "PERSISTING"},
        }
        report["reportContract"]["v2"]["themes"] = [
            report["reportContract"]["v2"]["themes"][0]
        ] + [dict(hidden, id=f"hidden-theme-{index}", name=f"표시범위 밖 테마 {index}") for index in range(6)]
        html = render_dashboard_html(report)
        markdown = render_dashboard_markdown(report)
        self.assertNotIn("표시범위 밖 테마 5", html_lib.unescape(html))
        self.assertNotIn("표시범위 밖 테마 5", markdown)
        self.assertEqual(validate_rendered_outputs(markdown, html, build_dashboard_model(report)), [])

    def test_unavailable_v2_theme_is_rendered_without_blocking_html(self):
        report = report_fixture()
        report["reportContract"]["v2"]["themes"] = []
        html = render_dashboard_html(report)
        markdown = render_dashboard_markdown(report)
        self.assertIn("검증 중인 후보 판정 보류 — 원천 검증 대기", html_lib.unescape(html))
        self.assertIn("검증 중인 후보 판정 보류 — 원천 검증 대기", markdown)
        self.assertEqual(validate_rendered_outputs(markdown, html, build_dashboard_model(report)), [])

    def test_completed_markdown_and_html_files_are_reopened_before_delivery(self):
        report = report_fixture()
        model = build_dashboard_model(report)
        html = render_dashboard_html(report)
        markdown = render_dashboard_markdown(report)
        with tempfile.TemporaryDirectory() as directory:
            markdown_path = Path(directory) / "report.md"
            html_path = Path(directory) / "report.html"
            markdown_path.write_text(markdown, encoding="utf-8")
            html_path.write_text(html, encoding="utf-8")
            self.assertEqual(validate_rendered_files(markdown_path, html_path, model), [])

    def test_session_summary_preserves_full_session_state(self):
        post = [{"name": "KOSPI", "marketStatus": "CLOSE", "session": "POST_MARKET_SNAPSHOT"}, {"name": "KOSDAQ", "marketStatus": "CLOSE", "session": "POST_MARKET_SNAPSHOT"}]
        intraday = [{"name": "KOSPI", "marketStatus": "OPEN", "session": "INTRADAY_SESSION"}, {"name": "KOSDAQ", "marketStatus": "OPEN", "session": "INTRADAY_SESSION"}]
        mixed = [post[0], intraday[1]]
        unknown = [{"name": "KOSPI"}, {"name": "KOSDAQ"}]
        self.assertEqual(build_dashboard_model({"reportContract": {"fullMarket": {"totalCount": 1, "breadth": {"advancers": 0, "decliners": 0, "unchanged": 1}}, "indices": post}})["indexSessionLabel"], "장후 관측")
        self.assertEqual(build_dashboard_model({"reportContract": {"fullMarket": {"totalCount": 1, "breadth": {"advancers": 0, "decliners": 0, "unchanged": 1}}, "indices": intraday}})["indexSessionLabel"], "장중 관측")
        self.assertEqual(build_dashboard_model({"reportContract": {"fullMarket": {"totalCount": 1, "breadth": {"advancers": 0, "decliners": 0, "unchanged": 1}}, "indices": mixed}})["indexSessionLabel"], "시장별 세션 상이")
        self.assertEqual(build_dashboard_model({"reportContract": {"fullMarket": {"totalCount": 1, "breadth": {"advancers": 0, "decliners": 0, "unchanged": 1}}, "indices": unknown}})["indexSessionLabel"], "세션 확인 대기")

    def test_preopen_report_keeps_prior_session_reference_in_both_outputs(self):
        report = report_fixture()
        report["reportContract"]["indices"] = [
            {"name": "KOSPI", "close": 6000.0, "changePoints": 0.0, "changeRate": 0.0,
             "marketStatus": "PREOPEN", "session": "UNKNOWN", "sourceTime": "2026-10-30T08:01:46+09:00"},
            {"name": "KOSDAQ", "close": 760.0, "changePoints": 0.0, "changeRate": 0.0,
             "marketStatus": "PREOPEN", "session": "UNKNOWN", "sourceTime": "2026-10-30T08:01:46+09:00"},
        ]
        report["reportContract"]["sessionContext"] = {
            "mode": "PREOPEN", "status": "AVAILABLE",
            "observation": {"tradingDate": "2026-10-30", "status": "AVAILABLE"},
            "previousTradingDay": {
                "status": "AVAILABLE", "tradingDate": "2026-10-29",
                "verificationStatus": "VERIFIED_PRIOR_SESSION_SOURCE_OBSERVED",
                "indices": [
                    {"name": "KOSPI", "close": 5937.66, "changePoints": -74.78, "changeRate": -1.24,
                     "sourceTime": "2026-10-29T20:15:00+09:00"},
                    {"name": "KOSDAQ", "close": 753.23, "changePoints": -18.05, "changeRate": -2.34,
                     "sourceTime": "2026-10-29T20:15:00+09:00"},
                ],
                "fullMarket": {"totalCount": 2767, "breadth": {"advancers": 717, "decliners": 1813, "unchanged": 237}},
                "v1V2": {"tradingDate": "2026-10-29", "status": "PRIOR_SESSION_REFERENCE"},
            },
        }
        model = build_dashboard_model(report)
        self.assertEqual(model["indexSessionLabel"], "장전 관측")
        markdown = render_dashboard_markdown(report)
        rendered_html = render_dashboard_html(report)
        html_text = html_lib.unescape(rendered_html)
        for token in ("장전 관측", "직전 거래일 참고", "2026-10-29", "5937.66", "-1.24", "753.23", "당일 장전 자료와 혼합하지 않음"):
            self.assertIn(token, markdown)
            self.assertIn(token, html_text)
        self.assertEqual(validate_rendered_outputs(markdown, rendered_html, model), [])

    def test_breadth_arithmetic_uses_contract_counts_for_other_sessions(self):
        report = report_fixture()
        report["reportContract"]["fullMarket"]["breadth"] = {
            "advancers": 705, "decliners": 1833, "unchanged": 229,
        }
        model = build_dashboard_model(report)
        self.assertEqual(model["fullMarket"]["declineRatio"], 66.25)
        html = render_dashboard_html(report)
        self.assertIn("1,833", html_lib.unescape(html))
        self.assertNotIn("2,205/2,767", html_lib.unescape(html))

    def test_bad_1813_sample_is_recomputed_and_not_reused_as_a_fixed_ratio(self):
        report = report_fixture()
        report["reportContract"]["fullMarket"]["breadth"] = {
            "advancers": 717, "decliners": 1813, "unchanged": 237,
        }
        html = html_lib.unescape(render_dashboard_html(report))
        self.assertIn("65.52%", html)
        self.assertNotIn("79.69%", html)

    def test_html_classes_are_from_approved_structure(self):
        html = render_dashboard_html(report_fixture())
        required = ("wrap", "topbar", "nav", "hero", "section", "card", "index-card", "kpis", "breadth", "industry-cards", "stock-list")
        for class_name in required:
            self.assertRegex(html, rf'class="[^"]*\b{class_name}\b[^"]*"')
            self.assertIn(f".{class_name}", load_approved_template()["css"])

    def test_dynamic_timestamp_has_mobile_wrap_guard(self):
        html = render_dashboard_html(report_fixture())
        self.assertIn(".kpi-value{overflow-wrap:anywhere;word-break:break-word}", html)

    def test_model_mismatch_is_detected_before_render_delivery(self):
        left = report_fixture()
        right = report_fixture()
        right["reportContract"]["kiwoomCandidates"]["stocks"][0]["price"] = 59900
        self.assertIn("kiwoom", validate_renderer_models(left, right))

    def test_partial_contract_renders_without_html_gate(self):
        report = report_fixture()
        report["reportContract"]["status"] = "PARTIAL"
        report["reportContract"]["tradingDateStatus"] = "SOURCE_OBSERVED"
        report["reportContract"]["rendering"] = {
            "status": "RENDERABLE", "reason": "PARTIAL_DATA_RETAINED"
        }
        html = html_lib.unescape(render_dashboard_html(report))
        markdown = render_dashboard_markdown(report)
        self.assertIn("부분 자료를 포함한 관측 보고서", html)
        self.assertIn("부분 자료를 포함한 관측 보고서", markdown)
        self.assertNotIn("HTML_BLOCKED", html)
        self.assertNotIn("htmlAllowed", markdown)

    def test_registered_universe_values_are_rendered_in_both_views(self):
        report = report_fixture()
        report["reportContract"]["registeredUniverse"] = {
            "status": "AVAILABLE", "scope": "REGISTERED_UNIVERSE", "count": 1,
            "fallbackUsed": False, "items": [{
                "itemCode": "005930", "itemName": "삼성전자",
                "current": {"price": 70000, "changeRate": 1.2, "status": "AVAILABLE"},
                "technicals": {"ma20": 68000, "ma60": 65000, "status": "AVAILABLE"},
                "state": {"status": "AVAILABLE", "trend": "DOWNTREND"},
                "status": "AVAILABLE",
            }],
        }
        html = html_lib.unescape(render_dashboard_html(report))
        markdown = render_dashboard_markdown(report)
        for output in (html, markdown):
            self.assertIn("70,000원", output)
            self.assertIn("68,000원", output)
            self.assertIn("65,000원", output)
            self.assertIn("하락 추세" if "하락 추세" in output else "DOWNTREND", output)

    def test_partial_theme_is_visible_but_unavailable_groups_are_not(self):
        report = report_fixture()
        report["reportContract"]["relativeStrengthTop5"] = {
            "status": "AVAILABLE", "tradingDate": "20261029",
            "themes": {"top5": [{
                "rank": 1, "id": "t-partial", "name": "부분 테마",
                "relativeStrength": 1.5, "breadthPct": 60.0,
                "memberCount": 4, "dataQuality": "PARTIAL",
                "leaderCandidates": [],
            }]},
            "industries": {"top5": []},
        }
        html = html_lib.unescape(render_dashboard_html(report))
        markdown = render_dashboard_markdown(report)
        self.assertIn("부분 테마", html)
        self.assertIn("부분 테마", markdown)

    def test_baseline_stale_current_relative_group_remains_visible(self):
        report = report_fixture()
        report["reportContract"]["relativeStrengthTop5"] = {
            "status": "AVAILABLE", "tradingDate": "20261029",
            "industries": {"top5": [{
                "rank": 1, "id": "i-stale", "name": "당일 업종",
                "relativeStrength": 1.2, "breadthPct": 55.0,
                "dataQuality": "BASELINE_STALE", "leaderCandidates": [],
            }]},
            "themes": {"top5": []},
        }
        html = html_lib.unescape(render_dashboard_html(report))
        markdown = render_dashboard_markdown(report)
        self.assertIn("당일 업종", html)
        self.assertIn("당일 업종", markdown)

    def test_v1_operating_groups_are_separate_limited_and_exclude_removed(self):
        report = report_fixture()
        report["reportContract"]["v1"] = {
            "industries": [
                {"id": str(i), "name": f"운영 업종 {i}", "status": "ACTIVE"}
                for i in range(6)
            ] + [{"id": "removed", "name": "제외 업종", "status": "REMOVED"}],
            "themes": [
                {"id": str(i), "name": f"운영 테마 {i}", "status": "DISCOVERED"}
                for i in range(6)
            ],
        }
        html = html_lib.unescape(render_dashboard_html(report))
        markdown = render_dashboard_markdown(report)
        for output in (html, markdown):
            self.assertIn("운영 업종 0", output)
            self.assertIn("운영 테마 0", output)
            self.assertNotIn("제외 업종", output)
        model = build_dashboard_model(report)
        self.assertEqual(len(model["v1Industries"]), 5)
        self.assertEqual(len(model["v1Themes"]), 5)

    def test_research_analysis_is_user_section_and_missing_research_is_explicit(self):
        report = report_fixture()
        report["reportContract"]["independentAnalysis"]["research"]["analysis"] = {
            "status": "AVAILABLE",
            "asOf": "2026-10-29",
            "items": [{"category": "리서치 집중", "text": "삼성전자에 리서치가 집중되었습니다."}],
        }
        html = html_lib.unescape(render_dashboard_html(report))
        markdown = render_dashboard_markdown(report)
        self.assertIn("네이버 리서치 분석", html)
        self.assertIn("삼성전자에 리서치가 집중되었습니다.", html)
        self.assertIn("삼성전자에 리서치가 집중되었습니다.", markdown)
        report["reportContract"]["independentAnalysis"]["research"].pop("analysis")
        self.assertIn("네이버 리서치 자료 없음", html_lib.unescape(render_dashboard_html(report)))
        self.assertIn("자료 없음", render_dashboard_markdown(report))

    def test_missing_kospi_and_kosdaq_are_reported_individually(self):
        report = report_fixture()
        report["reportContract"]["indices"] = [{"name": "KOSPI", "close": 3000.0}]
        html = html_lib.unescape(render_dashboard_html(report))
        markdown = render_dashboard_markdown(report)
        self.assertIn("KOSPI sourceTime 누락", html)
        self.assertIn("KOSDAQ 원천이 계약에 없음", html)
        self.assertIn("| 코스피 (KOSPI) | 확인 불가 |", markdown)
        self.assertIn("| 코스닥 (KOSDAQ) | 확인 불가 |", markdown)

    def test_empty_kiwoom_candidates_are_a_user_facing_no_data_state(self):
        report = report_fixture()
        report["reportContract"]["kiwoomCandidates"] = {"stocks": []}
        markdown = render_dashboard_markdown(report)
        self.assertIn("전일 장마감 후 조건검색 결과 자료 없음", markdown)
        self.assertNotIn("조건검색 실패", markdown)

    def test_index_code_and_korean_name_are_normalized_in_both_outputs(self):
        report = report_fixture()
        report["reportContract"]["indices"] = [
            {"code": "KOSPI", "name": "코스피", "close": 3000.12, "changePoints": 12.3, "changeRate": 0.41,
             "marketStatus": "OPEN", "session": "REGULAR", "sourceTime": "2026-10-29T14:00:00+09:00"},
            {"code": "KOSDAQ", "name": "코스닥", "close": 900.34, "changePoints": -3.2, "changeRate": -0.35,
             "marketStatus": "OPEN", "session": "REGULAR", "sourceTime": "2026-10-29T14:00:00+09:00"},
        ]
        html = html_lib.unescape(render_dashboard_html(report))
        markdown = render_dashboard_markdown(report)
        self.assertEqual(build_dashboard_model(report)["indexSessionLabel"], "장중 관측")
        for output in (html, markdown):
            self.assertIn("KOSPI", output)
            self.assertIn("3,000.12", output)
            self.assertIn("+12.30p", output)
            self.assertIn("+0.41%", output)
            self.assertIn("KOSDAQ", output)
            self.assertIn("900.34", output)
            self.assertIn("-3.20p", output)
            self.assertIn("-0.35%", output)

    def test_index_korean_names_without_code_are_normalized(self):
        report = report_fixture()
        report["reportContract"]["indices"] = [
            {"name": "코스피", "close": 3000.12, "changePoints": 12.3, "changeRate": 0.41,
             "marketStatus": "OPEN", "session": "REGULAR", "sourceTime": "2026-10-29T14:00:00+09:00"},
            {"indexName": "코스닥", "close": 900.34, "changePoints": -3.2, "changeRate": -0.35,
             "marketStatus": "OPEN", "session": "REGULAR", "sourceTime": "2026-10-29T14:00:00+09:00"},
        ]
        html = html_lib.unescape(render_dashboard_html(report))
        markdown = render_dashboard_markdown(report)
        self.assertIn("3,000.12", html)
        self.assertIn("3,000.12", markdown)
        self.assertIn("900.34", html)
        self.assertIn("900.34", markdown)

    def test_unverified_v2_source_never_renders_score_or_leader(self):
        report = report_fixture()
        report["reportContract"]["v2"]["sourceCommitStatus"] = "UNAVAILABLE"
        html = html_lib.unescape(render_dashboard_html(report))
        self.assertIn("검증 중인 후보 판정 보류 — 원천 검증 대기", html)
        self.assertNotIn("91.20", html)
        self.assertNotIn("SK하이닉스(000660)", html)

    def test_samsung_interest_card_is_present_even_when_values_are_unavailable(self):
        report = report_fixture()
        report["reportContract"].pop("registeredUniverse", None)
        html = html_lib.unescape(render_dashboard_html(report))
        markdown = render_dashboard_markdown(report)
        for text in (html, markdown):
            self.assertIn("삼성전자", text)
            self.assertIn("005930", text)

    def test_rendered_views_use_kst_and_hide_lineage_metadata(self):
        report = report_fixture()
        html = html_lib.unescape(render_dashboard_html(report))
        markdown = render_dashboard_markdown(report)
        self.assertIn("2026-10-29 12:10 KST", html)
        self.assertIn("2026-10-29 12:10 KST", markdown)
        for token in ("CONTRACT_TRACE", "V2_DATA_TRACE", "TEMPLATE_TRACE", "historyDates", "sourceCommitStatus", "inputSnapshotSha256"):
            self.assertNotIn(token, html)
            self.assertNotIn(token, markdown)


if __name__ == "__main__":
    unittest.main()
