"""Momentum-based asset ranking module."""

import pandas as pd
from src.data.indicators import trailing_return


def rank_growth_assets(price_data: dict[str, pd.Series]) -> list[tuple[str, float]]:
    """
    Rank growth assets by 12-month trailing return.

    Args:
        price_data: Dict of {ticker: price_series} for growth assets

    Returns:
        List of (ticker, momentum_score) sorted by momentum descending
    """
    rankings = []

    for ticker, prices in price_data.items():
        momentum = trailing_return(prices, days=252)  # 252 trading days ~ 1 year
        rankings.append((ticker, momentum))

    # Sort by momentum score descending
    rankings.sort(key=lambda x: x[1], reverse=True)

    return rankings


def rank_defensive_assets(price_data: dict[str, pd.Series]) -> list[tuple[str, float]]:
    """
    Rank defensive assets by 3-month trailing return.

    Args:
        price_data: Dict of {ticker: price_series} for defensive assets

    Returns:
        List of (ticker, momentum_score) sorted by momentum descending
    """
    rankings = []

    for ticker, prices in price_data.items():
        momentum = trailing_return(prices, days=63)  # 63 trading days ~ 3 months
        rankings.append((ticker, momentum))

    # Sort by momentum score descending
    rankings.sort(key=lambda x: x[1], reverse=True)

    return rankings


def select_top_growth(ranked_list: list[tuple[str, float]], n: int = 2) -> list[str]:
    """
    Select the top N growth assets from a ranked list.

    Args:
        ranked_list: List of (ticker, score) tuples sorted by score
        n: Number of top assets to select (default 2)

    Returns:
        List of top N ticker names
    """
    return [ticker for ticker, _ in ranked_list[:n]]
