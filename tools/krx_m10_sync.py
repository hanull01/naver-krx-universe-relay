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
import fcntl
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol


GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DATE_RE = re.compile(r"^\d{8}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_SCHEMA = "krx_m9_generation_manifest_v1"
MONITORING_SCHEMA = "krx_monitoring_interchange_v1"
TRANSFER_SCHEMA = "krx_m10_private_transfer_v1"


class SyncError(ValueError):
    """The source or receiver package cannot be trusted."""


class AuthenticatedTransport(Protocol):
    """Interface reserved for a future approved private transport.

    The local bridge never instantiates or calls this interface.  A production
    adapter must authenticate the sender, verify the signed envelope, enforce
    an authorization scope, and reject replayed transfer IDs before delivery.
    """

    def send(self, package: Path, *, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


def validate_authenticated_envelope(
    envelope: Mapping[str, Any],
    *,
    allowed_senders: set[str],
    replay_ids: set[str],
    verifier: Any,
    now_epoch: int,
) -> dict[str, Any]:
    """Validate the security contract without enabling external transport.

    SHA-256 is intentionally not treated as sender authentication.  The
    caller must inject a verifier backed by the approved key-management
    system; no secret or signature material is logged by this module.
    """
    sender = envelope.get("sender")
    transfer_id = envelope.get("transferId")
    expires_at = envelope.get("expiresAt")
    if not isinstance(sender, str) or sender not in allowed_senders:
        raise SyncError("UNAUTHORIZED_SENDER")
    if not isinstance(transfer_id, str) or not transfer_id:
        raise SyncError("AUTH_TRANSFER_ID_MISSING")
    if transfer_id in replay_ids:
        raise SyncError("REPLAYED_TRANSFER")
    if type(expires_at) is not int or expires_at <= now_epoch:
        raise SyncError("AUTH_ENVELOPE_EXPIRED")
    if not callable(verifier) or not verifier(envelope):
        raise SyncError("AUTHENTICATION_FAILED")
    return {"sender": sender, "transferId": transfer_id, "authenticated": True}


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
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _fsync_dir(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _receiver_lock(receiver: Path) -> Iterator[bool]:
    receiver.mkdir(parents=True, exist_ok=True)
    lock_path = receiver / ".sync.lock"
    handle = lock_path.open("a+")
    acquired = False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        if acquired:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


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


def _empty_receiver_state() -> dict[str, Any]:
    return {
        "schemaVersion": "krx_m10_receiver_v1",
        "accepted": [],
        "rejected": [],
        "pendingPromotion": None,
        "currentGeneration": None,
        "currentTradingDate": None,
    }


def _load_receiver_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_receiver_state()
    state = _json(path)
    if state.get("schemaVersion") != "krx_m10_receiver_v1":
        raise SyncError("RECEIVER_STATE_SCHEMA_INVALID")
    if not isinstance(state.get("accepted", []), list) or not isinstance(state.get("rejected", []), list):
        raise SyncError("RECEIVER_STATE_LEDGER_INVALID")
    return state


def _receiver_record(state: Mapping[str, Any], generation_id: str) -> Mapping[str, Any] | None:
    return next(
        (row for row in state.get("accepted", []) if isinstance(row, Mapping) and row.get("generationId") == generation_id),
        None,
    )


def _receiver_generation_matches(generation_dir: Path, transfer: Mapping[str, Any]) -> bool:
    if not generation_dir.is_dir() or generation_dir.is_symlink():
        return False
    try:
        transfer_record = _json(generation_dir / "transfer_manifest.json")
        if transfer_record.get("transferId") != transfer.get("transferId"):
            return False
        _verify_artifact(
            generation_dir / "monitoring_interchange.json",
            {"bytes": transfer["monitoringBytes"], "sha256": transfer["monitoringSha256"]},
            "receiver_monitoring",
        )
        _verify_artifact(
            generation_dir / "generation_manifest.json",
            {"bytes": generation_dir.joinpath("generation_manifest.json").stat().st_size, "sha256": transfer["manifestSha256"]},
            "receiver_manifest",
        )
        return True
    except (OSError, KeyError, SyncError):
        return False


def _pending_record(transfer: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "transferId": transfer["transferId"],
        "generationId": transfer["generationId"],
        "tradingDate": transfer["tradingDate"],
        "manifestSha256": transfer["manifestSha256"],
        "monitoringSha256": transfer["monitoringSha256"],
        "monitoringBytes": transfer["monitoringBytes"],
        "entityCount": transfer["entityCount"],
    }


def _accepted_record(transfer: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "transferId": transfer["transferId"],
        "generationId": transfer["generationId"],
        "tradingDate": transfer["tradingDate"],
        "manifestSha256": transfer["manifestSha256"],
        "monitoringSha256": transfer["monitoringSha256"],
        "monitoringBytes": transfer["monitoringBytes"],
        "entityCount": transfer["entityCount"],
    }


def _current_generation(receiver: Path) -> str | None:
    current_path = receiver / "CURRENT"
    if not current_path.exists():
        return None
    current = _generation_id(current_path.read_text(encoding="utf-8").strip())
    if not (receiver / "generations" / current).is_dir():
        raise SyncError("CURRENT_GENERATION_MISSING")
    return current


def _write_receiver_state(path: Path, state: Mapping[str, Any], failure_point: str | None, point: str) -> None:
    if failure_point == point:
        raise SyncError(f"INJECTED_{point.upper()}")
    _write_json(path, state)


def _reconcile_receiver_state(receiver: Path, state: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Recover only forward-safe state; leave ambiguous state for review."""
    current_id = _current_generation(receiver)
    accepted = state.setdefault("accepted", [])
    pending = state.get("pendingPromotion")

    if pending is not None:
        if not isinstance(pending, Mapping):
            raise SyncError("RECEIVER_PENDING_STATE_INVALID")
        pending_id = _generation_id(pending.get("generationId"))
        pending_dir = receiver / "generations" / pending_id
        if pending_dir.exists() and not _receiver_generation_matches(pending_dir, pending):
            raise SyncError("RECEIVER_PENDING_GENERATION_MISMATCH")
        pending_record = next((row for row in accepted if row.get("transferId") == pending.get("transferId")), None)
        if pending_dir.exists() and pending_record is None:
            accepted.append(dict(pending))
            pending_record = pending
            _write_json(receiver / "received_state.json", state)
        if pending_dir.exists() and current_id is None:
            _write_text_atomic(receiver / "CURRENT", pending_id + "\n")
            current_id = pending_id
        elif pending_dir.exists() and current_id != pending_id:
            current_record = _receiver_record(state, current_id) if current_id is not None else None
            if current_record is None:
                raise SyncError("RECEIVER_STATE_REQUIRES_REVIEW")
            current_date = _date(current_record.get("tradingDate"), "receiver_current_date")
            pending_date = _date(pending.get("tradingDate"), "pending_date")
            if pending_date > current_date:
                _write_text_atomic(receiver / "CURRENT", pending_id + "\n")
                current_id = pending_id
            elif pending_date < current_date:
                raise SyncError("RECEIVER_STATE_REQUIRES_REVIEW")
            else:
                raise SyncError("SAME_DATE_DIFFERENT_GENERATION_REQUIRES_REVIEW")
        if current_id == pending_id:
            state["currentGeneration"] = pending_id
            state["currentTradingDate"] = pending.get("tradingDate")
            state["pendingPromotion"] = None
            _write_json(receiver / "received_state.json", state)
            pending = None
        elif current_id is not None and pending.get("tradingDate", "") < state.get("currentTradingDate", ""):
            raise SyncError("RECEIVER_STATE_REQUIRES_REVIEW")

    if current_id is not None:
        record = _receiver_record(state, current_id)
        if record is None:
            raise SyncError("RECEIVER_STATE_REQUIRES_REVIEW")
        if not _receiver_generation_matches(receiver / "generations" / current_id, record):
            raise SyncError("RECEIVER_CURRENT_INTEGRITY_MISMATCH")
        current_date = _date(record.get("tradingDate"), "receiver_current_date")
        if state.get("currentGeneration") != current_id or state.get("currentTradingDate") != current_date:
            state["currentGeneration"] = current_id
            state["currentTradingDate"] = current_date
            _write_json(receiver / "received_state.json", state)
        return state, current_id

    if not accepted:
        return state, None

    latest = max(accepted, key=lambda row: _date(row.get("tradingDate"), "accepted_date"))
    latest_id = _generation_id(latest.get("generationId"))
    latest_dir = receiver / "generations" / latest_id
    if not latest_dir.is_dir():
        raise SyncError("RECEIVER_STATE_REQUIRES_REVIEW")
    _write_text_atomic(receiver / "CURRENT", latest_id + "\n")
    state["currentGeneration"] = latest_id
    state["currentTradingDate"] = _date(latest.get("tradingDate"), "accepted_date")
    _write_json(receiver / "received_state.json", state)
    return state, latest_id


def _mark_queue(queue_path: Path, queue: dict[str, Any], item: dict[str, Any], status: str, error: str | None = None) -> None:
    item["status"] = status
    item["lastError"] = error
    _write_json(queue_path, queue)


def deliver_pending_once(
    outbox_root: str | Path,
    receiver_root: str | Path,
    *,
    failure_injection: bool = False,
    failure_point: str | None = None,
) -> dict[str, Any]:
    """Deliver one queued package with forward-only atomic receiver promotion."""
    outbox = Path(outbox_root)
    receiver = Path(receiver_root)
    with _receiver_lock(receiver) as acquired:
        if not acquired:
            return {"status": "ALREADY_RUNNING"}
        queue_path = outbox / "queue.json"
        queue = _json(queue_path)
        item = next((item for item in queue.get("items", []) if item.get("status") in {"PENDING", "FAILED"}), None)
        if item is None:
            return {"status": "NO_CHANGES", "reason": "QUEUE_EMPTY"}
        item["attempts"] = int(item.get("attempts") or 0) + 1
        _write_json(queue_path, queue)
        package = Path(item["package"])
        point = failure_point or ("before_generation_promote" if failure_injection else None)
        try:
            verified = _validate_package(package)
            transfer = verified["transfer"]
            transfer_id = transfer["transferId"]
            state_path = receiver / "received_state.json"
            state = _load_receiver_state(state_path)
            state, current_id = _reconcile_receiver_state(receiver, state)
            accepted = state.setdefault("accepted", [])
            if any(row.get("transferId") == transfer_id for row in accepted):
                _mark_queue(queue_path, queue, item, "DELIVERED")
                return {"status": "NO_CHANGES", "transferId": transfer_id, "reason": "ALREADY_ACCEPTED"}

            if current_id is not None:
                current_record = _receiver_record(state, current_id)
                current_date = _date(current_record.get("tradingDate"), "receiver_current_date")
                incoming_date = _date(transfer["tradingDate"], "incoming_date")
                if incoming_date < current_date:
                    state.setdefault("rejected", []).append({
                        "transferId": transfer_id,
                        "generationId": transfer["generationId"],
                        "tradingDate": incoming_date,
                        "reason": "STALE_TRADING_DATE_CURRENT_UNCHANGED",
                    })
                    _write_json(state_path, state)
                    _mark_queue(queue_path, queue, item, "REJECTED_STALE", "STALE_TRADING_DATE_CURRENT_UNCHANGED")
                    return {"status": "REJECTED_STALE", "transferId": transfer_id, "currentGeneration": current_id}
                if incoming_date == current_date and transfer["generationId"] != current_id:
                    raise SyncError("SAME_DATE_DIFFERENT_GENERATION_REQUIRES_REVIEW")

            generation_id = _generation_id(transfer["generationId"])
            generation_dir = receiver / "generations" / generation_id
            staging = receiver / ".staging" / transfer_id
            if generation_dir.exists() and not _receiver_generation_matches(generation_dir, transfer):
                raise SyncError("RECEIVER_GENERATION_COLLISION")
            if not generation_dir.exists():
                if staging.exists() and not _receiver_generation_matches(staging, transfer):
                    shutil.rmtree(staging)
                staging.mkdir(parents=True, exist_ok=True)
                _copy_atomic(package / "monitoring_interchange.json", staging / "monitoring_interchange.json")
                _copy_atomic(package / "generation_manifest.json", staging / "generation_manifest.json")
                _copy_atomic(package / "transfer_manifest.json", staging / "transfer_manifest.json")
                if point == "generation_copy":
                    raise SyncError("INJECTED_GENERATION_COPY")
                _write_receiver_state(
                    state_path,
                    {**state, "pendingPromotion": _pending_record(transfer)},
                    point,
                    "pending_state_write",
                )
                if point == "before_generation_promote":
                    raise SyncError("INJECTED_BEFORE_GENERATION_PROMOTE")
                generation_dir.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, generation_dir)
                _fsync_dir(generation_dir.parent)
                if point == "generation_promote":
                    raise SyncError("INJECTED_GENERATION_PROMOTE")

            state["pendingPromotion"] = _pending_record(transfer)
            if not any(row.get("transferId") == transfer_id for row in accepted):
                accepted.append(_accepted_record(transfer))
            _write_receiver_state(state_path, state, point, "receiver_ledger_save")
            if point == "after_accepted_state":
                raise SyncError("INJECTED_AFTER_ACCEPTED_STATE")
            if current_id != generation_id:
                if point == "current_write":
                    raise SyncError("INJECTED_CURRENT_WRITE")
                _write_text_atomic(receiver / "CURRENT", generation_id + "\n")
                if point == "after_current":
                    raise SyncError("INJECTED_AFTER_CURRENT")
            state["currentGeneration"] = generation_id
            state["currentTradingDate"] = _date(transfer["tradingDate"], "incoming_date")
            state["pendingPromotion"] = None
            _write_json(state_path, state)
            _mark_queue(queue_path, queue, item, "DELIVERED")
            return {"status": "ACCEPTED", "transferId": transfer_id, "generationId": generation_id}
        except Exception as exc:
            _mark_queue(queue_path, queue, item, "FAILED", str(exc))
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
