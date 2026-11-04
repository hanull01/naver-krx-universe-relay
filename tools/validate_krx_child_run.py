#!/usr/bin/env python3
"""Validate one sequential KRX fan-out child workflow run."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _steps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        step
        for job in (payload.get("jobs") or [])
        if isinstance(job, dict)
        for step in (job.get("steps") or [])
        if isinstance(step, dict)
    ]


def validate(payload: dict[str, Any], publish: str) -> str | None:
    if payload.get("conclusion") != "success":
        return "CHILD_WORKFLOW_NOT_SUCCESS"

    steps = _steps(payload)
    commits = [
        step for step in steps
        if str(step.get("name", "")).startswith("Commit")
    ]
    provenance = [
        step for step in steps
        if "publication provenance" in str(step.get("name", "")).lower()
    ]
    no_publish = [
        step for step in steps
        if step.get("name") == "Report NO_PUBLISH validation result"
    ]

    if publish == "false":
        if any(
            step.get("conclusion") not in {"skipped", None}
            for step in commits + provenance
        ):
            return "PUBLISH_FALSE_COMMIT_STEP_RAN"
        if not any(step.get("conclusion") == "success" for step in no_publish):
            return "NO_PUBLISH_RESULT_MISSING"
        return None

    if publish == "true":
        if not commits or any(step.get("conclusion") != "success" for step in commits):
            return "PUBLISH_TRUE_COMMIT_STEP_FAILED"
        if not provenance or any(step.get("conclusion") != "success" for step in provenance):
            return "PUBLISH_TRUE_PROVENANCE_STEP_FAILED"
        return None

    return "CHILD_WORKFLOW_NOT_SUCCESS"


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[2] not in {"false", "true"}:
        print("CHILD_WORKFLOW_NOT_SUCCESS", file=sys.stderr)
        return 1
    try:
        payload = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        print("CHILD_WORKFLOW_NOT_SUCCESS", file=sys.stderr)
        return 1
    if not isinstance(payload, dict):
        print("CHILD_WORKFLOW_NOT_SUCCESS", file=sys.stderr)
        return 1
    error = validate(payload, argv[2])
    if error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
