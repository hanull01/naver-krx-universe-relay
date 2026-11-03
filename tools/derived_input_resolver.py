"""Strict reader for the published full-market derived generation.

The resolver is intentionally read-only.  It resolves the dated ``latest``
pointer, validates that every report input belongs to the same generation,
and exposes rolling history as the legacy daily-loader shape used by the
intraday relay.  It never falls back to ``data/daily`` or other production
snapshots.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from tools.file_digest import sha256_bytes, sha256_file


DATE_RE = re.compile(r"^\d{8}$")
REQUIRED_ARTIFACTS = (
    "technicals.json",
    "states.json",
    "group-states.json",
    "report-contract-projection.json",
    "manifest.json",
    "rolling-history-manifest.json",
    "report-contract-manifest.json",
)
REPORT_CONTRACT_SOURCE_ARTIFACTS = (
    "technicals.json",
    "states.json",
    "registered-universe-technicals.json",
    "registered-universe-states.json",
    "group-states.json",
    "provenance.json",
    "quality.json",
    "manifest.json",
    "rolling-history-manifest.json",
)
REPORT_CONTRACT_MANIFEST = "report-contract-manifest.json"


class DerivedInputError(ValueError):
    """Raised when a published derived generation is missing or inconsistent."""


def compact_date(value: Any, field: str = "targetDate") -> str:
    text = str(value or "").replace("-", "")
    if not DATE_RE.fullmatch(text):
        raise DerivedInputError(f"INVALID_{field.upper()}:{value!r}")
    return text


def filesystem_date(value: Any, field: str = "targetDate") -> str:
    """Return the canonical dashed date used by derived filesystem paths."""
    text = compact_date(value, field)
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def _read_json(
    path: Path,
    cache: dict[Path, tuple[dict[str, Any], str, int]] | None = None,
) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DerivedInputError(f"DERIVED_JSON_READ_FAILED:{path}:{exc}") from exc
    if not isinstance(payload, dict):
        raise DerivedInputError(f"DERIVED_JSON_OBJECT_REQUIRED:{path}")
    if cache is not None:
        cache[path] = (payload, sha256_bytes(raw), len(raw))
    return payload



def _verify_artifact_record(
    root: Path,
    name: str,
    record: Mapping[str, Any],
    run_id: str,
    cache: dict[Path, tuple[dict[str, Any], str, int]] | None = None,
) -> Path:
    """Verify a manifest record against the immutable file in this run."""
    if not isinstance(record, Mapping):
        raise DerivedInputError(f"DERIVED_ARTIFACT_METADATA_MISSING:{name}")
    declared_path = record.get("path")
    if not isinstance(declared_path, str) or Path(declared_path).name != name:
        raise DerivedInputError(f"DERIVED_ARTIFACT_PATH_MISMATCH:{name}")
    declared_run_id = record.get("runId")
    if declared_run_id not in (None, "") and str(declared_run_id) != run_id:
        raise DerivedInputError(
            f"DERIVED_ARTIFACT_RUN_ID_MISMATCH:{name}:{declared_run_id}!={run_id}"
        )
    expected_sha = str(record.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha):
        raise DerivedInputError(f"DERIVED_ARTIFACT_SHA256_MISSING:{name}")
    path = root / name
    if not path.is_file():
        raise DerivedInputError(f"DERIVED_ARTIFACT_MISSING:{name}")
    cached = cache.get(path) if cache is not None else None
    if cached is None:
        actual_sha = sha256_file(path)
        actual_bytes = path.stat().st_size
    else:
        actual_sha, actual_bytes = cached[1], cached[2]
    if actual_sha.lower() != expected_sha.lower():
        raise DerivedInputError(f"DERIVED_ARTIFACT_SHA256_MISMATCH:{name}")
    declared_bytes = record.get("bytes")
    if declared_bytes not in (None, "") and int(declared_bytes) != actual_bytes:
        raise DerivedInputError(f"DERIVED_ARTIFACT_BYTES_MISMATCH:{name}")
    return path


def _verify_artifact_metadata_consistency(
    name: str,
    left: Mapping[str, Any] | None,
    right: Mapping[str, Any] | None,
) -> None:
    if not left or not right:
        return
    if str(left.get("sha256") or "").lower() != str(right.get("sha256") or "").lower():
        raise DerivedInputError(f"DERIVED_ARTIFACT_METADATA_SHA_MISMATCH:{name}")
    if left.get("bytes") not in (None, "") and right.get("bytes") not in (None, ""):
        if int(left["bytes"]) != int(right["bytes"]):
            raise DerivedInputError(f"DERIVED_ARTIFACT_METADATA_BYTES_MISMATCH:{name}")


def _has_fallback(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("fallbackUsed") is True:
            return True
        return any(_has_fallback(child) for child in value.values())
    if isinstance(value, list):
        return any(_has_fallback(child) for child in value)
    return False


def _target(payload: Mapping[str, Any], name: str) -> str | None:
    if name == "report-contract-projection.json":
        value = payload.get("asOf")
    elif name == "manifest.json":
        source = payload.get("source")
        if not isinstance(source, Mapping) or source.get("targetDate") in (None, ""):
            raise DerivedInputError("DERIVED_MANIFEST_SOURCE_DATE_MISSING")
        value = source.get("targetDate")
    else:
        value = payload.get("targetDate")
    return compact_date(value, f"{name}.targetDate") if value not in (None, "") else None


def validate_derived_run(run_dir: str | Path, target_date: str, expected_run_id: str | None = None) -> dict[str, Any]:
    """Validate one immutable derived run and return its decoded artifacts."""
    root = Path(run_dir)
    target = compact_date(target_date)
    if not root.is_dir():
        raise DerivedInputError(f"DERIVED_RUN_NOT_FOUND:{root}")
    run_id = root.name
    if expected_run_id is not None and str(expected_run_id) != run_id:
        raise DerivedInputError(f"DERIVED_RUN_ID_MISMATCH:{run_id}!={expected_run_id}")

    paths = {name: root / name for name in REQUIRED_ARTIFACTS}
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise DerivedInputError(f"DERIVED_ARTIFACT_MISSING:{','.join(missing)}")

    cache: dict[Path, tuple[dict[str, Any], str, int]] = {}
    artifacts = {name: _read_json(path, cache) for name, path in paths.items()}
    for name, payload in artifacts.items():
        observed = _target(payload, name)
        if observed != target:
            raise DerivedInputError(f"DERIVED_TARGET_DATE_MISMATCH:{name}:{observed}!={target}")
        if _has_fallback(payload):
            raise DerivedInputError(f"DERIVED_FALLBACK_USED:{name}")

    manifest = artifacts["manifest.json"]
    source = manifest.get("source")
    if not isinstance(source, Mapping) or compact_date(source.get("targetDate"), "manifest.source.targetDate") != target:
        raise DerivedInputError("DERIVED_MANIFEST_SOURCE_DATE_MISMATCH")
    manifest_run_id = manifest.get("runId")
    if manifest_run_id not in (None, "") and str(manifest_run_id) != run_id:
        raise DerivedInputError(f"DERIVED_MANIFEST_RUN_ID_MISMATCH:{manifest_run_id}!={run_id}")

    report_manifest = artifacts[REPORT_CONTRACT_MANIFEST]
    report_manifest_run_id = report_manifest.get("runId")
    if report_manifest_run_id not in (None, "") and str(report_manifest_run_id) != run_id:
        raise DerivedInputError(
            f"DERIVED_REPORT_MANIFEST_RUN_ID_MISMATCH:{report_manifest_run_id}!={run_id}"
        )
    contract = report_manifest.get("contract")
    if not isinstance(contract, Mapping) or Path(str(contract.get("path") or "")).name != "report-contract-projection.json":
        raise DerivedInputError("DERIVED_REPORT_CONTRACT_PATH_MISMATCH")
    _verify_artifact_record(
        root,
        "report-contract-projection.json",
        contract,
        run_id,
        cache,
    )

    source_artifacts = report_manifest.get("sourceArtifacts")
    if not isinstance(source_artifacts, Mapping):
        raise DerivedInputError("DERIVED_REPORT_SOURCE_ARTIFACTS_MISSING")
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, Mapping):
        manifest_files = {}
    source_payloads: dict[str, dict[str, Any]] = {}
    for name in REPORT_CONTRACT_SOURCE_ARTIFACTS:
        source_record = source_artifacts.get(name)
        _verify_artifact_record(root, name, source_record, run_id, cache)
        _verify_artifact_metadata_consistency(
            name,
            manifest_files.get(name),
            source_record,
        )
        source_path = root / name
        cached = cache.get(source_path)
        source_payloads[name] = cached[0] if cached is not None else _read_json(source_path, cache)
        if _has_fallback(source_payloads[name]):
            raise DerivedInputError(f"DERIVED_FALLBACK_USED:{name}")

    rolling_manifest = source_payloads["rolling-history-manifest.json"]
    for code, entry in (rolling_manifest.get("byCode") or {}).items():
        if not isinstance(entry, Mapping):
            continue
        latest = entry.get("latest")
        if latest not in (None, "") and compact_date(latest, f"{code}.latest") > target:
            raise DerivedInputError(f"ROLLING_HISTORY_FUTURE_DATA:{code}:{latest}")

    generated_at_by_artifact = {
        name: str(payload.get("generatedAt"))
        for name, payload in artifacts.items()
        if payload.get("generatedAt")
    }
    projection_observed = artifacts["report-contract-projection.json"].get("observedAt")
    if projection_observed:
        generated_at_by_artifact["report-contract-projection.json"] = str(projection_observed)
    generated_values = set(generated_at_by_artifact.values())
    generation_diagnostic = {
        "generatedAtByArtifact": generated_at_by_artifact,
        "generatedAtMismatch": len(generated_values) > 1,
    }

    rolling_dir = root / "rolling-history"
    if not rolling_dir.is_dir():
        raise DerivedInputError(f"ROLLING_HISTORY_NOT_FOUND:{rolling_dir}")
    if int(rolling_manifest.get("window") or 0) != 120:
        raise DerivedInputError("ROLLING_HISTORY_WINDOW_INVALID")

    return {
        "targetDate": target,
        "runId": run_id,
        "runDir": root,
        "files": paths,
        "artifacts": artifacts,
        "generatedAt": manifest.get("generatedAt") or next(iter(generated_values), None),
        "generationDiagnostic": generation_diagnostic,
        "rollingHistoryDir": rolling_dir,
    }


def resolve_derived_input(root: str | Path, target_date: str) -> dict[str, Any]:
    """Resolve and validate ``data/monitoring-derived/<date>/latest.json``."""
    base = Path(root)
    target = compact_date(target_date)
    pointer_path = base / filesystem_date(target) / "latest.json"
    pointer = _read_json(pointer_path)
    if compact_date(pointer.get("targetDate"), "pointer.targetDate") != target:
        raise DerivedInputError("DERIVED_POINTER_TARGET_DATE_MISMATCH")
    run_id = str(pointer.get("runId") or "")
    if not run_id or Path(run_id).name != run_id:
        raise DerivedInputError("DERIVED_POINTER_RUN_ID_INVALID")
    relative = pointer.get("path")
    if not isinstance(relative, str) or not relative:
        raise DerivedInputError("DERIVED_POINTER_PATH_MISSING")
    manifest_path = (pointer_path.parent / relative).resolve()
    expected_parent = pointer_path.parent.resolve()
    if expected_parent not in manifest_path.parents:
        raise DerivedInputError("DERIVED_POINTER_PATH_ESCAPES_DATE_DIRECTORY")
    run_dir = manifest_path.parent
    if run_dir.name != run_id:
        raise DerivedInputError("DERIVED_POINTER_RUN_DIRECTORY_MISMATCH")
    result = validate_derived_run(run_dir, target, expected_run_id=run_id)
    result["pointerPath"] = pointer_path
    result["pointer"] = pointer
    return result


class RollingHistoryDailyLoader:
    """Adapt validated derived rolling history to relay's daily input shape."""

    def __init__(self, resolved: Mapping[str, Any]):
        self.target_date = str(resolved["targetDate"])
        self.run_dir = Path(resolved["runDir"])
        self.history_dir = Path(resolved["rollingHistoryDir"])
        self.manifest = resolved["artifacts"]["rolling-history-manifest.json"]
        self._cache: dict[str, dict[str, Any] | None] = {}

    def load(self, code: str) -> dict[str, Any] | None:
        code = str(code)
        if code in self._cache:
            return self._cache[code]
        entry = (self.manifest.get("byCode") or {}).get(code)
        if not isinstance(entry, Mapping):
            self._cache[code] = None
            return None
        relative = entry.get("path")
        if not isinstance(relative, str):
            raise DerivedInputError(f"ROLLING_HISTORY_PATH_MISSING:{code}")
        path = (self.run_dir / relative).resolve()
        if self.history_dir.resolve() not in path.parents:
            raise DerivedInputError(f"ROLLING_HISTORY_PATH_INVALID:{code}")
        expected_sha = str(entry.get("sha256") or "")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha):
            raise DerivedInputError(f"ROLLING_HISTORY_SHA256_MISSING:{code}")
        raw = path.read_bytes()
        if sha256_bytes(raw).lower() != expected_sha.lower():
            raise DerivedInputError(f"ROLLING_HISTORY_SHA256_MISMATCH:{code}")
        rows = []
        for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DerivedInputError(f"ROLLING_HISTORY_INVALID:{code}:{line_number}") from exc
            observed = compact_date(row.get("date"), f"{code}.date")
            if observed > self.target_date:
                raise DerivedInputError(f"ROLLING_HISTORY_FUTURE_DATA:{code}:{observed}")
            rows.append({
                **row,
                "date": f"{observed[:4]}-{observed[4:6]}-{observed[6:]}",
                "complete": True,
                "noTrading": bool(row.get("volume") == 0),
                "session": "ROLLING_HISTORY",
                "barType": "DERIVED_ROLLING_HISTORY",
                "source": "FULL_MARKET_DERIVED_ROLLING_HISTORY",
            })
        rows.sort(key=lambda row: row["date"])
        latest = rows[-1]["date"] if rows else None
        source_time = f"{latest}T20:00:00+09:00" if latest else None
        result = {
            "status": "ok" if rows else "unavailable",
            "datas": rows,
            "targetDate": f"{self.target_date[:4]}-{self.target_date[4:6]}-{self.target_date[6:]}",
            "sourceTime": source_time,
            "source": "FULL_MARKET_DERIVED_ROLLING_HISTORY",
            "finalityStatus": "SOURCE_OBSERVED",
            "fallbackUsed": False,
        }
        self._cache[code] = result
        return result
