"""Stateful, offline dynamic Universe selection.

The engine consumes published full-market/context payloads and never performs
network I/O or mutates ``config/universe.json``.  Its state/result/history
files are deliberately separate from user-managed watchlists.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from dynamic_universe_symbols import normalize_symbol


ROOT = Path(__file__).resolve().parent
DEFAULT_POLICY = ROOT / "config/dynamic_universe_policy.json"
AUTO_SOURCES = ("AUTO_SECTOR", "AUTO_THEME", "AUTO_LEADER", "KIWOOM_DISCOVERY")
STATUSES = ("DISCOVERED", "ACTIVE", "WATCH", "REMOVED", "REENTERED")
NON_EQUITY_LEADER_TYPES = {"ETF", "ETN", "FUND", "REIT", "SPAC", "INFRASTRUCTURE_FUND"}


def _upper(value):
    return str(value or "").strip().upper()


def _asset_registry_rows(asset_registry):
    """Return structured asset rows without treating names or symbol syntax as types."""
    if isinstance(asset_registry, dict):
        if isinstance(asset_registry.get("assets"), list):
            return asset_registry["assets"]
        rows = []
        for key in ("stocks", "etfs", "etns"):
            if isinstance(asset_registry.get(key), list):
                rows.extend(asset_registry[key])
        if rows:
            return rows
        return [row for row in asset_registry.values() if isinstance(row, dict)]
    return asset_registry if isinstance(asset_registry, list) else []


def leader_asset_type(candidate, asset_registry=None):
    """Classify a leader from structured source fields; unknown is fail-closed."""
    candidate = candidate if isinstance(candidate, dict) else {}
    code = _code(candidate)
    source = next((row for row in _asset_registry_rows(asset_registry)
                   if isinstance(row, dict) and _code(row) == code), {})
    # Candidate fields are allowed to refine the registry row, while the
    # registry remains the source for candidates that only contain signals.
    metadata = {**source, **{key: value for key, value in candidate.items()
                             if value is not None}}
    asset_type = _upper(metadata.get("assetType") or metadata.get("securityType"))
    stock_type = _upper(metadata.get("stockType") or metadata.get("type"))
    tags = {_upper(tag) for tag in (metadata.get("tags") or [])}

    if asset_type in NON_EQUITY_LEADER_TYPES:
        return asset_type
    if asset_type in {"ETF", "ETN"}:
        return asset_type
    if stock_type == "PF" or stock_type in {"MF"}:
        return "FUND"
    if stock_type == "IF":
        return "INFRASTRUCTURE_FUND"
    if stock_type == "RT" or "REIT" in tags:
        return "REIT"
    if "SPAC" in tags or stock_type == "SPAC":
        return "SPAC"
    if asset_type == "STOCK" and stock_type in {"", "ST", "FS", "DR"}:
        # Preferred shares remain STOCK; PREFERRED_LIKE is deliberately only
        # a subtype tag and never a name-based exclusion.
        return "STOCK"
    if asset_type == "STOCK" and stock_type:
        return "UNKNOWN"
    return "UNKNOWN"


def _candidate_trading_date(candidate):
    for key in ("tradingDate", "tradeDate", "marketDate", "sourceDate", "lastConfirmedDailyDate"):
        value = _normalize_trading_date((candidate or {}).get(key))
        if value:
            return value
    evidence = (candidate or {}).get("technicalEvidence") or {}
    return _normalize_trading_date(evidence.get("lastConfirmedDailyDate"))


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return default


def atomic_json(path, payload):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return path


def _number(value, default=None):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


def _code(row):
    value = (row.get("code") or row.get("itemCode") or row.get("symbol")) if isinstance(row, dict) else row
    return normalize_symbol(str(value)) if value else None


def _sector_decision(row, policy):
    metrics = row.get("metrics") or row
    checks = {
        "relativeStrength": (_number(metrics.get("relativeStrength"), _number(metrics.get("relativeStrengthVsMarket"), _number(metrics.get("changeRate")))) or 0) >= policy["minimumRelativeStrength"],
        "advancerPct": (_number(metrics.get("advancerPct"), _number(metrics.get("advancePct"), 0)) or 0) >= policy["minimumAdvancerPct"],
        "turnoverConcentrationPct": (_number(metrics.get("turnoverConcentrationPct"), _number(metrics.get("pctVolumeAbove20DayAverage"), 0)) or 0) >= policy["minimumTurnoverConcentrationPct"],
        "pctAboveMA20": (_number(metrics.get("pctAboveMA20"), 0) or 0) >= policy["minimumPctAboveMA20"],
        "leaderCount": int(_number(metrics.get("leaderCount"), len(row.get("leaders") or [])) or 0) >= policy["minimumLeaderCount"],
    }
    score = _number(row.get("score"), _number(metrics.get("score")))
    if score is None:
        score = round(sum(checks.values()) * 100 / len(checks), 2)
    return score >= policy["minimumScore"] and all(checks.values()), score, checks


def _theme_decision(row, policy):
    verified = bool(row.get("verifiedMembership") or row.get("membershipStatus") in {"VERIFIED", "AVAILABLE"})
    score = _number(row.get("score"), _number(row.get("changeRate"), 0)) or 0
    checks = {"verifiedMembership": verified, "minimumScore": score >= policy["minimumScore"]}
    return (not policy.get("requireVerifiedMembership") or verified) and checks["minimumScore"], score, checks


def _leader_decision(row, policy, asset_registry=None, observed_trading_date=None):
    technical = row.get("technical") or {}
    score = _number(row.get("score"), _number(technical.get("score")))
    if row.get("decision") is True:
        score = 100
    if score is None:
        positives = [
            (_number(row.get("changeRate"), 0) or 0) > 0,
            technical.get("aboveMA20") is True or technical.get("priceVsMA20") == "above" or row.get("above_ma20") is True,
            technical.get("breakout20") in {"attempt", "confirmed"} or row.get("breakout_20_intraday") is True,
        ]
        score = round(sum(positives) * 100 / len(positives), 2)
    persistence = int(_number(row.get("persistence"), 1) or 0)
    asset_type = leader_asset_type(row, asset_registry)
    candidate_date = _candidate_trading_date(row)
    date_verified = bool(observed_trading_date) and (not candidate_date or candidate_date == observed_trading_date)
    checks = {
        "minimumScore": score >= policy["minimumScore"],
        "minimumPersistence": persistence >= policy["minimumPersistence"],
        "assetType": asset_type,
        "assetTypeEligible": asset_type == "STOCK",
        "tradingDateVerified": date_verified,
    }
    return all(checks.values()), score, checks


def _normalize_trading_date(value):
    if value is None:
        return None
    normalized = str(value).replace("-", "").replace("/", "")
    if len(normalized) != 8 or not normalized.isdigit():
        return None
    try:
        datetime.strptime(normalized, "%Y%m%d")
    except ValueError:
        return None
    return normalized


def _trading_date(context):
    for key in ("tradingDate", "tradeDate", "marketDate"):
        value = _normalize_trading_date((context or {}).get(key))
        if value:
            return value
    for block_name in ("fullMarket", "baseline", "evidence"):
        block = (context or {}).get(block_name) or {}
        for key in ("tradingDate", "tradeDate", "marketDate"):
            value = _normalize_trading_date(block.get(key))
            if value:
                return value
    return None
def _transition(previous, qualified, policy):
    previous = previous or {}
    observed_date = policy.get("_observedTradingDate")
    last_date = str(previous.get("lastObservedTradingDate") or "")
    if not observed_date:
        return previous.get("status", "DISCOVERED"), previous.get("qualifyStreak", 0), previous.get("weakenStreak", 0)
    if last_date and observed_date <= last_date:
        return previous.get("status", "DISCOVERED"), previous.get("qualifyStreak", 0), previous.get("weakenStreak", 0)
    was_managed = previous.get("status") in {"ACTIVE", "WATCH", "REENTERED"}
    qualify_streak = (previous.get("qualifyStreak", 0) + 1) if qualified else 0
    weaken_streak = 0 if qualified else previous.get("weakenStreak", 0) + 1
    if qualified:
        if was_managed: status = "ACTIVE"
        elif previous.get("status") == "REMOVED" and qualify_streak >= policy["activationConfirmations"]: status = "REENTERED"
        elif qualify_streak >= policy["activationConfirmations"]: status = "ACTIVE"
        else: status = "DISCOVERED"
    elif was_managed and weaken_streak < policy["removalConfirmations"]: status = "WATCH"
    else: status = "REMOVED"
    return status, qualify_streak, weaken_streak


def _previous_index(previous, key):
    return {str(row.get("id")): row for row in (previous or {}).get(key, []) if isinstance(row, dict)}


def _managed(status):
    return status in {"ACTIVE", "WATCH", "REENTERED"}


def build_management_result(context, universe, discovery=None, previous=None, policy=None, generated_at=None,
                            asset_registry=None):
    """Select qualified groups/leaders and retain hysteresis state."""
    policy = policy or load_json(DEFAULT_POLICY, {})
    observed_trading_date = _trading_date(context)
    if observed_trading_date:
        policy = {**policy, "_observedTradingDate": observed_trading_date}
    generated_at = generated_at or datetime.now().astimezone().isoformat()
    previous = previous or {}
    sector_previous = _previous_index(previous, "sectors")
    theme_previous = _previous_index(previous, "themes")
    leader_previous = _previous_index(previous, "leaders")
    sectors, themes, leaders, initial_candidates, pending, changes = [], [], [], [], [], []
    blocked_leaders = {}

    def record_block(group_type, group_id, candidate, checks):
        code = _code(candidate)
        if not code:
            return
        asset_type = checks.get("assetType")
        if not checks.get("assetTypeEligible"):
            reason = "LEADER_ASSET_TYPE_UNVERIFIED" if asset_type == "UNKNOWN" else "NON_EQUITY_ASSET_TYPE"
        elif not checks.get("tradingDateVerified"):
            candidate_date = _candidate_trading_date(candidate)
            reason = "LEADER_TRADING_DATE_MISMATCH" if candidate_date else "LEADER_TRADING_DATE_UNVERIFIED"
        else:
            return
        identity = f"{group_type}:{group_id}:{code}"
        if identity in blocked_leaders:
            return
        blocked_leaders[identity] = {"reason": reason, "assetType": asset_type, "code": code}
        pending.append({"type": "AUTO_LEADER", "id": identity, "code": code,
                        "status": "PENDING", "reason": reason, "assetType": asset_type})

    industry_block = (context or {}).get("industries") or {}
    industry_rows = industry_block.get("industries") or industry_block.get("strong") or []
    for row in industry_rows:
        identity = str(row.get("id") or row.get("name") or "")
        if not identity: continue
        qualified, score, checks = _sector_decision(row, policy["sector"])
        old = sector_previous.get(identity)
        status, qs, ws = _transition(old, qualified, policy)
        entry = {"id": identity, "name": row.get("name") or identity, "status": status, "score": score,
                 "qualifyStreak": qs, "weakenStreak": ws, "checks": checks,
                 "source": "FULL_MARKET_EVIDENCE", "sourceTime": row.get("sourceTime") or context.get("generatedAt"),
                 "evidenceQuality": industry_block.get("qualityStatus") or "UNKNOWN"}
        sectors.append(entry)
        if not old or old.get("status") != status: changes.append({"type": "AUTO_SECTOR", "id": identity, "from": (old or {}).get("status"), "to": status})
        # Asset disqualification is independent of industry/date usability.
        # This prevents an already-active non-equity leader from being frozen
        # merely because the current market evidence is partial or undated.
        for candidate in row.get("leaders") or []:
            if leader_asset_type(candidate, asset_registry) != "STOCK":
                _, _, candidate_checks = _leader_decision(
                    candidate, policy["leader"], asset_registry, observed_trading_date)
                record_block("sector", identity, candidate, candidate_checks)
        evidence_usable = ((industry_block.get("qualityStatus") or "").upper() in {"FULL", "OK"}
                           or (industry_block.get("quality") or {}).get("industryEvidenceUsable") in {"FULL", "OK"})
        if status == "DISCOVERED" and evidence_usable:
            accepted_initial = []
            for candidate in row.get("leaders") or []:
                ok, candidate_score, candidate_checks = _leader_decision(
                    candidate, policy["leader"], asset_registry, observed_trading_date)
                if ok:
                    accepted_initial.append((candidate_score, candidate, candidate_checks))
                else:
                    record_block("sector", identity, candidate, candidate_checks)
            for candidate_score, candidate, candidate_checks in sorted(
                    accepted_initial, key=lambda item: (-item[0], _code(item[1]) or ""))[:3]:
                code = _code(candidate)
                if code:
                    initial_candidates.append({"id": f"initial:{identity}:{code}", "code": code,
                        "name": candidate.get("name"), "groupType": "sector", "groupId": identity,
                        "score": candidate_score, "assetType": candidate_checks.get("assetType"),
                        "checks": candidate_checks,
                        "status": "INITIAL_OBSERVATION", "source": "INITIAL_OBSERVATION",
                        "sourceTime": candidate.get("sourceTime") or row.get("sourceTime") or context.get("generatedAt")})
            if not accepted_initial:
                pending.append({"type": "INITIAL_OBSERVATION", "groupId": identity,
                                "status": "PENDING", "reason": "NO_VALID_GROUP_CANDIDATE"})
        elif status == "DISCOVERED":
            pending.append({"type": "INITIAL_OBSERVATION", "groupId": identity,
                            "status": "PENDING", "reason": "INDUSTRY_EVIDENCE_NOT_USABLE"})
        if _managed(status):
            accepted = []
            for candidate in row.get("leaders") or []:
                ok, leader_score, leader_checks = _leader_decision(
                    candidate, policy["leader"], asset_registry, observed_trading_date)
                if ok: accepted.append((leader_score, candidate, leader_checks))
                else: record_block("sector", identity, candidate, leader_checks)
            for leader_score, candidate, leader_checks in sorted(accepted, key=lambda item: (-item[0], _code(item[1]) or ""))[:policy["leaderLimitPerGroup"]]:
                code = _code(candidate)
                if code: leaders.append({"id": f"sector:{identity}:{code}", "code": code, "name": candidate.get("name"), "groupType": "sector", "groupId": identity, "score": leader_score, "assetType": leader_checks.get("assetType"), "checks": leader_checks, "source": "AUTO_LEADER"})

    theme_block = (context or {}).get("themes") or {}
    if theme_block.get("status") != "AVAILABLE":
        pending.append({"type": "AUTO_THEME", "status": "PENDING", "reason": theme_block.get("reason") or "VERIFIED_THEME_DATA_UNAVAILABLE"})
    else:
        theme_rows = ((theme_block.get("periods") or {}).get("daily") or {}).get("themes") or []
        current_theme_ids = set()
        for row in theme_rows:
            identity = str(row.get("id") or row.get("name") or "")
            if not identity: continue
            current_theme_ids.add(identity)
            qualified, score, checks = _theme_decision(row, policy["theme"])
            old = theme_previous.get(identity)
            status, qs, ws = _transition(old, qualified, policy)
            entry = {"id": identity, "name": row.get("name") or identity, "status": status, "score": score,
                     "qualifyStreak": qs, "weakenStreak": ws, "checks": checks, "source": "VERIFIED_THEME_EVIDENCE"}
            themes.append(entry)
            if not old or old.get("status") != status: changes.append({"type": "AUTO_THEME", "id": identity, "from": (old or {}).get("status"), "to": status})
            if _managed(status):
                accepted = []
                for candidate in row.get("systemLeaders") or []:
                    ok, leader_score, leader_checks = _leader_decision(
                        candidate, policy["leader"], asset_registry, observed_trading_date)
                    if ok: accepted.append((leader_score, candidate, leader_checks))
                    else: record_block("theme", identity, candidate, leader_checks)
                for leader_score, candidate, leader_checks in sorted(accepted, key=lambda item: (-item[0], _code(item[1]) or ""))[:policy["leaderLimitPerGroup"]]:
                    code = _code(candidate)
                    if code: leaders.append({"id": f"theme:{identity}:{code}", "code": code, "name": candidate.get("name"), "groupType": "theme", "groupId": identity, "score": leader_score, "assetType": leader_checks.get("assetType"), "checks": leader_checks, "source": "AUTO_LEADER"})
        for identity, old in theme_previous.items():
            if identity not in current_theme_ids:
                themes.append({**old, "dataQuality": "LIST_NOT_FOUND", "selectionDeferred": True,
                               "lastVerifiedAt": old.get("sourceTime") or previous.get("generatedAt")})

    normalized_leaders = []
    for row in leaders:
        old = leader_previous.get(row["id"])
        status, qs, ws = _transition(old, True, policy)
        normalized_leaders.append({**row, "status": status, "qualifyStreak": qs, "weakenStreak": ws})
        if not old or old.get("status") != status: changes.append({"type": "AUTO_LEADER", "id": row["id"], "from": (old or {}).get("status"), "to": status})
    current_ids = {row["id"] for row in normalized_leaders}
    for identity, old in leader_previous.items():
        if identity in current_ids: continue
        blocked = blocked_leaders.get(identity)
        if blocked:
            # A known non-equity candidate is hard-removed immediately; an
            # unverified type/date is retained only as non-admitted audit state.
            if blocked["reason"] == "NON_EQUITY_ASSET_TYPE":
                row = {**old, "status": "REMOVED", "qualifyStreak": 0, "weakenStreak": old.get("weakenStreak", 0),
                       "selectionBlocked": True, "selectionBlockReason": blocked["reason"],
                       "assetType": blocked["assetType"], "collectionAdmitted": False}
                normalized_leaders.append(row)
                if old.get("status") != "REMOVED":
                    changes.append({"type": "AUTO_LEADER", "id": identity, "from": old.get("status"), "to": "REMOVED"})
                continue
        status, qs, ws = _transition(old, False, policy)
        row = {**old, "status": status, "qualifyStreak": qs, "weakenStreak": ws,
               "selectionDeferred": bool(blocked), "selectionBlockReason": blocked.get("reason") if blocked else old.get("selectionBlockReason"),
               "assetType": blocked.get("assetType") if blocked else old.get("assetType"),
               "collectionAdmitted": False if blocked else old.get("collectionAdmitted")}
        normalized_leaders.append(row)
        if old.get("status") != status: changes.append({"type": "AUTO_LEADER", "id": identity, "from": old.get("status"), "to": status})

    discovery_rows = []
    if isinstance(discovery, dict) and discovery.get("status") in {"AVAILABLE", "AVAILABLE_EMPTY"}:
        discovery_rows = [{"code": code, "source": discovery.get("source"), "sourceGroup": discovery.get("source_group") or discovery.get("sourceGroup"), "status": "DISCOVERED"}
                          for item in discovery.get("symbols", []) if (code := _code(item))]
    watchlists = (universe or {}).get("watchlists") or {}
    user_watchlists = {name: list(codes) for name, codes in watchlists.items() if name != "legacy33"}
    memberships = {}
    def member(code, source, reason):
        row = memberships.setdefault(code, {"code": code, "sources": [], "reasons": []})
        if source not in row["sources"]: row["sources"].append(source)
        if reason not in row["reasons"]: row["reasons"].append(reason)
    # User and discovery ownership is never truncated.  Automatic leaders are
    # admitted by score only within the configured collection load budget.
    for row in discovery_rows: member(row["code"], "KIWOOM_DISCOVERY", row.get("sourceGroup") or row.get("source") or "KIWOOM")
    for name, codes in user_watchlists.items():
        for code in codes: member(str(code), "USER_WATCHLIST", name)
    limit = int(policy.get("collectionLimit", 120))
    admitted_auto_codes = set()
    for row in sorted((row for row in normalized_leaders
                       if _managed(row["status"]) and not row.get("selectionBlocked") and not row.get("selectionDeferred")),
                      key=lambda item: (-item.get("score", 0), item["code"], item["id"])):
        if row["code"] not in memberships and len(memberships) >= limit: continue
        admitted_auto_codes.add(row["code"])
        member(row["code"], "AUTO_LEADER", f"{row['groupType']}:{row['groupId']}")
    for row in normalized_leaders:
        row["collectionAdmitted"] = row.get("code") in admitted_auto_codes
    for collection, previous_index in ((sectors, sector_previous), (themes, theme_previous), (normalized_leaders, leader_previous)):
        for row in collection:
            prior = previous_index.get(row.get("id"), {})
            prior_date = _normalize_trading_date(prior.get("lastObservedTradingDate"))
            if observed_trading_date and (not prior_date or observed_trading_date > prior_date):
                row["lastObservedTradingDate"] = observed_trading_date
            elif prior_date:
                row["lastObservedTradingDate"] = prior_date
    return {"version": "dynamic-universe-management-v1", "generatedAt": generated_at, "networkCalls": 0,
            "industryEvidence": {"status": industry_block.get("qualityStatus") or "UNKNOWN",
                                  "sourceStatus": (industry_block.get("quality") or {}).get("sourceStatus"),
                                  "industryEvidenceUsable": (industry_block.get("quality") or {}).get("industryEvidenceUsable"),
                                  "analyzedIndustryCount": len(industry_rows)},
            "sectors": sectors, "themes": themes, "leaders": normalized_leaders,
            "initialObservationCandidates": initial_candidates, "kiwoomDiscovery": discovery_rows,
            "userWatchlists": user_watchlists, "memberships": [memberships[code] for code in sorted(memberships)],
            "collectionCodes": sorted(memberships), "pending": pending, "changes": changes,
            "policy": policy, "load": {"limit": limit, "count": len(memberships),
            "limitExceededByProtectedSources": max(0, len(memberships) - limit)},
            "safety": {"productionUniverseMutated": False, "userWatchlistsMutated": False,
            "legacyBaselinePreserved": True, "deduplicateCollectionBy": "itemCode"}}


def publish_result(result, result_path, state_path=None, history_path=None):
    """Atomically publish staged artifacts; caller chooses non-production paths."""
    atomic_json(result_path, result)
    atomic_json(state_path or result_path, result) if state_path and Path(state_path) != Path(result_path) else None
    if history_path:
        history = load_json(history_path, {"version": "dynamic-universe-history-v1", "events": []})
        history["events"].extend({**change, "at": result["generatedAt"]} for change in result.get("changes", []))
        atomic_json(history_path, history)
    return result
