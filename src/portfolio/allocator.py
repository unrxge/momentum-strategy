"""Portfolio allocation and position sizing module."""

import pandas as pd
from src.data.indicators import rolling_volatility


def calculate_inverse_vol_weights(tickers: list[str], price_data: dict[str, pd.Series]) -> dict[str, float]:
    """
    Calculate inverse-volatility weights for a set of tickers.

    Higher volatility assets get lower weights; lower volatility assets get higher weights.
    This smooths out portfolio volatility by overweighting stable assets.

    Args:
        tickers: List of ticker symbols
        price_data: Dict of {ticker: price_series}

    Returns:
        Dict of {ticker: weight} normalized to sum to 1.0
    """
    volatilities = {}

    for ticker in tickers:
        if ticker not in price_data:
            continue

        prices = price_data[ticker]
        vol = rolling_volatility(prices, window=20)
        volatilities[ticker] = vol

    if not volatilities:
        raise ValueError("No valid volatility data available")

    # Calculate inverse weights: 1 / volatility
    inverse_weights = {ticker: 1.0 / vol for ticker, vol in volatilities.items()}

    # Normalize to sum to 1.0
    total = sum(inverse_weights.values())
    normalized_weights = {ticker: weight / total for ticker, weight in inverse_weights.items()}

    return normalized_weights


def apply_position_limits(
    weights: dict[str, float],
    min_weight: float = 0.05,
    max_weight: float = 0.40
) -> dict[str, float]:
    """
    Apply position size limits to a weight allocation.

    - Caps any weight above max_weight
    - Removes weights below min_weight and redistributes their share
    - Re-normalizes to sum to 1.0

    Args:
        weights: Dict of {ticker: weight} (should sum to 1.0)
        min_weight: Minimum allowed weight (default 5%)
        max_weight: Maximum allowed weight (default 40%)

    Returns:
        Adjusted dict of {ticker: weight} normalized to sum to 1.0
    """
    if not weights:
        return {}

    # Step 1: Cap weights at max_weight
    capped = {ticker: min(weight, max_weight) for ticker, weight in weights.items()}

    # Step 2: Remove weights below min_weight
    above_min = {ticker: weight for ticker, weight in capped.items() if weight >= min_weight}

    if not above_min:
        raise ValueError("No assets remain after applying minimum weight limit")

    # Step 3: Calculate total of remaining weights
    total_above_min = sum(above_min.values())

    # Step 4: Redistribute excess from capped weights + removed weights
    # The excess is 1.0 - total_above_min
    # Redistribute proportionally among remaining positions
    if total_above_min < 1.0:
        excess = 1.0 - total_above_min
        proportional_increase = excess / len(above_min)
        adjusted = {ticker: weight + proportional_increase for ticker, weight in above_min.items()}
    else:
        adjusted = above_min

    # Step 5: Normalize to exactly 1.0
    total_adjusted = sum(adjusted.values())
    normalized = {ticker: weight / total_adjusted for ticker, weight in adjusted.items()}

    return normalized


def build_target_allocation(
    regime: str,
    top_growth: list[str],
    top_defensive: list[tuple[str, float]],
    price_data: dict[str, pd.Series],
    portfolio_value: float
) -> dict[str, dict]:
    """
    Build target portfolio allocation based on regime and top assets.

    HEALTHY REGIME:
    - Growth assets: inverse-vol weighted, scaled to 80%, with position limits
    - SGLN.L: fixed 15%
    - IGLS.L: fixed 5%

    UNHEALTHY REGIME:
    - If both defensives negative: 65% cash, 25% top growth, 10% least-negative defensive
    - Else: 50% top defensive, 25% second defensive, 15% cash, 10% top growth

    Args:
        regime: "healthy" or "unhealthy"
        top_growth: List of top growth ticker names, e.g. ["EQQQ.L", "VWRL.L"]
        top_defensive: List of (ticker, momentum_score) tuples, ranked by momentum
        price_data: Dict of {ticker: price_series}
        portfolio_value: Total portfolio value in GBP

    Returns:
        Dict of {ticker: {"weight": float, "gbp_amount": float}}
    """
    allocation = {}

    if regime == "healthy":
        # Growth assets: inverse-vol weighted, scaled to 80%
        if top_growth:
            growth_data = {t: price_data[t] for t in top_growth if t in price_data}
            growth_weights_raw = calculate_inverse_vol_weights(top_growth, price_data)

            # Apply position limits
            growth_weights_limited = apply_position_limits(growth_weights_raw)

            # Scale to 80% of portfolio
            growth_weights_scaled = {t: w * 0.80 for t, w in growth_weights_limited.items()}

            for ticker, weight in growth_weights_scaled.items():
                allocation[ticker] = {
                    "weight": weight,
                    "gbp_amount": weight * portfolio_value
                }

        # Fixed allocations for defensives
        allocation["SGLN.L"] = {
            "weight": 0.15,
            "gbp_amount": 0.15 * portfolio_value
        }
        allocation["IGLS.L"] = {
            "weight": 0.05,
            "gbp_amount": 0.05 * portfolio_value
        }

    else:  # regime == "unhealthy"
        # Extract momentum scores from ranked list
        defensive_momentums = {ticker: score for ticker, score in top_defensive}

        # Check if both defensives are negative
        all_negative = all(score < 0 for _, score in top_defensive)

        if all_negative:
            # 65% cash, 25% top growth, 10% least negative defensive
            if top_growth:
                top_growth_ticker = top_growth[0]
                allocation[top_growth_ticker] = {
                    "weight": 0.25,
                    "gbp_amount": 0.25 * portfolio_value
                }

            # Find least negative defensive
            least_negative = top_defensive[-1]  # Last in ranked list (lowest momentum)
            allocation[least_negative[0]] = {
                "weight": 0.10,
                "gbp_amount": 0.10 * portfolio_value
            }

            # 65% cash (represented as a key for tracking)
            allocation["CASH"] = {
                "weight": 0.65,
                "gbp_amount": 0.65 * portfolio_value
            }

        else:
            # 50% top defensive, 25% second defensive, 15% cash, 10% top growth
            if len(top_defensive) >= 1:
                top_defensive_ticker = top_defensive[0][0]
                allocation[top_defensive_ticker] = {
                    "weight": 0.50,
                    "gbp_amount": 0.50 * portfolio_value
                }

            if len(top_defensive) >= 2:
                second_defensive_ticker = top_defensive[1][0]
                allocation[second_defensive_ticker] = {
                    "weight": 0.25,
                    "gbp_amount": 0.25 * portfolio_value
                }

            allocation["CASH"] = {
                "weight": 0.15,
                "gbp_amount": 0.15 * portfolio_value
            }

            if top_growth:
                top_growth_ticker = top_growth[0]
                allocation[top_growth_ticker] = {
                    "weight": 0.10,
                    "gbp_amount": 0.10 * portfolio_value
                }

    return allocation
