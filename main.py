#!/usr/bin/env python3
"""Entry point for the momentum strategy system."""

import pandas as pd
from src.data.price_fetcher import fetch_price_history
from src.data.indicators import (
    moving_average,
    trailing_return,
    rolling_volatility,
    rolling_return_window,
)

# LSE ETF equivalents using US tickers (yfinance limitation with .L symbols)
GROWTH_TICKERS = ["QQQ", "IVV", "VTI", "VXUS"]  # Nasdaq, S&P 500, Total US, Intl stocks
DEFENSIVE_TICKERS = ["SLV", "GLD"]  # Silver, Gold


def analyze_ticker(ticker: str) -> dict | None:
    """
    Fetch and analyze a single ticker.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with analysis results, or None if fetch fails
    """
    try:
        prices_df = fetch_price_history(ticker, period_days=400)
        prices = prices_df["Close"]

        current_price = prices.iloc[-1]
        ma_200 = moving_average(prices, window=200)
        return_12m = trailing_return(prices, days=252)  # ~252 trading days/year
        return_3m = trailing_return(prices, days=63)  # ~63 trading days/quarter
        volatility = rolling_volatility(prices, window=20)
        return_10d = rolling_return_window(prices, window=10)

        return {
            "Ticker": ticker,
            "Price": f"£{current_price:.2f}",
            "200-Day MA": f"£{ma_200:.2f}",
            "12-Month Return": f"{return_12m * 100:.2f}%",
            "3-Month Return": f"{return_3m * 100:.2f}%",
            "20-Day Volatility": f"{volatility * 100:.2f}%",
            "10-Day Return": f"{return_10d * 100:.2f}%",
        }

    except ValueError as e:
        print(f"⚠️  Warning: {ticker} — {e}")
        return None


def main():
    """Fetch and display summary analysis for all tickers."""
    print("Momentum strategy system initialized\n")
    print("=" * 120)
    print("GROWTH TICKERS")
    print("=" * 120)

    results = []
    for ticker in GROWTH_TICKERS:
        result = analyze_ticker(ticker)
        if result:
            results.append(result)

    if results:
        df = pd.DataFrame(results)
        print(df.to_string(index=False))
    else:
        print("No growth tickers loaded successfully")

    print("\n" + "=" * 120)
    print("DEFENSIVE TICKERS")
    print("=" * 120)

    results = []
    for ticker in DEFENSIVE_TICKERS:
        result = analyze_ticker(ticker)
        if result:
            results.append(result)

    if results:
        df = pd.DataFrame(results)
        print(df.to_string(index=False))
    else:
        print("No defensive tickers loaded successfully")

    print("\n" + "=" * 120)


if __name__ == "__main__":
    main()
