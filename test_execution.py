#!/usr/bin/env python3
"""Test suite for the T212 execution layer."""

from src.execution.t212_client import T212Client
from src.execution.trade_generator import generate_trade_list
from src.data.price_fetcher import fetch_price_history
from src.signals.regime import check_regime
from src.signals.momentum import rank_growth_assets, rank_defensive_assets, select_top_growth
from src.portfolio.allocator import build_target_allocation

GROWTH_TICKERS = ["CSPX.L", "EQQQ.L", "VWRL.L", "VEUR.L"]
DEFENSIVE_TICKERS = ["SGLN.L", "IGLS.L"]

print("=" * 90)
print("T212 EXECUTION LAYER TEST SUITE")
print("=" * 90)

# Test 1: Initialize client and check environment
print("\n[1/5] INITIALIZE T212 CLIENT")
print("-" * 90)

try:
    client = T212Client()
except Exception as e:
    print(f"✗ FAILED TO INITIALIZE: {e}")
    print("  Check that ENVIRONMENT and T212_*_API_* variables are set in .env")
    exit(1)

# Test 2a: Get account cash
print("\n[2a/5] GET ACCOUNT CASH")
print("-" * 90)

try:
    cash_response = client.get_account_cash()
    print(f"  Raw JSON Response:")
    import json
    print(f"  {json.dumps(cash_response, indent=2)}")

    # Try to extract relevant fields
    free_cash = cash_response.get("free", cash_response.get("freeCash", 0))
    total_cash = cash_response.get("total", cash_response.get("totalCash", 0))

    print(f"\n  Free Cash: £{free_cash}")
    print(f"  Total Cash: £{total_cash}")
except Exception as e:
    print(f"✗ FAILED: {e}")
    exit(1)

# Test 2b: Get account info
print("\n[2b/5] GET ACCOUNT INFO")
print("-" * 90)

try:
    info_response = client.get_account_info()
    print(f"  Raw JSON Response:")
    import json
    print(f"  {json.dumps(info_response, indent=2)}")

    account_id = info_response.get("accountId", info_response.get("id"))
    currency = info_response.get("currency", "GBP")

    print(f"\n  Account ID: {account_id}")
    print(f"  Currency: {currency}")
except Exception as e:
    print(f"✗ FAILED: {e}")
    exit(1)

# Use the free cash as portfolio value for allocation
portfolio_value = free_cash if 'free_cash' in locals() else 5000

# Test 3: Get current positions
print("\n[3/5] GET CURRENT POSITIONS (from /equity/portfolio)")
print("-" * 90)

try:
    positions = client.get_current_positions()
    if not positions:
        print("  ✓ Account has no open positions (fresh account)")
    else:
        print(f"  Currently holding {len(positions)} asset(s):")
        for ticker, data in positions.items():
            print(f"    {ticker}: {data['quantity']} shares @ £{data['avg_price']:.2f} = £{data['current_value']:.2f}")
except Exception as e:
    print(f"✗ FAILED: {e}")
    exit(1)

# Test 4: Generate target allocation using real portfolio value
print("\n[4/6] GENERATE TARGET ALLOCATION (using actual account value)")
print("-" * 90)

try:
    # Fetch price data
    print("  Fetching price data for all tickers...")
    price_data = {}
    for ticker in GROWTH_TICKERS + DEFENSIVE_TICKERS:
        try:
            df = fetch_price_history(ticker, period_days=400, apply_delay=True)
            price_data[ticker] = df["Close"]
        except Exception as e:
            print(f"    Warning: Failed to fetch {ticker}: {e}")

    if not price_data:
        print("  ✗ Failed to fetch any price data")
        exit(1)

    # Run signal pipeline
    cspx_prices = price_data.get("CSPX.L")
    regime = check_regime(cspx_prices) if cspx_prices is not None else "unknown"

    growth_data = {t: price_data[t] for t in GROWTH_TICKERS if t in price_data}
    ranked_growth = rank_growth_assets(growth_data)
    top_growth = select_top_growth(ranked_growth, n=2)

    defensive_data = {t: price_data[t] for t in DEFENSIVE_TICKERS if t in price_data}
    ranked_defensive = rank_defensive_assets(defensive_data)

    # Build target allocation
    target_allocation = build_target_allocation(
        regime="healthy",
        top_growth=top_growth,
        top_defensive=ranked_defensive,
        price_data=price_data,
        portfolio_value=portfolio_value,
    )

    print(f"  Regime: {regime.upper()}")
    print(f"  Top Growth: {', '.join(top_growth)}")
    print(f"\n  Target Allocation for Portfolio Value £{portfolio_value:.2f}:")
    print(f"  {'Ticker':<12} {'Weight':<12} {'£ Target':<15}")
    print("  " + "-" * 39)
    for ticker in sorted(target_allocation.keys()):
        weight = target_allocation[ticker]["weight"]
        amount = target_allocation[ticker]["gbp_amount"]
        print(f"  {ticker:<12} {weight:<12.4f} £{amount:<14.2f}")

except Exception as e:
    print(f"  ✗ FAILED: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

# Test 5: Generate trade list
print("\n[5/6] GENERATE TRADE LIST")
print("-" * 90)

try:
    trades = generate_trade_list(target_allocation, positions, portfolio_value, min_trade_size=150)

    if not trades:
        print("  ✓ No trades needed (already at target allocation)")
    else:
        print(f"  Generated {len(trades)} trade instruction(s):")
        print(f"  {'#':<4} {'Ticker':<12} {'Action':<8} {'Amount (£)':<15}")
        print("  " + "-" * 39)

        total_buy = 0
        total_sell = 0

        for i, trade in enumerate(trades, 1):
            ticker = trade["ticker"]
            action = trade["action"]
            amount = trade["amount_gbp"]

            print(f"  {i:<4} {ticker:<12} {action:<8} £{amount:<14.2f}")

            if action == "BUY":
                total_buy += amount
            else:
                total_sell += amount

        print("  " + "-" * 39)
        print(f"  Total BUY amount: £{total_buy:.2f}")
        print(f"  Total SELL amount: £{total_sell:.2f}")
        print(f"  Net cash flow: £{total_buy - total_sell:.2f}")

except Exception as e:
    print(f"  ✗ FAILED: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

print("\n" + "=" * 90)
print("T212 EXECUTION LAYER TEST COMPLETE")
print("=" * 90)
print("\n⚠️  NOTE: No orders were placed. Trade list is for review only.")
print("    Order execution will be added in the next phase after review.")
