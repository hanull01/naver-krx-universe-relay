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


def _row(*, kind: str, target: dict[str, Any], source_row: dict[str, Any] | None, date: str | None, run_id: str, generated_at: str, failure_reason: str | None = None) -> dict[str, Any]:
    leaders = _leaders(kind, source_row) if source_row else []
    member_count = source_row.get("memberCount") if source_row else None
    if source_row and member_count is None:
        member_count = source_row.get("membershipCount")
    status = "AVAILABLE" if source_row and leaders else "UNAVAILABLE"
    reason = failure_reason or ("NO_LEADER_DATA" if source_row and not leaders else "TARGET_OUTPUT_MISSING")
    return {
        "id": target["id"], "name": source_row.get("name") if source_row else target["name"], "type": kind,
        "source": SOURCE, "sourceDate": date, "observationDate": date, "scope": SCOPE,
        "producerRunId": run_id, "generatedAt": generated_at, "membershipCount": member_count,
        "leaderCount": len(leaders), "leaders": leaders, "status": status, "failureReason": reason,
    }


def build_artifact(*, config: dict[str, Any], industry_input: dict[str, Any] | None, theme_input: dict[str, Any] | None, observation_date: str, run_id: str, generated_at: str, config_path: str, industry_path: str, theme_path: str) -> dict[str, Any]:
    industries = _targets(config, "industries")
    themes = _targets(config, "themes")
    if not industries and not themes:
        return {
            "schemaVersion": SCHEMA_VERSION, "status": "PRODUCER_NOT_CONFIGURED", "source": SOURCE,
            "sourceDate": observation_date, "observationDate": observation_date, "scope": SCOPE,
            "producerRunId": run_id, "generatedAt": generated_at, "configPath": config_path,
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
            rows_by_kind[kind].append(_row(kind=kind, target=target, source_row=source_row, date=observation_date if source_row else None, run_id=run_id, generated_at=generated_at, failure_reason=reason))

    rows = rows_by_kind["industry"] + rows_by_kind["theme"]
    available = sum(row["status"] == "AVAILABLE" for row in rows)
    status = "AVAILABLE" if available == len(rows) else "PARTIAL"
    failure_reason = ";".join(dict.fromkeys(input_errors)) if input_errors else (None if available == len(rows) else "ADDITIONAL_OBSERVATION_PARTIAL")
    if input_errors and not available:
        status = "PRODUCER_FAILED"
    return {
        "schemaVersion": SCHEMA_VERSION, "status": status, "source": SOURCE,
        "sourceDate": observation_date, "observationDate": observation_date, "scope": SCOPE,
        "producerRunId": run_id, "generatedAt": generated_at, "configPath": config_path,
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
        artifact = build_artifact(config=config, industry_input=industry_input, theme_input=theme_input, observation_date=observation_date, run_id=run_id, generated_at=generated_at, config_path=args.config, industry_path=args.industry_input, theme_path=args.theme_input)
    except Exception as exc:
        artifact = {
            "schemaVersion": SCHEMA_VERSION, "status": "PRODUCER_FAILED", "source": SOURCE,
            "sourceDate": _date(args.observation_date), "observationDate": _date(args.observation_date), "scope": SCOPE,
            "producerRunId": run_id, "generatedAt": generated_at, "configPath": args.config,
            "inputPaths": {"industries": args.industry_input, "themes": args.theme_input},
            "industries": [], "themes": [], "counts": {"configured": 0, "received": 0, "available": 0, "unavailable": 0}, "failureReason": str(exc),
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": artifact["status"], "output": str(output), "runId": run_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
