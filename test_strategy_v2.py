"""Unit tests for src/strategy/v2.py (pure functions, synthetic data). Run: venv/bin/python -m pytest test_strategy_v2.py -q  (or python test_strategy_v2.py)"""
import numpy as np
import pandas as pd
from src.strategy.v2 import (StrategyParams, compute_signals, target_weights, generate_trades, signal_prices,
                             momentum_12_1, trend_state, sma_ratio, CASH)

P = StrategyParams()
IDX = pd.bdate_range("2020-01-01", periods=400)


def series(drift_per_day: float, start: float = 100.0, noise: float = 0.0, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    r = drift_per_day + noise * rng.standard_normal(len(IDX))
    return pd.Series(start * np.cumprod(1 + r), index=IDX)


def universe(**overrides) -> dict:
    base = {t: series(0.0005, seed=i) for i, t in enumerate(P.all_tickers)}
    base.update(overrides)
    return base


def test_momentum_12_1_skips_last_month():
    s = series(0.001)
    full = s.iloc[-1] / s.iloc[-253] - 1
    skip = s.iloc[-22] / s.iloc[-253] - 1
    assert abs(momentum_12_1(s) - skip) < 1e-12 and abs(momentum_12_1(s, skip=0) - full) < 1e-12


def test_trend_state_hysteresis():
    assert trend_state(0.05, None, 0.02) is True and trend_state(-0.05, None, 0.02) is False
    assert trend_state(0.01, True, 0.02) is True and trend_state(0.01, False, 0.02) is False   # inside band keeps previous
    assert trend_state(0.001, None, 0.0) is True                                              # plain Faber rule with band 0


def test_selection_and_weights_sum_to_one():
    px = universe(**{"CSPX.L": series(0.002), "EQQQ.L": series(0.0015), "VWRL.L": series(-0.001), "VEUR.L": series(0.0)})
    sig = compute_signals(px, P)
    assert sig.selected_equity == ["CSPX.L", "EQQQ.L"]          # top-2 by 12-1 momentum among trend-on names
    assert "VWRL.L" not in sig.selected_equity                    # below its SMA → excluded
    w = target_weights(sig, P, px)
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert abs(w["CSPX.L"] - 0.35 * 0.98) < 1e-6 and w[CASH] >= P.cash_buffer - 1e-9


def test_switched_off_slots_split_between_cash_and_best_defensive():
    px = universe(**{t: series(-0.002) for t in P.growth}, **{"SGLN.L": series(0.002), "IGLT.L": series(0.0005)})
    sig = compute_signals(px, P)
    assert sig.selected_equity == [] and sig.fallback_asset == "SGLN.L"
    w = target_weights(sig, P, px)
    # 70% equity sleeve → 35% best defensive + 35% cash, plus 15% gold sleeve → gold 50% capped at 40%
    assert abs(w["SGLN.L"] - P.max_weight) < 1e-6
    assert w[CASH] > 0.4


def test_generate_trades_exits_carry_exact_quantity_and_bands_skip_small_rebalances():
    target = {"CSPX.L": 0.35, "EQQQ.L": 0.35, "IGLT.L": 0.15, "SGLN.L": 0.13, CASH: 0.02}
    positions = {"CSPX.L": {"quantity": 10.1234, "current_value": 7100.0},   # 35.5% of 20k → inside 5% band
                 "VWRL.L": {"quantity": 3.5, "current_value": 2000.0},       # not in target → full exit
                 "SGLN.L": {"quantity": 40.0, "current_value": 1200.0}}      # 6% → target 13% → 7pp > band → buy
    trades = generate_trades(target, positions, 20_000.0, P)
    by = {(t["ticker"], t["action"]): t for t in trades}
    assert ("VWRL.L", "SELL") in by and by[("VWRL.L", "SELL")]["quantity"] == 3.5 and by[("VWRL.L", "SELL")]["reason"] == "exit"
    assert ("CSPX.L", "SELL") not in by and ("CSPX.L", "BUY") not in by
    assert ("EQQQ.L", "BUY") in by and by[("EQQQ.L", "BUY")]["reason"] == "entry"
    assert ("SGLN.L", "BUY") in by
    assert trades[0]["action"] == "SELL"                                       # sells first


def test_signal_prices_mixed_converts_equities_only():
    px = universe()
    fx = pd.Series(1.30, index=IDX)
    sp = signal_prices(px, fx, P)
    assert np.allclose(sp["CSPX.L"].values, px["CSPX.L"].values * 1.30)
    assert np.allclose(sp["SGLN.L"].values, px["SGLN.L"].values)
    assert np.allclose(sp["IGLT.L"].values, px["IGLT.L"].values)


def test_insufficient_history_raises():
    px = {t: s.tail(100) for t, s in universe().items()}
    try:
        compute_signals(px, P)
        assert False, "expected ValueError"
    except ValueError:
        pass


if __name__ == "__main__":
    import sys
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as e:
                fails += 1; print("FAIL", name, repr(e))
    sys.exit(1 if fails else 0)
