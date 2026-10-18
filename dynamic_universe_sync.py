"""Validated, atomic pull of published dynamic-Universe artifacts.

The caller supplies an already authenticated/read-only fetch function.  This
module never edits config/universe.json and never changes USER_WATCHLIST data.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from dynamic_universe_symbols import normalize_symbol


class SyncError(ValueError):
    pass


def _read_json(raw):
    try:
        value = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise SyncError("INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise SyncError("INVALID_ROOT")
    return value


def _instant(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise SyncError("INVALID_GENERATED_AT") from exc


def validate(latest, history, commit, now=None, max_age_seconds=172800):
    if latest.get("version") != "dynamic-universe-management-v1":
        raise SyncError("SCHEMA_VERSION_INVALID")
    if history.get("version") != "dynamic-universe-history-v1" or not isinstance(history.get("events"), list):
        raise SyncError("HISTORY_SCHEMA_INVALID")
    if not isinstance(commit, str) or len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit.lower()):
        raise SyncError("COMMIT_INVALID")
    generated = _instant(latest.get("generatedAt"))
    current = _instant(now or datetime.now(timezone.utc).isoformat())
    age = (current - generated).total_seconds()
    if age < -60 or age > max_age_seconds:
        raise SyncError("PUBLISHED_RESULT_STALE")
    codes = latest.get("collectionCodes")
    if not isinstance(codes, list) or len(codes) != len(set(codes)):
        raise SyncError("COLLECTION_CODES_INVALID")
    normalized = [normalize_symbol(code) for code in codes]
    if any(code is None for code in normalized) or normalized != codes:
        raise SyncError("COLLECTION_CODE_INVALID")
    for row in latest.get("memberships", []):
        if not isinstance(row, dict) or normalize_symbol(row.get("code")) not in codes:
            raise SyncError("MEMBERSHIP_INVALID")
    return {"generatedAt": generated.isoformat(), "ageSeconds": round(age, 3),
            "commit": commit, "collectionCount": len(codes)}


def _atomic_write(path, payload):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def sync(fetch, latest_path, history_path, state_path, now=None, max_age_seconds=172800):
    """Fetch ``latest`` and ``history`` as ``fetch(path)->(bytes, commit)``."""
    latest_raw, latest_commit = fetch("data/dynamic-universe/latest.json")
    history_raw, history_commit = fetch("data/dynamic-universe/history.json")
    if latest_commit != history_commit:
        raise SyncError("ARTIFACT_COMMITS_MISMATCH")
    latest, history = _read_json(latest_raw), _read_json(history_raw)
    info = validate(latest, history, latest_commit, now, max_age_seconds)
    state = {}
    try:
        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    latest_sha = hashlib.sha256(latest_raw).hexdigest()
    if state.get("commit") == latest_commit:
        return {"status": "NOOP", **info}
    if state.get("generatedAt") == latest["generatedAt"]:
        if state.get("latestSha256") == latest_sha:
            state = {**state, "lastVerifiedSourceCommit": latest_commit,
                     "lastVerifiedAt": datetime.now(timezone.utc).isoformat()}
            _atomic_write(state_path, state)
            return {"status": "NOOP", **info,
                    "appliedCommit": state.get("commit"),
                    "sourceCommit": latest_commit}
        raise SyncError("PUBLISHED_RESULT_HASH_MISMATCH")
    if state.get("generatedAt") and _instant(latest["generatedAt"]) <= _instant(state["generatedAt"]):
        raise SyncError("PUBLISHED_RESULT_NOT_NEWER")
    previous = {}
    for path in (latest_path, history_path):
        try: previous[str(path)] = Path(path).read_bytes()
        except OSError: previous[str(path)] = None
    try:
        _atomic_write(latest_path, latest)
        _atomic_write(history_path, history)
        _atomic_write(state_path, {"commit": latest_commit, "generatedAt": latest["generatedAt"],
                                   "latestSha256": latest_sha})
    except Exception:
        for path, raw in previous.items():
            if raw is None: continue
            Path(path).write_bytes(raw)
        raise
    return {"status": "APPLIED", **info}
