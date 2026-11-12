"""Stable lineage helpers for report contracts and rendered views."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
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
        # A current-observation view may change only its display as-of date
        # while retaining the source contract identity.  Published contracts
        # are validated separately, so preserve that identity when present.
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


def validate_rendered_as_of(
    manifest: Mapping[str, Any],
    rendered: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> str:
    """Validate the canonical report date across manifest, renderer, and contract."""

    manifest_lineage = manifest.get("reportLineage")
    if not isinstance(manifest_lineage, Mapping):
        raise ValueError("REPORT_MANIFEST_LINEAGE_MISSING")
    manifest_as_of = manifest_lineage.get("asOf")
    if not manifest_as_of:
        raise ValueError("REPORT_MANIFEST_LINEAGE_ASOF_MISSING")

    contract_as_of = contract.get("asOf")
    if not contract_as_of:
        raise ValueError("REPORT_CONTRACT_ASOF_MISSING")
    if manifest_as_of != contract_as_of:
        raise ValueError("REPORT_AS_OF_MISMATCH")

    rendered_lineage = rendered.get("lineage")
    if not isinstance(rendered_lineage, Mapping):
        raise ValueError("REPORT_RENDERED_LINEAGE_MISSING")
    if rendered_lineage.get("asOf") != manifest_as_of:
        raise ValueError("REPORT_RENDERED_LINEAGE_ASOF_MISMATCH")

    rendered_as_of = rendered.get("asOf")
    if rendered_as_of is not None and rendered_as_of != manifest_as_of:
        raise ValueError("REPORT_RENDER_RESULT_ASOF_MISMATCH")
    return str(manifest_as_of)
