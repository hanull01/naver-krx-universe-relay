"""Validate and map an authorized KRX M10 monitoring generation.

The validator is deliberately disconnected from GitHub workflows.  A Mac-side
delivery process must place a receipt, the M10 monitoring interchange, and the
matching generation manifest in one staging directory.  Raw KRX data and the
full metrics bundle are never accepted as report inputs here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from tools.krx_m10_market_aggregates import build_m10_market_aggregates, safe_number

HEX_SHA = re.compile(r"^[0-9a-f]{64}$")
DATE = re.compile(r"^\d{8}$")
GENERATION = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
STOCK_OR_ETF_CODE = re.compile(r"^[A-Z0-9]{6}$")
M10_SCHEMA = "krx_monitoring_interchange_v1"
GENERATION_SCHEMA = "krx_m9_generation_manifest_v1"
REQUIRED_ENTITY_FIELDS = {
    "code", "name", "category", "identity", "asof", "source_api",
    "open_regular", "close_regular", "official_daily_volume", "MA",
    "returns_pct", "high_breakouts", "volume_ratio_prev20",
}


class KrxReceiptError(ValueError):
    """The staged generation cannot be trusted for monitoring ingestion."""


def _date(value: Any, field: str) -> str:
    normalized = str(value or "").replace("-", "")
    if not DATE.fullmatch(normalized):
        raise KrxReceiptError(f"INVALID_{field.upper()}")
    try:
        date.fromisoformat(f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:]}")
    except ValueError as exc:
        raise KrxReceiptError(f"INVALID_{field.upper()}") from exc
    return normalized


def _sha256(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KrxReceiptError(f"JSON_READ_FAILED:{path.name}") from exc
    if not isinstance(value, dict):
        raise KrxReceiptError(f"OBJECT_REQUIRED:{path.name}")
    return value


def _artifact(staging: Path, entry: Mapping[str, Any], expected_name: str) -> Path:
    if entry.get("name") != expected_name:
        raise KrxReceiptError(f"INVALID_{expected_name.upper().replace('.', '_')}_NAME")
    digest = entry.get("sha256")
    if not isinstance(digest, str) or not HEX_SHA.fullmatch(digest):
        raise KrxReceiptError(f"INVALID_{expected_name.upper().replace('.', '_')}_SHA")
    size = entry.get("bytes")
    if type(size) is not int or size <= 0:
        raise KrxReceiptError(f"INVALID_{expected_name.upper().replace('.', '_')}_BYTES")
    candidate = staging / expected_name
    if candidate.is_symlink() or not candidate.is_file():
        raise KrxReceiptError(f"ARTIFACT_MISSING_OR_SYMLINK:{expected_name}")
    if candidate.stat().st_size != size or _sha256(candidate) != digest:
        raise KrxReceiptError(f"ARTIFACT_INTEGRITY_MISMATCH:{expected_name}")
    return candidate


def _validate_entity(row: Any, expected_date: str) -> None:
    if not isinstance(row, Mapping):
        raise KrxReceiptError("ENTITY_OBJECT_REQUIRED")
    missing = REQUIRED_ENTITY_FIELDS - set(row)
    if missing:
        raise KrxReceiptError("ENTITY_FIELDS_MISSING:" + ",".join(sorted(missing)))
    code = row.get("code")
    category = row.get("category")
    if not isinstance(code, str) or not code.strip() or len(code) > 64:
        raise KrxReceiptError("INVALID_ENTITY_CODE")
    if category in {"stock", "etf"} and not STOCK_OR_ETF_CODE.fullmatch(code):
        raise KrxReceiptError("INVALID_STOCK_OR_ETF_CODE")
    if not isinstance(row.get("name"), str) or not row["name"].strip():
        raise KrxReceiptError("INVALID_ENTITY_NAME")
    if row.get("asof") != expected_date:
        raise KrxReceiptError("ENTITY_DATE_MISMATCH")
    if not isinstance(row.get("MA"), Mapping):
        raise KrxReceiptError("INVALID_ENTITY_MA")
    if not isinstance(row.get("returns_pct"), Mapping):
        raise KrxReceiptError("INVALID_ENTITY_RETURNS")
    if not isinstance(row.get("high_breakouts"), Mapping):
        raise KrxReceiptError("INVALID_ENTITY_BREAKOUTS")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")


def map_m10_entity(row: Mapping[str, Any]) -> dict[str, Any]:
    """Map one verified M10 entity to the report adapter's neutral shape.

    The current quote layer is intentionally absent.  Consumers may attach a
    NAVER present-value snapshot later; they must not replace these completed
    KRX-session fields or recompute the KRX moving averages.
    """
    return {
        "itemCode": row["code"],
        "name": row["name"],
        "assetType": row["category"],
        "asOfDate": row["asof"],
        "technicals": {
            "ma5": safe_number(row["MA"].get("MA5")),
            "ma20": safe_number(row["MA"].get("MA20")),
            "ma60": safe_number(row["MA"].get("MA60")),
            "ma120": safe_number(row["MA"].get("MA120")),
            "ma240": safe_number(row["MA"].get("MA240")),
            "return1dPct": safe_number(row["returns_pct"].get("return_1d_pct")),
            "return5dPct": safe_number(row["returns_pct"].get("return_5d_pct")),
            "return20dPct": safe_number(row["returns_pct"].get("return_20d_pct")),
            "return60dPct": safe_number(row["returns_pct"].get("return_60d_pct")),
            "return120dPct": safe_number(row["returns_pct"].get("return_120d_pct")),
            "return240dPct": safe_number(row["returns_pct"].get("return_240d_pct")),
            "highBreakoutPrev20": row["high_breakouts"].get("high_breakout_prev20"),
            "highBreakoutPrev60": row["high_breakouts"].get("high_breakout_prev60"),
            "volumeRatioPrev20": safe_number(row.get("volume_ratio_prev20")),
            "volumeRatio20": safe_number(row.get("volume_ratio_prev20")),
        },
        "regularSession": {
            "open": safe_number(row.get("open_regular")),
            "close": safe_number(row.get("close_regular")),
            "volume": safe_number(row.get("official_daily_volume")),
            "basis": "KRX_OFFICIAL_1530_CLOSE",
            "aftermarketUsed": False,
        },
        "provenance": {
            "source": "KRX_OPEN_API",
            "sourceApi": row["source_api"],
            "identity": row["identity"],
            "sourceDate": row["asof"],
            "historicalIndicatorSource": "KRX_M10_GENERATION",
        },
        "currentSnapshot": None,
    }


def validate_m10_staging(staging_dir: str | Path, *, expected_date: str | None = None) -> dict[str, Any]:
    """Validate a real M10-shaped staging directory without publishing it."""
    staging = Path(staging_dir).resolve(strict=True)
    receipt = _object(staging / "receipt.json")
    if receipt.get("schemaVersion") != 2:
        raise KrxReceiptError("UNSUPPORTED_RECEIPT_SCHEMA")
    if receipt.get("source") != "KRX_OPEN_API":
        raise KrxReceiptError("SOURCE_MUST_BE_KRX")
    trading_date = _date(receipt.get("tradingDate"), "trading_date")
    if expected_date is not None and trading_date != _date(expected_date, "expected_date"):
        raise KrxReceiptError("TARGET_DATE_MISMATCH")
    generation_id = receipt.get("generationId")
    if not isinstance(generation_id, str) or not GENERATION.fullmatch(generation_id):
        raise KrxReceiptError("INVALID_GENERATION_ID")
    if receipt.get("generationStatus") != "VERIFIED":
        raise KrxReceiptError("GENERATION_NOT_VERIFIED")
    if receipt.get("publicationAuthorized") is not True:
        raise KrxReceiptError("REDISTRIBUTION_NOT_AUTHORIZED")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != {"monitoring", "generationManifest"}:
        raise KrxReceiptError("INVALID_ARTIFACT_SET")
    monitoring_path = _artifact(staging, artifacts["monitoring"], "monitoring_interchange.json")
    manifest_path = _artifact(staging, artifacts["generationManifest"], "generation_manifest.json")
    monitoring = _object(monitoring_path)
    manifest = _object(manifest_path)
    if monitoring.get("schema") != M10_SCHEMA:
        raise KrxReceiptError("UNSUPPORTED_MONITORING_SCHEMA")
    if monitoring.get("source") != "KRX_OPEN_API":
        raise KrxReceiptError("MONITORING_SOURCE_MUST_BE_KRX")
    if _date(monitoring.get("date"), "monitoring_date") != trading_date:
        raise KrxReceiptError("INTERCHANGE_DATE_MISMATCH")
    if monitoring.get("aftermarket_used") is not False or monitoring.get("price_reference") != "KRX_OFFICIAL_1530_CLOSE":
        raise KrxReceiptError("SESSION_CONTRACT_MISMATCH")
    entities = monitoring.get("entities")
    if not isinstance(entities, list) or monitoring.get("entity_count") != len(entities):
        raise KrxReceiptError("ENTITY_COUNT_MISMATCH")
    seen_instrument_codes: set[str] = set()
    seen_identities: set[str] = set()
    for row in entities:
        _validate_entity(row, trading_date)
        if row["identity"] in seen_identities:
            raise KrxReceiptError(f"DUPLICATE_ENTITY_IDENTITY:{row['identity']}")
        # KOSPI/KOSDAQ sector index labels can legitimately share a display
        # code (for example, "IT 서비스"); their source-qualified identity is
        # the unique key. Instrument codes remain globally unique.
        if row["category"] in {"stock", "etf"}:
            if row["code"] in seen_instrument_codes:
                raise KrxReceiptError(f"DUPLICATE_ENTITY_CODE:{row['code']}")
            seen_instrument_codes.add(row["code"])
        seen_identities.add(row["identity"])
    if manifest.get("schema") != GENERATION_SCHEMA:
        raise KrxReceiptError("UNSUPPORTED_GENERATION_SCHEMA")
    if manifest.get("generation") != generation_id or _date(manifest.get("date"), "manifest_date") != trading_date:
        raise KrxReceiptError("GENERATION_MANIFEST_MISMATCH")
    if manifest.get("entity_count") != len(entities):
        raise KrxReceiptError("MANIFEST_ENTITY_COUNT_MISMATCH")
    if not isinstance(manifest.get("artifacts"), Mapping):
        raise KrxReceiptError("GENERATION_ARTIFACTS_MISSING")
    manifest_monitoring = manifest["artifacts"].get("monitoring")
    receipt_monitoring = artifacts["monitoring"]
    if not isinstance(manifest_monitoring, Mapping):
        raise KrxReceiptError("GENERATION_MONITORING_ARTIFACT_MISSING")
    if (manifest_monitoring.get("sha256") != receipt_monitoring.get("sha256")
            or manifest_monitoring.get("bytes") != receipt_monitoring.get("bytes")):
        raise KrxReceiptError("GENERATION_MONITORING_ARTIFACT_MISMATCH")
    source_metrics_sha = monitoring.get("source_metrics_gzip_sha256")
    if not isinstance(source_metrics_sha, str) or not HEX_SHA.fullmatch(source_metrics_sha):
        raise KrxReceiptError("INVALID_SOURCE_METRICS_SHA")
    return {
        "status": "VALIDATED_NOT_CONNECTED",
        "source": "KRX_OPEN_API",
        "tradingDate": trading_date,
        "generationId": generation_id,
        "generationStatus": "VERIFIED",
        "monitoringSchema": monitoring["schema"],
        "monitoringSha256": artifacts["monitoring"]["sha256"],
        "monitoringBytes": artifacts["monitoring"]["bytes"],
        "generationManifestSha256": artifacts["generationManifest"]["sha256"],
        "entityCount": len(entities),
        "sourceMetricsGzipSha256": source_metrics_sha,
        "fieldMappingVersion": "krx-m10-to-monitoring-v1",
        "reportInputReady": False,
    }


def stage_m10_report_inputs(
    staging_dir: str | Path,
    output_dir: str | Path,
    *,
    expected_date: str | None = None,
) -> dict[str, Any]:
    """Materialize verified M10 fields for the existing staged report adapter.

    This is a local, authenticated-receipt staging operation only. It writes
    a compact adapter input directory and never updates a GitHub workflow,
    production report, baseline, or public repository artifact.
    """
    validation = validate_m10_staging(staging_dir, expected_date=expected_date)
    staging = Path(staging_dir).resolve(strict=True)
    output = Path(output_dir)
    monitoring = _object(staging / "monitoring_interchange.json")
    entities = monitoring["entities"]
    target = validation["tradingDate"]
    generation = validation["generationId"]
    source_sha = validation["monitoringSha256"]
    instruments = [row for row in entities if row["category"] in {"stock", "etf"}]
    indexes = [row for row in entities if row["category"] == "index"]
    aggregates = build_m10_market_aggregates(monitoring)

    def provenance(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "sourceCanonicalPath": "m10/monitoring_interchange.json",
            "sourceCanonicalSha256": source_sha,
            "sourceStaging": "KRX_M10_GENERATION",
            "sourceGenerationId": generation,
            "sourceApi": row["source_api"],
            "sourceDate": row["asof"],
            "aftermarketUsed": False,
        }

    technical_rows = []
    state_rows = []
    null_counts: dict[str, int] = {}
    for row in instruments:
        mapped = map_m10_entity(row)
        technicals = dict(mapped["technicals"])
        for field, value in technicals.items():
            if value is None:
                null_counts[field] = null_counts.get(field, 0) + 1
        technical_rows.append({
            "itemCode": row["code"], "stockName": row["name"], "asOfDate": target,
            "technicals": technicals, "provenance": provenance(row),
        })
        state_rows.append({
            "itemCode": row["code"], "asOfDate": target,
            "state": {"status": "KRX_CONFIRMED", "sourceDate": target, "generationId": generation},
            "provenance": provenance(row),
        })

    counts = {
        "entityCount": len(entities), "instrumentCount": len(instruments),
        "indexCount": len(indexes), "stockCount": sum(r["category"] == "stock" for r in instruments),
        "etfCount": sum(r["category"] == "etf" for r in instruments),
    }
    common = {
        "schemaVersion": "krx-m10-report-staging-v1", "targetDate": target,
        "source": "KRX_M10_GENERATION", "generationId": generation,
        "sourceArtifactSha256": source_sha, "counts": counts,
        "historicalIndicatorSource": "KRX",
        "historicalPriority": "PRIMARY",
        "historicalFallbackAllowed": False,
        "conflictPolicy": "KRX_PRIMARY_NO_MERGE",
        "missingDataPolicy": "MISSING_OR_LAGGED_NO_NAVER_HISTORICAL_FALLBACK",
        "currentObservationPolicy": "NAVER_CURRENT_AND_AFTERMARKET_SEPARATE",
        "marketAggregateSchema": aggregates["schemaVersion"],
    }
    _write_json(output / "technicals.json", {**common, "datas": technical_rows, "scope": "FULL_MARKET_INSTRUMENTS"})
    _write_json(output / "states.json", {**common, "datas": state_rows, "scope": "FULL_MARKET_INSTRUMENTS"})
    _write_json(output / "provenance.json", {
        "schemaVersion": "krx-m10-provenance-v1", "targetDate": target,
        "source": "KRX_M10_GENERATION", "generationId": generation,
        "sourceCanonicalSha256": source_sha,
        "byCode": {row["code"]: provenance(row) for row in instruments},
    })
    _write_json(output / "conflict-audit.json", {
        "schemaVersion": "krx-m10-conflict-audit-v1",
        "targetDate": target,
        "generationId": generation,
        "status": "NO_NAVER_HISTORICAL_COMPARISON",
        "policy": "KRX_PRIMARY_NO_MERGE",
        "entries": [],
        "note": (
            "NAVER historical indicators are excluded from the KRX input path; "
            "current and aftermarket observations remain separate."
        ),
    })
    _write_json(output / "group-states.json", {
        "schemaVersion": "krx-m10-group-state-v1", "targetDate": target,
        "availabilityStatus": "KRX_M10_INDEX_SEPARATE", "generationId": generation,
        "counts": counts, "indexes": [{"code": row["code"], "name": row["name"], "asof": target} for row in indexes],
    })
    aggregate_full_market = {
        "status": "PARTIAL",
        "asOfDate": target,
        "snapshotDate": target,
        "snapshotDateStatus": "DERIVED_TARGET_DATE",
        "sourceQualityStatus": "KRX_M10_STAGED_NOT_FINAL",
        "qualityStatus": "KRX_M10_STAGED_NOT_FINAL",
        "historicalIndicatorSource": "KRX",
        "historicalPriority": "PRIMARY",
        "historicalFallbackAllowed": False,
        "quality": {
            **counts,
            "currentCount": len(instruments),
            "authoritativeCount": len(instruments),
        },
        "authoritativeCount": len(instruments),
        "tradingDate": target,
        "baselineAsOfDate": target,
        "breadth": aggregates["markets"]["TOTAL"],
        "leaders": aggregates["markets"]["TOTAL"]["leaderCandidates"],
        "markets": aggregates["markets"],
        "marketBreakdown": aggregates["marketBreakdown"],
        "indexIdentities": aggregates["indexIdentities"],
        "indicatorAvailability": aggregates["indicatorAvailability"],
        "marketTradingValue": {},
    }
    # The existing report loader requires a derived projection. This explicit
    # PARTIAL object carries only metadata; it never pretends to be a FINAL
    # breadth/leader/baseline result.
    _write_json(output / "report-contract-projection.json", {
        "schemaVersion": "krx-m10-report-projection-v1", "status": "PARTIAL",
        "asOf": target, "observedAt": None, "source": "KRX_M10_GENERATION",
        "sourceQualityStatus": "KRX_M10_STAGED_NOT_FINAL",
        "historicalIndicatorSource": "KRX",
        "historicalPriority": "PRIMARY",
        "historicalFallbackAllowed": False,
        "conflictPolicy": "KRX_PRIMARY_NO_MERGE",
        "generationId": generation, "fullMarket": aggregate_full_market,
    })
    return {
        "status": "STAGED_NOT_CONNECTED", "targetDate": target, "generationId": generation,
        "entityCount": len(entities), "instrumentCount": len(instruments), "indexCount": len(indexes),
        "nullIndicatorCounts": null_counts, "outputDir": str(output), "reportInputReady": False,
        "conflictAuditPath": str(output / "conflict-audit.json"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an authorized M10 staging receipt; no publication")
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--expected-date")
    args = parser.parse_args()
    try:
        result = validate_m10_staging(args.staging_dir, expected_date=args.expected_date)
    except (KrxReceiptError, OSError) as exc:
        parser.exit(2, f"KRX_RECEIPT_REJECTED:{exc}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
