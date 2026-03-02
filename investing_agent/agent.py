"""LangChain-based investing agent."""

import os
from typing import List, Dict, Any
from datetime import datetime
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor
from langchain.agents import create_openai_tools_agent
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder

from .tools import (
    get_stock_price,
    get_stock_historical_data,
    get_market_trends,
    get_stock_fundamentals,
    get_sector_performance
)


class InvestingAgent:
    """LLM-based investing agent for market research and stock recommendations."""

    def __init__(self, api_key: str = None, model: str = "gpt-5-nano"):
        """Initialize the investing agent.

        Args:
            api_key: OpenAI API key (defaults to OPENAI_API_KEY env var)
            model: OpenAI model to use
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key not provided")

        self.llm = ChatOpenAI(
            model=model,
            temperature=1,
            api_key=self.api_key
        )

        # Define system prompt
        self.system_prompt = """You are an expert investment analyst and financial advisor.
Your role is to analyze market trends, research stocks, and provide investment recommendations.

When analyzing stocks or markets:
1. Always use the available tools to gather current market data
2. Consider multiple time periods (daily, weekly, monthly trends)
3. Look at both technical indicators and fundamental data
4. Analyze sector performance and broader market trends
5. Provide clear reasoning for your recommendations

When making recommendations:
- Be specific about BUY, SELL, or HOLD recommendations
- Explain your reasoning with data-backed analysis
- Consider risk factors and market conditions
- Suggest appropriate position sizes or risk management strategies
- Note any important caveats or uncertainties

Today's date is {date}.
"""

        # Set up tools
        self.tools = [
            get_stock_price,
            get_stock_historical_data,
            get_market_trends,
            get_stock_fundamentals,
            get_sector_performance
        ]

        # Create prompt template
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", self.system_prompt.format(date=datetime.now().strftime("%Y-%m-%d"))),
            ("human", "{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ])

        self.agent = create_openai_tools_agent(
            llm=self.llm,
            tools=self.tools,
            prompt=self.prompt
        )

        # Create agent executor
        self.agent_executor = AgentExecutor(
            agent=self.agent,
            tools=self.tools,
            verbose=True,
            max_iterations=10,
            handle_parsing_errors=True
        )

    def analyze_market(self, period: str = "1mo") -> str:
        """Analyze current market trends.

        Args:
            period: Time period to analyze ('1d', '5d', '1mo', '3mo', '6mo', '1y')

        Returns:
            Market analysis and insights
        """
        query = f"""Analyze the current market trends over the past {period}.

Consider:
- Major indices performance (S&P 500, Dow Jones, NASDAQ)
- Market volatility (VIX)
- Overall market sentiment
- Key sectors driving or lagging the market

Provide a comprehensive market analysis with insights about the current market environment."""

        result = self.agent_executor.invoke({"input": query})
        return result["output"]

    def research_stock(self, ticker: str, timeframe: str = "1mo") -> str:
        """Research a specific stock.

        Args:
            ticker: Stock ticker symbol
            timeframe: Time period for analysis

        Returns:
            Stock analysis and research
        """
        query = f"""Research the stock {ticker} thoroughly.

Analyze:
- Current price and recent performance over {timeframe}
- Historical trends and price movements
- Fundamental metrics (P/E, valuation, financials)
- Sector performance and position
- Key strengths and weaknesses

Provide a comprehensive stock analysis."""

        result = self.agent_executor.invoke({"input": query})
        return result["output"]

    def get_recommendations(
        self,
        tickers: List[str] = None,
        period: str = "1mo",
        focus: str = None
    ) -> str:
        """Get stock recommendations.

        Args:
            tickers: List of stock tickers to analyze (optional)
            period: Time period for trend analysis
            focus: Specific focus area (e.g., 'growth', 'value', 'dividends')

        Returns:
            Stock recommendations with buy/sell/hold advice
        """
        if tickers:
            ticker_list = ", ".join(tickers)
            query = f"""Analyze these stocks: {ticker_list}

Based on the {period} trends and current market conditions, provide specific BUY, SELL, or HOLD recommendations for each stock.
"""
        else:
            query = f"""Based on current market trends over the past {period}, recommend stocks to buy or sell.
"""

        if focus:
            query += f"\nFocus on {focus} stocks."

        query += """

For each recommendation:
1. Specify the action (BUY, SELL, or HOLD)
2. Provide clear reasoning with supporting data
3. Note risk factors
4. Suggest position sizing or entry/exit strategies

Provide actionable investment recommendations."""

        result = self.agent_executor.invoke({"input": query})
        return result["output"]

    def custom_query(self, query: str) -> str:
        """Execute a custom investment research query.

        Args:
            query: Custom query string

        Returns:
            Agent response
        """
        result = self.agent_executor.invoke({"input": query})
        return result["output"]
