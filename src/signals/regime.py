"""Market regime detection module."""

import pandas as pd
from src.data.indicators import moving_average, rolling_return_window


def check_regime(prices: pd.Series) -> str:
    """
    Determine market regime based on price relative to 200-day moving average.

    Args:
        prices: Series of closing prices (most recent last)

    Returns:
        "healthy" if current price > 200-day MA, else "unhealthy"
    """
    current_price = prices.iloc[-1]
    ma_200 = moving_average(prices, window=200)

    if current_price > ma_200:
        return "healthy"
    else:
        return "unhealthy"


def check_fast_crash(prices: pd.Series) -> bool:
    """
    Detect rapid market downturns using 10-day rolling return.

    Args:
        prices: Series of closing prices (most recent last)

    Returns:
        True if 10-day rolling return is worse than -7%, else False
    """
    rolling_return = rolling_return_window(prices, window=10)

    return rolling_return < -0.07
