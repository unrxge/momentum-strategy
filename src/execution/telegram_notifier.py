"""Telegram notification layer for trade alerts and approvals."""

import os
import re
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


def send_message(text: str) -> bool:
    """
    Send a plain-text message to Telegram.

    Args:
        text: Message text to send

    Returns:
        True if successful, False otherwise
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("⚠️  Telegram not configured (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": text}

    try:
        response = requests.post(url, json=data, timeout=10)
        if response.status_code == 200:
            return True
        else:
            print(f"✗ Telegram send failed ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        print(f"✗ Telegram send error: {e}")
        return False


def format_rebalance_message(
    environment: str,
    account_value: float,
    regime: str,
    fast_crash_triggered: bool,
    growth_rankings: list[tuple[str, float]],
    defensive_rankings: list[tuple[str, float]],
    trade_list: list[dict],
    selected_growth: list[str] = None,
    is_simulated: bool = False,
    reason: str = "",
) -> str:
    """
    Format a rebalance action notification message for Telegram.

    Args:
        environment: "DEMO" or "LIVE"
        account_value: Current account value in GBP
        regime: "healthy" or "unhealthy"
        fast_crash_triggered: Whether fast-crash alert is active
        growth_rankings: List of (ticker, momentum) tuples
        defensive_rankings: List of (ticker, momentum) tuples
        trade_list: List of trade instructions
        selected_growth: List of selected growth ticker names
        is_simulated: Whether this is from a simulated test
        reason: Plain-English reason for the rebalance

    Returns:
        Formatted message string
    """
    selected_growth = selected_growth or []
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    regime_display = "Healthy" if regime == "healthy" else "Unhealthy"

    source = "🔴 SOURCE: SIMULATED TEST DATA" if is_simulated else "🟢 SOURCE: LIVE MARKET DATA"

    message = f"""{source}

🔔 MONTHLY REBALANCE — ACTION NEEDED

Date: {today}
Environment: {environment}
Account Value: £{account_value:.2f}

REGIME: {regime_display}
Fast-Crash Triggered: {"Yes" if fast_crash_triggered else "No"}

TOP GROWTH (12-month momentum):
"""

    for ticker, momentum in growth_rankings:
        selected = " ← selected" if ticker in selected_growth else ""
        message += f"  {ticker}: {momentum * 100:+.2f}%{selected}\n"

    message += "\nDEFENSIVE (3-month momentum):\n"
    for ticker, momentum in defensive_rankings:
        message += f"  {ticker}: {momentum * 100:+.2f}%\n"

    message += "\nWHY THESE TRADES:\n"
    message += f"  {reason}\n" if reason else "  Rebalancing to target allocation.\n"

    message += "\nPROPOSED TRADES:\n"
    for trade in trade_list:
        ticker = trade["ticker"]
        action = trade["action"]
        amount = trade["amount_gbp"]
        message += f"  {action:<6} {ticker:<10} £{amount:>10.2f}\n"

    message += "\nReply YES to execute, or NO to skip this cycle."

    return message


def format_no_action_needed_message(
    environment: str,
    account_value: float,
    regime: str,
    fast_crash_triggered: bool,
    circuit_breaker_triggered: bool,
    drawdown_pct: float,
    target_allocation: dict[str, dict],
    current_positions: dict[str, dict],
    reason: str = "",
    check_type: str = "MONTHLY",
    next_check_date: str = "",
    is_simulated: bool = False,
) -> str:
    """
    Format a "no action needed" notification for Telegram.

    Args:
        environment: "DEMO" or "LIVE"
        account_value: Current account value in GBP
        regime: "healthy" or "unhealthy"
        fast_crash_triggered: Whether fast-crash alert is active
        circuit_breaker_triggered: Whether circuit breaker is active
        drawdown_pct: Current drawdown from peak (0-100)
        target_allocation: Target allocation dict {ticker: {weight, gbp_amount}}
        current_positions: Current positions dict {ticker: {quantity, current_value, ...}}
        reason: Plain-English reason why no action is needed
        check_type: "MONTHLY" or "WEEKLY"
        next_check_date: When the next check is scheduled
        is_simulated: Whether this is from a simulated test

    Returns:
        Formatted message string
    """
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    regime_display = "Healthy" if regime == "healthy" else "Unhealthy"
    source = "🔴 SOURCE: SIMULATED TEST DATA" if is_simulated else "🟢 SOURCE: LIVE MARKET DATA"

    message = f"""{source}

✅ {check_type} CHECK COMPLETE — NO ACTION NEEDED

Date: {today}
Environment: {environment}"""

    if check_type == "MONTHLY":
        message += f"\nAccount Value: £{account_value:.2f}"

    message += f"""

REGIME: {regime_display}
Fast-Crash Triggered: {"Yes" if fast_crash_triggered else "No"}
Circuit Breaker: {"Triggered" if circuit_breaker_triggered else "Not triggered"}
Drawdown from peak: {drawdown_pct:.2f}%

WHY NO ACTION:
  {reason if reason else "Positions are within tolerance of target allocation."}
"""

    # For monthly checks, show the detailed table
    if check_type == "MONTHLY" and target_allocation:
        message += "\nPOSITION AUDIT (Target vs Actual):\n"
        message += "Ticker       Target      Actual      Difference\n"
        message += "-" * 50 + "\n"

        for ticker, target_data in target_allocation.items():
            if ticker == "CASH":
                continue

            target_amount = target_data["gbp_amount"]
            current_pos = current_positions.get(ticker, {})
            actual_value = current_pos.get("current_value", 0)
            difference = actual_value - target_amount

            message += f"{ticker:<12} £{target_amount:>8.2f}  £{actual_value:>8.2f}  £{difference:>8.2f}\n"

    if next_check_date:
        message += f"\nNext check: {next_check_date}"
    else:
        message += "\nNo reply needed."

    return message


def get_high_water_mark() -> int:
    """
    Get the update_id of the most recent message in the chat (high water mark).

    This marks the cutoff point — any message with update_id <= this value is old
    and should be ignored. Only NEW messages with update_id > this will be considered
    replies to a fresh prompt.

    Returns:
        The update_id of the most recent message, or 0 if none found
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        return 0

    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        response = requests.get(url, params={"limit": 1, "allowed_updates": ["message"]}, timeout=10)

        if response.status_code != 200:
            return 0

        data = response.json()
        updates = data.get("result", [])

        if updates:
            return updates[0].get("update_id", 0)

        return 0

    except requests.RequestException:
        return 0


def wait_for_reply(timeout_seconds: int = 86400, high_water_mark: int = 0) -> dict | None:
    """
    Poll Telegram for a YES or NO reply from the user.

    Only processes messages with update_id > high_water_mark (i.e., NEW messages sent
    after the prompt was sent). Also checks message timestamp as a safety layer.

    Args:
        timeout_seconds: How long to wait for a reply (default 24 hours)
        high_water_mark: Only consider messages with update_id > this value

    Returns:
        Dict with keys {reply: "YES"/"NO", update_id: int, timestamp: int},
        or None if timeout reached
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("⚠️  Telegram not configured")
        return None

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"timeout": 30, "allowed_updates": ["message"]}

    start_time = datetime.now()
    start_timestamp = int(start_time.timestamp())
    offset = None
    poll_count = 0

    print(f"⏳ Waiting for approval (up to {timeout_seconds} seconds)...")
    print(f"  (Ignoring messages with update_id <= {high_water_mark})")

    while True:
        elapsed = (datetime.now() - start_time).total_seconds()
        if elapsed > timeout_seconds:
            print("⏱️  Timeout reached — no response received")
            return None

        poll_count += 1
        if poll_count % 10 == 0:  # Print status every 10 polls (~5 minutes)
            print(f"  Still waiting... ({int(elapsed)} seconds elapsed)")

        try:
            if offset:
                params["offset"] = offset

            response = requests.get(url, params=params, timeout=40)

            if response.status_code != 200:
                print(f"✗ Telegram getUpdates failed: {response.status_code}")
                continue

            data = response.json()
            updates = data.get("result", [])

            for update in updates:
                update_id = update.get("update_id", 0)
                message = update.get("message", {})
                message_chat_id = message.get("chat", {}).get("id")
                message_timestamp = message.get("date", 0)
                text = message.get("text", "").upper()

                # Always update offset for next request (tells Telegram's servers to skip it)
                offset = update_id + 1

                # Skip old messages: update_id must be strictly greater than high water mark
                if update_id <= high_water_mark:
                    continue

                # Skip messages from wrong chat
                if str(message_chat_id) != str(chat_id):
                    continue

                # Safety layer: ignore messages sent before we started waiting
                if message_timestamp < start_timestamp:
                    continue

                # Check if this is a YES or NO reply (whole-word match: "NOT NOW" is not a NO,
                # "YESTERDAY" is not a YES)
                words = set(re.findall(r"[A-Z]+", text))
                if "YES" in words and "NO" not in words:
                    print(f"✅ Received: YES (update_id={update_id}, timestamp={message_timestamp})")
                    return {"reply": "YES", "update_id": update_id, "timestamp": message_timestamp}
                elif "NO" in words:
                    print(f"❌ Received: NO (update_id={update_id}, timestamp={message_timestamp})")
                    return {"reply": "NO", "update_id": update_id, "timestamp": message_timestamp}

        except requests.RequestException as e:
            print(f"⚠️  Telegram polling error: {e}")
            continue
