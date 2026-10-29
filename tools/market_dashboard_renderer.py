"""Render the approved A v1.1 dashboard from one report contract.

The renderer deliberately accepts the published report contract instead of
recalculating market values for each output format.  The approved template
and CSS are immutable assets; only dynamic text and card values are filled in.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
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


def _index_session_label(item: Mapping[str, Any]) -> str:
    """Describe the observed session without promoting it to a final close."""
    session = str(item.get("session") or "").upper()
    status = str(item.get("marketStatus") or "").upper()
    if "POST_MARKET" in session or status == "CLOSE":
        return "장후 관측"
    if session in {"REGULAR", "INTRADAY", "INTRADAY_SESSION"} or status == "OPEN":
        return "장중 관측"
    return "세션 확인 대기"


def _index_session_summary(indices: list[Mapping[str, Any]]) -> str:
    labels = [
        _index_session_label(row)
        for row in indices
        if row.get("name") in {"KOSPI", "KOSDAQ"} or row.get("indexName") in {"KOSPI", "KOSDAQ"}
    ]
    if not labels:
        return "세션 확인 대기"
    unique = set(labels)
    if unique == {"장후 관측"}:
        return "장후 관측"
    if unique == {"장중 관측"}:
        return "장중 관측"
    if "세션 확인 대기" in unique:
        return "시장별 세션 상이·확인 대기" if len(unique) > 1 else "세션 확인 대기"
    return "시장별 세션 상이"


def _market_value_text(market_values: Mapping[str, Any]) -> str:
    values = []
    for market in ("KOSPI", "KOSDAQ"):
        value = (market_values.get(market) or {}).get("value")
        value_text = f"{value:,.0f}원" if isinstance(value, (int, float)) else "확인 불가"
        values.append(f"{market} {value_text}")
    return " · ".join(values)


def _metric_line_value(metric: Mapping[str, Any]) -> str:
    count = metric.get("count")
    eligible = metric.get("eligibleCount")
    pct = metric.get("pct")
    if isinstance(count, (int, float)) and isinstance(eligible, (int, float)):
        return f"{count:,} / {eligible:,} · {_plain_pct(pct)}"
    return "확인 불가"


def _industry_breadth_text(row: Mapping[str, Any]) -> str:
    advancers = row.get("advancers")
    members = row.get("memberCount")
    if advancers is None or members is None:
        return "확인 불가"
    return f"{advancers} / {members}"


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
    indices = list(contract.get("indices") or [])
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
            f"sourceCommitStatus={_text(v2.get('sourceCommitStatus'))};"
            f"sourceProvenance={_text((v2.get('sourceProvenance') or {}).get('status'))};"
            f"inputSnapshotSha256={_text((v2.get('sourceProvenance') or {}).get('inputSnapshotSha256'))};"
            f"tradingDate={_text(v2.get('tradingDate'))};"
            f"historyDates={','.join(v2.get('history', {}).get('uniqueTradingDates', [])) or '확인 불가'}"
        ),
        "templateTrace": "filled-by-renderer",
        "evidence": evidence,
        "indices": indices,
        "indexSessionLabel": _index_session_summary(indices),
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
        "v2History": v2.get("history") or {},
        "v2Changes": list(contract.get("v2Changes") or v2.get("changes") or []),
        "research": contract.get("independentAnalysis", {}).get("research", {}),
        "kiwoom": contract.get("kiwoomCandidates") or {},
        "kiwoomPerformance": contract.get("kiwoomPerformance") or {"status": "UNAVAILABLE", "stocks": []},
        "technicalCandidates": list(contract.get("technicalCandidates") or []),
        "notice": _text(
            (contract.get("rendering") or {}).get("reason"),
            "확인된 계약의 원본 수치와 판정을 표시합니다.",
        ),
    }


def _metric_line(label: str, metric: Mapping[str, Any]) -> str:
    pct = metric.get("pct")
    value = _metric_line_value(metric)
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
        source_time = item.get("sourceTime") or model.get("observedAt")
        session_label = _index_session_label(item)
        cards.append(
            f'<article class="card index-card"><div class="index-name">{_escape(name)}</div>'
            f'<div class="index-value">{_escape(f"{item.get("close"):,.2f}" if isinstance(item.get("close"), (int, float)) else "확인 불가")}</div>'
            f'<div class="change {_change_class(rate)}">{_escape(f"{points:+,.2f}p" if isinstance(points, (int, float)) else "확인 불가")} ({_escape(_pct(rate))})</div>'
            f'<div class="subvalue">{_escape(source_time)} 기준 · {_escape(session_label)}</div></article>'
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
            f'<div>{_escape(_industry_breadth_text(row))}<small>상승 종목</small></div>'
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
        history = row.get("historySummary") or {}
        leaders = row.get("leaderCandidates") or []
        source_metrics = row.get("sourceMetrics") or {}
        trading_value = source_metrics.get("tradingValue") or {}
        metrics_text = (
            f"상승 {source_metrics.get('advancers', '확인 불가')}개 / "
            f"{_plain_pct(source_metrics.get('advancePct'))} · "
            f"중앙 등락률 {_pct(source_metrics.get('medianReturn1D'))} · "
            f"상대강도 {_pct(source_metrics.get('relativeStrengthVsMarket'))} · "
            f"거래대금 {_price(trading_value.get('total')) if trading_value.get('total') is not None else '확인 불가'}"
        )
        leader_text = ", ".join(
            f"{leader.get('name') or '이름 미확인'}({leader.get('code') or '코드 미확인'})"
            for leader in leaders[:3]
        ) or "주도주 0개"
        v1_v2 = f"V1 {row.get('v1Status') or '확인 불가'} · V2 {row.get('v2Status') or '확인 불가'}"
        persistence_text = row.get("persistenceDays")
        if persistence_text is None:
            persistence_text = persistence.get("days") or persistence.get("status")
        cards.append(
            f'<div class="industry-item"><div class="industry-head"><strong>{_escape(row.get("name") or row.get("id"))}</strong><span class="status-chip">{_escape(row.get("v2Status") or row.get("status") or "확인 불가")}</span></div>'
            f'<div class="industry-numbers"><div>{_escape(f"{score:.2f}" if isinstance(score, (int, float)) else "확인 불가")}<small>{_escape(label)} 점수</small></div>'
            f'<div>{_escape(persistence_text or "확인 불가")}<small>persistenceDays · 지속성 ({_escape(v1_v2)})</small></div>'
            f'<div>{_escape(reason if isinstance(reason, str) else ", ".join(map(str, reason or [])) or "확인 불가")}<small>선정 근거</small></div>'
            f'<div>{_escape(row.get("dataQuality") or row.get("qualityStatus") or "확인 불가")}<small>자료 품질</small></div>'
            f'<div style="grid-column:1 / -1;overflow-wrap:anywhere">{_escape(metrics_text)}<small>구성종목·확산·중앙값·시장 대비·거래대금 · {_escape(row.get("sourceMetricsStatus") or "확인 불가")}</small></div>'
            f'<div style="grid-column:1 / -1;overflow-wrap:anywhere">{_escape(leader_text)}<small>검증된 주도 후보 최대 3개 · {_escape(row.get("leaderEvidenceStatus") or "확인 불가")}</small></div>'
            f'<div style="grid-column:1 / -1;overflow-wrap:anywhere">{_escape(history.get("changeClass") or "확인 불가")} · 점수 변화 {_escape(str(history.get("scoreDelta") if history.get("scoreDelta") is not None else "확인 불가"))}<small>최근 {history.get("availableTradingDays", 0)}거래일 · 동일 거래일 중복 제거</small></div>'
            f'</div></div>'
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
    # Keep the approved A v1.1 stylesheet intact while allowing long dynamic
    # timestamps to wrap inside the mobile KPI card.
    assets["css"] += ".kpi-value{overflow-wrap:anywhere;word-break:break-word}"
    model = build_dashboard_model(report)
    if not model["htmlAllowed"]:
        raise TemplateUnavailable("TEMPLATE_UNAVAILABLE:CONTRACT_HTML_NOT_ALLOWED")
    model["templateTrace"] = assets["templateTrace"]
    market = model["fullMarket"]
    total = market["total"]
    ratio = market["declineRatio"]
    market_value_text = _market_value_text(market["marketTradingValue"])
    metric_html = "".join(
        _metric_line(label, market["metrics"].get(key) or {})
        for label, key in (("20일 이동평균 상회", "aboveMA20"), ("60일 이동평균 상회", "aboveMA60"), ("120일 이동평균 상회", "aboveMA120"), ("20일 고점 돌파", "breakout20"), ("60일 고점 돌파", "breakout60"), ("20일 평균 거래량 상회", "volumeAbove20DayAverage"))
    )
    trace_style = ' style="min-width:0;max-width:100%;white-space:normal;overflow-wrap:anywhere"'
    html_output = f'''<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><title>국내증시 시장 모니터링 A v1.1</title><style>{assets["css"]}</style></head><body><main class="wrap"><div class="topbar"><div class="brand">MARKET / DASHBOARD <span class="tag">A v1.1</span></div><nav aria-label="리포트 바로가기" class="nav"><a href="#part1">시장</a><a href="#part2">업종·종목</a><a href="#part3">대응</a></nav></div><header class="hero"><div class="eyebrow">KOREA MARKET INTELLIGENCE <span class="tag live">● {_escape(model["indexSessionLabel"])}</span></div><h1>국내증시 시장 모니터링</h1><p class="subtitle">{_escape(model["asOf"])} · {_escape(model["observedAt"])} 기준 · 거래일 증거 {_escape(model["tradingDateStatus"])}</p><div class="chips" style="margin-top:14px"><span class="tag">A v1.1 · 균형형</span><span class="tag"{trace_style}>CONTRACT_TRACE {_escape(model["contractTrace"])}</span><span class="tag"{trace_style}>V2_DATA_TRACE {_escape(model["v2Trace"])}</span><span class="tag"{trace_style}>TEMPLATE_TRACE {_escape(model["templateTrace"])}</span></div><p class="intro">검증된 하나의 보고서 계약에서 시장·업종·테마·후보 데이터를 읽어 표시합니다. 확인되지 않은 값은 생성하지 않습니다.</p></header><section class="section section-anchor" id="part1"><div class="section-head"><h2>01. 시장 대시보드</h2><span class="label">MARKET PULSE</span></div><div class="grid two index-grid">{_index_cards(model)}</div><div style="height:12px"></div><div class="kpis"><article class="card"><div class="kpi-label">상승 종목</div><div class="kpi-value up">{_escape(f"{market["advancers"]:,}")}</div><div class="kpi-sub">전체 { _escape(f"{total:,}") }종목</div></article><article class="card"><div class="kpi-label">하락 종목</div><div class="kpi-value down">{_escape(f"{market["decliners"]:,}")}</div><div class="kpi-sub">하락 비율 {_escape(_plain_pct(ratio))}</div></article><article class="card"><div class="kpi-label">보합 종목</div><div class="kpi-value">{_escape(f"{market["unchanged"]:,}")}</div><div class="kpi-sub">산술 검증 {'PASS' if market["advancers"] + market["decliners"] + market["unchanged"] == total else 'FAIL'}</div></article><article class="card"><div class="kpi-label">작성시각</div><div class="kpi-value" style="font-size:16px">{_escape(model["writtenAt"])}</div><div class="kpi-sub">원천 관측시각과 구분</div></article></div><div style="height:12px"></div><div class="grid two"><article class="card"><h3>전체시장 상승·하락 확산</h3><div class="big-number">{_escape(f"{total:,}")} <span class="muted tiny">종목</span></div><div aria-label="상승·하락·보합 비율" class="breadth" role="img"><div class="rise" style="width:{market["advancers"] / total * 100:.2f}%"></div><div class="fall" style="width:{market["decliners"] / total * 100:.2f}%"></div><div class="flat" style="width:{market["unchanged"] / total * 100:.2f}%"></div></div><div class="legend"><span><i class="dot" style="background:var(--up)"></i>상승 <b>{_escape(f"{market["advancers"]:,}")}</b></span><span><i class="dot" style="background:var(--down)"></i>하락 <b>{_escape(f"{market["decliners"]:,}")} · {_escape(_plain_pct(ratio))}</b></span><span><i class="dot" style="background:var(--flat)"></i>보합 <b>{_escape(f"{market["unchanged"]:,}")}</b></span></div><p class="muted tiny">하락 { _escape(f"{market["decliners"]:,}") } / 전체 { _escape(f"{total:,}") } 산술 검증: {'PASS' if market["advancers"] + market["decliners"] + market["unchanged"] == total else 'FAIL'}</p><p class="muted tiny">시장 거래대금 · {_escape(market_value_text)}</p></article><article class="card"><h3>추세·돌파 지표</h3>{metric_html}</article></div><div class="report-callout"><strong>계약 추적</strong><p style="overflow-wrap:anywhere">CONTRACT_TRACE {_escape(model["contractTrace"])}<br>V2_DATA_TRACE {_escape(model["v2Trace"])}<br>TEMPLATE_TRACE {_escape(model["templateTrace"])}<br>작성시각 {_escape(model["writtenAt"])}입니다.</p></div></section><section class="section section-anchor" id="part2"><div class="section-head"><h2>02. 업종·테마·주도주·리서치</h2><span class="label">DEEP DIVE</span></div><article class="card"><div class="miniheading"><h3>업종 강세 실측 비교</h3><span class="tag">검증된 계약 자료</span></div><div class="industry-cards">{_industry_cards(model["industries"])}</div></article><div style="height:12px"></div><article class="card"><div class="miniheading"><h3>V2 업종·테마 상태</h3><span class="tag">명칭·점수·근거·품질</span></div><div class="industry-cards">{_v2_cards(model["v2Industries"], "업종")}{_v2_cards(model["v2Themes"], "테마")}</div></article><div style="height:12px"></div><article class="card"><div class="statusline"><h3 style="margin:0">자동 주도주·독립 분석</h3><span class="status-chip">{_escape(model["research"].get("status") or "확인 불가")}</span></div><p class="muted">검증된 주도주 {len(model["leaders"])}종목: {_escape(_leader_text(model["leaders"]))}. 리서치 상태 {_escape(model["research"].get("status"))}, 수급·공시는 계약 원천이 없으면 확인 불가로 표시합니다.</p></article></section><section class="section section-anchor" id="part3"><div class="section-head"><h2>03. 키움 후보 추적·조건별 대응</h2><span class="label">WATCH POINTS</span></div><article class="card"><div class="section-head"><h3 style="margin:0">키움 전일후보군·종목별 상세 카드</h3><span class="tag">{_escape(model["observedAt"])}</span></div><div class="stock-list">{_stock_cards(model)}</div></article><div style="height:12px"></div><article class="card"><h3>다음 회차 조건</h3><div class="decision-grid"><article class="decision"><b>거래일 증거</b><span class="pill wait">{_escape(model["tradingDateStatus"])}</span><p>확정 일봉과 장중 관측은 구분하여 표시합니다.</p></article><article class="decision"><b>하락 비율</b><span class="pill ok">검증</span><p>하락 {_escape(f"{market["decliners"]:,}")} / {_escape(f"{total:,}")} = {_escape(_plain_pct(ratio))}입니다.</p></article><article class="decision"><b>V2 주도성</b><span class="pill wait">관찰</span><p>V2 데이터의 실제 이름·점수·근거·자료 품질을 계약 그대로 표시합니다.</p></article><article class="decision"><b>후보 조건</b><span class="pill wait">확인 대기</span><p>원천에서 확인된 조건만 다음 회차에 재확인합니다.</p></article></div></article><div class="notice"><b>NOTICE</b> · {_escape(model["notice"])} · 승인 CSS와 카드 구조를 유지합니다.</div></section><footer class="footer">A v1.1 승인 템플릿 기반 · 작성시각 {_escape(model["writtenAt"])} · 시장 데이터 기준시각 {_escape(model["observedAt"])}</footer></main></body></html>'''
    v2_change_html = "".join(
        f'<div class="industry-item"><strong>{_escape(row.get("name") or row.get("id"))}</strong>'
        f'<span class="status-chip">{_escape(row.get("officialChangeClass") or row.get("changeClass") or "비교 불가")}</span>'
        f'<p>점수 변화 {_escape(row.get("scoreDelta") if row.get("scoreDelta") is not None else "확인 불가")} · '
        f'지속 {_escape(row.get("persistenceDays") if row.get("persistenceDays") is not None else "확인 불가")}일 · '
        f'구성 변경 {_escape("있음" if row.get("membershipChanged") else "없음")} · 원천 {_escape(row.get("sourceCommitStatus") or "확인 불가")}</p></div>'
        for row in model["v2Changes"]
    ) or '<div class="industry-item">비교 가능한 V2 변화가 없습니다.</div>'
    performance_html = "".join(
        f'<div class="stock-interpretation"><b>{_escape(row.get("itemName") or "이름 미확인")} ({_escape(row.get("itemCode"))})</b> · '
        f'선정일 종가 {_escape(_price(row.get("selectionClose")))} · '
        f'{_escape(", ".join(f"{key} {value.get("date") or "대기"} / {(_pct(value.get("returnPct")) if value.get("returnPct") is not None else value.get("status", "확인 불가"))}" for key, value in (row.get("horizons") or {}).items()))} · '
        f'전략 판정 {_escape(row.get("strategyStatus") or "확인 불가")}</div>'
        for row in model["kiwoomPerformance"].get("stocks") or []
    ) or '<div class="stock-interpretation">키움 성과 추적 대상이 없습니다.</div>'
    html_output = html_output.replace(
        '</section><section class="section section-anchor" id="part3">',
        f'<article class="card"><div class="miniheading"><h3>V2 업종·테마 변화</h3><span class="tag">상태·점수·구성 변화</span></div><div class="industry-cards">{v2_change_html}</div></article></section><section class="section section-anchor" id="part3">'
    )
    html_output = html_output.replace(
        '<div class="notice"><b>NOTICE</b>',
        f'<article class="card"><h3>키움 후보 D+1/D+3/D+5 추적</h3><p class="muted">상태 {_escape(model["kiwoomPerformance"].get("status") or "확인 불가")} · 전략 판정은 기준 미확인 시 산출하지 않습니다.</p>{performance_html}</article><div class="notice"><b>NOTICE</b>'
    )
    issues = validate_rendered_outputs(
        render_dashboard_markdown(report, template_dir=template_dir),
        html_output,
        model,
    )
    if issues:
        raise TemplateUnavailable("TEMPLATE_UNAVAILABLE:RENDERED_OUTPUT_MISMATCH:" + ",".join(issues[:8]))
    return html_output


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
        f"- 세션 요약: {model['indexSessionLabel']}",
        f"- 계약 추적: CONTRACT_TRACE {model['contractTrace']} · V2_DATA_TRACE {model['v2Trace']} · TEMPLATE_TRACE {model['templateTrace']}",
        f"- 상승/하락/보합: {market['advancers']:,}/{market['decliners']:,}/{market['unchanged']:,}",
        f"- 하락 비율: {market['decliners']:,}/{market['total']:,} = {_plain_pct(market['declineRatio'])}",
        f"- 산술 검증: {'PASS' if market['advancers'] + market['decliners'] + market['unchanged'] == market['total'] else 'FAIL'}",
        f"- 시장 거래대금: {_market_value_text(market['marketTradingValue'])}",
        f"- 시장 세션: {model['indexSessionLabel']}",
        *[
            f"- {item.get('name') or item.get('indexName')}: {f'{item.get('close'):,.2f}' if isinstance(item.get('close'), (int, float)) else '확인 불가'} · {_pct(item.get('changeRate'))} · {_text(item.get('sourceTime') or model.get('observedAt'), '확인 불가')} · {_index_session_label(item)}"
            for item in model['indices']
            if item.get('name') in {'KOSPI', 'KOSDAQ'} or item.get('indexName') in {'KOSPI', 'KOSDAQ'}
        ],
        *[
            f"- 지표 {label}: {_metric_line_value(market['metrics'].get(key) or {})}"
            for label, key in (("20일 이동평균 상회", "aboveMA20"), ("60일 이동평균 상회", "aboveMA60"), ("120일 이동평균 상회", "aboveMA120"), ("20일 고점 돌파", "breakout20"), ("60일 고점 돌파", "breakout60"), ("20일 평균 거래량 상회", "volumeAbove20DayAverage"))
        ],
        "",
        "## 2. 업종·테마·주도주·리서치",
        "",
        f"- 업종: {len(model['industries'])}개, 테마: {len(model['v2Themes'])}개, 주도주: {len(model['leaders'])}종목",
        f"- 업종명: {', '.join(str(row.get('name') or row.get('id')) for row in model['industries']) or '확인 불가'}",
        f"- V2 업종·테마: {', '.join(str(row.get('name') or row.get('id')) for row in model['v2Industries'] + model['v2Themes']) or '확인 불가'}",
        f"- 자동 주도주: {_leader_text(model['leaders'])}",
        f"- 리서치 상태: {model['research'].get('status') or '확인 불가'}",
        "",
        "### V2 실명·주도 후보·최근 변화",
        "",
    ]
    for row in model["industries"][:6]:
        trading = row.get("tradingValue") or {}
        total_value = trading.get("total")
        industry_name = row.get("name") or row.get("id")
        value_text = f"{total_value:,.0f}원" if isinstance(total_value, (int, float)) else "확인 불가"
        lines.append(
            f"- 업종 실측 {industry_name}: 상승 {_industry_breadth_text(row)}, "
            f"1일 성과 {_pct(_first(row, 'avgReturn1D', 'medianReturn1D', 'changeRate'))}, "
            f"거래대금 {value_text}"
        )
    for label, rows in (("업종", model["v2Industries"]), ("테마", model["v2Themes"])):
        lines.append(f"- V2 {label}: {len(rows)}개")
        for row in rows:
            leaders = row.get("leaderCandidates") or []
            leader_text = ", ".join(
                f"{leader.get('name') or '이름 미확인'}({leader.get('code') or '코드 미확인'})"
                for leader in leaders[:3]
            ) or "주도주 0개"
            history = row.get("historySummary") or {}
            source_metrics = row.get("sourceMetrics") or {}
            trading_value = source_metrics.get("tradingValue") or {}
            score = row.get("score") if isinstance(row.get("score"), (int, float)) else "확인 불가"
            lines.append(
                f"  - {row.get('name') or row.get('id') or '이름 미확인'}: "
                f"V1 {row.get('v1Status') or '확인 불가'} · V2 {row.get('v2Status') or '확인 불가'}, "
                f"점수 {f'{score:.2f}' if isinstance(score, (int, float)) else score}, persistenceDays {row.get('persistenceDays') or 0}, "
                f"선정 근거 {row.get('selectionReason') or row.get('reason') or '확인 불가'}, "
                f"자료 품질 {row.get('dataQuality') or row.get('qualityStatus') or '확인 불가'}, "
                f"주도 후보 {leader_text}, 근거 {row.get('leaderEvidenceStatus') or '확인 불가'}, "
                f"최근 변화 {history.get('changeClass') or '확인 불가'}, "
                f"점수 변화 {history.get('scoreDelta') if history.get('scoreDelta') is not None else '확인 불가'}, "
                f"상승 {source_metrics.get('advancers', '확인 불가')}개 / {_plain_pct(source_metrics.get('advancePct'))}, "
                f"중앙 등락률 {_pct(source_metrics.get('medianReturn1D'))}, "
                f"상대강도 {_pct(source_metrics.get('relativeStrengthVsMarket'))}, "
                f"거래대금 {_price(trading_value.get('total')) if trading_value.get('total') is not None else '확인 불가'}, "
                f"자료 상태 {row.get('sourceMetricsStatus') or '확인 불가'}"
            )
    history = model.get("v2History") or {}
    lines.append(
        f"- V2 이력: {history.get('status', '확인 불가')}, "
        f"최근 거래일 {', '.join(history.get('recentTradingDates') or []) or '확인 불가'}, "
        f"실제 확보 {history.get('availableTradingDays', 0)}일, 동일 거래일 중복 제거 {history.get('sameDayDeduplicated', False)}"
    )
    lines.append("- V2 업종·테마 변화 추적:")
    for change in model["v2Changes"]:
        lines.append(
            f"  - {change.get('name') or change.get('id')}: {change.get('officialChangeClass') or change.get('changeClass') or '비교 불가'} · "
            f"점수 변화 {change.get('scoreDelta') if change.get('scoreDelta') is not None else '확인 불가'} · "
            f"지속 {change.get('persistenceDays') if change.get('persistenceDays') is not None else '확인 불가'}일 · "
            f"구성 변경 {'있음' if change.get('membershipChanged') else '없음'} · 원천 {change.get('sourceCommitStatus') or '확인 불가'}"
        )
    lines.extend([
        "",
        "## 3. 키움 후보 추적·조건별 대응",
        "",
        "| 종목명 | 종목코드 | 가격 | 등락률 | 조건 판정 |",
        "|---|---:|---:|---:|---|",
    ])
    for row in model["kiwoom"].get("stocks") or []:
        lines.append(
            f"| {_text(row.get('itemName'))} | {_text(row.get('itemCode'))} | {_price(_first(row, 'price', 'currentPrice'))} | {_pct(_first(row, 'changeRate', 'change'))} | {_text(row.get('conditionStatus') or row.get('state'), '확인 대기')} |"
        )
    performance = model["kiwoomPerformance"]
    lines.append("")
    lines.append(f"- 키움 후보 성과 추적: {performance.get('status', '확인 불가')} · 원본 {performance.get('source') or '확인 불가'}")
    for row in performance.get("stocks") or []:
        horizon_text = ", ".join(
            f"{key} {value.get('date') or '대기'} / {_pct(value.get('returnPct')) if value.get('returnPct') is not None else value.get('status', '확인 불가')}"
            for key, value in (row.get("horizons") or {}).items()
        )
        lines.append(f"  - {row.get('itemName') or '이름 미확인'}({row.get('itemCode')}): 선정일 종가 {_price(row.get('selectionClose'))} · {horizon_text} · 전략 판정 {row.get('strategyStatus', '확인 불가')}")
    lines.extend(["", f"> NOTICE: {model['notice']}", ""])
    return "\n".join(lines)


def validate_rendered_outputs(
    markdown_text: str, html_text: str, model: Mapping[str, Any]
) -> list[str]:
    """Check the saved-format content, not only the pre-render model.

    The same canonical tokens must survive both renderers.  This deliberately
    reports field-level omissions so callers can fail closed before attaching
    an incomplete HTML file.
    """
    html_plain = html.unescape(html_text)
    issues: list[str] = []

    required_sections = (
        ("MARKDOWN_PART1", "## 1. 시장 대시보드", markdown_text),
        ("MARKDOWN_PART2", "## 2. 업종·테마·주도주·리서치", markdown_text),
        ("MARKDOWN_PART3", "## 3. 키움 후보 추적·조건별 대응", markdown_text),
        ("HTML_PART1", 'id="part1"', html_plain),
        ("HTML_PART2", 'id="part2"', html_plain),
        ("HTML_PART3", 'id="part3"', html_plain),
    )
    for field, token, text in required_sections:
        if token not in text:
            issues.append(f"{field}_MISSING")

    def markdown_section(heading: str) -> str:
        match = re.search(
            rf"^{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s|\Z)",
            markdown_text,
            re.MULTILINE,
        )
        return match.group(1) if match else ""

    def html_section(section_id: str) -> str:
        match = re.search(
            rf'<section[^>]*\bid="{re.escape(section_id)}"[^>]*>([\s\S]*?)</section>',
            html_plain,
            re.IGNORECASE,
        )
        return match.group(1) if match else ""

    md_part1 = markdown_section("## 1. 시장 대시보드")
    md_part2 = markdown_section("## 2. 업종·테마·주도주·리서치")
    md_part3 = markdown_section("## 3. 키움 후보 추적·조건별 대응")
    html_part1 = html_section("part1")
    html_part2 = html_section("part2")
    html_part3 = html_section("part3")

    def require_in_section(field: str, value: Any, md_text: str, html_text_for_section: str) -> None:
        if value in (None, ""):
            return
        token = str(value)
        if token not in md_text:
            issues.append(f"MARKDOWN_{field}_MISSING_IN_SECTION")
        if token not in html_text_for_section:
            issues.append(f"HTML_{field}_MISSING_IN_SECTION")

    def require(field: str, value: Any) -> None:
        if value in (None, ""):
            return
        token = str(value)
        if token not in markdown_text:
            issues.append(f"MARKDOWN_MISSING:{field}")
        if token not in html_plain:
            issues.append(f"HTML_MISSING:{field}")

    require("asOf", model.get("asOf"))
    require("writtenAt", model.get("writtenAt"))
    require("observedAt", model.get("observedAt"))
    require("tradingDateStatus", model.get("tradingDateStatus"))
    require("indexSessionLabel", model.get("indexSessionLabel"))
    for field in ("contractTrace", "v2Trace"):
        require(field, model.get(field))
    if model.get("templateTrace") not in (None, "", "filled-by-renderer"):
        require("templateTrace", model.get("templateTrace"))
    require("NOTICE", "NOTICE")

    market = model["fullMarket"]
    for field, value in (
        ("advancers", f"{market['advancers']:,}"),
        ("decliners", f"{market['decliners']:,}"),
        ("unchanged", f"{market['unchanged']:,}"),
        ("total", f"{market['total']:,}"),
        ("declineRatio", _plain_pct(market["declineRatio"])),
        ("marketTradingValue", _market_value_text(market["marketTradingValue"])),
    ):
        require(field, value)
        require_in_section(field, value, md_part1, html_part1)
    for key in ("aboveMA20", "aboveMA60", "aboveMA120", "breakout20", "breakout60", "volumeAbove20DayAverage"):
        metric_value = _metric_line_value(market["metrics"].get(key) or {})
        require(f"metric.{key}", metric_value)
        require_in_section(f"metric.{key}", metric_value, md_part1, html_part1)

    for item in model["indices"]:
        name = item.get("name") or item.get("indexName")
        if name not in {"KOSPI", "KOSDAQ"}:
            continue
        require(f"index.{name}.name", name)
        require_in_section(f"index.{name}.name", name, md_part1, html_part1)
        close = item.get("close")
        close_value = f"{close:,.2f}" if isinstance(close, (int, float)) else "확인 불가"
        rate_value = _pct(item.get("changeRate"))
        source_time_value = _text(item.get("sourceTime") or model.get("observedAt"), "확인 불가")
        session_value = _index_session_label(item)
        for field, value in (("close", close_value), ("rate", rate_value), ("sourceTime", source_time_value), ("session", session_value)):
            require(f"index.{name}.{field}", value)
            require_in_section(f"index.{name}.{field}", value, md_part1, html_part1)

    for row in model["industries"][:6]:
        name = row.get("name") or row.get("id")
        industry_values = (
            ("industry.name", name),
            ("industry.performance", _pct(_first(row, "avgReturn1D", "medianReturn1D", "changeRate"))),
            ("industry.advancers", _industry_breadth_text(row)),
        )
        for field, value in industry_values:
            require(field, value)
            require_in_section(field, value, md_part2, html_part2)

    for label, rows in (("v2Industry", model["v2Industries"]), ("v2Theme", model["v2Themes"])):
        for row in rows:
            row_values: list[tuple[str, Any]] = [
                (f"{label}.name", row.get("name") or row.get("id")),
                (f"{label}.status", row.get("v2Status") or row.get("status")),
            ]
            if isinstance(row.get("score"), (int, float)):
                row_values.append((f"{label}.score", f"{row['score']:.2f}"))
            persistence = row.get("persistenceDays") or (row.get("persistence") or {}).get("days") or (row.get("persistence") or {}).get("status")
            row_values.extend([
                (f"{label}.persistence", persistence),
                (f"{label}.reason", row.get("selectionReason") or row.get("reason")),
                (f"{label}.quality", row.get("dataQuality") or row.get("qualityStatus")),
            ])
            for field, value in row_values:
                require(field, value)
                require_in_section(field, value, md_part2, html_part2)
            for leader in (row.get("leaderCandidates") or [])[:3]:
                for field, value in ((f"{label}.leader.name", leader.get("name") or "이름 미확인"), (f"{label}.leader.code", leader.get("code") or "코드 미확인")):
                    require(field, value)
                    require_in_section(field, value, md_part2, html_part2)
            history = row.get("historySummary") or {}
            require(f"{label}.history", history.get("changeClass"))
            require_in_section(f"{label}.history", history.get("changeClass"), md_part2, html_part2)

    for row in model["leaders"][:10]:
        for field, value in (("leader.name", row.get("name") or row.get("itemName")), ("leader.code", row.get("code") or row.get("itemCode"))):
            require(field, value)
            require_in_section(field, value, md_part2, html_part2)
    for row in model["kiwoom"].get("stocks") or []:
        stock_values = (
            ("kiwoom.name", row.get("itemName")),
            ("kiwoom.code", row.get("itemCode")),
            ("kiwoom.price", _price(_first(row, "price", "currentPrice"))),
            ("kiwoom.rate", _pct(_first(row, "changeRate", "change"))),
            ("kiwoom.condition", row.get("conditionStatus") or row.get("state") or "확인 대기"),
        )
        for field, value in stock_values:
            require(field, value)
            require_in_section(field, value, md_part3, html_part3)
    require_in_section("NOTICE.body", model.get("notice"), md_part3, html_part3)
    for change in model["v2Changes"]:
        token = str(change.get("name") or change.get("id"))
        require_in_section("v2Change.name", token, md_part2, html_part2)
        require_in_section("v2Change.status", str(change.get("officialChangeClass") or change.get("changeClass") or "비교 불가"), md_part2, html_part2)
    performance = model["kiwoomPerformance"]
    require_in_section("kiwoomPerformance.status", performance.get("status"), md_part3, html_part3)
    for row in performance.get("stocks") or []:
        require_in_section("kiwoomPerformance.code", row.get("itemCode"), md_part3, html_part3)
        for horizon in (row.get("horizons") or {}).values():
            if horizon.get("date"):
                require_in_section("kiwoomPerformance.date", horizon.get("date"), md_part3, html_part3)
    return issues


def validate_rendered_files(
    markdown_path: Path, html_path: Path, model: Mapping[str, Any]
) -> list[str]:
    """Re-open the completed files and run the same fail-closed checks."""
    try:
        markdown_text = markdown_path.read_text(encoding="utf-8")
        html_text = html_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"RENDERED_FILE_READ_FAILED:{type(exc).__name__}"]
    return validate_rendered_outputs(markdown_text, html_text, model)


def validate_renderer_models(markdown_report: Mapping[str, Any], html_report: Mapping[str, Any]) -> list[str]:
    """Compare the canonical model used before rendering both formats."""
    left = build_dashboard_model(markdown_report)
    right = build_dashboard_model(html_report)
    mismatches = []
    for key in ("asOf", "writtenAt", "observedAt", "contractTrace", "v2Trace", "fullMarket", "industries", "v2Industries", "v2Themes", "v2History", "v2Changes", "leaders", "kiwoom", "kiwoomPerformance"):
        if left.get(key) != right.get(key):
            mismatches.append(key)
    return mismatches
