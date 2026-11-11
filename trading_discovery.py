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
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import quote

from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
DISCOVERY_DIR = ROOT / "data" / "trading-discovery"
DISCOVERY_QUOTES_DIR = DISCOVERY_DIR / "quotes"
SYMBOL = re.compile(r"(?=.*\d)[0-9A-Z]{6}")
SNAPSHOT_KEYS = {
    "schema_version", "status", "selection_date", "monitoring_date", "source",
    "source_group", "group_id", "synced_at", "candidate_count", "symbols",
    "source_sha256", "validation",
}
SNAPSHOT_OPTIONAL_KEYS = {
    "candidate_details", "publisher_version", "artifact_status",
    "publication_status", "candidate_completeness", "generated_at",
    "producer_commit", "producer_status",
}
PUBLISHED_KEYS = {
    "schemaVersion", "status", "monitoringDate", "selectionDate", "source",
    "sourceGroup", "groupId", "syncedAt", "candidateCount", "symbols",
    "sourceSha256", "validation",
}
PUBLISHED_OPTIONAL_KEYS = {
    "candidateDetails", "publisherVersion", "artifactStatus",
    "publicationStatus", "candidateCompleteness", "generatedAt",
    "producerCommit", "producerStatus", "enrichmentStatus",
    "schemaTransition", "artifactIdentity", "originalGeneratedAt",
}
SENSITIVE_KEY = re.compile(r"(?:token|secret|password|account|authorization|app.?key)", re.I)
KST = ZoneInfo("Asia/Seoul")
SOURCE_CONTRACTS = {
    "KIWOOM_WATCHLIST": {"source_group": "전일후보군", "group_id": re.compile(r"\S+")},
    "KIWOOM_CONDITION_SEARCH": {
        "source_group": "장종료후 단타후보군 탐색",
        "group_id": re.compile(r"CONDITION:[0-9]{1,3}"),
    },
}


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
    if (not isinstance(payload, dict)
            or not SNAPSHOT_KEYS.issubset(payload)
            or set(payload) - SNAPSHOT_KEYS - SNAPSHOT_OPTIONAL_KEYS
            or payload.get("schema_version") != 1):
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
    if (not isinstance(validation, dict) or validation.get("status") != "VALID"
            or validation.get("calendar_source") != "KRX_OFFICIAL"
            or validation.get("immutable") is not True):
        raise DiscoveryError("SNAPSHOT_VALIDATION_INVALID")
    contract = SOURCE_CONTRACTS.get(payload.get("source"))
    if (contract is None or payload.get("source_group") != contract["source_group"]
            or not isinstance(payload.get("group_id"), str)
            or not contract["group_id"].fullmatch(payload["group_id"])):
        raise DiscoveryError("SNAPSHOT_SOURCE_INVALID")
    if payload.get("source") == "KIWOOM_CONDITION_SEARCH":
        try:
            synced_at = datetime.fromisoformat(payload["synced_at"])
        except (TypeError, ValueError, KeyError) as exc:
            raise DiscoveryError("SNAPSHOT_SYNC_TIME_INVALID") from exc
        if synced_at.utcoffset() != timedelta(hours=9):
            raise DiscoveryError("SNAPSHOT_SYNC_TIME_INVALID")
    if not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("source_sha256") or "")):
        raise DiscoveryError("SNAPSHOT_HASH_INVALID")
    if payload["source_sha256"] != source_hash(payload):
        raise DiscoveryError("SNAPSHOT_HASH_MISMATCH")
    details = payload.get("candidate_details")
    if details is not None:
        if not isinstance(details, list) or len(details) != len(symbols):
            raise DiscoveryError("SNAPSHOT_DETAILS_INVALID")
        detail_codes = [row.get("symbol") for row in details if isinstance(row, dict)]
        if detail_codes != symbols or any(not isinstance(row, dict) for row in details):
            raise DiscoveryError("SNAPSHOT_DETAILS_SYMBOL_MISMATCH")
    if calendar_path is not None:
        validate_date_relation(payload, calendar_path)
    return payload


def published_payload(snapshot, calendar_path=None):
    validate_snapshot(snapshot, calendar_path)
    payload = {
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
    if "candidate_details" in snapshot:
        payload["candidateDetails"] = snapshot["candidate_details"]
    if snapshot.get("artifact_status") is not None:
        payload["artifactStatus"] = snapshot["artifact_status"]
    if snapshot.get("publication_status") is not None:
        payload["publicationStatus"] = snapshot["publication_status"]
    if snapshot.get("candidate_completeness") is not None:
        payload["candidateCompleteness"] = snapshot["candidate_completeness"]
    if snapshot.get("publisher_version"):
        payload["publisherVersion"] = snapshot["publisher_version"]
    payload["generatedAt"] = snapshot.get("generated_at") or snapshot["synced_at"]
    if snapshot.get("producer_commit"):
        payload["producerCommit"] = snapshot["producer_commit"]
    if snapshot.get("producer_status"):
        payload["producerStatus"] = snapshot["producer_status"]
    return payload


def _is_missing_detail_value(value):
    return value in (None, "", "UNAVAILABLE", "NOT_PROVIDED", [], {})


def _detail_score(details):
    if not isinstance(details, list):
        return 0

    def score(value):
        if _is_missing_detail_value(value):
            return 0
        if isinstance(value, dict):
            return sum(score(item) for item in value.values())
        if isinstance(value, list):
            return sum(score(item) for item in value)
        return 1

    return sum(score(row) for row in details)


def _merge_detail_values(previous, incoming):
    """Merge an enrichment row while rejecting concrete value changes."""
    if isinstance(previous, dict) and isinstance(incoming, dict):
        merged = dict(incoming)
        for key, old_value in previous.items():
            if key not in incoming:
                merged[key] = old_value
                continue
            merged[key] = _merge_detail_values(old_value, incoming[key])
        return merged
    if _is_missing_detail_value(previous):
        return incoming
    if _is_missing_detail_value(incoming):
        return previous
    if previous != incoming:
        raise DiscoveryError("PUBLISHED_SNAPSHOT_ENRICHMENT_VALUE_CONFLICT")
    return previous


def _validate_enrichment_identity(existing, incoming):
    for key in ("monitoringDate", "selectionDate", "source", "sourceGroup",
                "groupId", "sourceSha256"):
        if existing.get(key) != incoming.get(key):
            raise DiscoveryError("PUBLISHED_SNAPSHOT_ENRICHMENT_IDENTITY_CONFLICT")
    old_symbols = existing.get("symbols")
    new_symbols = incoming.get("symbols")
    if (not isinstance(old_symbols, list) or not isinstance(new_symbols, list)
            or len(old_symbols) != len(new_symbols)
            or set(old_symbols) != set(new_symbols)):
        raise DiscoveryError("PUBLISHED_SNAPSHOT_ENRICHMENT_SYMBOL_SET_CONFLICT")


def _enrich_published_snapshot(existing, incoming):
    """Safely upgrade one same-identity symbol-only publication."""
    _validate_enrichment_identity(existing, incoming)
    old_details = existing.get("candidateDetails")
    new_details = incoming.get("candidateDetails")
    if not isinstance(new_details, list) or not new_details:
        raise DiscoveryError("PUBLISHED_SNAPSHOT_ENRICHMENT_DETAILS_REQUIRED")
    if len(new_details) != len(existing["symbols"]):
        raise DiscoveryError("PUBLISHED_SNAPSHOT_ENRICHMENT_DETAILS_INVALID")

    # Shared publication metadata is immutable.  generatedAt, producerCommit,
    # and completeness are lineage/enrichment metadata and may be introduced by
    # the detailed producer, but concrete prior values may not be rewritten.
    for key in ("status", "candidateCount", "validation", "artifactStatus",
                "publicationStatus", "candidateCompleteness", "producerStatus"):
        if key in existing and key in incoming and existing[key] != incoming[key]:
            raise DiscoveryError("PUBLISHED_SNAPSHOT_ENRICHMENT_VALUE_CONFLICT")

    incoming_by_symbol = {row.get("symbol"): row for row in new_details}
    if set(incoming_by_symbol) != set(existing["symbols"]):
        raise DiscoveryError("PUBLISHED_SNAPSHOT_ENRICHMENT_SYMBOL_SET_CONFLICT")
    merged_details = [
        _merge_detail_values(old_row, incoming_by_symbol[symbol])
        for symbol, old_row in (
            ((row.get("symbol"), row) for row in (old_details or []))
        )
    ] if old_details else [incoming_by_symbol[symbol] for symbol in existing["symbols"]]
    if old_details and _detail_score(new_details) <= _detail_score(old_details):
        if merged_details == old_details:
            return existing, "IDEMPOTENT"
        raise DiscoveryError("PUBLISHED_SNAPSHOT_ENRICHMENT_NOT_MORE_DETAILED")

    enriched = {**existing, **incoming}
    # The original publication identity and creation time remain authoritative.
    for key in ("schemaVersion", "status", "monitoringDate", "selectionDate",
                "source", "sourceGroup", "groupId", "syncedAt", "candidateCount",
                "symbols", "sourceSha256", "validation"):
        enriched[key] = existing[key]
    enriched["candidateDetails"] = merged_details
    enriched["generatedAt"] = incoming.get("generatedAt") or incoming.get("syncedAt")
    enriched["enrichmentStatus"] = "ENRICHED"
    original_generated = existing.get("generatedAt") or existing.get("syncedAt")
    enriched["originalGeneratedAt"] = original_generated
    enriched["artifactIdentity"] = {
        "monitoringDate": existing["monitoringDate"],
        "selectionDate": existing["selectionDate"],
        "source": existing["source"],
        "sourceSha256": existing["sourceSha256"],
        "symbols": list(existing["symbols"]),
        "originalGeneratedAt": original_generated,
    }
    enriched["schemaTransition"] = {
        "from": "SYMBOL_ONLY",
        "to": "CANDIDATE_DETAILS",
        "status": "ENRICHED",
        "sourceSha256": existing["sourceSha256"],
        "originalGeneratedAt": original_generated,
        "enrichedGeneratedAt": enriched["generatedAt"],
        "producerCommit": incoming.get("producerCommit"),
    }
    return enriched, "ENRICHED"


def _merge_publication(existing, incoming):
    if existing.get("sourceSha256") != incoming.get("sourceSha256"):
        raise DiscoveryError("PUBLISHED_SNAPSHOT_CONFLICT")
    return _enrich_published_snapshot(existing, incoming)


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
        dated_payload, result = _merge_publication(existing, payload)
    else:
        dated_payload, result = payload, "CREATED"
    try:
        current = load_json(latest) if latest.exists() else None
    except (OSError, ValueError):
        raise DiscoveryError("LATEST_INVALID")
    latest_payload = current
    latest_result = "UNCHANGED"
    if current is None:
        latest_payload, latest_result = payload, "CREATED"
    elif current.get("monitoringDate", "") < payload["monitoringDate"]:
        latest_payload, latest_result = dated_payload, "UPDATED"
    elif current.get("monitoringDate", "") == payload["monitoringDate"]:
        latest_payload, latest_result = _merge_publication(current, payload)
    if result in {"CREATED", "ENRICHED"}:
        atomic_json(dated, dated_payload)
    if latest_payload is not None and latest_payload != current:
        atomic_json(latest, latest_payload)
    return {
        "status": payload["status"], "result": result,
        "latestResult": latest_result, "dated": str(dated), "latest": str(latest),
    }


def discovery_status(as_of, path=None):
    # The report date is authoritative for production reads.  In particular,
    # do not let a later ``latest.json`` (for the next monitoring session)
    # replace the candidate set for an earlier report.  An explicit path is
    # retained for isolated callers and test fixtures.
    path = Path(path) if path is not None else DISCOVERY_DIR / f"{as_of}.json"
    source_path = str(path)
    try:
        source_path = path.relative_to(ROOT).as_posix()
    except ValueError:
        pass

    def with_source(value):
        return {**value, "sourcePath": source_path}

    try:
        payload = load_json(path)
    except FileNotFoundError:
        return with_source({"status": "UNAVAILABLE", "reason": "SNAPSHOT_MISSING", "candidateCount": 0, "symbols": []})
    except (OSError, ValueError, json.JSONDecodeError):
        return with_source({"status": "INVALID", "reason": "SNAPSHOT_UNREADABLE", "candidateCount": 0, "symbols": []})
    if (not PUBLISHED_KEYS.issubset(payload)
            or set(payload) - PUBLISHED_KEYS - PUBLISHED_OPTIONAL_KEYS
            or payload.get("schemaVersion") != 1
            or contains_sensitive_key(payload)):
        return with_source({"status": "INVALID", "reason": "SNAPSHOT_SCHEMA_INVALID", "candidateCount": 0, "symbols": []})
    symbols = payload.get("symbols")
    if (not isinstance(symbols, list) or len(symbols) != len(set(symbols))
            or any(not isinstance(code, str) or not SYMBOL.fullmatch(code) for code in symbols)
            or payload.get("candidateCount") != len(symbols)):
        return with_source({"status": "INVALID", "reason": "SNAPSHOT_SYMBOLS_INVALID", "candidateCount": 0, "symbols": []})
    if payload.get("monitoringDate") != as_of:
        return with_source({**payload, "status": "STALE", "reason": "MONITORING_DATE_MISMATCH"})
    material = {
        "source": payload.get("source"), "source_group": payload.get("sourceGroup"),
        "group_id": payload.get("groupId"), "selection_date": payload.get("selectionDate"),
        "symbols": payload.get("symbols"),
    }
    if payload.get("sourceSha256") != source_hash(material):
        return with_source({**payload, "status": "INVALID", "reason": "SNAPSHOT_HASH_MISMATCH"})
    details = payload.get("candidateDetails")
    if details is not None:
        if (not isinstance(details, list) or len(details) != len(symbols)
                or any(not isinstance(row, dict) or row.get("symbol") != code
                       for row, code in zip(details, symbols))):
            return with_source({**payload, "status": "INVALID", "reason": "SNAPSHOT_DETAILS_INVALID"})
    if symbols and details is None:
        return with_source({**payload, "status": "PARTIAL", "reason": "CANDIDATE_DETAILS_MISSING"})
    if payload.get("artifactStatus") not in (None, "AVAILABLE", "PARTIAL"):
        return with_source({**payload, "status": "INVALID", "reason": "ARTIFACT_STATUS_INVALID"})
    if payload.get("publicationStatus") not in (None, "AVAILABLE", "PARTIAL"):
        return with_source({**payload, "status": "INVALID", "reason": "PUBLICATION_STATUS_INVALID"})
    expected = "AVAILABLE" if symbols else "AVAILABLE_EMPTY"
    if payload.get("status") != expected or payload.get("validation", {}).get("status") != "VALID":
        return with_source({**payload, "status": "INVALID", "reason": "SNAPSHOT_VALIDATION_INVALID"})
    return with_source(payload)


def normalize_item_code(value):
    if value in (None, ""):
        return None
    text = str(value).strip().upper()
    return text.zfill(6) if text.isdigit() and len(text) < 6 else text


def _flatten_row(row):
    if not isinstance(row, dict):
        return {}
    result = dict(row)
    for key in ("technicals", "metrics", "current", "state"):
        nested = row.get(key)
        if isinstance(nested, dict):
            for name, value in nested.items():
                result.setdefault(name, value)
    return result


def _index(payload, keys=("itemCode", "code", "itemcode")):
    result = {}
    if not isinstance(payload, dict):
        return result
    rows = payload.get("datas") or payload.get("stocks") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = next((row.get(key) for key in keys if row.get(key)), None)
        if code:
            result[normalize_item_code(code)] = _flatten_row(row)
    return result


def analyze(as_of, discovery, quotes, technicals, states, full_market_signals=None,
            previous=None, name_lookup=None,
            historical_technicals=None, historical_states=None, krx_source=None):
    if discovery.get("status") not in {"AVAILABLE", "AVAILABLE_EMPTY"}:
        return {"title": "키움 전일후보군 — 당일 추적", **discovery, "stocks": []}
    qidx = _index(quotes)
    expected_selection_date = str(discovery.get("selectionDate") or "").replace("-", "")
    # Kiwoom candidate details are an independent artifact.  KRX historical
    # technicals/states are deliberately ignored here; they belong only to the
    # market technical section of the report and must never become candidate
    # indicators or selection values.
    _ = (technicals, states, full_market_signals, historical_technicals,
         historical_states, krx_source)
    previous_rows = _index(previous or {}, keys=("itemCode",))
    name_lookup = name_lookup if isinstance(name_lookup, dict) else {}
    candidate_details = {
        str(row.get("symbol")): row
        for row in (discovery.get("candidateDetails") or [])
        if isinstance(row, dict) and row.get("symbol")
    }
    quote_statuses = {
        str(row.get("symbol")): row
        for row in (quotes.get("candidateStatuses") or [])
        if isinstance(row, dict) and row.get("symbol")
    } if isinstance(quotes, dict) else {}
    projected_details = []
    rows = []
    for raw_code in discovery["symbols"]:
        code = normalize_item_code(raw_code)
        quote = qidx.get(code)
        identity_quote = quote
        rejected_quote = bool(quote and quote.get("_discoveryAdditionalLookup")
                              and (quote.get("status") not in (None, "ok")
                                   or quote.get("fresh") is False))
        if rejected_quote:
            quote = None
        market = quote or {}
        detail = candidate_details.get(code) or {}
        quote_status = quote_statuses.get(code) or {}
        candidate_copy = dict(detail)
        candidate_copy.setdefault("symbol", code)
        candidate_technical = candidate_copy.get("technicalIndicators")
        if not isinstance(candidate_technical, dict):
            candidate_technical = {}
        candidate_conditions = candidate_copy.get("conditionDetails")
        if not isinstance(candidate_conditions, dict):
            candidate_conditions = {}
        failures = candidate_copy.get("failureReason") or []
        if isinstance(failures, str):
            failures = [failures]
        failures = list(dict.fromkeys(failures))
        if not detail:
            failures.append("CANDIDATE_DETAILS_UNAVAILABLE")
        if not candidate_technical:
            failures.append("TECHNICAL_INDICATORS_UNAVAILABLE")
        if not candidate_conditions:
            failures.append("CONDITION_DETAILS_UNAVAILABLE")
        candidate_copy["failureReason"] = list(dict.fromkeys(failures))
        detail_current_price = candidate_copy.get("currentPrice")
        quote_price = market.get("closePrice", market.get("currentPrice", market.get("lastClose")))
        if quote_status.get("quoteStatus") == "AVAILABLE" or (quote and not quote_status):
            candidate_copy["currentPrice"] = (
                quote_status.get("currentPrice")
                if quote_status.get("currentPrice") is not None
                else detail_current_price if detail_current_price is not None else quote_price
            )
        else:
            candidate_copy["currentPrice"] = detail_current_price
        if quote_status:
            candidate_copy["quoteRequested"] = quote_status.get("quoteRequested")
            candidate_copy["quoteRequestTime"] = quote_status.get("quoteRequestTime")
            candidate_copy["quoteStatus"] = quote_status.get("quoteStatus")
            candidate_copy["quoteGeneratedAt"] = quote_status.get("generatedAt")
            if quote_status.get("failureReason"):
                candidate_copy["failureReason"] = list(dict.fromkeys(
                    [*candidate_copy["failureReason"], quote_status["failureReason"]]
                ))
        current_price = candidate_copy.get("currentPrice")
        has_quote = current_price is not None
        has_technical = bool(candidate_technical)
        has_conditions = bool(candidate_conditions)
        if not has_quote:
            failures = list(dict.fromkeys([*failures, "QUOTE_UNAVAILABLE"]))
            candidate_copy["failureReason"] = failures
        available_parts = sum((has_quote, has_technical, has_conditions))
        candidate_data_status = (
            "UNAVAILABLE" if not has_quote
            else "AVAILABLE" if available_parts == 3
            else "PARTIAL"
        )
        row = {
            "itemCode": code,
            "itemName": (market.get("stockName") or market.get("name")
                         or detail.get("name")
                         or (identity_quote or {}).get("stockName")
                         or name_lookup.get(code)),
            "itemNameSource": (
                "DISCOVERY_MARKET_DATA" if market.get("stockName") or market.get("name")
                else "VERIFIED_NAME_LOOKUP" if name_lookup.get(code)
                else None
            ),
            "price": current_price,
            "changeRate": market.get("fluctuationsRatio", market.get("changeRate")),
            "volume": market.get("accumulatedTradingVolume"),
            "tradingValue": market.get("accumulatedTradingValue"),
            "ma5": None, "ma20": None, "ma60": None,
            "volumeRatio20": None, "breakout20": None, "pullbackState": None,
            "selectionPrice": None,
            "selectionDate": expected_selection_date,
            "conditionStatus": candidate_conditions.get("matched", "UNAVAILABLE"),
            "nextCheck": "다음 관찰에서 현재값과 조건 충족 여부를 확인",
            "technicalMetrics": {}, "technicalSourceDate": None,
            "technicalGenerationId": None, "technicalReceiptStatus": None,
            "source": ("DISCOVERY_NAVER_LOOKUP" if quote and quote.get("_discoveryAdditionalLookup")
                       else "MONITORED_UNIVERSE" if quote
                       else None),
            "priceSourceDate": None,
            "priceSourceTime": market.get("sourceTime"),
            "priceBasis": market.get("priceBasis"),
            "rejectedQuoteReason": "STALE_OR_UNVERIFIED_QUOTE" if rejected_quote else None,
            "unavailableReason": "STALE_OR_UNVERIFIED_QUOTE" if rejected_quote else None,
        }
        row.update({
            "conditionName": candidate_conditions.get("conditionName")
            or detail.get("conditionName"),
            "conditionSequence": candidate_conditions.get("conditionId")
            or detail.get("conditionSequence"),
            "conditionResult": candidate_conditions.get("matched", detail.get("conditionResult")),
            "conditionDiscoveredAt": detail.get("discoveredAt"),
            "conditionSourceValues": candidate_conditions.get("sourceValues")
            or detail.get("sourceValues") or {},
            "conditionSourceValueStatus": candidate_conditions.get("sourceValuesStatus")
            or detail.get("sourceValueStatus") or "NOT_PROVIDED",
        })
        projected_details.append(candidate_copy)
        row["candidateDetail"] = candidate_copy
        row["candidateSelectionValue"] = candidate_copy.get("selectionValue")
        row["candidatePreviousClose"] = candidate_copy.get("previousClose")
        row["candidateTechnicalIndicators"] = candidate_technical
        row["candidateConditionDetails"] = candidate_conditions
        row["candidateSelectionStatus"] = candidate_copy.get("selectionStatus") or "UNAVAILABLE"
        row["candidateQuoteStatus"] = candidate_copy.get("quoteStatus") or "UNAVAILABLE"
        row["candidateDetailStatus"] = candidate_copy.get("detailStatus") or "UNAVAILABLE"
        row["candidateFailureReason"] = candidate_copy.get("failureReason") or []
        row["candidateSource"] = candidate_copy.get("source")
        row["candidateGeneratedAt"] = candidate_copy.get("generatedAt")
        row["unavailableReason"] = None if candidate_data_status != "UNAVAILABLE" else candidate_copy["failureReason"][0]
        row["dataStatus"] = candidate_data_status
        row["priceStatus"] = "AVAILABLE" if row["price"] is not None else "UNAVAILABLE"
        row["technicalStatus"] = "AVAILABLE" if has_technical else "UNAVAILABLE"
        row["analysisStatus"] = row["dataStatus"]
        row["status"] = row["dataStatus"]
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
    candidate_status = discovery.get("status")
    completeness = discovery.get("candidateCompleteness") or {}
    quote_completeness = quotes.get("quoteCompleteness") if isinstance(quotes, dict) else {}
    if (completeness.get("status") == "PARTIAL"
            or isinstance(quote_completeness, dict)
            and quote_completeness.get("status") == "PARTIAL"):
        candidate_status = "PARTIAL"
    return {
        "title": "키움 전일후보군 — 당일 추적", **discovery, "status": candidate_status,
        "asOf": as_of, "observedCount": available + partial,
        "missingCount": len(rows) - available - partial, "stocks": rows,
        "metrics": {"vwap": "UNAVAILABLE", "intradayHighLow": "UNAVAILABLE"},
        "selectionSnapshot": {
            "status": "NESTED_CANDIDATE_DETAILS_ONLY",
            "sourceDate": expected_selection_date,
        },
        "candidateDetails": projected_details,
        "candidateCompleteness": discovery.get("candidateCompleteness"),
        "artifactStatus": discovery.get("artifactStatus"),
        "publicationStatus": discovery.get("publicationStatus"),
    }


def fetch_missing_quotes(symbols, existing_codes, fetcher, normalizer, clock, limit=50):
    missing = [code for code in dict.fromkeys(symbols) if code not in set(existing_codes)]
    if len(missing) > limit:
        raise DiscoveryError("MISSING_QUOTE_LIMIT_EXCEEDED")
    if not missing:
        return {"networkCalls": 0, "requestedSymbols": [], "datas": [], "errors": []}
    try:
        payload = fetcher(missing)
    except Exception as exc:
        return {
            "networkCalls": 1, "requestedSymbols": missing, "datas": [],
            "errors": [{"reason": type(exc).__name__}],
        }
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


def fetch_naver_missing_quotes(symbols, existing_codes, limit=50, *, fetcher=None,
                               normalizer=None, clock=None):
    """Fetch only discovery symbols absent from existing report evidence.

    Imports are local so ordinary report generation remains offline and keeps
    the existing market-data path untouched.  Callers must opt in explicitly.
    """
    if fetcher is None or normalizer is None:
        import relay
        fetcher = fetcher or (lambda codes: relay.fetch(
            relay.QUOTE_URL + quote(",".join(codes), safe=",")))
        normalizer = normalizer or relay.normalize_quote
    clock = clock or (lambda: datetime.now(KST))
    return fetch_missing_quotes(
        symbols, existing_codes, fetcher, normalizer, clock, limit=limit)


SUPPLEMENTAL_QUOTE_FIELDS = (
    "itemCode", "stockName", "closePrice", "fluctuationsRatio",
    "accumulatedTradingVolume", "accumulatedTradingValue", "source",
    "sourceTime", "marketStatus", "session", "priceBasis", "fresh", "status",
)
SUPPLEMENTAL_QUOTE_KEYS = set(SUPPLEMENTAL_QUOTE_FIELDS)
SUPPLEMENTAL_KEYS = {
    "schemaVersion", "status", "monitoringDate", "candidateSourceSha256",
    "marketSourceTime", "generatedAt", "source", "requestedCount", "quoteCount",
    "missingSymbols", "errors", "datas", "candidateStatuses", "quoteCompleteness",
}
SUPPLEMENTAL_CANDIDATE_STATUS_KEYS = {
    "symbol", "quoteRequested", "quoteRequestTime", "currentPrice",
    "quoteStatus", "failureReason", "generatedAt", "source",
}


def _same_market_date(value, monitoring_date):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(KST).date().isoformat() == monitoring_date
    except (TypeError, ValueError):
        return False


def current_signal_codes(payload, monitoring_date):
    if not isinstance(payload, dict) or not _same_market_date(payload.get("generatedAt"), monitoring_date):
        return set()
    return set(_index(payload))


def supplemental_quote_row(row):
    return {key: row.get(key) for key in SUPPLEMENTAL_QUOTE_FIELDS}


def build_supplemental_quotes(discovery, quotes, full_market_signals, *,
                              fetcher=None, normalizer=None, clock=None, limit=50):
    if discovery.get("status") not in {"AVAILABLE", "AVAILABLE_EMPTY"}:
        raise DiscoveryError("DISCOVERY_NOT_AVAILABLE")
    monitoring_date = discovery.get("monitoringDate")
    market_source_time = quotes.get("sourceTime") if isinstance(quotes, dict) else None
    now = (clock or (lambda: datetime.now(KST)))()
    try:
        source_date = datetime.fromisoformat(str(market_source_time).replace("Z", "+00:00")).astimezone(KST).date().isoformat()
    except (TypeError, ValueError):
        source_date = None
    if (not market_source_time or quotes.get("status") != "ok"
            or quotes.get("fresh") is not True):
        raise DiscoveryError("MARKET_QUOTES_NOT_READY")
    if source_date != monitoring_date and now.astimezone(KST).time() >= time(9, 0):
        raise DiscoveryError("MARKET_QUOTES_DATE_MISMATCH")
    # The candidate artifact is the authoritative request set.  Existing
    # market quotes and full-market signals are evidence, not a reason to
    # suppress a candidate request.
    candidate_symbols = list(dict.fromkeys(discovery.get("symbols", [])))
    request_clock = clock or (lambda: datetime.now(KST))
    request_time_value = request_clock()
    request_time = (request_time_value.isoformat()
                    if hasattr(request_time_value, "isoformat") else str(request_time_value))
    result = fetch_naver_missing_quotes(
        candidate_symbols, set(), limit=limit, fetcher=fetcher,
        normalizer=normalizer, clock=clock)
    rows = [supplemental_quote_row(row) for row in result["datas"]]
    returned = {row.get("itemCode") for row in rows}
    missing = [code for code in candidate_symbols if code not in returned]
    errors = list(result["errors"])
    errors.extend({"itemCode": code, "reason": "QUOTE_MISSING"} for code in missing
                  if not any(error.get("itemCode") == code for error in errors))
    if not candidate_symbols:
        status = "AVAILABLE_EMPTY"
    elif not rows:
        status = "UNAVAILABLE"
    elif missing or errors:
        status = "PARTIAL"
    else:
        status = "AVAILABLE"
    now_value = now
    generated_at = now_value.isoformat() if hasattr(now_value, "isoformat") else str(now_value)
    error_by_code = {
        error.get("itemCode"): error.get("reason", "QUOTE_ERROR")
        for error in errors if isinstance(error, dict) and error.get("itemCode")
    }
    row_by_code = {row.get("itemCode"): row for row in rows}
    candidate_statuses = []
    for symbol in candidate_symbols:
        row = row_by_code.get(symbol)
        candidate_statuses.append({
            "symbol": symbol,
            "quoteRequested": True,
            "quoteRequestTime": request_time,
            "currentPrice": row.get("closePrice") if row else None,
            "quoteStatus": "AVAILABLE" if row else "UNAVAILABLE",
            "failureReason": None if row else error_by_code.get(symbol, "QUOTE_MISSING"),
            "generatedAt": generated_at,
            "source": "NAVER_KRX",
        })
    return {
        "schemaVersion": 1, "status": status, "monitoringDate": monitoring_date,
        "candidateSourceSha256": discovery.get("sourceSha256"),
        "marketSourceTime": market_source_time, "generatedAt": generated_at,
        "source": "NAVER_KRX", "requestedCount": len(candidate_symbols),
        "quoteCount": len(rows), "missingSymbols": missing, "errors": errors,
        "datas": rows, "candidateStatuses": candidate_statuses,
        "quoteCompleteness": {
            "status": "AVAILABLE" if len(rows) == len(candidate_symbols) else (
                "AVAILABLE_EMPTY" if not candidate_symbols else "PARTIAL"),
            "available": len(rows), "total": len(candidate_symbols),
        },
        "networkCalls": result["networkCalls"],
    }


def validate_supplemental_quotes(payload, discovery, market_source_time):
    if (not isinstance(payload, dict)
            or not SUPPLEMENTAL_KEYS.issubset(payload)
            or set(payload) - SUPPLEMENTAL_KEYS not in (set(), {"networkCalls"})):
        raise DiscoveryError("SUPPLEMENTAL_SCHEMA_INVALID")
    if (payload.get("schemaVersion") != 1 or payload.get("source") != "NAVER_KRX"
            or payload.get("monitoringDate") != discovery.get("monitoringDate")
            or payload.get("candidateSourceSha256") != discovery.get("sourceSha256")):
        raise DiscoveryError("SUPPLEMENTAL_CONTRACT_MISMATCH")
    if payload.get("marketSourceTime") != market_source_time:
        raise DiscoveryError("SUPPLEMENTAL_MARKET_TIME_MISMATCH")
    if payload.get("status") not in {"AVAILABLE", "AVAILABLE_EMPTY", "PARTIAL"}:
        raise DiscoveryError("SUPPLEMENTAL_STATUS_INVALID")
    rows = payload.get("datas")
    if not isinstance(rows, list) or any(not isinstance(row, dict) or set(row) != SUPPLEMENTAL_QUOTE_KEYS for row in rows):
        raise DiscoveryError("SUPPLEMENTAL_QUOTES_INVALID")
    candidate_statuses = payload.get("candidateStatuses")
    if (not isinstance(candidate_statuses, list)
            or any(not isinstance(row, dict) or set(row) != SUPPLEMENTAL_CANDIDATE_STATUS_KEYS
                   for row in candidate_statuses)):
        raise DiscoveryError("SUPPLEMENTAL_CANDIDATE_STATUS_INVALID")
    codes = [row.get("itemCode") for row in rows]
    try:
        monitoring_day = datetime.fromisoformat(str(payload.get("monitoringDate"))).date()
    except (TypeError, ValueError) as exc:
        raise DiscoveryError("SUPPLEMENTAL_DATE_INVALID") from exc
    for row in rows:
        try:
            row_day = datetime.fromisoformat(str(row.get("sourceTime")).replace("Z", "+00:00")).astimezone(KST).date()
        except (TypeError, ValueError) as exc:
            raise DiscoveryError("SUPPLEMENTAL_QUOTE_DATE_INVALID") from exc
        if row_day != monitoring_day:
            raise DiscoveryError("SUPPLEMENTAL_QUOTE_DATE_MISMATCH")
    missing = payload.get("missingSymbols")
    expected_symbols = list(dict.fromkeys(discovery.get("symbols", [])))
    status_codes = [row.get("symbol") for row in candidate_statuses]
    completeness = payload.get("quoteCompleteness")
    if (len(codes) != len(set(codes)) or any(code not in expected_symbols for code in codes)
            or payload.get("quoteCount") != len(rows)
            or not isinstance(payload.get("requestedCount"), int)
            or not isinstance(missing, list) or len(missing) != len(set(missing))
            or payload.get("requestedCount") != len(expected_symbols)
            or set(status_codes) != set(expected_symbols)
            or len(status_codes) != len(set(status_codes))
            or any(code not in expected_symbols for code in missing)
            or set(codes) & set(missing) or not isinstance(payload.get("errors"), list)
            or not isinstance(completeness, dict)
            or completeness.get("available") != len(rows)
            or completeness.get("total") != len(expected_symbols)
            or completeness.get("status") not in {"AVAILABLE", "AVAILABLE_EMPTY", "PARTIAL"}):
        raise DiscoveryError("SUPPLEMENTAL_QUOTES_INVALID")
    for candidate in candidate_statuses:
        if candidate.get("quoteRequested") is not True:
            raise DiscoveryError("SUPPLEMENTAL_CANDIDATE_NOT_REQUESTED")
        if candidate.get("quoteStatus") == "AVAILABLE" and candidate.get("currentPrice") is None:
            raise DiscoveryError("SUPPLEMENTAL_CANDIDATE_QUOTE_MISSING")
    return payload


def supplemental_quotes_status(discovery, market_source_time, path=None):
    path = Path(path) if path is not None else DISCOVERY_QUOTES_DIR / f"{discovery.get('monitoringDate')}.json"
    try:
        return validate_supplemental_quotes(load_json(path), discovery, market_source_time)
    except FileNotFoundError:
        return {"status": "UNAVAILABLE", "reason": "SUPPLEMENTAL_MISSING", "datas": []}
    except DiscoveryError as exc:
        reason = str(exc)
        status = "STALE" if reason in {"SUPPLEMENTAL_MARKET_TIME_MISMATCH", "SUPPLEMENTAL_QUOTE_DATE_MISMATCH"} else "INVALID"
        return {"status": status, "reason": reason, "datas": []}
    except (OSError, ValueError, json.JSONDecodeError):
        return {"status": "INVALID", "reason": "SUPPLEMENTAL_UNREADABLE", "datas": []}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot")
    parser.add_argument("--output-dir", default=str(DISCOVERY_DIR))
    parser.add_argument("--calendar", required=True)
    args = parser.parse_args()
    print(json.dumps(publish_snapshot(args.snapshot, args.output_dir, args.calendar), ensure_ascii=False))


if __name__ == "__main__":
    main()
