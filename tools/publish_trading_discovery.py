#!/usr/bin/env python3
"""Publish one validated discovery snapshot through an isolated worktree.

The command stages only the dated snapshot and ``latest.json``.  A remote
advance after the worktree was created aborts before push; the push itself is
never forced.  It does not dispatch workflows or modify market artifacts.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import trading_discovery


class PublishError(RuntimeError):
    pass


def run_git(repo, *args, check=True):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if check and result.returncode:
        raise PublishError(f"git {' '.join(args[:2])} failed")
    return result


def sha(repo, ref):
    return run_git(repo, "rev-parse", ref).stdout.strip()


def safe_publish(snapshot_path, calendar_path, repository=ROOT, *,
                 remote="origin", branch="main", before_final_fetch=None):
    repository = Path(repository).resolve()
    snapshot_path = Path(snapshot_path).resolve()
    calendar_path = Path(calendar_path).resolve()
    # Validate before any Git mutation or remote access.
    snapshot = trading_discovery.validate_snapshot(
        trading_discovery.load_json(snapshot_path), calendar_path)
    monitoring_date = snapshot["monitoring_date"]
    dated_rel = f"data/trading-discovery/{monitoring_date}.json"
    latest_rel = "data/trading-discovery/latest.json"

    run_git(repository, "fetch", remote, branch)
    remote_ref = f"refs/remotes/{remote}/{branch}"
    base = sha(repository, remote_ref)
    worktree = Path(tempfile.mkdtemp(prefix="trading-discovery-publish-"))
    attached = False
    try:
        run_git(repository, "worktree", "add", "--detach", str(worktree), base)
        attached = True
        result = trading_discovery.publish_snapshot(
            snapshot_path, worktree / "data/trading-discovery", calendar_path)
        run_git(worktree, "add", "--", dated_rel, latest_rel)
        staged = [line for line in run_git(
            worktree, "diff", "--cached", "--name-only", "--", dated_rel, latest_rel
        ).stdout.splitlines() if line]
        if not staged:
            return {**result, "publishStatus": "NOOP", "baseSha": base,
                    "commitSha": None, "remoteSha": base, "stagedFiles": []}
        allowed = {dated_rel, latest_rel}
        if not set(staged).issubset(allowed) or dated_rel not in staged:
            raise PublishError("STAGED_PATHS_INVALID")
        run_git(worktree, "diff", "--cached", "--check")
        run_git(worktree, "config", "user.name", "TC Kiwoom Publisher")
        run_git(worktree, "config", "user.email", "tc-kiwoom-publisher@localhost")
        run_git(worktree, "commit", "-m", f"Publish Kiwoom discovery for {monitoring_date}")
        commit = sha(worktree, "HEAD")
        if before_final_fetch:
            before_final_fetch(base)
        run_git(repository, "fetch", remote, branch)
        current = sha(repository, remote_ref)
        if current != base:
            raise PublishError("REMOTE_ADVANCED_BEFORE_PUSH")
        push = run_git(worktree, "push", remote, f"HEAD:refs/heads/{branch}", check=False)
        if push.returncode:
            raise PublishError("NON_FORCE_PUSH_REJECTED")
        run_git(repository, "fetch", remote, branch)
        remote_sha = sha(repository, remote_ref)
        if remote_sha != commit:
            raise PublishError("REMOTE_SHA_MISMATCH")
        raw = run_git(repository, "show", f"{remote_ref}:{dated_rel}").stdout
        published = json.loads(raw)
        if published.get("sourceSha256") != snapshot["source_sha256"]:
            raise PublishError("REMOTE_FILE_HASH_MISMATCH")
        return {**result, "publishStatus": "PUSHED", "baseSha": base,
                "commitSha": commit, "remoteSha": remote_sha,
                "stagedFiles": staged}
    finally:
        if attached:
            run_git(repository, "worktree", "remove", "--force", str(worktree), check=False)
        try:
            worktree.rmdir()
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot")
    parser.add_argument("--calendar", required=True)
    parser.add_argument("--repository", default=str(ROOT))
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    args = parser.parse_args()
    result = safe_publish(args.snapshot, args.calendar, args.repository,
                          remote=args.remote, branch=args.branch)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
