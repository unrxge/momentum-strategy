#!/usr/bin/env python3
"""Test both Telegram message types with realistic fake data."""

from src.execution.telegram_notifier import (
    format_rebalance_message,
    format_no_action_needed_message,
    send_message,
)

print("=" * 100)
print("TELEGRAM MESSAGE TYPES TEST")
print("=" * 100)

# Shared fake data
growth_rankings = [
    ("EQQQ.L", 0.435),
    ("VWRL.L", 0.309),
    ("CSPX.L", 0.277),
    ("VEUR.L", 0.218),
]
selected_growth = ["EQQQ.L", "VWRL.L"]

defensive_rankings = [
    ("IGLS.L", 0.0127),
    ("SGLN.L", -0.0847),
]

trade_list = [
    {"ticker": "EQQQ.L", "action": "BUY", "amount_gbp": 1466.94},
    {"ticker": "VWRL.L", "action": "BUY", "amount_gbp": 2533.06},
    {"ticker": "SGLN.L", "action": "BUY", "amount_gbp": 750.00},
    {"ticker": "IGLS.L", "action": "BUY", "amount_gbp": 250.00},
]

target_allocation = {
    "EQQQ.L": {"weight": 0.2934, "gbp_amount": 1466.94},
    "VWRL.L": {"weight": 0.5066, "gbp_amount": 2533.06},
    "SGLN.L": {"weight": 0.1500, "gbp_amount": 750.00},
    "IGLS.L": {"weight": 0.0500, "gbp_amount": 250.00},
}

current_positions = {
    "EQQQ.L": {"quantity": 2.6, "current_price": 561.20, "current_value": 1459.12},
    "VWRL.L": {"quantity": 18.3, "current_price": 138.95, "current_value": 2542.78},
    "SGLN.L": {"quantity": 12.3, "current_price": 60.92, "current_value": 749.31},
    "IGLS.L": {"quantity": 1.96, "current_price": 127.30, "current_value": 249.51},
}

# ============================================================================
# MESSAGE TYPE 1: ACTION NEEDED (with trades)
# ============================================================================

print("\n" + "=" * 100)
print("[TEST 1] ACTION NEEDED MESSAGE — Rebalance triggered (LIVE)")
print("=" * 100)

message_action = format_rebalance_message(
    environment="LIVE",
    account_value=5000.00,
    regime="unhealthy",
    fast_crash_triggered=True,
    growth_rankings=growth_rankings,
    defensive_rankings=defensive_rankings,
    trade_list=trade_list,
    selected_growth=selected_growth,
    is_simulated=False,
    reason="Fast-crash triggered — shifting to defensive allocation",
)

print("\n" + message_action)

print("\n[Would send this message to Telegram: YES/NO reply required]")

# ============================================================================
# MESSAGE TYPE 2: ACTION NEEDED (SIMULATED TEST)
# ============================================================================

print("\n" + "=" * 100)
print("[TEST 2] ACTION NEEDED MESSAGE — Simulated crash test (TEST)")
print("=" * 100)

message_simulated = format_rebalance_message(
    environment="DEMO",
    account_value=5000.00,
    regime="unhealthy",
    fast_crash_triggered=True,
    growth_rankings=growth_rankings,
    defensive_rankings=defensive_rankings,
    trade_list=trade_list,
    selected_growth=selected_growth,
    is_simulated=True,
    reason="SIMULATED: Fast-crash trigger forced for testing",
)

print("\n" + message_simulated)

print("\n[Would send this message to Telegram: YES/NO reply required]")

# ============================================================================
# MESSAGE TYPE 3: NO ACTION NEEDED (healthy regime, positions match)
# ============================================================================

print("\n" + "=" * 100)
print("[TEST 3] NO ACTION NEEDED MESSAGE — All OK (LIVE)")
print("=" * 100)

message_no_action = format_no_action_needed_message(
    environment="LIVE",
    account_value=5000.00,
    regime="healthy",
    fast_crash_triggered=False,
    circuit_breaker_triggered=False,
    drawdown_pct=3.5,
    target_allocation=target_allocation,
    current_positions=current_positions,
    reason="Positions are within 2% of target weights.",
    check_type="MONTHLY",
    next_check_date="2026-07-20",
    is_simulated=False,
)

print("\n" + message_no_action)

print("\n[Would send this message to Telegram: NO reply needed]")

# ============================================================================
# MESSAGE TYPE 4: NO ACTION NEEDED (circuit breaker active)
# ============================================================================

print("\n" + "=" * 100)
print("[TEST 4] NO ACTION NEEDED MESSAGE — Circuit breaker triggered (LIVE)")
print("=" * 100)

message_circuit_breaker = format_no_action_needed_message(
    environment="LIVE",
    account_value=4320.00,
    regime="unhealthy",
    fast_crash_triggered=True,
    circuit_breaker_triggered=True,
    drawdown_pct=13.6,
    target_allocation=target_allocation,
    current_positions=current_positions,
    reason="Circuit breaker is active — orders will not be placed until it clears.",
    check_type="MONTHLY",
    next_check_date="2026-07-20",
    is_simulated=False,
)

print("\n" + message_circuit_breaker)

print("\n[Would send this message to Telegram: NO reply needed]")

# ============================================================================
# MESSAGE TYPE 5: NO ACTION NEEDED (weekly check, no detailed table)
# ============================================================================

print("\n" + "=" * 100)
print("[TEST 5] NO ACTION NEEDED MESSAGE — Weekly check")
print("=" * 100)

message_weekly = format_no_action_needed_message(
    environment="DEMO",
    account_value=5000.00,
    regime="healthy",
    fast_crash_triggered=False,
    circuit_breaker_triggered=False,
    drawdown_pct=1.2,
    target_allocation={},  # No detailed table for weekly
    current_positions={},
    reason="Weekly health check — all indicators normal.",
    check_type="WEEKLY",
    next_check_date="2026-06-27",
    is_simulated=False,
)

print("\n" + message_weekly)

print("\n[Would send this message to Telegram: NO reply needed]")

# ============================================================================
# OPTIONAL: Send a test message
# ============================================================================

print("\n" + "=" * 100)
print("[OPTIONAL] Send a test message to Telegram?")
print("=" * 100)

send_test = input("\nType 'SEND' to send TEST 1 (ACTION NEEDED) to Telegram, or press Enter to skip: ").strip().upper()

if send_test == "SEND":
    print("\nSending TEST 1 to Telegram...")
    success = send_message(message_action)
    if success:
        print("✅ Message sent! Check your Telegram chat.")
    else:
        print("❌ Failed to send. Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
else:
    print("Skipped — no message sent")

print("\n" + "=" * 100)
print("TEST COMPLETE")
print("=" * 100)
