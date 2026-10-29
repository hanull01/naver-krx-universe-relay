"""Evidence for the files consumed by the V2 shadow calculation.

The publisher commit is deliberately not inferred from the working tree.  A
pre-publish calculation can prove which bytes it read, but it cannot prove a
future Git commit.  In that case the result is PARTIAL/UNVERIFIED rather than
silently treating the report publication SHA as a source SHA.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


SOURCE_FILES = (
    ("industries", "data/monitoring/latest-industries.json"),
    ("themes", "data/monitoring/latest-theme-signals.json"),
    ("v1Universe", "data/dynamic-universe/latest.json"),
)
OUTPUT_FILES = (
    ("v2Result", "data/dynamic-universe/v2-shadow-latest.json"),
    ("v2History", "data/dynamic-universe/v2-shadow-history.json"),
)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _compact_date(value: Any) -> str | None:
    text = str(value or "").replace("-", "")
    return text if len(text) == 8 and text.isdigit() else None


def _payload_date(payload: Mapping[str, Any]) -> str | None:
    for key in ("tradingDate", "sourceTradingDate", "asOfDate"):
        if (value := _compact_date(payload.get(key))):
            return value
    return None


def _read_file(root: Path, kind: str, relative: str, *, expected_generation: str | None,
               expected_date: str | None, check_generation: bool) -> tuple[dict[str, Any], bool, bool]:
    path = root / relative
    record: dict[str, Any] = {"kind": kind, "path": relative}
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        record.update({"status": "UNVERIFIED", "reason": "FILE_UNAVAILABLE_OR_INVALID"})
        return record, False, False
    if not isinstance(payload, Mapping):
        record.update({"status": "UNVERIFIED", "reason": "JSON_OBJECT_REQUIRED"})
        return record, False, False

    record.update({
        "status": "OBSERVED_HASH_ONLY",
        "sha256": sha256_bytes(raw),
        "bytes": len(raw),
        "generatedAt": payload.get("generatedAt"),
        "tradingDate": _payload_date(payload),
    })
    valid = True
    mismatch = False
    if check_generation and expected_generation and payload.get("generatedAt") != expected_generation:
        record["generationStatus"] = "MISMATCH"
        record["expectedGeneratedAt"] = expected_generation
        valid = False
        mismatch = True
    else:
        record["generationStatus"] = "ALIGNED" if check_generation else "NOT_REQUIRED"
    payload_date = _payload_date(payload)
    if expected_date and payload_date and payload_date != expected_date:
        record["dateStatus"] = "MISMATCH"
        record["expectedTradingDate"] = expected_date
        valid = False
        mismatch = True
    else:
        record["dateStatus"] = "ALIGNED" if payload_date else "UNAVAILABLE"
    return record, valid, mismatch


def build_source_provenance(root: Path, *, result: Mapping[str, Any] | None = None,
                            include_outputs: bool = False) -> dict[str, Any]:
    """Hash the exact JSON files used by V2 and classify their evidence.

    ``PARTIAL`` means all required bytes were observed but no verified source
    Git commit is available.  ``MISMATCH`` means observed bytes disagree with
    the calculation generation/date.  ``UNVERIFIED`` means required bytes
    could not be read or parsed.
    """
    root = Path(root)
    result = result if isinstance(result, Mapping) else {}
    input_evidence = result.get("inputEvidence") if isinstance(result.get("inputEvidence"), Mapping) else {}
    expected_generation = result.get("generatedAt") or input_evidence.get("generatedAt")
    expected_date = _compact_date(result.get("tradingDate") or input_evidence.get("tradingDate"))
    files: list[dict[str, Any]] = []
    valid_count = 0
    mismatch = False
    for kind, relative in SOURCE_FILES:
        record, valid, record_mismatch = _read_file(
            root, kind, relative,
            expected_generation=expected_generation,
            expected_date=expected_date,
            check_generation=kind in {"industries", "themes"},
        )
        files.append(record)
        valid_count += int(valid)
        mismatch = mismatch or record_mismatch

    if include_outputs:
        for kind, relative in OUTPUT_FILES:
            record, valid, record_mismatch = _read_file(
                root, kind, relative,
                expected_generation=expected_generation,
                expected_date=expected_date,
                check_generation=False,
            )
            files.append(record)
            mismatch = mismatch or record_mismatch

    source_files = [item for item in files if item.get("kind") in {"industries", "themes", "v1Universe"}
                    and item.get("sha256")]
    snapshot = [{"path": item["path"], "sha256": item["sha256"]} for item in source_files]
    input_snapshot_sha = sha256_bytes(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ) if snapshot else None
    required_count = len(SOURCE_FILES)
    if mismatch:
        status = "MISMATCH"
    elif valid_count == required_count:
        status = "PARTIAL"  # bytes verified; source Git commit is unavailable pre-publish
    elif valid_count:
        status = "PARTIAL"
    else:
        status = "UNVERIFIED"
    result_record = next((item for item in files if item.get("kind") == "v2Result"), None)
    history_record = next((item for item in files if item.get("kind") == "v2History"), None)
    return {
        "schemaVersion": "v2-source-provenance-v1",
        "status": status,
        "sourceCommit": None,
        "sourceCommitStatus": "UNAVAILABLE",
        "reason": "SOURCE_COMMIT_UNAVAILABLE_PRE_PUBLISH",
        "tradingDate": expected_date,
        "calculationGeneration": expected_generation,
        "inputSnapshotSha256": input_snapshot_sha,
        "resultSha256": (result_record or {}).get("sha256"),
        "historySha256": (history_record or {}).get("sha256"),
        "files": files,
    }
