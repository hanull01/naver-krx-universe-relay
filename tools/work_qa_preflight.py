#!/usr/bin/env python3
"""Prepare and validate the isolated Work QA environment.

This command is intentionally local-only: it never changes repository data,
GitHub settings, Actions, or credentials.  It validates the repository first,
then optionally creates a disposable QA virtual environment and installs only
the dependencies used by the regression workflow.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


REPOSITORY = "hanull01/naver-krx-universe-relay"
REQUIRED_IMPORTS = ("certifi", "webauthn")


class PreflightError(RuntimeError):
    pass


def run(command: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise PreflightError(f"COMMAND_UNAVAILABLE:{command[0]}") from exc
    if check and result.returncode != 0:
        raise PreflightError(f"COMMAND_FAILED:{' '.join(command)}:{result.returncode}")
    return result


def find_repository(start: Path) -> tuple[Path, str, str, str]:
    result = run(["git", "rev-parse", "--show-toplevel"], cwd=start, check=False)
    if result.returncode != 0:
        raise PreflightError("NOT_A_GIT_REPOSITORY")
    root = Path(result.stdout.strip()).resolve()
    remote = run(["git", "config", "--get", "remote.origin.url"], cwd=root, check=False)
    remote_url = remote.stdout.strip()
    if REPOSITORY not in remote_url:
        raise PreflightError("UNEXPECTED_ORIGIN")
    head = run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()
    branch = run(["git", "branch", "--show-current"], cwd=root).stdout.strip() or "DETACHED"
    return root, head, branch, remote_url


def choose_python() -> str:
    preferred = shutil.which("python3.12")
    if preferred:
        return preferred
    current = sys.version_info
    if (current.major, current.minor) == (3, 12):
        return sys.executable
    return sys.executable


def venv_python(path: Path) -> Path:
    return path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def imports_ok(python: Path) -> tuple[bool, str]:
    result = run(
        [str(python), "-c", "import sys, certifi, webauthn; print(sys.version.split()[0] + ' ' + certifi.where())"],
        cwd=python.parent.parent,
        check=False,
    )
    detail = result.stdout.strip() or result.stderr.strip()
    return result.returncode == 0, detail


def prepare(root: Path, env_path: Path) -> None:
    if not env_path.exists():
        builder = choose_python()
        run([builder, "-m", "venv", str(env_path)], cwd=root)
        print(f"created_venv={env_path} using={builder}")
    python = venv_python(env_path)
    ok, _ = imports_ok(python)
    if not ok:
        run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(root / "requirements-qa.txt")],
            cwd=root,
        )
    ok, detail = imports_ok(python)
    if not ok:
        raise PreflightError(f"QA_IMPORT_FAILED:{detail}")
    print(f"qa_imports=PASS python={python} certifi={detail}")


def github_read_check(root: Path) -> str:
    gh = shutil.which("gh")
    if not gh:
        return "gh_auth=UNAVAILABLE"
    status = run([gh, "auth", "status", "--hostname", "github.com"], cwd=root, check=False)
    if status.returncode != 0:
        return "gh_auth=UNVERIFIED"
    remote = run(["git", "ls-remote", "origin", "HEAD"], cwd=root, check=False)
    return "gh_auth=PASS git_remote_read=PASS" if remote.returncode == 0 else "gh_auth=PASS git_remote_read=UNVERIFIED"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true", help="create/reuse .qa-venv and install missing QA imports")
    parser.add_argument("--check", action="store_true", help="validate repository, Python imports, and GitHub read authentication")
    parser.add_argument("--venv", type=Path, default=None)
    args = parser.parse_args()
    if not args.prepare and not args.check:
        parser.error("use --prepare and/or --check")
    try:
        root, head, branch, remote = find_repository(Path.cwd())
        print(f"repository=PASS root={root}")
        print(f"head={head} branch={branch}")
        print(f"origin={remote}")
        env_path = (args.venv or (root / ".qa-venv")).resolve()
        if args.prepare:
            prepare(root, env_path)
        if args.check:
            python = venv_python(env_path) if env_path.exists() else Path(sys.executable)
            ok, detail = imports_ok(python)
            if not ok:
                raise PreflightError(f"QA_IMPORT_MISSING:{python}:{detail}")
            print(f"qa_imports=PASS python={python} certifi={detail}")
            print(github_read_check(root))
        return 0
    except PreflightError as exc:
        print(f"preflight=FAIL reason={exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
