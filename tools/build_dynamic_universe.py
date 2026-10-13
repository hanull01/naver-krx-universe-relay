#!/usr/bin/env python3
"""Build GitHub-publishable dynamic management artifacts from existing data."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dynamic_universe_manager as manager


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--industries", default=str(ROOT / "data/monitoring/latest-industries.json"))
    parser.add_argument("--universe", default=str(ROOT / "config/universe.json"))
    parser.add_argument("--discovery", default=str(ROOT / "data/trading-discovery/latest.json"))
    parser.add_argument("--policy", default=str(manager.DEFAULT_POLICY))
    parser.add_argument("--output", default=str(ROOT / "data/dynamic-universe/latest.json"))
    parser.add_argument("--history", default=str(ROOT / "data/dynamic-universe/history.json"))
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    industries = manager.load_json(args.industries, {}) or {}
    quality = industries.get("quality") or {}
    usable = quality.get("industryEvidenceUsable") == "FULL"
    context = {"generatedAt": industries.get("generatedAt"),
               "industries": {"status": "AVAILABLE" if usable else "UNAVAILABLE",
                              "industries": industries.get("industries", []) if usable else []},
               "themes": {"status": "UNAVAILABLE", "reason": "VERIFIED_THEME_MEMBERSHIP_NOT_PUBLISHED"}}
    previous = manager.load_json(args.output, {}) or {}
    result = manager.build_management_result(context, manager.load_json(args.universe, {}) or {},
                                              manager.load_json(args.discovery, {}) or {}, previous,
                                              manager.load_json(args.policy, {}) or {})
    if not args.no_write:
        manager.publish_result(result, args.output, args.output, args.history)
    print(json.dumps({"status": "SUCCESS", "networkCalls": 0, "sectorCount": len(result["sectors"]),
                      "themePending": bool(result["pending"]), "leaderCount": len(result["leaders"]),
                      "collectionCount": len(result["collectionCodes"]), "written": not args.no_write}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
