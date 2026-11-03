"""Opt-in bridge from verified KRX M10 staging to legacy relay inputs.

The normal relay path remains unchanged.  When a caller explicitly supplies
an M10 staging directory, this module projects the already-computed KRX
indicators into the legacy ``technicals.json`` shape.  It never reads NAVER
history and never recalculates a KRX indicator.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from tools.krx_m10_market_aggregates import safe_number


DATE_RE = re.compile(r"^\d{8}$")
CODE_RE = re.compile(r"^[A-Z0-9]{6}$")
SOURCE = "KRX_M10_GENERATION"


class KrxOperationalBridgeError(ValueError):
    """The KRX staging input is not safe for legacy consumers."""


def _date(value: Any, field: str) -> str:
    compact = str(value or "").replace("-", "")
    if not DATE_RE.fullmatch(compact):
        raise KrxOperationalBridgeError(f"INVALID_{field.upper()}")
    return compact


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KrxOperationalBridgeError(f"KRX_STAGING_READ_FAILED:{path.name}") from exc
    if not isinstance(payload, dict):
        raise KrxOperationalBridgeError(f"KRX_STAGING_OBJECT_REQUIRED:{path.name}")
    return payload


def _map_row(row: Mapping[str, Any], target: str) -> dict[str, Any]:
    code = str(row.get("itemCode") or "")
    if not CODE_RE.fullmatch(code):
        raise KrxOperationalBridgeError(f"INVALID_KRX_ITEM_CODE:{code}")
    if str(row.get("asOfDate") or "").replace("-", "") != target:
        raise KrxOperationalBridgeError(f"KRX_ROW_DATE_MISMATCH:{code}")
    metrics = row.get("technicals")
    if not isinstance(metrics, Mapping):
        raise KrxOperationalBridgeError(f"KRX_TECHNICALS_MISSING:{code}")
    provenance = row.get("provenance")
    if not isinstance(provenance, Mapping) or provenance.get("sourceStaging") != SOURCE:
        raise KrxOperationalBridgeError(f"KRX_PROVENANCE_MISSING:{code}")
    mapped = {
        "itemCode": code,
        "stockName": row.get("stockName") or row.get("name"),
        "asOf": f"{target[:4]}-{target[4:6]}-{target[6:]}",
        "sourceDate": target,
        "historicalIndicatorSource": "KRX",
        "historicalFallbackAllowed": False,
        "provenance": dict(provenance),
        "ma5": safe_number(metrics.get("ma5")),
        "ma20": safe_number(metrics.get("ma20")),
        "ma60": safe_number(metrics.get("ma60")),
        "ma120": safe_number(metrics.get("ma120")),
        "ma240": safe_number(metrics.get("ma240")),
        "return1dPct": safe_number(metrics.get("return1dPct")),
        "return5dPct": safe_number(metrics.get("return5dPct")),
        "return20dPct": safe_number(metrics.get("return20dPct")),
        "return60dPct": safe_number(metrics.get("return60dPct")),
        "return120dPct": safe_number(metrics.get("return120dPct")),
        "return240dPct": safe_number(metrics.get("return240dPct")),
        "highBreakoutPrev20": metrics.get("highBreakoutPrev20"),
        "highBreakoutPrev60": metrics.get("highBreakoutPrev60"),
        "volumeRatio20": safe_number(metrics.get("volumeRatio20")),
        "regularClose": safe_number((row.get("regularSession") or {}).get("close")),
        "officialDailyVolume": safe_number((row.get("regularSession") or {}).get("volume")),
        "status": "ok",
        "confirmedStatus": "ok",
        "provisional": False,
    }
    return mapped


def load_krx_technicals(
    staging_dir: str | Path,
    *,
    allowed_codes: set[str] | None = None,
) -> dict[str, Any]:
    """Load verified KRX technical rows in the legacy relay shape.

    Only the explicit staging directory is read.  A missing or inconsistent
    row fails closed; no NAVER historical fallback is attempted.
    """
    root = Path(staging_dir).resolve(strict=True)
    payload = _read(root / "technicals.json")
    target = _date(payload.get("targetDate"), "target_date")
    if payload.get("source") != SOURCE:
        raise KrxOperationalBridgeError("KRX_SOURCE_REQUIRED")
    if payload.get("historicalIndicatorSource") != "KRX":
        raise KrxOperationalBridgeError("KRX_HISTORICAL_SOURCE_REQUIRED")
    if payload.get("historicalFallbackAllowed") is not False:
        raise KrxOperationalBridgeError("KRX_NAVER_FALLBACK_FORBIDDEN")
    rows = payload.get("datas")
    if not isinstance(rows, list):
        raise KrxOperationalBridgeError("KRX_TECHNICAL_ROWS_REQUIRED")
    mapped = []
    full_market_count = len(rows)
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise KrxOperationalBridgeError("KRX_TECHNICAL_ROW_OBJECT_REQUIRED")
        item = _map_row(row, target)
        if item["itemCode"] in seen:
            raise KrxOperationalBridgeError(f"KRX_DUPLICATE_ITEM_CODE:{item['itemCode']}")
        seen.add(item["itemCode"])
        if allowed_codes is None or item["itemCode"] in allowed_codes:
            mapped.append(item)
    expected = (payload.get("counts") or {}).get("instrumentCount")
    if expected not in (None, full_market_count):
        raise KrxOperationalBridgeError("KRX_INSTRUMENT_COUNT_MISMATCH")
    mapped.sort(key=lambda row: row["itemCode"])
    return {
        "generatedAt": payload.get("generatedAt"),
        "targetDate": target,
        "source": SOURCE,
        "historicalIndicatorSource": "KRX",
        "historicalFallbackAllowed": False,
        "currentObservationPolicy": "NAVER_CURRENT_AND_AFTERMARKET_SEPARATE",
        "fullMarketCount": full_market_count,
        "projectionCodes": "FULL_MARKET" if allowed_codes is None else "REGISTERED_UNIVERSE",
        "expectedCount": len(mapped),
        "count": len(mapped),
        "missingCodes": [],
        "status": "ok",
        "datas": mapped,
    }
