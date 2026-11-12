#!/usr/bin/env python3
"""Publish one exact local KIS snapshot through the monitoring-owned path.

The caller supplies the finalized dated snapshot after the 21:00 producer has
completed.  This is a one-shot handoff, not a scheduler: the command validates
the source before any Git mutation, writes candidate and quote artifacts in an
isolated worktree, stages only the four approved JSON files, and uses a
non-force push.  The KIS source file is never written.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import trading_discovery as td


class PublishError(RuntimeError):
    pass


ALLOWED_PATHS = {
    "data/trading-discovery/latest.json",
    "data/trading-discovery/quotes/latest.json",
}


def run_git(repo, *args, check=True):
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        raise PublishError(f"git {' '.join(args[:2])} failed")
    return result


def git_sha(repo, ref):
    return run_git(repo, "rev-parse", ref).stdout.strip()


def _identity(payload):
    return (
        payload.get("monitoringDate"),
        payload.get("sourceSha256"),
        tuple(payload.get("symbols") or []),
    )


def _quote_identity(payload):
    symbols = tuple(row.get("symbol") for row in payload.get("candidateStatuses") or [])
    return (
        payload.get("monitoringDate"),
        payload.get("sourceHash", payload.get("candidateSourceSha256")),
        symbols,
    )


def validate_delivery_lineage(output_root, discovery):
    """Require dated/latest candidate and quote files to share one identity."""
    output_root = Path(output_root)
    candidate_dir = output_root / "data" / "trading-discovery"
    quote_dir = candidate_dir / "quotes"
    candidate_dated = json.loads(
        (candidate_dir / f"{discovery['monitoringDate']}.json").read_text(encoding="utf-8")
    )
    candidate_latest = json.loads(
        (candidate_dir / "latest.json").read_text(encoding="utf-8")
    )
    quote_dated = json.loads(
        (quote_dir / f"{discovery['monitoringDate']}.json").read_text(encoding="utf-8")
    )
    quote_latest = json.loads((quote_dir / "latest.json").read_text(encoding="utf-8"))
    expected = _identity(discovery)
    if _identity(candidate_dated) != expected or _identity(candidate_latest) != expected:
        raise PublishError("CANDIDATE_LINEAGE_MISMATCH")
    if _quote_identity(quote_dated) != expected or _quote_identity(quote_latest) != expected:
        raise PublishError("QUOTE_LINEAGE_MISMATCH")
    if quote_dated.get("requestedCount") != len(discovery.get("symbols") or []):
        raise PublishError("QUOTE_REQUEST_COUNT_MISMATCH")
    if quote_dated.get("candidateSourceSha256") != discovery.get("sourceSha256"):
        raise PublishError("QUOTE_SOURCE_HASH_MISMATCH")
    return {
        "monitoringDate": expected[0],
        "sourceHash": expected[1],
        "candidateCount": len(expected[2]),
        "requestedCount": quote_dated.get("requestedCount"),
        "quoteCount": quote_dated.get("quoteCount"),
        "quoteStatus": quote_dated.get("status"),
    }


def _network_quote_functions():
    import relay

    def fetcher(codes):
        return relay.fetch(relay.QUOTE_URL + ",".join(codes))

    return fetcher, relay.normalize_quote


def _build_delivery(snapshot_path, calendar_path, quotes_path, *, monitoring_date,
                    output_root, fetcher, normalizer, clock=None):
    snapshot = td.load_official_snapshot(
        snapshot_path,
        expected_monitoring_date=monitoring_date,
        calendar_path=calendar_path,
    )
    # Empty official snapshots are complete NO_RESULTS inputs.  There is no
    # quote source to read and, importantly, no previous quotes file may be
    # consulted as a substitute for this snapshot.
    quotes = None if not snapshot.get("symbols") else td.load_json(quotes_path)
    result = td.consume_official_snapshot(
        snapshot_path,
        quotes,
        calendar_path,
        discovery_output_dir=Path(output_root) / "data" / "trading-discovery",
        quote_output_dir=Path(output_root) / "data" / "trading-discovery" / "quotes",
        expected_monitoring_date=monitoring_date,
        fetcher=fetcher,
        normalizer=normalizer,
        clock=clock,
    )
    lineage = validate_delivery_lineage(output_root, td.published_payload(snapshot))
    return {**result, "lineage": lineage}


def _stage_and_commit(worktree, monitoring_date):
    dated = f"data/trading-discovery/{monitoring_date}.json"
    quote_dated = f"data/trading-discovery/quotes/{monitoring_date}.json"
    allowed = {dated, quote_dated, *ALLOWED_PATHS}
    run_git(worktree, "add", "--", *sorted(allowed))
    staged = [
        line for line in run_git(worktree, "diff", "--cached", "--name-only").stdout.splitlines()
        if line
    ]
    if not staged:
        return [], None
    if not set(staged).issubset(allowed):
        raise PublishError("STAGED_PATHS_INVALID")
    run_git(worktree, "diff", "--cached", "--check")
    run_git(worktree, "config", "user.name", "Monitoring Candidate Publisher")
    run_git(worktree, "config", "user.email", "monitoring-candidate-publisher@localhost")
    run_git(worktree, "commit", "-m", f"Publish KIS candidate delivery for {monitoring_date}")
    return staged, git_sha(worktree, "HEAD")


def safe_publish(snapshot_path, calendar_path, quotes_path, repository=ROOT, *,
                monitoring_date, remote="origin", branch="main", allow_network=False,
                dry_run=False, fetcher=None, normalizer=None, clock=None):
    """Validate, stage and optionally push one local snapshot handoff."""
    snapshot_path = Path(snapshot_path).resolve()
    calendar_path = Path(calendar_path).resolve()
    quotes_path = Path(quotes_path).resolve() if quotes_path is not None else None
    repository = Path(repository).resolve()
    # The source is checked before fetch/worktree creation and is never written.
    td.load_official_snapshot(
        snapshot_path,
        expected_monitoring_date=monitoring_date,
        calendar_path=calendar_path,
    )
    snapshot = td.load_official_snapshot(
        snapshot_path,
        expected_monitoring_date=monitoring_date,
        calendar_path=calendar_path,
    )
    if snapshot.get("symbols") and (quotes_path is None or not quotes_path.is_file()):
        raise PublishError("QUOTE_INPUT_MISSING")
    if fetcher is None or normalizer is None:
        if not allow_network and not dry_run:
            raise PublishError("NETWORK_PERMISSION_REQUIRED")
        if allow_network:
            fetcher, normalizer = _network_quote_functions()
        else:
            fetcher = lambda _codes: {"datas": []}
            normalizer = lambda row, _now: row

    if dry_run:
        staging = Path(tempfile.mkdtemp(prefix="kis-candidate-delivery-dryrun-"))
        try:
            result = _build_delivery(
                snapshot_path, calendar_path, quotes_path,
                monitoring_date=monitoring_date, output_root=staging,
                fetcher=fetcher, normalizer=normalizer, clock=clock,
            )
            return {**result, "publishStatus": "DRY_RUN", "stagingRoot": str(staging)}
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    run_git(repository, "fetch", remote, branch)
    remote_ref = f"refs/remotes/{remote}/{branch}"
    base = git_sha(repository, remote_ref)
    staging = Path(tempfile.mkdtemp(prefix="kis-candidate-delivery-publish-"))
    attached = False
    try:
        run_git(repository, "worktree", "add", "--detach", str(staging), base)
        attached = True
        result = _build_delivery(
            snapshot_path, calendar_path, quotes_path,
            monitoring_date=monitoring_date, output_root=staging,
            fetcher=fetcher, normalizer=normalizer, clock=clock,
        )
        staged, commit = _stage_and_commit(staging, monitoring_date)
        if commit is None:
            return {
                **result,
                "publishStatus": "NOOP",
                "baseSha": base,
                "commitSha": None,
                "remoteSha": base,
                "stagedFiles": [],
            }
        run_git(repository, "fetch", remote, branch)
        if git_sha(repository, remote_ref) != base:
            raise PublishError("REMOTE_ADVANCED_BEFORE_PUSH")
        pushed = run_git(staging, "push", remote, f"HEAD:refs/heads/{branch}", check=False)
        if pushed.returncode:
            raise PublishError("NON_FORCE_PUSH_REJECTED")
        run_git(repository, "fetch", remote, branch)
        remote_sha = git_sha(repository, remote_ref)
        if remote_sha != commit:
            raise PublishError("REMOTE_SHA_MISMATCH")
        return {
            **result,
            "publishStatus": "PUSHED",
            "baseSha": base,
            "commitSha": commit,
            "remoteSha": remote_sha,
            "stagedFiles": staged,
        }
    finally:
        if attached:
            run_git(repository, "worktree", "remove", "--force", str(staging), check=False)
        shutil.rmtree(staging, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--monitoring-date", required=True)
    parser.add_argument("--calendar", required=True, type=Path)
    parser.add_argument("--quotes", type=Path)
    parser.add_argument("--repository", default=str(ROOT), type=Path)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = safe_publish(
        args.snapshot, args.calendar, args.quotes, args.repository,
        monitoring_date=args.monitoring_date, remote=args.remote, branch=args.branch,
        allow_network=args.allow_network, dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
