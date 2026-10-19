#!/usr/bin/env python3
"""Build isolated optional-column recovery artifacts without touching inputs."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def load_canonical(directory: Path):
    result = {}
    for path in Path(directory).glob("*.jsonl"):
        rows = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                rows[str(row["date"])] = row
        result[path.stem] = rows
    return result


def recover(monitoring_dir, canonical_dir, output_dir):
    canonical = load_canonical(Path(canonical_dir))
    output = Path(output_dir); output.mkdir(parents=True, exist_ok=True)
    stats = Counter()
    for market in ("kospi", "kosdaq"):
        source = json.loads((Path(monitoring_dir) / f"{market}.json").read_text(encoding="utf-8"))
        result = json.loads(json.dumps(source))
        for stock in result.get("stocks", []):
            code = str(stock["code"]); dates = stock["dates"]
            stock.setdefault("open", [None] * len(dates))
            stock.setdefault("low", [None] * len(dates))
            stock.setdefault("value", [None] * len(dates))
            for index, date in enumerate(dates):
                row = canonical.get(code, {}).get(str(date))
                if row is None:
                    stats["canonical_missing"] += 1; continue
                if any(stock[key][index] != row.get(key) for key in ("close", "high", "volume")):
                    stats["conflict"] += 1; continue
                if stock["validity"][index] != 0:
                    stats["invalid"] += 1; continue
                if row.get("open") is None or row.get("low") is None:
                    stats["optional_source_missing"] += 1; continue
                stock["open"][index] = row["open"]
                stock["low"][index] = row["low"]
                stats["restored"] += 1
        (output / f"{market}.json").write_text(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (output / "recovery-report.json").write_text(
        json.dumps(dict(stats), ensure_ascii=False, indent=2), encoding="utf-8")
    return dict(stats)


BASE_FIELDS = ("dates", "close", "high", "volume", "validity")


def augment_latest(monitoring_dir, canonical_dir, output_dir):
    """Fill only missing open/low values from the latest input snapshot.

    The inputs are never modified. Existing optional values, metadata, ordering,
    and all FINAL rows are preserved; callers can compare the isolated output
    hashes before any separately approved publication step.
    """
    canonical = load_canonical(Path(canonical_dir))
    source_root = Path(monitoring_dir)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stats = Counter()
    for market in ("kospi", "kosdaq"):
        source_path = source_root / f"{market}.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))
        result = json.loads(json.dumps(source))
        for stock in result.get("stocks", []):
            code = str(stock["code"])
            dates = stock.get("dates", [])
            if any(len(stock.get(key, [])) != len(dates) for key in BASE_FIELDS):
                stats["shape_error"] += 1
                continue
            for index, date in enumerate(dates):
                row = canonical.get(code, {}).get(str(date))
                if row is None:
                    stats["canonical_missing"] += 1
                    continue
                if any(stock[key][index] != row.get(key) for key in ("close", "high", "volume")):
                    stats["conflict"] += 1
                    continue
                if stock["validity"][index] != 0:
                    stats["invalid"] += 1
                    continue
                for key in ("open", "low", "value"):
                    values = stock.setdefault(key, [None] * len(dates))
                    if len(values) != len(dates):
                        stats["shape_error"] += 1
                        break
                    if values[index] is not None:
                        stats[f"existing_{key}"] += 1
                        continue
                    if key == "value" or row.get(key) is None:
                        stats[f"missing_{key}"] += 1
                        continue
                    values[index] = row[key]
                    stats[f"restored_{key}"] += 1
        (output / f"{market}.json").write_text(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (output / "augmentation-report.json").write_text(
        json.dumps(dict(stats), ensure_ascii=False, indent=2), encoding="utf-8")
    return dict(stats)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--monitoring-dir", required=True)
    parser.add_argument("--canonical-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--augment-latest", action="store_true")
    args = parser.parse_args()
    fn = augment_latest if args.augment_latest else recover
    print(json.dumps(fn(args.monitoring_dir, args.canonical_dir, args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
