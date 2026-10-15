"""V2 shadow evaluator for Dynamic Universe.

This module is deliberately side-effect free and is not imported by the V1
publisher.  It consumes already-published monitoring snapshots only.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


WEIGHTS = {"priceRelativeStrength": 30, "breadth": 25, "turnoverParticipation": 20,
           "leaderQuality": 15, "persistence": 10}
STATES = ("ACTIVE", "WATCH", "DISCOVERED", "DEFENSIVE", "WEAK", "WEAKENING", "REMOVED", "WAIT")


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _pct(value: Any) -> Optional[float]:
    value = _num(value)
    return None if value is None else max(0.0, min(100.0, value))


def _weighted(parts: Dict[str, Optional[float]]) -> Tuple[Optional[float], List[str], float]:
    missing = [name for name, value in parts.items() if value is None]
    available_weight = sum(WEIGHTS[name] for name, value in parts.items() if value is not None)
    if not available_weight:
        return None, missing, 0.0
    score = sum(parts[name] * WEIGHTS[name] for name, value in parts.items() if value is not None) / available_weight
    return round(score, 2), missing, available_weight / 100


def _base_state(row: Dict[str, Any]) -> str:
    state = row.get("status") or row.get("state")
    return state if state in STATES else "WAIT"


def _evaluate(row: Dict[str, Any], kind: str) -> Dict[str, Any]:
    metrics = row.get("metrics") if kind == "theme" else row
    metrics = metrics if isinstance(metrics, dict) else {}
    trading = metrics.get("tradingValue") if isinstance(metrics.get("tradingValue"), dict) else {}
    persistence = metrics.get("persistence") if isinstance(metrics.get("persistence"), dict) else {}
    member_count = _num(row.get("memberCount"))
    coverage = _pct(row.get("coveragePct", row.get("featureValidMemberCount", 0) / member_count * 100 if member_count else None))
    stocks = row.get("stocks") if isinstance(row.get("stocks"), list) else []
    valid_stocks = [stock for stock in stocks if isinstance(stock, dict) and _num(stock.get("changeRate", stock.get("ret_1d"))) is not None]
    if kind == "theme" and valid_stocks:
        returns = [_num(stock.get("changeRate", stock.get("ret_1d"))) for stock in valid_stocks]
        advances = sum(value > 0 for value in returns)
        metrics = dict(metrics)
        metrics.setdefault("advancerPct", advances / len(returns) * 100)
        metrics.setdefault("averageReturn1D", sum(returns) / len(returns))
        metrics.setdefault("medianReturn1D", sorted(returns)[len(returns) // 2])
        metrics.setdefault("pctAboveMA20", sum(bool(stock.get("above_ma20")) for stock in valid_stocks) / len(valid_stocks) * 100)
        trading_values = [_num(stock.get("accumulatedTradingValue")) for stock in valid_stocks]
        trading_values = [value for value in trading_values if value is not None]
        if trading_values:
            metrics.setdefault("tradingValue", {"coveragePct": len(trading_values) / len(valid_stocks) * 100,
                                                  "total": sum(trading_values),
                                                  "top3ConcentrationPct": sum(sorted(trading_values, reverse=True)[:3]) / sum(trading_values) * 100})
        trading = metrics.get("tradingValue") if isinstance(metrics.get("tradingValue"), dict) else trading
    advance = _pct(metrics.get("advancePct", metrics.get("advancerPct")))
    rel = _num(metrics.get("relativeStrengthVsMarket"))
    avg = _num(metrics.get("avgReturn1D", metrics.get("averageReturn1D")))
    median = _num(metrics.get("medianReturn1D"))
    above20 = _pct(metrics.get("pctAboveMA20"))
    turnover_cov = _pct(trading.get("coveragePct"))
    leader_count = len(row.get("leaders", [])) if isinstance(row.get("leaders"), list) else None
    if kind == "theme" and valid_stocks:
        # A proxy for leader quality uses only same-snapshot observations and is
        # explicitly labelled as such; it does not alter V1 AUTO_LEADER.
        leader_count = min(3, sum((_num(stock.get("changeRate", stock.get("ret_1d"))) or 0) > 0 and
                                  (_num(stock.get("accumulatedTradingValue")) or 0) > 0 for stock in valid_stocks))
    parts = {
        "priceRelativeStrength": None if rel is None and avg is None else _pct((max(0, min(100, 50 + (rel or avg) * 5)))),
        "breadth": advance,
        "turnoverParticipation": turnover_cov,
        "leaderQuality": None if leader_count is None else min(100.0, leader_count / 3 * 100),
        "persistence": _pct(100 * min(1.0, _num(persistence.get("availableTradingDays")) / 5) if _num(persistence.get("availableTradingDays")) is not None else None),
    }
    score, missing, coverage_weight = _weighted(parts)
    absolute_available = avg is not None and median is not None and advance is not None and above20 is not None
    absolute_strength = bool(absolute_available and avg > 0 and median > 0 and advance >= 50 and above20 >= 50)
    relative_defense = bool(rel is not None and rel > 0 and avg is not None and avg <= 0 and
                            advance is not None and advance < 50 and not absolute_strength)
    small_group = member_count is not None and member_count <= 4
    corrected_score = round(score * (0.75 if small_group else 1.0), 2) if score is not None else None
    core_missing = [name for name in missing if name != "persistence"]
    previous = row.get("previous") if isinstance(row.get("previous"), dict) else None
    worsening = False
    if previous and avg is not None and _num(previous.get("avgReturn1D")) is not None:
        worsening = avg < _num(previous.get("avgReturn1D"))
    if core_missing:
        state = "WAIT"
    elif relative_defense:
        state = "DEFENSIVE"
    elif not absolute_strength and worsening:
        state = "WEAKENING"
    elif not absolute_strength:
        state = "WEAK"
    elif corrected_score is not None and corrected_score >= 70:
        state = "ACTIVE"
    else:
        state = "WATCH"
    persistence_days = _num(persistence.get("availableTradingDays"))
    component_status = {name: ("VALID" if value is not None else "UNAVAILABLE") for name, value in parts.items()}
    if persistence_days is not None and persistence_days < 3:
        component_status["persistence"] = "PARTIAL"
    quality = "UNAVAILABLE" if core_missing else ("PARTIAL" if missing or (persistence_days is not None and persistence_days < 3) else ("LOW_CONFIDENCE_SMALL_GROUP" if small_group else "OK"))
    return {
        "id": row.get("id"), "name": row.get("name"), "kind": kind,
        "v1Status": _base_state(row), "v2Status": state,
        "score": corrected_score, "rawScore": score, "weights": WEIGHTS,
        "components": parts,
        "componentStatus": component_status,
        "missingComponents": missing, "coreMissingComponents": core_missing,
        "availableWeight": round(coverage_weight, 2),
        "absoluteStrength": absolute_strength if absolute_available else None,
        "relativeDefense": relative_defense if rel is not None else None,
        "memberCount": member_count, "coveragePct": coverage,
        "smallGroupCorrection": 0.75 if small_group else 1.0,
        "persistenceDays": persistence.get("availableTradingDays"),
        "dataQuality": quality,
        "trendEvidence": "CONFIRMED_WORSENING" if worsening else ("NO_COMPARABLE_PRIOR" if not previous else "NOT_WORSENING"),
        "watchEligible": bool(absolute_strength and corrected_score is not None and corrected_score >= 70 and quality != "UNAVAILABLE"),
        "watchRank": None,
        "watchReasons": (["absolute strength confirmed", "core metrics available"] if absolute_strength and quality != "UNAVAILABLE" else ["not absolute strength"]),
    }


def evaluate_snapshot(industry_payload: Dict[str, Any], theme_payload: Dict[str, Any], v1_payload: Dict[str, Any]) -> Dict[str, Any]:
    industries = [_evaluate(row, "industry") for row in industry_payload.get("industries", [])]
    themes = [_evaluate(row, "theme") for row in theme_payload.get("themes", [])]
    v1_sections = {str(row.get("id")): row for row in v1_payload.get("sectors", [])}
    v1_themes = {str(row.get("id")): row for row in v1_payload.get("themes", [])}
    for collection, old in ((industries, v1_sections), (themes, v1_themes)):
        for row in collection:
            row["v1Status"] = (old.get(str(row["id"]), {}).get("status") or row["v1Status"])
        ranked = sorted((item for item in collection if item["watchEligible"] and item["score"] is not None),
                        key=lambda item: item["score"], reverse=True)
        for rank, item in enumerate(ranked, 1):
            item["watchRank"] = rank
    return {
        "schemaVersion": "dynamic-universe-v2-shadow-2",
        "generatedAt": industry_payload.get("generatedAt") or theme_payload.get("generatedAt"),
        "source": {"industries": industry_payload.get("generatedAt"), "themes": theme_payload.get("generatedAt")},
        "weights": WEIGHTS,
        "industries": industries, "themes": themes,
        "counts": {"industries": len(industries), "themes": len(themes)},
        "notes": ["V1 is unchanged; this is shadow output only.", "Persistence history is insufficient when availableTradingDays < 3."],
    }


def summarize(result: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for kind in ("industries", "themes"):
        rows = result[kind]
        dist: Dict[str, int] = {}
        diffs = 0
        for row in rows:
            dist[row["v2Status"]] = dist.get(row["v2Status"], 0) + 1
            diffs += row["v1Status"] != row["v2Status"]
        out[kind] = {"count": len(rows), "v2StatusDistribution": dist, "v1V2StatusDifferences": diffs,
                     "unavailable": sum(row["dataQuality"] == "UNAVAILABLE" for row in rows)}
    return out


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    ind = json.loads((root / "data/monitoring/latest-industries.json").read_text())
    theme = json.loads((root / "data/monitoring/latest-theme-signals.json").read_text())
    v1 = json.loads((root / "data/dynamic-universe/latest.json").read_text())
    result = evaluate_snapshot(ind, theme, v1)
    result["summary"] = summarize(result)
    out = root / "data/dynamic-universe/v2-shadow-latest.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
