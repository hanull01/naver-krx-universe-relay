#!/usr/bin/env python3
"""Persist candidate snapshots and evaluate their next KRX session.

Candidate-date files are immutable inputs.  Evaluation reads those snapshots;
it never rebuilds candidates from current report/state data.
"""

import argparse
import json
import statistics
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import relay
from krx_market_day import CalendarUnavailable, krx_holidays

KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parent
REPORT_PATH = ROOT / "data" / "reports" / "latest.json"
CANDIDATE_ROOT = ROOT / "data" / "trade-candidates"
PERFORMANCE_ROOT = ROOT / "data" / "trade-candidate-performance"
TARGET_MINUTES = {
    "1m": "090000",
    "3m": "090200",
    "5m": "090400",
    "10m": "090900",
    "30m": "092900",
}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def next_krx_trading_day(candidate_date, holiday_loader=krx_holidays):
    current = date.fromisoformat(candidate_date) + timedelta(days=1)
    holiday_cache = {}
    for _ in range(31):
        if current.weekday() < 5:
            if current.year not in holiday_cache:
                holiday_cache[current.year] = holiday_loader(current.year)
            if current.isoformat() not in holiday_cache[current.year]:
                return current
        current += timedelta(days=1)
    raise CalendarUnavailable("next KRX trading day was not found within 31 days")


def build_candidate_snapshot(report):
    source = report.get("nextSessionCandidates")
    if not isinstance(source, dict) or source.get("status") not in ("AVAILABLE", "PARTIAL"):
        raise ValueError("next-session candidates are unavailable")
    candidates = []
    fields = (
        "rank", "itemCode", "itemName", "primaryBucket", "allBuckets",
        "breakoutStrength", "regularSession", "current", "afterSession",
        "volume", "tags", "metadata",
    )
    for row in source.get("priority", []):
        if not isinstance(row, dict):
            raise ValueError("candidate row is invalid")
        candidates.append({key: deepcopy(row.get(key)) for key in fields if key in row})
    if len(candidates) != source.get("candidateCount"):
        raise ValueError("candidate count mismatch")
    return {
        "schemaVersion": 1,
        "asOf": source.get("asOf") or report.get("asOf"),
        "generatedAt": report.get("generatedAt"),
        "candidateCount": len(candidates),
        "candidates": candidates,
        "methodology": {
            "immutableCandidateDateSnapshot": True,
            "source": "data/reports/latest.json#nextSessionCandidates",
            "technicalBasis": "REGULAR_SESSION",
            "volumeBasis": "CURRENT_DAILY_TECHNICAL",
            "afterBasis": "CLOSED_REFERENCE",
        },
    }


def persist_candidate_snapshot(report, root=CANDIDATE_ROOT):
    snapshot = build_candidate_snapshot(report)
    root = Path(root)
    dated = root / f"{snapshot['asOf']}.json"
    if dated.exists() and load_json(dated) != snapshot:
        raise ValueError(f"immutable candidate snapshot differs: {dated}")
    if not dated.exists():
        write_json(dated, snapshot)
    write_json(root / "latest.json", snapshot)
    return snapshot


def naver_minute_rows(code, evaluation_date, fetcher=relay.fetch):
    day = evaluation_date.replace("-", "")
    url = (relay.MINUTE_URL.format(code=code)
           + f"?startDateTime={day}0900&endDateTime={day}1530")
    payload = fetcher(url)
    rows = payload if isinstance(payload, list) else payload.get("datas", [])
    if not isinstance(rows, list):
        raise ValueError("minute endpoint did not return array")
    return rows


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def pct(value, basis):
    if value is None or basis in (None, 0):
        return None
    return round((value - basis) / basis * 100, 6)


def normalized_minutes(rows, evaluation_date):
    prefix = evaluation_date.replace("-", "")
    result = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        stamp = str(row.get("localDateTime") or "")
        if not stamp.startswith(prefix) or len(stamp) != 14:
            continue
        parsed = {
            "open": number(row.get("openPrice")),
            "high": number(row.get("highPrice")),
            "low": number(row.get("lowPrice")),
            "close": number(row.get("currentPrice")),
        }
        if all(value is not None for value in parsed.values()):
            result[stamp] = parsed
    return result


def evaluate_candidate(candidate, evaluation_date, rows, pending=False):
    regular_close = number((candidate.get("regularSession") or {}).get("price"))
    minutes = normalized_minutes(rows, evaluation_date)
    prefix = evaluation_date.replace("-", "")
    open_row = minutes.get(prefix + "090000")
    open_price = open_row["open"] if open_row else None
    prices = {
        label: (minutes.get(prefix + suffix) or {}).get("close")
        for label, suffix in TARGET_MINUTES.items()
    }
    close_row = minutes.get(prefix + "153000")
    complete = regular_close is not None and open_price is not None and close_row is not None and all(
        value is not None for value in prices.values()
    )
    if pending and not minutes:
        status = "PENDING"
    elif not minutes:
        status = "UNAVAILABLE"
    elif complete:
        status = "COMPLETE"
    else:
        status = "PARTIAL"

    highs = [row["high"] for row in minutes.values()]
    lows = [row["low"] for row in minutes.values()]
    performance = {
        "openPrice": open_price,
        "gapFromRegularClosePct": pct(open_price, regular_close),
    }
    for label in TARGET_MINUTES:
        suffix = label[:-1]
        price = prices[label]
        performance[f"priceAt{label}"] = price
        performance[f"return{suffix}mFromOpenPct"] = pct(price, open_price)
        performance[f"return{suffix}mFromRegularClosePct"] = pct(price, regular_close)
    performance.update({
        "highPrice": max(highs) if highs else None,
        "lowPrice": min(lows) if lows else None,
        "closePrice": close_row["close"] if close_row else None,
        "mfeFromOpenPct": pct(max(highs), open_price) if highs else None,
        "maeFromOpenPct": pct(min(lows), open_price) if lows else None,
        "closeReturnFromOpenPct": pct(close_row["close"], open_price) if close_row else None,
        "closeReturnFromRegularClosePct": pct(close_row["close"], regular_close) if close_row else None,
    })
    return {
        "candidateAsOf": candidate.get("candidateAsOf"),
        "evaluationDate": evaluation_date,
        "itemCode": candidate.get("itemCode"),
        "itemName": candidate.get("itemName"),
        "candidateRank": candidate.get("rank"),
        "primaryBucket": candidate.get("primaryBucket"),
        "allBuckets": deepcopy(candidate.get("allBuckets") or []),
        "candidateContext": {
            "regularClose": regular_close,
            "sessionChangeRate": (candidate.get("afterSession") or {}).get("sessionChangeRate"),
            "volumeState": (candidate.get("volume") or {}).get("volumeState"),
            "breakoutStrength": candidate.get("breakoutStrength"),
        },
        "performance": performance,
        "status": status,
    }


def evaluate_snapshot(snapshot, minute_loader, evaluation_date=None, now=None):
    evaluation = evaluation_date or next_krx_trading_day(snapshot["asOf"]).isoformat()
    current = now or datetime.now(KST)
    pending = current.date() < date.fromisoformat(evaluation) or (
        current.date() == date.fromisoformat(evaluation) and current.time() < time(9, 0)
    )
    results = []
    for source in snapshot.get("candidates", []):
        candidate = deepcopy(source)
        candidate["candidateAsOf"] = snapshot["asOf"]
        source_error = None
        try:
            rows = [] if pending else minute_loader(candidate["itemCode"], evaluation)
        except Exception as exc:
            rows = []
            source_error = type(exc).__name__
        result = evaluate_candidate(candidate, evaluation, rows, pending=pending)
        if source_error:
            result["sourceError"] = source_error
        results.append(result)
    statuses = {row["status"] for row in results}
    if statuses == {"COMPLETE"}:
        status = "COMPLETE"
    elif statuses == {"PENDING"} or (not results and pending):
        status = "PENDING"
    elif statuses == {"UNAVAILABLE"}:
        status = "UNAVAILABLE"
    else:
        status = "PARTIAL"
    return {
        "schemaVersion": 1,
        "candidateAsOf": snapshot["asOf"],
        "evaluationDate": evaluation,
        "status": status,
        "candidateCount": snapshot.get("candidateCount", len(results)),
        "results": results,
        "methodology": {
            "minuteSource": "NAVER_MINUTE",
            "openPrice": "09:00 minute-bar open",
            "priceAt1m": "09:00 minute-bar close",
            "priceAt3m": "09:02 minute-bar close",
            "priceAt5m": "09:04 minute-bar close",
            "priceAt10m": "09:09 minute-bar close",
            "priceAt30m": "09:29 minute-bar close",
            "missingMinutePolicy": "NULL_NO_FALLBACK",
            "winDefinition": "return > 0",
            "tradingCostsApplied": False,
        },
    }


def metric_summary(values, win_rate=True):
    observed = [value for value in values if value is not None]
    result = {
        "observedCount": len(observed),
        "mean": round(statistics.fmean(observed), 6) if observed else None,
        "median": round(statistics.median(observed), 6) if observed else None,
    }
    if win_rate:
        positive = sum(value > 0 for value in observed)
        result.update({
            "positiveCount": positive,
            "winRate": round(positive / len(observed) * 100, 6) if observed else None,
        })
    return result


def summarize_rows(rows):
    metrics = {
        "1m": "return1mFromOpenPct", "3m": "return3mFromOpenPct",
        "5m": "return5mFromOpenPct", "10m": "return10mFromOpenPct",
        "30m": "return30mFromOpenPct", "close": "closeReturnFromOpenPct",
    }
    summary = {"sampleCount": len(rows)}
    for name, field in metrics.items():
        summary[name] = metric_summary([row["performance"].get(field) for row in rows])
    summary["MFE"] = metric_summary(
        [row["performance"].get("mfeFromOpenPct") for row in rows], False
    )
    summary["MAE"] = metric_summary(
        [row["performance"].get("maeFromOpenPct") for row in rows], False
    )
    return summary


def build_summary(artifact):
    primary = {}
    memberships = {}
    for row in artifact.get("results", []):
        primary.setdefault(row["primaryBucket"], []).append(row)
        for bucket in row.get("allBuckets", []):
            memberships.setdefault(bucket, []).append(row)
    return {
        "schemaVersion": 1,
        "candidateAsOf": artifact["candidateAsOf"],
        "evaluationDate": artifact["evaluationDate"],
        "status": artifact["status"],
        "summaryByPrimaryBucket": {
            name: summarize_rows(primary[name]) for name in sorted(primary)
        },
        "summaryByAllBuckets": {
            name: summarize_rows(memberships[name]) for name in sorted(memberships)
        },
        "methodology": {
            "winDefinition": "return > 0; zero is non-win",
            "tradingCostsApplied": False,
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-date")
    parser.add_argument("--snapshot-report", type=Path)
    parser.add_argument("--snapshot-only", action="store_true")
    parser.add_argument("--candidate-root", type=Path, default=CANDIDATE_ROOT)
    parser.add_argument("--performance-root", type=Path, default=PERFORMANCE_ROOT)
    args = parser.parse_args(argv)

    if args.snapshot_report:
        snapshot = persist_candidate_snapshot(load_json(args.snapshot_report), args.candidate_root)
    else:
        candidate_date = args.candidate_date
        if not candidate_date:
            parser.error("--candidate-date is required unless --snapshot-report is used")
        snapshot = load_json(args.candidate_root / f"{candidate_date}.json")
    if args.snapshot_only:
        return 0

    artifact = evaluate_snapshot(snapshot, naver_minute_rows)
    root = args.performance_root
    write_json(root / f"{snapshot['asOf']}.json", artifact)
    write_json(root / "latest.json", artifact)
    write_json(root / "summary.json", build_summary(artifact))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
