"""Tools for market research and stock analysis."""

from typing import Optional
from datetime import datetime, timedelta
import yfinance as yf
from langchain.tools import tool


@tool
def get_stock_price(ticker: str) -> str:
    """Get the current stock price for a given ticker symbol.

    Args:
        ticker: Stock ticker symbol (e.g., 'AAPL', 'GOOGL')

    Returns:
        Current stock price information
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        current_price = info.get('currentPrice', info.get('regularMarketPrice', 'N/A'))
        previous_close = info.get('previousClose', 'N/A')

        return f"""Stock: {ticker}
Current Price: ${current_price}
Previous Close: ${previous_close}
Company: {info.get('longName', 'N/A')}
Sector: {info.get('sector', 'N/A')}
"""
    except Exception as e:
        return f"Error fetching stock price for {ticker}: {str(e)}"


@tool
def get_stock_historical_data(ticker: str, period: str = "1mo") -> str:
    """Get historical stock data for analysis.

    Args:
        ticker: Stock ticker symbol (e.g., 'AAPL', 'GOOGL')
        period: Time period ('1d', '5d', '1mo', '3mo', '6mo', '1y', 'ytd')

    Returns:
        Historical price data and statistics
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period)

        if hist.empty:
            return f"No historical data available for {ticker}"

        # Calculate statistics
        latest_price = hist['Close'].iloc[-1]
        period_start = hist['Close'].iloc[0]
        period_change = ((latest_price - period_start) / period_start) * 100
        avg_volume = hist['Volume'].mean()
        high = hist['High'].max()
        low = hist['Low'].min()

        return f"""Historical Data for {ticker} ({period}):
Latest Price: ${latest_price:.2f}
Period Start: ${period_start:.2f}
Period Change: {period_change:+.2f}%
Highest: ${high:.2f}
Lowest: ${low:.2f}
Average Volume: {avg_volume:,.0f}
"""
    except Exception as e:
        return f"Error fetching historical data for {ticker}: {str(e)}"


@tool
def get_market_trends(period: str = "1mo") -> str:
    """Get market trends by analyzing major indices.

    Args:
        period: Time period to analyze ('1d', '5d', '1mo', '3mo', '6mo', '1y')

    Returns:
        Market trend analysis for major indices
    """
    indices = {
        '^GSPC': 'S&P 500',
        '^DJI': 'Dow Jones',
        '^IXIC': 'NASDAQ',
        '^VIX': 'Volatility Index'
    }

    results = [f"Market Trends Analysis ({period}):\n"]

    for ticker, name in indices.items():
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period=period)

            if not hist.empty:
                latest = hist['Close'].iloc[-1]
                start = hist['Close'].iloc[0]
                change = ((latest - start) / start) * 100

                results.append(f"{name} ({ticker}): {change:+.2f}%")
        except Exception as e:
            results.append(f"{name}: Error - {str(e)}")

    return "\n".join(results)


@tool
def get_stock_fundamentals(ticker: str) -> str:
    """Get fundamental data for a stock including valuation metrics.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Fundamental analysis data
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        return f"""Fundamental Data for {ticker}:
Company: {info.get('longName', 'N/A')}
Sector: {info.get('sector', 'N/A')}
Industry: {info.get('industry', 'N/A')}
Market Cap: ${info.get('marketCap', 0):,}
P/E Ratio: {info.get('trailingPE', 'N/A')}
Forward P/E: {info.get('forwardPE', 'N/A')}
PEG Ratio: {info.get('pegRatio', 'N/A')}
Price to Book: {info.get('priceToBook', 'N/A')}
Dividend Yield: {info.get('dividendYield', 0) * 100:.2f}%
52 Week High: ${info.get('fiftyTwoWeekHigh', 'N/A')}
52 Week Low: ${info.get('fiftyTwoWeekLow', 'N/A')}
Analyst Rating: {info.get('recommendationKey', 'N/A')}
Target Price: ${info.get('targetMeanPrice', 'N/A')}
"""
    except Exception as e:
        return f"Error fetching fundamentals for {ticker}: {str(e)}"


@tool
def get_sector_performance(sector: str = "technology") -> str:
    """Get performance data for a specific sector.

    Args:
        sector: Sector to analyze (technology, healthcare, finance, energy, consumer, etc.)

    Returns:
        Sector performance analysis
    """
    # Map sector keywords to representative ETFs
    sector_etfs = {
        'technology': 'XLK',
        'healthcare': 'XLV',
        'finance': 'XLF',
        'energy': 'XLE',
        'consumer': 'XLY',
        'utilities': 'XLU',
        'materials': 'XLB',
        'industrials': 'XLI',
        'real estate': 'XLRE',
        'communications': 'XLC'
    }

    etf = sector_etfs.get(sector.lower(), 'XLK')

    try:
        stock = yf.Ticker(etf)
        hist = stock.history(period="1mo")

        if hist.empty:
            return f"No data available for {sector} sector"

        latest = hist['Close'].iloc[-1]
        month_start = hist['Close'].iloc[0]
        month_change = ((latest - month_start) / month_start) * 100

        return f"""Sector Performance: {sector.upper()}
ETF: {etf}
1 Month Change: {month_change:+.2f}%
Current Price: ${latest:.2f}
"""
    except Exception as e:
        return f"Error analyzing {sector} sector: {str(e)}"
