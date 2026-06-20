#!/usr/bin/env python3
"""Test suite for the signal engine."""

from src.data.price_fetcher import fetch_price_history
from src.signals.regime import check_regime, check_fast_crash
from src.signals.momentum import rank_growth_assets, rank_defensive_assets, select_top_growth
from src.signals.risk import check_drawdown, check_profit_taking

GROWTH_TICKERS = ["CSPX.L", "EQQQ.L", "VWRL.L", "VEUR.L"]
DEFENSIVE_TICKERS = ["SGLN.L", "IGLS.L"]

print("=" * 80)
print("SIGNAL ENGINE TEST SUITE")
print("=" * 80)

# Fetch real data for all tickers
print("\n[1/6] Fetching price data for all tickers...\n")

price_data = {}
for ticker in GROWTH_TICKERS + DEFENSIVE_TICKERS:
    try:
        df = fetch_price_history(ticker, period_days=400, apply_delay=True)
        price_data[ticker] = df["Close"]
        print(f"  ✓ {ticker}: {len(df)} trading days")
    except Exception as e:
        print(f"  ✗ {ticker}: {e}")

print("\n" + "=" * 80)
print("[2/6] REGIME DETECTION (CSPX.L)")
print("=" * 80)

cspx_prices = price_data.get("CSPX.L")
if cspx_prices is not None:
    regime = check_regime(cspx_prices)
    print(f"  Regime: {regime.upper()}")
    print(f"  Current price: £{cspx_prices.iloc[-1]:.2f}")

    from src.data.indicators import moving_average
    ma_200 = moving_average(cspx_prices, window=200)
    print(f"  200-Day MA: £{ma_200:.2f}")

else:
    print("  ✗ CSPX.L data not available")

print("\n" + "=" * 80)
print("[3/6] FAST CRASH DETECTION (CSPX.L)")
print("=" * 80)

if cspx_prices is not None:
    is_crash = check_fast_crash(cspx_prices)
    from src.data.indicators import rolling_return_window
    return_10d = rolling_return_window(cspx_prices, window=10)
    print(f"  10-Day Return: {return_10d * 100:+.2f}%")
    print(f"  Crash Threshold: -7.00%")
    print(f"  Status: {'⚠️  CRASH DETECTED' if is_crash else '✓ No crash'}")
else:
    print("  ✗ CSPX.L data not available")

print("\n" + "=" * 80)
print("[4/6] GROWTH ASSET MOMENTUM RANKING")
print("=" * 80)

growth_data = {ticker: price_data[ticker] for ticker in GROWTH_TICKERS if ticker in price_data}
if len(growth_data) > 0:
    ranked_growth = rank_growth_assets(growth_data)
    print("\n  12-Month Momentum Rankings:")
    for rank, (ticker, momentum) in enumerate(ranked_growth, 1):
        print(f"    {rank}. {ticker}: {momentum * 100:+.2f}%")

    print("\n" + "-" * 80)
    print("[4b/6] TOP 2 GROWTH ASSETS")
    print("-" * 80)

    top_growth = select_top_growth(ranked_growth, n=2)
    print(f"\n  Selected for portfolio: {', '.join(top_growth)}")
else:
    print("  ✗ Growth ticker data not available")

print("\n" + "=" * 80)
print("[5/6] DEFENSIVE ASSET MOMENTUM RANKING")
print("=" * 80)

defensive_data = {ticker: price_data[ticker] for ticker in DEFENSIVE_TICKERS if ticker in price_data}
if len(defensive_data) > 0:
    ranked_defensive = rank_defensive_assets(defensive_data)
    print("\n  3-Month Momentum Rankings:")
    for rank, (ticker, momentum) in enumerate(ranked_defensive, 1):
        print(f"    {rank}. {ticker}: {momentum * 100:+.2f}%")
else:
    print("  ✗ Defensive ticker data not available")

print("\n" + "=" * 80)
print("[6/6] RISK ASSESSMENT")
print("=" * 80)

# Test drawdown calculation
print("\n  [6a] Drawdown Detection")
print("  ---------------------")
portfolio_history = [5000, 5200, 5800, 5100, 4900]
drawdown = check_drawdown(portfolio_history)
print(f"  Portfolio history: {portfolio_history}")
print(f"  Peak value: £{max(portfolio_history):.2f}")
print(f"  Current value: £{portfolio_history[-1]:.2f}")
print(f"  Drawdown: {drawdown * 100:.2f}%")
print(f"  Expected: ~15.52% (calculated as (5800-4900)/5800)")

# Test profit taking
print("\n  [6b] Profit Taking Trigger")
print("  ----------------------------")

test_cases = [
    (140, 100, True, "40% gain, should trigger"),
    (120, 100, False, "20% gain, should NOT trigger"),
]

for current, last_rebalance, expected, description in test_cases:
    result = check_profit_taking(current, last_rebalance)
    gain = (current / last_rebalance - 1) * 100
    status = "✓" if result == expected else "✗"
    print(f"  {status} Current £{current}, Last £{last_rebalance}: {gain:+.1f}% → {result}")
    print(f"     ({description})")

print("\n" + "=" * 80)
print("SIGNAL ENGINE TEST COMPLETE")
print("=" * 80)
