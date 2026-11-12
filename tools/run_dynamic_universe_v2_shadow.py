"""Opt-in one-shot V2 runner.

It reads the same already-published monitoring snapshot used by V1, but has
its own output and error status.  It is deliberately not wired into any
workflow; a caller can invoke it after a successful refresh without making V1
depend on its exit status.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

try:
    from tools.dynamic_universe_v2_history import record_current
    from tools.dynamic_universe_v2_shadow import evaluate_snapshot, summarize
    from tools.v2_source_provenance import build_source_provenance
except ModuleNotFoundError:  # direct `python tools/...py` invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.dynamic_universe_v2_history import record_current
    from tools.dynamic_universe_v2_shadow import evaluate_snapshot, summarize
    from tools.v2_source_provenance import build_source_provenance


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


def normalize_run_status(
    payload: Mapping[str, Any] | None,
    *,
    expected_generation_id: str | None = None,
    run_id: str | None = None,
    runner_exit_code: int = 0,
    generated_at: str | None = None,
) -> Dict[str, Any]:
    """Make producer status explicit without promoting an invalid V2 result."""
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    normalized = dict(payload) if isinstance(payload, Mapping) else {
        "status": "FAILED",
        "errorType": "V2_RUNNER_PROCESS_FAILED",
        "error": f"runner_exit={runner_exit_code}",
    }
    raw_status = normalized.get("status") or "FAILED"
    actual_generation = normalized.get("generationId")
    effective_status = raw_status
    reason = normalized.get("failureReason") or normalized.get("error")
    if raw_status in {"OK", "SUCCESS"}:
        if not actual_generation:
            effective_status = "GENERATION_UNAVAILABLE"
            reason = "V2_GENERATION_ID_MISSING"
        elif expected_generation_id and str(actual_generation) != str(expected_generation_id):
            effective_status = "GENERATION_MISMATCH"
            reason = "V2_GENERATION_ID_MISMATCH"
        else:
            effective_status = "SUCCESS"
    normalized["runId"] = normalized.get("runId") or run_id
    normalized["generatedAt"] = normalized.get("generatedAt") or generated_at
    normalized["rawStatus"] = raw_status
    normalized["producerStatus"] = raw_status
    normalized["runStatus"] = raw_status
    normalized["status"] = effective_status
    normalized["expectedGenerationId"] = expected_generation_id
    normalized["consumptionStatus"] = "CONSUMED" if effective_status == "SUCCESS" else "NOT_CONSUMED"
    normalized["publicationPolicy"] = "PARTIAL_NON_BLOCKING"
    normalized["failureReason"] = None if effective_status == "SUCCESS" else reason or "V2_SHADOW_FAILED"
    normalized.setdefault("artifactPath", "data/dynamic-universe/v2-shadow-latest.json")
    evidence = normalized.get("generationEvidence")
    if not isinstance(evidence, Mapping):
        evidence = {}
    normalized["generationEvidence"] = {
        "inputPath": evidence.get("inputPath"),
        "manifestPath": evidence.get("manifestPath"),
        "manifestPresent": evidence.get("manifestPresent"),
        "generationField": evidence.get("generationField"),
        "generationFieldPresent": evidence.get("generationFieldPresent", bool(actual_generation)),
        "expectedComparable": bool(actual_generation and expected_generation_id),
    }
    return normalized


def _generation_evidence(root: Path, quality: Mapping[str, Any], industry_payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Record where generation was sought; never invent a generation value."""
    quality_path = root / "data/monitoring/latest-quality.json"
    manifest_candidates = (
        root / "data/monitoring/manifest.json",
        root / "data/monitoring/latest-quality.json",
    )
    field_names = (
        "historicalGenerationId", "sourceGeneration", "generationId",
    )
    field = next((name for name in field_names if quality.get(name) not in (None, "")), None)
    if field is None:
        field = next((name for name in field_names if industry_payload.get(name) not in (None, "")), None)
    manifest_path = next((path for path in manifest_candidates if path.exists()), manifest_candidates[0])
    return {
        "inputPath": str(quality_path),
        "manifestPath": str(manifest_path),
        "manifestPresent": manifest_path.exists(),
        "generationField": field,
        "generationFieldPresent": field is not None,
    }


def run_once(root: Path, trading_date: str | None, observed_at: str | None = None) -> Dict[str, Any]:
    status_path = root / "data/dynamic-universe/v2-shadow-run-status.json"
    latest_path = root / "data/dynamic-universe/v2-shadow-latest.json"
    generated_at = datetime.now(timezone.utc).isoformat()
    run_id = os.environ.get("GITHUB_RUN_ID") or f"local-v2-shadow-{generated_at.replace(':', '').replace('-', '')}"
    quality: Dict[str, Any] = {}
    generation_evidence: Dict[str, Any] = {
        "inputPath": str(root / "data/monitoring/latest-quality.json"),
        "manifestPath": str(root / "data/monitoring/manifest.json"),
        "manifestPresent": False,
        "generationField": None,
        "generationFieldPresent": False,
    }
    generation_id = None
    prior_latest = latest_path.read_bytes() if latest_path.exists() else None
    history_path = root / "data/dynamic-universe/v2-shadow-history.json"
    prior_history = history_path.read_bytes() if history_path.exists() else None
    try:
        ind = json.loads((root / "data/monitoring/latest-industries.json").read_text())
        theme = json.loads((root / "data/monitoring/latest-theme-signals.json").read_text())
        v1 = json.loads((root / "data/dynamic-universe/latest.json").read_text())
        try:
            loaded_quality = json.loads(
                (root / "data/monitoring/latest-quality.json").read_text(encoding="utf-8")
            )
            if isinstance(loaded_quality, dict):
                quality = loaded_quality
        except (OSError, json.JSONDecodeError):
            quality = {}
        generation_evidence = _generation_evidence(root, quality, ind)
        trading_date = trading_date or _infer_trading_date(ind, theme)
        history_context = _history_context(root, trading_date)
        ind = _enrich_persistence(ind, "industries", history_context)
        theme = _enrich_persistence(theme, "themes", history_context)
        result = evaluate_snapshot(ind, theme, v1)
        industry_quality = ind.get("quality") if isinstance(ind.get("quality"), dict) else {}
        source_commit = quality.get("sourceCommit") or ind.get("sourceCommit")
        source_quality = (quality.get("sourceQualityStatus")
                          or quality.get("collectionQualityStatus")
                          or ind.get("sourceQualityStatus")
                          or industry_quality.get("sourceStatus"))
        generation_id = (
            quality.get("historicalGenerationId")
            or quality.get("sourceGeneration")
            or quality.get("generationId")
            or ind.get("historicalGenerationId")
            or ind.get("generationId")
        )
        if source_quality == "OK" and ind.get("tradingDateEvidence", {}).get("evidenceClass") == "INTRADAY_SESSION":
            source_quality = "INTRADAY_OBSERVED"
        result["inputEvidence"] = {
            "tradingDate": trading_date,
            "sourceQualityStatus": source_quality or "UNAVAILABLE",
            "sourceCommit": source_commit,
            "sourceCommitStatus": "VERIFIED" if source_commit else "UNAVAILABLE",
            "generationId": generation_id,
        }
        result["inputEvidence"]["sourceProvenance"] = build_source_provenance(root, result=result)
        result["summary"] = summarize(result)
        latest_path.parent.mkdir(parents=True, exist_ok=True)
        latest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # The history is written only after the complete V2 result exists.
        history = record_current(root, trading_date,
                                 result["inputEvidence"]["sourceProvenance"])
        outcome = {"runId": run_id, "status": "SUCCESS", "observedAt": observed_at or generated_at,
                   "generatedAt": generated_at,
                   "tradingDate": trading_date, "generationId": generation_id,
                   "summary": result["summary"],
                   "inputEvidence": result["inputEvidence"],
                   "generationEvidence": generation_evidence,
                   "artifactPath": str(latest_path),
                   "observationCount": len(history["observations"])}
    except Exception as exc:  # V2 isolation boundary; V1 is not called here.
        if prior_latest is None:
            latest_path.unlink(missing_ok=True)
        else:
            latest_path.parent.mkdir(parents=True, exist_ok=True)
            latest_path.write_bytes(prior_latest)
        if prior_history is None:
            history_path.unlink(missing_ok=True)
        else:
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history_path.write_bytes(prior_history)
        outcome = {"runId": run_id, "status": "FAILED", "observedAt": observed_at or generated_at,
                   "generatedAt": generated_at,
                   "tradingDate": trading_date, "errorType": type(exc).__name__,
                   "error": str(exc)[:240], "generationId": generation_id,
                   "generationEvidence": generation_evidence,
                   "artifactPath": str(latest_path),
                   "failureReason": f"V2_SHADOW_{type(exc).__name__.upper()}"}
    outcome = normalize_run_status(
        outcome,
        expected_generation_id=os.environ.get("EXPECTED_GENERATION_ID") or None,
        run_id=run_id,
        runner_exit_code=0 if outcome.get("status") in {"OK", "SUCCESS"} else 1,
        generated_at=generated_at,
    )
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n")
    return outcome


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(run_once(root, None), ensure_ascii=False))
