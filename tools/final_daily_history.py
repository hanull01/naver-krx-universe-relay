"""Dry-run bridge from the validated 20:30 snapshot to daily history.

This module deliberately has no filesystem or network side effects.  It does
not replace the STOCK-only monitoring state; it preserves the richer source
fields needed for a future, separately approved daily-history writer.
"""
from __future__ import annotations

from copy import deepcopy

try:
    from .final_daily_evidence import validate_snapshot
    from .trading_date_evidence import explicit_session_date
except ImportError:  # direct script/test loading compatibility
    from final_daily_evidence import validate_snapshot
    from trading_date_evidence import explicit_session_date


ASSET_TYPES = {"STOCK", "ETF", "ETN"}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _same(left, right):
    return all(left.get(key) == right.get(key) for key in (
        "assetType", "date", "open", "high", "low", "close", "volume", "value",
        "source", "collectedAt", "session", "validationStatus"))


def snapshot_rows(snapshot, baseline=None, expected_codes=None, target_date=None):
    """Convert a validated snapshot to proposed daily rows without writing."""
    diagnostic = validate_snapshot(snapshot, baseline, target_date=target_date,
                                   expected_codes=expected_codes)
    if diagnostic["errors"]:
        return {"status": "REJECTED", "reason": diagnostic["errors"], "diagnostic": diagnostic,
                "rows": []}
    rows = []
    for raw in snapshot.get("stocks") or []:
        asset_type = str(raw.get("assetType") or "").upper()
        if asset_type not in ASSET_TYPES:
            return {"status": "REJECTED", "reason": [f"ASSET_TYPE_MISSING:{raw.get('code')}"],
                    "diagnostic": diagnostic, "rows": []}
        required = {"openPrice": "open", "highPrice": "high", "lowPrice": "low",
                    "currentPrice": "close", "accumulatedTradingVolume": "volume",
                    "accumulatedTradingValue": "value"}
        if any(not _number(raw.get(source)) for source in required):
            return {"status": "REJECTED", "reason": [f"OHLCV_FIELD_MISSING:{raw.get('code')}"],
                    "diagnostic": diagnostic, "rows": []}
        if raw["highPrice"] < max(raw["openPrice"], raw["currentPrice"]):
            return {"status": "REJECTED", "reason": [f"HIGH_BELOW_OPEN_OR_CLOSE:{raw.get('code')}"],
                    "diagnostic": diagnostic, "rows": []}
        if raw["lowPrice"] > min(raw["openPrice"], raw["currentPrice"]):
            return {"status": "REJECTED", "reason": [f"LOW_ABOVE_OPEN_OR_CLOSE:{raw.get('code')}"],
                    "diagnostic": diagnostic, "rows": []}
        rows.append({"code": str(raw["code"]), "assetType": asset_type,
                     "date": diagnostic["tradingDate"], "open": raw["openPrice"],
                     "high": raw["highPrice"], "low": raw["lowPrice"],
                     "close": raw["currentPrice"], "volume": raw["accumulatedTradingVolume"],
                     "value": raw["accumulatedTradingValue"], "source": raw.get("source", "NAVER"),
                     "collectedAt": diagnostic["generatedAt"],
                     "session": raw.get("session") or ("REGULAR_ONLY" if asset_type in {"ETF", "ETN"}
                                                         else "REGULAR_PLUS_AFTER_MARKET"),
                     "validationStatus": "VALIDATED_CANDIDATE"})
    return {"status": "READY", "reason": [], "diagnostic": diagnostic,
            "rows": sorted(rows, key=lambda row: row["code"])}


def reconcile_rows(existing_rows, proposed_rows):
    """Return a no-write reconciliation plan; same rows are NOOP, changes conflict."""
    existing = {str(row["code"]): row for row in (existing_rows or [])}
    proposed = {str(row["code"]): row for row in proposed_rows}
    if len(existing) != len(existing_rows or []):
        return {"status": "REJECTED", "reason": "DUPLICATE_EXISTING_CODE", "rows": []}
    conflicts = sorted(code for code in set(existing) & set(proposed)
                       if not _same(existing[code], proposed[code]))
    if conflicts:
        return {"status": "CONFLICT", "reason": "SAME_DATE_VALUE_CHANGED", "conflicts": conflicts,
                "rows": []}
    added = sorted(set(proposed) - set(existing))
    return {"status": "NOOP" if not added else "READY", "reason": [], "added": added,
            "rows": [deepcopy(proposed[code]) for code in sorted(proposed)]}


def moving_averages(rows):
    """Calculate MA5/20/60 from one chronologically ordered code history."""
    ordered = sorted(rows, key=lambda row: str(row["date"]))
    closes = [row["close"] for row in ordered if _number(row.get("close"))]
    return {f"ma{window}": (sum(closes[-window:]) / window if len(closes) >= window else None)
            for window in (5, 20, 60)}


def monitoring_daily_rows(proposed_rows):
    """Adapt validated candidates to the existing STOCK monitoring schema.

    The existing COLUMN_ARRAY_V1 writer is intentionally STOCK-only and keeps
    close/high/volume plus validity flags.  This adapter performs no write and
    leaves ETF/ETN rows out of that path rather than coercing them into the
    stock universe.
    """
    rows = []
    excluded = []
    for row in proposed_rows or []:
        asset_type = str(row.get("assetType") or "").upper()
        if asset_type != "STOCK":
            excluded.append({"code": str(row.get("code") or ""), "assetType": asset_type,
                             "reason": "ASSET_TYPE_NOT_SUPPORTED_BY_MONITORING_DAILY"})
            continue
        rows.append({"code": str(row["code"]), "date": row["date"],
                     "open": row.get("open"), "low": row.get("low"),
                     "close": row["close"], "high": row["high"], "volume": row["volume"],
                     "value": row.get("value"),
                     "closeValid": True, "ohlcValid": True, "volumeValid": True})
    return {"rows": sorted(rows, key=lambda row: row["code"]), "excluded": excluded,
            "status": "READY" if rows else "WAIT"}


def monitoring_daily_payload(snapshot, baseline=None, expected_codes=None, target_date=None):
    """Build the existing update-latest input without writing or fetching."""
    target_date = target_date or explicit_session_date(current_snapshot=snapshot)
    if not target_date:
        return {"status": "WAIT", "reason": ["TRADING_DATE_MISSING_OR_INCONSISTENT"],
                "diagnostic": {"status": "UNUSABLE", "errors": ["TRADING_DATE_MISSING_OR_INCONSISTENT"],
                               "networkCalls": 0}, "rows": [], "excluded": []}
    # A structurally complete NAVER list with a stable composition delta is
    # itself the active-set authority for this observation.  The previous
    # all-assets file remains historical context, not a gate on today's FINAL.
    composition_pending = (snapshot.get("status") == "COMPLETE_RECONCILIATION_PENDING"
                           and snapshot.get("collectionStatus", "SUCCESS") == "SUCCESS"
                           and snapshot.get("qualityStatus") == "WARNING")
    if composition_pending:
        expected_codes = [str(row.get("code")) for row in snapshot.get("stocks", [])
                          if row.get("code")]
    candidate = snapshot_rows(snapshot, baseline=baseline, expected_codes=expected_codes,
                              target_date=target_date)
    if candidate["status"] != "READY":
        return {"status": "WAIT", "reason": candidate["reason"],
                "diagnostic": candidate["diagnostic"], "rows": []}
    adapted = monitoring_daily_rows(candidate["rows"])
    return {"status": adapted["status"], "reason": [],
            "diagnostic": candidate["diagnostic"], "excluded": adapted["excluded"],
            "rows": adapted["rows"]}
