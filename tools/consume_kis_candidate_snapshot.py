#!/usr/bin/env python3
"""Consume one local KIS candidate snapshot for the monitoring report.

The KIS file is read only.  This command writes only the relay-owned dated
candidate and quote artifacts after all identity/date/hash checks pass.  Live
quote access is an explicit opt-in so an accidental local invocation cannot
call an external endpoint.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import trading_discovery as td


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--monitoring-date", required=True)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--snapshot-root", type=Path,
                        default=td.KIS_OFFICIAL_SNAPSHOT_ROOT)
    parser.add_argument("--quotes", type=Path, default=ROOT / "data/quotes.json")
    parser.add_argument("--calendar", type=Path,
                        default=ROOT / "data/krx_trading_calendar.json")
    parser.add_argument("--output-dir", type=Path, default=td.DISCOVERY_DIR)
    parser.add_argument("--quote-output-dir", type=Path,
                        default=td.DISCOVERY_QUOTES_DIR)
    parser.add_argument(
        "--allow-network", action="store_true",
        help="Explicitly allow the existing NAVER quote fetcher; never needed by tests.",
    )
    args = parser.parse_args()
    if not args.allow_network:
        parser.error("--allow-network is required for live quote collection")
    snapshot = args.snapshot or td.official_snapshot_path(
        args.monitoring_date, root=args.snapshot_root
    )
    snapshot_payload = td.load_official_snapshot(
        snapshot,
        expected_monitoring_date=args.monitoring_date,
        calendar_path=args.calendar,
    )
    # An empty official snapshot is a valid NO_RESULTS handoff and has no
    # quote input.  Never read a prior quotes.json in that case.
    quotes = None if not snapshot_payload.get("symbols") else td.load_json(args.quotes)
    result = td.consume_official_snapshot(
        snapshot, quotes, args.calendar,
        discovery_output_dir=args.output_dir,
        quote_output_dir=args.quote_output_dir,
        expected_monitoring_date=args.monitoring_date,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
