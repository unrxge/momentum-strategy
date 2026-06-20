#!/usr/bin/env python3
"""Test single ticker fetch to verify yfinance upgrade works."""

from src.data.price_fetcher import fetch_price_history
from src.data.indicators import moving_average, trailing_return, rolling_volatility, rolling_return_window

ticker = "CSPX.L"

print(f"Testing {ticker}...\n")

try:
    prices_df = fetch_price_history(ticker, period_days=400, apply_delay=False)
    prices = prices_df["Close"]

    print(f"✓ Successfully fetched {len(prices)} trading days for {ticker}")
    print(f"Date range: {prices_df['Date'].min()} to {prices_df['Date'].max()}\n")

    current_price = prices.iloc[-1]
    ma_200 = moving_average(prices, window=200)
    return_12m = trailing_return(prices, days=252)
    return_3m = trailing_return(prices, days=63)
    volatility = rolling_volatility(prices, window=20)
    return_10d = rolling_return_window(prices, window=10)

    print(f"Current Price:       £{current_price:.2f}")
    print(f"200-Day MA:          £{ma_200:.2f}")
    print(f"12-Month Return:     {return_12m * 100:+.2f}%")
    print(f"3-Month Return:      {return_3m * 100:+.2f}%")
    print(f"20-Day Volatility:   {volatility * 100:.2f}%")
    print(f"10-Day Return:       {return_10d * 100:+.2f}%")

    print(f"\n✓ {ticker} test PASSED")

except Exception as e:
    print(f"✗ {ticker} test FAILED: {e}")
