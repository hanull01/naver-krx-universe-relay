#!/usr/bin/env python3
"""Publish the canonical report contract beside monitoring snapshots."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from daily_report import build_report, load_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/monitoring/latest-report-contract.json")
    args = parser.parse_args()
    quality = load_json(Path("data/monitoring/latest-quality.json"))
    raw_date = str(quality.get("tradingDate") or "")
    if len(raw_date) != 8 or not raw_date.isdigit():
        raise SystemExit("TRADING_DATE_UNAVAILABLE")
    as_of = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
    report = build_report(as_of=as_of)
    contract = report.get("reportContract")
    if not isinstance(contract, dict):
        raise SystemExit("REPORT_CONTRACT_UNAVAILABLE")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "status": contract.get("status"), "asOf": contract.get("asOf"), "observedAt": contract.get("observedAt")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
