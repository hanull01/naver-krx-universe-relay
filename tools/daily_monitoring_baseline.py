#!/usr/bin/env python3
"""Build an exact, compact daily feature baseline for intraday monitoring.

The baseline is derived only from confirmed canonical daily rows.  It is
deliberately read-only with respect to canonical history: intraday prices are
joined later, in memory, by the evidence builder.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import tempfile
from collections import deque
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import krx_market_day
HISTORY_DIR = ROOT / "data/naver-history/canonical/daily"
UNIVERSE_FILE = ROOT / "data/market-universe/all-assets.json"
DEFAULT_OUTPUT = ROOT / "data/monitoring-baseline/latest.json"
WINDOWS = (5, 20, 60, 120, 240)
RETURN_WINDOWS = (1, 5, 20, 60, 120, 240)
HIGH_WINDOWS = (20, 60, 120, 240)


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def mean(values):
    return round(sum(values) / len(values), 6) if values else None


def history_validity(row):
    # Rolling production state carries the canonical validity decision in a
    # compact bitmask.  Honour its decoded flags instead of re-inferring OHLC
    # validity from fields intentionally not retained in COLUMN_ARRAY_V1.
    if all(key in row for key in ("closeValid", "ohlcValid", "volumeValid")):
        return {key: bool(row[key]) for key in ("closeValid", "ohlcValid", "volumeValid")}
    close = number(row.get("close")); open_ = number(row.get("open"))
    high = number(row.get("high")); low = number(row.get("low")); volume = number(row.get("volume"))
    close_valid = close is not None and close > 0
    ohlc_valid = (bool(row.get("feature_valid", True)) and close_valid and None not in (open_, high, low)
                  and high >= max(open_, close) and low <= min(open_, close))
    volume_valid = volume is not None and volume > 0
    return {"closeValid": close_valid, "ohlcValid": ohlc_valid, "volumeValid": volume_valid}


def atomic_json(path, payload):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
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


def read_history(code, history_dir=HISTORY_DIR, max_rows=300, before_date=None):
    """Match the canonical reference reader's bounded, pre-current history."""
    try:
        # Canonical files are append-only and can be large.  Streaming the
        # tail avoids materializing the entire three-year file for every code.
        with (Path(history_dir) / f"{code}.jsonl").open(encoding="utf-8") as handle:
            lines = deque(handle, maxlen=max_rows + 1)
        rows = [json.loads(line) for line in lines if line.strip()]
        return [row for row in rows if before_date is None or str(row.get("date")) < before_date]
    except (OSError, ValueError, json.JSONDecodeError):
        return []


def asset_map(universe_file=UNIVERSE_FILE):
    payload = (universe_file if isinstance(universe_file, dict)
               else json.loads(Path(universe_file).read_text(encoding="utf-8")))
    return {str(row["code"]): row for row in payload.get("assets", []) if row.get("assetType") == "STOCK"}


def _state(values, window):
    retained = values[-(window - 1):] if window > 1 else []
    return {"count": len(retained), "sum": round(sum(retained), 6)}


def _overlay_state(values, window):
    """Compact state for replacing the already-confirmed as-of-day close."""
    retained = values[-window:-1] if len(values) >= window else []
    return {"count": len(retained), "sum": round(sum(retained), 6)}


def baseline_stock(asset, rows):
    rows = sorted(rows, key=lambda row: str(row.get("date")))
    close_values, high_values, volume_values = [], [], []
    for row in rows:
        valid = history_validity(row)
        if valid["closeValid"]:
            close_values.append(number(row.get("close")))
        if valid["ohlcValid"]:
            high_values.append(number(row.get("high")))
        if valid["volumeValid"]:
            volume_values.append(number(row.get("volume")))
    latest = rows[-1] if rows else {}
    latest_valid = history_validity(latest) if latest else {"closeValid": False, "ohlcValid": False, "volumeValid": False}
    result = {
        "code": str(asset["code"]), "name": asset.get("name"), "market": asset.get("market"),
        "lastClose": close_values[-1] if close_values else None,
        "lastTradingDate": str(latest.get("date")) if latest else None,
        "closeValid": latest_valid["closeValid"], "ohlcValid": latest_valid["ohlcValid"],
        "volumeValid": latest_valid["volumeValid"],
        "closeState": {str(window): _state(close_values, window) for window in WINDOWS},
        "overlayCloseState": {str(window): _overlay_state(close_values, window) for window in WINDOWS},
        "returnBases": {str(window): (close_values[-window] if len(close_values) >= window else None)
                        for window in RETURN_WINDOWS},
        "overlayReturnBases": {str(window): (close_values[-(window + 1)] if len(close_values) >= window + 1 else None)
                               for window in RETURN_WINDOWS},
        "rollingHighs": {str(window): (max(high_values[-window:]) if len(high_values) >= window else None)
                         for window in HIGH_WINDOWS},
        "rollingHighCounts": {str(window): min(len(high_values), window) for window in HIGH_WINDOWS},
        "overlayRollingHighs": {str(window): (max(high_values[-(window + 1):-1]) if len(high_values) >= window + 1 else None)
                                for window in HIGH_WINDOWS},
        "overlayRollingHighCounts": {str(window): (window if len(high_values) >= window + 1 else 0)
                                     for window in HIGH_WINDOWS},
        "volumeState20": {"count": min(len(volume_values), 20), "sum": round(sum(volume_values[-20:]), 6)},
        "overlayVolumeState20": {
            "count": 20 if len(volume_values) >= 21 else max(0, len(volume_values) - 1),
            "sum": round(sum(volume_values[-21:-1]), 6) if len(volume_values) >= 21 else round(sum(volume_values[:-1]), 6),
        },
    }
    for window in WINDOWS:
        result[f"ma{window}"] = mean(close_values[-window:]) if len(close_values) >= window else None
    result["previousMa20"] = result["ma20"]
    result["previousMa60"] = result["ma60"]
    result["rollingHigh20"] = result["rollingHighs"]["20"]
    result["rollingHigh60"] = result["rollingHighs"]["60"]
    result["rollingHigh120"] = result["rollingHighs"]["120"]
    result["rollingHigh240"] = result["rollingHighs"]["240"]
    result["volumeMa20"] = (round(result["volumeState20"]["sum"] / 20, 6)
                            if result["volumeState20"]["count"] == 20 else None)
    return result


def build_baseline(history_dir=HISTORY_DIR, universe_file=UNIVERSE_FILE, generated_at=None, before_date=None,
                   measure_tail=False, history_reader=None):
    assets = asset_map(universe_file)
    history_reader = history_reader or read_history
    stocks, dates, tail_rows = [], [], []
    before_date = before_date or datetime.now().astimezone().strftime("%Y%m%d")
    for code, asset in sorted(assets.items()):
        history_rows = history_reader(code, history_dir, before_date=before_date)
        row = baseline_stock(asset, history_rows)
        stocks.append(row)
        if measure_tail:
            closes = [number(item.get("close")) for item in history_rows if history_validity(item)["closeValid"]][-240:]
            highs = [number(item.get("high")) for item in history_rows if history_validity(item)["ohlcValid"]][-240:]
            volumes = [number(item.get("volume")) for item in history_rows if history_validity(item)["volumeValid"]][-20:]
            tail_rows.append({"code": code, "closeTail": closes, "highTail": highs, "volumeTail": volumes})
        if row["lastTradingDate"]:
            dates.append(row["lastTradingDate"])
    as_of = max(dates) if dates else None
    result = {
        "schemaVersion": 1, "asOfDate": as_of,
        "generatedAt": generated_at or datetime.now().astimezone().isoformat(),
        "authoritativeCount": len(assets), "count": len(stocks),
        "coveragePct": round(len(stocks) * 100 / len(assets), 4) if assets else None,
        "historyStatus": "OK" if len(stocks) == len(assets) and as_of else "PARTIAL",
        "architecture": "SCALAR_ROLLING_STATE", "stocks": stocks,
    }
    if measure_tail:
        result["_tailDesignBytes"] = len(json.dumps({"stocks": tail_rows}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return result


def tail_design_size(history_dir=HISTORY_DIR, universe_file=UNIVERSE_FILE, before_date=None):
    """Measure the exact-but-bulky tail alternative without publishing it."""
    tails = []
    before_date = before_date or datetime.now().astimezone().strftime("%Y%m%d")
    for code in asset_map(universe_file):
        rows = read_history(code, history_dir, before_date=before_date)
        closes = [number(row.get("close")) for row in rows if history_validity(row)["closeValid"]][-240:]
        highs = [number(row.get("high")) for row in rows if history_validity(row)["ohlcValid"]][-240:]
        volumes = [number(row.get("volume")) for row in rows if history_validity(row)["volumeValid"]][-20:]
        tails.append({"code": code, "closeTail": closes, "highTail": highs, "volumeTail": volumes})
    return len(json.dumps({"stocks": tails}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def baseline_sizes(baseline, history_dir=HISTORY_DIR, universe_file=UNIVERSE_FILE):
    encoded = json.dumps(baseline, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"scalarBytes": len(encoded), "scalarGzipBytes": len(gzip.compress(encoded)),
            "tailBytes": baseline.get("_tailDesignBytes")}


def refresh_baseline(target_date, output=DEFAULT_OUTPUT, history_dir=HISTORY_DIR, universe_file=UNIVERSE_FILE,
                     market_day_check=krx_market_day.market_day, generated_at=None, no_write=False,
                     history_reader=None):
    """Publish only an exact, confirmed trading-day baseline.

    No source history is changed.  A validation error leaves the previous
    latest.json untouched because atomic_json is called only after every gate.
    """
    target = date.fromisoformat(target_date) if isinstance(target_date, str) else target_date
    try:
        is_open, reason = market_day_check(target)
    except Exception as exc:
        return {"status": "NOT_READY", "reason": "KRX_CALENDAR_UNAVAILABLE", "detail": type(exc).__name__}
    if not is_open:
        return {"status": "NOT_READY", "reason": reason, "date": target.isoformat()}
    target_compact = target.strftime("%Y%m%d")
    # 'z' includes exactly target YYYYMMDD rows while excluding a future date.
    baseline = build_baseline(history_dir, universe_file, generated_at, before_date=target_compact + "z",
                              history_reader=history_reader)
    expected = set(asset_map(universe_file)); actual = {row["code"] for row in baseline["stocks"]}
    missing, extra = sorted(expected - actual), sorted(actual - expected)
    latest_missing = [row["code"] for row in baseline["stocks"] if row.get("lastTradingDate") != target_compact]
    today_rows = [row for row in baseline["stocks"] if row.get("lastTradingDate") == target_compact]
    invalid = {"close": sum(not row["closeValid"] for row in today_rows),
               "ohlc": sum(not row["ohlcValid"] for row in today_rows),
               "volume": sum(not row["volumeValid"] for row in today_rows)}
    diagnostics = {"targetDate": target.isoformat(), "authoritativeCount": len(expected), "count": baseline["count"],
                   "missingCodes": missing, "extraCodes": extra, "missingLatestCodes": latest_missing,
                   "todayRowCount": len(today_rows), "todayInvalid": invalid}
    if missing or extra or latest_missing or baseline.get("asOfDate") != target_compact or baseline.get("historyStatus") != "OK":
        return {"status": "NOT_READY", "reason": "DAILY_CANONICAL_NOT_CONFIRMED", "diagnostics": diagnostics}
    baseline["refreshDiagnostics"] = diagnostics
    baseline["generatedAt"] = generated_at or datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
    if not no_write:
        atomic_json(output, baseline)
    return {"status": "SUCCESS", "baseline": baseline, "diagnostics": diagnostics, "noWrite": no_write}


def current_validity(row):
    close = number(row.get("currentPrice")); open_ = number(row.get("openPrice"))
    high = number(row.get("highPrice")); low = number(row.get("lowPrice")); volume = number(row.get("accumulatedTradingVolume"))
    close_valid = close is not None and close > 0
    ohlc_valid = close_valid and None not in (open_, high, low) and high >= max(open_, close) and low <= min(open_, close)
    volume_valid = volume is not None and volume > 0
    return {"close_valid": close_valid, "ohlc_valid": ohlc_valid,
            "volume_valid": volume_valid, "traded_bar": close_valid and ohlc_valid and volume_valid}


def _current_ma(stock, window, price, overlay=False):
    state = stock.get("overlayCloseState" if overlay else "closeState", {}).get(str(window), {})
    if price is None or state.get("count") != window - 1:
        return None
    return round((number(state.get("sum")) + price) / window, 6)


def feature_row_from_baseline(current, stock, today, overlay=False):
    """Exactly reproduce the canonical reference feature contract for one current row."""
    validity = current_validity(current); price = number(current.get("currentPrice"))
    ma = {window: _current_ma(stock, window, price, overlay) for window in WINDOWS}
    overlay_close = stock.get("overlayCloseState", {})
    prior = {
        20: (round(number(overlay_close.get("20", {}).get("sum")) / 19, 6)
             if overlay and overlay_close.get("20", {}).get("count") == 19 else stock.get("previousMa20")),
        60: (round(number(overlay_close.get("60", {}).get("sum")) / 59, 6)
             if overlay and overlay_close.get("60", {}).get("count") == 59 else stock.get("previousMa60")),
    }
    rolling_key = "overlayRollingHighs" if overlay else "rollingHighs"
    rolling_count_key = "overlayRollingHighCounts" if overlay else "rollingHighCounts"
    rolling = {window: (stock.get(rolling_key, {}).get(str(window))
                         if stock.get(rolling_count_key, {}).get(str(window)) == window else None)
               for window in HIGH_WINDOWS}
    current_high = number(current.get("highPrice")) if validity["ohlc_valid"] else None
    volume_state = stock.get("overlayVolumeState20" if overlay else "volumeState20", {})
    volume_ma20 = (round(number(volume_state.get("sum")) / 20, 6)
                   if volume_state.get("count") == 20 else None)
    current_volume = number(current.get("accumulatedTradingVolume"))
    high52 = number(current.get("high52Week"))
    dist = lambda value: round((price / value - 1) * 100, 6) if price and value else None
    return_key = "overlayReturnBases" if overlay else "returnBases"
    ret = lambda window: (round((price / base - 1) * 100, 6)
                          if price and (base := number(stock.get(return_key, {}).get(str(window)))) else None)
    return {
        "code": current["code"], "name": current["name"], "market": current["market"],
        "currentPrice": price, "change": number(current.get("change")), "changeRate": number(current.get("changeRate")),
        **validity, **{f"ma{window}": ma[window] for window in WINDOWS},
        **{f"dist_ma{window}": dist(ma[window]) for window in WINDOWS},
        **{f"ret_{window}d": ret(window) for window in RETURN_WINDOWS},
        **{f"rolling_high_{window}": rolling[window] for window in HIGH_WINDOWS},
        **{f"dist_high_{window}": dist(rolling[window]) for window in HIGH_WINDOWS},
        "volume_ma20": volume_ma20,
        "current_volume_ratio_20": round(current_volume / volume_ma20, 6) if current_volume and volume_ma20 else None,
        "above_ma20": price > ma[20] if price and ma[20] else None,
        "above_ma60": price > ma[60] if price and ma[60] else None,
        "above_ma120": price > ma[120] if price and ma[120] else None,
        "ma20_rising": ma[20] > prior[20] if ma[20] and prior[20] else None,
        "ma60_rising": ma[60] > prior[60] if ma[60] and prior[60] else None,
        "bullish_alignment": ma[5] > ma[20] > ma[60] if all(ma[x] is not None for x in (5, 20, 60)) else None,
        "breakout_20_intraday": current_high > rolling[20] if current_high and rolling[20] else None,
        "breakout_60_intraday": current_high > rolling[60] if current_high and rolling[60] else None,
        "near_52week_high": price >= high52 * 0.98 if price and high52 else None,
        "snapshotGeneratedAt": current.get("snapshotGeneratedAt"), "temporaryCurrentBarDate": today,
        "sameDayOverlay": overlay,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="build", choices=("build", "refresh"))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--date", help="KST target date for refresh (YYYY-MM-DD)")
    args = parser.parse_args()
    if args.command == "refresh":
        target = args.date or datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
        outcome = refresh_baseline(target, args.output, no_write=args.no_write)
        print(json.dumps({key: value for key, value in outcome.items() if key != "baseline"}, ensure_ascii=False))
        return 0 if outcome["status"] == "SUCCESS" else 2
    baseline = build_baseline(measure_tail=True)
    sizes = baseline_sizes(baseline)
    baseline.pop("_tailDesignBytes", None)
    if not args.no_write:
        atomic_json(args.output, baseline)
    print(json.dumps({"asOfDate": baseline["asOfDate"], "count": baseline["count"], "sizes": sizes}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
