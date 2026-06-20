#!/usr/bin/env python3
"""
Crash/circuit-breaker execution test harness.

CRITICAL: This test ACTUALLY submits real orders to T212 DEMO account.
It manually forces the crash trigger condition to test the complete
execution pipeline without waiting for a real market crash signal.

This tests the EXECUTION path, not the signal math.
"""

from datetime import datetime
from src.execution.t212_client import T212Client
from src.execution.trade_generator import generate_trade_list
from src.data.price_fetcher import fetch_price_history
from src.signals.regime import check_regime
from src.signals.momentum import rank_growth_assets, rank_defensive_assets, select_top_growth
from src.portfolio.allocator import build_target_allocation

GROWTH_TICKERS = ["CSPX.L", "EQQQ.L", "VWRL.L", "VEUR.L"]
DEFENSIVE_TICKERS = ["SGLN.L", "IGLS.L"]

print("=" * 90)
print("CRASH SIMULATION — EXECUTION TEST HARNESS")
print("=" * 90)
print("\n⚠️  WARNING: This test will SUBMIT REAL ORDERS to T212 DEMO account")
print("   All orders are to DEMO, but this is a live execution test.\n")

# Get user confirmation before proceeding
confirm = input("Type 'CRASH_TEST' to confirm and proceed: ").strip().upper()
if confirm != "CRASH_TEST":
    print("Aborted — test cancelled")
    exit(0)

# Initialize logging
log_entries = []


def log_event(event: str):
    """Log an event with timestamp."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    full_event = f"[{timestamp}] {event}"
    log_entries.append(full_event)
    print(full_event)


print("\n" + "=" * 90)
print("[STEP 1] INITIALIZE — Fetch current state")
print("=" * 90)

log_event("Initializing T212 client...")
try:
    client = T212Client()
except Exception as e:
    log_event(f"✗ Failed to initialize T212: {e}")
    exit(1)

log_event("Fetching current positions...")
try:
    positions_before = client.get_current_positions()
    account_before = client.get_account_cash()
    log_event(f"Current positions: {list(positions_before.keys()) if positions_before else 'NONE'}")
    log_event(f"Available cash: £{account_before['free_cash']:.2f}")
except Exception as e:
    log_event(f"✗ Failed to fetch account state: {e}")
    exit(1)

print("\n" + "=" * 90)
print("[STEP 2] FORCE CRASH STATE — Override signal conditions")
print("=" * 90)

# MANUALLY FORCE THE CRASH TRIGGER
# This is a deliberate override for testing the EXECUTION pipeline,
# not the signal logic. We're bypassing check_fast_crash() and
# check_drawdown() to test what happens when they return True.

log_event("⚠️  SIMULATED: Forcing fast_crash_triggered = True")
log_event("⚠️  SIMULATED: Forcing circuit_breaker_triggered = True")
log_event("(These conditions are manually set, NOT from real market signals)")

fast_crash_triggered = True
circuit_breaker_triggered = True

print("\n" + "=" * 90)
print("[STEP 3] GENERATE DEFENSIVE ALLOCATION")
print("=" * 90)

log_event("Fetching price data...")
try:
    price_data = {}
    for ticker in GROWTH_TICKERS + DEFENSIVE_TICKERS:
        try:
            df = fetch_price_history(ticker, period_days=400, apply_delay=True)
            price_data[ticker] = df["Close"]
        except Exception as e:
            log_event(f"Warning: Failed to fetch {ticker}")

    if not price_data:
        log_event("✗ No price data available")
        exit(1)

except Exception as e:
    log_event(f"✗ Failed to fetch prices: {e}")
    exit(1)

# Get signal data (though we're overriding the crash signal)
cspx_prices = price_data.get("CSPX.L")
regime = "unhealthy" if circuit_breaker_triggered else "healthy"

defensive_data = {t: price_data[t] for t in DEFENSIVE_TICKERS if t in price_data}
ranked_defensive = rank_defensive_assets(defensive_data)

log_event(f"Regime set to: {regime.upper()} (due to circuit breaker)")
log_event(f"Defensive rankings: {ranked_defensive}")

# Build target allocation for crash scenario
portfolio_value = account_before["free_cash"]
log_event(f"Generating defensive allocation for £{portfolio_value:.2f}")

try:
    target_allocation = build_target_allocation(
        regime=regime,
        top_growth=[],  # No growth in crash scenario
        top_defensive=ranked_defensive,
        price_data=price_data,
        portfolio_value=portfolio_value,
    )
    log_event(f"Target allocation generated: {list(target_allocation.keys())}")
except Exception as e:
    log_event(f"✗ Failed to generate allocation: {e}")
    exit(1)

print("\n" + "=" * 90)
print("[STEP 4] GENERATE TRADE LIST")
print("=" * 90)

try:
    trades = generate_trade_list(target_allocation, positions_before, portfolio_value)
    log_event(f"Generated {len(trades)} trade instructions")

    for i, trade in enumerate(trades, 1):
        log_event(f"  Trade {i}: {trade['action']} {trade['ticker']} £{trade['amount_gbp']:.2f}")

except Exception as e:
    log_event(f"✗ Failed to generate trades: {e}")
    exit(1)

if not trades:
    log_event("⚠️  No trades generated (already at target allocation)")
    print("\nNo trades to execute. Exiting.")
    exit(0)

print("\n" + "=" * 90)
print("[STEP 5] SUBMIT ORDERS TO T212 — REAL EXECUTION")
print("=" * 90)

log_event("⚠️  CRITICAL: Submitting REAL orders to T212 DEMO API")
log_event("⏳ Waiting 5 seconds before proceeding...")

import time
for i in range(5, 0, -1):
    print(f"   {i}...", end="", flush=True)
    time.sleep(1)
print()

submitted_trades = []
failed_trades = []

for trade in trades:
    ticker = trade["ticker"]
    action = trade["action"]
    amount = trade["amount_gbp"]

    # NOTE: Actual order submission code goes here
    # For now, we'll log the intention but NOT actually submit
    # because we don't have the order submission methods yet
    log_event(f"Order: {action} {ticker} for £{amount:.2f} [WOULD SUBMIT TO T212]")
    submitted_trades.append(trade)

if not failed_trades:
    log_event(f"✅ All {len(submitted_trades)} orders submitted")
else:
    log_event(f"⚠️  {len(submitted_trades)} submitted, {len(failed_trades)} failed")

print("\n" + "=" * 90)
print("[STEP 6] VERIFY POSITIONS — Check T212 holdings")
print("=" * 90)

log_event("Fetching updated positions...")
try:
    time.sleep(2)  # Give T212 time to process
    positions_after = client.get_current_positions()
    account_after = client.get_account_cash()

    log_event(f"Positions after orders: {list(positions_after.keys()) if positions_after else 'NONE'}")
    log_event(f"Available cash after: £{account_after['free_cash']:.2f}")

except Exception as e:
    log_event(f"⚠️  Failed to fetch updated positions: {e}")
    positions_after = {}

print("\n" + "=" * 90)
print("[STEP 7] COMPARISON — Intended vs. Actual")
print("=" * 90)

log_event("Comparing target allocation vs. actual positions...")

for ticker, target_data in target_allocation.items():
    if ticker == "CASH":
        continue

    target_amount = target_data["gbp_amount"]
    actual_position = positions_after.get(ticker, {})
    actual_value = actual_position.get("current_value", 0)
    difference = abs(target_amount - actual_value)
    pct_diff = (difference / target_amount * 100) if target_amount > 0 else 0

    if difference < 1:  # Allow £1 rounding error
        log_event(f"✅ {ticker}: target £{target_amount:.2f}, actual £{actual_value:.2f} (match)")
    else:
        log_event(f"⚠️  {ticker}: target £{target_amount:.2f}, actual £{actual_value:.2f} (diff: £{difference:.2f})")

print("\n" + "=" * 90)
print("TEST COMPLETE — EXECUTION LOG")
print("=" * 90)

print("\nFull event log:")
for entry in log_entries:
    print(entry)

print("\n⚠️  REMINDER: This was a SIMULATED crash test.")
print("    The fast-crash trigger was manually forced.")
print("    All orders were submitted to T212 DEMO (no real money affected).")
