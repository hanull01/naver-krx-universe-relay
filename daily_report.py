#!/usr/bin/env python3

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
UNIVERSE_PATH = ROOT / "config" / "universe.json"

QUOTES_PATH = DATA / "quotes.json"
TECHNICALS_PATH = DATA / "technicals.json"
STATES_PATH = DATA / "states.json"
GROUP_STATES_PATH = DATA / "group-states.json"
RESEARCH_PATH = DATA / "research" / "intelligence" / "latest.json"
MONITORING_PATH = DATA / "monitoring"
FULL_MARKET_FILES = (
    "latest-breadth.json",
    "latest-stock-signals.json",
    "latest-industries.json",
    "latest-leaders.json",
    "latest-changes.json",
    "latest-quality.json",
    "latest-summary.json",
)


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
    if quality.get("publicationStatus") not in ("FINAL", "SUCCESS"):
        return {"status": "UNAVAILABLE", "reason": "QUALITY_NOT_FINAL"}
    if expected_date and baseline_date != expected_date:
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
    return {
        "status": "AVAILABLE",
        "asOfDate": baseline_date,
        "generatedAt": next(iter(generated), None),
        "sourceTime": quality.get("currentSnapshotGeneratedAt"),
        "quality": quality,
        "breadth": {
            "advancers": total.get("advancers"),
            "decliners": total.get("decliners"),
            "unchanged": total.get("unchanged"),
            "aboveMA20": {"count": total.get("aboveMA20Count"), "eligibleCount": total.get("aboveMA20EligibleCount"), "pct": total.get("pctAboveMA20")},
            "aboveMA60": {"count": total.get("aboveMA60Count"), "eligibleCount": total.get("aboveMA60EligibleCount"), "pct": total.get("pctAboveMA60")},
            "aboveMA120": {"count": total.get("aboveMA120Count"), "eligibleCount": total.get("aboveMA120EligibleCount"), "pct": total.get("pctAboveMA120")},
            "breakout20": {"count": total.get("breakout20Count"), "eligibleCount": total.get("breakout20EligibleCount"), "pct": total.get("pctBreakout20")},
            "breakout60": {"count": total.get("breakout60Count"), "eligibleCount": total.get("breakout60EligibleCount"), "pct": total.get("pctBreakout60")},
            "near52wHigh": total.get("pctNear52WeekHigh"),
            "medianReturn1D": total.get("medianReturn1D"),
        },
        "leaders": leaders.get("fullMarketLeaders") or leaders.get("candidates", []),
        "industries": payloads["latest-industries.json"].get("industries", []),
        "changes": payloads["latest-changes.json"],
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


def market_breadth(rows):
    """Summarize only observed market/state values; absent values stay absent."""
    observed = [row for row in rows if row.get("marketObserved")]
    directions = [row["market"]["changeRate"] for row in observed]
    directions = [value for value in directions if value is not None]

    ma20_observed = [
        row for row in observed
        if row["state"].get("priceVsMA20") is not None
    ]
    ma60_observed = [
        row for row in observed
        if row["state"].get("priceVsMA60") is not None
    ]

    def state_count(key, value):
        return sum(row["state"].get(key) == value for row in observed)

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
            str(row["state"].get("pullbackState", "")).startswith("pullback")
            for row in observed
        ),
        "nearBreakoutCount": sum(
            str(row["state"].get("pullbackState", "")).startswith("near_breakout")
            for row in observed
        ),
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
    ]
    return [
        {field: group.get(field) for field in fields}
        for group in groups
        if isinstance(group, dict)
    ]


def technical_events(rows):
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
        name: [event_stock(row) for row in rows if predicate(row["state"])]
        for name, predicate in predicates.items()
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


def build_report(as_of=None):
    universe = load_json(UNIVERSE_PATH)
    quotes = load_json(QUOTES_PATH)
    technicals = load_json(TECHNICALS_PATH)
    states = load_json(STATES_PATH)
    research = load_json(RESEARCH_PATH)

    group_states = {}
    if GROUP_STATES_PATH.exists():
        group_states = load_json(GROUP_STATES_PATH)

    quote_idx = index_rows(quotes)
    tech_idx = index_rows(technicals)
    state_idx = index_rows(states)
    research_idx = research_index(research)

    enabled = enabled_universe(universe)
    member_idx = memberships(universe)

    rows = []

    for item in enabled:
        code = item["itemCode"]

        q = quote_summary(quote_idx.get(code))
        t = tech_idx.get(code, {})
        s = state_idx.get(code, {})
        r = research_idx.get(code, {})

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
    groups = normalized_group_summary(group_states)
    research_overview = research_summary(rows, research)

    return {
        "status": "OK" if coverage_state == "OK" else "DEGRADED",
        "coverageStatus": coverage_state,
        "version": "daily-market-research-report-v2",
        "asOf": report_date,
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
            "breadth": breadth,
            "topGainers": [
                {
                    "itemCode": row["itemCode"],
                    "itemName": row["itemName"],
                    "changeRate": row["market"]["changeRate"],
                    "price": row["market"]["price"],
                }
                for row in market_movers[:10]
            ],
            "topDecliners": [
                {
                    "itemCode": row["itemCode"],
                    "itemName": row["itemName"],
                    "changeRate": row["market"]["changeRate"],
                    "price": row["market"]["price"],
                }
                for row in reversed(market_movers[-10:])
            ],
        },
        "monitoredUniverse": {
            "breadth": breadth,
            "scope": "MONITORED_UNIVERSE",
        },
        "fullMarket": full_market,
        "groupSummary": groups,
        "technicalEvents": technical_events(rows),
        "researchSummary": research_overview,
        "marketResearchCross": market_research_cross(rows),
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
    degraded = report["status"] == "DEGRADED"
    lines = [
        f"# 국내증시 일일 종합 리포트 - {report['asOf']}",
        "",
        f"- 생성시각: {report['generatedAt']}",
        f"- Universe: {report['coverage']['enabledStockCount']}종목",
    ]
    if degraded:
        lines.extend([
            "",
            f"> **데이터 품질 경고:** {report['coverageStatus']} 상태입니다. "
            "아래 내용은 불완전하거나 오래된 시장 데이터를 포함할 수 있습니다.",
        ])

    lines.extend([
        "",
        "## 1. 시장 요약",
        "",
        f"- 관측 종목: {breadth['observedCount']}종목",
        f"- 상승/하락/보합: {breadth['upCount']}/{breadth['downCount']}/{breadth['flatCount']}",
        f"- MA20 위/아래: {breadth['ma20AboveCount']}/{breadth['ma20BelowCount']}",
        f"- MA60 위/아래: {breadth['ma60AboveCount']}/{breadth['ma60BelowCount']}",
        f"- 20일 돌파 확인/시도/실패: {breadth['breakout20ConfirmedCount']}/{breadth['breakout20AttemptCount']}/{breadth['breakout20FailedCount']}",
        f"- 거래량 급증/증가: {breadth['volumeSurgeCount']}/{breadth['volumeElevatedCount']}",
        "",
        "### 등락 상위",
        "",
    ])

    for row in report["marketSummary"]["topGainers"][:5]:
        lines.append(
            f"- {row['itemName']} ({row['itemCode']}): "
            f"{row['changeRate']:+.2f}%"
        )

    lines.extend(
        [
            "",
            "### 등락 하위",
            "",
        ]
    )

    for row in report["marketSummary"]["topDecliners"][:5]:
        lines.append(
            f"- {row['itemName']} ({row['itemCode']}): "
            f"{row['changeRate']:+.2f}%"
        )

    full_market = report.get("fullMarket", {})
    lines.extend(["", "## 1-A. Full-market evidence", ""])
    if full_market.get("status") != "AVAILABLE":
        lines.append(f"- 상태: UNAVAILABLE ({full_market.get('reason', 'UNKNOWN')})")
    else:
        fb = full_market["breadth"]
        lines.extend([
            f"- 기준일: {full_market['asOfDate']}",
            f"- 상승/하락/보합: {fb['advancers']}/{fb['decliners']}/{fb['unchanged']}",
            f"- MA20 위: {fb['aboveMA20']['count']} ({fb['aboveMA20']['pct']}%)",
            f"- MA60 위: {fb['aboveMA60']['count']} ({fb['aboveMA60']['pct']}%)",
            f"- MA120 위: {fb['aboveMA120']['count']} ({fb['aboveMA120']['pct']}%)",
            f"- 20일 돌파: {fb['breakout20']['count']} ({fb['breakout20']['pct']}%)",
            f"- 52주 고점 근접: {fb['near52wHigh']}%",
            f"- Full-market leaders: {len(full_market['leaders'])}종목",
        ])

    lines.extend(
        [
            "",
            "## 2. 섹터·그룹 확산",
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

    lines.extend(["", "## 3. 기술 이벤트", ""])
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
        stocks = report["technicalEvents"][key]
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
        "--output-dir",
        default="data/reports",
    )
    args = parser.parse_args()

    report = build_report(args.as_of)

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
