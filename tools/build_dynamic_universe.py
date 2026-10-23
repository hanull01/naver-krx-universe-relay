#!/usr/bin/env python3
"""Build GitHub-publishable dynamic management artifacts from existing data."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dynamic_universe_manager as manager
import theme_research

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
    parser.add_argument("--theme-signals", default=str(ROOT / "data/monitoring/latest-theme-signals.json"))
    parser.add_argument("--theme-ranking", default=str(ROOT / "data/research/theme-ranking/latest.json"))
    parser.add_argument("--theme-membership", default=str(ROOT / "data/research/theme-membership/latest.json"))
    parser.add_argument("--discovery", default=str(ROOT / "data/trading-discovery/latest.json"))
    parser.add_argument("--policy", default=str(manager.DEFAULT_POLICY))
    parser.add_argument("--output", default=str(ROOT / "data/dynamic-universe/latest.json"))
    parser.add_argument("--history", default=str(ROOT / "data/dynamic-universe/history.json"))
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    industries = manager.load_json(args.industries, {}) or {}
    quality = industries.get("quality") or {}
    industry_rows, industry_quality_state = usable_industry_rows(industries)
    trading_date = industries.get("tradingDate") or industries.get("tradeDate") or industries.get("marketDate")
    context = {"generatedAt": industries.get("generatedAt"),
               "tradingDate": trading_date,
               "industries": {"status": "AVAILABLE" if industry_rows else "UNAVAILABLE",
                              "industries": industry_rows, "qualityStatus": industry_quality_state,
                              "quality": quality},
               "themes": {"status": "UNAVAILABLE", "reason": "VERIFIED_THEME_MEMBERSHIP_NOT_PUBLISHED"}}
    previous = manager.load_json(args.output, {}) or {}
    discovery = manager.load_json(args.discovery, {}) or {}
    universe_payload = manager.load_json(args.universe, {}) or {}
    result = manager.build_management_result(context, universe_payload,
                                              discovery, previous,
                                              manager.load_json(args.policy, {}) or {})
    signals = manager.load_json(args.signals, {}) or {}
    quotes = {str(row.get("code")): row for row in signals.get("stocks", []) if row.get("code")}
    theme_signals = manager.load_json(args.theme_signals, {}) or {}
    theme_quotes = {}
    for theme_row in theme_signals.get("themes", []) if isinstance(theme_signals, dict) else []:
        for row in theme_row.get("stocks", []):
            if row.get("code"):
                theme_quotes[str(row["code"])] = row
    for key in ("leaders", "initialObservationCandidates"):
        for row in result.get(key, []):
            quote = quotes.get(str(row.get("code")))
            if quote:
                for field in ("currentPrice", "changeRate", "sourceTime"):
                    if quote.get(field) is not None:
                        row[field] = quote[field]
    ranking = manager.load_json(args.theme_ranking, {}) or {}
    membership = manager.load_json(args.theme_membership, {}) or {}
    daily = ((ranking.get("workContract") or {}).get("periods") or {}).get("daily") or []
    if ranking.get("status") == "OK" and membership.get("status") in {"OK", "PARTIAL"} and daily:
        allowed_codes = {str(row.get("itemCode") or row.get("code"))
                         for row in universe_payload.get("stocks", [])
                         if row.get("itemCode") or row.get("code")}
        analyzed = theme_research.analyze_dynamic_themes(
            membership, theme_quotes, {}, allowed_codes or None)
        analyzed_by_id = {str(row["id"]): row for row in analyzed.get("groups", [])}
        ranked = []
        for theme in daily:
            group = analyzed_by_id.get(str(theme.get("id")))
            if not group or group.get("status") != "OK" or not group.get("knownQuoteCount"):
                continue
            member_rows = [theme_quotes.get(member.get("code")) for theme_row in membership.get("themes", [])
                           if str(theme_row.get("id")) == str(theme.get("id"))
                           for member in theme_row.get("members", [])]
            known = [row for row in member_rows if isinstance(row, dict)]
            above_ma20 = [row for row in known if isinstance(row.get("above_ma20"), bool)]
            metrics = {
                "relativeStrength": group.get("averageChangeRate"),
                "advancerPct": (group.get("risingCount", 0) / group.get("knownQuoteCount", 1) * 100),
                "turnoverConcentrationPct": 0,
                "pctAboveMA20": (sum(row["above_ma20"] for row in above_ma20) / len(above_ma20) * 100
                                  if above_ma20 else None),
                "leaderCount": len(group.get("leaders", [])),
            }
            _, score, checks = manager._sector_decision({"metrics": metrics, "leaders": group.get("leaders", [])},
                                                        (manager.load_json(args.policy, {}) or {}).get("sector", {}))
            ranked.append({**theme, "verifiedMembership": True,
                           "membershipStatus": group.get("status"),
                           "systemLeaders": group.get("leaders", []),
                           "score": score, "checks": checks, "metrics": metrics,
                           "memberCount": group.get("memberCount"),
                           "knownQuoteCount": group.get("knownQuoteCount"),
                           "risingCount": group.get("risingCount"),
                           "averageChangeRate": group.get("averageChangeRate"),
                           "sourceTime": group.get("sourceTime")})
        if ranked:
            context["themes"] = {"status": "AVAILABLE", "tradingDate": trading_date, "periods": {"daily": {"themes": ranked}},
                                   "qualityStatus": "FULL", "quality": {"sourceStatus": "OK"}}
        else:
            context["themes"] = {"status": "UNAVAILABLE", "tradingDate": trading_date, "reason": "NO_VERIFIED_THEME_QUOTES"}
        result = manager.build_management_result(context, universe_payload,
                                                 discovery, previous, manager.load_json(args.policy, {}) or {})
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
