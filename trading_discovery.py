#!/usr/bin/env python3
"""Optional Kiwoom discovery publication and report analysis.

This module is deliberately outside the required market-data contract.  Every
reader failure becomes a section-local status and never changes the main
report status, coverage, or MARKET_DATA_READY policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DISCOVERY_DIR = ROOT / "data" / "trading-discovery"
SYMBOL = re.compile(r"(?=.*\d)[0-9A-Z]{6}")
SNAPSHOT_KEYS = {
    "schema_version", "status", "selection_date", "monitoring_date", "source",
    "source_group", "group_id", "synced_at", "candidate_count", "symbols",
    "source_sha256", "validation",
}
PUBLISHED_KEYS = {
    "schemaVersion", "status", "monitoringDate", "selectionDate", "source",
    "sourceGroup", "groupId", "syncedAt", "candidateCount", "symbols",
    "sourceSha256", "validation",
}
SENSITIVE_KEY = re.compile(r"(?:token|secret|password|account|authorization|app.?key)", re.I)


class DiscoveryError(ValueError):
    pass


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def source_hash(payload):
    material = {
        "source": payload.get("source"), "source_group": payload.get("source_group"),
        "group_id": payload.get("group_id"), "selection_date": payload.get("selection_date"),
        "symbols": payload.get("symbols"),
    }
    raw = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def contains_sensitive_key(value):
    if isinstance(value, dict):
        return any(SENSITIVE_KEY.search(str(key)) or contains_sensitive_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_sensitive_key(item) for item in value)
    return False


def load_official_sessions(path):
    try:
        payload = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise DiscoveryError("KRX_CALENDAR_UNAVAILABLE") from exc
    dates = payload.get("dates") if isinstance(payload, dict) else None
    if payload.get("source") != "KRX_OFFICIAL" or not isinstance(dates, list):
        raise DiscoveryError("KRX_CALENDAR_INVALID")
    normalized = []
    for value in dates:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise DiscoveryError("KRX_CALENDAR_INVALID")
        normalized.append(value)
    if normalized != sorted(set(normalized)):
        raise DiscoveryError("KRX_CALENDAR_INVALID")
    return normalized


def validate_date_relation(payload, calendar_path):
    sessions = load_official_sessions(calendar_path)
    selection = payload.get("selection_date")
    if selection not in sessions:
        raise DiscoveryError("SELECTION_DATE_NOT_CONFIRMED")
    index = sessions.index(selection) + 1
    if index >= len(sessions) or sessions[index] != payload.get("monitoring_date"):
        raise DiscoveryError("MONITORING_DATE_NOT_NEXT_KRX_SESSION")


def validate_snapshot(payload, calendar_path=None):
    if not isinstance(payload, dict) or set(payload) != SNAPSHOT_KEYS or payload.get("schema_version") != 1:
        raise DiscoveryError("SNAPSHOT_SCHEMA_INVALID")
    if contains_sensitive_key(payload):
        raise DiscoveryError("SNAPSHOT_SENSITIVE_DATA_REJECTED")
    if payload.get("status") not in {"AVAILABLE", "AVAILABLE_EMPTY"}:
        raise DiscoveryError("SNAPSHOT_STATUS_INVALID")
    symbols = payload.get("symbols")
    if not isinstance(symbols, list) or any(not isinstance(code, str) or not SYMBOL.fullmatch(code) for code in symbols):
        raise DiscoveryError("SNAPSHOT_SYMBOLS_INVALID")
    if len(symbols) != len(set(symbols)) or payload.get("candidate_count") != len(symbols):
        raise DiscoveryError("SNAPSHOT_COUNT_INVALID")
    if payload["status"] != ("AVAILABLE" if symbols else "AVAILABLE_EMPTY"):
        raise DiscoveryError("SNAPSHOT_EMPTY_STATUS_INVALID")
    validation = payload.get("validation")
    if not isinstance(validation, dict) or validation.get("status") != "VALID" or validation.get("immutable") is not True:
        raise DiscoveryError("SNAPSHOT_VALIDATION_INVALID")
    if payload.get("source") != "KIWOOM_WATCHLIST" or payload.get("source_group") != "전일후보군":
        raise DiscoveryError("SNAPSHOT_SOURCE_INVALID")
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("source_sha256") or "")):
        raise DiscoveryError("SNAPSHOT_HASH_INVALID")
    if payload["source_sha256"] != source_hash(payload):
        raise DiscoveryError("SNAPSHOT_HASH_MISMATCH")
    if calendar_path is not None:
        validate_date_relation(payload, calendar_path)
    return payload


def published_payload(snapshot, calendar_path=None):
    validate_snapshot(snapshot, calendar_path)
    return {
        "schemaVersion": 1,
        "status": snapshot["status"],
        "monitoringDate": snapshot["monitoring_date"],
        "selectionDate": snapshot["selection_date"],
        "source": snapshot["source"],
        "sourceGroup": snapshot["source_group"],
        "groupId": snapshot["group_id"],
        "syncedAt": snapshot["synced_at"],
        "candidateCount": snapshot["candidate_count"],
        "symbols": list(snapshot["symbols"]),
        "sourceSha256": snapshot["source_sha256"],
        "validation": {
            "status": "VALID",
            "calendarSource": snapshot["validation"]["calendar_source"],
            "immutable": True,
        },
    }


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def publish_snapshot(snapshot_path, output_dir=DISCOVERY_DIR, calendar_path=None):
    if calendar_path is None:
        raise DiscoveryError("KRX_CALENDAR_REQUIRED")
    snapshot = validate_snapshot(load_json(snapshot_path), calendar_path)
    payload = published_payload(snapshot)
    output_dir = Path(output_dir)
    dated = output_dir / f"{payload['monitoringDate']}.json"
    latest = output_dir / "latest.json"
    if dated.exists():
        existing = load_json(dated)
        if existing.get("sourceSha256") != payload["sourceSha256"]:
            raise DiscoveryError("PUBLISHED_SNAPSHOT_CONFLICT")
        result = "IDEMPOTENT"
    else:
        atomic_json(dated, payload)
        result = "CREATED"
    try:
        current = load_json(latest) if latest.exists() else None
    except (OSError, ValueError):
        raise DiscoveryError("LATEST_INVALID")
    if current is None or current.get("monitoringDate", "") <= payload["monitoringDate"]:
        if current != payload:
            atomic_json(latest, payload)
    return {"status": payload["status"], "result": result, "dated": str(dated), "latest": str(latest)}


def discovery_status(as_of, path=None):
    path = Path(path or DISCOVERY_DIR / "latest.json")
    try:
        payload = load_json(path)
    except FileNotFoundError:
        return {"status": "UNAVAILABLE", "reason": "SNAPSHOT_MISSING", "candidateCount": 0, "symbols": []}
    except (OSError, ValueError, json.JSONDecodeError):
        return {"status": "INVALID", "reason": "SNAPSHOT_UNREADABLE", "candidateCount": 0, "symbols": []}
    if set(payload) != PUBLISHED_KEYS or payload.get("schemaVersion") != 1 or contains_sensitive_key(payload):
        return {"status": "INVALID", "reason": "SNAPSHOT_SCHEMA_INVALID", "candidateCount": 0, "symbols": []}
    symbols = payload.get("symbols")
    if (not isinstance(symbols, list) or len(symbols) != len(set(symbols))
            or any(not isinstance(code, str) or not SYMBOL.fullmatch(code) for code in symbols)
            or payload.get("candidateCount") != len(symbols)):
        return {"status": "INVALID", "reason": "SNAPSHOT_SYMBOLS_INVALID", "candidateCount": 0, "symbols": []}
    if payload.get("monitoringDate") != as_of:
        return {**payload, "status": "STALE", "reason": "MONITORING_DATE_MISMATCH"}
    material = {
        "source": payload.get("source"), "source_group": payload.get("sourceGroup"),
        "group_id": payload.get("groupId"), "selection_date": payload.get("selectionDate"),
        "symbols": payload.get("symbols"),
    }
    if payload.get("sourceSha256") != source_hash(material):
        return {**payload, "status": "INVALID", "reason": "SNAPSHOT_HASH_MISMATCH"}
    expected = "AVAILABLE" if symbols else "AVAILABLE_EMPTY"
    if payload.get("status") != expected or payload.get("validation", {}).get("status") != "VALID":
        return {**payload, "status": "INVALID", "reason": "SNAPSHOT_VALIDATION_INVALID"}
    return payload


def _index(payload, keys=("itemCode", "code")):
    result = {}
    if not isinstance(payload, dict):
        return result
    rows = payload.get("datas") or payload.get("stocks") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = next((row.get(key) for key in keys if row.get(key)), None)
        if code:
            result[str(code)] = row
    return result


def analyze(as_of, discovery, quotes, technicals, states, full_market_signals=None, previous=None):
    if discovery.get("status") not in {"AVAILABLE", "AVAILABLE_EMPTY"}:
        return {"title": "키움 전일후보군 — 당일 추적", **discovery, "stocks": []}
    qidx, tidx, sidx = _index(quotes), _index(technicals), _index(states)
    fidx = _index(full_market_signals or {})
    previous_rows = _index(previous or {}, keys=("itemCode",))
    rows = []
    for code in discovery["symbols"]:
        quote, technical, state, signal = qidx.get(code), tidx.get(code), sidx.get(code), fidx.get(code)
        market = quote or signal or {}
        technical = technical or signal or {}
        state = state or {}
        current = state.get("current") if isinstance(state.get("current"), dict) else state
        row = {
            "itemCode": code,
            "itemName": market.get("stockName") or market.get("name") or technical.get("stockName"),
            "price": market.get("closePrice", market.get("currentPrice")),
            "changeRate": market.get("fluctuationsRatio", market.get("changeRate")),
            "volume": market.get("accumulatedTradingVolume"),
            "tradingValue": market.get("accumulatedTradingValue"),
            "ma5": technical.get("ma5"),
            "ma20": technical.get("ma20"),
            "ma60": technical.get("ma60"),
            "volumeRatio20": technical.get("volumeRatio20", signal.get("current_volume_ratio_20") if signal else None),
            "breakout20": current.get("breakout20", signal.get("breakout_20_intraday") if signal else None),
            "pullbackState": current.get("pullbackState"),
            "source": "MONITORED_UNIVERSE" if quote else "FULL_MARKET_SIGNAL" if signal else None,
        }
        observed = [row[key] for key in ("price", "changeRate", "volume", "tradingValue", "ma5", "ma20", "ma60")]
        row["dataStatus"] = "AVAILABLE" if all(value is not None for value in observed) else "PARTIAL" if any(value is not None for value in observed) else "UNAVAILABLE"
        prior = previous_rows.get(code, {})
        row["previousReportChange"] = {
            "available": bool(prior),
            "price": (row["price"] - prior["price"] if isinstance(row["price"], (int, float)) and isinstance(prior.get("price"), (int, float)) else None),
            "dataStatusChanged": bool(prior) and prior.get("dataStatus") != row["dataStatus"],
            "breakoutChanged": bool(prior) and prior.get("breakout20") != row["breakout20"],
        }
        rows.append(row)
    available = sum(row["dataStatus"] == "AVAILABLE" for row in rows)
    partial = sum(row["dataStatus"] == "PARTIAL" for row in rows)
    return {
        "title": "키움 전일후보군 — 당일 추적", **discovery,
        "asOf": as_of, "observedCount": available + partial,
        "missingCount": len(rows) - available - partial, "stocks": rows,
        "metrics": {"vwap": "UNAVAILABLE", "intradayHighLow": "UNAVAILABLE"},
    }


def fetch_missing_quotes(symbols, existing_codes, fetcher, normalizer, clock, limit=50):
    missing = [code for code in dict.fromkeys(symbols) if code not in set(existing_codes)]
    if len(missing) > limit:
        raise DiscoveryError("MISSING_QUOTE_LIMIT_EXCEEDED")
    if not missing:
        return {"networkCalls": 0, "requestedSymbols": [], "datas": [], "errors": []}
    payload = fetcher(missing)
    source_rows = payload.get("datas") if isinstance(payload, dict) else None
    if not isinstance(source_rows, list):
        return {"networkCalls": 1, "requestedSymbols": missing, "datas": [], "errors": [{"reason": "RESPONSE_SCHEMA_INVALID"}]}
    rows, errors = [], []
    for source in source_rows:
        code = source.get("itemCode") if isinstance(source, dict) else None
        if code not in missing:
            continue
        try:
            rows.append(normalizer(source, clock()))
        except Exception as exc:
            errors.append({"itemCode": code, "reason": type(exc).__name__})
    return {"networkCalls": 1, "requestedSymbols": missing, "datas": rows, "errors": errors}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot")
    parser.add_argument("--output-dir", default=str(DISCOVERY_DIR))
    parser.add_argument("--calendar", required=True)
    args = parser.parse_args()
    print(json.dumps(publish_snapshot(args.snapshot, args.output_dir, args.calendar), ensure_ascii=False))


if __name__ == "__main__":
    main()
