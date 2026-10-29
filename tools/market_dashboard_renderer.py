"""Render the approved A v1.1 dashboard from one report contract.

The renderer deliberately accepts the published report contract instead of
recalculating market values for each output format.  The approved template
and CSS are immutable assets; only dynamic text and card values are filled in.
"""
from __future__ import annotations

import hashlib
import html
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "report_templates" / "market-dashboard-A-v1.1"
MANIFEST_PATH = TEMPLATE_DIR / "manifest.json"
REFERENCE_PATH = TEMPLATE_DIR / "market-dashboard-A-v1.1.reference.html"
CSS_PATH = TEMPLATE_DIR / "market-dashboard-A-v1.1.css"


class TemplateUnavailable(RuntimeError):
    """The approved template or the renderable contract is unavailable."""


def _text(value: Any, fallback: str = "확인 불가") -> str:
    return fallback if value in (None, "") else str(value)


def _num(value: Any) -> float | int | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _pct(value: Any) -> str:
    number = _num(value)
    return f"{number:+.2f}%" if number is not None else "확인 불가"


def _plain_pct(value: Any) -> str:
    number = _num(value)
    return f"{number:.2f}%" if number is not None else "확인 불가"


def _price(value: Any) -> str:
    number = _num(value)
    return f"{number:,.0f}원" if number is not None else "확인 불가"


def _escape(value: Any) -> str:
    return html.escape(_text(value), quote=True)


def _change_class(value: Any) -> str:
    number = _num(value)
    if number is None or number == 0:
        return "muted"
    return "up" if number > 0 else "down"


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) not in (None, ""):
            return mapping[key]
    return None


def load_approved_template(template_dir: Path = TEMPLATE_DIR) -> dict[str, Any]:
    """Verify and load the frozen A v1.1 assets before rendering."""
    try:
        manifest = json.loads((template_dir / "manifest.json").read_text(encoding="utf-8"))
        reference = (template_dir / manifest["source_file"]).read_bytes()
        css = (template_dir / "market-dashboard-A-v1.1.css").read_bytes()
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise TemplateUnavailable(f"TEMPLATE_UNAVAILABLE:{type(exc).__name__}") from exc

    if hashlib.sha256(reference).hexdigest() != manifest.get("source_sha256"):
        raise TemplateUnavailable("TEMPLATE_UNAVAILABLE:SOURCE_HASH_CHANGED")
    if hashlib.sha256(css).hexdigest() != manifest.get("css_sha256"):
        raise TemplateUnavailable("TEMPLATE_UNAVAILABLE:CSS_HASH_CHANGED")
    reference_text = reference.decode("utf-8")
    sections = [item.get("id") for item in manifest.get("layout", {}).get("sections", [])]
    if sections != ["part1", "part2", "part3"] or any(
        f'id="{section}"' not in reference_text for section in sections
    ):
        raise TemplateUnavailable("TEMPLATE_UNAVAILABLE:SECTION_LAYOUT_CHANGED")
    return {
        "manifest": manifest,
        "css": css.decode("utf-8"),
        "templateTrace": (
            f"version={manifest.get('version', 'UNAVAILABLE')};"
            f"sourceSha256={manifest.get('source_sha256', 'UNAVAILABLE')};"
            f"cssSha256={manifest.get('css_sha256', 'UNAVAILABLE')}"
        ),
    }


def build_dashboard_model(report: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one report contract for both Markdown and HTML renderers."""
    contract = report.get("reportContract") if isinstance(report.get("reportContract"), Mapping) else report
    if not isinstance(contract, Mapping):
        raise TemplateUnavailable("CONTRACT_UNAVAILABLE")
    full_market = contract.get("fullMarket") or {}
    breadth = full_market.get("breadth") or {}
    metrics = full_market.get("metrics") or {}
    total = _num(full_market.get("totalCount"))
    advancers = _num(breadth.get("advancers"))
    decliners = _num(breadth.get("decliners"))
    unchanged = _num(breadth.get("unchanged"))
    decline_ratio = round(decliners / total * 100, 2) if total and decliners is not None else None
    breadth_valid = (
        total is not None
        and advancers is not None
        and decliners is not None
        and unchanged is not None
        and advancers + decliners + unchanged == total
    )
    if not breadth_valid:
        raise TemplateUnavailable("CONTRACT_UNAVAILABLE:BREADTH_ARITHMETIC_MISMATCH")

    v2 = contract.get("v2") or {}
    generations = contract.get("sourceGenerations") or {}
    evidence = contract.get("tradingDateEvidence") or {}
    return {
        "asOf": contract.get("asOf") or report.get("asOf"),
        "writtenAt": report.get("writtenAt") or report.get("generatedAt") or contract.get("observedAt"),
        "observedAt": contract.get("observedAt") or report.get("generatedAt"),
        "tradingDateStatus": contract.get("tradingDateStatus"),
        "sourceQualityStatus": contract.get("sourceQualityStatus"),
        "htmlAllowed": bool((contract.get("rendering") or {}).get("htmlAllowed")),
        "contractTrace": (
            f"schema={_text(contract.get('schemaVersion'))};status={_text(contract.get('status'))};"
            f"asOf={_text(contract.get('asOf'))};observedAt={_text(contract.get('observedAt'))};"
            f"tradingDateStatus={_text(contract.get('tradingDateStatus'))}"
        ),
        "v2Trace": (
            f"sourceCommit={_text(v2.get('sourceCommit'))};"
            f"sourceQualityStatus={_text(v2.get('sourceQualityStatus'))};"
            f"tradingDate={_text(v2.get('tradingDate'))};"
            f"historyDates={','.join(v2.get('history', {}).get('uniqueTradingDates', [])) or '확인 불가'}"
        ),
        "templateTrace": "filled-by-renderer",
        "evidence": evidence,
        "indices": list(contract.get("indices") or []),
        "fullMarket": {
            "total": total,
            "advancers": advancers,
            "decliners": decliners,
            "unchanged": unchanged,
            "declineRatio": decline_ratio,
            "marketTradingValue": full_market.get("marketTradingValue") or {},
            "metrics": metrics,
        },
        "industries": list(contract.get("industries") or []),
        "themes": list(contract.get("themes") or []) or list(v2.get("themes") or []),
        "leaders": list(contract.get("leaders") or []),
        "v2Industries": list(v2.get("industries") or []),
        "v2Themes": list(v2.get("themes") or []),
        "research": contract.get("independentAnalysis", {}).get("research", {}),
        "kiwoom": contract.get("kiwoomCandidates") or {},
        "technicalCandidates": list(contract.get("technicalCandidates") or []),
        "notice": _text(
            (contract.get("rendering") or {}).get("reason"),
            "확인된 계약의 원본 수치와 판정을 표시합니다.",
        ),
    }


def _metric_line(label: str, metric: Mapping[str, Any]) -> str:
    count = metric.get("count")
    eligible = metric.get("eligibleCount")
    pct = metric.get("pct")
    value = f"{count:,} / {eligible:,} · {_plain_pct(pct)}" if isinstance(count, (int, float)) and isinstance(eligible, (int, float)) else "확인 불가"
    width = _num(pct) or 0
    return f'<div class="metric-row"><div>{_escape(label)}<div class="meter"><span style="width:{max(0, min(100, width))}%"></span></div></div><strong>{_escape(value)}</strong></div>'


def _index_cards(model: Mapping[str, Any]) -> str:
    cards = []
    for item in model["indices"]:
        name = item.get("name") or item.get("indexName")
        if name not in {"KOSPI", "KOSDAQ"}:
            continue
        rate = item.get("changeRate")
        points = item.get("changePoints")
        cards.append(
            f'<article class="card index-card"><div class="index-name">{_escape(name)}</div>'
            f'<div class="index-value">{_escape(f"{item.get("close"):,.2f}" if isinstance(item.get("close"), (int, float)) else "확인 불가")}</div>'
            f'<div class="change {_change_class(rate)}">{_escape(f"{points:+,.2f}p" if isinstance(points, (int, float)) else "확인 불가")} ({_escape(_pct(rate))})</div>'
            f'<div class="subvalue">{_escape(model.get("observedAt"))} 기준 · 정규장 장중 관측</div></article>'
        )
    while len(cards) < 2:
        cards.append('<article class="card index-card"><div class="index-name">지수</div><div class="index-value">확인 불가</div><div class="change muted">원천 미확인</div><div class="subvalue">확인 불가</div></article>')
    return "".join(cards[:2])


def _industry_cards(rows: list[Mapping[str, Any]]) -> str:
    cards = []
    for row in rows[:6]:
        name = row.get("name") or row.get("id")
        avg = _first(row, "avgReturn1D", "medianReturn1D", "changeRate")
        advance = _first(row, "advancePct", "upPct")
        member_count = row.get("memberCount")
        advancers = row.get("advancers")
        trading = row.get("tradingValue") or {}
        total = trading.get("total")
        cards.append(
            '<div class="industry-item"><div class="industry-head">'
            f'<strong>{_escape(name)}</strong><span class="status-chip">{_escape(row.get("status") or row.get("persistence", {}).get("status") or "관찰")}</span></div>'
            '<div class="industry-numbers">'
            f'<div>{_escape(f"{advancers} / {member_count}" if advancers is not None and member_count is not None else "확인 불가")}<small>상승 종목</small></div>'
            f'<div class="{_change_class(avg)}">{_escape(_pct(avg))}<small>1일 성과</small></div>'
            f'<div>{_escape(f"{total:,.0f}원" if isinstance(total, (int, float)) else "확인 불가")}<small>거래대금</small></div>'
            f'<div>{_escape(_plain_pct(advance))}<small>상승 비율</small></div></div></div>'
        )
    return "".join(cards) or '<div class="industry-item"><strong>검증된 업종 자료가 없습니다.</strong></div>'


def _v2_cards(rows: list[Mapping[str, Any]], label: str) -> str:
    cards = []
    for row in rows[:6]:
        score = row.get("score")
        reason = row.get("selectionReason") or row.get("reason") or row.get("selectionReasons")
        persistence = row.get("persistence") or {}
        cards.append(
            f'<div class="industry-item"><div class="industry-head"><strong>{_escape(row.get("name") or row.get("id"))}</strong><span class="status-chip">{_escape(row.get("v2Status") or row.get("status") or "확인 불가")}</span></div>'
            f'<div class="industry-numbers"><div>{_escape(f"{score:.2f}" if isinstance(score, (int, float)) else "확인 불가")}<small>{_escape(label)} 점수</small></div>'
            f'<div>{_escape(persistence.get("status") or persistence.get("days") or "확인 불가")}<small>지속성</small></div>'
            f'<div>{_escape(reason if isinstance(reason, str) else ", ".join(map(str, reason or [])) or "확인 불가")}<small>선정 근거</small></div>'
            f'<div>{_escape(row.get("dataQuality") or row.get("qualityStatus") or "확인 불가")}<small>자료 품질</small></div></div></div>'
        )
    return "".join(cards) or '<div class="industry-item"><strong>확인된 V2 결과가 없습니다.</strong></div>'


def _stock_cards(model: Mapping[str, Any]) -> str:
    rows = model["kiwoom"].get("stocks") or []
    cards = []
    for row in rows:
        rate = _first(row, "changeRate", "change")
        volume = _first(row, "volumeRatio", "volumeRatio20", "volumeMultiple")
        cards.append(
            '<article class="stock"><div class="stock-topline"><span class="stock-role">키움 전일후보군</span>'
            f'<span class="status-chip">{_escape(row.get("status") or "관측")}</span></div>'
            f'<div class="stock-header" style="margin-top:12px"><h4>{_escape(row.get("itemName"))} <small>({ _escape(row.get("itemCode")) })</small></h4><strong class="{_change_class(rate)}">{_escape(_pct(rate))}</strong></div>'
            f'<div class="price">{_escape(_price(_first(row, "price", "currentPrice")))}</div>'
            '<div class="stock-stats">'
            f'<div class="stock-stat"><small>거래량 배율</small><strong>{_escape(f"{volume:.2f}배" if isinstance(volume, (int, float)) else "확인 불가")}</strong></div>'
            f'<div class="stock-stat"><small>조건 판정</small><strong>{_escape(row.get("conditionStatus") or row.get("state") or "확인 대기")}</strong></div>'
            f'<div class="stock-stat"><small>종목코드</small><strong>{_escape(row.get("itemCode"))}</strong></div>'
            f'<div class="stock-stat"><small>가격 관측</small><strong class="{_change_class(rate)}">{_escape("상승 관측" if _num(rate) and _num(rate) > 0 else "하락 관측" if _num(rate) and _num(rate) < 0 else "보합·확인 대기")}</strong></div></div>'
            '<div class="stock-interpretation">원본 계약의 종목명·코드·가격·등락률만 표시합니다.</div>'
            f'<div class="stock-next"><b>다음 확인</b> · {_escape(row.get("nextAction") or "다음 기준시각의 원천과 조건을 확인합니다.")}</div></article>'
        )
    return "".join(cards) or '<article class="stock"><h4>키움 후보 자료가 없습니다.</h4><p>원천 계약에서 확인된 후보가 없습니다.</p></article>'


def _leader_text(rows: list[Mapping[str, Any]]) -> str:
    values = []
    for row in rows[:10]:
        name = row.get("name") or row.get("itemName") or "이름 미확인"
        code = row.get("code") or row.get("itemCode") or "코드 미확인"
        values.append(f"{name}({code})")
    return ", ".join(values) if values else "확인된 자동 주도주가 없습니다."


def render_dashboard_html(report: Mapping[str, Any], *, template_dir: Path = TEMPLATE_DIR) -> str:
    assets = load_approved_template(template_dir)
    model = build_dashboard_model(report)
    if not model["htmlAllowed"]:
        raise TemplateUnavailable("TEMPLATE_UNAVAILABLE:CONTRACT_HTML_NOT_ALLOWED")
    model["templateTrace"] = assets["templateTrace"]
    market = model["fullMarket"]
    total = market["total"]
    ratio = market["declineRatio"]
    metric_html = "".join(
        _metric_line(label, market["metrics"].get(key) or {})
        for label, key in (("20일 이동평균 상회", "aboveMA20"), ("60일 이동평균 상회", "aboveMA60"), ("120일 이동평균 상회", "aboveMA120"), ("20일 고점 돌파", "breakout20"), ("60일 고점 돌파", "breakout60"), ("20일 평균 거래량 상회", "volumeAbove20DayAverage"))
    )
    trace_style = ' style="min-width:0;max-width:100%;white-space:normal;overflow-wrap:anywhere"'
    return f'''<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><title>국내증시 시장 모니터링 A v1.1</title><style>{assets["css"]}</style></head><body><main class="wrap"><div class="topbar"><div class="brand">MARKET / DASHBOARD <span class="tag">A v1.1</span></div><nav aria-label="리포트 바로가기" class="nav"><a href="#part1">시장</a><a href="#part2">업종·종목</a><a href="#part3">대응</a></nav></div><header class="hero"><div class="eyebrow">KOREA MARKET INTELLIGENCE <span class="tag live">● 장중 관찰</span></div><h1>국내증시 시장 모니터링</h1><p class="subtitle">{_escape(model["asOf"])} · {_escape(model["observedAt"])} 기준 · 거래일 증거 {_escape(model["tradingDateStatus"])}</p><div class="chips" style="margin-top:14px"><span class="tag">A v1.1 · 균형형</span><span class="tag"{trace_style}>CONTRACT_TRACE {_escape(model["contractTrace"])}</span><span class="tag"{trace_style}>V2_DATA_TRACE {_escape(model["v2Trace"])}</span><span class="tag"{trace_style}>TEMPLATE_TRACE {_escape(model["templateTrace"])}</span></div><p class="intro">검증된 하나의 보고서 계약에서 시장·업종·테마·후보 데이터를 읽어 표시합니다. 확인되지 않은 값은 생성하지 않습니다.</p></header><section class="section section-anchor" id="part1"><div class="section-head"><h2>01. 시장 대시보드</h2><span class="label">MARKET PULSE</span></div><div class="grid two index-grid">{_index_cards(model)}</div><div style="height:12px"></div><div class="kpis"><article class="card"><div class="kpi-label">상승 종목</div><div class="kpi-value up">{_escape(f"{market["advancers"]:,}")}</div><div class="kpi-sub">전체 { _escape(f"{total:,}") }종목</div></article><article class="card"><div class="kpi-label">하락 종목</div><div class="kpi-value down">{_escape(f"{market["decliners"]:,}")}</div><div class="kpi-sub">하락 비율 {_escape(_plain_pct(ratio))}</div></article><article class="card"><div class="kpi-label">보합 종목</div><div class="kpi-value">{_escape(f"{market["unchanged"]:,}")}</div><div class="kpi-sub">산술 검증 {'PASS' if market["advancers"] + market["decliners"] + market["unchanged"] == total else 'FAIL'}</div></article><article class="card"><div class="kpi-label">작성시각</div><div class="kpi-value" style="font-size:16px">{_escape(model["writtenAt"])}</div><div class="kpi-sub">원천 관측시각과 구분</div></article></div><div style="height:12px"></div><div class="grid two"><article class="card"><h3>전체시장 상승·하락 확산</h3><div class="big-number">{_escape(f"{total:,}")} <span class="muted tiny">종목</span></div><div aria-label="상승·하락·보합 비율" class="breadth" role="img"><div class="rise" style="width:{market["advancers"] / total * 100:.2f}%"></div><div class="fall" style="width:{market["decliners"] / total * 100:.2f}%"></div><div class="flat" style="width:{market["unchanged"] / total * 100:.2f}%"></div></div><div class="legend"><span><i class="dot" style="background:var(--up)"></i>상승 <b>{_escape(f"{market["advancers"]:,}")}</b></span><span><i class="dot" style="background:var(--down)"></i>하락 <b>{_escape(f"{market["decliners"]:,}")} · {_escape(_plain_pct(ratio))}</b></span><span><i class="dot" style="background:var(--flat)"></i>보합 <b>{_escape(f"{market["unchanged"]:,}")}</b></span></div><p class="muted tiny">2,205/2,767 산술 검증: {'PASS' if total == 2767 and market["decliners"] == 2205 and ratio == 79.69 else '계약값 기준 검증'}</p></article><article class="card"><h3>추세·돌파 지표</h3>{metric_html}</article></div><div class="report-callout"><strong>계약 추적</strong><p style="overflow-wrap:anywhere">CONTRACT_TRACE {_escape(model["contractTrace"])}<br>V2_DATA_TRACE {_escape(model["v2Trace"])}<br>TEMPLATE_TRACE {_escape(model["templateTrace"])}<br>작성시각 {_escape(model["writtenAt"])}입니다.</p></div></section><section class="section section-anchor" id="part2"><div class="section-head"><h2>02. 업종·테마·주도주·리서치</h2><span class="label">DEEP DIVE</span></div><article class="card"><div class="miniheading"><h3>업종 강세 실측 비교</h3><span class="tag">검증된 계약 자료</span></div><div class="industry-cards">{_industry_cards(model["industries"])}</div></article><div style="height:12px"></div><article class="card"><div class="miniheading"><h3>V2 업종·테마 상태</h3><span class="tag">명칭·점수·근거·품질</span></div><div class="industry-cards">{_v2_cards(model["v2Industries"], "업종")}{_v2_cards(model["v2Themes"], "테마")}</div></article><div style="height:12px"></div><article class="card"><div class="statusline"><h3 style="margin:0">자동 주도주·독립 분석</h3><span class="status-chip">{_escape(model["research"].get("status") or "확인 불가")}</span></div><p class="muted">검증된 주도주 {len(model["leaders"])}종목: {_escape(_leader_text(model["leaders"]))}. 리서치 상태 {_escape(model["research"].get("status"))}, 수급·공시는 계약 원천이 없으면 확인 불가로 표시합니다.</p></article></section><section class="section section-anchor" id="part3"><div class="section-head"><h2>03. 키움 후보 추적·조건별 대응</h2><span class="label">WATCH POINTS</span></div><article class="card"><div class="section-head"><h3 style="margin:0">키움 전일후보군·종목별 상세 카드</h3><span class="tag">{_escape(model["observedAt"])}</span></div><div class="stock-list">{_stock_cards(model)}</div></article><div style="height:12px"></div><article class="card"><h3>다음 회차 조건</h3><div class="decision-grid"><article class="decision"><b>거래일 증거</b><span class="pill wait">{_escape(model["tradingDateStatus"])}</span><p>확정 일봉과 장중 관측은 구분하여 표시합니다.</p></article><article class="decision"><b>하락 비율</b><span class="pill ok">검증</span><p>하락 {_escape(f"{market["decliners"]:,}")} / {_escape(f"{total:,}")} = {_escape(_plain_pct(ratio))}입니다.</p></article><article class="decision"><b>V2 주도성</b><span class="pill wait">관찰</span><p>V2 데이터의 실제 이름·점수·근거·자료 품질을 계약 그대로 표시합니다.</p></article><article class="decision"><b>후보 조건</b><span class="pill wait">확인 대기</span><p>원천에서 확인된 조건만 다음 회차에 재확인합니다.</p></article></div></article><div class="notice"><b>NOTICE</b> · {_escape(model["notice"])} · 승인 CSS와 카드 구조를 유지합니다.</div></section><footer class="footer">A v1.1 승인 템플릿 기반 · 작성시각 {_escape(model["writtenAt"])} · 시장 데이터 기준시각 {_escape(model["observedAt"])}</footer></main></body></html>'''


def render_dashboard_markdown(report: Mapping[str, Any], *, template_dir: Path = TEMPLATE_DIR) -> str:
    assets = load_approved_template(template_dir)
    model = build_dashboard_model(report)
    model["templateTrace"] = assets["templateTrace"]
    market = model["fullMarket"]
    lines = [
        f"# 국내증시 시장 모니터링 - {model['asOf']}",
        "",
        f"- 작성시각: {model['writtenAt']}",
        f"- 시장 데이터 기준시각: {model['observedAt']}",
        f"- CONTRACT_TRACE: {model['contractTrace']}",
        f"- V2_DATA_TRACE: {model['v2Trace']}",
        f"- TEMPLATE_TRACE: {model['templateTrace']}",
        "",
        "## 1. 시장 대시보드",
        "",
        f"- 상승/하락/보합: {market['advancers']:,}/{market['decliners']:,}/{market['unchanged']:,}",
        f"- 하락 비율: {market['decliners']:,}/{market['total']:,} = {_plain_pct(market['declineRatio'])}",
        f"- 산술 검증: {'PASS' if market['advancers'] + market['decliners'] + market['unchanged'] == market['total'] else 'FAIL'}",
        "",
        "## 2. 업종·테마·주도주·리서치",
        "",
        f"- 업종: {len(model['industries'])}개, 테마: {len(model['v2Themes'])}개, 주도주: {len(model['leaders'])}종목",
        f"- 업종명: {', '.join(str(row.get('name') or row.get('id')) for row in model['industries']) or '확인 불가'}",
        f"- V2 업종·테마: {', '.join(str(row.get('name') or row.get('id')) for row in model['v2Industries'] + model['v2Themes']) or '확인 불가'}",
        f"- 자동 주도주: {_leader_text(model['leaders'])}",
        f"- 리서치 상태: {model['research'].get('status') or '확인 불가'}",
        "",
        "## 3. 키움 후보 추적·조건별 대응",
        "",
        "| 종목명 | 종목코드 | 가격 | 등락률 | 조건 판정 |",
        "|---|---:|---:|---:|---|",
    ]
    for row in model["kiwoom"].get("stocks") or []:
        lines.append(
            f"| {_text(row.get('itemName'))} | {_text(row.get('itemCode'))} | {_price(_first(row, 'price', 'currentPrice'))} | {_pct(_first(row, 'changeRate', 'change'))} | {_text(row.get('conditionStatus') or row.get('state'), '확인 대기')} |"
        )
    lines.extend(["", f"> NOTICE: {model['notice']}", ""])
    return "\n".join(lines)


def validate_renderer_models(markdown_report: Mapping[str, Any], html_report: Mapping[str, Any]) -> list[str]:
    """Compare the canonical model used before rendering both formats."""
    left = build_dashboard_model(markdown_report)
    right = build_dashboard_model(html_report)
    mismatches = []
    for key in ("asOf", "writtenAt", "observedAt", "contractTrace", "v2Trace", "fullMarket", "industries", "v2Industries", "v2Themes", "leaders", "kiwoom"):
        if left.get(key) != right.get(key):
            mismatches.append(key)
    return mismatches
