"""Stable lineage helpers for report contracts and rendered views."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any


def _identity(contract: Mapping[str, Any]) -> dict[str, Any]:
    historical = contract.get("historicalTechnicalMarket")
    historical = historical if isinstance(historical, Mapping) else {}
    lineage = contract.get("historicalLineage")
    lineage = lineage if isinstance(lineage, Mapping) else {}
    return {
        "schemaVersion": contract.get("schemaVersion"),
        "asOf": contract.get("asOf"),
        "observedAt": contract.get("observedAt"),
        "observationDate": contract.get("observationDate") or contract.get("observedDate"),
        "sourceGeneration": contract.get("sourceGeneration"),
        "historicalGenerationId": contract.get("historicalGenerationId") or historical.get("generationId") or lineage.get("generationId"),
        "historicalTargetDate": contract.get("historicalTargetDate") or historical.get("targetDate") or lineage.get("targetDate"),
    }


def contract_id(contract: Mapping[str, Any]) -> str:
    encoded = json.dumps(_identity(contract), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "mrc-" + hashlib.sha256(encoded).hexdigest()[:16]


def lineage_from_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    explicit = contract.get("reportLineage")
    if isinstance(explicit, Mapping) and explicit.get("contractId"):
        return dict(explicit)
    current = contract.get("currentMarketObservation")
    current = current if isinstance(current, Mapping) else {}
    historical = contract.get("historicalTechnicalMarket")
    historical = historical if isinstance(historical, Mapping) else {}
    return {
        "schemaVersion": "report-lineage-v1",
        "contractId": contract.get("contractId") or contract_id(contract),
        "asOf": contract.get("asOf"),
        "generatedAt": contract.get("observedAt"),
        "source": "REPORT_CONTRACT",
        "scope": "MARKET_REPORT",
        "current": {"source": current.get("source"), "date": current.get("observedDate"), "scope": current.get("scope")},
        "historical": {"source": historical.get("source"), "date": historical.get("targetDate"), "scope": historical.get("scope"), "generationId": historical.get("generationId") or contract.get("historicalGenerationId")},
    }


def lineage_marker(contract: Mapping[str, Any]) -> str:
    lineage = lineage_from_contract(contract)
    value = json.dumps({
        "contractId": lineage["contractId"],
        "asOf": lineage["asOf"],
        "createdAt": lineage["generatedAt"],
        "source": lineage["source"],
        "scope": lineage["scope"],
        "currentSource": lineage["current"].get("source"),
        "currentDate": lineage["current"].get("date"),
        "currentScope": lineage["current"].get("scope"),
        "historicalSource": lineage["historical"].get("source"),
        "historicalDate": lineage["historical"].get("date"),
        "historicalScope": lineage["historical"].get("scope"),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"<!-- report-lineage: {value} -->"


def normalize_report_as_of(value: Any) -> str:
    """Normalize supported report dates to the canonical YYYYMMDD form."""
    if value is None:
        raise ValueError("REPORT_ASOF_MISSING")
    text = str(value).strip()
    try:
        if re.fullmatch(r"\d{8}", text):
            return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}").strftime("%Y%m%d")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text).strftime("%Y%m%d")
        iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
        return datetime.fromisoformat(iso_text).date().strftime("%Y%m%d")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("REPORT_ASOF_INVALID") from exc


def validate_rendered_as_of(manifest: Mapping[str, Any], rendered: Mapping[str, Any], contract: Mapping[str, Any]) -> str:
    """Validate the canonical report date across manifest, renderer, and contract."""
    manifest_lineage = manifest.get("reportLineage")
    if not isinstance(manifest_lineage, Mapping):
        raise ValueError("REPORT_MANIFEST_LINEAGE_MISSING")
    manifest_raw_as_of = manifest_lineage.get("asOf")
    contract_raw_as_of = contract.get("asOf")
    canonical_raw_as_of = contract_raw_as_of or manifest_raw_as_of
    if canonical_raw_as_of is None:
        raise ValueError("REPORT_ASOF_MISSING")
    canonical_as_of = normalize_report_as_of(canonical_raw_as_of)
    if manifest_raw_as_of is not None and normalize_report_as_of(manifest_raw_as_of) != canonical_as_of:
        raise ValueError("REPORT_AS_OF_MISMATCH")
    if contract_raw_as_of is not None and normalize_report_as_of(contract_raw_as_of) != canonical_as_of:
        raise ValueError("REPORT_AS_OF_MISMATCH")
    rendered_lineage = rendered.get("lineage")
    if not isinstance(rendered_lineage, Mapping):
        raise ValueError("REPORT_RENDERED_LINEAGE_MISSING")
    if rendered_lineage.get("asOf") is None:
        raise ValueError("REPORT_RENDERED_LINEAGE_ASOF_MISSING")
    if normalize_report_as_of(rendered_lineage.get("asOf")) != canonical_as_of:
        raise ValueError("REPORT_RENDERED_LINEAGE_ASOF_MISMATCH")
    rendered_as_of = rendered.get("asOf")
    if rendered_as_of is not None and normalize_report_as_of(rendered_as_of) != canonical_as_of:
        raise ValueError("REPORT_RENDER_RESULT_ASOF_MISMATCH")
    return canonical_as_of


def validate_current_observation_publish(
    manifest: Mapping[str, Any],
    rendered: Mapping[str, Any],
    contract: Mapping[str, Any],
    markdown_text: str,
    html_text: str,
    contract_sha256: str,
) -> None:
    """Validate internal publish lineage without requiring metadata in user views."""
    expected_id = contract.get("contractId")
    expected_lineage = contract.get("reportLineage")
    if not expected_id or not isinstance(expected_lineage, Mapping):
        raise ValueError("REPORT_LINEAGE_MISSING_FROM_CONTRACT")
    if rendered.get("contractId") != expected_id or rendered.get("lineage") != expected_lineage:
        raise ValueError("REPORT_RENDER_RESULT_LINEAGE_MISMATCH")
    if manifest.get("contractId") != expected_id:
        raise ValueError("CURRENT_OBSERVATION_CONTRACT_ID_MISMATCH")
    if manifest.get("reportLineage") != expected_lineage:
        raise ValueError("CURRENT_OBSERVATION_LINEAGE_MISMATCH")
    if manifest.get("reportContractSha256") != contract_sha256:
        raise ValueError("CURRENT_OBSERVATION_CONTRACT_DIGEST_MISMATCH")
    forbidden = (
        "contractId", "reportLineage", "sourceHash", "runId", "executionHead",
        "producer", "artifact", "workflow", "commit",
    )
    for path, text in (("markdown", markdown_text), ("html", html_text)):
        for token in forbidden:
            if token in text:
                raise ValueError(f"CURRENT_OBSERVATION_INTERNAL_METADATA_RENDERED:{token}:{path}")
    validate_rendered_as_of(manifest, rendered, contract)
    if rendered.get("lineage", {}).get("current") != expected_lineage.get("current"):
        raise ValueError("REPORT_CURRENT_LINEAGE_MISMATCH")
