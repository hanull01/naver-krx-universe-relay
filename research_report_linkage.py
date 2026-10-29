"""Compact, source-backed research links for the report contract.

This module deliberately reads the already published NAVER research snapshot.
It does not collect research data, promote a stock into a universe, or create
numeric forecasts that are not present in the stored source rows.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any, Mapping


FORECAST_UNAVAILABLE_REASON = (
    "보존된 원문에는 매출·영업이익·EPS 수치 전망이 없습니다."
)


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _sort_key(row: Mapping[str, Any]) -> tuple[str, int]:
    try:
        nid = int(str(row.get("nid") or 0))
    except ValueError:
        nid = 0
    return str(row.get("writeDate") or ""), nid


def load_report_rows(reports_dir: str | Path, item_code: str) -> list[dict[str, Any]]:
    """Load one stock's stored rows without making a network request."""
    root = Path(reports_dir)
    rows: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return []
    for path in sorted(root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        for row in payload.get("reports", []) if isinstance(payload, Mapping) else []:
            if str(row.get("itemCode") or "") != str(item_code):
                continue
            nid = str(row.get("nid") or "")
            if nid:
                rows[nid] = dict(row)
    return sorted(rows.values(), key=_sort_key)


def _compact_report(row: Mapping[str, Any]) -> dict[str, Any]:
    analysis = row.get("analysis") if isinstance(row.get("analysis"), Mapping) else {}
    return {
        "nid": row.get("nid"),
        "writeDate": row.get("writeDate"),
        "brokerName": row.get("brokerName"),
        "title": row.get("title"),
        "goalPrice": row.get("goalPrice"),
        "normalizedOpinion": row.get("normalizedOpinion"),
        "previousSameBrokerNid": row.get("previousSameBrokerNid"),
        "goalPriceChange": row.get("goalPriceChange"),
        "opinionChange": row.get("opinionChange"),
        "textSignals": {
            "earningsTextDirection": analysis.get("earningsTextDirection"),
            "targetPriceMentionDirection": analysis.get("targetPriceMentionDirection"),
            "topics": analysis.get("topics") or {},
        },
    }


def _stock_research(code: str, name: str | None, rows: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    if not rows:
        return {
            "itemCode": code,
            "itemName": name,
            "status": "UNAVAILABLE",
            "reason": "해당 종목의 보존된 NAVER 리서치 원문이 없습니다.",
            "reportCount": 0,
            "brokerCount": 0,
            "brokers": [],
            "forecastSignals": {"status": "UNAVAILABLE", "reason": FORECAST_UNAVAILABLE_REASON},
        }

    recent_start = as_of - timedelta(days=30)
    previous_start = recent_start - timedelta(days=30)
    recent = [row for row in rows if recent_start <= (_date(row.get("writeDate")) or date.min) <= as_of]
    previous = [row for row in rows if previous_start <= (_date(row.get("writeDate")) or date.min) < recent_start]
    recent_nids = {str(row.get("nid")) for row in recent}
    by_broker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_broker[str(row.get("brokerName") or "UNKNOWN")].append(row)

    brokers = []
    revision_up = revision_down = opinion_changes = 0
    for broker, broker_rows in sorted(by_broker.items()):
        ordered = sorted(broker_rows, key=_sort_key)
        compact = [_compact_report(row) for row in ordered[-3:]]
        for row in ordered:
            if str(row.get("nid")) not in recent_nids:
                continue
            change = row.get("goalPriceChange")
            if isinstance(change, Mapping):
                amount = change.get("amount")
                if isinstance(amount, (int, float)):
                    revision_up += amount > 0
                    revision_down += amount < 0
            if row.get("opinionChange"):
                opinion_changes += 1
        latest = compact[-1]
        brokers.append({
            "brokerName": broker,
            "reportCount": len(ordered),
            "latest": latest,
            "history": compact,
        })

    latest_targets = [
        broker["latest"].get("goalPrice")
        for broker in brokers
        if isinstance(broker["latest"].get("goalPrice"), (int, float))
    ]
    dispersion = {"status": "UNAVAILABLE", "brokerCount": len(latest_targets)}
    if latest_targets:
        dispersion.update({
            "status": "AVAILABLE" if len(latest_targets) >= 2 else "PARTIAL",
            "min": min(latest_targets),
            "max": max(latest_targets),
            "range": max(latest_targets) - min(latest_targets),
        })

    recent_earnings = [
        (row.get("analysis") or {}).get("earningsTextDirection")
        for row in recent
        if isinstance(row.get("analysis"), Mapping)
    ]
    trend = "STRENGTHENING" if len(recent) > len(previous) or revision_up > revision_down else (
        "WEAKENING" if len(recent) < len(previous) or revision_down > revision_up else "MIXED_OR_STABLE"
    )
    return {
        "itemCode": code,
        "itemName": name or rows[-1].get("itemName"),
        "status": "AVAILABLE",
        "reportCount": len(rows),
        "brokerCount": len(brokers),
        "latestReportDate": rows[-1].get("writeDate"),
        "recentVsPrevious": {
            "recentDays": 30,
            "recentReportCount": len(recent),
            "previousReportCount": len(previous),
            "revisionUp": revision_up,
            "revisionDown": revision_down,
            "opinionChanges": opinion_changes,
            "trend": trend,
        },
        "brokers": brokers,
        "brokerDispersion": dispersion,
        "forecastSignals": {
            "status": "UNAVAILABLE",
            "reason": FORECAST_UNAVAILABLE_REASON,
            "textEarningsDirections": sorted({value for value in recent_earnings if value}),
        },
    }


def _watchlists(universe: Mapping[str, Any]) -> tuple[dict[str, list[str]], list[str]]:
    result: dict[str, list[str]] = {}
    for group, values in (universe.get("watchlists") or {}).items():
        if not isinstance(values, list):
            continue
        codes = sorted({str(value) for value in values if str(value)})
        result[str(group)] = codes
    return result, sorted({code for codes in result.values() for code in codes})


def _v2_rows(v2_latest: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {
        (str(row.get("kind") or kind), str(row.get("id"))): row
        for kind, field in (("industry", "industries"), ("theme", "themes"))
        for row in v2_latest.get(field, [])
        if isinstance(row, Mapping)
        and row.get("v2Status") in {"ACTIVE", "WATCH"}
        and row.get("id") is not None
    }


def _v2_history(v2_history: Mapping[str, Any], kind: str, group_id: str) -> dict[str, Any]:
    rows = []
    dates = set()
    for observation in v2_history.get("observations", []) if isinstance(v2_history, Mapping) else []:
        if not isinstance(observation, Mapping) or not observation.get("tradingDate"):
            continue
        for row in observation.get("industries" if kind == "industry" else "themes", []):
            if isinstance(row, Mapping) and str(row.get("id")) == group_id:
                rows.append(row)
                dates.add(str(observation.get("tradingDate")))
    ordered = sorted(rows, key=lambda row: str(row.get("tradingDate") or ""))
    delta = None
    if len(ordered) >= 2 and isinstance(ordered[-1].get("score"), (int, float)) and isinstance(ordered[-2].get("score"), (int, float)):
        delta = round(ordered[-1]["score"] - ordered[-2]["score"], 2)
    return {
        "status": "AVAILABLE" if len(dates) >= 2 else "PARTIAL" if dates else "UNAVAILABLE",
        "availableTradingDays": len(dates),
        "scoreDelta": delta,
        "changeClass": "STRENGTHENING" if delta is not None and delta > 0 else "WEAKENING" if delta is not None and delta < 0 else "PERSISTING_OR_UNAVAILABLE",
    }


def build_research_linkage(
    *,
    universe: Mapping[str, Any],
    v2_latest: Mapping[str, Any],
    v2_history: Mapping[str, Any],
    industries: Mapping[str, Any],
    themes: Mapping[str, Any],
    reports_dir: str | Path,
    manifest: Mapping[str, Any] | None,
    as_of: str,
) -> dict[str, Any]:
    """Build only compact links whose membership and research rows exist."""
    as_of_date = _date(as_of) or date.today()
    watchlists, watch_codes = _watchlists(universe)
    source_commit = ((v2_latest.get("inputEvidence") or {}).get("sourceCommit")
                     if isinstance(v2_latest.get("inputEvidence"), Mapping) else None)
    source_commit_status = ((v2_latest.get("inputEvidence") or {}).get("sourceCommitStatus")
                            if isinstance(v2_latest.get("inputEvidence"), Mapping) else "UNAVAILABLE")
    source_by_kind = {
        "industry": {str(row.get("id")): row for row in industries.get("industries", []) if isinstance(row, Mapping)},
        "theme": {str(row.get("id")): row for row in themes.get("themes", []) if isinstance(row, Mapping)},
    }
    v2_groups = _v2_rows(v2_latest)
    group_links: list[dict[str, Any]] = []
    group_codes: set[str] = set()
    for (kind, group_id), v2_row in sorted(v2_groups.items()):
        source = source_by_kind[kind].get(group_id)
        source_generation = (
            industries.get("generatedAt") if kind == "industry"
            else themes.get("generatedAt")
        )
        expected_generation = v2_latest.get("generatedAt")
        if source_generation and expected_generation and source_generation != expected_generation:
            membership_status = "UNAVAILABLE_SOURCE_GENERATION_MISMATCH"
            members = []
        elif kind == "theme" and isinstance(source, Mapping):
            members = [row for row in source.get("stocks", []) if isinstance(row, Mapping) and row.get("code")]
            membership_status = source.get("membershipStatus") or "AVAILABLE"
        elif kind == "industry" and isinstance(source, Mapping):
            members = [row for row in source.get("leaders", []) if isinstance(row, Mapping) and row.get("code")]
            membership_status = "LEADER_ONLY"
        else:
            members = []
            membership_status = "UNAVAILABLE_MEMBERSHIP_SOURCE"
        codes = sorted({str(row.get("code")) for row in members})
        group_codes.update(codes)
        group_links.append({
            "kind": kind,
            "id": group_id,
            "name": v2_row.get("name") or (source or {}).get("name"),
            "v2Status": v2_row.get("v2Status"),
            "score": v2_row.get("score"),
            "persistenceDays": v2_row.get("persistenceDays"),
            "trend": _v2_history(v2_history, kind, group_id),
            "membershipStatus": membership_status,
            "membershipScope": "full_theme_membership" if kind == "theme" else "leader_candidates_only",
            "memberCount": source.get("memberCount") if isinstance(source, Mapping) else None,
            "memberCodes": codes,
            "sourceGeneration": source_generation,
            "sourceCommit": source_commit,
            "sourceCommitStatus": source_commit_status,
        })

    linked_codes = sorted(set(watch_codes) | group_codes)
    stocks = []
    for code in linked_codes:
        rows = load_report_rows(reports_dir, code)
        item_name = rows[-1].get("itemName") if rows else None
        groups = [
            {"kind": link["kind"], "id": link["id"], "name": link["name"], "v2Status": link["v2Status"], "score": link["score"], "persistenceDays": link["persistenceDays"], "membershipStatus": link["membershipStatus"]}
            for link in group_links if code in link.get("memberCodes", [])
        ]
        watch_groups = [name for name, codes in watchlists.items() if code in codes]
        stocks.append({
            **_stock_research(code, item_name, rows, as_of_date),
            "v2Groups": groups,
            "manualWatchlists": watch_groups,
        })

    if not watchlists:
        watchlist_status = "AVAILABLE_EMPTY"
    else:
        watchlist_status = "AVAILABLE"
    source_info = {
        "reportsPath": "data/research/reports/*.json",
        "manifestPath": "data/research/manifest.json",
        "generatedAt": (manifest or {}).get("generatedAt") if isinstance(manifest, Mapping) else None,
        "storedReportCount": (manifest or {}).get("storedReportCount") if isinstance(manifest, Mapping) else None,
        "source": "NAVER_STOCK_RESEARCH",
    }
    return {
        "status": "AVAILABLE" if stocks or watchlist_status == "AVAILABLE_EMPTY" else "UNAVAILABLE",
        "asOf": as_of,
        "source": source_info,
        "manualWatchlists": {"status": watchlist_status, "groups": watchlists, "codeCount": len(watch_codes)},
        "v2GroupLinks": group_links,
        "stocks": stocks,
        "methodology": {
            "sameBrokerOnlyForChanges": True,
            "numericForecasts": "UNAVAILABLE",
            "automaticUniversePromotion": False,
            "staticTestSymbolsExcluded": True,
        },
    }
