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


DATE_RE = re.compile(r"^\d{8}$")
REQUIRED_ARTIFACTS = (
    "technicals.json",
    "states.json",
    "group-states.json",
    "report-contract-projection.json",
    "manifest.json",
    "rolling-history-manifest.json",
)


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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DerivedInputError(f"DERIVED_JSON_READ_FAILED:{path}:{exc}") from exc
    if not isinstance(payload, dict):
        raise DerivedInputError(f"DERIVED_JSON_OBJECT_REQUIRED:{path}")
    return payload


def _has_fallback(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("fallbackUsed") is True:
            return True
        return any(_has_fallback(child) for child in value.values())
    if isinstance(value, list):
        return any(_has_fallback(child) for child in value)
    return False


def _target(payload: Mapping[str, Any], name: str) -> str | None:
    value = payload.get("asOf") if name == "report-contract-projection.json" else payload.get("targetDate")
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

    artifacts = {name: _read_json(path) for name, path in paths.items()}
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

    generated_values = {
        str(payload.get("generatedAt"))
        for name, payload in artifacts.items()
        if name != "report-contract-projection.json" and payload.get("generatedAt")
    }
    projection_observed = artifacts["report-contract-projection.json"].get("observedAt")
    if projection_observed:
        generated_values.add(str(projection_observed))
    if len(generated_values) > 1:
        raise DerivedInputError("DERIVED_GENERATION_MISMATCH")

    rolling_dir = root / "rolling-history"
    if not rolling_dir.is_dir():
        raise DerivedInputError(f"ROLLING_HISTORY_NOT_FOUND:{rolling_dir}")
    rolling_manifest = artifacts["rolling-history-manifest.json"]
    if int(rolling_manifest.get("window") or 0) != 120:
        raise DerivedInputError("ROLLING_HISTORY_WINDOW_INVALID")

    return {
        "targetDate": target,
        "runId": run_id,
        "runDir": root,
        "files": paths,
        "artifacts": artifacts,
        "generatedAt": next(iter(generated_values), None),
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
        rows = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
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
