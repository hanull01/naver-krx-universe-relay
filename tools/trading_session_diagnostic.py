"""Build a sidecar diagnostic from validated NAVER localDate observations."""
from __future__ import annotations

import json
from pathlib import Path

from tools.trading_session_observation import build_observation_diagnostic


def write_local_date_diagnostic(output_path, target_date, observed_dates, expected_codes=None):
    """Write diagnostics without changing collection output or state."""
    observed = {str(code): value for code, value in (observed_dates or {}).items()}
    expected = {str(code) for code in (expected_codes or observed)}
    result = build_observation_diagnostic(target_date, observed)
    result.update({"expectedCount": len(expected), "observedCount": len(observed),
                   "missingCodes": sorted(expected - set(observed)),
                   "unexpectedCodes": sorted(set(observed) - expected),
                   "source": "NAVER_LOCAL_DATE"})
    if result["missingCodes"] or result["unexpectedCodes"]:
        result["status"] = "UNVERIFIED"
        result["reason"] = "CODE_SET_MISMATCH"
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
