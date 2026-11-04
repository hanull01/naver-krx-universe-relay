#!/usr/bin/env python3
"""Validate an optional KRX daily-input package for rolling-state updates.

The rolling-state workflow must never turn a KRX technical generation into a
daily OHLCV input implicitly.  Only an explicit, complete package with matching
generation provenance is accepted here.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping


DATE_RE = re.compile(r"^\d{8}$")


class DailyInputError(ValueError):
    pass


def _date(value: Any, label: str) -> str:
    text = str(value or "").replace("-", "")
    if not DATE_RE.fullmatch(text):
        raise DailyInputError(f"INVALID_{label.upper()}")
    return text


def _number(value: Any, label: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool):
        raise DailyInputError(f"INVALID_{label}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise DailyInputError(f"INVALID_{label}") from None
    if not math.isfinite(number) or (number < 0 if allow_zero else number <= 0):
        raise DailyInputError(f"INVALID_{label}")
    return number


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise DailyInputError(f"{label}_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise DailyInputError(f"{label}_OBJECT_REQUIRED")
    return value


def validate_daily_input(
    input_path: str | Path,
    metadata_path: str | Path,
    *,
    expected_date: str,
    expected_generation: str,
) -> dict[str, Any]:
    payload = _load(Path(input_path), "DAILY_INPUT")
    metadata = _load(Path(metadata_path), "KRX_RESOLUTION")
    target = _date(expected_date, "expected_date")

    if payload.get("source") not in {"KRX_OPEN_API", "KRX_M10_GENERATION"}:
        raise DailyInputError("DAILY_INPUT_SOURCE_NOT_KRX")
    if payload.get("historicalIndicatorSource") != "KRX":
        raise DailyInputError("DAILY_INPUT_HISTORICAL_SOURCE_INVALID")
    if payload.get("historicalFallbackAllowed") is not False:
        raise DailyInputError("DAILY_INPUT_NAVER_FALLBACK_ALLOWED")
    if payload.get("receiptStatus") != "VERIFIED" or payload.get("publicationAuthorized") is not True:
        raise DailyInputError("DAILY_INPUT_RECEIPT_NOT_VERIFIED")

    generation = str(payload.get("generationId") or payload.get("sourceGeneration") or "")
    if generation != expected_generation or generation != str(metadata.get("sourceGeneration") or ""):
        raise DailyInputError("DAILY_INPUT_GENERATION_MISMATCH")
    if _date(payload.get("targetDate") or payload.get("tradingDate"), "daily_input_date") != target:
        raise DailyInputError("DAILY_INPUT_DATE_MISMATCH")
    if _date(metadata.get("targetDate"), "resolution_date") != target:
        raise DailyInputError("RESOLUTION_DATE_MISMATCH")
    for key in ("manifestSha256", "monitoringSha256"):
        if payload.get(key) != metadata.get(key):
            raise DailyInputError(f"DAILY_INPUT_{key.upper()}_MISMATCH")

    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise DailyInputError("DAILY_INPUT_ROWS_MISSING")
    expected_count = payload.get("expectedRowCount")
    if expected_count is None:
        expected_count = payload.get("rowCount")
    if expected_count is None and isinstance(payload.get("counts"), Mapping):
        expected_count = payload["counts"].get("stockCount")
    if not isinstance(expected_count, int) or expected_count <= 0 or len(rows) != expected_count:
        raise DailyInputError("DAILY_INPUT_COVERAGE_UNVERIFIED")

    codes: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise DailyInputError("DAILY_INPUT_ROW_INVALID")
        code = str(row.get("code") or row.get("itemCode") or "")
        if not code or code in codes:
            raise DailyInputError("DAILY_INPUT_DUPLICATE_CODE")
        codes.add(code)
        if _date(row.get("date") or row.get("asOfDate"), "row_date") != target:
            raise DailyInputError("DAILY_INPUT_ROW_DATE_MISMATCH")
        _number(row.get("close"), "close")
        _number(row.get("high"), "high")
        _number(row.get("volume"), "volume", allow_zero=True)

    return {
        "status": "VERIFIED",
        "path": str(Path(input_path)),
        "source": payload["source"],
        "generationId": generation,
        "targetDate": target,
        "rowCount": len(rows),
        "manifestSha256": payload["manifestSha256"],
        "monitoringSha256": payload["monitoringSha256"],
        "historicalSource": "KRX",
        "historicalFallbackAllowed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an explicit KRX daily input package")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--expected-date", required=True)
    parser.add_argument("--generation-id", required=True)
    args = parser.parse_args(argv)
    try:
        result = validate_daily_input(
            args.input,
            args.metadata,
            expected_date=args.expected_date,
            expected_generation=args.generation_id,
        )
    except (DailyInputError, OSError) as exc:
        print(f"KRX_DAILY_INPUT_REJECTED:{exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
