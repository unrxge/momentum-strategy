"""Technical indicators module for price analysis."""

import pandas as pd
import numpy as np


def moving_average(prices: pd.Series, window: int) -> float:
    """
    Calculate the most recent N-day moving average.

    Args:
        prices: Series of closing prices
        window: Number of days for moving average

    Returns:
        The most recent moving average value
    """
    if len(prices) < window:
        raise ValueError(f"Insufficient data: need {window} prices, got {len(prices)}")

    return prices.tail(window).mean()


def trailing_return(prices: pd.Series, days: int) -> float:
    """
    Calculate percentage return over the trailing N trading days.

    Args:
        prices: Series of closing prices
        days: Number of trading days to look back

    Returns:
        Percentage return as a decimal (e.g., 0.05 for 5%)
    """
    if len(prices) < days:
        raise ValueError(f"Insufficient data: need {days} prices, got {len(prices)}")

    current_price = prices.iloc[-1]
    past_price = prices.iloc[-days]

    return (current_price / past_price) - 1


def rolling_volatility(prices: pd.Series, window: int = 20) -> float:
    """
    Calculate annualized volatility over the most recent window.

    Uses standard deviation of daily returns annualized by sqrt(252).

    Args:
        prices: Series of closing prices
        window: Number of days for rolling calculation (default 20)

    Returns:
        Annualized volatility as a decimal (e.g., 0.15 for 15%)
    """
    if len(prices) < window + 1:
        raise ValueError(f"Insufficient data: need {window + 1} prices, got {len(prices)}")

    recent_prices = prices.tail(window + 1)
    daily_returns = recent_prices.pct_change().dropna()

    return daily_returns.std() * np.sqrt(252)


def rolling_return_window(prices: pd.Series, window: int = 10) -> float:
    """
    Calculate percentage return over the most recent N trading days.

    Args:
        prices: Series of closing prices
        window: Number of trading days to look back (default 10)

    Returns:
        Percentage return as a decimal (e.g., 0.03 for 3%)
    """
    if len(prices) < window:
        raise ValueError(f"Insufficient data: need {window} prices, got {len(prices)}")

    current_price = prices.iloc[-1]
    past_price = prices.iloc[-window]

    return (current_price / past_price) - 1
