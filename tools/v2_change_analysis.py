"""Compact, source-aware change analysis for existing V2 shadow history."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _rows(observation: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    result = {}
    for kind, field in (("industry", "industries"), ("theme", "themes")):
        for row in observation.get(field) or []:
            if isinstance(row, Mapping) and row.get("id") is not None:
                result[(kind, str(row["id"]))] = row
    return result


def _members(row: Mapping[str, Any]) -> set[str]:
    values = row.get("memberCodes") or row.get("members") or row.get("stocks") or []
    result = set()
    for value in values:
        if isinstance(value, Mapping):
            value = value.get("code") or value.get("itemCode")
        if value:
            result.add(str(value))
    return result


def _leaders(row: Mapping[str, Any]) -> set[str]:
    values = row.get("leaderCandidates") or row.get("leaders") or []
    return {str(item.get("code") or item.get("itemCode")) for item in values if isinstance(item, Mapping) and (item.get("code") or item.get("itemCode"))}


def _value(row: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        value = row.get(name)
        if value is not None:
            return value
    metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
    for name in names:
        if metrics.get(name) is not None:
            return metrics[name]
    return None


def compare_v2_changes(history: Mapping[str, Any], *, current: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    observations = [row for row in history.get("observations") or [] if isinstance(row, Mapping) and row.get("tradingDate") and row.get("dateQuality", "VERIFIED") != "MISMATCH"]
    by_date: dict[str, Mapping[str, Any]] = {}
    for row in sorted(observations, key=lambda item: (str(item.get("tradingDate")), str(item.get("observedAt")))):
        by_date[str(row["tradingDate"])] = row
    dates = sorted(by_date)
    if current is not None and current.get("tradingDate"):
        current_date = str(current["tradingDate"])
        current_obs = current
    elif dates:
        current_date, current_obs = dates[-1], by_date[dates[-1]]
    else:
        return []
    previous = by_date.get(dates[-2]) if len(dates) >= 2 and dates[-1] == current_date else (by_date.get(dates[-1]) if dates else None)
    if previous is current_obs and len(dates) >= 2:
        previous = by_date[dates[-2]]
    current_rows = _rows(current_obs)
    previous_rows = _rows(previous or {})
    keys = sorted(set(current_rows) | set(previous_rows))
    output = []
    for key in keys:
        now = current_rows.get(key)
        old = previous_rows.get(key)
        source_verified = all(isinstance(row.get("sourceProvenance"), Mapping) and row["sourceProvenance"].get("sourceCommit") for row in (current_obs, previous or {}))
        if now is None:
            change = "EXITED"
        elif old is None:
            change = "NEW"
        else:
            old_status, new_status = old.get("v2Status"), now.get("v2Status")
            old_score, new_score = old.get("score"), now.get("score")
            delta = round(new_score - old_score, 2) if isinstance(old_score, (int, float)) and isinstance(new_score, (int, float)) else None
            change = "STATUS_CHANGED" if old_status != new_status else "STRENGTHENING" if delta is not None and delta > 0 else "WEAKENING" if delta is not None and delta < 0 else "PERSISTING"
        if not source_verified:
            official = "COMPARISON_UNAVAILABLE"
        else:
            official = change
        output.append({"kind": key[0], "id": key[1], "name": (now or old).get("name"),
                       "changeClass": change, "officialChangeClass": official,
                       "status": "AVAILABLE" if source_verified else "UNVERIFIED_SOURCE",
                       "baseTradingDate": previous.get("tradingDate") if previous else None, "currentTradingDate": current_date,
                       "scoreDelta": (round(now.get("score") - old.get("score"), 2) if now and old and isinstance(now.get("score"), (int, float)) and isinstance(old.get("score"), (int, float)) else None),
                       "relativeStrengthDelta": _delta(old, now, ("relativeStrengthVsMarket", "relativeStrength")),
                       "breadthDelta": _delta(old, now, ("advancePct", "breadthPct")),
                       "tradingValueConcentrationDelta": _delta(old, now, ("top3ConcentrationPct", "tradingValueConcentrationPct")),
                       "persistenceDays": (now or old).get("persistenceDays"), "membershipChanged": _members(now or {}) != _members(old or {}),
                       "leaderChanged": _leaders(now or {}) != _leaders(old or {}),
                       "sourceCommitStatus": "VERIFIED" if source_verified else "UNVERIFIED"})
    return output


def _delta(old: Mapping[str, Any] | None, now: Mapping[str, Any] | None, names: tuple[str, ...]) -> float | None:
    a, b = _value(old or {}, names), _value(now or {}, names)
    return round(b - a, 6) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
