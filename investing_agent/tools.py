"""Tools for market research and stock analysis."""

import random
import time
from typing import Dict, List, Tuple

import requests
from langchain.tools import tool


_YAHOO_BASE_URLS = [
    "https://query1.finance.yahoo.com",
    "https://query2.finance.yahoo.com",
]
_REQUEST_TIMEOUT_SECONDS = 12
_MIN_REQUEST_INTERVAL_SECONDS = 3.0
_LAST_REQUEST_TS = 0.0
_CACHE: Dict[str, Tuple[float, Dict]] = {}
_CACHE_TTL_SECONDS = {
    "/v7/finance/quote": 60.0,
    "/v8/finance/chart": 180.0,
}
_LAST_COOKIE_WARM_TS = 0.0
_COOKIE_WARM_INTERVAL_SECONDS = 600.0
_GLOBAL_COOLDOWN_UNTIL = 0.0
_DEFAULT_429_COOLDOWN_SECONDS = 75.0

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://finance.yahoo.com/",
    }
)

_PERIOD_TO_RANGE = {
    "1d": "1d",
    "5d": "5d",
    "1mo": "1mo",
    "3mo": "3mo",
    "6mo": "6mo",
    "1y": "1y",
    "ytd": "ytd",
}

_PERIOD_TO_INTERVAL = {
    "1d": "5m",
    "5d": "15m",
    "1mo": "1d",
    "3mo": "1d",
    "6mo": "1d",
    "1y": "1d",
    "ytd": "1d",
}


def _is_rate_limited_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "too many requests" in msg or "rate limit" in msg


def _throttle_requests() -> None:
    global _LAST_REQUEST_TS
    now = time.time()
    wait_for = _MIN_REQUEST_INTERVAL_SECONDS - (now - _LAST_REQUEST_TS)
    if wait_for > 0:
        time.sleep(wait_for)
    _LAST_REQUEST_TS = time.time()


def _respect_global_cooldown() -> None:
    now = time.time()
    if _GLOBAL_COOLDOWN_UNTIL > now:
        time.sleep(_GLOBAL_COOLDOWN_UNTIL - now)


def _apply_global_cooldown(wait_seconds: float) -> None:
    global _GLOBAL_COOLDOWN_UNTIL
    _GLOBAL_COOLDOWN_UNTIL = max(_GLOBAL_COOLDOWN_UNTIL, time.time() + wait_seconds)


def _cache_key(path: str, params: Dict[str, str]) -> str:
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{path}?{query}"


def _get_cached(path: str, params: Dict[str, str], allow_stale: bool = False):
    key = _cache_key(path, params)
    cached = _CACHE.get(key)
    if not cached:
        return None

    ts, payload = cached
    max_age = _CACHE_TTL_SECONDS["/v7/finance/quote"] if path.startswith("/v7/finance/quote") else _CACHE_TTL_SECONDS["/v8/finance/chart"]
    if allow_stale or (time.time() - ts) <= max_age:
        return payload
    return None


def _set_cached(path: str, params: Dict[str, str], payload: Dict) -> None:
    _CACHE[_cache_key(path, params)] = (time.time(), payload)


def _warm_yahoo_cookies() -> None:
    global _LAST_COOKIE_WARM_TS
    now = time.time()
    if (now - _LAST_COOKIE_WARM_TS) < _COOKIE_WARM_INTERVAL_SECONDS:
        return

    try:
        _SESSION.get("https://finance.yahoo.com/", timeout=8)
    except Exception:
        # Cookie warmup is best-effort only.
        pass
    finally:
        _LAST_COOKIE_WARM_TS = time.time()


def _fetch_json(path: str, params: Dict[str, str], retries: int = 4) -> Dict:
    cached = _get_cached(path, params, allow_stale=False)
    if cached is not None:
        return cached

    _warm_yahoo_cookies()
    last_exc = None

    for attempt in range(retries):
        try:
            _respect_global_cooldown()
            _throttle_requests()
            base_url = _YAHOO_BASE_URLS[attempt % len(_YAHOO_BASE_URLS)]
            response = _SESSION.get(
                f"{base_url}{path}",
                params=params,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                wait_seconds = None
                if retry_after and retry_after.isdigit():
                    wait_seconds = float(retry_after)
                effective_wait = wait_seconds if wait_seconds is not None else _DEFAULT_429_COOLDOWN_SECONDS
                _apply_global_cooldown(effective_wait)
                if attempt < retries - 1:
                    time.sleep((2.0 * (2 ** attempt)) + random.uniform(0.5, 1.0))
                    continue
                raise RuntimeError(f"429 Too Many Requests for {response.url}")

            response.raise_for_status()
            payload = response.json()
            _set_cached(path, params, payload)
            return payload
        except Exception as exc:
            last_exc = exc
            is_retryable = _is_rate_limited_error(exc)

            if isinstance(exc, requests.RequestException) and getattr(exc, "response", None) is not None:
                status_code = exc.response.status_code
                is_retryable = is_retryable or status_code in (429, 500, 502, 503, 504)

            if attempt == retries - 1 or not is_retryable:
                break

            backoff = (2.0 * (2 ** attempt)) + random.uniform(0.5, 1.0)
            time.sleep(backoff)

    # If live fetch repeatedly fails, return stale cache instead of hard failing.
    stale = _get_cached(path, params, allow_stale=True)
    if stale is not None:
        return stale

    raise last_exc


def _quote_for_symbol(ticker: str) -> Dict:
    data = _fetch_json("/v7/finance/quote", {"symbols": ticker})
    results = data.get("quoteResponse", {}).get("result", [])
    print(results)
    if not results:
        raise ValueError(f"No quote data available for {ticker}")
    return results[0]


def _chart_for_symbol(ticker: str, period: str) -> Tuple[List[float], List[float], List[float], List[float]]:
    period_value = period if period in _PERIOD_TO_RANGE else "1mo"
    params = {
        "range": _PERIOD_TO_RANGE[period_value],
        "interval": _PERIOD_TO_INTERVAL[period_value],
        "includePrePost": "false",
        "events": "div,splits",
    }

    data = _fetch_json(f"/v8/finance/chart/{ticker}", params)
    result = data.get("chart", {}).get("result")
    if not result:
        error_obj = data.get("chart", {}).get("error")
        raise ValueError(f"No chart data for {ticker}: {error_obj}")

    quote = result[0].get("indicators", {}).get("quote", [{}])[0]
    closes = [float(v) for v in quote.get("close", []) if v is not None]
    highs = [float(v) for v in quote.get("high", []) if v is not None]
    lows = [float(v) for v in quote.get("low", []) if v is not None]
    volumes = [float(v) for v in quote.get("volume", []) if v is not None]

    if not closes:
        raise ValueError(f"No close prices available for {ticker}")

    return closes, highs, lows, volumes


def _format_currency(value, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.{decimals}f}"


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
        quote = _quote_for_symbol(symbol)
        current_price = quote.get("regularMarketPrice")
        previous_close = quote.get("regularMarketPreviousClose")
        company_name = quote.get("longName") or quote.get("shortName") or symbol
        exchange = quote.get("fullExchangeName") or quote.get("exchange") or "N/A"

        return f"""Stock: {symbol}
Current Price: {_format_currency(current_price)}
Previous Close: {_format_currency(previous_close)}
Company: {company_name}
Exchange: {exchange}
"""
    except Exception as e:
        if _is_rate_limited_error(e):
            return (
                f"Rate limited by Yahoo Finance while fetching {symbol}. "
                "Please retry in 30-60 seconds."
            )
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
        closes, highs, lows, volumes = _chart_for_symbol(symbol, period)

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
"""
    except Exception as e:
        if _is_rate_limited_error(e):
            return (
                f"Rate limited by Yahoo Finance while fetching {symbol} history. "
                "Please retry in 30-60 seconds."
            )
        return f"Error fetching historical data for {symbol}: {str(e)}"


@tool
def get_market_trends(period: str = "1mo") -> str:
    """Get market trends by analyzing major indices.

    Args:
        period: Time period to analyze ('1d', '5d', '1mo', '3mo', '6mo', '1y')

    Returns:
        Market trend analysis for major indices
    """
    indices = {
        "^GSPC": {"name": "S&P 500", "fallback": "SPY"},
        "^DJI": {"name": "Dow Jones", "fallback": "DIA"},
        "^IXIC": {"name": "NASDAQ", "fallback": "QQQ"},
        "^VIX": {"name": "Volatility Index", "fallback": "VIXY"},
    }

    results = [f"Market Trends Analysis ({period}):\n"]

    for ticker, meta in indices.items():
        name = meta["name"]
        fallback = meta["fallback"]

        try:
            closes, _, _, _ = _chart_for_symbol(ticker, period)
            change = ((closes[-1] - closes[0]) / closes[0]) * 100 if closes[0] else 0.0
            results.append(f"{name} ({ticker}): {change:+.2f}%")
            continue
        except Exception as primary_error:
            if _is_rate_limited_error(primary_error):
                results.append(f"{name}: Rate limited (retry shortly)")
                continue

        try:
            closes, _, _, _ = _chart_for_symbol(fallback, period)
            change = ((closes[-1] - closes[0]) / closes[0]) * 100 if closes[0] else 0.0
            results.append(f"{name} ({fallback} proxy): {change:+.2f}%")
        except Exception as fallback_error:
            if _is_rate_limited_error(fallback_error):
                results.append(f"{name}: Rate limited (retry shortly)")
            else:
                results.append(
                    f"{name}: Error - index {ticker} failed ({str(primary_error)}); "
                    f"fallback {fallback} failed ({str(fallback_error)})"
                )

    return "\n".join(results)


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
        quote = _quote_for_symbol(symbol)
        closes, highs, lows, volumes = _chart_for_symbol(symbol, "1y")

        one_year_change = ((closes[-1] - closes[0]) / closes[0]) * 100 if closes[0] else 0.0
        avg_volume_30d = (sum(volumes[-30:]) / len(volumes[-30:])) if volumes else 0.0

        market_cap = quote.get("marketCap")
        market_cap_text = f"${market_cap:,}" if isinstance(market_cap, (int, float)) else "N/A"

        dividend_yield = quote.get("trailingAnnualDividendYield")
        dividend_yield_text = f"{dividend_yield * 100:.2f}%" if isinstance(dividend_yield, (int, float)) else "N/A"

        analyst = quote.get("averageAnalystRating") or "N/A"

        return f"""Fundamental Data for {symbol} (Yahoo live):
Company: {quote.get('longName') or quote.get('shortName') or symbol}
Sector: N/A
Industry: N/A
Market Cap: {market_cap_text}
P/E Ratio: {quote.get('trailingPE', 'N/A')}
Forward P/E: N/A
PEG Ratio: N/A
Price to Book: {quote.get('priceToBook', 'N/A')}
Dividend Yield: {dividend_yield_text}
52 Week High: {_format_currency(quote.get('fiftyTwoWeekHigh') or (max(highs) if highs else None))}
52 Week Low: {_format_currency(quote.get('fiftyTwoWeekLow') or (min(lows) if lows else None))}
Analyst Rating: {analyst}
Target Price: N/A
Current Price: {_format_currency(quote.get('regularMarketPrice') or closes[-1])}
1Y Price Change: {one_year_change:+.2f}%
Avg Volume (30d): {avg_volume_30d:,.0f}
"""
    except Exception as e:
        if _is_rate_limited_error(e):
            return (
                f"Rate limited by Yahoo Finance while fetching fundamentals for {symbol}. "
                "Please retry in 30-60 seconds."
            )
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
        closes, _, _, _ = _chart_for_symbol(etf, "1mo")
        latest = closes[-1]
        month_start = closes[0]
        month_change = ((latest - month_start) / month_start) * 100 if month_start else 0.0

        return f"""Sector Performance: {sector.upper()}
ETF: {etf}
1 Month Change: {month_change:+.2f}%
Current Price: {_format_currency(latest)}
"""
    except Exception as e:
        if _is_rate_limited_error(e):
            return (
                f"Rate limited by Yahoo Finance while analyzing {sector} sector. "
                "Please retry in 30-60 seconds."
            )
        return f"Error analyzing {sector} sector: {str(e)}"
