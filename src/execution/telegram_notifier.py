"""Telegram notification layer for trade alerts and approvals."""

import os
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
) -> str:
    """
    Format a rebalance notification message for Telegram.

    Args:
        environment: "DEMO" or "LIVE"
        account_value: Current account value in GBP
        regime: "healthy" or "unhealthy"
        fast_crash_triggered: Whether fast-crash alert is active
        growth_rankings: List of (ticker, momentum) tuples
        defensive_rankings: List of (ticker, momentum) tuples
        trade_list: List of trade instructions
        selected_growth: List of selected growth ticker names

    Returns:
        Formatted message string
    """
    selected_growth = selected_growth or []
    today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    regime_display = "Healthy" if regime == "healthy" else "Unhealthy"

    message = f"""🔔 MONTHLY REBALANCE — ACTION NEEDED

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

    message += "\nPROPOSED TRADES:\n"
    if trade_list:
        for trade in trade_list:
            ticker = trade["ticker"]
            action = trade["action"]
            amount = trade["amount_gbp"]
            message += f"  {action:<6} {ticker:<10} £{amount:>10.2f}\n"
    else:
        message += "  (No trades needed)\n"

    message += "\nReply YES to execute, or NO to skip this cycle."

    return message


def wait_for_reply(timeout_seconds: int = 86400) -> str | None:
    """
    Poll Telegram for a YES or NO reply from the user.

    Args:
        timeout_seconds: How long to wait for a reply (default 24 hours)

    Returns:
        "YES", "NO", or None if timeout reached
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("⚠️  Telegram not configured")
        return None

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    params = {"timeout": 30, "allowed_updates": ["message"]}

    start_time = datetime.now()
    offset = None
    poll_count = 0

    print(f"⏳ Waiting for approval (up to {timeout_seconds} seconds)...")

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
                message = update.get("message", {})
                message_chat_id = message.get("chat", {}).get("id")
                text = message.get("text", "").upper()

                # Update offset to skip processed messages
                offset = update.get("update_id", 0) + 1

                # Check if this is from the right chat and contains YES/NO
                if str(message_chat_id) == str(chat_id):
                    if "YES" in text:
                        print("✅ Received: YES — executing trades")
                        return "YES"
                    elif "NO" in text:
                        print("❌ Received: NO — skipping this cycle")
                        return "NO"

        except requests.RequestException as e:
            print(f"⚠️  Telegram polling error: {e}")
            continue
