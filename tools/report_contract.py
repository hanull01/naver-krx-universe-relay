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

from tools.report_lineage import contract_id, lineage_from_contract
from tools.report_user_status import availability, first_reason


REQUIRED_RENDER_FIELDS = (
    "asOf",
    "observedAt",
    "currentMarketObservation.breadth.advancers",
    "currentMarketObservation.breadth.decliners",
    "currentMarketObservation.breadth.unchanged",
    "historicalTechnicalMarket.metrics.aboveMA20.count",
    "historicalTechnicalMarket.metrics.aboveMA60.count",
    "historicalTechnicalMarket.metrics.aboveMA120.count",
    "fullMarket.breadth.advancers",
    "fullMarket.breadth.decliners",
    "fullMarket.breadth.unchanged",
    "fullMarket.metrics.breakout20.count",
    "v2.industries",
    "v2.themes",
    "candidates",
    "technicalCandidates",
    "kiwoomCandidates.symbols",
    "kiwoomCandidates.stocks",
)

TRADING_DATE_EVIDENCE_REASONS = {
    "VERIFIED_CANONICAL_SESSION",
    "VERIFIED_NAVER_LOCAL_DATE_DIAGNOSTIC",
    "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT",
}

# GitHub Contents API does not return inline content for large blobs. Keep
# the Work-facing contract below that transport limit while preserving the
# verified group metrics and a pointer to the raw detail.
MAX_WORK_CONTRACT_BYTES = 900_000


def compact_detail_reference(
    value: Any,
    *,
    source_path: str,
    source_sha256: str | None = None,
    preview_limit: int = 3,
) -> dict[str, Any]:
    """Represent large diagnostic/detail collections without copying them.

    The complete collection remains in the generation artifact named by
    ``sourcePath``.  The Work-facing contract carries only the count,
    availability, integrity metadata, and a tiny deterministic preview.
    Already compact references are preserved so this helper is safe at both
    the projection and final-contract boundaries.
    """
    if isinstance(value, Mapping) and "count" in value and "sourcePath" in value:
        result = dict(value)
        result.setdefault("available", bool(result.get("count")))
        result.setdefault("preview", [])
        return result
    if isinstance(value, Mapping):
        entries = list(value)
    elif isinstance(value, (list, tuple, set)):
        entries = list(value)
    elif value in (None, ""):
        entries = []
    else:
        entries = [value]
    return {
        "count": len(entries),
        "available": bool(entries),
        "sourcePath": source_path,
        "sourceSha256": source_sha256,
        "preview": entries[:preview_limit],
    }


def compact_registered_universe(
    registered: Mapping[str, Any] | None,
    *,
    source_path: str = "data/monitoring-derived/registered-universe-technicals.json",
) -> dict[str, Any]:
    """Keep registered-universe coverage while leaving member rows in artifacts."""
    source = dict(registered or {})
    items = source.get("items") if isinstance(source.get("items"), list) else []
    count = source.get("registeredCount", source.get("count", len(items)))
    observed = source.get("registeredObservedCount", len(items))
    missing = source.get("missingCodes")
    if not isinstance(missing, list):
        missing = []
    def compact_item(row: Mapping[str, Any]) -> dict[str, Any]:
        current = row.get("current") or row.get("market") or {}
        technicals = row.get("technicals") or row.get("technical") or row.get("metrics") or {}
        state = row.get("state") or row.get("currentState") or {}
        if not isinstance(current, Mapping):
            current = {}
        if not isinstance(technicals, Mapping):
            technicals = {}
        if not isinstance(state, Mapping):
            state = {}
        compact = {
            "itemCode": row.get("itemCode") or row.get("code"),
            "itemName": row.get("stockName") or row.get("itemName") or row.get("name"),
            "asOfDate": (
                row.get("asOfDate")
                or technicals.get("sourceDate")
                or technicals.get("asOfDate")
            ),
            "current": {
                key: current.get(key)
                for key in ("price", "currentPrice", "changeRate", "sourceTime", "status")
                if current.get(key) is not None
            },
            "technicals": {
                key: technicals.get(key)
                for key in ("ma20", "ma60", "ma120", "return20d", "volumeRatio20", "sourceDate", "status")
                if technicals.get(key) is not None
            },
            "state": {
                key: state.get(key)
                for key in ("trend", "trendStatus", "status", "priceVsMA20", "priceVsMA60")
                if state.get(key) is not None
            },
            "status": row.get("status") or "UNAVAILABLE",
        }
        current_value = current.get("price", current.get("currentPrice"))
        ma20_value = technicals.get("ma20")
        ma60_value = technicals.get("ma60")
        trend_value = state.get("trend") or state.get("trendStatus")
        compact["availability"] = {
            "current": availability(
                status=current.get("status") or row.get("status"),
                required_data="관심종목 현재가",
                received=current_value is not None,
                producer="NAVER 현재 관측 수집",
                reason=first_reason(current, "failureReason", "reason")
                or (None if current_value is not None else "CURRENT_SNAPSHOT_MISSING"),
                next_check_at=current.get("nextCheckAt"),
            ),
            "ma20": availability(
                status=technicals.get("status") or row.get("status"),
                required_data="20일 이동평균",
                received=ma20_value is not None,
                producer="KRX historical technical",
                reason=first_reason(technicals, "failureReason", "reason")
                or (None if ma20_value is not None else "TECHNICAL_DATA_MISSING"),
                next_check_at=technicals.get("nextCheckAt"),
            ),
            "ma60": availability(
                status=technicals.get("status") or row.get("status"),
                required_data="60일 이동평균",
                received=ma60_value is not None,
                producer="KRX historical technical",
                reason=first_reason(technicals, "failureReason", "reason")
                or (None if ma60_value is not None else "TECHNICAL_DATA_MISSING"),
                next_check_at=technicals.get("nextCheckAt"),
            ),
            "trend": availability(
                status=state.get("status") or row.get("status"),
                required_data="현재가와 이동평균 기반 추세",
                received=trend_value is not None,
                producer="리포트 추세 계산",
                reason=first_reason(state, "failureReason", "reason")
                or (None if trend_value is not None else "TREND_DATA_MISSING"),
                next_check_at=state.get("nextCheckAt"),
            ),
        }
        # Preserve the compact metrics shape for older report readers while
        # carrying the current/staged fields needed by the registered card.
        if isinstance(row.get("metrics"), Mapping):
            compact["metrics"] = {
                key: row["metrics"].get(key)
                for key in ("ma20", "ma60", "ma120", "return20d", "volumeRatio20")
                if row["metrics"].get(key) is not None
            }
        return compact

    result = {
        "status": source.get("status") or ("AVAILABLE" if count else "AVAILABLE_EMPTY"),
        "scope": source.get("scope") or "REGISTERED_UNIVERSE",
        "registeredCount": count,
        "observedCount": observed,
        "count": count,
        "fallbackUsed": bool(source.get("fallbackUsed", False)),
        "missing": compact_detail_reference(
            missing,
            source_path=f"{source_path}#missingCodes",
            source_sha256=source.get("sourceSha256") or source.get("detailSha256"),
        ),
        "detailSource": source_path,
        "detailSha256": source.get("sourceSha256") or source.get("detailSha256"),
        "preview": [compact_item(row) for row in items[:3] if isinstance(row, Mapping)],
    }
    return result


def compact_candidate_rows(rows: list[Mapping[str, Any]] | None, limit: int = 100) -> list[dict[str, Any]]:
    """Keep the report-facing candidate summary bounded and deterministic."""
    compact: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        item = {
            key: row.get(key)
            for key in (
                "rank", "itemCode", "itemName", "primaryBucket", "allBuckets",
                "candidateAsOf", "candidateDate", "regularClose", "breakoutStrength",
                "volumeState", "state", "status",
            )
            if row.get(key) not in (None, "", [])
        }
        if isinstance(row.get("regular"), Mapping):
            item["regular"] = {
                key: row["regular"].get(key)
                for key in ("close", "changeRate", "asOfDate", "availabilityStatus")
                if row["regular"].get(key) not in (None, "")
            }
        compact.append(item)
        if len(compact) >= limit:
            break
    return compact


def compact_kiwoom_candidate_rows(
    rows: list[Mapping[str, Any]] | None,
    candidate_details: list[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Project Kiwoom rows without allowing KRX market metrics to leak in.

    ``candidateDetails.technicalIndicators`` is the only authority for a
    Kiwoom candidate's technical values.  Historical KRX fields that may be
    present in an older producer row are intentionally discarded.
    """
    details_by_code = {
        str(detail.get("symbol")): detail
        for detail in candidate_details or []
        if isinstance(detail, Mapping) and detail.get("symbol")
    }
    projected: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        code = str(row.get("itemCode") or row.get("symbol") or "")
        detail = details_by_code.get(code)
        if not isinstance(detail, Mapping):
            detail = row.get("candidateDetail") if isinstance(row.get("candidateDetail"), Mapping) else {}
        technical = detail.get("technicalIndicators")
        technical = dict(technical) if isinstance(technical, Mapping) else {}
        conditions = detail.get("conditionDetails")
        conditions = dict(conditions) if isinstance(conditions, Mapping) else {}
        failures = detail.get("failureReason") or row.get("candidateFailureReason") or []
        if isinstance(failures, str):
            failures = [failures]
        failures = list(dict.fromkeys(failures))
        if not detail:
            failures.append("CANDIDATE_DETAILS_UNAVAILABLE")
        if detail.get("selectionValue") in (None, "", "UNAVAILABLE"):
            failures.append("SELECTION_VALUE_UNAVAILABLE")
        if not technical:
            failures.append("TECHNICAL_INDICATORS_UNAVAILABLE")
        if not conditions:
            failures.append("CONDITION_DETAILS_UNAVAILABLE")
        failures = list(dict.fromkeys(failures))
        parts = sum((
            detail.get("selectionValue") not in (None, "", "UNAVAILABLE"),
            detail.get("currentPrice") is not None or row.get("price") is not None,
            bool(technical),
            bool(conditions),
        ))
        status = "AVAILABLE" if parts == 4 else "PARTIAL" if parts else "UNAVAILABLE"
        item = {
            key: row.get(key)
            for key in (
                "rank", "itemCode", "itemName", "itemNameSource", "price", "changeRate",
                "volume", "tradingValue", "priceStatus", "status", "dataStatus",
                "analysisStatus", "nextCheck", "conditionName", "conditionSequence",
                "conditionResult", "conditionDiscoveredAt", "conditionSourceValues",
                "conditionSourceValueStatus",
            )
            if row.get(key) not in (None, "", [])
        }
        item["itemCode"] = code
        item["candidateSelectionValue"] = detail.get("selectionValue")
        item["candidatePreviousClose"] = detail.get("previousClose")
        item["candidateTechnicalIndicators"] = technical
        item["candidateConditionDetails"] = conditions
        item["candidateSelectionStatus"] = detail.get("selectionStatus") or "UNAVAILABLE"
        item["candidateQuoteStatus"] = detail.get("quoteStatus") or "UNAVAILABLE"
        item["candidateDetailStatus"] = detail.get("detailStatus") or status
        item["candidateFailureReason"] = failures
        item["candidateSource"] = detail.get("source")
        item["candidateGeneratedAt"] = detail.get("generatedAt")
        item["candidateSourceHash"] = detail.get("sourceHash")
        item["candidatePaperOnly"] = detail.get("paper_only")
        item["technicalStatus"] = "AVAILABLE" if technical else "UNAVAILABLE"
        item["dataStatus"] = status
        item["status"] = status
        return_item_price = detail.get("currentPrice")
        if return_item_price is not None:
            item["price"] = return_item_price
        projected.append(item)
    return projected


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
    expected_contract_id = contract_id(contract)
    if contract.get("contractId") not in (None, expected_contract_id):
        return {"status": "CONTRACT_ID_MISMATCH", "sha256": digest, "expectedContractId": expected_contract_id}
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


def _historical_lineage(
    quality: Mapping[str, Any], full_market: Mapping[str, Any]
) -> dict[str, Any]:
    """Return KRX lineage and coverage metadata without using legacy history.

    The KRX receipt/resolution is authoritative for this metadata.  A missing
    or unverified receipt is intentionally represented as unavailable rather
    than being inferred from deprecated NAVER canonical coverage.
    """
    source = quality.get("historicalIndicatorSource") or full_market.get("historicalIndicatorSource")
    source = str(source or "UNAVAILABLE")
    is_krx = source == "KRX"
    instrument_count = quality.get("historicalInstrumentCount")
    if instrument_count is None:
        instrument_count = quality.get("instrumentCount")
    entity_count = quality.get("historicalEntityCount")
    if entity_count is None:
        entity_count = quality.get("entityCount")
    observed_count = quality.get("historicalObservedInstrumentCount")
    if observed_count is None:
        observed_count = quality.get("currentCount")
    detail_sha = (
        quality.get("detailSha256")
        or quality.get("sourceSha256")
        or quality.get("historicalDetailSha256")
        or quality.get("manifestSha256")
        or quality.get("monitoringSha256")
    )
    missing_history = quality.get("missingHistoryCodes")
    if not isinstance(missing_history, list):
        missing_history = quality.get("missingCodes") if is_krx else []
    missing_history = list(missing_history) if isinstance(missing_history, list) else []
    regular_unavailable = quality.get("regularSessionUnavailableCodes")
    current_missing = quality.get("currentSnapshotMissingCodes")
    regular_unavailable = list(regular_unavailable) if isinstance(regular_unavailable, list) else []
    current_missing = list(current_missing) if isinstance(current_missing, list) else []
    coverage = quality.get("historyCoveragePct")
    # KRX generation counts are authoritative. Do not preserve a stale
    # coverage value inherited from the deprecated NAVER canonical path.
    if is_krx and isinstance(instrument_count, int) and instrument_count > 0:
        if isinstance(observed_count, int):
            coverage = round(observed_count * 100 / instrument_count, 6)
    receipt_status = str(
        quality.get("historicalReceiptStatus")
        or quality.get("receiptStatus")
        or "UNAVAILABLE"
    ).upper()
    lineage_status = str(
        quality.get("historicalLineageStatus")
        or ("VERIFIED" if receipt_status == "VERIFIED" and is_krx else "UNAVAILABLE" if is_krx else "NOT_APPLICABLE")
    ).upper()
    return {
        "source": "KRX" if is_krx else source,
        "generationId": quality.get("historicalGenerationId") or quality.get("sourceGeneration"),
        "targetDate": quality.get("historicalTargetDate") or quality.get("tradingDate") or full_market.get("tradingDate"),
        "fallbackAllowed": False if is_krx else quality.get("historicalFallbackAllowed", False),
        "receiptStatus": receipt_status,
        "lineageStatus": lineage_status,
        "entityCount": entity_count,
        "instrumentCount": instrument_count,
        "observedInstrumentCount": observed_count,
        "historyCoveragePct": coverage,
        "missingHistoryCodes": compact_detail_reference(
            missing_history,
            source_path="data/monitoring/latest-quality.json#missingHistoryCodes",
            source_sha256=detail_sha,
        ),
        "regularSessionUnavailableCodes": compact_detail_reference(
            regular_unavailable,
            source_path="data/monitoring/latest-quality.json#regularSessionUnavailableCodes",
            source_sha256=detail_sha,
        ),
        "currentSnapshotMissingCodes": compact_detail_reference(
            current_missing,
            source_path="data/monitoring/latest-quality.json#currentSnapshotMissingCodes",
            source_sha256=detail_sha,
        ),
        "adjustmentStatus": quality.get("historicalAdjustmentStatus") or "UNAVAILABLE",
        "currentSnapshotSource": quality.get("currentSnapshotSource") or "NAVER_SEPARATE",
    }


def _current_market_observation(
    source: Mapping[str, Any] | None,
    quality: Mapping[str, Any],
) -> dict[str, Any]:
    """Build current NAVER breadth without a historical fallback."""
    source = source if isinstance(source, Mapping) else {}
    breadth_root = source.get("breadth") if isinstance(source.get("breadth"), Mapping) else {}
    total = breadth_root.get("TOTAL") if isinstance(breadth_root.get("TOTAL"), Mapping) else {}
    full_market = source.get("fullMarket") if isinstance(source.get("fullMarket"), Mapping) else {}
    total_count = total.get("stockCount") if total.get("stockCount") is not None else total.get("validCount")
    if total_count is None:
        total_count = full_market.get("authoritativeCount")
    values = {key: total.get(key) for key in ("advancers", "decliners", "unchanged")}
    complete = (
        isinstance(total_count, int) and not isinstance(total_count, bool) and total_count > 0
        and all(isinstance(value, int) and not isinstance(value, bool) for value in values.values())
        and sum(values.values()) == total_count
    )
    warnings = full_market.get("qualityWarnings")
    if not isinstance(warnings, list):
        warnings = []
    return {
        "status": "AVAILABLE" if complete else "UNAVAILABLE",
        "source": "NAVER_CURRENT_SNAPSHOT",
        "observedDate": quality.get("observedDate") or quality.get("currentSnapshotDate") or full_market.get("tradingDate"),
        "sourceTime": quality.get("currentSnapshotGeneratedAt") or source.get("generatedAt") or quality.get("generatedAt"),
        "scope": "STOCK_ONLY",
        "totalCount": total_count if complete else None,
        "breadth": values if complete else {"advancers": None, "decliners": None, "unchanged": None},
        "qualityStatus": full_market.get("qualityStatus") or quality.get("currentSnapshotQualityStatus") or "UNAVAILABLE",
        "qualityWarnings": warnings,
        "reason": None if complete else "CURRENT_BREADTH_UNAVAILABLE",
    }



_GROUP_SUMMARY_FIELDS = (
    "id", "name", "kind", "status", "v1Status", "v2Status",
    "membershipStatus", "memberCount", "coveragePct",
    "advancers", "decliners", "unchanged", "advancePct", "avgReturn1D",
    "averageReturn1D", "relativeStrengthVsMarket",
    "officialChangeRateRelativeStrength", "dataQuality", "qualityStatus",
    "source", "sourceDate", "observationDate", "scope", "artifactPath",
    "producerRunId", "producerStatus", "currentSnapshotStatus",
    "relativeStrengthStatus", "missingReason",
)


def _compact_group_leaders(rows: Any) -> list[dict[str, Any]]:
    compact = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        # Group rows only need the visible representative identity and the
        # current movement.  Historical/technical leader diagnostics remain
        # in the source artifact and are not duplicated into the contract.
        item = {
            key: row.get(key)
            for key in ("code", "name", "market", "currentPrice", "changeRate")
            if row.get(key) is not None
        }
        if row.get("itemCode") is not None and item.get("code") is None:
            item["code"] = row.get("itemCode")
        if row.get("itemName") is not None and item.get("name") is None:
            item["name"] = row.get("itemName")
        compact.append(item)
    return compact[:3]


def _compact_group_row(row: Any, kind: str) -> dict[str, Any]:
    """Keep report-facing group facts without copying source diagnostics."""
    if not isinstance(row, Mapping):
        return {}
    metrics = row.get("metrics") if kind == "theme" else row
    metrics = metrics if isinstance(metrics, Mapping) else {}
    item: dict[str, Any] = {}
    for field in _GROUP_SUMMARY_FIELDS:
        value = row.get(field)
        if value is None:
            value = metrics.get(field)
        if value is not None:
            item[field] = value
    if "averageReturn1D" in item and "avgReturn1D" not in item:
        item["avgReturn1D"] = item["averageReturn1D"]
    # Trading-value and persistence detail is not consumed by the canonical
    # HTML/Markdown projection.  Their source artifacts retain the full
    # diagnostics; the contract keeps the group counts and availability
    # lineage below instead of duplicating those arrays/objects.
    missing_codes = row.get("missingCodes")
    if isinstance(missing_codes, list):
        item["missingCodeCount"] = len(missing_codes)
    if kind == "theme" and isinstance(row.get("stocks"), list):
        item["stockDetailCount"] = len(row["stocks"])
        item["stockDetailSource"] = "data/monitoring/latest-theme-signals.json#themes[].stocks"
    leader_binding = _current_group_leader_binding(
        row,
        kind,
        row.get("observationDate") or row.get("sourceDate") or _group_observation_date(row),
    )
    leaders = row.get("leaders") or row.get("leaderCandidates") or leader_binding.get("candidates")
    if leaders:
        item["leaderCandidates"] = _compact_group_leaders(leaders)
    item["leaderEvidenceStatus"] = row.get("leaderEvidenceStatus") or leader_binding.get("status")
    item["leaderSource"] = row.get("leaderSource") or leader_binding.get("source")
    item["leaderObservationDate"] = row.get("leaderObservationDate") or leader_binding.get("observationDate")
    item["leaderScope"] = row.get("leaderScope") or leader_binding.get("scope")
    item["leaderMissingReason"] = row.get("leaderMissingReason") or leader_binding.get("missingReason")
    item["detailSource"] = (
        "data/monitoring/latest-industries.json#industries[]"
        if kind == "industry" else
        "data/monitoring/latest-theme-signals.json#themes[]"
    )
    status = row.get("status") or row.get("qualityStatus") or row.get("dataQuality")
    member_count = row.get("memberCount")
    missing_codes = row.get("missingCodes")
    member_count_valid = member_count is None or (
        isinstance(member_count, (int, float))
        and not isinstance(member_count, bool)
        and member_count > 0
    )
    received = (
        status not in {"UNAVAILABLE", "FAILED", "INVALID", "ERROR"}
        and member_count_valid
        and not (isinstance(missing_codes, list) and missing_codes)
    )
    default_reason = (
        "INDUSTRY_MEMBERSHIP_MISSING" if kind == "industry" else "THEME_MEMBERSHIP_MISSING"
    )
    if status in {"FAILED", "ERROR", "INVALID"}:
        default_reason = "PRODUCER_FAILED"
    elif member_count is None and not row.get("artifactPath"):
        default_reason = "INPUT_ARTIFACT_MISSING"
    elif kind == "theme" and row.get("membershipStatus") in {"MISSING", "UNAVAILABLE"}:
        default_reason = "THEME_MEMBERSHIP_MISSING"
    item["availability"] = availability(
        status=status,
        required_data=("업종 구성종목·현재 관측값·상대강도" if kind == "industry" else "테마 구성종목·현재 관측값·상대강도"),
        received=received,
        producer=("NAVER 업종 수집" if kind == "industry" else "NAVER 테마 수집"),
        reason=first_reason(
            row, "leaderMissingReason", "missingReason", "reason", "validationReason", "qualityReason"
        )
        or leader_binding.get("missingReason")
        or (default_reason if not received else None),
        next_check_at=row.get("nextCheckAt"),
        producer_run_id=row.get("producerRunId"),
        artifact_path=row.get("artifactPath") or item.get("detailSource"),
        freshness_status=row.get("freshnessStatus"),
        checked_at=row.get("checkedAt"),
        generated_at=row.get("generatedAt"),
        source_date=row.get("sourceDate"),
        observation_date=row.get("observationDate"),
        scope=row.get("scope"),
        input_count=row.get("inputCount") or row.get("memberCount"),
        matched_count=row.get("matchedCount") or row.get("featureCount"),
        output_count=row.get("outputCount") or (len(leaders) if leaders else 0),
        next_expected_at=row.get("nextExpectedAt"),
    )
    return item


def _compact_group_rows(rows: Any, kind: str) -> list[dict[str, Any]]:
    return [item for item in (_compact_group_row(row, kind) for row in rows or []) if item]


def _compact_v2_change(row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the V2 change facts used by the report and its lineage."""
    fields = (
        "kind", "id", "name", "officialChangeClass", "status", "v1Status",
        "v2Status", "reason", "source", "sourceDate", "observationDate", "scope",
    )
    return {key: row[key] for key in fields if row.get(key) is not None}


_REPORT_V1_STATUSES = {"ACTIVE", "DISCOVERED", "MAINTAINED", "WATCH"}


def _group_collection_availability(
    groups: Mapping[str, Any] | None, kind: str, trading_date: Any
) -> dict[str, Any]:
    source_key = "sectors" if kind == "industry" else "themes"
    if not isinstance(groups, Mapping):
        return availability(
            status="UNAVAILABLE",
            required_data=("운영 관심 업종과 현재 관측" if kind == "industry" else "운영 관심 테마와 현재 관측"),
            received=False,
            producer=("운영 관심 업종 설정·수집" if kind == "industry" else "운영 관심 테마 설정·수집"),
            reason="INTEREST_GROUP_NOT_CONFIGURED",
        )
    rows = [row for row in groups.get(source_key, []) if isinstance(row, Mapping)]
    raw_status = (groups or {}).get("status") or (groups or {}).get("qualityStatus")
    if str((groups or {}).get("configurationStatus") or "").upper() == "NOT_CONFIGURED":
        return availability(
            status="UNAVAILABLE",
            required_data=("운영 관심 업종 설정과 현재 관측" if kind == "industry" else "운영 관심 테마 설정과 현재 관측"),
            received=False,
            producer=("config/universe.json 관심 업종 설정" if kind == "industry" else "config/universe.json 관심 테마 설정"),
            reason="INTEREST_GROUP_NOT_CONFIGURED",
            artifact_path=(groups or {}).get("artifactPath"),
            generated_at=(groups or {}).get("generatedAt"),
            source_date=(groups or {}).get("sourceDate"),
            observation_date=(groups or {}).get("observationDate"),
            scope="INTEREST_GROUP_CONFIGURATION",
            input_count=0,
            matched_count=0,
            output_count=0,
            next_expected_at=(groups or {}).get("nextExpectedAt"),
        )
    reason = first_reason(groups, "missingReason", "reason", "policyReason", "validationReason")
    if not rows:
        reason = reason or (
            "PRODUCER_FAILED" if raw_status in {"FAILED", "ERROR", "INVALID"}
            else "INPUT_ARTIFACT_MISSING" if groups.get("artifactPath") is None
            else "INDUSTRY_MEMBERSHIP_MISSING" if kind == "industry"
            else "THEME_MEMBERSHIP_MISSING"
        )
    return availability(
        status=raw_status or ("AVAILABLE" if rows else "UNAVAILABLE"),
        required_data=("운영 관심 업종과 현재 관측" if kind == "industry" else "운영 관심 테마와 현재 관측"),
        received=bool(rows),
        producer=("운영 관심 업종 설정·수집" if kind == "industry" else "운영 관심 테마 설정·수집"),
        reason=reason,
        next_check_at=(groups or {}).get("nextCheckAt"),
        producer_run_id=(groups or {}).get("producerRunId"),
        artifact_path=(groups or {}).get("artifactPath"),
        freshness_status=(groups or {}).get("freshnessStatus"),
        checked_at=(groups or {}).get("checkedAt"),
        generated_at=(groups or {}).get("generatedAt"),
        source_date=(groups or {}).get("sourceDate"),
        observation_date=(groups or {}).get("observationDate"),
        scope=(groups or {}).get("scope"),
        input_count=(groups or {}).get("inputCount"),
        matched_count=(groups or {}).get("matchedCount"),
        output_count=(groups or {}).get("outputCount") or len(rows),
        next_expected_at=(groups or {}).get("nextExpectedAt"),
    )


def _compact_v1_rows(
    groups: Mapping[str, Any] | None,
    kind: str,
    relative_rows: list[Mapping[str, Any]],
    trading_date: Any,
    source_rows: list[Mapping[str, Any]] | None = None,
    observation_date: Any = None,
) -> list[dict[str, Any]]:
    """Project the existing V1 selection without changing its policy."""
    if not isinstance(groups, Mapping):
        return []
    source_key = "sectors" if kind == "industry" else "themes"
    relative_by_id = {
        str(row.get("id")): row
        for row in relative_rows
        if isinstance(row, Mapping) and row.get("id") is not None
    }
    source_by_id = {
        str(row.get("id")): row
        for row in source_rows or []
        if isinstance(row, Mapping) and row.get("id") is not None
    }
    expected_date = str(trading_date or "").replace("-", "")
    rows: list[dict[str, Any]] = []
    for row in groups.get(source_key) or []:
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status") or "").upper()
        row_date = str(row.get("lastObservedTradingDate") or "").replace("-", "")
        if status not in _REPORT_V1_STATUSES or (expected_date and row_date and row_date != expected_date):
            continue
        relative = relative_by_id.get(str(row.get("id")), {})
        source_row = source_by_id.get(str(row.get("id")), {})
        source_binding = (
            _current_group_leader_binding(source_row, kind, observation_date or trading_date)
            if source_row else {}
        )
        candidates = (
            relative.get("leaderCandidates")
            or relative.get("leaders")
            or source_binding.get("candidates")
            or []
        )
        leader_status = (
            relative.get("leaderEvidenceStatus")
            or source_binding.get("status")
            or "UNAVAILABLE_NO_CURRENT_SNAPSHOT"
        )
        leader_reason = (
            relative.get("leaderMissingReason")
            or source_binding.get("missingReason")
            or row.get("missingReason")
        )
        if not candidates and not leader_reason:
            if source_row.get("membershipStatus") in {"MISSING", "UNAVAILABLE"} or source_row.get("memberCount") in (0, None):
                leader_reason = "NO_CURRENT_MEMBERSHIP"
            elif source_row.get("stocks"):
                leader_reason = "SYMBOL_MATCH_FAILED"
            else:
                leader_reason = "LEADER_CALCULATION_FAILED"
        rows.append({
            "id": row.get("id"),
            "name": row.get("name"),
            "kind": kind,
            "status": status,
            "score": row.get("score"),
            "tradingDate": row.get("lastObservedTradingDate") or trading_date,
            "leaders": _compact_group_leaders(candidates),
            "leaderCandidates": _compact_group_leaders(candidates),
            "leaderEvidenceStatus": leader_status,
            "leaderSource": relative.get("leaderSource") or source_binding.get("source") or "NAVER_CURRENT_SNAPSHOT",
            "leaderObservationDate": relative.get("leaderObservationDate") or source_binding.get("observationDate") or relative.get("observationDate") or trading_date,
            "leaderScope": relative.get("leaderScope") or source_binding.get("scope") or relative.get("scope") or "STOCK_ONLY",
            "leaderMissingReason": leader_reason,
            "source": relative.get("source") or "NAVER_CURRENT_SNAPSHOT",
            "sourceDate": relative.get("sourceDate") or relative.get("sourceTradingDate") or trading_date,
            "observationDate": relative.get("observationDate") or relative.get("sourceTradingDate") or trading_date,
            "scope": relative.get("scope") or "STOCK_ONLY",
            "availability": availability(
                status=relative.get("leaderEvidenceStatus") or row.get("status"),
                required_data=("업종 현재 관측과 대표 주도주" if kind == "industry" else "테마 현재 관측과 대표 주도주"),
                received=bool(candidates),
                producer=("NAVER 업종 현재 관측" if kind == "industry" else "NAVER 테마 현재 관측"),
                reason=leader_reason if not candidates else None,
                next_check_at=relative.get("nextCheckAt"),
                producer_run_id=relative.get("producerRunId"),
                artifact_path=relative.get("artifactPath"),
                freshness_status=relative.get("freshnessStatus"),
                checked_at=relative.get("checkedAt"),
                generated_at=relative.get("generatedAt") or source_row.get("generatedAt"),
                source_date=relative.get("sourceDate") or source_row.get("sourceDate"),
                observation_date=relative.get("observationDate") or source_binding.get("observationDate"),
                scope=relative.get("scope") or source_binding.get("scope"),
                input_count=source_row.get("memberCount"),
                matched_count=source_row.get("featureCount"),
                output_count=len(candidates),
                next_expected_at=relative.get("nextExpectedAt") or source_row.get("nextExpectedAt"),
            ),
        })
    rows.sort(key=lambda item: (-float(item["score"]) if isinstance(item.get("score"), (int, float)) else 1e9, str(item.get("id") or "")))
    return rows[:5]


def _compact_research_analysis(
    research: Mapping[str, Any] | None,
    trading_date: Any,
    relative_tracking: Mapping[str, Any],
) -> dict[str, Any]:
    """Summarize the existing research intelligence for the user view."""
    if not isinstance(research, Mapping):
        return {
            "status": "UNAVAILABLE", "asOf": None, "items": [],
            "slotLineage": {},
            "availability": availability(
                status="UNAVAILABLE", required_data="리서치 원문과 분석 산출물",
                received=False, producer="NAVER Research Action",
                reason="RESEARCH_SLOT_ARTIFACT_MISSING",
            ),
        }
    research_status = str(research.get("status") or "UNAVAILABLE")
    as_of = str(research.get("asOf") or "").replace("-", "")
    expected = str(trading_date or "").replace("-", "")
    stocks = [row for row in research.get("stocks") or [] if isinstance(row, Mapping)]
    lineage = research.get("slotLineage") if isinstance(research.get("slotLineage"), Mapping) else {}
    if research_status in {"AVAILABLE", "CONSUMED"} and lineage.get("consumedByReport") is False:
        return {
            "status": "REPORT_LINK_FAILED", "asOf": research.get("asOf"), "items": [],
            "slotLineage": dict(lineage),
            "availability": availability(
                status="REPORT_LINK_FAILED", required_data="리서치 원문과 분석 산출물",
                received=True, producer="NAVER Research Action",
                reason="RESEARCH_REPORT_LINK_FAILED",
                next_check_at=lineage.get("nextCheckAt") or lineage.get("expectedAt"),
                producer_run_id=lineage.get("producerRunId"),
                artifact_path=lineage.get("artifactPath"),
                freshness_status=lineage.get("freshnessStatus"),
                report_slot=lineage.get("reportSlot"),
                research_slot=lineage.get("researchSlot"),
                expected_at=lineage.get("expectedAt"),
                checked_at=lineage.get("checkedAt"),
            ),
        }
    source_generation = (
        research.get("sourceGeneration")
        or research.get("generationId")
    )
    if (expected and as_of != expected) or not source_generation:
        reason = research.get("unavailableReason") or (
            "RESEARCH_DATE_MISMATCH" if expected and as_of != expected
            else "RESEARCH_SLOT_ARTIFACT_MISSING"
        )
        return {
            "status": research_status, "asOf": research.get("asOf"), "items": [],
            "slotLineage": dict(lineage),
            "availability": availability(
                status=research_status, required_data="리서치 원문과 분석 산출물",
                received=False, producer="NAVER Research Action", reason=reason,
                next_check_at=lineage.get("nextCheckAt") or lineage.get("expectedAt"),
                producer_run_id=lineage.get("producerRunId"),
                artifact_path=lineage.get("artifactPath"),
                freshness_status=lineage.get("freshnessStatus"),
                report_slot=lineage.get("reportSlot"),
                research_slot=lineage.get("researchSlot"),
                expected_at=lineage.get("expectedAt"),
                checked_at=lineage.get("checkedAt"),
            ),
        }
    eligible = [row for row in stocks if row.get("rankingEligible") is True]
    if not eligible:
        return {
            "status": research_status,
            "asOf": research.get("asOf"), "items": [],
            "slotLineage": dict(lineage),
            "availability": availability(
                status=research_status, required_data="리서치 원문과 분석 산출물",
                received=False, producer="NAVER Research Action",
                reason=research.get("unavailableReason") or "RESEARCH_ANALYSIS_MISSING",
                next_check_at=(research.get("slotLineage") or {}).get("nextCheckAt"),
                producer_run_id=lineage.get("producerRunId"),
                artifact_path=lineage.get("artifactPath"),
                freshness_status=lineage.get("freshnessStatus"),
                report_slot=lineage.get("reportSlot"),
                research_slot=lineage.get("researchSlot"),
                expected_at=lineage.get("expectedAt"),
                checked_at=lineage.get("checkedAt"),
            ),
        }

    items: list[dict[str, Any]] = []
    focus = max(eligible, key=lambda row: (row.get("recentReportCount") or 0, row.get("brokerCount") or 0))
    items.append({
        "category": "리서치 집중",
        "text": f"{focus.get('itemName') or focus.get('itemCode')}에 최근 리서치 {focus.get('recentReportCount') or 0}건이 집중되었습니다.",
    })
    revisions = sorted(eligible, key=lambda row: abs(row.get("targetMeanChangePct") or 0), reverse=True)
    if revisions and revisions[0].get("targetMeanChangePct") is not None:
        row = revisions[0]
        direction = "상향" if row.get("targetMeanChangePct", 0) > 0 else "하향" if row.get("targetMeanChangePct", 0) < 0 else "변동 없음"
        items.append({
            "category": "목표주가 방향",
            "text": f"{row.get('itemName') or row.get('itemCode')} 목표주가 평균 {direction} {abs(row.get('targetMeanChangePct')):.2f}%.",
        })
    opinion = max(eligible, key=lambda row: (row.get("revisionUp") or 0) + (row.get("revisionDown") or 0))
    opinion_count = (opinion.get("revisionUp") or 0) + (opinion.get("revisionDown") or 0)
    if opinion_count:
        items.append({
            "category": "투자의견 변경",
            "text": f"{opinion.get('itemName') or opinion.get('itemCode')} 의견 변경 관측 {opinion_count}건 (상향 {opinion.get('revisionUp') or 0}·하향 {opinion.get('revisionDown') or 0}).",
        })
    repeated = max(eligible, key=lambda row: (row.get("brokerCount") or 0, row.get("recentReportCount") or 0))
    items.append({
        "category": "반복 언급",
        "text": f"{repeated.get('itemName') or repeated.get('itemCode')}가 {repeated.get('brokerCount') or 0}개 증권사에서 언급되었습니다.",
    })
    leader_codes = {
        str(leader.get("code"))
        for section in (relative_tracking.get("industries") or {}, relative_tracking.get("themes") or {})
        for row in section.get("top5") or []
        for leader in row.get("leaderCandidates") or []
        if isinstance(leader, Mapping) and leader.get("code")
    }
    aligned = next((row for row in eligible if str(row.get("itemCode")) in leader_codes), None)
    if aligned:
        items.append({
            "category": "시장 방향 일치",
            "text": f"{aligned.get('itemName') or aligned.get('itemCode')}가 오늘의 주도 그룹 대표 종목과 리서치 양쪽에서 확인됩니다.",
        })
    return {
        "status": str(research.get("status") or "AVAILABLE"),
        "asOf": research.get("asOf"), "items": items[:5],
        "slotLineage": dict(lineage),
        "availability": availability(
            status=research.get("status") or "AVAILABLE",
            required_data="리서치 원문과 분석 산출물", received=True,
            producer="NAVER Research Action", reason=None,
            next_check_at=(research.get("slotLineage") or {}).get("checkedAt"),
            producer_run_id=lineage.get("producerRunId"),
            artifact_path=lineage.get("artifactPath"),
            freshness_status=lineage.get("freshnessStatus"),
            report_slot=lineage.get("reportSlot"),
            research_slot=lineage.get("researchSlot"),
            expected_at=lineage.get("expectedAt"),
            checked_at=lineage.get("checkedAt"),
        ),
    }


def _group_metrics(row: Mapping[str, Any], kind: str) -> dict[str, Any]:
    metrics = row.get("metrics") if kind == "theme" else row
    metrics = metrics if isinstance(metrics, Mapping) else {}
    # Published theme source rows nest metrics; V2 history rows expose the
    # comparable fields at the row root.  Do not mistake V2 component scores
    # for the source metrics.
    if kind == "theme" and not any(
        key in metrics for key in ("averageReturn1D", "relativeStrengthVsMarket", "officialChangeRateRelativeStrength")
    ):
        metrics = row
    relative = next(
        (metrics.get(field) for field in (
            "officialChangeRateRelativeStrength",
            "relativeStrengthVsMarket",
            "relativeStrengthVsMarketMedian",
        ) if isinstance(metrics.get(field), (int, float)) and not isinstance(metrics.get(field), bool)),
        None,
    )
    absolute = next(
        (metrics.get(field) for field in ("avgReturn1D", "averageReturn1D", "medianReturn1D")
         if isinstance(metrics.get(field), (int, float)) and not isinstance(metrics.get(field), bool)),
        None,
    )
    breadth = next(
        (metrics.get(field) for field in ("advancePct", "advancerPct")
         if isinstance(metrics.get(field), (int, float)) and not isinstance(metrics.get(field), bool)),
        None,
    )
    member_count = row.get("memberCount") or metrics.get("memberCount")
    coverage = row.get("coveragePct")
    if coverage is None:
        coverage = metrics.get("coveragePct")
    missing_codes = row.get("missingCodes")
    quality = row.get("dataQuality") or row.get("qualityStatus") or metrics.get("dataQuality")
    # Missing derived history affects only persistence metrics; the current
    # NAVER group observation remains usable when its own date and membership
    # evidence are valid.
    explicit_invalid = quality in {"UNAVAILABLE", "MISMATCH", "ERROR"}
    if kind == "industry":
        derived = row.get("derivedFeatureValidMemberCount")
        if derived is None:
            derived = row.get("featureValidMemberCount")
        feature_invalid = derived == 0 if derived is not None else False
    else:
        derived = metrics.get("derivedValidQuoteCount")
        feature_invalid = derived == 0 if derived is not None else False
    eligible = (
        relative is not None
        and not explicit_invalid
        and (member_count is None or float(member_count) > 0)
        and (coverage is None or float(coverage) > 0)
        and not (isinstance(missing_codes, list) and missing_codes)
    )
    reason = None
    if relative is None:
        reason = "RELATIVE_STRENGTH_UNAVAILABLE"
    elif explicit_invalid:
        reason = f"QUALITY_{quality}"
    elif feature_invalid:
        reason = "DERIVED_FEATURES_UNAVAILABLE"
    elif isinstance(missing_codes, list) and missing_codes:
        reason = "GROUP_MEMBERSHIP_INCOMPLETE"
    return {
        "relativeStrength": relative,
        "absoluteReturn1D": absolute,
        "breadthPct": breadth,
        "memberCount": member_count,
        "coveragePct": coverage,
        "eligible": eligible,
        "eligibilityReason": reason,
    }


def _group_observation_date(row: Mapping[str, Any]) -> str | None:
    """Find a current NAVER group date without treating it as a KRX date."""
    for key in ("observationDate", "observedDate", "sourceTradingDate", "tradingDate"):
        value = row.get(key)
        if value:
            return str(value).replace("-", "")[:8]
    for stock in row.get("stocks") or []:
        if not isinstance(stock, Mapping):
            continue
        for key in ("temporaryCurrentBarDate", "observationDate", "observedDate", "sourceTradingDate", "tradingDate"):
            value = stock.get(key)
            if value:
                return str(value).replace("-", "")[:8]
    return None


def _compact_normalized_date(value: Any) -> str | None:
    text = str(value or "").replace("-", "").replace("/", "")[:8]
    return text if len(text) == 8 and text.isdigit() else None


def _history_rankings(history: Mapping[str, Any], kind: str) -> tuple[list[str], dict[str, dict[str, int]], dict[str, dict[str, Mapping[str, Any]]]]:
    by_date: dict[str, list[Mapping[str, Any]]] = {}
    for observation in history.get("observations") or []:
        if not isinstance(observation, Mapping) or not observation.get("tradingDate"):
            continue
        if observation.get("dateQuality", "VERIFIED") == "MISMATCH":
            continue
        date = str(observation.get("tradingDate"))
        by_date.setdefault(date, []).append(observation)
    dates = sorted(by_date)[-7:]
    ranks: dict[str, dict[str, int]] = {}
    rows_by_date: dict[str, dict[str, Mapping[str, Any]]] = {}
    for date in dates:
        latest = sorted(by_date[date], key=lambda item: str(item.get("observedAt") or ""))[-1]
        rows = [row for row in latest.get("industries" if kind == "industry" else "themes", []) if isinstance(row, Mapping)]
        eligible = [row for row in rows if _group_metrics(row, kind)["eligible"]]
        eligible.sort(key=lambda row: (
            -float(_group_metrics(row, kind)["relativeStrength"]),
            -float(_group_metrics(row, kind)["absoluteReturn1D"] or -1e9),
            -float(_group_metrics(row, kind)["breadthPct"] or -1e9),
            -float(_group_metrics(row, kind)["memberCount"] or -1),
            str(row.get("id") or ""),
        ))
        ranks[date] = {str(row.get("id")): index for index, row in enumerate(eligible, 1) if row.get("id") is not None}
        rows_by_date[date] = {str(row.get("id")): row for row in rows if row.get("id") is not None}
    return dates, ranks, rows_by_date


def _current_group_leader_binding(
    row: Mapping[str, Any], kind: str, observation_date: Any
) -> dict[str, Any]:
    """Bind group leaders to the same-day NAVER observation only."""
    expected_date = _compact_normalized_date(observation_date)
    source_date = _group_observation_date(row)
    if expected_date and source_date and source_date != expected_date:
        status = "UNAVAILABLE_DATE_MISMATCH"
        candidates = []
        reason = "SOURCE_DATE_MISMATCH"
    elif kind == "industry":
        raw_candidates = [candidate for candidate in (row.get("leaders") or [])
                          if isinstance(candidate, Mapping)]
        candidates = [candidate for candidate in raw_candidates if (
            not expected_date
            or _compact_normalized_date(
                candidate.get("sourceDate") or candidate.get("observationDate")
                or candidate.get("temporaryCurrentBarDate")
            ) in {None, expected_date}
        )]
        if candidates:
            status = "AVAILABLE"
            reason = None
        elif raw_candidates and expected_date:
            status = "UNAVAILABLE_DATE_MISMATCH"
            reason = "LEADER_DATE_MISMATCH"
        else:
            status = "UNAVAILABLE_NO_CURRENT_SNAPSHOT"
            reason = "NO_CURRENT_LEADER_CANDIDATES"
    else:
        candidates = [stock for stock in (row.get("stocks") or [])
                      if isinstance(stock, Mapping)
                      and stock.get("currentPrice") is not None
                      and stock.get("changeRate") is not None
                      and (
                          stock.get("derivedMetricsUsable", True) is True
                          or not expected_date
                          or _group_observation_date({"stocks": [stock]}) == expected_date
                      )]
        candidates = sorted(candidates, key=_leader_sort_key, reverse=True)[:3]
        status = "AVAILABLE" if candidates else "UNAVAILABLE_NO_CURRENT_SNAPSHOT"
        reason = None if candidates else "NO_CURRENT_LEADER_CANDIDATES"
    return {
        "status": status,
        "source": "NAVER_CURRENT_SNAPSHOT",
        "sourceDate": source_date or expected_date,
        "observationDate": expected_date or source_date,
        "scope": "STOCK_ONLY",
        "missingReason": reason,
        "candidates": _compact_group_leaders(candidates),
    }


def build_relative_strength_tracking(
    *,
    industries: Mapping[str, Any],
    themes: Mapping[str, Any],
    v2_latest: Mapping[str, Any],
    v2_history: Mapping[str, Any],
    trading_date: Any,
    source_commit: Any,
    observation_date: Any = None,
) -> dict[str, Any]:
    """Create an independent TOP-5 observation layer; V1/V2 states stay intact."""
    result: dict[str, Any] = {
        "schemaVersion": "relative-strength-top5-v1",
        "status": "AVAILABLE",
        "tradingDate": trading_date,
        "observationDate": observation_date,
        "sourceCommit": source_commit,
        "sourceCommitStatus": "VERIFIED" if source_commit else "UNVERIFIED",
        "sourcePaths": {
            "industries": "data/monitoring/latest-industries.json#industries[]",
            "themes": "data/monitoring/latest-theme-signals.json#themes[]",
            "history": "data/dynamic-universe/v2-shadow-history.json#observations[]",
        },
        "observedTradingDates": [],
        "industries": {"top5": [], "active": [], "exited": []},
        "themes": {"top5": [], "active": [], "exited": []},
    }
    if not trading_date:
        result["status"] = "PARTIAL"
        result["reason"] = "TRADING_DATE_UNAVAILABLE"
        for kind, label in (("industries", "업종"), ("themes", "테마")):
            result[kind]["availability"] = availability(
                status="UNAVAILABLE", required_data=f"{label} 원천과 관측일",
                received=False,
                producer=("NAVER 업종 수집" if kind == "industries" else "NAVER 테마 수집"),
                reason="TRADING_DATE_UNAVAILABLE",
                next_check_at=None,
            )
        return result
    v2_by_key = {
        (str(row.get("kind")), str(row.get("id"))): row
        for row in (list(v2_latest.get("industries") or []) + list(v2_latest.get("themes") or []))
        if isinstance(row, Mapping) and row.get("id") is not None
    }
    for kind, source in (("industry", industries), ("theme", themes)):
        rows = [row for row in source.get("industries" if kind == "industry" else "themes", []) if isinstance(row, Mapping)]
        eligible = []
        for row in rows:
            metrics = _group_metrics(row, kind)
            v2 = v2_by_key.get((kind, str(row.get("id"))), {})
            if metrics["eligible"]:
                eligible.append((row, metrics, v2))
        eligible.sort(key=lambda item: (
            -float(item[1]["relativeStrength"]),
            -float(item[1]["absoluteReturn1D"] or -1e9),
            -float(item[1]["breadthPct"] or -1e9),
            -float(item[1]["memberCount"] or -1),
            str(item[0].get("id") or ""),
        ))
        dates, prior_ranks, prior_rows = _history_rankings(v2_history, kind)
        result["observedTradingDates"] = sorted(set(result["observedTradingDates"]) | set(dates) | ({str(trading_date)} if trading_date else set()))
        current_ranks = {str(row.get("id")): index for index, (row, _, _) in enumerate(eligible, 1)}
        current_date = str(trading_date) if trading_date else None
        previous_date = (
            dates[-2] if current_date and dates and dates[-1] == current_date and len(dates) >= 2
            else dates[-1] if dates and dates[-1] != current_date
            else None
        )
        previous_rank_map = prior_ranks.get(previous_date, {}) if previous_date else {}
        first_dates = {}
        for date in dates:
            for group_id, rank in prior_ranks.get(date, {}).items():
                if rank <= 5 and group_id not in first_dates:
                    first_dates[group_id] = date
        top5 = []
        for rank, (row, metrics, v2) in enumerate(eligible[:5], 1):
            group_id = str(row.get("id"))
            prior_rank = previous_rank_map.get(group_id)
            if prior_rank is None:
                change_class = "NEW_ENTRY" if previous_date else "FIRST_OBSERVATION"
            elif prior_rank > 5:
                change_class = "NEW_ENTRY"
            elif rank < prior_rank:
                change_class = "RANK_UP"
            elif rank > prior_rank:
                change_class = "RANK_DOWN"
            else:
                change_class = "MAINTAINED"
            consecutive = 1
            prior_dates = [date for date in dates if date != current_date]
            for date in reversed(prior_dates):
                if prior_ranks.get(date, {}).get(group_id, 999) <= 5:
                    consecutive += 1
                else:
                    break
            leader_binding = _current_group_leader_binding(row, kind, observation_date)
            top5.append({
                "rank": rank,
                "id": row.get("id"),
                "name": row.get("name"),
                "kind": kind,
                "relativeStrength": metrics["relativeStrength"],
                "absoluteReturn1D": metrics["absoluteReturn1D"],
                "breadthPct": metrics["breadthPct"],
                "memberCount": metrics["memberCount"],
                "coveragePct": metrics["coveragePct"],
                "eligibility": "ELIGIBLE",
                "v1Status": v2.get("v1Status") or row.get("status"),
                "v2Status": v2.get("v2Status"),
                "dataQuality": v2.get("dataQuality") or row.get("qualityStatus") or row.get("dataQuality") or "AVAILABLE",
                "score": v2.get("score"),
                "persistenceDays": v2.get("persistenceDays"),
                "leaderCandidates": leader_binding["candidates"],
                "leaderEvidenceStatus": leader_binding["status"],
                "leaderSource": leader_binding["source"],
                "leaderObservationDate": leader_binding["observationDate"],
                "leaderScope": leader_binding["scope"],
                "leaderMissingReason": leader_binding["missingReason"],
                "leaderAvailability": availability(
                    status=leader_binding["status"],
                    required_data=("업종 현재 관측과 대표 주도주" if kind == "industry" else "테마 현재 관측과 대표 주도주"),
                    received=bool(leader_binding["candidates"]),
                    producer=("NAVER 업종 현재 관측" if kind == "industry" else "NAVER 테마 현재 관측"),
                    reason=leader_binding["missingReason"],
                    next_check_at=None,
                ),
                "source": "NAVER_CURRENT_SNAPSHOT",
                "sourceDate": leader_binding["sourceDate"],
                "observationDate": leader_binding["observationDate"],
                "scope": "STOCK_ONLY",
                "rankBefore": prior_rank,
                "rankChange": (prior_rank - rank) if prior_rank is not None else None,
                "changeClass": change_class,
                "firstTop5Date": first_dates.get(group_id),
                "consecutiveTop5TradingDays": consecutive,
                "sourceGeneration": source.get("generatedAt"),
                "sourceCommit": source_commit,
                "sourceTradingDate": _group_observation_date(row),
            })
        active = []
        for row in (list(v2_latest.get("industries" if kind == "industry" else "themes") or [])):
            if not isinstance(row, Mapping) or row.get("v2Status") != "ACTIVE":
                continue
            active.append({"id": row.get("id"), "name": row.get("name"), "v2Status": row.get("v2Status"), "score": row.get("score"), "rank": current_ranks.get(str(row.get("id"))), "inTop5": str(row.get("id")) in current_ranks and current_ranks[str(row.get("id"))] <= 5})
        exited = []
        if previous_date:
            for group_id, prior_rank in previous_rank_map.items():
                if prior_rank <= 5 and current_ranks.get(group_id, 999) > 5:
                    prior = prior_rows.get(previous_date, {}).get(group_id, {})
                    exited.append({"id": group_id, "name": prior.get("name"), "rankBefore": prior_rank, "changeClass": "EXITED", "reason": "OUTSIDE_CURRENT_TOP5", "tradingDate": previous_date})
        result["industries" if kind == "industry" else "themes"] = {
            "top5": top5, "active": active, "exited": exited[:5],
            "availability": availability(
                status="AVAILABLE" if top5 else "UNAVAILABLE",
                required_data=("업종 상대강도와 현재 관측" if kind == "industry" else "테마 상대강도와 현재 관측"),
                received=bool(top5),
                producer=("NAVER 업종 수집" if kind == "industry" else "NAVER 테마 수집"),
                reason=("SOURCE_MISSING" if not rows else "RELATIVE_STRENGTH_UNAVAILABLE") if not top5 else None,
                next_check_at=None,
            ),
        }
    result["observedTradingDates"] = sorted(result["observedTradingDates"])[-7:]
    return result


_TRADING_DATE_EVIDENCE_SCALARS = (
    "tradingDate",
    "status",
    "reason",
    "evidenceClass",
    "collectionGeneratedAt",
    "sourceTime",
    "diagnosticStatus",
)
_TRADING_DATE_EVIDENCE_DETAIL_FIELDS = (
    "missingCanonicalCodes",
    "invalidCanonicalCodes",
    "missingFinalCodes",
    "unexpectedFinalCodes",
    "invalidFinalCodes",
)


def _compact_trading_date_evidence(evidence: Any) -> dict[str, Any]:
    """Keep the date decision while referencing bulky per-code diagnostics.

    The quality and summary artifacts remain the authoritative place for the
    full diagnostic lists.  They are not needed by Work to render a report,
    and copying them into the contract can exceed the GitHub Contents
    transport limit for a full-market snapshot.
    """
    if not isinstance(evidence, Mapping):
        return {}
    compact = {
        key: evidence[key]
        for key in _TRADING_DATE_EVIDENCE_SCALARS
        if key in evidence
    }
    detail_counts = {}
    for key in _TRADING_DATE_EVIDENCE_DETAIL_FIELDS:
        value = evidence.get(key)
        if isinstance(value, (list, Mapping)):
            detail_counts[key] = len(value)
    for key in ("observedDateCounts", "explicitSessionDates"):
        value = evidence.get(key)
        if value not in (None, {}, []):
            compact[key] = value
    for key in ("secondaryEvidence", "sourceObservedEvidence", "localDateDiagnostic"):
        nested = evidence.get(key)
        if isinstance(nested, Mapping):
            nested_counts = {
                field: len(nested[field])
                for field in _TRADING_DATE_EVIDENCE_DETAIL_FIELDS
                if isinstance(nested.get(field), (list, Mapping))
            }
            nested_summary = {
                field: nested[field]
                for field in _TRADING_DATE_EVIDENCE_SCALARS
                if field in nested
            }
            if nested_counts:
                nested_summary["detailCounts"] = nested_counts
            compact[key] = nested_summary
    if detail_counts:
        compact["detailCounts"] = detail_counts
        compact["detailSource"] = (
            "data/monitoring/latest-quality.json#tradingDateEvidence;"
            "data/monitoring/latest-summary.json#fullMarket.tradingDateEvidence"
        )
    return compact


def _compact_additional_observation_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep one configured additional-observation row in the Work contract."""
    leaders = [
        {
            key: item.get(key)
            for key in ("code", "symbol", "name", "changeRate", "currentPrice")
            if item.get(key) is not None
        }
        for item in (row.get("leaders") or [])[:3]
        if isinstance(item, Mapping)
    ]
    return {
        key: row.get(key)
        for key in (
            "id", "name", "type", "source", "sourceDate", "observationDate",
            "scope", "producer", "producerRunId", "artifactPath", "generatedAt",
            "membershipCount", "leaderCount", "selectionRank", "selectionScore",
            "selectionReason", "selectionEvidence", "status", "failureReason",
        )
        if row.get(key) is not None
    } | {"leaders": leaders}


def _compact_additional_observations(value: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(value or {})
    return {
        key: source.get(key)
        for key in (
            "schemaVersion", "status", "source", "sourceDate", "observationDate",
            "scope", "producer", "producerRunId", "generatedAt", "configPath", "artifactPath",
            "failureReason",
        )
        if source.get(key) is not None
    } | {
        "industries": [
            _compact_additional_observation_row(row)
            for row in (source.get("industries") or [])
            if isinstance(row, Mapping)
        ],
        "themes": [
            _compact_additional_observation_row(row)
            for row in (source.get("themes") or [])
            if isinstance(row, Mapping)
        ],
        "counts": dict(source.get("counts") or {}),
    }


def _leader_sort_key(row: Mapping[str, Any]) -> tuple[float, bool, float]:
    rate = row.get("changeRate")
    try:
        rate_value = float(rate)
    except (TypeError, ValueError):
        rate_value = -9999.0
    volume = row.get("current_volume_ratio_20")
    try:
        volume_value = float(volume)
    except (TypeError, ValueError):
        volume_value = -1.0
    return rate_value, row.get("breakout_20_intraday") is True, volume_value


def _compact_leader(row: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "code", "name", "market", "currentPrice", "changeRate",
        "accumulatedTradingValue", "dist_high_20", "current_volume_ratio_20",
        "above_ma20", "above_ma60", "breakout_20_intraday",
    )
    return {key: row.get(key) for key in fields if key in row}


def _leader_evidence(
    source_row: Mapping[str, Any] | None,
    *,
    kind: str,
    expected_generation: Any,
    source_commit: Any,
    observation_date: Any = None,
) -> dict[str, Any]:
    source_path = (
        "data/monitoring/latest-industries.json#industries[].leaders"
        if kind == "industry" else
        "data/monitoring/latest-theme-signals.json#themes[].stocks"
    )
    expected_observation_date = _compact_normalized_date(observation_date)
    if not isinstance(source_row, Mapping):
        return {
            "status": "UNAVAILABLE_MEMBERSHIP_SOURCE",
            "sourcePath": source_path,
            "sourceGeneration": None,
            "sourceCommit": source_commit,
            "source": "NAVER_CURRENT_SNAPSHOT",
            "sourceDate": None,
            "observationDate": expected_observation_date,
            "scope": "STOCK_ONLY",
            "missingReason": "NO_CURRENT_MEMBERSHIP",
            "candidates": [],
        }
    source_generation = source_row.get("generatedAt")
    if source_generation is None:
        # The group files carry generation at the document root; callers mark
        # this as unavailable when the root and row generations cannot align.
        source_generation = expected_generation
    if not expected_generation or source_generation != expected_generation:
        return {
            "status": "UNAVAILABLE_SOURCE_GENERATION_MISMATCH",
            "sourcePath": source_path,
            "sourceGeneration": source_generation,
            "sourceCommit": source_commit,
            "source": "NAVER_CURRENT_SNAPSHOT",
            "sourceDate": _group_observation_date(source_row),
            "observationDate": expected_observation_date,
            "scope": "STOCK_ONLY",
            "missingReason": "SOURCE_GENERATION_MISMATCH",
            "candidates": [],
        }
    source_date = _group_observation_date(source_row)
    if expected_observation_date and source_date and source_date != expected_observation_date:
        return {
            "status": "UNAVAILABLE_DATE_MISMATCH",
            "sourcePath": source_path,
            "sourceGeneration": source_generation,
            "sourceCommit": source_commit,
            "source": "NAVER_CURRENT_SNAPSHOT",
            "sourceDate": source_date,
            "observationDate": expected_observation_date,
            "scope": "STOCK_ONLY",
            "missingReason": "SOURCE_DATE_MISMATCH",
            "candidates": [],
        }
    if kind == "industry":
        candidates = [row for row in (source_row.get("leaders") or [])
                      if isinstance(row, Mapping)][:3]
    else:
        candidates = [
            stock for stock in (source_row.get("stocks") or [])
            if isinstance(stock, Mapping)
            and stock.get("currentPrice") is not None
            and stock.get("changeRate") is not None
            and (
                stock.get("derivedMetricsUsable", True) is True
                or not expected_observation_date
                or _group_observation_date({"stocks": [stock]}) == expected_observation_date
            )
        ]
        candidates = sorted(candidates, key=_leader_sort_key, reverse=True)[:3]
    candidate_dates = {
        _compact_normalized_date(
            row.get("sourceDate") or row.get("observationDate")
            or row.get("temporaryCurrentBarDate")
        )
        for row in candidates
        if isinstance(row, Mapping)
    }
    if expected_observation_date and candidate_dates and candidate_dates != {expected_observation_date}:
        candidates = [
            row for row in candidates
            if _compact_normalized_date(
                row.get("sourceDate") or row.get("observationDate")
                or row.get("temporaryCurrentBarDate")
            ) in {None, expected_observation_date}
        ]
    compact = [_compact_leader(row) for row in candidates if isinstance(row, Mapping)]
    if not compact:
        status = "UNAVAILABLE_NO_CURRENT_SNAPSHOT"
        missing_reason = "NO_CURRENT_LEADER_CANDIDATES"
    elif source_commit:
        status = "AVAILABLE"
        missing_reason = None
    else:
        status = "AVAILABLE_UNVERIFIED_SOURCE_COMMIT"
        missing_reason = None
    return {
        "status": status,
        "sourcePath": source_path,
        "sourceGeneration": source_generation,
        "sourceCommit": source_commit,
        "source": "NAVER_CURRENT_SNAPSHOT",
        "sourceDate": source_date or expected_observation_date,
        "observationDate": expected_observation_date or source_date,
        "scope": "STOCK_ONLY",
        "missingReason": missing_reason,
        "candidates": compact,
    }


def _compact_v2_source_metrics(
    source_row: Mapping[str, Any] | None,
    *,
    kind: str,
    expected_generation: Any,
    source_commit: Any,
) -> tuple[str, dict[str, Any]]:
    if not isinstance(source_row, Mapping):
        return "UNAVAILABLE_MEMBERSHIP_SOURCE", {}
    source_generation = source_row.get("generatedAt")
    if not expected_generation or source_generation != expected_generation:
        return "UNAVAILABLE_SOURCE_GENERATION_MISMATCH", {}
    metrics = source_row.get("metrics") if kind == "theme" else source_row
    metrics = metrics if isinstance(metrics, Mapping) else {}
    trading = metrics.get("tradingValue") if isinstance(metrics.get("tradingValue"), Mapping) else {}
    compact = {
        "memberCount": source_row.get("memberCount"),
        "advancers": metrics.get("advancers"),
        "advancePct": metrics.get("advancePct", source_row.get("advancePct")),
        "medianReturn1D": metrics.get("medianReturn1D"),
        "relativeStrengthVsMarket": metrics.get("relativeStrengthVsMarket"),
        "officialChangeRateRelativeStrength": metrics.get("officialChangeRateRelativeStrength"),
        "tradingValue": {
            key: trading.get(key)
            for key in ("total", "availableCount", "coveragePct", "top3ConcentrationPct")
            if key in trading
        },
        "coveragePct": source_row.get("coveragePct"),
    }
    compact = {key: value for key, value in compact.items() if value not in (None, {})}
    return "AVAILABLE_UNVERIFIED_SOURCE_COMMIT" if not source_commit else "AVAILABLE", compact


def _v2_history_summary(
    history: Mapping[str, Any],
    current_rows: list[Mapping[str, Any]],
    current_trading_date: Any,
) -> dict[str, Any]:
    def date_key(value: Any) -> str:
        return str(value or "").replace("-", "")

    observations = [
        item for item in (history.get("observations") or [])
        if isinstance(item, Mapping)
        and item.get("tradingDate")
        and item.get("dateQuality", "VERIFIED") != "MISMATCH"
    ]
    dates = sorted({str(item.get("tradingDate")) for item in observations})
    recent_dates = dates[-7:]
    latest_by_key: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for observation in sorted(observations, key=lambda item: (str(item.get("tradingDate")), str(item.get("observedAt")))):
        date = str(observation.get("tradingDate"))
        if date not in recent_dates:
            continue
        for kind, field in (("industry", "industries"), ("theme", "themes")):
            for row in observation.get(field) or []:
                if isinstance(row, Mapping) and row.get("id") is not None:
                    latest_by_key[(kind, str(row.get("id")), date)] = row

    tracked = []
    for current in current_rows:
        kind = str(current.get("kind") or "")
        key = (kind, str(current.get("id")),)
        snapshots = []
        for date in recent_dates:
            row = latest_by_key.get((*key, date))
            if row is None:
                continue
            snapshots.append({
                "tradingDate": date,
                "v2Status": row.get("v2Status"),
                "score": row.get("score"),
                "persistenceDays": row.get("persistenceDays"),
                "sourceSnapshotHash": next(
                    (item.get("sourceSnapshotHash") for item in observations if str(item.get("tradingDate")) == date),
                    None,
                ),
            })
        previous = snapshots[-2] if len(snapshots) >= 2 else None
        current_snapshot = snapshots[-1] if snapshots else None
        score_delta = None
        if previous and isinstance(previous.get("score"), (int, float)) and isinstance(current_snapshot and current_snapshot.get("score"), (int, float)):
            score_delta = round(current_snapshot["score"] - previous["score"], 2)
        if not previous:
            change_class = "NEW_OR_NO_COMPARABLE_PRIOR"
        elif previous.get("v2Status") != current_snapshot.get("v2Status"):
            change_class = "STATUS_CHANGED"
        elif score_delta is not None and score_delta > 0:
            change_class = "STRENGTHENING"
        elif score_delta is not None and score_delta < 0:
            change_class = "WEAKENING"
        else:
            change_class = "PERSISTING"
        current["historySummary"] = {
            "status": "AVAILABLE" if len(snapshots) >= 2 else "PARTIAL" if snapshots else "UNAVAILABLE",
            "currentObserved": bool(current_snapshot and date_key(current_snapshot.get("tradingDate")) == date_key(current_trading_date)),
            "availableTradingDays": len(snapshots),
            "recentTradingDates": recent_dates,
            "sameDayDeduplicated": True,
            "changeClass": change_class,
            "scoreDelta": score_delta,
            "snapshots": snapshots,
        }
        tracked.append({
            "kind": kind, "id": current.get("id"), "name": current.get("name"),
            **current["historySummary"],
        })
    return {
        "recordCount": len(observations),
        "uniqueTradingDates": dates,
        "recentTradingDates": recent_dates,
        "availableTradingDays": len(recent_dates),
        "sameDayDeduplicated": True,
        "status": "AVAILABLE" if len(recent_dates) >= 2 else "PARTIAL" if recent_dates else "UNAVAILABLE",
        "trackedRows": tracked,
    }


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
    v2_provenance: Mapping[str, Any] | None = None,
    kiwoom_performance: Mapping[str, Any] | None = None,
    v2_changes: list[Mapping[str, Any]] | None = None,
    session_context: Mapping[str, Any] | None = None,
    registered_universe: Mapping[str, Any] | None = None,
    v1_groups: Mapping[str, Any] | None = None,
    market_snapshot: Mapping[str, Any] | None = None,
    current_market_observation: Mapping[str, Any] | None = None,
    historical_technical_market: Mapping[str, Any] | None = None,
    v2_status: Mapping[str, Any] | None = None,
    additional_observations: Mapping[str, Any] | None = None,
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
    evidence = full_market.get("tradingDateEvidence") or quality.get("tradingDateEvidence") or {}
    trading_date = full_market.get("tradingDate") or quality.get("tradingDate")
    source_quality = (
        quality.get("collectionQualityStatus")
        or quality.get("sourceQualityStatus")
        or full_market.get("qualityStatus")
        or "UNAVAILABLE"
    )
    historical_lineage = _historical_lineage(quality, full_market)
    observed_date = (
        quality.get("observedDate")
        or full_market.get("observedDate")
        or quality.get("currentSnapshotDate")
        or trading_date
    )
    observed_at = (
        quality.get("observedAt")
        or full_market.get("observedAt")
        or generation
    )

    def indices_are_complete() -> bool:
        """Require both current index observations before marking a contract available."""
        rows = [row for row in (indices.get("indices") or []) if isinstance(row, Mapping)]
        by_name = {str(row.get("code") or row.get("indexCode") or row.get("name") or row.get("indexName")): row for row in rows}
        expected = str(
            (
                observed_date
                if historical_lineage.get("currentSnapshotSource") == "NAVER_SEPARATE"
                else trading_date
            )
            or ""
        ).replace("-", "")
        if not expected or not {"KOSPI", "KOSDAQ"}.issubset(by_name):
            return False
        for name in ("KOSPI", "KOSDAQ"):
            row = by_name[name]
            if not isinstance(row.get("close"), (int, float)) or not row.get("sourceTime"):
                return False
            source_date = str(row["sourceTime"])[:10].replace("-", "")
            if source_date != expected:
                return False
        return True

    observation_date = str(observed_date or "").replace("-", "")
    observation_date_verified = bool(observation_date and indices_are_complete())
    observation_date_status = "VERIFIED" if observation_date_verified else "UNAVAILABLE"
    observation_date_evidence = {
        "tradingDate": observation_date or None,
        "status": observation_date_status,
        "reason": (
            "VERIFIED_CURRENT_SNAPSHOT_INDICES"
            if observation_date_verified
            else "CURRENT_SNAPSHOT_DATE_EVIDENCE_INVALID"
        ),
        "evidenceClass": "INTRADAY_SESSION",
        "source": "NAVER_CURRENT_SNAPSHOT",
        "collectionGeneratedAt": observed_at,
    }

    def metric(name: str, count: str, eligible: str, pct: str) -> dict[str, Any]:
        return {
            "count": total.get(count),
            "eligibleCount": total.get(eligible),
            "pct": total.get(pct),
        }

    current_observation = _current_market_observation(
        current_market_observation if current_market_observation is not None else breadth,
        quality,
    )
    historical_source = historical_technical_market if isinstance(historical_technical_market, Mapping) else full_market

    def historical_metric(name: str, count: str, eligible: str, pct: str) -> dict[str, Any]:
        supplied = historical_source.get("metrics")
        if not isinstance(supplied, Mapping):
            supplied = historical_source.get("breadth")
        supplied_metric = supplied.get(name) if isinstance(supplied, Mapping) else None
        return dict(supplied_metric) if isinstance(supplied_metric, Mapping) else metric(name, count, eligible, pct)

    historical_metrics = {
        "aboveMA20": historical_metric("aboveMA20", "aboveMA20Count", "aboveMA20EligibleCount", "pctAboveMA20"),
        "aboveMA60": historical_metric("aboveMA60", "aboveMA60Count", "aboveMA60EligibleCount", "pctAboveMA60"),
        "aboveMA120": historical_metric("aboveMA120", "aboveMA120Count", "aboveMA120EligibleCount", "pctAboveMA120"),
        "breakout20": historical_metric("breakout20", "breakout20Count", "breakout20EligibleCount", "pctBreakout20"),
        "breakout60": historical_metric("breakout60", "breakout60Count", "breakout60EligibleCount", "pctBreakout60"),
        "volumeAbove20DayAverage": historical_metric("volumeAbove20DayAverage", "volumeAbove20DayAverageCount", "volumeAbove20DayAverageEligibleCount", "pctVolumeAbove20DayAverage"),
    }
    historical_total = ((historical_source.get("markets") or {}).get("TOTAL")
                        or historical_source.get("breadth") or total)
    historical_source_name = historical_lineage.get("source") or "UNAVAILABLE"
    historical_technical = {
        "status": "AVAILABLE" if any(isinstance(item.get("count"), int) for item in historical_metrics.values()) else "UNAVAILABLE",
        "source": historical_source_name,
        "targetDate": historical_lineage.get("targetDate") or trading_date,
        "generationId": historical_lineage.get("generationId"),
        "scope": "STOCK_ETF" if historical_source_name == "KRX" else (historical_source.get("scope") or "FULL_MARKET"),
        "instrumentCount": (
            historical_lineage.get("instrumentCount")
            or historical_source.get("instrumentCount")
            or historical_source.get("authoritativeCount")
            or historical_total.get("directionEligibleCount")
        ),
        "breadth": {key: historical_total.get(key) for key in ("advancers", "decliners", "unchanged")},
        "metrics": historical_metrics,
        "qualityStatus": quality.get("sourceQualityStatus") or quality.get("collectionQualityStatus") or historical_source.get("qualityStatus") or "UNAVAILABLE",
        "qualityWarnings": historical_source.get("qualityWarnings") or [],
    }

    source_by_kind = {
        "industry": {
            str(row.get("id")): row for row in (industries.get("industries") or [])
            if isinstance(row, Mapping) and row.get("id") is not None
        },
        "theme": {
            str(row.get("id")): row for row in (themes.get("themes") or [])
            if isinstance(row, Mapping) and row.get("id") is not None
        },
    }
    source_generations = {
        "industry": industries.get("generatedAt"),
        "theme": themes.get("generatedAt"),
    }
    v2_source_commit = (
        v2_latest.get("inputEvidence", {}).get("sourceCommit")
        if isinstance(v2_latest.get("inputEvidence"), Mapping) else None
    )
    v2_provenance = dict(v2_provenance) if isinstance(v2_provenance, Mapping) else (
        dict(v2_latest.get("inputEvidence", {}).get("sourceProvenance"))
        if isinstance(v2_latest.get("inputEvidence"), Mapping)
        and isinstance(v2_latest.get("inputEvidence", {}).get("sourceProvenance"), Mapping)
        else {
            "schemaVersion": "v2-source-provenance-v1",
            "status": "UNVERIFIED",
            "sourceCommit": None,
            "sourceCommitStatus": "UNAVAILABLE",
            "reason": "SOURCE_PROVENANCE_NOT_SUPPLIED",
        }
    )
    v2_status = dict(v2_status) if isinstance(v2_status, Mapping) else {}
    v2_effective_status = v2_status.get("status") or ("SUCCESS" if v2_latest else "GENERATION_UNAVAILABLE")
    v2_producer_status = v2_status.get("producerStatus") or v2_status.get("rawStatus") or v2_effective_status
    v2_failure_reason = (
        v2_status.get("failureReason")
        or v2_status.get("error")
        or ("V2_STATUS_NOT_SUPPLIED" if not v2_status and not v2_latest else None)
    )
    v2_consumption_status = "CONSUMED" if v2_effective_status in {"SUCCESS", "OK"} else "NOT_CONSUMED"
    v2_input_evidence = v2_latest.get("inputEvidence")
    v2_input_evidence = v2_input_evidence if isinstance(v2_input_evidence, Mapping) else {}
    v2_generation_id = (
        v2_status.get("generationId")
        or v2_input_evidence.get("generationId")
    )
    v2_status_summary = {
        "status": v2_effective_status,
        "producerStatus": v2_producer_status,
        "consumptionStatus": v2_consumption_status,
        "publicationPolicy": "PARTIAL_NON_BLOCKING",
        "runId": v2_status.get("runId"),
        "generatedAt": v2_status.get("generatedAt") or v2_latest.get("generatedAt"),
        "tradingDate": v2_status.get("tradingDate") or v2_latest.get("tradingDate") or v2_latest.get("sourceTradingDate"),
        "generationId": v2_generation_id,
        "expectedGenerationId": v2_status.get("expectedGenerationId"),
        "errorType": v2_status.get("errorType"),
        "failureReason": v2_failure_reason,
        "runStatus": v2_status.get("runStatus") or v2_status.get("rawStatus") or v2_producer_status,
        "artifactPath": v2_status.get("artifactPath") or "data/dynamic-universe/v2-shadow-latest.json",
        "generationEvidence": v2_status.get("generationEvidence") or {},
    }

    def selected(rows: Any, kind: str) -> list[dict[str, Any]]:
        result = []
        for row in rows or []:
            if row.get("v2Status") not in {"ACTIVE", "WATCH"}:
                continue
            item = dict(row)
            source_row = source_by_kind.get(kind, {}).get(str(row.get("id")))
            if isinstance(source_row, Mapping):
                source_row = dict(source_row)
                source_row["generatedAt"] = source_generations.get(kind)
            leader_evidence = _leader_evidence(
                source_row,
                kind=kind,
                expected_generation=v2_latest.get("generatedAt"),
                source_commit=v2_source_commit,
                observation_date=observation_date,
            )
            item["leaderCandidates"] = leader_evidence["candidates"]
            # Preserve the prior compact key for readers that already consume
            # V2 rows; the explicit name is preferred by new renderers.
            item["leaders"] = item["leaderCandidates"]
            item["leaderEvidenceStatus"] = leader_evidence["status"]
            item["leaderSource"] = leader_evidence.get("source")
            item["leaderObservationDate"] = leader_evidence.get("observationDate")
            item["leaderScope"] = leader_evidence.get("scope")
            item["leaderMissingReason"] = leader_evidence.get("missingReason")
            item["leaderEvidence"] = {
                key: value for key, value in leader_evidence.items() if key != "candidates"
            }
            source_metric_status, source_metrics = _compact_v2_source_metrics(
                source_row,
                kind=kind,
                expected_generation=v2_latest.get("generatedAt"),
                source_commit=v2_source_commit,
            )
            item["sourceMetricsStatus"] = source_metric_status
            item["sourceMetrics"] = source_metrics
            result.append(item)
        return result

    v2_industries = selected(v2_latest.get("industries"), "industry")
    v2_themes = selected(v2_latest.get("themes"), "theme")
    relative_strength_tracking = build_relative_strength_tracking(
        industries=industries,
        themes=themes,
        v2_latest=v2_latest,
        v2_history=v2_history,
        trading_date=trading_date,
        source_commit=v2_source_commit,
        observation_date=observed_date,
    )
    v1_industries = _compact_v1_rows(
        v1_groups, "industry", relative_strength_tracking["industries"]["top5"], trading_date,
        source_rows=industries.get("industries") or [], observation_date=observed_date,
    )
    v1_themes = _compact_v1_rows(
        v1_groups, "theme", relative_strength_tracking["themes"]["top5"], trading_date,
        source_rows=themes.get("themes") or [], observation_date=observed_date,
    )
    research_analysis = _compact_research_analysis(
        research, trading_date, relative_strength_tracking
    )
    observations = v2_history.get("observations") or []
    unique_dates = sorted({str(row.get("tradingDate")) for row in observations if row.get("tradingDate")})
    v2_history_summary = _v2_history_summary(
        v2_history,
        v2_industries + v2_themes,
        v2_latest.get("tradingDate") or v2_latest.get("sourceTradingDate") or trading_date,
    )
    compact_kiwoom_performance = dict(kiwoom_performance) if isinstance(kiwoom_performance, Mapping) else {
        "status": "UNAVAILABLE", "reason": "KIWOOM_PERFORMANCE_NOT_SUPPLIED", "stocks": []
    }
    # Keep the report contract compact: detailed raw history remains in the
    # dated source files and only the per-candidate horizon results cross the
    # Work boundary.
    compact_v2_changes = [
        _compact_v2_change(row)
        for row in (v2_changes or [])
        if isinstance(row, Mapping)
    ]
    session_context = dict(session_context) if isinstance(session_context, Mapping) else {
        "mode": "NOT_PREOPEN",
        "status": "NOT_APPLICABLE",
    }
    market_snapshot = dict(market_snapshot) if isinstance(market_snapshot, Mapping) else {
        "schemaVersion": "market-snapshot-report-projection-v1",
        "source": "NAVER_CURRENT_SNAPSHOT",
        "status": "UNAVAILABLE",
        "reason": "MARKET_SNAPSHOT_PROJECTION_NOT_SUPPLIED",
        "rankings": {"status": "UNAVAILABLE", "items": {}},
        "investorFlow": {"status": "UNAVAILABLE", "periods": {}},
        "industries": {"status": "UNAVAILABLE", "top": []},
        "industryMembership": {"status": "UNAVAILABLE"},
    }

    # ``candidates`` is retained as a compatibility alias for older callers.
    # New callers must provide technical and Kiwoom candidates separately.
    technical_candidates = (
        technical_candidates if technical_candidates is not None else candidates or []
    )
    compact_technical_candidates = compact_candidate_rows(technical_candidates)
    kiwoom_candidates = kiwoom_candidates if isinstance(kiwoom_candidates, Mapping) else {}
    kiwoom_symbols = list(kiwoom_candidates.get("symbols") or [])
    kiwoom_details = list(kiwoom_candidates.get("candidateDetails") or [])
    kiwoom_stocks = compact_kiwoom_candidate_rows(
        kiwoom_candidates.get("stocks") or [], kiwoom_details
    )
    kiwoom_codes = [row.get("itemCode") for row in kiwoom_stocks if isinstance(row, Mapping)]
    kiwoom_name_map = {
        str(row.get("itemCode")): row.get("itemName")
        for row in kiwoom_stocks
        if isinstance(row, Mapping) and row.get("itemCode")
    }
    source_validation = kiwoom_candidates.get("validation")
    kiwoom_source_path = kiwoom_candidates.get("sourcePath")
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
        if not trading_date or not trading_date_evidence_valid
        or not indices_are_complete()
        or (historical_lineage["source"] == "KRX" and historical_lineage["lineageStatus"] != "VERIFIED")
        else "AVAILABLE" if aligned and full_market else "PARTIAL"
    )

    contract: dict[str, Any] = {
        "schemaVersion": "market-report-contract-v1",
        "status": contract_status,
        "asOf": trading_date,
        "observedAt": observed_at,
        "historicalAsOfDate": historical_lineage["targetDate"],
        "observedDate": observed_date,
        "observationDate": observation_date or None,
        "observationDateStatus": observation_date_status,
        "observationDateEvidence": observation_date_evidence,
        "tradingDateStatus": trading_date_status,
        "sourceQualityStatus": source_quality,
        "historicalIndicatorSource": historical_lineage["source"],
        "sourceGeneration": historical_lineage["generationId"],
        "historicalGenerationId": historical_lineage["generationId"],
        "historicalTargetDate": historical_lineage["targetDate"],
        "historicalFallbackAllowed": historical_lineage["fallbackAllowed"],
        "currentSnapshotSource": historical_lineage["currentSnapshotSource"],
        "manifestSha256": quality.get("manifestSha256") or full_market.get("manifestSha256"),
        "monitoringSha256": quality.get("monitoringSha256") or full_market.get("monitoringSha256"),
        "entityCount": quality.get("entityCount") or historical_lineage["entityCount"],
        "instrumentCount": quality.get("instrumentCount") or historical_lineage["instrumentCount"],
        "historicalLineage": historical_lineage,
        "tradingDateEvidence": _compact_trading_date_evidence(evidence),
        "sessionContext": session_context,
        "sourceGenerations": {
            "market": generation,
            "indices": indices.get("generatedAt"),
            "v2": v2_status.get("generatedAt") or v2_latest.get("generatedAt"),
            "kiwoom": kiwoom_candidates.get("syncedAt"),
        },
        "sourceBindings": {
            "reportContract": "data/monitoring/latest-report-contract.json",
            "currentMarketObservation": "data/monitoring/latest-breadth.json#breadth.TOTAL",
            "historicalTechnicalMarket": "reportContract.historicalTechnicalMarket",
            "markdownReport": "data/reports/latest.md",
            "markdownReportWriter": "tools/market_dashboard_renderer.py#render_contract_views",
            "htmlReport": "data/reports/latest.html",
            "htmlReportWriter": "tools/market_dashboard_renderer.py#render_contract_views",
            "currentObservationMarkdown": "data/reports/current-observation/<workflowRunId>/report.md",
            "currentObservationHtml": "data/reports/current-observation/<workflowRunId>/report.html",
            "kiwoomCandidates": kiwoom_source_path or "UNAVAILABLE",
            "technicalCandidates": {
                "path": "data/monitoring/latest-report-contract.json#technicalCandidates",
                "count": len(technical_candidates),
                "included": len(compact_technical_candidates),
            },
            "themeStockDetails": "data/monitoring/latest-theme-signals.json#themes[].stocks",
            "industryDetails": "data/monitoring/latest-industries.json#industries[]",
            "marketSnapshot": "reportContract.marketSnapshot",
            "marketSnapshotSource": "data/market/report-snapshot-projection.json",
            "v2HistoryDetails": "data/dynamic-universe/v2-shadow-history.json#observations[]",
            "relativeStrengthTop5": "reportContract.relativeStrengthTop5",
            "additionalObservations": "reportContract.additionalObservations",
            "additionalObservationConfig": "config/additional-observations.json",
            "additionalObservationArtifact": "data/monitoring/additional-observations.json",
            "forbidCandidateMixing": True,
            "historicalAndPreMarketMustRemainSeparate": True,
        },
        "currentMarketObservation": current_observation,
        "historicalTechnicalMarket": historical_technical,
        "indices": indices.get("indices") or [],
        "marketSnapshot": market_snapshot,
        "fullMarket": {
            # Breadth arithmetic is evaluated against the valid direction
            # denominator.  Entity coverage remains available in the market
            # breakdown and quality fields; missing returns are not coerced
            # into unchanged rows.
            "totalCount": total.get("directionEligibleCount") or full_market.get("authoritativeCount"),
            "breadth": {
                "advancers": total.get("advancers"),
                "decliners": total.get("decliners"),
                "unchanged": total.get("unchanged"),
                "directionEligibleCount": total.get("directionEligibleCount"),
            },
            "metrics": {
                "aboveMA20": metric("aboveMA20", "aboveMA20Count", "aboveMA20EligibleCount", "pctAboveMA20"),
                "aboveMA60": metric("aboveMA60", "aboveMA60Count", "aboveMA60EligibleCount", "pctAboveMA60"),
                "aboveMA120": metric("aboveMA120", "aboveMA120Count", "aboveMA120EligibleCount", "pctAboveMA120"),
                "breakout20": metric("breakout20", "breakout20Count", "breakout20EligibleCount", "pctBreakout20"),
                "breakout60": metric("breakout60", "breakout60Count", "breakout60EligibleCount", "pctBreakout60"),
                "volumeAbove20DayAverage": metric("volumeAbove20DayAverage", "volumeAbove20DayAverageCount", "volumeAbove20DayAverageEligibleCount", "pctVolumeAbove20DayAverage"),
            },
            "maBasisDates": full_market.get("maBasisDates") or [],
            "maBasisDateEvidence": full_market.get("maBasisDateEvidence") or {},
            "marketBreakdown": full_market.get("marketBreakdown") or {},
            "indexIdentities": full_market.get("indexIdentities") or [],
            "indicatorAvailability": full_market.get("indicatorAvailability") or {},
            "historicalIndicatorSource": full_market.get("historicalIndicatorSource"),
            "historicalPriority": full_market.get("historicalPriority"),
            "historicalFallbackAllowed": full_market.get("historicalFallbackAllowed", False),
            "historicalLineage": historical_lineage,
        },
        # Full member-level source rows stay in their published artifacts;
        # the contract carries only the stable group summary needed by Work.
        "industries": _compact_group_rows(industries.get("industries") or [], "industry"),
        "themes": _compact_group_rows(themes.get("themes") or [], "theme"),
        "relativeStrengthTop5": relative_strength_tracking,
        "v1": {
            "status": "AVAILABLE" if v1_industries or v1_themes else "UNAVAILABLE",
            "industries": v1_industries,
            "themes": v1_themes,
            "availability": {
                "industries": _group_collection_availability(v1_groups, "industry", trading_date),
                "themes": _group_collection_availability(v1_groups, "theme", trading_date),
            },
        },
        "leaders": [
            _compact_leader(row)
            for row in (leaders.get("fullMarketLeaders") or leaders.get("candidates") or [])
            if isinstance(row, Mapping)
        ][:30],
        "registeredUniverse": compact_registered_universe(registered_universe),
        "v2": {
            **v2_status_summary,
            "tradingDate": v2_status_summary["tradingDate"] or v2_latest.get("tradingDate") or v2_latest.get("sourceTradingDate") or trading_date,
            "sourceQualityStatus": v2_latest.get("inputEvidence", {}).get("sourceQualityStatus") if isinstance(v2_latest.get("inputEvidence"), Mapping) else None,
            "sourceCommit": v2_latest.get("inputEvidence", {}).get("sourceCommit") if isinstance(v2_latest.get("inputEvidence"), Mapping) else None,
            "sourceCommitStatus": v2_latest.get("inputEvidence", {}).get("sourceCommitStatus") if isinstance(v2_latest.get("inputEvidence"), Mapping) else "UNAVAILABLE",
            "sourceProvenance": v2_provenance,
            "history": v2_history_summary,
            "industries": v2_industries,
            "themes": v2_themes,
            # The canonical list lives at the top level.  Keep a source
            # pointer here instead of serializing the same 343-row list a
            # second time.
            "changesSource": "reportContract.v2Changes",
        },
        "v2Changes": compact_v2_changes,
        "additionalObservations": _compact_additional_observations(additional_observations),
        "independentAnalysis": {
            "research": {
                "status": (research or {}).get("status") if isinstance(research, Mapping) else "UNAVAILABLE",
                "asOf": (research or {}).get("asOf") if isinstance(research, Mapping) else None,
                "sourceGeneration": (
                    (research or {}).get("sourceGeneration")
                    or (research or {}).get("generationId")
                    or (research or {}).get("generatedAt")
                ) if isinstance(research, Mapping) else None,
                "generatedAt": (research or {}).get("generatedAt") if isinstance(research, Mapping) else None,
                "slotLineage": (research or {}).get("slotLineage") if isinstance(research, Mapping) else [],
                "availability": research_analysis.get("availability") or {},
                "analysis": research_analysis,
            },
            "investorFlow": {
                "status": (market_snapshot.get("investorFlow") or {}).get("status", "UNAVAILABLE"),
                "source": "reportContract.marketSnapshot.investorFlow",
                "periodCount": len((market_snapshot.get("investorFlow") or {}).get("periods", {})),
                "periodsSource": "reportContract.marketSnapshot.investorFlow.periods",
            },
            "disclosures": {
                "status": "UNAVAILABLE",
                "reason": "이 계약 입력에 동일 기준시각의 공시 원천이 없습니다.",
            },
        },
        # Keep one canonical candidate list.  The second key is a pointer,
        # not a second copy of thousands of rows.
        "candidates": {
            "source": "reportContract.technicalCandidates",
            "count": len(technical_candidates),
            "included": len(compact_technical_candidates),
        },
        "technicalCandidates": compact_technical_candidates,
        "kiwoomCandidates": {
            # ``source`` is the immutable date-selected file actually read by
            # the report.  Keep the semantic producer in sourceType so callers
            # do not confuse it with the next session's latest.json.
            "source": kiwoom_source_path,
            "sourceType": kiwoom_candidates.get("source") or "KIWOOM_CONDITION_SEARCH",
            "sourcePath": kiwoom_source_path,
            "selectionDate": kiwoom_candidates.get("selectionDate"),
            "monitoringDate": kiwoom_candidates.get("monitoringDate"),
            "sourceSha256": kiwoom_candidates.get("sourceSha256"),
            "symbols": kiwoom_symbols,
            "stocks": kiwoom_stocks,
            "codeNameMap": kiwoom_name_map,
            "codeNameStatus": kiwoom_name_status,
            "sourceValidation": source_validation if isinstance(source_validation, Mapping) else None,
            "sourceCandidateCount": kiwoom_candidates.get("candidateCount"),
            "performanceStatus": compact_kiwoom_performance.get("status"),
            "candidateDetails": kiwoom_details,
            "candidateCompleteness": kiwoom_candidates.get("candidateCompleteness"),
            "artifactStatus": kiwoom_candidates.get("artifactStatus"),
            "publicationStatus": kiwoom_candidates.get("publicationStatus"),
        },
        "kiwoomPerformance": compact_kiwoom_performance,
        "rendering": {
            "status": "RENDERABLE" if contract_status in {"AVAILABLE", "PARTIAL"} and trading_date else "UNAVAILABLE",
            "reason": (
                "CONFIRMED_CONTRACT"
                if contract_status == "AVAILABLE"
                else "PARTIAL_DATA_RETAINED"
                if contract_status == "PARTIAL" and trading_date
                else "TRADING_DATE_UNAVAILABLE"
            ),
        },
    }
    # Retired monitoring lifecycle state is intentionally absent from the
    # canonical contract. Historical KRX lineage is carried by
    # ``historicalTechnicalMarket`` and current observations by
    # ``currentMarketObservation``.
    contract["contractId"] = contract_id(contract)
    contract["reportLineage"] = lineage_from_contract(contract)
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
        elif any(row.get(key) not in (None, "", {}, []) for key in (
            "ma5", "ma20", "ma60", "volumeRatio20", "technicalMetrics",
            "technicalSourceDate", "technicalGenerationId", "technicalReceiptStatus",
        )):
            errors.append("KIWOOM_KRX_TECHNICAL_MIX")
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
