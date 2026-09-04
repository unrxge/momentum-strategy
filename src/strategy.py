"""
Strategy: trend-filtered relative momentum with a diversified defensive sleeve.

Pure functions only — no I/O.  Rules (all published, decades old):

  * 12-1 relative momentum (Jegadeesh & Titman 1993; Antonacci 2014) ranks the equity
    instruments; the top `top_n` are held, equal weight, inside a fixed equity sleeve.
  * 10-month (210-day) simple-moving-average trend filter per instrument (Faber 2007):
    an instrument is held only while its signal series is above its SMA.
  * Fixed strategic sleeves: equity / gold / gilts.
  * Capital from a switched-off equity slot goes half to cash and half to the strongest
    defensive instrument by 6-month momentum (if in an uptrend and positive), else to cash.
  * Rebalance tolerance bands, a single-instrument cap, a small cash buffer for T212's
    order reservation behaviour.

Signals are computed on each instrument's `signal_ticker` series (see config.py): the
underlying index for equities, the GBP line itself for gold and gilts.  Validated in
backtest/ and documented in docs/STRATEGY.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
import pandas as pd

from src.config import EQUITY_KEYS, GOLD_KEY, BONDS_KEY

CASH = "CASH"


@dataclass(frozen=True)
class StrategyParams:
    equities: tuple[str, ...] = EQUITY_KEYS
    gold: str = GOLD_KEY
    bonds: str = BONDS_KEY
    top_n: int = 2
    mom_lookback: int = 252          # 12 months
    mom_skip: int = 21               # skip most recent month (12-1)
    trend_sma: int = 210             # 10-month SMA
    trend_band: float = 0.0          # hysteresis band; 0 = plain Faber rule (stateless)
    trend_filter: bool = True
    def_lookback: int = 126          # 6-month momentum for the defensive fallback choice
    w_equity: float = 0.70
    w_gold: float = 0.15
    w_bonds: float = 0.15
    fallback: str = "split"          # split | best_defensive | cash
    max_weight: float = 0.40
    cash_buffer: float = 0.02
    rebalance_band: float = 0.05
    min_trade_gbp: float = 50.0
    min_history: int = 260

    @property
    def defensives(self) -> tuple[str, ...]:
        return (self.gold, self.bonds)

    @property
    def all_keys(self) -> tuple[str, ...]:
        return tuple(self.equities) + self.defensives


@dataclass
class Signals:
    date: object
    mom: dict[str, float]
    def_mom: dict[str, float]
    sma_ratio: dict[str, float]
    trend_on: dict[str, bool]
    selected_equity: list[str]
    fallback_asset: str
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- indicators
def momentum_12_1(prices: pd.Series, lookback: int = 252, skip: int = 21) -> float:
    if len(prices) < lookback + 1:
        raise ValueError(f"need {lookback + 1} rows, got {len(prices)}")
    end = prices.iloc[-1 - skip] if skip > 0 else prices.iloc[-1]
    return float(end / prices.iloc[-1 - lookback] - 1)


def trailing_return(prices: pd.Series, days: int) -> float:
    if len(prices) < days + 1:
        raise ValueError(f"need {days + 1} rows, got {len(prices)}")
    return float(prices.iloc[-1] / prices.iloc[-1 - days] - 1)


def sma_ratio(prices: pd.Series, window: int) -> float:
    if len(prices) < window:
        raise ValueError(f"need {window} rows, got {len(prices)}")
    return float(prices.iloc[-1] / prices.tail(window).mean() - 1)


def trend_state(ratio: float, prev_on: bool | None, band: float) -> bool:
    if ratio > band:
        return True
    if ratio < -band:
        return False
    return bool(prev_on) if prev_on is not None else ratio > 0


# ---------------------------------------------------------------- signals
def compute_signals(signal_px: dict[str, pd.Series], params: StrategyParams,
                    prev_trend: dict[str, bool] | None = None, date=None) -> Signals:
    """signal_px: {key: close series (oldest first, completed bars only)} for every key in params.all_keys."""
    p = params
    prev_trend = prev_trend or {}
    for k in p.all_keys:
        if k not in signal_px:
            raise ValueError(f"missing signal series for {k}")
        if len(signal_px[k]) < p.min_history:
            raise ValueError(f"{k}: only {len(signal_px[k])} rows, need {p.min_history}")
    mom, dmom, ratio, trend = {}, {}, {}, {}
    for k in p.all_keys:
        s = signal_px[k]
        mom[k] = momentum_12_1(s, p.mom_lookback, p.mom_skip)
        dmom[k] = trailing_return(s, p.def_lookback)
        ratio[k] = sma_ratio(s, p.trend_sma)
        trend[k] = trend_state(ratio[k], prev_trend.get(k), p.trend_band) if p.trend_filter else True
    eligible = [k for k in p.equities if trend[k]]
    selected = sorted(eligible, key=lambda k: mom[k], reverse=True)[: p.top_n]
    if p.fallback == "cash":
        fb = CASH
    else:
        cands = [k for k in p.defensives if trend[k] and dmom[k] > 0]
        fb = max(cands, key=lambda k: dmom[k]) if cands else CASH
    return Signals(date, mom, dmom, ratio, trend, selected, fb)


# ---------------------------------------------------------------- allocation
def target_weights(sig: Signals, params: StrategyParams) -> dict[str, float]:
    """{key: weight} including CASH, summing to 1.0 with the cash buffer applied."""
    p = params
    w = {k: 0.0 for k in p.all_keys}
    w[CASH] = 0.0
    slot = p.w_equity / p.top_n
    for k in sig.selected_equity:
        w[k] += slot
    empty = p.top_n - len(sig.selected_equity)
    if empty:
        if p.fallback == "split" and sig.fallback_asset != CASH:
            w[sig.fallback_asset] += empty * slot / 2
            w[CASH] += empty * slot / 2
        else:
            w[sig.fallback_asset] += empty * slot
    w[p.gold if sig.trend_on[p.gold] else CASH] += p.w_gold
    w[p.bonds if sig.trend_on[p.bonds] else CASH] += p.w_bonds
    for k in list(w):                                   # concentration cap
        if k != CASH and w[k] > p.max_weight:
            w[CASH] += w[k] - p.max_weight
            w[k] = p.max_weight
    invested = 1.0 - w[CASH]                            # cash buffer
    if invested > 1.0 - p.cash_buffer:
        scale = (1.0 - p.cash_buffer) / invested
        for k in w:
            if k != CASH:
                w[k] *= scale
        w[CASH] = 1.0 - sum(v for k, v in w.items() if k != CASH)
    return {k: round(v, 6) for k, v in w.items() if v > 1e-9 or k == CASH}


# ---------------------------------------------------------------- trades
def generate_trades(target: dict[str, float], positions: dict[str, dict], portfolio_value: float,
                    params: StrategyParams) -> list[dict]:
    """
    positions: {key: {"quantity": q, "value": v}} in GBP.
    Returns sells first, then buys: {"key", "action", "amount_gbp", "quantity"|None, "reason"}.
    Full exits carry the exact held quantity.  Re-weights inside the tolerance band are skipped.
    """
    p = params
    trades = []
    band_gbp = p.rebalance_band * portfolio_value
    for k, wt in target.items():
        if k == CASH:
            continue
        tgt = wt * portfolio_value
        cur = positions.get(k, {}).get("value", 0.0)
        diff = tgt - cur
        if cur == 0 and tgt > 0:
            if tgt >= p.min_trade_gbp:
                trades.append({"key": k, "action": "BUY", "amount_gbp": round(tgt, 2), "quantity": None, "reason": "entry"})
            continue
        if abs(diff) < max(band_gbp, p.min_trade_gbp):
            continue
        trades.append({"key": k, "action": "BUY" if diff > 0 else "SELL", "amount_gbp": round(abs(diff), 2),
                       "quantity": None, "reason": "rebalance"})
    for k, pos in positions.items():
        if pos.get("quantity", 0) > 0 and target.get(k, 0) == 0:
            trades.append({"key": k, "action": "SELL", "amount_gbp": round(pos.get("value", 0.0), 2),
                           "quantity": pos["quantity"], "reason": "exit"})
    trades.sort(key=lambda t: 0 if t["action"] == "SELL" else 1)
    return trades


def describe(sig: Signals, target: dict[str, float], params: StrategyParams) -> str:
    lines = [f"Signal date: {sig.date}", "Equities (12-1 momentum | trend):"]
    for k in sorted(params.equities, key=lambda x: sig.mom[x], reverse=True):
        tag = " ← held" if k in sig.selected_equity else ""
        lines.append(f"  {k:7s} {sig.mom[k]:+6.1%} | {'ON ' if sig.trend_on[k] else 'off'} ({sig.sma_ratio[k]:+.1%} vs 10m SMA){tag}")
    lines.append("Defensives (6m momentum | trend):")
    for k in params.defensives:
        lines.append(f"  {k:7s} {sig.def_mom[k]:+6.1%} | {'ON ' if sig.trend_on[k] else 'off'} ({sig.sma_ratio[k]:+.1%})")
    if len(sig.selected_equity) < params.top_n:
        lines.append(f"Switched-off equity capital → {sig.fallback_asset}" + (" / cash" if params.fallback == "split" and sig.fallback_asset != CASH else ""))
    lines.append("Target: " + ", ".join(f"{k} {v:.0%}" for k, v in target.items() if v > 0.001))
    return "\n".join(lines)
