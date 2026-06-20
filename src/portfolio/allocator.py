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

    - Caps any weight above max_weight at EXACTLY max_weight
    - Redistributes excess only to non-capped assets (not back to capped ones)
    - Handles cascading caps if redistribution pushes another asset over max_weight
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

    # PHASE 1: Handle max_weight caps with cascading redistribution
    weights_dict = dict(weights)
    max_iterations = 100  # Prevent infinite loops

    for iteration in range(max_iterations):
        # Identify all assets currently above max_weight
        over_max = {t for t, w in weights_dict.items() if w > max_weight}

        if not over_max:
            break  # No more assets above max_weight

        # Calculate total excess from all over-max assets
        total_excess = sum(weights_dict[t] - max_weight for t in over_max)

        # Cap all over-max assets at exactly max_weight
        for t in over_max:
            weights_dict[t] = max_weight

        # Find assets that are NOT capped (under or at max_weight)
        under_max = set(weights_dict.keys()) - over_max

        if not under_max:
            break  # All assets are at the cap, nowhere to redistribute

        # Redistribute excess equally among only the under-max assets
        per_asset = total_excess / len(under_max)
        for t in under_max:
            weights_dict[t] += per_asset

    # PHASE 2: Handle min_weight floor
    above_min = {t: w for t, w in weights_dict.items() if w >= min_weight}

    if not above_min:
        raise ValueError("No assets remain after applying minimum weight limit")

    # Normalize to 1.0
    total = sum(above_min.values())
    normalized = {t: w / total for t, w in above_min.items()}

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
