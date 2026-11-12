"""Projection helpers for configured additional industry/theme observations.

This module is intentionally a consumer-side contract boundary.  It never
fetches NAVER data and never falls back to a previous or generic artifact.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SCHEMA_VERSION = "additional-observations-contract-v1"
ARTIFACT_SCHEMA_VERSION = "additional-observations-artifact-v1"


def _date(value: Any) -> str | None:
    text = str(value or "").strip().replace("-", "")
    return text if len(text) == 8 and text.isdigit() else None


def _target_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") or row.get("code") or row.get("name") or "").strip()


def _targets(config: Mapping[str, Any] | None, kind: str) -> list[dict[str, Any]]:
    values = config.get(kind) if isinstance(config, Mapping) else []
    if not isinstance(values, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, Mapping):
            continue
        target_id = _target_id(value)
        if not target_id or target_id in seen:
            continue
        seen.add(target_id)
        result.append({"id": target_id, "name": value.get("name") or target_id, "type": kind})
    return result


def _auto_select_enabled(config: Mapping[str, Any] | None) -> bool:
    return bool(config.get("autoSelect", True)) if isinstance(config, Mapping) else True


def _artifact_targets(artifact: Mapping[str, Any] | None, kind: str) -> list[dict[str, Any]]:
    key = "industries" if kind == "industries" else "themes"
    rows = artifact.get(key) if isinstance(artifact, Mapping) else []
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        target_id = _target_id(row)
        if not target_id or target_id in seen:
            continue
        seen.add(target_id)
        result.append({"id": target_id, "name": row.get("name") or target_id, "type": kind})
    return result


def _metadata(artifact: Mapping[str, Any] | None) -> dict[str, Any]:
    source = artifact if isinstance(artifact, Mapping) else {}
    return {
        "source": source.get("source"),
        "sourceDate": source.get("sourceDate"),
        "observationDate": source.get("observationDate"),
        "scope": source.get("scope"),
        "producer": source.get("producer"),
        "producerRunId": source.get("producerRunId"),
        "generatedAt": source.get("generatedAt"),
        "artifactPath": source.get("artifactPath"),
    }

def _row_from_artifact(artifact: Mapping[str, Any], target: Mapping[str, Any]) -> Mapping[str, Any] | None:
    key = "industries" if target["type"] == "industries" else "themes"
    rows = artifact.get(key)
    if not isinstance(rows, list):
        return None
    target_id = target["id"]
    return next(
        (row for row in rows if isinstance(row, Mapping) and _target_id(row) == target_id),
        None,
    )


def _failure_row(target: Mapping[str, Any], metadata: Mapping[str, Any], status: str, reason: str) -> dict[str, Any]:
    return {
        "source": metadata.get("source"),
        "sourceDate": metadata.get("sourceDate"),
        "observationDate": metadata.get("observationDate"),
        "scope": metadata.get("scope") or "STOCK_ONLY",
        "producerRunId": metadata.get("producerRunId"),
        "producer": metadata.get("producer"),
        "artifactPath": metadata.get("artifactPath"),
        "generatedAt": metadata.get("generatedAt"),
        "id": target["id"],
        "name": target["name"],
        "type": "industry" if target["type"] == "industries" else "theme",
        "membershipCount": None,
        "leaderCount": 0,
        "leaders": [],
        "status": status,
        "failureReason": reason,
    }


def _project_row(target: Mapping[str, Any], artifact: Mapping[str, Any], row: Mapping[str, Any], expected_date: str | None) -> dict[str, Any]:
    metadata = _metadata(artifact)
    source_date = _date(row.get("sourceDate") or metadata.get("sourceDate"))
    observation_date = _date(row.get("observationDate") or metadata.get("observationDate"))
    leaders = row.get("leaders") or row.get("leaderCandidates") or []
    leaders = [dict(item) for item in leaders[:3] if isinstance(item, Mapping)]
    membership_count = row.get("membershipCount")
    if membership_count is None:
        membership_count = row.get("memberCount")
    leader_count = row.get("leaderCount")
    if leader_count is None:
        leader_count = len(leaders)
    status = str(row.get("status") or artifact.get("status") or "UNAVAILABLE").upper()
    reason = row.get("failureReason") or row.get("reason")
    if expected_date and (source_date != expected_date or observation_date != expected_date):
        status, reason = "UNAVAILABLE", "OBSERVATION_DATE_MISMATCH"
    else:
        lineage_values = {
            "source": row.get("source") or metadata.get("source"),
            "sourceDate": source_date,
            "observationDate": observation_date,
            "scope": row.get("scope") or metadata.get("scope"),
            "producer": row.get("producer") or metadata.get("producer"),
            "artifactPath": row.get("artifactPath") or metadata.get("artifactPath"),
            "generatedAt": row.get("generatedAt") or metadata.get("generatedAt"),
        }
        missing_lineage = [key for key, value in lineage_values.items() if value in (None, "")]
        if missing_lineage:
            status, reason = "UNAVAILABLE", f"ADDITIONAL_LINEAGE_MISSING:{','.join(missing_lineage)}"
        elif not leaders and status in {"AVAILABLE", "OK", "SUCCESS"}:
            status, reason = "UNAVAILABLE", reason or "NO_LEADER_DATA"
        elif status in {"OK", "SUCCESS"}:
            status = "AVAILABLE"
    return {
        **metadata,
        "id": target["id"],
        "name": row.get("name") or target["name"],
        "type": "industry" if target["type"] == "industries" else "theme",
        "source": row.get("source") or metadata.get("source"),
        "sourceDate": source_date,
        "observationDate": observation_date,
        "scope": row.get("scope") or metadata.get("scope"),
        "producer": row.get("producer") or metadata.get("producer"),
        "producerRunId": row.get("producerRunId") or metadata.get("producerRunId"),
        "artifactPath": row.get("artifactPath") or (
            f"{metadata.get('artifactPath')}#{target['type']}/{target['id']}"
            if metadata.get("artifactPath") else None
        ),
        "generatedAt": row.get("generatedAt") or metadata.get("generatedAt"),
        "membershipCount": membership_count,
        "leaderCount": int(leader_count or 0),
        "leaders": leaders,
        "selectionRank": row.get("selectionRank"),
        "selectionScore": row.get("selectionScore"),
        "selectionReason": row.get("selectionReason"),
        "selectionEvidence": row.get("selectionEvidence"),
        "status": status,
        "failureReason": reason,
    }


def build_additional_observation_projection(
    config: Mapping[str, Any] | None,
    artifact: Mapping[str, Any] | None,
    *,
    observation_date: Any,
    now: str | None = None,
    config_path: str = "config/additional-observations.json",
    artifact_path: str = "data/monitoring/additional-observations.json",
) -> dict[str, Any]:
    """Build a date-bound, non-fallback projection for report consumption."""
    expected = _date(observation_date)
    industries = _targets(config, "industries")
    themes = _targets(config, "themes")
    auto_select = _auto_select_enabled(config)
    if auto_select and not industries and not themes:
        industries = _artifact_targets(artifact, "industries")
        themes = _artifact_targets(artifact, "themes")
    targets = industries + themes
    if not targets:
        if auto_select:
            artifact_status = str((artifact or {}).get("status") or "").upper() if isinstance(artifact, Mapping) else ""
            if artifact_status == "NO_ELIGIBLE_TARGETS":
                status = "NO_ELIGIBLE_TARGETS"
                reason = (artifact or {}).get("failureReason") or "NO_QUALIFIED_ADDITIONAL_TARGETS"
            elif artifact_status == "PRODUCER_FAILED":
                status = "PRODUCER_FAILED"
                reason = (artifact or {}).get("failureReason") or "PRODUCER_FAILED"
            else:
                status = "INPUT_UNAVAILABLE"
                reason = "AUTO_SELECTION_INPUT_UNAVAILABLE"
            return {
                "schemaVersion": SCHEMA_VERSION,
                "status": status,
                "source": (artifact or {}).get("source") if isinstance(artifact, Mapping) else None,
                "sourceDate": (artifact or {}).get("sourceDate") if isinstance(artifact, Mapping) else None,
                "observationDate": expected,
                "scope": "STOCK_ONLY",
                "producer": (artifact or {}).get("producer") if isinstance(artifact, Mapping) else None,
                "producerRunId": (artifact or {}).get("producerRunId") if isinstance(artifact, Mapping) else None,
                "generatedAt": (artifact or {}).get("generatedAt") if isinstance(artifact, Mapping) else now,
                "configPath": config_path,
                "artifactPath": artifact_path,
                "industries": [],
                "themes": [],
                "counts": {"configured": 0, "received": 0, "available": 0, "unavailable": 0},
                "failureReason": reason,
            }
        return {
            "schemaVersion": SCHEMA_VERSION,
            "status": "PRODUCER_NOT_CONFIGURED",
            "source": None,
            "sourceDate": None,
            "observationDate": expected,
            "scope": "STOCK_ONLY",
            "producer": None,
            "producerRunId": None,
            "generatedAt": now,
            "configPath": config_path,
            "artifactPath": artifact_path,
            "industries": [],
            "themes": [],
            "counts": {"configured": 0, "received": 0, "available": 0, "unavailable": 0},
            "failureReason": "ADDITIONAL_OBSERVATION_TARGETS_NOT_CONFIGURED",
        }

    if not isinstance(artifact, Mapping) or not artifact:
        rows = [_failure_row(target, {"observationDate": expected, "scope": "STOCK_ONLY"}, "NOT_YET_RUN", "PRODUCER_NOT_RUN") for target in targets]
        return {
            "schemaVersion": SCHEMA_VERSION,
            "status": "NOT_YET_RUN",
            "source": None,
            "sourceDate": None,
            "observationDate": expected,
            "scope": "STOCK_ONLY",
            "producer": None,
            "producerRunId": None,
            "generatedAt": now,
            "configPath": config_path,
            "artifactPath": artifact_path,
            "industries": [row for row in rows if row["type"] == "industry"],
            "themes": [row for row in rows if row["type"] == "theme"],
            "counts": {"configured": len(targets), "received": 0, "available": 0, "unavailable": len(targets)},
            "failureReason": "PRODUCER_NOT_RUN",
        }

    metadata = _metadata(artifact)
    artifact_source_date = _date(artifact.get("sourceDate"))
    artifact_observation_date = _date(artifact.get("observationDate"))
    artifact_status = str(artifact.get("status") or "UNAVAILABLE").upper()
    stale = expected and (artifact_source_date != expected or artifact_observation_date != expected)
    if stale:
        rows = [_failure_row(target, metadata, "UNAVAILABLE", "STALE_ARTIFACT") for target in targets]
        overall = "PRODUCER_FAILED"
        failure = "STALE_ARTIFACT"
    elif artifact_status in {"FAILED", "ERROR", "PRODUCER_FAILED"}:
        rows = [_failure_row(target, metadata, "PRODUCER_FAILED", artifact.get("failureReason") or "PRODUCER_FAILED") for target in targets]
        overall = "PRODUCER_FAILED"
        failure = artifact.get("failureReason") or "PRODUCER_FAILED"
    else:
        rows = []
        for target in targets:
            source_row = _row_from_artifact(artifact, target)
            rows.append(
                _project_row(target, artifact, source_row, expected)
                if source_row is not None
                else _failure_row(target, metadata, "UNAVAILABLE", "TARGET_OUTPUT_MISSING")
            )
        available = sum(row["status"] == "AVAILABLE" for row in rows)
        overall = "AVAILABLE" if available == len(rows) else "PARTIAL"
        failure = None if available == len(rows) else "ADDITIONAL_OBSERVATION_PARTIAL"

    available = sum(row["status"] == "AVAILABLE" for row in rows)
    received = sum(row["status"] not in {"NOT_YET_RUN", "PRODUCER_FAILED"} for row in rows)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": overall,
        "source": metadata.get("source"),
        "sourceDate": metadata.get("sourceDate"),
        "observationDate": metadata.get("observationDate") or expected,
        "scope": metadata.get("scope") or "STOCK_ONLY",
        "producer": metadata.get("producer"),
        "producerRunId": metadata.get("producerRunId"),
        "generatedAt": metadata.get("generatedAt") or now,
        "configPath": config_path,
        "artifactPath": artifact_path,
        "industries": [row for row in rows if row["type"] == "industry"],
        "themes": [row for row in rows if row["type"] == "theme"],
        "counts": {"configured": len(targets), "received": received, "available": available, "unavailable": len(rows) - available},
        "failureReason": failure,
    }
