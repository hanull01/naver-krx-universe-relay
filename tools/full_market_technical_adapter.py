"""Read-only bridge from staged full-market indicators to report inputs.

This module is deliberately opt-in.  It reads only the supplied staged
``technicals.json``/``states.json`` files and projects the enabled registered
Universe from those same rows.  Current-market observations are accepted as a
separate payload; they are never reconstructed from historical staging data.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping


DATE_RE = re.compile(r"^\d{8}$")
MARKET_CODE_RE = re.compile(r"^[A-Z0-9]{6}$")
FORBIDDEN_FALLBACK_MARKERS = (
    "/data/daily/",
    "/data/technicals.json",
    "/data/states.json",
    "/data/group-states.json",
)


class FullMarketTechnicalAdapterError(ValueError):
    """Raised when staged report input is missing or unsafe."""


def _date(value: Any, field: str) -> str:
    text = str(value or "").replace("-", "")
    if not DATE_RE.fullmatch(text):
        raise FullMarketTechnicalAdapterError(f"invalid {field}: {value!r}")
    return text


def _read_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FullMarketTechnicalAdapterError(f"cannot read staged input: {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FullMarketTechnicalAdapterError(f"staged input must be an object: {source}")
    return payload


def _validate_provenance(row: Mapping[str, Any], expected_source: str | None) -> None:
    provenance = row.get("provenance")
    if not isinstance(provenance, Mapping):
        raise FullMarketTechnicalAdapterError(f"{row.get('itemCode')}: provenance missing")
    source_path = str(provenance.get("sourceCanonicalPath") or "")
    source_staging = str(provenance.get("sourceStaging") or "")
    if any(marker in source_path for marker in FORBIDDEN_FALLBACK_MARKERS):
        raise FullMarketTechnicalAdapterError(
            f"{row.get('itemCode')}: legacy fallback provenance is forbidden: {source_path}"
        )
    if expected_source and source_staging != expected_source:
        raise FullMarketTechnicalAdapterError(
            f"{row.get('itemCode')}: source staging mismatch: {source_staging}"
        )
    if not source_path or not str(provenance.get("sourceCanonicalSha256") or ""):
        raise FullMarketTechnicalAdapterError(f"{row.get('itemCode')}: incomplete provenance")


def _load_rows(path: str | Path, expected_source: str | None = None) -> dict[str, Any]:
    payload = _read_json(path)
    target = _date(payload.get("targetDate"), "targetDate")
    if target == "20261030":
        raise FullMarketTechnicalAdapterError("20261030 is not allowed in staged report input")
    rows = payload.get("datas")
    if not isinstance(rows, list):
        raise FullMarketTechnicalAdapterError(f"datas must be a list: {path}")
    by_code: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise FullMarketTechnicalAdapterError(f"non-object row in {path}")
        code = str(row.get("itemCode") or "")
        if not MARKET_CODE_RE.fullmatch(code):
            raise FullMarketTechnicalAdapterError(f"invalid itemCode: {code!r}")
        if code in by_code:
            raise FullMarketTechnicalAdapterError(f"duplicate itemCode: {code}")
        as_of = _date(row.get("asOfDate") or row.get("asOf"), f"{code}.asOfDate")
        if as_of == "20261030":
            raise FullMarketTechnicalAdapterError(f"{code}: 20261030 row is not allowed")
        _validate_provenance(row, expected_source)
        by_code[code] = row
    return {"payload": payload, "targetDate": target, "rows": by_code}


def load_full_market_staging(
    technicals_path: str | Path,
    states_path: str | Path,
    group_states_path: str | Path,
) -> dict[str, Any]:
    """Load full-market technical/state staging without any production fallback."""
    technicals = _load_rows(technicals_path)
    source = str(next(iter(technicals["rows"].values()))["provenance"]["sourceStaging"])
    states = _load_rows(states_path, expected_source=source)
    groups = _read_json(group_states_path)
    target = technicals["targetDate"]
    if states["targetDate"] != target or _date(groups.get("targetDate"), "group targetDate") != target:
        raise FullMarketTechnicalAdapterError("staged input target dates do not match")
    if set(technicals["rows"]) != set(states["rows"]):
        raise FullMarketTechnicalAdapterError("technicals/states symbol sets do not match")
    for row in states["rows"].values():
        _validate_provenance(row, source)
    return {
        "targetDate": target,
        "sourceStaging": source,
        "technicals": technicals["rows"],
        "states": states["rows"],
        "technicalPayload": technicals["payload"],
        "statePayload": states["payload"],
        "groupStates": groups,
        "fallbackUsed": False,
    }


def _load_universe(universe_path: str | Path) -> tuple[list[dict[str, str]], dict[str, list[str]]]:
    payload = _read_json(universe_path)
    enabled: list[dict[str, str]] = []
    for stock in payload.get("stocks", []):
        if not isinstance(stock, dict) or stock.get("enabled") is not True:
            continue
        code = str(stock.get("itemCode") or "")
        name = stock.get("stockName")
        if not re.fullmatch(r"\d{6}", code) or not isinstance(name, str) or not name:
            raise FullMarketTechnicalAdapterError(f"invalid registered stock: {stock!r}")
        enabled.append({"itemCode": code, "stockName": name})
    watchlists = payload.get("watchlists") or {}
    if not isinstance(watchlists, dict):
        raise FullMarketTechnicalAdapterError("watchlists must be an object")
    normalized = {}
    for group, codes in watchlists.items():
        if not isinstance(codes, list) or any(not re.fullmatch(r"\d{6}", str(code)) for code in codes):
            raise FullMarketTechnicalAdapterError(f"invalid watchlist: {group!r}")
        normalized[str(group)] = [str(code) for code in codes]
    return enabled, normalized


def project_registered_universe(
    full_market: Mapping[str, Any], universe_path: str | Path
) -> dict[str, Any]:
    """Project only configured registered symbols from full-market rows."""
    enabled, watchlists = _load_universe(universe_path)
    names = {row["itemCode"]: row["stockName"] for row in enabled}
    codes = sorted(set(names) | {code for values in watchlists.values() for code in values})
    technicals = full_market["technicals"]
    states = full_market["states"]
    items = []
    missing = []
    for code in codes:
        if code not in technicals or code not in states:
            missing.append(code)
            continue
        items.append({
            "itemCode": code,
            "stockName": names.get(code),
            "technicals": technicals[code],
            "state": states[code],
            "watchlistGroups": sorted(group for group, values in watchlists.items() if code in values),
        })
    return {
        "targetDate": full_market["targetDate"],
        "sourceStaging": full_market["sourceStaging"],
        "registeredCount": len(codes),
        "items": items,
        "missingCodes": missing,
        "watchlists": watchlists,
        "fallbackUsed": False,
    }


def build_report_input(
    technicals_path: str | Path,
    states_path: str | Path,
    group_states_path: str | Path,
    universe_path: str | Path,
    current_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build separated report inputs; current snapshot remains an independent source."""
    full_market = load_full_market_staging(technicals_path, states_path, group_states_path)
    registered = project_registered_universe(full_market, universe_path)
    current = dict(current_snapshot) if current_snapshot is not None else {
        "status": "UNAVAILABLE",
        "reason": "CURRENT_SNAPSHOT_NOT_PROVIDED",
    }
    return {
        "schemaVersion": 1,
        "source": "STAGED_FULL_MARKET_TECHNICAL_ADAPTER",
        "targetDate": full_market["targetDate"],
        "sourceStaging": full_market["sourceStaging"],
        "fullMarket": {
            "technicals": full_market["technicals"],
            "states": full_market["states"],
            "technicalsPayload": full_market["technicalPayload"],
            "statesPayload": full_market["statePayload"],
            "counts": full_market["technicalPayload"].get("counts", {}),
            "fallbackUsed": False,
        },
        "registeredUniverse": registered,
        "historicalGroups": full_market["groupStates"],
        "currentSnapshot": current,
        "currentSnapshotSeparated": True,
        "fallbackUsed": False,
    }
