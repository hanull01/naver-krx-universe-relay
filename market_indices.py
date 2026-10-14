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


def write(payload, path=OUTPUT):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2); handle.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


if __name__ == "__main__":
    payload = collect()
    write(payload)
    print(json.dumps({"status": payload["status"], "count": len(payload["indices"]), "errors": payload["errors"]}, ensure_ascii=False))
