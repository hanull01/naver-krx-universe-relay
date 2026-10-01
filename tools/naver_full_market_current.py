#!/usr/bin/env python3
"""Reliable current snapshot collector for NAVER's authoritative STOCK universe.

Only a complete KOSPI+KOSDAQ code-set is publishable.  Ranking-driven page
movement is handled by discarding the entire attempt and restarting it.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import ssl
import tempfile
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CONTEXT = ssl.create_default_context()


URL = "https://stock.naver.com/api/domestic/market/stock/default"
SOURCE = "NAVER_STOCKLIST_CURRENT"
SCHEMA_VERSION = 1
MARKETS = ("KOSPI", "KOSDAQ")
DEFAULT_UNIVERSE = Path("data/market-universe/all-assets.json")
DEFAULT_OUTPUT = Path("data/market/stocks-current.json")
DEFAULT_DIAGNOSTIC = Path("data/market/stocks-current-error.json")


def now_iso():
    return datetime.now().astimezone().isoformat()


def atomic_json(path, payload, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if compact:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            else:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def number(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value).replace(",", "").strip()
    try:
        parsed = float(text)
    except ValueError:
        return None
    return int(parsed) if parsed.is_integer() else parsed


def fetch_page(market, page_index, page_size=200, timeout=20):
    params = {"tradeType": "KRX", "marketType": market, "orderType": "marketSum",
              "startIdx": page_index, "pageSize": page_size}
    request = Request(URL + "?" + urlencode(params), headers={
        "User-Agent": "Mozilla/5.0 (compatible; naver-krx-universe-relay/1.0)",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://stock.naver.com/market/stock/kr/stocklist/priceTop",
    })
    with urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
        raw = response.read()
        payload = json.loads(raw.decode("utf-8"))
    if response.status != 200 or not isinstance(payload, list):
        raise RuntimeError("unexpected NAVER stocklist response")
    return payload


def load_authoritative_universe(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("assets") or payload.get("stocks")
    if not isinstance(rows, list):
        raise ValueError("authoritative universe has no assets/stocks array")
    result = {market: set() for market in MARKETS}
    for row in rows:
        if str(row.get("assetType", "STOCK")).upper() != "STOCK":
            continue
        market = str(row.get("market") or "")
        code = str(row.get("code") or "")
        if market not in result or not code:
            raise ValueError(f"invalid authoritative STOCK row: {row!r}")
        if code in result[market]:
            raise ValueError(f"duplicate authoritative code: {market}/{code}")
        result[market].add(code)
    if not all(result.values()):
        raise ValueError("authoritative universe missing KOSPI or KOSDAQ")
    return result


def normalize(row, market, snapshot_generated_at):
    code = str(row.get("itemcode") or "").strip()
    name = str(row.get("itemname") or "").strip()
    if not code or not name:
        raise ValueError("missing mandatory code or name")
    fields = {
        "currentPrice": "nowPrice", "change": "prevChangePrice", "changeRate": "prevChangeRate",
        "openPrice": "openPrice", "highPrice": "highPrice", "lowPrice": "lowPrice",
        "accumulatedTradingVolume": "tradeVolume", "accumulatedTradingValue": "tradeAmount",
        "marketCap": "marketSum", "high52Week": "week52HighPrice", "low52Week": "week52LowPrice",
        "foreignHoldingRate": "foreignRetentionRate",
    }
    item = {"code": code, "name": name, "market": market,
            "marketStatus": row.get("marketStatus"),
            "tradingSessionType": row.get("tradingSessionType"),
            "tradeStopYn": row.get("tradeStopYn"),
            "tradableStatus": row.get("tradableStatus"),
            "tradableStatusUpdatedAt": row.get("tradableStatusUpdatedAt"),
            "snapshotGeneratedAt": snapshot_generated_at}
    malformed = []
    for target, source in fields.items():
        item[target] = number(row.get(source))
        if row.get(source) not in (None, "") and item[target] is None:
            malformed.append(source)
    return item, malformed


def collect_attempt(expected, fetcher=fetch_page, page_size=200, sleep_seconds=0.15,
                    snapshot_generated_at=None):
    snapshot_generated_at = snapshot_generated_at or now_iso()
    started = time.perf_counter()
    rows, codes, duplicates, repeated_pages, errors = [], set(), [], [], []
    market_counts, malformed = {market: 0 for market in MARKETS}, 0
    page_counts, terminal = {}, {}
    for market in MARKETS:
        index, pages, signatures = 0, 0, set()
        while True:
            try:
                page = fetcher(market, index, page_size)
            except Exception as exc:
                errors.append(f"{market}[{index}]: {type(exc).__name__}")
                break
            if not isinstance(page, list):
                errors.append(f"{market}[{index}]: malformed page")
                break
            pages += 1
            signature = tuple(str(row.get("itemcode") or "") for row in page)
            if page and signature in signatures:
                repeated_pages.append(f"{market}[{index}]")
                break
            signatures.add(signature)
            if not page:
                terminal[market] = True
                break
            for raw in page:
                try:
                    item, bad_fields = normalize(raw, market, snapshot_generated_at)
                except ValueError as exc:
                    errors.append(f"{market}[{index}]: {exc}")
                    continue
                malformed += len(bad_fields)
                if item["code"] in codes:
                    duplicates.append(item["code"])
                else:
                    codes.add(item["code"])
                    rows.append(item)
                    market_counts[market] += 1
            if len(page) < page_size:
                try:
                    next_page = fetcher(market, index + 1, page_size)
                    terminal[market] = len(next_page) == 0
                    if next_page:
                        errors.append(f"{market}: short page followed by non-empty page")
                except Exception as exc:
                    terminal[market] = False
                    errors.append(f"{market}: terminal check {type(exc).__name__}")
                break
            index += 1
            if sleep_seconds:
                time.sleep(sleep_seconds)
        page_counts[market] = pages

    expected_codes = set().union(*expected.values())
    missing, extra = sorted(expected_codes - codes), sorted(codes - expected_codes)
    malformed_limit = max(10, math.ceil(len(expected_codes) * 0.01))
    valid = not (errors or duplicates or missing or extra or repeated_pages or not rows
                 or malformed > malformed_limit or not all(terminal.get(market) for market in MARKETS))
    return {
        "valid": valid, "count": len(rows), "markets": market_counts,
        "expectedCount": len(expected_codes), "expectedMarkets": {key: len(value) for key, value in expected.items()},
        "coverageCount": len(codes & expected_codes), "missingCodes": missing,
        "extraCodes": extra, "duplicateCodes": sorted(set(duplicates)),
        "repeatedPages": repeated_pages, "errors": errors, "malformedFieldCount": malformed,
        "malformedFieldLimit": malformed_limit, "pageCounts": page_counts,
        "emptyPageTermination": terminal, "elapsedSeconds": round(time.perf_counter() - started, 3),
        "stocks": sorted(rows, key=lambda item: (item["market"], item["code"])),
    }


def collect_snapshot(universe_file=DEFAULT_UNIVERSE, fetcher=fetch_page, page_size=200,
                     max_attempts=3, sleep_seconds=0.15, clock=now_iso):
    expected = load_authoritative_universe(universe_file)
    diagnostics = []
    for attempt in range(1, max_attempts + 1):
        result = collect_attempt(expected, fetcher, page_size, sleep_seconds, clock())
        diagnostics.append({key: value for key, value in result.items() if key != "stocks"} | {"attempt": attempt})
        if result["valid"]:
            return {
                "schemaVersion": SCHEMA_VERSION, "generatedAt": clock(), "source": SOURCE,
                "status": "SUCCESS", "expectedCount": result["expectedCount"], "count": result["count"],
                "coverageCount": result["coverageCount"], "markets": result["markets"],
                "attemptCount": attempt, "missingCodes": [], "extraCodes": [], "duplicateCodes": [],
                "stocks": result["stocks"], "attemptDiagnostics": diagnostics,
            }
    return {
        "schemaVersion": SCHEMA_VERSION, "generatedAt": clock(), "source": SOURCE, "status": "FAILURE",
        "expectedCount": sum(len(codes) for codes in expected.values()), "count": 0, "coverageCount": 0,
        "markets": {market: 0 for market in MARKETS}, "attemptCount": max_attempts,
        "missingCodes": [], "extraCodes": [], "duplicateCodes": [], "stocks": [],
        "attemptDiagnostics": diagnostics,
    }


def output_sizes(payload):
    pretty = len(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    compact = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    count = len(payload.get("stocks", []))
    return {"prettyBytes": pretty, "compactBytes": compact,
            "averageCompactBytesPerStock": round(compact / count, 1) if count else 0}


def publish(snapshot, output=DEFAULT_OUTPUT, diagnostic=DEFAULT_DIAGNOSTIC, compact=True):
    if snapshot["status"] == "SUCCESS":
        atomic_json(output, snapshot, compact=compact)
        return {"published": str(output), "diagnostic": None}
    # Do not overwrite the last known-good production snapshot.
    atomic_json(diagnostic, snapshot, compact=False)
    return {"published": None, "diagnostic": str(diagnostic)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe-file", default=str(DEFAULT_UNIVERSE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--diagnostic", default=str(DEFAULT_DIAGNOSTIC))
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--sleep", type=float, default=0.15)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    snapshot = collect_snapshot(args.universe_file, page_size=args.page_size,
                                max_attempts=args.max_attempts, sleep_seconds=args.sleep)
    report = output_sizes(snapshot)
    if not args.no_write:
        report |= publish(snapshot, args.output, args.diagnostic)
    print(json.dumps({key: snapshot[key] for key in ("status", "expectedCount", "count", "coverageCount", "markets", "attemptCount")} | report,
                     ensure_ascii=False))
    return 0 if snapshot["status"] == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
