"""Read-only, allowlisted source adapter for a private KRX sync repository.

The public monitoring repository never clones KIS_Trading and this module does
not perform network I/O.  An approved caller injects a GitHub Contents client
with contents:read permission scoped to the private repository.  Only the
designated KRX files are fetched, then the existing M10 receiver can consume
the materialized package.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import tempfile
from pathlib import PurePosixPath
from pathlib import Path
from typing import Any, Mapping, Protocol


GENERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DATE_RE = re.compile(r"^\d{8}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
PRIVATE_MANIFEST_SCHEMA = "krx_private_sync_manifest_v1"
PRIVATE_INPUT_SCHEMA = "krx_private_report_input_v1"
M10_MANIFEST_SCHEMA = "krx_m9_generation_manifest_v1"
M10_MONITORING_SCHEMA = "krx_monitoring_interchange_v1"


class PrivateSourceError(ValueError):
    """The private source is not authorized, complete, or internally consistent."""


class ContentsFetcher(Protocol):
    """Minimal authenticated read interface; no write or repository checkout."""

    def fetch(self, repository: str, ref: str, path: str) -> bytes:
        ...


def _json(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrivateSourceError(f"JSON_INVALID:{label}") from exc
    if not isinstance(value, dict):
        raise PrivateSourceError(f"JSON_OBJECT_REQUIRED:{label}")
    return value


def _date(value: Any, label: str) -> str:
    text = str(value or "").replace("-", "")
    if not DATE_RE.fullmatch(text):
        raise PrivateSourceError(f"INVALID_{label.upper()}")
    return text


def _generation(value: Any) -> str:
    if not isinstance(value, str) or not GENERATION_RE.fullmatch(value):
        raise PrivateSourceError("INVALID_GENERATION_ID")
    return value


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_path(root: str, relative: str) -> str:
    candidate = posixpath.normpath(posixpath.join(root, relative))
    root_norm = posixpath.normpath(root)
    if candidate != root_norm and not candidate.startswith(root_norm + "/"):
        raise PrivateSourceError("PATH_OUTSIDE_ALLOWLIST")
    if any(part in {"", ".", ".."} for part in PurePosixPath(relative).parts):
        raise PrivateSourceError("PATH_TRAVERSAL_REJECTED")
    return candidate


def _fetch(fetcher: ContentsFetcher, repository: str, ref: str, path: str) -> bytes:
    payload = fetcher.fetch(repository, ref, path)
    if not isinstance(payload, bytes):
        raise PrivateSourceError(f"FETCHER_BYTES_REQUIRED:{path}")
    return payload


def load_verified_private_generation(
    fetcher: ContentsFetcher,
    *,
    repository: str,
    ref: str = "main",
    root: str = "data/krx_sync",
) -> dict[str, Any]:
    """Fetch and verify only one private KRX generation.

    The caller supplies authentication.  This function intentionally has no
    token, GitHub API, write, checkout, or KIS-source access capability.
    """
    if any(part in {".", ".."} for part in PurePosixPath(root).parts):
        raise PrivateSourceError("PATH_TRAVERSAL_REJECTED")
    root = posixpath.normpath(root)
    current_path = _safe_path(root, "CURRENT")
    current = _json(_fetch(fetcher, repository, ref, current_path), current_path)
    generation = _generation(current.get("generationId"))
    trading_date = _date(current.get("tradingDate"), "current_trading_date")
    generation_root = _safe_path(root, f"generations/{generation}")
    paths = {
        "manifest": _safe_path(generation_root, "generation_manifest.json"),
        "privateManifest": _safe_path(generation_root, "private_sync_manifest.json"),
        "monitoring": _safe_path(generation_root, "monitoring_interchange.json"),
        "reportInput": _safe_path(generation_root, "report_input.json"),
        "transfer": _safe_path(generation_root, "transfer_manifest.json"),
    }
    payloads = {key: _fetch(fetcher, repository, ref, path) for key, path in paths.items()}
    private_manifest = _json(payloads["privateManifest"], paths["privateManifest"])
    transfer = _json(payloads["transfer"], paths["transfer"])
    manifest = _json(payloads["manifest"], paths["manifest"])
    monitoring = _json(payloads["monitoring"], paths["monitoring"])
    report_input = _json(payloads["reportInput"], paths["reportInput"])

    if private_manifest.get("schema") != PRIVATE_MANIFEST_SCHEMA:
        raise PrivateSourceError("PRIVATE_MANIFEST_SCHEMA_INVALID")
    if private_manifest.get("generationId") != generation or _date(private_manifest.get("tradingDate"), "private_manifest_date") != trading_date:
        raise PrivateSourceError("PRIVATE_MANIFEST_ID_DATE_MISMATCH")
    if manifest.get("schema") != M10_MANIFEST_SCHEMA or manifest.get("generation") != generation:
        raise PrivateSourceError("M10_MANIFEST_INVALID")
    if _date(manifest.get("date"), "manifest_date") != trading_date:
        raise PrivateSourceError("M10_MANIFEST_DATE_MISMATCH")
    if transfer.get("schemaVersion") != "krx_m10_private_transfer_v1" or transfer.get("publication") != "PRIVATE_KIS_TRADING_ONLY":
        raise PrivateSourceError("TRANSFER_CONTRACT_INVALID")
    if transfer.get("transferId") != f"{generation}:{trading_date}":
        raise PrivateSourceError("TRANSFER_ID_INVALID")
    if transfer.get("generationId") != generation or _date(transfer.get("tradingDate"), "transfer_date") != trading_date:
        raise PrivateSourceError("TRANSFER_ID_DATE_MISMATCH")
    if transfer.get("rawIncluded") or transfer.get("metricsIncluded") or transfer.get("aftermarketIncluded"):
        raise PrivateSourceError("FORBIDDEN_ARTIFACT_INCLUDED")
    if transfer.get("includedArtifacts") != ["generation_manifest.json", "monitoring_interchange.json", "private_sync_manifest.json", "report_input.json"]:
        raise PrivateSourceError("TRANSFER_ALLOWLIST_INVALID")

    report_meta = private_manifest.get("artifacts", {}).get("report_input", {})
    monitoring_meta = private_manifest.get("artifacts", {}).get("monitoring_interchange", {})
    if _digest(payloads["reportInput"]) != report_meta.get("sha256") or len(payloads["reportInput"]) != report_meta.get("bytes"):
        raise PrivateSourceError("REPORT_INPUT_INTEGRITY_MISMATCH")
    if _digest(payloads["monitoring"]) != monitoring_meta.get("sha256") or len(payloads["monitoring"]) != monitoring_meta.get("bytes"):
        raise PrivateSourceError("MONITORING_INTEGRITY_MISMATCH")
    if _digest(payloads["reportInput"]) != transfer.get("reportInputSha256") or len(payloads["reportInput"]) != transfer.get("reportInputBytes"):
        raise PrivateSourceError("TRANSFER_REPORT_INPUT_INTEGRITY_MISMATCH")
    if _digest(payloads["monitoring"]) != transfer.get("monitoringSha256") or len(payloads["monitoring"]) != transfer.get("monitoringBytes"):
        raise PrivateSourceError("TRANSFER_MONITORING_INTEGRITY_MISMATCH")
    if _digest(payloads["manifest"]) != transfer.get("manifestSha256") or len(payloads["manifest"]) != transfer.get("manifestBytes"):
        raise PrivateSourceError("TRANSFER_MANIFEST_INTEGRITY_MISMATCH")
    if _digest(payloads["privateManifest"]) != transfer.get("privateManifestSha256") or len(payloads["privateManifest"]) != transfer.get("privateManifestBytes"):
        raise PrivateSourceError("TRANSFER_PRIVATE_MANIFEST_INTEGRITY_MISMATCH")

    if monitoring.get("schema") != M10_MONITORING_SCHEMA or monitoring.get("source") != "KRX_OPEN_API":
        raise PrivateSourceError("MONITORING_CONTRACT_INVALID")
    if _date(monitoring.get("date"), "monitoring_date") != trading_date:
        raise PrivateSourceError("MONITORING_DATE_MISMATCH")
    entities = monitoring.get("entities")
    if not isinstance(entities, list) or monitoring.get("entity_count") != len(entities):
        raise PrivateSourceError("MONITORING_ENTITY_COUNT_INVALID")
    identities = [row.get("identity") for row in entities if isinstance(row, Mapping)]
    if len(identities) != len(entities) or len(identities) != len(set(identities)):
        raise PrivateSourceError("MONITORING_IDENTITY_INVALID")

    if report_input.get("schema") != PRIVATE_INPUT_SCHEMA or report_input.get("source") != "KRX_OPEN_API":
        raise PrivateSourceError("REPORT_INPUT_CONTRACT_INVALID")
    if report_input.get("generationId") != generation or _date(report_input.get("tradingDate"), "report_input_date") != trading_date:
        raise PrivateSourceError("REPORT_INPUT_ID_DATE_MISMATCH")
    if report_input.get("historicalIndicatorSource") != "KRX" or report_input.get("historicalFallbackAllowed") is not False:
        raise PrivateSourceError("KRX_HISTORICAL_POLICY_INVALID")
    if report_input.get("entityCount") != len(report_input.get("entities") or []):
        raise PrivateSourceError("REPORT_INPUT_ENTITY_COUNT_INVALID")
    if private_manifest.get("entityCount") != len(entities) or report_input.get("entityCount") != len(entities) or transfer.get("entityCount") != len(entities):
        raise PrivateSourceError("PRIVATE_ENTITY_COUNT_MISMATCH")
    report_identities = [row.get("identity") for row in report_input.get("entities") or [] if isinstance(row, Mapping)]
    if len(report_identities) != len(set(report_identities)):
        raise PrivateSourceError("REPORT_INPUT_DUPLICATE_IDENTITY")
    generation_meta = private_manifest.get("artifacts", {}).get("generation_manifest", {})
    if _digest(payloads["manifest"]) != generation_meta.get("sha256") or len(payloads["manifest"]) != generation_meta.get("bytes"):
        raise PrivateSourceError("GENERATION_MANIFEST_INTEGRITY_MISMATCH")
    return {
        "status": "VERIFIED",
        "repository": repository,
        "ref": ref,
        "root": root,
        "generationId": generation,
        "tradingDate": trading_date,
        "entityCount": len(entities),
        "paths": paths,
        "manifest": manifest,
        "privateManifest": private_manifest,
        "transfer": transfer,
        "monitoring": monitoring,
        "reportInput": report_input,
        "payloads": payloads,
    }


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def materialize_private_generation_to_outbox(verified: Mapping[str, Any], outbox_root: str | Path) -> dict[str, Any]:
    """Create an existing-receiver-compatible local package; never sends it."""
    if verified.get("status") != "VERIFIED":
        raise PrivateSourceError("SOURCE_NOT_VERIFIED")
    payloads = verified.get("payloads")
    if not isinstance(payloads, Mapping):
        raise PrivateSourceError("SOURCE_PAYLOADS_MISSING")
    generation = _generation(verified.get("generationId"))
    trading_date = _date(verified.get("tradingDate"), "verified_trading_date")
    outbox = Path(outbox_root)
    package = outbox / "packages" / generation
    package.mkdir(parents=True, exist_ok=True)
    for key, name in {
        "manifest": "generation_manifest.json",
        "monitoring": "monitoring_interchange.json",
        "privateManifest": "private_sync_manifest.json",
        "reportInput": "report_input.json",
        "transfer": "transfer_manifest.json",
    }.items():
        _write_bytes_atomic(package / name, payloads[key])
    queue_path = outbox / "queue.json"
    if queue_path.exists():
        queue = _json(queue_path.read_bytes(), "outbox_queue")
    else:
        queue = {"schemaVersion": "krx_m10_outbox_v1", "items": []}
    transfer_id = f"{generation}:{trading_date}"
    if not any(row.get("transferId") == transfer_id for row in queue.get("items", [])):
        queue.setdefault("items", []).append({"transferId": transfer_id, "package": str(package), "status": "PENDING", "attempts": 0})
    _write_bytes_atomic(queue_path, (json.dumps(queue, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())
    return {"status": "STAGED_NOT_SENT", "package": str(package), "queue": str(queue_path), "transferId": transfer_id}
