#!/usr/bin/env python3
"""Publish the canonical report contract beside monitoring snapshots."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from daily_report import build_report, load_json
from tools.report_contract import MAX_WORK_CONTRACT_BYTES, validate_contract_payload


FINAL_EVIDENCE_REASONS = {
    "VERIFIED_CANONICAL_SESSION",
    "VERIFIED_MONITORING_DAILY_FINAL_SESSION",
    "SOURCE_OBSERVED_MONITORING_DAILY_FINAL_DATE",
    "VERIFIED_NAVER_LOCAL_DATE_DIAGNOSTIC",
}
INTRADAY_EVIDENCE_REASON = "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT"
SOURCE_OBSERVED_EVIDENCE_REASON = "SOURCE_OBSERVED_MONITORING_DAILY_FINAL_DATE"


def _serialize_contract(contract: dict) -> bytes:
    payload = (json.dumps(contract, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if not payload:
        raise SystemExit("REPORT_CONTRACT_EMPTY")
    if len(payload) > MAX_WORK_CONTRACT_BYTES:
        raise SystemExit("REPORT_CONTRACT_TOO_LARGE")
    try:
        json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SystemExit("REPORT_CONTRACT_INVALID_JSON") from exc
    return payload


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as temp:
            temp.write(payload)
            temp.flush()
            os.fsync(temp.fileno())
            temp_name = temp.name
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def _trading_date_evidence(quality: dict, summary: dict) -> tuple[str, dict]:
    """Resolve same-run date evidence without using execution time or old data."""
    quality_evidence = quality.get("tradingDateEvidence")
    full_market = summary.get("fullMarket") or {}
    summary_evidence = full_market.get("tradingDateEvidence")
    if not isinstance(quality_evidence, dict):
        quality_evidence = {}
    if not isinstance(summary_evidence, dict):
        summary_evidence = {}

    candidates = [e for e in (quality_evidence, summary_evidence) if e.get("tradingDate")]
    if not candidates:
        raise SystemExit("TRADING_DATE_UNAVAILABLE")

    dates = {str(e.get("tradingDate")) for e in candidates}
    if len(dates) != 1 or any(len(d) != 8 or not d.isdigit() for d in dates):
        raise SystemExit("TRADING_DATE_EVIDENCE_MISMATCH")
    trading_date = next(iter(dates))

    reasons = {e.get("reason") for e in candidates}
    accepted_reasons = FINAL_EVIDENCE_REASONS | {INTRADAY_EVIDENCE_REASON}
    valid = reasons & accepted_reasons
    statuses = {e.get("status") for e in candidates}
    if reasons == {SOURCE_OBSERVED_EVIDENCE_REASON}:
        if statuses != {"SOURCE_OBSERVED"} or any(
                e.get("evidenceClass") != "SOURCE_OBSERVED" for e in candidates
        ):
            raise SystemExit("TRADING_DATE_UNAVAILABLE")
    elif (not valid or not reasons.issubset(accepted_reasons)
          or any(e.get("status") != "VERIFIED" for e in candidates)):
        raise SystemExit("TRADING_DATE_UNAVAILABLE")

    # A current snapshot may support an intraday contract, but never a
    # finalized daily-bar claim. Preserve that distinction in the contract.
    if INTRADAY_EVIDENCE_REASON in valid:
        if reasons != {INTRADAY_EVIDENCE_REASON}:
            raise SystemExit("TRADING_DATE_UNAVAILABLE")
        intraday_candidates = [e for e in candidates if e.get("reason") == INTRADAY_EVIDENCE_REASON]
        if any(
            e.get("evidenceClass") not in {"INTRADAY_SESSION", "POST_MARKET_SNAPSHOT"}
            or e.get("finalConfirmed") is not False
            for e in intraday_candidates
        ):
            raise SystemExit("TRADING_DATE_UNAVAILABLE")

    generated_at = quality.get("generatedAt")
    summary_generated_at = summary.get("generatedAt")
    if not generated_at or not summary_generated_at or generated_at != summary_generated_at:
        raise SystemExit("TRADING_DATE_EVIDENCE_STALE")
    observed_at = {e.get("collectionGeneratedAt") for e in candidates}
    if INTRADAY_EVIDENCE_REASON in valid and (
        observed_at != {generated_at}
        or any(not e.get("collectionGeneratedAt") for e in candidates)
    ):
        raise SystemExit("TRADING_DATE_EVIDENCE_STALE")
    if INTRADAY_EVIDENCE_REASON not in valid and any(
        value is not None and value != generated_at for value in observed_at
    ):
        raise SystemExit("TRADING_DATE_EVIDENCE_STALE")
    if any(str(e.get("tradingDate")) != trading_date for e in candidates):
        raise SystemExit("TRADING_DATE_EVIDENCE_MISMATCH")
    return trading_date, next(iter(candidates))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/monitoring/latest-report-contract.json")
    args = parser.parse_args()
    quality = load_json(Path("data/monitoring/latest-quality.json"))
    summary = load_json(Path("data/monitoring/latest-summary.json"))
    raw_date, evidence = _trading_date_evidence(quality, summary)
    as_of = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
    report = build_report(as_of=as_of)
    contract = report.get("reportContract")
    if not isinstance(contract, dict):
        raise SystemExit("REPORT_CONTRACT_UNAVAILABLE")
    contract["tradingDateEvidence"] = evidence
    output = Path(args.output)
    payload = _serialize_contract(contract)
    validation = validate_contract_payload(
        payload,
        expected_as_of=contract.get("asOf"),
        expected_observed_at=contract.get("observedAt"),
    )
    if validation.get("status") != "VALID":
        raise SystemExit(f"REPORT_CONTRACT_{validation.get('status', 'INVALID')}")
    _write_atomic(output, payload)
    print(json.dumps({
        "output": str(output),
        "status": contract.get("status"),
        "asOf": contract.get("asOf"),
        "observedAt": contract.get("observedAt"),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
