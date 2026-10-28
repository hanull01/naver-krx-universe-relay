#!/usr/bin/env python3
"""Publish the canonical report contract beside monitoring snapshots."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from daily_report import build_report, load_json


FINAL_EVIDENCE_REASONS = {
    "VERIFIED_CANONICAL_SESSION",
    "VERIFIED_MONITORING_DAILY_FINAL_SESSION",
    "SOURCE_OBSERVED_MONITORING_DAILY_FINAL_DATE",
    "VERIFIED_NAVER_LOCAL_DATE_DIAGNOSTIC",
}
INTRADAY_EVIDENCE_REASON = "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT"


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
    valid = reasons & (FINAL_EVIDENCE_REASONS | {INTRADAY_EVIDENCE_REASON})
    if not valid or any(e.get("status") != "VERIFIED" for e in candidates):
        raise SystemExit("TRADING_DATE_UNAVAILABLE")

    # A current snapshot may support an intraday contract, but never a
    # finalized daily-bar claim. Preserve that distinction in the contract.
    if INTRADAY_EVIDENCE_REASON in valid:
        intraday = next(e for e in candidates if e.get("reason") == INTRADAY_EVIDENCE_REASON)
        if intraday.get("evidenceClass") not in {"INTRADAY_SESSION", "POST_MARKET_SNAPSHOT"} or intraday.get("finalConfirmed") is not False:
            raise SystemExit("TRADING_DATE_UNAVAILABLE")

    generated_at = quality.get("generatedAt")
    summary_generated_at = summary.get("generatedAt")
    if not generated_at or not summary_generated_at or generated_at != summary_generated_at:
        raise SystemExit("TRADING_DATE_EVIDENCE_STALE")
    observed_at = {e.get("collectionGeneratedAt") for e in candidates if e.get("collectionGeneratedAt")}
    if observed_at and observed_at != {generated_at}:
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
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "status": contract.get("status"), "asOf": contract.get("asOf"), "observedAt": contract.get("observedAt")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
