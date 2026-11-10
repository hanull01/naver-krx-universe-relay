#!/usr/bin/env python3
"""Build a small report contract projection over full-market staging artifacts.

The member-level technical/state/provenance files remain the source artifacts.
Only report-facing summaries and the configured Universe projection are copied
into this contract, so the Work contract stays below its transport limit.
"""
from __future__ import annotations

import argparse

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from tools.file_digest import sha256_bytes, sha256_file

from tools.report_contract import (
    MAX_WORK_CONTRACT_BYTES,
    compact_detail_reference,
    validate_contract_payload,
)


class ReportContractProjectionError(ValueError):
    """Raised when the staged artifacts cannot form a safe projection."""


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReportContractProjectionError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReportContractProjectionError(f"JSON object expected: {path}")
    return payload


def _artifact(root: Path, name: str) -> dict[str, Any]:
    path = root / name
    if not path.exists():
        raise ReportContractProjectionError(f"artifact missing: {path}")
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _percent(count: int, eligible: int) -> float | None:
    return round(count * 100 / eligible, 6) if eligible else None


def _normalize_date(value: Any, field: str) -> str:
    text = str(value or "").replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ReportContractProjectionError(f"invalid {field}: {value!r}")
    return text


def _validate_rows_not_after(rows: list[Mapping[str, Any]], target: str, label: str) -> None:
    for row in rows:
        raw = row.get("asOfDate") or row.get("asOf")
        if raw in (None, ""):
            continue
        as_of = _normalize_date(raw, f"{label}.asOfDate")
        if as_of > target:
            raise ReportContractProjectionError(
                f"{label} contains future data: {as_of} > targetDate {target}"
            )


def _metric_summary(rows: list[Mapping[str, Any]], value: str) -> dict[str, Any]:
    eligible = [row for row in rows if row.get("asOfDate") and row.get(value) is not None]
    if value.startswith("breakout"):
        positive = [row for row in eligible if row.get(value) is True]
    elif value == "volumeRatio20":
        positive = [row for row in eligible if isinstance(row.get(value), (int, float)) and row[value] > 1]
    else:
        positive = [
            row for row in eligible
            if isinstance(row.get("price"), (int, float))
            and isinstance(row.get(value), (int, float))
            and row["price"] > row[value]
        ]
    return {
        "count": len(positive),
        "eligibleCount": len(eligible),
        "pct": _percent(len(positive), len(eligible)),
    }


def _market_summary(
    states: list[Mapping[str, Any]], technicals: list[Mapping[str, Any]], target: str
) -> dict[str, Any]:
    state_rows = [
        row for row in states
        if row.get("asOfDate") == target and row.get("availabilityStatus") != "UNAVAILABLE"
    ]
    change_rows = [row for row in state_rows if isinstance(row.get("changeRate"), (int, float))]
    advancers = sum(row["changeRate"] > 0 for row in change_rows)
    decliners = sum(row["changeRate"] < 0 for row in change_rows)
    unchanged = sum(row["changeRate"] == 0 for row in change_rows)
    technical_by_code = {str(row.get("itemCode")): row for row in technicals}
    metric_rows = []
    for row in state_rows:
        merged = dict(row)
        merged.update({
            key: technical_by_code.get(str(row.get("itemCode")), {}).get(key)
            for key in ("ma5", "ma20", "ma60", "ma120")
        })
        metric_rows.append(merged)
    leaders = sorted(
        (
            {
                "itemCode": row.get("itemCode"),
                "changeRate": row.get("changeRate"),
                "price": row.get("price"),
                "volume": row.get("volume"),
                "volumeRatio20": technical_by_code.get(str(row.get("itemCode")), {}).get("volumeRatio20"),
                "trend": technical_by_code.get(str(row.get("itemCode")), {}).get("trend"),
                "status": row.get("availabilityStatus"),
                "asOfDate": row.get("asOfDate"),
            }
            for row in change_rows
        ),
        key=lambda row: (float(row.get("changeRate") or 0), str(row.get("itemCode") or "")),
        reverse=True,
    )[:5]
    return {
        "totalCount": len(states),
        "observedCount": len(change_rows),
        "breadth": {
            "advancers": advancers,
            "decliners": decliners,
            "unchanged": unchanged,
            "eligibleCount": len(change_rows),
        },
        "metrics": {
            "aboveMA5": _metric_summary(metric_rows, "ma5"),
            "aboveMA20": _metric_summary(metric_rows, "ma20"),
            "aboveMA60": _metric_summary(metric_rows, "ma60"),
            "aboveMA120": _metric_summary(metric_rows, "ma120"),
            "breakout20": _metric_summary(metric_rows, "breakout20"),
            "breakout60": _metric_summary(metric_rows, "breakout60"),
            "volumeAbove20DayAverage": _metric_summary(metric_rows, "volumeRatio20"),
        },
        "leaders": leaders,
    }


def _registered_projection(
    root: Path, technicals: Mapping[str, Any], states: Mapping[str, Any], universe: Mapping[str, Any]
) -> dict[str, Any]:
    names = {
        str(row.get("itemCode")): row.get("stockName")
        for row in universe.get("stocks", [])
        if isinstance(row, Mapping) and row.get("enabled") is True
    }
    tech_by_code = {str(row.get("itemCode")): row for row in technicals.get("datas", [])}
    state_by_code = {str(row.get("itemCode")): row for row in states.get("datas", [])}
    codes = sorted(set(names) | {
        str(code) for values in (universe.get("watchlists") or {}).values() for code in values
    })
    items = []
    missing = []
    for code in codes:
        tech = tech_by_code.get(code)
        state = state_by_code.get(code)
        if not tech or not state:
            missing.append(code)
            continue
        items.append({
            "itemCode": code,
            "stockName": names.get(code),
            "asOfDate": tech.get("asOfDate"),
            "status": tech.get("availabilityStatus"),
            "metrics": {
                key: tech.get(key)
                for key in (
                    "ma5", "ma20", "ma60", "ma120", "rsi14", "macd", "macdSignal",
                    "macdHistogram", "bollinger", "return5d", "return20d", "return60d",
                    "return120d", "avgVolume20", "volumeRatio20", "high20", "high60",
                    "high120", "breakout20", "breakout60", "breakout120", "trend",
                )
            },
            "state": {
                key: state.get(key)
                for key in (
                    "price", "previousClose", "changeRate", "volume", "ma20", "ma60",
                    "volumeRatio20", "priceVsMA20", "priceVsMA60", "maAlignment",
                    "breakout20", "breakout60", "trend", "availabilityStatus",
                )
            },
            "provenanceRef": f"provenance.json#byCode.{code}",
        })
    return {
        "status": "AVAILABLE" if not missing else "PARTIAL" if items else "UNAVAILABLE",
        "configuredCount": len(codes),
        "observedCount": len(items),
        "missingCodes": compact_detail_reference(
            missing,
            source_path="registered-universe-technicals.json#missingCodes",
        ),
        "detailCount": len(items),
        "detailSource": "registered-universe-technicals.json,registered-universe-states.json",
        "detailSha256": None,
        "watchlists": universe.get("watchlists") or {},
        "source": "registered-universe-projection-from-full-market-derivatives",
    }


def build_report_contract_projection(
    derived_dir: str | Path,
    universe_path: str | Path,
    output_path: str | Path | None = None,
    expected_target_date: str | None = None,
) -> dict[str, Any]:
    root = Path(derived_dir)
    technicals = _read(root / "technicals.json")
    states = _read(root / "states.json")
    quality = _read(root / "quality.json")
    groups = _read(root / "group-states.json")
    manifest = _read(root / "manifest.json")
    universe = _read(Path(universe_path))
    target = _normalize_date(technicals.get("targetDate"), "technicals.targetDate")
    if expected_target_date is not None:
        expected = _normalize_date(expected_target_date, "expected_target_date")
        if target != expected:
            raise ReportContractProjectionError(
                f"staged targetDate {target} does not match expected targetDate {expected}"
            )
    if states.get("targetDate") != target:
        raise ReportContractProjectionError("staged target dates are invalid or mixed")
    if quality.get("targetDate") != target or groups.get("targetDate") != target:
        raise ReportContractProjectionError("staged quality/group dates do not match")
    source_target = (manifest.get("source") or {}).get("targetDate")
    if source_target is not None and source_target != target:
        raise ReportContractProjectionError("staged manifest targetDate does not match")
    if technicals.get("fallbackUsed") or states.get("fallbackUsed") or quality.get("fallbackUsed"):
        raise ReportContractProjectionError("fallback data is forbidden")
    technical_rows = technicals.get("datas") or []
    state_rows = states.get("datas") or []
    if len(technical_rows) != len(state_rows) or len(technical_rows) != int(quality.get("totalAssets") or 0):
        raise ReportContractProjectionError("full-market row coverage mismatch")
    _validate_rows_not_after(technical_rows, target, "technicals")
    _validate_rows_not_after(state_rows, target, "states")
    rolling_manifest = _read(root / "rolling-history-manifest.json")
    if rolling_manifest.get("targetDate") != target:
        raise ReportContractProjectionError("rolling history targetDate does not match")
    provenance = _read(root / "provenance.json")
    if len(provenance.get("byCode") or {}) != len(technical_rows):
        raise ReportContractProjectionError("provenance coverage mismatch")

    generated_at = str(quality.get("generatedAt") or technicals.get("generatedAt") or datetime.now().astimezone().isoformat())
    artifact_names = (
        "technicals.json", "states.json", "registered-universe-technicals.json",
        "registered-universe-states.json", "group-states.json", "provenance.json",
        "quality.json", "manifest.json", "rolling-history-manifest.json",
    )
    artifacts = {name: _artifact(root, name) for name in artifact_names}
    quality_summary = {
        key: quality.get(key)
        for key in (
            "status", "totalAssets", "byAssetType", "statusCounts", "missingCodes",
            "historyCoveragePct", "historicalIndicatorSource",
            "historicalFallbackAllowed", "currentSnapshotSource", "historicalGenerationId",
            "historicalTargetDate", "historicalReceiptStatus", "historicalLineageStatus",
            "historicalEntityCount", "historicalInstrumentCount", "historicalAdjustmentStatus",
            "historicalObservedInstrumentCount",
            "noDailyBar", "date20261030Rows", "historicalGroups", "rollingHistory",
        )
    }
    detail_sha = artifacts["quality.json"]["sha256"]
    for key in (
        "missingCodes", "missingHistoryCodes", "regularSessionUnavailableCodes",
        "currentSnapshotMissingCodes",
    ):
        quality_summary[key] = compact_detail_reference(
            quality.get(key) or [],
            source_path=f"quality.json#{key}",
            source_sha256=detail_sha,
        )
    if str(quality_summary.get("historicalIndicatorSource") or technicals.get("historicalIndicatorSource") or "").upper() == "KRX":
        krx_instrument_count = (
            quality_summary.get("historicalInstrumentCount")
            or quality_summary.get("instrumentCount")
            or sum((quality_summary.get("byAssetType") or {}).values())
            or len(technical_rows)
        )
        krx_observed_count = sum(
            row.get("asOfDate") == target
            and str(row.get("availabilityStatus") or row.get("status") or "AVAILABLE").upper() != "UNAVAILABLE"
            for row in technical_rows
        )
        quality_summary["historicalInstrumentCount"] = krx_instrument_count
        quality_summary["historicalObservedInstrumentCount"] = krx_observed_count
        quality_summary["historyCoveragePct"] = (
            round(krx_observed_count * 100 / krx_instrument_count, 6)
            if isinstance(krx_instrument_count, int) and krx_instrument_count > 0 else None
        )
    full_market_projection = {
        **_market_summary(state_rows, technical_rows, target),
        "quality": quality_summary,
        "artifacts": {
            name: {key: value for key, value in ref.items() if key != "path"}
            | {"path": str(Path(ref["path"]).name)}
            for name, ref in artifacts.items()
        },
    }
    historical_source = quality_summary.get("historicalIndicatorSource") or technicals.get("historicalIndicatorSource") or "UNAVAILABLE"
    contract = {
        "schemaVersion": "market-report-contract-v1",
        "status": quality.get("status", "PARTIAL"),
        "asOf": target,
        "observedAt": generated_at,
        "historicalIndicatorSource": quality.get("historicalIndicatorSource") or technicals.get("historicalIndicatorSource"),
        "sourceGeneration": quality.get("historicalGenerationId") or technicals.get("generationId"),
        "historicalGenerationId": quality.get("historicalGenerationId") or technicals.get("generationId"),
        "historicalTargetDate": quality.get("historicalTargetDate") or target,
        "historicalFallbackAllowed": quality.get("historicalFallbackAllowed", technicals.get("historicalFallbackAllowed", False)),
        "currentSnapshotSource": quality.get("currentSnapshotSource") or technicals.get("currentSnapshotSource") or "NAVER_SEPARATE",
        "historicalLineage": {
            "source": quality.get("historicalIndicatorSource") or technicals.get("historicalIndicatorSource"),
            "generationId": quality.get("historicalGenerationId") or technicals.get("generationId"),
            "targetDate": quality.get("historicalTargetDate") or target,
            "receiptStatus": quality.get("historicalReceiptStatus") or technicals.get("receiptStatus") or "UNAVAILABLE",
            "lineageStatus": quality.get("historicalLineageStatus") or "UNAVAILABLE",
            "fallbackAllowed": quality.get("historicalFallbackAllowed", technicals.get("historicalFallbackAllowed", False)),
            "currentSnapshotSource": quality.get("currentSnapshotSource") or technicals.get("currentSnapshotSource") or "NAVER_SEPARATE",
        },
        "tradingDateStatus": quality.get("tradingDateStatus", "UNAVAILABLE"),
        "sourceQualityStatus": quality.get("status", "PARTIAL"),
        "sourceBindings": {
            "fullMarketManifest": "manifest.json",
            "fullMarketDetails": "technicals.json,states.json",
            "fullMarketProvenance": "provenance.json",
            "historicalTechnicalMarket": "reportContract.historicalTechnicalMarket",
            "registeredUniverse": "registered-universe-technicals.json,registered-universe-states.json",
            "groupStates": "group-states.json",
            "rollingHistory": "rolling-history-manifest.json",
            "currentSnapshot": "SEPARATE_NOT_INCLUDED",
        },
        "currentMarketObservation": {
            "status": "UNAVAILABLE",
            "source": "NAVER_CURRENT_SNAPSHOT",
            "observedDate": None,
            "sourceTime": None,
            "scope": "STOCK_ONLY",
            "totalCount": None,
            "breadth": {"advancers": None, "decliners": None, "unchanged": None},
            "qualityStatus": "SEPARATE_NOT_INCLUDED",
            "qualityWarnings": [],
            "reason": "CURRENT_SNAPSHOT_IS_NOT_BACKFILL_DERIVATIVE",
        },
        "historicalTechnicalMarket": {
            "status": "AVAILABLE" if full_market_projection.get("metrics") else "UNAVAILABLE",
            "source": historical_source,
            "targetDate": quality_summary.get("historicalTargetDate") or target,
            "generationId": quality_summary.get("historicalGenerationId") or technicals.get("generationId"),
            "scope": "STOCK_ETF",
            "instrumentCount": quality_summary.get("historicalInstrumentCount") or len(technical_rows),
            "breadth": full_market_projection.get("breadth") or {},
            "metrics": full_market_projection.get("metrics") or {},
            "qualityStatus": quality_summary.get("status") or "UNAVAILABLE",
            "qualityWarnings": [],
        },
        "fullMarket": full_market_projection,
        "registeredUniverse": _registered_projection(root, technicals, states, universe),
        "groupStates": groups,
        "currentSnapshot": {
            "status": "SEPARATE_NOT_INCLUDED",
            "reason": "CURRENT_SNAPSHOT_IS_NOT_BACKFILL_DERIVATIVE",
        },
        "provenance": {
            "sourceStaging": quality.get("sourceStaging"),
            "targetDate": target,
            "manifest": "manifest.json",
            "manifestSha256": artifacts["manifest.json"]["sha256"],
            "fullMarketRows": len(technical_rows),
            "provenanceRows": len(provenance["byCode"]),
            "fallbackUsed": False,
        },
    }
    payload = (json.dumps(contract, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(payload) > MAX_WORK_CONTRACT_BYTES:
        raise ReportContractProjectionError(f"projection exceeds {MAX_WORK_CONTRACT_BYTES} bytes: {len(payload)}")
    validation = validate_contract_payload(payload, expected_as_of=target, expected_observed_at=generated_at)
    if validation.get("status") != "VALID":
        raise ReportContractProjectionError(f"projection validation failed: {validation}")
    result = {
        "contract": contract,
        "payload": payload,
        "bytes": len(payload),
        "sha256": sha256_bytes(payload),
        "artifacts": artifacts,
    }
    if output_path is not None:
        output = Path(output_path)
        output.write_bytes(payload)
        result["output"] = str(output)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--derived-dir", required=True)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--target-date")
    args = parser.parse_args(argv)
    result = build_report_contract_projection(
        args.derived_dir,
        args.universe,
        args.output,
        expected_target_date=args.target_date,
    )
    manifest_path = Path(args.output).with_name("report-contract-manifest.json")
    manifest = {
        "schemaVersion": 1,
        "contract": {
            "path": Path(args.output).name,
            "bytes": result["bytes"],
            "sha256": result["sha256"],
        },
        "sourceArtifacts": result["artifacts"],
        "targetDate": result["contract"]["asOf"],
        "status": result["contract"]["status"],
    }
    manifest_payload = (json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    manifest_path.write_bytes(manifest_payload)
    print(json.dumps({
        "output": str(args.output),
        "bytes": result["bytes"],
        "sha256": result["sha256"],
        "manifest": str(manifest_path),
        "manifestBytes": len(manifest_payload),
        "status": result["contract"]["status"],
        "asOf": result["contract"]["asOf"],
    }, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
