#!/usr/bin/env python3
"""Build NAVER-authoritative Korean historical-universe asset files.

Operating watchlists and industry membership are intentionally not sources for
the historical market universe.  The latter remains an explicit fallback only.
"""

import argparse
import json
import random
import ssl
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CONTEXT = ssl.create_default_context()

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FALLBACK_SOURCE = ROOT / "data" / "market" / "industry-membership.json"
DEFAULT_OUTPUT = ROOT / "data" / "market-universe"
STOCKLIST_URL = "https://stock.naver.com/api/domestic/market/stock/default"
ETF_URL = "https://stock.naver.com/api/stockSecurity/etfs/v3/domestic"
ETN_URL = "https://stock.naver.com/api/domestic/market/etn"
STOCKLIST_PATH = "/api/domestic/market/stock/default"
ETF_PATH = "/api/stockSecurity/etfs/v3/domestic"
ETN_PATH = "/api/domestic/market/etn"
MARKETS = {"KOSPI": "0", "KOSDAQ": "1"}
STOCK_PAGE_SIZE = 200
ETF_PAGE_SIZE = 100  # NAVER v3 contract rejects values above 100.
ETN_PAGE_SIZE = 200
MIN_STOCK_COUNTS = {"KOSPI": 500, "KOSDAQ": 1000}
CODE_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")


class UniverseSourceError(RuntimeError):
    """A public-but-undocumented NAVER source did not satisfy its contract."""


def now_iso():
    return datetime.now().astimezone().isoformat()


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def classify_name(name):
    name = str(name)
    tags = []
    if "스팩" in name or "SPAC" in name.upper():
        tags.append("SPAC")
    if (name.endswith("우") or "우B" in name or "1우" in name or "2우" in name
            or "3우" in name or "(전환)" in name):
        tags.append("PREFERRED_LIKE")
    return tags


def valid_code(code):
    return bool(code) and all(char in CODE_CHARS for char in str(code))


def request_json(url, params, timeout=20, retries=2, expected_type=None):
    request = Request(url + "?" + urlencode(params), headers={
        "User-Agent": "Mozilla/5.0 (compatible; naver-krx-universe-relay/1.0)",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://stock.naver.com/market/stock/kr/stocklist/priceTop",
    })
    last_error = None
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if expected_type and not isinstance(payload, expected_type):
                raise UniverseSourceError("unexpected JSON response root")
            return payload
        except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError,
                UniverseSourceError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1) + random.uniform(0, 0.2))
    raise UniverseSourceError(f"request failed: {last_error}")


def stocklist_request(market, page_index, page_size, timeout=20, retries=2):
    return request_json(STOCKLIST_URL, {
        "tradeType": "KRX", "marketType": market, "orderType": "marketSum",
        "startIdx": page_index, "pageSize": page_size,
    }, timeout, retries, list)


def etf_request(page_index, page_size, timeout=20, retries=2):
    return request_json(ETF_URL, {
        "listingType": "tradingValueDesc", "size": page_size, "index": page_index,
    }, timeout, retries, dict)


def etn_request(page_index, page_size, timeout=20, retries=2):
    return request_json(ETN_URL, {
        "orderType": "AMOUNT_ETN", "startIdx": page_index, "pageSize": page_size,
    }, timeout, retries, list)


def _identity(code, name):
    code = str(code or "").strip()
    name = str(name or "").strip()
    if not valid_code(code) or not name:
        raise UniverseSourceError("row missing a valid item code or name")
    return code, name


def normalize_stock_row(row, market):
    code, name = _identity(row.get("itemcode"), row.get("itemname"))
    sosok = str(row.get("sosok") or "").strip()
    if sosok and sosok != MARKETS[market]:
        raise UniverseSourceError(f"market mismatch for {code}: {market} / sosok={sosok!r}")
    return {
        "code": code, "name": name, "assetType": "STOCK", "market": market,
        "sosok": MARKETS[market], "stockType": str(row.get("type") or "") or None,
        "tradableStatus": row.get("tradableStatus"),
        "tradableStatusCode": row.get("tradableStatusCode"),
        "tradeStopYn": row.get("tradeStopYn"), "listedDate": row.get("listedDate"),
        "tags": classify_name(name), "source": "naver_stocklist",
        "sourceApiPath": STOCKLIST_PATH,
    }


def normalize_etf_row(row):
    code, name = _identity(row.get("itemCode"), row.get("itemName"))
    return {
        "code": code, "name": name, "assetType": "ETF", "market": row.get("marketType"),
        "etfType": row.get("etfType"), "newlyListed": row.get("isNewlyListed"),
        "tradableStatus": (row.get("krx") or {}).get("tradableStatus"),
        "tradableStatusCode": (row.get("krx") or {}).get("tradableStatusCode"),
        "tags": [], "source": "naver_etf_list", "sourceApiPath": ETF_PATH,
    }


def normalize_etn_row(row):
    code, name = _identity(row.get("itemcode"), row.get("itemname"))
    return {
        "code": code, "name": name, "assetType": "ETN", "market": "KOSPI",
        "stockType": "ETN", "tradableStatus": row.get("tradableStatus"),
        "tradableStatusCode": row.get("tradableStatusCode"), "tags": [],
        "source": "naver_etn_list", "sourceApiPath": ETN_PATH,
    }


def _tag_counts(rows):
    counts = {}
    for row in rows:
        for tag in row.get("tags", []):
            counts[tag] = counts.get(tag, 0) + 1
    return counts


def _collect_pages(markets, fetch_page, normalize, page_size, max_pages, sleep_seconds,
                   page_param="startIdx", response_rows=lambda item: item):
    rows, failures, page_info = {}, {}, {}
    for group in markets:
        page_index = pages_fetched = 0
        while True:
            if pages_fetched >= max_pages:
                failures[f"{group}:pagination"] = f"max pages exceeded ({max_pages})"
                break
            try:
                response = fetch_page(group, page_index, page_size, 20, 2)
                page = response_rows(response)
                if not isinstance(page, list):
                    raise UniverseSourceError("page rows are not an array")
            except Exception as exc:
                failures[f"{group}:{page_param}={page_index}"] = str(exc)
                break
            pages_fetched += 1
            if not page:
                break
            for source_row in page:
                try:
                    row = normalize(source_row, group) if group in MARKETS else normalize(source_row)
                    if row["code"] in rows:
                        raise UniverseSourceError(f"duplicate code {row['code']}")
                    rows[row["code"]] = row
                except Exception as exc:
                    code = source_row.get("itemcode") or source_row.get("itemCode") if isinstance(source_row, dict) else page_index
                    failures[f"{group}:{code}"] = str(exc)
            if len(page) < page_size:
                break
            page_index += 1
            if sleep_seconds:
                time.sleep(sleep_seconds)
        page_info[group] = {"pagesFetched": pages_fetched, "endCondition": "EMPTY_OR_SHORT_ARRAY"}
    return sorted(rows.values(), key=lambda row: (row["assetType"], row.get("market") or "", row["code"])), failures, page_info


def collect_stocklist(fetch_page=stocklist_request, page_size=STOCK_PAGE_SIZE,
                      sleep_seconds=0.15, max_pages=30):
    if page_size < 1:
        raise ValueError("page_size must be positive")
    stocks, failures, pages = _collect_pages(MARKETS, fetch_page, normalize_stock_row,
                                               page_size, max_pages, sleep_seconds)
    counts = {market: sum(item["market"] == market for item in stocks) for market in MARKETS}
    return {"stocks": stocks, "metadata": {
        "source": "NAVER_STOCKLIST", "sourceApiPath": STOCKLIST_PATH, "tradeType": "KRX",
        "marketTypes": list(MARKETS), "pagination": {"type": "PAGE_INDEX", "parameter": "startIdx",
            "pageSize": page_size, "markets": pages}, "markets": counts,
        "resolvedCount": len(stocks), "failedCount": len(failures), "failures": failures,
        "tags": _tag_counts(stocks),
    }}


def collect_etfs(fetch_page=etf_request, page_size=ETF_PAGE_SIZE, sleep_seconds=0.15, max_pages=30):
    def fetch(_, index, size, timeout, retries):
        return fetch_page(index, size, timeout, retries)
    assets, failures, pages = _collect_pages(["ETF"], fetch, normalize_etf_row, page_size,
                                               max_pages, sleep_seconds, "index",
                                               lambda response: response.get("items"))
    return {"etfs": assets, "metadata": {"source": "NAVER_ETF_LIST", "sourceApiPath": ETF_PATH,
        "pagination": {"type": "PAGE_INDEX", "parameter": "index", "pageSize": page_size, "markets": pages},
        "resolvedCount": len(assets), "failedCount": len(failures), "failures": failures}}


def collect_etns(fetch_page=etn_request, page_size=ETN_PAGE_SIZE, sleep_seconds=0.15, max_pages=30):
    def fetch(_, index, size, timeout, retries):
        return fetch_page(index, size, timeout, retries)
    assets, failures, pages = _collect_pages(["ETN"], fetch, normalize_etn_row, page_size,
                                               max_pages, sleep_seconds)
    return {"etns": assets, "metadata": {"source": "NAVER_ETN_LIST", "sourceApiPath": ETN_PATH,
        "pagination": {"type": "PAGE_INDEX", "parameter": "startIdx", "pageSize": page_size, "markets": pages},
        "resolvedCount": len(assets), "failedCount": len(failures), "failures": failures}}


def ensure_complete(stock_payload):
    metadata = stock_payload["metadata"]
    if metadata["failedCount"]:
        raise UniverseSourceError("authoritative stock pagination has failures")
    for market, minimum in MIN_STOCK_COUNTS.items():
        if metadata["markets"].get(market, 0) < minimum:
            raise UniverseSourceError(f"authoritative {market} result below safety floor")


def ensure_assets_complete(*payloads):
    """Do not publish a mixed universe when any requested source paginates incompletely."""
    for payload in payloads:
        metadata = payload["metadata"]
        if metadata.get("failedCount"):
            raise UniverseSourceError(f"incomplete authoritative source: {metadata.get('source')}")
        if metadata.get("resolvedCount", 0) == 0:
            raise UniverseSourceError(f"empty authoritative source: {metadata.get('source')}")


def collect_all_assets(stock_fetch=stocklist_request, etf_fetch=etf_request, etn_fetch=etn_request,
                       sleep_seconds=0.15):
    stocks = collect_stocklist(stock_fetch, sleep_seconds=sleep_seconds)
    etfs = collect_etfs(etf_fetch, sleep_seconds=sleep_seconds)
    etns = collect_etns(etn_fetch, sleep_seconds=sleep_seconds)
    ensure_complete(stocks)
    ensure_assets_complete(stocks, etfs, etns)
    all_assets = stocks["stocks"] + etfs["etfs"] + etns["etns"]
    codes = [row["code"] for row in all_assets]
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    if duplicates:
        raise UniverseSourceError(f"duplicate codes across asset lists: {duplicates[:10]}")
    metadata = {
        "generatedAt": now_iso(), "sourceMode": "NAVER_AUTHORITATIVE", "fallbackUsed": False,
        "sources": {"stock": stocks["metadata"], "etf": etfs["metadata"], "etn": etns["metadata"]},
        "counts": {"KOSPI": stocks["metadata"]["markets"]["KOSPI"],
                   "KOSDAQ": stocks["metadata"]["markets"]["KOSDAQ"],
                   "STOCK": len(stocks["stocks"]), "ETF": len(etfs["etfs"]),
                   "ETN": len(etns["etns"]), "OTHER": 0},
        "duplicateCount": 0, "failureCount": sum(part["metadata"]["failedCount"] for part in (stocks, etfs, etns)),
        "classificationPolicy": "stocklist=STOCK; NAVER ETF/ETN dedicated APIs preserve ETF and ETN separately",
    }
    return {"stocks": stocks, "etfs": etfs, "etns": etns,
            "allAssets": {"metadata": metadata, "assets": all_assets}, "metadata": metadata}


def discover_candidates(obj):
    """Legacy discovery only; never an authoritative market-universe source."""
    rows = {}
    def walk(value):
        if isinstance(value, dict):
            code = value.get("code") or value.get("itemCode")
            name = value.get("name") or value.get("stockName") or value.get("itemName")
            if code and name:
                rows.setdefault(str(code).strip(), str(name).strip())
            for child in value.values(): walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    walk(obj)
    return [{"code": code, "name": name} for code, name in sorted(rows.items())]


def collect_industry_fallback(source):
    obj = load_json(source)
    if obj is None:
        raise UniverseSourceError(f"cannot read fallback source {source}")
    assets = [{"code": item["code"], "name": item["name"], "assetType": "UNKNOWN", "market": None,
               "tags": classify_name(item["name"]), "source": "industry_membership_fallback"}
              for item in discover_candidates(obj)]
    return {"metadata": {"generatedAt": now_iso(), "sourceMode": "INDUSTRY_MEMBERSHIP_FALLBACK",
             "fallbackUsed": True, "authoritative": False, "counts": {"OTHER": len(assets)}}, "assets": assets}


def write_universe(payload, output_root, asset_type="all"):
    out = Path(output_root)
    if "allAssets" not in payload:
        atomic_json(out / "all-assets.json", payload)
        atomic_json(out / "metadata.json", payload["metadata"])
        return
    if asset_type in ("stock", "all"):
        stock_payload = {"metadata": payload["stocks"]["metadata"], "stocks": payload["stocks"]["stocks"]}
        atomic_json(out / "stocks-kospi-kosdaq.json", stock_payload)
        # Compatibility filename, now carrying the same authoritative stock list.
        atomic_json(out / "stocks-all.json", stock_payload)
    if asset_type in ("etf", "all"):
        atomic_json(out / "etfs.json", payload["etfs"])
    if asset_type in ("etn", "all"):
        atomic_json(out / "etns.json", payload["etns"])
    if asset_type == "all":
        atomic_json(out / "all-assets.json", payload["allAssets"])
        atomic_json(out / "metadata.json", payload["metadata"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--mode", choices=("authoritative", "industry-fallback"), default="authoritative")
    parser.add_argument("--asset-type", choices=("stock", "etf", "etn", "all"), default="all")
    parser.add_argument("--source", default=str(DEFAULT_FALLBACK_SOURCE))
    parser.add_argument("--sleep", type=float, default=0.15)
    args = parser.parse_args()
    if args.mode == "authoritative":
        payload = collect_all_assets(sleep_seconds=args.sleep)
    else:
        payload = collect_industry_fallback(Path(args.source))
    write_universe(payload, args.output_root, args.asset_type)
    print(json.dumps(payload["metadata"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
