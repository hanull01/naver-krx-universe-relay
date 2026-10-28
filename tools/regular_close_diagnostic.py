"""Independent diagnostics for separating KRX regular close from after-market.

This module is intentionally not imported by the production relay.  It stores
only diagnostic records under ``data/daily-regular-diagnostic`` when called
explicitly, so existing FINAL/Baseline and technical calculation paths remain
unchanged.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timezone
from pathlib import Path


KST = timezone.utc  # replaced below without importing application runtime
try:
    from zoneinfo import ZoneInfo

    KST = ZoneInfo("Asia/Seoul")
except Exception:  # pragma: no cover - environments without zoneinfo
    pass

DIAGNOSTIC_DIR = Path("data/daily-regular-diagnostic")
SCHEMA_VERSION = 1


def _number(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    if isinstance(value, (int, float)):
        number = value
    elif isinstance(value, str):
        text = value.replace(",", "").strip()
        try:
            number = float(text)
        except ValueError as exc:
            raise ValueError(f"{field} must be numeric") from exc
    else:
        raise ValueError(f"{field} must be numeric")
    if number <= 0:
        raise ValueError(f"{field} must be positive")
    return int(number) if float(number).is_integer() else number


def _date_text(value):
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return date.fromisoformat(text).isoformat()


def _rows(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("datas"), list):
        return payload["datas"]
    return []


def extract_integration_last_close(payload):
    """Return NAVER's explicit previous regular-session close field."""
    infos = payload.get("totalInfos", []) if isinstance(payload, dict) else []
    for item in infos:
        if (isinstance(item, dict) and item.get("code") == "lastClosePrice"
                and item.get("value") is not None):
            return _number(item["value"], "lastClosePrice")
    return None


def extract_exact_1530_close(minute_payload, trading_date):
    """Return exactly one KRX 15:30 row; never use a nearby minute."""
    day_text = _date_text(trading_date).replace("-", "")
    expected = f"{day_text}153000"
    matches = [row for row in _rows(minute_payload)
               if isinstance(row, dict) and str(row.get("localDateTime")) == expected]
    if not matches:
        raise ValueError("EXACT_1530_UNAVAILABLE")
    if len(matches) != 1:
        raise ValueError("EXACT_1530_DUPLICATE")
    row = matches[0]
    close = _number(row.get("currentPrice"), "minute.currentPrice")
    return {
        "close": close,
        "source": "NAVER_MINUTE",
        "sourceTime": f"{_date_text(trading_date)}T15:30:00+09:00",
        "basis": "KRX_REGULAR_1530",
    }


def extract_day_close(day_payload, trading_date):
    """Read the day endpoint separately; it is not treated as regular close."""
    target = _date_text(trading_date).replace("-", "")
    matches = [row for row in _rows(day_payload)
               if isinstance(row, dict) and str(row.get("localDate")) == target]
    if len(matches) != 1:
        return None
    return _number(matches[0].get("closePrice"), "day.closePrice")


def _collection_time(value):
    if value is None:
        return datetime.now(KST).isoformat()
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST).isoformat()


def build_record(item_code, trading_date, minute_payload, day_payload=None,
                 collected_at=None):
    """Build one independent, non-production regular-close diagnostic record."""
    target = _date_text(trading_date)
    record = {
        "schemaVersion": SCHEMA_VERSION,
        "tradingDate": target,
        "itemCode": str(item_code),
        "regularClose": None,
        "source": "NAVER_MINUTE",
        "regularCloseBasisTimestamp": f"{target}T15:30:00+09:00",
        "collectedAt": _collection_time(collected_at),
        "verificationStatus": "UNAVAILABLE",
        "verificationMethod": "EXACT_LOCAL_DATETIME_153000",
        "dayClose": extract_day_close(day_payload, target) if day_payload is not None else None,
        "dayCloseSource": "NAVER_DAY" if day_payload is not None else None,
        "dayCloseComparison": "UNAVAILABLE",
        "dayCloseDifference": None,
        "failureReason": None,
    }
    try:
        exact = extract_exact_1530_close(minute_payload, target)
    except ValueError as exc:
        record["failureReason"] = str(exc)
        return record

    record.update(regularClose=exact["close"],
                  regularCloseSourceTime=exact["sourceTime"],
                  verificationStatus="VERIFIED")
    if record["dayClose"] is None:
        record["dayCloseComparison"] = "UNAVAILABLE"
    else:
        difference = record["dayClose"] - record["regularClose"]
        record["dayCloseDifference"] = difference
        record["dayCloseComparison"] = (
            "MATCHED" if difference == 0 else "DAY_CLOSE_MAY_INCLUDE_AFTER_MARKET"
        )
    return record


def save_record(root, record):
    """Persist only the independent diagnostic record, atomically."""
    if not isinstance(record, dict) or record.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("invalid regular-close diagnostic record")
    code = str(record.get("itemCode") or "")
    trading_date = str(record.get("tradingDate") or "")
    if not re.fullmatch(r"\d{6}", code) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", trading_date):
        raise ValueError("invalid diagnostic identity")
    directory = Path(root) / DIAGNOSTIC_DIR
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{trading_date.replace('-', '')}_{code}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target
