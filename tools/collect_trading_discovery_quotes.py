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
    dated = output_dir / f"{monitoring_date}.json"
    if dated.exists():
        try:
            existing = td.load_json(dated)
        except (OSError, ValueError):
            existing = None
        existing_hash = existing.get("sourceHash", existing.get("candidateSourceSha256")) if isinstance(existing, dict) else None
        if (isinstance(existing, dict)
                and existing_hash == discovery.get("sourceSha256")
                and existing.get("marketSourceTime") == market_source_time):
            return {"result": "NOOP", "publicationResult": "IDEMPOTENT",
                    "networkCalls": 0, "status": existing.get("status"),
                    "dated": str(dated), "latest": str(output_dir / "latest.json")}
    if fetcher is None or normalizer is None:
        # The workflow's normal path uses the existing NAVER quote client.
        # Tests and dry-runs inject both functions, so they never make a live
        # request accidentally.
        import relay
        fetcher = fetcher or (lambda codes: relay.fetch(relay.QUOTE_URL + ",".join(codes)))
        normalizer = normalizer or relay.normalize_quote
    signals = td.load_json(signals_path) if Path(signals_path).exists() else {}
    payload = td.build_supplemental_quotes(
        discovery, quotes, signals, fetcher=fetcher, normalizer=normalizer, clock=clock)
    network_calls = payload.pop("networkCalls")
    publication = td.publish_supplemental_quote_artifact(
        payload, discovery, output_dir
    )
    legacy_result = "NOOP" if publication.get("result") in {"IDEMPOTENT", "PROTECTED_IDENTITY_CONFLICT"} else "PUBLISHED"
    return {**publication, **payload, "result": legacy_result,
            "publicationResult": publication.get("result"),
            "networkCalls": network_calls}


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
