"""Run the opt-in V2 observation from the NAVER current snapshot.

V2 is an observation of the current NAVER market snapshot. It is not a
second KRX projection and never reads KRX CURRENT or generation files.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

try:
    from tools.dynamic_universe_v2_history import record_current
    from tools.dynamic_universe_v2_shadow import evaluate_snapshot, summarize
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.dynamic_universe_v2_history import record_current
    from tools.dynamic_universe_v2_shadow import evaluate_snapshot, summarize


SOURCE = "NAVER_CURRENT_SNAPSHOT"
SELECTION_POLICY_VERSION = "v2-current-observation-20261113"
INPUT_FILES = (
    ("breadth", "data/monitoring/latest-breadth.json"),
    ("industries", "data/monitoring/latest-industries.json"),
    ("themes", "data/monitoring/latest-theme-signals.json"),
)


class V2InputError(ValueError):
    def __init__(self, status: str, reason: str, **details: Any) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.details = details


def _compact_date(value: Any) -> str | None:
    text = str(value or "").strip().replace("-", "").replace("/", "")
    if len(text) == 8 and text.isdigit():
        return text
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        date = text[:10].replace("-", "")
        return date if date.isdigit() else None
    return None


def _payload_date(payload: Mapping[str, Any], kind: str) -> str | None:
    for key in ("observationDate", "sourceDate", "tradingDate", "asOfDate"):
        value = _compact_date(payload.get(key))
        if value:
            return value
    rows = payload.get("themes" if kind == "themes" else "industries")
    if isinstance(rows, list):
        dates: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            for candidate in (row, row.get("metrics")):
                if isinstance(candidate, Mapping):
                    value = _compact_date(candidate.get("observationDate") or candidate.get("sourceDate"))
                    if value:
                        dates.add(value)
            stocks = row.get("stocks")
            if isinstance(stocks, list):
                for stock in stocks:
                    if isinstance(stock, Mapping):
                        value = _compact_date(stock.get("temporaryCurrentBarDate") or stock.get("observationDate"))
                        if value:
                            dates.add(value)
        if len(dates) == 1:
            return next(iter(dates))
        if len(dates) > 1:
            raise V2InputError("INPUT_MISMATCH", "V2_INPUT_MULTIPLE_OBSERVATION_DATES", kind=kind)
    return None


def _declared_source_hash(payload: Mapping[str, Any]) -> str | None:
    for key in ("sourceHash", "sourceSha256", "inputSnapshotSha256", "snapshotHash"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def v2_observation_id(*, source_date: str, observation_date: str, source_hash: str,
                     selection_policy_version: str = SELECTION_POLICY_VERSION) -> str:
    """Return a deterministic identity for one NAVER snapshot input."""
    canonical = json.dumps({
        "sourceDate": source_date,
        "observationDate": observation_date,
        "sourceHash": source_hash,
        "selectionPolicyVersion": selection_policy_version,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "v2obs-" + hashlib.sha256(canonical).hexdigest()[:32]


def _read_current_inputs(root: Path, expected_date: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    payloads: dict[str, Any] = {}
    raw_by_kind: dict[str, bytes] = {}
    paths: dict[str, str] = {}
    dates: dict[str, str] = {}
    explicit_hashes: dict[str, str] = {}
    for kind, relative in INPUT_FILES:
        path = root / relative
        try:
            raw = path.read_bytes()
            value = json.loads(raw.decode("utf-8"))
        except FileNotFoundError as exc:
            raise V2InputError("INPUT_UNAVAILABLE", "V2_INPUT_ARTIFACT_MISSING", inputArtifactPath=relative) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise V2InputError("ARTIFACT_INVALID", "V2_INPUT_ARTIFACT_INVALID", inputArtifactPath=relative) from exc
        if not isinstance(value, Mapping):
            raise V2InputError("ARTIFACT_INVALID", "V2_INPUT_ARTIFACT_NOT_OBJECT", inputArtifactPath=relative)
        payloads[kind] = dict(value)
        raw_by_kind[kind] = raw
        paths[kind] = relative
        date = _payload_date(value, kind)
        if not date:
            raise V2InputError("INPUT_UNAVAILABLE", "V2_INPUT_OBSERVATION_DATE_MISSING", inputArtifactPath=relative)
        dates[kind] = date
        declared = _declared_source_hash(value)
        if declared:
            explicit_hashes[kind] = declared

    unique_dates = set(dates.values())
    if len(unique_dates) != 1 or (expected_date and next(iter(unique_dates)) != _compact_date(expected_date)):
        raise V2InputError("INPUT_MISMATCH", "V2_INPUT_SOURCE_DATE_MISMATCH", dates=dates, expectedDate=expected_date)
    source_date = next(iter(unique_dates))
    if explicit_hashes and len(set(explicit_hashes.values())) != 1:
        raise V2InputError("INPUT_MISMATCH", "V2_INPUT_SOURCE_HASH_MISMATCH", hashes=explicit_hashes)
    source_hash = next(iter(explicit_hashes.values()), None)
    if not source_hash:
        source_hash = _sha256(json.dumps(
            {kind: _sha256(raw_by_kind[kind]) for kind, _ in INPUT_FILES},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8"))
    lineage = {
        "v2ObservationId": v2_observation_id(source_date=source_date, observation_date=source_date, source_hash=source_hash),
        "source": SOURCE,
        "sourceDate": source_date,
        "observationDate": source_date,
        "sourceHash": source_hash,
        "generatedAt": None,
        "producerRunId": None,
        "artifactPath": "data/dynamic-universe/v2-shadow-latest.json",
        "inputArtifactPath": paths["breadth"],
        "inputArtifactSha256": _sha256(raw_by_kind["breadth"]),
        "selectionPolicyVersion": SELECTION_POLICY_VERSION,
    }
    evidence = {
        "source": SOURCE, "sourceDate": source_date, "observationDate": source_date,
        "sourceHash": source_hash,
        "files": [{"kind": kind, "path": paths[kind], "sha256": _sha256(raw_by_kind[kind]), "date": dates[kind]}
                  for kind, _ in INPUT_FILES],
    }
    return payloads, {"lineage": lineage, "inputEvidence": evidence}


def _history_context(root: Path, trading_date: str) -> Dict[str, Dict[str, Any]]:
    """Read history for persistence diagnostics, never as current input."""
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
    payload = dict(payload)
    rows = []
    for source_row in payload.get(kind, []) if isinstance(payload.get(kind), list) else []:
        row = dict(source_row)
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


def normalize_run_status(payload: Mapping[str, Any] | None, *, run_id: str | None = None,
                          runner_exit_code: int = 0, generated_at: str | None = None) -> Dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    normalized = dict(payload) if isinstance(payload, Mapping) else {
        "status": "FAILED", "errorType": "V2_RUNNER_PROCESS_FAILED", "error": f"runner_exit={runner_exit_code}",
    }
    raw_status = str(normalized.get("status") or "FAILED")
    lineage = dict(normalized.get("v2Lineage") or {}) if isinstance(normalized.get("v2Lineage"), Mapping) else {}
    effective = raw_status
    reason = normalized.get("failureReason") or normalized.get("error")
    if raw_status in {"OK", "SUCCESS"}:
        required = ("v2ObservationId", "source", "sourceDate", "observationDate", "sourceHash", "selectionPolicyVersion")
        if any(not lineage.get(key) for key in required):
            effective, reason = "INPUT_UNAVAILABLE", "V2_LINEAGE_INCOMPLETE"
        else:
            effective = "SUCCESS"
    normalized.update({
        "runId": normalized.get("runId") or run_id, "generatedAt": normalized.get("generatedAt") or generated_at,
        "rawStatus": raw_status, "producerStatus": raw_status, "runStatus": raw_status, "status": effective,
        "consumptionStatus": "CONSUMED" if effective == "SUCCESS" else "NOT_CONSUMED",
        "publicationPolicy": "PARTIAL_NON_BLOCKING",
        "failureReason": None if effective == "SUCCESS" else reason or "V2_SHADOW_FAILED",
    })
    if lineage:
        lineage.update({"generatedAt": lineage.get("generatedAt") or normalized["generatedAt"],
                        "producerRunId": lineage.get("producerRunId") or normalized["runId"],
                        "producerStatus": normalized["producerStatus"],
                        "consumptionStatus": normalized["consumptionStatus"],
                        "failureReason": normalized["failureReason"]})
        normalized["v2Lineage"] = lineage
    normalized.setdefault("artifactPath", "data/dynamic-universe/v2-shadow-latest.json")
    return normalized


def run_once(root: Path, trading_date: str | None, observed_at: str | None = None) -> Dict[str, Any]:
    status_path = root / "data/dynamic-universe/v2-shadow-run-status.json"
    latest_path = root / "data/dynamic-universe/v2-shadow-latest.json"
    history_path = root / "data/dynamic-universe/v2-shadow-history.json"
    generated_at = datetime.now(timezone.utc).isoformat()
    run_id = os.environ.get("GITHUB_RUN_ID") or f"local-v2-shadow-{generated_at.replace(':', '').replace('-', '')}"
    prior_latest = latest_path.read_bytes() if latest_path.exists() else None
    prior_history = history_path.read_bytes() if history_path.exists() else None
    try:
        inputs, context = _read_current_inputs(root, trading_date)
        source_date = context["lineage"]["sourceDate"]
        history_context = _history_context(root, source_date)
        ind = _enrich_persistence(inputs["industries"], "industries", history_context)
        theme = _enrich_persistence(inputs["themes"], "themes", history_context)
        try:
            v1 = json.loads((root / "data/dynamic-universe/latest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            v1 = {"sectors": [], "themes": []}
        result = evaluate_snapshot(ind, theme, v1)
        lineage = dict(context["lineage"])
        lineage.update({"generatedAt": generated_at, "producerRunId": run_id, "producerStatus": "SUCCESS",
                        "consumptionStatus": "CONSUMED", "failureReason": None})
        result.update({"v2Lineage": lineage, "source": SOURCE, "sourceDate": source_date,
                       "observationDate": source_date, "inputEvidence": context["inputEvidence"]})
        result["summary"] = summarize(result)
        latest_path.parent.mkdir(parents=True, exist_ok=True)
        latest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        history = record_current(root, source_date, {"inputSnapshotSha256": lineage["sourceHash"]})
        outcome = {"runId": run_id, "status": "SUCCESS", "observedAt": observed_at or generated_at,
                   "generatedAt": generated_at, "tradingDate": source_date,
                   "v2ObservationId": lineage["v2ObservationId"], "v2Lineage": lineage,
                   "summary": result["summary"], "inputEvidence": result["inputEvidence"],
                   "artifactPath": str(latest_path), "observationCount": len(history["observations"])}
    except V2InputError as exc:
        diagnostic_lineage = {
            "source": SOURCE,
            "producerRunId": run_id,
            "artifactPath": "data/dynamic-universe/v2-shadow-latest.json",
            "selectionPolicyVersion": SELECTION_POLICY_VERSION,
            "producerStatus": exc.status,
            "consumptionStatus": "NOT_CONSUMED",
            "failureReason": exc.reason,
        }
        if isinstance(exc.details.get("inputArtifactPath"), str):
            diagnostic_lineage["inputArtifactPath"] = exc.details["inputArtifactPath"]
        if isinstance(exc.details.get("dates"), Mapping) and len(set(exc.details["dates"].values())) == 1:
            diagnostic_lineage["sourceDate"] = next(iter(exc.details["dates"].values()))
            diagnostic_lineage["observationDate"] = diagnostic_lineage["sourceDate"]
        outcome = {"runId": run_id, "status": exc.status, "observedAt": observed_at or generated_at,
                   "generatedAt": generated_at, "tradingDate": trading_date, "errorType": type(exc).__name__,
                   "error": exc.reason, "failureReason": exc.reason, "inputEvidence": exc.details,
                   "artifactPath": str(latest_path), "v2Lineage": diagnostic_lineage}
    except Exception as exc:
        outcome = {"runId": run_id, "status": "FAILED", "observedAt": observed_at or generated_at,
                   "generatedAt": generated_at, "tradingDate": trading_date, "errorType": type(exc).__name__,
                   "error": str(exc)[:240], "failureReason": f"V2_SHADOW_{type(exc).__name__.upper()}",
                   "artifactPath": str(latest_path)}
    if outcome.get("status") != "SUCCESS":
        if prior_latest is not None:
            latest_path.parent.mkdir(parents=True, exist_ok=True)
            latest_path.write_bytes(prior_latest)
        elif latest_path.exists():
            latest_path.unlink()
        if prior_history is not None:
            history_path.parent.mkdir(parents=True, exist_ok=True)
            history_path.write_bytes(prior_history)
        elif history_path.exists():
            history_path.unlink()
    outcome = normalize_run_status(outcome, run_id=run_id,
                                   runner_exit_code=0 if outcome.get("status") == "SUCCESS" else 1,
                                   generated_at=generated_at)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return outcome


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    print(json.dumps(run_once(root, None), ensure_ascii=False))
