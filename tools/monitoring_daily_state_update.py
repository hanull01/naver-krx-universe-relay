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
from tools.market_universe_reconcile import (
    classify_delta, classify_status_candidate, verified_lifecycle_events,
    reconcile_verified, reconcile_naver_observed,
)

# Match daily_monitoring_baseline.read_history(max_rows=300) exactly.
KEEP = 301
FORMAT = "COLUMN_ARRAY_V1"
OPTIONAL_COLUMNS = ("open", "low", "value", "source_valid", "source_status", "missing_reason")
MARKETS = ("KOSPI", "KOSDAQ")
KST = ZoneInfo("Asia/Seoul")
# A regular-session close is not a FINAL observation.  The rolling state is
# only eligible after the KRX after-market window has ended at 20:00 KST.
REGULAR_CLOSE_KST = time(15, 30)
FINAL_CLOSE_KST = time(20, 0)
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
    after_close = now.time() >= FINAL_CLOSE_KST
    return {
        "status": "AFTER_FINAL_CLOSE" if after_close else "BEFORE_FINAL_CLOSE",
        "eligible": after_close,
        "nowKst": now.isoformat(),
        "targetDate": now.date().isoformat(),
        "regularCloseKst": "15:30",
        "finalCloseKst": "20:00",
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
    has_optional = any(key in stock for key in OPTIONAL_COLUMNS)
    if has_optional and any(not isinstance(stock.get(key), list) or len(stock[key]) != len(stock["dates"])
                           for key in OPTIONAL_COLUMNS):
        raise ValueError(f"optional column length mismatch: {stock['code']}")
    dates = [compact_date(value) for value in stock["dates"]]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError(f"invalid date sequence: {stock['code']}")
    rows = []
    for index, day in enumerate(dates):
        row = {"date": day, "close": stock["close"][index], "high": stock["high"][index],
               "volume": stock["volume"][index], **flags(stock["validity"][index])}
        if has_optional:
            row.update({key: stock[key][index] for key in OPTIONAL_COLUMNS})
        rows.append(row)
    return rows


def encode_stock(code, rows):
    payload = {"code": str(code), "dates": [row["date"] for row in rows], "close": [row["close"] for row in rows],
            "high": [row["high"] for row in rows], "volume": [row["volume"] for row in rows],
            "validity": [mask(row) for row in rows]}
    if any(any(key in row for key in OPTIONAL_COLUMNS) for row in rows):
        for key in OPTIONAL_COLUMNS:
            payload[key] = [row.get(key) for row in rows]
    return payload


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
        "date": str(target_date), "status": "UNIVERSE_HISTORY_REQUIRED" if market_changed else "READY",
        "previousCount": len(old_codes), "currentCount": len(new_codes),
        "previousMarketCounts": {market: len(old_groups[market]) for market in MARKETS},
        "currentMarketCounts": {market: len(current[market]) for market in MARKETS},
        "removed": [{"code": code, "name": old_assets.get(code, {}).get("name"),
                     "reason": "REMOVED_FROM_AUTHORITATIVE_SOURCE"} for code in removed_codes],
        "added": [{"code": code, "name": next(current[m][code].get("name") for m in MARKETS if code in current[m]),
                   "reason": "ADDED_TO_AUTHORITATIVE_SOURCE"} for code in added_codes],
        "marketChanged": market_changed,
    }
    if not removed_codes and not added_codes:
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
    """Preserve unchanged histories, remove retired stocks, and add empty observed-only states."""
    previous_groups = authoritative_groups(previous_universe)
    current_groups = authoritative_groups(current_universe)
    output = {}
    for market in MARKETS:
        payload = dict(existing[market])
        by_code = {str(stock.get("code")): stock for stock in payload["stocks"]}
        if set(by_code) != set(previous_groups[market]):
            raise ValueError(f"previous authoritative code mismatch: {market}")
        payload["stocks"] = [by_code.get(code, encode_stock(code, []))
                             for code in sorted(current_groups[market])]
        output[market] = payload
    return output


def load_retired_archive(state_dir):
    path = Path(state_dir) / "retired.json"
    if not path.exists():
        return {"schemaVersion": 1, "stocks": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schemaVersion") != 1 or not isinstance(value.get("stocks"), dict):
        raise ValueError("invalid retired history archive")
    return value


def build_retired_archive(existing, current_universe, archive, target_date):
    current = {str(row["code"]) for row in _universe_payload(current_universe).get("assets", [])
               if row.get("assetType", "STOCK") == "STOCK"}
    output = {"schemaVersion": 1, "stocks": dict(archive.get("stocks", {}))}
    for market in MARKETS:
        for stock in existing[market]["stocks"]:
            code = str(stock["code"])
            if code in current:
                continue
            entry = dict(output["stocks"].get(code) or {})
            entry.update({"code": code, "market": market, "stock": stock,
                          "legalStatus": "UNVERIFIED",
                          "inactiveSince": entry.get("inactiveSince") or compact_date(target_date)})
            output["stocks"][code] = entry
    return output


def restore_retired_states(existing, current_universe, archive):
    current_groups = authoritative_groups(current_universe)
    output = {market: dict(payload) for market, payload in existing.items()}
    by_code = {str(stock["code"]): stock for market in MARKETS for stock in existing[market]["stocks"]}
    for code, entry in archive.get("stocks", {}).items():
        if code not in by_code and any(code in current_groups[market] for market in MARKETS):
            market = next(market for market in MARKETS if code in current_groups[market])
            output[market]["stocks"] = list(output[market]["stocks"]) + [entry["stock"]]
    return output


def universe_with_retired_archive(universe, state, current_universe, archive):
    current_codes = {str(row["code"]) for row in _universe_payload(current_universe).get("assets", [])
                     if row.get("assetType", "STOCK") == "STOCK"}
    known = {str(row["code"]) for market in MARKETS for row in state[market]["stocks"]}
    assets = list(_universe_payload(universe).get("assets", []))
    for code, entry in archive.get("stocks", {}).items():
        if code in current_codes and code not in known and entry.get("stock"):
            assets.append({"code": code, "name": entry.get("name", code),
                           "market": entry.get("market"), "assetType": "STOCK"})
    return {**_universe_payload(universe), "assets": assets}


def load_state(state_dir, universe_file=None):
    """Load structurally valid state, optionally enforcing an expected set."""
    expected = authoritative_groups(universe_file) if universe_file is not None else None; loaded = {}
    for market in MARKETS:
        path = Path(state_dir) / f"{market.lower()}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schemaVersion") != 1 or payload.get("format") != FORMAT or payload.get("market") != market:
            raise ValueError(f"invalid state header: {path.name}")
        stocks = payload.get("stocks")
        if not isinstance(stocks, list): raise ValueError(f"invalid stocks: {path.name}")
        codes = [str(item.get("code")) for item in stocks]
        if len(codes) != len(set(codes)) or (expected is not None and set(codes) != set(expected[market])):
            raise ValueError(f"authoritative code mismatch: {path.name}")
        for stock in stocks:
            if len(decode_stock(stock)) > KEEP: raise ValueError(f"retention overflow: {stock['code']}")
        loaded[market] = payload
    return loaded


def universe_from_state(state, reference_universe):
    """Use state-file membership as yesterday's universe for reconciliation."""
    reference = _universe_payload(reference_universe)
    reference_assets = {str(row.get("code")): row for row in reference.get("assets", [])}
    assets = []
    for market in MARKETS:
        for stock in state[market]["stocks"]:
            code = str(stock["code"])
            row = dict(reference_assets.get(code) or {})
            row.update({"code": code, "market": market, "assetType": "STOCK"})
            assets.append(row)
    assets.extend(row for row in reference.get("assets", [])
                  if row.get("assetType", "STOCK") != "STOCK")
    return {**reference, "assets": assets}


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
        low_ok = raw.get("low") is None or (isinstance(raw.get("low"), (int, float)) and raw["low"] <= raw["close"])
        ohlc_valid = bool(raw.get("ohlcValid", close_valid and positive("high") and raw["high"] >= raw["close"] and low_ok))
        volume_valid = bool(raw.get("volumeValid", positive("volume")))
        rows[code] = {"date": target, "close": raw.get("close"), "high": raw.get("high"), "volume": raw.get("volume"),
                      "closeValid": close_valid, "ohlcValid": ohlc_valid, "volumeValid": volume_valid}
        for key in OPTIONAL_COLUMNS:
            if key in raw:
                rows[code][key] = raw[key]
    return rows


def collect_today_rows(target_date, universe_file, fetcher=None, *,
                       max_workers=COLLECT_MAX_WORKERS,
                       retries=COLLECT_RETRIES,
                       timeout=COLLECT_TIMEOUT_SECONDS,
                       retry_sleep=COLLECT_RETRY_SLEEP_SECONDS,
                       diagnostic_output=None):
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
            observed_values = []
            for response_row in rows:
                observed_values.append(str(response_row.get("localDate")))
                compact_date(response_row.get("localDate"))
            observed_date = observed_values[0] if observed_values else None
            candidates = [row for row in rows if compact_date(row.get("localDate")) == target]
            if not candidates:
                if attempt < retries:
                    if retry_sleep:
                        time_module.sleep(retry_sleep)
                    continue
                return code, None, observed_date, observed_values, False
            if len(candidates) != 1:
                error = ValueError(f"duplicate today daily row: {code}")
                error.observed_date = observed_date
                error.observed_date_values = observed_values
                error.duplicate = True
                raise error
            row = candidates[0]
            reasons = daily.row_validation_reasons(row)
            return code, {"code": code, "date": target, "open": row.get("openPrice"),
                          "high": row.get("highPrice"), "low": row.get("lowPrice"),
                          "close": row.get("closePrice"), "volume": row.get("accumulatedTradingVolume"),
                          "value": row.get("accumulatedTradingValue"),
                          "closeValid": not any("close" in item for item in reasons),
                          "ohlcValid": not reasons,
                          "volumeValid": not any("volume" in item for item in reasons),
                          "market": market}, str(row.get("localDate")), observed_values, False
        raise RuntimeError(f"daily fetch failed without result: {code}") from last_error

    collected = {}
    observed_dates = {}
    observed_date_values = {}
    duplicate_codes = set()
    worker_error = None
    expected = {code for code, _market in targets}
    worker_count = min(max_workers, len(targets))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {executor.submit(fetch_one, code, market): code for code, market in targets}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception as error:
                code = futures[future]
                if getattr(error, "observed_date", None) is not None:
                    observed_dates[code] = error.observed_date
                if getattr(error, "observed_date_values", None):
                    observed_date_values[code] = list(error.observed_date_values)
                if getattr(error, "duplicate", False):
                    duplicate_codes.add(code)
                if worker_error is None:
                    worker_error = error
                continue
            code, row, observed_date, values, duplicate = result
            if code in collected:
                raise ValueError(f"duplicate collected daily code: {code}")
            collected[code] = row
            if observed_date is not None:
                observed_dates[code] = observed_date
            if values:
                observed_date_values[code] = list(values)
            if duplicate:
                duplicate_codes.add(code)

    def write_diagnostic():
        if diagnostic_output is None:
            return
        from tools.trading_session_diagnostic import write_local_date_diagnostic
        write_local_date_diagnostic(
            diagnostic_output, target, observed_dates, expected,
            duplicate_codes=duplicate_codes,
            observed_date_values=observed_date_values,
        )

    # Drain every worker before reporting the first error so the diagnostic
    # includes successful and failed observations from the whole batch.
    if worker_error is not None:
        try:
            write_diagnostic()
        except Exception:
            # Preserve the existing collection failure contract.  A
            # diagnostic write must never turn a source failure into a new
            # collection result or hide the original exception.
            pass
        raise worker_error

    missing = [code for code, _market in targets if collected.get(code) is None]
    if missing:
        try:
            write_diagnostic()
        except Exception:
            # TodayRowsNotReady remains the authoritative collection result.
            pass
        raise TodayRowsNotReady(target, missing)
    if set(collected) != expected:
        raise ValueError("collected authoritative code mismatch")
    write_diagnostic()
    output = [collected[code] for code in sorted(collected)]
    return normalize_daily_rows({"rows": output}, target)


def collect_today_rows_partial(target_date, universe_file, fetcher=None, *,
                               max_workers=COLLECT_MAX_WORKERS,
                               retries=COLLECT_RETRIES,
                               timeout=COLLECT_TIMEOUT_SECONDS,
                               retry_sleep=COLLECT_RETRY_SLEEP_SECONDS,
                               diagnostic_output=None):
    """Collect rows without turning per-code failures into valid data.

    The complete collector remains fail-closed.  This companion returns a
    diagnostic envelope for controlled PARTIAL publication; it never writes
    operational state files or calls an additional endpoint.  When requested,
    it writes only the separate localDate diagnostic sidecar.
    """
    groups = authoritative_groups(universe_file)
    target = compact_date(target_date)
    targets = sorted((code, market) for market, assets in groups.items() for code in assets)
    import sys
    tool_dir = str(Path(__file__).parent)
    if tool_dir not in sys.path:
        sys.path.insert(0, tool_dir)
    import naver_daily_backfill as daily
    def one(code, market):
        last_error = None
        observed_date = None
        observed_values = []
        duplicate = False
        for attempt in range(1, retries + 1):
            try:
                response = fetcher(code, datetime.strptime(target, "%Y%m%d"), datetime.strptime(target, "%Y%m%d")) if fetcher else daily.fetch(code, datetime.strptime(target, "%Y%m%d"), datetime.strptime(target, "%Y%m%d"), timeout=timeout, retries=1, sleep=0)
                payload = json.loads(response["raw"].decode("utf-8"))
                rows = payload.get("priceInfos", payload.get("data", [])) if isinstance(payload, dict) else []
                observed_values = [str(item.get("localDate")) for item in rows]
                for value in observed_values:
                    compact_date(value)
                observed_date = observed_values[0] if observed_values else None
                candidates = [row for row in rows if compact_date(row.get("localDate")) == target]
                if len(candidates) != 1:
                    duplicate = len(candidates) > 1
                    raise ValueError("missing or duplicate target-date row")
                row = candidates[0]; reasons = daily.row_validation_reasons(row)
                if reasons:
                    raise ValueError(";".join(reasons))
                return (
                    code,
                    {"code": code, "date": target, "open": row.get("openPrice"),
                     "high": row.get("highPrice"), "low": row.get("lowPrice"),
                     "close": row.get("closePrice"), "volume": row.get("accumulatedTradingVolume"),
                     "value": row.get("accumulatedTradingValue"),
                     "closeValid": True, "ohlcValid": True, "volumeValid": True, "market": market},
                    None, observed_date, observed_values, duplicate,
                )
            except Exception as exc:
                last_error = str(exc)
                if attempt < retries and retry_sleep: time_module.sleep(retry_sleep)
        return code, None, last_error, observed_date, observed_values, duplicate
    rows, errors = {}, {}
    observed_dates = {}
    observed_date_values = {}
    duplicate_codes = set()
    with ThreadPoolExecutor(max_workers=min(max_workers, len(targets))) as executor:
        futures = {executor.submit(one, code, market): code for code, market in targets}
        for future in as_completed(futures):
            result = future.result()
            code = result[0]
            if result[1] is not None:
                rows[code] = result[1]
            else:
                errors[code] = result[2]
            if result[3] is not None:
                observed_dates[code] = result[3]
            if result[4]:
                observed_date_values[code] = list(result[4])
            if result[5]:
                duplicate_codes.add(code)
    if diagnostic_output is not None:
        from tools.trading_session_diagnostic import write_local_date_diagnostic
        write_local_date_diagnostic(
            diagnostic_output, target, observed_dates,
            {code for code, _market in targets}, duplicate_codes=duplicate_codes,
            observed_date_values=observed_date_values,
        )
    return {"status": "COMPLETE" if not errors else "PARTIAL", "targetDate": target,
            "rows": normalize_daily_rows({"rows": list(rows.values())}, target) if rows else {},
            "errors": errors, "missingCodes": sorted(errors),
            "expectedCount": len(targets), "collectedCount": len(rows)}


def collect_today_rows_source_observed(target_date, universe_file, fetcher=None, *,
                                       max_workers=COLLECT_MAX_WORKERS,
                                       retries=COLLECT_RETRIES,
                                       timeout=COLLECT_TIMEOUT_SECONDS,
                                       retry_sleep=COLLECT_RETRY_SLEEP_SECONDS,
                                       diagnostic_output=None):
    """Collect a dated NAVER observation while preserving normal empty days.

    A successful response with no target-date bar is a source-observed
    absence, not a request failure.  Transport, parsing, date-mismatch, and
    duplicate responses remain fail-closed and prevent publication.
    """
    groups = authoritative_groups(universe_file)
    target = compact_date(target_date)
    targets = sorted((code, market) for market, assets in groups.items() for code in assets)
    import sys
    tool_dir = str(Path(__file__).parent)
    if tool_dir not in sys.path:
        sys.path.insert(0, tool_dir)
    import naver_daily_backfill as daily

    def one(code, market):
        last_error = None
        observed_values = []
        for attempt in range(1, retries + 1):
            try:
                response = (fetcher(code, datetime.strptime(target, "%Y%m%d"),
                                    datetime.strptime(target, "%Y%m%d"))
                            if fetcher else daily.fetch(
                                code, datetime.strptime(target, "%Y%m%d"),
                                datetime.strptime(target, "%Y%m%d"),
                                timeout=timeout, retries=1, sleep=0))
                payload = json.loads(response["raw"].decode("utf-8"))
                rows = payload.get("priceInfos", payload.get("data", [])) if isinstance(payload, dict) else []
                if not isinstance(rows, list):
                    raise ValueError("invalid daily response collection")
                observed_values = [str(item.get("localDate")) for item in rows]
                for value in observed_values:
                    compact_date(value)
                candidates = [row for row in rows if compact_date(row.get("localDate")) == target]
                if not candidates:
                    if observed_values:
                        return code, None, "DATE_MISMATCH", observed_values, market
                    return code, None, "NORMAL_EMPTY_DAILY", observed_values, market
                if len(candidates) != 1:
                    return code, None, "DUPLICATE_LOCAL_DATE", observed_values, market
                row = candidates[0]
                reasons = daily.row_validation_reasons(row)
                return code, {
                    "code": code, "date": target, "open": row.get("openPrice"),
                    "high": row.get("highPrice"), "low": row.get("lowPrice"),
                    "close": row.get("closePrice"), "volume": row.get("accumulatedTradingVolume"),
                    "value": row.get("accumulatedTradingValue"),
                    "closeValid": not any("close" in item for item in reasons),
                    "ohlcValid": not reasons,
                    "volumeValid": not any("volume" in item for item in reasons),
                    "source_valid": True, "source_status": "OBSERVED",
                    "missing_reason": None, "market": market,
                }, None, observed_values, market
            except Exception as exc:
                last_error = f"API_OR_PARSE_FAILURE:{type(exc).__name__}:{exc}"
                if attempt < retries and retry_sleep:
                    time_module.sleep(retry_sleep)
        return code, None, last_error or "API_OR_PARSE_FAILURE", observed_values, market

    rows, missing_reasons, errors, observed_dates = {}, {}, {}, {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(targets))) as executor:
        futures = {executor.submit(one, code, market): code for code, market in targets}
        for future in as_completed(futures):
            code, row, reason, values, _market = future.result()
            if values:
                observed_dates[code] = values[0]
            if row is not None:
                rows[code] = row
            elif reason == "NORMAL_EMPTY_DAILY":
                missing_reasons[code] = reason
            else:
                errors[code] = reason

    if diagnostic_output is not None:
        from tools.trading_session_diagnostic import write_local_date_diagnostic
        write_local_date_diagnostic(
            diagnostic_output, target, observed_dates,
            {code for code, _market in targets},
            observed_date_values={code: [value] for code, value in observed_dates.items()},
            source_missing_codes=missing_reasons,
            source_missing_reasons=missing_reasons,
        )
    return {
        "status": "SUCCESS" if not errors else "FAILED",
        "targetDate": target,
        "rows": normalize_daily_rows({"rows": list(rows.values())}, target) if rows else {},
        "sourceMissingCodes": sorted(missing_reasons),
        "sourceMissingReasons": missing_reasons,
        "errors": errors,
        "expectedCount": len(targets),
        "collectedCount": len(rows),
    }


def update_partial_state(existing, daily_rows, target_date, universe_file, state_dir=None, *, errors=None, no_write=False):
    """Apply only validated rows and leave the baseline untouched."""
    target = compact_date(target_date); groups = authoritative_groups(universe_file)
    expected = set().union(*(set(group) for group in groups.values()))
    if not set(daily_rows).issubset(expected):
        raise ValueError("partial daily authoritative code mismatch")
    output = {}
    for market in MARKETS:
        stocks = []
        for old_stock in existing[market]["stocks"]:
            code = str(old_stock["code"]); rows = decode_stock(old_stock)
            incoming = daily_rows.get(code)
            if incoming is not None:
                matching = next((i for i, row in enumerate(rows) if row["date"] == target), None)
                if matching is None: rows.append(incoming)
                elif rows[matching] == incoming: pass
                else: raise ValueError(f"partial final daily conflict: {code} {target}")
                rows.sort(key=lambda row: row["date"])
            stocks.append(encode_stock(code, rows[-KEEP:]))
        output[market] = {**existing[market], "stocks": sorted(stocks, key=lambda row: row["code"]),
                          "publicationStatus": "PARTIAL", "asOfDate": target,
                          "partialDiagnostics": {"missingCodes": sorted(errors or {}),
                                                  "collectedCount": len(daily_rows),
                                                  "expectedCount": len(expected)}}
    if not no_write and state_dir is not None:
        transactional_publish({Path(state_dir) / "kospi.json": output["KOSPI"],
                               Path(state_dir) / "kosdaq.json": output["KOSDAQ"]})
    return {"status": "PARTIAL", "changed": True, "asOfDate": target,
            "coverage": len(daily_rows), "expectedCount": len(expected), "baselineUpdated": False,
            "errors": dict(errors or {})}


def update_state(existing, daily_rows, target_date, universe_file, publication_status="FINAL",
                 allow_source_observed=False, source_missing_reasons=None):
    if publication_status not in {"PROVISIONAL", "FINAL"}:
        raise ValueError("invalid publication status")
    target = compact_date(target_date); groups = authoritative_groups(universe_file)
    expected = set().union(*(set(group) for group in groups.values()))
    if (set(daily_rows) != expected and not allow_source_observed) or not set(daily_rows).issubset(expected):
        raise ValueError("daily authoritative code mismatch")
    output, changed = {}, False
    for market in MARKETS:
        prior = existing[market]; prior_final = prior.get("publicationStatus", "FINAL") == "FINAL"; stocks = []
        for old_stock in prior["stocks"]:
            code = str(old_stock["code"]); rows = decode_stock(old_stock); incoming = daily_rows.get(code)
            if incoming is None:
                stocks.append(encode_stock(code, rows[-KEEP:]))
                continue
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
                          "publicationStatus": publication_status, "asOfDate": target,
                          "sourceQualityStatus": "SOURCE_OBSERVED" if allow_source_observed else "VALIDATED",
                          "sourceMissingCodes": sorted(set(expected) - set(daily_rows)) if allow_source_observed else [],
                          "sourceMissingReasons": dict(source_missing_reasons or {}) if allow_source_observed else {},
                          "stocks": sorted(stocks, key=lambda row: row["code"])}
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


def baseline_from_state(state, target_date, universe_file, output, generated_at=None,
                        allow_source_observed=False, market_day_check=None):
    from daily_monitoring_baseline import refresh_baseline
    kwargs = {"output": output, "universe_file": universe_file, "generated_at": generated_at,
              "history_reader": state_history_reader(state), "no_write": True,
              "allow_source_observed": allow_source_observed}
    if market_day_check is not None:
        kwargs["market_day_check"] = market_day_check
    outcome = refresh_baseline(target_date, **kwargs)
    if outcome.get("status") != "SUCCESS":
        raise ValueError("baseline validation failed: " + str(outcome.get("reason")))
    baseline = outcome["baseline"]; groups = authoritative_groups(universe_file)
    counts = {market: sum(item.get("market") == market for item in baseline["stocks"]) for market in MARKETS}
    if counts != {market: len(groups[market]) for market in MARKETS}:
        raise ValueError("baseline market coverage mismatch")
    return baseline


def update_from_file(state_dir, daily_input, target_date, universe_file, baseline_output, no_write=False,
                     provisional=False, allow_source_observed=False):
    existing = load_state(state_dir, universe_file)
    incoming = normalize_daily_rows(json.loads(Path(daily_input).read_text(encoding="utf-8")), target_date)
    return update_from_rows(existing, incoming, state_dir, target_date, universe_file, baseline_output,
                            no_write, provisional, allow_source_observed=allow_source_observed)


def update_from_rows(existing, incoming, state_dir, target_date, universe_file, baseline_output, no_write=False,
                     provisional=False, allow_source_observed=False, source_missing_reasons=None):
    publication_status = "PROVISIONAL" if provisional else "FINAL"
    updated, changed = update_state(existing, incoming, target_date, universe_file, publication_status,
                                     allow_source_observed=allow_source_observed,
                                     source_missing_reasons=source_missing_reasons)
    baseline = baseline_from_state(updated, target_date, universe_file, baseline_output,
                                   allow_source_observed=allow_source_observed)
    if changed and not no_write:
        payloads = {Path(state_dir) / "kospi.json": updated["KOSPI"], Path(state_dir) / "kosdaq.json": updated["KOSDAQ"]}
        if not provisional:
            payloads[Path(baseline_output)] = baseline
        transactional_publish(payloads)
    return {"status": "SUCCESS", "changed": changed, "asOfDate": compact_date(target_date),
            "coverage": sum(len(value["stocks"]) for value in updated.values()), "baseline": baseline}


def update_with_universe_refresh(state_dir, universe_file, baseline_output, lifecycle_output,
                                 target_date, stock_payload, fetcher=None, no_write=False, provisional=False,
                                 enforce_lifecycle_verification=False,
                                 lifecycle_candidates=None, allow_partial=False, daily_rows=None,
                                 allow_source_observed=False, market_day_check=None,
                                 **collection_options):
    """Reconcile retired stocks and publish one exact universe/state/baseline generation."""
    previous_universe = _universe_payload(universe_file)
    existing = load_state(state_dir)
    retired_archive = load_retired_archive(state_dir)
    state_universe = universe_from_state(existing, previous_universe)
    current_universe, diagnostics = refresh_stock_universe(
        state_universe, stock_payload, target_date)
    if enforce_lifecycle_verification:
        delta = classify_delta(state_universe, current_universe)
        candidates = list(lifecycle_candidates or stock_payload.get("statusCandidates") or [])
        normalized = []
        for item in candidates:
            normalized.append(classify_status_candidate(
                code=item.get("code"), delta_type=item.get("deltaType") or item.get("type"),
                evidence=item.get("evidence"),
                collection_missing=item.get("collectionMissing", False)))
        verified_events = verified_lifecycle_events(normalized)
        # The complete NAVER list controls the operational active set.  Any
        # legal interpretation remains explicitly UNVERIFIED; verified
        # events are retained as optional evidence, never required to apply
        # a complete observed set.
        reconciled_universe, lifecycle = reconcile_naver_observed(
            state_universe, current_universe, target_date=compact_date(target_date))
        lifecycle["candidates"] = normalized
        lifecycle["verifiedEvents"] = verified_events
        diagnostics = dict(diagnostics)
        diagnostics["lifecycle"] = lifecycle
        current_universe = reconciled_universe
    restored_state = restore_retired_states(existing, current_universe, retired_archive)
    previous_for_reconcile = universe_with_retired_archive(
        state_universe, restored_state, current_universe, retired_archive)
    reconciled = reconcile_authoritative_universe(
        restored_state, previous_for_reconcile, current_universe, diagnostics)
    retired_archive = build_retired_archive(existing, current_universe, retired_archive, target_date)
    target = compact_date(target_date)
    lifecycle_changed = bool(diagnostics["removed"] or diagnostics["added"])
    if not lifecycle_changed and all(
            state.get("publicationStatus", "FINAL") == "FINAL" and
            compact_date(state.get("asOfDate")) == target for state in existing.values()):
        baseline = json.loads(Path(baseline_output).read_text(encoding="utf-8"))
        expected = set().union(*(set(group) for group in authoritative_groups(current_universe).values()))
        baseline_codes = {str(stock.get("code")) for stock in baseline.get("stocks", [])}
        source_baseline = (baseline.get("historyStatus") == "SOURCE_OBSERVED" and
                           baseline.get("sourceQualityStatus") == "SOURCE_OBSERVED")
        if (compact_date(baseline.get("asOfDate")) != target or baseline_codes != expected or
                baseline.get("authoritativeCount") != len(expected) or baseline.get("count") != len(expected) or
                baseline.get("coveragePct") != 100.0 or
                (baseline.get("historyStatus") != "OK" and not source_baseline)):
            raise ValueError("same-day final generation baseline mismatch")
        return {"status": "SUCCESS", "changed": False, "asOfDate": target,
                "coverage": len(expected), "lifecycle": diagnostics, "baseline": baseline}
    source_missing_reasons = {}
    if daily_rows is not None:
        # The full-market snapshot is already a complete, validated NAVER
        # observation.  Reuse its daily rows after the universe reconciliation
        # above; never issue a second per-code NAVER request for this path.
        incoming = normalize_daily_rows(
            {"rows": daily_rows} if isinstance(daily_rows, list) else daily_rows,
            target_date,
        )
    elif allow_source_observed:
        observed = collect_today_rows_source_observed(target_date, current_universe,
                                                      fetcher=fetcher, **collection_options)
        if observed["status"] != "SUCCESS":
            raise ValueError("SOURCE_OBSERVED_COLLECTION_FAILED:" + ",".join(
                f"{code}={reason}" for code, reason in sorted(observed["errors"].items())))
        incoming = observed["rows"]
        source_missing_reasons = observed["sourceMissingReasons"]
    elif allow_partial:
        partial = collect_today_rows_partial(target_date, current_universe, fetcher=fetcher,
                                             **collection_options)
        if partial["status"] == "PARTIAL":
            return update_partial_state(
                reconciled, partial["rows"], target_date, current_universe,
                state_dir=state_dir, errors=partial["errors"], no_write=no_write)
        incoming = partial["rows"]
    else:
        incoming = collect_today_rows(target_date, current_universe, fetcher=fetcher,
                                      **collection_options)
    if daily_rows is None and not provisional and not allow_source_observed:
        invalid_codes = sorted(
            code for code, row in incoming.items()
            if not all(row.get(key) is True for key in
                       ("closeValid", "ohlcValid", "volumeValid"))
        )
        if invalid_codes:
            raise ValueError(
                "daily OHLCV invalid: " + ",".join(invalid_codes[:20])
            )
    publication_status = "PROVISIONAL" if provisional else "FINAL"
    updated, changed = update_state(
        reconciled, incoming, target_date, current_universe, publication_status,
        allow_source_observed=allow_source_observed,
        source_missing_reasons=source_missing_reasons,
    )
    baseline_kwargs = {"allow_source_observed": True} if allow_source_observed else {}
    if market_day_check is not None:
        baseline_kwargs["market_day_check"] = market_day_check
    baseline = baseline_from_state(
        updated, target_date, current_universe, baseline_output, **baseline_kwargs)
    if allow_source_observed:
        baseline.setdefault("sourceDiagnostics", {})["missingReasons"] = source_missing_reasons
    groups = authoritative_groups(current_universe)
    expected = set().union(*(set(group) for group in groups.values()))
    state_codes = {str(stock["code"]) for market in MARKETS for stock in updated[market]["stocks"]}
    baseline_codes = {str(stock["code"]) for stock in baseline["stocks"]}
    if expected != state_codes or expected != baseline_codes:
        raise ValueError("atomic authoritative set validation failed")
    baseline.setdefault("refreshDiagnostics", {})["universeLifecycle"] = diagnostics
    publication_changed = changed or lifecycle_changed
    if publication_changed and not no_write:
        payloads = {
            Path(universe_file): current_universe,
            Path(state_dir) / "kospi.json": updated["KOSPI"],
            Path(state_dir) / "kosdaq.json": updated["KOSDAQ"],
            Path(lifecycle_output): diagnostics,
            Path(state_dir) / "retired.json": retired_archive,
        }
        if not provisional:
            payloads[Path(baseline_output)] = baseline
        transactional_publish(payloads)
    return {"status": "SUCCESS", "changed": publication_changed,
            "asOfDate": compact_date(target_date), "coverage": len(expected),
            "lifecycle": diagnostics, "baseline": baseline}
