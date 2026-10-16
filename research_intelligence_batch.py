#!/usr/bin/env python3

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import research_intelligence as ri


UNIVERSE_PATH = Path("config/universe.json")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Batch NAVER research intelligence analyzer"
    )
    parser.add_argument(
        "--as-of",
        default=date.today().isoformat(),
        help="분석 기준일 YYYY-MM-DD",
    )
    parser.add_argument(
        "--recent-days",
        type=int,
        default=30,
        help="최근 비교 구간 일수",
    )
    parser.add_argument(
        "--min-recent-reports",
        type=int,
        default=3,
        help="정상 acceleration 판정 최소 최근 리포트 수",
    )
    parser.add_argument(
        "--output-dir",
        default="/tmp/research_intelligence_batch",
        help="결과 저장 디렉터리",
    )
    return parser.parse_args()


def load_universe():
    with UNIVERSE_PATH.open("r", encoding="utf-8") as f:
        universe = json.load(f)

    stocks = [
        stock
        for stock in universe.get("stocks", [])
        if stock.get("enabled", True)
    ]

    return stocks


def prune_stale_stock_outputs(output_dir, stocks):
    """Remove per-stock outputs no longer present in the enabled universe."""
    expected_codes = {
        str(stock.get("itemCode"))
        for stock in stocks
        if stock.get("itemCode")
    }
    for path in output_dir.glob("*.json"):
        if path.name == "latest.json":
            continue
        if path.stem not in expected_codes:
            path.unlink()


def revision_summary(reports):
    revisions = []

    for report in reports:
        change = report.get("goalPriceChange")

        if (
            change
            and isinstance(change.get("amount"), (int, float))
        ):
            revisions.append(change["amount"])

    return {
        "total": len(revisions),
        "up": sum(1 for value in revisions if value > 0),
        "down": sum(1 for value in revisions if value < 0),
        "flat": sum(1 for value in revisions if value == 0),
    }


def safe_pct_change(current, previous):
    if current is None or previous in (None, 0):
        return None

    return round(
        (current / previous - 1) * 100,
        2,
    )


def get_metric(stats, key):
    if not stats:
        return None
    return stats.get(key)


def analyze_stock(
    code,
    name,
    as_of,
    recent_days,
    min_recent_reports,
):
    reports = ri.load_reports(code)

    if not reports:
        return {
            "status": "NO_RESEARCH",
            "itemCode": code,
            "itemName": name,
            "reportCount": 0,
            "brokerCount": 0,
            "recentReportCount": 0,
            "previousReportCount": 0,
            "rankingEligible": False,
        }

    recent_start = as_of - timedelta(days=recent_days)
    previous_start = recent_start - timedelta(days=recent_days)

    recent = [
        report
        for report in reports
        if recent_start
        <= ri.report_date(report)
        <= as_of
    ]

    previous = [
        report
        for report in reports
        if previous_start
        <= ri.report_date(report)
        < recent_start
    ]

    recent_summary = ri.period_summary(recent)
    previous_summary = ri.period_summary(previous)

    if len(recent) == 0:
        status = "NO_RECENT_RESEARCH"
    elif len(recent) < min_recent_reports:
        status = "LOW_SAMPLE"
    else:
        status = "OK"

    ranking_eligible = status == "OK"

    topic_acceleration = ri.compare_topics(
        recent,
        previous,
    )

    recent_target = recent_summary.get(
        "targetPrice",
        {},
    )
    previous_target = previous_summary.get(
        "targetPrice",
        {},
    )

    recent_mean = get_metric(
        recent_target,
        "mean",
    )
    previous_mean = get_metric(
        previous_target,
        "mean",
    )

    recent_median = get_metric(
        recent_target,
        "median",
    )
    previous_median = get_metric(
        previous_target,
        "median",
    )

    recent_stdev = get_metric(
        recent_target,
        "stdev",
    )
    previous_stdev = get_metric(
        previous_target,
        "stdev",
    )

    full_result = {
        "status": status,
        "rankingEligible": ranking_eligible,
        "itemCode": code,
        "itemName": name,
        "asOf": as_of.isoformat(),
        "dateRange": {
            "from": reports[0]["writeDate"],
            "to": reports[-1]["writeDate"],
        },
        "reportCount": len(reports),
        "brokerCount": len(
            {
                r.get("brokerName")
                for r in reports
                if r.get("brokerName")
            }
        ),
        "recentVsPrevious": {
            "recent": {
                "from": recent_start.isoformat(),
                "to": as_of.isoformat(),
                **recent_summary,
            },
            "previous": {
                "from": previous_start.isoformat(),
                "to": (
                    recent_start - timedelta(days=1)
                ).isoformat(),
                **previous_summary,
            },
            "topicAcceleration": topic_acceleration,
        },
        "comparison": {
            "reportCountChange": (
                len(recent) - len(previous)
            ),
            "targetMeanChangePct": safe_pct_change(
                recent_mean,
                previous_mean,
            ),
            "targetMedianChangePct": safe_pct_change(
                recent_median,
                previous_median,
            ),
            "targetDispersionChangePct": safe_pct_change(
                recent_stdev,
                previous_stdev,
            ),
        },
        "goalPriceRevisions": revision_summary(
            reports
        ),
        "monthly": ri.monthly_summary(reports),
        "brokerTrajectory": ri.broker_trajectories(
            reports
        ),
        "methodology": {
            "rawContentStored": False,
            "topicSource": (
                "report title + stored rule-based "
                "topic keys only"
            ),
            "topicCountsAreFullTextCounts": False,
            "buySellUsedAsRecommendationScore": False,
            "minimumRecentReportsForRanking":
                min_recent_reports,
        },
    }

    return full_result


def compact_summary(result):
    recent = (
        result.get("recentVsPrevious", {})
        .get("recent", {})
    )

    previous = (
        result.get("recentVsPrevious", {})
        .get("previous", {})
    )

    recent_target = recent.get(
        "targetPrice",
        {},
    )
    previous_target = previous.get(
        "targetPrice",
        {},
    )

    acceleration = (
        result.get("recentVsPrevious", {})
        .get("topicAcceleration", [])
    )

    rising_topics = [
        {
            "topic": row["topic"],
            "delta": row["perReportDelta"],
            "ratio": row["ratio"],
        }
        for row in acceleration
        if row["perReportDelta"] > 0
    ][:5]

    revisions = result.get(
        "goalPriceRevisions",
        {},
    )

    return {
        "itemCode": result["itemCode"],
        "itemName": result["itemName"],
        "status": result["status"],
        "rankingEligible": result.get(
            "rankingEligible",
            False,
        ),
        "reportCount": result.get(
            "reportCount",
            0,
        ),
        "brokerCount": result.get(
            "brokerCount",
            0,
        ),
        "recentReportCount": recent.get(
            "reportCount",
            0,
        ),
        "previousReportCount": previous.get(
            "reportCount",
            0,
        ),
        "recentTargetMean": recent_target.get(
            "mean"
        ),
        "previousTargetMean": previous_target.get(
            "mean"
        ),
        "recentTargetMedian": recent_target.get(
            "median"
        ),
        "recentTargetStdev": recent_target.get(
            "stdev"
        ),
        "targetMeanChangePct": (
            result.get("comparison", {})
            .get("targetMeanChangePct")
        ),
        "targetDispersionChangePct": (
            result.get("comparison", {})
            .get("targetDispersionChangePct")
        ),
        "revisionUp": revisions.get("up", 0),
        "revisionDown": revisions.get("down", 0),
        "risingTopics": rising_topics,
    }


def main():
    args = parse_args()

    as_of = date.fromisoformat(args.as_of)
    stocks = load_universe()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    results = []
    summaries = []

    for stock in stocks:
        code = stock["itemCode"]
        name = stock["stockName"]

        result = analyze_stock(
            code=code,
            name=name,
            as_of=as_of,
            recent_days=args.recent_days,
            min_recent_reports=(
                args.min_recent_reports
            ),
        )

        results.append(result)
        summaries.append(
            compact_summary(result)
        )

        stock_path = (
            output_dir / f"{code}.json"
        )

        with stock_path.open(
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                result,
                f,
                ensure_ascii=False,
                indent=2,
            )

    prune_stale_stock_outputs(output_dir, stocks)

    status_counts = {}

    for result in results:
        status = result["status"]
        status_counts[status] = (
            status_counts.get(status, 0) + 1
        )

    eligible = [
        row
        for row in summaries
        if row["rankingEligible"]
    ]

    attention_increase = sorted(
        eligible,
        key=lambda x: (
            x["recentReportCount"]
            - x["previousReportCount"]
        ),
        reverse=True,
    )

    target_mean_change = sorted(
        [
            row
            for row in eligible
            if row["targetMeanChangePct"]
            is not None
        ],
        key=lambda x: x["targetMeanChangePct"],
        reverse=True,
    )

    dispersion_increase = sorted(
        [
            row
            for row in eligible
            if row["targetDispersionChangePct"]
            is not None
        ],
        key=lambda x: (
            x["targetDispersionChangePct"]
        ),
        reverse=True,
    )

    latest = {
        "status": "OK",
        "version": "research-intelligence-batch-v1",
        "asOf": as_of.isoformat(),
        "universeSource": str(UNIVERSE_PATH),
        "enabledStockCount": len(stocks),
        "statusCounts": status_counts,
        "rankingEligibleCount": len(eligible),
        "stocks": summaries,
        "rankings": {
            "researchAttentionIncrease": (
                attention_increase[:10]
            ),
            "targetMeanChange": (
                target_mean_change[:10]
            ),
            "targetDispersionIncrease": (
                dispersion_increase[:10]
            ),
        },
        "methodology": {
            "automaticUniversePromotion": False,
            "buySellUsedAsRecommendationScore": False,
            "minimumRecentReportsForRanking":
                args.min_recent_reports,
        },
    }

    latest_path = output_dir / "latest.json"

    with latest_path.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            latest,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(
        json.dumps(
            {
                "status": "OK",
                "enabledStockCount": len(stocks),
                "statusCounts": status_counts,
                "rankingEligibleCount": len(eligible),
                "outputDir": str(output_dir),
                "latest": str(latest_path),
                "sample": [
                    {
                        "code": row["itemCode"],
                        "name": row["itemName"],
                        "status": row["status"],
                        "reports": row["reportCount"],
                        "recent": row[
                            "recentReportCount"
                        ],
                    }
                    for row in summaries[:10]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
