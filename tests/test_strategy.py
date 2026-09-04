"""Unit tests for src/strategy.py — pure functions on synthetic data.  Run: python -m pytest -q"""
import numpy as np
import pandas as pd

from src.strategy import (StrategyParams, compute_signals, target_weights, generate_trades,
                          momentum_12_1, trend_state, CASH)

P = StrategyParams()
IDX = pd.bdate_range("2020-01-01", periods=400)


def series(drift: float, start: float = 100.0, noise: float = 0.0, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(start * np.cumprod(1 + drift + noise * rng.standard_normal(len(IDX))), index=IDX)


def universe(**overrides) -> dict:
    base = {k: series(0.0005, seed=i) for i, k in enumerate(P.all_keys)}
    base.update(overrides)
    return base


def test_momentum_12_1_skips_last_month():
    s = series(0.001)
    assert abs(momentum_12_1(s) - (s.iloc[-22] / s.iloc[-253] - 1)) < 1e-12
    assert abs(momentum_12_1(s, skip=0) - (s.iloc[-1] / s.iloc[-253] - 1)) < 1e-12


def test_trend_state():
    assert trend_state(0.05, None, 0.02) and not trend_state(-0.05, None, 0.02)
    assert trend_state(0.01, True, 0.02) and not trend_state(0.01, False, 0.02)
    assert trend_state(0.001, None, 0.0) and not trend_state(-0.001, None, 0.0)


def test_selection_and_weights():
    px = universe(SP500=series(0.002), NDX=series(0.0015), WORLD=series(-0.001), EUROPE=series(0.0))
    sig = compute_signals(px, P)
    assert sig.selected_equity == ["SP500", "NDX"]
    assert not sig.trend_on["WORLD"]
    w = target_weights(sig, P)
    assert abs(sum(w.values()) - 1.0) < 1e-6
    assert abs(w["SP500"] - 0.35 * 0.98) < 1e-6 and abs(w["GOLD"] - 0.15 * 0.98) < 1e-6
    assert w[CASH] >= P.cash_buffer - 1e-9


def test_all_equities_off_splits_to_best_defensive_and_cash_with_cap():
    px = universe(**{k: series(-0.002) for k in P.equities}, GOLD=series(0.002), GILTS=series(0.0005))
    sig = compute_signals(px, P)
    assert sig.selected_equity == [] and sig.fallback_asset == "GOLD"
    w = target_weights(sig, P)
    assert abs(w["GOLD"] - P.max_weight) < 1e-6          # 15% + 35% → capped at 40%
    assert w[CASH] > 0.4 and abs(sum(w.values()) - 1.0) < 1e-6


def test_defensive_off_goes_to_cash():
    px = universe(GOLD=series(-0.002), GILTS=series(-0.002))
    sig = compute_signals(px, P)
    w = target_weights(sig, P)
    assert w.get("GOLD", 0) == 0 and w.get("GILTS", 0) == 0 and w[CASH] >= 0.3 - 1e-9


def test_generate_trades_exits_bands_and_ordering():
    target = {"SP500": 0.35, "NDX": 0.35, "GILTS": 0.15, "GOLD": 0.13, CASH: 0.02}
    positions = {"SP500": {"quantity": 10.12, "value": 7100.0},    # 35.5% of 20k → inside band
                 "WORLD": {"quantity": 3.5, "value": 2000.0},       # not in target → exit with exact qty
                 "GOLD": {"quantity": 40.0, "value": 1200.0}}       # 6% → 13%: outside band → buy
    trades = generate_trades(target, positions, 20_000.0, P)
    by = {(t["key"], t["action"]): t for t in trades}
    assert by[("WORLD", "SELL")]["quantity"] == 3.5 and by[("WORLD", "SELL")]["reason"] == "exit"
    assert ("SP500", "SELL") not in by and ("SP500", "BUY") not in by
    assert by[("NDX", "BUY")]["reason"] == "entry" and ("GILTS", "BUY") in by and ("GOLD", "BUY") in by
    assert trades[0]["action"] == "SELL"


def test_insufficient_history_raises():
    px = {k: s.tail(100) for k, s in universe().items()}
    try:
        compute_signals(px, P)
        assert False
    except ValueError:
        pass


def test_no_trend_filter_holds_everything_ranked():
    px = universe(SP500=series(-0.002), NDX=series(-0.001))
    sig = compute_signals(px, StrategyParams(trend_filter=False, top_n=4))
    assert len(sig.selected_equity) == 4 and sig.selected_equity[-1] == "SP500"
