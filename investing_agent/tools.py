"""Scraping-based tools for market research and stock analysis."""

import csv
import io
import random
import re
import time
from datetime import datetime
from typing import List, Tuple
from urllib.parse import unquote

import requests
from langchain.tools import tool


_REQUEST_TIMEOUT_SECONDS = 15
_MIN_REQUEST_INTERVAL_SECONDS = 1.2
_LAST_REQUEST_TS = 0.0

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
)

_PERIOD_TO_POINTS = {
    "1d": 2,
    "5d": 5,
    "1mo": 22,
    "3mo": 66,
    "6mo": 132,
    "1y": 252,
    "ytd": 252,
}


def _format_currency(value, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.{decimals}f}"


def _safe_float(value):
    try:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text in {"N/D", "-"}:
            return None
        return float(text.replace(",", ""))
    except Exception:
        return None


def _throttle_requests() -> None:
    global _LAST_REQUEST_TS
    now = time.time()
    wait_for = _MIN_REQUEST_INTERVAL_SECONDS - (now - _LAST_REQUEST_TS)
    if wait_for > 0:
        time.sleep(wait_for)
    _LAST_REQUEST_TS = time.time()


def _http_get(url: str, params=None, retries: int = 3) -> requests.Response:
    last_exc = None
    for attempt in range(retries):
        try:
            _throttle_requests()
            resp = _SESSION.get(url, params=params, timeout=_REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            last_exc = exc
            if attempt == retries - 1:
                break
            time.sleep((0.8 * (2 ** attempt)) + random.uniform(0.2, 0.6))
    raise last_exc


def _stooq_symbol(symbol: str) -> str:
    s = symbol.strip().upper()

    # Common index aliases to tradable proxies for consistent scraping.
    aliases = {
        "^GSPC": "SPY.US",
        "^DJI": "DIA.US",
        "^IXIC": "QQQ.US",
        "^VIX": "VIXY.US",
    }
    if s in aliases:
        return aliases[s].lower()

    if "." in s:
        return s.lower()
    return f"{s.lower()}.us"


def _stooq_latest_quote(symbol: str):
    stooq = _stooq_symbol(symbol)
    resp = _http_get("https://stooq.com/q/l/", params={"s": stooq, "i": "d"})
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    if not rows:
        raise ValueError(f"No quote row for {symbol}")
    row = rows[0]
    close = _safe_float(row.get("Close"))
    open_ = _safe_float(row.get("Open"))
    high = _safe_float(row.get("High"))
    low = _safe_float(row.get("Low"))
    volume = _safe_float(row.get("Volume"))
    date = row.get("Date", "N/A")
    return close, open_, high, low, volume, date


def _stooq_history(symbol: str) -> List[dict]:
    stooq = _stooq_symbol(symbol)
    resp = _http_get("https://stooq.com/q/d/l/", params={"s": stooq, "i": "d"})
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    cleaned = []
    for row in rows:
        close = _safe_float(row.get("Close"))
        high = _safe_float(row.get("High"))
        low = _safe_float(row.get("Low"))
        volume = _safe_float(row.get("Volume"))
        if close is None:
            continue
        cleaned.append(
            {
                "date": row.get("Date"),
                "close": close,
                "high": high,
                "low": low,
                "volume": volume,
            }
        )
    if not cleaned:
        raise ValueError(f"No historical rows for {symbol}")
    return cleaned


def _recent_ohlcv(symbol: str, period: str) -> Tuple[List[float], List[float], List[float], List[float]]:
    rows = _stooq_history(symbol)

    if period == "ytd":
        current_year = datetime.utcnow().year
        rows = [r for r in rows if str(r.get("date", "")).startswith(str(current_year))]

    points = _PERIOD_TO_POINTS.get(period, 22)
    selected = rows[-points:] if len(rows) >= points else rows

    closes = [r["close"] for r in selected if r.get("close") is not None]
    highs = [r["high"] for r in selected if r.get("high") is not None]
    lows = [r["low"] for r in selected if r.get("low") is not None]
    volumes = [r["volume"] for r in selected if r.get("volume") is not None]

    if not closes:
        raise ValueError(f"No close prices for {symbol}")

    return closes, highs, lows, volumes


def _google_top_links(query: str, limit: int = 3) -> List[str]:
    resp = _http_get("https://www.google.com/search", params={"q": query, "hl": "en"})
    html = resp.text

    links: List[str] = []
    for match in re.finditer(r'href="/url\\?q=(https?://[^&\"]+)', html):
        candidate = unquote(match.group(1))
        if "google.com" in candidate or "webcache.googleusercontent.com" in candidate:
            continue
        if candidate not in links:
            links.append(candidate)
        if len(links) >= limit:
            break
    return links


def _strip_html(text: str) -> str:
    text = re.sub(r"<script[\\s\\S]*?</script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<style[\\s\\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\\s+", " ", text)
    return text.strip()


def _google_scrape_summary(query: str, max_links: int = 3) -> str:
    links = _google_top_links(query, limit=max_links)
    if not links:
        return "No links found."

    snippets = []
    for link in links:
        try:
            page = _http_get(link)
            clean = _strip_html(page.text)
            snippets.append(f"- {link}\\n  {clean[:260]}...")
        except Exception:
            snippets.append(f"- {link}\\n  Unable to scrape content.")

    return "\\n".join(snippets)


@tool
def get_stock_price(ticker: str) -> str:
    """Get the current stock price for a given ticker symbol.

    Args:
        ticker: Stock ticker symbol (e.g., 'AAPL', 'GOOGL')

    Returns:
        Current stock price information
    """
    symbol = ticker.upper()
    try:
        close, open_, high, low, volume, date = _stooq_latest_quote(symbol)
        volume_text = f"{volume:,.0f}" if volume is not None else "N/A"

        return f"""Stock: {symbol}
Current Price: {_format_currency(close, 4)}
Open: {_format_currency(open_, 4)}
Day High: {_format_currency(high, 4)}
Day Low: {_format_currency(low, 4)}
Volume: {volume_text}
Latest Trading Day: {date}
Source: Web scraping (Stooq)
"""
    except Exception as e:
        return f"Error fetching stock price for {symbol}: {str(e)}"


@tool
def get_stock_historical_data(ticker: str, period: str = "1mo") -> str:
    """Get historical stock data for analysis.

    Args:
        ticker: Stock ticker symbol (e.g., 'AAPL', 'GOOGL')
        period: Time period ('1d', '5d', '1mo', '3mo', '6mo', '1y', 'ytd')

    Returns:
        Historical price data and statistics
    """
    symbol = ticker.upper()
    try:
        closes, highs, lows, volumes = _recent_ohlcv(symbol, period)

        latest_price = closes[-1]
        period_start = closes[0]
        period_change = ((latest_price - period_start) / period_start) * 100 if period_start else 0.0
        avg_volume = (sum(volumes) / len(volumes)) if volumes else 0.0
        high = max(highs) if highs else latest_price
        low = min(lows) if lows else latest_price

        return f"""Historical Data for {symbol} ({period}):
Latest Price: {_format_currency(latest_price)}
Period Start: {_format_currency(period_start)}
Period Change: {period_change:+.2f}%
Highest: {_format_currency(high)}
Lowest: {_format_currency(low)}
Average Volume: {avg_volume:,.0f}
Source: Web scraping (Stooq)
"""
    except Exception as e:
        return f"Error fetching historical data for {symbol}: {str(e)}"


@tool
def get_market_trends(period: str = "1mo") -> str:
    """Get market trends by analyzing major indices.

    Args:
        period: Time period to analyze ('1d', '5d', '1mo', '3mo', '6mo', '1y')

    Returns:
        Market trend analysis for major indices
    """
    proxies = {
        "SPY": "S&P 500",
        "DIA": "Dow Jones",
        "QQQ": "NASDAQ",
        "VIXY": "Volatility Index",
    }

    results = [f"Market Trends Analysis ({period}):\\n"]

    for symbol, name in proxies.items():
        try:
            closes, _, _, _ = _recent_ohlcv(symbol, period)
            change = ((closes[-1] - closes[0]) / closes[0]) * 100 if closes[0] else 0.0
            results.append(f"{name} ({symbol} proxy): {change:+.2f}%")
        except Exception as e:
            results.append(f"{name}: Error - {str(e)}")

    results.append("\\nSource: Web scraping (Stooq proxies)")
    return "\\n".join(results)


@tool
def get_stock_fundamentals(ticker: str) -> str:
    """Get fundamental data for a stock including valuation metrics.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Fundamental analysis data
    """
    symbol = ticker.upper()
    try:
        closes, highs, lows, volumes = _recent_ohlcv(symbol, "1y")
        latest_price = closes[-1]
        one_year_change = ((latest_price - closes[0]) / closes[0]) * 100 if closes[0] else 0.0
        avg_volume_30d = (sum(volumes[-30:]) / len(volumes[-30:])) if volumes else 0.0

        web_summary = _google_scrape_summary(f"{symbol} stock market cap pe ratio dividend yield")

        return f"""Fundamental Snapshot for {symbol}:
Current Price: {_format_currency(latest_price)}
52 Week High: {_format_currency(max(highs) if highs else None)}
52 Week Low: {_format_currency(min(lows) if lows else None)}
1Y Price Change: {one_year_change:+.2f}%
Avg Volume (30d): {avg_volume_30d:,.0f}

Web Research Snippets:
{web_summary}

Source: Web scraping (Stooq + Google top links)
"""
    except Exception as e:
        return f"Error fetching fundamentals for {symbol}: {str(e)}"


@tool
def get_sector_performance(sector: str = "technology") -> str:
    """Get performance data for a specific sector.

    Args:
        sector: Sector to analyze (technology, healthcare, finance, energy, consumer, etc.)

    Returns:
        Sector performance analysis
    """
    sector_etfs = {
        "technology": "XLK",
        "healthcare": "XLV",
        "finance": "XLF",
        "energy": "XLE",
        "consumer": "XLY",
        "utilities": "XLU",
        "materials": "XLB",
        "industrials": "XLI",
        "real estate": "XLRE",
        "communications": "XLC",
    }

    etf = sector_etfs.get(sector.lower(), "XLK")

    try:
        closes, _, _, _ = _recent_ohlcv(etf, "1mo")
        latest = closes[-1]
        month_start = closes[0]
        month_change = ((latest - month_start) / month_start) * 100 if month_start else 0.0
        web_summary = _google_scrape_summary(f"{sector} sector performance today")

        return f"""Sector Performance: {sector.upper()}
Proxy ETF: {etf}
1 Month Change: {month_change:+.2f}%
Current Price: {_format_currency(latest)}

Web Research Snippets:
{web_summary}

Source: Web scraping (Stooq + Google top links)
"""
    except Exception as e:
        return f"Error analyzing {sector} sector: {str(e)}"
