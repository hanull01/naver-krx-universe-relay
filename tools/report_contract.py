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
SOURCE_OBSERVED_TRADING_DATE_REASON = "SOURCE_OBSERVED_MONITORING_DAILY_FINAL_DATE"

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


_GROUP_SUMMARY_FIELDS = (
    "id", "name", "kind", "status", "v1Status", "v2Status",
    "membershipStatus", "memberCount", "featureCount", "coveragePct",
    "advancers", "decliners", "unchanged", "advancePct", "avgReturn1D",
    "averageReturn1D", "medianReturn1D", "relativeStrengthVsMarket",
    "officialChangeRateRelativeStrength", "pctAboveMA5", "pctAboveMA20",
    "pctAboveMA60", "pctBreakout20", "pctNear52WeekHigh",
    "pctVolumeAbove20DayAverage", "volumeIncreasePct", "dataQuality",
    "qualityStatus", "industrySourcePresent", "featureValidMemberCount",
    "derivedFeatureValidMemberCount", "closeValidMemberCount",
    "historyAdjustmentUnverifiedCount", "missingCodeCount",
)


def _compact_group_leaders(rows: Any) -> list[dict[str, Any]]:
    compact = []
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        item = _compact_leader(row)
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
    trading = metrics.get("tradingValue")
    if isinstance(trading, Mapping):
        item["tradingValue"] = {
            key: trading.get(key)
            for key in ("total", "availableCount", "coveragePct", "top3ConcentrationPct", "comparisonStatus")
            if trading.get(key) is not None
        }
    persistence = metrics.get("persistence")
    if isinstance(persistence, Mapping):
        item["persistence"] = {
            key: persistence.get(key)
            for key in ("status", "availableTradingDays")
            if persistence.get(key) is not None
        }
    missing_codes = row.get("missingCodes")
    if isinstance(missing_codes, list):
        item["missingCodeCount"] = len(missing_codes)
    if kind == "theme" and isinstance(row.get("stocks"), list):
        item["stockDetailCount"] = len(row["stocks"])
        item["stockDetailSource"] = "data/monitoring/latest-theme-signals.json#themes[].stocks"
    leaders = row.get("leaders")
    if leaders:
        item["leaders"] = _compact_group_leaders(leaders)
    item["detailSource"] = (
        "data/monitoring/latest-industries.json#industries[]"
        if kind == "industry" else
        "data/monitoring/latest-theme-signals.json#themes[]"
    )
    return item


def _compact_group_rows(rows: Any, kind: str) -> list[dict[str, Any]]:
    return [item for item in (_compact_group_row(row, kind) for row in rows or []) if item]


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
    explicit_invalid = quality in {"UNAVAILABLE", "BASELINE_STALE", "MISMATCH", "ERROR"}
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
        and not feature_invalid
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


def build_relative_strength_tracking(
    *,
    industries: Mapping[str, Any],
    themes: Mapping[str, Any],
    v2_latest: Mapping[str, Any],
    v2_history: Mapping[str, Any],
    trading_date: Any,
    source_commit: Any,
) -> dict[str, Any]:
    """Create an independent TOP-5 observation layer; V1/V2 states stay intact."""
    result: dict[str, Any] = {
        "schemaVersion": "relative-strength-top5-v1",
        "status": "AVAILABLE",
        "tradingDate": trading_date,
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
            source_leaders = row.get("leaders")
            if kind == "theme" and not source_leaders:
                source_leaders = sorted(
                    [stock for stock in (row.get("stocks") or []) if isinstance(stock, Mapping)],
                    key=lambda stock: (
                        -float(stock.get("changeRate") or -1e9),
                        -float(stock.get("accumulatedTradingValue") or 0),
                        str(stock.get("code") or ""),
                    ),
                )[:3]
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
                "leaderCandidates": _compact_group_leaders(source_leaders),
                "rankBefore": prior_rank,
                "rankChange": (prior_rank - rank) if prior_rank is not None else None,
                "changeClass": change_class,
                "firstTop5Date": first_dates.get(group_id),
                "consecutiveTop5TradingDays": consecutive,
                "sourceGeneration": source.get("generatedAt"),
                "sourceCommit": source_commit,
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
            "top5": top5, "active": active, "exited": exited[:5]
        }
    result["observedTradingDates"] = sorted(result["observedTradingDates"])[-7:]
    return result


_TRADING_DATE_EVIDENCE_SCALARS = (
    "tradingDate",
    "status",
    "reason",
    "evidenceClass",
    "finalConfirmed",
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
) -> dict[str, Any]:
    if not isinstance(source_row, Mapping):
        return {
            "status": "UNAVAILABLE_MEMBERSHIP_SOURCE",
            "sourcePath": "data/monitoring/latest-industries.json#industries[].leaders"
            if kind == "industry" else "data/monitoring/latest-theme-signals.json#themes[].stocks",
            "sourceGeneration": None,
            "sourceCommit": source_commit,
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
            "sourcePath": "data/monitoring/latest-industries.json#industries[].leaders"
            if kind == "industry" else "data/monitoring/latest-theme-signals.json#themes[].stocks",
            "sourceGeneration": source_generation,
            "sourceCommit": source_commit,
            "candidates": [],
        }
    if kind == "industry":
        candidates = list(source_row.get("leaders") or [])[:3]
    else:
        candidates = [
            stock for stock in (source_row.get("stocks") or [])
            if isinstance(stock, Mapping)
            and stock.get("derivedMetricsUsable", True) is True
        ]
        candidates = sorted(candidates, key=_leader_sort_key, reverse=True)[:3]
    compact = [_compact_leader(row) for row in candidates if isinstance(row, Mapping)]
    if not compact:
        status = "AVAILABLE_ZERO"
    elif source_commit:
        status = "AVAILABLE"
    else:
        status = "AVAILABLE_UNVERIFIED_SOURCE_COMMIT"
    return {
        "status": status,
        "sourcePath": "data/monitoring/latest-industries.json#industries[].leaders"
        if kind == "industry" else "data/monitoring/latest-theme-signals.json#themes[].stocks",
        "sourceGeneration": source_generation,
        "sourceCommit": source_commit,
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
            )
            item["leaderCandidates"] = leader_evidence["candidates"]
            # Preserve the prior compact key for readers that already consume
            # V2 rows; the explicit name is preferred by new renderers.
            item["leaders"] = item["leaderCandidates"]
            item["leaderEvidenceStatus"] = leader_evidence["status"]
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
    compact_v2_changes = [dict(row) for row in (v2_changes or []) if isinstance(row, Mapping)]
    session_context = dict(session_context) if isinstance(session_context, Mapping) else {
        "mode": "NOT_PREOPEN",
        "status": "NOT_APPLICABLE",
    }

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
    source_observed_date = (
        isinstance(evidence, Mapping)
        and evidence.get("status") == "SOURCE_OBSERVED"
        and evidence.get("reason") == SOURCE_OBSERVED_TRADING_DATE_REASON
        and evidence.get("evidenceClass") == "SOURCE_OBSERVED"
        and str(evidence.get("tradingDate") or "") == str(trading_date or "")
    )
    trading_date_status = (
        "VERIFIED" if trading_date_evidence_valid
        else "SOURCE_OBSERVED" if source_observed_date
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
        "tradingDateEvidence": _compact_trading_date_evidence(evidence),
        "sessionContext": session_context,
        "sourceGenerations": {
            "market": generation,
            "indices": indices.get("generatedAt"),
            "v2": v2_latest.get("generatedAt"),
            "kiwoom": kiwoom_candidates.get("syncedAt"),
        },
        "sourceBindings": {
            "reportContract": "data/monitoring/latest-report-contract.json",
            "markdownReport": "data/reports/latest.md",
            "htmlReport": "data/reports/latest.html",
            "kiwoomCandidates": kiwoom_source_path or "UNAVAILABLE",
            "technicalCandidates": "data/reports/latest.json#nextSessionCandidates",
            "themeStockDetails": "data/monitoring/latest-theme-signals.json#themes[].stocks",
            "industryDetails": "data/monitoring/latest-industries.json#industries[]",
            "v2HistoryDetails": "data/dynamic-universe/v2-shadow-history.json#observations[]",
            "relativeStrengthTop5": "reportContract.relativeStrengthTop5",
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
        # Full member-level source rows stay in their published artifacts;
        # the contract carries only the stable group summary needed by Work.
        "industries": _compact_group_rows(industries.get("industries") or [], "industry"),
        "themes": _compact_group_rows(themes.get("themes") or [], "theme"),
        "relativeStrengthTop5": relative_strength_tracking,
        "leaders": leaders.get("fullMarketLeaders") or leaders.get("candidates") or [],
        "v2": {
            "tradingDate": v2_latest.get("tradingDate") or v2_latest.get("sourceTradingDate") or trading_date,
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
        },
        "kiwoomPerformance": compact_kiwoom_performance,
        "rendering": {
            "htmlAllowed": bool(
                contract_status == "AVAILABLE"
                and trading_date
                and trading_date_evidence_valid
            ),
            "reason": (
                "CONTRACT_VERIFIED"
                if contract_status == "AVAILABLE" and trading_date
                else "SOURCE_OBSERVED_PARTIAL_HTML_BLOCKED"
                if trading_date_status == "SOURCE_OBSERVED"
                else "BASELINE_STALE_HTML_BLOCKED"
                if baseline_status == "BASELINE_STALE"
                else "TRADING_DATE_UNAVAILABLE_HTML_BLOCKED"
                if not trading_date
                else "TRADING_DATE_EVIDENCE_INVALID_HTML_BLOCKED"
                if not trading_date_evidence_valid
                else "CONTRACT_UNAVAILABLE_HTML_BLOCKED"
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
