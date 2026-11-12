#!/usr/bin/env python3

import argparse
import json
import os
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import trading_discovery
from tools.market_dashboard_renderer import (
    TemplateUnavailable,
    render_dashboard_html,
    render_dashboard_markdown,
    render_contract_views,
)
from tools.report_contract import build_report_contract, validate_kiwoom_candidate_binding
from tools.v2_source_provenance import build_source_provenance
from tools.kiwoom_performance import build_kiwoom_performance, load_naver_daily_prices
from tools.v2_change_analysis import compare_v2_changes
from tools.full_market_technical_adapter import build_report_input as build_staged_technical_input
from tools.market_snapshot_projection import validate_for_report as validate_market_snapshot_for_report
from tools.report_user_status import user_status_label

KST = ZoneInfo("Asia/Seoul")

STATUS_LABELS = {
    "AVAILABLE": "수신됨",
    "AVAILABLE_EMPTY": "수신됨(대상 없음)",
    "OK": "정상 수신",
    "SUCCESS": "정상 수신",
    "PARTIAL": "일부 수신",
    "UNAVAILABLE": "미수신",
    "UNUSABLE": "사용 불가",
    "DISABLED_PENDING_RESEARCH_GATE": "현재 자동 산출 기능 없음",
    "MONITORED_UNIVERSE_EMPTY": "관심종목 미설정",
    "MONITORED_UNIVERSE_CONFIGURED": "관심종목 설정됨",
}

CANDIDATE_BUCKET_LABELS = {
    "REGULAR_BREAKOUT_STRONG": "정규장 돌파 강도 관찰",
    "REGULAR_BREAKOUT_FAILED_AFTER_RECOVERY": "정규장 돌파 실패 후 회복 관찰",
    "REGULAR_ABOVE_MA_MOMENTUM": "정규장 MA20·MA60 상회 관찰",
    "NEAR_BREAKOUT_WATCH": "돌파 근접 관찰",
    "PULLBACK_WATCH": "눌림목 관찰",
}

CANDIDATE_EXCLUSION_LABELS = {
    "REGULAR_SESSION_UNAVAILABLE": "정규장 확정 증거 미확인",
}

CANDIDATE_VOLUME_LABELS = {
    "surge": "급증",
    "elevated": "증가",
    "normal": "정상 범위",
    "unknown": "확인 불가",
}

CANDIDATE_STATE_LABELS = {
    "above": "상회",
    "below": "하회",
    "confirmed": "확인",
    "failed": "실패",
    "none": "없음",
    "near_breakout20": "20일 돌파 근접",
    "near_breakout60": "60일 돌파 근접",
    "pullback_ma20": "MA20 눌림",
    "pullback_ma60": "MA60 눌림",
}

CANDIDATE_DATE_REASON_LABELS = {
    "REPORT_AS_OF_MISSING": "리포트 기준일 미확인",
    "REGULAR_SOURCE_DATE_MISSING": "정규장 원천 날짜 미확인",
    "REGULAR_SOURCE_DATE_MISMATCH": "정규장 원천 날짜 불일치",
}


def korean_status(value):
    """Render status labels for readers while retaining raw JSON values."""
    return user_status_label(value)


def display_number(value, suffix=""):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:,.2f}{suffix}"
    return "확인 불가"


def display_candidate_state(value):
    if value in (None, ""):
        return "확인 불가"
    return CANDIDATE_STATE_LABELS.get(str(value), str(value))


def comparable_report_date(value):
    if value in (None, ""):
        return None
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def split_candidates_for_markdown(candidates, report_as_of):
    """Keep only candidates with regular-session evidence for report asOf."""
    verified = []
    unverified = []
    comparable_report_as_of = comparable_report_date(report_as_of)
    for candidate in candidates:
        regular = candidate.get("regularSession") or {}
        source_date = regular.get("sourceDate")
        comparable_source_date = comparable_report_date(source_date)
        if not comparable_report_as_of:
            reason = "REPORT_AS_OF_MISSING"
        elif not comparable_source_date:
            reason = "REGULAR_SOURCE_DATE_MISSING"
        elif comparable_source_date != comparable_report_as_of:
            reason = "REGULAR_SOURCE_DATE_MISMATCH"
        else:
            verified.append(candidate)
            continue
        unverified.append((candidate, reason))
    return verified, unverified


def discovery_source_path(report_date):
    """Resolve the report-date snapshot without falling back to future latest data."""
    configured = Path(TRADING_DISCOVERY_PATH)
    if configured == Path(DEFAULT_TRADING_DISCOVERY_PATH):
        return None
    return configured


def discovery_quotes_source_path(report_date):
    """Resolve same-date supplemental quotes in the production path."""
    configured = Path(TRADING_DISCOVERY_QUOTES_PATH)
    if configured == Path(DEFAULT_TRADING_DISCOVERY_QUOTES_PATH):
        return None
    return configured

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
UNIVERSE_PATH = ROOT / "config" / "universe.json"

QUOTES_PATH = DATA / "quotes.json"
TECHNICALS_PATH = DATA / "technicals.json"
STATES_PATH = DATA / "states.json"
GROUP_STATES_PATH = DATA / "group-states.json"
RESEARCH_PATH = DATA / "research" / "intelligence" / "latest.json"
RESEARCH_MANIFEST_PATH = DATA / "research" / "manifest.json"
RESEARCH_SLOTS_PATH = DATA / "research" / "intelligence" / "slots"
MONITORING_PATH = DATA / "monitoring"
TRADING_DISCOVERY_PATH = DATA / "trading-discovery" / "latest.json"
TRADING_DISCOVERY_QUOTES_PATH = DATA / "trading-discovery" / "quotes" / "latest.json"
DEFAULT_TRADING_DISCOVERY_PATH = TRADING_DISCOVERY_PATH
DEFAULT_TRADING_DISCOVERY_QUOTES_PATH = TRADING_DISCOVERY_QUOTES_PATH
FULL_MARKET_SIGNALS_PATH = MONITORING_PATH / "latest-stock-signals.json"
MARKET_INDICES_PATH = DATA / "market" / "indices.json"
MARKET_SNAPSHOT_PROJECTION_PATH = DATA / "market" / "report-snapshot-projection.json"
MARKET_UNIVERSE_PATH = DATA / "market-universe" / "all-assets.json"
DYNAMIC_UNIVERSE_PATH = DATA / "dynamic-universe" / "latest.json"
V2_SHADOW_LATEST_PATH = DATA / "dynamic-universe" / "v2-shadow-latest.json"
V2_SHADOW_HISTORY_PATH = DATA / "dynamic-universe" / "v2-shadow-history.json"
V2_SHADOW_STATUS_PATH = DATA / "dynamic-universe" / "v2-shadow-run-status.json"


def compact_date(value):
    """Return an explicit calendar date in YYYYMMDD form, or None."""
    text = str(value or "").strip().replace("-", "").replace("/", "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
    return text


def _unavailable_research(reason, *, report_date=None, research_slot=None, artifact_path=None):
    """Return an explicit missing-input result without consulting ``latest``."""
    report_slot = str(os.environ.get("REPORT_SLOT") or "").strip() or None
    lineage = {
        "researchSlot": research_slot,
        "reportSlot": report_slot,
        "expectedAt": None,
        "artifactPath": artifact_path,
        "producerStatus": None,
        "artifactStatus": "MISSING" if reason.endswith("MISSING") else None,
        "freshnessStatus": None,
        "failureReason": reason,
        "consumedByReport": False,
        "checkedAt": None,
    }
    lineage = {key: value for key, value in lineage.items() if value not in (None, "")}
    return {
        "status": "UNAVAILABLE", "asOf": None, "stocks": [],
        "unavailableReason": reason, "slotLineage": lineage,
    }


def validated_research_for_report(payload, report_date, expected_generation=None):
    """Use research only when its date and source generation are explicit."""
    if not isinstance(payload, dict):
        return _unavailable_research("RESEARCH_SOURCE_MISSING")
    if compact_date(payload.get("asOf")) != compact_date(report_date):
        return _unavailable_research("RESEARCH_DATE_MISMATCH")
    source_generation = payload.get("sourceGeneration") or payload.get("generationId")
    if not source_generation:
        return _unavailable_research("RESEARCH_GENERATION_MISSING")
    if expected_generation and str(source_generation) != str(expected_generation):
        return _unavailable_research("RESEARCH_GENERATION_MISMATCH")
    return payload


def load_research_slot_for_report(report_date, expected_generation=None):
    """Load only the research slot explicitly assigned to this report."""
    slot = str(os.environ.get("REPORT_RESEARCH_SLOT") or "").strip()
    if slot not in {"07:20", "12:20", "19:20"}:
        return _unavailable_research("RESEARCH_SLOT_REQUIRED", report_date=report_date)
    compact = compact_date(report_date)
    if not compact:
        return _unavailable_research("RESEARCH_SLOT_DATE_UNAVAILABLE", report_date=report_date, research_slot=slot)
    path = RESEARCH_SLOTS_PATH / f"{compact}-{slot.replace(':', '')}.json"
    try:
        payload = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return _unavailable_research("RESEARCH_SLOT_ARTIFACT_MISSING", report_date=report_date, research_slot=slot, artifact_path=str(path))
    if not isinstance(payload, dict):
        return _unavailable_research("RESEARCH_SLOT_ARTIFACT_FAILED", report_date=report_date, research_slot=slot, artifact_path=str(path))
    if compact_date(payload.get("asOf")) != compact:
        return _unavailable_research("RESEARCH_SLOT_DATE_MISMATCH", report_date=report_date, research_slot=slot, artifact_path=str(path))
    lineage = payload.get("slotLineage")
    if not isinstance(lineage, dict) or lineage.get("researchSlot") != slot:
        return _unavailable_research("RESEARCH_SLOT_LINEAGE_MISSING", report_date=report_date, research_slot=slot, artifact_path=str(path))
    if lineage.get("producerStatus") == "FAILED":
        return _unavailable_research("RESEARCH_SLOT_PRODUCER_FAILED", report_date=report_date, research_slot=slot, artifact_path=str(path))
    if lineage.get("artifactStatus") == "FAILED":
        return _unavailable_research("RESEARCH_SLOT_ARTIFACT_FAILED", report_date=report_date, research_slot=slot, artifact_path=str(path))
    if lineage.get("freshnessStatus") not in {"FRESH", "AVAILABLE", "OK"}:
        return _unavailable_research("RESEARCH_SLOT_NOT_FRESH", report_date=report_date, research_slot=slot, artifact_path=str(path))
    payload = dict(payload)
    payload["slotLineage"] = {
        **lineage,
        "researchSlot": slot,
        "reportSlot": str(os.environ.get("REPORT_SLOT") or "").strip() or None,
        "artifactPath": lineage.get("artifactPath") or str(path),
        "consumedByReport": True,
    }
    validated = validated_research_for_report(payload, report_date, expected_generation)
    if validated.get("status") == "UNAVAILABLE":
        return validated
    return validated


def validate_research_coverage(report):
    """Validate optional research coverage without fabricating rows."""
    coverage = report.get("coverage") if isinstance(report, dict) else {}
    sources = report.get("sources") if isinstance(report, dict) else {}
    research_source = sources.get("researchIntelligence") if isinstance(sources, dict) else {}
    coverage = coverage if isinstance(coverage, dict) else {}
    research_source = research_source if isinstance(research_source, dict) else {}
    expected = int(coverage.get("enabledStockCount") or 0)
    actual = int(coverage.get("researchStockCount") or 0)
    status = str(research_source.get("status") or "UNAVAILABLE").upper()
    reason = research_source.get("unavailableReason")

    if actual == expected:
        return {"ok": True, "blocking": False, "status": "OK",
                "expected": expected, "actual": actual, "reason": reason}
    if status in {"UNAVAILABLE", "PARTIAL"}:
        return {
            "ok": True, "blocking": False,
            "status": "OPTIONAL_RESEARCH_UNAVAILABLE",
            "expected": expected, "actual": actual,
            "reason": reason or "RESEARCH_COVERAGE_INCOMPLETE",
        }
    return {
        "ok": False, "blocking": True,
        "status": "RESEARCH_COVERAGE_MISMATCH",
        "expected": expected, "actual": actual,
        "reason": reason or "RESEARCH_COVERAGE_INCOMPLETE",
    }


def validated_group_source(payload, kind, report_date, peer_generated_at=None):
    """Keep current group observations only when date and generation evidence align."""
    if not isinstance(payload, dict):
        return {"status": "UNAVAILABLE", "industries" if kind == "industry" else "themes": []}
    key = "industries" if kind == "industry" else "themes"
    rows = payload.get(key) or []
    generated_at = payload.get("generatedAt")
    observed_date = (
        payload.get("tradingDate") or payload.get("asOf")
        or payload.get("sourceTradingDate") or payload.get("sourceDate")
        or payload.get("observationDate")
    )
    if not observed_date and kind == "theme":
        for row in rows:
            for stock in (row.get("stocks") or []) if isinstance(row, dict) else []:
                observed_date = stock.get("temporaryCurrentBarDate") or stock.get("tradingDate")
                if observed_date:
                    break
            if observed_date:
                break
    reason = None
    if compact_date(observed_date) != compact_date(report_date):
        reason = "GROUP_DATE_MISMATCH"
    elif not generated_at:
        reason = "GROUP_GENERATION_MISSING"
    elif peer_generated_at and str(generated_at) != str(peer_generated_at):
        reason = "GROUP_GENERATION_MISMATCH"
    if reason:
        return {**payload, key: [], "status": "UNAVAILABLE", "validationReason": reason}
    return payload


def source_date(value):
    """Return the local source-observation date without calling it a trading date."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(KST)
        return parsed.strftime("%Y%m%d")
    except ValueError:
        return compact_date(text)


def _explicit_dates(payload, keys):
    if not isinstance(payload, dict):
        return []
    return [date for key in keys if (date := compact_date(payload.get(key)))]


def date_alignment(
    report_date,
    indices,
    full_market,
    *,
    observed_date=None,
    historical_source=None,
    current_snapshot_source=None,
):
    """Describe date alignment by source role, not by calendar equality.

    KRX historical indicators and a NAVER current snapshot intentionally have
    different dates. That combination is valid only when each source's own
    date evidence matches its declared role; an old or mismatched current
    snapshot remains a real alignment failure.
    """
    report_compact = compact_date(report_date)
    observed_compact = compact_date(observed_date)
    historical_snapshot_date = compact_date(
        full_market.get("snapshotDate") or full_market.get("asOfDate")
    )
    separate_sources = (
        str(historical_source or "").upper() == "KRX"
        and str(current_snapshot_source or "").upper() == "NAVER_SEPARATE"
        and bool(observed_compact)
        and bool(historical_snapshot_date)
        and historical_snapshot_date != observed_compact
    )
    index_expected_date = observed_compact if separate_sources else report_compact
    full_market_available = full_market.get("status") == "AVAILABLE"
    result = {
        "status": "OK" if full_market_available else "UNAVAILABLE",
        "dateAlignmentMode": (
            "KRX_HISTORICAL_NAVER_CURRENT_SEPARATE"
            if separate_sources else "SINGLE_SOURCE"
        ),
        "fullMarketStatus": full_market.get("status", "UNAVAILABLE"),
        "fullMarketReason": full_market.get("reason"),
        "reportTradingDate": report_compact,
        "historicalAsOfDate": report_compact,
        "observedDate": observed_compact,
        "historicalInputDate": compact_date(full_market.get("asOfDate")),
        "indexSourceDates": [],
        "fullMarketSnapshotDate": full_market.get("snapshotDate"),
        "fullMarketSnapshotDateStatus": full_market.get("snapshotDateStatus", "MISSING"),
        "maBasisDates": full_market.get("maBasisDates", []),
        "missingEvidenceDetails": {},
        "dateAlignmentMissingEvidence": [],
        "mismatches": [],
        "missingEvidence": [],
    }
    result["indexSourceDates"] = sorted({
        date for row in (indices.get("indices") or [])
        if (date := source_date(row.get("sourceTime")))
    })

    if not result["indexSourceDates"]:
        result["missingEvidence"].append("INDEX_SOURCE_DATE")
    elif index_expected_date and any(date != index_expected_date for date in result["indexSourceDates"]):
        result["mismatches"].append(
            "OBSERVED_VS_INDEX_SOURCE_DATE"
            if separate_sources else "REPORT_VS_INDEX_SOURCE_DATE"
        )
    result["indexAlignmentStatus"] = (
        "ALIGNED_TO_OBSERVED_DATE"
        if separate_sources and result["indexSourceDates"]
        and all(date == index_expected_date for date in result["indexSourceDates"])
        else "ALIGNED_TO_HISTORICAL_DATE"
        if result["indexSourceDates"]
        and all(date == report_compact for date in result["indexSourceDates"])
        else "MISMATCH" if result["indexSourceDates"] else "UNAVAILABLE"
    )

    if not full_market_available:
        result["missingEvidence"].append("FULL_MARKET_EVIDENCE")
        result["dateAlignmentMissingEvidence"] = list(result["missingEvidence"])
        if result["indexSourceDates"] and result["indexAlignmentStatus"] == "ALIGNED_TO_HISTORICAL_DATE":
            result["indexAlignmentStatus"] = "ALIGNED"
        return result

    if not result["fullMarketSnapshotDate"]:
        result["missingEvidence"].append("FULL_MARKET_TRADING_DATE")
    if not result["maBasisDates"]:
        result["missingEvidence"].append("MA_BASIS_DATE")
        result["missingEvidenceDetails"]["MA_BASIS_DATE"] = (
            full_market.get("maBasisDateEvidence")
            or {"status": "UNAVAILABLE", "reason": "EVIDENCE_NOT_PROVIDED"}
        )

    observed = {
        "INDEX_SOURCE_DATE": result["indexSourceDates"],
        "FULL_MARKET_TRADING_DATE": [result["fullMarketSnapshotDate"]] if result["fullMarketSnapshotDate"] else [],
        "MA_BASIS_DATE": result["maBasisDates"],
    }
    for label, dates in observed.items():
        if label == "INDEX_SOURCE_DATE":
            continue
        expected_date = historical_snapshot_date if separate_sources else report_compact
        if dates and expected_date and any(date != expected_date for date in dates):
            result["mismatches"].append(f"REPORT_VS_{label}")
    present = [date for dates in observed.values() for date in dates]
    if present:
        allowed_dates = ({observed_compact, historical_snapshot_date}
                         if separate_sources else {report_compact})
        if any(date not in allowed_dates for date in present):
            result["mismatches"].append("SOURCE_DATE_MISMATCH")
    if result["fullMarketSnapshotDateStatus"] == "INCONSISTENT":
        result["mismatches"].append("FULL_MARKET_TRADING_DATE_INCONSISTENT")
    result["dateAlignmentMissingEvidence"] = list(result["missingEvidence"])
    if result["missingEvidence"] or result["mismatches"]:
        result["status"] = "PARTIAL"
    elif separate_sources:
        result["status"] = "SEPARATE_SOURCES_ALIGNED"
    return result


def build_preopen_session_context(report_date, indices, previous_date=None, reports_root=None):
    """Keep a pre-open observation separate from the prior confirmed session.

    The current index snapshot is useful as an observation, but it is not a
    trading-day close. The prior session is loaded only from the explicit KRX
    historical target date and its dated report.
    """
    rows = [row for row in (indices.get("indices") or []) if isinstance(row, dict)]
    current_date = compact_date(report_date)
    preopen_rows = [
        row for row in rows
        if str(row.get("marketStatus") or "").upper() in {"PREOPEN", "PRE_OPEN", "BEFORE_OPEN"}
        or str(row.get("session") or "").upper() in {"PREOPEN", "PRE_OPEN"}
    ]
    if not preopen_rows:
        return {"mode": "NOT_PREOPEN", "status": "NOT_APPLICABLE"}

    observation = {
        "status": "AVAILABLE",
        "tradingDate": current_date,
        "generatedAt": indices.get("generatedAt"),
        "source": indices.get("source"),
        "indices": preopen_rows,
    }
    generated_date = source_date(indices.get("generatedAt"))
    if current_date and generated_date and generated_date != current_date:
        observation["status"] = "UNAVAILABLE"
        observation["reason"] = "PREOPEN_GENERATION_DATE_MISMATCH"
    if any(source_date(row.get("sourceTime")) != current_date for row in preopen_rows):
        observation["status"] = "UNAVAILABLE"
        observation["reason"] = "PREOPEN_SOURCE_DATE_MISMATCH"

    if isinstance(previous_date, dict):
        # Compatibility for isolated callers of the retired helper contract.
        previous_date = previous_date.get("asOfDate")
    previous_date = compact_date(previous_date)
    context = {
        "mode": "PREOPEN",
        "status": "PARTIAL",
        "observation": observation,
        "previousTradingDay": {
            "status": "UNAVAILABLE",
            "tradingDate": previous_date,
            "indices": [],
            "source": None,
            "verificationStatus": "UNAVAILABLE",
        },
    }
    if not previous_date or previous_date == current_date:
        context["previousTradingDay"]["reason"] = "PREVIOUS_TRADING_DATE_UNAVAILABLE"
        return context

    reports_root = Path(reports_root or DATA / "reports")
    previous_path = reports_root / f"{previous_date[:4]}-{previous_date[4:6]}-{previous_date[6:]}.json"
    try:
        previous_report = load_json(previous_path)
    except (OSError, ValueError, json.JSONDecodeError):
        context["previousTradingDay"]["reason"] = "PREVIOUS_DATED_REPORT_UNAVAILABLE"
        return context

    if compact_date(previous_report.get("asOf")) != previous_date:
        context["previousTradingDay"]["reason"] = "PREVIOUS_REPORT_DATE_MISMATCH"
        return context
    previous_market = previous_report.get("marketSummary") or {}
    previous_indices = ((previous_market.get("indices") or {}).get("indices")
                        or (previous_report.get("reportContract") or {}).get("indices")
                        or [])
    def index_key(row):
        return row.get("code") or {
            "코스피": "KOSPI", "코스닥": "KOSDAQ",
            "KOSPI": "KOSPI", "KOSDAQ": "KOSDAQ",
        }.get(row.get("name"))

    valid_previous = (
        len(previous_indices) == 2
        and {index_key(row) for row in previous_indices} == {"KOSPI", "KOSDAQ"}
        and all(
            str(row.get("marketStatus") or "").upper() in {"CLOSE", "CLOSED", "AFTER_MARKET"}
            and source_date(row.get("sourceTime")) == previous_date
            for row in previous_indices
        )
        and (previous_report.get("dateAlignment") or {}).get("status") == "OK"
    )
    if not valid_previous:
        context["previousTradingDay"]["reason"] = "PREVIOUS_SESSION_EVIDENCE_INVALID"
        return context

    previous_contract = previous_report.get("reportContract") or {}
    previous_full_market = previous_contract.get("fullMarket") or previous_market.get("fullMarket") or {}
    previous_v2 = previous_contract.get("v2") or {}
    try:
        source_path = str(previous_path.relative_to(ROOT))
    except ValueError:
        source_path = str(previous_path)
    context["previousTradingDay"] = {
        "status": "AVAILABLE",
        "tradingDate": previous_date,
        "source": source_path,
        "generatedAt": previous_report.get("generatedAt"),
        "verificationStatus": "VERIFIED_PRIOR_SESSION_REFERENCE",
        "indices": previous_indices,
        "fullMarket": {
            "totalCount": previous_full_market.get("totalCount"),
            "breadth": previous_full_market.get("breadth") or {},
            "metrics": previous_full_market.get("metrics") or {},
        },
        "v1V2": {
            "tradingDate": previous_v2.get("tradingDate") or previous_date,
            "status": "PRIOR_SESSION_REFERENCE",
            "sourceQualityStatus": previous_v2.get("sourceQualityStatus"),
            "sourceCommitStatus": previous_v2.get("sourceCommitStatus"),
        },
    }
    context["status"] = "AVAILABLE" if observation["status"] == "AVAILABLE" else "PARTIAL"
    return context


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _project_staged_payload(payload, rows, *, missing_codes, target_date):
    """Keep the legacy payload shape for registered-Universe consumers."""
    projected = dict(payload)
    projected["datas"] = list(rows)
    projected["targetDate"] = target_date
    projected["scope"] = "REGISTERED_UNIVERSE_PROJECTION"
    projected["missingCodes"] = list(missing_codes)
    projected["fallbackUsed"] = False
    return projected


def load_report_technical_inputs(staging_input_dir=None, current_snapshot=None):
    """Load default inputs or opt-in staged full-market technical inputs.

    The default path remains the production files. Staging mode is strict:
    the adapter must supply the full-market rows, and registered-Universe
    rows are projected from that same generation without legacy fallback.
    """
    if staging_input_dir is None:
        technicals_payload = load_json(TECHNICALS_PATH)
        states_payload = load_json(STATES_PATH)
        return {
            "mode": "DEFAULT",
            "technicals": technicals_payload,
            "states": states_payload,
            "groupStates": load_json(GROUP_STATES_PATH) if GROUP_STATES_PATH.exists() else {},
            "analysisTechnicals": None,
            "analysisStates": None,
            "derivedProjection": None,
            "metadata": {
                "mode": "DEFAULT", "fallbackUsed": False,
                "historicalIndicatorSource": technicals_payload.get("historicalIndicatorSource") or technicals_payload.get("source"),
                "sourceGeneration": technicals_payload.get("generationId") or technicals_payload.get("sourceGeneration"),
                "targetDate": technicals_payload.get("targetDate") or technicals_payload.get("asOfDate"),
                "historicalLineage": {
                    "generationId": technicals_payload.get("generationId") or technicals_payload.get("sourceGeneration"),
                    "targetDate": technicals_payload.get("targetDate") or technicals_payload.get("asOfDate"),
                    "receiptStatus": technicals_payload.get("receiptStatus", "UNAVAILABLE"),
                    "status": "VERIFIED" if technicals_payload.get("receiptStatus") == "VERIFIED" else "UNAVAILABLE",
                },
                "sourcePath": "data/technicals.json",
                "manifestSha256": technicals_payload.get("manifestSha256"),
                "monitoringSha256": technicals_payload.get("monitoringSha256"),
                "receiptStatus": technicals_payload.get("receiptStatus", "UNAVAILABLE"),
                "publicationAuthorized": technicals_payload.get("publicationAuthorized"),
            },
        }

    root = Path(staging_input_dir)
    projection_path = root / "report-contract-projection.json"
    try:
        derived_projection = load_json(projection_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"DERIVED_REPORT_PROJECTION_UNAVAILABLE:{projection_path}") from exc
    technicals_payload = load_json(root / "technicals.json")
    expected_source_staging = (
        "KRX_M10_GENERATION"
        if technicals_payload.get("source") == "KRX_M10_GENERATION"
        else None
    )
    staged = build_staged_technical_input(
        root / "technicals.json",
        root / "states.json",
        root / "group-states.json",
        UNIVERSE_PATH,
        current_snapshot=current_snapshot,
        expected_source_staging=expected_source_staging,
    )
    full_market = staged["fullMarket"]
    registered = staged["registeredUniverse"]
    technical_rows = [item["technicals"] for item in registered["items"]]
    state_rows = [item["state"] for item in registered["items"]]
    registered_technicals = _project_staged_payload(
        full_market["technicalsPayload"],
        technical_rows,
        missing_codes=registered["missingCodes"],
        target_date=staged["targetDate"],
    )
    registered_states = _project_staged_payload(
        full_market["statesPayload"],
        state_rows,
        missing_codes=registered["missingCodes"],
        target_date=staged["targetDate"],
    )
    metadata = {
        "mode": "STAGING",
        "adapter": staged["source"],
        "targetDate": staged["targetDate"],
        "sourceStaging": staged["sourceStaging"],
        "fullMarketTechnicalCount": len(full_market["technicals"]),
        "fullMarketStateCount": len(full_market["states"]),
        "registeredCount": registered["registeredCount"],
        "registeredObservedCount": len(registered["items"]),
        "missingCodes": list(registered["missingCodes"]),
        "historicalGroupsStatus": staged["historicalGroups"].get("availabilityStatus"),
        "currentSnapshot": dict(staged["currentSnapshot"]),
        "currentSnapshotSeparated": True,
        "currentSnapshotSource": "NAVER_SEPARATE",
        "historicalIndicatorSource": staged["historicalIndicatorSource"],
        "historicalPriority": staged["historicalPriority"],
        "historicalFallbackAllowed": staged["historicalFallbackAllowed"],
        "currentObservationPolicy": staged["currentObservationPolicy"],
        "historicalLineage": dict(staged.get("historicalLineage") or {}),
        "missingHistoryCodes": list(staged.get("missingHistoryCodes") or []),
        "regularSessionUnavailableCodes": list(staged.get("regularSessionUnavailableCodes") or []),
        "currentSnapshotMissingCodes": list(staged.get("currentSnapshotMissingCodes") or []),
        "conflictPolicy": (
            "KRX_PRIMARY_NO_MERGE"
            if staged["historicalIndicatorSource"] == "KRX"
            else "EXTERNAL_STAGING_POLICY"
        ),
        "missingDataPolicy": (
            "MISSING_OR_LAGGED_NO_NAVER_HISTORICAL_FALLBACK"
            if staged["historicalIndicatorSource"] == "KRX"
            else "STAGING_SOURCE_DEFINED"
        ),
        "fallbackUsed": False,
        "sourcePath": str(root / "technicals.json"),
    }
    resolution_path = root / "krx-resolution.json"
    if resolution_path.exists():
        resolution = load_json(resolution_path)
        if resolution.get("receiptStatus") != "VERIFIED" or resolution.get("publicationAuthorized") is not True:
            raise ValueError("KRX_RESOLUTION_NOT_VERIFIED")
        if resolution.get("sourceGeneration") != technicals_payload.get("generationId"):
            raise ValueError("KRX_RESOLUTION_GENERATION_MISMATCH")
        if resolution.get("targetDate") != staged["targetDate"]:
            raise ValueError("KRX_RESOLUTION_DATE_MISMATCH")
        metadata.update({
            "sourceGeneration": resolution.get("sourceGeneration"),
            "manifestSha256": resolution.get("manifestSha256"),
            "monitoringSha256": resolution.get("monitoringSha256"),
            "entityCount": resolution.get("entityCount"),
            "instrumentCount": resolution.get("instrumentCount"),
            "receiptStatus": resolution.get("receiptStatus"),
            "publicationAuthorized": resolution.get("publicationAuthorized"),
        })
    lineage = metadata.get("historicalLineage") or {}
    if metadata.get("historicalIndicatorSource") == "KRX":
        metadata.update({
            "historicalGenerationId": lineage.get("generationId") or metadata.get("sourceGeneration"),
            "historicalTargetDate": lineage.get("targetDate") or metadata.get("targetDate"),
            "historicalReceiptStatus": lineage.get("receiptStatus", "UNAVAILABLE"),
            "historicalReceiptVerified": lineage.get("receiptVerified") is True,
            "historicalEntityCount": lineage.get("entityCount"),
            "historicalInstrumentCount": lineage.get("instrumentCount"),
            "historicalAdjustmentStatus": lineage.get("adjustmentStatus", "UNAVAILABLE"),
            "historicalLineageStatus": lineage.get("status", "UNAVAILABLE"),
            "currentSnapshotSource": "NAVER_SEPARATE",
        })
    return {
        "mode": "STAGING",
        "technicals": registered_technicals,
        "states": registered_states,
        "groupStates": staged["historicalGroups"],
        "analysisTechnicals": full_market["technicalsPayload"],
        "analysisStates": full_market["statesPayload"],
        "derivedProjection": derived_projection,
        "metadata": metadata,
    }


def _legacy_staged_technical_row(row):
    """Expose verified staging fields at the legacy report-consumer level."""
    if not isinstance(row, dict) or not isinstance(row.get("technicals"), dict):
        return row
    flattened = dict(row)
    flattened.update(row["technicals"])
    flattened["sourceDate"] = row.get("asOfDate")
    flattened["confirmedStatus"] = "ok"
    flattened["status"] = "ok"
    return flattened


def _legacy_staged_state_row(row):
    """Keep KRX state/provenance without mixing NAVER current quotes."""
    if not isinstance(row, dict) or not isinstance(row.get("state"), dict):
        return row
    flattened = dict(row)
    flattened.update(row["state"])
    return flattened


def restrict_staging_current_quotes(quotes, target_date):
    """Keep current observations even when their date differs from history.

    The current quote snapshot and staged historical technicals are separate
    generations by design.  A date difference is diagnostic metadata, not a
    reason to erase the current observation or relabel it as stale.
    """
    target = compact_date(target_date)
    dates = set()
    top_date = source_date(quotes.get("sourceTime")) if isinstance(quotes, dict) else None
    if top_date:
        dates.add(top_date)
    for row in (quotes.get("datas") or []) if isinstance(quotes, dict) else []:
        if not isinstance(row, dict):
            continue
        row_date = source_date(row.get("sourceTime") or row.get("collectedAt"))
        if row_date:
            dates.add(row_date)
    current = dict(quotes) if isinstance(quotes, dict) else {}
    current.update({
        "targetDate": target,
        "historicalTechnicalDate": target,
        "observedSourceDates": sorted(dates),
        "currentSnapshotSeparated": True,
        "currentSnapshotDateStatus": (
            "MATCH" if target and dates == {target}
            else "CURRENT_OBSERVATION_SEPARATE"
        ),
    })
    return current


def configured_interest_group_projection(universe):
    """Project only explicitly configured operating interest groups.

    The dynamic-universe artifact contains automatic selection history, not
    the user's operating interest-group configuration.  Keep those inputs
    separate so removed/automatic rows cannot masquerade as configured
    interest groups in the report.
    """
    universe = universe if isinstance(universe, dict) else {}

    def rows_for(value):
        rows = []
        if isinstance(value, dict):
            for key, entry in value.items():
                if isinstance(entry, dict):
                    row = dict(entry)
                    row.setdefault("id", str(key))
                    row.setdefault("name", row.get("label") or str(key))
                else:
                    row = {"id": str(key), "name": str(entry or key)}
                rows.append(row)
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict) and (entry.get("id") or entry.get("code")):
                    row = dict(entry)
                    row.setdefault("id", row.get("code"))
                    row.setdefault("name", row.get("label") or row.get("id"))
                    rows.append(row)
                elif entry not in (None, ""):
                    rows.append({"id": str(entry), "name": str(entry)})
        return rows

    sectors = rows_for(universe.get("sectors"))
    themes = rows_for(universe.get("themes"))
    configured = bool(sectors or themes)
    return {
        "status": "CONFIGURED" if configured else "NOT_CONFIGURED",
        "configurationStatus": "CONFIGURED" if configured else "NOT_CONFIGURED",
        "artifactPath": "config/universe.json#sectors,themes",
        "generatedAt": universe.get("generatedAt"),
        "sourceDate": universe.get("sourceDate"),
        "observationDate": universe.get("observationDate"),
        "scope": "INTEREST_GROUP_CONFIGURATION",
        "inputCount": len(sectors) + len(themes),
        "matchedCount": 0,
        "outputCount": len(sectors) + len(themes),
        "nextExpectedAt": universe.get("nextExpectedAt"),
        "sectors": sectors,
        "themes": themes,
    }


def registered_universe_projection(enabled, rows):
    """Project configured items without mixing current and historical data."""
    by_code = {str(row.get("itemCode")): row for row in rows if row.get("itemCode")}
    items = []
    for item in enabled:
        code = str(item["itemCode"])
        row = by_code.get(code, {})
        market = row.get("market") or {}
        technical = row.get("technical") or {}
        current_state = row.get("currentState") or {}
        state = row.get("state") or {}
        current_price = market.get("price")
        ma20 = technical.get("ma20")
        ma60 = technical.get("ma60")

        def position(price, moving_average):
            if not isinstance(price, (int, float)) or not isinstance(moving_average, (int, float)):
                return None
            if price > moving_average:
                return "above"
            if price < moving_average:
                return "below"
            return "equal"

        price_vs_ma20 = position(current_price, ma20)
        price_vs_ma60 = position(current_price, ma60)
        if price_vs_ma20 and price_vs_ma60:
            if price_vs_ma20 == price_vs_ma60 == "above":
                trend = "ABOVE_MA20"
            elif price_vs_ma20 == price_vs_ma60 == "below":
                trend = "BELOW_MA20"
            else:
                trend = "MIXED_MA20_MA60"
            trend_status = "AVAILABLE"
            trend_reason = None
        else:
            trend = "UNAVAILABLE_TREND_INPUT"
            trend_status = "UNAVAILABLE"
            trend_reason = "UNAVAILABLE_TREND_INPUT"
        items.append({
            "itemCode": code,
            "itemName": item.get("stockName") or row.get("itemName"),
            "current": {
                "price": current_price,
                "changeRate": market.get("changeRate"),
                "sourceTime": market.get("sourceTime"),
                "status": "AVAILABLE" if row.get("marketObserved") else "UNAVAILABLE",
            },
            "technicals": {
                "ma20": ma20,
                "ma60": ma60,
                "sourceDate": technical.get("sourceDate"),
                "status": technical.get("confirmedStatus") or technical.get("status"),
            },
            "state": {
                "status": current_state.get("status") or state.get("status") or "UNAVAILABLE",
                "trend": trend,
                "trendStatus": trend_status,
                "failureReason": trend_reason,
                "priceVsMA20": price_vs_ma20 or current_state.get("priceVsMA20") or state.get("priceVsMA20"),
                "priceVsMA60": price_vs_ma60 or current_state.get("priceVsMA60") or state.get("priceVsMA60"),
                "maAlignment": "ALIGNED" if price_vs_ma20 and price_vs_ma20 == price_vs_ma60 else "MIXED" if price_vs_ma20 and price_vs_ma60 else None,
                "trendSource": "NAVER_CURRENT_SNAPSHOT+KRX_HISTORICAL_TECHNICAL" if trend_status == "AVAILABLE" else None,
                "trendSourceDate": technical.get("sourceDate"),
            },
            "status": "AVAILABLE" if row else "UNAVAILABLE",
        })
    return {
        "status": "AVAILABLE" if items else "AVAILABLE_EMPTY",
        "scope": "REGISTERED_UNIVERSE",
        "count": len(items),
        "fallbackUsed": False,
        "items": items,
    }


def full_market_from_derived_projection(projection, target_date):
    """Expose a verified staged KRX historical projection to the report."""
    if not isinstance(projection, dict):
        return {"status": "UNAVAILABLE", "reason": "DERIVED_PROJECTION_MISSING"}
    target = str(target_date or "").replace("-", "")
    if projection.get("asOf") != target:
        return {"status": "UNAVAILABLE", "reason": "DERIVED_PROJECTION_DATE_MISMATCH"}
    summary = projection.get("fullMarket") or {}
    quality = summary.get("quality") or {}
    return {
        # A legacy PARTIAL projection is not a usable KRX input. The producer
        # must emit an AVAILABLE projection after receipt/generation checks.
        "status": "AVAILABLE" if projection.get("status") == "AVAILABLE" else "UNAVAILABLE",
        "asOfDate": target,
        "snapshotDate": target,
        "snapshotDateStatus": "DERIVED_TARGET_DATE",
        "sourceQualityStatus": projection.get("sourceQualityStatus", "PARTIAL"),
        "qualityStatus": projection.get("status", "PARTIAL"),
        "historicalIndicatorSource": projection.get("historicalIndicatorSource", "KRX"),
        "historicalLineageStatus": projection.get("historicalLineageStatus", "UNAVAILABLE"),
        "historicalReceiptStatus": projection.get("historicalReceiptStatus", "UNAVAILABLE"),
        "generationId": projection.get("generationId"),
        "historicalGenerationId": projection.get("historicalGenerationId") or projection.get("generationId"),
        "historicalTargetDate": projection.get("historicalTargetDate") or target,
        "instrumentCount": summary.get("instrumentCount") or (quality.get("instrumentCount") if isinstance(quality, dict) else None),
        "scope": summary.get("scope") or "STOCK_ETF",
        "currentSnapshotSource": projection.get("currentSnapshotSource", "NAVER_SEPARATE"),
        "quality": quality,
        "breadth": summary.get("breadth") or {},
        "leaders": summary.get("leaders") or [],
        "industries": [],
        "maBasisDates": summary.get("maBasisDates") or [],
        "maBasisDateEvidence": summary.get("maBasisDateEvidence") or {},
        "generatedAt": projection.get("observedAt"),
        "sourceTime": None,
        "reason": "DERIVED_KRX_PROJECTION",
        "derivedProjection": projection,
    }


def _usable_v2_shadow_inputs(shadow_latest, shadow_history, shadow_status):
    """Return only a V2 result proven current by its run status artifact."""
    if not isinstance(shadow_status, Mapping) or shadow_status.get("status") != "OK":
        return {}, {}
    shadow_date = shadow_status.get("tradingDate")
    latest_date = shadow_latest.get("tradingDate") if isinstance(shadow_latest, Mapping) else None
    if shadow_date and latest_date and str(shadow_date) != str(latest_date):
        return {}, {}
    return (
        shadow_latest if isinstance(shadow_latest, Mapping) else {},
        shadow_history if isinstance(shadow_history, Mapping) else {},
    )


def shadow_v1_v2_analysis(dynamic_universe, shadow_latest, shadow_history, shadow_status):
    """Build a report-only comparison; never changes V1 or watchlist data."""
    v1_sectors = dynamic_universe.get("sectors") or []
    v1_themes = dynamic_universe.get("themes") or []
    v2_industries = shadow_latest.get("industries") or []
    v2_themes = shadow_latest.get("themes") or []

    def distribution(rows, field):
        result = {}
        for row in rows:
            value = row.get(field) or "UNKNOWN"
            result[value] = result.get(value, 0) + 1
        return result

    def evidence(row):
        components = row.get("components") or {}
        statuses = row.get("componentStatus") or {}
        return {
            "relativeStrength": {"value": components.get("priceRelativeStrength"), "status": statuses.get("priceRelativeStrength", "WAIT")},
            "breadth": {"value": components.get("breadth"), "status": statuses.get("breadth", "WAIT")},
            "ma20AboveRatio": {"value": row.get("ma20AboveRatio"), "status": row.get("ma20Status", "WAIT")},
            "turnoverParticipation": {"value": components.get("turnoverParticipation"), "status": statuses.get("turnoverParticipation", "WAIT")},
            "persistence": {"value": components.get("persistence"), "status": statuses.get("persistence", "WAIT"), "observedDays": row.get("persistenceDays", 0)},
        }

    def rows(items):
        return [{
            "id": row.get("id"), "name": row.get("name"),
            "v1Status": row.get("v1Status"), "v2Status": row.get("v2Status"),
            "score": row.get("score"), "criteria": evidence(row),
            "reason": row.get("watchReasons") or row.get("missingComponents") or [],
            "dataQuality": row.get("dataQuality", "UNAVAILABLE"),
            "sourceTradingDate": row.get("sourceTradingDate") or shadow_status.get("tradingDate"),
            "sourceGeneratedAt": shadow_latest.get("generatedAt"),
            "watchEligible": row.get("watchEligible"), "watchRank": row.get("watchRank"),
        } for row in items]

    observations = shadow_history.get("observations") or []
    unique_dates = sorted({str(row.get("tradingDate")) for row in observations if row.get("tradingDate")})
    leader_codes = {str(row.get("code")) for row in dynamic_universe.get("leaders", []) if row.get("code")}
    watchlists = dynamic_universe.get("userWatchlists")
    watchlist_rows = watchlists.get("stocks", []) if isinstance(watchlists, dict) else []
    user_codes = {str(row.get("code")) for row in watchlist_rows if isinstance(row, dict) and row.get("code")}
    return {
        "asOf": shadow_status.get("tradingDate") or shadow_latest.get("tradingDate"),
        "sourceGeneratedAt": shadow_latest.get("generatedAt"),
        "source": "data/dynamic-universe/v2-shadow-latest.json",
        "v1": {
            "sectors": {"total": len(v1_sectors), "statusCounts": distribution(v1_sectors, "status")},
            "themes": {"total": len(v1_themes), "statusCounts": distribution(v1_themes, "status")},
            "leaders": {"count": len(dynamic_universe.get("leaders", [])), "codes": sorted(leader_codes)},
        },
        "v2Shadow": {
            "experimental": True,
            "industries": {"total": len(v2_industries), "statusCounts": distribution(v2_industries, "v2Status"), "rows": rows(v2_industries)},
            "themes": {"total": len(v2_themes), "statusCounts": distribution(v2_themes, "v2Status"), "rows": rows(v2_themes)},
            "history": {"recordCount": len(observations), "uniqueTradingDates": unique_dates, "status": shadow_status.get("status", "UNAVAILABLE")},
            "note": "V2 Shadow는 실험적 분석이며 V1 선정·관심종목·매매 판단을 변경하지 않습니다.",
        },
        "observationSources": {
            "postedLeaders": len(leader_codes),
            "userWatchlistCodes": sorted(user_codes),
            "uiObservationCount": None,
            "unresolvedDifference": "UI 관찰종목 28개 원천은 이 리포트 입력에 포함되지 않아 코드 기준 추가 1개를 확정할 수 없음",
        },
    }


def stock_code(row):
    return (
        row.get("itemCode")
        or row.get("code")
        or row.get("symbol")
        or row.get("stockCode")
    )


def stock_name(row):
    return (
        row.get("stockName")
        or row.get("itemName")
        or row.get("name")
        or row.get("stockNameKo")
    )


def extract_rows(payload):
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    for key in (
        "stocks",
        "rows",
        "items",
        "quotes",
        "technicals",
        "states",
        "datas",
        "data",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    return []


def index_rows(payload):
    result = {}
    for row in extract_rows(payload):
        if not isinstance(row, dict):
            continue
        code = stock_code(row)
        if code:
            result[str(code)] = row
    return result


def enabled_universe(payload):
    rows = []
    for row in payload.get("stocks", []):
        if row.get("enabled"):
            rows.append(
                {
                    "itemCode": str(row["itemCode"]),
                    "stockName": row.get("stockName"),
                }
            )
    return rows


def safe_number(value):
    if isinstance(value, (int, float)):
        return value
    try:
        if value is None:
            return None
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def first_number(row, keys):
    if not isinstance(row, dict):
        return None
    for key in keys:
        value = safe_number(row.get(key))
        if value is not None:
            return value
    return None


def quote_summary(row):
    if not row:
        return {
            "price": None,
            "changeRate": None,
            "volume": None,
            "tradingValue": None,
            "sourceTime": None,
            "fresh": None,
            "referencePrice": None,
            "referenceSourceTime": None,
            "referenceStatus": None,
            "sessionChange": None,
            "sessionChangeRate": None,
        }

    return {
        "price": first_number(
            row,
            ["price", "closePrice", "nowPrice", "close", "currentPrice"],
        ),
        "changeRate": first_number(
            row,
            ["changeRate", "fluctuationsRatio", "changePercent", "rate"],
        ),
        "volume": first_number(
            row,
            ["volume", "accumulatedTradingVolume", "tradeVolume"],
        ),
        "tradingValue": first_number(
            row,
            [
                "tradingValue",
                "accumulatedTradingValueRaw",
                "accumulatedTradingValue",
                "tradeAmount",
            ],
        ),
        "sourceTime": row.get("sourceTime") or row.get("localTradedAt"),
        "fresh": row.get("fresh"),
        "referencePrice": first_number(row, ["referencePrice"]),
        "referenceSourceTime": row.get("referenceSourceTime"),
        "referenceStatus": row.get("referenceStatus"),
        "sessionChange": first_number(row, ["sessionChange"]),
        "sessionChangeRate": first_number(row, ["sessionChangeRate"]),
    }


def compact_dict(row, preferred_keys):
    if not isinstance(row, dict):
        return {}

    out = {}
    for key in preferred_keys:
        if key in row:
            out[key] = row[key]
    return out


def research_index(payload):
    result = {}
    for row in payload.get("stocks", []):
        code = row.get("itemCode")
        if code:
            result[str(code)] = row
    return result


def memberships(universe):
    result = {}

    for group_type in ("sectors", "themes", "watchlists"):
        for group_name, codes in universe.get(group_type, {}).items():
            for code in codes:
                result.setdefault(str(code), []).append(
                    {
                        "type": group_type,
                        "name": group_name,
                    }
                )

    return result


def coverage_status(
    enabled_count,
    quote_count,
    technical_count,
    state_count,
    quote_status=None,
    quote_fresh=None,
    *,
    historical_as_of_date=None,
    observed_date=None,
    observed_at=None,
    current_snapshot_source=None,
):
    if enabled_count == 0:
        return "EMPTY_CONFIGURED_UNIVERSE"
    complete = (
        quote_count >= enabled_count
        and technical_count >= enabled_count
        and state_count >= enabled_count
    )

    if not complete:
        return "INCOMPLETE_MARKET_COVERAGE"

    aligned_current_observation = (
        str(current_snapshot_source or "").upper() in {"NAVER", "NAVER_SEPARATE"}
        and historical_as_of_date
        and observed_date
        and str(observed_date).replace("-", "") >= str(historical_as_of_date).replace("-", "")
        and quote_status == "ok"
    )
    if (quote_status != "ok" or quote_fresh is not True) and not aligned_current_observation:
        return "STALE_MARKET_DATA"

    return "OK"


def ratio(count, denominator):
    """Return a descriptive ratio, without inventing a zero denominator."""
    return count / denominator if denominator else None


def event_stock(row):
    return {
        "itemCode": row["itemCode"],
        "itemName": row["itemName"],
        "changeRate": row["market"]["changeRate"],
    }


def market_breadth(rows, state_key="state", include_directions=True):
    """Summarize only observed market/state values; absent values stay absent."""
    observed = [row for row in rows if row.get("marketObserved")]
    directions = ([row["market"]["changeRate"] for row in observed]
                  if include_directions else [])
    directions = [value for value in directions if value is not None]

    def state(row):
        value = row.get(state_key)
        return value if isinstance(value, dict) else {}

    ma20_observed = [
        row for row in observed
        if state(row).get("priceVsMA20") is not None
    ]
    ma60_observed = [
        row for row in observed
        if state(row).get("priceVsMA60") is not None
    ]

    def state_count(key, value):
        return sum(state(row).get(key) == value for row in observed)

    up_count = sum(value > 0 for value in directions)
    down_count = sum(value < 0 for value in directions)
    flat_count = sum(value == 0 for value in directions)
    ma20_above = state_count("priceVsMA20", "above")
    ma20_below = state_count("priceVsMA20", "below")
    ma60_above = state_count("priceVsMA60", "above")
    ma60_below = state_count("priceVsMA60", "below")

    return {
        "observedCount": len(observed),
        "directionObservedCount": len(directions),
        "ma20ObservedCount": len(ma20_observed),
        "ma60ObservedCount": len(ma60_observed),
        "upCount": up_count,
        "downCount": down_count,
        "flatCount": flat_count,
        "upRatio": ratio(up_count, len(directions)),
        "downRatio": ratio(down_count, len(directions)),
        "ma20AboveCount": ma20_above,
        "ma20BelowCount": ma20_below,
        "ma20AboveRatio": ratio(ma20_above, len(ma20_observed)),
        "ma20BelowRatio": ratio(ma20_below, len(ma20_observed)),
        "ma60AboveCount": ma60_above,
        "ma60BelowCount": ma60_below,
        "ma60AboveRatio": ratio(ma60_above, len(ma60_observed)),
        "ma60BelowRatio": ratio(ma60_below, len(ma60_observed)),
        "breakout20AttemptCount": state_count("breakout20", "attempt"),
        "breakout20ConfirmedCount": state_count("breakout20", "confirmed"),
        "breakout20FailedCount": state_count("breakout20", "failed"),
        "breakout60AttemptCount": state_count("breakout60", "attempt"),
        "breakout60ConfirmedCount": state_count("breakout60", "confirmed"),
        "breakout60FailedCount": state_count("breakout60", "failed"),
        "volumeSurgeCount": state_count("volumeState", "surge"),
        "volumeElevatedCount": state_count("volumeState", "elevated"),
        "pullbackCount": sum(
            str(state(row).get("pullbackState", "")).startswith("pullback")
            for row in observed
        ),
        "nearBreakoutCount": sum(
            str(state(row).get("pullbackState", "")).startswith("near_breakout")
            for row in observed
        ),
    }


def full_market_summary(full_market, alignment):
    """Expose verified full-market material separately from monitored stocks."""
    full_market = full_market if isinstance(full_market, dict) else {}
    alignment = alignment if isinstance(alignment, dict) else {}
    evidence_status = full_market.get("status", "UNAVAILABLE")
    alignment_status = alignment.get("status", "UNAVAILABLE")
    alignment_is_valid = alignment_status in {"OK", "SEPARATE_SOURCES_ALIGNED"}
    snapshot_status = full_market.get("snapshotDateStatus", "MISSING")
    verified = (
        evidence_status == "AVAILABLE"
        and alignment_is_valid
        and snapshot_status == "VERIFIED_EXPLICIT"
        and bool(full_market.get("snapshotDate"))
    )
    staged_krx = (
        evidence_status == "AVAILABLE"
        and alignment_is_valid
        and snapshot_status == "DERIVED_TARGET_DATE"
        and full_market.get("historicalIndicatorSource") == "KRX"
        and full_market.get("historicalLineageStatus") == "VERIFIED"
        and full_market.get("historicalReceiptStatus") == "VERIFIED"
        and bool(full_market.get("snapshotDate"))
    )
    if verified or staged_krx:
        status = "AVAILABLE"
        reason = None
    elif evidence_status == "AVAILABLE":
        status = "PARTIAL"
        reason = (
            "DERIVED_KRX_LINEAGE_UNVERIFIED"
            if snapshot_status == "DERIVED_TARGET_DATE"
            else "DATE_ALIGNMENT_NOT_VERIFIED"
        )
    else:
        status = "UNAVAILABLE"
        reason = full_market.get("reason") or "FULL_MARKET_EVIDENCE_UNAVAILABLE"

    derived_summary = snapshot_status == "DERIVED_TARGET_DATE"
    quality = full_market.get("quality") if (verified or derived_summary) else {}
    industries = full_market.get("industries", []) if (verified or derived_summary) else []
    industries = [row for row in industries if isinstance(row, dict)]
    industries.sort(
        key=lambda row: (
            row.get("avgReturn1D") is not None,
            row.get("avgReturn1D") if row.get("avgReturn1D") is not None else -9999,
        ),
        reverse=True,
    )
    return {
        "status": status,
        "evidenceStatus": evidence_status,
        "alignmentStatus": alignment_status,
        "reason": reason,
        "tradingDate": full_market.get("snapshotDate"),
        "historicalInputDate": full_market.get("asOfDate"),
        "snapshotDateStatus": snapshot_status,
        "sourceTime": full_market.get("sourceTime") if (verified or derived_summary) else None,
        "generatedAt": full_market.get("generatedAt") if (verified or derived_summary) else None,
        "qualityStatus": full_market.get("qualityStatus") if (verified or derived_summary) else None,
        "sourceQualityStatus": full_market.get("sourceQualityStatus") if (verified or derived_summary) else None,
        "quality": quality,
        "breadth": full_market.get("breadth") if (verified or derived_summary) else None,
        "marketBreakdown": full_market.get("marketBreakdown", {}) if (verified or derived_summary) else {},
        "indexIdentities": full_market.get("indexIdentities", []) if (verified or derived_summary) else [],
        "indicatorAvailability": full_market.get("indicatorAvailability", {}) if (verified or derived_summary) else {},
        "industries": industries,
        "industrySortBasis": "AVG_RETURN_1D_DESC_NULLS_LAST",
        "leaders": full_market.get("leaders", []) if (verified or derived_summary) else [],
        "mismatches": alignment.get("mismatches", []),
        "missingEvidence": alignment.get("missingEvidence", []),
        "missingEvidenceDetails": alignment.get("missingEvidenceDetails", {}),
    }


def normalized_group_summary(payload):
    """Keep the dynamic group-state contract, with no group-name assumptions."""
    groups = payload.get("groups", []) if isinstance(payload, dict) else []
    if not isinstance(groups, list):
        groups = []
    fields = [
        "groupType", "groupName", "enabledMembers", "upCount", "downCount",
        "flatCount", "upRatio", "downRatio", "aboveMA20Count",
        "aboveMA20CountRatio", "aboveMA60Count", "aboveMA60CountRatio",
        "breakout20AttemptCount", "breakout20ConfirmedCount",
        "breakout20FailedCount", "volumeSurgeCount", "volumeElevatedCount",
        "leaderUpCount", "averageChangePct", "diffusionState", "status",
        "current", "regularSession",
    ]
    return [
        {field: group.get(field) for field in fields}
        for group in groups
        if isinstance(group, dict)
    ]


def technical_events(rows, state_key="state"):
    """Classify existing state values only; this is not a ranking or score."""
    predicates = {
        "breakout20Confirmed": lambda s: s.get("breakout20") == "confirmed",
        "breakout20Attempt": lambda s: s.get("breakout20") == "attempt",
        "breakout20Failed": lambda s: s.get("breakout20") == "failed",
        "breakout60Confirmed": lambda s: s.get("breakout60") == "confirmed",
        "breakout60Attempt": lambda s: s.get("breakout60") == "attempt",
        "breakout60Failed": lambda s: s.get("breakout60") == "failed",
        "volumeSurge": lambda s: s.get("volumeState") == "surge",
        "volumeElevated": lambda s: s.get("volumeState") == "elevated",
        "nearBreakout": lambda s: str(s.get("pullbackState", "")).startswith("near_breakout"),
        "pullback": lambda s: str(s.get("pullbackState", "")).startswith("pullback"),
        "ma20Above": lambda s: s.get("priceVsMA20") == "above",
        "ma20Below": lambda s: s.get("priceVsMA20") == "below",
        "ma60Above": lambda s: s.get("priceVsMA60") == "above",
        "ma60Below": lambda s: s.get("priceVsMA60") == "below",
    }
    return {
        name: [event_stock(row) for row in rows
               if predicate(row.get(state_key) if isinstance(row.get(state_key), dict) else {})]
        for name, predicate in predicates.items()
    }


NEXT_SESSION_BUCKETS = (
    "REGULAR_BREAKOUT_STRONG",
    "REGULAR_BREAKOUT_FAILED_AFTER_RECOVERY",
    "REGULAR_ABOVE_MA_MOMENTUM",
    "NEAR_BREAKOUT_WATCH",
    "PULLBACK_WATCH",
)


def next_session_candidates(rows, as_of):
    """Build deterministic, descriptive candidates from existing state labels.

    Regular-session technical labels decide the primary technical buckets.
    Current/CLOSED values are retained only as observation, volume, and
    after-session context; they never replace an unavailable regular state.
    """
    bucket_members = {name: [] for name in NEXT_SESSION_BUCKETS}
    candidates = []
    excluded = []

    breakout_rank = {"20_60": 0, "60": 1, "20": 2, "none": 3}
    volume_rank = {"surge": 0, "elevated": 1, "normal": 2, "unknown": 3}

    for row in rows:
        code = row["itemCode"]
        name = row["itemName"]
        regular = row.get("regularSessionState")
        current = row.get("currentState")
        market = row.get("market")
        regular = regular if isinstance(regular, dict) else {}
        current = current if isinstance(current, dict) else {}
        market = market if isinstance(market, dict) else {}

        if regular.get("status") != "CONFIRMED":
            excluded.append({
                "itemCode": code,
                "itemName": name,
                "reason": "REGULAR_SESSION_UNAVAILABLE",
            })
            continue

        breakout20 = regular.get("breakout20")
        breakout60 = regular.get("breakout60")
        if breakout20 == "confirmed" and breakout60 == "confirmed":
            strength = "20_60"
        elif breakout60 == "confirmed":
            strength = "60"
        elif breakout20 == "confirmed":
            strength = "20"
        else:
            strength = "none"

        session_rate = safe_number(market.get("sessionChangeRate"))
        buckets = []
        if strength != "none":
            buckets.append("REGULAR_BREAKOUT_STRONG")
        if (
            breakout20 == "failed" or breakout60 == "failed"
        ) and session_rate is not None and session_rate > 0:
            buckets.append("REGULAR_BREAKOUT_FAILED_AFTER_RECOVERY")
        if (
            regular.get("priceVsMA20") == "above"
            and regular.get("priceVsMA60") == "above"
        ):
            buckets.append("REGULAR_ABOVE_MA_MOMENTUM")

        pullback_state = current.get("pullbackState")
        if pullback_state in ("near_breakout20", "near_breakout60"):
            buckets.append("NEAR_BREAKOUT_WATCH")
        elif isinstance(pullback_state, str) and pullback_state.startswith("pullback"):
            buckets.append("PULLBACK_WATCH")

        if not buckets:
            continue

        volume_state = current.get("volumeState")
        if volume_state not in volume_rank:
            volume_state = "unknown"
        tags = []
        above20 = regular.get("priceVsMA20") == "above"
        above60 = regular.get("priceVsMA60") == "above"
        if above20 and above60:
            tags.append("ABOVE_MA20_MA60")
        else:
            if above20:
                tags.append("ABOVE_MA20")
            if above60:
                tags.append("ABOVE_MA60")
        volume_tags = {
            "surge": "VOLUME_SURGE",
            "elevated": "VOLUME_ELEVATED",
            "normal": "VOLUME_NORMAL",
        }
        if volume_state in volume_tags:
            tags.append(volume_tags[volume_state])
        if session_rate is not None:
            tags.append(
                "AFTER_UP" if session_rate > 0
                else "AFTER_DOWN" if session_rate < 0
                else "AFTER_FLAT"
            )

        candidate = {
            "itemCode": code,
            "itemName": name,
            "primaryBucket": buckets[0],
            "allBuckets": buckets,
            "breakoutStrength": strength,
            "regularSession": compact_dict(regular, [
                "price", "ma20", "ma60", "priceVsMA20", "priceVsMA60",
                "priorHigh20", "priorHigh60", "breakout20", "breakout60",
                "sourceDate", "sourceTime",
            ]),
            "current": compact_dict(current, ["price", "sourceTime", "pullbackState"]),
            "afterSession": {
                "referencePrice": market.get("referencePrice"),
                "referenceSourceTime": market.get("referenceSourceTime"),
                "sessionChange": market.get("sessionChange"),
                "sessionChangeRate": session_rate,
            },
            "volume": {
                "volumeRatio20": current.get("volumeRatio20"),
                "volumeState": volume_state,
            },
            "tags": tags,
            "metadata": {
                "technicalBasis": "REGULAR_SESSION",
                "volumeBasis": "CURRENT_DAILY_TECHNICAL",
                "afterBasis": "CLOSED_REFERENCE",
                "bucketBasis": {
                    bucket: "CURRENT_OBSERVATION"
                    for bucket in buckets
                    if bucket in ("NEAR_BREAKOUT_WATCH", "PULLBACK_WATCH")
                },
            },
        }
        candidate["_sort"] = (
            NEXT_SESSION_BUCKETS.index(candidate["primaryBucket"]),
            breakout_rank[strength],
            volume_rank[volume_state],
            -(session_rate if session_rate is not None else float("-inf")),
            -(safe_number(market.get("changeRate"))
              if safe_number(market.get("changeRate")) is not None
              else float("-inf")),
            code,
        )
        candidates.append(candidate)
        for bucket in buckets:
            bucket_members[bucket].append({"itemCode": code, "itemName": name})

    candidates.sort(key=lambda item: item["_sort"])
    for rank, candidate in enumerate(candidates, 1):
        candidate.pop("_sort")
        candidate["rank"] = rank
    for members in bucket_members.values():
        members.sort(key=lambda item: item["itemCode"])

    confirmed_count = len(rows) - len(excluded)
    if not confirmed_count:
        status = "UNAVAILABLE"
    elif excluded:
        status = "PARTIAL"
    else:
        status = "AVAILABLE"
    return {
        "status": status,
        "asOf": as_of,
        "candidateCount": len(candidates),
        "excludedCount": len(excluded),
        "buckets": {
            name: {
                "count": len(bucket_members[name]),
                "basis": (
                    "CURRENT_OBSERVATION"
                    if name in ("NEAR_BREAKOUT_WATCH", "PULLBACK_WATCH")
                    else "REGULAR_SESSION"
                ),
                "stocks": bucket_members[name],
            }
            for name in NEXT_SESSION_BUCKETS
        },
        "priority": candidates,
        "excluded": excluded,
        "methodology": {
            "priority": "LEXICOGRAPHIC",
            "weightedScoreUsed": False,
            "investmentRecommendation": False,
            "technicalBasis": "REGULAR_SESSION",
            "volumeBasis": "CURRENT_DAILY_TECHNICAL",
            "afterBasis": "CLOSED_REFERENCE",
        },
    }


def research_summary(rows, source):
    active = [row for row in rows if row["research"]["rankingEligible"]]
    active.sort(
        key=lambda row: (
            row["research"]["recentReportCount"],
            abs(row["research"]["targetMeanChangePct"] or 0),
        ),
        reverse=True,
    )
    return {
        "statusCounts": source.get("statusCounts", {}),
        "rankingEligibleCount": source.get("rankingEligibleCount", len(active)),
        "activeStocks": [
            {"itemCode": row["itemCode"], "itemName": row["itemName"], **row["research"]}
            for row in active
        ],
        "recentCoverageActiveCount": sum(
            row["research"]["recentReportCount"] > 0 for row in active
        ),
        "targetRevisionObservedCount": sum(
            (row["research"]["revisionUp"] or 0) > 0
            or (row["research"]["revisionDown"] or 0) > 0
            for row in active
        ),
        "risingTopicObservedCount": sum(
            bool(row["research"]["risingTopics"]) for row in active
        ),
    }


def market_research_cross(rows):
    buckets = {
        "MARKET_ACTIVE_RESEARCH_ACTIVE": [],
        "MARKET_ACTIVE_RESEARCH_LIMITED": [],
        "MARKET_QUIET_RESEARCH_ACTIVE": [],
        "MARKET_QUIET_RESEARCH_LIMITED": [],
        "MARKET_WEAK_RESEARCH_ACTIVE": [],
        "MARKET_WEAK_RESEARCH_LIMITED": [],
        "INSUFFICIENT_DATA": [],
    }
    for row in rows:
        market = row["market"]
        state = row["state"]
        research = row["research"]
        if not row.get("marketObserved") or market["changeRate"] is None:
            bucket = "INSUFFICIENT_DATA"
        else:
            market_active = any((
                market["changeRate"] > 0,
                state.get("breakout20") in ("attempt", "confirmed"),
                state.get("volumeState") in ("surge", "elevated"),
                state.get("priceVsMA20") == "above",
            ))
            research_active = (
                research["rankingEligible"]
                and research["recentReportCount"] > 0
            )
            if market_active and research_active:
                bucket = "MARKET_ACTIVE_RESEARCH_ACTIVE"
            elif market_active:
                bucket = "MARKET_ACTIVE_RESEARCH_LIMITED"
            elif market["changeRate"] < 0 and research_active:
                bucket = "MARKET_WEAK_RESEARCH_ACTIVE"
            elif market["changeRate"] < 0:
                bucket = "MARKET_WEAK_RESEARCH_LIMITED"
            elif research_active:
                bucket = "MARKET_QUIET_RESEARCH_ACTIVE"
            else:
                bucket = "MARKET_QUIET_RESEARCH_LIMITED"
        buckets[bucket].append(event_stock(row))
    return {name: {"count": len(items), "stocks": items} for name, items in buckets.items()}


def build_report(as_of=None, fetch_discovery_missing=False,
                 discovery_quote_fetcher=None, discovery_quote_normalizer=None,
                 discovery_clock=None, staging_input_dir=None):
    universe = load_json(UNIVERSE_PATH)
    quotes = load_json(QUOTES_PATH)
    quote_observed_at = quotes.get("generatedAt") or quotes.get("sourceTime") if isinstance(quotes, dict) else None
    quote_observed_date = None
    if quote_observed_at:
        quote_observed_date = str(quote_observed_at)[:10].replace("-", "")
    current_snapshot = {
        "status": "AVAILABLE" if MARKET_INDICES_PATH.exists() else "UNAVAILABLE",
        "source": "NAVER_SEPARATE",
        "currentSnapshotSource": "NAVER_SEPARATE",
        "observedDate": quote_observed_date,
        "observedAt": quote_observed_at,
        "path": str(MARKET_INDICES_PATH),
    }
    technical_inputs = load_report_technical_inputs(staging_input_dir, current_snapshot)
    technicals = technical_inputs["technicals"]
    states = technical_inputs["states"]
    analysis_technicals = technical_inputs["analysisTechnicals"] or technicals
    analysis_states = technical_inputs["analysisStates"] or states
    if staging_input_dir:
        quotes = restrict_staging_current_quotes(
            quotes, technical_inputs["metadata"]["targetDate"]
        )
    report_input_date = as_of or quote_observed_date or technical_inputs["metadata"].get("targetDate")
    historical_generation = (
        (technical_inputs.get("metadata") or {}).get("historicalGenerationId")
        or (technical_inputs.get("metadata") or {}).get("sourceGeneration")
    )
    expected_research_generation = (
        historical_generation
        if (technical_inputs.get("metadata") or {}).get("historicalIndicatorSource") == "KRX"
        else None
    )
    research_payload = load_research_slot_for_report(report_input_date, expected_research_generation)
    research = research_payload
    try:
        indices = load_json(MARKET_INDICES_PATH)
    except (OSError, ValueError, json.JSONDecodeError):
        indices = {"status": "UNAVAILABLE", "indices": [], "errors": [{"reason": "MISSING_INDEX_SNAPSHOT"}]}
    try:
        dynamic_universe = load_json(DYNAMIC_UNIVERSE_PATH)
    except (OSError, ValueError, json.JSONDecodeError):
        dynamic_universe = {"status": "UNAVAILABLE", "reason": "MISSING_DYNAMIC_UNIVERSE_SNAPSHOT", "themes": [], "leaders": []}
    def optional_json(path, default):
        try:
            return load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            return default
    shadow_latest = optional_json(V2_SHADOW_LATEST_PATH, {})
    shadow_history = optional_json(V2_SHADOW_HISTORY_PATH, {})
    shadow_status = optional_json(V2_SHADOW_STATUS_PATH, {})
    # A failed shadow run must not make the previous V2 latest/history look
    # current.  V1 reporting continues independently with an explicit V2
    # unavailable state.
    shadow_latest, shadow_history = _usable_v2_shadow_inputs(
        shadow_latest, shadow_history, shadow_status
    )
    published_summary = optional_json(MONITORING_PATH / "latest-summary.json", {})
    published_breadth = optional_json(MONITORING_PATH / "latest-breadth.json", {})
    published_quality = optional_json(MONITORING_PATH / "latest-quality.json", {})
    published_industries = optional_json(MONITORING_PATH / "latest-industries.json", {})
    published_themes = optional_json(MONITORING_PATH / "latest-theme-signals.json", {})
    breadth_full_market = published_breadth.get("fullMarket") if isinstance(published_breadth, Mapping) else {}
    breadth_full_market = breadth_full_market if isinstance(breadth_full_market, Mapping) else {}
    current_observation_date = compact_date(
        breadth_full_market.get("tradingDate")
        or published_breadth.get("tradingDate")
        or published_breadth.get("observationDate")
        or published_breadth.get("sourceDate")
        or published_quality.get("observationDate")
        or published_quality.get("sourceDate")
        or quote_observed_date
    )
    group_observation_date = current_observation_date or report_input_date
    published_industries = validated_group_source(
        published_industries, "industry", group_observation_date
    )
    published_themes = validated_group_source(
        published_themes,
        "theme",
        group_observation_date,
        published_industries.get("generatedAt"),
    )
    published_leaders = optional_json(MONITORING_PATH / "latest-leaders.json", {})
    market_snapshot = optional_json(MARKET_SNAPSHOT_PROJECTION_PATH, {
        "schemaVersion": "market-snapshot-report-projection-v1",
        "source": "NAVER_CURRENT_SNAPSHOT",
        "status": "UNAVAILABLE",
        "reason": "MARKET_SNAPSHOT_PROJECTION_NOT_FOUND",
        "rankings": {"status": "UNAVAILABLE", "items": {}},
        "investorFlow": {"status": "UNAVAILABLE", "periods": {}},
        "industries": {"status": "UNAVAILABLE", "top": []},
        "industryMembership": {"status": "UNAVAILABLE"},
    })
    market_snapshot = validate_market_snapshot_for_report(market_snapshot, group_observation_date)

    group_states = technical_inputs["groupStates"]

    quote_idx = index_rows(quotes)
    tech_idx = index_rows(technicals)
    state_idx = index_rows(states)
    enabled = enabled_universe(universe)
    # Research intelligence is a snapshot keyed by item code.  It can outlive
    # a user's enabled universe, so only active configured items count toward
    # this report's current research coverage.  Stale per-stock research is
    # not promoted back into the active universe by the report builder.
    enabled_codes = {str(item["itemCode"]) for item in enabled}
    research_idx = {
        code: row for code, row in research_index(research).items()
        if code in enabled_codes
    }
    member_idx = memberships(universe)

    rows = []
    state_fields = [
        "status", "source", "sourceDate", "sourceTime", "price", "close",
        "trend", "trendStatus",
        "priceVsMA20", "priceVsMA60", "maAlignment", "distanceMA20Pct",
        "distanceMA60Pct", "priorHigh20", "priorHigh60",
        "distancePriorHigh20Pct", "distancePriorHigh60Pct", "breakout20",
        "breakout60", "volumeRatio20", "volumeState", "pullbackState",
        "referencePrice", "referenceSourceTime", "referencePriceBasis",
        "referenceStatus", "sessionChange", "sessionChangeRate",
    ]

    for item in enabled:
        code = item["itemCode"]

        q = quote_summary(quote_idx.get(code))
        t = _legacy_staged_technical_row(tech_idx.get(code, {}))
        s = _legacy_staged_state_row(state_idx.get(code, {}))
        r = research_idx.get(code, {})
        current_state = s.get("current") if isinstance(s.get("current"), dict) else s
        regular_state = (s.get("regularSession")
                         if isinstance(s.get("regularSession"), dict)
                         and s["regularSession"].get("status") == "CONFIRMED"
                         else {})

        rows.append(
            {
                "itemCode": code,
                "itemName": item["stockName"],
                "groups": member_idx.get(code, []),
                "marketObserved": code in quote_idx,
                "market": q,
                "technical": compact_dict(
                    t,
                    [
                        "ma5",
                        "ma20",
                        "ma60",
                        "ma120",
                        "ma240",
                        "return1dPct",
                        "return5dPct",
                        "return20dPct",
                        "return60dPct",
                        "return120dPct",
                        "return240dPct",
                        "sourceDate",
                        "sourceTime",
                        "high20",
                        "high52w",
                        "high60",
                        "avgVolume20",
                        "volumeRatio20",
                        "confirmedStatus",
                        "provisional",
                    ],
                ),
                "state": compact_dict(
                    s,
                    [
                        "priceVsMA20",
                        "priceVsMA60",
                        "maAlignment",
                        "distanceMA20Pct",
                        "distanceMA60Pct",
                        "priorHigh20",
                        "priorHigh60",
                        "distancePriorHigh20Pct",
                        "distancePriorHigh60Pct",
                        "breakout20",
                        "breakout60",
                        "volumeRatio20",
                        "volumeState",
                        "pullbackState",
                    ],
                ),
                "currentState": compact_dict(current_state, state_fields),
                "regularSessionState": compact_dict(regular_state, state_fields),
                "research": {
                    "status": r.get("status"),
                    "rankingEligible": r.get("rankingEligible", False),
                    "recentReportCount": r.get("recentReportCount", 0),
                    "previousReportCount": r.get("previousReportCount", 0),
                    "recentTargetMean": r.get("recentTargetMean"),
                    "targetMeanChangePct": r.get("targetMeanChangePct"),
                    "targetDispersionChangePct": r.get(
                        "targetDispersionChangePct"
                    ),
                    "revisionUp": r.get("revisionUp", 0),
                    "revisionDown": r.get("revisionDown", 0),
                    "risingTopics": r.get("risingTopics", [])[:5],
                },
            }
        )

    market_movers = sorted(
        [
            row
            for row in rows
            if row["market"]["changeRate"] is not None
        ],
        key=lambda row: row["market"]["changeRate"],
        reverse=True,
    )

    report_date = as_of or datetime.now(KST).date().isoformat()
    full_market = full_market_from_derived_projection(
        technical_inputs.get("derivedProjection"),
        technical_inputs.get("metadata", {}).get("targetDate") or report_date,
    )
    technical_metadata = technical_inputs.get("metadata") or {}
    current_metadata = technical_metadata.get("currentSnapshot") or {}
    alignment = date_alignment(
        report_date,
        indices,
        full_market,
        observed_date=(
            current_metadata.get("observedDate")
            or current_metadata.get("tradingDate")
            or current_metadata.get("asOfDate")
        ),
        historical_source=technical_metadata.get("historicalIndicatorSource"),
        current_snapshot_source=technical_metadata.get("currentSnapshotSource"),
    )
    discovery = trading_discovery.discovery_status(
        report_date, discovery_source_path(report_date)
    )
    discovery_name_lookup = {}
    if discovery.get("status") in {"AVAILABLE", "AVAILABLE_EMPTY"}:
        try:
            name_source = load_json(MARKET_UNIVERSE_PATH)
            discovery_name_lookup = {
                str(row.get("code")): row.get("name")
                for row in (name_source.get("assets") or [])
                if isinstance(row, dict) and row.get("code") in discovery.get("symbols", [])
                and row.get("name")
            }
        except (OSError, ValueError, json.JSONDecodeError):
            discovery_name_lookup = {}
    try:
        full_market_signals = load_json(FULL_MARKET_SIGNALS_PATH)
    except (OSError, ValueError, json.JSONDecodeError):
        full_market_signals = {}
    try:
        theme_signals = load_json(MONITORING_PATH / "latest-theme-signals.json")
    except (OSError, ValueError, json.JSONDecodeError):
        theme_signals = {"status": "UNAVAILABLE", "themes": []}
    previous_trading_date = (
        technical_metadata.get("historicalTargetDate")
        or technical_metadata.get("targetDate")
    )
    session_context = build_preopen_session_context(
        report_date, indices, previous_trading_date
    )
    # Kiwoom candidates are sourced from the immutable trading-discovery
    # artifact.  Do not depend on the retired report JSON history for prior
    # report comparison.
    previous_discovery = {}
    supplemental_quotes = {"datas": []}
    lookup = {"networkCalls": 0, "requestedSymbols": [], "datas": [], "errors": []}
    published_supplemental = trading_discovery.supplemental_quotes_status(
        discovery, quotes.get("sourceTime"), discovery_quotes_source_path(report_date))
    supplemental_quotes = {
        "datas": [
            {**row, "_discoveryAdditionalLookup": True}
            for row in published_supplemental.get("datas", [])
        ],
        "candidateStatuses": published_supplemental.get("candidateStatuses") or [],
        "quoteCompleteness": published_supplemental.get("quoteCompleteness"),
    }
    if fetch_discovery_missing and discovery.get("status") == "AVAILABLE":
        existing_codes = set(quote_idx) | set(trading_discovery._index(full_market_signals))
        existing_codes |= set(trading_discovery._index(supplemental_quotes))
        try:
            lookup = trading_discovery.fetch_naver_missing_quotes(
                discovery["symbols"], existing_codes,
                fetcher=discovery_quote_fetcher,
                normalizer=discovery_quote_normalizer,
                clock=discovery_clock,
            )
            live_rows = [
                {**row, "_discoveryAdditionalLookup": True}
                for row in lookup["datas"]
            ]
            supplemental_quotes["datas"].extend(live_rows)
        except Exception as exc:
            lookup = {
                "networkCalls": 0, "requestedSymbols": [], "datas": [],
                "errors": [{"reason": type(exc).__name__}],
            }
    discovery_quotes = {
        **quotes,
        "datas": list(quotes.get("datas") or []) + supplemental_quotes["datas"],
        "candidateStatuses": supplemental_quotes.get("candidateStatuses") or [],
        "quoteCompleteness": supplemental_quotes.get("quoteCompleteness"),
    }
    kiwoom_section = trading_discovery.analyze(
        report_date, discovery, discovery_quotes,
        analysis_technicals, analysis_states,
        full_market_signals, previous_discovery,
        name_lookup=discovery_name_lookup,
    )
    leader_by_code = {
        str(row.get("code")): row for row in full_market.get("leaders", [])
        if isinstance(row, dict) and row.get("code")
    }
    dynamic_leader_evidence = []
    for leader in dynamic_universe.get("leaders", []):
        code = str(leader.get("code") or leader.get("itemCode") or "")
        evidence = leader_by_code.get(code, {}).get("technicalEvidence")
        if evidence is not None:
            dynamic_leader_evidence.append({"code": code, "name": leader.get("name"), "technicalEvidence": evidence})
    theme_analysis = []
    for theme in theme_signals.get("themes", []) if isinstance(theme_signals, dict) else []:
        if not isinstance(theme, dict):
            continue
        theme_analysis.append({
            "id": theme.get("id"), "name": theme.get("name"),
            "membershipStatus": theme.get("membershipStatus"),
            "memberCount": theme.get("memberCount"), "featureCount": theme.get("featureCount"),
            "coveragePct": theme.get("coveragePct"), "missingCodes": theme.get("missingCodes", []),
            "metrics": theme.get("metrics", {}),
        })
    kiwoom_section["additionalQuoteLookup"] = {
        "enabled": bool(fetch_discovery_missing),
        "networkCalls": lookup["networkCalls"],
        "requestedSymbols": lookup["requestedSymbols"],
        "errors": lookup["errors"],
        "publishedStatus": published_supplemental.get("status"),
        "publishedReason": published_supplemental.get("reason"),
    }
    registered_projection = registered_universe_projection(enabled, rows)
    # Performance is read-only and date-bound. Candidate snapshots and the
    # NAVER daily cache are the only price inputs.
    if discovery.get("status") == "AVAILABLE" and discovery.get("symbols"):
        kiwoom_prices, kiwoom_trading_dates = load_naver_daily_prices(
            ROOT, discovery.get("symbols")
        )
        kiwoom_performance = build_kiwoom_performance(
            discovery, kiwoom_prices, kiwoom_trading_dates,
            official_trading_dates=None, now_date=report_date
        )
    else:
        # Preserve the existing report shape for missing/invalid/empty source
        # snapshots; the immutable discovery status remains authoritative.
        kiwoom_performance = None
    v2_changes = compare_v2_changes(shadow_history)
    technical_candidate_section = next_session_candidates(rows, report_date)
    # The report contract's trading-date evidence belongs to the current
    # monitoring snapshot.  Historical KRX technical metadata is merged into
    # the quality payload below, but must never replace the published current
    # session evidence with the KRX target date.
    contract_summary = published_summary
    contract_breadth = published_breadth
    contract_quality = published_quality
    contract_leaders = published_leaders
    historical_technical_market = None
    if technical_inputs["metadata"].get("historicalIndicatorSource") == "KRX":
        # The KRX generation is authoritative for historical indicators and
        # market breadth in this isolated path.  NAVER current observations,
        # industries, themes, and research remain separate inputs.
        krx_projection = technical_inputs["derivedProjection"] or {}
        krx_full_market = dict(krx_projection.get("fullMarket") or {})
        krx_counts = (technical_inputs.get("technicals") or {}).get("counts") or {}
        krx_full_market.setdefault("instrumentCount", krx_counts.get("instrumentCount"))
        krx_full_market.setdefault("authoritativeCount", krx_counts.get("instrumentCount"))
        krx_lineage = technical_inputs["metadata"].get("historicalLineage") or {}
        krx_technicals = technical_inputs.get("analysisTechnicals") or {}
        krx_states = technical_inputs.get("analysisStates") or {}
        krx_quality = krx_full_market.get("quality") or {}
        krx_missing_history = krx_technicals.get("missingHistoryCodes")
        if not isinstance(krx_missing_history, list):
            krx_missing_history = krx_technicals.get("missingCodes") or []
        krx_regular_unavailable = krx_states.get("regularSessionUnavailableCodes") or []
        krx_current_missing = quotes.get("missingCodes") or []
        current_snapshot = technical_inputs["metadata"].get("currentSnapshot") or {}
        current_breadth_full_market = published_breadth.get("fullMarket") if isinstance(published_breadth, Mapping) else {}
        current_breadth_full_market = current_breadth_full_market if isinstance(current_breadth_full_market, Mapping) else {}
        observed_date = (
            current_breadth_full_market.get("tradingDate")
            or published_breadth.get("observationDate")
            or published_breadth.get("sourceDate")
            or published_quality.get("observationDate")
            or published_quality.get("sourceDate")
            or current_snapshot.get("observedDate")
            or current_snapshot.get("tradingDate")
            or current_snapshot.get("asOfDate")
        )
        observed_at = (
            current_snapshot.get("observedAt")
            or current_snapshot.get("generatedAt")
            or quotes.get("generatedAt")
            or published_breadth.get("generatedAt")
        )
        historical_quality = {
            "historicalTargetDate": technical_inputs["metadata"].get("targetDate"),
            "collectionQualityStatus": "KRX_M10_STAGED_AVAILABLE",
            "sourceQualityStatus": "KRX_M10_STAGED_AVAILABLE",
            "currentCount": krx_full_market.get("quality", {}).get("currentCount"),
            "authoritativeCount": krx_full_market.get("quality", {}).get("authoritativeCount"),
            "historicalIndicatorSource": "KRX",
            "historicalGenerationId": krx_lineage.get("generationId") or technical_inputs["metadata"].get("historicalGenerationId"),
            "historicalTargetDate": krx_lineage.get("targetDate") or technical_inputs["metadata"].get("targetDate"),
            "historicalFallbackAllowed": False,
            "historicalReceiptStatus": krx_lineage.get("receiptStatus", "UNAVAILABLE"),
            "historicalLineageStatus": krx_lineage.get("status", "UNAVAILABLE"),
            "historicalEntityCount": krx_lineage.get("entityCount") or krx_quality.get("entityCount"),
            "historicalInstrumentCount": krx_lineage.get("instrumentCount") or krx_quality.get("instrumentCount"),
            "historicalObservedInstrumentCount": krx_quality.get("currentCount"),
            "missingHistoryCodes": list(krx_missing_history),
            "regularSessionUnavailableCodes": list(krx_regular_unavailable),
            "currentSnapshotMissingCodes": list(krx_current_missing),
            "historicalAdjustmentStatus": krx_lineage.get("adjustmentStatus", "UNAVAILABLE"),
            "currentSnapshotSource": "NAVER_SEPARATE",
            "observedDate": observed_date,
            "observedAt": observed_at,
            "manifestSha256": technical_inputs["metadata"].get("manifestSha256"),
            "monitoringSha256": technical_inputs["metadata"].get("monitoringSha256"),
            "entityCount": technical_inputs["metadata"].get("entityCount"),
            "instrumentCount": technical_inputs["metadata"].get("instrumentCount"),
        }
        contract_quality = {**published_quality, **historical_quality}
        historical_technical_market = krx_full_market
    report_contract = build_report_contract(
        indices=indices,
        summary=contract_summary,
        breadth=contract_breadth,
        quality=contract_quality,
        industries=published_industries,
        themes=published_themes,
        v2_latest=shadow_latest,
        v2_history=shadow_history,
        leaders=contract_leaders,
        technical_candidates=technical_candidate_section.get("priority") or [],
        kiwoom_candidates=kiwoom_section,
        research=research,
        v2_provenance=build_source_provenance(
            ROOT, result=shadow_latest, include_outputs=True
        ),
        kiwoom_performance=kiwoom_performance,
        v2_changes=v2_changes,
        session_context=session_context,
        registered_universe=registered_projection,
        current_market_observation=published_breadth,
        historical_technical_market=historical_technical_market,
        v1_groups=configured_interest_group_projection(universe),
        market_snapshot=market_snapshot,
    )
    kiwoom_contract_errors = validate_kiwoom_candidate_binding(
        discovery, report_contract
    )
    if kiwoom_contract_errors:
        raise ValueError(
            "KIWOOM_CANDIDATE_CONTRACT_INVALID:"
            + ",".join(kiwoom_contract_errors)
        )

    missing_market = [
        {
            "itemCode": item["itemCode"],
            "itemName": item["stockName"],
        }
        for item in enabled
        if (
            item["itemCode"] not in quote_idx
            or item["itemCode"] not in tech_idx
            or item["itemCode"] not in state_idx
        )
    ]

    coverage_state = coverage_status(
        len(enabled),
        len(quote_idx),
        len(tech_idx),
        len(state_idx),
        quotes.get("status"),
        quotes.get("fresh"),
        historical_as_of_date=(technical_inputs.get("metadata") or {}).get("historicalTargetDate")
        or (technical_inputs.get("metadata") or {}).get("targetDate"),
        observed_date=(technical_inputs.get("metadata") or {}).get("currentSnapshot", {}).get("observedDate")
        or (technical_inputs.get("metadata") or {}).get("currentSnapshot", {}).get("tradingDate")
        or (technical_inputs.get("metadata") or {}).get("currentSnapshot", {}).get("asOfDate"),
        current_snapshot_source=(technical_inputs.get("metadata") or {}).get("currentSnapshotSource"),
    )
    breadth = market_breadth(rows)
    current_breadth = market_breadth(rows, "currentState")
    regular_breadth = market_breadth(
        rows, "regularSessionState", include_directions=False
    )
    regular_confirmed = sum(bool(row["regularSessionState"]) for row in rows)
    regular_breadth.update({
        "status": "AVAILABLE" if regular_confirmed == len(enabled)
        else "PARTIAL" if regular_confirmed else "UNAVAILABLE",
        "confirmedCount": regular_confirmed,
    })
    groups = normalized_group_summary(group_states)
    research_overview = research_summary(rows, research)
    monitored_evidence = full_market.get("monitoredUniverse", {})
    monitored_current = monitored_evidence.get("current", current_breadth)
    monitored_regular = monitored_evidence.get("regularSession", regular_breadth)
    full_market_summary_data = full_market_summary(full_market, alignment)

    alignment_is_valid = alignment["status"] in {"OK", "SEPARATE_SOURCES_ALIGNED"}
    report_status = "OK" if coverage_state == "OK" else "DEGRADED"
    status_reason = None
    if coverage_state == "EMPTY_CONFIGURED_UNIVERSE" or not alignment_is_valid:
        report_status = "PARTIAL"
        status_reason = "DATE_ALIGNMENT_UNVERIFIED"
    monitored_scope = (
        "MONITORED_UNIVERSE_EMPTY" if not enabled
        else "MONITORED_UNIVERSE_CONFIGURED"
    )
    monitored_summary = {
        "status": coverage_state,
        "scope": monitored_scope,
        "configuredCount": len(enabled),
        "observedCount": breadth.get("observedCount", 0),
        "coverageStatus": coverage_state,
        "breadth": breadth,
        "current": monitored_current,
        "regularSession": monitored_regular,
    }
    monitored_top_gainers = [
        {
            "itemCode": row["itemCode"],
            "itemName": row.get("stockName") or row.get("itemName"),
            "changeRate": row["market"]["changeRate"],
            "price": row["market"]["price"],
        }
        for row in market_movers[:10]
    ]
    monitored_top_decliners = [
        {
            "itemCode": row["itemCode"],
            "itemName": row.get("stockName") or row.get("itemName"),
            "changeRate": row["market"]["changeRate"],
            "price": row["market"]["price"],
        }
        for row in reversed(market_movers[-10:])
    ]
    monitored_summary.update({
        "topGainers": monitored_top_gainers,
        "topDecliners": monitored_top_decliners,
        "rankingBasis": "MONITORED_UNIVERSE_CHANGE_RATE",
    })

    return {
        "status": report_status,
        "statusReason": status_reason,
        "coverageStatus": coverage_state,
        "version": "daily-market-research-report-v2",
        "asOf": report_date,
        "dateAlignment": alignment,
        "generatedAt": datetime.now(KST).isoformat(),
        "sources": {
            "quotes": {
                "path": "data/quotes.json",
                "generatedAt": quotes.get("generatedAt"),
                "sourceTime": quotes.get("sourceTime"),
                "status": quotes.get("status"),
                "fresh": quotes.get("fresh"),
                "currentSnapshotSeparated": quotes.get("currentSnapshotSeparated", False),
                "historicalTechnicalDate": quotes.get("historicalTechnicalDate"),
                "observedSourceDates": quotes.get("observedSourceDates", []),
                "currentSnapshotDateStatus": quotes.get("currentSnapshotDateStatus"),
            },
            "technicals": {
                "path": (str(Path(staging_input_dir) / "technicals.json")
                         if staging_input_dir else "data/technicals.json"),
                "generatedAt": technicals.get("generatedAt"),
                "status": technicals.get("status"),
                "scope": "REGISTERED_UNIVERSE_PROJECTION" if staging_input_dir else "DEFAULT",
            },
            "states": {
                "path": (str(Path(staging_input_dir) / "states.json")
                         if staging_input_dir else "data/states.json"),
                "generatedAt": states.get("generatedAt"),
                "status": states.get("status"),
                "scope": "REGISTERED_UNIVERSE_PROJECTION" if staging_input_dir else "DEFAULT",
            },
            "researchIntelligence": {
                "path": (research.get("slotLineage") or {}).get("artifactPath") or "data/research/intelligence/slots/<date>-<slot>.json",
                "asOf": research.get("asOf"),
                "status": research.get("status"),
                "unavailableReason": research.get("unavailableReason"),
                "generatedAt": research.get("generatedAt"),
                "producerStatus": (research.get("slotLineage") or {}).get("producerStatus"),
                "freshnessStatus": (research.get("slotLineage") or {}).get("freshnessStatus"),
                "checkedAt": (research.get("slotLineage") or {}).get("checkedAt"),
            },
            "indices": {
                "path": "data/market/indices.json",
                "generatedAt": indices.get("generatedAt"),
                "status": indices.get("status"),
                "source": indices.get("source"),
            },
            "dynamicUniverse": {
                "path": "data/dynamic-universe/latest.json",
                "generatedAt": dynamic_universe.get("generatedAt"),
                "status": dynamic_universe.get("status", "AVAILABLE"),
                "industryEvidence": dynamic_universe.get("industryEvidence"),
                "source": "GITHUB_DYNAMIC_UNIVERSE",
            },
            "themeSignals": {
                "path": "data/monitoring/latest-theme-signals.json",
                "generatedAt": theme_signals.get("generatedAt"),
                "status": theme_signals.get("status"),
                "sourceStatus": theme_signals.get("sourceStatus"),
                "themeCount": theme_signals.get("themeCount", len(theme_analysis)),
            },
        },
        "coverage": {
            "enabledStockCount": len(enabled),
            "quoteCount": len(quote_idx),
            "technicalCount": len(tech_idx),
            "stateCount": len(state_idx),
            "researchStockCount": len(research_idx),
            "missingMarketCount": len(missing_market),
            "missingMarketStocks": missing_market,
        },
        "technicalInput": technical_inputs["metadata"],
        "marketSummary": {
            "fullMarket": full_market_summary_data,
            "monitoredUniverse": monitored_summary,
            "breadth": breadth,
            "indices": indices,
            "dynamicThemes": dynamic_universe.get("themes", []),
            "topGainers": monitored_top_gainers,
            "topDecliners": monitored_top_decliners,
            "topGainersScope": monitored_scope,
            "topDeclinersScope": monitored_scope,
        },
        "monitoredUniverse": monitored_summary,
        "fullMarket": full_market,
        "dynamicUniverse": {
            "status": dynamic_universe.get("status", "AVAILABLE"),
            "generatedAt": dynamic_universe.get("generatedAt"),
            "industryEvidence": dynamic_universe.get("industryEvidence"),
            "themes": dynamic_universe.get("themes", []),
            "leaders": dynamic_universe.get("leaders", []),
            "initialObservationCandidates": dynamic_universe.get("initialObservationCandidates", []),
            "leaderEvidence": dynamic_leader_evidence,
            "themeAnalysis": theme_analysis,
            "source": "GITHUB_DYNAMIC_UNIVERSE",
        },
        "v1V2Analysis": shadow_v1_v2_analysis(
            dynamic_universe, shadow_latest, shadow_history, shadow_status
        ),
        "reportContract": report_contract,
        "sessionContext": session_context,
        "groupSummary": groups,
        "technicalEvents": technical_events(rows),
        "technicalEventsCurrent": technical_events(rows, "currentState"),
        "technicalEventsRegularSession": technical_events(
            rows, "regularSessionState"
        ),
        "nextSessionCandidates": technical_candidate_section,
        "researchSummary": research_overview,
        "marketResearchCross": market_research_cross(rows),
        "kiwoomPreviousCandidates": kiwoom_section,
        "stocks": rows,
        "methodology": {
            "investmentRecommendation": False,
            "buySellScoreUsed": False,
            "researchRankingRequiresEligibleStatus": True,
            "description": (
                "Descriptive daily market and research intelligence report. "
                "Market, technical, state, group and research signals are "
                "kept separate; no automatic buy/sell recommendation is made."
            ),
        },
    }


def write_markdown(report, path):
    breadth = report["marketSummary"]["breadth"]
    market_summary = report.get("marketSummary") or {}
    full_summary = market_summary.get("fullMarket") or {}
    monitored_summary = market_summary.get("monitoredUniverse") or report.get("monitoredUniverse") or {}
    historical_source = (report.get("technicalInput") or {}).get("historicalIndicatorSource")
    degraded = report["status"] != "OK"
    lines = [
        f"# 국내증시 일일 종합 리포트 - {report['asOf']}",
        "",
        f"- 생성시각: {report['generatedAt']}",
        f"- 관심종목 설정: {report['coverage']['enabledStockCount']}종목",
    ]
    if degraded:
        lines.extend([
            "",
            f"> **데이터 품질 경고:** {report['coverageStatus']} 상태입니다. "
            "아래 내용은 불완전하거나 오래된 시장 데이터를 포함할 수 있습니다.",
        ])

    alignment = report.get("dateAlignment") or {}
    if alignment.get("status") not in {"OK", "SEPARATE_SOURCES_ALIGNED"}:
        lines.extend([
            "",
            f"> **기준일 정합성 경고:** 상태 {alignment.get('status') or '확인 불가'}, "
            f"리포트 기준일 {alignment.get('reportTradingDate') or '확인 불가'}과 "
            "지수·시장 스냅샷·이동평균 자료의 원천 기준일을 동일 시점으로 간주하지 않습니다.",
            f"> Full-market 상태: {alignment.get('fullMarketStatus') or '확인 불가'}; "
            f"사유: {alignment.get('fullMarketReason') or '없음'}",
            f"> 지수 날짜 검증: {alignment.get('indexAlignmentStatus') or '확인 불가'}; "
            f"확인된 지수 날짜: {', '.join(alignment.get('indexSourceDates', [])) or '확인 불가'}",
            f"> 원천 날짜 차이: {', '.join(alignment.get('mismatches', [])) or '없음'}; "
            f"누락 증거: {', '.join(alignment.get('missingEvidence', [])) or '없음'}",
        ])
    elif alignment.get("status") == "SEPARATE_SOURCES_ALIGNED":
        lines.extend([
            "",
            "> 기준일 분리: KRX 전일 확정 지표와 NAVER 당일 현재 관측을 "
            "서로 다른 원천 날짜로 검증했습니다.",
            f"> historicalAsOfDate: {alignment.get('historicalAsOfDate') or '확인 불가'}; "
            f"observedDate: {alignment.get('observedDate') or '확인 불가'}",
        ])

    lines.extend([
        "",
        "## 1. 시장 요약",
        "",
        "### 전체시장 분석",
        "",
    ])

    if full_summary.get("status") == "AVAILABLE":
        full_breadth = full_summary.get("breadth") or {}
        lines.extend([
            "- 분석 상태: 검증된 전체시장 자료 사용",
            f"- 기준일: {full_summary.get('tradingDate') or '확인 불가'}",
            f"- 기준시각: {full_summary.get('sourceTime') or '확인 불가'}; "
            f"자료 품질: {full_summary.get('qualityStatus') or '확인 불가'}; "
            f"원천 상태: {full_summary.get('sourceQualityStatus') or '확인 불가'}",
            f"- 전체시장 종목: {full_summary.get('quality', {}).get('currentCount', '확인 불가')}/"
            f"{full_summary.get('quality', {}).get('authoritativeCount', '확인 불가')}종목",
            f"- 상승/하락/보합: {full_breadth.get('advancers', '확인 불가')}/"
            f"{full_breadth.get('decliners', '확인 불가')}/{full_breadth.get('unchanged', '확인 불가')}",
        ])
        for label, key in (("MA20", "aboveMA20"), ("MA60", "aboveMA60"), ("MA120", "aboveMA120")):
            metric = full_breadth.get(key) or {}
            lines.append(
                f"- {label} 위: {metric.get('count', '확인 불가')}종목 "
                f"(대상 {metric.get('eligibleCount', '확인 불가')}, "
                f"비율 {metric.get('pct', '확인 불가')}%)"
            )
        volume = full_breadth.get("volumeAbove20DayAverage") or {}
        lines.append(
            f"- 평균 대비 거래량 증가: {volume.get('count', '확인 불가')}종목 "
            f"(대상 {volume.get('eligibleCount', '확인 불가')}, "
            f"비율 {volume.get('pct', '확인 불가')}%)"
        )
        industries = full_summary.get("industries") or []
        lines.append(f"- 업종 분석: {len(industries)}개 업종")
        lines.append("- 업종 정렬: 1일 평균 수익률 내림차순(값 미확인 업종은 후순위)")
        for industry in industries[:5]:
            name = industry.get("name") or industry.get("id") or "이름 미확인"
            average = industry.get("avgReturn1D")
            relative = industry.get("relativeStrengthVsMarket")
            advance = industry.get("advancePct")
            average_text = f"1일 평균 {average:+.2f}%" if isinstance(average, (int, float)) else "1일 평균 확인 불가"
            relative_text = f"상대강도 {relative:+.2f}" if isinstance(relative, (int, float)) else "상대강도 확인 불가"
            advance_text = f"상승 비율 {advance:.2f}%" if isinstance(advance, (int, float)) else "상승 비율 확인 불가"
            lines.append(
                f"  - {name}: {average_text}, {relative_text}, {advance_text}, "
                f"상태 {korean_status(industry.get('persistence', {}).get('status'))}"
            )
        leaders = full_summary.get("leaders") or []
        lines.append(f"- 전체시장 주도주 근거: {len(leaders)}종목")
        reason_labels = {
            "POSITIVE_RETURN": "상승률 양호", "VOLUME_ABOVE_20D_AVERAGE": "20일 평균 대비 거래량 증가",
            "ABOVE_MA20": "MA20 상회", "ABOVE_MA60": "MA60 상회",
            "BREAKOUT_20_INTRADAY": "20일 고점 돌파", "RETAINED_FROM_PREVIOUS": "전회 유지",
            "NEW_IN_CURRENT_LEADERS": "신규 편입",
        }
        for leader in leaders[:5]:
            change = leader.get("changeRate")
            volume_ratio = leader.get("current_volume_ratio_20")
            change_text = f"등락률 {change:+.2f}%" if isinstance(change, (int, float)) else "등락률 확인 불가"
            volume_text = f"거래량 배수 {volume_ratio:.2f}" if isinstance(volume_ratio, (int, float)) else "거래량 지표 확인 불가"
            ma_text = ", ".join(
                f"{label} {'상회' if leader.get(key) is True else '하회' if leader.get(key) is False else '확인 불가'}"
                for label, key in (("MA20", "above_ma20"), ("MA60", "above_ma60"))
            )
            evidence_status = korean_status((leader.get("technicalEvidence") or {}).get("status"))
            reasons = ", ".join(reason_labels.get(reason, reason) for reason in (leader.get("selectionReasons") or [])) or "선정 사유 확인 불가"
            lines.append(
                f"  - {leader.get('name') or '이름 미확인'}({leader.get('code') or '코드 미확인'}): "
                f"{change_text}, {volume_text}, {ma_text}, 선정 사유 {reasons}, 증거 {evidence_status}"
            )
    else:
        reason = full_summary.get("reason") or "확인 불가"
        lines.extend([
            f"- 분석 상태: {('부분 확인' if full_summary.get('status') == 'PARTIAL' else '확인 불가')} ({reason})",
            "- 전체시장 수치는 기준일 또는 원천 증거가 검증되지 않아 당일 정상값으로 대체하지 않았습니다.",
        ])

    lines.extend([
        "",
        "### 관심종목 분석",
        "",
        f"- 설정 종목 수: {monitored_summary.get('configuredCount', 0)}종목",
        f"- 분석 상태: {monitored_summary.get('coverageStatus') or '확인 불가'}",
    ])
    if monitored_summary.get("configuredCount", 0) == 0:
        lines.append("- 관심종목이 설정되지 않았습니다. 이는 전체시장 종목 수 0을 의미하지 않습니다.")
    else:
        lines.extend([
            f"- 관측 종목: {breadth['observedCount']}종목",
            f"- 상승/하락/보합: {breadth['upCount']}/{breadth['downCount']}/{breadth['flatCount']}",
            f"- MA20 위/아래: {breadth['ma20AboveCount']}/{breadth['ma20BelowCount']}",
            f"- MA60 위/아래: {breadth['ma60AboveCount']}/{breadth['ma60BelowCount']}",
            f"- 20일 돌파 확인/시도/실패: {breadth['breakout20ConfirmedCount']}/{breadth['breakout20AttemptCount']}/{breadth['breakout20FailedCount']}",
            f"- 거래량 급증/증가: {breadth['volumeSurgeCount']}/{breadth['volumeElevatedCount']}",
        ])

    lines.extend(["", "### 코스피·코스닥 지수", ""])

    index_snapshot = report["marketSummary"].get("indices") or {}
    if index_snapshot.get("status") == "OK":
        for index in index_snapshot.get("indices", []):
            lines.append(
                f"- {index['name']}: {index['close']:,.2f} "
                f"({index['changePoints']:+,.2f}p, {index['changeRate']:+.2f}%), "
                f"시장상태 {index.get('marketStatus') or '확인 불가'}, "
                f"원천시각 {index.get('sourceTime') or '확인 불가'}"
            )
    else:
        lines.append(f"- 지수 자료 확인 불가: {index_snapshot.get('errors') or 'UNKNOWN'}")

    next_candidates = report.get("nextSessionCandidates") or {}
    candidate_scope = monitored_summary.get("scope") or "확인 불가"
    candidates = next_candidates.get("priority") or []
    excluded_candidates = next_candidates.get("excluded") or []
    report_as_of = report.get("asOf")
    verified_candidates, unverified_candidates = split_candidates_for_markdown(
        candidates, report_as_of
    )
    lines.extend(["", "### 다음 거래일 관찰 후보", ""])
    lines.extend([
        f"- 후보 분석 기준일: {next_candidates.get('asOf') or '확인 불가'}",
        f"- 리포트 기준일 대조: {report_as_of or '확인 불가'}",
        f"- 분석 기준일: {alignment.get('reportTradingDate') or report.get('asOf') or '확인 불가'}",
        f"- 분석 범위: {candidate_scope}; 전체시장 후보를 임의 생성하지 않습니다.",
        f"- 후보 선정 상태: {korean_status(next_candidates.get('status'))}; "
        "우선순위는 기존 정규장 확정 버킷과 거래량·장후 관찰값의 사전식 정렬입니다.",
        f"- 후보 총수: {next_candidates.get('candidateCount', 0)}종목; "
        f"제외: {next_candidates.get('excludedCount', len(excluded_candidates))}종목; "
        f"정규장 날짜 일치 검증: {len(verified_candidates)}종목; "
        f"날짜 불일치·누락: {len(unverified_candidates)}종목",
    ])
    if candidate_scope == "MONITORED_UNIVERSE_EMPTY":
        lines.append("- 관심종목 설정이 없어 후보를 생성하지 않았습니다.")
    elif verified_candidates:
        lines.append("- 아래 목록은 기존 우선순위 상위 10개이며 매매 추천이나 점수가 아닙니다.")
        for candidate in verified_candidates[:10]:
            regular = candidate.get("regularSession") or {}
            current = candidate.get("current") or {}
            after = candidate.get("afterSession") or {}
            volume = candidate.get("volume") or {}
            regular_price = regular.get("price")
            current_price = current.get("price")
            price_delta = (
                current_price - regular_price
                if isinstance(current_price, (int, float))
                and isinstance(regular_price, (int, float))
                else None
            )
            bucket_text = ", ".join(
                CANDIDATE_BUCKET_LABELS.get(bucket, bucket)
                for bucket in candidate.get("allBuckets", [])
            ) or "관찰 유형 확인 불가"
            volume_state = CANDIDATE_VOLUME_LABELS.get(
                volume.get("volumeState"), "확인 불가"
            )
            price_delta_text = display_number(price_delta, "")
            if price_delta is not None:
                price_delta_text += "원"
            lines.extend([
                f"- {candidate.get('rank', '확인 불가')}. "
                f"{candidate.get('itemName') or '이름 미확인'} "
                f"({candidate.get('itemCode') or '코드 미확인'})",
                f"  - 관찰 유형: {bucket_text}",
                f"  - 정규장 확정: 기준일 {regular.get('sourceDate') or '확인 불가'}, "
                f"기준시각 {regular.get('sourceTime') or '확인 불가'}, "
                f"MA20 {display_candidate_state(regular.get('priceVsMA20'))}, "
                f"MA60 {display_candidate_state(regular.get('priceVsMA60'))}, "
                f"20일 돌파 {display_candidate_state(regular.get('breakout20'))}, "
                f"60일 돌파 {display_candidate_state(regular.get('breakout60'))}",
                f"  - 현재 관찰값: 가격 {display_number(current_price, '원')}, "
                f"눌림 상태 {display_candidate_state(current.get('pullbackState'))}, "
                f"기준시각 {current.get('sourceTime') or '확인 불가'}",
                f"  - 정규장 대비: 정규장 가격 {display_number(regular_price, '원')}, "
                f"현재 관찰값 차이 {price_delta_text}, "
                f"장후 등락률 {display_number(after.get('sessionChangeRate'), '%')}",
                f"  - 거래량: {display_number(volume.get('volumeRatio20'), '배')}, "
                f"상태 {volume_state}; 정규장 근거 {(candidate.get('metadata') or {}).get('technicalBasis') or '확인 불가'}",
            ])
    elif candidates:
        lines.append(
            "- 정규장 원천 날짜가 리포트 기준일과 일치하는 검증 후보가 없어 목록에 표시하지 않았습니다."
        )
    else:
        lines.append(
            "- 확인 가능한 후보가 없습니다. 정규장 확정 증거 또는 기존 관찰 조건이 부족한 상태입니다."
        )
    if unverified_candidates:
        lines.append("- 정규장 날짜 검증 제외:")
        for candidate, reason_code in unverified_candidates[:10]:
            regular = candidate.get("regularSession") or {}
            reason = CANDIDATE_DATE_REASON_LABELS.get(reason_code, reason_code)
            lines.append(
                f"  - {candidate.get('itemName') or '이름 미확인'} "
                f"({candidate.get('itemCode') or '코드 미확인'}): {reason}; "
                f"원천 날짜 {regular.get('sourceDate') or '확인 불가'}, "
                f"리포트 기준일 {report_as_of or '확인 불가'}"
            )
    if excluded_candidates:
        lines.append("- 후보 제외 사유:")
        for excluded in excluded_candidates[:10]:
            reason = CANDIDATE_EXCLUSION_LABELS.get(
                excluded.get("reason"), excluded.get("reason") or "확인 불가"
            )
            lines.append(
                f"  - {excluded.get('itemName') or '이름 미확인'} "
                f"({excluded.get('itemCode') or '코드 미확인'}): {reason}"
            )

    lines.extend(["", "### 위험·주의 요인", "", "#### 데이터 품질 경고"])
    quality_warnings = []
    if not alignment:
        quality_warnings.append("기준일 정합성 검증 결과가 없습니다.")
    elif alignment.get("status") != "OK":
        quality_warnings.append(
            f"기준일 정합성 {korean_status(alignment.get('status'))}: "
            f"{', '.join(alignment.get('mismatches', [])) or '불일치·누락 세부사유 확인 불가'}"
        )
    if full_summary.get("status") != "AVAILABLE":
        quality_warnings.append(
            f"전체시장 증거 {korean_status(full_summary.get('status'))}: "
            f"{full_summary.get('reason') or '원천 증거 확인 불가'}"
        )
    if report.get("coverageStatus") != "OK":
        quality_warnings.append(
            f"관심종목 데이터 범위 {korean_status(report.get('coverageStatus'))}"
        )
    if candidate_scope == "MONITORED_UNIVERSE_EMPTY":
        quality_warnings.append("관심종목 분석이 설정되지 않았습니다.")
    if next_candidates.get("status") == "UNAVAILABLE":
        quality_warnings.append("정규장 기술 증거가 부족해 후보를 확정할 수 없습니다.")
    if quality_warnings:
        lines.extend(f"- {warning}" for warning in quality_warnings)
    else:
        lines.append("- 확인된 데이터 품질 경고 없음")

    lines.extend(["", "#### 기술적 관찰 주의사항"])
    technical_warnings = []
    regular_events = report.get("technicalEventsRegularSession") or {}
    breakout20_failed = len(regular_events.get("breakout20Failed") or [])
    breakout60_failed = len(regular_events.get("breakout60Failed") or [])
    if breakout20_failed or breakout60_failed:
        technical_warnings.append(
            f"정규장 돌파 실패 관찰: 20일 {breakout20_failed}종목, "
            f"60일 {breakout60_failed}종목"
        )
    missing_ma = sum(
        1 for candidate in verified_candidates
        if (candidate.get("regularSession") or {}).get("priceVsMA20") in (None, "")
        or (candidate.get("regularSession") or {}).get("priceVsMA60") in (None, "")
    )
    missing_volume = sum(
        1 for candidate in verified_candidates
        if (candidate.get("volume") or {}).get("volumeRatio20") in (None, "")
    )
    if missing_ma:
        technical_warnings.append(f"후보 {missing_ma}종목의 이동평균 상태가 확인 불가입니다.")
    if missing_volume:
        technical_warnings.append(f"후보 {missing_volume}종목의 거래량 배수가 확인 불가입니다.")
    if excluded_candidates:
        technical_warnings.append(
            f"후보 {len(excluded_candidates)}종목은 정규장 증거 부족 등으로 분석에서 제외됐습니다."
        )
    if unverified_candidates:
        technical_warnings.append(
            f"후보 {len(unverified_candidates)}종목은 정규장 원천 날짜 불일치·누락으로 표시에서 제외됐습니다."
        )
    if technical_warnings:
        lines.extend(f"- {warning}" for warning in technical_warnings)
    else:
        lines.append("- 확인된 기술적 관찰 주의사항 없음")

    dynamic = report.get("dynamicUniverse", {})
    lines.extend(["", "### 동적 Universe 테마·관찰 결과", ""])
    lines.append(
        f"- 운영 선정 상태: {korean_status(dynamic.get('status'))}; "
        f"원천: GitHub 동적 Universe; 생성시각 {dynamic.get('generatedAt') or '확인 불가'}"
    )
    evidence = dynamic.get("industryEvidence") or {}
    lines.append(f"- 업종 근거: {korean_status(evidence.get('status') or evidence.get('industryEvidenceUsable'))}")
    themes = dynamic.get("themes") or []
    if themes:
        lines.append(f"- 운영 동적 테마: {len(themes)}개")
        for theme in themes[:10]:
            lines.append(f"- {theme.get('name') or theme.get('id')}: 상태 {korean_status(theme.get('status'))}")
    else:
        lines.append("- 운영 동적 테마: 현재 선정 없음")

    research_themes = dynamic.get("themeAnalysis") or []
    lines.extend(["", "### 연구용 테마 자료(운영 선정 아님)", ""])
    theme_source = report.get("sources", {}).get("themeSignals", {})
    lines.append(
        f"- 자료 상태: {korean_status(theme_source.get('status') or theme_source.get('sourceStatus'))}; "
        "운영 Universe·관심종목 선정에 직접 사용하지 않습니다."
    )
    if research_themes:
        lines.append(f"- 연구용 테마 수: {len(research_themes)}개")
        for theme in research_themes[:10]:
            lines.append(
                f"- {theme.get('name') or theme.get('id') or '이름 미확인'}: "
                f"회원 {theme.get('memberCount', '확인 불가')}종목, "
                f"커버리지 {theme.get('coveragePct', '확인 불가')}%, "
                f"회원 상태 {korean_status(theme.get('membershipStatus'))}"
            )
    else:
        lines.append("- 연구용 테마 자료: 확인 가능한 항목 없음")

    comparison = report.get("v1V2Analysis") or {}
    v1 = comparison.get("v1") or {}
    v2 = comparison.get("v2Shadow") or {}
    lines.extend(["", "<details>", "<summary>V1 운영 선정과 V2 Shadow 판정 근거</summary>", ""])
    lines.append("- V1: 운영 선정 결과입니다. V2 결과와 섞지 않습니다.")
    lines.append("- V2: 실험적 주도성 분석이며 관심종목·매매 판단을 변경하지 않습니다.")
    lines.append(f"- V1 업종: {v1.get('sectors', {}).get('total', 0)}개, 상태 {v1.get('sectors', {}).get('statusCounts', {})}")
    lines.append(f"- V1 테마: {v1.get('themes', {}).get('total', 0)}개, 상태 {v1.get('themes', {}).get('statusCounts', {})}")
    lines.append(f"- V2 업종: {v2.get('industries', {}).get('total', 0)}개, 상태 {v2.get('industries', {}).get('statusCounts', {})}")
    lines.append(f"- V2 테마: {v2.get('themes', {}).get('total', 0)}개, 상태 {v2.get('themes', {}).get('statusCounts', {})}")
    lines.append(f"- V2 관측 거래일: {', '.join(v2.get('history', {}).get('uniqueTradingDates', [])) or '확인 불가'}; 지속성은 자료 부족 시 WAIT/PARTIAL")
    lines.append("- V2 판정 근거: 상대강도·상승 확산·MA20·지속성의 실제값과 각 항목 상태를 원본 그대로 표시합니다.")
    lines.append("- V2 세부 근거는 생성된 JSON의 `v1V2Analysis.v2Shadow.*.rows`에서 확인할 수 있습니다.")
    lines.extend(["", "#### V2 ACTIVE·WATCH 실명·점수·근거", ""])
    for kind, label in (("industries", "업종"), ("themes", "테마")):
        selected = [
            row for row in (v2.get(kind, {}).get("rows") or [])
            if row.get("v2Status") in ("ACTIVE", "WATCH")
        ]
        lines.append(f"- {label}: {len(selected)}개")
        for row in selected:
            criteria = row.get("criteria") or {}
            observed_days = (criteria.get("persistence") or {}).get("observedDays", 0)
            reason = ", ".join(row.get("reason") or []) or "선정 근거 확인 불가"
            score = row.get("score") if isinstance(row.get("score"), (int, float)) else "확인 불가"
            lines.append(
                f"  - {row.get('name') or row.get('id') or '이름 미확인'}: "
                f"{row.get('v2Status')}, 점수 {score}, 지속성 관측 {observed_days}거래일, 근거 {reason}"
            )
    lines.append("</details>")

    sources = comparison.get("observationSources") or {}
    lines.extend(["", "### 관찰 종목 출처", "", f"- 게시 주도주: {sources.get('postedLeaders', 0)}개"])
    if sources.get("uiObservationCount") is None:
        lines.append(f"- UI 관찰 종목 차이: {sources.get('unresolvedDifference')}")

    lines.extend([
        "",
        "### 관심종목 등락 상위",
        "",
        f"- 분석 범위: {monitored_summary.get('scope') or '확인 불가'}; 전체시장 순위가 아닙니다.",
    ])

    for row in (monitored_summary.get("topGainers") or report["marketSummary"].get("topGainers", []))[:5]:
        lines.append(
            f"- {row['itemName']} ({row['itemCode']}): "
            f"{row['changeRate']:+.2f}%"
        )

    lines.extend(
        [
            "",
            "### 관심종목 등락 하위",
            "",
        ]
    )

    for row in (monitored_summary.get("topDecliners") or report["marketSummary"].get("topDecliners", []))[:5]:
        lines.append(
            f"- {row['itemName']} ({row['itemCode']}): "
            f"{row['changeRate']:+.2f}%"
        )

    if historical_source == "KRX":
        krx_stock_rows = [
            row for row in (report.get("stocks") or [])
            if isinstance(row.get("technical"), dict)
            and (row.get("technical", {}).get("ma20") is not None
                 or row.get("technical", {}).get("ma60") is not None)
        ]
        if krx_stock_rows:
            lines.extend(["", "### KRX 확정 기술지표", ""])
            for row in krx_stock_rows:
                technical = row.get("technical") or {}
                lines.append(
                    f"- {row.get('itemName') or '이름 미확인'} ({row.get('itemCode') or '코드 미확인'}): "
                    f"MA20 {display_number(technical.get('ma20'), '원')}, "
                    f"MA60 {display_number(technical.get('ma60'), '원')}, "
                    f"기준일 {row.get('asOfDate') or '확인 불가'}"
                )

    full_market = report.get("fullMarket", {})
    krx_projection = (report.get("marketSummary") or {}).get("fullMarket") or {}
    krx_projection_available = (
        historical_source == "KRX"
        and isinstance(krx_projection.get("breadth"), dict)
        and krx_projection.get("asOfDate")
    )
    lines.extend(["", "## 1-A. Full-market evidence", ""])
    if full_market.get("status") != "AVAILABLE" and not krx_projection_available:
        lines.append(f"- 상태: UNAVAILABLE ({full_market.get('reason', 'UNKNOWN')})")
    else:
        if krx_projection_available and full_market.get("status") != "AVAILABLE":
            lines.append(
                "- 상태: KRX_M10_VERIFIED_GENERATION "
                "(외부 지수·현재 스냅샷 정합성 상태와 별도 표시)"
            )
            full_market = krx_projection
        lines.append(f"- 수집 품질: {full_market.get('qualityStatus', 'UNKNOWN')}")
        # KRX-only staging deliberately does not synthesize breadth/leader
        # aggregates that were not supplied by the verified generation.
        # Render unavailable values instead of failing the whole report.
        fb = full_market.get("breadth") or {}

        def full_market_metric(name):
            metric = fb.get(name)
            if not isinstance(metric, dict):
                return "확인 불가"
            count = metric.get("count", "확인 불가")
            pct = metric.get("pct", "확인 불가")
            return f"{count} ({pct}%)"

        lines.extend([
            f"- 기준일: {full_market['asOfDate']}",
            f"- 분석 대상/기준 Universe: {full_market.get('quality', {}).get('currentCount', 'UNKNOWN')}/"
            f"{full_market.get('quality', {}).get('authoritativeCount', 'UNKNOWN')}종목",
            f"- 상승/하락/보합: {fb.get('advancers', '확인 불가')}/"
            f"{fb.get('decliners', '확인 불가')}/{fb.get('unchanged', '확인 불가')}",
            f"- MA20 위: {full_market_metric('aboveMA20')}",
            f"- MA60 위: {full_market_metric('aboveMA60')}",
            f"- MA120 위: {full_market_metric('aboveMA120')}",
            f"- 20일 돌파: {full_market_metric('breakout20')}",
            f"- 52주 고점 근접: {full_market_metric('near52wHigh')}",
            f"- 거래량 확산: {full_market_metric('volumeAbove20DayAverage')}",
            f"- Full-market leaders: {len(full_market['leaders'])}종목",
        ])
        delta = full_market.get("quality", {}).get("universeDelta")
        if delta:
            lines.append(
                f"- Universe 변동 확인: {delta.get('verificationStatus', 'UNKNOWN')} "
                f"(추가 {len(delta.get('added', []))}, 제외 {len(delta.get('removed', []))}, "
                f"시장변경 {len(delta.get('marketChanged', []))}, 명칭변경 {len(delta.get('nameChanged', []))})"
            )

    lines.extend(
        [
            "",
            "## 2. 관심종목 그룹 확산",
            "",
        ]
    )

    for group in report["groupSummary"]:
        lines.append(
            f"- [{group['groupType']}] {group['groupName']}: "
            f"상승 {group['upCount']}, 하락 {group['downCount']}, "
            f"20일 돌파확인 {group['breakout20ConfirmedCount']}, "
            f"확산 {group['diffusionState']}, 상태 {group['status']}"
        )

    lines.extend(["", "## 3. 관심종목 정규장 확정 기술 이벤트", ""])
    for label, key in (
        ("20일 돌파 확인", "breakout20Confirmed"),
        ("20일 돌파 시도", "breakout20Attempt"),
        ("20일 돌파 실패", "breakout20Failed"),
        ("60일 돌파 확인", "breakout60Confirmed"),
        ("60일 돌파 시도", "breakout60Attempt"),
        ("60일 돌파 실패", "breakout60Failed"),
        ("거래량 급증", "volumeSurge"),
        ("거래량 증가", "volumeElevated"),
        ("근접 돌파", "nearBreakout"),
        ("눌림목", "pullback"),
    ):
        stocks = report["technicalEventsRegularSession"][key]
        names = ", ".join(
            f"{stock['itemName']}({stock['itemCode']})" for stock in stocks[:10]
        )
        lines.append(f"- {label}: {len(stocks)}종목" + (f" — {names}" if names else ""))

    lines.extend([
        "",
        "## 4. 리서치 변화",
        "",
        "- 리서치 상태: " + ", ".join(
            f"{key} {value}" for key, value in report["researchSummary"]["statusCounts"].items()
        ),
        f"- 최근 커버리지 활성: {report['researchSummary']['recentCoverageActiveCount']}종목",
        f"- 목표가 수정 관측: {report['researchSummary']['targetRevisionObservedCount']}종목",
        f"- 상승 주제 관측: {report['researchSummary']['risingTopicObservedCount']}종목",
        "",
        "### 최근 리서치 표본이 충분한 종목",
        "",
    ])

    for row in report["researchSummary"]["activeStocks"]:
        change = row.get("targetMeanChangePct")
        change_text = (
            f"{change:+.2f}%"
            if isinstance(change, (int, float))
            else "N/A"
        )
        topics = ", ".join(
            topic.get("topic", "")
            for topic in row.get("risingTopics", [])[:3]
        )
        lines.append(
            f"- {row['itemName']} ({row['itemCode']}): "
            f"최근 {row['recentReportCount']}건, "
            f"목표가 평균 변화 {change_text}, "
            f"주요 상승 주제 {topics or '없음'}"
        )

    lines.extend(
        [
            "",
            "## 5. 시장 × 리서치",
            "",
        ]
    )
    for bucket, detail in report["marketResearchCross"].items():
        lines.append(f"- {bucket}: {detail['count']}종목")

    kiwoom = report.get("kiwoomPreviousCandidates", {})
    lines.extend(["", "## 🟪 키움 전일후보군 — 당일 추적", ""])
    lines.append(f"- 상태: {kiwoom.get('status', 'UNAVAILABLE')}")
    if kiwoom.get("reason"):
        lines.append(f"- 사유: {kiwoom['reason']}")
    if kiwoom.get("status") in ("AVAILABLE", "AVAILABLE_EMPTY"):
        lines.extend([
            f"- 감시 거래일: {kiwoom.get('monitoringDate')}",
            f"- 동기화 시각: {kiwoom.get('syncedAt')}",
            f"- 총 감시종목: {kiwoom.get('candidateCount', 0)}종목",
        ])
        for row in kiwoom.get("stocks", []):
            rate = row.get("changeRate")
            rate_text = f"{rate:+.2f}%" if isinstance(rate, (int, float)) else "UNAVAILABLE"
            lines.append(
                f"- {row.get('itemName') or '이름 미확인'} ({row['itemCode']}): "
                f"등락률 {rate_text}, 상태 {row['dataStatus']}, "
                f"돌파 {row.get('breakout20') if row.get('breakout20') is not None else 'UNAVAILABLE'}"
            )

    lines.extend([
        "",
        "## 6. 데이터 품질",
        "",
        f"- 보고서 상태: {report['status']}",
        f"- 시장 커버리지 상태: {report['coverageStatus']}",
        f"- Quotes fresh: {report['sources']['quotes']['fresh']}",
        f"- Quotes status: {report['sources']['quotes']['status']}",
        f"- Quotes sourceTime: {report['sources']['quotes']['sourceTime']}",
        f"- Quotes generatedAt: {report['sources']['quotes']['generatedAt']}",
        f"- Research asOf: {report['sources']['researchIntelligence']['asOf']}",
        "- Research statusCounts: " + ", ".join(
            f"{key} {value}"
            for key, value in report["researchSummary"]["statusCounts"].items()
        ),
        f"- 누락 시장 데이터: {report['coverage']['missingMarketCount']}종목",
    ])
    missing_names = [
        row["itemName"] for row in report["coverage"]["missingMarketStocks"]
    ]
    if missing_names:
        lines.append("- 누락 종목: " + ", ".join(missing_names))
    lines.extend([
        "",
        "## 주의",
        "",
        "이 리포트는 시장 및 리서치 데이터의 기술적·서술적 요약이며 매수·매도 추천을 생성하지 않습니다.",
        "",
    ])

    Path(path).write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def write_work_markdown(report, path):
    """Write the fixed three-part Work Markdown view from the report contract."""
    Path(path).write_text(render_dashboard_markdown(report), encoding="utf-8")


def write_work_html(report, path):
    """Write the approved A v1.1 HTML view, or fail closed if unavailable."""
    Path(path).write_text(render_dashboard_html(report), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of")
    parser.add_argument(
        "--fetch-discovery-missing",
        action="store_true",
        help="fetch only Kiwoom candidates absent from existing report evidence",
    )
    parser.add_argument(
        "--output-dir",
        default="data/reports",
    )
    parser.add_argument(
        "--staging-input-dir",
        help=(
            "opt in to strict staged full-market technicals/states input; "
            "default production paths are not used for technical fallback"
        ),
    )
    args = parser.parse_args()

    report = build_report(
        args.as_of,
        fetch_discovery_missing=args.fetch_discovery_missing,
        staging_input_dir=args.staging_input_dir,
    )

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    contract = report.get("reportContract") if isinstance(report.get("reportContract"), Mapping) else report
    html_status = "TEMPLATE_UNAVAILABLE"
    try:
        rendered = render_contract_views(contract, out_dir, allow_current_observation=True)
        html_status = "AVAILABLE"
    except TemplateUnavailable as exc:
        print(f"HTML_REPORT={exc}")

    print(
        json.dumps(
            {
                "status": report["status"],
                "coverageStatus": report["coverageStatus"],
                "asOf": report["asOf"],
                "enabledStockCount": report["coverage"][
                    "enabledStockCount"
                ],
                "rankingEligibleCount": report[
                    "researchSummary"
                ]["rankingEligibleCount"],
                "output": rendered.get("markdown") if html_status == "AVAILABLE" else str(out_dir / "latest.md"),
                "htmlStatus": html_status,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
