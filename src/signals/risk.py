"""Risk assessment module."""


def check_drawdown(portfolio_value_history: list[float]) -> float:
    """
    Calculate current portfolio drawdown from peak.

    Args:
        portfolio_value_history: List of historical portfolio values
                                (most recent value last)

    Returns:
        Drawdown as a decimal (e.g., 0.12 for 12% down from peak)
    """
    if not portfolio_value_history or len(portfolio_value_history) < 2:
        return 0.0

    peak_value = max(portfolio_value_history)
    current_value = portfolio_value_history[-1]

    drawdown = (peak_value - current_value) / peak_value

    return drawdown


def check_profit_taking(current_price: float, price_at_last_rebalance: float) -> bool:
    """
    Check if current position represents a gain of more than 35%.

    Args:
        current_price: Current asset price
        price_at_last_rebalance: Price at last portfolio rebalance

    Returns:
        True if gain exceeds 35%, else False
    """
    gain = (current_price / price_at_last_rebalance) - 1

    return gain > 0.35
