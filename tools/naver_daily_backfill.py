#!/usr/bin/env python3
"""
NAVER domestic stock daily historical backfill collector.

Principles
----------
- GET only / unauthenticated
- raw response snapshots are immutable
- canonical data is derived from raw observations
- no synthetic trading days / no forward fill
- checkpoint/resume
- collection status separated from feature/label validity
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import random
import re
import ssl
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CONTEXT = ssl.create_default_context()


BASE_URL = "https://api.stock.naver.com/chart/domestic/item/{code}/day"
DEFAULT_OUTPUT = Path("data/naver-history")
DEFAULT_UNIVERSE_FILE = Path("data/market-universe/all-assets.json")
MANIFEST_LOCK = threading.Lock()
CHECKPOINT_LOCK = threading.Lock()


def now_local():
    return datetime.now().astimezone()


def compact_ts(dt=None):
    dt = dt or now_local()
    return dt.strftime("%Y%m%dT%H%M%S%z")


def request_datetime(dt):
    return dt.strftime("%Y%m%d%H%M")


def years_ago(dt, years):
    try:
        return dt.replace(year=dt.year - years)
    except ValueError:
        # Feb 29
        return dt.replace(year=dt.year - years, day=28)


def parse_codes(value):
    if not value:
        return []
    out = []
    seen = set()
    for token in value.replace("\n", ",").split(","):
        code = token.strip()
        if not code:
            continue
        if not re.fullmatch(r"[A-Za-z0-9]+", code):
            raise ValueError(f"invalid stock code: {code!r}")
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out


def load_universe_targets(path, asset_type="stock"):
    """Read only authoritative builder outputs; never infer from industry data."""
    payload = load_json(Path(path), None)
    if not isinstance(payload, dict):
        raise ValueError(f"invalid universe file: {path}")
    rows = payload.get("assets") or payload.get("stocks") or payload.get("etfs") or payload.get("etns")
    if not isinstance(rows, list):
        raise ValueError("universe file has no assets/stocks list")
    wanted = {"STOCK"} if asset_type == "stock" else {asset_type.upper()}
    if asset_type == "all":
        wanted = {"STOCK", "ETF", "ETN"}
    targets, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("universe contains a non-object row")
        code = str(row.get("code") or "").strip()
        kind = str(row.get("assetType") or "STOCK").upper()
        if not re.fullmatch(r"[A-Za-z0-9]+", code):
            raise ValueError(f"invalid universe code: {code!r}")
        if kind not in wanted:
            continue
        key = (kind, code)
        if key in seen:
            continue
        seen.add(key)
        targets.append({"code": code, "assetType": kind, "market": row.get("market"),
                        "name": row.get("name")})
    if not targets:
        raise ValueError(f"no {asset_type} targets in authoritative universe")
    return targets


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def append_jsonl(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    with MANIFEST_LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception:
        return default


def fetch(code, start_dt, end_dt, timeout=30, retries=3, sleep=0.5):
    params = {
        "startDateTime": request_datetime(start_dt),
        "endDateTime": request_datetime(end_dt),
        "wrapper": "true",
    }
    url = BASE_URL.format(code=code) + "?" + urlencode(params)

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": f"https://stock.naver.com/domestic/stock/{code}/price",
    }

    last_error = None

    for attempt in range(1, retries + 1):
        req = Request(url, headers=headers, method="GET")
        started = time.perf_counter()

        try:
            with urlopen(req, timeout=timeout, context=SSL_CONTEXT) as r:
                raw = r.read()
                elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}")

                return {
                    "url": url,
                    "status": r.status,
                    "raw": raw,
                    "elapsed_ms": elapsed_ms,
                    "attempt": attempt,
                }

        except (HTTPError, URLError, TimeoutError, RuntimeError) as e:
            last_error = e
            if attempt < retries:
                delay = sleep * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
                time.sleep(delay)

    raise RuntimeError(f"request failed after {retries} attempts: {last_error}")


def row_validation_reasons(row):
    """Return feature-quality reasons without changing NAVER's observed values."""
    required = ("openPrice", "highPrice", "lowPrice", "closePrice", "accumulatedTradingVolume")
    missing = [key for key in required if row.get(key) is None]
    if missing:
        return [f"missing fields: {', '.join(missing)}"]

    try:
        o = float(row["openPrice"])
        h = float(row["highPrice"])
        l = float(row["lowPrice"])
        c = float(row["closePrice"])
        v = float(row["accumulatedTradingVolume"])
    except (TypeError, ValueError):
        return ["non_numeric_ohlcv"]

    reasons = []
    if v == 0 and o == 0 and h == 0 and l == 0 and c > 0:
        reasons.append("zero_volume_zero_ohl_with_close")
    if l > min(o, c):
        reasons.append("lowPrice inconsistent")
    if h < max(o, c):
        reasons.append("highPrice inconsistent")
    if v < 0:
        reasons.append("negative volume")
    return reasons


def validate_rows(rows):
    """Validate canonical identity separately from row-level feature quality."""
    errors = []
    warnings = []
    invalid_indices = []
    validation_reasons = {}

    if not isinstance(rows, list):
        return {
            "valid": False, "collection_valid": False,
            "errors": ["priceInfos is not a list"], "warnings": [],
            "row_count": 0, "unique_dates": 0, "duplicate_dates": [],
            "invalid_row_count": 0, "invalid_row_indices": [],
            "validation_reasons": {}, "earliest": None, "latest": None,
        }

    dates, seen, duplicates = [], set(), []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"row[{index}] is not an object")
            continue
        date = str(row.get("localDate") or "").strip()
        if not date:
            errors.append(f"row[{index}] missing localDate")
            continue
        dates.append(date)
        if date in seen:
            duplicates.append(date)
        seen.add(date)

        reasons = row_validation_reasons(row)
        if reasons:
            invalid_indices.append(index)
            validation_reasons[str(index)] = reasons

    if duplicates:
        errors.append(f"duplicate localDate count={len(duplicates)}")
    if not dates:
        errors.append("no canonical dates")

    collection_valid = not errors
    if invalid_indices:
        warnings.append(f"feature-invalid rows={len(invalid_indices)}")
    return {
        "valid": collection_valid,
        "collection_valid": collection_valid,
        "errors": errors[:100],
        "warnings": warnings[:100],
        "row_count": len(rows),
        "unique_dates": len(seen),
        "duplicate_dates": sorted(set(duplicates)),
        "invalid_row_count": len(invalid_indices),
        "invalid_row_indices": invalid_indices[:100],
        "validation_reasons": validation_reasons,
        "earliest": min(dates) if dates else None,
        "latest": max(dates) if dates else None,
    }


def canonicalize(rows, code, asset_type="STOCK", market=None):
    canonical = []

    for row in rows:
        reasons = row_validation_reasons(row)
        canonical.append(
            {
                "code": code,
                "date": str(row["localDate"]),
                "open": row["openPrice"],
                "high": row["highPrice"],
                "low": row["lowPrice"],
                "close": row["closePrice"],
                "volume": row["accumulatedTradingVolume"],
                "foreignRetentionRate": row.get("foreignRetentionRate"),
                "source": "naver_stock_day",
                "assetType": asset_type,
                "market": market,
                "feature_valid": not reasons,
                "validation_reasons": reasons,
                "label_valid": True,
            }
        )

    canonical.sort(key=lambda x: x["date"])
    return canonical


def write_canonical(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                existing[row["date"]] = row

    for row in rows:
        existing[row["date"]] = row

    ordered = [existing[k] for k in sorted(existing)]

    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for row in ordered:
                f.write(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise

    return len(ordered)


def checkpoint_key(code, start_dt, end_dt, asset_type="STOCK"):
    return (
        f"{asset_type}:{code}:"
        f"{start_dt.strftime('%Y%m%d')}:"
        f"{end_dt.strftime('%Y%m%d')}"
    )


def collect_one(
    code,
    start_dt,
    end_dt,
    output_root,
    timeout,
    retries,
    request_sleep,
    dry_run,
    asset_type="STOCK",
    market=None,
):
    started_at = now_local()
    key = checkpoint_key(code, start_dt, end_dt, asset_type)

    if dry_run:
        return {
            "code": code,
            "assetType": asset_type,
            "market": market,
            "status": "DRY_RUN",
            "checkpoint_key": key,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
        }

    result = fetch(
        code=code,
        start_dt=start_dt,
        end_dt=end_dt,
        timeout=timeout,
        retries=retries,
        sleep=request_sleep,
    )

    raw = result["raw"]
    digest = sha256_bytes(raw)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"invalid JSON response: {e}") from e

    rows = payload.get("priceInfos", []) if isinstance(payload, dict) else []
    validation = validate_rows(rows)

    collected_at = now_local()
    day_dir = collected_at.strftime("%Y%m%d")

    raw_path = (
        output_root
        / "raw"
        / day_dir
        / code
        / f"{compact_ts(collected_at)}_{digest[:16]}.json"
    )

    raw_path.parent.mkdir(parents=True, exist_ok=True)

    # Immutable snapshot: never overwrite an existing raw object.
    if raw_path.exists():
        raise RuntimeError(f"raw snapshot already exists: {raw_path}")

    envelope = {
        "metadata": {
            "source": "NAVER",
            "dataset": "domestic_stock_daily",
            "code": code,
            "asset_type": asset_type,
            "market": market,
            "request_url": result["url"],
            "request_start": start_dt.isoformat(),
            "request_end": end_dt.isoformat(),
            "collected_at": collected_at.isoformat(),
            "http_status": result["status"],
            "elapsed_ms": result["elapsed_ms"],
            "attempt": result["attempt"],
            "sha256_response": digest,
            "collection_status": "SUCCESS",
            "validation": validation,
        },
        "response": payload,
    }

    atomic_write_json(raw_path, envelope)

    canonical_count = None
    canonical_path = None

    if validation["collection_valid"]:
        canonical_rows = canonicalize(rows, code, asset_type, market)
        canonical_path = output_root / "canonical" / "daily" / f"{code}.jsonl"
        canonical_count = write_canonical(canonical_path, canonical_rows)

    canonical_bytes = canonical_path.stat().st_size if canonical_path else 0

    manifest = {
        "timestamp": collected_at.isoformat(),
        "code": code,
        "assetType": asset_type,
        "market": market,
        "checkpoint_key": key,
        "collection_status": "SUCCESS",
        "feature_valid": validation["invalid_row_count"] == 0,
        "label_valid": validation["invalid_row_count"] == 0,
        "canonical_written": canonical_path is not None,
        "canonical_path": str(canonical_path) if canonical_path else None,
        "row_count": validation["row_count"],
        "valid_row_count": validation["row_count"] - validation["invalid_row_count"],
        "feature_valid_row_count": validation["row_count"] - validation["invalid_row_count"],
        "invalid_row_count": validation["invalid_row_count"],
        "invalid_row_indices": validation["invalid_row_indices"],
        "validation_reasons": validation["validation_reasons"],
        "unique_dates": validation.get("unique_dates"),
        "earliest": validation.get("earliest"),
        "latest": validation.get("latest"),
        "duplicate_dates": validation.get("duplicate_dates"),
        "raw_path": str(raw_path),
        "canonical_total_rows": canonical_count,
        "raw_bytes": raw_path.stat().st_size,
        "canonical_bytes": canonical_bytes,
        "sha256_response": digest,
        "elapsed_ms": result["elapsed_ms"],
        "started_at": started_at.isoformat(),
        "finished_at": collected_at.isoformat(),
    }

    append_jsonl(output_root / "metadata" / "manifest.jsonl", manifest)

    return manifest


def update_checkpoint(path, key, result):
    with CHECKPOINT_LOCK:
        data = load_json(path, {})
        data[key] = {
            "status": result.get("collection_status", result.get("status")),
            "code": result.get("code"),
            "row_count": result.get("row_count"),
            "canonical_path": result.get("canonical_path"),
            "canonical_row_count": result.get("canonical_total_rows"),
            "canonical_written": result.get("canonical_written", False),
            "invalid_row_count": result.get("invalid_row_count", 0),
            "earliest": result.get("earliest"),
            "latest": result.get("latest"),
            "updated_at": now_local().isoformat(),
        }
        atomic_write_json(path, data)


def canonical_file_is_usable(path):
    """A SUCCESS checkpoint is resumable only when a parseable canonical exists."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    return isinstance(row, dict) and bool(row.get("date"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return False


def checkpoint_is_resumable(checkpoint, canonical_path):
    return (
        isinstance(checkpoint, dict)
        and checkpoint.get("status") == "SUCCESS"
        and canonical_file_is_usable(canonical_path)
    )


def latest_raw_snapshot(output_root, code):
    """Return the latest immutable raw envelope for a code without network access."""
    candidates = sorted((output_root / "raw").glob(f"*/{code}/*.json"), reverse=True)
    latest = None
    latest_collected_at = ""
    for path in candidates:
        envelope = load_json(path, None)
        if not isinstance(envelope, dict):
            continue
        metadata = envelope.get("metadata") or {}
        if metadata.get("collection_status") != "SUCCESS":
            continue
        payload = envelope.get("response")
        if not isinstance(payload, dict):
            continue
        collected_at = str(metadata.get("collected_at") or path.name)
        if collected_at >= latest_collected_at:
            latest, latest_collected_at = (path, envelope), collected_at
    return latest


def recover_missing_canonical(output_root, targets):
    """Rebuild only absent canonical files from immutable raw snapshots."""
    recovered, unavailable, invalid_rows = [], [], 0
    for target in targets:
        code = target["code"]
        path = output_root / "canonical" / "daily" / f"{code}.jsonl"
        if canonical_file_is_usable(path):
            continue
        raw = latest_raw_snapshot(output_root, code)
        if raw is None:
            unavailable.append(code)
            continue
        raw_path, envelope = raw
        rows = envelope["response"].get("priceInfos")
        validation = validate_rows(rows)
        if not validation["collection_valid"]:
            unavailable.append(code)
            continue
        count = write_canonical(path, canonicalize(rows, code, target["assetType"], target["market"]))
        invalid_rows += validation["invalid_row_count"]
        recovered.append({"code": code, "path": str(path), "rows": count, "raw_path": str(raw_path),
                          "invalid_row_count": validation["invalid_row_count"]})
    return {"recovered": recovered, "unavailable": unavailable, "invalid_row_count": invalid_rows}


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--codes", help="comma-separated NAVER item codes")
    parser.add_argument("--universe-file", help="authoritative builder output JSON")
    parser.add_argument("--all-market", action="store_true",
                        help="use the authoritative all-assets output")
    parser.add_argument("--asset-type", choices=("stock", "etf", "etn", "all"), default="stock")
    parser.add_argument("--limit", type=int, help="limit universe targets for a smoke run")
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT),
    )
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--sleep", type=float, default=0.35)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--recover-missing-canonical",
        action="store_true",
        help="rebuild only absent canonical files from existing immutable raw snapshots",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip successful checkpoint entries",
    )
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if args.years < 1:
        parser.error("--years must be >= 1")

    if args.max_workers < 1:
        parser.error("--max-workers must be >= 1")

    selected_sources = sum(bool(value) for value in (args.codes, args.universe_file, args.all_market))
    if selected_sources != 1:
        parser.error("choose exactly one of --codes, --universe-file, or --all-market")
    if args.codes:
        targets = [{"code": code, "assetType": "STOCK", "market": None, "name": None}
                   for code in parse_codes(args.codes)]
    else:
        universe = Path(args.universe_file) if args.universe_file else DEFAULT_UNIVERSE_FILE
        try:
            targets = load_universe_targets(universe, args.asset_type)
        except ValueError as exc:
            parser.error(str(exc))
    if args.limit is not None:
        if args.limit < 1:
            parser.error("--limit must be >= 1")
        targets = targets[:args.limit]

    output_root = Path(args.output_root)
    checkpoint_path = output_root / "metadata" / "checkpoints.json"

    if args.recover_missing_canonical:
        recovery = recover_missing_canonical(output_root, targets)
        print(
            f"canonical recovery recovered={len(recovery['recovered'])} "
            f"unavailable={len(recovery['unavailable'])} "
            f"invalidRows={recovery['invalid_row_count']}"
        )
        if recovery["unavailable"]:
            print("unavailable=" + ",".join(recovery["unavailable"]), file=sys.stderr)
            return 1
        return 0

    end_dt = now_local().replace(second=0, microsecond=0)
    start_dt = years_ago(end_dt, args.years)

    checkpoints = load_json(checkpoint_path, {}) if args.resume else {}

    pending = []

    for target in targets:
        code = target["code"]
        key = checkpoint_key(code, start_dt, end_dt, target["assetType"])

        canonical_path = output_root / "canonical" / "daily" / f"{code}.jsonl"
        if args.resume and checkpoint_is_resumable(checkpoints.get(key), canonical_path):
            print(f"SKIP {code} checkpoint=SUCCESS")
            continue

        pending.append(target)

    print(
        f"codes={len(targets)} pending={len(pending)} assetType={args.asset_type} "
        f"years={args.years} "
        f"start={start_dt.strftime('%Y-%m-%d')} "
        f"end={end_dt.strftime('%Y-%m-%d')} "
        f"workers={args.max_workers}"
    )

    if not pending:
        return 0

    failures = 0
    completed = []
    run_started = time.perf_counter()

    def worker(target):
        code = target["code"]
        if args.sleep > 0:
            time.sleep(random.uniform(0, args.sleep))

        try:
            result = collect_one(
                code=code,
                start_dt=start_dt,
                end_dt=end_dt,
                output_root=output_root,
                timeout=args.timeout,
                retries=args.retries,
                request_sleep=args.sleep,
                dry_run=args.dry_run,
                asset_type=target["assetType"],
                market=target["market"],
            )

            key = result["checkpoint_key"]

            if not args.dry_run:
                update_checkpoint(checkpoint_path, key, result)

            return target, result, None

        except Exception as e:
            return target, None, str(e)

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.max_workers
    ) as executor:
        futures = [executor.submit(worker, target) for target in pending]

        for future in concurrent.futures.as_completed(futures):
            target, result, error = future.result()
            code = target["code"]

            if error:
                failures += 1
                print(f"FAIL {code} {error}", file=sys.stderr)

                if not args.dry_run:
                    append_jsonl(
                        output_root / "metadata" / "manifest.jsonl",
                        {
                            "timestamp": now_local().isoformat(),
                            "code": code,
                            "assetType": target["assetType"],
                            "market": target["market"],
                            "collection_status": "FAILED",
                            "feature_valid": False,
                            "label_valid": False,
                            "error": error,
                        },
                    )
                continue

            completed.append(result)

            if args.dry_run:
                print(
                    f"DRY  {code} "
                    f"{result['start']} -> {result['end']}"
                )
            else:
                print(
                    f"OK   {code} "
                    f"rows={result['row_count']} "
                    f"{result['earliest']}..{result['latest']} "
                    f"{result['elapsed_ms']}ms"
                )

    if not args.dry_run:
        successful = [result for result in completed if result.get("collection_status") == "SUCCESS"]
        latencies = [result["elapsed_ms"] for result in successful]
        summary = {
            "generatedAt": now_local().isoformat(), "universeCount": len(targets),
            "assetType": args.asset_type, "attemptedCount": len(pending),
            "successCount": len(successful), "failedCount": failures,
            "skippedResumeCount": len(targets) - len(pending),
            "canonicalStockCount": sum(result.get("canonical_total_rows") is not None for result in successful),
            "totalCanonicalRows": sum(result.get("canonical_total_rows") or 0 for result in successful),
            "earliestDate": min((result.get("earliest") for result in successful if result.get("earliest")), default=None),
            "latestDate": max((result.get("latest") for result in successful if result.get("latest")), default=None),
            "duplicateViolations": sum(bool(result.get("duplicate_dates")) for result in successful),
            "validationFailures": sum(not result.get("feature_valid") for result in successful),
            "rawBytes": sum(result.get("raw_bytes", 0) for result in successful),
            "canonicalBytes": sum(result.get("canonical_bytes", 0) for result in successful),
            "avgLatencyMs": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "maxLatencyMs": max(latencies) if latencies else None,
            "elapsedSeconds": round(time.perf_counter() - run_started, 2),
        }
        atomic_write_json(output_root / "metadata" / "latest-summary.json", summary)

    if failures:
        print(f"completed with failures={failures}", file=sys.stderr)
        return 1

    print("completed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
