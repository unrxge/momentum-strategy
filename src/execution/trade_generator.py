"""Trade instruction generation based on portfolio rebalancing."""


def generate_trade_list(
    target_allocation: dict[str, dict],
    current_positions: dict[str, dict],
    portfolio_value: float,
    min_trade_size: float = 150.0,
) -> list[dict]:
    """
    Generate a list of trade instructions to rebalance from current to target allocation.

    Compares target allocation (with GBP amounts) to current positions, and generates
    BUY or SELL instructions for any position that differs by more than min_trade_size.

    Also identifies positions held but not in target allocation, generating full exit orders.

    Args:
        target_allocation: Dict of {ticker: {weight, gbp_amount}} from portfolio allocator
        current_positions: Dict of {ticker: {quantity, current_value, avg_price}} from T212
        portfolio_value: Total portfolio value in GBP (used for reference)
        min_trade_size: Minimum trade size in GBP to execute (default £150)

    Returns:
        List of trade instructions: [
            {"ticker": str, "action": "BUY"|"SELL", "amount_gbp": float},
            ...
        ]
    """
    trades = []

    # Process each asset in target allocation
    for ticker, target_data in target_allocation.items():
        # Skip CASH entries (these don't generate trades)
        if ticker == "CASH":
            continue

        target_amount = target_data["gbp_amount"]
        current_position = current_positions.get(ticker, {})
        current_value = current_position.get("current_value", 0)

        # Calculate difference
        difference = target_amount - current_value

        # Skip if trade is below minimum size
        if abs(difference) < min_trade_size:
            continue

        # Generate trade instruction
        action = "BUY" if difference > 0 else "SELL"
        trades.append({"ticker": ticker, "action": action, "amount_gbp": abs(difference)})

    # Check for positions NOT in target allocation (exit positions)
    for ticker, position_data in current_positions.items():
        if ticker not in target_allocation or target_allocation[ticker]["gbp_amount"] == 0:
            current_value = position_data["current_value"]

            # Generate SELL instruction if position is above minimum size
            if current_value >= min_trade_size:
                trades.append({"ticker": ticker, "action": "SELL", "amount_gbp": current_value})

    return trades
