#!/usr/bin/env python3
"""Publish bounded supplemental quotes for finalized discovery candidates."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import trading_discovery as td


def collect(discovery_path, quotes_path, signals_path, output_dir, *,
            fetcher=None, normalizer=None, clock=None):
    discovery_path, quotes_path = Path(discovery_path), Path(quotes_path)
    output_dir = Path(output_dir)
    quotes = td.load_json(quotes_path)
    market_source_time = quotes.get("sourceTime")
    try:
        monitoring_date = td.load_json(discovery_path).get("monitoringDate")
    except (OSError, ValueError, AttributeError):
        raise td.DiscoveryError("DISCOVERY_NOT_AVAILABLE")
    discovery = td.discovery_status(monitoring_date, discovery_path)
    signals = td.load_json(signals_path) if Path(signals_path).exists() else {}
    latest = output_dir / "latest.json"
    if latest.exists():
        try:
            existing = td.validate_supplemental_quotes(
                td.load_json(latest), discovery, market_source_time)
            return {"result": "NOOP", "networkCalls": 0, **existing}
        except (OSError, ValueError, td.DiscoveryError):
            pass
    payload = td.build_supplemental_quotes(
        discovery, quotes, signals, fetcher=fetcher, normalizer=normalizer, clock=clock)
    network_calls = payload.pop("networkCalls")
    if payload["status"] == "UNAVAILABLE":
        raise td.DiscoveryError("SUPPLEMENTAL_COLLECTION_UNAVAILABLE")
    dated = output_dir / f"{payload['monitoringDate']}.json"
    td.atomic_json(dated, payload)
    td.atomic_json(latest, payload)
    return {"result": "PUBLISHED", "networkCalls": network_calls,
            "dated": str(dated), "latest": str(latest), **payload}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--discovery", default=str(td.DISCOVERY_DIR / "latest.json"))
    parser.add_argument("--quotes", default=str(ROOT / "data/quotes.json"))
    parser.add_argument("--signals", default=str(ROOT / "data/monitoring/latest-stock-signals.json"))
    parser.add_argument("--output-dir", default=str(td.DISCOVERY_QUOTES_DIR))
    args = parser.parse_args()
    result = collect(args.discovery, args.quotes, args.signals, args.output_dir)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
