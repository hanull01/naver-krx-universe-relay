#!/usr/bin/env python3
"""NAVER stock research collector.

- Uses the current NAVER Stock research JSON endpoint.
- Keeps raw provider text out of the repository.
- Persists compact metadata and derived changes only.
- nid is the stable report identifier.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

BASE_URL = "https://stock.naver.com"
ENDPOINT = "/api/stockSecurity/researches/v2/company"
PAGE_SIZE = 50
SCHEMA_VERSION = 1

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Referer": "https://stock.naver.com/research/company",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}


def atomic_json_write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</p\s*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n\s*\n+", "\n", value)
    return value.strip()


def normalize_opinion(text: str | None) -> str:
    if text is None:
        return "NONE"

    v = re.sub(r"\s+", "", str(text)).lower()

    if v in {"", "없음", "-", "n/a", "na"}:
        return "NONE"
    if v in {"buy", "매수", "strongbuy", "strongbuy(maintain)"}:
        return "BUY"
    if v in {"hold", "중립", "neutral"}:
        return "HOLD"
    if v in {"sell", "매도"}:
        return "SELL"
    if v == "outperform":
        return "OUTPERFORM"
    if v == "marketperform":
        return "MARKETPERFORM"

    return "OTHER"


def parse_goal_price(value: Any) -> int | None:
    if value is None:
        return None
    s = str(value).replace(",", "").strip()
    if not s:
        return None
    try:
        n = int(s)
    except ValueError:
        return None
    return n if n > 0 else None


POSITIVE_TERMS = (
    "상향", "증가", "개선", "호조", "성장", "회복", "확대",
    "강세", "수혜", "긍정", "기대", "흑자전환", "턴어라운드",
)

NEGATIVE_TERMS = (
    "하향", "감소", "둔화", "부진", "악화", "축소", "약세",
    "부담", "우려", "리스크", "적자", "하락",
)

EARNINGS_TERMS = (
    "실적", "매출", "영업이익", "순이익", "eps", "이익",
)

# 실적 방향 판단에는 문맥에 따라 의미가 뒤집힐 수 있는
# 강세/약세/기대/우려 같은 일반 감성어를 사용하지 않는다.
EARNINGS_POSITIVE_TERMS = (
    "증가", "개선", "호조", "성장", "회복", "확대",
    "상향", "흑자전환", "턴어라운드",
)

EARNINGS_NEGATIVE_TERMS = (
    "감소", "둔화", "부진", "악화", "축소",
    "하향", "적자", "하락",
)

TOPIC_GROUPS = {
    "환율": ("환율", "원화", "달러"),
    "원가": ("원가", "비용", "원재료"),
    "수요": ("수요", "판매량", "출하"),
    "가격": ("가격", "판가", "asp"),
    "수주": ("수주", "수주잔고", "order"),
    "재고": ("재고", "inventory"),
    "금리": ("금리", "이자율"),
    "중국": ("중국", "china"),
    "미국": ("미국", "미주", "usa"),
    "AI": ("ai", "인공지능"),
    "로봇": ("로봇", "robot"),
    "반도체": ("반도체", "dram", "nand", "hbm"),
    "자동차": ("자동차", "전기차", "ev"),
    "배터리": ("배터리", "2차전지", "이차전지"),
    "조선": ("조선", "선박", "lng선"),
    "방산": ("방산", "방위", "무기"),
    "원전": ("원전", "원자력"),
    "파업": ("파업", "노조"),
}


def _direction(pos: int, neg: int) -> str:
    if pos and neg:
        if pos > neg:
            return "UP"
        if neg > pos:
            return "DOWN"
        return "MIXED"
    if pos:
        return "UP"
    if neg:
        return "DOWN"
    return "UNKNOWN"


def analyze_content(content: str | None) -> dict[str, Any]:
    text = strip_html(content)
    lower = text.lower()

    positive_count = sum(lower.count(x.lower()) for x in POSITIVE_TERMS)
    negative_count = sum(lower.count(x.lower()) for x in NEGATIVE_TERMS)

    # 문장/문단 단위 분리. 콜론/세미콜론도 경계로 사용한다.
    chunks = [
        x.strip()
        for x in re.split(r"[\\n.!?。:;]+", lower)
        if x.strip()
    ]

    earnings_chunks = [
        chunk
        for chunk in chunks
        if any(term in chunk for term in EARNINGS_TERMS)
    ]

    earnings_positive = sum(
        sum(chunk.count(x.lower()) for x in EARNINGS_POSITIVE_TERMS)
        for chunk in earnings_chunks
    )
    earnings_negative = sum(
        sum(chunk.count(x.lower()) for x in EARNINGS_NEGATIVE_TERMS)
        for chunk in earnings_chunks
    )

    # 목표가/목표주가가 등장하는 주변 문맥에서 방향을 검출한다.
    target_up = 0
    target_down = 0
    target_hold = 0

    target_pattern = re.compile(r"목표(?:주)?가")

    for match in target_pattern.finditer(lower):
        left = max(0, match.start() - 80)
        right = min(len(lower), match.end() + 120)
        window = lower[left:right]

        target_up += window.count("상향")
        target_down += window.count("하향")
        target_hold += (
            window.count("유지")
            + window.count("동결")
        )

    if target_up > target_down:
        target_direction = "UP"
    elif target_down > target_up:
        target_direction = "DOWN"
    elif target_hold > 0 and target_up == 0 and target_down == 0:
        target_direction = "UNCHANGED"
    elif target_up and target_down:
        target_direction = "MIXED"
    else:
        target_direction = "UNKNOWN"

    topics = {}
    for label, terms in TOPIC_GROUPS.items():
        count = sum(lower.count(term.lower()) for term in terms)
        if count:
            topics[label] = count

    return {
        # 전체 어조의 투자판단으로 사용하지 않는 단순 텍스트 통계
        "textSignalBalance": {
            "positive": positive_count,
            "negative": negative_count,
        },
        "earningsTextDirection": _direction(
            earnings_positive,
            earnings_negative,
        ),
        "earningsPositiveSignalCount": earnings_positive,
        "earningsNegativeSignalCount": earnings_negative,
        "targetPriceMentionDirection": target_direction,
        "topics": dict(
            sorted(
                topics.items(),
                key=lambda x: (-x[1], x[0]),
            )
        ),
    }


def fetch_page(
    page_index: int,
    start_date: str,
    end_date: str,
    timeout: int = 30,
    retries: int = 3,
) -> dict[str, Any]:
    params = {
        "index": page_index,
        "size": PAGE_SIZE,
        "startDate": start_date,
        "endDate": end_date,
    }
    url = BASE_URL + ENDPOINT + "?" + urllib.parse.urlencode(params)

    last_error: Exception | None = None

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}: {url}")
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            if attempt + 1 == retries:
                break
            time.sleep(1.0 * (attempt + 1))

    raise RuntimeError(
        f"NAVER research request failed page={page_index}: {last_error}"
    )


def collect(
    start_date: str,
    end_date: str,
    sleep_seconds: float = 0.30,
) -> tuple[list[dict[str, Any]], int]:
    reports: list[dict[str, Any]] = []
    seen: set[str] = set()
    page = 0
    expected_total: int | None = None
    duplicate_count = 0

    while True:
        data = fetch_page(page, start_date, end_date)

        if expected_total is None:
            expected_total = int(data.get("totalCount") or 0)

        items = data.get("items")
        if not isinstance(items, list):
            raise RuntimeError(f"items is not a list at page={page}")

        page_seen: set[str] = set()
        for item in items:
            nid = str(item.get("nid") or "")
            if not nid:
                raise RuntimeError(f"missing nid at page={page}")
            if nid in page_seen:
                raise RuntimeError(f"duplicate nid within page={page}: {nid}")
            page_seen.add(nid)
            if nid in seen:
                previous = next(
                    report for report in reports if str(report.get("nid")) == nid
                )
                if item != previous:
                    raise RuntimeError(
                        f"conflicting duplicate nid across pages: {nid}"
                    )
                duplicate_count += 1
                continue
            seen.add(nid)
            reports.append(item)

        print(
            f"page={page:03d} received={len(items):2d} "
            f"cumulative={len(reports):5d} "
            f"hasNext={data.get('hasNext')}"
        )

        if not data.get("hasNext"):
            break

        page += 1
        time.sleep(sleep_seconds)

    if expected_total is None:
        expected_total = 0

    if len(reports) != expected_total:
        raise RuntimeError(
            f"totalCount mismatch: api={expected_total} collected={len(reports)} "
            f"pageBoundaryDuplicates={duplicate_count}"
        )

    return reports, expected_total


def compact_base(raw: dict[str, Any]) -> dict[str, Any]:
    body = strip_html(raw.get("content"))
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

    return {
        "nid": str(raw.get("nid")),
        "writeDate": raw.get("writeDate"),
        "itemCode": raw.get("itemCode"),
        "itemName": raw.get("itemName"),
        "title": raw.get("title"),
        "brokerCode": raw.get("brokerCode"),
        "brokerName": raw.get("brokerName"),
        "goalPrice": parse_goal_price(raw.get("goalPrice")),
        "opinionText": raw.get("opinionText"),
        "opinionType": raw.get("opinionType"),
        "normalizedOpinion": normalize_opinion(raw.get("opinionText")),
        "readCount": (
            int(raw["readCount"])
            if str(raw.get("readCount") or "").isdigit()
            else None
        ),
        "contentLength": len(body),
        "contentSha256": body_hash,
        "analysis": analyze_content(raw.get("content")),
    }


def report_sort_key(r: dict[str, Any]) -> tuple[str, int]:
    try:
        nid = int(str(r.get("nid") or 0))
    except ValueError:
        nid = 0
    return str(r.get("writeDate") or ""), nid


def apply_same_broker_changes(
    reports: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    ordered = sorted(reports, key=report_sort_key)

    # key별 "이전 날짜"의 마지막 리포트만 비교 기준으로 사용한다.
    previous_date_reports: dict[
        tuple[str, str], dict[str, Any]
    ] = {}

    result: list[dict[str, Any]] = []
    i = 0

    while i < len(ordered):
        current_date = str(ordered[i].get("writeDate") or "")
        same_date = []

        while (
            i < len(ordered)
            and str(ordered[i].get("writeDate") or "") == current_date
        ):
            same_date.append(ordered[i])
            i += 1

        processed_today = []

        for report in same_date:
            r = dict(report)
            key = (
                str(r.get("itemCode") or ""),
                str(r.get("brokerCode") or ""),
            )

            prev = previous_date_reports.get(key)

            r["previousSameBrokerNid"] = (
                prev.get("nid") if prev else None
            )
            r["goalPriceChange"] = None
            r["opinionChange"] = None
            r["goalPriceChangeLarge"] = False

            if prev:
                old_gp = prev.get("goalPrice")
                new_gp = r.get("goalPrice")

                if old_gp and new_gp and old_gp != new_gp:
                    pct = round(
                        (new_gp - old_gp) / old_gp * 100,
                        2,
                    )
                    r["goalPriceChange"] = {
                        "from": old_gp,
                        "to": new_gp,
                        "amount": new_gp - old_gp,
                        "pct": pct,
                    }
                    r["goalPriceChangeLarge"] = abs(pct) >= 50

                old_op = prev.get("normalizedOpinion")
                new_op = r.get("normalizedOpinion")

                if old_op != new_op:
                    r["opinionChange"] = {
                        "from": old_op,
                        "to": new_op,
                    }

            processed_today.append((key, r))
            result.append(r)

        # 오늘 데이터는 모두 처리한 뒤에야 다음 날짜의 previous 후보가 된다.
        # 같은 날짜에 여러 리포트가 있으면 nid가 가장 큰 것을 대표값으로 둔다.
        today_last = {}
        for key, r in processed_today:
            prev = today_last.get(key)
            if (
                prev is None
                or report_sort_key(r) > report_sort_key(prev)
            ):
                today_last[key] = r

        previous_date_reports.update(today_last)

    return sorted(result, key=report_sort_key, reverse=True)


def load_existing(output_dir: Path) -> list[dict[str, Any]]:
    reports_dir = output_dir / "reports"
    if not reports_dir.exists():
        return []

    found: dict[str, dict[str, Any]] = {}

    for path in sorted(reports_dir.glob("*.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for report in obj.get("reports", []):
            nid = str(report.get("nid") or "")
            if nid:
                found[nid] = report

    return list(found.values())


def build_outputs(
    raw_items: list[dict[str, Any]],
    output_dir: Path,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    existing = load_existing(output_dir)
    existing_by_nid = {
        str(x.get("nid")): x
        for x in existing
        if x.get("nid")
    }

    fresh = [compact_base(x) for x in raw_items]

    merged_by_nid = dict(existing_by_nid)
    for report in fresh:
        merged_by_nid[str(report["nid"])] = report

    all_reports = apply_same_broker_changes(list(merged_by_nid.values()))

    new_nids = sorted(
        set(str(x["nid"]) for x in fresh) - set(existing_by_nid)
    )

    # True no-op for scheduled incremental runs with no new reports.
    if existing_by_nid and not new_nids:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "source": "NAVER_STOCK_RESEARCH",
            "endpoint": ENDPOINT,
            "generatedAt": datetime.now(KST).isoformat(),
            "requestedStartDate": start_date,
            "requestedEndDate": end_date,
            "fetchedCount": len(raw_items),
            "newReportCount": 0,
            "storedReportCount": len(existing_by_nid),
            "stockCount": len({
                str(x.get("itemCode"))
                for x in existing_by_nid.values()
                if x.get("itemCode")
            }),
            "newNids": [],
            "unchanged": True,
        }

    by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_stock: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for report in all_reports:
        write_date = str(report.get("writeDate") or "")
        if len(write_date) >= 7:
            by_month[write_date[:7]].append(report)

        code = str(report.get("itemCode") or "")
        if code:
            by_stock[code].append(report)

    for month, reports in by_month.items():
        atomic_json_write(
            output_dir / "reports" / f"{month}.json",
            {
                "schemaVersion": SCHEMA_VERSION,
                "month": month,
                "count": len(reports),
                "reports": sorted(
                    reports,
                    key=report_sort_key,
                    reverse=True,
                ),
            },
        )

    stocks_dir = output_dir / "stocks"
    stocks_dir.mkdir(parents=True, exist_ok=True)

    for code, reports in by_stock.items():
        ordered = sorted(reports, key=report_sort_key, reverse=True)
        latest = ordered[0]

        atomic_json_write(
            stocks_dir / f"{code}.json",
            {
                "schemaVersion": SCHEMA_VERSION,
                "itemCode": code,
                "itemName": latest.get("itemName"),
                "reportCount": len(ordered),
                "latestReportNid": latest.get("nid"),
                "latestWriteDate": latest.get("writeDate"),
                "latest": {
                    "nid": latest.get("nid"),
                    "writeDate": latest.get("writeDate"),
                    "brokerName": latest.get("brokerName"),
                    "title": latest.get("title"),
                    "goalPrice": latest.get("goalPrice"),
                    "normalizedOpinion": latest.get(
                        "normalizedOpinion"
                    ),
                    "analysis": latest.get("analysis"),
                },
                "reportNids": [
                    r.get("nid") for r in ordered
                ],
            },
        )

    latest_reports = sorted(
        all_reports,
        key=report_sort_key,
        reverse=True,
    )[:200]

    atomic_json_write(
        output_dir / "latest.json",
        {
            "schemaVersion": SCHEMA_VERSION,
            "generatedAt": datetime.now(KST).isoformat(),
            "reportCount": len(all_reports),
            "stockCount": len(by_stock),
            "latest": latest_reports,
        },
    )

    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "source": "NAVER_STOCK_RESEARCH",
        "endpoint": ENDPOINT,
        "generatedAt": datetime.now(KST).isoformat(),
        "requestedStartDate": start_date,
        "requestedEndDate": end_date,
        "fetchedCount": len(raw_items),
        "newReportCount": len(new_nids),
        "storedReportCount": len(all_reports),
        "stockCount": len(by_stock),
        "newNids": new_nids,
    }

    atomic_json_write(output_dir / "manifest.json", manifest)
    return manifest


def resolve_dates(args: argparse.Namespace) -> tuple[str, str]:
    today = datetime.now(KST).date()

    if args.start_date:
        start = date.fromisoformat(args.start_date)
    elif args.mode == "backfill":
        start = today - timedelta(days=184)
    else:
        start = today - timedelta(days=args.lookback_days)

    end = date.fromisoformat(args.end_date) if args.end_date else today

    if start > end:
        raise SystemExit("start date must be <= end date")

    return start.isoformat(), end.isoformat()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=["backfill", "update"],
        nargs="?",
        default="update",
    )
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--sleep", type=float, default=0.30)
    parser.add_argument(
        "--output-dir",
        default="data/research",
    )
    args = parser.parse_args()

    start_date, end_date = resolve_dates(args)
    output_dir = Path(args.output_dir)

    print("=== NAVER RESEARCH ===")
    print("mode       :", args.mode)
    print("period     :", start_date, "~", end_date)
    print("output     :", output_dir)

    raw_items, total = collect(
        start_date,
        end_date,
        sleep_seconds=args.sleep,
    )

    manifest = build_outputs(
        raw_items,
        output_dir,
        start_date,
        end_date,
    )

    print("\n=== RESULT ===")
    print("API count        :", total)
    print("fetched          :", manifest["fetchedCount"])
    print("new reports      :", manifest["newReportCount"])
    print("stored reports   :", manifest["storedReportCount"])
    print("stocks           :", manifest["stockCount"])
    print("manifest         :", output_dir / "manifest.json")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
