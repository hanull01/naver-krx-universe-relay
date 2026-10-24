"""Read-only trading-date evidence derived from canonical NAVER daily bars."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")

def _valid_date(value):
    match = _DATE.fullmatch(str(value or ""))
    if not match:
        return False
    try:
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return False
    return True

def _read_latest(path):
    rows = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not _valid_date(row.get("date")):
                return None, "INVALID_CANONICAL_ROW"
            rows.append(row)
    except (OSError, ValueError, json.JSONDecodeError):
        return None, "INVALID_CANONICAL_FILE"
    if not rows:
        return None, "EMPTY_CANONICAL_FILE"
    dates = [str(row["date"]) for row in rows]
    if len(dates) != len(set(dates)):
        return None, "DUPLICATE_CANONICAL_DATE"
    return max(dates), None

def verify_trading_date(history_dir, current_codes, session_date=None):
    """Return a verified date, or null with a diagnostic reason.

    A canonical date is accepted only when it is valid and independently
    corroborated by the current snapshot's explicit session date.  Execution
    timestamps, generatedAt, and a date mode are never used as substitutes.
    """
    codes = sorted({str(code) for code in (current_codes or []) if code})
    observed = {}
    invalid = {}
    for code in codes:
        latest, reason = _read_latest(Path(history_dir) / (code + ".jsonl"))
        if reason:
            invalid[code] = reason
        elif latest:
            observed[code] = latest
    candidate = str(session_date or "")
    if not candidate or not _valid_date(candidate):
        return {"tradingDate": None, "reason": "INSUFFICIENT_TRADING_DATE_EVIDENCE",
                "observedDateCounts": dict(Counter(observed.values())),
                "missingCanonicalCodes": sorted(set(codes) - set(observed)),
                "invalidCanonicalCodes": invalid}
    counts = Counter(observed.values())
    if candidate not in counts:
        reason = "CANONICAL_SESSION_DATE_MISMATCH"
    elif invalid or set(observed) != set(codes):
        reason = "CANONICAL_COVERAGE_INCOMPLETE"
    elif any(value != candidate for value in observed.values()):
        reason = "MIXED_CANONICAL_TRADING_DATES"
    else:
        return {"tradingDate": candidate, "reason": "VERIFIED_CANONICAL_SESSION",
                "observedDateCounts": dict(counts), "missingCanonicalCodes": [],
                "invalidCanonicalCodes": {}}
    return {"tradingDate": None, "reason": reason,
            "observedDateCounts": dict(counts),
            "missingCanonicalCodes": sorted(set(codes) - set(observed)),
            "invalidCanonicalCodes": invalid}
