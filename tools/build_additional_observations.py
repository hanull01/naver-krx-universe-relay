"""Build the monitoring-owned additional industry/theme observation artifact.

Only current-run NAVER monitoring artifacts are consumed.  This producer does
not fetch data, read a previous date, or use a generic latest fallback.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SOURCE = "NAVER_CURRENT_SNAPSHOT"
SCOPE = "STOCK_ONLY"
SCHEMA_VERSION = "additional-observations-artifact-v1"
PRODUCER = "tools/build_additional_observations.py"
DEFAULT_POLICY = {
    "maxIndustries": 3,
    "maxThemes": 3,
    "minimumRelativeStrength": 0.0,
    "minimumAdvancePct": 50.0,
    "minimumMemberCount": 3,
    "minimumCoveragePct": 80.0,
    "excludeRelativeStrengthTop5": True,
}


def _date(value: Any) -> str | None:
    text = str(value or "").strip().replace("-", "")
    return text if len(text) == 8 and text.isdigit() else None


def _now() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="microseconds")


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"INPUT_NOT_OBJECT:{path}")
    return value


def _target_id(value: dict[str, Any]) -> str:
    return str(value.get("id") or value.get("code") or value.get("name") or "").strip()


def _targets(config: dict[str, Any], key: str) -> list[dict[str, Any]]:
    values = config.get(key) or []
    if not isinstance(values, list):
        raise ValueError(f"CONFIG_{key.upper()}_NOT_LIST")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError(f"CONFIG_{key.upper()}_ITEM_INVALID")
        target_id = _target_id(value)
        if not target_id:
            raise ValueError(f"CONFIG_{key.upper()}_ID_MISSING")
        if target_id in seen:
            raise ValueError(f"CONFIG_{key.upper()}_DUPLICATE:{target_id}")
        seen.add(target_id)
        result.append({"id": target_id, "name": value.get("name") or target_id})
    return result


def _input_date(payload: dict[str, Any], rows: list[dict[str, Any]]) -> str | None:
    for key in ("observationDate", "sourceDate", "tradingDate"):
        value = _date(payload.get(key))
        if value:
            return value
    for row in rows:
        for key in ("observationDate", "sourceDate", "tradingDate"):
            value = _date(row.get(key))
            if value:
                return value
    generated = str(payload.get("generatedAt") or "")
    if generated:
        try:
            return datetime.fromisoformat(generated.replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
        except ValueError:
            pass
    return None


def _find(rows: list[dict[str, Any]], target: dict[str, Any]) -> dict[str, Any] | None:
    target_id = target["id"]
    target_name = str(target.get("name") or "")
    for row in rows:
        if _target_id(row) == target_id or (target_name and str(row.get("name") or "") == target_name):
            return row
    return None


def _numeric(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _input_date_for_kind(kind: str, payload: dict[str, Any]) -> str | None:
    rows = payload.get("industries" if kind == "industry" else "themes") or []
    clean_rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    return _input_date(payload, clean_rows)


def _relative_strength(row: dict[str, Any], kind: str) -> float | None:
    if kind == "industry":
        return _numeric(row.get("relativeStrengthVsMarket")) \
            if _numeric(row.get("relativeStrengthVsMarket")) is not None \
            else _numeric(row.get("officialChangeRateRelativeStrength"))
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    return _numeric(metrics.get("relativeStrengthVsMarket")) \
        if _numeric(metrics.get("relativeStrengthVsMarket")) is not None \
        else _numeric(metrics.get("officialChangeRateRelativeStrength")) \
        if _numeric(metrics.get("officialChangeRateRelativeStrength")) is not None \
        else _numeric(row.get("officialChangeRateRelativeStrength"))


def _advance_pct(row: dict[str, Any], kind: str) -> float | None:
    if kind == "industry":
        return _numeric(row.get("advancePct"))
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    return _numeric(metrics.get("advancerPct")) \
        if _numeric(metrics.get("advancerPct")) is not None \
        else _numeric(metrics.get("advancePct"))


def _member_count(row: dict[str, Any]) -> int | None:
    value = row.get("memberCount") if row.get("memberCount") is not None else row.get("membershipCount")
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def _quality_ok(row: dict[str, Any], kind: str, policy: dict[str, Any]) -> tuple[bool, str | None]:
    member_count = _member_count(row)
    if member_count is None or member_count < int(policy["minimumMemberCount"]):
        return False, "MEMBERSHIP_BELOW_MINIMUM"
    coverage = row.get("coveragePct")
    if kind == "industry":
        coverage = row.get("featureValidMemberCount")
        if coverage is not None and member_count:
            coverage = float(coverage) * 100 / member_count
    else:
        coverage = _numeric(coverage)
    if coverage is not None and coverage < float(policy["minimumCoveragePct"]):
        return False, "INPUT_COVERAGE_BELOW_MINIMUM"
    if kind == "industry" and row.get("industrySourcePresent") is False:
        return False, "INDUSTRY_SOURCE_UNAVAILABLE"
    if kind == "theme" and str(row.get("membershipStatus") or "OK").upper() not in {"OK", "AVAILABLE", "SUCCESS"}:
        return False, "THEME_MEMBERSHIP_UNAVAILABLE"
    return True, None


def _leader_candidates(kind: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    if kind == "industry":
        values = row.get("leaders") or row.get("leaderCandidates") or []
        return [dict(value) for value in values[:3] if isinstance(value, dict)]
    values = [value for value in (row.get("stocks") or []) if isinstance(value, dict)]
    values.sort(
        key=lambda value: (
            _numeric(value.get("changeRate")) if _numeric(value.get("changeRate")) is not None else float("-inf"),
            _numeric(value.get("accumulatedTradingValue")) if _numeric(value.get("accumulatedTradingValue")) is not None else float("-inf"),
            str(value.get("code") or ""),
        ),
        reverse=True,
    )
    return [dict(value) for value in values[:3]]


def _auto_select(kind: str, payload: dict[str, Any], observation_date: str, policy: dict[str, Any]) -> dict[str, Any]:
    key = "industries" if kind == "industry" else "themes"
    rows = payload.get(key)
    if not isinstance(rows, list):
        return {"status": "INPUT_UNAVAILABLE", "reason": f"{kind.upper()}_INPUT_ROWS_INVALID", "rows": [], "excludedTop5": []}
    clean_rows = [row for row in rows if isinstance(row, dict)]
    if _input_date_for_kind(kind, payload) != observation_date:
        return {"status": "INPUT_UNAVAILABLE", "reason": f"{kind.upper()}_INPUT_DATE_MISMATCH", "rows": [], "excludedTop5": []}

    ranked_source = [row for row in clean_rows if _relative_strength(row, kind) is not None]
    ranked_source.sort(key=lambda row: (_relative_strength(row, kind), str(row.get("id") or row.get("name") or "")), reverse=True)
    top5 = [str(row.get("id") or row.get("code") or row.get("name") or "") for row in ranked_source[:5]]
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for row in clean_rows:
        identifier = str(row.get("id") or row.get("code") or row.get("name") or "").strip()
        relative = _relative_strength(row, kind)
        advance = _advance_pct(row, kind)
        member_count = _member_count(row)
        if not identifier or relative is None or advance is None or member_count is None:
            excluded.append({"id": identifier, "reason": "SELECTION_INPUT_UNAVAILABLE"})
            continue
        if policy["excludeRelativeStrengthTop5"] and identifier in top5:
            excluded.append({"id": identifier, "reason": "DUPLICATES_RELATIVE_STRENGTH_TOP5"})
            continue
        quality_ok, quality_reason = _quality_ok(row, kind, policy)
        if not quality_ok:
            excluded.append({"id": identifier, "reason": quality_reason})
            continue
        if relative < float(policy["minimumRelativeStrength"]):
            excluded.append({"id": identifier, "reason": "RELATIVE_STRENGTH_BELOW_MINIMUM"})
            continue
        if advance < float(policy["minimumAdvancePct"]):
            excluded.append({"id": identifier, "reason": "ADVANCE_PCT_BELOW_MINIMUM"})
            continue
        eligible.append({"row": row, "relativeStrength": relative, "advancePct": advance, "memberCount": member_count})

    if not eligible:
        return {"status": "NO_ELIGIBLE_TARGETS", "reason": "NO_QUALIFIED_ADDITIONAL_TARGETS", "rows": [], "excludedTop5": top5, "excluded": excluded}
    max_members = max(item["memberCount"] for item in eligible) or 1
    min_relative = min(item["relativeStrength"] for item in eligible)
    max_relative = max(item["relativeStrength"] for item in eligible)
    min_advance = min(item["advancePct"] for item in eligible)
    max_advance = max(item["advancePct"] for item in eligible)
    for item in eligible:
        relative_score = 100.0 if max_relative == min_relative else (item["relativeStrength"] - min_relative) * 100 / (max_relative - min_relative)
        advance_score = 100.0 if max_advance == min_advance else (item["advancePct"] - min_advance) * 100 / (max_advance - min_advance)
        membership_score = item["memberCount"] * 100 / max_members
        item["score"] = round(relative_score * 0.5 + advance_score * 0.3 + membership_score * 0.2, 6)
    eligible.sort(key=lambda item: (item["score"], item["relativeStrength"], item["advancePct"], item["memberCount"], str(item["row"].get("id") or "")), reverse=True)
    limit = int(policy["maxIndustries"] if kind == "industry" else policy["maxThemes"])
    selected = []
    for rank, item in enumerate(eligible[:limit], start=1):
        row = item["row"]
        selected.append({
            "id": str(row.get("id") or row.get("code") or row.get("name")),
            "name": row.get("name") or row.get("id"),
            "type": kind,
            "selectionRank": rank,
            "selectionScore": item["score"],
            "selectionReason": "CURRENT_OBSERVATION_RANKING",
            "selectionEvidence": {
                "relativeStrength": item["relativeStrength"],
                "advancePct": item["advancePct"],
                "memberCount": item["memberCount"],
                "weights": {"relativeStrength": 0.5, "advancePct": 0.3, "memberCount": 0.2},
                "source": SOURCE,
                "sourceDate": observation_date,
                "observationDate": observation_date,
                "scope": SCOPE,
            },
        })
    return {"status": "AVAILABLE", "reason": None, "rows": selected, "excludedTop5": top5, "excluded": excluded}


def _leaders(kind: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    if kind == "industry":
        values = row.get("leaders") or row.get("leaderCandidates") or []
        return [dict(value) for value in values[:3] if isinstance(value, dict)]
    values = [value for value in (row.get("stocks") or []) if isinstance(value, dict)]
    values.sort(
        key=lambda value: (
            float(value.get("changeRate")) if isinstance(value.get("changeRate"), (int, float)) else float("-inf"),
            float(value.get("accumulatedTradingValue")) if isinstance(value.get("accumulatedTradingValue"), (int, float)) else float("-inf"),
            str(value.get("code") or ""),
        ),
        reverse=True,
    )
    return [dict(value) for value in values[:3]]


def _row(*, kind: str, target: dict[str, Any], source_row: dict[str, Any] | None, date: str | None, run_id: str, generated_at: str, artifact_path: str, failure_reason: str | None = None) -> dict[str, Any]:
    leaders = _leaders(kind, source_row) if source_row else []
    member_count = source_row.get("memberCount") if source_row else None
    if source_row and member_count is None:
        member_count = source_row.get("membershipCount")
    status = "AVAILABLE" if source_row and leaders else "UNAVAILABLE"
    reason = failure_reason or ("NO_LEADER_DATA" if source_row and not leaders else None if source_row else "TARGET_OUTPUT_MISSING")
    return {
        "id": target["id"], "name": source_row.get("name") if source_row else target["name"], "type": kind,
        "source": SOURCE, "sourceDate": date, "observationDate": date, "scope": SCOPE,
        "producer": PRODUCER, "producerRunId": run_id,
        "artifactPath": f"{artifact_path}#{kind}s/{target['id']}",
        "generatedAt": generated_at, "membershipCount": member_count,
        "leaderCount": len(leaders), "leaders": leaders, "status": status, "failureReason": reason,
    }


def build_artifact(*, config: dict[str, Any], industry_input: dict[str, Any] | None, theme_input: dict[str, Any] | None, observation_date: str, run_id: str, generated_at: str, config_path: str, industry_path: str, theme_path: str, artifact_path: str = "data/monitoring/additional-observations.json") -> dict[str, Any]:
    industries = _targets(config, "industries")
    themes = _targets(config, "themes")
    auto_select = bool(config.get("autoSelect", True))
    policy = dict(DEFAULT_POLICY)
    configured_policy = config.get("policy") if isinstance(config.get("policy"), dict) else {}
    policy.update({key: value for key, value in configured_policy.items() if key in policy})

    if auto_select and not industries and not themes:
        input_payloads = {"industry": industry_input, "theme": theme_input}
        selections = {}
        for kind in ("industry", "theme"):
            payload = input_payloads[kind]
            selections[kind] = (
                _auto_select(kind, payload, observation_date, policy)
                if isinstance(payload, dict)
                else {"status": "INPUT_UNAVAILABLE", "reason": f"{kind.upper()}_INPUT_MISSING", "rows": [], "excludedTop5": []}
            )
        selected = selections["industry"]["rows"] + selections["theme"]["rows"]
        input_unavailable = any(item["status"] == "INPUT_UNAVAILABLE" for item in selections.values())
        if not selected and input_unavailable:
            overall_status = "INPUT_UNAVAILABLE"
        elif not selected:
            overall_status = "NO_ELIGIBLE_TARGETS"
        else:
            overall_status = "AVAILABLE"
        rows_by_kind: dict[str, list[dict[str, Any]]] = {"industry": [], "theme": []}
        for kind in ("industry", "theme"):
            payload = input_payloads[kind]
            source_rows = payload.get("industries" if kind == "industry" else "themes", []) if isinstance(payload, dict) else []
            source_rows = [row for row in source_rows if isinstance(row, dict)] if isinstance(source_rows, list) else []
            for target in selections[kind]["rows"]:
                source_row = _find(source_rows, target)
                rows_by_kind[kind].append(_row(
                    kind=kind, target=target, source_row=source_row, date=observation_date,
                    run_id=run_id, generated_at=generated_at, artifact_path=artifact_path,
                    failure_reason=None if source_row else "TARGET_OUTPUT_MISSING",
                ))
                rows_by_kind[kind][-1].update({
                    "selectionRank": target["selectionRank"],
                    "selectionScore": target["selectionScore"],
                    "selectionReason": target["selectionReason"],
                    "selectionEvidence": target["selectionEvidence"],
                })
        selected_rows = rows_by_kind["industry"] + rows_by_kind["theme"]
        available = sum(row["status"] == "AVAILABLE" for row in selected_rows)
        if selected_rows:
            overall_status = "AVAILABLE" if available == len(selected_rows) and not input_unavailable else "PARTIAL"
        failure_reasons = [item["reason"] for item in selections.values() if item.get("reason")]
        return {
            "schemaVersion": SCHEMA_VERSION,
            "status": overall_status,
            "source": SOURCE,
            "sourceDate": observation_date,
            "observationDate": observation_date,
            "scope": SCOPE,
            "producer": PRODUCER, "producerRunId": run_id,
            "generatedAt": generated_at,
            "artifactPath": artifact_path,
            "configPath": config_path,
            "inputPaths": {"industries": industry_path, "themes": theme_path},
            "selectionPolicy": {
                "mode": "AUTO_CURRENT_OBSERVATION",
                "criteria": policy,
                "excludedRelativeStrengthTop5": {
                    "industries": selections["industry"].get("excludedTop5", []),
                    "themes": selections["theme"].get("excludedTop5", []),
                },
                "excluded": {"industries": selections["industry"].get("excluded", []), "themes": selections["theme"].get("excluded", [])},
            },
            "industries": rows_by_kind["industry"],
            "themes": rows_by_kind["theme"],
            "counts": {"configured": len(selected_rows), "received": available, "available": available, "unavailable": len(selected_rows) - available},
            "failureReason": ";".join(dict.fromkeys(failure_reasons)) if failure_reasons else (None if overall_status == "AVAILABLE" else "NO_QUALIFIED_ADDITIONAL_TARGETS"),
        }

    if not industries and not themes:
        return {
            "schemaVersion": SCHEMA_VERSION, "status": "PRODUCER_NOT_CONFIGURED", "source": SOURCE,
            "sourceDate": observation_date, "observationDate": observation_date, "scope": SCOPE,
            "producer": PRODUCER, "producerRunId": run_id, "generatedAt": generated_at,
            "artifactPath": artifact_path, "configPath": config_path,
            "inputPaths": {"industries": industry_path, "themes": theme_path}, "industries": [], "themes": [],
            "counts": {"configured": 0, "received": 0, "available": 0, "unavailable": 0},
            "failureReason": "ADDITIONAL_OBSERVATION_TARGETS_NOT_CONFIGURED",
        }

    rows_by_kind: dict[str, list[dict[str, Any]]] = {"industry": [], "theme": []}
    input_errors: list[str] = []
    for kind, targets, payload, path in (("industry", industries, industry_input, industry_path), ("theme", themes, theme_input, theme_path)):
        source_rows = []
        kind_error: str | None = None
        if isinstance(payload, dict):
            source_rows = payload.get("industries" if kind == "industry" else "themes") or []
            if not isinstance(source_rows, list):
                kind_error = f"{kind.upper()}_INPUT_ROWS_INVALID"
                source_rows = []
            clean_rows = [value for value in source_rows if isinstance(value, dict)]
            explicit_dates = {
                value
                for value in (
                    _date(payload.get("sourceDate")),
                    _date(payload.get("observationDate")),
                    _date(payload.get("tradingDate")),
                )
                if value
            }
            if not explicit_dates:
                explicit_dates = {
                    value
                    for row in clean_rows
                    for value in (
                        _date(row.get("sourceDate")),
                        _date(row.get("observationDate")),
                        _date(row.get("tradingDate")),
                    )
                    if value
                }
            current_date = _input_date(payload, clean_rows)
            if (explicit_dates and explicit_dates != {observation_date}) or (not explicit_dates and current_date != observation_date):
                kind_error = f"{kind.upper()}_INPUT_DATE_MISMATCH"
        else:
            kind_error = f"{kind.upper()}_INPUT_MISSING"
        if kind_error:
            input_errors.append(kind_error)
        clean_rows = [value for value in source_rows if isinstance(value, dict)]
        for target in targets:
            source_row = _find(clean_rows, target) if not kind_error else None
            reason = kind_error
            rows_by_kind[kind].append(_row(kind=kind, target=target, source_row=source_row, date=observation_date if source_row else None, run_id=run_id, generated_at=generated_at, artifact_path=artifact_path, failure_reason=reason))

    rows = rows_by_kind["industry"] + rows_by_kind["theme"]
    available = sum(row["status"] == "AVAILABLE" for row in rows)
    status = "AVAILABLE" if available == len(rows) else "PARTIAL"
    failure_reason = ";".join(dict.fromkeys(input_errors)) if input_errors else (None if available == len(rows) else "ADDITIONAL_OBSERVATION_PARTIAL")
    if input_errors and not available:
        status = "PRODUCER_FAILED"
    return {
        "schemaVersion": SCHEMA_VERSION, "status": status, "source": SOURCE,
        "sourceDate": observation_date, "observationDate": observation_date, "scope": SCOPE,
        "producer": PRODUCER, "producerRunId": run_id, "generatedAt": generated_at,
        "artifactPath": artifact_path, "configPath": config_path,
        "inputPaths": {"industries": industry_path, "themes": theme_path},
        "industries": rows_by_kind["industry"], "themes": rows_by_kind["theme"],
        "counts": {"configured": len(rows), "received": sum(row["status"] == "AVAILABLE" for row in rows), "available": available, "unavailable": len(rows) - available},
        "failureReason": failure_reason,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/additional-observations.json")
    parser.add_argument("--industry-input", default="data/monitoring/latest-industries.json")
    parser.add_argument("--theme-input", default="data/monitoring/latest-theme-signals.json")
    parser.add_argument("--output", default="data/monitoring/additional-observations.json")
    parser.add_argument("--observation-date", default=None)
    parser.add_argument("--run-id", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    output = Path(args.output)
    generated_at = _now()
    run_id = str(args.run_id or os.environ.get("GITHUB_RUN_ID") or f"local-additional-{generated_at[:10]}")
    try:
        config = _load(Path(args.config))
        industry_input = _load(Path(args.industry_input))
        theme_input = _load(Path(args.theme_input))
        observation_date = _date(args.observation_date) or _input_date(industry_input, []) or _input_date(theme_input, [])
        if not observation_date:
            raise ValueError("OBSERVATION_DATE_UNAVAILABLE")
        artifact = build_artifact(config=config, industry_input=industry_input, theme_input=theme_input, observation_date=observation_date, run_id=run_id, generated_at=generated_at, config_path=args.config, industry_path=args.industry_input, theme_path=args.theme_input, artifact_path=args.output)
    except Exception as exc:
        artifact = {
            "schemaVersion": SCHEMA_VERSION, "status": "PRODUCER_FAILED", "source": SOURCE,
            "sourceDate": _date(args.observation_date), "observationDate": _date(args.observation_date), "scope": SCOPE,
            "producer": PRODUCER, "producerRunId": run_id, "generatedAt": generated_at,
            "artifactPath": args.output, "configPath": args.config,
            "inputPaths": {"industries": args.industry_input, "themes": args.theme_input},
            "industries": [], "themes": [], "counts": {"configured": 0, "received": 0, "available": 0, "unavailable": 0}, "failureReason": str(exc),
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": artifact["status"], "output": str(output), "runId": run_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
