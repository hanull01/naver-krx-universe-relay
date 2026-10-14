#!/usr/bin/env python3
"""Build GitHub-publishable dynamic management artifacts from existing data."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dynamic_universe_manager as manager

REQUIRED_INDUSTRY_FIELDS = (
    "advancePct", "relativeStrengthVsMarket", "pctAboveMA20",
    "pctVolumeAbove20DayAverage",
)


def usable_industry_rows(payload):
    """Select only rows with complete calculated market evidence."""
    quality = payload.get("quality") or {}
    selected = []
    for row in payload.get("industries") or []:
        if not isinstance(row, dict):
            continue
        member_count = row.get("memberCount")
        if (not isinstance(member_count, int) or member_count <= 0
                or row.get("closeValidMemberCount") != member_count
                or not isinstance(row.get("featureValidMemberCount"), int)
                or row.get("featureValidMemberCount") <= 0):
            continue
        if any(not isinstance(row.get(field), (int, float)) for field in REQUIRED_INDUSTRY_FIELDS):
            continue
        if not isinstance(row.get("leaders"), list):
            continue
        selected.append(row)
    if quality.get("sourceStatus") == "OK" and quality.get("industryEvidenceUsable") == "FULL":
        status = "FULL"
    elif selected and quality.get("sourceStatus") in {"OK", "PARTIAL"}:
        status = "PARTIAL_WITH_COVERAGE"
    else:
        status = "UNUSABLE"
    return selected, status


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--industries", default=str(ROOT / "data/monitoring/latest-industries.json"))
    parser.add_argument("--universe", default=str(ROOT / "config/universe.json"))
    parser.add_argument("--signals", default=str(ROOT / "data/monitoring/latest-stock-signals.json"))
    parser.add_argument("--discovery", default=str(ROOT / "data/trading-discovery/latest.json"))
    parser.add_argument("--policy", default=str(manager.DEFAULT_POLICY))
    parser.add_argument("--output", default=str(ROOT / "data/dynamic-universe/latest.json"))
    parser.add_argument("--history", default=str(ROOT / "data/dynamic-universe/history.json"))
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    industries = manager.load_json(args.industries, {}) or {}
    quality = industries.get("quality") or {}
    industry_rows, industry_quality_state = usable_industry_rows(industries)
    context = {"generatedAt": industries.get("generatedAt"),
               "industries": {"status": "AVAILABLE" if industry_rows else "UNAVAILABLE",
                              "industries": industry_rows, "qualityStatus": industry_quality_state,
                              "quality": quality},
               "themes": {"status": "UNAVAILABLE", "reason": "VERIFIED_THEME_MEMBERSHIP_NOT_PUBLISHED"}}
    previous = manager.load_json(args.output, {}) or {}
    discovery = manager.load_json(args.discovery, {}) or {}
    result = manager.build_management_result(context, manager.load_json(args.universe, {}) or {},
                                              discovery, previous,
                                              manager.load_json(args.policy, {}) or {})
    signals = manager.load_json(args.signals, {}) or {}
    quotes = {str(row.get("code")): row for row in signals.get("stocks", []) if row.get("code")}
    for key in ("leaders", "initialObservationCandidates"):
        for row in result.get(key, []):
            quote = quotes.get(str(row.get("code")))
            if quote:
                for field in ("currentPrice", "changeRate", "sourceTime"):
                    if quote.get(field) is not None:
                        row[field] = quote[field]
    if not args.no_write:
        manager.publish_result(result, args.output, args.output, args.history)
    print(json.dumps({"status": "SUCCESS", "networkCalls": 0, "industryQuality": industry_quality_state,
                      "sectorCount": len(result["sectors"]),
                      "themePending": bool(result["pending"]), "leaderCount": len(result["leaders"]),
                      "collectionCount": len(result["collectionCodes"]), "written": not args.no_write}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
