"""Price data fetching module using yfinance."""

import time
import random
import pandas as pd
import yfinance as yf

# Cache for GBP/USD exchange rate (fetched once per run)
_gbpusd_rate_cache = None


def get_gbpusd_rate() -> float:
    """
    Fetch the current GBP/USD exchange rate.

    Returns the most recent closing rate from the GBPUSD=X ticker.
    Represents how many USD = 1 GBP.

    Returns:
        float: Current GBP/USD exchange rate
    """
    global _gbpusd_rate_cache

    if _gbpusd_rate_cache is not None:
        return _gbpusd_rate_cache

    try:
        rate_ticker = yf.Ticker("GBPUSD=X")
        rate_data = rate_ticker.history(period="1d", auto_adjust=True)

        if rate_data.empty:
            raise ValueError("No exchange rate data available")

        rate = rate_data["Close"].iloc[-1]
        _gbpusd_rate_cache = rate
        return rate

    except Exception as e:
        raise ValueError(f"Failed to fetch GBP/USD exchange rate: {e}")


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
            # Convert USD to GBP using current exchange rate
            elif currency == "USD":
                gbpusd_rate = get_gbpusd_rate()
                data["Close"] = data["Close"] / gbpusd_rate
                print(f" → converting USD to GBP (rate={gbpusd_rate:.4f})")
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
