"""Date-bound Kiwoom candidate performance using existing daily state files.

This module is deliberately read-only.  It consumes an immutable discovery
snapshot and a verified KRX trading-date sequence; it never calls a market
API or infers dates from weekdays.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools.monitoring_daily_state_update import decode_stock


HORIZONS = (1, 3, 5)
VERIFIED_QUALITY = {"VERIFIED", "VALIDATED", "FINAL", "VERIFIED_FINAL"}


def _number(value: Any) -> float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


def _date(value: Any) -> str:
    text = str(value or "").replace("/", "-")
    compact = text.replace("-", "")
    if len(compact) == 8 and compact.isdigit():
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
    return text


def _row_quality(row: Mapping[str, Any], file_quality: str | None = None) -> str:
    """Use per-date evidence; never promote a row from a file header."""
    explicit = row.get("qualityStatus") or row.get("sourceQualityStatus")
    if explicit:
        return str(explicit)
    source_status = str(row.get("source_status") or "").upper()
    if source_status in {"OBSERVED", "SOURCE_OBSERVED"}:
        return "SOURCE_OBSERVED"
    if source_status in {"VERIFIED", "VALIDATED", "FINAL", "VERIFIED_FINAL"}:
        return source_status
    return "UNAVAILABLE"


def _corporate_action_status(row: Mapping[str, Any]) -> str | None:
    for key in ("corporateActionStatus", "historyAdjustmentStatus", "adjustmentStatus"):
        if row.get(key) not in (None, ""):
            return str(row[key])
    return None


def _eligible_close(row: Mapping[str, Any] | None, file_quality: str | None = None) -> tuple[float | None, str]:
    if not isinstance(row, Mapping):
        return None, "PRICE_ROW_MISSING"
    close = _number(row.get("close", row.get("lastClose")))
    if close is None:
        return None, "CLOSE_INVALID"
    action = _corporate_action_status(row)
    if action is None:
        return None, "CORPORATE_ACTION_STATUS_UNVERIFIED"
    if action not in {"VERIFIED_NONE", "NONE", "NOT_APPLICABLE", "VERIFIED_ADJUSTED", "COMPARABLE_ADJUSTED_PRICE"}:
        return None, "CORPORATE_ACTION_UNVERIFIED"
    quality = _row_quality(row, file_quality)
    if quality not in VERIFIED_QUALITY:
        return None, "SOURCE_NOT_VERIFIED_FINAL"
    return close, "VERIFIED_FINAL_CLOSE"


def load_monitoring_daily_prices(root: Path, codes: Iterable[str] | None = None) -> tuple[dict[str, dict[str, dict[str, Any]]], list[str]]:
    """Decode the existing COLUMN_ARRAY_V1 files without writing or fetching."""
    wanted = {str(code) for code in codes} if codes is not None else None
    result: dict[str, dict[str, dict[str, Any]]] = {}
    dates: set[str] = set()
    for market in ("kospi", "kosdaq"):
        path = Path(root) / "data" / "monitoring-daily" / f"{market}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        file_quality = payload.get("sourceQualityStatus")
        for stock in payload.get("stocks") or []:
            code = str(stock.get("code") or "")
            if not code or (wanted is not None and code not in wanted):
                continue
            rows = result.setdefault(code, {})
            try:
                decoded = decode_stock(stock)
            except (TypeError, ValueError, KeyError):
                continue
            for decoded_row in decoded:
                date = _date(decoded_row.get("date"))
                if not date:
                    continue
                dates.add(date)
                row = dict(decoded_row)
                row["date"] = date
                # Keep the file-level quality only as provenance metadata.
                # _row_quality intentionally ignores it when the decoded row
                # has no per-date verification evidence.
                row["fileSourceQualityStatus"] = file_quality
                rows[date] = row
    return result, sorted(dates)


def _measurement(row: Mapping[str, Any] | None, date: str) -> dict[str, Any]:
    value, reason = _eligible_close(row)
    item = {"date": date, "close": value, "status": "AVAILABLE" if value is not None else "UNAVAILABLE",
            "reason": None if value is not None else reason, "priceSource": "MONITORING_DAILY"}
    return item


def build_kiwoom_performance(discovery: Mapping[str, Any], price_rows: Mapping[str, Mapping[str, Mapping[str, Any]]],
                             trading_dates: Iterable[str] | None = None, *, official_trading_dates: Iterable[str] | None = None,
                             now_date: str | None = None) -> dict[str, Any]:
    """Return every candidate with explicit D+1/D+3/D+5 state."""
    dates = sorted({_date(item) for item in (official_trading_dates if official_trading_dates is not None else []) if _date(item)})
    selection = _date(discovery.get("selectionDate"))
    monitoring = _date(discovery.get("monitoringDate"))
    symbols = [str(code) for code in discovery.get("symbols") or []]
    base_index = dates.index(selection) if selection in dates else None
    calendar_available = official_trading_dates is not None and bool(dates)
    stocks = []
    for code in symbols:
        rows = price_rows.get(code) or {}
        base_row = rows.get(selection)
        base_close, base_reason = _eligible_close(base_row) if calendar_available else (None, "OFFICIAL_KRX_TRADING_DATES_UNAVAILABLE")
        stock = {"itemCode": code, "itemName": (discovery.get("validation", {}).get("codeNameBindings", {}) or {}).get(code),
                 "selectionDate": discovery.get("selectionDate"), "monitoringDate": discovery.get("monitoringDate"),
                 "selectionClose": base_close, "selectionCloseStatus": "AVAILABLE" if base_close is not None else "UNAVAILABLE",
                 "selectionCloseReason": None if base_close is not None else base_reason,
                 "horizons": {}, "strategyStatus": "UNVERIFIED", "strategyReason": "전략 성공·실패 기준이 계약에 없습니다."}
        for horizon in HORIZONS:
            key = f"D+{horizon}"
            target_date = None if base_index is None else (dates[base_index + horizon] if base_index + horizon < len(dates) else None)
            if not calendar_available:
                status, reason = "PENDING", "OFFICIAL_KRX_TRADING_DATES_UNAVAILABLE"
                stock["horizons"][key] = {"date": None, "close": None, "returnPct": None, "status": status, "reason": reason}
                continue
            if base_index is None:
                status, reason = "UNAVAILABLE", "SELECTION_DATE_NOT_IN_KRX_TRADING_DATES"
                stock["horizons"][key] = {"date": None, "close": None, "returnPct": None, "status": status, "reason": reason}
                continue
            if target_date is None:
                status, reason = "PENDING", "MEASUREMENT_TRADING_DATE_NOT_AVAILABLE"
                stock["horizons"][key] = {"date": None, "close": None, "returnPct": None, "status": status, "reason": reason}
                continue
            if now_date and target_date > _date(now_date):
                stock["horizons"][key] = {"date": target_date, "close": None, "returnPct": None,
                                           "status": "PENDING", "reason": "MEASUREMENT_DATE_IN_FUTURE"}
                continue
            measurement = _measurement(rows.get(target_date), target_date)
            if base_close is None:
                measurement.update({"returnPct": None, "status": "UNAVAILABLE", "reason": "SELECTION_CLOSE_UNAVAILABLE"})
            elif measurement.get("close") is not None:
                measurement["returnPct"] = round((measurement["close"] / base_close - 1) * 100, 6)
            stock["horizons"][key] = measurement
        stock["extrema"] = {"status": "UNAVAILABLE", "reason": "FULL_INTERVAL_OHLC_NOT_SUPPLIED"}
        stocks.append(stock)
    statuses = [row["horizons"]["D+1"]["status"] for row in stocks]
    if not stocks:
        status = "AVAILABLE_EMPTY"
    elif all(value == "AVAILABLE" for value in statuses):
        status = "AVAILABLE"
    elif any(value == "PENDING" for value in statuses):
        status = "PENDING"
    else:
        status = "PARTIAL"
    return {"status": status, "source": discovery.get("sourcePath") or f"data/trading-discovery/{monitoring}.json",
            "selectionDate": discovery.get("selectionDate"), "monitoringDate": discovery.get("monitoringDate"),
            "sourceSha256": discovery.get("sourceSha256"), "candidateCount": len(symbols), "symbols": symbols,
            "stocks": stocks, "tradingDates": dates, "nowDate": now_date}
