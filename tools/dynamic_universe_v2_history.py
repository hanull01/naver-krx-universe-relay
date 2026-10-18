"""Local, opt-in history for Dynamic Universe V2 shadow observations.

This file is intentionally not imported by the V1 publisher or any workflow.
It records the exact V2 output and source snapshot identity so future
1/3/5-trading-day evaluations cannot use a later membership list by accident.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _sha256(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _observation_id(result: Dict[str, Any], trading_date: str, observed_at: str) -> str:
    # observedAt is intentionally excluded: rerunning the same published
    # snapshot on the same trading day must be idempotent.
    return _sha256({"tradingDate": trading_date, "source": result.get("source"),
                    "generatedAt": result.get("generatedAt"), "schema": result.get("schemaVersion")})[:24]


def make_observation(result: Dict[str, Any], trading_date: str, observed_at: Optional[str] = None) -> Dict[str, Any]:
    observed_at = observed_at or datetime.now(timezone.utc).isoformat()
    observation = {
        "observationId": _observation_id(result, trading_date, observed_at),
        "tradingDate": trading_date,
        "observedAt": observed_at,
        "schemaVersion": result.get("schemaVersion"),
        "source": result.get("source"),
        "sourceSnapshotHash": _sha256({"source": result.get("source"), "generatedAt": result.get("generatedAt")}),
        "generatedAt": result.get("generatedAt"),
        "industries": result.get("industries", []),
        "themes": result.get("themes", []),
    }
    generated = result.get("generatedAt")
    if isinstance(generated, str) and len(generated) >= 10 and generated[4] == "-" and generated[7] == "-":
        observation["sourceTradingDate"] = generated[:10]
        observation["dateQuality"] = "VERIFIED" if trading_date == generated[:10] else "MISMATCH"
    return observation


def append_observation(history: Dict[str, Any], observation: Dict[str, Any]) -> Dict[str, Any]:
    rows = list(history.get("observations", []))
    if not any(item.get("observationId") == observation["observationId"] or
               (item.get("tradingDate") == observation.get("tradingDate") and
                item.get("sourceSnapshotHash") == observation.get("sourceSnapshotHash")) for item in rows):
        rows.append(observation)
    rows.sort(key=lambda item: (item.get("tradingDate", ""), item.get("observedAt", "")))
    return {"schemaVersion": "dynamic-universe-v2-shadow-history-1",
            "observations": rows,
            "notes": ["Same-day observations are retained but never count as separate trading days.",
                      "Future evaluation requires an exact later observation with compatible source identity."]}


def unique_trading_dates(history: Dict[str, Any]) -> List[str]:
    return sorted({item.get("tradingDate") for item in history.get("observations", [])
                   if item.get("tradingDate") and item.get("dateQuality") != "MISMATCH"})


def evaluate_horizons(history: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Prepare only evaluable horizon pairs; never invent future returns."""
    rows = history.get("observations", [])
    dates = unique_trading_dates(history)
    output: List[Dict[str, Any]] = []
    for base_date in dates:
        for horizon in (1, 3, 5):
            if len(dates) <= dates.index(base_date) + horizon:
                continue
            future_date = dates[dates.index(base_date) + horizon]
            base = next(item for item in rows if item.get("tradingDate") == base_date)
            future = next(item for item in rows if item.get("tradingDate") == future_date)
            base_by_id = {str(item.get("id")): item for item in base.get("industries", []) + base.get("themes", [])}
            future_by_id = {str(item.get("id")): item for item in future.get("industries", []) + future.get("themes", [])}
            matches = []
            for item_id, item in base_by_id.items():
                later = future_by_id.get(item_id)
                if later is None or item.get("watchEligible") is not True:
                    continue
                matches.append({"id": item_id, "kind": item.get("kind"),
                                "baseStatus": item.get("v2Status"), "futureStatus": later.get("v2Status"),
                                "baseScore": item.get("score"), "futureScore": later.get("score"),
                                "baseSourceSnapshotHash": base.get("sourceSnapshotHash"),
                                "futureSourceSnapshotHash": future.get("sourceSnapshotHash")})
            output.append({"baseTradingDate": base_date, "futureTradingDate": future_date,
                           "horizonTradingDays": horizon, "evaluated": matches,
                           "status": "EVALUATED" if matches else "NO_ELIGIBLE_BASE_ITEMS"})
    return output


def record_current(root: Path, trading_date: str) -> Dict[str, Any]:
    result = json.loads((root / "data/dynamic-universe/v2-shadow-latest.json").read_text())
    path = root / "data/dynamic-universe/v2-shadow-history.json"
    history = json.loads(path.read_text()) if path.exists() else {}
    updated = append_observation(history, make_observation(result, trading_date))
    updated["horizonEvaluations"] = evaluate_horizons(updated)
    path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    return updated


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    result = record_current(root, "2026-10-15")
    print(json.dumps({"observations": len(result["observations"]),
                      "uniqueTradingDates": unique_trading_dates(result),
                      "horizonEvaluations": len(result["horizonEvaluations"])}, ensure_ascii=False))
