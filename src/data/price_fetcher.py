"""Price data fetching module using yfinance."""

import time
import random
import pandas as pd
import yfinance as yf


def fetch_price_history(ticker: str, period_days: int = 400, apply_delay: bool = True) -> pd.DataFrame:
    """
    Fetch daily historical price data for a ticker with retry logic and rate-limit handling.

    Converts GBX (pence) to GBP (pounds) for LSE-quoted securities.

    Args:
        ticker: Stock ticker symbol (e.g., "CSPX.L")
        period_days: Number of days of history to fetch (default 400)
        apply_delay: Add random delay to avoid rate limiting (default True)

    Returns:
        DataFrame with columns [Date, Close], sorted by date ascending, prices in GBP

    Raises:
        ValueError: If ticker not found or no data available after 3 retry attempts
    """
    if apply_delay:
        delay = random.uniform(0.5, 1.5)
        time.sleep(delay)

    max_retries = 3
    retry_delay = 2

    for attempt in range(max_retries):
        try:
            ticker_obj = yf.Ticker(ticker)
            data = ticker_obj.history(period=f"{period_days}d", auto_adjust=True)

            if data.empty:
                raise ValueError(f"No price data found for ticker: {ticker}")

            # Detect currency and normalize to GBP
            currency = ticker_obj.info.get("currency", "unknown")
            print(f"  {ticker}: currency={currency}", end="")

            # Convert GBX (pence) to GBP (pounds) if needed
            if currency in ["GBX", "GBp"]:
                data["Close"] = data["Close"] / 100
                print(" → converting GBX to GBP")
            else:
                print()

            # Extract Close column and reset index to make Date a column
            df = data[["Close"]].reset_index()
            df.columns = ["Date", "Close"]

            # Sort by date ascending
            df = df.sort_values("Date").reset_index(drop=True)

            return df

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                raise ValueError(f"Failed to fetch data for {ticker} after {max_retries} attempts: {e}")
