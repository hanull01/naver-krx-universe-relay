"""Safe update/read support for compact COLUMN_ARRAY_V1 daily state.

This module deliberately accepts a complete, already-collected daily input.
Collection/retry policy belongs to the future workflow, while this layer owns
strict coverage validation and never publishes a half-market update.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

# Match daily_monitoring_baseline.read_history(max_rows=300) exactly.
KEEP = 301
FORMAT = "COLUMN_ARRAY_V1"
MARKETS = ("KOSPI", "KOSDAQ")
KST = ZoneInfo("Asia/Seoul")
REGULAR_CLOSE_KST = time(15, 30)


class TodayRowsNotReady(ValueError):
    """The authoritative response is structurally valid but incomplete."""

    def __init__(self, target_date, missing_codes):
        self.target_date = compact_date(target_date)
        self.missing_codes = tuple(sorted(missing_codes))
        self.diagnostics = {
            "status": "NOT_READY",
            "targetDate": self.target_date,
            "missingTodayCount": len(self.missing_codes),
            "firstMissingCodes": list(self.missing_codes[:10]),
        }
        super().__init__(
            f"today daily rows unavailable: missingTodayCount={len(self.missing_codes)}, "
            f"firstMissingCodes={','.join(self.missing_codes[:10])}"
        )


def closing_window(now=None):
    """Return the KST-only eligibility decision for a same-day FINAL update."""
    if now is None:
        now = datetime.now(KST)
    elif now.tzinfo is None:
        raise ValueError("closing-window time must be timezone-aware")
    else:
        now = now.astimezone(KST)
    after_close = now.time() >= REGULAR_CLOSE_KST
    return {
        "status": "AFTER_REGULAR_CLOSE" if after_close else "BEFORE_REGULAR_CLOSE",
        "eligible": after_close,
        "nowKst": now.isoformat(),
        "targetDate": now.date().isoformat(),
        "regularCloseKst": "15:30",
    }


def compact_date(value):
    text = str(value or "").replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"invalid daily date: {value!r}")
    return text


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def _temp(path, content):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    with os.fdopen(fd, "wb") as handle:
        handle.write(content); handle.flush(); os.fsync(handle.fileno())
    return Path(name)


def transactional_publish(payloads):
    """Validate/write all files before replace; restore bytes on replacement error."""
    items = [(Path(path), _encode(value)) for path, value in payloads.items()]
    temps = [(path, _temp(path, content)) for path, content in items]
    previous = {path: path.read_bytes() if path.exists() else None for path, _ in items}
    replaced = []
    try:
        for path, tmp in temps:
            os.replace(tmp, path); replaced.append(path)
    except Exception:
        for path in reversed(replaced):
            if previous[path] is None:
                try: path.unlink()
                except FileNotFoundError: pass
            else:
                os.replace(_temp(path, previous[path]), path)
        raise
    finally:
        for _, tmp in temps:
            try: tmp.unlink()
            except FileNotFoundError: pass


def flags(mask):
    if not isinstance(mask, int) or not 0 <= mask <= 7:
        raise ValueError(f"invalid validity bitmask: {mask!r}")
    return {"closeValid": not bool(mask & 1), "ohlcValid": not bool(mask & 2), "volumeValid": not bool(mask & 4)}


def mask(row):
    return (0 if row["closeValid"] else 1) | (0 if row["ohlcValid"] else 2) | (0 if row["volumeValid"] else 4)


def decode_stock(stock):
    keys = ("dates", "close", "high", "volume", "validity")
    if not stock.get("code") or any(not isinstance(stock.get(key), list) for key in keys):
        raise ValueError("malformed COLUMN_ARRAY_V1 stock")
    if len({len(stock[key]) for key in keys}) != 1:
        raise ValueError(f"column length mismatch: {stock['code']}")
    dates = [compact_date(value) for value in stock["dates"]]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError(f"invalid date sequence: {stock['code']}")
    return [{"date": day, "close": stock["close"][index], "high": stock["high"][index],
             "volume": stock["volume"][index], **flags(stock["validity"][index])}
            for index, day in enumerate(dates)]


def encode_stock(code, rows):
    return {"code": str(code), "dates": [row["date"] for row in rows], "close": [row["close"] for row in rows],
            "high": [row["high"] for row in rows], "volume": [row["volume"] for row in rows],
            "validity": [mask(row) for row in rows]}


def authoritative_groups(universe_file):
    assets = json.loads(Path(universe_file).read_text(encoding="utf-8")).get("assets", [])
    groups = {market: {} for market in MARKETS}; seen = set()
    for asset in assets:
        if asset.get("assetType", "STOCK") != "STOCK": continue
        code, market = str(asset.get("code")), asset.get("market")
        if not code or market not in groups or code in seen: raise ValueError("invalid authoritative stock universe")
        seen.add(code); groups[market][code] = asset
    if not all(groups.values()): raise ValueError("empty authoritative market")
    return groups


def load_state(state_dir, universe_file):
    expected = authoritative_groups(universe_file); loaded = {}
    for market in MARKETS:
        path = Path(state_dir) / f"{market.lower()}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schemaVersion") != 1 or payload.get("format") != FORMAT or payload.get("market") != market:
            raise ValueError(f"invalid state header: {path.name}")
        stocks = payload.get("stocks")
        if not isinstance(stocks, list): raise ValueError(f"invalid stocks: {path.name}")
        codes = [str(item.get("code")) for item in stocks]
        if len(codes) != len(set(codes)) or set(codes) != set(expected[market]):
            raise ValueError(f"authoritative code mismatch: {path.name}")
        for stock in stocks:
            if len(decode_stock(stock)) > KEEP: raise ValueError(f"retention overflow: {stock['code']}")
        loaded[market] = payload
    return loaded


def normalize_daily_rows(payload, target_date):
    source = payload.get("rows", payload) if isinstance(payload, dict) else payload
    if not isinstance(source, list): raise ValueError("daily input rows must be a list")
    target = compact_date(target_date); rows = {}
    for raw in source:
        if not isinstance(raw, dict) or not raw.get("code"): raise ValueError("malformed daily row")
        code = str(raw["code"])
        if code in rows: raise ValueError(f"duplicate daily code: {code}")
        if compact_date(raw.get("date")) != target: raise ValueError(f"daily date mismatch: {code}")
        def positive(name):
            value = raw.get(name)
            return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
        close_valid = bool(raw.get("closeValid", positive("close")))
        ohlc_valid = bool(raw.get("ohlcValid", close_valid and positive("high") and raw["high"] >= raw["close"]))
        volume_valid = bool(raw.get("volumeValid", positive("volume")))
        rows[code] = {"date": target, "close": raw.get("close"), "high": raw.get("high"), "volume": raw.get("volume"),
                      "closeValid": close_valid, "ohlcValid": ohlc_valid, "volumeValid": volume_valid}
    return rows


def collect_today_rows(target_date, universe_file, fetcher=None):
    """Collect one observed daily row per authoritative code from NAVER.

    This is intentionally a bounded source adapter, not a publisher.  A
    missing today's row in even one response fails the complete collection;
    row-level OHLC/volume quality remains represented by validity flags.
    """
    import sys
    tool_dir = str(Path(__file__).parent)
    if tool_dir not in sys.path:
        sys.path.insert(0, tool_dir)
    import naver_daily_backfill as daily
    target = compact_date(target_date)
    target_dt = datetime.strptime(target, "%Y%m%d")
    fetcher = fetcher or daily.fetch
    output, missing = [], []
    for market, assets in authoritative_groups(universe_file).items():
        for code in sorted(assets):
            response = fetcher(code, target_dt, target_dt)
            payload = json.loads(response["raw"].decode("utf-8"))
            rows = payload.get("priceInfos", payload.get("data", [])) if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                raise ValueError(f"invalid daily response collection: {code}")
            candidates = [row for row in rows if compact_date(row.get("localDate")) == target]
            if not candidates:
                missing.append(code)
                continue
            if len(candidates) != 1:
                raise ValueError(f"duplicate today daily row: {code}")
            row = candidates[0]
            reasons = daily.row_validation_reasons(row)
            output.append({"code": code, "date": target, "close": row.get("closePrice"), "high": row.get("highPrice"),
                           "volume": row.get("accumulatedTradingVolume"), "closeValid": not any("close" in item for item in reasons),
                           "ohlcValid": not reasons, "volumeValid": not any("volume" in item for item in reasons),
                           "market": market})
    if missing:
        raise TodayRowsNotReady(target, missing)
    return normalize_daily_rows({"rows": output}, target)


def update_state(existing, daily_rows, target_date, universe_file):
    target = compact_date(target_date); groups = authoritative_groups(universe_file)
    expected = set().union(*(set(group) for group in groups.values()))
    if set(daily_rows) != expected:
        raise ValueError("daily authoritative code mismatch")
    output, changed = {}, False
    for market in MARKETS:
        prior = existing[market]; prior_final = prior.get("publicationStatus", "FINAL") == "FINAL"; stocks = []
        for old_stock in prior["stocks"]:
            code = str(old_stock["code"]); rows = decode_stock(old_stock); incoming = daily_rows[code]
            matching = next((index for index, row in enumerate(rows) if row["date"] == target), None)
            if matching is None:
                rows.append(incoming); changed = True
            elif rows[matching] == incoming:
                pass
            elif not prior_final:
                rows[matching] = incoming; changed = True
            else:
                raise ValueError(f"final daily conflict: {code} {target}")
            rows.sort(key=lambda row: row["date"])
            stocks.append(encode_stock(code, rows[-KEEP:]))
        output[market] = {"schemaVersion": 1, "format": FORMAT, "market": market, "retainedObservations": KEEP,
                          "publicationStatus": "FINAL", "asOfDate": target, "stocks": sorted(stocks, key=lambda row: row["code"])}
    return output, changed


def state_history_reader(state):
    by_code = {stock["code"]: decode_stock(stock) for market in MARKETS for stock in state[market]["stocks"]}
    def reader(code, _history_dir=None, max_rows=300, before_date=None):
        rows = by_code.get(str(code), [])
        rows = [row for row in rows if before_date is None or row["date"] < before_date]
        # Mirror daily_monitoring_baseline.read_history exactly: its deque
        # keeps max_rows + 1 before a potential before_date exclusion.
        return rows[-(max_rows + 1):]
    return reader


def baseline_from_state(state, target_date, universe_file, output, generated_at=None):
    from daily_monitoring_baseline import refresh_baseline
    outcome = refresh_baseline(target_date, output=output, universe_file=universe_file, generated_at=generated_at,
                               history_reader=state_history_reader(state), no_write=True)
    if outcome.get("status") != "SUCCESS":
        raise ValueError("baseline validation failed: " + str(outcome.get("reason")))
    baseline = outcome["baseline"]; groups = authoritative_groups(universe_file)
    counts = {market: sum(item.get("market") == market for item in baseline["stocks"]) for market in MARKETS}
    if counts != {market: len(groups[market]) for market in MARKETS}:
        raise ValueError("baseline market coverage mismatch")
    return baseline


def update_from_file(state_dir, daily_input, target_date, universe_file, baseline_output, no_write=False):
    existing = load_state(state_dir, universe_file)
    incoming = normalize_daily_rows(json.loads(Path(daily_input).read_text(encoding="utf-8")), target_date)
    return update_from_rows(existing, incoming, state_dir, target_date, universe_file, baseline_output, no_write)


def update_from_rows(existing, incoming, state_dir, target_date, universe_file, baseline_output, no_write=False):
    updated, changed = update_state(existing, incoming, target_date, universe_file)
    baseline = baseline_from_state(updated, target_date, universe_file, baseline_output)
    if changed and not no_write:
        transactional_publish({Path(state_dir) / "kospi.json": updated["KOSPI"], Path(state_dir) / "kosdaq.json": updated["KOSDAQ"],
                               Path(baseline_output): baseline})
    return {"status": "SUCCESS", "changed": changed, "asOfDate": compact_date(target_date),
            "coverage": sum(len(value["stocks"]) for value in updated.values()), "baseline": baseline}
