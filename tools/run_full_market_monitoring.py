#!/usr/bin/env python3
"""Production entry point for compact full-market intraday evidence.

The full current snapshot remains in memory.  All pre-publication checks run
before any latest evidence file is replaced.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import krx_market_day
import relay

sys.path.insert(0, str(Path(__file__).parent))
import full_market_monitoring_evidence as evidence
import naver_full_market_current as current_collector


BASELINE_FILE = ROOT / "data/monitoring-baseline/latest.json"
OUTPUT_DIR = ROOT / "data/monitoring"
UNIVERSE_CONFIG = ROOT / "config/universe.json"
RELAY_QUOTES_FILE = ROOT / "data/quotes.json"
RELAY_STATES_FILE = ROOT / "data/states.json"
KST = ZoneInfo("Asia/Seoul")


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def extract_universe_snapshot(current_snapshot, universe_file=UNIVERSE_CONFIG):
    """Return the enabled Universe rows from the one validated full snapshot.

    This is deliberately a validation/subset operation, not a replacement for
    relay quotes: the full-market API does not provide relay's source-time and
    delay-time freshness contract.  It proves the evidence snapshot covers
    every configured monitoring stock without another NAVER request.
    """
    universe = load_json(universe_file) or {}
    expected = [str(row.get("itemCode")) for row in universe.get("stocks", [])
                if row.get("enabled", True) and row.get("itemCode")]
    by_code = {str(row.get("code")): row for row in current_snapshot.get("stocks", [])}
    missing = [code for code in expected if code not in by_code]
    if missing:
        raise ValueError(f"full snapshot missing {len(missing)} enabled Universe codes")
    return [by_code[code] for code in expected]


def cross_check_universe_snapshot(universe_subset, relay_quotes_file=RELAY_QUOTES_FILE):
    """Summarize the independent 42-stock/full-market comparison.

    The two NAVER endpoints are intentionally not required to have identical
    values: they can be seconds apart.  This records coverage, comparable
    field counts, and each source's time/latency evidence without inventing a
    price-difference tolerance or replacing the relay quote contract.
    """
    relay = load_json(relay_quotes_file)
    if not isinstance(relay, dict) or not isinstance(relay.get("datas"), list):
        raise ValueError("relay quote snapshot unavailable for cross-check")
    relay_by_code = {str(row.get("itemCode")): row for row in relay["datas"] if row.get("itemCode")}
    expected = [str(row.get("code")) for row in universe_subset]
    missing = [code for code in expected if code not in relay_by_code]
    if missing:
        raise ValueError(f"relay quote snapshot missing {len(missing)} enabled Universe codes")
    fields = {
        "currentPrice": "closePrice", "change": "compareToPreviousClosePrice",
        "changeRate": "fluctuationsRatio", "openPrice": "openPrice",
        "highPrice": "highPrice", "lowPrice": "lowPrice",
        "accumulatedTradingVolume": "accumulatedTradingVolume",
    }
    comparisons = {}
    for full_field, relay_field in fields.items():
        pairs = [(row.get(full_field), relay_by_code[str(row["code"])].get(relay_field)) for row in universe_subset]
        comparable = [(left, right) for left, right in pairs if left is not None and right is not None]
        comparisons[full_field] = {
            "comparedCount": len(comparable),
            "equalCount": sum(left == right for left, right in comparable),
            "differentCount": sum(left != right for left, right in comparable),
        }
    return {
        "status": "AVAILABLE", "expectedCount": len(expected), "relayCount": len(relay_by_code),
        "missingRelayCodes": [], "fieldComparisons": comparisons,
        "relayGeneratedAt": relay.get("generatedAt"), "relaySourceTime": relay.get("sourceTime"),
        "relaySourceTimeLatest": relay.get("sourceTimeLatest"),
        "relaySessions": sorted({str(row.get("session")) for row in relay_by_code.values() if row.get("session")}),
        "relayDelayTimes": sorted({row.get("delayTime") for row in relay_by_code.values() if row.get("delayTime") is not None}),
    }


def previous_completed_close(daily, target_date):
    """Return the latest completed close strictly before the target date."""
    candidates = []
    for row in (daily or {}).get("datas", []):
        if not isinstance(row, dict) or row.get("complete") is not True or row.get("noTrading") is True:
            continue
        close = row.get("close")
        if not isinstance(close, (int, float)) or isinstance(close, bool):
            continue
        try:
            row_date = date.fromisoformat(str(row.get("date")))
        except ValueError:
            continue
        if row_date < target_date:
            candidates.append((row_date, close))
    return max(candidates, default=(None, None), key=lambda item: item[0])


def monitored_universe_summary(universe_subset, relay_quotes_file=RELAY_QUOTES_FILE,
                               relay_states_file=RELAY_STATES_FILE,
                               regular_daily_dir=None, daily_dir=None):
    """Create an explicitly scoped 42-stock view from relay-owned artifacts.

    It is intentionally independent from full-market breadth.  `current` is
    the latest relay quote view; `regularSession` contains only immutable
    confirmed-close state rows and reports availability when that source is
    not yet present.
    """
    quotes = load_json(relay_quotes_file) or {}
    states = load_json(relay_states_file) or {}
    quote_by_code = {str(row.get("itemCode")): row for row in quotes.get("datas", []) if row.get("itemCode")}
    state_by_code = {str(row.get("itemCode")): row for row in states.get("datas", []) if row.get("itemCode")}
    codes = [str(row.get("code")) for row in universe_subset]
    rows = [(code, quote_by_code.get(code), state_by_code.get(code)) for code in codes]
    regular_daily_dir = Path(regular_daily_dir or Path(relay_states_file).parent / "daily-regular")
    daily_dir = Path(daily_dir or Path(relay_states_file).parent / "daily")
    regular_by_code = {}
    for code, quote, state in rows:
        if not quote or not state:
            continue
        try:
            target = datetime.fromisoformat(str(quote.get("sourceTime"))).astimezone(KST)
        except (ValueError, TypeError):
            continue
        if target.time() < time(15, 30):
            continue
        daily = load_json(regular_daily_dir / f"{code}.json")
        if not isinstance(daily, dict) or not isinstance(daily.get("datas"), list):
            continue
        daily = dict(daily, regularSessionDate=target.date().isoformat())
        if not relay._valid_same_day_regular_override(daily, target):
            continue
        bar = daily["datas"][0] if len(daily["datas"]) == 1 else next(
            row for row in daily["datas"] if row.get("date") == target.date().isoformat())
        if any(not isinstance(bar.get(key), (int, float)) for key in ("close", "high")):
            continue
        # Relay's published thresholds are the source for this view.  Reuse
        # its comparisons/breakout classifier with the verified regular bar.
        current_state = state.get("current") or state
        if state.get("status") != "ok":
            continue
        try:
            state_date = datetime.fromisoformat(str(state.get("sourceTime"))).astimezone(KST).date()
        except (ValueError, TypeError):
            continue
        if state_date != target.date():
            continue
        previous_date, previous_close = previous_completed_close(
            load_json(daily_dir / f"{code}.json"), target.date())
        regular_change = (bar["close"] - previous_close
                          if previous_date is not None and previous_close is not None else None)
        regular_by_code[code] = {
            "status": "CONFIRMED", "sourceDate": bar["date"],
            "sourceTime": bar["sourceTime"],
            "priceVsMA20": relay.compare(bar["close"], current_state.get("ma20")),
            "priceVsMA60": relay.compare(bar["close"], current_state.get("ma60")),
            "breakout20": relay.breakout(bar["close"], bar["high"], current_state.get("priorHigh20"), True),
            "previousCloseDate": previous_date.isoformat() if previous_date else None,
            "previousClose": previous_close, "regularChange": regular_change,
        }

    def above_ma(state, period):
        value = state.get(f"aboveMA{period}")
        if isinstance(value, bool):
            return value
        return {"above": True, "below": False, "equal": False}.get(state.get(f"priceVsMA{period}"))

    def scoped_metrics(view):
        values = []
        for code, quote, state in rows:
            if not quote or not state:
                continue
            selected = ((state.get("current") or state) if view == "current"
                        else regular_by_code.get(code) or {})
            if view == "regularSession" and selected.get("status") != "CONFIRMED":
                continue
            values.append((quote if view == "current" else selected, selected))
        change_key = "regularChange" if view == "regularSession" else "fluctuationsRatio"
        changes = [source.get(change_key) for source, _ in values
                   if isinstance(source.get(change_key), (int, float)) and not isinstance(source.get(change_key), bool)]
        above20 = [above_ma(state, 20) for _, state in values if above_ma(state, 20) is not None]
        above60 = [above_ma(state, 60) for _, state in values if above_ma(state, 60) is not None]
        breakout20 = [state.get("breakout20") for _, state in values if state.get("breakout20") not in (None, "unknown")]
        return {
            "stockCount": len(codes), "validCount": len(values),
            "advancers": sum(value > 0 for value in changes), "decliners": sum(value < 0 for value in changes),
            "unchanged": sum(value == 0 for value in changes),
            "changeEligibleCount": len(changes),
            "advancerPct": round(sum(value > 0 for value in changes) / len(changes) * 100, 4) if changes else None,
            "aboveMA20Count": sum(value is True for value in above20), "aboveMA20EligibleCount": len(above20),
            "pctAboveMA20": round(sum(value is True for value in above20) / len(above20) * 100, 4) if above20 else None,
            "aboveMA60Count": sum(value is True for value in above60), "aboveMA60EligibleCount": len(above60),
            "pctAboveMA60": round(sum(value is True for value in above60) / len(above60) * 100, 4) if above60 else None,
            "breakout20Count": sum(value in {"attempt", "confirmed"} for value in breakout20),
            "breakout20EligibleCount": len(breakout20),
            "pctBreakout20": round(sum(value in {"attempt", "confirmed"} for value in breakout20) / len(breakout20) * 100, 4) if breakout20 else None,
        }

    leaders = sorted(
        [{"code": code, "name": quote.get("stockName"), "changeRate": quote.get("fluctuationsRatio"),
          "currentPrice": quote.get("closePrice"), "breakout20": (state.get("current") or state).get("breakout20")}
         for code, quote, state in rows if quote and state],
        key=lambda row: row["changeRate"] if isinstance(row["changeRate"], (int, float)) else -float("inf"), reverse=True,
    )[:30]
    regular_available = list(regular_by_code.values())
    return {
        "scope": "MONITORED_UNIVERSE", "configuredCount": len(codes),
        "relayQuoteGeneratedAt": quotes.get("generatedAt"), "relaySourceTime": quotes.get("sourceTime"),
        "relaySourceTimeLatest": quotes.get("sourceTimeLatest"),
        "current": scoped_metrics("current"),
        "regularSession": {"status": "AVAILABLE" if regular_available else "UNAVAILABLE",
                           "confirmedCount": len(regular_available),
                           **scoped_metrics("regularSession")},
        "monitoredUniverseLeaders": leaders,
    }
def session_at(now):
    current = now.timetz().replace(tzinfo=None)
    if current < time(9, 0): return "PRE"
    if current < time(15, 30): return "REGULAR"
    if current < time(15, 40): return "REGULAR_CLOSED"
    if current < time(20, 0): return "AFTER"
    return "CLOSED"


def previous_open_day(target, market_day_check=krx_market_day.market_day):
    for offset in range(1, 12):
        candidate = target - timedelta(days=offset)
        is_open, _ = market_day_check(candidate)
        if is_open:
            return candidate
    raise RuntimeError("previous KRX trading day unavailable")


def baseline_freshness(baseline, now, market_day_check=krx_market_day.market_day):
    if not baseline or baseline.get("historyStatus") != "OK":
        return False, "BASELINE_UNAVAILABLE", None
    try:
        today = now.date(); is_open, reason = market_day_check(today)
        if not is_open:
            return False, reason, None
        previous = previous_open_day(today, market_day_check).strftime("%Y%m%d")
    except Exception:
        return False, "KRX_CALENDAR_UNAVAILABLE", None
    as_of = str(baseline.get("asOfDate") or "")
    if baseline.get("count") != baseline.get("authoritativeCount") or baseline.get("coveragePct") != 100.0:
        return False, "BASELINE_COVERAGE_INVALID", previous
    if as_of == previous:
        return True, "VALID_PREVIOUS_TRADING_DAY", previous
    if as_of == today.strftime("%Y%m%d") and session_at(now) in {"AFTER", "CLOSED"}:
        return True, "VALID_CURRENT_DAY_FINAL", previous
    if as_of == today.strftime("%Y%m%d"):
        return False, "BASELINE_CURRENT_DAY_BEFORE_FINAL_SESSION", previous
    return False, "BASELINE_STALE", previous


def baseline_readiness(baseline, target_date):
    """Validate the scalar daily baseline publication contract.

    Rolling KOSPI/KOSDAQ state files may have their own publicationStatus;
    this compact scalar baseline intentionally does not.  Its authoritative
    readiness is expressed by the fields produced by daily_monitoring_baseline.
    """
    target = str(target_date).replace("-", "")
    if not isinstance(baseline, dict):
        return False, "BASELINE_UNAVAILABLE"
    if baseline.get("asOfDate") != target:
        return False, "BASELINE_DATE_MISMATCH"
    if baseline.get("historyStatus") != "OK":
        return False, "BASELINE_HISTORY_INVALID"
    if baseline.get("count") != baseline.get("authoritativeCount"):
        return False, "BASELINE_COVERAGE_INVALID"
    if baseline.get("coveragePct") != 100.0:
        return False, "BASELINE_COVERAGE_INVALID"
    diagnostics = baseline.get("refreshDiagnostics") or {}
    if diagnostics.get("missingCodes"):
        return False, "BASELINE_MISSING_CODES"
    if diagnostics.get("extraCodes"):
        return False, "BASELINE_EXTRA_CODES"
    if diagnostics.get("targetDate") and str(diagnostics["targetDate"]).replace("-", "") != target:
        return False, "BASELINE_DIAGNOSTIC_DATE_MISMATCH"
    return True, "READY"


def run(now=None, baseline_file=BASELINE_FILE, output_dir=OUTPUT_DIR, current_file=None,
        no_write=False, market_day_check=krx_market_day.market_day, collector=current_collector.collect_snapshot,
        universe_file=UNIVERSE_CONFIG, relay_quotes_file=RELAY_QUOTES_FILE,
        relay_states_file=RELAY_STATES_FILE):
    now = now or datetime.now(KST)
    baseline = load_json(baseline_file)
    fresh, freshness_status, _ = baseline_freshness(baseline, now, market_day_check)
    if not fresh:
        return {"status": "NOT_PUBLISHED", "reason": freshness_status}
    current = load_json(current_file) if current_file else collector()
    if not current or current.get("status") != "SUCCESS" or current.get("coverageCount") != current.get("expectedCount"):
        return {"status": "NOT_PUBLISHED", "reason": "CURRENT_SNAPSHOT_INCOMPLETE"}
    universe_subset = extract_universe_snapshot(current, universe_file)
    cross_check = cross_check_universe_snapshot(universe_subset, relay_quotes_file)
    monitored = monitored_universe_summary(universe_subset, relay_quotes_file, relay_states_file)
    context = {
        "baselineAsOfDate": baseline["asOfDate"], "baselineGeneratedAt": baseline.get("generatedAt"),
        "baselineCoverage": baseline.get("coveragePct"), "baselineFreshnessStatus": freshness_status,
        # These deliberately name distinct clocks.  A delayed full-market
        # collection must not be mistaken for the relay quote's source time.
        "currentSnapshotGeneratedAt": current.get("generatedAt"), "currentCoverage": current.get("coverageCount"),
        "currentAttempt": current.get("attemptCount"), "evidenceGeneratedAt": now.isoformat(),
        "relayQuoteGeneratedAt": cross_check.get("relayGeneratedAt"),
        "relayQuoteSourceTime": cross_check.get("relaySourceTime"),
        "relayQuoteSourceTimeLatest": cross_check.get("relaySourceTimeLatest"),
        "session": session_at(now), "publicationStatus": "SUCCESS",
        "universeSubsetCount": len(universe_subset),
        "universeSubsetSnapshotGeneratedAt": current.get("generatedAt"),
        "universeCrossCheck": cross_check,
        "monitoredUniverse": monitored,
        "monitoredUniverseLeaders": monitored["monitoredUniverseLeaders"],
    }
    industries = evidence.load_json(evidence.MARKET_DIR / "industries.json", {"status": "UNUSABLE", "industries": []})
    membership = evidence.load_json(evidence.MARKET_DIR / "industry-membership.json", {"status": "UNUSABLE", "industries": []})
    previous_good = evidence.load_json(evidence.MARKET_DIR / "industries-latest-ok.json")
    previous = evidence.load_json(Path(output_dir) / "latest-summary.json")
    payloads, _ = evidence.build_evidence(
        current, industries=industries, membership=membership, previous_summary=previous,
        today=now.strftime("%Y%m%d"), previous_good_industries=previous_good,
        baseline=baseline, publication_context=context,
    )
    sizes = {} if no_write else evidence.write_evidence(payloads, output_dir)
    return {"status": "SUCCESS", "baselineAsOfDate": baseline["asOfDate"],
            "baselineFreshnessStatus": freshness_status, "currentCount": current["coverageCount"],
            "session": context["session"], "files": sizes,
            "totalBytes": sum(sizes.values()) if sizes else sum(len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) for value in payloads.values())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-file", default=str(BASELINE_FILE))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--current-file")
    parser.add_argument("--relay-quotes-file", default=str(RELAY_QUOTES_FILE))
    parser.add_argument("--relay-states-file", default=str(RELAY_STATES_FILE))
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    result = run(baseline_file=args.baseline_file, output_dir=args.output_dir, current_file=args.current_file,
                 relay_quotes_file=args.relay_quotes_file, relay_states_file=args.relay_states_file,
                 no_write=args.no_write)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "SUCCESS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
