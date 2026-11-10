#!/usr/bin/env python3
"""Production entry point for compact full-market intraday evidence.

The full current snapshot remains in memory.  All pre-publication checks run
before any latest evidence file is replaced.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import krx_market_day
import relay

sys.path.insert(0, str(Path(__file__).parent))
import full_market_monitoring_evidence as evidence
import naver_full_market_current as current_collector


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
        return {"status": "UNAVAILABLE", "reason": "RELAY_QUOTE_SNAPSHOT_MISSING",
                "expectedCount": len(universe_subset), "relayCount": 0,
                "missingRelayCodes": [str(row.get("code")) for row in universe_subset]}
    relay_by_code = {str(row.get("itemCode")): row for row in relay["datas"] if row.get("itemCode")}
    expected = [str(row.get("code")) for row in universe_subset]
    missing = [code for code in expected if code not in relay_by_code]
    if missing:
        return {"status": "PARTIAL", "reason": "RELAY_QUOTE_COVERAGE_INCOMPLETE",
                "expectedCount": len(expected), "relayCount": len(relay_by_code),
                "missingRelayCodes": missing}
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
        # Prefer the already verified 15:30 reference embedded in quotes.json.
        # This avoids reopening the per-symbol daily-regular cache and makes
        # no additional network request.  Keep the cache as a compatibility
        # fallback for older quote artifacts.
        reference_time = str(quote.get("referenceSourceTime") or "")
        reference_price = quote.get("referencePrice")
        reference_ok = (quote.get("referenceStatus") == "ok"
                        and quote.get("referencePriceBasis") == "TODAY_REGULAR_CLOSE"
                        and reference_time.startswith(target.strftime("%Y%m%d"))
                        and isinstance(reference_price, (int, float))
                        and not isinstance(reference_price, bool))
        if reference_ok:
            bar = {"date": target.date().isoformat(), "close": reference_price,
                   "high": reference_price, "complete": True,
                   "session": "REGULAR", "barType": "REGULAR_SESSION",
                   "source": "NAVER_VERIFIED_REGULAR_CLOSE",
                   "sourceTime": reference_time}
        else:
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


def snapshot_is_publishable(current):
    """Accept complete collection with pending Universe reconciliation only."""
    publishable_statuses = {"SUCCESS", "SUCCESS_WITH_WARNING", "COMPLETE_RECONCILIATION_PENDING"}
    return bool(
        current
        and current.get("status") in publishable_statuses
        and current.get("collectionStatus", "SUCCESS") == "SUCCESS"
        and not current.get("duplicateCodes")
        and not current.get("repeatedPages")
        and not current.get("errors")
        and current.get("coverageCount", 0) > 0
        and (
            current.get("count") == current.get("coverageCount")
            or (current.get("qualityStatus") == "WARNING"
                and current.get("coverageCount", 0) * 100 >= current.get("expectedCount", 1) * 99)
        )
    )


def intraday_trading_date_evidence(current, now,
                                   market_day_check=krx_market_day.market_day):
    """Build date evidence from the validated current snapshot and calendar."""
    session = session_at(now)
    evidence_class = (
        "POST_MARKET_SNAPSHOT" if session == "CLOSED" else "INTRADAY_SESSION"
    )
    base = {
        "tradingDate": None, "status": "UNAVAILABLE",
        "reason": "INTRADAY_TRADING_DATE_EVIDENCE_UNAVAILABLE",
        "evidenceClass": evidence_class,
    }
    if not isinstance(current, dict):
        return {**base, "reason": "CURRENT_SNAPSHOT_UNAVAILABLE"}
    target = now.date()
    try:
        is_open, calendar_reason = market_day_check(target)
    except Exception:
        return {**base, "reason": "KRX_CALENDAR_UNAVAILABLE"}
    if not is_open:
        return {**base, "reason": calendar_reason}
    # After 20:00 the current snapshot still identifies the KRX trading day
    # when the calendar and every collection timestamp agree.  Keep it as a
    # non-final post-market observation; final daily-bar validation remains a
    # separate contract.
    if session not in {"PRE", "REGULAR", "REGULAR_CLOSED", "AFTER", "CLOSED"}:
        return {**base, "reason": "OUTSIDE_INTRADAY_SESSION"}
    if not snapshot_is_publishable(current):
        return {**base, "reason": "CURRENT_SNAPSHOT_INCOMPLETE"}
    target_date = target.strftime("%Y%m%d")
    timestamps = [current.get("generatedAt")]
    timestamps.extend(row.get("snapshotGeneratedAt") for row in current.get("stocks", []))
    if not timestamps or any(not isinstance(value, str) or not value for value in timestamps):
        return {**base, "reason": "SNAPSHOT_COLLECTION_TIME_MISSING"}
    try:
        observed_dates = {datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(KST).strftime("%Y%m%d")
                          for value in timestamps}
    except (TypeError, ValueError):
        return {**base, "reason": "SNAPSHOT_COLLECTION_TIME_INVALID"}
    if observed_dates != {target_date}:
        return {**base, "reason": "SNAPSHOT_COLLECTION_TIME_MISMATCH",
                "observedDates": sorted(observed_dates), "expectedDate": target_date}
    if current.get("expectedCount") != current.get("coverageCount"):
        return {**base, "reason": "SNAPSHOT_COVERAGE_INCOMPLETE"}
    return {
        "tradingDate": target_date, "status": "VERIFIED",
        "reason": "VERIFIED_KRX_TRADING_DAY_AND_CURRENT_SNAPSHOT",
        "evidenceClass": evidence_class,
        "source": "KRX_CALENDAR_AND_NAVER_CURRENT_SNAPSHOT",
        "observedDateCounts": {target_date: current.get("coverageCount", 0)},
        "collectionGeneratedAt": current.get("generatedAt"),
        "collectionStatus": current.get("collectionStatus"),
    }


def run(now=None, output_dir=OUTPUT_DIR, current_file=None,
        no_write=False, market_day_check=krx_market_day.market_day, collector=current_collector.collect_snapshot,
        universe_file=UNIVERSE_CONFIG, relay_quotes_file=RELAY_QUOTES_FILE,
        relay_states_file=RELAY_STATES_FILE,
        krx_resolution_file=None, krx_input_dir=None):
    """Collect current NAVER evidence against a verified KRX input.

    Production callers must provide the immutable staged KRX input.
    """
    now = now or datetime.now(KST)
    if not krx_input_dir:
        return {"status": "NOT_PUBLISHED", "reason": "KRX_INPUT_REQUIRED"}
    krx_resolution = None
    if krx_resolution_file:
        krx_resolution = load_json(krx_resolution_file)
        if krx_resolution.get("receiptStatus") != "VERIFIED":
            return {"status": "NOT_PUBLISHED", "reason": "KRX_RESOLUTION_NOT_VERIFIED"}
        if krx_resolution.get("publicationAuthorized") is not True:
            return {"status": "NOT_PUBLISHED", "reason": "KRX_PUBLICATION_NOT_AUTHORIZED"}
        if krx_resolution.get("historicalSource") != "KRX":
            return {"status": "NOT_PUBLISHED", "reason": "KRX_HISTORICAL_SOURCE_INVALID"}
    if krx_input_dir:
        input_root = Path(krx_input_dir)
        required = (
            "technicals.json",
            "states.json",
            "group-states.json",
            "report-contract-projection.json",
            "krx-resolution.json",
        )
        missing = [name for name in required if not (input_root / name).is_file()]
        if missing:
            return {"status": "NOT_PUBLISHED", "reason": "KRX_INPUT_UNAVAILABLE", "missing": missing}
        input_resolution = load_json(input_root / "krx-resolution.json")
        if input_resolution.get("receiptStatus") != "VERIFIED":
            return {"status": "NOT_PUBLISHED", "reason": "KRX_INPUT_UNAVAILABLE"}
        if krx_resolution and (
            input_resolution.get("sourceGeneration") != krx_resolution.get("sourceGeneration")
            or input_resolution.get("targetDate") != krx_resolution.get("targetDate")
        ):
            return {"status": "NOT_PUBLISHED", "reason": "KRX_INPUT_GENERATION_MISMATCH"}
        krx_resolution = dict(krx_resolution or input_resolution)
        krx_resolution["inputMode"] = "DIRECT_KRX_REPORT_INPUT"
        krx_resolution["inputArtifacts"] = list(required)
    # The immutable KRX receipt and staged artifacts are the only historical
    # input for this production path.  A current NAVER snapshot identifies the
    # observation date; it is never promoted to a historical close or FINAL.
    current = load_json(current_file) if current_file else collector()
    if not snapshot_is_publishable(current):
        return {"status": "NOT_PUBLISHED", "reason": "CURRENT_SNAPSHOT_INCOMPLETE"}
    universe_subset = extract_universe_snapshot(current, universe_file)
    # The relay/legacy view is an advisory comparison only.  Full-market
    # evidence must remain publishable when the registered Universe is empty,
    # stale, or intentionally independent from the authoritative snapshot.
    cross_check = cross_check_universe_snapshot(universe_subset, relay_quotes_file)
    monitored = monitored_universe_summary(universe_subset, relay_quotes_file, relay_states_file)
    context = {
        "historicalTargetDate": (krx_resolution or {}).get("targetDate"),
        "historicalGenerationId": (krx_resolution or {}).get("sourceGeneration"),
        "historicalLineageStatus": (krx_resolution or {}).get("receiptStatus"),
        # These deliberately name distinct clocks.  A delayed full-market
        # collection must not be mistaken for the relay quote's source time.
        "currentSnapshotGeneratedAt": current.get("generatedAt"), "currentCoverage": current.get("coverageCount"),
        "observedDate": now.strftime("%Y%m%d"),
        "observedAt": current.get("generatedAt") or now.isoformat(),
        "currentAttempt": current.get("attemptCount"), "evidenceGeneratedAt": now.isoformat(),
        "relayQuoteGeneratedAt": cross_check.get("relayGeneratedAt"),
        "relayQuoteSourceTime": cross_check.get("relaySourceTime"),
        "relayQuoteSourceTimeLatest": cross_check.get("relaySourceTimeLatest"),
        "session": session_at(now), "publicationStatus": current.get("status", "SUCCESS"),
        "universeSubsetCount": len(universe_subset),
        "universeSubsetSnapshotGeneratedAt": current.get("generatedAt"),
        "universeCrossCheck": cross_check,
        "monitoredUniverse": monitored,
        "monitoredUniverseLeaders": monitored["monitoredUniverseLeaders"],
        "sourceGeneration": (krx_resolution or {}).get("sourceGeneration"),
        "krxManifestSha": (krx_resolution or {}).get("manifestSha256"),
        "krxMonitoringSha": (krx_resolution or {}).get("monitoringSha256"),
        "krxTargetDate": (krx_resolution or {}).get("targetDate"),
        "krxReceiptStatus": (krx_resolution or {}).get("receiptStatus"),
        "krxEntityCount": (krx_resolution or {}).get("entityCount"),
        "krxInstrumentCount": (krx_resolution or {}).get("instrumentCount"),
        "historicalSource": (krx_resolution or {}).get("historicalSource", "KRX"),
        "currentSnapshotSource": (krx_resolution or {}).get("currentSnapshotSource", "NAVER"),
        "krxInputMode": (krx_resolution or {}).get("inputMode"),
        "krxInputArtifacts": (krx_resolution or {}).get("inputArtifacts"),
    }
    context["intradayTradingDateEvidence"] = intraday_trading_date_evidence(current, now, market_day_check)
    industries = evidence.load_json(evidence.MARKET_DIR / "industries.json", {"status": "UNUSABLE", "industries": []})
    membership = evidence.load_json(evidence.MARKET_DIR / "industry-membership.json", {"status": "UNUSABLE", "industries": []})
    theme_membership = evidence.load_json(evidence.THEME_MEMBERSHIP_FILE, {"status": "UNUSABLE", "themes": []})
    previous_good = evidence.load_json(evidence.MARKET_DIR / "industries-latest-ok.json")
    previous = evidence.load_json(Path(output_dir) / "latest-summary.json") or {}
    previous["previousLeaders"] = (evidence.load_json(Path(output_dir) / "latest-leaders.json") or {}).get("fullMarketLeaders", [])
    payloads, _ = evidence.build_evidence(
        current, industries=industries, membership=membership, previous_summary=previous,
        today=now.strftime("%Y%m%d"), previous_good_industries=previous_good,
        publication_context=context, theme_membership=theme_membership,
    )
    sizes = {} if no_write else evidence.write_evidence(payloads, output_dir)
    return {"status": "SUCCESS", "historicalTargetDate": (krx_resolution or {}).get("targetDate"),
            "historicalGenerationId": (krx_resolution or {}).get("sourceGeneration"),
            "currentCount": current["coverageCount"],
            "session": context["session"], "files": sizes,
            "totalBytes": sum(sizes.values()) if sizes else sum(len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) for value in payloads.values())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--current-file")
    parser.add_argument("--relay-quotes-file", default=str(RELAY_QUOTES_FILE))
    parser.add_argument("--relay-states-file", default=str(RELAY_STATES_FILE))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--krx-resolution")
    parser.add_argument("--krx-input-dir")
    args = parser.parse_args()
    result = run(output_dir=args.output_dir, current_file=args.current_file,
                 relay_quotes_file=args.relay_quotes_file, relay_states_file=args.relay_states_file,
                 no_write=args.no_write,
                 krx_resolution_file=args.krx_resolution, krx_input_dir=args.krx_input_dir)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "SUCCESS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
