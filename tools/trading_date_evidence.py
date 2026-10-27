"""Read-only trading-date evidence derived from canonical NAVER daily bars."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")

def _valid_date(value):
    match = _DATE.fullmatch(str(value or ""))
    if not match:
        return False
    try:
        date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return False
    return True

def _read_latest(path):
    rows = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not _valid_date(row.get("date")):
                return None, "INVALID_CANONICAL_ROW"
            rows.append(row)
    except (OSError, ValueError, json.JSONDecodeError):
        return None, "INVALID_CANONICAL_FILE"
    if not rows:
        return None, "EMPTY_CANONICAL_FILE"
    dates = [str(row["date"]) for row in rows]
    if len(dates) != len(set(dates)):
        return None, "DUPLICATE_CANONICAL_DATE"
    return max(dates), None

def _explicit_session_dates(payload):
    if not isinstance(payload, dict):
        return []
    values = []
    for key in ("tradingDate", "tradeDate", "marketDate"):
        if payload.get(key) is not None:
            values.append(str(payload[key]))
    rows = payload.get("stocks") or []
    row_values = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("tradingDate", "tradeDate", "marketDate", "localDate", "date"):
            if row.get(key) is not None:
                row_values.append(str(row[key]))
    if row_values:
        values.extend(row_values)
    return values


def _session_date_evidence(session_date=None, current_snapshot=None):
    explicit = []
    if session_date is not None:
        explicit.append(str(session_date))
    explicit.extend(_explicit_session_dates(current_snapshot))
    explicit = [value.replace("-", "").replace("/", "") for value in explicit]
    candidate = explicit[0] if explicit and _valid_date(explicit[0]) else ""
    if not candidate or any(value != candidate or not _valid_date(value) for value in explicit):
        return None, sorted(set(explicit))
    return candidate, [candidate]


def explicit_session_date(session_date=None, current_snapshot=None):
    """Return only an explicitly observed session date, never a clock date."""
    candidate, _ = _session_date_evidence(session_date, current_snapshot)
    return candidate


def _read_final_state(path, market, candidate):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None, {"reason": "INVALID_MONITORING_DAILY_FINAL_FILE", "market": market}
    if (payload.get("format") != "COLUMN_ARRAY_V1"
            or payload.get("publicationStatus") != "FINAL"
            or str(payload.get("market") or "").upper() != market
            or str(payload.get("asOfDate") or "").replace("-", "") != candidate):
        return None, {"reason": "MONITORING_DAILY_FINAL_CONTRACT_INVALID", "market": market,
                      "asOfDate": payload.get("asOfDate"),
                      "publicationStatus": payload.get("publicationStatus")}
    stocks = payload.get("stocks")
    if not isinstance(stocks, list):
        return None, {"reason": "MONITORING_DAILY_FINAL_STOCKS_INVALID", "market": market}
    return payload, None


def _read_session_diagnostic(path, current_codes, candidate=None):
    """Validate the optional localDate sidecar when it is published."""
    path = Path(path)
    if not path.is_file():
        return {"status": "ABSENT", "reason": "MONITORING_DAILY_DIAGNOSTIC_ABSENT",
                "tradingDate": None}, None
    try:
        diagnostic = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None, {"reason": "MONITORING_DAILY_DIAGNOSTIC_INVALID"}
    diagnostic_date = str(diagnostic.get("targetDate") or "").replace("-", "").replace("/", "")
    effective_date = candidate or diagnostic_date
    if not _valid_date(effective_date):
        return None, {"reason": "MONITORING_DAILY_DIAGNOSTIC_DATE_INVALID",
                      "targetDate": diagnostic.get("targetDate")}
    expected = sorted({str(code) for code in current_codes if code})
    observed = diagnostic.get("observedDates")
    invalid_lists = any(diagnostic.get(key) for key in (
        "missingCodes", "unexpectedCodes", "duplicateCodes", "mismatchCodes",
        "dateMismatchCodes"))
    valid = (
        diagnostic.get("status") == "VERIFIED"
        and diagnostic.get("source") == "NAVER_LOCAL_DATE"
        and diagnostic_date == effective_date
        and diagnostic.get("expectedCount") == len(expected)
        and diagnostic.get("observedCount") == len(expected)
        and sorted(diagnostic.get("expectedCodes") or []) == expected
        and sorted(diagnostic.get("observedCodes") or []) == expected
        and isinstance(observed, dict)
        and {str(code): str(value).replace("-", "") for code, value in observed.items()}
        == {code: effective_date for code in expected}
        and not invalid_lists
    )
    if not valid:
        return None, {"reason": "MONITORING_DAILY_DIAGNOSTIC_UNVERIFIED",
                      "status": diagnostic.get("status"),
                      "targetDate": diagnostic.get("targetDate")}
    return {"status": "VERIFIED", "reason": diagnostic.get("reason"),
            "source": diagnostic.get("source"), "tradingDate": effective_date}, None


def _verify_snapshot_dates(current_codes, current_snapshot, candidate):
    """Require one matching explicit date for every current snapshot code."""
    expected = {str(code) for code in current_codes if code}
    rows = (current_snapshot or {}).get("stocks") or []
    observed = {}
    invalid = {}
    hard_failure = False
    for row in rows:
        if not isinstance(row, dict) or not row.get("code"):
            invalid[str(row.get("code") if isinstance(row, dict) else "")] = "SNAPSHOT_ROW_INVALID"
            hard_failure = True
            continue
        code = str(row["code"])
        if code in observed or code in invalid:
            invalid[code] = "DUPLICATE_SNAPSHOT_CODE"
            hard_failure = True
            continue
        if code not in expected:
            invalid[code] = "UNEXPECTED_SNAPSHOT_CODE"
            hard_failure = True
            continue
        values = []
        for key in ("tradingDate", "tradeDate", "marketDate", "localDate", "date"):
            if row.get(key) is not None:
                values.append(str(row[key]).replace("-", "").replace("/", ""))
        if not values:
            invalid[code] = "SNAPSHOT_DATE_MISSING"
            continue
        if len(values) != 1 or values[0] != candidate or not _valid_date(values[0]):
            invalid[code] = "SNAPSHOT_DATE_MISSING_OR_MISMATCH"
            hard_failure = True
            continue
        observed[code] = candidate
    missing = sorted(expected - set(observed))
    unexpected = sorted(set(observed) - expected)
    if invalid or missing or unexpected or set(observed) != expected:
        return {"status": "UNVERIFIED", "reason": "SNAPSHOT_TRADING_DATE_EVIDENCE_INCOMPLETE",
                "observedDateCounts": dict(Counter(observed.values())),
                "missingSnapshotCodes": missing, "unexpectedSnapshotCodes": unexpected,
                "invalidSnapshotCodes": invalid, "hardFailure": hard_failure}
    return {"status": "VERIFIED", "reason": "SNAPSHOT_TRADING_DATE_EVIDENCE_COMPLETE",
            "observedDateCounts": dict(Counter(observed.values())),
            "missingSnapshotCodes": [], "unexpectedSnapshotCodes": [],
            "invalidSnapshotCodes": {}, "hardFailure": False}


def verify_monitoring_daily_final(monitoring_dir, current_codes, session_date=None,
                                  current_snapshot=None):
    """Verify an exact-date rolling FINAL state without treating it as canonical history.

    The rolling state is accepted only as an independent, explicitly named
    source.  It must cover the current authoritative code set exactly, contain
    one valid target-date row per code, and agree with an explicit date from
    the current NAVER snapshot.  No execution timestamp or local calendar
    date is used as a substitute.
    """
    codes = sorted({str(code) for code in (current_codes or []) if code})
    candidate, explicit = _session_date_evidence(session_date, current_snapshot)
    base = {"tradingDate": None, "source": "MONITORING_DAILY_FINAL",
            "observedDateCounts": {}, "missingFinalCodes": codes,
            "unexpectedFinalCodes": [], "invalidFinalCodes": {},
            "explicitSessionDates": explicit}
    if not codes:
        return {**base, "reason": "AUTHORITATIVE_CODE_SET_EMPTY"}

    diagnostic, diagnostic_error = _read_session_diagnostic(
        Path(monitoring_dir) / "trading-session-diagnostic.json", codes, candidate)
    if diagnostic_error:
        return {**base, **diagnostic_error, "diagnosticStatus": "UNVERIFIED"}
    if explicit and not candidate:
        return {**base, "reason": "INSUFFICIENT_TRADING_DATE_EVIDENCE"}
    candidate = candidate or diagnostic.get("tradingDate")
    if not candidate:
        return {**base, "reason": "INSUFFICIENT_TRADING_DATE_EVIDENCE"}
    snapshot_evidence = _verify_snapshot_dates(codes, current_snapshot, candidate)

    found = {}
    invalid = {}
    for market in ("KOSPI", "KOSDAQ"):
        payload, error = _read_final_state(Path(monitoring_dir) / (market.lower() + ".json"),
                                           market, candidate)
        if error:
            invalid[market] = error
            continue
        for stock in payload["stocks"]:
            if not isinstance(stock, dict):
                invalid[market] = {"reason": "MONITORING_DAILY_FINAL_ROW_INVALID"}
                continue
            code = str(stock.get("code") or "")
            if not code:
                invalid[market] = {"reason": "MONITORING_DAILY_FINAL_CODE_MISSING"}
                continue
            if code in found:
                invalid[code] = {"reason": "DUPLICATE_MONITORING_DAILY_FINAL_CODE"}
                continue
            columns = ("dates", "close", "high", "volume", "validity")
            if any(not isinstance(stock.get(key), list) for key in columns):
                invalid[code] = {"reason": "MONITORING_DAILY_FINAL_COLUMNS_INVALID"}
                continue
            lengths = {len(stock[key]) for key in columns}
            if len(lengths) != 1:
                invalid[code] = {"reason": "MONITORING_DAILY_FINAL_COLUMN_LENGTH_MISMATCH"}
                continue
            normalized_dates = [str(day).replace("-", "") for day in stock["dates"]]
            if (len(normalized_dates) != len(set(normalized_dates))
                    or any(not _valid_date(day) for day in normalized_dates)):
                invalid[code] = {"reason": "MONITORING_DAILY_FINAL_DATES_INVALID"}
                continue
            matches = [index for index, day in enumerate(stock["dates"])
                       if str(day).replace("-", "") == candidate]
            if len(matches) != 1:
                invalid[code] = {"reason": "MONITORING_DAILY_FINAL_TARGET_DATE_MISSING"}
                continue
            index = matches[0]
            close, high, volume = (stock[key][index] for key in ("close", "high", "volume"))
            numeric = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool)
            if (stock["validity"][index] != 0 or not numeric(close) or close <= 0
                    or not numeric(high) or high < close or not numeric(volume) or volume < 0):
                invalid[code] = {"reason": "MONITORING_DAILY_FINAL_TARGET_ROW_INVALID"}
                continue
            found[code] = candidate

    found_codes = set(found)
    missing = sorted(set(codes) - found_codes)
    unexpected = sorted(found_codes - set(codes))
    counts = dict(Counter(found.values()))
    if invalid or missing or unexpected or found_codes != set(codes):
        return {**base, "reason": "MONITORING_DAILY_FINAL_COVERAGE_INCOMPLETE",
                "observedDateCounts": counts, "missingFinalCodes": missing,
                "unexpectedFinalCodes": unexpected, "invalidFinalCodes": invalid,
                "snapshotDateEvidence": snapshot_evidence}
    if (snapshot_evidence.get("hardFailure")
            or (diagnostic.get("status") != "VERIFIED"
                and snapshot_evidence["status"] != "VERIFIED")):
        return {**base, "reason": snapshot_evidence["reason"],
                **snapshot_evidence, "diagnosticStatus": diagnostic["status"]}
    return {**base, "tradingDate": candidate,
            "reason": "VERIFIED_MONITORING_DAILY_FINAL_SESSION",
            "diagnosticStatus": diagnostic["status"],
            "diagnosticReason": diagnostic["reason"],
            "snapshotDateEvidence": snapshot_evidence,
            "observedDateCounts": counts, "missingFinalCodes": [],
            "unexpectedFinalCodes": [], "invalidFinalCodes": {}}


def verify_source_observed_monitoring_daily_final(monitoring_dir, expected_codes,
                                                  session_date=None):
    """Validate an explicit SOURCE_OBSERVED rolling FINAL date.

    This is an opt-in recovery contract for a preserved NAVER-origin state.
    It deliberately does not claim that every current snapshot row carried a
    localDate; the state files' explicit asOfDate and source-quality marker
    remain a separate, weaker evidence class.
    """
    codes = sorted({str(code) for code in (expected_codes or []) if code})
    candidate = str(session_date or "").replace("-", "").replace("/", "")
    base = {"tradingDate": None, "source": "MONITORING_DAILY_FINAL",
            "status": "UNAVAILABLE", "observedDateCounts": {},
            "missingFinalCodes": codes, "unexpectedFinalCodes": [],
            "invalidFinalCodes": {}, "evidenceClass": "SOURCE_OBSERVED"}
    if not codes or not _valid_date(candidate):
        return {**base, "reason": "SOURCE_OBSERVED_DATE_UNAVAILABLE"}

    found = {}
    invalid = {}
    declared_missing = set()
    for market in ("KOSPI", "KOSDAQ"):
        payload, error = _read_final_state(
            Path(monitoring_dir) / (market.lower() + ".json"), market, candidate
        )
        if error:
            invalid[market] = error
            continue
        if payload.get("sourceQualityStatus") != "SOURCE_OBSERVED":
            invalid[market] = {"reason": "SOURCE_OBSERVED_MARKER_MISSING",
                               "sourceQualityStatus": payload.get("sourceQualityStatus")}
            continue
        market_missing = {
            str(code) for code in (payload.get("sourceMissingCodes") or [])
            if str(code)
        }
        if not market_missing <= set(codes):
            invalid[market] = {"reason": "SOURCE_OBSERVED_MISSING_CODE_OUT_OF_SCOPE",
                               "codes": sorted(market_missing - set(codes))}
            continue
        declared_missing.update(market_missing)
        for stock in payload["stocks"]:
            code = str(stock.get("code") or "") if isinstance(stock, dict) else ""
            if not code or code in found:
                invalid[code or market] = {"reason": "DUPLICATE_OR_MISSING_SOURCE_CODE"}
                continue
            if code in market_missing:
                dates = [str(day).replace("-", "")
                         for day in (stock.get("dates") or [])]
                if candidate in dates:
                    invalid[code] = {"reason": "SOURCE_OBSERVED_MISSING_CODE_HAS_DATA"}
                continue
            columns = ("dates", "close", "high", "volume", "validity",
                       "source_valid", "source_status")
            if any(not isinstance(stock.get(key), list) for key in columns):
                invalid[code] = {"reason": "SOURCE_OBSERVED_COLUMNS_INVALID"}
                continue
            if len({len(stock[key]) for key in columns}) != 1:
                invalid[code] = {"reason": "SOURCE_OBSERVED_COLUMN_LENGTH_MISMATCH"}
                continue
            dates = [str(day).replace("-", "") for day in stock["dates"]]
            matches = [index for index, day in enumerate(dates) if day == candidate]
            if len(dates) != len(set(dates)) or any(not _valid_date(day) for day in dates):
                invalid[code] = {"reason": "SOURCE_OBSERVED_DATES_INVALID"}
                continue
            if len(matches) != 1:
                invalid[code] = {"reason": "SOURCE_OBSERVED_TARGET_DATE_MISSING"}
                continue
            index = matches[0]
            close, high, volume = (stock[key][index] for key in ("close", "high", "volume"))
            numeric = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool)
            source_valid = stock["source_valid"][index]
            source_status = stock["source_status"][index]
            if (source_valid is not True or source_status != "OBSERVED"
                    or not numeric(close) or close <= 0
                    or not numeric(volume) or volume < 0):
                invalid[code] = {"reason": "SOURCE_OBSERVED_TARGET_ROW_INVALID"}
                continue
            found[code] = candidate

    expected = set(codes)
    found_codes = set(found)
    missing = sorted(expected - found_codes)
    unexpected = sorted(found_codes - expected)
    counts = dict(Counter(found.values()))
    missing_without_declared_source = set(missing) - declared_missing
    undeclared_missing = declared_missing - set(missing)
    if undeclared_missing:
        invalid["sourceMissingCodes"] = {
            "reason": "SOURCE_OBSERVED_MISSING_CODE_HAS_DATA",
            "codes": sorted(undeclared_missing),
        }
    if invalid or missing_without_declared_source or unexpected:
        return {**base, "reason": "SOURCE_OBSERVED_FINAL_COVERAGE_INCOMPLETE",
                "observedDateCounts": counts, "missingFinalCodes": missing,
                "unexpectedFinalCodes": unexpected, "invalidFinalCodes": invalid}
    return {**base, "status": "SOURCE_OBSERVED", "tradingDate": candidate,
            "reason": "SOURCE_OBSERVED_MONITORING_DAILY_FINAL_DATE",
            "observedDateCounts": counts, "missingFinalCodes": [],
            "unexpectedFinalCodes": [], "invalidFinalCodes": {},
            "sourceMissingCodes": sorted(declared_missing)}


def verify_trading_date(history_dir, current_codes, session_date=None, current_snapshot=None):
    """Return a verified date, or null with a diagnostic reason.

    Canonical dates are accepted only when corroborated by an explicit market
    session date from the supplied snapshot.  Execution timestamps,
    generatedAt, and a date mode are never substitutes.
    """
    codes = sorted({str(code) for code in (current_codes or []) if code})
    observed, invalid = {}, {}
    for code in codes:
        latest, reason = _read_latest(Path(history_dir) / (code + ".jsonl"))
        if reason:
            invalid[code] = reason
        elif latest:
            observed[code] = latest
    candidate, explicit = _session_date_evidence(session_date, current_snapshot)
    if not candidate:
        return {"tradingDate": None, "reason": "INSUFFICIENT_TRADING_DATE_EVIDENCE",
                "observedDateCounts": dict(Counter(observed.values())),
                "missingCanonicalCodes": sorted(set(codes) - set(observed)),
                "invalidCanonicalCodes": invalid, "explicitSessionDates": sorted(set(explicit))}
    counts = Counter(observed.values())
    if candidate not in counts:
        reason = "CANONICAL_SESSION_DATE_MISMATCH"
    elif invalid or set(observed) != set(codes):
        reason = "CANONICAL_COVERAGE_INCOMPLETE"
    elif any(value != candidate for value in observed.values()):
        reason = "MIXED_CANONICAL_TRADING_DATES"
    else:
        return {"tradingDate": candidate, "reason": "VERIFIED_CANONICAL_SESSION",
                "observedDateCounts": dict(counts), "missingCanonicalCodes": [],
                "invalidCanonicalCodes": {}, "explicitSessionDates": [candidate]}
    return {"tradingDate": None, "reason": reason,
            "observedDateCounts": dict(counts),
            "missingCanonicalCodes": sorted(set(codes) - set(observed)),
            "invalidCanonicalCodes": invalid, "explicitSessionDates": [candidate]}
