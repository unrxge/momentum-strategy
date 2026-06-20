#!/usr/bin/env python3
"""Test Telegram notification layer with realistic rebalance message."""

from src.execution.telegram_notifier import format_rebalance_message, send_message

print("=" * 90)
print("TELEGRAM NOTIFIER TEST")
print("=" * 90)

# Fake data for a realistic rebalance scenario
environment = "DEMO"
account_value = 5000.00
regime = "healthy"
fast_crash_triggered = False

# Growth rankings (12-month momentum)
growth_rankings = [
    ("EQQQ.L", 0.435),
    ("VWRL.L", 0.309),
    ("CSPX.L", 0.277),
    ("VEUR.L", 0.218),
]
selected_growth = ["EQQQ.L", "VWRL.L"]

# Defensive rankings (3-month momentum)
defensive_rankings = [
    ("IGLS.L", 0.0127),
    ("SGLN.L", -0.0847),
]

# Trade list
trade_list = [
    {"ticker": "EQQQ.L", "action": "BUY", "amount_gbp": 1466.94},
    {"ticker": "VWRL.L", "action": "BUY", "amount_gbp": 2533.06},
    {"ticker": "SGLN.L", "action": "BUY", "amount_gbp": 750.00},
    {"ticker": "IGLS.L", "action": "BUY", "amount_gbp": 250.00},
]

# Format the message
print("\n[1/2] FORMATTING REBALANCE MESSAGE")
print("-" * 90)

message = format_rebalance_message(
    environment=environment,
    account_value=account_value,
    regime=regime,
    fast_crash_triggered=fast_crash_triggered,
    growth_rankings=growth_rankings,
    defensive_rankings=defensive_rankings,
    trade_list=trade_list,
    selected_growth=selected_growth,
)

print("\nPreview of message that will be sent:\n")
print(message)

# Send the message
print("\n[2/2] SENDING TO TELEGRAM")
print("-" * 90)

success = send_message(message)

if success:
    print("✅ Message sent successfully!")
    print("   Check your Telegram chat for the notification.")
else:
    print("❌ Failed to send message")
    print("   Check that TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set in .env")

print("\n" + "=" * 90)
print("TELEGRAM NOTIFIER TEST COMPLETE")
print("=" * 90)
