"""Render current-observation Markdown/HTML from one published report contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.market_dashboard_renderer import (
    TemplateUnavailable,
    build_dashboard_model,
    render_dashboard_html,
    render_dashboard_markdown,
    validate_rendered_files,
)

DEFAULT_CONTRACT = ROOT / "data/monitoring/latest-report-contract.json"
DEFAULT_OUTPUT_ROOT = ROOT / "data/reports/current-observation"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _session(indices: list[Mapping[str, Any]]) -> str:
    sessions = {str(row.get("session") or "").upper() for row in indices}
    statuses = {str(row.get("marketStatus") or "").upper() for row in indices}
    if any("PRE" in value for value in sessions):
        return "PREOPEN"
    if any("INTRADAY" in value for value in sessions) or "OPEN" in statuses:
        return "INTRADAY"
    if any("POST_MARKET" in value for value in sessions) or "CLOSE" in statuses:
        return "POST_MARKET"
    return "UNKNOWN"


def _source_time(contract: Mapping[str, Any], indices: list[Mapping[str, Any]]) -> str | None:
    values = {str(row.get("sourceTime")) for row in indices if row.get("sourceTime")}
    if len(values) == 1:
        return next(iter(values))
    value = contract.get("observedAt")
    return str(value) if value else None


def _load_contract(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    contract = payload.get("reportContract") if isinstance(payload, Mapping) else None
    if not isinstance(contract, Mapping):
        contract = payload
    if not isinstance(contract, Mapping):
        raise TemplateUnavailable("CONTRACT_UNAVAILABLE")
    return dict(contract), raw


def render_current_observation(
    contract_path: Path = DEFAULT_CONTRACT,
    output_dir: Path | None = None,
    *,
    run_id: str | None = None,
    head_sha: str | None = None,
) -> dict[str, Any]:
    contract, contract_bytes = _load_contract(contract_path)
    if contract.get("status") not in {"AVAILABLE", "PARTIAL"}:
        raise TemplateUnavailable("CURRENT_OBSERVATION_CONTRACT_NOT_PUBLISHABLE")

    full_market = contract.get("fullMarket") or {}
    breadth = full_market.get("breadth") or {}
    total = full_market.get("totalCount")
    values = [breadth.get(key) for key in ("advancers", "decliners", "unchanged")]
    if not isinstance(total, int) or total <= 0 or any(not isinstance(value, int) for value in values):
        raise TemplateUnavailable("CURRENT_OBSERVATION_MARKET_SOURCE_UNAVAILABLE")
    if sum(values) != total:
        raise TemplateUnavailable("CURRENT_OBSERVATION_BREADTH_INVALID")

    indices = [row for row in contract.get("indices") or [] if isinstance(row, Mapping)]
    if not indices:
        raise TemplateUnavailable("CURRENT_OBSERVATION_INDEX_SOURCE_UNAVAILABLE")
    observed_at = contract.get("observedAt")
    if not observed_at:
        raise TemplateUnavailable("CURRENT_OBSERVATION_GENERATION_MISSING")

    report = {
        "reportContract": contract,
        "generatedAt": observed_at,
        "writtenAt": observed_at,
    }
    model = build_dashboard_model(report, allow_current_observation=True)
    markdown = render_dashboard_markdown(report, allow_current_observation=True)
    html = render_dashboard_html(report, allow_current_observation=True)

    run_id = str(run_id or os.environ.get("GITHUB_RUN_ID") or "local")
    head_sha = str(head_sha or os.environ.get("GITHUB_SHA") or "UNAVAILABLE")
    if output_dir is None:
        output_dir = DEFAULT_OUTPUT_ROOT / run_id
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / "report.md"
    html_path = output_dir / "report.html"
    manifest_path = output_dir / "manifest.json"
    markdown_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")
    issues = validate_rendered_files(markdown_path, html_path, model)
    if issues:
        raise TemplateUnavailable("CURRENT_OBSERVATION_OUTPUT_INVALID:" + ",".join(issues[:8]))

    def repo_path(path: Path) -> str:
        try:
            return path.relative_to(ROOT).as_posix()
        except ValueError:
            return path.name
    baseline = contract.get("baseline") or {}
    evidence = contract.get("tradingDateEvidence") or {}
    baseline_stale = (
        baseline.get("freshnessStatus") == "BASELINE_STALE"
        or contract.get("baselineFreshnessStatus") == "BASELINE_STALE"
    )
    final_pending = (
        contract.get("status") != "AVAILABLE"
        or contract.get("tradingDateStatus") != "VERIFIED"
        or evidence.get("finalConfirmed") is False
    )
    manifest = {
        "schemaVersion": "current-observation-manifest-v1",
        "reportType": "current_observation",
        "session": _session(indices),
        "tradingDate": contract.get("asOf"),
        "tradingDateStatus": contract.get("tradingDateStatus") or "UNAVAILABLE",
        "sourceTime": _source_time(contract, indices),
        "reportContractPath": repo_path(contract_path),
        "reportContractSha256": hashlib.sha256(contract_bytes).hexdigest(),
        "reportContractGeneratedAt": observed_at,
        "workflowRunId": run_id,
        "executionHead": head_sha,
        "markdownPath": repo_path(markdown_path),
        "htmlPath": repo_path(html_path),
        "manifestPath": repo_path(manifest_path),
        "markdownSha256": _sha256(markdown_path),
        "htmlSha256": _sha256(html_path),
        "reportStatus": contract.get("status"),
        "currentObservationAllowed": True,
        "finalPending": final_pending,
        "baselineStale": baseline_stale,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "status": "SUCCESS",
        "session": manifest["session"],
        "manifestPath": manifest["manifestPath"],
        "markdownPath": manifest["markdownPath"],
        "htmlPath": manifest["htmlPath"],
        "manifest": manifest,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--head-sha")
    args = parser.parse_args()
    try:
        result = render_current_observation(
            args.contract, args.output_dir, run_id=args.run_id, head_sha=args.head_sha
        )
    except (OSError, ValueError, TemplateUnavailable) as exc:
        print(json.dumps({"status": "FAILED", "reason": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
