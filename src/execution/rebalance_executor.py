"""
Rebalance executor: orchestrates placing multiple orders from a trade list.

Handles order sequencing (SELLs first, then BUYs), rate limiting,
logging, and graceful failure handling.
"""

import time
from datetime import datetime
from uuid import UUID
from src.execution.t212_client import T212Client
from src.execution.t212_tickers import get_t212_ticker
from src.logging.supabase_client import log_executed_order


def check_for_pending_orders() -> dict:
    """
    Check for any pending/unfilled orders in the account.

    CRITICAL: If pending orders exist, the rebalance must NOT proceed.
    We cannot safely calculate target positions without knowing what these
    orders will eventually fill at — doing so risks duplicate orders.
    This function enforces that rebalance decisions are always based on
    a fully-settled, known account state.

    Returns:
        {
            "has_pending": bool,
            "pending_orders": list of order dicts,
            "oldest_order_age_hours": float or None
        }
    """
    from datetime import timezone

    client = T212Client()
    orders = client.get_pending_orders()

    if not orders:
        return {
            "has_pending": False,
            "pending_orders": [],
            "oldest_order_age_hours": None,
        }

    # Calculate age of oldest order
    oldest_age_hours = None
    now = datetime.now(timezone.utc)  # Timezone-aware UTC now

    for order in orders:
        created_at_str = order.get("createdAt")
        if created_at_str:
            try:
                # Handle ISO format timestamps (with or without Z suffix)
                created_at_str_clean = created_at_str.replace("Z", "+00:00")
                created_at = datetime.fromisoformat(created_at_str_clean)

                # Ensure both datetimes are timezone-aware for subtraction
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)

                age_hours = (now - created_at).total_seconds() / 3600
                if oldest_age_hours is None or age_hours > oldest_age_hours:
                    oldest_age_hours = age_hours
            except (ValueError, AttributeError):
                # If we can't parse the timestamp, skip age calculation for this order
                pass

    return {
        "has_pending": True,
        "pending_orders": orders,
        "oldest_order_age_hours": oldest_age_hours,
    }


def execute_sells(
    trade_list: list[dict],
    trade_decision_id: str,
    price_data: dict,
) -> dict:
    """
    Execute ONLY the sell orders from a trade list.

    Raises cash for subsequent buy operations. Sell orders are placed
    immediately without waiting for settlement. Settlement can take minutes
    to days depending on asset liquidity.

    Args:
        trade_list: List of trades (will filter to SELL only)
        trade_decision_id: UUID (str) of the parent trade_decision or None
        price_data: Dict of {ticker: price_series}

    Returns:
        Summary dict with total_trades, successful, failed, results
    """
    # Validate trade_decision_id: if provided (not None), must be a valid UUID
    if trade_decision_id is not None:
        try:
            UUID(trade_decision_id)
        except (ValueError, TypeError):
            raise ValueError(
                f"Invalid trade_decision_id: '{trade_decision_id}'. "
                f"Must be a valid UUID (e.g., from uuid.uuid4()) or None for standalone tests"
            )

    client = T212Client()

    # Fetch instruments for precision requirements
    try:
        instruments = client.get_instruments()
        instrument_map = {inst.get("ticker"): inst for inst in instruments}
    except Exception as e:
        print(f"⚠️  Warning: Could not fetch instrument metadata: {e}")
        print("   Falling back to default 4-decimal precision for all orders\n")
        instrument_map = {}

    # Filter to SELL trades only
    sell_trades = [t for t in trade_list if t["action"] == "SELL"]

    results = []
    successful = 0
    failed = 0

    print("\n" + "=" * 100)
    print("EXECUTING SELL ORDERS")
    print("=" * 100)
    print(f"\nTotal SELL orders to execute: {len(sell_trades)}\n")

    for i, trade in enumerate(sell_trades, 1):
        yfinance_ticker = trade["ticker"]
        action = "SELL"  # We're only processing SELL orders in this function
        requested_amount = trade["amount_gbp"]

        print(f"[{i}/{len(sell_trades)}] {action} {yfinance_ticker}")

        try:
            # Step 1: Convert to T212 ticker
            t212_ticker = get_t212_ticker(yfinance_ticker)
            print(f"  T212 ticker: {t212_ticker}")

            # Step 2: Get current price
            price_series = price_data.get(yfinance_ticker)
            if price_series is None or len(price_series) == 0:
                raise ValueError(f"No price data available for {yfinance_ticker}")

            current_price = float(price_series.iloc[-1])
            print(f"  Current price: £{current_price:.2f}")

            # Step 3: Calculate quantity with instrument-specific precision
            # Look up precision for this ticker; default to 4 if not found
            instrument = instrument_map.get(t212_ticker, {})
            # For now, use heuristic: if no precision field, try 2 decimals for problematic instruments
            # (T212 rejects 4-decimal precision for some instruments like SGLN)
            precision = 4  # Default
            if t212_ticker == "SGLNl_EQ":
                precision = 2  # SGLN specifically needs 2-decimal precision

            quantity = client.calculate_order_quantity(requested_amount, current_price, precision=precision)

            # CRITICAL: For SELL orders, quantity must be NEGATIVE
            if action == "SELL":
                quantity = -quantity
                print(f"  Quantity (negative for SELL): {quantity}")
            else:
                print(f"  Quantity: {quantity}")

            # Step 4: Place market order
            order_response = client.place_market_order(t212_ticker, quantity)

            # Check if order succeeded
            if "error" in order_response:
                raise ValueError(order_response["error"])

            # Extract order ID
            order_id = order_response.get("id")
            print(f"  ✓ Order placed: ID {order_id}\n")

            # Step 5: Log the successful order
            log_executed_order(
                {
                    "trade_decision_id": trade_decision_id,
                    "environment": client.environment.upper(),
                    "ticker": t212_ticker,
                    "action": action,
                    "amount_gbp": requested_amount,
                    "t212_order_id": order_id,
                    "status": "submitted",
                    "error_message": None,
                }
            )

            results.append(
                {
                    "ticker": yfinance_ticker,
                    "action": action,
                    "requested_amount_gbp": requested_amount,
                    "status": "SUCCESS",
                    "t212_order_id": order_id,
                    "error_message": None,
                }
            )

            successful += 1

        except Exception as e:
            error_message = str(e)
            print(f"  ✗ Failed: {error_message}\n")

            # Log the failed order
            log_executed_order(
                {
                    "trade_decision_id": trade_decision_id,
                    "environment": client.environment.upper(),
                    "ticker": yfinance_ticker,
                    "action": action,
                    "amount_gbp": requested_amount,
                    "t212_order_id": None,
                    "status": "failed",
                    "error_message": error_message,
                }
            )

            results.append(
                {
                    "ticker": yfinance_ticker,
                    "action": action,
                    "requested_amount_gbp": requested_amount,
                    "status": "FAILED",
                    "t212_order_id": None,
                    "error_message": error_message,
                }
            )

            failed += 1

        # Rate limiting: 1.5 seconds between orders (not after the last one)
        if i < len(sell_trades):
            time.sleep(1.5)

    print()
    # Build and return summary
    summary = {
        "total_trades": len(sell_trades),
        "successful": successful,
        "failed": failed,
        "results": results,
    }

    return summary


def execute_buys(
    trade_list: list[dict],
    trade_decision_id: str,
    price_data: dict,
) -> dict:
    """
    Execute ONLY the buy orders from a trade list.

    CRITICAL: Before placing ANY buy orders, checks if pending orders exist.
    If so, returns status "waiting_on_settlement" without placing any buys.
    This prevents buying with unsettled sell proceeds.

    Args:
        trade_list: List of trades (will filter to BUY only)
        trade_decision_id: UUID (str) of the parent trade_decision or None
        price_data: Dict of {ticker: price_series}

    Returns:
        Summary dict with total_trades, successful, failed, results, and phase status
    """
    # Validate trade_decision_id: if provided (not None), must be a valid UUID
    if trade_decision_id is not None:
        try:
            UUID(trade_decision_id)
        except (ValueError, TypeError):
            raise ValueError(
                f"Invalid trade_decision_id: '{trade_decision_id}'. "
                f"Must be a valid UUID (e.g., from uuid.uuid4()) or None for standalone tests"
            )

    # Filter to BUY trades only
    buy_trades = [t for t in trade_list if t["action"] == "BUY"]

    print("\n" + "=" * 100)
    print("EXECUTING BUY ORDERS")
    print("=" * 100)
    print(f"\nTotal BUY orders to execute: {len(buy_trades)}\n")

    # CRITICAL: Check for pending orders before placing ANY buys
    # If prior sells from this rebalance haven't settled yet, we can't safely spend their proceeds
    pending_check = check_for_pending_orders()

    if pending_check["has_pending"]:
        print(f"⏸️  BLOCKING BUY EXECUTION — {len(pending_check['pending_orders'])} pending order(s) still outstanding")
        print(f"   Likely from sell orders in the same rebalance that haven't settled yet.\n")
        print(f"   Pending orders:")
        for order in pending_check["pending_orders"]:
            print(f"     • {order.get('ticker')}: {order.get('quantity')} shares")
        print()
        print(f"   Call execute_buys() again once these orders settle (can be minutes to days).\n")

        return {
            "total_trades": len(buy_trades),
            "successful": 0,
            "failed": 0,
            "results": [],
            "phase_status": "waiting_on_settlement",
            "pending_order_count": len(pending_check["pending_orders"]),
        }

    # No pending orders — safe to proceed with buys
    client = T212Client()

    # Fetch instruments for precision requirements
    try:
        instruments = client.get_instruments()
        instrument_map = {inst.get("ticker"): inst for inst in instruments}
    except Exception as e:
        print(f"⚠️  Warning: Could not fetch instrument metadata: {e}")
        print("   Falling back to default 4-decimal precision for all orders\n")
        instrument_map = {}

    results = []
    successful = 0
    failed = 0

    for i, trade in enumerate(buy_trades, 1):
        yfinance_ticker = trade["ticker"]
        action = "BUY"  # We're only processing BUY orders in this function
        requested_amount = trade["amount_gbp"]

        print(f"[{i}/{len(buy_trades)}] {action} {yfinance_ticker}")

        try:
            # Step 1: Convert to T212 ticker
            t212_ticker = get_t212_ticker(yfinance_ticker)
            print(f"  T212 ticker: {t212_ticker}")

            # Step 2: Get current price
            price_series = price_data.get(yfinance_ticker)
            if price_series is None or len(price_series) == 0:
                raise ValueError(f"No price data available for {yfinance_ticker}")

            current_price = float(price_series.iloc[-1])
            print(f"  Current price: £{current_price:.2f}")

            # Step 3: Calculate quantity with instrument-specific precision
            precision = 4  # Default
            if t212_ticker == "SGLNl_EQ":
                precision = 2  # SGLN specifically needs 2-decimal precision

            quantity = client.calculate_order_quantity(requested_amount, current_price, precision=precision)
            print(f"  Quantity: {quantity}")

            # Step 4: Place market order
            order_response = client.place_market_order(t212_ticker, quantity)

            # Check if order succeeded
            if "error" in order_response:
                raise ValueError(order_response["error"])

            # Extract order ID
            order_id = order_response.get("id")
            print(f"  ✓ Order placed: ID {order_id}\n")

            # Step 5: Log the successful order
            log_executed_order(
                {
                    "trade_decision_id": trade_decision_id,
                    "environment": client.environment.upper(),
                    "ticker": t212_ticker,
                    "action": action,
                    "amount_gbp": requested_amount,
                    "t212_order_id": order_id,
                    "status": "submitted",
                    "error_message": None,
                }
            )

            results.append(
                {
                    "ticker": yfinance_ticker,
                    "action": action,
                    "requested_amount_gbp": requested_amount,
                    "status": "SUCCESS",
                    "t212_order_id": order_id,
                    "error_message": None,
                }
            )

            successful += 1

        except Exception as e:
            error_message = str(e)
            print(f"  ✗ Failed: {error_message}\n")

            # Log the failed order
            log_executed_order(
                {
                    "trade_decision_id": trade_decision_id,
                    "environment": client.environment.upper(),
                    "ticker": yfinance_ticker,
                    "action": action,
                    "amount_gbp": requested_amount,
                    "t212_order_id": None,
                    "status": "failed",
                    "error_message": error_message,
                }
            )

            results.append(
                {
                    "ticker": yfinance_ticker,
                    "action": action,
                    "requested_amount_gbp": requested_amount,
                    "status": "FAILED",
                    "t212_order_id": None,
                    "error_message": error_message,
                }
            )

            failed += 1

        # Rate limiting: 1.5 seconds between orders (not after the last one)
        if i < len(buy_trades):
            time.sleep(1.5)

    print()
    # Build and return summary
    summary = {
        "total_trades": len(buy_trades),
        "successful": successful,
        "failed": failed,
        "results": results,
        "phase_status": "complete",
    }

    return summary


def execute_rebalance(
    trade_list: list[dict],
    trade_decision_id: str,
    price_data: dict,
) -> dict:
    """
    Orchestrate a complete rebalance across two phases: SELL then BUY.

    Phase 1: Execute all SELL orders to raise cash.
    Phase 2: Only proceed to BUY orders if:
             - There were no sells (pure fresh deployment), OR
             - There were sells AND no pending orders remain (all settled)

    If sells are just placed and still pending, returns immediately with status
    "sells_placed_awaiting_settlement". Call execute_rebalance() again later
    once settlement is complete.

    Args:
        trade_list: List of trades (will be split into SELLs and BUYs)
        trade_decision_id: UUID (str) of the parent trade_decision or None
        price_data: Dict of {ticker: price_series}

    Returns:
        Summary dict with phase_status, summary of each phase, and combined results
    """
    sell_trades = [t for t in trade_list if t["action"] == "SELL"]
    buy_trades = [t for t in trade_list if t["action"] == "BUY"]

    print("\n" + "=" * 100)
    print("REBALANCE EXECUTION — TWO-PHASE APPROACH")
    print("=" * 100)
    print(f"\nTrade list: {len(sell_trades)} SELLs, {len(buy_trades)} BUYs")
    print(f"Environment: {T212Client().environment.upper()}\n")

    # ========================================================================
    # PHASE 1: Execute sells (if any)
    # ========================================================================
    sell_summary = None
    if sell_trades:
        print("PHASE 1: Executing SELL orders")
        print("-" * 100 + "\n")
        sell_summary = execute_sells(trade_list, trade_decision_id, price_data)

        # After executing sells, immediately check if any are still pending
        pending_check = check_for_pending_orders()
        if pending_check["has_pending"]:
            print(f"\n⏸️  SELL orders placed but still pending settlement.")
            print(f"   Pending: {len(pending_check['pending_orders'])} order(s)")
            print(f"   BUY phase cannot proceed until these settle (can take hours/days).\n")

            return {
                "phase_status": "sells_placed_awaiting_settlement",
                "sell_summary": sell_summary,
                "buy_summary": None,
                "total_successful": sell_summary.get("successful", 0),
                "total_failed": sell_summary.get("failed", 0),
            }
    else:
        print("No SELL orders in this rebalance\n")

    # ========================================================================
    # PHASE 2: Execute buys (only if no sells, or sells are settled)
    # ========================================================================
    if buy_trades:
        print("\nPHASE 2: Executing BUY orders")
        print("-" * 100 + "\n")
        buy_summary = execute_buys(trade_list, trade_decision_id, price_data)
    else:
        print("\nNo BUY orders in this rebalance\n")
        buy_summary = {
            "total_trades": 0,
            "successful": 0,
            "failed": 0,
            "results": [],
            "phase_status": "complete",
        }

    # ========================================================================
    # Combined summary
    # ========================================================================
    total_successful = (sell_summary.get("successful", 0) if sell_summary else 0) + buy_summary.get("successful", 0)
    total_failed = (sell_summary.get("failed", 0) if sell_summary else 0) + buy_summary.get("failed", 0)

    return {
        "phase_status": "complete",
        "sell_summary": sell_summary,
        "buy_summary": buy_summary,
        "total_successful": total_successful,
        "total_failed": total_failed,
    }
