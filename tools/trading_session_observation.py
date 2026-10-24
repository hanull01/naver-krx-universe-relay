"""Validated NAVER daily-session observation diagnostics."""
from __future__ import annotations

from collections import Counter
from datetime import date


def _compact(value):
    text = str(value or "").replace("-", "").replace("/", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"invalid trading date: {value!r}")
    date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    return text


def build_observation_diagnostic(target_date, observed_dates):
    """Summarize localDate observations without changing collection output."""
    target = _compact(target_date)
    if not isinstance(observed_dates, dict) or not observed_dates:
        return {"status": "UNVERIFIED", "targetDate": target,
                "observedDateCounts": {}, "matchingCount": 0,
                "mismatchCount": 0, "reason": "NO_LOCAL_DATE_OBSERVATIONS"}
    normalized = {str(code): _compact(value) for code, value in observed_dates.items()}
    counts = Counter(normalized.values())
    mismatches = sorted(code for code, value in normalized.items() if value != target)
    status = "VERIFIED" if not mismatches else "UNVERIFIED"
    return {"status": status, "targetDate": target,
            "observedDateCounts": dict(sorted(counts.items())),
            "matchingCount": len(normalized) - len(mismatches),
            "mismatchCount": len(mismatches), "mismatchCodes": mismatches,
            "reason": "LOCAL_DATE_MATCHES_TARGET" if not mismatches else "LOCAL_DATE_MISMATCH"}
