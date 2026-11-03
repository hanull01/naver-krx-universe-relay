"""Isolated KRX-vs-existing input impact analysis for V1/V2.

This module deliberately reuses the existing industry/theme aggregators and
V1/V2 engines.  It only builds an in-memory feature overlay: NAVER current
observations remain current observations, while historical technical fields
come from a verified KRX M10 interchange generation.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dynamic_universe_manager as universe_manager
import theme_research
from tools.dynamic_universe_v2_shadow import evaluate_snapshot
from tools.full_market_monitoring_evidence import (
    aggregate_industries,
    build_theme_signals,
    derived_metrics_usable,
    mean,
    median,
    pct,
)


HISTORICAL_WINDOWS = (5, 20, 60, 120, 240)
RETURN_WINDOWS = (1, 5, 20, 60, 120, 240)
MISSING_METRIC_KEYS = (
    "ma5", "ma20", "ma60", "ma120", "ma240",
    "ret_1d", "ret_5d", "ret_20d", "ret_60d", "ret_120d", "ret_240d",
    "krx_high_breakout_prev20", "krx_high_breakout_prev60", "krx_volume_ratio_prev20",
)


class KrxImpactError(ValueError):
    """The M10 input cannot be safely used for an impact comparison."""


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        value = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return int(value) if value.is_integer() else value


def _date(value: Any) -> str:
    text = str(value or "").replace("-", "").replace("/", "")
    if len(text) != 8 or not text.isdigit():
        raise KrxImpactError("INVALID_KRX_DATE")
    return text


def _read_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KrxImpactError(f"KRX_INPUT_READ_FAILED:{Path(path).name}") from exc
    if not isinstance(value, dict):
        raise KrxImpactError("KRX_INPUT_OBJECT_REQUIRED")
    return value


def load_m10_entities(path: str | Path, *, expected_date: str | None = None) -> dict[str, Any]:
    """Load and validate a read-only M10 monitoring interchange."""
    payload = _read_json(path)
    date = _date(payload.get("date"))
    if expected_date and date != _date(expected_date):
        raise KrxImpactError("KRX_TARGET_DATE_MISMATCH")
    if payload.get("source") != "KRX_OPEN_API":
        raise KrxImpactError("KRX_SOURCE_REQUIRED")
    entities = payload.get("entities")
    if not isinstance(entities, list) or not entities:
        raise KrxImpactError("KRX_ENTITIES_REQUIRED")
    by_identity: dict[str, dict[str, Any]] = {}
    by_code_category: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_identities: list[str] = []
    duplicate_codes: list[str] = []
    category_counts: dict[str, int] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            raise KrxImpactError("KRX_ENTITY_OBJECT_REQUIRED")
        if _date(entity.get("asof")) != date:
            raise KrxImpactError("KRX_ENTITY_DATE_MISMATCH")
        category = str(entity.get("category") or "")
        code = str(entity.get("code") or "")
        identity = str(entity.get("identity") or "")
        if category not in {"stock", "etf", "index"} or not code or not identity:
            raise KrxImpactError("KRX_ENTITY_IDENTITY_INVALID")
        if identity in by_identity:
            duplicate_identities.append(identity)
        # Sector index labels can legitimately repeat across KOSPI/KOSDAQ;
        # their identity/API is the discriminator.  Instrument codes must be
        # unique within stock/ETF categories.
        if category != "index" and (code, category) in by_code_category:
            duplicate_codes.append(f"{category}:{code}")
        by_identity[identity] = entity
        by_code_category[(code, category)] = entity
        category_counts[category] = category_counts.get(category, 0) + 1
    if duplicate_identities or duplicate_codes:
        raise KrxImpactError("KRX_DUPLICATE_ENTITY")
    expected_count = payload.get("entity_count")
    if expected_count not in (None, len(entities)):
        raise KrxImpactError("KRX_ENTITY_COUNT_MISMATCH")
    return {
        "date": date,
        "entities": entities,
        "by_code_category": by_code_category,
        "category_counts": category_counts,
        "source": payload.get("source"),
        "schema": payload.get("schema"),
        "entity_count": len(entities),
    }


def _registry_by_code(registry: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None) -> dict[str, dict[str, Any]]:
    if isinstance(registry, Mapping):
        rows = registry.get("assets") or registry.get("stocks") or []
    else:
        rows = registry or []
    return {str(row.get("code")): dict(row) for row in rows if isinstance(row, Mapping) and row.get("code")}


def load_existing_feature_scope(theme_signals_path: str | Path,
                                stock_signals_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Read the existing published feature scope without network access.

    Theme rows are preferred because they carry the complete legacy feature
    shape.  Stock-signal rows fill only codes absent from that scope.
    """
    theme_payload = _read_json(theme_signals_path)
    by_code: dict[str, dict[str, Any]] = {}
    for theme in theme_payload.get("themes", []):
        for row in theme.get("stocks", []) if isinstance(theme, Mapping) else []:
            if isinstance(row, Mapping) and row.get("code"):
                by_code.setdefault(str(row["code"]), dict(row))
    if stock_signals_path:
        stock_payload = _read_json(stock_signals_path)
        for row in stock_payload.get("stocks", []):
            if isinstance(row, Mapping) and row.get("code"):
                by_code.setdefault(str(row["code"]), dict(row))
    if not by_code:
        raise KrxImpactError("EXISTING_FEATURE_SCOPE_EMPTY")
    return [by_code[code] for code in sorted(by_code)]


def _current_fields(base: Mapping[str, Any]) -> dict[str, Any]:
    """Keep NAVER quote fields and never reinterpret them as KRX history."""
    return {
        key: base.get(key)
        for key in (
            "code", "name", "market", "currentPrice", "change", "changeRate",
            "accumulatedTradingValue", "currentHigh", "snapshotGeneratedAt",
            "close_valid", "ohlc_valid", "volume_valid", "traded_bar",
        )
        if key in base
    }


def _overlay_row(base: Mapping[str, Any], entity: Mapping[str, Any], *, target_date: str,
                 current_observation_date: str) -> tuple[dict[str, Any], dict[str, Any]]:
    ma = entity.get("MA") if isinstance(entity.get("MA"), Mapping) else {}
    returns = entity.get("returns_pct") if isinstance(entity.get("returns_pct"), Mapping) else {}
    breakouts = entity.get("high_breakouts") if isinstance(entity.get("high_breakouts"), Mapping) else {}
    close = _number(entity.get("close_regular"))
    volume = _number(entity.get("official_daily_volume"))
    current_price = _number(base.get("currentPrice"))
    row = _current_fields(base)
    row.update({
        "name": base.get("name") or entity.get("name"),
        "historicalIndicatorSource": "KRX",
        "historicalFallbackAllowed": False,
        "technicalAsOfDate": target_date,
        "temporaryCurrentBarDate": current_observation_date,
        "currentObservationSource": "NAVER",
        "currentObservationDate": current_observation_date,
        "krx_regular_close": close,
        "krx_official_daily_volume": volume,
        "close_valid": close is not None and close > 0,
        # M10 interchange does not provide regular high/low.  Do not claim
        # confirmed OHLC or synthesize intraday breakout fields.
        "ohlc_valid": False,
        "volume_valid": volume is not None and volume > 0,
        "traded_bar": close is not None and close > 0 and volume is not None and volume > 0,
        "derivedMetricsUsable": True,
        "historyAdjustmentStatus": "KRX_M10_NOT_REEVALUATED",
        "historyAdjustmentReason": "KRX_GENERATION_ALREADY_VERIFIED",
        "officialReferencePrice": None,
        "canonicalPreviousClose": None,
        "historyScaleRatio": None,
        "corporateActionConfirmed": False,
        "krx_volume_ratio_prev20": _number(entity.get("volume_ratio_prev20")),
        # It is intentionally impossible to use this as NAVER's intraday
        # current_volume_ratio_20.
        "volume_ma20": None,
        "current_volume_ratio_20": None,
        "breakout_20_intraday": None,
        "breakout_60_intraday": None,
        "rolling_high_20": None,
        "rolling_high_60": None,
        "rolling_high_120": None,
        "rolling_high_240": None,
        "dist_high_20": None,
        "dist_high_60": None,
        "dist_high_120": None,
        "dist_high_240": None,
        "near_52week_high": None,
        "krx_high_breakout_prev20": breakouts.get("high_breakout_prev20"),
        "krx_high_breakout_prev60": breakouts.get("high_breakout_prev60"),
    })
    for window in HISTORICAL_WINDOWS:
        key = f"MA{window}"
        average = _number(ma.get(key))
        row[f"ma{window}"] = average
        row[f"dist_ma{window}"] = round((current_price / average - 1) * 100, 6) if current_price and average else None
    for window in RETURN_WINDOWS:
        value = _number(returns.get(f"return_{window}d_pct"))
        row[f"ret_{window}d"] = value
    for window in (20, 60, 120):
        row[f"above_ma{window}"] = current_price > row.get(f"ma{window}") if current_price and row.get(f"ma{window}") else None
    row["ma20_rising"] = None
    row["ma60_rising"] = None
    row["bullish_alignment"] = (
        row.get("ma5") is not None and row.get("ma20") is not None and row.get("ma60") is not None
        and row["ma5"] > row["ma20"] > row["ma60"]
    )
    row["provenance"] = {
        "source": "KRX_OPEN_API",
        "sourceApi": entity.get("source_api"),
        "identity": entity.get("identity"),
        "historicalDate": target_date,
        "currentObservationSource": "NAVER",
    }
    missing = {key: row.get(key) is None for key in MISSING_METRIC_KEYS}
    return row, {"code": str(entity.get("code")), "missing": missing}


def build_krx_overlay_features(
    base_rows: Iterable[Mapping[str, Any]],
    m10: Mapping[str, Any],
    *,
    expected_date: str,
    current_observation_date: str,
    registry: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Overlay KRX historical values onto an existing NAVER feature scope."""
    target = _date(expected_date)
    current_date = _date(current_observation_date)
    if str(m10.get("date")) != target:
        raise KrxImpactError("KRX_TARGET_DATE_MISMATCH")
    registry_rows = _registry_by_code(registry)
    entities = {
        str(entity.get("code")): entity
        for entity in m10.get("entities", [])
        if entity.get("category") == "stock"
    }
    base_by_code: dict[str, Mapping[str, Any]] = {}
    duplicate_base: list[str] = []
    mixed_asset_codes: list[str] = []
    for row in base_rows:
        code = str(row.get("code") or "")
        if not code:
            continue
        if code in base_by_code:
            duplicate_base.append(code)
            continue
        base_by_code[code] = row
        asset_type = str(registry_rows.get(code, {}).get("assetType") or row.get("assetType") or "STOCK").upper()
        if asset_type != "STOCK":
            mixed_asset_codes.append(code)
    output: list[dict[str, Any]] = []
    missing_codes: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    for code, base in sorted(base_by_code.items()):
        asset_type = str(registry_rows.get(code, {}).get("assetType") or base.get("assetType") or "STOCK").upper()
        if asset_type != "STOCK":
            continue
        entity = entities.get(code)
        if entity is None:
            missing_codes.append(code)
            continue
        row, diagnostic = _overlay_row(base, entity, target_date=target,
                                       current_observation_date=current_date)
        output.append(row)
        diagnostics.append(diagnostic)
    base_stock_codes = {
        code for code in base_by_code
        if str(registry_rows.get(code, {}).get("assetType") or base_by_code[code].get("assetType") or "STOCK").upper() == "STOCK"
    }
    extra_krx_codes = sorted(set(entities) - base_stock_codes)
    missing_by_field = {
        key: sum(item["missing"].get(key, False) for item in diagnostics)
        for key in MISSING_METRIC_KEYS
    }
    return output, {
        "targetDate": target,
        "currentObservationDate": current_date,
        "baseCount": len(base_by_code),
        "baseStockCount": len(base_stock_codes),
        "overlayCount": len(output),
        "m10StockCount": len(entities),
        "missingCodes": sorted(missing_codes),
        "extraKrxCodes": extra_krx_codes,
        "duplicateBaseCodes": sorted(set(duplicate_base)),
        "mixedAssetCodes": sorted(set(mixed_asset_codes)),
        "missingMetricCounts": missing_by_field,
        "volumeRatioMapping": "KRX_VOLUME_RATIO_PREV20_SEPARATE",
        "historicalFallback": False,
        "coveragePct": pct(len(output), len(base_stock_codes)),
    }


def _by_id(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(row.get("id")): row for row in rows if row.get("id") is not None}


def compare_group_rows(existing: Mapping[str, Any], krx: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    fields = (
        ("avgReturn1D" if kind == "industry" else "averageReturn1D"),
        "relativeStrengthVsMarket", "pctAboveMA20", "pctAboveMA60",
        "pctVolumeAbove20DayAverage", "advancePct", "memberCount",
        "featureValidMemberCount" if kind == "industry" else "featureCount",
    )
    left = _by_id(existing.get("industries" if kind == "industry" else "themes", []))
    right = _by_id(krx.get("industries" if kind == "industry" else "themes", []))
    rows = []
    changed = 0
    for identity in sorted(set(left) | set(right)):
        lrow = left.get(identity, {})
        rrow = right.get(identity, {})
        lmetrics = lrow.get("metrics", {}) if kind == "theme" else lrow
        rmetrics = rrow.get("metrics", {}) if kind == "theme" else rrow
        differences = {}
        for field in fields:
            lv = lmetrics.get(field)
            rv = rmetrics.get(field)
            if lv != rv:
                differences[field] = {"existing": lv, "krx": rv}
        if differences:
            changed += 1
        rows.append({"id": identity, "name": rrow.get("name") or lrow.get("name"), "differences": differences})
    return {"kind": kind, "existingCount": len(left), "krxCount": len(right),
            "changedCount": changed, "rows": rows}


def _theme_engine_input(theme_membership: Mapping[str, Any], features: list[dict[str, Any]]) -> dict[str, Any]:
    quotes = {str(row["code"]): dict(row) for row in features if row.get("code")}
    technicals = {
        code: {"above_ma20": row.get("above_ma20"), "above_ma60": row.get("above_ma60"),
               "volumeRatio20": row.get("krx_volume_ratio_prev20"),
               "historicalIndicatorSource": row.get("historicalIndicatorSource")}
        for code, row in quotes.items()
    }
    return theme_research.analyze_dynamic_themes(theme_membership, quotes, technicals,
                                                 quotes.keys())


def _v1_input(industry_payload: Mapping[str, Any], theme_membership: Mapping[str, Any],
              features: list[dict[str, Any]], *, trading_date: str,
              sector_policy: Mapping[str, Any]) -> dict[str, Any]:
    themes = _theme_engine_input(theme_membership, features)
    features_by_code = {str(row.get("code")): row for row in features if row.get("code")}
    ranked_themes = []
    for group in themes.get("groups", []):
        if not group.get("knownQuoteCount"):
            continue
        rows = [features_by_code.get(member.get("code")) for theme in theme_membership.get("themes", [])
                if str(theme.get("id")) == str(group.get("id"))
                for member in theme.get("members", [])]
        rows = [row for row in rows if row]
        above = [row for row in rows if isinstance(row.get("above_ma20"), bool)]
        metrics = {
            "relativeStrength": group.get("averageChangeRate"),
            "advancerPct": group.get("risingCount", 0) * 100 / group.get("knownQuoteCount", 1),
            "turnoverConcentrationPct": 0,
            "pctAboveMA20": pct(sum(row.get("above_ma20") is True for row in above), len(above)),
            "leaderCount": len(group.get("leaders", [])),
        }
        _, score, checks = universe_manager._sector_decision(
            {"metrics": metrics, "leaders": group.get("leaders", [])}, sector_policy
        )
        ranked_themes.append({
            **group,
            "verifiedMembership": True,
            "membershipStatus": group.get("status"),
            "systemLeaders": group.get("leaders", []),
            "score": score,
            "checks": checks,
            "metrics": metrics,
            "memberCount": group.get("memberCount"),
            "knownQuoteCount": group.get("knownQuoteCount"),
            "risingCount": group.get("risingCount"),
            "averageChangeRate": group.get("averageChangeRate"),
            "sourceTime": group.get("sourceTime"),
        })
    return {
        "tradingDate": trading_date,
        "industries": {"status": "AVAILABLE", "industries": industry_payload.get("industries", []),
                       "qualityStatus": "PARTIAL_WITH_COVERAGE", "quality": industry_payload.get("quality", {})},
        "themes": {"status": "AVAILABLE" if ranked_themes else "UNAVAILABLE",
                    "tradingDate": trading_date,
                    "periods": {"daily": {"themes": ranked_themes}},
                    "qualityStatus": "PARTIAL_WITH_COVERAGE"},
    }


def run_v1_v2_comparison(existing_features: list[dict[str, Any]], krx_features: list[dict[str, Any]],
                         *, industries: Mapping[str, Any], industry_membership: Mapping[str, Any],
                         theme_membership: Mapping[str, Any], previous: Mapping[str, Any],
                         universe: Mapping[str, Any], policy: Mapping[str, Any],
                         asset_registry: Mapping[str, Any], trading_date: str) -> dict[str, Any]:
    """Run unchanged aggregators/engines against two isolated feature sets."""
    # The published theme payload predates a few optional compatibility
    # fields.  Fill only structural defaults; never invent historical values.
    existing_features = [_canonicalize_existing_row(row) for row in existing_features]
    krx_features = [_canonicalize_existing_row(row) for row in krx_features]
    results = {}
    for label, features in (("existing", existing_features), ("krx", krx_features)):
        quality, industry_rows = aggregate_industries(
            features, industries, industry_membership
        )
        industry_payload = {"quality": quality, "industries": industry_rows}
        theme_payload = build_theme_signals(trading_date, features, theme_membership)
        context = _v1_input(
            industry_payload, theme_membership, features, trading_date=trading_date,
            sector_policy=policy.get("sector", {}),
        )
        v1 = universe_manager.build_management_result(
            context, universe, {}, previous, policy,
            generated_at="2026-11-03T00:00:00+09:00", asset_registry=asset_registry,
        )
        v2 = evaluate_snapshot(
            {"tradingDate": trading_date, **industry_payload},
            theme_payload,
            v1,
        )
        results[label] = {
            "featureCount": len(features),
            "industry": industry_payload,
            "theme": theme_payload,
            "v1": {key: v1.get(key) for key in ("sectors", "themes", "leaders", "initialObservationCandidates", "pending")},
            "v2": v2,
        }
    return {
        "existing": results["existing"],
        "krx": results["krx"],
        "industryComparison": compare_group_rows(results["existing"]["industry"], results["krx"]["industry"], kind="industry"),
        "themeComparison": compare_group_rows(results["existing"]["theme"], results["krx"]["theme"], kind="theme"),
        "v1Comparison": {
            "sectorStatusChanges": _status_changes(results["existing"]["v1"].get("sectors", []), results["krx"]["v1"].get("sectors", [])),
            "themeStatusChanges": _status_changes(results["existing"]["v1"].get("themes", []), results["krx"]["v1"].get("themes", [])),
            "leaderCodeChanges": _code_changes(results["existing"]["v1"].get("leaders", []), results["krx"]["v1"].get("leaders", [])),
        },
        "v2Comparison": {
            "statusChanges": _status_changes(results["existing"]["v2"].get("industries", []) + results["existing"]["v2"].get("themes", []),
                                               results["krx"]["v2"].get("industries", []) + results["krx"]["v2"].get("themes", [])),
        },
        "contract": {
            "historicalSource": "KRX",
            "currentObservationSource": "NAVER",
            "volumeRatio20": "UNAVAILABLE_FOR_INTRADAY; KRX volume_ratio_prev20 kept separate",
            "naverHistoricalFallback": False,
        },
    }


def _canonicalize_existing_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result.setdefault("accumulatedTradingValue", None)
    result.setdefault("ma5", None)
    result.setdefault("traded_bar", bool(result.get("close_valid") and result.get("volume_valid")))
    result.setdefault("derivedMetricsUsable", True)
    return result


def _status_changes(left: Iterable[Mapping[str, Any]], right: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    a = {str(row.get("id")): row.get("status") or row.get("v2Status") for row in left if row.get("id") is not None}
    b = {str(row.get("id")): row.get("status") or row.get("v2Status") for row in right if row.get("id") is not None}
    changes = [{"id": key, "existing": a.get(key), "krx": b.get(key)} for key in sorted(set(a) | set(b)) if a.get(key) != b.get(key)]
    return {"changedCount": len(changes), "rows": changes}


def _code_changes(left: Iterable[Mapping[str, Any]], right: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    a = {str(row.get("code")) for row in left if row.get("code")}
    b = {str(row.get("code")) for row in right if row.get("code")}
    return {"existingCount": len(a), "krxCount": len(b), "added": sorted(b - a), "removed": sorted(a - b)}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m10", required=True)
    parser.add_argument("--base", required=True, help="existing theme-signals JSON")
    parser.add_argument("--stock-signals")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--industries", required=True)
    parser.add_argument("--industry-membership", required=True)
    parser.add_argument("--theme-membership", required=True)
    parser.add_argument("--previous")
    parser.add_argument("--universe")
    parser.add_argument("--policy")
    parser.add_argument("--out", required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--current-date", required=True)
    args = parser.parse_args(argv)
    base_rows = load_existing_feature_scope(args.base, args.stock_signals)
    registry = _read_json(args.registry)
    m10 = load_m10_entities(args.m10, expected_date=args.target_date)
    overlay, diagnostics = build_krx_overlay_features(
        base_rows, m10, expected_date=args.target_date,
        current_observation_date=args.current_date, registry=registry,
    )
    industries = _read_json(args.industries)
    industry_membership = _read_json(args.industry_membership)
    theme_membership = _read_json(args.theme_membership)
    previous = _read_json(args.previous) if args.previous else {}
    universe = _read_json(args.universe) if args.universe else {"watchlists": {}}
    policy = _read_json(args.policy) if args.policy else universe_manager.load_json(universe_manager.DEFAULT_POLICY, {})
    comparison = run_v1_v2_comparison(
        base_rows, overlay, industries=industries,
        industry_membership=industry_membership, theme_membership=theme_membership,
        previous=previous, universe=universe, policy=policy,
        asset_registry=registry, trading_date=_date(args.target_date),
    )
    payload = {"diagnostics": diagnostics, "comparison": comparison}
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
