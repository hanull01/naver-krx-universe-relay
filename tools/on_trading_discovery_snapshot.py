#!/usr/bin/env python3
"""One-shot event entrypoint for a finalized Trading Console snapshot.

The Trading Console (or an operator) passes the exact immutable snapshot path
after synchronization succeeds.  This command does not watch directories,
calculate trading dates, call Kiwoom, or modify the source snapshot.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publish_trading_discovery import safe_publish


class EventError(RuntimeError):
    pass


def handle(snapshot, calendar, repository, *, remote="origin", branch="main",
           publisher=safe_publish, lock_root=None):
    snapshot = Path(snapshot).resolve()
    calendar = Path(calendar).resolve()
    repository = Path(repository).resolve()
    if not snapshot.is_file():
        raise EventError("FINALIZED_SNAPSHOT_MISSING")
    if not calendar.is_file():
        raise EventError("OFFICIAL_CALENDAR_MISSING")
    lock_root = Path(lock_root or tempfile.gettempdir())
    lock = lock_root / "tc-kiwoom-discovery-publisher.lock"
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise EventError("PUBLISH_ALREADY_RUNNING") from exc
    try:
        marker = lock / "owner"
        marker.write_text(str(os.getpid()), encoding="ascii")
        return publisher(snapshot, calendar, repository,
                         remote=remote, branch=branch)
    finally:
        try:
            (lock / "owner").unlink()
            lock.rmdir()
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", help="exact finalized candidate snapshot path")
    parser.add_argument("--calendar", required=True,
                        help="Trading Console KRX_OFFICIAL calendar artifact")
    parser.add_argument("--repository", required=True, help="Relay repository")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    args = parser.parse_args()
    result = handle(args.snapshot, args.calendar, args.repository,
                    remote=args.remote, branch=args.branch)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
