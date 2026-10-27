"""Fetch the two official NAVER market index snapshots used by reports."""
from __future__ import annotations

import json
import os
import ssl
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

try:
    import certifi
    SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL = ssl.create_default_context()

INDEX_CODES = ("KOSPI", "KOSDAQ")
BASE_URL = "https://m.stock.naver.com/api/index/{code}/basic"
OUTPUT = Path("data/market/indices.json")
FINAL_SESSION = "POST_MARKET_OR_CLOSED"


def number(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def fetch(code, fetcher=None):
    url = BASE_URL.format(code=code)
    if fetcher:
        payload = fetcher(url)
    else:
        request = Request(url, headers={"User-Agent": "naver-krx-universe-relay/1.0"})
        with urlopen(request, timeout=15, context=SSL) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP_{response.status}")
            payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("INDEX_PAYLOAD_INVALID")
    close = number(payload.get("closePrice"))
    point = number(payload.get("compareToPreviousClosePrice"))
    rate = number(payload.get("fluctuationsRatio"))
    source_time = payload.get("localTradedAt")
    if None in (close, point, rate) or not source_time:
        raise ValueError("INDEX_FIELDS_MISSING")
    status = payload.get("marketStatus")
    session = "UNKNOWN"
    if status in {"OPEN", "REGULAR"}:
        session = "REGULAR"
    elif status in {"CLOSE", "CLOSED", "AFTER_MARKET"}:
        session = "POST_MARKET_OR_CLOSED"
    return {
        "code": code,
        "name": payload.get("stockName") or code,
        "close": close,
        "changePoints": point,
        "changeRate": rate,
        "marketStatus": payload.get("marketStatus"),
        "session": session,
        "sourceTime": source_time,
        "source": "NAVER_MOBILE_INDEX_BASIC",
        "sourceUrl": url,
    }


def collect(fetcher=None, now=None):
    rows, errors = [], []
    for code in INDEX_CODES:
        try:
            rows.append(fetch(code, fetcher))
        except Exception as exc:
            errors.append({"code": code, "error": type(exc).__name__})
    return {
        "schemaVersion": 1,
        "generatedAt": (now or datetime.now().astimezone()).isoformat(),
        "status": "OK" if not errors else "PARTIAL" if rows else "FAILED",
        "source": "NAVER_MOBILE_INDEX_BASIC",
        "indices": rows,
        "errors": errors,
    }


def _timestamp(value, field):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"INDEX_{field.upper()}_INVALID") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"INDEX_{field.upper()}_TIMEZONE_MISSING")
    return parsed


def validate_final_payload(payload, collection_started_at, collection_finished_at):
    """Validate that index data belongs to this final collection window."""
    if not isinstance(payload, dict) or payload.get("status") != "OK":
        raise ValueError("INDEX_COLLECTION_NOT_SUCCESS")
    if payload.get("source") != "NAVER_MOBILE_INDEX_BASIC":
        raise ValueError("INDEX_SOURCE_INVALID")
    generated_at = _timestamp(payload.get("generatedAt"), "generated_at")
    started_at = _timestamp(collection_started_at, "collection_started_at")
    finished_at = _timestamp(collection_finished_at, "collection_finished_at")
    if started_at > finished_at or not started_at <= generated_at <= finished_at:
        raise ValueError("INDEX_GENERATED_AT_STALE")

    rows = payload.get("indices") or []
    if {row.get("code") for row in rows} != set(INDEX_CODES) or len(rows) != len(INDEX_CODES):
        raise ValueError("INDEX_CODE_SET_INVALID")
    source_dates = []
    for row in rows:
        if row.get("session") != FINAL_SESSION:
            raise ValueError("INDEX_SESSION_NOT_CLOSED")
        if row.get("marketStatus") not in {"CLOSE", "CLOSED", "AFTER_MARKET"}:
            raise ValueError("INDEX_MARKET_NOT_CLOSED")
        source_time = _timestamp(row.get("sourceTime"), "source_time")
        if source_time > generated_at:
            raise ValueError("INDEX_SOURCE_TIME_AFTER_GENERATED_AT")
        source_dates.append(source_time.date())
    if len(set(source_dates)) != 1:
        raise ValueError("INDEX_SOURCE_DATE_MISMATCH")
    if source_dates[0] != generated_at.date():
        raise ValueError("INDEX_SOURCE_DATE_GENERATED_DATE_MISMATCH")
    return {"targetDate": source_dates[0].strftime("%Y%m%d"),
            "generatedAt": generated_at.isoformat()}


def write(payload, path=OUTPUT):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2); handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def main(fetcher=None, path=OUTPUT, now=None):
    payload = collect(fetcher=fetcher, now=now)
    write(payload, path)
    print(json.dumps({"status": payload["status"], "count": len(payload["indices"]), "errors": payload["errors"]}, ensure_ascii=False))
    return 0 if payload["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
