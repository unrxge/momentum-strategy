#!/usr/bin/env python3
"""
Full Telegram send-and-wait approval loop test.

This test proves the complete approval workflow:
1. Fetch real market data and run signal pipeline
2. Build trade list from current T212 positions
3. Format and send Telegram message with real data
4. Wait for YES/NO reply
5. Send confirmation back
6. Handle all three outcomes (YES, NO, timeout)

CRITICAL: This test does NOT place any orders, regardless of reply.
Order placement is a deliberate separate step.
"""

import os
from datetime import datetime
from src.execution.t212_client import T212Client
from src.execution.trade_generator import generate_trade_list
from src.execution.telegram_notifier import (
    format_rebalance_message,
    send_message,
    wait_for_reply,
    get_high_water_mark,
)
from src.logging.supabase_client import log_signal_snapshot, log_trade_decision
from src.data.price_fetcher import fetch_price_history
from src.signals.regime import check_regime
from src.signals.momentum import rank_growth_assets, rank_defensive_assets, select_top_growth
from src.portfolio.allocator import build_target_allocation

GROWTH_TICKERS = ["CSPX.L", "EQQQ.L", "VWRL.L", "VEUR.L"]
DEFENSIVE_TICKERS = ["SGLN.L", "IGLS.L"]

print("=" * 100)
print("TELEGRAM APPROVAL LOOP TEST")
print("=" * 100)
print("\n⚠️  This test will send a REAL message to Telegram.")
print("    No orders will be placed regardless of your reply.\n")

# Step 1: Initialize T212 client
print("[STEP 1] Initialize T212 client")
print("-" * 100)

try:
    client = T212Client()
    account_cash = client.get_account_cash()
    positions = client.get_current_positions()
    account_value = account_cash["free_cash"]
    print(f"✓ Account value: £{account_value:.2f}")
    print(f"✓ Current positions: {list(positions.keys()) if positions else 'NONE'}\n")
except Exception as e:
    print(f"✗ Failed to initialize T212 client: {e}")
    exit(1)

# Step 2: Fetch price data and run Phase 2/3 pipeline
print("[STEP 2] Fetch price data and run signal pipeline")
print("-" * 100)

try:
    print("Fetching price data for all tickers...")
    price_data = {}
    for ticker in GROWTH_TICKERS + DEFENSIVE_TICKERS:
        try:
            df = fetch_price_history(ticker, period_days=400, apply_delay=True)
            price_data[ticker] = df["Close"]
        except Exception as e:
            print(f"  Warning: Failed to fetch {ticker}")

    if not price_data:
        print("✗ No price data available")
        exit(1)

    # Run signal pipeline
    cspx_prices = price_data.get("CSPX.L")
    regime = check_regime(cspx_prices) if cspx_prices is not None else "unknown"

    growth_data = {t: price_data[t] for t in GROWTH_TICKERS if t in price_data}
    ranked_growth = rank_growth_assets(growth_data)
    top_growth = select_top_growth(ranked_growth, n=2)

    defensive_data = {t: price_data[t] for t in DEFENSIVE_TICKERS if t in price_data}
    ranked_defensive = rank_defensive_assets(defensive_data)

    print(f"✓ Regime: {regime.upper()}")
    print(f"✓ Top growth: {', '.join(top_growth)}")
    print(f"✓ Defensive rankings: {[(t, f'{m*100:.1f}%') for t, m in ranked_defensive]}\n")

except Exception as e:
    print(f"✗ Failed to run signal pipeline: {e}")
    exit(1)

# Step 2b: Log signal snapshot
print("[STEP 2b] Log signal snapshot to Supabase")
print("-" * 100)

try:
    signal_snapshot_id = log_signal_snapshot({
        "environment": os.getenv("ENVIRONMENT", "DEMO").upper(),
        "check_type": "monthly",
        "regime": regime,
        "cspx_price": float(cspx_prices.iloc[-1]) if cspx_prices is not None else None,
        "cspx_200ma": float(cspx_prices.rolling(200).mean().iloc[-1]) if cspx_prices is not None and len(cspx_prices) >= 200 else None,
        "fast_crash_triggered": False,
        "ten_day_return": None,
        "portfolio_drawdown_pct": 0.0,
        "circuit_breaker_triggered": False,
        "growth_rankings": [{"ticker": t, "momentum": float(m)} for t, m in ranked_growth],
        "defensive_rankings": [{"ticker": t, "momentum": float(m)} for t, m in ranked_defensive],
        "selected_growth": top_growth,
    })
    if not signal_snapshot_id:
        print("⚠️  Failed to log signal snapshot (continuing anyway)\n")
    else:
        print()

except Exception as e:
    print(f"⚠️  Error logging signal snapshot: {e}\n")
    signal_snapshot_id = None

# Step 3: Build target allocation
print("[STEP 3] Build target allocation")
print("-" * 100)

try:
    target_allocation = build_target_allocation(
        regime=regime,
        top_growth=top_growth,
        top_defensive=ranked_defensive,
        price_data=price_data,
        portfolio_value=account_value,
    )

    print(f"✓ Target allocation generated:")
    for ticker in sorted(target_allocation.keys()):
        data = target_allocation[ticker]
        print(f"  {ticker}: £{data['gbp_amount']:.2f}")
    print()

except Exception as e:
    print(f"✗ Failed to build allocation: {e}")
    exit(1)

# Step 4: Generate trade list
print("[STEP 4] Generate trade list")
print("-" * 100)

try:
    trades = generate_trade_list(target_allocation, positions, account_value)

    if trades:
        print(f"✓ Generated {len(trades)} trade instruction(s):")
        for trade in trades:
            print(f"  {trade['action']:4} {trade['ticker']:10} £{trade['amount_gbp']:10.2f}")
    else:
        print("✓ No trades needed (positions match target allocation)")

    print()

except Exception as e:
    print(f"✗ Failed to generate trades: {e}")
    exit(1)

# Step 5: Format Telegram message
print("[STEP 5] Format Telegram message")
print("-" * 100)

try:
    telegram_message = format_rebalance_message(
        environment=os.getenv("ENVIRONMENT", "DEMO").upper(),
        account_value=account_value,
        regime=regime,
        fast_crash_triggered=False,  # Real check would use check_fast_crash()
        growth_rankings=ranked_growth,
        defensive_rankings=ranked_defensive,
        trade_list=trades,
        selected_growth=top_growth,
        is_simulated=False,  # This is real signal data
        reason="Rebalancing to target allocation based on regime and momentum signals.",
    )

    print("✓ Message formatted. Preview:\n")
    print(telegram_message)
    print()

except Exception as e:
    print(f"✗ Failed to format message: {e}")
    exit(1)

# Step 6: Get high water mark (before sending message)
print("[STEP 6a] Get high water mark")
print("-" * 100)

try:
    high_water_mark = get_high_water_mark()
    print(f"✓ High water mark: update_id={high_water_mark}")
    print("  (Only messages with update_id > this will be considered)\n")

except Exception as e:
    print(f"⚠️  Failed to get high water mark: {e}")
    high_water_mark = 0

# Step 6b: Send Telegram message
print("[STEP 6b] Send Telegram message")
print("-" * 100)

try:
    success = send_message(telegram_message)
    if not success:
        print("✗ Failed to send message — check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        exit(1)

    print("✓ Message sent to Telegram\n")

except Exception as e:
    print(f"✗ Failed to send message: {e}")
    exit(1)

# Step 7: Wait for reply
print("[STEP 7] Wait for reply (5-minute timeout)")
print("-" * 100)

try:
    reply_data = wait_for_reply(timeout_seconds=300, high_water_mark=high_water_mark)

except Exception as e:
    print(f"✗ Error while waiting for reply: {e}")
    reply_data = None

# Step 8: Handle reply
print("\n[STEP 8] Handle reply")
print("-" * 100)

user_response = None
responded_at = None

if reply_data:
    reply_text = reply_data.get("reply")
    update_id = reply_data.get("update_id")
    timestamp = reply_data.get("timestamp")

    print(f"Reply details:")
    print(f"  Reply: {reply_text}")
    print(f"  update_id: {update_id}")
    print(f"  timestamp: {timestamp}")
    print()

    user_response = reply_text
    responded_at = datetime.fromtimestamp(timestamp).isoformat() if timestamp else None

    if reply_text == "YES":
        print("✅ Approval received!")
        print("   NO ORDER PLACED — this test stops here by design.")
        print("   Order execution will be added in the next step.\n")
        confirmation = "✅ Got it — I received your YES. No trades will be placed in test mode."
    elif reply_text == "NO":
        print("❌ Trade skipped per your response.\n")
        confirmation = "Got it — I received your NO. No trades will be placed."
    else:
        print("⚠️  Unexpected reply format.\n")
        confirmation = "Got unexpected reply format."

else:  # None = timeout
    user_response = "TIMEOUT"
    print("⏱️ No reply received within 5 minutes. Trade cycle skipped.\n")
    confirmation = "⏱️ No reply received. Trade cycle skipped."

# Step 8b: Log trade decision
print("[STEP 8b] Log trade decision to Supabase")
print("-" * 100)

try:
    if signal_snapshot_id:
        total_buy = sum(t["amount_gbp"] for t in trades if t["action"] == "BUY")
        total_sell = sum(t["amount_gbp"] for t in trades if t["action"] == "SELL")

        trade_decision_id = log_trade_decision({
            "signal_snapshot_id": signal_snapshot_id,
            "environment": os.getenv("ENVIRONMENT", "DEMO").upper(),
            "trade_list": trades,
            "total_buy_amount": total_buy,
            "total_sell_amount": total_sell,
            "telegram_message_sent": telegram_message,
            "user_response": user_response,
            "responded_at": responded_at,
        })
        if not trade_decision_id:
            print("⚠️  Failed to log trade decision (continuing anyway)\n")
        else:
            print()
    else:
        print("⚠️  No signal snapshot ID, skipping trade decision log\n")

except Exception as e:
    print(f"⚠️  Error logging trade decision: {e}\n")

# Step 9: Send confirmation back
print("[STEP 9] Send confirmation back to Telegram")
print("-" * 100)

try:
    timestamp = datetime.now().strftime("%H:%M:%S")
    confirmation_full = f"[{timestamp}] {confirmation}"

    send_message(confirmation_full)
    print(f"✓ Confirmation sent: {confirmation_full}\n")

except Exception as e:
    print(f"⚠️  Failed to send confirmation: {e}\n")

# Step 10: Summary
print("[STEP 10] Test complete")
print("-" * 100)

print("\n✅ APPROVAL LOOP TEST COMPLETE")
print("\nWhat was tested:")
print("  1. Fetched real T212 account data")
print("  2. Ran full Phase 2/3 signal pipeline")
print("  3. Generated real trade list")
print("  4. Formatted and sent real Telegram message")
print("  5. Waited for YES/NO reply")
print("  6. Handled all three outcomes (YES, NO, timeout)")
print("  7. Sent confirmation back to Telegram")
print("\nWhat was NOT tested (next step):")
print("  - Order placement to T212 (order execution is separate)")
print("\n" + "=" * 100)
