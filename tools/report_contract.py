"""Canonical data contract shared by report renderers.

The Work report is allowed to choose presentation, but it must not recompute
market values independently for Markdown and HTML.  This module extracts the
values from one published snapshot generation and provides a small comparison
guard for renderer output.
"""
from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from typing import Any


REQUIRED_RENDER_FIELDS = (
    "asOf",
    "observedAt",
    "fullMarket.breadth.advancers",
    "fullMarket.breadth.decliners",
    "fullMarket.breadth.unchanged",
    "fullMarket.metrics.breakout20.count",
    "fullMarket.marketTradingValue.KOSPI.value",
    "fullMarket.marketTradingValue.KOSDAQ.value",
    "v2.industries",
    "v2.themes",
    "candidates",
    "technicalCandidates",
    "kiwoomCandidates.symbols",
    "kiwoomCandidates.stocks",
)

TRADING_DATE_EVIDENCE_REASONS = {
    "VERIFIED_CANONICAL_SESSION",
    "VERIFIED_MONITORING_DAILY_FINAL_SESSION",
    "SOURCE_OBSERVED_MONITORING_DAILY_FINAL_DATE",
    "VERIFIED_NAVER_LOCAL_DATE_DIAGNOSTIC",
    "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT",
}

# GitHub Contents API does not return inline content for large blobs. Keep
# the Work-facing contract below that transport limit while preserving the
# verified group metrics and a pointer to the raw detail.
MAX_WORK_CONTRACT_BYTES = 900_000


def classify_contract_transport_response(response: Any) -> dict[str, Any]:
    """Distinguish an API inline-content limitation from an empty file."""
    if not isinstance(response, Mapping):
        return {"status": "INVALID_RESPONSE"}
    if response.get("encoding") == "none" and response.get("download_url"):
        return {
            "status": "INLINE_CONTENT_UNAVAILABLE",
            "fallbackUrl": response.get("download_url"),
            "size": response.get("size"),
        }
    content = response.get("content")
    if not content:
        return {"status": "EMPTY_CONTENT", "size": response.get("size")}
    if response.get("encoding") != "base64":
        return {"status": "UNSUPPORTED_ENCODING", "encoding": response.get("encoding")}
    try:
        payload = base64.b64decode(str(content), validate=False)
    except Exception:
        return {"status": "INVALID_BASE64"}
    return {
        "status": "CONTENT",
        "payload": payload,
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def validate_contract_payload(
    payload: bytes,
    *,
    expected_sha256: str | None = None,
    expected_as_of: str | None = None,
    expected_observed_at: str | None = None,
) -> dict[str, Any]:
    """Validate the bytes actually read from a published contract artifact."""
    if not payload:
        return {"status": "EMPTY_FILE"}
    digest = hashlib.sha256(payload).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        return {"status": "SHA_MISMATCH", "sha256": digest}
    try:
        contract = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"status": "INVALID_JSON", "sha256": digest}
    if not isinstance(contract, Mapping) or not contract.get("schemaVersion"):
        return {"status": "REQUIRED_FIELDS_MISSING", "sha256": digest}
    if expected_as_of is not None and contract.get("asOf") != expected_as_of:
        return {"status": "TRADING_DATE_MISMATCH", "sha256": digest}
    if expected_observed_at is not None and contract.get("observedAt") != expected_observed_at:
        return {"status": "OBSERVED_AT_MISMATCH", "sha256": digest}
    return {"status": "VALID", "sha256": digest, "contract": contract}



def _path(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _same_generation(payloads: Mapping[str, Mapping[str, Any]]) -> tuple[str | None, bool]:
    values = {
        str(payload.get("generatedAt"))
        for payload in payloads.values()
        if isinstance(payload, Mapping) and payload.get("generatedAt")
    }
    return (next(iter(values), None), len(values) <= 1)


def _valid_trading_date_evidence(
    evidence: Any, trading_date: Any, observed_at: str | None = None
) -> bool:
    """Require an explicit, matching evidence record before VERIFIED/HTML."""
    if not isinstance(evidence, Mapping) or not trading_date:
        return False
    if evidence.get("status") != "VERIFIED":
        return False
    if str(evidence.get("tradingDate") or "") != str(trading_date):
        return False
    reason = evidence.get("reason")
    if reason not in TRADING_DATE_EVIDENCE_REASONS:
        return False
    if reason == "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT":
        return (
            evidence.get("evidenceClass") in {"INTRADAY_SESSION", "POST_MARKET_SNAPSHOT"}
            and evidence.get("finalConfirmed") is False
            and isinstance(observed_at, str)
            and evidence.get("collectionGeneratedAt") == observed_at
        )
    return True


def _valid_code_name_rows(stocks: list[Any], symbols: list[Any], bindings: Any) -> bool:
    if not isinstance(bindings, Mapping):
        return False
    for row in stocks:
        if not isinstance(row, Mapping) or row.get("itemCode") not in symbols or not row.get("itemName"):
            return False
        expected_name = bindings.get(str(row.get("itemCode")))
        if not expected_name or row.get("itemName") != expected_name:
            return False
    return bool(stocks)


def _compact_theme_rows(rows: Any) -> list[dict[str, Any]]:
    """Keep theme metrics without duplicating every member quote row."""
    compact: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        item = dict(row)
        stocks = item.pop("stocks", None)
        if isinstance(stocks, list):
            item["stockDetailCount"] = len(stocks)
            item["stockDetailSource"] = (
                "data/monitoring/latest-theme-signals.json#themes[].stocks"
            )
        compact.append(item)
    return compact


def build_report_contract(
    *,
    indices: Mapping[str, Any],
    summary: Mapping[str, Any],
    breadth: Mapping[str, Any],
    quality: Mapping[str, Any],
    industries: Mapping[str, Any],
    themes: Mapping[str, Any],
    v2_latest: Mapping[str, Any],
    v2_history: Mapping[str, Any],
    leaders: Mapping[str, Any],
    candidates: list[Mapping[str, Any]] | None = None,
    technical_candidates: list[Mapping[str, Any]] | None = None,
    kiwoom_candidates: Mapping[str, Any] | None = None,
    research: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one renderer-neutral model from one published generation.

    No value is inferred from a different generation.  When the source files
    disagree on ``generatedAt`` the contract is PARTIAL and keeps the raw
    evidence out of the renderable numeric sections.
    """
    generation, aligned = _same_generation({
        "summary": summary,
        "breadth": breadth,
        "quality": quality,
        "industries": industries,
        "themes": themes,
    })
    full_market = summary.get("fullMarket") if isinstance(summary, Mapping) else {}
    full_market = full_market if isinstance(full_market, Mapping) else {}
    total = (full_market.get("markets") or {}).get("TOTAL") or {}
    market_values = breadth.get("fullMarket", {}).get("marketTradingValue", {})
    evidence = full_market.get("tradingDateEvidence") or quality.get("tradingDateEvidence") or {}
    trading_date = full_market.get("tradingDate") or quality.get("tradingDate")
    source_quality = (
        quality.get("collectionQualityStatus")
        or quality.get("sourceQualityStatus")
        or full_market.get("qualityStatus")
        or "UNAVAILABLE"
    )
    baseline_status = (
        quality.get("baselineFreshnessStatus")
        or summary.get("baselineFreshnessStatus")
        or full_market.get("baselineFreshnessStatus")
    )
    baseline_as_of = (
        quality.get("baselineAsOfDate")
        or summary.get("baselineAsOfDate")
        or full_market.get("baselineAsOfDate")
    )
    expected_previous = (
        quality.get("expectedPreviousTradingDate")
        or summary.get("expectedPreviousTradingDate")
        or full_market.get("expectedPreviousTradingDate")
    )

    def metric(name: str, count: str, eligible: str, pct: str) -> dict[str, Any]:
        return {
            "count": total.get(count),
            "eligibleCount": total.get(eligible),
            "pct": total.get(pct),
        }

    def selected(rows: Any) -> list[dict[str, Any]]:
        result = []
        for row in rows or []:
            if row.get("v2Status") not in {"ACTIVE", "WATCH"}:
                continue
            item = dict(row)
            item["leaders"] = row.get("leaders") or []
            item["leaderEvidenceStatus"] = (
                "AVAILABLE" if row.get("leaders") else "UNAVAILABLE_SAME_GENERATION_LEADER_DATA"
            )
            result.append(item)
        return result

    v2_industries = selected(v2_latest.get("industries"))
    v2_themes = selected(v2_latest.get("themes"))
    observations = v2_history.get("observations") or []
    unique_dates = sorted({str(row.get("tradingDate")) for row in observations if row.get("tradingDate")})

    # ``candidates`` is retained as a compatibility alias for older callers.
    # New callers must provide technical and Kiwoom candidates separately.
    technical_candidates = (
        technical_candidates if technical_candidates is not None else candidates or []
    )
    kiwoom_candidates = kiwoom_candidates if isinstance(kiwoom_candidates, Mapping) else {}
    kiwoom_symbols = list(kiwoom_candidates.get("symbols") or [])
    kiwoom_stocks = list(kiwoom_candidates.get("stocks") or [])
    kiwoom_codes = [row.get("itemCode") for row in kiwoom_stocks if isinstance(row, Mapping)]
    kiwoom_name_map = {
        str(row.get("itemCode")): row.get("itemName")
        for row in kiwoom_stocks
        if isinstance(row, Mapping) and row.get("itemCode")
    }
    source_validation = kiwoom_candidates.get("validation")
    name_evidence_valid = (
        isinstance(source_validation, Mapping)
        and source_validation.get("status") == "VALID"
        and source_validation.get("calendarSource") == "KRX_OFFICIAL"
        and source_validation.get("immutable") is True
        and _valid_code_name_rows(kiwoom_stocks, kiwoom_symbols, source_validation.get("codeNameBindings"))
    )
    kiwoom_name_status = "VERIFIED" if name_evidence_valid else "PARTIAL" if kiwoom_stocks else "UNAVAILABLE"
    trading_date_evidence_valid = _valid_trading_date_evidence(
        evidence, trading_date, generation
    )
    trading_date_status = (
        "VERIFIED" if trading_date_evidence_valid
        else "UNAVAILABLE_TRADING_DATE_NULL" if not trading_date
        else "UNAVAILABLE_TRADING_DATE_EVIDENCE_INVALID"
    )
    contract_status = (
        "PARTIAL"
        if baseline_status == "BASELINE_STALE" or not trading_date or not trading_date_evidence_valid
        else "AVAILABLE" if aligned and full_market else "PARTIAL"
    )

    contract: dict[str, Any] = {
        "schemaVersion": "market-report-contract-v1",
        "status": contract_status,
        "asOf": trading_date,
        "observedAt": generation,
        "tradingDateStatus": trading_date_status,
        "sourceQualityStatus": source_quality,
        "tradingDateEvidence": evidence,
        "sourceGenerations": {
            "market": generation,
            "indices": indices.get("generatedAt"),
            "v2": v2_latest.get("generatedAt"),
            "kiwoom": kiwoom_candidates.get("syncedAt"),
        },
        "sourceBindings": {
            "kiwoomCandidates": "data/trading-discovery/latest.json",
            "technicalCandidates": "data/reports/latest.json#nextSessionCandidates",
            "themeStockDetails": "data/monitoring/latest-theme-signals.json#themes[].stocks",
            "forbidCandidateMixing": True,
            "historicalAndPreMarketMustRemainSeparate": True,
        },
        "indices": indices.get("indices") or [],
        "fullMarket": {
            "totalCount": full_market.get("authoritativeCount"),
            "breadth": {
                "advancers": total.get("advancers"),
                "decliners": total.get("decliners"),
                "unchanged": total.get("unchanged"),
            },
            "metrics": {
                "aboveMA20": metric("aboveMA20", "aboveMA20Count", "aboveMA20EligibleCount", "pctAboveMA20"),
                "aboveMA60": metric("aboveMA60", "aboveMA60Count", "aboveMA60EligibleCount", "pctAboveMA60"),
                "aboveMA120": metric("aboveMA120", "aboveMA120Count", "aboveMA120EligibleCount", "pctAboveMA120"),
                "breakout20": metric("breakout20", "breakout20Count", "breakout20EligibleCount", "pctBreakout20"),
                "breakout60": metric("breakout60", "breakout60Count", "breakout60EligibleCount", "pctBreakout60"),
                "volumeAbove20DayAverage": metric("volumeAbove20DayAverage", "volumeAbove20DayAverageCount", "volumeAbove20DayAverageEligibleCount", "pctVolumeAbove20DayAverage"),
            },
            "marketTradingValue": market_values,
        },
        "industries": industries.get("industries") or [],
        "themes": _compact_theme_rows(themes.get("themes") or []),
        "leaders": leaders.get("fullMarketLeaders") or leaders.get("candidates") or [],
        "v2": {
            "tradingDate": v2_latest.get("tradingDate") or v2_latest.get("sourceTradingDate") or trading_date,
            "sourceQualityStatus": v2_latest.get("inputEvidence", {}).get("sourceQualityStatus") if isinstance(v2_latest.get("inputEvidence"), Mapping) else None,
            "sourceCommit": v2_latest.get("inputEvidence", {}).get("sourceCommit") if isinstance(v2_latest.get("inputEvidence"), Mapping) else None,
            "history": {"recordCount": len(observations), "uniqueTradingDates": unique_dates},
            "industries": v2_industries,
            "themes": v2_themes,
        },
        "independentAnalysis": {
            "research": {
                "status": (research or {}).get("status") if isinstance(research, Mapping) else "UNAVAILABLE",
                "asOf": (research or {}).get("asOf") if isinstance(research, Mapping) else None,
            },
            "investorFlow": {
                "status": "UNAVAILABLE",
                "reason": "이 계약 입력에 동일 기준시각의 종목별 수급 판정이 없습니다.",
            },
            "disclosures": {
                "status": "UNAVAILABLE",
                "reason": "이 계약 입력에 동일 기준시각의 공시 원천이 없습니다.",
            },
        },
        # Keep the legacy key as the technical-candidate alias.  Renderers
        # must use the explicit keys below for source-sensitive sections.
        "candidates": technical_candidates,
        "technicalCandidates": technical_candidates,
        "kiwoomCandidates": {
            "source": "data/trading-discovery/latest.json",
            "sourceType": "KIWOOM_CONDITION_SEARCH",
            "selectionDate": kiwoom_candidates.get("selectionDate"),
            "monitoringDate": kiwoom_candidates.get("monitoringDate"),
            "sourceSha256": kiwoom_candidates.get("sourceSha256"),
            "symbols": kiwoom_symbols,
            "stocks": kiwoom_stocks,
            "codeNameMap": kiwoom_name_map,
            "codeNameStatus": kiwoom_name_status,
            "sourceValidation": source_validation if isinstance(source_validation, Mapping) else None,
            "sourceCandidateCount": kiwoom_candidates.get("candidateCount"),
        },
        "rendering": {
            "htmlAllowed": bool(
                contract_status == "AVAILABLE"
                and trading_date
                and trading_date_evidence_valid
            ),
            "reason": (
                "CONTRACT_VERIFIED"
                if contract_status == "AVAILABLE" and trading_date
                else "CONTRACT_UNAVAILABLE_OR_TRADING_DATE_NULL"
            ),
        },
    }
    if baseline_status == "BASELINE_STALE":
        contract["baseline"] = {
            "asOfDate": baseline_as_of,
            "expectedPreviousTradingDate": expected_previous,
            "freshnessStatus": baseline_status,
            "dateMismatch": True,
            "derivedMetricsStatus": "UNAVAILABLE",
            "derivedMetricsReason": "BASELINE_STALE",
        }
    return contract


def validate_kiwoom_candidate_binding(
    discovery: Mapping[str, Any], contract: Mapping[str, Any]
) -> list[str]:
    """Reject candidate/source drift before a report can be rendered.

    The immutable Kiwoom snapshot is the only authority for the Kiwoom section.
    General technical candidates are intentionally not accepted here.
    """
    errors: list[str] = []
    if discovery.get("status") not in {"AVAILABLE", "AVAILABLE_EMPTY"}:
        return errors
    expected = list(discovery.get("symbols") or [])
    section = contract.get("kiwoomCandidates") if isinstance(contract, Mapping) else None
    section = section if isinstance(section, Mapping) else {}
    actual = list(section.get("symbols") or [])
    stocks = list(section.get("stocks") or [])
    codes = [row.get("itemCode") for row in stocks if isinstance(row, Mapping)]
    if actual != expected:
        errors.append("KIWOOM_SYMBOLS_MISMATCH")
    if codes != expected:
        errors.append("KIWOOM_STOCK_CODES_MISMATCH")
    if section.get("sourceSha256") != discovery.get("sourceSha256"):
        errors.append("KIWOOM_SOURCE_HASH_MISMATCH")
    if section.get("selectionDate") != discovery.get("selectionDate"):
        errors.append("KIWOOM_SELECTION_DATE_MISMATCH")
    if section.get("monitoringDate") != discovery.get("monitoringDate"):
        errors.append("KIWOOM_MONITORING_DATE_MISMATCH")
    if section.get("sourceCandidateCount") != len(expected):
        errors.append("KIWOOM_SOURCE_COUNT_MISMATCH")
    for row in stocks:
        if not isinstance(row, Mapping) or not row.get("itemCode"):
            errors.append("KIWOOM_CODE_NAME_MAPPING_INVALID")
        elif isinstance((discovery.get("validation") if isinstance(discovery, Mapping) else None), Mapping) and isinstance(discovery.get("validation", {}).get("codeNameBindings"), Mapping) and discovery["validation"]["codeNameBindings"].get(str(row.get("itemCode"))) != row.get("itemName"):
            errors.append("KIWOOM_CODE_NAME_MISMATCH")
    if len(set(codes)) != len(codes):
        errors.append("KIWOOM_DUPLICATE_STOCK_CODE")
    return errors


def validate_rendered_models(markdown_model: Mapping[str, Any], html_model: Mapping[str, Any]) -> list[str]:
    """Return exact contract paths whose rendered values differ."""
    mismatches = []
    for path in REQUIRED_RENDER_FIELDS:
        left = _path(markdown_model, path)
        right = _path(html_model, path)
        if left != right:
            mismatches.append(path)
    return mismatches
