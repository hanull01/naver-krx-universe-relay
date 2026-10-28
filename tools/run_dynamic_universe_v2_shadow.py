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


def _history_context(root: Path, trading_date: str) -> Dict[str, Dict[str, Any]]:
    """Return prior V2 observations without resetting or inventing metrics."""
    path = root / "data/dynamic-universe/v2-shadow-history.json"
    try:
        history = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    context: Dict[str, Dict[str, Any]] = {}
    for observation in history.get("observations", []):
        observed_date = str(observation.get("tradingDate") or "")
        if not observed_date or observed_date > trading_date:
            continue
        for kind in ("industries", "themes"):
            for row in observation.get(kind, []) if isinstance(observation.get(kind), list) else []:
                key = f"{kind}:{row.get('id')}"
                entry = context.setdefault(key, {"dates": set(), "previous": None})
                entry["dates"].add(observed_date)
                if observed_date < trading_date:
                    previous_date = str((entry.get("previous") or {}).get("tradingDate") or "")
                    if not previous_date or observed_date > previous_date:
                        entry["previous"] = {"tradingDate": observed_date, **row}
    return context


def _enrich_persistence(payload: Dict[str, Any], kind: str,
                        history_context: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Connect existing shadow history to today's input without changing V1."""
    payload = dict(payload)
    rows = []
    for row in payload.get(kind, []) if isinstance(payload.get(kind), list) else []:
        row = dict(row)
        entry = history_context.get(f"{kind}:{row.get('id')}") or {}
        persistence = dict(row.get("persistence") or {})
        observed_days = len(entry.get("dates") or set())
        current_days = persistence.get("availableTradingDays")
        if isinstance(current_days, (int, float)) and not isinstance(current_days, bool):
            observed_days = max(observed_days, int(current_days))
        persistence["availableTradingDays"] = observed_days
        persistence["status"] = "AVAILABLE" if observed_days >= 2 else persistence.get("status", "UNAVAILABLE_NO_GROUP_HISTORY")
        row["persistence"] = persistence
        previous = entry.get("previous")
        if previous:
            row["historyPreviousTradingDate"] = previous.get("tradingDate")
        rows.append(row)
    payload[kind] = rows
    return payload


def run_once(root: Path, trading_date: str | None, observed_at: str | None = None) -> Dict[str, Any]:
    status_path = root / "data/dynamic-universe/v2-shadow-run-status.json"
    try:
        ind = json.loads((root / "data/monitoring/latest-industries.json").read_text())
        theme = json.loads((root / "data/monitoring/latest-theme-signals.json").read_text())
        v1 = json.loads((root / "data/dynamic-universe/latest.json").read_text())
        quality_path = root / "data/monitoring/latest-quality.json"
        quality = json.loads(quality_path.read_text()) if quality_path.is_file() else {}
        baseline_status = quality.get("baselineFreshnessStatus")
        if baseline_status:
            ind["baselineFreshnessStatus"] = baseline_status
            theme["baselineFreshnessStatus"] = baseline_status
        trading_date = trading_date or _infer_trading_date(ind, theme)
        history_context = _history_context(root, trading_date)
        ind = _enrich_persistence(ind, "industries", history_context)
        theme = _enrich_persistence(theme, "themes", history_context)
        result = evaluate_snapshot(ind, theme, v1)
        source_commit = quality.get("sourceCommit") or ind.get("sourceCommit")
        industry_quality = ind.get("quality") if isinstance(ind.get("quality"), dict) else {}
        source_quality = (quality.get("sourceQualityStatus")
                          or quality.get("collectionQualityStatus")
                          or ind.get("sourceQualityStatus")
                          or industry_quality.get("sourceStatus"))
        if source_quality == "OK" and ind.get("tradingDateEvidence", {}).get("evidenceClass") == "INTRADAY_SESSION":
            source_quality = "INTRADAY_OBSERVED"
        result["inputEvidence"] = {
            "tradingDate": trading_date,
            "sourceQualityStatus": source_quality or "UNAVAILABLE",
            "sourceCommit": source_commit,
            "sourceCommitStatus": "VERIFIED" if source_commit else "UNAVAILABLE",
        }
        if baseline_status == "BASELINE_STALE":
            result["inputEvidence"]["baselineFreshnessStatus"] = baseline_status
        result["summary"] = summarize(result)
        (root / "data/dynamic-universe/v2-shadow-latest.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        # The history is written only after the complete V2 result exists.
        history = record_current(root, trading_date)
        outcome = {"status": "OK", "observedAt": observed_at or datetime.now(timezone.utc).isoformat(),
                   "tradingDate": trading_date, "summary": result["summary"],
                   "inputEvidence": result["inputEvidence"],
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
