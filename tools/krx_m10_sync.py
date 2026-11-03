"""Private, local-only delivery bridge for a verified KRX M10 generation.

The bridge packages only the monitoring interchange and generation manifest.
It never reads or transmits KRX raw files, metrics bundles, or credentials to
GitHub.  The durable outbox and receiver state make a later approved
transport replaceable without changing validation or idempotency semantics.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import argparse
from pathlib import Path
from typing import Any, Mapping


GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DATE_RE = re.compile(r"^\d{8}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_SCHEMA = "krx_m9_generation_manifest_v1"
MONITORING_SCHEMA = "krx_monitoring_interchange_v1"
TRANSFER_SCHEMA = "krx_m10_private_transfer_v1"


class SyncError(ValueError):
    """The source or receiver package cannot be trusted."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SyncError(f"JSON_READ_FAILED:{path.name}") from exc
    if not isinstance(value, dict):
        raise SyncError(f"JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        shutil.copyfile(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _date(value: Any, field: str) -> str:
    text = str(value or "").replace("-", "")
    if not DATE_RE.fullmatch(text):
        raise SyncError(f"INVALID_{field.upper()}")
    return text


def _generation_id(value: Any) -> str:
    if not isinstance(value, str) or not GENERATION_RE.fullmatch(value):
        raise SyncError("INVALID_GENERATION_ID")
    return value


def _artifact_record(manifest: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    record = (manifest.get("artifacts") or {}).get(key)
    if not isinstance(record, Mapping):
        raise SyncError(f"MANIFEST_ARTIFACT_MISSING:{key}")
    digest = record.get("sha256")
    if not isinstance(digest, str) or not SHA_RE.fullmatch(digest):
        raise SyncError(f"MANIFEST_ARTIFACT_SHA_INVALID:{key}")
    size = record.get("bytes")
    if type(size) is not int or size <= 0:
        raise SyncError(f"MANIFEST_ARTIFACT_BYTES_INVALID:{key}")
    return record


def _verify_artifact(path: Path, record: Mapping[str, Any], key: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise SyncError(f"ARTIFACT_MISSING:{key}")
    actual = {"bytes": path.stat().st_size, "sha256": _sha256(path)}
    if actual != {"bytes": record["bytes"], "sha256": record["sha256"]}:
        raise SyncError(f"ARTIFACT_INTEGRITY_MISMATCH:{key}")
    return actual


def _extension_review(metrics: Mapping[str, Any] | None) -> dict[str, Any]:
    available = set()
    if isinstance(metrics, Mapping):
        for row in (metrics.get("metrics") or {}).values():
            if isinstance(row, Mapping):
                available.update(row)
    candidates = {
        "relative_strength_v1_pct", "relative_strength_v2_daily_proxy_pct",
        "high52w", "low52w", "rolling_high_20", "rolling_high_60",
        "rolling_high_120", "rolling_high_240", "rolling_low_20",
        "rolling_low_60", "rolling_low_120", "rolling_low_240",
        "trade_value_krx_official_daily_aggregate", "trade_value_ma20_inclusive",
        "trade_value_ratio_20_inclusive", "trade_value_acceleration_3_over_20",
    }
    return {
        "status": "REVIEW_ONLY_NOT_TRANSMITTED",
        "availableInInternalMetrics": sorted(available & candidates),
        "transported": [],
        "reason": "CURRENT_MONITORING_INTERCHANGE_DOES_NOT_DECLARE_THESE_FIELDS",
    }


def inspect_current_generation(krx_project: str | Path, generation_id: str | None = None) -> dict[str, Any]:
    """Resolve CURRENT and verify the generation without modifying KRX files."""
    project = Path(krx_project).resolve(strict=True)
    runtime = project / "krx_m9_runtime_data"
    current_path = runtime / "CURRENT"
    current = current_path.read_text(encoding="utf-8").strip()
    current = _generation_id(current)
    if generation_id is not None and current != _generation_id(generation_id):
        raise SyncError("CURRENT_GENERATION_MISMATCH")
    generation_root = (runtime / "generations" / current).resolve(strict=True)
    generations_root = (runtime / "generations").resolve(strict=True)
    if generations_root not in generation_root.parents:
        raise SyncError("GENERATION_PATH_ESCAPES_RUNTIME")

    manifest_path = generation_root / "generation_manifest.json"
    manifest = _json(manifest_path)
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("generation") != current:
        raise SyncError("GENERATION_MANIFEST_INVALID")
    trading_date = _date(manifest.get("date"), "trading_date")
    if _date(manifest.get("metrics_date"), "metrics_date") != trading_date:
        raise SyncError("METRICS_DATE_MISMATCH")

    monitoring_path = generation_root / "consumers" / "monitoring_interchange.json"
    trading_path = generation_root / "consumers" / "trading_interchange.json"
    metrics_path = generation_root / "engine" / "latest_metrics.json"
    artifact_paths = {"monitoring": monitoring_path, "trading": trading_path, "metrics": metrics_path}
    artifact_meta = {}
    for key, path in artifact_paths.items():
        artifact_meta[key] = _verify_artifact(path, _artifact_record(manifest, key), key)

    monitoring = _json(monitoring_path)
    if monitoring.get("schema") != MONITORING_SCHEMA or monitoring.get("source") != "KRX_OPEN_API":
        raise SyncError("MONITORING_CONTRACT_INVALID")
    if _date(monitoring.get("date"), "monitoring_date") != trading_date:
        raise SyncError("MONITORING_DATE_MISMATCH")
    entities = monitoring.get("entities")
    if not isinstance(entities, list) or monitoring.get("entity_count") != len(entities):
        raise SyncError("MONITORING_ENTITY_COUNT_INVALID")
    identities = [row.get("identity") for row in entities if isinstance(row, Mapping)]
    if len(identities) != len(set(identities)):
        raise SyncError("MONITORING_DUPLICATE_IDENTITY")
    expected_count = manifest.get("consumer_counts", {}).get("monitoring")
    if expected_count != len(entities):
        raise SyncError("MANIFEST_MONITORING_COUNT_MISMATCH")

    metrics = _json(metrics_path)
    missing_return_rows = [
        row for row in entities
        if row.get("category") in {"stock", "etf"}
        and (row.get("returns_pct") or {}).get("return_1d_pct") is None
    ]
    return {
        "status": "VERIFIED",
        "currentPath": str(current_path),
        "generationId": current,
        "tradingDate": trading_date,
        "manifestSha256": _sha256(manifest_path),
        "manifestBytes": manifest_path.stat().st_size,
        "entityCount": len(entities),
        "consumerCounts": dict(manifest.get("consumer_counts") or {}),
        "artifacts": artifact_meta,
        "monitoringPath": str(monitoring_path),
        "metricsPath": str(metrics_path),
        "extensionReview": _extension_review(metrics),
        "directionExcluded": [
            {
                "identity": row.get("identity"),
                "code": row.get("code"),
                "name": row.get("name"),
                "reason": "HISTORY_BARS_LT_2_NO_PREVIOUS_AVAILABLE_CLOSE",
            }
            for row in missing_return_rows
        ],
    }


def stage_current_generation(
    krx_project: str | Path,
    outbox_root: str | Path,
    *,
    execution_id: str,
) -> dict[str, Any]:
    """Create a private local outbox package; no external transport occurs."""
    inspection = inspect_current_generation(krx_project)
    outbox = Path(outbox_root)
    package = outbox / "packages" / inspection["generationId"]
    package.mkdir(parents=True, exist_ok=True)
    source_root = Path(inspection["monitoringPath"]).parent
    manifest_source = Path(inspection["currentPath"]).parent / "generations" / inspection["generationId"] / "generation_manifest.json"
    _copy_atomic(Path(inspection["monitoringPath"]), package / "monitoring_interchange.json")
    _copy_atomic(manifest_source, package / "generation_manifest.json")
    transfer = {
        "schemaVersion": TRANSFER_SCHEMA,
        "transferId": f"{inspection['generationId']}:{inspection['tradingDate']}",
        "executionId": execution_id,
        "status": "STAGED_NOT_SENT",
        "publication": "PRIVATE_LOCAL_ONLY",
        "generationId": inspection["generationId"],
        "tradingDate": inspection["tradingDate"],
        "entityCount": inspection["entityCount"],
        "manifestSha256": inspection["manifestSha256"],
        "monitoringSha256": inspection["artifacts"]["monitoring"]["sha256"],
        "monitoringBytes": inspection["artifacts"]["monitoring"]["bytes"],
        "includedArtifacts": ["generation_manifest.json", "monitoring_interchange.json"],
        "rawIncluded": False,
        "metricsIncluded": False,
    }
    _write_json(package / "transfer_manifest.json", transfer)
    queue_path = outbox / "queue.json"
    queue = _json(queue_path) if queue_path.exists() else {"schemaVersion": "krx_m10_outbox_v1", "items": []}
    items = queue.setdefault("items", [])
    key = transfer["transferId"]
    existing = next((item for item in items if item.get("transferId") == key), None)
    if existing is None:
        items.append({"transferId": key, "package": str(package), "status": "PENDING", "attempts": 0})
    _write_json(queue_path, queue)
    return {**inspection, "status": "STAGED_NOT_SENT", "package": str(package), "queue": str(queue_path)}


def _validate_package(package: Path) -> dict[str, Any]:
    transfer = _json(package / "transfer_manifest.json")
    if transfer.get("schemaVersion") != TRANSFER_SCHEMA or transfer.get("publication") != "PRIVATE_LOCAL_ONLY":
        raise SyncError("TRANSFER_MANIFEST_INVALID")
    generation = _generation_id(transfer.get("generationId"))
    trading_date = _date(transfer.get("tradingDate"), "trading_date")
    monitoring_path = package / "monitoring_interchange.json"
    manifest_path = package / "generation_manifest.json"
    if _sha256(monitoring_path) != transfer.get("monitoringSha256"):
        raise SyncError("TRANSFER_MONITORING_SHA_MISMATCH")
    if transfer.get("monitoringBytes") != monitoring_path.stat().st_size:
        raise SyncError("TRANSFER_MONITORING_BYTES_MISMATCH")
    if _sha256(manifest_path) != transfer.get("manifestSha256"):
        raise SyncError("TRANSFER_MANIFEST_SHA_MISMATCH")
    monitoring = _json(monitoring_path)
    manifest = _json(manifest_path)
    if manifest.get("generation") != generation or _date(manifest.get("date"), "manifest_date") != trading_date:
        raise SyncError("TRANSFER_GENERATION_MISMATCH")
    if monitoring.get("schema") != MONITORING_SCHEMA or _date(monitoring.get("date"), "monitoring_date") != trading_date:
        raise SyncError("TRANSFER_MONITORING_CONTRACT_INVALID")
    if monitoring.get("entity_count") != transfer.get("entityCount") or len(monitoring.get("entities") or []) != transfer.get("entityCount"):
        raise SyncError("TRANSFER_ENTITY_COUNT_MISMATCH")
    return {"transfer": transfer, "monitoring": monitoring, "manifest": manifest}


def deliver_pending_once(
    outbox_root: str | Path,
    receiver_root: str | Path,
    *,
    failure_injection: bool = False,
) -> dict[str, Any]:
    """Deliver one queued package; a later invocation retries FAILED items."""
    outbox = Path(outbox_root)
    queue_path = outbox / "queue.json"
    queue = _json(queue_path)
    item = next((item for item in queue.get("items", []) if item.get("status") in {"PENDING", "FAILED"}), None)
    if item is None:
        return {"status": "NO_CHANGES", "reason": "QUEUE_EMPTY"}
    item["attempts"] = int(item.get("attempts") or 0) + 1
    package = Path(item["package"])
    try:
        verified = _validate_package(package)
        if failure_injection:
            raise SyncError("INJECTED_BEFORE_PROMOTION")
        transfer = verified["transfer"]
        receiver = Path(receiver_root)
        state_path = receiver / "received_state.json"
        state = _json(state_path) if state_path.exists() else {"schemaVersion": "krx_m10_receiver_v1", "accepted": []}
        transfer_id = transfer["transferId"]
        if any(row.get("transferId") == transfer_id for row in state.get("accepted", [])):
            result = {"status": "NO_CHANGES", "transferId": transfer_id, "reason": "ALREADY_ACCEPTED"}
        else:
            conflict = next((row for row in state.get("accepted", []) if row.get("tradingDate") == transfer["tradingDate"] and row.get("generationId") != transfer["generationId"]), None)
            if conflict:
                raise SyncError("SAME_DATE_DIFFERENT_GENERATION_REQUIRES_REVIEW")
            generation_dir = receiver / "generations" / transfer["generationId"]
            staging = receiver / ".staging" / transfer["transferId"]
            staging.mkdir(parents=True, exist_ok=True)
            _copy_atomic(package / "monitoring_interchange.json", staging / "monitoring_interchange.json")
            _copy_atomic(package / "generation_manifest.json", staging / "generation_manifest.json")
            _copy_atomic(package / "transfer_manifest.json", staging / "transfer_manifest.json")
            generation_dir.parent.mkdir(parents=True, exist_ok=True)
            if generation_dir.exists():
                shutil.rmtree(generation_dir)
            os.replace(staging, generation_dir)
            current_tmp = receiver / ".CURRENT.tmp"
            current_tmp.write_text(transfer["generationId"] + "\n", encoding="utf-8")
            os.replace(current_tmp, receiver / "CURRENT")
            state.setdefault("accepted", []).append({
                "transferId": transfer_id,
                "generationId": transfer["generationId"],
                "tradingDate": transfer["tradingDate"],
                "monitoringSha256": transfer["monitoringSha256"],
            })
            _write_json(state_path, state)
            result = {"status": "ACCEPTED", "transferId": transfer_id, "generationId": transfer["generationId"]}
        item["status"] = "DELIVERED"
        item["lastError"] = None
        _write_json(queue_path, queue)
        return result
    except Exception as exc:
        item["status"] = "FAILED"
        item["lastError"] = str(exc)
        _write_json(queue_path, queue)
        return {"status": "FAILED", "transferId": item.get("transferId"), "error": str(exc), "attempts": item["attempts"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local-only KRX M10 sync validation bridge")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--krx-project", type=Path, required=True)
    stage = sub.add_parser("stage")
    stage.add_argument("--krx-project", type=Path, required=True)
    stage.add_argument("--outbox", type=Path, required=True)
    stage.add_argument("--execution-id", required=True)
    deliver = sub.add_parser("deliver")
    deliver.add_argument("--outbox", type=Path, required=True)
    deliver.add_argument("--receiver", type=Path, required=True)
    deliver.add_argument("--inject-failure", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "inspect":
        result = inspect_current_generation(args.krx_project)
    elif args.command == "stage":
        result = stage_current_generation(args.krx_project, args.outbox, execution_id=args.execution_id)
    else:
        result = deliver_pending_once(args.outbox, args.receiver, failure_injection=args.inject_failure)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") not in {"FAILED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
