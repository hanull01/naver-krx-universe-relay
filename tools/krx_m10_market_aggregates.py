"""Deterministic market aggregates from one verified KRX M10 generation.

This module only uses fields present in ``monitoring_interchange.json``.  It
does not import NAVER history, manufacture unavailable indicators, or treat a
candidate ranking as a confirmed leader list.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any, Mapping


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


def safe_number(value: Any) -> int | float | None:
    """Convert numeric strings without rounding; preserve null as null."""
    number = _decimal(value)
    if number is None:
        return None
    if number == number.to_integral_value():
        return int(number)
    result = float(number)
    return result if isfinite(result) else None


def _pct(count: int, eligible: int) -> float | None:
    if not eligible:
        return None
    return float(Decimal(count) * Decimal(100) / Decimal(eligible))


def _metric(count: int, eligible: int) -> dict[str, Any]:
    return {"count": count, "eligibleCount": eligible, "pct": _pct(count, eligible)}


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _entity_metric(row: Mapping[str, Any], key: str) -> Decimal | None:
    if key.startswith("MA"):
        return _decimal((row.get("MA") or {}).get(key))
    if key.startswith("return_"):
        return _decimal((row.get("returns_pct") or {}).get(key))
    return _decimal(row.get(key))


def _market_bucket(rows: list[Mapping[str, Any]], *, name: str) -> dict[str, Any]:
    closes = [_decimal(row.get("close_regular")) for row in rows]
    returns = [_entity_metric(row, "return_1d_pct") for row in rows]
    above: dict[str, dict[str, int]] = {}
    for window in ("MA20", "MA60", "MA120"):
        eligible = sum(close is not None and ma is not None for close, ma in zip(
            closes, (_entity_metric(row, window) for row in rows)
        ))
        count = sum(
            close is not None and ma is not None and close > ma
            for close, ma in zip(closes, (_entity_metric(row, window) for row in rows))
        )
        above[window] = {"count": count, "eligible": eligible}

    direction_eligible = sum(value is not None for value in returns)
    advancers = sum(value is not None and value > 0 for value in returns)
    decliners = sum(value is not None and value < 0 for value in returns)
    unchanged = sum(value is not None and value == 0 for value in returns)

    volume_ratios = [_entity_metric(row, "volume_ratio_prev20") for row in rows]
    volume_eligible = sum(value is not None for value in volume_ratios)
    volume_increased = sum(value is not None and value > 1 for value in volume_ratios)

    breakout20 = [_bool((row.get("high_breakouts") or {}).get("high_breakout_prev20")) for row in rows]
    breakout60 = [_bool((row.get("high_breakouts") or {}).get("high_breakout_prev60")) for row in rows]
    breakout20_eligible = sum(value is not None for value in breakout20)
    breakout60_eligible = sum(value is not None for value in breakout60)

    candidates = []
    for row, close, ret, volume, ma20, high20 in zip(
        rows,
        closes,
        returns,
        volume_ratios,
        (_entity_metric(row, "MA20") for row in rows),
        breakout20,
    ):
        if ret is None or ret <= 0:
            continue
        above_ma20 = close is not None and ma20 is not None and close > ma20
        if not (above_ma20 or high20 is True or (volume is not None and volume > 1)):
            continue
        candidates.append({
            "code": row.get("code"),
            "name": row.get("name"),
            "category": row.get("category"),
            "return1dPct": safe_number(ret),
            "aboveMA20": above_ma20,
            "highBreakoutPrev20": high20,
            "volumeRatio20": safe_number(volume),
            "status": "OBSERVATION_ONLY",
            "basis": "KRX_RETURN_1D_MA20_BREAKOUT_VOLUME",
        })
    candidates.sort(
        key=lambda row: (
            row.get("return1dPct") is not None,
            row.get("return1dPct") if row.get("return1dPct") is not None else -float("inf"),
            row.get("volumeRatio20") if row.get("volumeRatio20") is not None else -float("inf"),
            str(row.get("code") or ""),
        ),
        reverse=True,
    )

    above_ma = {
        "aboveMA20": _metric(above["MA20"]["count"], above["MA20"]["eligible"]),
        "aboveMA60": _metric(above["MA60"]["count"], above["MA60"]["eligible"]),
        "aboveMA120": _metric(above["MA120"]["count"], above["MA120"]["eligible"]),
    }
    breakout20_metric = _metric(sum(value is True for value in breakout20), breakout20_eligible)
    breakout60_metric = _metric(sum(value is True for value in breakout60), breakout60_eligible)
    volume_metric = _metric(volume_increased, volume_eligible)
    return {
        "market": name,
        "entityCount": len(rows),
        "directionEligibleCount": direction_eligible,
        "advancers": advancers,
        "decliners": decliners,
        "unchanged": unchanged,
        "aboveMA20": above_ma["aboveMA20"],
        "aboveMA60": above_ma["aboveMA60"],
        "aboveMA120": above_ma["aboveMA120"],
        "breakout20": breakout20_metric,
        "breakout60": breakout60_metric,
        "volumeAbove20DayAverage": volume_metric,
        "aboveMA20Count": above_ma["aboveMA20"]["count"],
        "aboveMA20EligibleCount": above_ma["aboveMA20"]["eligibleCount"],
        "pctAboveMA20": above_ma["aboveMA20"]["pct"],
        "aboveMA60Count": above_ma["aboveMA60"]["count"],
        "aboveMA60EligibleCount": above_ma["aboveMA60"]["eligibleCount"],
        "pctAboveMA60": above_ma["aboveMA60"]["pct"],
        "aboveMA120Count": above_ma["aboveMA120"]["count"],
        "aboveMA120EligibleCount": above_ma["aboveMA120"]["eligibleCount"],
        "pctAboveMA120": above_ma["aboveMA120"]["pct"],
        "breakout20Count": breakout20_metric["count"],
        "breakout20EligibleCount": breakout20_metric["eligibleCount"],
        "pctBreakout20": breakout20_metric["pct"],
        "breakout60Count": breakout60_metric["count"],
        "breakout60EligibleCount": breakout60_metric["eligibleCount"],
        "pctBreakout60": breakout60_metric["pct"],
        "volumeAbove20DayAverageCount": volume_metric["count"],
        "volumeAbove20DayAverageEligibleCount": volume_metric["eligibleCount"],
        "pctVolumeAbove20DayAverage": volume_metric["pct"],
        "near52wHigh": {"count": None, "eligibleCount": 0, "pct": None, "status": "UNAVAILABLE_M10_FIELD_MISSING"},
        "newLow": {"count": None, "eligibleCount": 0, "pct": None, "status": "UNAVAILABLE_M10_FIELD_MISSING"},
        "relativeStrength": {
            "status": "UNAVAILABLE_M10_FIELD_MISSING",
            "validCount": 0,
            "eligibleCount": 0,
        },
        "leaderCandidates": candidates[:20],
        "leaderCandidateStatus": "OBSERVATION_ONLY",
    }


def build_m10_market_aggregates(monitoring: Mapping[str, Any]) -> dict[str, Any]:
    """Build stock/ETF-separated aggregates from verified M10 entities."""
    entities = monitoring.get("entities") or []
    instruments = [row for row in entities if row.get("category") in {"stock", "etf"}]
    stock_rows = [row for row in instruments if row.get("category") == "stock"]
    etf_rows = [row for row in instruments if row.get("category") == "etf"]
    total_rows = stock_rows + etf_rows
    index_identities = [
        {
            "code": row.get("code"),
            "name": row.get("name"),
            "category": "index",
            "identity": row.get("identity"),
            "sourceApi": row.get("source_api"),
            "asof": row.get("asof"),
        }
        for row in entities if row.get("category") == "index"
    ]
    index_identities.sort(key=lambda row: str(row.get("identity") or ""))
    markets = {
        "STOCK": _market_bucket(stock_rows, name="STOCK"),
        "ETF": _market_bucket(etf_rows, name="ETF"),
        "TOTAL": _market_bucket(total_rows, name="TOTAL"),
    }
    total = markets["TOTAL"]
    total["marketBreakdown"] = {"STOCK": markets["STOCK"], "ETF": markets["ETF"]}
    return {
        "schemaVersion": "krx-m10-market-aggregate-v1",
        "targetDate": monitoring.get("date"),
        "source": "KRX_M10_GENERATION",
        "sourceSchema": monitoring.get("schema"),
        "historicalIndicatorSource": "KRX",
        "markets": markets,
        "marketBreakdown": {"STOCK": markets["STOCK"], "ETF": markets["ETF"]},
        "indexIdentities": index_identities,
        "indexCount": len(index_identities),
        "indicatorAvailability": {
            "relativeStrength": "UNAVAILABLE_M10_FIELD_MISSING",
            "newLow": "UNAVAILABLE_M10_FIELD_MISSING",
            "marketTradingValue": "UNAVAILABLE_M10_FIELD_MISSING",
            "highBreakoutPrev20": "AVAILABLE",
            "highBreakoutPrev60": "AVAILABLE",
        },
    }
