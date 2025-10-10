#!/usr/bin/env python3
"""Main script to run the LLM-based investing agent."""

import os
import argparse
from dotenv import load_dotenv
from investing_agent import InvestingAgent


def main():
    """Run the investing agent."""
    # Load environment variables
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="LLM-based Investing Agent for market research and stock recommendations"
    )
    parser.add_argument(
        "--mode",
        choices=["market", "research", "recommend", "custom"],
        default="market",
        help="Operation mode: market analysis, stock research, recommendations, or custom query"
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        help="Stock ticker symbols to analyze (e.g., AAPL GOOGL MSFT)"
    )
    parser.add_argument(
        "--period",
        default="1mo",
        choices=["1d", "5d", "1mo", "3mo", "6mo", "1y"],
        help="Time period for analysis"
    )
    parser.add_argument(
        "--focus",
        help="Investment focus (e.g., growth, value, dividends)"
    )
    parser.add_argument(
        "--query",
        help="Custom query for the agent"
    )
    parser.add_argument(
        "--model",
        default="gpt-4-turbo-preview",
        help="OpenAI model to use"
    )

    args = parser.parse_args()

    # Initialize agent
    print("🤖 Initializing LLM Investing Agent...")
    try:
        agent = InvestingAgent(model=args.model)
    except ValueError as e:
        print(f"❌ Error: {e}")
        print("Please set OPENAI_API_KEY environment variable or create a .env file")
        return

    print(f"✅ Agent initialized with model: {args.model}\n")

    # Execute based on mode
    try:
        if args.mode == "market":
            print(f"📊 Analyzing market trends (period: {args.period})...\n")
            result = agent.analyze_market(period=args.period)

        elif args.mode == "research":
            if not args.tickers:
                print("❌ Error: --tickers required for research mode")
                return
            print(f"🔍 Researching stocks: {', '.join(args.tickers)}...\n")
            results = []
            for ticker in args.tickers:
                print(f"\n{'='*60}")
                print(f"Analyzing {ticker}")
                print('='*60)
                result = agent.research_stock(ticker, timeframe=args.period)
                results.append(result)
            result = "\n\n".join(results)

        elif args.mode == "recommend":
            print(f"💡 Getting investment recommendations (period: {args.period})...\n")
            result = agent.get_recommendations(
                tickers=args.tickers,
                period=args.period,
                focus=args.focus
            )

        elif args.mode == "custom":
            if not args.query:
                print("❌ Error: --query required for custom mode")
                return
            print(f"🔎 Processing custom query...\n")
            result = agent.custom_query(args.query)

        # Display results
        print("\n" + "="*80)
        print("RESULTS")
        print("="*80 + "\n")
        print(result)

    except Exception as e:
        print(f"❌ Error during execution: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
