"""Stock search provider for the local Universe Manager UI.

Local Universe, market-ranking, and research metadata are preferred.  A single
NAVER autocomplete request is only attempted when local data has no match.
"""

import json
import os
import threading
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
UNIVERSE_PATH = ROOT / "config" / "universe.json"
RANKINGS_PATH = ROOT / "data" / "market" / "rankings.json"
RESEARCH_DIR = ROOT / "data" / "research" / "stocks"
_INDEX_CACHE = {}
_INDEX_CACHE_LOCK = threading.RLock()


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _market(value):
    return {"0": "KOSPI", "1": "KOSDAQ", "2": "KONEX"}.get(str(value))


def _source_fingerprint(universe_path):
    def file_fingerprint(path):
        try:
            stat = Path(path).stat()
            return (str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            return (str(path), None, None)

    research = []
    try:
        with os.scandir(RESEARCH_DIR) as entries:
            for entry in entries:
                if entry.name.endswith(".json") and entry.is_file():
                    try:
                        stat = entry.stat()
                        research.append((entry.name, stat.st_mtime_ns, stat.st_size))
                    except OSError:
                        research.append((entry.name, None, None))
    except OSError:
        pass
    return (file_fingerprint(universe_path), file_fingerprint(RANKINGS_PATH), tuple(sorted(research)))


def _build_local_stocks(universe_path):
    rows = {}
    universe = _load(universe_path, {})
    for item in universe.get("stocks", []):
        if isinstance(item, dict) and item.get("itemCode") and item.get("stockName"):
            rows[str(item["itemCode"])] = {"code": str(item["itemCode"]), "name": item["stockName"], "market": None,
                                            "inUniverse": True, "enabled": item.get("enabled"), "source": "universe"}
    rankings = _load(RANKINGS_PATH, {})
    for ranking in rankings.get("rankings", []):
        for item in ranking.get("rows", []):
            code, name = str(item.get("code", "")), item.get("name")
            if not code or not name:
                continue
            source = item.get("source", {})
            entry = rows.setdefault(code, {"code": code, "name": name, "market": None, "inUniverse": False, "enabled": None, "source": "market"})
            entry["market"] = entry["market"] or _market(source.get("sosok"))
    if RESEARCH_DIR.exists():
        for path in RESEARCH_DIR.glob("*.json"):
            item = _load(path, {})
            code, name = str(item.get("itemCode", "")), item.get("itemName")
            if code and name:
                rows.setdefault(code, {"code": code, "name": name, "market": None, "inUniverse": False, "enabled": None, "source": "research"})
    return tuple(rows.values())


def local_stocks(universe_path=UNIVERSE_PATH):
    """Build a deduplicated local index from existing project data only."""
    path = Path(universe_path)
    fingerprint = _source_fingerprint(path)
    key = str(path.resolve())
    with _INDEX_CACHE_LOCK:
        cached = _INDEX_CACHE.get(key)
        if cached and cached[0] == fingerprint:
            return [dict(row) for row in cached[1]]
        rows = _build_local_stocks(path)
        _INDEX_CACHE[key] = (fingerprint, rows)
        return [dict(row) for row in rows]


def _matches(row, query):
    value = query.casefold().strip()
    return row["code"] == value or row["name"].casefold() == value or row["code"].startswith(value) or row["name"].casefold().startswith(value) or value in row["name"].casefold()


def _remote_search(query, fetch_json):
    url = "https://ac.stock.naver.com/ac?q=" + quote(query) + "&target=stock&format=json"
    payload = fetch_json(url)
    items = payload.get("items", []) if isinstance(payload, dict) else []
    result = []
    for item in items:
        if isinstance(item, list) and len(item) >= 2:
            result.append({"code": str(item[0]), "name": item[1], "market": _market(item[2] if len(item) > 2 else None), "inUniverse": False, "enabled": None, "source": "naver"})
    return result


def _fetch_json(url):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def search_stocks(query, universe_path=UNIVERSE_PATH, fetch_json=_fetch_json, limit=12):
    query = str(query or "").strip()
    if not query:
        return {"results": [], "source": "local", "message": None}
    local = [row for row in local_stocks(universe_path) if _matches(row, query)]
    local.sort(key=lambda row: (row["name"] != query, not row["inUniverse"], row["name"]))
    if local:
        return {"results": local[:limit], "source": "local", "message": None}
    try:
        remote = _remote_search(query, fetch_json)
        return {"results": remote[:limit], "source": "naver", "message": None}
    except Exception:
        return {"results": [], "source": "local", "message": "종목 검색 서비스를 사용할 수 없습니다. 현재 Universe 종목만 검색할 수 있습니다."}
