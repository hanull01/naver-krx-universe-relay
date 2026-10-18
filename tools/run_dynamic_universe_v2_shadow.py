"""Opt-in one-shot V2 runner.

It reads the same already-published monitoring snapshot used by V1, but has
its own output and error status.  It is deliberately not wired into any
workflow; a caller can invoke it after a successful refresh without making V1
depend on its exit status.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

try:
    from tools.dynamic_universe_v2_history import record_current
    from tools.dynamic_universe_v2_shadow import evaluate_snapshot, summarize
except ModuleNotFoundError:  # direct `python tools/...py` invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.dynamic_universe_v2_history import record_current
    from tools.dynamic_universe_v2_shadow import evaluate_snapshot, summarize


def _infer_trading_date(industry_payload: Dict[str, Any], theme_payload: Dict[str, Any]) -> str:
    for row in theme_payload.get("themes", []):
        for stock in row.get("stocks", []) if isinstance(row, dict) else []:
            value = stock.get("temporaryCurrentBarDate") if isinstance(stock, dict) else None
            if isinstance(value, str) and len(value) == 8 and value.isdigit():
                return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    generated = industry_payload.get("generatedAt") or theme_payload.get("generatedAt")
    if isinstance(generated, str) and len(generated) >= 10:
        return generated[:10]
    raise ValueError("TRADING_DATE_UNAVAILABLE")


def run_once(root: Path, trading_date: str | None, observed_at: str | None = None) -> Dict[str, Any]:
    status_path = root / "data/dynamic-universe/v2-shadow-run-status.json"
    try:
        ind = json.loads((root / "data/monitoring/latest-industries.json").read_text())
        theme = json.loads((root / "data/monitoring/latest-theme-signals.json").read_text())
        v1 = json.loads((root / "data/dynamic-universe/latest.json").read_text())
        trading_date = trading_date or _infer_trading_date(ind, theme)
        result = evaluate_snapshot(ind, theme, v1)
        result["summary"] = summarize(result)
        (root / "data/dynamic-universe/v2-shadow-latest.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        # The history is written only after the complete V2 result exists.
        history = record_current(root, trading_date)
        outcome = {"status": "OK", "observedAt": observed_at or datetime.now(timezone.utc).isoformat(),
                   "tradingDate": trading_date, "summary": result["summary"],
                   "observationCount": len(history["observations"])}
    except Exception as exc:  # V2 isolation boundary; V1 is not called here.
        outcome = {"status": "FAILED", "observedAt": observed_at or datetime.now(timezone.utc).isoformat(),
                   "tradingDate": trading_date, "errorType": type(exc).__name__,
                   "error": str(exc)[:240]}
    status_path.write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n")
    return outcome


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(run_once(root, None), ensure_ascii=False))
