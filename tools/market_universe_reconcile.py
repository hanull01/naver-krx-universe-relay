"""Pure, fail-closed reconciliation for an observed market stock set.

Collection proves pagination completeness; this module decides whether an
observed difference may change the current Universe.  It never deletes
historical files and requires an explicit verified lifecycle event for every
change.
"""
from __future__ import annotations

from datetime import date
import math

MARKETS = ("KOSPI", "KOSDAQ")
CHANGE_TYPES = {"NEW_LISTING", "DELISTING", "MARKET_TRANSFER", "NAME_CHANGE", "CODE_CHANGE"}


def _stocks(payload):
    rows = payload.get("assets", []) if isinstance(payload, dict) else []
    return {str(row.get("code")): row for row in rows
            if row.get("assetType", "STOCK") == "STOCK" and row.get("code")}


def classify_delta(previous, observed):
    old, new = _stocks(previous), _stocks(observed)
    old_codes, new_codes = set(old), set(new)
    common = old_codes & new_codes
    return {
        "added": sorted(new_codes - old_codes),
        "removed": sorted(old_codes - new_codes),
        "marketChanged": sorted(code for code in common
                                 if old[code].get("market") != new[code].get("market")),
        "nameChanged": sorted(code for code in common
                               if old[code].get("name") != new[code].get("name")),
    }


def pending_observation(previous, observed, *, observed_at, source="NAVER_STOCKLIST_CURRENT"):
    """Record an observation without approving or changing the Universe."""
    delta = classify_delta(previous, observed)
    return {
        "firstDetectedAt": observed_at,
        "lastCheckedAt": observed_at,
        "source": source,
        "verificationStatus": "PENDING_EXTERNAL_VERIFICATION" if any(delta.values()) else "NO_CHANGE",
        "applied": False,
        "delta": delta,
    }


def _event_key(event):
    kind = event.get("type")
    if kind not in CHANGE_TYPES or not event.get("sourceVerified"):
        raise ValueError("unverified or unsupported lifecycle event")
    effective = str(event.get("effectiveDate") or "")
    try:
        date.fromisoformat(effective)
    except ValueError as exc:
        raise ValueError("lifecycle event requires effectiveDate") from exc
    if kind == "CODE_CHANGE":
        return kind, str(event.get("oldCode")), str(event.get("newCode"))
    return kind, str(event.get("code"))


def reconcile_verified(previous, observed, verified_events, *, target_date=None,
                       max_unverified_change_fraction=0.20):
    """Return a new current Universe only when every delta is verified.

    ``verified_events`` is an external evidence result, not inferred from
    NAVER.  The returned lifecycle record is suitable for a transactional
    publisher; this function performs no I/O.
    """
    old, new = _stocks(previous), _stocks(observed)
    delta = classify_delta(previous, observed)
    changed = set(delta["added"]) | set(delta["removed"]) | set(delta["marketChanged"]) | set(delta["nameChanged"])
    if not changed:
        return dict(previous), {"status": "NOOP", "delta": delta, "events": []}
    total = max(len(old), 1)
    allowed = max(10, math.ceil(total * max_unverified_change_fraction))
    if len(changed) > allowed:
        raise ValueError("ABNORMAL_LARGE_UNIVERSE_CHANGE")
    events = list(verified_events or [])
    keys = {_event_key(event) for event in events}
    covered = set()
    for code in delta["added"]:
        if ("NEW_LISTING", code) in keys: covered.add(code)
    for code in delta["removed"]:
        if ("DELISTING", code) in keys: covered.add(code)
    for code in delta["marketChanged"]:
        if ("MARKET_TRANSFER", code) in keys: covered.add(code)
    for code in delta["nameChanged"]:
        if ("NAME_CHANGE", code) in keys: covered.add(code)
    for key in keys:
        if key[0] == "CODE_CHANGE":
            _, old_code, new_code = key
            if old_code in old and new_code in new:
                covered.update((old_code, new_code))
    if not changed.issubset(covered):
        raise ValueError("UNVERIFIED_UNIVERSE_CHANGE")
    output = dict(previous)
    nonstocks = [row for row in previous.get("assets", []) if row.get("assetType", "STOCK") != "STOCK"]
    output["assets"] = sorted(list(new.values()) + nonstocks,
                               key=lambda row: (row.get("market") or "", str(row.get("code") or "")))
    metadata = dict(previous.get("metadata") or {})
    metadata["lifecycle"] = {"status": "VERIFIED", "targetDate": target_date,
                              "delta": delta, "events": events}
    output["metadata"] = metadata
    return output, metadata["lifecycle"]
