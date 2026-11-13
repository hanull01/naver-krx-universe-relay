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

from tools.report_lineage import lineage_from_contract
from tools.report_user_status import availability, next_check_label, user_reason_label, user_status_label


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


def _source_label(value: Any) -> str:
    text = str(value or "").strip()
    if "운영 관심" in text and ("설정" in text or "수집" in text):
        return "V1 운영 관심 설정"
    if "/" in text or "\\" in text or text.startswith("tools."):
        return "공급 단계"
    return {
        "NAVER_CURRENT_SNAPSHOT": "NAVER 현재 관측",
        "NAVER": "NAVER 공개자료",
        "KRX": "KRX 기술지표",
        "KIS_KRX_DAILY_CACHE": "KRX 일봉 자료",
        "KIWOOM_CONDITION_SEARCH": "키움 조건검색",
    }.get(text.upper(), _text(value, "자료 미수신"))


def _scope_label(value: Any) -> str:
    return {"STOCK_ONLY": "일반주식", "STOCK_ETF": "일반주식"}.get(
        str(value or "").upper(), _text(value, "범위 확인 불가")
    )


def _display_status(value: Any, fallback: str = "현재 자료로 확인 불가") -> str:
    if value in {"장후 관측", "장중 관측", "장전 관측", "시장별 세션 상이", "관측"}:
        return str(value)
    if value in {"세션 확인 대기", "세션 확인 필요"}:
        return "세션 확인 필요"
    text = str(value or "")
    mapped = user_status_label(value, fallback=fallback)
    if mapped == fallback and text and not text.isupper() and "_" not in text:
        return text
    return mapped


def _display_reason(value: Any, fallback: str = "확인 불가 사유가 기록되지 않았습니다.") -> str:
    if value in (None, ""):
        return fallback
    text = str(value)
    if text.isupper() and ("_" in text or text in {"FINAL", "SOURCE_OBSERVED", "SOURCE_VERIFIED"}):
        return user_reason_label(text, fallback)
    return text


def _sanitize_user_text(value: str) -> str:
    """Prevent raw lifecycle vocabulary from leaking into published views."""
    replacements = (
        ("BASELINE_STALE", "이전 자료 최신성 확인 실패"),
        ("finalPending", "정규장 확정 자료 수신 전"),
        ("finalConfirmed", "정규장 확정 자료 수신"),
        ("FINAL_PENDING", "정규장 확정 자료 수신 전"),
        ("FINAL_NOT_CONFIRMED", "정규장 확정 여부 확인 필요"),
        ("SOURCE_OBSERVED", "원천 수신"),
        ("SOURCE_VERIFIED", "원천 검증됨"),
        ("monitoring-daily", "구형 모니터링 경로"),
        ("MONITORING_DAILY_PRE", "구형 모니터링 경로"),
        ("확인 대기", "공급 상태 미수신"),
        ("검증 대기", "추가 관찰 기능 상태 미수신"),
        ("검증 중인 후보", "자동 산출 상태"),
        ("확인 가능", "수신됨"),
        ("자료 없음", "자료 미수신"),
        ("상태 확인 필요", "현재 자료로 확인 불가"),
        ("원천 자료 확인 필요", "확인 불가 사유가 기록되지 않았습니다."),
        ("다음 정상 수집 후", "다음 회차에서 확인"),
        ("V2", "추가 관찰"),
        ("slotLineage", "회차 연결 정보"),
        ("candidateDetails", "후보 상세"),
        ("reportLineage", "보고서 연결 정보"),
        ("current-observation", "현재 관측"),
        ("artifact", "산출 자료"),
        ("producer", "공급자"),
        ("workflow", "실행 경로"),
        ("sourceHash", "원천 식별값"),
        ("technicalEvidence", "기술 자료 근거"),
    )
    result = value
    for source, target in replacements:
        result = result.replace(source, target)
    return result


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


def _snapshot_price(value: Any) -> str:
    number = _num(value)
    return f"{number:,.0f}원" if number is not None else "자료 없음"


def _candidate_technical_text(row: Mapping[str, Any]) -> str:
    metrics = row.get("candidateTechnicalIndicators")
    if not isinstance(metrics, Mapping):
        detail = row.get("candidateDetail")
        metrics = detail.get("technicalIndicators") if isinstance(detail, Mapping) else {}
    metrics = metrics if isinstance(metrics, Mapping) else {}
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
    return "세션 확인 필요"


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
        return "세션 확인 필요"
    unique = set(labels)
    if unique == {"장후 관측"}:
        return "장후 관측"
    if unique == {"장중 관측"}:
        return "장중 관측"
    if unique == {"장전 관측"}:
        return "장전 관측"
    if "세션 확인 필요" in unique:
        return "시장별 세션 상이·세션 확인 필요" if len(unique) > 1 else "세션 확인 필요"
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
        "contractId": model["contractId"],
        "lineage": model["reportLineage"],
        "markdownSha256": hashlib.sha256(markdown_text.encode("utf-8")).hexdigest(),
        "markdownBytes": len(markdown_text.encode("utf-8")),
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
        row.get("membershipStatus"),
        row.get("sourceCommitStatus"), row.get("sourceProvenanceStatus"),
        row.get("eligibility"),
    )
    blocked = {"UNAVAILABLE", "FAILED", "STALE", "INVALID", "INELIGIBLE", "REMOVED", "WAIT", "PENDING"}
    for value in values:
        if value in (None, ""):
            continue
        normalized = str(value).upper()
        if any(token in normalized for token in blocked):
            return False
    return True


def _compact_v2_verified(contract: Mapping[str, Any], v2: Mapping[str, Any]) -> bool:
    lineage = v2.get("v2Lineage")
    if isinstance(lineage, Mapping):
        source = str(lineage.get("source") or "").upper()
        status = str(v2.get("status") or lineage.get("status") or "").upper()
        consumed = str(v2.get("consumptionStatus") or lineage.get("consumptionStatus") or "").upper()
        expected = _compact_normalized_date(contract.get("observationDate") or contract.get("asOf"))
        source_date = _compact_normalized_date(lineage.get("sourceDate"))
        observation_date = _compact_normalized_date(lineage.get("observationDate"))
        if (
            status in {"SUCCESS", "OK"}
            and consumed == "CONSUMED"
            and source == "NAVER_CURRENT_SNAPSHOT"
            and lineage.get("v2ObservationId")
            and lineage.get("sourceHash")
            and source_date == observation_date == expected
        ):
            return True
    source_commit = v2.get("sourceCommit")
    commit_status = str(v2.get("sourceCommitStatus") or "").upper()
    if not source_commit or commit_status not in {"VERIFIED", "AVAILABLE", "OK"}:
        return False
    expected = _compact_normalized_date((contract.get("tradingDateEvidence") or {}).get("tradingDate") or contract.get("asOf"))
    observed = _compact_normalized_date(v2.get("tradingDate"))
    if not expected or expected != observed:
        return False
    return str(v2.get("sourceQualityStatus") or "").upper() not in {"UNAVAILABLE", "PARTIAL", "STALE", "INVALID"}


def _v2_hold_reason(v2: Mapping[str, Any]) -> str:
    reason = str(v2.get("reason") or v2.get("sourceQualityReason") or "").upper()
    labels = {
        "SOURCE_MISSING": "입력 자료 없음",
        "TRADING_DATE_UNAVAILABLE": "기준일 확인 불가",
        "INSUFFICIENT_MEMBERS": "구성 종목 부족",
        "SOURCE_UNVERIFIED": "현재 자동 산출 기능이 없습니다(자동 산출 기능 미연결)",
        "V2_NOT_PRODUCED": "현재 자동 산출 기능이 없습니다(자동 산출 기능 미연결)",
        "NO_SCHEDULED_COLLECTION": "자료 수집 예정 없음",
        "DERIVED_ARTIFACT_MISSING": "산출 자료 없음",
        "SOURCE_GENERATION_MISMATCH": "이전 세대 자료와 불일치",
        "V2_INPUT_SOURCE_HASH_MISMATCH": "현재 관측 입력 식별자가 일치하지 않습니다.",
        "V2_INPUT_DATE_MISMATCH": "현재 관측일이 일치하지 않습니다.",
        "V2_INPUT_UNAVAILABLE": "현재 관측 입력 자료가 수신되지 않았습니다.",
        "V2_LINEAGE_INCOMPLETE": "현재 관측 산출물의 출처 정보가 완전하지 않습니다.",
        "SELECTION_NOT_MET": "산출 조건 미충족",
    }
    return labels.get(reason, "현재 자동 산출 기능이 없습니다(자동 산출 기능 미연결)")


def _compact_relative_top5(contract: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    relative = contract.get("relativeStrengthTop5") or {}
    section = relative.get(key) or {}
    if str(relative.get("status") or "").upper() in {"UNAVAILABLE", "FAILED", "INVALID"}:
        return []
    historical_date = _compact_normalized_date(relative.get("tradingDate"))
    contract_date = _compact_normalized_date(contract.get("asOf"))
    observation_date = _compact_normalized_date(contract.get("observationDate"))
    relative_observation_date = _compact_normalized_date(relative.get("observationDate"))
    rows = [row for row in (section.get("top5") or []) if isinstance(row, Mapping)]
    row_observation_dates = {
        _compact_normalized_date(row.get("sourceTradingDate"))
        for row in rows
        if row.get("sourceTradingDate")
    }
    same_historical_date = historical_date and historical_date == contract_date
    same_current_observation = observation_date and (
        relative_observation_date == observation_date
        or observation_date in row_observation_dates
    )
    if not (same_historical_date or same_current_observation):
        return []
    return [
        row for row in rows
        if _compact_row_available(row) and row.get("eligibility", "ELIGIBLE") == "ELIGIBLE"
    ][:5]


def _compact_interest_rows(contract: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    merged: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for key in ("registeredUniverse", "monitoredUniverse", "userWatchlist", "interestStocks"):
        value = contract.get(key)
        items = (
            value.get("items") or value.get("stocks") or value.get("preview") or []
            if isinstance(value, Mapping) else value or []
        )
        for row in items:
            if not isinstance(row, Mapping):
                continue
            identity = str(row.get("itemCode") or row.get("code") or row.get("symbol") or row.get("itemName") or row.get("name") or "")
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            merged.append(row)
    if merged:
        return merged
    # Do not recreate a historical or hard-coded interest list when the
    # current contract has no registered-universe projection.
    return []


def _compact_interest(row: Mapping[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") or row.get("technicals") or row
    state = row.get("state") or row
    current = row.get("current") or {}
    def pick(*sources: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
        for source in sources:
            for key in keys:
                if source.get(key) not in (None, ""):
                    return source[key]
        return None
    availability_map = row.get("availability") if isinstance(row.get("availability"), Mapping) else {}
    current_availability = availability_map.get("current") if isinstance(availability_map.get("current"), Mapping) else {}
    ma20_availability = availability_map.get("ma20") if isinstance(availability_map.get("ma20"), Mapping) else {}
    ma60_availability = availability_map.get("ma60") if isinstance(availability_map.get("ma60"), Mapping) else {}
    trend_availability = availability_map.get("trend") if isinstance(availability_map.get("trend"), Mapping) else {}
    trend_value = pick(state, row, keys=("trend", "trendStatus"))
    current_value = pick(current, row, metrics, keys=("price", "currentPrice", "close"))
    ma20_value = pick(row, metrics, keys=("ma20", "MA20"))
    ma60_value = pick(row, metrics, keys=("ma60", "MA60"))
    current_availability = current_availability or availability(
        status=current.get("status") or row.get("status"),
        required_data="관심종목 현재가", received=current_value is not None,
        producer="NAVER 현재 관측 수집",
        reason=None if current_value is not None else "CURRENT_SNAPSHOT_MISSING",
    )
    ma20_availability = ma20_availability or availability(
        status=metrics.get("status") or row.get("status"),
        required_data="20일 이동평균", received=ma20_value is not None,
        producer="KRX historical technical",
        reason=None if ma20_value is not None else "TECHNICAL_DATA_MISSING",
    )
    ma60_availability = ma60_availability or availability(
        status=metrics.get("status") or row.get("status"),
        required_data="60일 이동평균", received=ma60_value is not None,
        producer="KRX historical technical",
        reason=None if ma60_value is not None else "TECHNICAL_DATA_MISSING",
    )
    trend_availability = trend_availability or availability(
        status=state.get("status") or row.get("status"),
        required_data="현재가와 이동평균 기반 추세", received=trend_value not in (None, ""),
        producer="리포트 추세 계산",
        reason=None if trend_value not in (None, "") else "TREND_DATA_MISSING",
    )
    def ma_position(value: Any) -> str | None:
        normalized = str(value or "").upper()
        if normalized in {"ABOVE", "OVER", "UP", "상회"}:
            return "ABOVE"
        if normalized in {"BELOW", "UNDER", "DOWN", "하회"}:
            return "BELOW"
        return None
    ma20_position = ma_position(pick(state, row, keys=("priceVsMA20", "ma20Position")))
    ma60_position = ma_position(pick(state, row, keys=("priceVsMA60", "ma60Position")))
    trend_code = str(trend_value or "").upper()
    if ma20_position and ma60_position and ma20_position != ma60_position:
        trend_label = "단기·중기 방향 엇갈림"
    elif ma20_position == "ABOVE" or (not ma20_position and trend_code in {"ABOVE", "UPTREND"}):
        trend_label = "20일 이동평균 상회"
    elif ma20_position == "BELOW" or (not ma20_position and trend_code in {"BELOW", "DOWNTREND"}):
        trend_label = "20일 이동평균 하회"
    elif trend_code == "SIDEWAYS":
        trend_label = "단기·중기 방향 엇갈림"
    else:
        trend_label = "추세 계산 자료 부족" if trend_value in (None, "") else _display_status(trend_value)
    return {
        "name": _text(row.get("itemName") or row.get("name"), "이름 수신 필요"),
        "code": _text(row.get("itemCode") or row.get("code"), ""),
        "price": _price(current_value),
        "ma20": _price(ma20_value),
        "ma60": _price(ma60_value),
        "trend": trend_label,
        "status": _display_status(row.get("status") or current.get("status") or state.get("status") or row.get("dataStatus") or row.get("qualityStatus")),
        "priceStatus": current_availability.get("label") or ("수신됨" if pick(current, row, metrics, keys=("price", "currentPrice", "close")) is not None else "미수신"),
        "ma20Status": ma20_availability.get("label") or ("수신됨" if pick(row, metrics, keys=("ma20", "MA20")) is not None else "미수신"),
        "ma60Status": ma60_availability.get("label") or ("수신됨" if pick(row, metrics, keys=("ma60", "MA60")) is not None else "미수신"),
        "trendStatus": trend_availability.get("label") or ("수신됨" if trend_value not in (None, "") else "미수신"),
        "priceReason": current_availability.get("missingReason") or "원천 자료 확인 필요",
        "ma20Reason": ma20_availability.get("missingReason") or "원천 자료 확인 필요",
        "ma60Reason": ma60_availability.get("missingReason") or "원천 자료 확인 필요",
        "trendReason": trend_availability.get("missingReason") or "원천 자료 확인 필요",
        "priceAvailability": current_availability,
        "ma20Availability": ma20_availability,
        "ma60Availability": ma60_availability,
        "trendAvailability": trend_availability,
    }


def _compact_leaders(row: Mapping[str, Any]) -> str:
    leaders = row.get("leaderCandidates") or row.get("leaders") or []
    values = [
        _text(item.get('name'), "이름 확인 대기")
        for item in leaders[:3] if isinstance(item, Mapping)
    ]
    if values:
        return ", ".join(values)
    reason = row.get("leaderMissingReason") or row.get("missingReason")
    return _display_reason(reason, "대표 주도주 자료 없음") if reason else "대표 주도주 자료 없음"


def _availability_text(row: Mapping[str, Any] | None, fallback: str = "현재 자료로 확인 불가") -> str:
    if not isinstance(row, Mapping):
        return fallback
    detail = row.get("availability") or row.get("leaderAvailability")
    if not isinstance(detail, Mapping) and "requiredData" in row:
        detail = row
    if not isinstance(detail, Mapping):
        return fallback
    received = detail.get("received") is True
    base = (
        f"필요 자료: {detail.get('requiredData') or '원천 자료'} · "
        f"수신: {'예' if received else '아니오'} · "
        f"공급자: {_source_label(detail.get('producer'))}"
    )
    if received:
        parts = [base, "상태: 정상 수신"]
        if detail.get("researchSlot"):
            parts.append(f"대상 회차: {detail['researchSlot']} → {detail.get('reportSlot') or '리포트'}")
        return " · ".join(parts)
    parts = [
        base,
        f"사유: {_display_reason(detail.get('missingReason'))} · "
        f"다음 확인: {next_check_label(detail.get('nextCheckAt'))}"
    ]
    if detail.get("researchSlot"):
        parts.append(f"대상 회차: {detail['researchSlot']} → {detail.get('reportSlot') or '리포트'}")
    return " · ".join(parts)


def _group_row_status(row: Mapping[str, Any]) -> str:
    """Use evidence completeness for TOP5 row status, not rank movement."""
    relative_strength = _num(row.get("relativeStrength"))
    breadth = _num(row.get("breadthPct"))
    leaders = row.get("leaderCandidates") or row.get("leaders") or []
    if relative_strength is not None and breadth is not None and leaders:
        return "수신됨"
    if relative_strength is not None or breadth is not None or leaders:
        return "일부 자료 수신됨"
    status_code = str(row.get("leaderEvidenceStatus") or row.get("dataQuality") or row.get("status") or "").upper()
    if status_code in {"PARTIAL", "WARNING"}:
        return "일부 자료 수신됨"
    if status_code in {"AVAILABLE", "OK", "VERIFIED", "SOURCE_VERIFIED"}:
        return "수신됨"
    return "자료 미수신"


def _compact_group_lineage(rows: list[Mapping[str, Any]]) -> str:
    """Render the current-observation binding shared by HTML and Markdown."""
    row = next((item for item in rows if isinstance(item, Mapping)), {})
    source = _source_label(row.get("leaderSource") or row.get("source"))
    source_date = _compact_date(row.get("sourceDate") or row.get("leaderObservationDate"))
    observation_date = _compact_date(row.get("observationDate") or row.get("leaderObservationDate"))
    scope = _scope_label(row.get("leaderScope") or row.get("scope"))
    status = _group_row_status(row)
    return f"원천: {source} · 원천일: {source_date} · 관측일: {observation_date} · 범위: {scope} · 상태: {status}"


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


def _snapshot_flow_evidence_rows(model: Mapping[str, Any]) -> list[tuple[str, str, Mapping[str, Any]]]:
    return [
        (investor, direction, row)
        for investor, direction, row in _snapshot_flow_rows(model)
        if row.get("amountStatus") in {"AVAILABLE", "SOURCE_ZERO"}
        and _num(row.get("amount")) is not None
    ]


def _snapshot_flow_date_mismatch(model: Mapping[str, Any]) -> bool:
    flow = (model.get("marketSnapshot") or {}).get("investorFlow") or {}
    if str(flow.get("status") or "").upper() == "DATE_MISMATCH":
        return True
    dates = set()
    for _, _, row in _snapshot_flow_evidence_rows(model):
        value = row.get("sourceDate") or row.get("asOf") or row.get("tradingDate")
        if value:
            dates.add(_compact_normalized_date(value))
    return len({date for date in dates if date}) > 1


def _snapshot_flow_date_text(model: Mapping[str, Any]) -> str:
    dates: dict[str, set[str]] = {"외국인": set(), "기관": set()}
    for investor, _, row in _snapshot_flow_evidence_rows(model):
        value = row.get("sourceDate") or row.get("asOf") or row.get("tradingDate")
        normalized = _compact_normalized_date(value)
        if normalized:
            dates[investor].add(_compact_date(normalized))
    foreign = ", ".join(sorted(dates["외국인"])) or "미수신"
    institution = ", ".join(sorted(dates["기관"])) or "미수신"
    if dates["외국인"] and dates["기관"] and dates["외국인"] != dates["기관"]:
        return f"수급 자료 기준일이 달라 비교 보류 · 외국인 {foreign} · 기관 {institution}"
    if dates["외국인"] and not dates["기관"]:
        return f"외국인 자료 수신, 기관 자료 미수신 · 외국인 {foreign}"
    if dates["기관"] and not dates["외국인"]:
        return f"기관 자료 수신, 외국인 자료 미수신 · 기관 {institution}"
    if dates["외국인"] and dates["기관"]:
        return f"수급 기준일 {foreign}"
    return "수급 자료 미수신"


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


def _breadth_quality_text(current: Mapping[str, Any], total: int | float | None) -> str:
    breadth = current.get("breadth") if isinstance(current.get("breadth"), Mapping) else {}
    values = [breadth.get(key) for key in ("advancers", "decliners", "unchanged")]
    if not isinstance(total, (int, float)) or any(not isinstance(value, (int, float)) for value in values):
        return "시장 확산 자료 미수신"
    warnings = {str(value).upper() for value in current.get("qualityWarnings") or []}
    if "UNIVERSE_COMPOSITION_CHANGED" in warnings:
        return "종목 구성 변동으로 비교 주의"
    if sum(values) != total:
        return "일부 종목 자료 누락"
    return "자료 수신됨"


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
            f'현재가 {_escape(_snapshot_price(row.get("price")))} · '
            f'{_escape(_plain_pct(row.get("changeRate")))}</li>'
            for row in rows
        ) or "<li>자료 없음</li>"
        cards.append(f'<div class="industry-item"><div class="industry-head"><strong>{_escape(label)}</strong></div><ul>{items}</ul></div>')
    evidence_rows = _snapshot_flow_evidence_rows(model)
    flow_rows = "".join(
        f'<tr><td>{_escape(investor)}</td><td>{_escape(direction)}</td><td>{_escape(row.get("name") or "이름 확인 대기")}</td><td>{_escape(_snapshot_amount(row))}</td></tr>'
        for investor, direction, row in evidence_rows
        if not _snapshot_flow_date_mismatch(model)
    )
    if _snapshot_flow_date_mismatch(model):
        flow = f'<p>{_escape(_snapshot_flow_date_text(model))}</p>'
    elif not evidence_rows:
        flow = '<p>수급 자료 미수신</p>'
    else:
        flow = (
            '<table><thead><tr><th>구분</th><th>방향</th><th>종목</th><th>금액</th></tr></thead>'
            f'<tbody>{flow_rows}</tbody></table>'
        )
    return '<article class="card"><div class="miniheading"><h3>현재 스냅샷 순위·수급</h3><span class="tag">NAVER 현재가 관측</span></div><div class="industry-cards">' + "".join(cards) + f'</div><h4>외국인·기관 수급 상위</h4>{flow}</article>'


def _compact_snapshot_markdown(model: Mapping[str, Any]) -> list[str]:
    snapshot = model.get("marketSnapshot") or {}
    lines = ["", "### 현재 스냅샷 순위·수급"]
    if snapshot.get("status") not in {"OK", "PARTIAL"}:
        lines.append("현재 실행의 NAVER 스냅샷 자료를 확인할 수 없습니다.")
        return lines
    ranking_rows = []
    for key, label in (("gainers", "상승률 상위"), ("volume_surge", "거래량 급증"), ("high_52week", "52주 신고가")):
        for rank, row in enumerate(_snapshot_rank_rows(model, key), start=1):
            ranking_rows.append((label, rank, row.get("name") or "이름 확인 대기", _snapshot_price(row.get("price")), _plain_pct(row.get("changeRate"))))
    lines += _markdown_table(("구분", "순위", "종목", "현재가", "등락률"), ranking_rows or [("자료 없음", "-", "-", "-", "-")])
    evidence_rows = _snapshot_flow_evidence_rows(model)
    flow_rows = [
        (investor, direction, row.get("name") or "이름 확인 대기", _snapshot_amount(row))
        for investor, direction, row in evidence_rows
        if not _snapshot_flow_date_mismatch(model)
    ]
    lines += ["", "#### 외국인·기관 수급 상위"]
    if _snapshot_flow_date_mismatch(model):
        lines.append(_snapshot_flow_date_text(model))
    elif not evidence_rows:
        lines.append("수급 자료 미수신")
    else:
        lines += _markdown_table(("구분", "방향", "종목", "금액"), flow_rows)
    return lines


def _compact_model(report: Mapping[str, Any], *, allow_current_observation: bool = False) -> dict[str, Any]:
    contract = report.get("reportContract") if isinstance(report.get("reportContract"), Mapping) else report
    if not isinstance(contract, Mapping):
        raise TemplateUnavailable("CONTRACT_UNAVAILABLE")
    full_market = contract.get("fullMarket") or {}
    current_observation = contract.get("currentMarketObservation")
    if not isinstance(current_observation, Mapping):
        raise TemplateUnavailable("CURRENT_MARKET_OBSERVATION_UNAVAILABLE")
    current_breadth = current_observation.get("breadth") or {}
    total = _num(current_observation.get("totalCount"))
    advancers = _num(current_breadth.get("advancers"))
    decliners = _num(current_breadth.get("decliners"))
    unchanged = _num(current_breadth.get("unchanged"))
    if None in (total, advancers, decliners, unchanged):
        raise TemplateUnavailable("CURRENT_MARKET_OBSERVATION_UNAVAILABLE:BREADTH_ARITHMETIC_MISMATCH")
    historical_technical = contract.get("historicalTechnicalMarket") or {}
    if not isinstance(historical_technical, Mapping):
        historical_technical = {}
    v2 = contract.get("v2") or {}
    rendering = contract.get("rendering") or {}
    status = _text(contract.get("status"), "PARTIAL")
    quality = _text(contract.get("sourceQualityStatus"), "확인 대기")
    reason = _text(rendering.get("reason"), quality if status != "AVAILABLE" else "확인된 계약 자료")
    if "HTML_BLOCKED" in reason or reason in {"CONTRACT_UNAVAILABLE_HTML_BLOCKED"}:
        reason = "부분 자료를 포함한 현재 관측"
    else:
        reason = {
            "PARTIAL_DATA_RETAINED": "부분 자료를 포함한 관측 보고서",
            "CONFIRMED_CONTRACT": "검증된 계약 자료",
            "TRADING_DATE_UNAVAILABLE": "거래일 자료가 수신되지 않았습니다.",
        }.get(reason, _display_reason(reason, "선택 자료 일부가 수신되지 않았습니다." if status != "AVAILABLE" else "검증된 계약 자료"))
    relative_industries = _compact_relative_top5(contract, "industries")
    relative_themes = _compact_relative_top5(contract, "themes")
    v2_verified = _compact_v2_verified(contract, v2)
    v1 = contract.get("v1") or {}
    research = (contract.get("independentAnalysis") or {}).get("research") or {}
    report_lineage = lineage_from_contract(contract)
    return {
        "contract": contract,
        "contractId": report_lineage["contractId"],
        "reportLineage": report_lineage,
        "asOf": _compact_date(contract.get("asOf") or report.get("asOf")),
        "asOfRaw": contract.get("asOf") or report.get("asOf"),
        "writtenAt": _compact_kst(report.get("writtenAt") or report.get("generatedAt") or contract.get("observedAt")),
        "observedAt": _compact_kst(contract.get("observedAt") or report.get("generatedAt")),
        "tradingDateStatus": _display_status(contract.get("tradingDateStatus")),
        "observationDate": _compact_date(contract.get("observationDate") or contract.get("observedDate")),
        "observationDateStatus": _display_status(contract.get("observationDateStatus")),
        "status": status,
        "statusLabel": _display_status(status),
        "quality": quality,
        "reason": reason,
        "renderable": bool(allow_current_observation or (status in {"AVAILABLE", "PARTIAL"} and contract.get("asOf"))),
        "indices": list(contract.get("indices") or []),
        "indexSessionLabel": _index_session_summary(list(contract.get("indices") or [])),
        "sessionContext": contract.get("sessionContext") or {"mode": "NOT_PREOPEN"},
        "currentMarketObservation": {
            "source": _source_label(current_observation.get("source")),
            "observedDate": _compact_date(current_observation.get("observedDate")),
            "sourceTime": _compact_kst(current_observation.get("sourceTime")),
            "scope": _scope_label(current_observation.get("scope")),
        "qualityStatus": _breadth_quality_text(current_observation, total),
            "qualityWarnings": list(current_observation.get("qualityWarnings") or []),
        },
        "historicalTechnicalMarket": {
            "source": _source_label(historical_technical.get("source")),
            "targetDate": _compact_date(historical_technical.get("targetDate")),
            "scope": _scope_label(historical_technical.get("scope")),
            "instrumentCount": _num(historical_technical.get("instrumentCount")),
            "metrics": historical_technical.get("metrics") or {},
        },
        "fullMarket": {
            "total": total,
            "advancers": advancers,
            "decliners": decliners,
            "unchanged": unchanged,
            "declineRatio": round(decliners / total * 100, 2) if total else None,
            "metrics": historical_technical.get("metrics") or {},
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
        "relativeIndustryAvailability": ((contract.get("relativeStrengthTop5") or {}).get("industries") or {}).get("availability") or {},
        "relativeThemeAvailability": ((contract.get("relativeStrengthTop5") or {}).get("themes") or {}).get("availability") or {},
        "v1IndustryAvailability": (v1.get("availability") or {}).get("industries") or {},
        "v1ThemeAvailability": (v1.get("availability") or {}).get("themes") or {},
        "v2Verified": v2_verified,
        "v2Industries": list(v2.get("industries") or [])[:5] if v2_verified else [],
        "v2Themes": list(v2.get("themes") or [])[:5] if v2_verified else [],
        "v2HoldReason": _v2_hold_reason(v2),
        "v2Lineage": dict(v2.get("v2Lineage") or {}),
        "additionalObservations": _additional_contract_projection(contract),
        "interests": [_compact_interest(row) for row in _compact_interest_rows(contract)],
        "kiwoom": contract.get("kiwoomCandidates") or {},
        "researchAnalysis": {
            **(research.get("analysis") or {}),
            "status": research.get("status", "UNAVAILABLE"),
            "unavailableReason": research.get("unavailableReason"),
            "availability": (research.get("analysis") or {}).get("availability") or {},
            "slotLineage": research.get("slotLineage") or {},
        },
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


def _compact_rank_cards(rows: list[Mapping[str, Any]], label: str, availability_detail: Mapping[str, Any] | None = None) -> str:
    cards = []
    for row in rows[:5]:
        if not _compact_row_available(row):
            continue
        strength = row.get("relativeStrength")
        breadth = row.get("breadthPct")
        cards.append(
            '<div class="industry-item"><div class="industry-head">'
            f'<strong>{_escape(row.get("rank") or "")}. {_escape(row.get("name") or row.get("id"))}</strong><span class="status-chip">{_escape(_group_row_status(row))}</span></div>'
            '<div class="industry-numbers">'
            f'<div class="{_change_class(strength)}">{_escape(_pct(strength))}<small>상대강도</small></div>'
            f'<div>{_escape(_plain_pct(breadth))}<small>상승 비율</small></div>'
            f'<div>{_escape(str(row.get("memberCount") or "확인 불가"))}<small>구성 종목</small></div>'
            f'<div style="grid-column:1 / -1;overflow-wrap:anywhere">{_escape(_compact_leaders(row))}<small>대표 주도주 최대 3개</small></div>'
            f'<div style="grid-column:1 / -1;overflow-wrap:anywhere"><small>{_escape(_compact_group_lineage([row]))}</small></div>'
            '</div></div>'
        )
    return "".join(cards) or f'<div class="industry-item"><strong>{_escape(label)} 미수신</strong><p>{_escape(_availability_text(availability_detail))}</p></div>'


def _compact_v1_cards(rows: list[Mapping[str, Any]], label: str, availability_detail: Mapping[str, Any] | None = None) -> str:
    rows = [row for row in rows if _compact_row_available(row)]
    if not rows:
        return f'<div class="industry-item"><strong>운영 선정 {label} 미수신</strong><p>{_escape(_availability_text(availability_detail))}</p></div>'
    return "".join(
        f'<div class="industry-item"><div class="industry-head"><strong>{_escape(row.get("name") or row.get("id"))}</strong><span class="status-chip">{_escape(_display_status(row.get("status")))}</span></div><p>공급자: V1 운영 관심 설정 · 원천: 당일 NAVER 관측자료 · 대표 주도주 {_escape(_compact_leaders(row))}<br><small>{_escape(_compact_group_lineage([row]))}</small></p></div>'
        for row in rows[:5]
    )


def _compact_research_cards(model: Mapping[str, Any]) -> str:
    research = model.get("researchAnalysis") or {}
    items = research.get("items") or []
    lineage = research.get("slotLineage") or {}
    slot_text = ""
    if lineage.get("researchSlot"):
        slot_text = f'<p>대상 회차: {_escape(lineage.get("researchSlot"))} → {_escape(lineage.get("reportSlot") or "리포트")}</p>'
    if not items:
        reason = research.get("unavailableReason") or "RESEARCH_SLOT_ARTIFACT_MISSING"
        detail = research.get("availability") or {}
        display_status = research.get("status") if detail.get("received") is True else "UNAVAILABLE"
        return f'<div class="industry-item"><strong>네이버 리서치 {_escape(_display_status(display_status))}</strong>{slot_text}<p>{_escape(_display_reason(reason))}</p><p>{_escape(_availability_text(detail))}</p></div>'
    return "".join(
        f'<div class="industry-item"><div class="industry-head"><strong>{_escape(item.get("category") or "리서치 분석")}</strong></div><p>{_escape(item.get("text") or "분석 내용 미수신")}</p></div>'
        for item in items[:5]
    )


def _compact_v2_cards(model: Mapping[str, Any]) -> str:
    return _compact_additional_cards(model)



def _additional_status(value: Any) -> str:
    return {
        "AVAILABLE": "수신됨",
        "PARTIAL": "일부 수신",
        "UNAVAILABLE": "미수신",
        "PRODUCER_NOT_CONFIGURED": "자동 산출 기능 미연결",
        "NOT_YET_RUN": "아직 산출되지 않음",
        "PRODUCER_FAILED": "공급 실행 실패",
        "INPUT_UNAVAILABLE": "입력 자료 미수신",
        "NO_ELIGIBLE_TARGETS": "선정 가능한 대상 없음",
        "REPORT_LINK_FAILED": "리포트 연결 실패",
        "CONSUMED": "리포트 반영됨",
    }.get(str(value or "").upper(), "현재 자료로 확인 불가")


def _additional_reason(value: Any) -> str:
    if str(value or "").upper().startswith("ADDITIONAL_LINEAGE_MISSING"):
        return "원천·날짜·범위 연결 정보가 완전하지 않습니다."
    return {
        "ADDITIONAL_OBSERVATION_TARGETS_NOT_CONFIGURED": "추가 관찰 대상 설정이 없습니다.",
        "PRODUCER_NOT_RUN": "추가 관찰 producer가 아직 실행되지 않았습니다.",
        "STALE_ARTIFACT": "현재 관측일에 해당하는 산출물이 없습니다.",
        "TARGET_OUTPUT_MISSING": "해당 대상의 산출물이 없습니다.",
        "NO_LEADER_DATA": "현재 관측에서 대표 주도주를 찾지 못했습니다.",
        "OBSERVATION_DATE_MISMATCH": "산출물의 관측일이 현재 리포트와 다릅니다.",
        "ADDITIONAL_OBSERVATION_PARTIAL": "일부 추가 관찰 대상만 수신됐습니다.",
        "AUTO_SELECTION_INPUT_UNAVAILABLE": "추가 관찰 대상을 선정할 현재 관측 입력이 수신되지 않았습니다.",
        "NO_QUALIFIED_ADDITIONAL_TARGETS": "현재 관측에서 품질 기준을 충족한 추가 대상이 없습니다.",
        "ADDITIONAL_OBSERVATION_CONTRACT_LINK_FAILED": "추가 관찰 산출물은 수신됐지만 리포트 연결에 실패했습니다.",
    }.get(str(value or "").upper(), "확인 불가 사유가 기록되지 않았습니다.")


def _additional_contract_projection(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the renderer contract-only and distinguish a broken link."""
    value = contract.get("additionalObservations")
    if isinstance(value, Mapping):
        return dict(value)
    sources = contract.get("sources")
    source = sources.get("additionalObservations") if isinstance(sources, Mapping) else None
    if isinstance(source, Mapping) and source.get("status"):
        projection = dict(source)
        if source.get("artifactPath") and source.get("status") in {"AVAILABLE", "PARTIAL", "CONSUMED"}:
            projection["status"] = "REPORT_LINK_FAILED"
            projection["failureReason"] = "ADDITIONAL_OBSERVATION_CONTRACT_LINK_FAILED"
        projection.setdefault("industries", [])
        projection.setdefault("themes", [])
        return projection
    return {
        "status": "PRODUCER_NOT_CONFIGURED",
        "industries": [],
        "themes": [],
        "failureReason": "ADDITIONAL_OBSERVATION_TARGETS_NOT_CONFIGURED",
    }


def _additional_rows(model: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    additional = model.get("additionalObservations") or {}
    return [
        row for row in (additional.get("industries") or []) + (additional.get("themes") or [])
        if isinstance(row, Mapping)
    ]


def _additional_lineage(row: Mapping[str, Any]) -> str:
    parts = [
        f"원천: {_source_label(row.get('source'))}",
        f"원천일: {_compact_date(row.get('sourceDate'))}",
        f"관측일: {_compact_date(row.get('observationDate'))}",
        f"범위: {_scope_label(row.get('scope'))}",
    ]
    if row.get("selectionRank") not in (None, ""):
        parts.append(f"선정순위: {row['selectionRank']}")
    if row.get("selectionReason") not in (None, ""):
        parts.append(f"선정근거: {row['selectionReason']}")
    if row.get("failureReason") not in (None, ""):
        parts.append(f"원인: {_additional_reason(row['failureReason'])}")
    return " · ".join(parts)


def _compact_additional_cards(model: Mapping[str, Any]) -> str:
    additional = model.get("additionalObservations") or {}
    rows = _additional_rows(model)
    if not rows:
        return (
            '<div class="industry-item"><strong>추가 관찰 업종·테마</strong>'
            f'<p>{_escape(_additional_status(additional.get("status")))} · '
            f'{_escape(_additional_reason(additional.get("failureReason")))}</p></div>'
        )
    cards = []
    for row in rows:
        lineage = _additional_lineage(row)
        cards.append(
            f'<div class="industry-item"><div class="industry-head"><strong>'
            f'{_escape(_text(row.get("type"), "대상"))} · {_escape(_text(row.get("name"), row.get("id")))}'
            f'</strong><span class="status-chip">{_escape(_additional_status(row.get("status")))}</span></div>'
            f'<p>구성종목 {_escape(_text(row.get("membershipCount"), "확인 불가"))}개 · '
            f'대표 주도주 {_escape(_compact_leaders(row))} · 주도주 수 {_escape(_text(row.get("leaderCount"), "0"))}개<br>'
            f'<small>{_escape(lineage)}</small></p></div>'
        )
    return "".join(cards)


def _compact_v2_diagnostic_cards(model: Mapping[str, Any]) -> str:
    return ""


def _compact_v2_diagnostic_markdown(model: Mapping[str, Any]) -> list[str]:
    return []

def _compact_interest_cards(model: Mapping[str, Any]) -> str:
    if not model["interests"]:
        return '<p>등록 관심종목 미수신</p>'
    return "".join(
        f'<article class="stock"><div class="stock-topline"><span class="stock-role">관심종목</span><span class="status-chip">{_escape(row["status"])}</span></div><div class="stock-header"><h4>{_escape(row["name"])}</h4></div><div class="stock-stats"><div class="stock-stat"><small>현재가</small><strong>{_escape(row["price"])} <small>{_escape(row["priceStatus"])}</small></strong><small>{_escape(_availability_text(row["priceAvailability"]))}</small></div><div class="stock-stat"><small>20일 이동평균</small><strong>{_escape(row["ma20"])} <small>{_escape(row["ma20Status"])}</small></strong><small>{_escape(_availability_text(row["ma20Availability"]))}</small></div><div class="stock-stat"><small>60일 이동평균</small><strong>{_escape(row["ma60"])} <small>{_escape(row["ma60Status"])}</small></strong><small>{_escape(_availability_text(row["ma60Availability"]))}</small></div><div class="stock-stat"><small>추세</small><strong>{_escape(row["trend"])} <small>{_escape(row["trendStatus"])}</small></strong><small>{_escape(_availability_text(row["trendAvailability"]))}</small></div></div></article>'
        for row in model["interests"]
    )


def _compact_kiwoom_cards(model: Mapping[str, Any]) -> str:
    rows = model["kiwoom"].get("stocks") or []
    if not rows:
        status = str(model["kiwoom"].get("status") or "").upper()
        if status in {"AVAILABLE_EMPTY", "NO_RESULTS"}:
            return '<article class="stock"><h4>키움 후보 없음</h4><p>전일 조건검색 결과가 0건입니다.</p></article>'
        if status in {"UNAVAILABLE", "MISSING", "NOT_FOUND"}:
            return '<article class="stock"><h4>키움 후보 자료 미수신</h4><p>해당 날짜 후보 자료가 수신되지 않았습니다.</p></article>'
        return '<article class="stock"><h4>키움 후보 자료 확인 불가</h4><p>후보 자료의 날짜 또는 형식을 확인할 수 없습니다.</p></article>'
    def condition_text(row):
        details = row.get("candidateConditionDetails")
        if not isinstance(details, Mapping):
            detail = row.get("candidateDetail")
            details = detail.get("conditionDetails") if isinstance(detail, Mapping) else {}
        if details.get("matched") is True or row.get("conditionResult") is True:
            return "조건검색 충족"
        if details.get("matched") is False or row.get("conditionResult") is False:
            return "조건검색 미충족"
        reason = row.get("candidateFailureReason") or []
        if isinstance(reason, list) and reason:
            return "조건 상세 미수신 · " + ", ".join(_display_reason(item) for item in reason)
        return "조건 상세 미수신"
    def failure_text(row):
        reasons = row.get("candidateFailureReason") or []
        if isinstance(reasons, str):
            reasons = [reasons]
        return ", ".join(_display_reason(item) for item in reasons) or "없음"
    def selection_text(row):
        value = row.get("candidateSelectionValue")
        return _price(value) if value is not None else "기준값 미수신"
    def current_text(row):
        value = _first(row, "price", "currentPrice")
        return _price(value) if value is not None else "현재가 미수신"
    return "".join(
        f'<article class="stock"><div class="stock-topline"><span class="stock-role">키움 전일후보군</span><span class="status-chip">{_escape(_display_status(row.get("candidateDetailStatus") or row.get("status") or "관측"))}</span></div><div class="stock-header"><h4>{_escape(row.get("itemName") or "이름 수신 필요")}</h4></div><div class="stock-stats"><div class="stock-stat"><small>기준값</small><strong>{_escape(selection_text(row))}</strong></div><div class="stock-stat"><small>현재가</small><strong>{_escape(current_text(row))}</strong></div><div class="stock-stat"><small>기술 지표</small><strong>{_escape(_candidate_technical_text(row))}</strong></div><div class="stock-stat"><small>조건 충족 여부</small><strong>{_escape(condition_text(row))}</strong></div><div class="stock-stat"><small>실패 원인</small><strong>{_escape(failure_text(row))}</strong></div></div></article>'
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
        selection_price = row.get("candidateSelectionValue")
        technical = _candidate_technical_text(row)
        details = row.get("candidateConditionDetails")
        if not isinstance(details, Mapping):
            details = {}
        condition = ("조건검색 충족" if details.get("matched") is True or row.get("conditionResult") is True else
                     "조건검색 미충족" if details.get("matched") is False or row.get("conditionResult") is False else
                     "조건 상세 미수신")
        next_check = _first(row, "nextCheck", "nextObservation", "nextAction") or "다음 관찰에서 확인"
        result.append((
            _text(row.get("itemName"), "이름 수신 필요"),
            _price(selection_price) if selection_price is not None else "기준값 미수신",
            _price(_first(row, "price", "currentPrice")) if _first(row, "price", "currentPrice") is not None else "현재가 미수신",
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
    additional = model.get("additionalObservations") or {}
    additional_status = str(additional.get("status") or "").upper()
    if not model["v2Verified"] and not _additional_rows(model):
        if additional_status == "PRODUCER_NOT_CONFIGURED":
            result.append("추가 관찰 자료: 자동 산출 기능 미연결")
        elif additional_status not in {"AVAILABLE", "PARTIAL", "CONSUMED"}:
            result.append("추가 관찰 자료: 현재 수신되지 않음")
    return result[:5]


def _compact_market_html(model: Mapping[str, Any]) -> str:
    market = model["fullMarket"]
    current = model["currentMarketObservation"]
    historical = model["historicalTechnicalMarket"]
    total = market["total"]
    metrics = historical["metrics"]
    historical_count = historical["instrumentCount"]
    historical_count_label = (
        f"{historical_count:,}"
        if isinstance(historical_count, (int, float)) and not isinstance(historical_count, bool)
        else _escape(historical_count)
    )
    metric_html = "".join(_metric_line(label, metrics.get(key) or {}) for label, key in (("MA20 상회", "aboveMA20"), ("MA60 상회", "aboveMA60"), ("MA120 상회", "aboveMA120")))
    current_meta = f'원천 {_escape(current["source"])} · 관측일 {_escape(current["observedDate"])} · 범위 {_escape(current["scope"])} · 상태 {_escape(current["qualityStatus"])}'
    historical_meta = f'원천 {_escape(historical["source"])} · 기준일 {_escape(historical["targetDate"])} · 범위 {_escape(historical["scope"])} · 대상 {historical_count_label}종목'
    return f'<div class="kpis"><article class="card"><div class="kpi-label">상승 종목</div><div class="kpi-value up">{market["advancers"]:,}</div><div class="kpi-sub">전체 {total:,}종목</div></article><article class="card"><div class="kpi-label">하락 종목</div><div class="kpi-value down">{market["decliners"]:,}</div><div class="kpi-sub">하락 비율 {_plain_pct(market["declineRatio"])}</div></article><article class="card"><div class="kpi-label">보합 종목</div><div class="kpi-value">{market["unchanged"]:,}</div><div class="kpi-sub">산술 검증 PASS</div></article></div><div class="grid two"><article class="card"><h3>시장 확산 <span class="tag">NAVER 현재시장 관측</span></h3><p class="muted tiny">{current_meta}</p><div class="big-number">{total:,} <span class="muted tiny">종목</span></div><div class="breadth" role="img" aria-label="상승 하락 보합"><div class="rise" style="width:{market["advancers"] / total * 100:.2f}%"></div><div class="fall" style="width:{market["decliners"] / total * 100:.2f}%"></div><div class="flat" style="width:{market["unchanged"] / total * 100:.2f}%"></div></div><div class="legend"><span>상승 <b>{market["advancers"]:,}</b></span><span>하락 <b>{market["decliners"]:,} · {_plain_pct(market["declineRatio"])}</b></span><span>보합 <b>{market["unchanged"]:,}</b></span></div></article><article class="card"><h3>이동평균 상회 비율 <span class="tag">KRX 기술지표</span></h3><p class="muted tiny">{historical_meta}</p>{metric_html}</article></div>'


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
    lineage = model["reportLineage"]
    output = f'''<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark"><title>국내증시 시장 모니터링 A v1.1</title><style>{css}</style></head><body><main class="wrap"><div class="topbar"><div class="brand">MARKET / DASHBOARD <span class="tag">A v1.1</span></div><nav aria-label="리포트 바로가기" class="nav"><a href="#part1">시장</a><a href="#part2">주도 그룹</a><a href="#part3">관심·후보</a></nav></div><header class="hero"><div class="eyebrow">KOREA MARKET INTELLIGENCE <span class="tag live">● {_escape(model["indexSessionLabel"])}</span></div><h1>국내증시 시장 모니터링</h1><p class="subtitle">{_escape(model["asOf"])} · {_escape(model["observedAt"])} 기준 · 관측일 {_escape(model["observationDateStatus"])} · 거래일 {_escape(model["tradingDateStatus"])}</p>{status}<div class="card"><h3>핵심 결론</h3><ul>{conclusions}</ul></div></header><section class="section section-anchor" id="part1"><div class="section-head"><h2>01. 시장 대시보드</h2><span class="label">MARKET PULSE</span></div><div class="grid two index-grid">{_compact_index_cards(model)}</div>{_session_context_html(model)}{_compact_market_html(model)}<div style="height:12px"></div>{_compact_snapshot_html(model)}</section><section class="section section-anchor" id="part2"><div class="section-head"><h2>02. 주도 업종·테마</h2><span class="label">TOP 5</span></div><article class="card"><div class="miniheading"><h3>오늘의 인기 업종 TOP 5</h3><span class="tag">실측 상대강도</span></div><div class="industry-cards">{_compact_rank_cards(model["relativeIndustries"], "업종", model.get("relativeIndustryAvailability"))}</div></article><div style="height:12px"></div><article class="card"><div class="miniheading"><h3>오늘의 인기 테마 TOP 5</h3><span class="tag">실측 상대강도</span></div><div class="industry-cards">{_compact_rank_cards(model["relativeThemes"], "테마", model.get("relativeThemeAvailability"))}</div></article><div style="height:12px"></div><article class="card"><div class="miniheading"><h3>운영 관심 업종</h3><span class="tag">운영 선정</span></div><div class="industry-cards">{_compact_v1_cards(model["v1Industries"], "업종", model.get("v1IndustryAvailability"))}</div></article><div style="height:12px"></div><article class="card"><div class="miniheading"><h3>운영 관심 테마</h3><span class="tag">운영 선정</span></div><div class="industry-cards">{_compact_v1_cards(model["v1Themes"], "테마", model.get("v1ThemeAvailability"))}</div></article><div style="height:12px"></div><article class="card"><div class="miniheading"><h3>추가 관찰 업종·테마</h3><span class="tag">추가 관찰 대상</span></div><div class="industry-cards">{_compact_v2_cards(model)}</div></article><div style="height:12px"></div><article class="card"><div class="miniheading"><h3>네이버 리서치 분석</h3><span class="tag">별도 참고</span></div><div class="industry-cards">{_compact_research_cards(model)}</div></article></section><section class="section section-anchor" id="part3"><div class="section-head"><h2>03. 관심종목·키움 후보</h2><span class="label">확인 항목</span></div><article class="card"><h3>관심종목</h3><div class="stock-list">{_compact_interest_cards(model)}</div></article><div style="height:12px"></div><article class="card"><h3>키움 전일 발굴 후보</h3><div class="stock-list">{_compact_kiwoom_cards(model)}</div></article><div class="notice"><b>안내</b> · {_escape(model["notice"])}</div></section><footer class="footer">승인 템플릿 기반 · 기준일 {_escape(model["asOf"])} · 기준시각 {_escape(model["observedAt"])}</footer></main></body></html>'''
    return _sanitize_user_text(
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
        "| 기준일 | 기준시각 | 시장 세션 | 관측일 상태 | 거래일 상태 |",
        "| --- | --- | --- | --- | --- |",
        f"| {model['asOf']} | {model['observedAt']} | {model['indexSessionLabel']} | {model['observationDateStatus']} | {model['tradingDateStatus']} |",
    ]
    if model["status"].upper() != "AVAILABLE":
        lines += ["", f"> 안내: {_display_status(model['status'])} — {model['reason']}"]
    lines += ["", "## 1. 시장 대시보드", "", f"시장 세션: {model['indexSessionLabel']}"]
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
    current = model["currentMarketObservation"]
    historical = model["historicalTechnicalMarket"]
    historical_count = historical["instrumentCount"]
    historical_count_label = (
        f"{historical_count:,}"
        if isinstance(historical_count, (int, float)) and not isinstance(historical_count, bool)
        else _escape(historical_count)
    )
    lines += ["", "### 시장 확산", f"원천: {current['source']} · 관측일: {current['observedDate']} · 범위: {current['scope']} · 대상: {market['total']:,}종목 · 상태: {current['qualityStatus']}"]
    lines += _markdown_table(("상승", "하락", "보합", "하락 비율"), [(
        f"{market['advancers']:,}", f"{market['decliners']:,}", f"{market['unchanged']:,}",
        _plain_pct(market["declineRatio"]),
    )])
    lines += ["", "### 이동평균 상회율", f"원천: {historical['source']} · 기준일: {historical['targetDate']} · 범위: {historical['scope']} · 대상: {historical_count_label}종목"]
    lines += _markdown_table(("MA20", "MA60", "MA120"), [tuple(
        _metric_line_value(market["metrics"].get(key) or {})
        for key in ("aboveMA20", "aboveMA60", "aboveMA120")
    )])
    lines += _compact_snapshot_markdown(model)
    lines += ["", "### 핵심 결론", ""] + [f"- {item}" for item in _compact_conclusions(model)]
    lines += ["", "## 2. 주도 업종·테마", "", "### 업종 상대강도 TOP 5"]
    lines += [_compact_group_lineage(model["relativeIndustries"])]
    industry_rows = [(
        row.get("rank"), row.get("name") or row.get("id"), _pct(row.get("relativeStrength")),
        _plain_pct(row.get("breadthPct")), _compact_leaders(row),
        _group_row_status(row),
    ) for row in model["relativeIndustries"]]
    if not industry_rows:
        lines.append(_availability_text(model.get("relativeIndustryAvailability")))
    lines += _markdown_table(("순위", "업종", "상대강도", "상승 비율", "대표 주도주", "상태"), industry_rows or [("-", "자료 미수신", "-", "-", "-", "자료 미수신")])
    lines += ["", "### 테마 상대강도 TOP 5"]
    lines += [_compact_group_lineage(model["relativeThemes"])]
    theme_rows = [(
        row.get("rank"), row.get("name") or row.get("id"), _pct(row.get("relativeStrength")),
        _plain_pct(row.get("breadthPct")), _compact_leaders(row),
        _group_row_status(row),
    ) for row in model["relativeThemes"]]
    if not theme_rows:
        lines.append(_availability_text(model.get("relativeThemeAvailability")))
    lines += _markdown_table(("순위", "테마", "상대강도", "상승 비율", "대표 주도주", "상태"), theme_rows or [("-", "자료 미수신", "-", "-", "-", "자료 미수신")])
    lines += ["", "### 운영 관심 업종"]
    lines.append("공급자: V1 운영 관심 설정")
    lines += [_compact_group_lineage(model["v1Industries"])]
    v1_industry_rows = [(
        row.get("name") or row.get("id"), _display_status(row.get("status")), _compact_leaders(row)
    ) for row in model["v1Industries"] if _compact_row_available(row)]
    if not v1_industry_rows:
        lines.append(_availability_text(model.get("v1IndustryAvailability")))
    lines += _markdown_table(("업종", "상태", "대표 주도주"), v1_industry_rows or [("운영 관심 업종 미설정", "운영 관심 대상 미설정", "-")])
    lines += ["", "### 운영 관심 테마"]
    lines.append("공급자: V1 운영 관심 설정")
    lines += [_compact_group_lineage(model["v1Themes"])]
    v1_theme_rows = [(
        row.get("name") or row.get("id"), _display_status(row.get("status")), _compact_leaders(row)
    ) for row in model["v1Themes"] if _compact_row_available(row)]
    if not v1_theme_rows:
        lines.append(_availability_text(model.get("v1ThemeAvailability")))
    lines += _markdown_table(("테마", "상태", "대표 주도주"), v1_theme_rows or [("운영 관심 테마 미설정", "운영 관심 대상 미설정", "-")])
    lines += ["", "### 추가 관찰 업종·테마"]
    additional = model.get("additionalObservations") or {}
    additional_rows = _additional_rows(model)
    if not additional_rows:
        lines.append(f"{_additional_status(additional.get('status'))} — {_additional_reason(additional.get('failureReason'))}")
    else:
        rows = [(
            row.get("name") or row.get("id"),
            _additional_status(row.get("status")),
            row.get("membershipCount") if row.get("membershipCount") is not None else "확인 불가",
            row.get("leaderCount") if row.get("leaderCount") is not None else 0,
            _compact_leaders(row),
            _additional_lineage(row),
        ) for row in additional_rows]
        lines += _markdown_table(("대상", "상태", "구성종목", "주도주 수", "대표 주도주", "원천·기준일·범위·사유"), rows)
    lines += _compact_v2_diagnostic_markdown(model)
    lines += ["", "### 네이버 리서치 분석"]
    research_items = (model.get("researchAnalysis") or {}).get("items") or []
    if research_items:
        lines += _markdown_table(("분석 항목", "핵심 내용"), [
            (item.get("category") or "리서치 분석", item.get("text") or "자료 없음")
            for item in research_items[:5]
        ])
    else:
        research_reason = (model.get("researchAnalysis") or {}).get("unavailableReason") or "RESEARCH_SLOT_ARTIFACT_MISSING"
        research_labels = {
            "RESEARCH_SLOT_REQUIRED": "이 리포트에 지정된 리서치 회차가 없습니다.",
            "RESEARCH_SLOT_ARTIFACT_MISSING": "해당 회차 산출물이 아직 도착하지 않았습니다.",
            "RESEARCH_SLOT_PRODUCER_FAILED": "해당 회차 수집 단계가 실패했습니다.",
            "RESEARCH_SLOT_ARTIFACT_FAILED": "해당 회차 산출물 생성이 실패했습니다.",
            "RESEARCH_SLOT_NOT_FRESH": "해당 회차 산출물이 최신성 검증을 통과하지 못했습니다.",
            "RESEARCH_SLOT_DATE_MISMATCH": "해당 회차 산출물의 기준일이 리포트와 다릅니다.",
        }
        availability_detail = (model.get("researchAnalysis") or {}).get("availability") or {}
        lines.append(f"리서치: {_display_status((model.get('researchAnalysis') or {}).get('status'))} — {_display_reason(research_reason)}")
        lineage = (model.get("researchAnalysis") or {}).get("slotLineage") or {}
        if lineage.get("researchSlot"):
            lines.append(f"대상 회차: {lineage.get('researchSlot')} → {lineage.get('reportSlot') or '리포트'}")
        lines.append(_availability_text(availability_detail))
    lines += ["", "## 3. 관심종목·키움 후보", "", "### 관심종목"]
    interest_rows = [
        (
            row["name"],
            f"{row['price']} · {row['priceStatus']} · {_availability_text(row['priceAvailability'])}",
            f"{row['ma20']} · {row['ma20Status']} · {_availability_text(row['ma20Availability'])}",
            f"{row['ma60']} · {row['ma60Status']} · {_availability_text(row['ma60Availability'])}",
            f"{row['trend']} · {row['trendStatus']} · {_availability_text(row['trendAvailability'])}",
            row["status"],
        )
        for row in model["interests"]
    ]
    lines += _markdown_table(("종목", "현재가", "20일 이동평균", "60일 이동평균", "추세", "상태"), interest_rows or [("등록 관심종목 미수신", "-", "-", "-", "-", "-")])
    lines += ["", "### 키움 전일 발굴 후보"]
    lines += _markdown_table(("후보", "기준값", "현재값", "기술지표", "조건 판정", "다음 확인사항"), _compact_kiwoom_markdown_rows(model) or [("후보 없음", "-", "-", "-", "후보 없음", "전일 조건검색 결과가 0건입니다.")])
    lines += ["", f"> 안내: {model['notice']}", ""]
    return _sanitize_user_text("\n".join(lines))


def _compact_validate(markdown_text: str, html_text: str, model: Mapping[str, Any]) -> list[str]:
    plain = html.unescape(html_text)
    issues: list[str] = []
    lineage = model.get("reportLineage") or {}
    for field, token, text in (("MARKDOWN_PART1", "## 1. 시장 대시보드", markdown_text), ("MARKDOWN_PART2", "## 2. 주도 업종·테마", markdown_text), ("MARKDOWN_PART3", "## 3. 관심종목·키움 후보", markdown_text), ("HTML_PART1", 'id="part1"', plain), ("HTML_PART2", 'id="part2"', plain), ("HTML_PART3", 'id="part3"', plain)):
        if token not in text:
            issues.append(f"{field}_MISSING")
    for forbidden in ("CONTRACT_TRACE", "V2_DATA_TRACE", "TEMPLATE_TRACE", "historyDates", "sourceCommitStatus", "inputSnapshotSha256", "sourceSha256", "sourcePath", "technicalProvenance", "technicalSourceDate", "technicalGenerationId", "technicalReceiptStatus", "htmlAllowed", "contractHtmlAllowed", "HTML_BLOCKED", "manifestPath", "runId", "executionHead", "provenance", "generatedAt", "generationId", "historicalGenerationId", "historicalTargetDate", "historicalLineage", "historicalIndicatorSource", "historicalFallbackAllowed", "currentSnapshotSource", "fallbackUsed", "finality", "evidenceClass", "contractId", "reportLineage", "slotLineage", "candidateDetails", "current-observation", "sourceHash", "technicalEvidence", "workflow", "commit", "producer", "artifact"):
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

    required = [model["asOf"], model["tradingDateStatus"], model["observationDateStatus"], model["indexSessionLabel"], f"{model['fullMarket']['advancers']:,}", f"{model['fullMarket']['decliners']:,}", f"{model['fullMarket']['unchanged']:,}"]
    required += [_metric_line_value(model["historicalTechnicalMarket"]["metrics"].get(key) or {}) for key in ("aboveMA20", "aboveMA60", "aboveMA120")]
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
    # V2/shadow diagnostics remain in the internal contract only. They are
    # deliberately not required in user-facing HTML/Markdown.
    def _contains_rendered(value: Any, text: str) -> bool:
        if value in (None, ""):
            return True
        raw = str(value)
        if raw in text:
            return True
        normalized = _compact_normalized_date(value)
        return bool(normalized and _compact_date(normalized) in text)

    for row in _additional_rows(model):
        name = row.get("name") or row.get("id")
        if name and (str(name) not in part2_md or str(name) not in part2_html):
            issues.append(f"PART2_ADDITIONAL_MISSING:{name}")
        for field in ("source", "sourceDate", "observationDate", "scope"):
            value = row.get(field)
            if value in (None, ""):
                continue
            # Lineage remains authoritative in the contract/manifest. User-facing
            # views deliberately use localized source/scope labels, so validate
            # the public representation rather than requiring internal tokens.
            display_value = value
            if field == "source":
                display_value = _source_label(value)
            elif field == "scope":
                display_value = _scope_label(value)
            if not _contains_rendered(display_value, part2_md) or not _contains_rendered(display_value, part2_html):
                issues.append(f"PART2_ADDITIONAL_LINEAGE_MISSING:{value}")
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
    return [key for key in ("asOf", "observedAt", "tradingDateStatus", "observationDateStatus", "status", "currentMarketObservation", "historicalTechnicalMarket", "fullMarket", "relativeIndustries", "relativeThemes", "v1Industries", "v1Themes", "v2Industries", "v2Themes", "additionalObservations", "researchAnalysis", "interests", "kiwoom") if left.get(key) != right.get(key)]


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
