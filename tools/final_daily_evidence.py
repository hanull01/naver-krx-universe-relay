"""Read-only validation for a post-20:00 NAVER current snapshot.

This module never fetches data or writes operating daily/baseline files.  It
only evaluates a supplied snapshot and existing baseline for a possible same-
day daily-bar observation.  NAVER's official post-20:00 definition allows a
candidate final trade without a terminal marker; this module still never
writes history or promotes the operating FINAL store.
"""
from __future__ import annotations

from datetime import datetime
import argparse
import json
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
TERMINAL_MARKERS = {"CLOSED", "AFTER_MARKET_CLOSED", "FINAL"}
OFFICIAL_POLICY = "KRX_AFTER_MARKET_CLOSE_20_CANDIDATE"
MARKET_CODES = {"0": "KOSPI", "1": "KOSDAQ"}


def _date(value):
    text = str(value or "").replace("-", "")
    return text if len(text) == 8 and text.isdigit() else None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_snapshot(snapshot, baseline=None, target_date=None, expected_codes=None):
    """Return deterministic diagnostics; no network or filesystem access."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    rows = snapshot.get("stocks") or []
    # Only explicit session fields can establish the target date.  In
    # particular, snapshot.asOfDate is not a session-date source because it
    # may be inherited from the prior baseline.
    snapshot_target = _date(snapshot.get("tradingDate")
                            or snapshot.get("tradeDate") or snapshot.get("marketDate")
                            or snapshot.get("localDate"))
    validated_target = _date(target_date)
    # When the FINAL workflow supplies the optional argument, an empty or
    # invalid value means that independent KRX date evidence was unavailable;
    # do not silently fall back to a snapshot-local date from another path.
    target = snapshot_target if target_date is None else validated_target
    generated = snapshot.get("generatedAt")
    errors, warnings = [], []
    if snapshot.get("source") != "NAVER_STOCKLIST_CURRENT":
        errors.append("SNAPSHOT_SOURCE_UNVERIFIED")
    evidence = snapshot.get("sourceMarketEvidence") or {}
    if (snapshot.get("sourceMarket") != "KRX"
            or evidence.get("provider") != "NAVER"
            or evidence.get("requestTradeType") != "KRX"
            or evidence.get("responseField") != "sosok"
            or set(evidence.get("markets") or []) != {"KOSPI", "KOSDAQ"}):
        errors.append("SOURCE_MARKET_EVIDENCE_UNVERIFIED")
    if not target:
        errors.append("TRADING_DATE_MISSING")
    if target_date is not None and not validated_target:
        errors.append("TRADING_DATE_EVIDENCE_INVALID")
    elif validated_target and snapshot_target and validated_target != snapshot_target:
        errors.append("TRADING_DATE_MISMATCH")
    if not generated:
        errors.append("SNAPSHOT_TIME_MISSING")
    else:
        try:
            instant = datetime.fromisoformat(str(generated).replace("Z", "+00:00")).astimezone(KST)
            if instant.hour < 20:
                errors.append("SNAPSHOT_BEFORE_20_KST")
        except ValueError:
            errors.append("SNAPSHOT_TIME_INVALID")

    codes = [str(row.get("code") or "") for row in rows]
    duplicates = sorted({code for code in codes if code and codes.count(code) > 1})
    if duplicates:
        errors.append("DUPLICATE_CODES")
    composition_pending = (snapshot.get("status") == "COMPLETE_RECONCILIATION_PENDING"
                           and snapshot.get("collectionStatus", "SUCCESS") == "SUCCESS"
                           and snapshot.get("qualityStatus") == "WARNING")
    if expected_codes is not None and not composition_pending:
        missing = sorted(set(map(str, expected_codes)) - set(codes))
        extra = sorted(set(codes) - set(map(str, expected_codes)))
        if missing:
            errors.append("MISSING_CODES")
        if extra:
            errors.append("EXTRA_CODES")
    else:
        missing, extra = [], []
    expected_count = snapshot.get("expectedCount")
    if isinstance(expected_count, int) and len(rows) != expected_count and not composition_pending:
        errors.append("COVERAGE_MISMATCH")
    elif isinstance(expected_count, int) and len(rows) != expected_count:
        warnings.append("UNIVERSE_COMPOSITION_CHANGED")

    for row in rows:
        code = str(row.get("code") or "")
        raw_source_market_code = row.get("sourceMarketCode")
        source_market = MARKET_CODES.get("" if raw_source_market_code is None
                                         else str(raw_source_market_code))
        if source_market not in {"KOSPI", "KOSDAQ"}:
            errors.append(f"SOURCE_MARKET_CODE_UNVERIFIED:{code}")
        elif str(row.get("market") or "").upper() != source_market:
            errors.append(f"SOURCE_MARKET_CODE_MISMATCH:{code}")
        prices = [row.get(key) for key in ("openPrice", "highPrice", "lowPrice", "currentPrice")]
        if not all(_number(value) and value >= 0 for value in prices):
            errors.append(f"OHLCV_PRICE_INVALID:{code}")
        if _number(row.get("accumulatedTradingVolume")) and row["accumulatedTradingVolume"] < 0:
            errors.append(f"VOLUME_INVALID:{code}")
        if _number(row.get("accumulatedTradingValue")) and row["accumulatedTradingValue"] < 0:
            errors.append(f"VALUE_INVALID:{code}")
        market_status = str(row.get("marketStatus") or "").upper()
        session = str(row.get("tradingSessionType") or "").upper()
        closed_status = (market_status in {"CLOSE", "CLOSED"}
                         or market_status in TERMINAL_MARKERS
                         or session in TERMINAL_MARKERS)
        if not closed_status:
            errors.append(f"MARKET_NOT_CLOSED:{code}")
        halted = (str(row.get("tradeStopYn") or "").upper() == "Y"
                  or str(row.get("tradableStatus") or "").upper() in {"HALTED", "SUSPENDED", "STOPPED"})
        if halted:
            row["sessionEvidence"] = "TRADE_STOPPED"
        elif market_status in TERMINAL_MARKERS or session in TERMINAL_MARKERS:
            row["sessionEvidence"] = "EXPLICIT_TERMINAL"
        elif session == "AFTER_MARKET":
            row["sessionEvidence"] = "AFTER_MARKET"
        elif session in {"REGULAR_CLOSED", "REGULAR_ONLY"} or row.get("afterMarketEligible") is False:
            row["sessionEvidence"] = "AFTER_MARKET_EXCLUDED"
        else:
            row["sessionEvidence"] = "UNVERIFIED"
            errors.append(f"SESSION_EVIDENCE_UNVERIFIED:{code}")

    baseline_date = _date((baseline or {}).get("asOfDate"))
    if baseline_date and target:
        # Before final collection the baseline is normally the previous KRX
        # trading day. The same-day baseline is generated downstream from
        # this validated snapshot, not required as its prerequisite.
        try:
            if int(baseline_date) > int(target):
                errors.append("BASELINE_DATE_MISMATCH")
        except ValueError:
            errors.append("BASELINE_DATE_MISMATCH")
    baseline_source_observed = (
        (baseline or {}).get("historyStatus") == "SOURCE_OBSERVED"
        and (baseline or {}).get("sourceQualityStatus") == "SOURCE_OBSERVED"
    )
    if ((baseline or {}).get("historyStatus") not in (None, "OK")
            and not baseline_source_observed):
        errors.append("BASELINE_NOT_READY")

    markers = {str(row.get("marketStatus") or "").upper() for row in rows}
    markers |= {str(row.get("tradingSessionType") or "").upper() for row in rows}
    markers.discard("")
    terminal = bool(markers & TERMINAL_MARKERS)
    official_candidate = not errors
    if terminal:
        session_status = "EXPLICIT_TERMINAL"
    elif official_candidate:
        session_status = "KRX_AFTER_MARKET_CLOSE_20"
    else:
        session_status = "UNKNOWN"
        warnings.append("SESSION_FINALITY_UNKNOWN")

    status = "PASS_WITH_UNKNOWN_FINALITY" if not errors else "UNUSABLE"
    return {
        "status": status,
        "finalEligible": bool(official_candidate),
        "finalityPolicy": OFFICIAL_POLICY,
        "sessionStatus": session_status,
        "tradingDate": target,
        "generatedAt": generated,
        "rowCount": len(rows),
        "expectedCount": expected_count,
        "duplicateCodes": duplicates,
        "missingCodes": missing,
        "extraCodes": extra,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "baselineDate": baseline_date,
        "networkCalls": 0,
        "sessionEvidence": {str(row.get("code") or ""): row.get("sessionEvidence") for row in rows},
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument(
        "--target-date",
        default=None,
        help="validated KRX trading date supplied by the FINAL evidence path",
    )
    args = parser.parse_args(argv)
    snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    result = validate_snapshot(snapshot, baseline, target_date=args.target_date)
    print(json.dumps({key: result[key] for key in (
        "status", "tradingDate", "generatedAt", "rowCount", "expectedCount",
        "sessionStatus", "finalEligible", "finalityPolicy", "errors", "warnings", "networkCalls"
    )}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
