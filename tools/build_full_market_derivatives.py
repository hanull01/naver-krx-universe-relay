#!/usr/bin/env python3
"""Build compact report inputs from a staged NAVER daily canonical tree.

The builder is staging-only by design.  It never reads production daily or
technical files and never writes below the repository's ``data`` directory.
Raw responses remain outside the output; provenance points back to the staged
canonical file and records its content hash.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from tools.file_digest import sha256_file


ROLLING_WINDOW = 120
REQUIRED_OHLCV = ("open", "high", "low", "close", "volume")


class FullMarketDerivativeError(ValueError):
    """Raised when a staging derivative cannot be built safely."""


def resolve_rolling_window(
    history_dir: str | Path,
    target_date: str,
    is_trading_day,
    window: int = ROLLING_WINDOW,
) -> dict[str, Any]:
    """Choose bootstrap, gap-fill, or incremental collection without network I/O."""
    target = date.fromisoformat(target_date)
    history_root = Path(history_dir)
    manifest_path = history_root / "rolling-history-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    entries = manifest.get("byCode") or {}
    counts = [int(row.get("rowCount") or 0) for row in entries.values() if isinstance(row, dict)]
    latest_dates = [
        _date(row.get("latest"))
        for row in entries.values()
        if isinstance(row, dict) and _date(row.get("latest"))
    ]
    latest = max(latest_dates, default=None)

    def is_open(day: date) -> bool:
        result = is_trading_day(day)
        return bool(result[0] if isinstance(result, tuple) else result)

    if not entries or not counts or min(counts) < window:
        dates = []
        cursor = target
        while len(dates) < window:
            if is_open(cursor):
                dates.append(cursor)
            cursor -= timedelta(days=1)
        return {
            "mode": "BOOTSTRAP",
            "startDate": min(dates).isoformat(),
            "endDate": target.isoformat(),
            "historyLatest": latest,
            "historyCountMin": min(counts) if counts else 0,
        }

    target_compact = target.strftime("%Y%m%d")
    if latest and latest < target_compact:
        start = date.fromisoformat(f"{latest[:4]}-{latest[4:6]}-{latest[6:]}") + timedelta(days=1)
        while start < target and not is_open(start):
            start += timedelta(days=1)
        if start > target:
            start = target
        mode = "GAP_FILL" if start < target else "INCREMENTAL"
    else:
        start = target
        mode = "INCREMENTAL"
    return {
        "mode": mode,
        "startDate": start.isoformat(),
        "endDate": target.isoformat(),
        "historyLatest": latest,
        "historyCountMin": min(counts),
    }


def _date(value: Any) -> str | None:
    text = str(value or "").replace("-", "")
    if len(text) != 8 or not text.isdigit():
        return None
    return text


def _write_json(path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.write_text(encoded, encoding="utf-8")
    return {"path": str(path), "bytes": len(encoded.encode("utf-8")), "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FullMarketDerivativeError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise FullMarketDerivativeError(f"JSON object expected: {path}")
    return payload


def _numeric(row: Mapping[str, Any], key: str) -> float | None:
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None


def _mean(values: list[float], window: int) -> float | None:
    return round(sum(values[-window:]) / window, 6) if len(values) >= window else None


def _ema(values: list[float], window: int) -> float | None:
    series = _ema_series(values, window)
    return series[-1] if series else None


def _ema_series(values: list[float], window: int) -> list[float]:
    if len(values) < window:
        return []
    result = sum(values[:window]) / window
    series = [None] * (window - 1) + [result]
    alpha = 2 / (window + 1)
    for value in values[window:]:
        result = alpha * value + (1 - alpha) * result
        series.append(result)
    return series


def _metric(value: Any, *, reason: str | None = None) -> dict[str, Any]:
    return {"status": "AVAILABLE", "value": value} if value is not None else {
        "status": "UNAVAILABLE", "reason": reason or "INSUFFICIENT_HISTORY", "value": None
    }


def _rsi(closes: list[float], window: int = 14) -> float | None:
    if len(closes) < window + 1:
        return None
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:window]) / window
    avg_loss = sum(losses[:window]) / window
    for index in range(window, len(changes)):
        avg_gain = (avg_gain * (window - 1) + gains[index]) / window
        avg_loss = (avg_loss * (window - 1) + losses[index]) / window
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 6)


def _metric_status(history_count: int, window: int, *, extra: int = 0) -> dict[str, Any]:
    return _metric(None, reason=f"NEED_{window + extra}_BARS") if history_count < window + extra else {"status": "AVAILABLE"}


def _load_coverage(root: Path) -> list[dict[str, Any]]:
    path = root / "provenance" / "coverage.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _load_asset_files(directory: Path, *, source_kind: str) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    files = sorted(directory.rglob("*.jsonl")) if directory.exists() else []
    assets: dict[str, list[dict[str, Any]]] = {}
    file_meta: dict[str, dict[str, Any]] = {}
    for path in files:
        code = path.stem
        tail_lines = deque(maxlen=121)
        line_count = 0
        first_row = None
        max_observed_date = None
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                line_count += 1
                if first_row is None:
                    try:
                        first_row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise FullMarketDerivativeError(f"invalid canonical JSONL {path}:{line_number}: {exc}") from exc
                    if not isinstance(first_row, dict) or str(first_row.get("code")) != code:
                        raise FullMarketDerivativeError(f"canonical code mismatch: {path}:{line_number}")
                try:
                    observed = json.loads(line)
                except json.JSONDecodeError:
                    observed = None
                observed_date = _date(observed.get("date")) if isinstance(observed, dict) else None
                if observed_date and (max_observed_date is None or observed_date > max_observed_date):
                    max_observed_date = observed_date
                tail_lines.append(line)
        rows = []
        seen = set()
        for line_number, line in enumerate(tail_lines, max(1, line_count - len(tail_lines) + 1)):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise FullMarketDerivativeError(f"invalid canonical JSONL {path}:{line_number}: {exc}") from exc
            if not isinstance(row, dict) or str(row.get("code")) != code:
                raise FullMarketDerivativeError(f"canonical code mismatch: {path}:{line_number}")
            date = _date(row.get("date"))
            if not date or date in seen:
                raise FullMarketDerivativeError(f"invalid or duplicate date: {path}:{line_number}")
            seen.add(date)
            if not all(_numeric(row, key) is not None for key in REQUIRED_OHLCV):
                continue
            if row.get("feature_valid") is False or row.get("source_valid") is False:
                continue
            copied = dict(row)
            copied["date"] = date
            rows.append(copied)
        rows.sort(key=lambda value: value["date"])
        assets[code] = rows
        file_meta[code] = {
            "sourceCanonicalPath": str(path),
            "sourceCanonicalSha256": sha256_file(path),
            "historyCount": line_count,
            "sourceDateRange": {
                "earliest": _date(first_row.get("date")) if isinstance(first_row, dict) else None,
                "latest": rows[-1]["date"] if rows else None,
            },
            "maxObservedDate": max_observed_date,
            "assetType": str(
                first_row.get("assetType") if isinstance(first_row, dict) else None
                or (path.parent.name if path.parent.name.upper() in {"STOCK", "ETF"} else "STOCK")
            ).upper(),
            "sourceKind": source_kind,
        }
    return assets, file_meta


def _load_assets(
    canonical_dir: Path,
    history_dir: Path | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    current_assets, current_meta = _load_asset_files(
        canonical_dir, source_kind="CURRENT_COLLECTION"
    )
    history_assets, history_meta = _load_asset_files(
        history_dir, source_kind="RESTORED_ROLLING_HISTORY"
    ) if history_dir is not None else ({}, {})
    if not current_assets and not history_assets:
        raise FullMarketDerivativeError(f"no canonical files: {canonical_dir}")

    assets: dict[str, list[dict[str, Any]]] = {}
    file_meta: dict[str, dict[str, Any]] = {}
    for code in sorted(set(current_assets) | set(history_assets)):
        by_date = {row["date"]: row for row in history_assets.get(code, [])}
        by_date.update({row["date"]: row for row in current_assets.get(code, [])})
        rows = sorted(by_date.values(), key=lambda value: value["date"])[-ROLLING_WINDOW:]
        assets[code] = rows
        current = current_meta.get(code)
        history = history_meta.get(code)
        meta = dict(current or history or {})
        observed_dates = [
            str(value.get("maxObservedDate"))
            for value in (current, history)
            if isinstance(value, Mapping) and value.get("maxObservedDate")
        ]
        if observed_dates:
            meta["maxObservedDate"] = max(observed_dates)
        meta["historyCount"] = len(rows)
        meta["rollingHistoryWindow"] = ROLLING_WINDOW
        if current and history:
            meta["restoredHistoryPath"] = history.get("sourceCanonicalPath")
            meta["restoredHistorySha256"] = history.get("sourceCanonicalSha256")
        meta["sourceKind"] = "MERGED_CURRENT_AND_ROLLING_HISTORY" if current and history else meta.get("sourceKind")
        file_meta[code] = meta
    if not any(assets.values()):
        raise FullMarketDerivativeError("canonical rows are empty")
    return assets, file_meta


def _provenance(root: Path, code: str, meta: Mapping[str, Any], rows: list[dict[str, Any]], target: str) -> dict[str, Any]:
    return {
        "sourceStaging": str(root),
        "sourceCanonicalPath": meta.get("sourceCanonicalPath"),
        "sourceCanonicalSha256": meta.get("sourceCanonicalSha256"),
        "sourceDateRange": meta.get("sourceDateRange"),
        "sourceAsOfDate": rows[-1]["date"] if rows else None,
        "sourceStatus": "OBSERVED" if rows else "NO_DAILY_BAR",
        "finalityStatus": "SOURCE_OBSERVED" if rows else "UNAVAILABLE",
        "targetDate": target,
    }


def _technical_row(code: str, meta: Mapping[str, Any], rows: list[dict[str, Any]], target: str, root: Path) -> dict[str, Any]:
    asset_type = meta.get("assetType", "UNKNOWN")
    as_of = rows[-1]["date"] if rows else None
    closes = [_numeric(row, "close") for row in rows]
    highs = [_numeric(row, "high") for row in rows]
    volumes = [_numeric(row, "volume") for row in rows]
    closes = [value for value in closes if value is not None]
    highs = [value for value in highs if value is not None]
    volumes = [value for value in volumes if value is not None]
    count = int(meta.get("historyCount") or len(rows))
    metric_status = {}
    metrics: dict[str, Any] = {}
    for name, window, values in (
        ("ma5", 5, closes), ("ma20", 20, closes), ("ma60", 60, closes), ("ma120", 120, closes),
        ("avgVolume20", 20, volumes), ("high20", 20, highs), ("high60", 60, highs), ("high120", 120, highs),
    ):
        value = _mean(values, window) if name == "avgVolume20" else (max(values[-window:]) if len(values) >= window else None)
        if name.startswith("ma"):
            value = _mean(values, window)
        metrics[name] = value
        metric_status[name] = _metric(value, reason=f"NEED_{window}_BARS")
    rsi = _rsi(closes)
    metrics["rsi14"] = rsi
    metric_status["rsi14"] = _metric(rsi, reason="NEED_15_BARS")
    ema12_series, ema26_series = _ema_series(closes, 12), _ema_series(closes, 26)
    ema12 = ema12_series[-1] if ema12_series else None
    ema26 = ema26_series[-1] if ema26_series else None
    macd = (ema12 - ema26) if ema12 is not None and ema26 is not None else None
    macd_values = [short - long for short, long in zip(ema12_series, ema26_series)
                   if short is not None and long is not None]
    signal = _ema(macd_values, 9)
    metrics.update({"macd": round(macd, 6) if macd is not None else None,
                    "macdSignal": round(signal, 6) if signal is not None else None,
                    "macdHistogram": round(macd - signal, 6) if macd is not None and signal is not None else None})
    metric_status["macd"] = _metric(metrics["macd"], reason="NEED_26_BARS")
    metric_status["macdSignal"] = _metric(metrics["macdSignal"], reason="NEED_34_BARS")
    metric_status["macdHistogram"] = _metric(metrics["macdHistogram"], reason="NEED_34_BARS")
    middle = _mean(closes, 20)
    stddev = statistics.pstdev(closes[-20:]) if len(closes) >= 20 else None
    bollinger = {"status": "AVAILABLE", "middle": round(middle, 6), "upper": round(middle + 2 * stddev, 6), "lower": round(middle - 2 * stddev, 6), "stddev": round(stddev, 6)} if middle is not None and stddev is not None else {"status": "UNAVAILABLE", "reason": "NEED_20_BARS"}
    metrics["bollinger"] = bollinger
    metric_status["bollinger"] = bollinger
    for window in (5, 20, 60, 120):
        value = (closes[-1] / closes[-window - 1] - 1) * 100 if len(closes) > window else None
        metrics[f"return{window}d"] = round(value, 6) if value is not None else None
        metric_status[f"return{window}d"] = _metric(value, reason=f"NEED_{window + 1}_BARS")
    ratio = closes and volumes and metrics.get("avgVolume20") and volumes[-1] / metrics["avgVolume20"] if len(volumes) >= 20 else None
    metrics["volumeRatio20"] = round(ratio, 6) if ratio is not None else None
    metric_status["volumeRatio20"] = _metric(metrics["volumeRatio20"], reason="NEED_20_BARS")
    prior20 = max(highs[-21:-1]) if len(highs) >= 21 else None
    prior60 = max(highs[-61:-1]) if len(highs) >= 61 else None
    for window, prior in ((20, prior20), (60, prior60), (120, max(highs[-121:-1]) if len(highs) >= 121 else None)):
        value = closes[-1] > prior if prior is not None else None
        metrics[f"breakout{window}"] = value
        metric_status[f"breakout{window}"] = _metric(value, reason=f"NEED_{window + 1}_BARS")
    ma5, ma20, ma60 = metrics.get("ma5"), metrics.get("ma20"), metrics.get("ma60")
    trend = "UPTREND" if all(value is not None for value in (closes[-1] if closes else None, ma5, ma20, ma60)) and closes[-1] > ma20 > ma60 and ma5 > ma20 else "DOWNTREND" if all(value is not None for value in (closes[-1] if closes else None, ma5, ma20, ma60)) and closes[-1] < ma20 < ma60 and ma5 < ma20 else "MIXED" if ma60 is not None else None
    metrics["trend"] = trend
    metric_status["trend"] = _metric(trend, reason="NEED_MA5_MA20_MA60")
    status = "UNAVAILABLE" if count < 2 else "PARTIAL" if as_of != target or count < 20 else "AVAILABLE"
    return {
        "itemCode": code, "assetType": asset_type, "asOfDate": as_of,
        "historyCount": count, **metrics, "metricStatus": metric_status,
        "status": status, "availabilityStatus": status,
        "qualityReasons": ([] if rows and as_of == target and count >= 20 else ["LATEST_DATE_NOT_TARGET"] if rows and as_of != target else ["INSUFFICIENT_HISTORY"]),
        "provenance": _provenance(root, code, meta, rows, target),
    }


def _compare(value: float | None, reference: float | None) -> str:
    if value is None or reference is None:
        return "unknown"
    return "above" if value > reference else "below" if value < reference else "equal"


def _state_row(technical: Mapping[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = technical.get("metrics") or technical
    price = _numeric(rows[-1], "close") if rows else None
    previous_price = _numeric(rows[-2], "close") if len(rows) >= 2 else None
    change_rate = (
        round((price / previous_price - 1) * 100, 6)
        if price is not None and previous_price not in (None, 0)
        else None
    )
    volume = _numeric(rows[-1], "volume") if rows else None
    highs = [_numeric(row, "high") for row in rows]
    highs = [value for value in highs if value is not None]
    prior20 = max(highs[-21:-1]) if len(highs) >= 21 else None
    prior60 = max(highs[-61:-1]) if len(highs) >= 61 else None
    current = {
        "status": technical["availabilityStatus"], "availabilityStatus": technical["availabilityStatus"],
        "asOfDate": technical["asOfDate"], "price": price, "previousClose": previous_price,
        "changeRate": change_rate, "volume": volume, "ma20": metrics.get("ma20"), "ma60": metrics.get("ma60"),
        "volumeRatio20": metrics.get("volumeRatio20"), "priceVsMA20": _compare(price, metrics.get("ma20")),
        "priceVsMA60": _compare(price, metrics.get("ma60")),
        "maAlignment": _compare(metrics.get("ma20"), metrics.get("ma60")),
        "breakout20": price > prior20 if price is not None and prior20 is not None else None,
        "breakout60": price > prior60 if price is not None and prior60 is not None else None,
        "trend": metrics.get("trend"),
    }
    return {
        "itemCode": technical["itemCode"], "assetType": technical["assetType"],
        "asOfDate": technical["asOfDate"], "sourceTime": technical["asOfDate"],
        **{key: value for key, value in current.items() if key not in ("status", "availabilityStatus")},
        "status": technical["availabilityStatus"], "availabilityStatus": technical["availabilityStatus"],
        "current": current, "regularSession": {"status": "UNAVAILABLE", "reason": "NO_REGULAR_SESSION_INPUT"},
        "provenance": technical["provenance"],
    }


def _load_universe(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    stocks = [row for row in payload.get("stocks", []) if isinstance(row, dict) and row.get("enabled") is True]
    watchlists = payload.get("watchlists") or {}
    if not isinstance(watchlists, dict):
        raise FullMarketDerivativeError("universe.watchlists must be an object")
    return {"stocks": stocks, "watchlists": watchlists}


def _group_states(states: Mapping[str, Mapping[str, Any]], universe: Mapping[str, Any], target: str, root: Path) -> dict[str, Any]:
    groups = []
    missing = {}
    for name, codes in universe.get("watchlists", {}).items():
        codes = [str(code) for code in codes]
        rows = [states[code] for code in codes if code in states]
        missing_codes = [code for code in codes if code not in states]
        if missing_codes:
            missing[str(name)] = missing_codes
        status = "AVAILABLE" if rows and not missing_codes else "PARTIAL" if rows else "UNAVAILABLE"
        groups.append({
            "groupType": "watchlist", "groupName": str(name), "memberCount": len(codes),
            "observedCount": len(rows), "missingCodes": missing_codes, "status": status,
            "targetDate": target, "source": "FULL_MARKET_DERIVATIVE_STATES",
        })
    return {
        "schemaVersion": 1, "generatedAt": datetime.now().astimezone().isoformat(),
        "targetDate": target, "sourceStaging": str(root), "status": "AVAILABLE" if groups else "UNAVAILABLE",
        "availabilityStatus": "AVAILABLE" if groups else "UNAVAILABLE",
        "groups": groups, "missingCodes": missing, "excludedGroupTypes": ["sector", "theme"],
        "reason": None if groups else "GROUP_MEMBERSHIP_NOT_PRESENT_IN_CONFIGURED_WATCHLISTS",
    }


def _write_rolling_history(
    output: Path,
    assets: Mapping[str, list[dict[str, Any]]],
    file_meta: Mapping[str, Mapping[str, Any]],
    target: str,
    generated_at: str,
) -> dict[str, Any]:
    history_root = output / "rolling-history"
    entries: dict[str, Any] = {}
    total_rows = 0
    for code, rows in sorted(assets.items()):
        asset_type = str(file_meta.get(code, {}).get("assetType") or "STOCK").upper()
        path = history_root / asset_type / f"{code}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for row in rows[-ROLLING_WINDOW:]:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        total_rows += len(rows)
        entries[code] = {
            "assetType": asset_type,
            "path": str(path.relative_to(output)),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "rowCount": len(rows),
            "earliest": rows[0]["date"] if rows else None,
            "latest": rows[-1]["date"] if rows else None,
            "sourceKind": file_meta.get(code, {}).get("sourceKind"),
            "sourceCanonicalPath": file_meta.get(code, {}).get("sourceCanonicalPath"),
            "sourceCanonicalSha256": file_meta.get(code, {}).get("sourceCanonicalSha256"),
            "restoredHistoryPath": file_meta.get(code, {}).get("restoredHistoryPath"),
            "restoredHistorySha256": file_meta.get(code, {}).get("restoredHistorySha256"),
        }
    manifest = {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "targetDate": target,
        "window": ROLLING_WINDOW,
        "fallbackUsed": False,
        "count": len(entries),
        "totalRows": total_rows,
        "byCode": entries,
    }
    _write_json(output / "rolling-history-manifest.json", manifest)
    return manifest


def build_derivatives(
    canonical_dir: str | Path,
    universe_path: str | Path,
    output_dir: str | Path,
    history_dir: str | Path | None = None,
    target_date: str | None = None,
) -> dict[str, Any]:
    canonical = Path(canonical_dir)
    root = canonical.parent.parent if canonical.parent.name == "canonical" else canonical.parent
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    restored_history = Path(history_dir) if history_dir is not None else None
    assets, file_meta = _load_assets(canonical, restored_history)
    latest_dates = [rows[-1]["date"] for rows in assets.values() if rows]
    if not latest_dates:
        raise FullMarketDerivativeError("no usable canonical rows")
    target = _date(target_date) if target_date is not None else max(latest_dates)
    if not target:
        raise FullMarketDerivativeError(f"invalid target date: {target_date!r}")
    future_files = [
        (code, str(meta.get("maxObservedDate")))
        for code, meta in file_meta.items()
        if meta.get("maxObservedDate") and str(meta["maxObservedDate"]) > target
    ]
    if future_files:
        raise FullMarketDerivativeError(
            f"future data beyond targetDate {target}: {future_files[:5]}"
        )
    universe = _load_universe(Path(universe_path))
    technical_rows = [_technical_row(code, file_meta[code], rows, target, root) for code, rows in sorted(assets.items())]
    state_rows = [_state_row(row, assets[row["itemCode"]]) for row in technical_rows]
    technical_by_code = {row["itemCode"]: row for row in technical_rows}
    state_by_code = {row["itemCode"]: row for row in state_rows}
    public_technical_rows = [{key: value for key, value in row.items() if key != "provenance"} for row in technical_rows]
    public_state_rows = [{key: value for key, value in row.items() if key != "provenance"} for row in state_rows]
    public_technical_by_code = {row["itemCode"]: row for row in public_technical_rows}
    public_state_by_code = {row["itemCode"]: row for row in public_state_rows}
    missing_codes = sorted(code for code, rows in assets.items() if not rows)
    coverage = _load_coverage(root)
    no_daily_bar = sorted({str(row.get("code")) for row in coverage if row.get("missingReason") == "NO_DAILY_BAR"})
    source = {
        "sourceStaging": str(root), "sourceCanonicalDir": str(canonical), "targetDate": target,
        "date20261030Rows": sum(row.get("date") == "20261030" for rows in assets.values() for row in rows),
        "fallbackUsed": False,
    }
    technical_payload = {
        "schemaVersion": 1, "generatedAt": datetime.now().astimezone().isoformat(),
        "expectedCount": len(technical_rows), "count": len(technical_rows), "targetDate": target,
        **source, "missingCodes": missing_codes, "noDailyBar": no_daily_bar,
        "status": "AVAILABLE" if all(row["availabilityStatus"] == "AVAILABLE" for row in technical_rows) else "PARTIAL",
        "datas": public_technical_rows,
    }
    state_payload = {
        "schemaVersion": 1, "generatedAt": technical_payload["generatedAt"],
        "expectedCount": len(state_rows), "count": len(state_rows), "targetDate": target,
        **source, "missingCodes": missing_codes, "noDailyBar": no_daily_bar,
        "status": "AVAILABLE" if all(row["availabilityStatus"] == "AVAILABLE" for row in state_rows) else "PARTIAL",
        "datas": public_state_rows,
    }
    registered_codes = sorted({str(row.get("itemCode")) for row in universe["stocks"]} | {str(code) for values in universe["watchlists"].values() for code in values})
    registered_missing = [code for code in registered_codes if code not in technical_by_code]
    registered_technicals = {**technical_payload, "scope": "REGISTERED_UNIVERSE_PROJECTION", "expectedCount": len(registered_codes), "count": len(registered_codes) - len(registered_missing), "missingCodes": registered_missing, "datas": [public_technical_by_code[code] for code in registered_codes if code in public_technical_by_code]}
    registered_states = {**state_payload, "scope": "REGISTERED_UNIVERSE_PROJECTION", "expectedCount": len(registered_codes), "count": len(registered_codes) - len(registered_missing), "missingCodes": registered_missing, "datas": [public_state_by_code[code] for code in registered_codes if code in public_state_by_code]}
    group_payload = _group_states(public_state_by_code, universe, target, root)
    provenance = {"schemaVersion": 1, "sourceStaging": str(root), "targetDate": target, "byCode": {code: {key: value for key, value in row["provenance"].items() if key != "targetDate"} for code, row in technical_by_code.items()}}
    generated_at = technical_payload["generatedAt"]
    rolling_history = _write_rolling_history(output, assets, file_meta, target, generated_at)
    quality = {
        "schemaVersion": 1, "generatedAt": technical_payload["generatedAt"], "status": technical_payload["status"],
        "targetDate": target, "sourceStaging": str(root), "fallbackUsed": False,
        "totalAssets": len(technical_rows), "byAssetType": {
            kind: sum(row["assetType"] == kind for row in technical_rows) for kind in ("STOCK", "ETF")
        },
        "statusCounts": {status: sum(row["availabilityStatus"] == status for row in technical_rows) for status in ("AVAILABLE", "PARTIAL", "UNAVAILABLE")},
        "missingCodes": missing_codes, "noDailyBar": no_daily_bar,
        "date20261030Rows": source["date20261030Rows"],
        "historicalGroups": "UNAVAILABLE" if not group_payload["groups"] else "WATCHLIST_ONLY",
        "currentSnapshot": {"status": "SEPARATE_NOT_INCLUDED", "reason": "CURRENT_SNAPSHOT_IS_NOT_BACKFILL_DERIVATIVE"},
        "rollingHistory": {
            "status": "AVAILABLE" if rolling_history["count"] else "UNAVAILABLE",
            "window": ROLLING_WINDOW,
            "count": rolling_history["count"],
            "totalRows": rolling_history["totalRows"],
            "manifest": "rolling-history-manifest.json",
        },
    }
    file_info = {}
    for name, payload in (
        ("technicals.json", technical_payload), ("states.json", state_payload),
        ("registered-universe-technicals.json", registered_technicals),
        ("registered-universe-states.json", registered_states), ("group-states.json", group_payload),
        ("provenance.json", provenance), ("quality.json", quality),
    ):
        file_info[name] = _write_json(output / name, payload)
    manifest = {
        "schemaVersion": 1, "generatedAt": technical_payload["generatedAt"], "status": quality["status"],
        "source": source, "quality": {key: value for key, value in quality.items() if key not in ("generatedAt",)},
        "registeredUniverse": {"count": len(registered_codes) - len(registered_missing), "expectedCount": len(registered_codes), "missingCodes": registered_missing},
        "files": file_info,
        "rollingHistory": {
            "manifest": "rolling-history-manifest.json",
            "window": ROLLING_WINDOW,
            "count": rolling_history["count"],
            "totalRows": rolling_history["totalRows"],
        },
    }
    file_info["manifest.json"] = _write_json(output / "manifest.json", manifest)
    return {"outputDir": str(output), "targetDate": target, "files": file_info, "quality": quality}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical-dir", required=True)
    parser.add_argument("--universe", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--history-dir")
    parser.add_argument("--target-date")
    args = parser.parse_args(argv)
    result = build_derivatives(
        args.canonical_dir,
        args.universe,
        args.output_dir,
        history_dir=args.history_dir,
        target_date=args.target_date,
    )
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
