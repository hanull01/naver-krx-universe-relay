"""Build the compact market-snapshot input consumed by the report renderer.

The NAVER collector keeps the complete source payloads under ``data/market``.
Only a small, deterministic projection crosses into the report contract so a
large ranking or investor-flow response cannot make the contract too large.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
MARKET_DIR = ROOT / "data" / "market"
DEFAULT_OUTPUT = MARKET_DIR / "report-snapshot-projection.json"
MAX_RANKING_ROWS = 5
MAX_FLOW_ROWS = 5
MAX_INDUSTRIES = 5


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"status": "UNAVAILABLE", "error": {"reason": "SOURCE_UNAVAILABLE", "path": str(path)}}
    return payload if isinstance(payload, dict) else {"status": "UNAVAILABLE", "error": {"reason": "SOURCE_NOT_OBJECT"}}


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _stock(row: Mapping[str, Any], *, investor: bool = False) -> dict[str, Any]:
    result = {
        "code": row.get("code") or row.get("itemcode"),
        "name": row.get("name") or row.get("itemname"),
        "price": _number(row.get("price") if "price" in row else row.get("nowPrice")),
        "changeRate": _number(row.get("changeRate") if "changeRate" in row else row.get("prevChangeRate")),
    }
    if investor:
        source_field = row.get("accTradeAmountSourceField") or "accTradeAmount"
        source_status = row.get("accTradeAmountSourceStatus")
        amount = _number(row.get("accTradeAmount"))
        result.update({
            "volume": _number(row.get("accTradeVolume")),
            "from": row.get("bizdateFrom"),
            "to": row.get("bizdateTo"),
        })
        if "accTradeAmount" in row and amount is not None and source_status != "UNAVAILABLE":
            result["amount"] = amount
            result["amountStatus"] = "SOURCE_ZERO" if amount == 0 else "AVAILABLE"
            result["amountSourceField"] = source_field
            if source_status:
                result["amountSourceStatus"] = source_status
        else:
            result["amountStatus"] = "SOURCE_UNAVAILABLE"
    else:
        result.update({
            "volume": _number(row.get("volume") if "volume" in row else row.get("tradeVolume")),
            "tradingValue": _number(row.get("tradingValue") if "tradingValue" in row else row.get("tradeAmount")),
            "marketCap": _number(row.get("marketCap") if "marketCap" in row else row.get("marketSum")),
            "high52Week": _number(row.get("high52Week") if "high52Week" in row else row.get("week52HighPrice")),
            "volumeDiff": _number(row.get("volumeDiff") if "volumeDiff" in row else row.get("quantDiff")),
        })
    return {key: value for key, value in result.items() if value not in (None, "")}


def _ranking_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"status": payload.get("status", "UNAVAILABLE"), "items": {}}
    for entry in payload.get("rankings") or []:
        if not isinstance(entry, Mapping):
            continue
        name = str(entry.get("name") or "unknown")
        result["items"][name] = {
            "status": entry.get("status", "UNAVAILABLE"),
            "rows": [_stock(row) for row in (entry.get("rows") or [])[:MAX_RANKING_ROWS] if isinstance(row, Mapping)],
        }
        if entry.get("error"):
            result["items"][name]["error"] = entry["error"]
    return result


def _flow_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "investorType": entry.get("investorType"),
        "periodType": entry.get("periodType"),
        "status": entry.get("status", "UNAVAILABLE"),
        "buy": [_stock(row, investor=True) for row in (entry.get("buy") or [])[:MAX_FLOW_ROWS] if isinstance(row, Mapping)],
        "sell": [_stock(row, investor=True) for row in (entry.get("sell") or [])[:MAX_FLOW_ROWS] if isinstance(row, Mapping)],
    }
    if entry.get("error"):
        result["error"] = entry["error"]
    return result


def _investor_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"status": payload.get("status", "UNAVAILABLE"), "periods": {}}
    result["sourceDates"] = _source_dates(payload)
    day = [_flow_entry(entry) for entry in (payload.get("investors") or []) if isinstance(entry, Mapping)]
    result["periods"]["DAY"] = day
    for period, value in (payload.get("multiPeriod", {}).get("periods") or {}).items():
        if isinstance(value, Mapping):
            result["periods"][str(period)] = [
                _flow_entry(entry) for entry in (value.get("investors") or []) if isinstance(entry, Mapping)
            ]
    return result


def _industry_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    rows = []
    for row in payload.get("industries") or []:
        if not isinstance(row, Mapping):
            continue
        rows.append({
            "id": row.get("id"),
            "name": row.get("name"),
            "changeRate": _number(row.get("changeRate")),
            "risingStocks": _number(row.get("risingStocks")),
            "fallingStocks": _number(row.get("fallingStocks")),
            "totalStocks": _number(row.get("totalStocks")),
            "totalVolume": _number(row.get("totalVolume")),
        })
    rows.sort(key=lambda row: row.get("changeRate") if isinstance(row.get("changeRate"), (int, float)) else -10**9, reverse=True)
    return {
        "status": payload.get("status", "UNAVAILABLE"),
        "count": payload.get("count", len(rows)),
        "top": rows[:MAX_INDUSTRIES],
    }


def _membership_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": payload.get("status", "UNAVAILABLE"),
        "generatedAt": payload.get("generatedAt"),
        "industryCount": payload.get("industryCount"),
        "membershipCount": payload.get("membershipCount"),
        "cacheStatus": payload.get("cacheStatus", "COLLECTED"),
    }


def build_projection(
    *,
    rankings: Mapping[str, Any],
    investor_flow: Mapping[str, Any],
    industries: Mapping[str, Any],
    membership: Mapping[str, Any],
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    statuses = [
        rankings.get("status"),
        investor_flow.get("status"),
        industries.get("status"),
        membership.get("status"),
    ]
    status = "OK" if statuses and all(value == "OK" for value in statuses) else "PARTIAL" if any(value in {"OK", "PARTIAL"} for value in statuses) else "UNAVAILABLE"
    generated_at = (manifest or {}).get("generatedAt") or rankings.get("generatedAt") or investor_flow.get("generatedAt")
    return {
        "schemaVersion": "market-snapshot-report-projection-v1",
        "source": "NAVER_CURRENT_SNAPSHOT",
        "status": status,
        "generatedAt": generated_at,
        "sourceDates": _source_dates(investor_flow),
        "sourcePaths": {
            "rankings": "data/market/rankings.json",
            "investorFlow": "data/market/investor-flow.json",
            "industries": "data/market/industries.json",
            "industryMembership": "data/market/industry-membership.json",
        },
        "rankings": _ranking_projection(rankings),
        "investorFlow": _investor_projection(investor_flow),
        "industries": _industry_projection(industries),
        "industryMembership": _membership_projection(membership),
    }


def _source_dates(payload: Mapping[str, Any]) -> list[str]:
    dates = set()
    for entry in payload.get("investors") or []:
        if not isinstance(entry, Mapping):
            continue
        for row in (entry.get("buy") or []) + (entry.get("sell") or []):
            if not isinstance(row, Mapping):
                continue
            for key in ("bizdateFrom", "bizdateTo", "from", "to"):
                if row.get(key) not in (None, ""):
                    dates.add(str(row[key]).replace("-", ""))
    return sorted(dates)


def validate_for_report(payload: Mapping[str, Any], expected_date: Any) -> dict[str, Any]:
    """Prevent an investor snapshot from crossing into a different report date."""
    result = copy.deepcopy(dict(payload))
    expected = str(expected_date or "").replace("-", "")[:8]
    flow = result.get("investorFlow")
    if not expected or not isinstance(flow, Mapping):
        return result
    dates = {str(value).replace("-", "") for value in flow.get("sourceDates", []) if value}
    if dates and dates != {expected}:
        result["status"] = "PARTIAL"
        result["investorFlow"] = {
            **dict(flow),
            "status": "STALE_ARTIFACT",
            "sourceDates": sorted(dates),
            "periods": {},
            "reason": "INVESTOR_FLOW_DATE_MISMATCH",
        }
    return result


def build_from_directory(market_dir: Path = MARKET_DIR) -> dict[str, Any]:
    return build_projection(
        rankings=_read(market_dir / "rankings.json"),
        investor_flow=_read(market_dir / "investor-flow.json"),
        industries=_read(market_dir / "industries.json"),
        membership=_read(market_dir / "industry-membership.json"),
        manifest=_read(market_dir / "manifest.json"),
    )


def write_projection(payload: Mapping[str, Any], output: Path = DEFAULT_OUTPUT) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-dir", type=Path, default=MARKET_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    payload = build_from_directory(args.market_dir)
    write_projection(payload, args.output)
    print(json.dumps({"status": payload["status"], "output": str(args.output), "generatedAt": payload.get("generatedAt")}, ensure_ascii=False))
    return 0 if payload["status"] in {"OK", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
