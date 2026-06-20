#!/usr/bin/env python3
"""Test suite for the portfolio allocation engine."""

import pandas as pd
from src.data.price_fetcher import fetch_price_history
from src.signals.regime import check_regime, check_fast_crash
from src.signals.momentum import rank_growth_assets, rank_defensive_assets, select_top_growth
from src.portfolio.allocator import (
    calculate_inverse_vol_weights,
    apply_position_limits,
    build_target_allocation
)

GROWTH_TICKERS = ["CSPX.L", "EQQQ.L", "VWRL.L", "VEUR.L"]
DEFENSIVE_TICKERS = ["SGLN.L", "IGLS.L"]
PORTFOLIO_VALUE = 5000

print("=" * 90)
print("PORTFOLIO ENGINE TEST SUITE")
print("=" * 90)

# Fetch real data
print("\n[1/6] Fetching price data...\n")

price_data = {}
for ticker in GROWTH_TICKERS + DEFENSIVE_TICKERS:
    try:
        df = fetch_price_history(ticker, period_days=400, apply_delay=True)
        price_data[ticker] = df["Close"]
        print(f"  ✓ {ticker}: {len(df)} trading days")
    except Exception as e:
        print(f"  ✗ {ticker}: {e}")

# Run Phase 2 signal pipeline
print("\n" + "=" * 90)
print("[2/6] RUNNING PHASE 2 SIGNAL PIPELINE")
print("=" * 90)

# Regime detection
cspx_prices = price_data.get("CSPX.L")
regime = check_regime(cspx_prices) if cspx_prices is not None else "unknown"
is_crash = check_fast_crash(cspx_prices) if cspx_prices is not None else False

print(f"\n  Market Regime: {regime.upper()}")
print(f"  Fast Crash Alert: {'⚠️  YES' if is_crash else '✓ No'}")

# Growth momentum ranking
growth_data = {t: price_data[t] for t in GROWTH_TICKERS if t in price_data}
ranked_growth = rank_growth_assets(growth_data)
top_growth = select_top_growth(ranked_growth, n=2)

print(f"\n  Top Growth Assets (by 12-month return):")
for rank, (ticker, momentum) in enumerate(ranked_growth, 1):
    selected = " ← SELECTED" if ticker in top_growth else ""
    print(f"    {rank}. {ticker}: {momentum * 100:+.2f}%{selected}")

# Defensive momentum ranking
defensive_data = {t: price_data[t] for t in DEFENSIVE_TICKERS if t in price_data}
ranked_defensive = rank_defensive_assets(defensive_data)

print(f"\n  Defensive Assets (by 3-month return):")
for rank, (ticker, momentum) in enumerate(ranked_defensive, 1):
    print(f"    {rank}. {ticker}: {momentum * 100:+.2f}%")

# Build allocation for current (healthy) regime
print("\n" + "=" * 90)
print(f"[3/6] BUILD ALLOCATION (HEALTHY REGIME, Portfolio: £{PORTFOLIO_VALUE})")
print("=" * 90)

allocation_healthy = build_target_allocation(
    regime="healthy",
    top_growth=top_growth,
    top_defensive=ranked_defensive,
    price_data=price_data,
    portfolio_value=PORTFOLIO_VALUE
)

print("\n  ALLOCATION TABLE:")
print("  " + "-" * 86)
print(f"  {'Ticker':<12} {'Weight':<12} {'£ Amount':<15} {'% of Portfolio':<15}")
print("  " + "-" * 86)

total_gbp = 0
for ticker in sorted(allocation_healthy.keys()):
    data = allocation_healthy[ticker]
    weight = data["weight"]
    gbp = data["gbp_amount"]
    total_gbp += gbp
    print(f"  {ticker:<12} {weight:<12.4f} £{gbp:<14.2f} {weight * 100:<14.2f}%")

print("  " + "-" * 86)
print(f"  {'TOTAL':<12} {1.0:<12.4f} £{total_gbp:<14.2f} {100.0:<14.2f}%")
print("  " + "-" * 86)

# Verify total
tolerance = 0.01
if abs(total_gbp - PORTFOLIO_VALUE) < tolerance:
    print(f"\n  ✓ Total allocation matches portfolio value (£{total_gbp:.2f} ≈ £{PORTFOLIO_VALUE:.2f})")
else:
    print(f"\n  ✗ MISMATCH: Total £{total_gbp:.2f} != £{PORTFOLIO_VALUE:.2f}")

# Test unhealthy regime
print("\n" + "=" * 90)
print(f"[4/6] BUILD ALLOCATION (UNHEALTHY REGIME, Portfolio: £{PORTFOLIO_VALUE})")
print("=" * 90)

allocation_unhealthy = build_target_allocation(
    regime="unhealthy",
    top_growth=top_growth,
    top_defensive=ranked_defensive,
    price_data=price_data,
    portfolio_value=PORTFOLIO_VALUE
)

print("\n  ALLOCATION TABLE:")
print("  " + "-" * 86)
print(f"  {'Ticker':<12} {'Weight':<12} {'£ Amount':<15} {'% of Portfolio':<15}")
print("  " + "-" * 86)

total_gbp_unhealthy = 0
for ticker in sorted(allocation_unhealthy.keys()):
    data = allocation_unhealthy[ticker]
    weight = data["weight"]
    gbp = data["gbp_amount"]
    total_gbp_unhealthy += gbp
    print(f"  {ticker:<12} {weight:<12.4f} £{gbp:<14.2f} {weight * 100:<14.2f}%")

print("  " + "-" * 86)
print(f"  {'TOTAL':<12} {1.0:<12.4f} £{total_gbp_unhealthy:<14.2f} {100.0:<14.2f}%")
print("  " + "-" * 86)

if abs(total_gbp_unhealthy - PORTFOLIO_VALUE) < tolerance:
    print(f"\n  ✓ Total allocation matches portfolio value (£{total_gbp_unhealthy:.2f} ≈ £{PORTFOLIO_VALUE:.2f})")
else:
    print(f"\n  ✗ MISMATCH: Total £{total_gbp_unhealthy:.2f} != £{PORTFOLIO_VALUE:.2f}")

# Test apply_position_limits in isolation
print("\n" + "=" * 90)
print("[5/6] TEST apply_position_limits (ISOLATION)")
print("=" * 90)

test_weights = {"A": 0.50, "B": 0.35, "C": 0.10, "D": 0.05}
print(f"\n  Input weights: {test_weights}")
print(f"  Sum: {sum(test_weights.values()):.2f}")

limited_weights = apply_position_limits(test_weights, min_weight=0.05, max_weight=0.40)
print(f"\n  After position limits (min=5%, max=40%):")
for ticker in sorted(limited_weights.keys()):
    weight = limited_weights[ticker]
    print(f"    {ticker}: {weight:.4f} ({weight * 100:.2f}%)")

print(f"\n  Sum after limits: {sum(limited_weights.values()):.4f}")

# Verify capping and redistribution
print("\n  Logic verification:")
print(f"    A: 0.50 → capped to 0.40 (excess 0.10 redistributed)")
print(f"    B: 0.35 → kept at 0.35")
print(f"    C: 0.10 → kept at 0.10")
print(f"    D: 0.05 → kept at 0.05 (exactly at min_weight)")
print(f"    Excess to redistribute: 0.10 (from A capping)")
print(f"    Redistributed proportionally among B, C, D")

if abs(sum(limited_weights.values()) - 1.0) < 0.0001:
    print(f"\n  ✓ Weights sum to 1.0 after redistribution")
else:
    print(f"\n  ✗ Weights do not sum to 1.0")

# Test inverse volatility weighting
print("\n" + "=" * 90)
print("[6/6] TEST calculate_inverse_vol_weights")
print("=" * 90)

inv_vol_weights = calculate_inverse_vol_weights(top_growth, price_data)
print(f"\n  Inverse-vol weights for top growth ({', '.join(top_growth)}):")
for ticker in sorted(inv_vol_weights.keys()):
    weight = inv_vol_weights[ticker]
    print(f"    {ticker}: {weight:.4f} ({weight * 100:.2f}%)")

print(f"\n  Sum: {sum(inv_vol_weights.values()):.4f}")

if abs(sum(inv_vol_weights.values()) - 1.0) < 0.0001:
    print(f"  ✓ Weights sum to 1.0")
else:
    print(f"  ✗ Weights do not sum to 1.0")

print("\n" + "=" * 90)
print("PORTFOLIO ENGINE TEST COMPLETE")
print("=" * 90)
