"""Build a sidecar diagnostic from validated NAVER localDate observations."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from tools.trading_session_observation import build_observation_diagnostic


def _atomic_json(path, value):
    """Write one diagnostic JSON without exposing a partial file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_local_date_diagnostic(output_path, target_date, observed_dates, expected_codes=None,
                               duplicate_codes=None, observed_date_values=None,
                               source_missing_codes=None, source_missing_reasons=None):
    """Write diagnostics without changing collection output or state."""
    observed = {str(code): value for code, value in (observed_dates or {}).items()}
    expected = {str(code) for code in (expected_codes or observed)}
    result = build_observation_diagnostic(target_date, observed)
    duplicates = sorted({str(code) for code in (duplicate_codes or ())})
    result.update({"expectedCodes": sorted(expected), "observedCodes": sorted(observed),
                   "observedDates": dict(sorted(observed.items())),
                   "expectedCount": len(expected), "observedCount": len(observed),
                   "missingCodes": sorted(expected - set(observed)),
                   "unexpectedCodes": sorted(set(observed) - expected),
                   "duplicateCodes": duplicates,
                   "dateMismatchCodes": list(result.get("mismatchCodes", [])),
                   "source": "NAVER_LOCAL_DATE"})
    if source_missing_codes:
        result["sourceMissingCodes"] = sorted({str(code) for code in source_missing_codes})
        result["sourceMissingReasons"] = dict(sorted((source_missing_reasons or {}).items()))
        result["status"] = "SOURCE_OBSERVED"
        result["reason"] = "SOURCE_OBSERVED_WITH_NORMAL_EMPTY_DAILY"
    if observed_date_values:
        result["observedDateValues"] = {
            str(code): list(values) for code, values in sorted(observed_date_values.items())
        }
    normal_empty = set(result.get("sourceMissingCodes") or ())
    if (result["missingCodes"] and not normal_empty
            or set(result["missingCodes"]) != normal_empty
            or result["unexpectedCodes"]):
        result["status"] = "UNVERIFIED"
        result["reason"] = "CODE_SET_MISMATCH"
    elif duplicates:
        result["status"] = "UNVERIFIED"
        result["reason"] = "DUPLICATE_LOCAL_DATE_OBSERVATION"
    path = Path(output_path)
    _atomic_json(path, result)
    return result
