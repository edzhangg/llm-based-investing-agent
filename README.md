# LLM-Based Investing Agent

An intelligent investing agent powered by LangChain and OpenAI that researches market trends and provides stock recommendations.

## Features

- **Market Analysis**: Analyze trends across major indices (S&P 500, Dow Jones, NASDAQ, VIX)
- **Stock Research**: Deep dive into individual stocks with fundamental and technical analysis
- **Investment Recommendations**: Get BUY/SELL/HOLD recommendations based on market data
- **Flexible Time Periods**: Analyze daily, weekly, monthly, or yearly trends
- **Sector Analysis**: Track performance across different market sectors

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd <repository-name>
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up your OpenAI API key:
```bash
cp .env.example .env
# Edit .env and add your OpenAI API key
```

## Usage

### Market Analysis

Analyze overall market trends:
```bash
python main.py --mode market --period 1mo
```

### Stock Research

Research specific stocks:
```bash
python main.py --mode research --tickers AAPL GOOGL MSFT --period 1mo
```

### Get Recommendations

Get investment recommendations:
```bash
# General recommendations
python main.py --mode recommend --period 1mo

# Recommendations for specific stocks
python main.py --mode recommend --tickers AAPL TSLA NVDA --period 1mo

# Focus on specific investment style
python main.py --mode recommend --focus growth --period 3mo
```

### Custom Queries

Ask custom investment questions:
```bash
python main.py --mode custom --query "What are the best dividend stocks in the technology sector?"
```

## Command Line Options

- `--mode`: Operation mode (market, research, recommend, custom)
- `--tickers`: Stock ticker symbols to analyze
- `--period`: Time period (1d, 5d, 1mo, 3mo, 6mo, 1y)
- `--focus`: Investment focus (growth, value, dividends, etc.)
- `--query`: Custom query for the agent
- `--model`: OpenAI model to use (default: gpt-4-turbo-preview)

## How It Works

The agent uses LangChain to orchestrate LLM calls with specialized tools:

1. **Market Data Tools**: Fetch real-time and historical stock data using yfinance
2. **LLM Analysis**: GPT-4 analyzes the data and market trends
3. **Recommendations**: The agent provides actionable investment advice based on analysis

## Tools Available to the Agent

- `get_stock_price`: Get current stock prices
- `get_stock_historical_data`: Analyze historical price movements
- `get_market_trends`: Track major market indices
- `get_stock_fundamentals`: Access P/E ratios, valuation metrics, analyst ratings
- `get_sector_performance`: Monitor sector-specific trends

## Example Output

```
📊 Analyzing market trends (period: 1mo)...

Market Trends Analysis (1mo):
S&P 500 (^GSPC): +3.45%
Dow Jones (^DJI): +2.87%
NASDAQ (^IXIC): +4.23%
Volatility Index (^VIX): -12.34%

The market shows positive momentum across all major indices...
[Additional AI-generated analysis]
```

## Requirements

- Python 3.8+
- OpenAI API key
- Internet connection for market data

## Disclaimer

This tool is for educational and research purposes only. Always do your own research and consult with a qualified financial advisor before making investment decisions. Past performance does not guarantee future results.

## License

MIT
