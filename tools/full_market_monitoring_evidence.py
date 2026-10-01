#!/usr/bin/env python3
"""Build machine-readable full-market monitoring evidence without mutating history."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import naver_full_market_current as current_collector
import daily_monitoring_baseline as baseline_builder


ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "data/naver-history/canonical/daily"
MARKET_DIR = ROOT / "data/market"
MONITORING_DIR = ROOT / "data/monitoring"
UNIVERSE_FILE = ROOT / "data/market-universe/all-assets.json"
KST_DATE = lambda: datetime.now().astimezone().strftime("%Y%m%d")


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return default


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def pct(numerator, denominator):
    return round(numerator * 100 / denominator, 4) if denominator else None


def mean(values):
    return round(statistics.mean(values), 6) if values else None


def median(values):
    return round(statistics.median(values), 6) if values else None


def history_validity(row):
    close = number(row.get("close"))
    open_ = number(row.get("open")); high = number(row.get("high")); low = number(row.get("low"))
    volume = number(row.get("volume"))
    close_valid = close is not None and close > 0
    ohlc_valid = (bool(row.get("feature_valid", True)) and close_valid and None not in (open_, high, low)
                  and high >= max(open_, close) and low <= min(open_, close))
    volume_valid = volume is not None and volume > 0
    return {"close_valid": close_valid, "ohlc_valid": ohlc_valid,
            "volume_valid": volume_valid, "traded_bar": close_valid and ohlc_valid and volume_valid}


def current_validity(row):
    close = number(row.get("currentPrice")); open_ = number(row.get("openPrice"))
    high = number(row.get("highPrice")); low = number(row.get("lowPrice")); volume = number(row.get("accumulatedTradingVolume"))
    close_valid = close is not None and close > 0
    ohlc_valid = close_valid and None not in (open_, high, low) and high >= max(open_, close) and low <= min(open_, close)
    volume_valid = volume is not None and volume > 0
    return {"close_valid": close_valid, "ohlc_valid": ohlc_valid,
            "volume_valid": volume_valid, "traded_bar": close_valid and ohlc_valid and volume_valid}


def read_history(code, history_dir=HISTORY_DIR, before_date=None, max_rows=300):
    path = Path(history_dir) / f"{code}.jsonl"
    rows = []
    try:
        # The largest current feature window is 240 sessions.  Retaining a
        # small extra buffer avoids decoding ~1.9m historical JSON rows on
        # every intraday run while never altering canonical history.
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in lines[-max_rows:]:
            if not line.strip():
                continue
            row = json.loads(line)
            if before_date is None or str(row.get("date")) < before_date:
                rows.append(row)
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    return sorted(rows, key=lambda row: str(row.get("date")))


def window_mean(values, window):
    return mean(values[-window:]) if len(values) >= window else None


def ret(current, values, lookback):
    if current is None or len(values) < lookback:
        return None
    base = values[-lookback]
    return round((current / base - 1) * 100, 6) if base else None


def feature_row(current, history, today, overlay=False):
    """Build the canonical reference row.

    When ``overlay`` is true, history already ends with the confirmed regular
    bar for ``today``.  Drop only that final observation so the current AFTER
    value replaces it rather than adding a second trading day.
    """
    if overlay:
        history = [row for row in history if str(row.get("date")) != str(today)]
    validity = current_validity(current)
    close_history = [number(row.get("close")) for row in history if history_validity(row)["close_valid"]]
    high_history = [number(row.get("high")) for row in history if history_validity(row)["ohlc_valid"]]
    volume_history = [number(row.get("volume")) for row in history if history_validity(row)["volume_valid"]]
    price = number(current.get("currentPrice"))
    closes = close_history + ([price] if validity["close_valid"] else [])
    ma = {window: window_mean(closes, window) for window in (5, 20, 60, 120, 240)}
    previous_ma = {window: window_mean(close_history, window) for window in (20, 60)}
    rolling = {window: max(high_history[-window:]) if len(high_history) >= window else None
               for window in (20, 60, 120, 240)}
    current_high = number(current.get("highPrice")) if validity["ohlc_valid"] else None
    high52 = number(current.get("high52Week"))
    volume_ma20 = window_mean(volume_history, 20)
    current_volume = number(current.get("accumulatedTradingVolume"))
    dist = lambda value: round((price / value - 1) * 100, 6) if price and value else None
    result = {
        "code": current["code"], "name": current["name"], "market": current["market"],
        "currentPrice": price, "change": number(current.get("change")), "changeRate": number(current.get("changeRate")),
        "close_valid": validity["close_valid"], "ohlc_valid": validity["ohlc_valid"],
        "volume_valid": validity["volume_valid"], "traded_bar": validity["traded_bar"],
        **{f"ma{window}": ma[window] for window in ma},
        **{f"dist_ma{window}": dist(ma[window]) for window in ma},
        **{f"ret_{window}d": ret(price, close_history, window) for window in (1, 5, 20, 60, 120, 240)},
        **{f"rolling_high_{window}": rolling[window] for window in rolling},
        **{f"dist_high_{window}": dist(rolling[window]) for window in rolling},
        "volume_ma20": volume_ma20,
        "current_volume_ratio_20": round(current_volume / volume_ma20, 6) if current_volume and volume_ma20 else None,
        "above_ma20": price > ma[20] if price and ma[20] else None,
        "above_ma60": price > ma[60] if price and ma[60] else None,
        "above_ma120": price > ma[120] if price and ma[120] else None,
        "ma20_rising": ma[20] > previous_ma[20] if ma[20] and previous_ma[20] else None,
        "ma60_rising": ma[60] > previous_ma[60] if ma[60] and previous_ma[60] else None,
        "bullish_alignment": ma[5] > ma[20] > ma[60] if all(ma[x] is not None for x in (5, 20, 60)) else None,
        "breakout_20_intraday": current_high > rolling[20] if current_high and rolling[20] else None,
        "breakout_60_intraday": current_high > rolling[60] if current_high and rolling[60] else None,
        "near_52week_high": price >= high52 * 0.98 if price and high52 else None,
        "snapshotGeneratedAt": current.get("snapshotGeneratedAt"),
        "temporaryCurrentBarDate": today,
    }
    return result


def breadth(rows, market="TOTAL"):
    selected = [row for row in rows if market == "TOTAL" or row["market"] == market]
    valid = [row for row in selected if row["close_valid"]]
    changes = [row["changeRate"] for row in valid if row["changeRate"] is not None]
    def true_count(key): return sum(row[key] is True for row in valid)
    def eligible_count(key): return sum(row[key] is not None for row in valid)
    above20_count, above20_eligible = true_count("above_ma20"), eligible_count("above_ma20")
    above60_count, above60_eligible = true_count("above_ma60"), eligible_count("above_ma60")
    breakout20_count, breakout20_eligible = true_count("breakout_20_intraday"), eligible_count("breakout_20_intraday")
    breakout60_count, breakout60_eligible = true_count("breakout_60_intraday"), eligible_count("breakout_60_intraday")
    return {
        "market": market, "stockCount": len(selected), "validCount": len(valid),
        "advancers": sum(value > 0 for value in changes), "decliners": sum(value < 0 for value in changes),
        "unchanged": sum(value == 0 for value in changes), "advancerPct": pct(sum(value > 0 for value in changes), len(changes)),
        "aboveMA20Count": above20_count, "aboveMA20EligibleCount": above20_eligible,
        "pctAboveMA20": pct(above20_count, above20_eligible),
        "aboveMA60Count": above60_count, "aboveMA60EligibleCount": above60_eligible,
        "pctAboveMA60": pct(above60_count, above60_eligible),
        "pctAboveMA120": pct(true_count("above_ma120"), sum(row["above_ma120"] is not None for row in valid)),
        "breakout20Count": breakout20_count, "breakout20EligibleCount": breakout20_eligible,
        "pctBreakout20": pct(breakout20_count, breakout20_eligible),
        "breakout60Count": breakout60_count, "breakout60EligibleCount": breakout60_eligible,
        "pctBreakout60": pct(breakout60_count, breakout60_eligible),
        "pctNear52WeekHigh": pct(true_count("near_52week_high"), sum(row["near_52week_high"] is not None for row in valid)),
        "pctVolumeAbove20DayAverage": pct(sum((row["current_volume_ratio_20"] or 0) > 1 for row in valid),
                                          sum(row["current_volume_ratio_20"] is not None for row in valid)),
        "medianReturn1D": median([row["ret_1d"] for row in valid if row["ret_1d"] is not None]),
        "medianReturn5D": median([row["ret_5d"] for row in valid if row["ret_5d"] is not None]),
        "medianReturn20D": median([row["ret_20d"] for row in valid if row["ret_20d"] is not None]),
    }


def industry_quality(industries, previous_good=None):
    diagnostics = industries.get("diagnostics") or {}
    status = industries.get("status", "UNUSABLE")
    coverage = diagnostics.get("currentCoveragePct")
    usable = "FULL" if status == "OK" else ("PARTIAL_WITH_COVERAGE" if status == "PARTIAL" and coverage is not None else "UNUSABLE")
    return {"sourceStatus": status, "industryEvidenceUsable": usable,
            "pagesFetched": diagnostics.get("pagesFetched"), "failedPage": diagnostics.get("failedPage"),
            "coverageVsPreviousPct": coverage, "missingCount": len(diagnostics.get("missingCategoryIds", [])),
            "partialReason": diagnostics.get("partialReason"), "comparisonSource": diagnostics.get("comparisonSource"),
            "previousGoodGeneratedAt": (previous_good or {}).get("generatedAt"),
            "missingFromPrevious": diagnostics.get("missingCategoryIds", []),
            "newSincePrevious": diagnostics.get("newSincePrevious", [])}


def aggregate_industries(features, industries, membership, previous_good=None):
    by_code = {row["code"]: row for row in features}
    quality = industry_quality(industries, previous_good)
    source = {str(row.get("id")): row for row in industries.get("industries", [])}
    result = []
    for group in membership.get("industries", []):
        if group.get("status") != "OK":
            continue
        members = [by_code[item.get("code")] for item in group.get("members", []) if item.get("code") in by_code]
        valid = [item for item in members if item["close_valid"]]
        feature_valid = [item for item in members if item["traded_bar"]]
        returns = [item["ret_1d"] for item in valid if item["ret_1d"] is not None]
        leaders = sorted(valid, key=lambda item: (item["changeRate"] or -9999, item["breakout_20_intraday"] is True,
                                                   item["current_volume_ratio_20"] or -1), reverse=True)[:3]
        result.append({
            "id": group.get("id"), "name": group.get("name"), "memberCount": len(members),
            "featureValidMemberCount": len(feature_valid), "closeValidMemberCount": len(valid),
            "advancers": sum((item["changeRate"] or 0) > 0 for item in valid),
            "decliners": sum((item["changeRate"] or 0) < 0 for item in valid), "avgReturn1D": mean(returns),
            "medianReturn1D": median(returns), "avgReturn5D": mean([item["ret_5d"] for item in valid if item["ret_5d"] is not None]),
            "pctAboveMA20": pct(sum(item["above_ma20"] is True for item in valid), sum(item["above_ma20"] is not None for item in valid)),
            "pctAboveMA60": pct(sum(item["above_ma60"] is True for item in valid), sum(item["above_ma60"] is not None for item in valid)),
            "pctBreakout20": pct(sum(item["breakout_20_intraday"] is True for item in valid), sum(item["breakout_20_intraday"] is not None for item in valid)),
            "pctNear52WeekHigh": pct(sum(item["near_52week_high"] is True for item in valid), sum(item["near_52week_high"] is not None for item in valid)),
            "pctVolumeAbove20DayAverage": pct(sum((item["current_volume_ratio_20"] or 0) > 1 for item in valid), sum(item["current_volume_ratio_20"] is not None for item in valid)),
            "leaders": [{key: item.get(key) for key in ("code", "name", "changeRate", "dist_high_20", "current_volume_ratio_20", "above_ma20", "breakout_20_intraday")} for item in leaders],
            "industrySourcePresent": str(group.get("id")) in source,
        })
    return quality, sorted(result, key=lambda item: (item["avgReturn1D"] is not None, item["avgReturn1D"] or -9999), reverse=True)


def compare_breadth(previous, current):
    if not previous or not previous.get("breadth"):
        return {"comparisonAvailable": False, "previousGeneratedAt": None, "currentGeneratedAt": current.get("generatedAt")}
    before, after = previous["breadth"].get("TOTAL", {}), current["breadth"].get("TOTAL", {})
    keys = ("advancerPct", "pctAboveMA20", "pctAboveMA60", "pctBreakout20", "pctNear52WeekHigh", "medianReturn1D", "pctVolumeAbove20DayAverage")
    result = {"comparisonAvailable": True, "previousGeneratedAt": previous.get("generatedAt"), "currentGeneratedAt": current.get("generatedAt")}
    for key in keys:
        result["delta" + key[0].upper() + key[1:]] = round(after[key] - before[key], 6) if after.get(key) is not None and before.get(key) is not None else None
    return result


def signal_subset(features):
    ordered = lambda rows, key, reverse=True: sorted([row for row in rows if row.get(key) is not None], key=lambda row: row[key], reverse=reverse)[:60]
    selected = {row["code"]: row for row in ordered(features, "changeRate")}
    selected.update({row["code"]: row for row in ordered(features, "changeRate", False)})
    selected.update({row["code"]: row for row in ordered(features, "current_volume_ratio_20")})
    selected.update({row["code"]: row for row in features if row.get("breakout_20_intraday") or row.get("near_52week_high")})
    keys = ("code", "name", "market", "currentPrice", "change", "changeRate", "ret_1d", "ret_5d", "ret_20d",
            "dist_ma20", "dist_ma60", "dist_high_20", "above_ma20", "above_ma60", "breakout_20_intraday",
            "near_52week_high", "current_volume_ratio_20", "close_valid", "ohlc_valid", "volume_valid")
    return [{key: row.get(key) for key in keys} for row in sorted(selected.values(), key=lambda row: row["code"])]


def build_evidence(current_snapshot, history_dir=HISTORY_DIR, industries=None, membership=None,
                   previous_summary=None, today=None, previous_good_industries=None, baseline=None,
                   publication_context=None):
    if current_snapshot.get("status") != "SUCCESS":
        raise ValueError("current snapshot is not SUCCESS")
    today = today or KST_DATE()
    if baseline is not None:
        if baseline.get("historyStatus") != "OK":
            raise ValueError("baseline history is not OK")
        publication_context = publication_context or {}
        as_of = str(baseline.get("asOfDate") or "")
        overlay = today == as_of
        if as_of and today < as_of:
            raise ValueError("current date must be after baseline asOfDate")
        if overlay and publication_context.get("baselineFreshnessStatus") != "VALID_CURRENT_DAY_FINAL":
            raise ValueError("same-day baseline requires validated final-session overlay")
        by_code = {row["code"]: row for row in baseline.get("stocks", [])}
        missing = [row["code"] for row in current_snapshot["stocks"] if row["code"] not in by_code]
        if missing:
            raise ValueError(f"baseline missing {len(missing)} current codes")
        features = [baseline_builder.feature_row_from_baseline(row, by_code[row["code"]], today, overlay=overlay)
                    for row in current_snapshot["stocks"]]
    else:
        features = [feature_row(row, read_history(row["code"], history_dir, today), today)
                    for row in current_snapshot["stocks"]]
    history_codes = {path.stem for path in Path(history_dir).glob("*.jsonl")}
    current_codes = {row["code"] for row in current_snapshot["stocks"]}
    industries = industries or {"status": "UNUSABLE", "industries": []}
    membership = membership or {"status": "UNUSABLE", "industries": []}
    breadth_data = {market: breadth(features, market) for market in ("TOTAL", "KOSPI", "KOSDAQ")}
    publication_context = publication_context or {}
    full_market = {"scope": "AUTHORITATIVE_FULL_MARKET", "authoritativeCount": current_snapshot["expectedCount"],
                   "markets": breadth_data}
    summary = {"schemaVersion": 1, "generatedAt": current_snapshot["generatedAt"], "source": "NAVER_FULL_MARKET_EVIDENCE",
               # Keep breadth for current readers; fullMarket is the explicit
               # scope Work must use for market-wide interpretation.
               "breadth": breadth_data, "fullMarket": full_market,
               "themeEvidenceStatus": "DISABLED_PENDING_RESEARCH_GATE", "availableThemeCount": 0,
               "themeEvidence": {"scope": "FULL_MARKET", "status": "DISABLED_PENDING_RESEARCH_GATE"}}
    summary.update({key: publication_context[key] for key in (
        "session", "publicationStatus", "monitoredUniverse", "universeCrossCheck",
    ) if key in publication_context})
    quality, industry_rows = aggregate_industries(features, industries, membership, previous_good_industries)
    leaders = sorted(features, key=lambda item: (item["changeRate"] or -9999, item["breakout_20_intraday"] is True,
                                                  item["current_volume_ratio_20"] or -1), reverse=True)[:30]
    evidence = {
        "latest-breadth.json": {"generatedAt": summary["generatedAt"], "scope": "AUTHORITATIVE_FULL_MARKET",
                                  "fullMarket": full_market, "breadth": breadth_data,
                                  "monitoredUniverse": publication_context.get("monitoredUniverse")},
        "latest-stock-signals.json": {"generatedAt": summary["generatedAt"], "scope": "AUTHORITATIVE_FULL_MARKET",
                                      "count": len(signal_subset(features)), "stocks": signal_subset(features)},
        "latest-industries.json": {"generatedAt": summary["generatedAt"], "scope": "AUTHORITATIVE_FULL_MARKET", "quality": quality,
                                     "membershipSourceStatus": membership.get("status"),
                                     "sourceIndustryCount": len(industries.get("industries", [])),
                                     "aggregationUsableIndustryCount": len(industry_rows), "industries": industry_rows},
        "latest-leaders.json": {"generatedAt": summary["generatedAt"], "scope": "AUTHORITATIVE_FULL_MARKET",
                                "fullMarketLeaders": [{key: item.get(key) for key in ("code", "name", "market", "changeRate", "dist_high_20", "current_volume_ratio_20", "above_ma20", "breakout_20_intraday")} for item in leaders],
                                "monitoredUniverseLeaders": publication_context.get("monitoredUniverseLeaders", []),
                                # Compatibility alias; this is never a 42-stock list.
                                "candidates": [{key: item.get(key) for key in ("code", "name", "market", "changeRate", "dist_high_20", "current_volume_ratio_20", "above_ma20", "breakout_20_intraday")} for item in leaders]},
        "latest-changes.json": compare_breadth(previous_summary, summary),
        "latest-quality.json": {"generatedAt": summary["generatedAt"], "scope": "AUTHORITATIVE_FULL_MARKET", "authoritativeCount": current_snapshot["expectedCount"],
                                "currentCount": len(current_codes), "currentCoveragePct": pct(len(current_codes), current_snapshot["expectedCount"]),
                                "historyCoveragePct": pct(len(current_codes & history_codes), current_snapshot["expectedCount"]),
                                "duplicateCodes": current_snapshot.get("duplicateCodes", []), "missingCurrentCodes": current_snapshot.get("missingCodes", []),
                                "missingHistoryCodes": sorted(current_codes - history_codes),
                                "closeValidCount": sum(item["close_valid"] for item in features),
                                "featureValidCount": sum(item["traded_bar"] for item in features),
                                "ohlcInvalidCount": sum(not item["ohlc_valid"] for item in features), "volumeInvalidCount": sum(not item["volume_valid"] for item in features),
                                "currentSnapshotAttemptCount": current_snapshot["attemptCount"], "industryStatus": quality["sourceStatus"],
                                "industryCoverageVsPreviousPct": quality["coverageVsPreviousPct"], "industryFailedPage": quality["failedPage"],
                                "industryMissingCount": quality["missingCount"], "themeStatus": "DISABLED_PENDING_RESEARCH_GATE"},
        "latest-summary.json": summary,
    }
    evidence["latest-quality.json"].update(publication_context)
    return evidence, features


def write_evidence(evidence, output_dir=MONITORING_DIR):
    sizes = {}
    for name, payload in evidence.items():
        path = Path(output_dir) / name
        atomic_json(path, payload)
        sizes[name] = path.stat().st_size
    return sizes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(MONITORING_DIR))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--current-file")
    parser.add_argument("--history-mode", choices=("canonical", "baseline"), default="canonical")
    parser.add_argument("--baseline-file", default=str(ROOT / "data/monitoring-baseline/latest.json"))
    args = parser.parse_args()
    current = load_json(args.current_file) if args.current_file else current_collector.collect_snapshot(UNIVERSE_FILE)
    industries = load_json(MARKET_DIR / "industries.json", {"status": "UNUSABLE", "industries": []})
    membership = load_json(MARKET_DIR / "industry-membership.json", {"status": "UNUSABLE", "industries": []})
    previous_good_industries = load_json(MARKET_DIR / "industries-latest-ok.json")
    previous = load_json(Path(args.output_dir) / "latest-summary.json")
    baseline = load_json(args.baseline_file) if args.history_mode == "baseline" else None
    evidence, _ = build_evidence(
        current, HISTORY_DIR, industries, membership, previous,
        previous_good_industries=previous_good_industries, baseline=baseline,
    )
    sizes = write_evidence(evidence, args.output_dir) if not args.no_write else {name: len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) for name, payload in evidence.items()}
    print(json.dumps({"currentCoverage": current["coverageCount"], "files": sizes, "totalBytes": sum(sizes.values())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
