"""OpenAI web-search-backed market tools.

All structured data retrieval in public fetchers uses OpenAI web search.
"""

import json
import logging
import os
import random
import re
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from langchain.tools import tool
from logging.handlers import RotatingFileHandler
from openai import OpenAI


_REQUEST_TIMEOUT_SECONDS = int(os.getenv("INVESTING_AGENT_HTTP_TIMEOUT_SECONDS", "10"))
_MIN_REQUEST_INTERVAL_SECONDS = float(os.getenv("INVESTING_AGENT_MIN_REQUEST_INTERVAL_SECONDS", "1.5"))
_MAX_HTTP_RETRIES = int(os.getenv("INVESTING_AGENT_MAX_HTTP_RETRIES", "4"))
_BASE_429_BACKOFF_SECONDS = float(os.getenv("INVESTING_AGENT_BASE_429_BACKOFF_SECONDS", "8"))
_MAX_429_BACKOFF_SECONDS = float(os.getenv("INVESTING_AGENT_MAX_429_BACKOFF_SECONDS", "45"))
_LAST_REQUEST_TS = 0.0
_GLOBAL_COOLDOWN_UNTIL_TS = 0.0

_SEARCH_RESULT_LIMIT = int(os.getenv("INVESTING_AGENT_SEARCH_RESULT_LIMIT", "5"))
_OPENAI_WEBSEARCH_ENABLED = (os.getenv("INVESTING_AGENT_ENABLE_OPENAI_WEBSEARCH", "1").strip().lower() not in {"0", "false", "no"})
_OPENAI_WEBSEARCH_MODEL = os.getenv("INVESTING_AGENT_WEBSEARCH_MODEL", "gpt-5-mini")

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
)

_HTTP_CACHE: Dict[str, Any] = {}
_RESULT_CACHE: Dict[str, Dict[str, Any]] = {}
_KEY_LOCKS: Dict[str, threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()
_REQUEST_GUARD = threading.Lock()
_OPENAI_CLIENT: Optional[OpenAI] = None

_PERIOD_TO_QUERY = {
    "1d": "1 day",
    "5d": "5 day",
    "1mo": "1 month",
    "3mo": "3 month",
    "6mo": "6 month",
    "1y": "1 year",
    "ytd": "year to date",
}

_DEFAULT_FINANCE_DOMAINS = [
    "reuters.com",
    "marketwatch.com",
    "nasdaq.com",
    "investing.com",
    "stockanalysis.com",
    "morningstar.com",
    "fool.com",
    "cnbc.com",
]


# ---------------- Logging ----------------
def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("investing_agent.tools")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    log_dir = Path(os.getenv("INVESTING_AGENT_LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "tools.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


LOGGER = _setup_logger()


def _get_openai_client() -> OpenAI:
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is not None:
        return _OPENAI_CLIENT
    api_key = (os.getenv("OPENAI_API_KEY", "") or "").strip().strip("\"'")
    if not api_key:
        try:
            from dotenv import load_dotenv

            load_dotenv()
            api_key = (os.getenv("OPENAI_API_KEY", "") or "").strip().strip("\"'")
        except Exception:
            pass
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set. Add it to your environment or .env file.")
    _OPENAI_CLIENT = OpenAI(api_key=api_key)
    return _OPENAI_CLIENT


# ---------------- Basic utils ----------------
def _get_key_lock(key: str) -> threading.Lock:
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _KEY_LOCKS[key] = lock
        return lock


def _cache_key(prefix: str, *parts: str) -> str:
    return prefix + ":" + "|".join(parts)


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def _fmt_currency(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    try:
        return f"${float(value):,.{decimals}f}"
    except Exception:
        return "N/A"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return "N/A"


def _parse_ticker_list(raw: str) -> List[str]:
    if not raw:
        return []
    items = re.split(r"[,\s]+", raw.strip().upper())
    out: List[str] = []
    seen = set()
    for item in items:
        token = item.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _normalize_sources(raw: Any) -> List[Dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, str]] = []
    seen = set()
    for item in raw:
        if isinstance(item, str):
            url = item.strip()
            label = "Web source"
        elif isinstance(item, dict):
            url = str(item.get("url", "")).strip()
            label = str(item.get("label", "Web source")).strip() or "Web source"
        else:
            continue
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        out.append({"label": label, "url": url})
    return out


def _throttle() -> None:
    global _LAST_REQUEST_TS, _GLOBAL_COOLDOWN_UNTIL_TS
    while True:
        with _REQUEST_GUARD:
            now = time.time()
            wait_for = max(_MIN_REQUEST_INTERVAL_SECONDS - (now - _LAST_REQUEST_TS), _GLOBAL_COOLDOWN_UNTIL_TS - now, 0.0)
            if wait_for <= 0:
                _LAST_REQUEST_TS = time.time()
                return
        time.sleep(min(wait_for, 1.0))


def _set_global_cooldown(seconds: float) -> None:
    global _GLOBAL_COOLDOWN_UNTIL_TS
    if seconds <= 0:
        return
    with _REQUEST_GUARD:
        until = time.time() + seconds
        if until > _GLOBAL_COOLDOWN_UNTIL_TS:
            _GLOBAL_COOLDOWN_UNTIL_TS = until


def _http_get(url: str, params: Optional[Dict[str, Any]] = None, response_kind: str = "text") -> Any:
    key = _cache_key("http", response_kind, url, json.dumps(params or {}, sort_keys=True))
    if key in _HTTP_CACHE:
        LOGGER.info("HTTP cache hit url=%s params=%s", url, params)
        return _HTTP_CACHE[key]

    for attempt in range(1, _MAX_HTTP_RETRIES + 1):
        try:
            LOGGER.info("HTTP GET attempt=%s/%s url=%s params=%s", attempt, _MAX_HTTP_RETRIES, url, params)
            _throttle()
            resp = _SESSION.get(url, params=params, timeout=_REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            payload = resp.json() if response_kind == "json" else resp.text
            _HTTP_CACHE[key] = payload
            return payload
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 429 and attempt < _MAX_HTTP_RETRIES:
                backoff = min(_BASE_429_BACKOFF_SECONDS * (2 ** (attempt - 1)), _MAX_429_BACKOFF_SECONDS)
                backoff += random.uniform(0, 1.5)
                _set_global_cooldown(backoff)
                LOGGER.warning("HTTP 429 url=%s attempt=%s backoff=%.1fs", url, attempt, backoff)
                continue
            raise


def _duckduckgo_search(query: str, max_results: int = 5, query_domains: Optional[List[str]] = None) -> List[Dict[str, str]]:
    search_query = query
    if query_domains:
        domain_query = " OR ".join([f"site:{d}" for d in query_domains if d])
        if domain_query:
            search_query = f"({domain_query}) {query}"
    cache_key = _cache_key("ddg", search_query, str(max_results))
    if cache_key in _RESULT_CACHE:
        cached = _RESULT_CACHE[cache_key]
        if isinstance(cached, dict) and isinstance(cached.get("results"), list):
            return cached.get("results", [])

    try:
        from ddgs import DDGS  # lazy import so module loads even if package is not installed yet
    except Exception as exc:
        raise ValueError("Package 'ddgs' is required. Install it with `pip install ddgs`.") from exc

    out: List[Dict[str, str]] = []
    try:
        _throttle()
        with DDGS() as ddg:
            items = ddg.text(search_query, region="wt-wt", max_results=max_results) or []
    except Exception as exc:
        LOGGER.warning("DuckDuckGo search failed query=%s error=%s", search_query, exc)
        items = []

    out: List[Dict[str, str]] = []
    for result in items:
        try:
            link = (result.get("href") or result.get("url") or "").strip()
            if not link or "youtube.com" in link:
                continue
            out.append(
                {
                    "title": (result.get("title") or "").strip(),
                    "href": link,
                    "body": (result.get("body") or result.get("snippet") or "").strip(),
                }
            )
        except Exception:
            continue

    LOGGER.info("DuckDuckGo search query=%s results=%s", search_query, len(out))
    LOGGER.info("DuckDuckGo search raw items query=%s items=%s", search_query, out)
    final = out[:max_results]
    _RESULT_CACHE[cache_key] = {"results": final}
    return final


def _google_search(query: str, max_results: int = 5, query_domains: Optional[List[str]] = None) -> List[Dict[str, str]]:
    # Scraping retriever disabled by request; keep compatibility surface.
    LOGGER.warning("Scraping retriever disabled; _google_search returns empty query=%s", query)
    return []


def _google_top_links(query: str, limit: int = 5) -> List[str]:
    try:
        data = _search_json_fallback(
            query=f"Find top relevant sources for: {query}. Return source URLs only.",
            schema_hint='{"sources": [{"label": string, "url": string}]}',
        )
        sources = _normalize_sources(data.get("sources"))
        return [s["url"] for s in sources[:limit]]
    except Exception as exc:
        LOGGER.warning("Top-link OpenAI web-search failed query=%s error=%s", query, exc)
        return []


def _coerce_json(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def _search_json_fallback(query: str, schema_hint: str) -> Dict[str, Any]:
    if not _OPENAI_WEBSEARCH_ENABLED:
        LOGGER.warning("OpenAI web search is disabled query=%s", query[:180])
        return {}
    key = _cache_key("openai_websearch", query, schema_hint)
    if key in _RESULT_CACHE:
        return _RESULT_CACHE[key]
    with _get_key_lock(key):
        if key in _RESULT_CACHE:
            return _RESULT_CACHE[key]
        client = _get_openai_client()
        LOGGER.warning("OpenAI web search invoked query=%s", query[:200])
        response = client.responses.create(
            model=_OPENAI_WEBSEARCH_MODEL,
            tools=[{"type": "web_search_preview"}],
            input=(
                "Return strict JSON only. Do not include markdown fences.\n"
                f"Schema:\n{schema_hint}\n\n"
                f"Task:\n{query}"
            ),
        )
        text = (getattr(response, "output_text", None) or "").strip()
        data = _coerce_json(text)
        if not isinstance(data, dict):
            data = {}
        _RESULT_CACHE[key] = data
        return data


# ---------------- BeautifulSoup scraper ----------------
def _clean_soup(soup: BeautifulSoup) -> BeautifulSoup:
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.extract()
    return soup


def _extract_title(soup: BeautifulSoup) -> str:
    if soup.title and soup.title.string:
        return re.sub(r"\s+", " ", soup.title.string).strip()
    h1 = soup.find("h1")
    if h1:
        return re.sub(r"\s+", " ", h1.get_text(" ", strip=True)).strip()
    return ""


def _get_text_from_soup(soup: BeautifulSoup) -> str:
    text = soup.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _scrape_page(link: str) -> Tuple[str, str]:
    try:
        html = _http_get(link, response_kind="text")
        soup = BeautifulSoup(html, "lxml")
        soup = _clean_soup(soup)
        content = _get_text_from_soup(soup)
        title = _extract_title(soup)
        LOGGER.info("Scrape success url=%s title=%s snippet=%s", link, title[:120], content[:500])
        return content, title
    except Exception as exc:
        LOGGER.warning("Scrape failed url=%s error=%s", link, exc)
        return "", ""


# ---------------- Extraction helpers ----------------
def _extract_first_pct(text: str) -> Optional[float]:
    m = re.search(r"([+\-]?[0-9]{1,3}(?:\.[0-9]{1,3})?)%", text)
    return _safe_float(m.group(1)) if m else None


def _symbol_mentioned(text: str, symbol: str) -> bool:
    if not text:
        return False
    sym = re.escape(symbol.upper())
    patterns = [
        rf"\b{sym}\b",
        rf"\b{sym}\.TO\b",
        rf"\b{sym}-CA\b",
        rf"\b{sym}\.CA\b",
        rf"\b{sym}:[A-Z]+\b",
    ]
    upper = text.upper()
    return any(re.search(p, upper) for p in patterns)


def _result_relevant(symbol: str, link: str, title: str, snippet: str, content: str) -> bool:
    # Hard reject clearly mismatched ticker pages by URL/title/content checks.
    if _symbol_mentioned(link, symbol):
        return True
    merged = " ".join([title or "", snippet or "", (content or "")[:4000]])
    return _symbol_mentioned(merged, symbol)


def _best_blob(snippet: str, content: str) -> str:
    # DDG snippets can contain merged unrelated entries; prefer scraped page content.
    cleaned_content = (content or "").strip()
    if len(cleaned_content) >= 120:
        return cleaned_content
    return f"{snippet or ''} {cleaned_content}".strip()


def _cnbc_symbol_candidates(symbol: str) -> List[str]:
    s = (symbol or "").upper().strip()
    if not s:
        return []
    out: List[str] = []
    if s.endswith(".TO"):
        root = s[:-3]
        out.extend([f"{root}-CA", root])
    elif s.endswith("-CA"):
        out.extend([s, s[:-3]])
    else:
        out.extend([s, f"{s}-CA"])
    deduped: List[str] = []
    seen = set()
    for item in out:
        token = item.strip()
        if token and token not in seen:
            seen.add(token)
            deduped.append(token)
    return deduped


def _extract_range_after(label_regex: str, text: str) -> Tuple[Optional[float], Optional[float]]:
    m = re.search(
        label_regex + r"\s*[:\-]?\s*\$?\s*([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{1,4})?)\s*[-–]\s*\$?\s*([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{1,4})?)",
        text,
        flags=re.IGNORECASE,
    )
    if not m:
        return None, None
    return _safe_float(m.group(1)), _safe_float(m.group(2))


def _extract_token_after(label_regex: str, text: str) -> Optional[str]:
    m = re.search(label_regex + r"\s*[:\-]?\s*([A-Za-z0-9 .,%/$&+\-]{2,80})", text, flags=re.IGNORECASE)
    if not m:
        return None
    token = re.sub(r"\s+", " ", m.group(1)).strip(" .,:;")
    return token or None


def _parse_cnbc_key_facts(content: str, title: str = "") -> Dict[str, Any]:
    blob = f"{title} {content}"
    day_low, day_high = _extract_range_after(r"(?:day|day's)\s*range", blob)
    wk52_low, wk52_high = _extract_range_after(r"(?:52\s*week|52-week)\s*range", blob)

    company = None
    if title:
        company = re.sub(r"\s*[-|:]\s*.*$", "", title).strip()

    data: Dict[str, Any] = {
        "company": company,
        "price": _extract_number_after(r"(?:last|last price|price)", blob) or _extract_first_price(blob),
        "open": _extract_number_after(r"open", blob),
        "high": _extract_number_after(r"(?:day|intraday)\s*high", blob) or day_high,
        "low": _extract_number_after(r"(?:day|intraday)\s*low", blob) or day_low,
        "volume": _extract_volume(blob),
        "change_percent": _extract_first_pct(blob),
        "market_cap": _extract_market_cap(blob),
        "pe_ratio": _extract_pe(blob),
        "dividend_yield": _extract_dividend_yield(blob),
        "fifty_two_week_high": _extract_number_after(r"(?:52\s*week|52-week)\s*high", blob) or wk52_high,
        "fifty_two_week_low": _extract_number_after(r"(?:52\s*week|52-week)\s*low", blob) or wk52_low,
        "sector": _extract_token_after(r"sector", blob),
        "industry": _extract_token_after(r"industry", blob),
    }
    return data


def _fetch_cnbc_quote_data(symbol: str) -> Dict[str, Any]:
    symbol = symbol.upper()
    key = _cache_key("cnbc_quote", symbol)
    if key in _RESULT_CACHE:
        return _RESULT_CACHE[key]
    with _get_key_lock(key):
        if key in _RESULT_CACHE:
            return _RESULT_CACHE[key]

        for cand in _cnbc_symbol_candidates(symbol):
            url = f"https://www.cnbc.com/quotes/{cand}"
            content, title = _scrape_page(url)
            if not content:
                continue
            if not _result_relevant(cand, url, title, "", content):
                continue
            parsed = _parse_cnbc_key_facts(content, title=title)
            parsed["symbol"] = symbol
            parsed["cnbc_symbol"] = cand
            parsed["source_url"] = url
            LOGGER.info(
                "CNBC key facts parsed symbol=%s cnbc_symbol=%s price=%s high=%s low=%s volume=%s",
                symbol,
                cand,
                parsed.get("price"),
                parsed.get("high"),
                parsed.get("low"),
                parsed.get("volume"),
            )
            _RESULT_CACHE[key] = parsed
            return parsed

        LOGGER.warning("CNBC quote parse returned no usable result symbol=%s", symbol)
        empty = {"symbol": symbol, "source_url": None}
        _RESULT_CACHE[key] = empty
        return empty


def _extract_price_candidates(text: str) -> List[float]:
    vals: List[float] = []
    for m in re.finditer(r"\$\s?([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{1,4})?)", text):
        v = _safe_float(m.group(1))
        if v is not None and 0 < v < 500000:
            vals.append(v)
    return vals


def _extract_first_price(text: str) -> Optional[float]:
    vals = _extract_price_candidates(text)
    return vals[0] if vals else None


def _extract_number_after(label_regex: str, text: str) -> Optional[float]:
    m = re.search(label_regex + r"\s*[:\-]?\s*\$?\s*([0-9]{1,6}(?:,[0-9]{3})*(?:\.[0-9]{1,4})?)", text, flags=re.IGNORECASE)
    return _safe_float(m.group(1)) if m else None


def _extract_volume(text: str) -> Optional[float]:
    m = re.search(r"(?:volume|vol)\s*[:\-]?\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)", text, flags=re.IGNORECASE)
    return _safe_float(m.group(1)) if m else None


def _extract_market_cap(text: str) -> Optional[str]:
    m = re.search(r"market cap\s*[:\-]?\s*\$?([0-9.,]+\s*[TMBK]?)", text, flags=re.IGNORECASE)
    return m.group(1).strip() if m else None


def _extract_pe(text: str) -> Optional[str]:
    m = re.search(r"(?:p/e|pe ratio|price[- ]to[- ]earnings)\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)", text, flags=re.IGNORECASE)
    return m.group(1).strip() if m else None


def _extract_dividend_yield(text: str) -> Optional[str]:
    m = re.search(r"dividend yield\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?%)", text, flags=re.IGNORECASE)
    return m.group(1).strip() if m else None


def _extract_sector_industry(text: str) -> Tuple[Optional[str], Optional[str]]:
    sector = None
    industry = None
    s = re.search(r"\bsector\s*[:\-]?\s*([A-Z][A-Za-z0-9 &/\-]{2,80})", text, flags=re.IGNORECASE)
    i = re.search(r"\bindustry\s*[:\-]?\s*([A-Z][A-Za-z0-9 &/\-]{2,80})", text, flags=re.IGNORECASE)
    if s:
        sector = s.group(1).strip(" .,:;")
    if i:
        industry = i.group(1).strip(" .,:;")
    return sector, industry


# ---------------- Public structured fetchers ----------------
def fetch_stock_snapshot_data(symbol: str, period: str = "1mo") -> Dict[str, Any]:
    symbol = symbol.upper()
    period = (period or "1mo").lower()
    period_key = _cache_key("stock_snapshot", symbol, period)
    symbol_key = _cache_key("stock_snapshot_symbol", symbol)
    if symbol_key in _RESULT_CACHE:
        cached = _RESULT_CACHE[symbol_key]
        LOGGER.info(
            "fetch_stock_snapshot_data symbol-level cache hit symbol=%s requested_period=%s cached_period=%s",
            symbol,
            period,
            cached.get("period"),
        )
        return cached
    if period_key in _RESULT_CACHE:
        cached = _RESULT_CACHE[period_key]
        _RESULT_CACHE[symbol_key] = cached
        return cached

    with _get_key_lock(symbol_key):
        if symbol_key in _RESULT_CACHE:
            return _RESULT_CACHE[symbol_key]
        if period_key in _RESULT_CACHE:
            cached = _RESULT_CACHE[period_key]
            _RESULT_CACHE[symbol_key] = cached
            return cached
        period_text = _PERIOD_TO_QUERY.get(period, "1 month")
        LOGGER.info("fetch_stock_snapshot_data start symbol=%s period=%s via OpenAI web search", symbol, period)
        data = _search_json_fallback(
            query=(
                f"Get a consolidated stock snapshot for {symbol}. "
                f"Include latest quote, profile, fundamentals, and historical performance over {period_text} "
                "plus 1-year high/low context. Provide reliable source URLs."
            ),
            schema_hint=(
                "{"
                '"symbol": string, "company": string|null, "sector": string|null, "industry": string|null, '
                '"price": number|null, "currency": string|null, "open": number|null, "day_high": number|null, '
                '"day_low": number|null, "volume": number|null, "change_percent": number|null, '
                '"latest_trading_day": string|null, '
                '"period_start_price": number|null, "period_end_price": number|null, "period_change_percent": number|null, '
                '"period_high": number|null, "period_low": number|null, "avg_volume": number|null, "period_interpretation": string|null, '
                '"market_cap": string|null, "pe_ratio": string|null, "dividend_yield": string|null, '
                '"fifty_two_week_high": number|null, "fifty_two_week_low": number|null, '
                '"recommendation": string|null, "rationale": string|null, '
                '"sources": [{"label": string, "url": string}]'
                "}"
            ),
        )
        out = {
            "symbol": symbol,
            "period": period,
            "company": data.get("company"),
            "sector": data.get("sector"),
            "industry": data.get("industry"),
            "price": _safe_float(data.get("price")),
            "currency": data.get("currency"),
            "open": _safe_float(data.get("open")),
            "day_high": _safe_float(data.get("day_high")),
            "day_low": _safe_float(data.get("day_low")),
            "volume": _safe_float(data.get("volume")),
            "change_percent": _safe_float(data.get("change_percent")),
            "latest_trading_day": data.get("latest_trading_day"),
            "period_start_price": _safe_float(data.get("period_start_price")),
            "period_end_price": _safe_float(data.get("period_end_price")),
            "period_change_percent": _safe_float(data.get("period_change_percent")),
            "period_high": _safe_float(data.get("period_high")),
            "period_low": _safe_float(data.get("period_low")),
            "avg_volume": _safe_float(data.get("avg_volume")),
            "period_interpretation": data.get("period_interpretation"),
            "market_cap": data.get("market_cap"),
            "pe_ratio": data.get("pe_ratio"),
            "dividend_yield": data.get("dividend_yield"),
            "fifty_two_week_high": _safe_float(data.get("fifty_two_week_high")),
            "fifty_two_week_low": _safe_float(data.get("fifty_two_week_low")),
            "recommendation": data.get("recommendation"),
            "rationale": data.get("rationale"),
            "sources": _normalize_sources(data.get("sources")),
        }
        _RESULT_CACHE[period_key] = out
        _RESULT_CACHE[symbol_key] = out
        return out


def fetch_stock_price_data(symbol: str) -> Dict[str, Any]:
    symbol = symbol.upper()
    key = _cache_key("stock_price", symbol)
    if key in _RESULT_CACHE:
        return _RESULT_CACHE[key]

    with _get_key_lock(key):
        if key in _RESULT_CACHE:
            return _RESULT_CACHE[key]
        data = fetch_stock_snapshot_data(symbol, "1mo")
        out = {
            "symbol": symbol,
            "price": _safe_float(data.get("price")),
            "currency": data.get("currency") or ("USD" if data.get("price") not in (None, "") else None),
            "open": _safe_float(data.get("open")),
            "high": _safe_float(data.get("day_high")),
            "low": _safe_float(data.get("day_low")),
            "volume": _safe_float(data.get("volume")),
            "change_percent": _safe_float(data.get("change_percent")),
            "latest_trading_day": data.get("latest_trading_day") or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "summary": "Quote retrieved via OpenAI web search.",
            "sources": _normalize_sources(data.get("sources")),
        }
        _RESULT_CACHE[key] = out
        return out


def fetch_stock_historical_summary(symbol: str, period: str = "1mo") -> Dict[str, Any]:
    symbol = symbol.upper()
    period = (period or "1mo").lower()
    key = _cache_key("stock_hist", symbol, period)
    if key in _RESULT_CACHE:
        return _RESULT_CACHE[key]

    with _get_key_lock(key):
        if key in _RESULT_CACHE:
            return _RESULT_CACHE[key]
        data = fetch_stock_snapshot_data(symbol, period)
        out = {
            "symbol": symbol,
            "period": period,
            "start_price": _safe_float(data.get("period_start_price")),
            "end_price": _safe_float(data.get("period_end_price")),
            "high": _safe_float(data.get("period_high")),
            "low": _safe_float(data.get("period_low")),
            "avg_volume": _safe_float(data.get("avg_volume")),
            "change_percent": _safe_float(data.get("period_change_percent")),
            "interpretation": data.get("period_interpretation") or "Trend context via OpenAI web search.",
            "sources": _normalize_sources(data.get("sources")),
        }
        _RESULT_CACHE[key] = out
        return out


def fetch_market_trends_data(period: str = "1mo") -> Dict[str, Any]:
    period = (period or "1mo").lower()
    key = _cache_key("market", period)
    if key in _RESULT_CACHE:
        return _RESULT_CACHE[key]

    with _get_key_lock(key):
        if key in _RESULT_CACHE:
            return _RESULT_CACHE[key]
        LOGGER.info("fetch_market_trends_data start period=%s via OpenAI web search", period)
        data = _search_json_fallback(
            query=f"Get market trend summary over {period} for S&P 500, Dow Jones, NASDAQ, and VIX. Include change percent and short notes for each.",
            schema_hint=(
                "{"
                '"period": string, "overview": string, "indices": ['
                '{"name": string, "symbol": string, "change_percent": number|null, "note": string}'
                '], "sources": [{"label": string, "url": string}]'
                "}"
            ),
        )
        indices: List[Dict[str, Any]] = []
        for item in data.get("indices", []) if isinstance(data.get("indices"), list) else []:
            if not isinstance(item, dict):
                continue
            indices.append(
                {
                    "name": item.get("name") or "N/A",
                    "symbol": item.get("symbol") or "N/A",
                    "change_percent": _safe_float(item.get("change_percent")),
                    "note": item.get("note") or "",
                }
            )
        out = {
            "period": period,
            "overview": data.get("overview") or "Market overview via OpenAI web search.",
            "indices": indices,
            "sources": _normalize_sources(data.get("sources")),
        }
        _RESULT_CACHE[key] = out
        return out


def fetch_ticker_profile_data(symbol: str, period: str = "1mo") -> Dict[str, Any]:
    symbol = symbol.upper()
    period = (period or "1mo").lower()
    key = _cache_key("profile", symbol)
    if key in _RESULT_CACHE:
        return _RESULT_CACHE[key]

    with _get_key_lock(key):
        if key in _RESULT_CACHE:
            return _RESULT_CACHE[key]
        data = fetch_stock_snapshot_data(symbol, period)
        out = {
            "symbol": symbol,
            "company": data.get("company"),
            "sector": data.get("sector"),
            "industry": data.get("industry"),
            "sources": _normalize_sources(data.get("sources")),
        }
        _RESULT_CACHE[key] = out
        return out


def fetch_stock_fundamentals_data(symbol: str, period: str = "1mo") -> Dict[str, Any]:
    symbol = symbol.upper()
    period = (period or "1mo").lower()
    key = _cache_key("fund", symbol)
    if key in _RESULT_CACHE:
        return _RESULT_CACHE[key]

    with _get_key_lock(key):
        if key in _RESULT_CACHE:
            return _RESULT_CACHE[key]
        data = fetch_stock_snapshot_data(symbol, period)
        out = {
            "symbol": symbol,
            "company": data.get("company"),
            "sector": data.get("sector"),
            "industry": data.get("industry"),
            "market_cap": data.get("market_cap"),
            "pe_ratio": data.get("pe_ratio"),
            "dividend_yield": data.get("dividend_yield"),
            "fifty_two_week_high": _safe_float(data.get("fifty_two_week_high")),
            "fifty_two_week_low": _safe_float(data.get("fifty_two_week_low")),
            "recommendation": (data.get("recommendation") or "HOLD"),
            "rationale": data.get("rationale") or "Fundamental context via OpenAI web search.",
            "sources": _normalize_sources(data.get("sources")),
        }
        _RESULT_CACHE[key] = out
        return out


def fetch_sector_performance_data(sector: str = "technology", tickers: Optional[List[str]] = None, period: str = "1mo") -> Dict[str, Any]:
    parsed_tickers = [t.strip().upper() for t in (tickers or []) if t and t.strip()]
    resolved_sector = (sector or "").strip().lower()

    dominant_sector = None
    dominant_industry = None
    sector_counts: Dict[str, int] = {}
    industry_counts: Dict[str, int] = {}
    profile_sources: List[Dict[str, str]] = []

    if parsed_tickers:
        sector_counter: Counter = Counter()
        industry_counter: Counter = Counter()
        for sym in parsed_tickers:
            profile = fetch_ticker_profile_data(sym, period=period)
            sec = (profile.get("sector") or "").strip().lower()
            ind = (profile.get("industry") or "").strip().lower()
            if sec:
                sector_counter[sec] += 1
            if ind:
                industry_counter[ind] += 1
            profile_sources.extend(profile.get("sources") or [])

        if sector_counter:
            dominant_sector = sector_counter.most_common(1)[0][0]
            sector_counts = dict(sector_counter)
        if industry_counter:
            dominant_industry = industry_counter.most_common(1)[0][0]
            industry_counts = dict(industry_counter)

        if not resolved_sector or resolved_sector in {"auto", "ticker", "tickers"}:
            resolved_sector = dominant_sector or "technology"

    if not resolved_sector:
        resolved_sector = "technology"

    key = _cache_key("sector", resolved_sector, ",".join(parsed_tickers))
    if key in _RESULT_CACHE:
        return _RESULT_CACHE[key]

    with _get_key_lock(key):
        if key in _RESULT_CACHE:
            return _RESULT_CACHE[key]
        LOGGER.info("fetch_sector_performance_data start sector=%s via OpenAI web search", resolved_sector)
        data = _search_json_fallback(
            query=(
                f"Get current performance overview for the {resolved_sector} sector: 1-month change percent, "
                "sector ETF proxy symbol, and key drivers. "
                f"Ticker context: {', '.join(parsed_tickers) if parsed_tickers else 'none'}."
            ),
            schema_hint=(
                "{"
                '"sector": string, "proxy": string|null, "one_month_change_percent": number|null, '
                '"overview": string, "drivers": [string], "sources": [{"label": string, "url": string}]'
                "}"
            ),
        )
        out = {
            "sector": data.get("sector") or resolved_sector,
            "proxy": data.get("proxy"),
            "one_month_change_percent": _safe_float(data.get("one_month_change_percent")),
            "overview": data.get("overview") or "Sector context via OpenAI web search.",
            "drivers": data.get("drivers") if isinstance(data.get("drivers"), list) else [],
            "ticker_context": {
                "tickers": parsed_tickers,
                "dominant_sector": dominant_sector,
                "dominant_industry": dominant_industry,
                "sector_counts": sector_counts,
                "industry_counts": industry_counts,
            },
            "sources": _normalize_sources((data.get("sources") or []) + profile_sources),
        }
        _RESULT_CACHE[key] = out
        return out


# ---------------- LangChain tool wrappers ----------------
@tool
def get_stock_price(ticker: str) -> str:
    """Get latest stock price and intraday stats for a ticker."""
    symbol = ticker.upper()
    LOGGER.info("tool get_stock_price start ticker=%s", symbol)
    try:
        d = fetch_stock_price_data(symbol)
        sources = ", ".join(s["url"] for s in d.get("sources", [])) or "N/A"
        volume = d.get("volume")
        volume_text = f"{float(volume):,.0f}" if volume not in (None, "") else "N/A"
        return (
            f"Stock: {symbol}\n"
            f"Current Price: {_fmt_currency(d.get('price'), 4)}\n"
            f"Open: {_fmt_currency(d.get('open'), 4)}\n"
            f"Day High: {_fmt_currency(d.get('high'), 4)}\n"
            f"Day Low: {_fmt_currency(d.get('low'), 4)}\n"
            f"Volume: {volume_text}\n"
            f"Change: {_fmt_pct(d.get('change_percent'))}\n"
            f"Summary: {d.get('summary', 'N/A')}\n"
            f"Source URLs: {sources}\n"
        )
    except Exception as e:
        LOGGER.exception("tool get_stock_price failed ticker=%s error=%s", symbol, e)
        return f"Error fetching stock price for {symbol}: {e}"


@tool
def get_stock_historical_data(ticker: str, period: str = "1mo") -> str:
    """Get historical performance summary for a ticker over a selected period."""
    symbol = ticker.upper()
    LOGGER.info("tool get_stock_historical_data start ticker=%s period=%s", symbol, period)
    try:
        d = fetch_stock_historical_summary(symbol, period)
        sources = ", ".join(s["url"] for s in d.get("sources", [])) or "N/A"
        avg_volume = d.get("avg_volume")
        avg_volume_text = f"{float(avg_volume):,.0f}" if avg_volume not in (None, "") else "N/A"
        return (
            f"Historical Data for {symbol} ({period}):\n"
            f"Start Price: {_fmt_currency(d.get('start_price'))}\n"
            f"End Price: {_fmt_currency(d.get('end_price'))}\n"
            f"Period Change: {_fmt_pct(d.get('change_percent'))}\n"
            f"Highest: {_fmt_currency(d.get('high'))}\n"
            f"Lowest: {_fmt_currency(d.get('low'))}\n"
            f"Average Volume: {avg_volume_text}\n"
            f"Interpretation: {d.get('interpretation', 'N/A')}\n"
            f"Source URLs: {sources}\n"
        )
    except Exception as e:
        LOGGER.exception("tool get_stock_historical_data failed ticker=%s period=%s error=%s", symbol, period, e)
        return f"Error fetching historical data for {symbol}: {e}"


@tool
def get_market_trends(period: str = "1mo") -> str:
    """Get broad market trend summary for major indices over a selected period."""
    LOGGER.info("tool get_market_trends start period=%s", period)
    try:
        d = fetch_market_trends_data(period)
        lines = [f"Market Trends Analysis ({period}):", ""]
        for item in d.get("indices", []):
            lines.append(
                f"{item.get('name', 'Unknown')} ({item.get('symbol', 'N/A')}): "
                f"{_fmt_pct(item.get('change_percent'))} {item.get('note', '')}".strip()
            )
        lines.extend(["", f"Overview: {d.get('overview', 'N/A')}"])
        sources = ", ".join(s["url"] for s in d.get("sources", [])) or "N/A"
        lines.append(f"Source URLs: {sources}")
        return "\n".join(lines)
    except Exception as e:
        LOGGER.exception("tool get_market_trends failed period=%s error=%s", period, e)
        return f"Error fetching market trends: {e}"


@tool
def get_stock_fundamentals(ticker: str) -> str:
    """Get fundamental profile and recommendation context for a ticker."""
    symbol = ticker.upper()
    LOGGER.info("tool get_stock_fundamentals start ticker=%s", symbol)
    try:
        d = fetch_stock_fundamentals_data(symbol)
        sources = ", ".join(s["url"] for s in d.get("sources", [])) or "N/A"
        return (
            f"Fundamental Data for {symbol}:\n"
            f"Company: {d.get('company', 'N/A')}\n"
            f"Sector: {d.get('sector', 'N/A')}\n"
            f"Industry: {d.get('industry', 'N/A')}\n"
            f"Market Cap: {d.get('market_cap', 'N/A')}\n"
            f"P/E Ratio: {d.get('pe_ratio', 'N/A')}\n"
            f"Dividend Yield: {d.get('dividend_yield', 'N/A')}\n"
            f"52 Week High: {_fmt_currency(d.get('fifty_two_week_high'))}\n"
            f"52 Week Low: {_fmt_currency(d.get('fifty_two_week_low'))}\n"
            f"Recommendation: {d.get('recommendation', 'N/A')}\n"
            f"Rationale: {d.get('rationale', 'N/A')}\n"
            f"Source URLs: {sources}\n"
        )
    except Exception as e:
        LOGGER.exception("tool get_stock_fundamentals failed ticker=%s error=%s", symbol, e)
        return f"Error fetching fundamentals for {symbol}: {e}"


@tool
def get_ticker_sector_industry(tickers: str) -> str:
    """Get sector and industry classification for one or more ticker symbols."""
    symbols = _parse_ticker_list(tickers)
    LOGGER.info("tool get_ticker_sector_industry start tickers=%s", symbols)
    if not symbols:
        return "No valid ticker symbols provided."
    try:
        lines = ["Ticker Sector/Industry Mapping:", ""]
        all_sources: List[str] = []
        for symbol in symbols:
            d = fetch_ticker_profile_data(symbol)
            lines.append(
                f"- {symbol}: Sector={d.get('sector') or 'N/A'}, Industry={d.get('industry') or 'N/A'}, Company={d.get('company') or 'N/A'}"
            )
            all_sources.extend([s["url"] for s in d.get("sources", []) if s.get("url")])
        source_text = ", ".join(dict.fromkeys(all_sources)) if all_sources else "N/A"
        lines.extend(["", f"Source URLs: {source_text}"])
        return "\n".join(lines)
    except Exception as e:
        LOGGER.exception("tool get_ticker_sector_industry failed tickers=%s error=%s", symbols, e)
        return f"Error fetching ticker sector/industry mapping: {e}"


@tool
def get_sector_performance(sector: str = "technology", tickers: str = "") -> str:
    """Get sector-level performance overview and key market drivers."""
    symbols = _parse_ticker_list(tickers)
    LOGGER.info("tool get_sector_performance start sector=%s tickers=%s", sector, symbols)
    try:
        d = fetch_sector_performance_data(sector, symbols)
        sources = ", ".join(s["url"] for s in d.get("sources", [])) or "N/A"
        drivers = "; ".join(d.get("drivers", [])) if d.get("drivers") else "N/A"
        ctx = d.get("ticker_context") or {}
        ctx_line = "N/A"
        if ctx.get("tickers"):
            ctx_line = (
                f"Tickers={', '.join(ctx.get('tickers', []))}; "
                f"Dominant Sector={ctx.get('dominant_sector') or 'N/A'}; "
                f"Dominant Industry={ctx.get('dominant_industry') or 'N/A'}"
            )
        return (
            f"Sector Performance: {d.get('sector', sector)}\n"
            f"Proxy: {d.get('proxy', 'N/A')}\n"
            f"1 Month Change: {_fmt_pct(d.get('one_month_change_percent'))}\n"
            f"Overview: {d.get('overview', 'N/A')}\n"
            f"Ticker Context: {ctx_line}\n"
            f"Drivers: {drivers}\n"
            f"Source URLs: {sources}\n"
        )
    except Exception as e:
        LOGGER.exception("tool get_sector_performance failed sector=%s error=%s", sector, e)
        return f"Error analyzing {sector} sector: {e}"
