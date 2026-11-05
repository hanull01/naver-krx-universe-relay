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
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "report_templates" / "market-dashboard-A-v1.1"


class TemplateUnavailable(RuntimeError):
    """The approved template or the renderable contract is unavailable."""


def _date_stem(value: Any) -> str:
    """Return a stable dated-output stem without inferring a date."""
    text = str(value or "").strip().replace("-", "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return "latest"


def _text(value: Any, fallback: str = "확인 불가") -> str:
    return fallback if value in (None, "") else str(value)


USER_STATUS_LABELS = {
    "PARTIAL": "부분 확인",
    "AVAILABLE": "확인 가능",
    "AVAILABLE_EMPTY": "확인 가능(대상 없음)",
    "UNAVAILABLE": "확인 불가",
    "VERIFIED": "검증됨",
    "SOURCE_OBSERVED": "관측값",
    "DISCOVERED": "발견",
    "REMOVED": "제외",
    "MAINTAINED": "유지",
    "ACTIVE": "활성",
    "WATCH": "관찰",
    "UNVERIFIED": "검증 대기",
    "PENDING": "대기",
    "WAIT": "대기",
    "STALE": "오래된 자료",
    "FAILED": "실패",
    "INVALID": "검증 오류",
    "INELIGIBLE": "대상 제외",
    "RENDERABLE": "표시 가능",
    "UPTREND": "상승 추세",
    "DOWNTREND": "하락 추세",
    "SIDEWAYS": "횡보",
    "STRENGTHENING": "강화",
    "WEAKENING": "약화",
    "PERSISTING": "유지",
    "NEW": "신규 진입",
    "NEW_ENTRY": "신규 진입",
    "RANK_UP": "순위 상승",
    "RANK_DOWN": "순위 하락",
    "EXITED": "이탈",
    "COMPARISON_UNAVAILABLE": "비교 불가",
    "BASELINE_STALE": "부분 자료",
    "OK": "확인 가능",
    "SUCCESS": "확인 가능",
}


def _display_status(value: Any, fallback: str = "확인 대기") -> str:
    if value in (None, ""):
        return fallback
    text = str(value)
    return USER_STATUS_LABELS.get(text.upper(), text)


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


def _candidate_technical_text(row: Mapping[str, Any]) -> str:
    metrics = row.get("technicalMetrics") if isinstance(row.get("technicalMetrics"), Mapping) else {}
    parts = []
    for label, key in (("MA5", "ma5"), ("MA20", "ma20"), ("MA60", "ma60")):
        if metrics.get(key) is not None:
            parts.append(f"{label} {_price(metrics[key])}")
    if metrics.get("volumeRatio20") is not None:
        parts.append(f"거래량비율 {float(metrics['volumeRatio20']):.2f}")
    if metrics.get("breakout20") is not None:
        parts.append(f"돌파 {_display_status(metrics['breakout20'])}")
    if metrics.get("pullbackState") is not None:
        parts.append(f"눌림 {_display_status(metrics['pullbackState'])}")
    if metrics.get("trend") is not None:
        parts.append(f"추세 {_display_status(metrics['trend'])}")
    return " · ".join(parts) or "기술 지표 자료 없음"


def _index_close(value: Any) -> str:
    number = _num(value)
    return f"{number:,.2f}" if number is not None else "확인 불가"


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
    if session in {"PREOPEN", "PRE_OPEN", "BEFORE_OPEN"} or status in {"PREOPEN", "PRE_OPEN", "BEFORE_OPEN"}:
        return "장전 관측"
    if session in {"REGULAR", "INTRADAY", "INTRADAY_SESSION"} or status == "OPEN":
        return "장중 관측"
    return "세션 확인 대기"


def _index_code(item: Mapping[str, Any]) -> str | None:
    """Return the canonical index code, preferring code over display names."""
    aliases = {
        "KOSPI": "KOSPI",
        "코스피": "KOSPI",
        "KOSDAQ": "KOSDAQ",
        "코스닥": "KOSDAQ",
    }
    for key in ("code", "name", "indexName"):
        value = str(item.get(key) or "").strip()
        if value in aliases:
            return aliases[value]
        upper = value.upper()
        if upper in aliases:
            return aliases[upper]
    return None


def _index_session_summary(indices: list[Mapping[str, Any]]) -> str:
    labels = [
        _index_session_label(row)
        for row in indices
        if _index_code(row) in {"KOSPI", "KOSDAQ"}
    ]
    if not labels:
        return "세션 확인 대기"
    unique = set(labels)
    if unique == {"장후 관측"}:
        return "장후 관측"
    if unique == {"장중 관측"}:
        return "장중 관측"
    if unique == {"장전 관측"}:
        return "장전 관측"
    if "세션 확인 대기" in unique:
        return "시장별 세션 상이·확인 대기" if len(unique) > 1 else "세션 확인 대기"
    return "시장별 세션 상이"


def _session_context_lines(model: Mapping[str, Any]) -> list[str]:
    context = model.get("sessionContext") or {}
    if context.get("mode") != "PREOPEN":
        return []
    observation = context.get("observation") or {}
    previous = context.get("previousTradingDay") or {}
    lines = [
        "### 장전 관측과 직전 거래일 참고자료",
        f"- 당일 장전 관측: {observation.get('tradingDate') or '확인 불가'} · 상태 {observation.get('status') or '확인 불가'} · 원천시각 {observation.get('indices', [{}])[0].get('sourceTime') if observation.get('indices') else '확인 불가'}",
        f"- 직전 거래일 참고: {previous.get('tradingDate') or '확인 불가'} · 상태 {previous.get('status') or '확인 불가'} · 검증 {previous.get('verificationStatus') or '확인 불가'}",
    ]
    if previous.get("status") == "AVAILABLE":
        for row in previous.get("indices") or []:
            lines.append(
                f"- 직전 {row.get('name') or row.get('code')}: "
                f"{row.get('close') if row.get('close') is not None else '확인 불가'} · "
                f"{row.get('changePoints') if row.get('changePoints') is not None else '확인 불가'}p · "
                f"{row.get('changeRate') if row.get('changeRate') is not None else '확인 불가'}% · "
                f"원천시각 {row.get('sourceTime') or '확인 불가'}"
            )
        full_market = previous.get("fullMarket") or {}
        lines.append(
            f"- 직전 거래일 기술지표 기준: 전체 {full_market.get('totalCount') or '확인 불가'}종목 · "
            f"상승/하락/보합 {((full_market.get('breadth') or {}).get('advancers', '확인 불가'))}/"
            f"{((full_market.get('breadth') or {}).get('decliners', '확인 불가'))}/"
            f"{((full_market.get('breadth') or {}).get('unchanged', '확인 불가'))} · "
            "당일 장전 자료와 혼합하지 않음"
        )
        v1v2 = previous.get("v1V2") or {}
        lines.append(
            f"- V1/V2 직전 거래일 결과: {v1v2.get('tradingDate') or '확인 불가'} · "
            f"상태 {v1v2.get('status') or '확인 불가'}"
        )
    elif previous.get("reason"):
        lines.append(f"- 직전 거래일 참고자료 보류 사유: {previous['reason']}")
    return lines


def _session_context_html(model: Mapping[str, Any]) -> str:
    lines = _session_context_lines(model)
    if not lines:
        return ""
    return '<article class="card session-context"><h3>장전 관측과 직전 거래일 참고자료</h3>' + "".join(
        f"<p>{_escape(line[2:] if line.startswith('- ') else line)}</p>" for line in lines[1:]
    ) + "</article>"


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


def render_contract_views(
    contract: Mapping[str, Any],
    output_dir: Path,
    *,
    template_dir: Path = TEMPLATE_DIR,
    allow_current_observation: bool = False,
) -> dict[str, Any]:
    """Render and validate both views from one published report contract.

    This is the GitHub-facing, contract-only path.  It renders both strings
    before writing anything, validates the strings, writes the files once,
    then reopens the completed files for a second validation pass.  HTML is
    never written when the contract disallows it or either view is incomplete.
    """
    if not isinstance(contract, Mapping):
        raise TemplateUnavailable("CONTRACT_UNAVAILABLE:JSON_OBJECT_REQUIRED")

    model = build_dashboard_model(
        contract, allow_current_observation=allow_current_observation
    )
    assets = load_approved_template(template_dir)
    markdown_text = render_dashboard_markdown(
        contract,
        template_dir=template_dir,
        allow_current_observation=allow_current_observation,
        _model=model,
        _template_assets=assets,
    )
    html_text = render_dashboard_html(
        contract,
        template_dir=template_dir,
        allow_current_observation=allow_current_observation,
        _model=model,
        _assets=assets,
    )
    issues = validate_rendered_outputs(markdown_text, html_text, model)
    if issues:
        raise TemplateUnavailable(
            "TEMPLATE_UNAVAILABLE:RENDERED_OUTPUT_MISMATCH:" + ",".join(issues[:8])
        )

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    stem = _date_stem(model.get("asOf"))
    dated_markdown = destination / f"{stem}.md"
    latest_markdown = destination / "latest.md"
    dated_html = destination / f"{stem}.html"
    latest_html = destination / "latest.html"

    staged: list[tuple[Path, Path]] = []
    try:
        for path, text in (
            (dated_markdown, markdown_text),
            (latest_markdown, markdown_text),
            (dated_html, html_text),
            (latest_html, html_text),
        ):
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=destination,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(text)
                staged.append((Path(handle.name), path))

        staged_markdown, _ = staged[0]
        staged_html, _ = staged[2]
        reopened_issues = validate_rendered_files(staged_markdown, staged_html, model)
        if reopened_issues:
            raise TemplateUnavailable(
                "TEMPLATE_UNAVAILABLE:REOPENED_OUTPUT_MISMATCH:"
                + ",".join(reopened_issues[:8])
            )
        for temporary, target in staged:
            temporary.replace(target)
    except Exception:
        for temporary, _ in staged:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise

    final_issues = validate_rendered_files(dated_markdown, dated_html, model)
    if final_issues:
        raise TemplateUnavailable(
            "TEMPLATE_UNAVAILABLE:FINAL_OUTPUT_MISMATCH:" + ",".join(final_issues[:8])
        )

    return {
        "asOf": model.get("asOf"),
        "htmlStatus": "AVAILABLE",
        "markdown": str(latest_markdown),
        "html": str(latest_html),
        "datedMarkdown": str(dated_markdown),
        "datedHtml": str(dated_html),
        "templateTrace": assets["templateTrace"],
        "htmlSha256": hashlib.sha256(html_text.encode("utf-8")).hexdigest(),
        "htmlBytes": len(html_text.encode("utf-8")),
    }


def _metric_line(label: str, metric: Mapping[str, Any]) -> str:
    pct = metric.get("pct")
    value = _metric_line_value(metric)
    width = _num(pct) or 0
    return f'<div class="metric-row"><div>{_escape(label)}<div class="meter"><span style="width:{max(0, min(100, width))}%"></span></div></div><strong>{_escape(value)}</strong></div>'

def _compact_date(value: Any) -> str:
    text = str(value or "").strip().replace("-", "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return _text(value)


def _compact_kst(value: Any) -> str:
    if value in (None, ""):
        return "확인 불가"
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(ZoneInfo("Asia/Seoul"))
        return parsed.strftime("%Y-%m-%d %H:%M KST")
    except (TypeError, ValueError):
        return _compact_date(value)


def _compact_normalized_date(value: Any) -> str | None:
    text = str(value or "").replace("-", "").strip()
    return text if len(text) == 8 and text.isdigit() else None


def _compact_row_available(row: Mapping[str, Any]) -> bool:
    values = (
        row.get("status"), row.get("dataStatus"), row.get("qualityStatus"),
        row.get("dataQuality"), row.get("membershipStatus"),
        row.get("sourceCommitStatus"), row.get("sourceProvenanceStatus"),
        row.get("eligibility"),
    )
    blocked = {"UNAVAILABLE", "FAILED", "STALE", "INVALID", "INELIGIBLE", "REMOVED", "WAIT", "PENDING"}
    for value in values:
        if value in (None, ""):
            continue
        normalized = str(value).upper()
        # A stale baseline only removes derived persistence metrics. It does
        # not invalidate a same-day NAVER relative-strength observation.
        if normalized == "BASELINE_STALE":
            continue
        if any(token in normalized for token in blocked):
            return False
    return True


def _compact_v2_verified(contract: Mapping[str, Any], v2: Mapping[str, Any]) -> bool:
    source_commit = v2.get("sourceCommit")
    commit_status = str(v2.get("sourceCommitStatus") or "").upper()
    if not source_commit or commit_status not in {"VERIFIED", "AVAILABLE", "OK"}:
        return False
    expected = _compact_normalized_date((contract.get("tradingDateEvidence") or {}).get("tradingDate") or contract.get("asOf"))
    observed = _compact_normalized_date(v2.get("tradingDate"))
    if not expected or expected != observed:
        return False
    return str(v2.get("sourceQualityStatus") or "").upper() not in {"UNAVAILABLE", "PARTIAL", "STALE", "INVALID"}


def _compact_relative_top5(contract: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    relative = contract.get("relativeStrengthTop5") or {}
    section = relative.get(key) or {}
    if str(relative.get("status") or "").upper() in {"UNAVAILABLE", "FAILED", "INVALID"}:
        return []
    if _compact_normalized_date(relative.get("tradingDate")) != _compact_normalized_date(contract.get("asOf")):
        return []
    return [
        row for row in (section.get("top5") or [])
        if isinstance(row, Mapping) and _compact_row_available(row) and row.get("eligibility", "ELIGIBLE") == "ELIGIBLE"
    ][:5]


def _compact_interest_rows(contract: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in ("registeredUniverse", "monitoredUniverse", "userWatchlist", "interestStocks"):
        value = contract.get(key)
        items = (
            value.get("items") or value.get("stocks") or value.get("preview") or []
            if isinstance(value, Mapping) else value or []
        )
        if items:
            return [row for row in items if isinstance(row, Mapping)][:1]
    # The configured user universe is Samsung Electronics.  No market values
    # are invented when the current contract does not carry its projection.
    return [{"itemCode": "005930", "itemName": "삼성전자", "status": "UNAVAILABLE"}]


def _compact_interest(row: Mapping[str, Any]) -> dict[str, str]:
    metrics = row.get("metrics") or row.get("technicals") or row
    state = row.get("state") or row
    current = row.get("current") or {}
    def pick(*sources: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
        for source in sources:
            for key in keys:
                if source.get(key) not in (None, ""):
                    return source[key]
        return None
    return {
        "name": _text(row.get("itemName") or row.get("name"), "삼성전자"),
        "code": _text(row.get("itemCode") or row.get("code"), "005930"),
        "price": _price(pick(current, row, metrics, keys=("price", "currentPrice", "close"))),
        "ma20": _price(pick(row, metrics, keys=("ma20", "MA20"))),
        "ma60": _price(pick(row, metrics, keys=("ma60", "MA60"))),
        "trend": _display_status(pick(state, row, keys=("trend", "trendStatus")), "확인 대기"),
        "status": _display_status(row.get("status") or current.get("status") or state.get("status") or row.get("dataStatus") or row.get("qualityStatus")),
    }


def _compact_leaders(row: Mapping[str, Any]) -> str:
    leaders = row.get("leaderCandidates") or row.get("leaders") or []
    values = [
        _text(item.get('name'), "이름 확인 대기")
        for item in leaders[:3] if isinstance(item, Mapping)
    ]
    return ", ".join(values) if values else "주도주 자료 없음"


def _snapshot_rank_rows(model: Mapping[str, Any], name: str) -> list[Mapping[str, Any]]:
    snapshot = model.get("marketSnapshot") or {}
    rankings = snapshot.get("rankings") or {}
    item = rankings.get("items", {}).get(name) or {}
    return [row for row in (item.get("rows") or []) if isinstance(row, Mapping)]


def _snapshot_flow_rows(model: Mapping[str, Any]) -> list[tuple[str, str, Mapping[str, Any]]]:
    snapshot = model.get("marketSnapshot") or {}
    periods = (snapshot.get("investorFlow") or {}).get("periods") or {}
    rows = []
    for entry in periods.get("DAY") or []:
        if not isinstance(entry, Mapping):
            continue
        label = "외국인" if entry.get("investorType") == "FOREIGNER" else "기관"
        for direction in ("buy", "sell"):
            for row in (entry.get(direction) or [])[:3]:
                if isinstance(row, Mapping):
                    rows.append((label, "순매수" if direction == "buy" else "순매도", row))
    return rows


def _snapshot_number(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:,.0f}"
    return "확인 불가"


def _snapshot_amount(row: Mapping[str, Any]) -> str:
    """Render investor money only when the source explicitly vouches for it."""
    if row.get("amountStatus") not in {"AVAILABLE", "SOURCE_ZERO"}:
        return "자료 없음"
    amount = _num(row.get("amount"))
    if amount is None:
        return "자료 없음"
    return f"{int(amount // 1_000_000):,}백만원"


def _compact_snapshot_html(model: Mapping[str, Any]) -> str:
    snapshot = model.get("marketSnapshot") or {}
    if snapshot.get("status") not in {"OK", "PARTIAL"}:
        return '<article class="card"><h3>현재 스냅샷 순위·수급</h3><p>현재 실행의 NAVER 스냅샷 자료를 확인할 수 없습니다.</p></article>'
    ranking_names = (("gainers", "상승률 상위"), ("volume_surge", "거래량 급증"), ("high_52week", "52주 신고가"))
    cards = []
    for key, label in ranking_names:
        rows = _snapshot_rank_rows(model, key)
        items = "".join(
            f'<li><strong>{_escape(row.get("name") or "이름 확인 대기")}</strong> · '
            f'{_escape(_plain_pct(row.get("changeRate")))}</li>'
            for row in rows
        ) or "<li>자료 없음</li>"
        cards.append(f'<div class="industry-item"><div class="industry-head"><strong>{_escape(label)}</strong></div><ul>{items}</ul></div>')
    flow_rows = "".join(
        f'<tr><td>{_escape(investor)}</td><td>{_escape(direction)}</td><td>{_escape(row.get("name") or "이름 확인 대기")}</td><td>{_escape(_snapshot_amount(row))}</td></tr>'
        for investor, direction, row in _snapshot_flow_rows(model)
        if row.get("amountStatus") in {"AVAILABLE", "SOURCE_ZERO"}
    )
    flow = (
        '<table><thead><tr><th>구분</th><th>방향</th><th>종목</th><th>금액</th></tr></thead>'
        f'<tbody>{flow_rows or "<tr><td colspan=4>자료 없음</td></tr>"}</tbody></table>'
    )
    return '<article class="card"><div class="miniheading"><h3>현재 스냅샷 순위·수급</h3><span class="tag">NAVER 현재 관측</span></div><div class="industry-cards">' + "".join(cards) + f'</div><h4>외국인·기관 수급 상위</h4>{flow}</article>'


def _compact_snapshot_markdown(model: Mapping[str, Any]) -> list[str]:
    snapshot = model.get("marketSnapshot") or {}
    lines = ["", "### 현재 스냅샷 순위·수급"]
    if snapshot.get("status") not in {"OK", "PARTIAL"}:
        lines.append("현재 실행의 NAVER 스냅샷 자료를 확인할 수 없습니다.")
        return lines
    ranking_rows = []
    for key, label in (("gainers", "상승률 상위"), ("volume_surge", "거래량 급증"), ("high_52week", "52주 신고가")):
        for rank, row in enumerate(_snapshot_rank_rows(model, key), start=1):
            ranking_rows.append((label, rank, row.get("name") or "이름 확인 대기", _plain_pct(row.get("changeRate"))))
    lines += _markdown_table(("구분", "순위", "종목", "등락률"), ranking_rows or [("자료 없음", "-", "-", "-")])
    flow_rows = [
        (investor, direction, row.get("name") or "이름 확인 대기", _snapshot_amount(row))
        for investor, direction, row in _snapshot_flow_rows(model)
        if row.get("amountStatus") in {"AVAILABLE", "SOURCE_ZERO"}
    ]
    lines += ["", "#### 외국인·기관 수급 상위"]
    lines += _markdown_table(("구분", "방향", "종목", "금액"), flow_rows or [("자료 없음", "-", "-", "-")])
    return lines


def _compact_model(report: Mapping[str, Any], *, allow_current_observation: bool = False) -> dict[str, Any]:
    contract = report.get("reportContract") if isinstance(report.get("reportContract"), Mapping) else report
    if not isinstance(contract, Mapping):
        raise TemplateUnavailable("CONTRACT_UNAVAILABLE")
    full_market = contract.get("fullMarket") or {}
    breadth = full_market.get("breadth") or {}
    total = _num(full_market.get("totalCount"))
    advancers = _num(breadth.get("advancers"))
    decliners = _num(breadth.get("decliners"))
    unchanged = _num(breadth.get("unchanged"))
    if None in (total, advancers, decliners, unchanged) or advancers + decliners + unchanged != total:
        raise TemplateUnavailable("CONTRACT_UNAVAILABLE:BREADTH_ARITHMETIC_MISMATCH")
    v2 = contract.get("v2") or {}
    rendering = contract.get("rendering") or {}
    status = _text(contract.get("status"), "PARTIAL")
    quality = _text(contract.get("sourceQualityStatus"), "확인 대기")
    reason = _text(rendering.get("reason"), quality if status != "AVAILABLE" else "확인된 계약 자료")
    if "HTML_BLOCKED" in reason or reason in {"SOURCE_OBSERVED_PARTIAL_HTML_BLOCKED", "BASELINE_STALE_HTML_BLOCKED", "CONTRACT_UNAVAILABLE_HTML_BLOCKED"}:
        reason = "부분 자료를 포함한 현재 관측"
    else:
        reason = {
            "PARTIAL_DATA_RETAINED": "부분 자료를 포함한 관측 보고서",
            "CONFIRMED_CONTRACT": "검증된 계약 자료",
            "TRADING_DATE_UNAVAILABLE": "거래일 자료 확인 대기",
            "BASELINE_STALE": "부분 자료를 포함한 현재 관측",
        }.get(reason, "선택 자료 일부 확인 대기" if status != "AVAILABLE" else "확인된 계약 자료")
    relative_industries = _compact_relative_top5(contract, "industries")
    relative_themes = _compact_relative_top5(contract, "themes")
    v2_verified = _compact_v2_verified(contract, v2)
    v1 = contract.get("v1") or {}
    research = (contract.get("independentAnalysis") or {}).get("research") or {}
    return {
        "contract": contract,
        "asOf": _compact_date(contract.get("asOf") or report.get("asOf")),
        "asOfRaw": contract.get("asOf") or report.get("asOf"),
        "writtenAt": _compact_kst(report.get("writtenAt") or report.get("generatedAt") or contract.get("observedAt")),
        "observedAt": _compact_kst(contract.get("observedAt") or report.get("generatedAt")),
        "tradingDateStatus": _display_status(contract.get("tradingDateStatus")),
        "status": status,
        "statusLabel": _display_status(status),
        "quality": quality,
        "reason": reason,
        "renderable": bool(allow_current_observation or (status in {"AVAILABLE", "PARTIAL"} and contract.get("asOf"))),
        "indices": list(contract.get("indices") or []),
        "indexSessionLabel": _index_session_summary(list(contract.get("indices") or [])),
        "sessionContext": contract.get("sessionContext") or {"mode": "NOT_PREOPEN"},
        "fullMarket": {
            "total": total,
            "advancers": advancers,
            "decliners": decliners,
            "unchanged": unchanged,
            "declineRatio": round(decliners / total * 100, 2) if total else None,
            "metrics": full_market.get("metrics") or {},
        },
        "marketSnapshot": contract.get("marketSnapshot") or {
            "status": "UNAVAILABLE",
            "rankings": {"status": "UNAVAILABLE", "items": {}},
            "investorFlow": {"status": "UNAVAILABLE", "periods": {}},
        },
        "relativeIndustries": relative_industries,
        "relativeThemes": relative_themes,
        "v1Industries": list(v1.get("industries") or [])[:5],
        "v1Themes": list(v1.get("themes") or [])[:5],
        "v2Verified": v2_verified,
        "v2Industries": list(v2.get("industries") or [])[:5] if v2_verified else [],
        "v2Themes": list(v2.get("themes") or [])[:5] if v2_verified else [],
        "v2HoldReason": "V2 판정 보류 — 원천 검증 대기",
        "interests": [_compact_interest(row) for row in _compact_interest_rows(contract)],
        "kiwoom": contract.get("kiwoomCandidates") or {},
        "researchAnalysis": research.get("analysis") or {"status": research.get("status", "UNAVAILABLE"), "items": []},
        "notice": reason,
    }


def _compact_index_reason(row: Mapping[str, Any] | None, name: str, expected_date: Any) -> str:
    if not row:
        return f"{name} 원천이 계약에 없음"
    for key in ("reason", "error", "valueStatus", "dataStatus", "qualityStatus"):
        value = row.get(key)
        if value not in (None, "") and str(value).upper() not in {"AVAILABLE", "OK"}:
            return str(value)
    if row.get("close") is None:
        return f"{name} close 누락"
    source_time = row.get("sourceTime")
    if not source_time:
        return f"{name} sourceTime 누락"
    source_date = _compact_normalized_date(str(source_time)[:10])
    if source_date and source_date != _compact_normalized_date(expected_date):
        return f"{name} 기준일 불일치"
    return f"{name} 지수 확인 대기"


def _compact_index_available(row: Mapping[str, Any], expected_date: Any = None) -> bool:
    statuses = (row.get("status"), row.get("dataStatus"), row.get("qualityStatus"), row.get("valueStatus"))
    if any(any(token in str(value).upper() for token in ("UNAVAILABLE", "UNVERIFIED", "STALE", "INVALID")) for value in statuses if value not in (None, "")):
        return False
    if not isinstance(row.get("close"), (int, float)) or not row.get("sourceTime"):
        return False
    if expected_date is not None:
        source_date = _compact_normalized_date(str(row.get("sourceTime"))[:10])
        if source_date and source_date != _compact_normalized_date(expected_date):
            return False
    return True


def _compact_index_cards(model: Mapping[str, Any]) -> str:
    cards = []
    rows_by_name = {
        _index_code(row): row
        for row in model["indices"]
        if _index_code(row) in {"KOSPI", "KOSDAQ"}
    }
    for name in ("KOSPI", "KOSDAQ"):
        row = rows_by_name.get(name)
        available = _compact_index_available(row or {}, model.get("asOfRaw"))
        close = row.get("close") if available else None
        points = row.get("changePoints") if available else None
        rate = row.get("changeRate") if available else None
        source_time = _compact_kst(row.get("sourceTime")) if available else "원천 확인 대기"
        display_name = "코스피 (KOSPI)" if name == "KOSPI" else "코스닥 (KOSDAQ)" if name == "KOSDAQ" else name
        cards.append(
            f'<article class="card index-card"><div class="index-name">{_escape(display_name)}</div>'
            f'<div class="index-value">{_escape(f"{close:,.2f}" if isinstance(close, (int, float)) else "확인 불가")}</div>'
            f'<div class="change {_change_class(rate)}">{_escape(f"{points:+,.2f}p" if isinstance(points, (int, float)) else "확인 불가")} ({_escape(_pct(rate))})</div>'
            f'<div class="subvalue">{_escape(source_time)} · {_escape(_display_status(_index_session_label(row or {})))}</div>'
            f'{"<p class=\"muted tiny\">" + _escape(_compact_index_reason(row, name, model.get("asOfRaw"))) + "</p>" if not available else ""}</article>'
        )
    return "".join(cards)


def _compact_rank_cards(rows: list[Mapping[str, Any]], label: str) -> str:
    cards = []
    for row in rows[:5]:
        if not _compact_row_available(row):
            continue
        strength = row.get("relativeStrength")
        breadth = row.get("breadthPct")
        cards.append(
            '<div class="industry-item"><div class="industry-head">'
            f'<strong>{_escape(row.get("rank") or "")}. {_escape(row.get("name") or row.get("id"))}</strong><span class="status-chip">{_escape(_display_status(row.get("changeClass") or "관측"))}</span></div>'
            '<div class="industry-numbers">'
            f'<div class="{_change_class(strength)}">{_escape(_pct(strength))}<small>상대강도</small></div>'
            f'<div>{_escape(_plain_pct(breadth))}<small>상승 비율</small></div>'
            f'<div>{_escape(str(row.get("memberCount") or "확인 불가"))}<small>구성 종목</small></div>'
            f'<div style="grid-column:1 / -1;overflow-wrap:anywhere">{_escape(_compact_leaders(row))}<small>대표 주도주 최대 3개</small></div>'
            '</div></div>'
        )
    return "".join(cards) or f'<div class="industry-item"><strong>{_escape(label)} 자료 없음</strong><p>검증된 현재 거래일 자료를 확인 대기 중입니다.</p></div>'


def _compact_v1_cards(rows: list[Mapping[str, Any]], label: str) -> str:
    rows = [row for row in rows if _compact_row_available(row)]
    if not rows:
        return f'<div class="industry-item"><strong>운영 선정 {label} 자료 없음</strong><p>현재 거래일의 검증된 운영 선정 상태가 없습니다.</p></div>'
    return "".join(
        f'<div class="industry-item"><div class="industry-head"><strong>{_escape(row.get("name") or row.get("id"))}</strong><span class="status-chip">{_escape(_display_status(row.get("status")))}</span></div><p>운영 선정 상태 · 대표 주도주 {_escape(_compact_leaders(row))}</p></div>'
        for row in rows[:5]
    )


def _compact_research_cards(model: Mapping[str, Any]) -> str:
    research = model.get("researchAnalysis") or {}
    items = research.get("items") or []
    if not items:
        return '<div class="industry-item"><strong>네이버 리서치 자료 없음</strong><p>현재 거래일 기준의 분석 가능한 리서치가 없습니다.</p></div>'
    return "".join(
        f'<div class="industry-item"><div class="industry-head"><strong>{_escape(item.get("category") or "리서치 분석")}</strong></div><p>{_escape(item.get("text") or "자료 없음")}</p></div>'
        for item in items[:5]
    )


def _compact_v2_cards(model: Mapping[str, Any]) -> str:
    if not model["v2Verified"]:
        return '<div class="industry-item"><strong>검증 중인 후보 판정 보류 — 원천 검증 대기</strong><p>점수·운영 상태·주도주는 표시하지 않습니다.</p></div>'
    rows = []
    for label, group in (("업종", model["v2Industries"]), ("테마", model["v2Themes"])):
        for row in group:
            if _compact_row_available(row):
                score = f"{row.get('score'):.2f}" if isinstance(row.get("score"), (int, float)) else "확인 대기"
                rows.append(f'<div class="industry-item"><div class="industry-head"><strong>{_escape(label)} · {_escape(row.get("name") or row.get("id"))}</strong><span class="status-chip">{_escape(_display_status(row.get("v2Status")))}</span></div><p>점수 {_escape(score)} · 주도주 {_escape(_compact_leaders(row))}</p></div>')
    return "".join(rows) or '<div class="industry-item"><strong>V2 자료 없음</strong></div>'


def _compact_interest_cards(model: Mapping[str, Any]) -> str:
    return "".join(
        f'<article class="stock"><div class="stock-topline"><span class="stock-role">관심종목</span><span class="status-chip">{_escape(row["status"])}</span></div><div class="stock-header"><h4>{_escape(row["name"])}</h4></div><div class="stock-stats"><div class="stock-stat"><small>현재가</small><strong>{_escape(row["price"])}</strong></div><div class="stock-stat"><small>MA20</small><strong>{_escape(row["ma20"])}</strong></div><div class="stock-stat"><small>MA60</small><strong>{_escape(row["ma60"])}</strong></div><div class="stock-stat"><small>추세</small><strong>{_escape(row["trend"])}</strong></div></div></article>'
        for row in model["interests"]
    )


def _compact_kiwoom_cards(model: Mapping[str, Any]) -> str:
    rows = model["kiwoom"].get("stocks") or []
    if not rows:
        return '<article class="stock"><h4>키움 후보 자료 없음</h4><p>확인된 후보가 없습니다.</p></article>'
    def condition_text(row):
        value = row.get("conditionStatus")
        return "조건판정 자료 없음" if value in (None, "", "UNAVAILABLE") else _display_status(value)
    return "".join(
        f'<article class="stock"><div class="stock-topline"><span class="stock-role">키움 전일후보군</span><span class="status-chip">{_escape(_display_status(row.get("status") or "관측"))}</span></div><div class="stock-header"><h4>{_escape(row.get("itemName") or "이름 확인 대기")}</h4></div><div class="stock-stats"><div class="stock-stat"><small>기준값</small><strong>{_escape(_price(row.get("selectionPrice")))}</strong></div><div class="stock-stat"><small>현재가</small><strong>{_escape(_price(_first(row, "price", "currentPrice")))}</strong></div><div class="stock-stat"><small>20일 이동평균</small><strong>{_escape(_price(row.get("ma20")))}</strong></div><div class="stock-stat"><small>60일 이동평균</small><strong>{_escape(_price(row.get("ma60")))}</strong></div><div class="stock-stat"><small>기술 지표</small><strong>{_escape(_candidate_technical_text(row))}</strong></div><div class="stock-stat"><small>조건 충족 여부</small><strong>{_escape(condition_text(row))}</strong></div></div></article>'
        for row in rows
    )


def _markdown_cell(value: Any, fallback: str = "확인 대기") -> str:
    """Keep user-facing Markdown tables valid and compact."""
    text = _text(value, fallback).replace("\r", " ").replace("\n", " ")
    return text.replace("|", "\\|")


def _markdown_table(headers: tuple[str, ...], rows: list[tuple[Any, ...]]) -> list[str]:
    """Build a readable pipe table without exposing renderer metadata."""
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_markdown_cell(value) for value in row) + " |")
    return lines


def _compact_kiwoom_markdown_rows(model: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    rows = model["kiwoom"].get("stocks") or []
    result = []
    for row in rows:
        baseline = _first(row, "selectionPrice", "baselinePrice", "referencePrice", "selectedPrice")
        metrics = row.get("technicalMetrics") if isinstance(row.get("technicalMetrics"), Mapping) else {}
        technical = _candidate_technical_text(row)
        condition_value = row.get("conditionStatus")
        condition = "조건판정 자료 없음" if condition_value in (None, "", "UNAVAILABLE") else _display_status(condition_value)
        next_check = _first(row, "nextCheck", "nextObservation", "nextAction") or "다음 관찰에서 확인"
        result.append((
            _text(row.get("itemName"), "이름 확인 대기"),
            _price(baseline),
            _price(_first(row, "price", "currentPrice")),
            technical,
            condition,
            next_check,
        ))
    return result


def _compact_conclusions(model: Mapping[str, Any]) -> list[str]:
    market = model["fullMarket"]
    result = []
    if model["status"].upper() != "AVAILABLE" or model["quality"].upper() not in {"AVAILABLE", "VERIFIED"}:
        result.append(f"리포트 상태 {model['statusLabel']} — {model['reason']}")
    if model["relativeIndustries"]:
        row = model["relativeIndustries"][0]
        result.append(f"업종 상대강도 1위: {row.get('name') or row.get('id')} ({_pct(row.get('relativeStrength'))})")
    if model["relativeThemes"]:
        row = model["relativeThemes"][0]
        result.append(f"테마 상대강도 1위: {row.get('name') or row.get('id')} ({_pct(row.get('relativeStrength'))})")
    result.append(f"시장 확산: 상승 {market['advancers']:,} · 하락 {market['decliners']:,} · 보합 {market['unchanged']:,}")
    if not model["v2Verified"]:
        result.append("검증 중인 후보 판정 보류 — 원천 검증 대기")
    return result[:5]


def _compact_market_html(model: Mapping[str, Any]) -> str:
    market = model["fullMarket"]
    total = market["total"]
    metrics = market["metrics"]
    metric_html = "".join(_metric_line(label, metrics.get(key) or {}) for label, key in (("MA20 상회", "aboveMA20"), ("MA60 상회", "aboveMA60"), ("MA120 상회", "aboveMA120")))
    return f'<div class="kpis"><article class="card"><div class="kpi-label">상승 종목</div><div class="kpi-value up">{market["advancers"]:,}</div><div class="kpi-sub">전체 {total:,}종목</div></article><article class="card"><div class="kpi-label">하락 종목</div><div class="kpi-value down">{market["decliners"]:,}</div><div class="kpi-sub">하락 비율 {_plain_pct(market["declineRatio"])}</div></article><article class="card"><div class="kpi-label">보합 종목</div><div class="kpi-value">{market["unchanged"]:,}</div><div class="kpi-sub">산술 검증 PASS</div></article></div><div class="grid two"><article class="card"><h3>시장 확산</h3><div class="big-number">{total:,} <span class="muted tiny">종목</span></div><div class="breadth" role="img" aria-label="상승 하락 보합"><div class="rise" style="width:{market["advancers"] / total * 100:.2f}%"></div><div class="fall" style="width:{market["decliners"] / total * 100:.2f}%"></div><div class="flat" style="width:{market["unchanged"] / total * 100:.2f}%"></div></div><div class="legend"><span>상승 <b>{market["advancers"]:,}</b></span><span>하락 <b>{market["decliners"]:,} · {_plain_pct(market["declineRatio"])}</b></span><span>보합 <b>{market["unchanged"]:,}</b></span></div></article><article class="card"><h3>이동평균 상회 비율</h3>{metric_html}</article></div>'


def _compact_html(
    report: Mapping[str, Any],
    *,
    template_dir: Path = TEMPLATE_DIR,
    allow_current_observation: bool = False,
    _model: Mapping[str, Any] | None = None,
    _assets: Mapping[str, Any] | None = None,
) -> str:
    assets = _assets if _assets is not None else load_approved_template(template_dir)
    model = _model if _model is not None else _compact_model(
        report, allow_current_observation=allow_current_observation
    )
    if not model["renderable"]:
        raise TemplateUnavailable("CONTRACT_UNAVAILABLE:REQUIRED_RENDER_FIELDS")
    css = assets["css"] + (
        ".kpi-value{overflow-wrap:anywhere;word-break:break-word}"
        ".industry-item,.stock{overflow-wrap:anywhere}"
        "@media(max-width:520px){"
        ".wrap{width:100%;min-width:0;overflow-x:hidden}"
        ".topbar{flex-wrap:wrap;min-width:0}"
        ".brand,.nav{min-width:0}"
        ".nav{flex:1 1 100%;justify-content:flex-start;max-width:100%;overflow:visible}"
        ".section-head{flex-wrap:wrap}"
        ".section-head .label{margin-left:0;flex-basis:100%}"
        ".index-grid{grid-template-columns:1fr!important}"
        ".index-grid .index-card,.card{min-width:0;max-width:100%;width:100%}"
        ".grid,.kpis,.industry-cards,.stock-list{min-width:0;max-width:100%}"
        ".kpis{grid-template-columns:1fr}"
        ".kpi-value{overflow-wrap:anywhere;word-break:break-word}"
        ".card ul,.card li{min-width:0;overflow-wrap:anywhere;word-break:break-word}"
        "}"
    )
    status = "" if model["status"].upper() == "AVAILABLE" else f'<div class="notice"><b>부분 자료 포함</b> · {_escape(model["reason"])}</div>'
    conclusions = "".join(f"<li>{_escape(item)}</li>" for item in _compact_conclusions(model))
    output = f'''<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><title>국내증시 시장 모니터링 A v1.1</title><style>{css}</style></head><body><main class="wrap"><div class="topbar"><div class="brand">MARKET / DASHBOARD <span class="tag">A v1.1</span></div><nav aria-label="리포트 바로가기" class="nav"><a href="#part1">시장</a><a href="#part2">주도 그룹</a><a href="#part3">관심·후보</a></nav></div><header class="hero"><div class="eyebrow">KOREA MARKET INTELLIGENCE <span class="tag live">● {_escape(model["indexSessionLabel"])}</span></div><h1>국내증시 시장 모니터링</h1><p class="subtitle">{_escape(model["asOf"])} · {_escape(model["observedAt"])} 기준 · 거래일 {_escape(model["tradingDateStatus"])}</p>{status}<div class="card"><h3>핵심 결론</h3><ul>{conclusions}</ul></div></header><section class="section section-anchor" id="part1"><div class="section-head"><h2>01. 시장 대시보드</h2><span class="label">MARKET PULSE</span></div><div class="grid two index-grid">{_compact_index_cards(model)}</div>{_session_context_html(model)}{_compact_market_html(model)}<div style="height:12px"></div>{_compact_snapshot_html(model)}</section><section class="section section-anchor" id="part2"><div class="section-head"><h2>02. 주도 업종·테마</h2><span class="label">TOP 5</span></div><article class="card"><div class="miniheading"><h3>오늘의 인기 업종 TOP 5</h3><span class="tag">실측 상대강도</span></div><div class="industry-cards">{_compact_rank_cards(model["relativeIndustries"], "업종")}</div></article><div style="height:12px"></div><article class="card"><div class="miniheading"><h3>오늘의 인기 테마 TOP 5</h3><span class="tag">실측 상대강도</span></div><div class="industry-cards">{_compact_rank_cards(model["relativeThemes"], "테마")}</div></article><div style="height:12px"></div><article class="card"><div class="miniheading"><h3>운영 관심 업종</h3><span class="tag">운영 선정</span></div><div class="industry-cards">{_compact_v1_cards(model["v1Industries"], "업종")}</div></article><div style="height:12px"></div><article class="card"><div class="miniheading"><h3>운영 관심 테마</h3><span class="tag">운영 선정</span></div><div class="industry-cards">{_compact_v1_cards(model["v1Themes"], "테마")}</div></article><div style="height:12px"></div><article class="card"><div class="miniheading"><h3>추가 관찰 업종·테마</h3><span class="tag">검증 중인 후보</span></div><div class="industry-cards">{_compact_v2_cards(model)}</div></article><div style="height:12px"></div><article class="card"><div class="miniheading"><h3>네이버 리서치 분석</h3><span class="tag">별도 참고</span></div><div class="industry-cards">{_compact_research_cards(model)}</div></article></section><section class="section section-anchor" id="part3"><div class="section-head"><h2>03. 관심종목·키움 후보</h2><span class="label">WATCH POINTS</span></div><article class="card"><h3>관심종목</h3><div class="stock-list">{_compact_interest_cards(model)}</div></article><div style="height:12px"></div><article class="card"><h3>키움 전일 발굴 후보</h3><div class="stock-list">{_compact_kiwoom_cards(model)}</div></article><div class="notice"><b>NOTICE</b> · {_escape(model["notice"])}</div></section><footer class="footer">A v1.1 승인 템플릿 기반 · 기준일 {_escape(model["asOf"])} · 기준시각 {_escape(model["observedAt"])}</footer></main></body></html>'''
    return (
        output
        .replace("MARKET / DASHBOARD", "국내증시")
        .replace("KOREA MARKET INTELLIGENCE", "국내시장 관측")
        .replace("MARKET PULSE", "시장 현황")
        .replace("WATCH POINTS", "확인 항목")
        .replace("V1 운영 상태", "운영 선정")
        .replace("V2 시험 관찰", "검증 중인 후보")
        .replace("V2와 분리", "검증 중인 후보와 분리")
        .replace("공식 승격 금지", "공식 승격 전")
        .replace("NOTICE", "안내")
        .replace("A v1.1 승인 템플릿 기반", "승인 템플릿 기반")
    )


def _compact_markdown(
    report: Mapping[str, Any],
    *,
    template_dir: Path = TEMPLATE_DIR,
    allow_current_observation: bool = False,
    _model: Mapping[str, Any] | None = None,
    _template_assets: Mapping[str, Any] | None = None,
) -> str:
    if _template_assets is None:
        load_approved_template(template_dir)
    model = _model if _model is not None else _compact_model(
        report, allow_current_observation=allow_current_observation
    )
    if not model["renderable"]:
        raise TemplateUnavailable("CONTRACT_UNAVAILABLE:REQUIRED_RENDER_FIELDS")
    market = model["fullMarket"]
    lines = [
        f"# 국내증시 시장 모니터링 — {model['asOf']}",
        "",
        "| 기준일 | 기준시각 | 시장 세션 | 거래일 상태 |",
        "| --- | --- | --- | --- |",
        f"| {model['asOf']} | {model['observedAt']} | {model['indexSessionLabel']} | {model['tradingDateStatus']} |",
    ]
    if model["status"].upper() != "AVAILABLE":
        lines += ["", f"> 안내: {_display_status(model['status'])} — {model['reason']}"]
    lines += ["", "## 1. 시장 대시보드", ""]
    if (model.get("sessionContext") or {}).get("mode") == "PREOPEN":
        observation = model["sessionContext"].get("observation") or {}
        previous = model["sessionContext"].get("previousTradingDay") or {}
        lines += [
            "",
            "### 장전 관측과 직전 거래일 참고자료",
            f"당일 장전 관측: {_compact_date(observation.get('tradingDate'))} · 상태 {_display_status(observation.get('status'))}",
            f"직전 거래일 참고: {_compact_date(previous.get('tradingDate'))} · 상태 {_display_status(previous.get('status'))}",
        ]
        for row in previous.get("indices") or []:
            lines.append(f"직전 {row.get('name') or row.get('code')}: {row.get('close') if row.get('close') is not None else '확인 불가'} · {row.get('changeRate') if row.get('changeRate') is not None else '확인 불가'}% · 당일 장전 자료와 혼합하지 않음")

    rows_by_name = {
        _index_code(row): row
        for row in model["indices"]
        if _index_code(row) in {"KOSPI", "KOSDAQ"}
    }
    index_rows = []
    for name in ("KOSPI", "KOSDAQ"):
        row = rows_by_name.get(name) or {}
        available = _compact_index_available(row, model.get("asOfRaw"))
        points = row.get("changePoints")
        points_text = f"{points:+,.2f}p" if isinstance(points, (int, float)) else "확인 불가"
        display_name = "코스피 (KOSPI)" if name == "KOSPI" else "코스닥 (KOSDAQ)"
        index_rows.append((
            display_name,
            _index_close(row.get("close")) if available else "확인 불가",
            points_text if available else "확인 불가",
            _pct(row.get("changeRate")) if available else "확인 불가",
        ))
    lines += _markdown_table(("지수", "현재값", "전일 대비", "등락률"), index_rows)
    lines += ["", "### 시장 확산"]
    lines += _markdown_table(("상승", "하락", "보합", "하락 비율"), [(
        f"{market['advancers']:,}", f"{market['decliners']:,}", f"{market['unchanged']:,}",
        _plain_pct(market["declineRatio"]),
    )])
    lines += ["", "### 이동평균 상회율"]
    lines += _markdown_table(("MA20", "MA60", "MA120"), [tuple(
        _metric_line_value(market["metrics"].get(key) or {})
        for key in ("aboveMA20", "aboveMA60", "aboveMA120")
    )])
    lines += _compact_snapshot_markdown(model)
    lines += ["", "### 핵심 결론", ""] + [f"- {item}" for item in _compact_conclusions(model)]
    lines += ["", "## 2. 주도 업종·테마", "", "### 업종 상대강도 TOP 5"]
    industry_rows = [(
        row.get("rank"), row.get("name") or row.get("id"), _pct(row.get("relativeStrength")),
        _plain_pct(row.get("breadthPct")), _compact_leaders(row),
        _display_status(row.get("dataQuality") or row.get("status") or row.get("changeClass")),
    ) for row in model["relativeIndustries"]]
    lines += _markdown_table(("순위", "업종", "상대강도", "상승 비율", "대표 주도주", "상태"), industry_rows or [("-", "자료 없음", "-", "-", "-", "확인 대기")])
    lines += ["", "### 테마 상대강도 TOP 5"]
    theme_rows = [(
        row.get("rank"), row.get("name") or row.get("id"), _pct(row.get("relativeStrength")),
        _plain_pct(row.get("breadthPct")), _compact_leaders(row),
        _display_status(row.get("dataQuality") or row.get("status") or row.get("changeClass")),
    ) for row in model["relativeThemes"]]
    lines += _markdown_table(("순위", "테마", "상대강도", "상승 비율", "대표 주도주", "상태"), theme_rows or [("-", "자료 없음", "-", "-", "-", "확인 대기")])
    lines += ["", "### 운영 관심 업종"]
    v1_industry_rows = [(
        row.get("name") or row.get("id"), _display_status(row.get("status")), _compact_leaders(row)
    ) for row in model["v1Industries"] if _compact_row_available(row)]
    lines += _markdown_table(("업종", "상태", "대표 주도주"), v1_industry_rows or [("자료 없음", "확인 대기", "-")])
    lines += ["", "### 운영 관심 테마"]
    v1_theme_rows = [(
        row.get("name") or row.get("id"), _display_status(row.get("status")), _compact_leaders(row)
    ) for row in model["v1Themes"] if _compact_row_available(row)]
    lines += _markdown_table(("테마", "상태", "대표 주도주"), v1_theme_rows or [("자료 없음", "확인 대기", "-")])
    lines += ["", "### 추가 관찰 업종·테마"]
    lines += ["검증 중인 후보 판정 보류 — 원천 검증 대기" if not model["v2Verified"] else "검증된 후보 자료를 표시합니다."]
    if model["v2Verified"]:
        v2_rows = [(
            row.get("name") or row.get("id"), _display_status(row.get("v2Status")),
            f"{row.get('score'):.2f}" if isinstance(row.get("score"), (int, float)) else "확인 대기",
            _compact_leaders(row),
        ) for row in (model["v2Industries"] + model["v2Themes"])[:5]]
        lines += _markdown_table(("그룹", "상태", "점수", "대표 주도주"), v2_rows or [("자료 없음", "확인 대기", "-", "-")])
    lines += ["", "### 네이버 리서치 분석"]
    research_items = (model.get("researchAnalysis") or {}).get("items") or []
    if research_items:
        lines += _markdown_table(("분석 항목", "핵심 내용"), [
            (item.get("category") or "리서치 분석", item.get("text") or "자료 없음")
            for item in research_items[:5]
        ])
    else:
        lines.append("자료 없음")
    lines += ["", "## 3. 관심종목·키움 후보", "", "### 관심종목"]
    interest_rows = [
        (row["name"], row["price"], row["ma20"], row["ma60"], row["trend"], row["status"])
        for row in model["interests"]
    ]
    lines += _markdown_table(("종목", "현재가", "MA20", "MA60", "추세", "상태"), interest_rows or [("삼성전자", "확인 불가", "확인 불가", "확인 불가", "확인 대기", "확인 불가")])
    lines += ["", "### 키움 전일 발굴 후보"]
    lines += _markdown_table(("후보", "기준값", "현재값", "기술지표", "조건 판정", "다음 확인사항"), _compact_kiwoom_markdown_rows(model) or [("자료 없음", "-", "-", "-", "자료 없음", "전일 장마감 후 조건검색 결과 자료 없음")])
    lines += ["", f"> 안내: {model['notice']}", ""]
    return "\n".join(lines)


def _compact_validate(markdown_text: str, html_text: str, model: Mapping[str, Any]) -> list[str]:
    plain = html.unescape(html_text)
    issues: list[str] = []
    for field, token, text in (("MARKDOWN_PART1", "## 1. 시장 대시보드", markdown_text), ("MARKDOWN_PART2", "## 2. 주도 업종·테마", markdown_text), ("MARKDOWN_PART3", "## 3. 관심종목·키움 후보", markdown_text), ("HTML_PART1", 'id="part1"', plain), ("HTML_PART2", 'id="part2"', plain), ("HTML_PART3", 'id="part3"', plain)):
        if token not in text:
            issues.append(f"{field}_MISSING")
    for forbidden in ("CONTRACT_TRACE", "V2_DATA_TRACE", "TEMPLATE_TRACE", "historyDates", "sourceCommitStatus", "inputSnapshotSha256", "sourceSha256", "htmlAllowed", "contractHtmlAllowed", "HTML_BLOCKED", "manifestPath", "runId", "executionHead", "provenance", "generatedAt", "generationId", "historicalGenerationId", "historicalTargetDate", "historicalLineage", "historicalIndicatorSource", "historicalFallbackAllowed", "currentSnapshotSource", "fallbackUsed", "finality", "evidenceClass"):
        if forbidden in markdown_text or forbidden in plain:
            issues.append(f"INTERNAL_TRACE_EXPOSED:{forbidden}")
    if "<pre" in plain.lower():
        issues.append("HTML_MARKDOWN_PRE_EXPOSED")
    if "+00:00" in markdown_text or "+00:00" in plain:
        issues.append("RAW_UTC_EXPOSED")
    def markdown_section(heading: str) -> str:
        match = re.search(rf"^{re.escape(heading)}\s*$([\s\S]*?)(?=^##\s|\Z)", markdown_text, re.MULTILINE)
        return match.group(1) if match else ""

    def html_section(section_id: str) -> str:
        match = re.search(rf'<section[^>]*\bid="{re.escape(section_id)}"[^>]*>([\s\S]*?)</section>', plain, re.IGNORECASE)
        return match.group(1) if match else ""

    part1_md, part1_html = markdown_section("## 1. 시장 대시보드"), html_section("part1")
    part2_md, part2_html = markdown_section("## 2. 주도 업종·테마"), html_section("part2")
    part3_md, part3_html = markdown_section("## 3. 관심종목·키움 후보"), html_section("part3")

    required = [model["asOf"], model["tradingDateStatus"], model["indexSessionLabel"], f"{model['fullMarket']['advancers']:,}", f"{model['fullMarket']['decliners']:,}", f"{model['fullMarket']['unchanged']:,}"]
    required += [_metric_line_value(model["fullMarket"]["metrics"].get(key) or {}) for key in ("aboveMA20", "aboveMA60", "aboveMA120")]
    for value in required:
        if value and (str(value) not in markdown_text or str(value) not in plain):
            issues.append(f"CORE_VALUE_MISSING:{value}")
    for value in required[3:]:
        if value and (str(value) not in part1_md or str(value) not in part1_html):
            issues.append(f"PART1_VALUE_MISSING:{value}")
    for row in model["interests"]:
        for value in (row["name"],):
            if value and (str(value) not in part3_md or str(value) not in part3_html):
                issues.append(f"PART3_INTEREST_MISSING:{value}")
    for _compact_row in model["relativeIndustries"] + model["relativeThemes"]:
        value = _compact_row.get("name") or _compact_row.get("id")
        if value and (str(value) not in part2_md or str(value) not in part2_html):
            issues.append(f"PART2_TOP5_MISSING:{value}")
    if model["v2Verified"]:
        for row in model["v2Industries"] + model["v2Themes"]:
            name = row.get("name") or row.get("id")
            if name and (str(name) not in part2_md or str(name) not in part2_html):
                issues.append(f"PART2_V2_MISSING:{name}")
            for leader in (row.get("leaderCandidates") or row.get("leaders") or [])[:3]:
                for value in (leader.get("name"), leader.get("code")):
                    if value and (str(value) not in part2_md or str(value) not in part2_html):
                        issues.append(f"PART2_V2_LEADER_MISSING:{value}")
    if model["v2Verified"] is False and "검증 중인 후보 판정 보류 — 원천 검증 대기" not in markdown_text and "검증 중인 후보 판정 보류 — 원천 검증 대기" not in plain:
        issues.append("V2_HOLD_MISSING")
    return issues


# Override the public rendering/validation functions after the legacy
# implementation so every caller (daily_report and GitHub workflows included)
# receives the compact projection without changing the approved template.
build_dashboard_model = _compact_model
render_dashboard_html = _compact_html
render_dashboard_markdown = _compact_markdown
validate_rendered_outputs = _compact_validate


def validate_rendered_files(markdown_path: Path, html_path: Path, model: Mapping[str, Any]) -> list[str]:
    try:
        markdown_text = markdown_path.read_text(encoding="utf-8")
        html_text = html_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"RENDERED_FILE_READ_FAILED:{type(exc).__name__}"]
    return _compact_validate(markdown_text, html_text, model)


def validate_renderer_models(markdown_report: Mapping[str, Any], html_report: Mapping[str, Any]) -> list[str]:
    left = _compact_model(markdown_report)
    right = _compact_model(html_report)
    return [key for key in ("asOf", "observedAt", "tradingDateStatus", "status", "fullMarket", "relativeIndustries", "relativeThemes", "v1Industries", "v1Themes", "v2Industries", "v2Themes", "researchAnalysis", "interests", "kiwoom") if left.get(key) != right.get(key)]


def _compact_session_context_html(model: Mapping[str, Any]) -> str:
    context = model.get("sessionContext") or {}
    if context.get("mode") != "PREOPEN":
        return ""
    observation = context.get("observation") or {}
    previous = context.get("previousTradingDay") or {}
    rows = [
        f"당일 장전 관측: {_compact_date(observation.get('tradingDate'))} · 상태 {_display_status(observation.get('status'))}",
        f"직전 거래일 참고: {_compact_date(previous.get('tradingDate'))} · 상태 {_display_status(previous.get('status'))}",
    ]
    if previous.get("status") == "AVAILABLE":
        for row in previous.get("indices") or []:
            rows.append(
                f"직전 {row.get('name') or row.get('code')}: "
                f"{row.get('close') if row.get('close') is not None else '확인 불가'} · "
                f"{row.get('changeRate') if row.get('changeRate') is not None else '확인 불가'}% · "
                "당일 장전 자료와 혼합하지 않음"
            )
    return '<article class="card session-context"><h3>장전 관측과 직전 거래일 참고자료</h3>' + "".join(f"<p>{_escape(row)}</p>" for row in rows) + "</article>"


_session_context_html = _compact_session_context_html
