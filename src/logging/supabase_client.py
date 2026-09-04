"""Supabase logging client for signal snapshots, trade decisions, and orders."""

import os
import json
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


def sanitize_for_json(data: dict) -> dict:
    """
    Recursively convert numpy types to native Python types for JSON serialization.

    Handles:
    - numpy.bool_ → bool
    - numpy.int64, numpy.int32, etc. → int
    - numpy.float64, numpy.float32, etc. → float
    - Lists and dicts (recursively)

    Args:
        data: Dictionary potentially containing numpy types

    Returns:
        Dictionary with all numpy types converted to native Python types
    """
    if not isinstance(data, dict):
        return data

    result = {}
    for key, value in data.items():
        if value is None:
            result[key] = None
        elif isinstance(value, dict):
            # Recursively sanitize nested dicts
            result[key] = sanitize_for_json(value)
        elif isinstance(value, list):
            # Recursively sanitize list items
            result[key] = [
                sanitize_for_json(item) if isinstance(item, dict) else _convert_numpy_type(item)
                for item in value
            ]
        else:
            # Convert individual numpy types
            result[key] = _convert_numpy_type(value)

    return result


def _convert_numpy_type(value):
    """Convert a single numpy type to native Python type."""
    try:
        import numpy as np

        if isinstance(value, np.bool_):
            return bool(value)
        elif isinstance(value, (np.integer, np.int64, np.int32, np.int16, np.int8)):
            return int(value)
        elif isinstance(value, (np.floating, np.float64, np.float32)):
            return float(value)
    except ImportError:
        pass

    return value


def get_supabase_client() -> Client:
    """Initialize and return a Supabase client."""
    url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_KEY")

    if not url or not service_key:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY in .env")

    return create_client(url, service_key)


def log_signal_snapshot(data: dict) -> str:
    """
    Insert a signal snapshot row into the database.

    Args:
        data: Dict with keys:
            environment, check_type, regime, cspx_price, cspx_200ma,
            fast_crash_triggered, ten_day_return, portfolio_drawdown_pct,
            circuit_breaker_triggered, growth_rankings, defensive_rankings,
            selected_growth

    Returns:
        The new row's id (UUID string)
    """
    try:
        client = get_supabase_client()

        payload = {
            "environment": data.get("environment"),
            "check_type": data.get("check_type"),
            "regime": data.get("regime"),
            "cspx_price": data.get("cspx_price"),
            "cspx_200ma": data.get("cspx_200ma"),
            "fast_crash_triggered": data.get("fast_crash_triggered", False),
            "ten_day_return": data.get("ten_day_return"),
            "portfolio_drawdown_pct": data.get("portfolio_drawdown_pct"),
            "circuit_breaker_triggered": data.get("circuit_breaker_triggered", False),
            "growth_rankings": data.get("growth_rankings"),
            "defensive_rankings": data.get("defensive_rankings"),
            "selected_growth": data.get("selected_growth"),
        }

        # Sanitize numpy types before sending to Supabase
        payload = sanitize_for_json(payload)

        response = client.table("signal_snapshots").insert(payload).execute()

        if response.data and len(response.data) > 0:
            row_id = response.data[0]["id"]
            print(f"✓ Signal snapshot logged: {row_id}")
            return row_id
        else:
            print("✗ Failed to log signal snapshot: no data in response")
            return None

    except Exception as e:
        print(f"✗ Error logging signal snapshot: {e}")
        return None


def log_trade_decision(data: dict) -> str:
    """
    Insert a trade decision row into the database.

    Args:
        data: Dict with keys:
            signal_snapshot_id, environment, trade_list, total_buy_amount,
            total_sell_amount, telegram_message_sent, user_response, responded_at

    Returns:
        The new row's id (UUID string)
    """
    try:
        client = get_supabase_client()

        payload = {
            "signal_snapshot_id": data.get("signal_snapshot_id"),
            "environment": data.get("environment"),
            "trade_list": data.get("trade_list"),
            "total_buy_amount": data.get("total_buy_amount", 0),
            "total_sell_amount": data.get("total_sell_amount", 0),
            "telegram_message_sent": data.get("telegram_message_sent"),
            "user_response": data.get("user_response"),
            "responded_at": data.get("responded_at"),
        }

        # Sanitize numpy types before sending to Supabase
        payload = sanitize_for_json(payload)

        response = client.table("trade_decisions").insert(payload).execute()

        if response.data and len(response.data) > 0:
            row_id = response.data[0]["id"]
            print(f"✓ Trade decision logged: {row_id}")
            return row_id
        else:
            print("✗ Failed to log trade decision: no data in response")
            return None

    except Exception as e:
        print(f"✗ Error logging trade decision: {e}")
        return None


def log_executed_order(data: dict) -> str:
    """
    Insert an executed order row into the database.

    Args:
        data: Dict with keys:
            trade_decision_id, environment, ticker, action, amount_gbp,
            t212_order_id, status, error_message

    Returns:
        The new row's id (UUID string)
    """
    try:
        client = get_supabase_client()

        payload = {
            "trade_decision_id": data.get("trade_decision_id"),
            "environment": data.get("environment"),
            "ticker": data.get("ticker"),
            "action": data.get("action"),
            "amount_gbp": data.get("amount_gbp"),
            "t212_order_id": data.get("t212_order_id"),
            "status": data.get("status", "submitted"),
            "error_message": data.get("error_message"),
        }

        # Sanitize numpy types before sending to Supabase
        payload = sanitize_for_json(payload)

        response = client.table("executed_orders").insert(payload).execute()

        if response.data and len(response.data) > 0:
            row_id = response.data[0]["id"]
            print(f"✓ Executed order logged: {row_id}")
            return row_id
        else:
            print("✗ Failed to log executed order: no data in response")
            return None

    except Exception as e:
        print(f"✗ Error logging executed order: {e}")
        return None


def log_portfolio_value(data: dict) -> str:
    """
    Insert a portfolio value snapshot into the database.

    Args:
        data: Dict with keys:
            environment, total_value_gbp, cash_gbp, invested_gbp

    Returns:
        The new row's id (UUID string)
    """
    try:
        client = get_supabase_client()

        payload = {
            "environment": data.get("environment"),
            "total_value_gbp": data.get("total_value_gbp"),
            "cash_gbp": data.get("cash_gbp"),
            "invested_gbp": data.get("invested_gbp"),
        }

        # Sanitize numpy types before sending to Supabase
        payload = sanitize_for_json(payload)

        response = client.table("portfolio_value_history").insert(payload).execute()

        if response.data and len(response.data) > 0:
            row_id = response.data[0]["id"]
            print(f"✓ Portfolio value logged: {row_id}")
            return row_id
        else:
            print("✗ Failed to log portfolio value: no data in response")
            return None

    except Exception as e:
        print(f"✗ Error logging portfolio value: {e}")
        return None


def get_portfolio_value_history(environment: str, limit: int = 30) -> list[float]:
    """
    Fetch the most recent N portfolio values for the given environment.

    Ordered from oldest to newest (ascending by created_at).

    Args:
        environment: 'demo' or 'live'
        limit: Number of recent entries to fetch (default 30)

    Returns:
        List of total_value_gbp floats, oldest to newest
    """
    try:
        client = get_supabase_client()

        response = (
            client.table("portfolio_value_history")
            .select("total_value_gbp")
            .eq("environment", environment)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )

        if response.data:
            # Reverse to get oldest-to-newest order
            values = [float(row["total_value_gbp"]) for row in response.data]
            values.reverse()
            return values
        else:
            return []

    except Exception as e:
        print(f"✗ Error fetching portfolio value history: {e}")
        return []


def send_heartbeat() -> bool:
    """
    Insert a heartbeat row to indicate the bot is running.

    Returns:
        True if successful, False otherwise
    """
    try:
        client = get_supabase_client()

        payload = {
            "status": "alive",
        }

        response = client.table("heartbeat").insert(payload).execute()

        if response.data and len(response.data) > 0:
            print("✓ Heartbeat sent")
            return True
        else:
            print("✗ Failed to send heartbeat: no data in response")
            return False

    except Exception as e:
        print(f"✗ Error sending heartbeat: {e}")
        return False
