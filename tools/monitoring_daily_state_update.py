"""Safe update/read support for compact COLUMN_ARRAY_V1 daily state.

This module deliberately accepts a complete, already-collected daily input.
Collection/retry policy belongs to the future workflow, while this layer owns
strict coverage validation and never publishes a half-market update.
"""
from __future__ import annotations

import json
import os
import tempfile
import time as time_module
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

# Match daily_monitoring_baseline.read_history(max_rows=300) exactly.
KEEP = 301
FORMAT = "COLUMN_ARRAY_V1"
MARKETS = ("KOSPI", "KOSDAQ")
KST = ZoneInfo("Asia/Seoul")
REGULAR_CLOSE_KST = time(15, 30)
COLLECT_MAX_WORKERS = 4
COLLECT_MAX_WORKERS_LIMIT = 8
COLLECT_RETRIES = 3
COLLECT_TIMEOUT_SECONDS = 30
COLLECT_RETRY_SLEEP_SECONDS = 0.5


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


class UniverseHistoryRequired(ValueError):
    """A newly listed stock needs an explicit historical-state bootstrap."""

    def __init__(self, diagnostics):
        self.diagnostics = diagnostics
        super().__init__("UNIVERSE_HISTORY_REQUIRED")


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


def _universe_payload(universe):
    if isinstance(universe, dict):
        return universe
    return json.loads(Path(universe).read_text(encoding="utf-8"))


def authoritative_groups(universe):
    assets = _universe_payload(universe).get("assets", [])
    groups = {market: {} for market in MARKETS}; seen = set()
    for asset in assets:
        if asset.get("assetType", "STOCK") != "STOCK": continue
        code, market = str(asset.get("code")), asset.get("market")
        if not code or market not in groups or code in seen: raise ValueError("invalid authoritative stock universe")
        seen.add(code); groups[market][code] = asset
    if not all(groups.values()): raise ValueError("empty authoritative market")
    return groups


def refresh_stock_universe(previous_universe, stock_payload, target_date):
    """Replace only STOCK rows after validating an exact NAVER stocklist result."""
    previous = _universe_payload(previous_universe)
    metadata = stock_payload.get("metadata") or {}
    stocks = stock_payload.get("stocks")
    if not isinstance(stocks, list) or metadata.get("failedCount") != 0:
        raise ValueError("authoritative stock source incomplete")
    source_counts = metadata.get("markets") or {}
    if any(source_counts.get(market) != sum(row.get("market") == market for row in stocks)
           for market in MARKETS):
        raise ValueError("authoritative stock source market count mismatch")

    old_groups = authoritative_groups(previous)
    current = {market: {} for market in MARKETS}
    for row in stocks:
        code, market = str(row.get("code") or ""), row.get("market")
        if not code or market not in current or code in current[market]:
            raise ValueError("invalid current authoritative stock universe")
        current[market][code] = row
    if not all(current.values()):
        raise ValueError("empty current authoritative market")
    old_codes = set().union(*(set(group) for group in old_groups.values()))
    new_codes = set().union(*(set(group) for group in current.values()))
    removed_codes, added_codes = sorted(old_codes - new_codes), sorted(new_codes - old_codes)
    market_changed = sorted(code for code in old_codes & new_codes
                            if next(m for m in MARKETS if code in old_groups[m]) !=
                               next(m for m in MARKETS if code in current[m]))
    old_assets = {str(row.get("code")): row for row in previous.get("assets", [])
                  if row.get("assetType", "STOCK") == "STOCK"}
    diagnostics = {
        "date": str(target_date), "status": "UNIVERSE_HISTORY_REQUIRED" if added_codes or market_changed else "READY",
        "previousCount": len(old_codes), "currentCount": len(new_codes),
        "previousMarketCounts": {market: len(old_groups[market]) for market in MARKETS},
        "currentMarketCounts": {market: len(current[market]) for market in MARKETS},
        "removed": [{"code": code, "name": old_assets.get(code, {}).get("name"),
                     "reason": "REMOVED_FROM_AUTHORITATIVE_SOURCE"} for code in removed_codes],
        "added": [{"code": code, "name": next(current[m][code].get("name") for m in MARKETS if code in current[m]),
                   "reason": "HISTORY_BOOTSTRAP_REQUIRED"} for code in added_codes],
        "marketChanged": market_changed,
    }
    if added_codes or market_changed:
        raise UniverseHistoryRequired(diagnostics)
    if not removed_codes:
        # A same-set refresh is a lifecycle NOOP.  Preserve the committed
        # universe bytes so a same-day rerun does not create metadata-only
        # publications or break the atomic five-artifact whitelist.
        return previous, diagnostics
    nonstocks = [row for row in previous.get("assets", []) if row.get("assetType", "STOCK") != "STOCK"]
    new_stocks = sorted(stocks, key=lambda row: (row.get("market") or "", str(row.get("code") or "")))
    output = dict(previous)
    output["assets"] = new_stocks + nonstocks
    output_metadata = dict(previous.get("metadata") or {})
    counts = dict(output_metadata.get("counts") or {})
    counts.update({"KOSPI": len(current["KOSPI"]), "KOSDAQ": len(current["KOSDAQ"]),
                   "STOCK": len(new_codes)})
    output_metadata.update({"generatedAt": metadata.get("generatedAt") or datetime.now(KST).isoformat(),
                            "counts": counts, "stockSource": metadata, "lifecycle": diagnostics})
    output["metadata"] = output_metadata
    return output, diagnostics


def reconcile_authoritative_universe(existing, previous_universe, current_universe, diagnostics):
    """Remove retired stocks while preserving every unchanged stock history byte-for-byte."""
    previous_groups = authoritative_groups(previous_universe)
    current_groups = authoritative_groups(current_universe)
    previous_codes = set().union(*(set(group) for group in previous_groups.values()))
    current_codes = set().union(*(set(group) for group in current_groups.values()))
    if current_codes - previous_codes:
        raise UniverseHistoryRequired(diagnostics)
    output = {}
    for market in MARKETS:
        payload = dict(existing[market])
        by_code = {str(stock.get("code")): stock for stock in payload["stocks"]}
        if set(by_code) != set(previous_groups[market]):
            raise ValueError(f"previous authoritative code mismatch: {market}")
        payload["stocks"] = [by_code[code] for code in sorted(current_groups[market])]
        output[market] = payload
    return output


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


def collect_today_rows(target_date, universe_file, fetcher=None, *,
                       max_workers=COLLECT_MAX_WORKERS,
                       retries=COLLECT_RETRIES,
                       timeout=COLLECT_TIMEOUT_SECONDS,
                       retry_sleep=COLLECT_RETRY_SLEEP_SECONDS):
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
    if not isinstance(max_workers, int) or not 1 <= max_workers <= COLLECT_MAX_WORKERS_LIMIT:
        raise ValueError(f"max_workers must be between 1 and {COLLECT_MAX_WORKERS_LIMIT}")
    if not isinstance(retries, int) or retries < 1:
        raise ValueError("retries must be a positive integer")
    if retry_sleep < 0:
        raise ValueError("retry_sleep must be non-negative")
    source_fetcher = fetcher
    groups = authoritative_groups(universe_file)
    targets = sorted((code, market) for market, assets in groups.items() for code in assets)

    def fetch_one(code, market):
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                if source_fetcher is None:
                    response = daily.fetch(code, target_dt, target_dt, timeout=timeout,
                                           retries=1, sleep=0)
                else:
                    response = source_fetcher(code, target_dt, target_dt)
            except Exception as error:
                last_error = error
                if attempt < retries:
                    if retry_sleep:
                        time_module.sleep(retry_sleep)
                    continue
                raise RuntimeError(f"daily fetch failed after {retries} attempts: {code}") from error
            payload = json.loads(response["raw"].decode("utf-8"))
            rows = payload.get("priceInfos", payload.get("data", [])) if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                raise ValueError(f"invalid daily response collection: {code}")
            candidates = [row for row in rows if compact_date(row.get("localDate")) == target]
            if not candidates:
                if attempt < retries:
                    if retry_sleep:
                        time_module.sleep(retry_sleep)
                    continue
                return code, None
            if len(candidates) != 1:
                raise ValueError(f"duplicate today daily row: {code}")
            row = candidates[0]
            reasons = daily.row_validation_reasons(row)
            return code, {"code": code, "date": target, "close": row.get("closePrice"),
                          "high": row.get("highPrice"), "volume": row.get("accumulatedTradingVolume"),
                          "closeValid": not any("close" in item for item in reasons),
                          "ohlcValid": not reasons,
                          "volumeValid": not any("volume" in item for item in reasons),
                          "market": market}
        raise RuntimeError(f"daily fetch failed without result: {code}") from last_error

    collected = {}
    worker_count = min(max_workers, len(targets))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(fetch_one, code, market): code for code, market in targets}
        for future in as_completed(futures):
            code, row = future.result()
            if code in collected:
                raise ValueError(f"duplicate collected daily code: {code}")
            collected[code] = row
    missing = [code for code, _market in targets if collected.get(code) is None]
    if missing:
        raise TodayRowsNotReady(target, missing)
    expected = {code for code, _market in targets}
    if set(collected) != expected:
        raise ValueError("collected authoritative code mismatch")
    output = [collected[code] for code in sorted(collected)]
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


def update_with_universe_refresh(state_dir, universe_file, baseline_output, lifecycle_output,
                                 target_date, stock_payload, fetcher=None, no_write=False,
                                 **collection_options):
    """Reconcile retired stocks and publish one exact universe/state/baseline generation."""
    previous_universe = _universe_payload(universe_file)
    existing = load_state(state_dir, previous_universe)
    current_universe, diagnostics = refresh_stock_universe(
        previous_universe, stock_payload, target_date)
    reconciled = reconcile_authoritative_universe(
        existing, previous_universe, current_universe, diagnostics)
    incoming = collect_today_rows(target_date, current_universe, fetcher=fetcher,
                                  **collection_options)
    updated, changed = update_state(reconciled, incoming, target_date, current_universe)
    baseline = baseline_from_state(updated, target_date, current_universe, baseline_output)
    groups = authoritative_groups(current_universe)
    expected = set().union(*(set(group) for group in groups.values()))
    state_codes = {str(stock["code"]) for market in MARKETS for stock in updated[market]["stocks"]}
    baseline_codes = {str(stock["code"]) for stock in baseline["stocks"]}
    if expected != state_codes or expected != baseline_codes:
        raise ValueError("atomic authoritative set validation failed")
    baseline.setdefault("refreshDiagnostics", {})["universeLifecycle"] = diagnostics
    publication_changed = changed or bool(diagnostics["removed"])
    if publication_changed and not no_write:
        transactional_publish({
            Path(universe_file): current_universe,
            Path(state_dir) / "kospi.json": updated["KOSPI"],
            Path(state_dir) / "kosdaq.json": updated["KOSDAQ"],
            Path(baseline_output): baseline,
            Path(lifecycle_output): diagnostics,
        })
    return {"status": "SUCCESS", "changed": publication_changed,
            "asOfDate": compact_date(target_date), "coverage": len(expected),
            "lifecycle": diagnostics, "baseline": baseline}
