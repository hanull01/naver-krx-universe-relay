#!/usr/bin/env python3
"""Safely prepare or promote the source-observed monitoring recovery.

The default is a no-write dry run.  Production writes require both
``--apply`` and ``--confirm-production``.  The three production artifacts are
backed up before replacement and restored together if any replacement fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


TARGET_DATE = "20261026"
RECOVERY_DATES = ("20261019", "20261020", "20261021", "20261022", "20261023", TARGET_DATE)
RELATIVE_FILES = (
    Path("data/monitoring-daily/kospi.json"),
    Path("data/monitoring-daily/kosdaq.json"),
    Path("data/monitoring-baseline/latest.json"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def state_stocks(payload):
    stocks = payload.get("stocks")
    if not isinstance(stocks, list):
        raise ValueError("state stocks must be a list")
    return {str(stock.get("code")): stock for stock in stocks}


def validate_state(path: Path, expected_codes: set[str]) -> dict:
    payload = read_json(path)
    if payload.get("asOfDate") != TARGET_DATE or payload.get("publicationStatus") != "FINAL":
        raise ValueError(f"{path.name}: target/publication status mismatch")
    stocks = payload.get("stocks")
    if not isinstance(stocks, list) or len(stocks) != len(expected_codes):
        raise ValueError(f"{path.name}: stock coverage mismatch")
    if set(state_stocks(payload)) != expected_codes:
        raise ValueError(f"{path.name}: stock code set mismatch")
    duplicates = []
    target_entries = 0
    for stock in stocks:
        dates = stock.get("dates")
        if not isinstance(dates, list):
            raise ValueError(f"{path.name}: malformed dates for {stock.get('code')}")
        if len(dates) != len(set(dates)):
            duplicates.append(str(stock.get("code")))
        target_entries += sum(date in RECOVERY_DATES for date in dates)
    if duplicates:
        raise ValueError(f"{path.name}: duplicate dates for {','.join(duplicates[:10])}")
    return {"asOfDate": payload["asOfDate"], "stockCount": len(stocks),
            "targetDateEntries": target_entries,
            "sourceMissingCodes": payload.get("sourceMissingCodes", [])}


def validate_baseline(path: Path, expected_codes: set[str]) -> dict:
    payload = read_json(path)
    if payload.get("asOfDate") != TARGET_DATE or payload.get("historyStatus") != "SOURCE_OBSERVED":
        raise ValueError(f"{path.name}: source-observed baseline status mismatch")
    if payload.get("authoritativeCount") != len(expected_codes) or payload.get("count") != len(expected_codes):
        raise ValueError(f"{path.name}: baseline coverage mismatch")
    if payload.get("coveragePct") != 100.0:
        raise ValueError(f"{path.name}: baseline coverage is not 100%")
    codes = {str(stock.get("code")) for stock in payload.get("stocks", [])}
    if codes != expected_codes:
        raise ValueError(f"{path.name}: baseline code set mismatch")
    return {"asOfDate": payload["asOfDate"], "stockCount": len(codes),
            "missingLatestCodes": payload.get("missingLatestCodes", []),
            "calendarStatus": payload.get("calendarStatus")}


def validate_history_preservation(production_state: Path, staged_state: Path) -> int:
    before = state_stocks(read_json(production_state))
    after = state_stocks(read_json(staged_state))
    compared = 0
    for code, old in before.items():
        new = after.get(code)
        if not new:
            raise ValueError(f"history code removed: {code}")
        old_dates = {date: index for index, date in enumerate(old["dates"])}
        new_dates = {date: index for index, date in enumerate(new["dates"])}
        if "20261016" in old_dates:
            if "20261016" not in new_dates:
                raise ValueError(f"20261016 history removed: {code}")
            old_index, new_index = old_dates["20261016"], new_dates["20261016"]
            for key in ("close", "high", "volume", "validity"):
                if old[key][old_index] != new[key][new_index]:
                    raise ValueError(f"20261016 history changed: {code} {key}")
            compared += 1
    return compared


def backup_files(production_root: Path, backup_root: Path) -> dict[Path, str]:
    backup_root.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for relative in RELATIVE_FILES:
        source = production_root / relative
        if not source.is_file():
            raise ValueError(f"missing production file: {relative}")
        destination = backup_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        hashes[relative] = sha256(source)
    return hashes


def validate_promotion(production_root: Path, staging_root: Path) -> dict:
    production_root = Path(production_root); staging_root = Path(staging_root)
    staged_baseline = read_json(staging_root / "baseline-final.json")
    expected_codes = {str(stock.get("code")) for stock in staged_baseline.get("stocks", [])}
    if not expected_codes:
        raise ValueError("staging baseline has no stock codes")
    universe = read_json(production_root / "data/market-universe/all-assets.json")
    expected_by_market = {"KOSPI": set(), "KOSDAQ": set()}
    for asset in universe.get("assets", []):
        if asset.get("assetType") == "STOCK" and asset.get("market") in expected_by_market:
            expected_by_market[asset["market"]].add(str(asset["code"]))
    staged_state_info = {}
    for market, filename in (("KOSPI", "kospi.json"), ("KOSDAQ", "kosdaq.json")):
        staged_state_info[market.lower()] = validate_state(staging_root / "state" / filename, expected_by_market[market])
    baseline_info = validate_baseline(staging_root / "baseline-final.json", expected_codes)
    history_counts = {
        market: validate_history_preservation(
            production_root / "data/monitoring-daily" / f"{market}.json",
            staging_root / "state" / f"{market}.json",
        )
        for market in ("kospi", "kosdaq")
    }
    production_hashes = {str(relative): sha256(production_root / relative) for relative in RELATIVE_FILES}
    staging_hashes = {
        str(RELATIVE_FILES[0]): sha256(staging_root / "state/kospi.json"),
        str(RELATIVE_FILES[1]): sha256(staging_root / "state/kosdaq.json"),
        str(RELATIVE_FILES[2]): sha256(staging_root / "baseline-final.json"),
    }
    return {"status": "PROMOTION_READY", "mode": "DRY_RUN", "targetDate": TARGET_DATE,
            "expectedCodes": len(expected_codes), "stagedStates": staged_state_info,
            "stagedBaseline": baseline_info, "historyPreserved": history_counts,
            "productionHashes": production_hashes, "stagingHashes": staging_hashes}


def promote(production_root: Path, staging_root: Path, backup_root: Path, *, apply=False,
            confirm_production=False, replace_fn=os.replace) -> dict:
    report = validate_promotion(production_root, staging_root)
    if not apply or not confirm_production:
        return report
    backup_hashes = backup_files(production_root, backup_root)
    staged = {
        production_root / RELATIVE_FILES[0]: staging_root / "state/kospi.json",
        production_root / RELATIVE_FILES[1]: staging_root / "state/kosdaq.json",
        production_root / RELATIVE_FILES[2]: staging_root / "baseline-final.json",
    }
    temporary = []
    try:
        for destination, source in staged.items():
            fd, name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".promote", dir=destination.parent)
            os.close(fd)
            shutil.copy2(source, name)
            temporary.append((destination, Path(name)))
        for destination, temporary_path in temporary:
            replace_fn(str(temporary_path), str(destination))
        report["mode"] = "APPLIED"
        report["postHashes"] = {str(relative): sha256(production_root / relative) for relative in RELATIVE_FILES}
        report["backupHashes"] = {str(relative): value for relative, value in backup_hashes.items()}
        return report
    except Exception:
        for relative in RELATIVE_FILES:
            source = backup_root / relative
            destination = production_root / relative
            fd, name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".rollback", dir=destination.parent)
            os.close(fd)
            shutil.copy2(source, name)
            replace_fn(name, destination)
        raise
    finally:
        for _, path in temporary:
            path.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--production-root", type=Path, default=Path.cwd())
    parser.add_argument("--staging-root", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-production", action="store_true")
    args = parser.parse_args(argv)
    backup_root = args.backup_root or Path(tempfile.mkdtemp(prefix="naver-krx-promotion-backup-"))
    try:
        report = promote(args.production_root, args.staging_root, backup_root,
                         apply=args.apply, confirm_production=args.confirm_production)
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({**report, "backupRoot": str(backup_root)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
