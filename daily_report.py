#!/usr/bin/env python3

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import trading_discovery

KST = ZoneInfo("Asia/Seoul")

STATUS_LABELS = {
    "AVAILABLE": "확인 가능",
    "AVAILABLE_EMPTY": "확인 가능(대상 없음)",
    "AVAILABLE_WITH_BASELINE_WARNING": "확인 가능(기준선 경고)",
    "OK": "정상",
    "SUCCESS": "성공",
    "PARTIAL": "부분 확인",
    "UNAVAILABLE": "확인 불가",
    "UNUSABLE": "사용 불가",
    "DISABLED_PENDING_RESEARCH_GATE": "연구 검증 대기",
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
    if value in (None, ""):
        return "확인 불가"
    return STATUS_LABELS.get(str(value), str(value))


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

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
UNIVERSE_PATH = ROOT / "config" / "universe.json"

QUOTES_PATH = DATA / "quotes.json"
TECHNICALS_PATH = DATA / "technicals.json"
STATES_PATH = DATA / "states.json"
GROUP_STATES_PATH = DATA / "group-states.json"
RESEARCH_PATH = DATA / "research" / "intelligence" / "latest.json"
MONITORING_PATH = DATA / "monitoring"
TRADING_DISCOVERY_PATH = DATA / "trading-discovery" / "latest.json"
TRADING_DISCOVERY_QUOTES_PATH = DATA / "trading-discovery" / "quotes" / "latest.json"
FULL_MARKET_SIGNALS_PATH = MONITORING_PATH / "latest-stock-signals.json"
MONITORING_BASELINE_PATH = DATA / "monitoring-baseline" / "latest.json"
MARKET_INDICES_PATH = DATA / "market" / "indices.json"
DYNAMIC_UNIVERSE_PATH = DATA / "dynamic-universe" / "latest.json"
V2_SHADOW_LATEST_PATH = DATA / "dynamic-universe" / "v2-shadow-latest.json"
V2_SHADOW_HISTORY_PATH = DATA / "dynamic-universe" / "v2-shadow-history.json"
V2_SHADOW_STATUS_PATH = DATA / "dynamic-universe" / "v2-shadow-run-status.json"
PREVIOUS_REPORT_PATH = DATA / "reports" / "latest.json"
FULL_MARKET_FILES = (
    "latest-breadth.json",
    "latest-stock-signals.json",
    "latest-industries.json",
    "latest-leaders.json",
    "latest-theme-signals.json",
    "latest-changes.json",
    "latest-quality.json",
    "latest-summary.json",
)


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


def full_market_date_evidence(payloads):
    """Extract explicit session and MA dates; generatedAt is never a date source."""
    session_keys = ("tradingDate", "tradeDate", "marketDate", "snapshotTradingDate")
    session_dates = []
    for name in ("latest-quality.json", "latest-breadth.json", "latest-industries.json", "latest-summary.json"):
        payload = payloads.get(name) or {}
        session_dates.extend(_explicit_dates(payload, session_keys))
        session_dates.extend(_explicit_dates(payload.get("fullMarket"), session_keys))

    ma_dates = []
    for row in (payloads.get("latest-leaders.json", {}).get("fullMarketLeaders") or []):
        evidence = row.get("technicalEvidence") if isinstance(row, dict) else {}
        ma_dates.extend(_explicit_dates(evidence, ("lastConfirmedDailyDate", "maBasisDate")))

    unique_session = sorted(set(session_dates))
    unique_ma = sorted(set(ma_dates))
    return {
        "snapshotDate": unique_session[0] if len(unique_session) == 1 else None,
        "snapshotDates": unique_session,
        "maBasisDates": unique_ma,
        "snapshotDateStatus": (
            "VERIFIED_EXPLICIT" if len(unique_session) == 1
            else "MISSING" if not unique_session
            else "INCONSISTENT"
        ),
    }


def date_alignment(report_date, indices, full_market):
    """Describe date alignment without treating source timestamps as trading dates."""
    report_compact = compact_date(report_date)
    full_market_available = full_market.get("status") == "AVAILABLE"
    result = {
        "status": "OK" if full_market_available else "UNAVAILABLE",
        "fullMarketStatus": full_market.get("status", "UNAVAILABLE"),
        "fullMarketReason": full_market.get("reason"),
        "reportTradingDate": report_compact,
        "baselineDate": compact_date(full_market.get("asOfDate")),
        "indexSourceDates": [],
        "marketTradingValueSourceDates": [],
        "fullMarketSnapshotDate": full_market.get("snapshotDate"),
        "fullMarketSnapshotDateStatus": full_market.get("snapshotDateStatus", "MISSING"),
        "maBasisDates": full_market.get("maBasisDates", []),
        "mismatches": [],
        "missingEvidence": [],
    }
    result["indexSourceDates"] = sorted({
        date for row in (indices.get("indices") or [])
        if (date := source_date(row.get("sourceTime")))
    })

    if not result["indexSourceDates"]:
        result["missingEvidence"].append("INDEX_SOURCE_DATE")
    elif report_compact and any(date != report_compact for date in result["indexSourceDates"]):
        result["mismatches"].append("REPORT_VS_INDEX_SOURCE_DATE")

    if not full_market_available:
        result["missingEvidence"].append("FULL_MARKET_EVIDENCE")
        result["indexAlignmentStatus"] = (
            "MISMATCH" if "REPORT_VS_INDEX_SOURCE_DATE" in result["mismatches"]
            else "ALIGNED" if result["indexSourceDates"]
            else "UNAVAILABLE"
        )
        return result

    result["marketTradingValueSourceDates"] = sorted({
        date for row in (full_market.get("marketTradingValue") or {}).values()
        if (date := source_date(row.get("sourceTime")))
    })

    if not result["fullMarketSnapshotDate"]:
        result["missingEvidence"].append("FULL_MARKET_TRADING_DATE")
    if not result["marketTradingValueSourceDates"]:
        result["missingEvidence"].append("MARKET_TRADING_VALUE_SOURCE_DATE")
    if not result["maBasisDates"]:
        result["missingEvidence"].append("MA_BASIS_DATE")

    observed = {
        "INDEX_SOURCE_DATE": result["indexSourceDates"],
        "MARKET_TRADING_VALUE_SOURCE_DATE": result["marketTradingValueSourceDates"],
        "FULL_MARKET_TRADING_DATE": [result["fullMarketSnapshotDate"]] if result["fullMarketSnapshotDate"] else [],
        "MA_BASIS_DATE": result["maBasisDates"],
    }
    for label, dates in observed.items():
        if label == "INDEX_SOURCE_DATE":
            continue
        if dates and report_compact and any(date != report_compact for date in dates):
            result["mismatches"].append(f"REPORT_VS_{label}")
    present = [date for dates in observed.values() for date in dates]
    if present and len(set(present)) > 1:
        result["mismatches"].append("SOURCE_DATE_MISMATCH")
    if result["fullMarketSnapshotDateStatus"] == "INCONSISTENT":
        result["mismatches"].append("FULL_MARKET_TRADING_DATE_INCONSISTENT")
    if result["missingEvidence"] or result["mismatches"]:
        result["status"] = "PARTIAL"
    return result


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def full_market_evidence(as_of=None, root=MONITORING_PATH):
    """Read full-market evidence only when its publication contract is valid.

    This is deliberately separate from the monitored-universe inputs.  A
    missing, stale, partial, or mismatched evidence set is unavailable; no
    monitored-universe value is promoted as a full-market estimate.
    """
    root = Path(root)
    try:
        payloads = {name: load_json(root / name) for name in FULL_MARKET_FILES}
    except (OSError, ValueError, json.JSONDecodeError):
        return {"status": "UNAVAILABLE", "reason": "MISSING_EVIDENCE_FILE"}

    quality = payloads["latest-quality.json"]
    expected_date = str(as_of or "").replace("-", "")
    baseline_date = str(quality.get("baselineAsOfDate") or "")
    if quality.get("publicationStatus") not in ("FINAL", "SUCCESS", "COMPLETE_RECONCILIATION_PENDING"):
        return {"status": "UNAVAILABLE", "reason": "QUALITY_NOT_FINAL"}
    date_evidence = full_market_date_evidence(payloads)
    if expected_date and baseline_date != expected_date:
        snapshot_date = date_evidence.get("snapshotDate")
        if (quality.get("baselineFreshnessStatus") != "VALID_PREVIOUS_TRADING_DAY"
                or snapshot_date != expected_date):
            return {"status": "UNAVAILABLE", "reason": "BASELINE_DATE_MISMATCH"}
    if quality.get("currentCount") != quality.get("authoritativeCount"):
        return {"status": "UNAVAILABLE", "reason": "CURRENT_COVERAGE_INVALID"}
    if quality.get("currentCoveragePct") != 100.0:
        return {"status": "UNAVAILABLE", "reason": "CURRENT_COVERAGE_INVALID"}
    if quality.get("duplicateCodes") or quality.get("missingCurrentCodes"):
        return {"status": "UNAVAILABLE", "reason": "CURRENT_QUALITY_INVALID"}

    generated = {payload.get("generatedAt") for payload in payloads.values()
                 if payload.get("generatedAt")}
    if len(generated) > 1:
        return {"status": "UNAVAILABLE", "reason": "EVIDENCE_GENERATED_AT_MISMATCH"}
    breadth = payloads["latest-breadth.json"].get("breadth")
    if not isinstance(breadth, dict) or not isinstance(breadth.get("TOTAL"), dict):
        return {"status": "UNAVAILABLE", "reason": "BREADTH_SCHEMA_INVALID"}

    total = breadth["TOTAL"]
    leaders = payloads["latest-leaders.json"]
    summary = payloads["latest-summary.json"]
    return {
        "status": "AVAILABLE",
        "asOfDate": baseline_date,
        "generatedAt": next(iter(generated), None),
        "sourceTime": quality.get("currentSnapshotGeneratedAt"),
        "snapshotDate": date_evidence.get("snapshotDate"),
        "snapshotDates": date_evidence.get("snapshotDates", []),
        "snapshotDateStatus": date_evidence.get("snapshotDateStatus"),
        "maBasisDates": date_evidence.get("maBasisDates", []),
        "quality": quality,
        "qualityStatus": quality.get("collectionQualityStatus", "UNKNOWN"),
        "breadth": {
            "advancers": total.get("advancers"),
            "decliners": total.get("decliners"),
            "unchanged": total.get("unchanged"),
            "aboveMA20": {"count": total.get("aboveMA20Count"), "eligibleCount": total.get("aboveMA20EligibleCount"), "pct": total.get("pctAboveMA20")},
            "aboveMA60": {"count": total.get("aboveMA60Count"), "eligibleCount": total.get("aboveMA60EligibleCount"), "pct": total.get("pctAboveMA60")},
            "aboveMA120": {"count": total.get("aboveMA120Count"), "eligibleCount": total.get("aboveMA120EligibleCount"), "pct": total.get("pctAboveMA120")},
            "breakout20": {"count": total.get("breakout20Count"), "eligibleCount": total.get("breakout20EligibleCount"), "pct": total.get("pctBreakout20")},
            "breakout60": {"count": total.get("breakout60Count"), "eligibleCount": total.get("breakout60EligibleCount"), "pct": total.get("pctBreakout60")},
            "near52wHigh": {"count": total.get("near52WeekHighCount"),
                            "eligibleCount": total.get("near52WeekHighEligibleCount"),
                            "pct": total.get("pctNear52WeekHigh")},
            "volumeAbove20DayAverage": {"count": total.get("volumeAbove20DayAverageCount"),
                                         "eligibleCount": total.get("volumeAbove20DayAverageEligibleCount"),
                                         "pct": total.get("pctVolumeAbove20DayAverage")},
            "medianReturn1D": total.get("medianReturn1D"),
        },
        "leaders": leaders.get("fullMarketLeaders") or leaders.get("candidates", []),
        "industries": payloads["latest-industries.json"].get("industries", []),
        "changes": payloads["latest-changes.json"],
        "monitoredUniverse": summary.get("monitoredUniverse", {}),
        "marketTradingValue": payloads["latest-breadth.json"].get("fullMarket", {}).get("marketTradingValue", {}),
    }


def resolve_report_trading_date(monitoring_root=MONITORING_PATH,
                                baseline_path=MONITORING_BASELINE_PATH,
                                requested_date=None):
    """Resolve an already-published trading date without using wall-clock date.

    The report must not infer yesterday/weekends or use a baseline date as the
    current session.  A published full-market artifact must carry explicit,
    verified session-date evidence; the baseline is checked separately.
    """
    try:
        quality = load_json(Path(monitoring_root) / "latest-quality.json")
        summary = load_json(Path(monitoring_root) / "latest-summary.json")
        baseline = load_json(baseline_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"status": "WAIT", "reason": "TRADING_DATE_MATERIAL_UNAVAILABLE"}

    def compact(value):
        text = str(value or "").replace("-", "")
        return text if len(text) == 8 and text.isdigit() else None

    quality_date = compact(quality.get("tradingDate"))
    summary_full_market = summary.get("fullMarket") if isinstance(summary, dict) else {}
    summary_date = compact(summary_full_market.get("tradingDate"))
    evidence = summary_full_market.get("tradingDateEvidence") or {}
    evidence_reason = evidence.get("reason")
    baseline_date = compact(baseline.get("asOfDate"))
    requested = compact(requested_date) if requested_date else None
    if not summary_date or evidence_reason not in {
        "VERIFIED_CANONICAL_SESSION",
        "VERIFIED_MONITORING_DAILY_FINAL_SESSION",
    }:
        return {"status": "WAIT", "reason": "TRADING_DATE_EVIDENCE_UNAVAILABLE",
                "publishedDate": summary_date, "evidenceReason": evidence_reason,
                "baselineDate": baseline_date}
    if quality_date and quality_date != summary_date:
        return {"status": "WAIT", "reason": "TRADING_DATE_MISMATCH",
                "qualityDate": quality_date, "publishedDate": summary_date,
                "baselineDate": baseline_date}
    if requested and requested != summary_date:
        return {"status": "WAIT", "reason": "REQUESTED_DATE_MISMATCH",
                "requestedDate": requested, "availableDate": summary_date}
    if quality.get("publicationStatus") not in ("FINAL", "SUCCESS"):
        return {"status": "WAIT", "reason": "QUALITY_NOT_PUBLISHED",
                "publicationStatus": quality.get("publicationStatus")}
    if baseline.get("historyStatus") not in (None, "OK"):
        return {"status": "WAIT", "reason": "BASELINE_NOT_READY",
                "historyStatus": baseline.get("historyStatus")}
    return {"status": "READY", "tradingDate": f"{summary_date[:4]}-{summary_date[4:6]}-{summary_date[6:]}",
            "source": "monitoring-full-market-evidence-and-baseline",
            "baselineDate": baseline_date,
            "tradingDateEvidenceReason": evidence_reason,
            "qualityGeneratedAt": quality.get("generatedAt"),
            "baselineGeneratedAt": baseline.get("generatedAt")}


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

    if quote_status != "ok" or quote_fresh is not True:
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
    snapshot_status = full_market.get("snapshotDateStatus", "MISSING")
    verified = (
        evidence_status == "AVAILABLE"
        and alignment_status == "OK"
        and snapshot_status == "VERIFIED_EXPLICIT"
        and bool(full_market.get("snapshotDate"))
    )
    if verified:
        status = "AVAILABLE"
        reason = None
    elif evidence_status == "AVAILABLE":
        status = "PARTIAL"
        reason = "DATE_ALIGNMENT_NOT_VERIFIED"
    else:
        status = "UNAVAILABLE"
        reason = full_market.get("reason") or "FULL_MARKET_EVIDENCE_UNAVAILABLE"

    quality = full_market.get("quality") if verified else {}
    industries = full_market.get("industries", []) if verified else []
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
        "baselineDate": full_market.get("asOfDate"),
        "snapshotDateStatus": snapshot_status,
        "sourceTime": full_market.get("sourceTime") if verified else None,
        "generatedAt": full_market.get("generatedAt") if verified else None,
        "qualityStatus": full_market.get("qualityStatus") if verified else None,
        "quality": quality,
        "breadth": full_market.get("breadth") if verified else None,
        "marketTradingValue": full_market.get("marketTradingValue", {}) if verified else {},
        "industries": industries,
        "industrySortBasis": "AVG_RETURN_1D_DESC_NULLS_LAST",
        "leaders": full_market.get("leaders", []) if verified else [],
        "mismatches": alignment.get("mismatches", []),
        "missingEvidence": alignment.get("missingEvidence", []),
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
                 discovery_clock=None):
    universe = load_json(UNIVERSE_PATH)
    quotes = load_json(QUOTES_PATH)
    technicals = load_json(TECHNICALS_PATH)
    states = load_json(STATES_PATH)
    research = load_json(RESEARCH_PATH)
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

    group_states = {}
    if GROUP_STATES_PATH.exists():
        group_states = load_json(GROUP_STATES_PATH)

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
        t = tech_idx.get(code, {})
        s = state_idx.get(code, {})
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
    full_market = full_market_evidence(report_date)
    alignment = date_alignment(report_date, indices, full_market)
    discovery = trading_discovery.discovery_status(report_date, TRADING_DISCOVERY_PATH)
    try:
        full_market_signals = load_json(FULL_MARKET_SIGNALS_PATH)
    except (OSError, ValueError, json.JSONDecodeError):
        full_market_signals = {}
    try:
        theme_signals = load_json(MONITORING_PATH / "latest-theme-signals.json")
    except (OSError, ValueError, json.JSONDecodeError):
        theme_signals = {"status": "UNAVAILABLE", "themes": []}
    try:
        monitoring_baseline = load_json(MONITORING_BASELINE_PATH)
    except (OSError, ValueError, json.JSONDecodeError):
        monitoring_baseline = {}
    try:
        previous_report = load_json(PREVIOUS_REPORT_PATH)
        previous_discovery = previous_report.get("kiwoomPreviousCandidates", {})
    except (OSError, ValueError, json.JSONDecodeError, AttributeError):
        previous_discovery = {}
    supplemental_quotes = {"datas": []}
    lookup = {"networkCalls": 0, "requestedSymbols": [], "datas": [], "errors": []}
    published_supplemental = trading_discovery.supplemental_quotes_status(
        discovery, quotes.get("sourceTime"), TRADING_DISCOVERY_QUOTES_PATH)
    supplemental_quotes = {"datas": [
        {**row, "_discoveryAdditionalLookup": True}
        for row in published_supplemental.get("datas", [])
    ]}
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
    }
    kiwoom_section = trading_discovery.analyze(
        report_date, discovery, discovery_quotes, technicals, states,
        full_market_signals, previous_discovery, monitoring_baseline,
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

    report_status = "OK" if coverage_state == "OK" else "DEGRADED"
    if coverage_state == "EMPTY_CONFIGURED_UNIVERSE" or alignment["status"] != "OK":
        report_status = "PARTIAL"
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
            },
            "technicals": {
                "path": "data/technicals.json",
                "generatedAt": technicals.get("generatedAt"),
                "status": technicals.get("status"),
            },
            "states": {
                "path": "data/states.json",
                "generatedAt": states.get("generatedAt"),
                "status": states.get("status"),
            },
            "researchIntelligence": {
                "path": "data/research/intelligence/latest.json",
                "asOf": research.get("asOf"),
                "status": research.get("status"),
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
        "marketSummary": {
            "fullMarket": full_market_summary_data,
            "monitoredUniverse": monitored_summary,
            "breadth": breadth,
            "indices": indices,
            "marketTradingValue": full_market_summary_data["marketTradingValue"],
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
        "groupSummary": groups,
        "technicalEvents": technical_events(rows),
        "technicalEventsCurrent": technical_events(rows, "currentState"),
        "technicalEventsRegularSession": technical_events(
            rows, "regularSessionState"
        ),
        "nextSessionCandidates": next_session_candidates(rows, report_date),
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
    if alignment.get("status") != "OK":
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
            f"- 기준일: {full_summary.get('tradingDate') or '확인 불가'}; "
            f"Baseline 기준일: {full_summary.get('baselineDate') or '확인 불가'}",
            f"- 기준시각: {full_summary.get('sourceTime') or '확인 불가'}; "
            f"자료 품질: {full_summary.get('qualityStatus') or '확인 불가'}",
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
        for market in ("KOSPI", "KOSDAQ"):
            value = (full_summary.get("marketTradingValue") or {}).get(market) or {}
            amount = value.get("value")
            source_time = value.get("sourceTime") or "확인 불가"
            available_count = value.get("availableCount", "확인 불가")
            observed_count = value.get("observedCount", "확인 불가")
            lines.append(
                f"- {market} 전체시장 거래대금: {amount:,.0f}원 "
                f"(확보 {available_count}/{observed_count}종목, 기준시각 {source_time})"
                if isinstance(amount, (int, float))
                else f"- {market} 전체시장 거래대금: 확인 불가 "
                f"(확보 {available_count}/{observed_count}종목, 기준시각 {source_time})"
            )
        industries = full_summary.get("industries") or []
        lines.append(f"- 업종 분석: {len(industries)}개 업종")
        lines.append("- 업종 정렬: 1일 평균 수익률 내림차순(값 미확인 업종은 후순위)")
        for industry in industries[:5]:
            name = industry.get("name") or industry.get("id") or "이름 미확인"
            average = industry.get("avgReturn1D")
            relative = industry.get("relativeStrengthVsMarket")
            advance = industry.get("advancePct")
            turnover = industry.get("tradingValue") or {}
            total_turnover = turnover.get("total")
            average_text = f"1일 평균 {average:+.2f}%" if isinstance(average, (int, float)) else "1일 평균 확인 불가"
            relative_text = f"상대강도 {relative:+.2f}" if isinstance(relative, (int, float)) else "상대강도 확인 불가"
            advance_text = f"상승 비율 {advance:.2f}%" if isinstance(advance, (int, float)) else "상승 비율 확인 불가"
            turnover_text = (
                f"거래대금 {total_turnover:,.0f}원(확보 {turnover.get('availableCount', 0)}/{industry.get('memberCount', '확인 불가')})"
                if isinstance(total_turnover, (int, float))
                else "거래대금 확인 불가"
            )
            lines.append(
                f"  - {name}: {average_text}, {relative_text}, {advance_text}, "
                f"{turnover_text}, 상태 {korean_status(industry.get('persistence', {}).get('status'))}"
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
    lines.append("- V2 판정 근거: 상대강도·상승 확산·MA20·거래대금·지속성의 실제값과 각 항목 상태를 원본 그대로 표시합니다.")
    lines.append("- V2 세부 근거는 생성된 JSON의 `v1V2Analysis.v2Shadow.*.rows`에서 확인할 수 있습니다.")
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

    full_market = report.get("fullMarket", {})
    lines.extend(["", "## 1-A. Full-market evidence", ""])
    if full_market.get("status") != "AVAILABLE":
        lines.append(f"- 상태: UNAVAILABLE ({full_market.get('reason', 'UNKNOWN')})")
    else:
        lines.append(f"- 수집 품질: {full_market.get('qualityStatus', 'UNKNOWN')}")
        fb = full_market["breadth"]
        lines.extend([
            f"- 기준일: {full_market['asOfDate']}",
            f"- 분석 대상/기준 Universe: {full_market.get('quality', {}).get('currentCount', 'UNKNOWN')}/"
            f"{full_market.get('quality', {}).get('authoritativeCount', 'UNKNOWN')}종목",
            f"- 상승/하락/보합: {fb['advancers']}/{fb['decliners']}/{fb['unchanged']}",
            f"- MA20 위: {fb['aboveMA20']['count']} ({fb['aboveMA20']['pct']}%)",
            f"- MA60 위: {fb['aboveMA60']['count']} ({fb['aboveMA60']['pct']}%)",
            f"- MA120 위: {fb['aboveMA120']['count']} ({fb['aboveMA120']['pct']}%)",
            f"- 20일 돌파: {fb['breakout20']['count']} ({fb['breakout20']['pct']}%)",
            f"- 52주 고점 근접: {fb['near52wHigh']['count']} ({fb['near52wHigh']['pct']}%)",
            f"- 거래량 확산: {fb['volumeAbove20DayAverage']['count']} ({fb['volumeAbove20DayAverage']['pct']}%)",
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
    args = parser.parse_args()

    report = build_report(args.as_of, fetch_discovery_missing=args.fetch_discovery_missing)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    latest_path = out_dir / "latest.json"
    dated_json = out_dir / f"{report['asOf']}.json"
    dated_md = out_dir / f"{report['asOf']}.md"

    payload = json.dumps(
        report,
        ensure_ascii=False,
        indent=2,
    ) + "\n"

    latest_path.write_text(payload, encoding="utf-8")
    dated_json.write_text(payload, encoding="utf-8")
    write_markdown(report, dated_md)

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
                "output": str(latest_path),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
