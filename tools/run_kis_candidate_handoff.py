#!/usr/bin/env python3
"""Run the monitoring-owned KIS snapshot handoff once after file creation.

This command is intended for a disabled-until-approved LaunchAgent with
WatchPaths.  It selects only a newly modified dated snapshot, waits for the
file to become stable, validates it with the official KRX calendar, and then
delegates to the monitoring publisher.  It never scans or consumes
``latest.json`` and never modifies the KIS snapshot.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import trading_discovery as td
from tools import publish_kis_candidate_delivery as publisher


DATE_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
KST = ZoneInfo("Asia/Seoul")
DEFAULT_STATE_DIR = Path(os.getenv(
    "KIS_CANDIDATE_HANDOFF_STATE_DIR",
    "/Users/hanull/Library/Application Support/Hanull/monitoring-kis-candidate-handoff",
))


def _read_state(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return {"schemaVersion": 1, "processed": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("processed"), dict):
        return {"schemaVersion": 1, "processed": {}}
    return payload


def _write_state(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _acquire_lock(state_dir):
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = state_dir / ".handoff.lock"
    try:
        handle = lock.open("a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        try:
            handle.close()
        except UnboundLocalError:
            pass
        return None
    return handle


def _stable(path, seconds):
    try:
        before = path.stat()
    except OSError:
        return False
    if seconds > 0:
        time.sleep(seconds)
    try:
        after = path.stat()
    except OSError:
        return False
    return (before.st_size == after.st_size
            and before.st_mtime_ns == after.st_mtime_ns)


def _dated_files(snapshot_root):
    try:
        return [path for path in Path(snapshot_root).iterdir()
                if path.is_file() and DATE_FILE.fullmatch(path.name)]
    except (FileNotFoundError, NotADirectoryError, OSError):
        return []


def select_snapshot(snapshot_root, *, monitoring_date=None, max_age_seconds=7200,
                    stability_seconds=2.0, now=None):
    root = Path(snapshot_root)
    if monitoring_date is not None:
        path = root / f"{monitoring_date}.json"
        if not path.is_file() or not _stable(path, stability_seconds):
            return None
        return path

    current = time.time() if now is None else now
    candidates = []
    for path in _dated_files(root):
        try:
            age = current - path.stat().st_mtime
        except OSError:
            continue
        if 0 <= age <= max_age_seconds and _stable(path, stability_seconds):
            candidates.append(path)
    if not candidates:
        return None
    # The newest changed dated file is the handoff candidate.  No ``latest``
    # pointer or previous-date fallback is considered.
    return max(candidates, key=lambda path: path.stat().st_mtime_ns)


def run_once(snapshot_root, calendar, quotes, repository, *, state_dir=DEFAULT_STATE_DIR,
             monitoring_date=None, branch="main", remote="origin", allow_network=False,
             dry_run=False, max_age_seconds=7200, stability_seconds=2.0,
             publisher_fn=publisher.safe_publish):
    state_dir = Path(state_dir)
    state_path = state_dir / "state.json"
    lock = _acquire_lock(state_dir)
    if lock is None:
        return {"status": "SKIPPED_LOCKED"}
    try:
        path = select_snapshot(
            snapshot_root, monitoring_date=monitoring_date,
            max_age_seconds=max_age_seconds, stability_seconds=stability_seconds,
        )
        if path is None:
            return {"status": "WAITING_SNAPSHOT", "snapshot": None}

        target_date = path.stem
        snapshot = td.load_official_snapshot(
            path, expected_monitoring_date=target_date, calendar_path=calendar,
        )
        source_hash = snapshot["source_sha256"]
        candidate_count = int(snapshot.get("candidate_count", 0))
        condition_status = "NO_RESULTS" if candidate_count == 0 else "RESULTS"
        state = _read_state(state_path)
        previous = state["processed"].get(target_date)
        if isinstance(previous, dict) and previous.get("sourceHash") == source_hash:
            return {
                "status": "IDEMPOTENT",
                "monitoringDate": target_date,
                "sourceHash": source_hash,
                "snapshot": str(path),
                "previousStatus": previous.get("status"),
                "inputSnapshotPath": str(path),
                "candidateCount": candidate_count,
                "snapshotStatus": snapshot.get("status"),
                "conditionSearchStatus": condition_status,
                "quoteRequestedCount": 0 if candidate_count == 0 else None,
                "quoteSuccessCount": 0 if candidate_count == 0 else None,
                "quoteFailureCount": 0 if candidate_count == 0 else None,
                "statusDetail": "AVAILABLE_EMPTY" if candidate_count == 0 else previous.get("status"),
                "publisherCalled": False,
                "publicationStatus": "IDEMPOTENT",
                "duplicateGuardStatus": "IDEMPOTENT",
            }

        try:
            result = publisher_fn(
                path, calendar, quotes, repository,
                monitoring_date=target_date, remote=remote, branch=branch,
                allow_network=allow_network, dry_run=dry_run,
            )
            outcome = {
                "status": result.get("publishStatus", "UNKNOWN"),
                "monitoringDate": target_date,
                "sourceHash": source_hash,
                "snapshot": str(path),
                "publisher": result,
                "checkedAt": datetime.now(KST).isoformat(),
                "inputSnapshotPath": str(path),
                "candidateCount": candidate_count,
                "snapshotStatus": snapshot.get("status"),
                "conditionSearchStatus": condition_status,
                "quoteRequestedCount": result.get("lineage", {}).get("requestedCount"),
                "quoteSuccessCount": result.get("lineage", {}).get("quoteCount"),
                "quoteFailureCount": (
                    (result.get("lineage", {}).get("requestedCount") or 0)
                    - (result.get("lineage", {}).get("quoteCount") or 0)
                ),
                "statusDetail": result.get("quotes", {}).get("status"),
                "publisherCalled": True,
                "publicationStatus": (
                    "PUBLISHED" if result.get("publishStatus") == "PUSHED"
                    else result.get("publishStatus", "UNKNOWN")
                ),
                "duplicateGuardStatus": "CREATED",
            }
        except Exception as exc:
            outcome = {
                "status": "FAILED",
                "monitoringDate": target_date,
                "sourceHash": source_hash,
                "snapshot": str(path),
                "failureReason": type(exc).__name__,
                "failureDetail": str(exc),
                "checkedAt": datetime.now(KST).isoformat(),
                "inputSnapshotPath": str(path),
                "candidateCount": candidate_count,
                "snapshotStatus": snapshot.get("status"),
                "conditionSearchStatus": condition_status,
                "quoteRequestedCount": 0 if candidate_count == 0 else None,
                "quoteSuccessCount": 0 if candidate_count == 0 else None,
                "quoteFailureCount": 0 if candidate_count == 0 else None,
                "statusDetail": "AVAILABLE_EMPTY" if candidate_count == 0 else "FAILED",
                "publisherCalled": True,
                "publicationStatus": "FAILED",
                "duplicateGuardStatus": "CREATED",
            }
        if not dry_run:
            state["processed"][target_date] = {
                "sourceHash": source_hash,
                "status": outcome["status"],
                "failureReason": outcome.get("failureReason"),
                "checkedAt": outcome["checkedAt"],
                "inputSnapshotPath": outcome["inputSnapshotPath"],
                "monitoringDate": target_date,
                "candidateCount": outcome["candidateCount"],
                "snapshotStatus": outcome["snapshotStatus"],
                "conditionSearchStatus": outcome["conditionSearchStatus"],
                "quoteRequestedCount": outcome["quoteRequestedCount"],
                "quoteSuccessCount": outcome["quoteSuccessCount"],
                "quoteFailureCount": outcome["quoteFailureCount"],
                "statusDetail": outcome["statusDetail"],
                "publisherCalled": outcome["publisherCalled"],
                "publicationStatus": outcome["publicationStatus"],
                "duplicateGuardStatus": outcome["duplicateGuardStatus"],
            }
            _write_state(state_path, state)
        return outcome
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--quotes", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--monitoring-date")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--max-age-seconds", type=float, default=7200)
    parser.add_argument("--stability-seconds", type=float, default=2.0)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.dry_run and not args.allow_network:
        parser.error("--allow-network is required for live quote collection")
    result = run_once(
        args.snapshot_root, args.calendar, args.quotes, args.repository,
        state_dir=args.state_dir, monitoring_date=args.monitoring_date,
        branch=args.branch, remote=args.remote, allow_network=args.allow_network,
        dry_run=args.dry_run, max_age_seconds=args.max_age_seconds,
        stability_seconds=args.stability_seconds,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
