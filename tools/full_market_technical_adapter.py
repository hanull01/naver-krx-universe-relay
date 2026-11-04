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
KRX_M10_SOURCE_STAGING = "KRX_M10_GENERATION"


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


def _historical_lineage(
    root: Path,
    technicals_payload: Mapping[str, Any],
    states_payload: Mapping[str, Any],
    provenance_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Describe the historical source without treating missing receipt as valid.

    KRX generations already carry their generation and receipt metadata in the
    staged input.  This helper only reads that metadata; it never reconstructs
    historical values from the deprecated NAVER canonical tree.
    """
    source = str(
        technicals_payload.get("source")
        or technicals_payload.get("historicalIndicatorSource")
        or ""
    )
    is_krx = source == KRX_M10_SOURCE_STAGING or technicals_payload.get("historicalIndicatorSource") == "KRX"
    generation = technicals_payload.get("generationId")
    target = technicals_payload.get("targetDate")
    counts = technicals_payload.get("counts") if isinstance(technicals_payload.get("counts"), Mapping) else {}
    receipt_path = root / "receipt.json"
    receipt: Mapping[str, Any] = {}
    if receipt_path.is_file():
        try:
            loaded = _read_json(receipt_path)
            receipt = loaded if isinstance(loaded, Mapping) else {}
        except FullMarketTechnicalAdapterError:
            receipt = {}
    receipt_status = str(
        receipt.get("generationStatus")
        or technicals_payload.get("receiptStatus")
        or "UNAVAILABLE"
    ).upper()
    receipt_matches = bool(
        receipt
        and receipt.get("source") == "KRX_OPEN_API"
        and receipt.get("generationId") == generation
        and str(receipt.get("tradingDate") or "").replace("-", "") == str(target or "").replace("-", "")
        and receipt_status == "VERIFIED"
    )
    provenance_rows = provenance_payload.get("byCode") if isinstance(provenance_payload, Mapping) else {}
    provenance_rows = provenance_rows if isinstance(provenance_rows, Mapping) else {}
    provenance_generation_ok = bool(provenance_rows) and all(
        isinstance(row, Mapping) and row.get("sourceGenerationId") == generation
        for row in provenance_rows.values()
    )
    status = "NOT_APPLICABLE" if not is_krx else (
        "VERIFIED" if receipt_matches and provenance_generation_ok else "UNAVAILABLE"
    )
    return {
        "source": "KRX" if is_krx else source or "UNKNOWN",
        "generationId": generation,
        "targetDate": target,
        "receiptStatus": receipt_status,
        "receiptVerified": receipt_matches,
        "provenanceGenerationVerified": provenance_generation_ok if is_krx else None,
        "entityCount": counts.get("entityCount"),
        "instrumentCount": counts.get("instrumentCount"),
        "adjustmentStatus": (
            receipt.get("adjustmentStatus")
            or technicals_payload.get("adjustmentStatus")
            or "UNAVAILABLE"
        ),
        "status": status,
        "fallbackAllowed": False,
        "currentSnapshotSource": "NAVER_SEPARATE",
    }


def _validate_provenance(
    row: Mapping[str, Any], expected_source: str | None,
    provenance: Mapping[str, Any] | None = None,
) -> None:
    provenance = provenance or row.get("provenance")
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


def _load_rows(
    path: str | Path,
    expected_source: str | None = None,
    provenance_by_code: Mapping[str, Any] | None = None,
    expected_target_date: str | None = None,
) -> dict[str, Any]:
    payload = _read_json(path)
    target = _date(payload.get("targetDate"), "targetDate")
    if expected_target_date is not None:
        expected = _date(expected_target_date, "expected targetDate")
        if target != expected:
            raise FullMarketTechnicalAdapterError(
                f"staged targetDate mismatch: {target} != {expected}"
            )
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
        raw_as_of = row.get("asOfDate") or row.get("asOf")
        if raw_as_of in (None, ""):
            if str(row.get("availabilityStatus") or row.get("status")) not in {
                "PARTIAL", "UNAVAILABLE", "NO_DAILY_BAR"
            }:
                raise FullMarketTechnicalAdapterError(f"invalid {code}.asOfDate: {raw_as_of!r}")
            as_of = None
        else:
            as_of = _date(raw_as_of, f"{code}.asOfDate")
        if as_of is not None and as_of > target:
            raise FullMarketTechnicalAdapterError(
                f"{code}: asOfDate {as_of} is after targetDate {target}"
            )
        provenance = row.get("provenance")
        if not isinstance(provenance, Mapping) and isinstance(provenance_by_code, Mapping):
            candidate = provenance_by_code.get(code)
            provenance = candidate if isinstance(candidate, Mapping) else None
        _validate_provenance(row, expected_source, provenance)
        copied = dict(row)
        if "provenance" not in copied and provenance is not None:
            copied["provenance"] = dict(provenance)
        by_code[code] = copied
    return {"payload": payload, "targetDate": target, "rows": by_code}


def load_full_market_staging(
    technicals_path: str | Path,
    states_path: str | Path,
    group_states_path: str | Path,
    expected_target_date: str | None = None,
    expected_source_staging: str | None = None,
) -> dict[str, Any]:
    """Load full-market technical/state staging without any production fallback."""
    provenance_path = Path(technicals_path).with_name("provenance.json")
    provenance_payload = _read_json(provenance_path) if provenance_path.exists() else {}
    provenance_by_code = provenance_payload.get("byCode") if isinstance(provenance_payload, Mapping) else None
    technicals = _load_rows(
        technicals_path,
        provenance_by_code=provenance_by_code,
        expected_target_date=expected_target_date,
    )
    source = str(next(iter(technicals["rows"].values()))["provenance"]["sourceStaging"])
    if expected_source_staging is not None and source != expected_source_staging:
        raise FullMarketTechnicalAdapterError(
            f"staged source mismatch: {source} != {expected_source_staging}"
        )
    states = _load_rows(
        states_path,
        expected_source=source,
        provenance_by_code=provenance_by_code,
        expected_target_date=technicals["targetDate"],
    )
    groups = _read_json(group_states_path)
    provenance = provenance_payload if isinstance(provenance_payload, Mapping) else {}
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
        "historicalLineage": _historical_lineage(
            Path(technicals_path).parent,
            technicals["payload"],
            states["payload"],
            provenance,
        ),
        "missingHistoryCodes": list(technicals["payload"].get("missingHistoryCodes") or technicals["payload"].get("missingCodes") or []),
        "regularSessionUnavailableCodes": list(technicals["payload"].get("regularSessionUnavailableCodes") or []),
        "currentSnapshotMissingCodes": list(technicals["payload"].get("currentSnapshotMissingCodes") or []),
        "fallbackUsed": False,
        "historicalIndicatorSource": "KRX" if source == KRX_M10_SOURCE_STAGING else "STAGED_EXTERNAL",
        "historicalPriority": "PRIMARY" if source == KRX_M10_SOURCE_STAGING else "UNSPECIFIED",
        "historicalFallbackAllowed": False,
        "currentObservationPolicy": "SEPARATE",
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
    regular_session_unavailable = []
    current_snapshot_missing = []
    for code in codes:
        if code not in technicals or code not in states:
            missing.append(code)
            continue
        technical = technicals[code]
        state = states[code]
        regular = state.get("regularSession") or technical.get("regularSession")
        if isinstance(regular, Mapping) and str(regular.get("status") or "").upper() == "UNAVAILABLE":
            regular_session_unavailable.append(code)
        current = state.get("currentSnapshot") or technical.get("currentSnapshot")
        if isinstance(current, Mapping) and str(current.get("status") or "").upper() == "UNAVAILABLE":
            current_snapshot_missing.append(code)
        items.append({
            "itemCode": code,
            "stockName": names.get(code),
            "technicals": technical,
            "state": state,
            "watchlistGroups": sorted(group for group, values in watchlists.items() if code in values),
        })
    return {
        "targetDate": full_market["targetDate"],
        "sourceStaging": full_market["sourceStaging"],
        "registeredCount": len(codes),
        "items": items,
        "missingCodes": missing,
        "missingHistoryCodes": missing,
        "regularSessionUnavailableCodes": sorted(regular_session_unavailable),
        "currentSnapshotMissingCodes": sorted(current_snapshot_missing),
        "watchlists": watchlists,
        "fallbackUsed": False,
    }


def build_report_input(
    technicals_path: str | Path,
    states_path: str | Path,
    group_states_path: str | Path,
    universe_path: str | Path,
    current_snapshot: Mapping[str, Any] | None = None,
    expected_target_date: str | None = None,
    expected_source_staging: str | None = None,
) -> dict[str, Any]:
    """Build separated report inputs; current snapshot remains an independent source."""
    full_market = load_full_market_staging(
        technicals_path,
        states_path,
        group_states_path,
        expected_target_date=expected_target_date,
        expected_source_staging=expected_source_staging,
    )
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
        "historicalIndicatorSource": full_market["historicalIndicatorSource"],
        "historicalPriority": full_market["historicalPriority"],
        "historicalFallbackAllowed": full_market["historicalFallbackAllowed"],
        "currentObservationPolicy": full_market["currentObservationPolicy"],
        "historicalLineage": full_market["historicalLineage"],
        "missingHistoryCodes": list(full_market.get("missingHistoryCodes") or []),
        "regularSessionUnavailableCodes": list(full_market.get("regularSessionUnavailableCodes") or []),
        "currentSnapshotMissingCodes": list(full_market.get("currentSnapshotMissingCodes") or []),
    }
