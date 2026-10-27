#!/usr/bin/env python3
"""Build GitHub-publishable dynamic management artifacts from existing data."""
import argparse
import json
import sys
from datetime import datetime
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


def verified_trading_date(payload):
    """Return only an explicit, calendar-valid trading-date field."""
    for key in ("tradingDate", "tradeDate", "marketDate"):
        value = (payload or {}).get(key)
        if value is None:
            continue
        normalized = str(value).replace("-", "").replace("/", "")
        if len(normalized) != 8 or not normalized.isdigit():
            continue
        try:
            datetime.strptime(normalized, "%Y%m%d")
        except ValueError:
            continue
        return normalized
    return None


def input_evidence(industry_payload, quality_payload):
    """Keep V1's input date and source quality separate from result time."""
    industry_payload = industry_payload or {}
    quality_payload = quality_payload or {}
    source_commit = (quality_payload.get("sourceCommit")
                     or industry_payload.get("sourceCommit"))
    return {
        "tradingDate": verified_trading_date(industry_payload),
        "sourceQualityStatus": (quality_payload.get("sourceQualityStatus")
                                 or industry_payload.get("sourceQualityStatus")
                                 or "UNAVAILABLE"),
        "sourceCommit": source_commit,
        "sourceCommitStatus": "VERIFIED" if source_commit else "UNAVAILABLE",
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--industries", default=str(ROOT / "data/monitoring/latest-industries.json"))
    parser.add_argument("--universe", default=str(ROOT / "config/universe.json"))
    parser.add_argument("--signals", default=str(ROOT / "data/monitoring/latest-stock-signals.json"))
    parser.add_argument("--theme-signals", default=str(ROOT / "data/monitoring/latest-theme-signals.json"))
    parser.add_argument("--theme-ranking", default=str(ROOT / "data/research/theme-ranking/latest.json"))
    parser.add_argument("--theme-membership", default=str(ROOT / "data/research/theme-membership/latest.json"))
    parser.add_argument("--quality")
    parser.add_argument("--discovery", default=str(ROOT / "data/trading-discovery/latest.json"))
    parser.add_argument("--asset-registry", default=str(ROOT / "data/market-universe/all-assets.json"))
    parser.add_argument("--policy", default=str(manager.DEFAULT_POLICY))
    parser.add_argument("--output", default=str(ROOT / "data/dynamic-universe/latest.json"))
    parser.add_argument("--history", default=str(ROOT / "data/dynamic-universe/history.json"))
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    industries = manager.load_json(args.industries, {}) or {}
    quality_payload = manager.load_json(
        args.quality or str(Path(args.industries).with_name("latest-quality.json")), {}) or {}
    quality = industries.get("quality") or {}
    industry_rows, industry_quality_state = usable_industry_rows(industries)
    trading_date = verified_trading_date(industries)
    context = {"generatedAt": industries.get("generatedAt"),
               "tradingDate": trading_date,
               "industries": {"status": "AVAILABLE" if industry_rows else "UNAVAILABLE",
                              "industries": industry_rows, "qualityStatus": industry_quality_state,
                              "quality": quality},
               "themes": {"status": "UNAVAILABLE", "reason": "VERIFIED_THEME_MEMBERSHIP_NOT_PUBLISHED"}}
    previous = manager.load_json(args.output, {}) or {}
    discovery = manager.load_json(args.discovery, {}) or {}
    universe_payload = manager.load_json(args.universe, {}) or {}
    asset_registry = manager.load_json(args.asset_registry, {}) or {}
    result = manager.build_management_result(context, universe_payload,
                                              discovery, previous,
                                              manager.load_json(args.policy, {}) or {},
                                              asset_registry=asset_registry)
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
                                                 discovery, previous, manager.load_json(args.policy, {}) or {},
                                                 asset_registry=asset_registry)
        for key in ("leaders", "initialObservationCandidates"):
            for row in result.get(key, []):
                quote = quotes.get(str(row.get("code")))
                if quote:
                    for field in ("currentPrice", "changeRate", "sourceTime"):
                        if quote.get(field) is not None:
                            row[field] = quote[field]
    if not args.no_write:
        result["inputEvidence"] = input_evidence(industries, quality_payload)
        manager.publish_result(result, args.output, args.output, args.history)
    else:
        result["inputEvidence"] = input_evidence(industries, quality_payload)
    print(json.dumps({"status": "SUCCESS", "networkCalls": 0, "industryQuality": industry_quality_state,
                      "sectorCount": len(result["sectors"]),
                      "themePending": bool(result["pending"]), "leaderCount": len(result["leaders"]),
                      "collectionCount": len(result["collectionCodes"]), "written": not args.no_write}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
