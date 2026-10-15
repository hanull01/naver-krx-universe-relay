"""Authentication-free, one-shot pull of published dynamic Universe data.

This process is intentionally independent of the web UI.  It only updates the
separate data/dynamic-universe files through dynamic_universe_sync.sync and
never touches config/universe.json or user watchlists.
"""
from __future__ import annotations

import argparse
import json
import logging
import ssl
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

import certifi

import dynamic_universe_sync as syncer


ROOT = Path(__file__).resolve().parent
DEFAULT_REPOSITORY = "hanull01/naver-krx-universe-relay"


def _log(run_id, event, started, stage, result=None, error_code=None, error_type=None, details=None):
    payload = {"run_id": run_id, "event": event,
               "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
               "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
               "stage": stage}
    if result is not None: payload["result"] = result
    if error_code: payload["safe_error_code"] = error_code
    if error_type: payload["safe_error_type"] = error_type
    if details: payload.update(details)
    print("DYNAMIC_SYNC " + json.dumps(payload, ensure_ascii=False), file=sys.stdout, flush=True)


def _error_code(exc, stage):
    if isinstance(exc, TimeoutError) or isinstance(exc, urllib.error.URLError) and isinstance(getattr(exc, "reason", None), TimeoutError):
        return "GITHUB_TIMEOUT"
    if isinstance(exc, urllib.error.HTTPError): return "GITHUB_HTTP_ERROR"
    if isinstance(exc, (urllib.error.URLError, OSError)): return "GITHUB_NETWORK_ERROR"
    if stage == "validation": return "PAYLOAD_VALIDATION_ERROR"
    if stage == "persistence": return "SNAPSHOT_WRITE_ERROR"
    if stage == "result_contract": return "RESULT_CONTRACT_ERROR"
    return "UNKNOWN_SYNC_ERROR"


def fetch_factory(repository=DEFAULT_REPOSITORY, ref="main", run_id=None, started=None):
    run_id = run_id or "untracked"
    started = started or time.monotonic()
    base = f"https://raw.githubusercontent.com/{repository}/{ref}/"
    context = ssl.create_default_context(cafile=certifi.where())
    api_url = f"https://api.github.com/repos/{repository}/commits/{ref}"
    request = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json", "User-Agent": "dynamic-universe-sync/1"})
    _log(run_id, "github_commit_request", started, "github_commit")
    try:
        with urllib.request.urlopen(request, context=context, timeout=15) as response:
            commit = json.loads(response.read().decode("utf-8"))["sha"]
        _log(run_id, "github_commit_result", started, "github_commit", "OK")
    except Exception as exc:
        _log(run_id, "github_commit_result", started, "github_commit", "FAILED", _error_code(exc, "github_commit"), type(exc).__name__)
        raise

    def fetch(path):
        stage = "latest_fetch" if path.endswith("latest.json") else "history_fetch"
        event = "latest_fetch_result" if stage == "latest_fetch" else "history_fetch_result"
        request = urllib.request.Request(base + path, headers={"User-Agent": "dynamic-universe-sync/1"})
        try:
            with urllib.request.urlopen(request, context=context, timeout=15) as response:
                value = response.read()
            _log(run_id, event, started, stage, "OK", details={"sha256": __import__("hashlib").sha256(value).hexdigest(), "bytes": len(value), "commit": commit})
            return value, commit
        except Exception as exc:
            _log(run_id, event, started, stage, "FAILED", _error_code(exc, stage), type(exc).__name__)
            raise

    return fetch


def run_result(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--ref", default="main")
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    run_id = uuid.uuid4().hex
    started = time.monotonic()
    _log(run_id, "sync_start", started, "start", "RUNNING")
    root = args.root.resolve()
    target = root / "data" / "dynamic-universe"
    target.mkdir(parents=True, exist_ok=True)
    try:
        result = syncer.sync(fetch_factory(args.repository, args.ref, run_id, started), target / "latest.json",
                             target / "history.json", target / ".sync-state.json", now=args.now)
        _log(run_id, "validation_result", started, "validation", "OK", details={"source_commit": result.get("commit"), "collectionCount": result.get("collectionCount")})
        _log(run_id, "persistence_result", started, "persistence", result.get("status", "UNKNOWN"))
        _log(run_id, "sync_finished", started, "finished", result.get("status", "UNKNOWN"))
        return {**result, "run_id": run_id}
    except Exception as exc:
        code = (exc.diagnostic.get("safe_error_code") if isinstance(exc, syncer.SyncError) else None) or _error_code(exc, "validation" if isinstance(exc, syncer.SyncError) else "sync")
        diagnostic = exc.diagnostic if isinstance(exc, syncer.SyncError) else {}
        failed_stage = "validation" if isinstance(exc, syncer.SyncError) else "sync"
        if diagnostic.get("validation_rule") == "published_result_monotonicity":
            failed_stage = "freshness_gate"
        _log(run_id, "validation_result" if isinstance(exc, syncer.SyncError) else "persistence_result", started,
             failed_stage, "FAILED", code, type(exc).__name__, diagnostic)
        _log(run_id, "sync_finished", started, "finished", "FAILED", code, type(exc).__name__, diagnostic)
        raise


def run(argv=None):
    result = run_result(argv)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
