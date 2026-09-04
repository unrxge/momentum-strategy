"""
Strategy v2 — trend-filtered dual momentum, monthly, with a diversified defensive sleeve.

Pure functions only: no I/O, no broker calls, no scheduling.  The live pipeline (or a
backtest) supplies GBP price series and current positions; this module returns target
weights and a trade list.  Every rule here is a published, long-tested one:

  * 12-1 momentum (Jegadeesh & Titman 1993; Antonacci 2014 "dual momentum") for ranking
    equity ETFs and for the absolute-momentum test (hold only if the 12-1 return beats
    the cash proxy).
  * 10-month simple moving-average trend filter per asset (Faber 2007, "A Quantitative
    Approach to Tactical Asset Allocation"), applied to every held instrument.
  * Equal weight inside the equity sleeve (inverse-vol added nothing in the v1 backtest
    and doubled turnover).
  * Fixed strategic sleeve weights (equity / gold / gilts) with rebalance tolerance bands.
  * When an equity slot is switched off it goes to the strongest defensive asset by 6-month
    momentum, or to cash if none is positive (this is what handled 2022 in the backtest).

Signals are computed on GBP prices (the portfolio currency).  All parameters live in
StrategyParams; the defaults are the values validated in backtest_v2.py / STRATEGY_V2.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
import pandas as pd

CASH = "CASH"


@dataclass(frozen=True)
class StrategyParams:
    growth: tuple[str, ...] = ("CSPX.L", "EQQQ.L", "VWRL.L", "VEUR.L")
    gold: str = "SGLN.L"
    bonds: str = "IGLT.L"                           # bond sleeve: all-maturity gilts (trend-filtered); IGLS.L = short-gilt alternative
    cash_proxy: str = "IGLS.L"                      # return hurdle for absolute momentum
    extra_defensive: tuple[str, ...] = ()          # e.g. ("IGLT.L",) — must also be in price_data
    top_n: int = 2                                  # equity slots
    mom_lookback: int = 252                         # 12 months of trading days
    mom_skip: int = 21                              # skip the most recent month (12-1)
    trend_sma: int = 210                            # 10-month SMA (Faber)
    trend_band: float = 0.0                         # hysteresis: on above SMA*(1+b), off below SMA*(1-b)
    trend_filter: bool = True                       # Faber trend gate on every instrument
    abs_momentum: bool = False                      # Antonacci absolute-momentum gate; redundant with the trend gate (backtested: −0.1 Sharpe both windows)
    def_lookback: int = 126                         # 6-month momentum for defensive selection
    w_equity: float = 0.70
    w_gold: float = 0.15
    w_bonds: float = 0.15
    fallback: str = "split"                         # split (½ cash, ½ best defensive) | best_defensive | bonds | cash
    max_weight: float = 0.40                        # cap on any single instrument; excess goes to cash
    cash_buffer: float = 0.02                       # T212 over-reservation buffer (keep 2% uninvested)
    rebalance_band: float = 0.05                    # trade only if |target-current| > band of portfolio
    min_trade_gbp: float = 150.0
    vol_target: float | None = None                 # optional portfolio vol cap (annualised)
    vol_window: int = 60
    min_history: int = 260                          # rows needed before signals are trusted
    signal_currency: str = "mixed"                  # mixed (equities in fund base ccy USD, defensives in GBP) | underlying | gbp

    @property
    def defensives(self) -> tuple[str, ...]:
        return (self.gold, self.bonds) + tuple(self.extra_defensive)

    @property
    def all_tickers(self) -> tuple[str, ...]:
        extra = (self.cash_proxy,) if self.cash_proxy not in self.defensives else ()
        return tuple(self.growth) + self.defensives + extra


@dataclass
class Signals:
    date: object
    mom: dict[str, float]                 # 12-1 momentum per ticker
    def_mom: dict[str, float]             # 6-month momentum per ticker
    sma_ratio: dict[str, float]           # price / SMA - 1
    trend_on: dict[str, bool]             # after hysteresis
    abs_ok: dict[str, bool]               # 12-1 return > cash proxy 12-1 return
    cash_ret: float
    selected_equity: list[str]
    fallback_asset: str                   # where switched-off equity capital goes
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- signal series
USD_BASE_FUNDS = ("CSPX.L", "EQQQ.L", "VWRL.L", "VEUR.L", "SGLN.L")   # funds whose base currency is USD (VEUR's underlying is EUR; USD terms still removes GBP noise)


def signal_prices(close_gbp: dict[str, pd.Series], gbpusd: pd.Series | None, params: StrategyParams) -> dict[str, pd.Series]:
    """
    Build the price series the signals are computed on.

    mixed (default): equity ETFs are trend/momentum-tested in their fund base currency
      (GBP price × GBPUSD = USD NAV) so sterling moves don't create false equity exits or
      entries; gold and gilts are tested in GBP because their job is to protect GBP wealth.
    underlying: all five USD-base funds in USD.   gbp: everything in GBP.

    close_gbp: {ticker: GBP close series indexed by date}.  gbpusd: USD per GBP, daily.
    Series are returned with the same index as the input (the caller slices/tails them).
    """
    mode = params.signal_currency
    if mode == "gbp" or gbpusd is None:
        if mode != "gbp":
            import warnings
            warnings.warn("signal_prices: no GBPUSD series supplied — falling back to GBP signals")
        return {t: s.copy() for t, s in close_gbp.items()}
    convert = set(USD_BASE_FUNDS) if mode == "underlying" else set(params.growth)
    out = {}
    for t, s in close_gbp.items():
        if t in convert:
            fx = gbpusd.reindex(s.index.union(gbpusd.index)).ffill().reindex(s.index)
            out[t] = (s * fx).rename(t)
        else:
            out[t] = s.copy()
    return out


# --------------------------------------------------------------------------- indicators
def momentum_12_1(prices: pd.Series, lookback: int = 252, skip: int = 21) -> float:
    if len(prices) < lookback + 1:
        raise ValueError(f"need {lookback + 1} rows, got {len(prices)}")
    return float(prices.iloc[-1 - skip] / prices.iloc[-1 - lookback] - 1) if skip > 0 else float(prices.iloc[-1] / prices.iloc[-1 - lookback] - 1)


def trailing_return(prices: pd.Series, days: int) -> float:
    if len(prices) < days + 1:
        raise ValueError(f"need {days + 1} rows, got {len(prices)}")
    return float(prices.iloc[-1] / prices.iloc[-1 - days] - 1)


def sma_ratio(prices: pd.Series, window: int) -> float:
    if len(prices) < window:
        raise ValueError(f"need {window} rows, got {len(prices)}")
    return float(prices.iloc[-1] / prices.tail(window).mean() - 1)


def trend_state(ratio: float, prev_on: bool | None, band: float) -> bool:
    """Hysteresis around the SMA. With band=0 this is the plain Faber rule."""
    if ratio > band:
        return True
    if ratio < -band:
        return False
    return bool(prev_on) if prev_on is not None else ratio > 0


# --------------------------------------------------------------------------- signals
def compute_signals(price_data: dict[str, pd.Series], params: StrategyParams,
                    prev_trend: dict[str, bool] | None = None, date=None) -> Signals:
    """price_data: {ticker: GBP close series, oldest first, ending on the decision date's last close}."""
    p = params
    prev_trend = prev_trend or {}
    for t in p.all_tickers:
        if t not in price_data:
            raise ValueError(f"missing price series for {t}")
        if len(price_data[t]) < p.min_history:
            raise ValueError(f"{t}: only {len(price_data[t])} rows, need {p.min_history}")

    cash_ret = momentum_12_1(price_data[p.cash_proxy], p.mom_lookback, p.mom_skip)   # short gilts = cash proxy
    mom, dmom, ratio, trend, abs_ok = {}, {}, {}, {}, {}
    for t in p.all_tickers:
        s = price_data[t]
        mom[t] = momentum_12_1(s, p.mom_lookback, p.mom_skip)
        dmom[t] = trailing_return(s, p.def_lookback)
        ratio[t] = sma_ratio(s, p.trend_sma)
        trend[t] = trend_state(ratio[t], prev_trend.get(t), p.trend_band) if p.trend_filter else True
        abs_ok[t] = (mom[t] > cash_ret) if p.abs_momentum else True

    eligible = [t for t in p.growth if trend[t] and abs_ok[t]]
    ranked = sorted(eligible, key=lambda t: mom[t], reverse=True)
    selected = ranked[: p.top_n]

    # fallback for switched-off equity capital
    if p.fallback == "cash":
        fb = CASH
    elif p.fallback == "bonds":
        fb = p.bonds if trend[p.bonds] else CASH
    else:  # best_defensive / split: strongest 6m momentum among defensives that are in an uptrend and positive
        cands = [t for t in p.defensives if trend[t] and dmom[t] > 0]
        fb = max(cands, key=lambda t: dmom[t]) if cands else CASH
    return Signals(date, mom, dmom, ratio, trend, abs_ok, cash_ret, selected, fb)


# --------------------------------------------------------------------------- allocation
def target_weights(sig: Signals, params: StrategyParams, price_data: dict[str, pd.Series] | None = None) -> dict[str, float]:
    """Return {ticker: weight} incl. CASH, summing to 1.0 (cash_buffer already applied)."""
    p = params
    w: dict[str, float] = {t: 0.0 for t in p.all_tickers}
    w[CASH] = 0.0
    slot = p.w_equity / p.top_n
    for t in sig.selected_equity:
        w[t] += slot
    empty = p.top_n - len(sig.selected_equity)
    if empty:
        if p.fallback == "split" and sig.fallback_asset != CASH:
            w[sig.fallback_asset] += empty * slot / 2
            w[CASH] += empty * slot / 2
        else:
            w[sig.fallback_asset] += empty * slot
    # gold and bond sleeves: held when in uptrend, else to cash (bonds) / fallback logic
    w[p.gold if sig.trend_on[p.gold] else CASH] += p.w_gold
    w[p.bonds if sig.trend_on[p.bonds] else CASH] += p.w_bonds

    # single-instrument cap (concentration guard); excess to cash
    for t in list(w):
        if t != CASH and w[t] > p.max_weight:
            w[CASH] += w[t] - p.max_weight
            w[t] = p.max_weight
    if p.vol_target and price_data is not None:
        risky = [t for t, x in w.items() if t != CASH and x > 0]
        if risky:
            rets = pd.DataFrame({t: price_data[t].tail(p.vol_window + 1).pct_change() for t in risky}).dropna()
            wv = np.array([w[t] for t in risky])
            vol = float(np.sqrt(wv @ rets.cov().values @ wv) * np.sqrt(252))
            scale = min(1.0, p.vol_target / vol) if vol > 0 else 1.0
            if scale < 1.0:
                for t in risky:
                    w[CASH] += w[t] * (1 - scale)
                    w[t] *= scale
    # cash buffer for T212 reservation behaviour
    invested = 1.0 - w[CASH]
    if invested > 1.0 - p.cash_buffer:
        k = (1.0 - p.cash_buffer) / invested
        for t in w:
            if t != CASH:
                w[t] *= k
        w[CASH] = 1.0 - sum(x for t, x in w.items() if t != CASH)
    return {t: round(x, 6) for t, x in w.items() if x > 1e-9 or t == CASH}


# --------------------------------------------------------------------------- trades
def generate_trades(target: dict[str, float], positions: dict[str, dict], portfolio_value: float,
                    params: StrategyParams) -> list[dict]:
    """
    positions: {ticker: {"quantity": q, "current_value": v}} (GBP).
    Returns [{"ticker", "action", "amount_gbp", "quantity"|None, "reason"}].
    Full exits carry the exact held quantity so the executor never oversells.
    Re-weights inside the tolerance band are skipped (rebalance bands cut turnover
    with no return cost in the backtest).
    """
    p = params
    trades = []
    band_gbp = p.rebalance_band * portfolio_value
    for t, wt in target.items():
        if t == CASH:
            continue
        tgt = wt * portfolio_value
        cur = positions.get(t, {}).get("current_value", 0.0)
        diff = tgt - cur
        if cur == 0 and tgt > 0:
            if tgt >= p.min_trade_gbp:
                trades.append({"ticker": t, "action": "BUY", "amount_gbp": round(tgt, 2), "quantity": None, "reason": "entry"})
            continue
        if abs(diff) < max(band_gbp, p.min_trade_gbp):
            continue
        trades.append({"ticker": t, "action": "BUY" if diff > 0 else "SELL", "amount_gbp": round(abs(diff), 2),
                       "quantity": None, "reason": "rebalance"})
    for t, pos in positions.items():
        if t not in target or target.get(t, 0) == 0:
            if pos.get("current_value", 0) >= p.min_trade_gbp or pos.get("quantity", 0) > 0:
                trades.append({"ticker": t, "action": "SELL", "amount_gbp": round(pos.get("current_value", 0), 2),
                               "quantity": pos.get("quantity"), "reason": "exit"})
    # sells first so cash is available for buys
    trades.sort(key=lambda x: 0 if x["action"] == "SELL" else 1)
    return trades


def describe(sig: Signals, target: dict[str, float], params: StrategyParams) -> str:
    """Human-readable summary for the Telegram message / logs."""
    lines = [f"Decision date: {sig.date}", f"Cash-proxy 12-1 return: {sig.cash_ret:+.2%}", "Equity candidates (12-1 mom | trend | abs):"]
    for t in sorted(params.growth, key=lambda x: sig.mom[x], reverse=True):
        flag = "SELECTED" if t in sig.selected_equity else ""
        lines.append(f"  {t:8s} {sig.mom[t]:+7.2%} | {'ON ' if sig.trend_on[t] else 'off'} ({sig.sma_ratio[t]:+.1%} vs SMA) | {'ok' if sig.abs_ok[t] else 'FAIL'} {flag}")
    lines.append("Defensives (6m mom | trend):")
    for t in params.defensives:
        lines.append(f"  {t:8s} {sig.def_mom[t]:+7.2%} | {'ON ' if sig.trend_on[t] else 'off'}")
    lines.append(f"Fallback for switched-off equity capital: {sig.fallback_asset}")
    lines.append("Target weights: " + ", ".join(f"{t} {w:.1%}" for t, w in target.items()))
    return "\n".join(lines)
