#!/usr/bin/env python3
"""
backtest.py — standalone historical replay of the live momentum strategy.

READ-ONLY with respect to the live code: this file imports and calls the real
signal / sizing / trade-generation / calendar functions from src/ and never
modifies them.  Parameter variants are produced by monkey-patching the indicator
functions that the live modules call (see `patched_params`), so the live code
path is still what executes.

Usage
-----
    python backtest.py                      # baseline only, writes backtest_output/
    python backtest.py --all                # baseline + every variant, sensitivity,
                                            #   walk-forward, bootstrap, proxy history
    python backtest.py --variants costs,crash --start 2016-01-01

Outputs (backtest_output/):
    summary.csv / summary.md      one row per variant with all metrics
    <variant>_equity.csv          daily equity, weights, regime, drawdown
    <variant>_orders.csv          every simulated order (date, ticker, side, £, px, units, cost, reason)
    <variant>_roundtrips.csv      position round-trips (entry→flat) with P&L
    <variant>_signals.csv         what the live functions returned on each decision date
    baseline_equity.png           equity curve vs benchmarks (needs matplotlib)
    walkforward.md, bootstrap.md, sensitivity.md, periods.md
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import sys
import time
import warnings
from dataclasses import dataclass, field, replace, asdict
from datetime import date, timedelta
from unittest import mock

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
for extra in os.environ.get("BACKTEST_PYLIB", "").split(os.pathsep):
    if extra:
        sys.path.append(extra)

# ---------------------------------------------------------------------------
# Live code under test (imported, never modified)
# ---------------------------------------------------------------------------
from src.data.indicators import moving_average, trailing_return, rolling_volatility, rolling_return_window  # noqa: E402
import src.signals.regime as regime_mod  # noqa: E402
import src.signals.momentum as momentum_mod  # noqa: E402
import src.portfolio.allocator as allocator_mod  # noqa: E402
from src.signals.regime import check_regime, check_fast_crash  # noqa: E402
from src.signals.momentum import rank_growth_assets, rank_defensive_assets, select_top_growth  # noqa: E402
from src.signals.risk import check_drawdown  # noqa: E402
from src.portfolio.allocator import build_target_allocation  # noqa: E402
from src.execution.trade_generator import generate_trade_list  # noqa: E402
from src.execution.t212_client import T212Client  # noqa: E402

try:
    # jobs.py imports supabase/telegram clients; they are import-safe without credentials.
    from src.scheduler.jobs import GROWTH_TICKERS, DEFENSIVE_TICKERS, get_nth_trading_day_of_month, is_trading_day  # noqa: E402
    LIVE_CALENDAR = True
except Exception as exc:  # pragma: no cover
    print(f"WARNING: could not import src.scheduler.jobs ({exc}); using fallback calendar")
    GROWTH_TICKERS = ["CSPX.L", "EQQQ.L", "VWRL.L", "VEUR.L"]
    DEFENSIVE_TICKERS = ["SGLN.L", "IGLS.L"]
    LIVE_CALENDAR = False

    def is_trading_day(d: date) -> bool:
        return d.weekday() < 5

    def get_nth_trading_day_of_month(year, month, n=5):
        d = date(year, month, 1)
        c = 0
        while True:
            if is_trading_day(d):
                c += 1
                if c == n:
                    return d
            d += timedelta(days=1)

ALL_TICKERS = GROWTH_TICKERS + DEFENSIVE_TICKERS
OUT_DIR = os.path.join(ROOT, "backtest_output")
CACHE_DIR = os.path.join(OUT_DIR, "cache")
# Live pipeline: fetch_price_history(period_days=400) -> ~275 trading rows
LIVE_HIST_ROWS = 275
# Quantity precision used by rebalance_executor (4 dp, SGLN 2 dp)
PRECISION = {"SGLN.L": 2}

# Yahoo quote currency per ticker (mirrors what yf.Ticker(t).info["currency"] returns; used to
# reproduce the live price_fetcher conversion: GBp/100, USD/current-rate).
YF_CURRENCY = {"CSPX.L": "USD", "EQQQ.L": "GBp", "VWRL.L": "GBP", "VEUR.L": "GBP", "SGLN.L": "GBp", "IGLS.L": "GBP"}
# Reference series used only to repair obviously broken Yahoo prints (USD/GBP unit mixing in 2010-14)
CLEAN_REF = {"CSPX.L": "SPY", "EQQQ.L": "QQQ", "VWRL.L": "VT", "VEUR.L": "VGK", "SGLN.L": "GLD"}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class Config:
    name: str = "baseline"
    start: str = "2014-06-01"
    end: str = "2026-09-04"
    initial_capital: float = 20_000.0
    universe: str = "live"            # "live" (LSE tickers) | "proxy" (US ETFs in GBP, 2006+)
    # --- timing ---
    fill: str = "next_close"          # signal on close t-1, fill at close t | next_open | same_close (look-ahead)
    rebalance_nth_day: int = 5        # live: 5th trading day (was 1st until 2026-09-03)
    hist_rows: int = LIVE_HIST_ROWS
    # --- live parameters (defaults == live code) ---
    ma_len: int = 200
    growth_lookback: int = 252
    def_lookback: int = 63
    vol_window: int = 20
    crash_window: int = 10
    crash_thr: float = -0.07
    top_n: int = 2
    min_trade_size: float = 150.0
    # --- what the live weekly job does NOT do (signal-only) but the design intends ---
    crash_trigger: bool = False       # act on check_fast_crash at weekly check
    circuit_breaker: bool = False     # act on portfolio drawdown > 15%
    circuit_breaker_thr: float = 0.15
    crash_reentry: str = "monthly"    # monthly: stay defensive until next monthly rebalance
    # --- signal currency ---
    signal_currency: str = "live"     # live: CSPX signals in USD (as price_fetcher does) | gbp
    # --- costs ---
    cost_bps: float = 0.0             # one-way spread+slippage in basis points of traded value
    # --- proposal variants (not in live code) ---
    momentum: str = "single"          # single | blend_3_6_12 | skip_month (12-1) | six_month
    regime_mode: str = "live"         # live | hysteresis | dual (MA and 12m>0) | per_asset
    hysteresis_band: float = 0.02
    vol_target: float | None = None   # e.g. 0.12 → scale risky sleeve to target realised vol
    vol_target_window: int = 60
    vol_estimator: str = "live"       # live (20d simple) | ewma
    weighting: str = "live"           # live (inverse vol) | erc | equal
    fix_least_negative: bool = False  # bug-fix: 'least negative' defensive picks the highest-momentum one
    unhealthy_growth: bool = True     # keep the 10%/25% growth sleeve in unhealthy regime
    event_driven: bool = False        # also rebalance at weekly check when regime flips
    equal_defensive_split: bool = False


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def _read_cache(t: str) -> pd.DataFrame | None:
    p = os.path.join(CACHE_DIR, f"{t.replace('=', '_').replace('^', '_')}.csv")
    if os.path.exists(p):
        df = pd.read_csv(p, index_col=0)
        df.index = pd.to_datetime(df.index.astype(str).str[:10])
        return df
    return None


def fetch_yf(t: str, refresh: bool = False) -> pd.DataFrame:
    """Full-history daily bars from yfinance (auto_adjust=True, same as live), cached on disk."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    if not refresh:
        c = _read_cache(t)
        if c is not None and len(c):
            return c
    import yfinance as yf
    for attempt in range(3):
        try:
            h = yf.Ticker(t).history(period="max", auto_adjust=True)
            if h.empty:
                raise ValueError("empty")
            h.index = pd.to_datetime(h.index.astype(str).str[:10])   # avoid tz→date shift
            h = h[~h.index.duplicated(keep="last")].sort_index()
            h.to_csv(os.path.join(CACHE_DIR, f"{t.replace('=', '_').replace('^', '_')}.csv"))
            time.sleep(0.6)
            return h
        except Exception as exc:
            if attempt == 2:
                raise
            time.sleep(2)


def repair_prints(close: pd.Series, ref_gbp: pd.Series, label: str, window: int = 63, tol: float = 0.20) -> tuple[pd.Series, int]:
    """Replace closes whose ratio to a reference GBP series deviates >tol from the rolling median ratio.
    Only fixes gross unit-mixing prints (Yahoo quoting the same ETF in USD one day, GBP the next)."""
    j = pd.concat([close.rename("x"), ref_gbp.rename("r")], axis=1).dropna()
    ratio = j.x / j.r
    med = ratio.rolling(window, min_periods=10, center=True).median()
    dev = (np.log(ratio) - np.log(med)).abs()
    bad = dev > tol
    fixed = close.copy()
    if bad.any():
        fixed.loc[bad[bad].index] = (med[bad] * j.r[bad]).values
    return fixed, int(bad.sum())


@dataclass
class Market:
    close_gbp: pd.DataFrame      # GBP prices for P&L / execution
    open_gbp: pd.DataFrame
    signal_px: pd.DataFrame      # what the live price_fetcher hands to the signal functions (scale only differs)
    notes: list[str] = field(default_factory=list)


def load_market(universe: str = "live", signal_currency: str = "live", refresh: bool = False) -> Market:
    fx = fetch_yf("GBPUSD=X", refresh)["Close"]
    fx = fx[~fx.index.duplicated()].sort_index()
    notes = []

    def usd_to_gbp(s: pd.Series) -> pd.Series:
        f = fx.reindex(s.index.union(fx.index)).ffill().reindex(s.index)
        return s / f

    closes, opens, sigs = {}, {}, {}
    if universe == "live":
        refs = {}
        for tk, ref in CLEAN_REF.items():
            r = fetch_yf(ref, refresh)["Close"]
            refs[tk] = usd_to_gbp(r)
        for tk in ALL_TICKERS:
            raw = fetch_yf(tk, refresh)
            cur = YF_CURRENCY[tk]
            c, o = raw["Close"].copy(), raw["Open"].copy()
            c = c[c > 0]
            o = o.reindex(c.index).fillna(c)
            if cur == "GBp":
                c, o = c / 100, o / 100
            if tk in refs:
                # reference is in GBP; compare in GBP
                c_gbp = usd_to_gbp(c) if cur == "USD" else c
                fixed, n = repair_prints(c_gbp, refs[tk], tk)
                if n:
                    notes.append(f"{tk}: repaired {n} broken Yahoo prints (unit mixing), last on {fixed.index[(fixed != c_gbp)].max().date()}")
                    scale = (fixed / c_gbp)
                    c = c * scale
                    o = o * scale
            if cur == "USD":
                sig = c.copy()                       # live: USD series / constant → scale-invariant ⇒ USD signal
                c_g, o_g = usd_to_gbp(c), usd_to_gbp(o)
            else:
                sig = c.copy()
                c_g, o_g = c, o
            closes[tk], opens[tk] = c_g, o_g
            sigs[tk] = sig if signal_currency == "live" else c_g
    elif universe == "proxy":
        # Longer history with US-listed proxies converted to GBP. Keys keep the live names because
        # build_target_allocation hard-codes "SGLN.L"/"IGLS.L".
        def px(t):
            d = fetch_yf(t, refresh)
            return d["Close"], d["Open"]

        spy_c, spy_o = px("SPY"); qqq_c, qqq_o = px("QQQ"); vgk_c, vgk_o = px("VGK"); gld_c, gld_o = px("GLD")
        vt_c, vt_o = px("VT"); efa_c, _ = px("EFA")
        # All-world proxy: VT where it exists, spliced backwards with 0.55 SPY + 0.45 EFA returns
        blend = (0.55 * spy_c.pct_change() + 0.45 * efa_c.reindex(spy_c.index).ffill().pct_change()).dropna()
        pre = blend[blend.index < vt_c.index[0]]
        chain = (1 + pre).cumprod()
        chain = chain / chain.iloc[-1] * vt_c.iloc[0] / (1 + blend.loc[vt_c.index[0]] if vt_c.index[0] in blend.index else 1)
        vt_full = pd.concat([chain[chain.index < vt_c.index[0]], vt_c]).sort_index()
        vt_o_full = vt_full.copy()
        vt_o_full.loc[vt_o.index] = vt_o
        # Short gilts proxy: IGLS.L (2009-04→), IGLT.L (2008-01→), SHY USD returns before that (no FX: bond-only)
        igls = fetch_yf("IGLS.L", refresh)["Close"]; iglt = fetch_yf("IGLT.L", refresh)["Close"]; shy = fetch_yf("SHY", refresh)["Close"]
        parts = []
        seg3 = shy[shy.index < iglt.index[0]].pct_change().dropna()
        seg2 = iglt[iglt.index < igls.index[0]].pct_change().dropna()
        seg2 = seg2[seg2.index > seg3.index[-1]]
        seg1 = igls.pct_change().dropna()
        seg1 = seg1[seg1.index > seg2.index[-1]]
        rets = pd.concat([seg3, seg2, seg1]).sort_index()
        gilts = (1 + rets).cumprod()
        gilts = gilts / gilts.loc[igls.index[0]:].iloc[0] * igls.iloc[0]
        mapping = {
            "CSPX.L": (usd_to_gbp(spy_c), usd_to_gbp(spy_o), spy_c),
            "EQQQ.L": (usd_to_gbp(qqq_c), usd_to_gbp(qqq_o), usd_to_gbp(qqq_c)),
            "VWRL.L": (usd_to_gbp(vt_full), usd_to_gbp(vt_o_full), usd_to_gbp(vt_full)),
            "VEUR.L": (usd_to_gbp(vgk_c), usd_to_gbp(vgk_o), usd_to_gbp(vgk_c)),
            "SGLN.L": (usd_to_gbp(gld_c), usd_to_gbp(gld_o), usd_to_gbp(gld_c)),
            "IGLS.L": (gilts, gilts, gilts),
        }
        for tk, (c, o, s) in mapping.items():
            closes[tk], opens[tk] = c, o
            sigs[tk] = s if signal_currency == "live" else c
        notes.append("proxy universe: SPY/QQQ/VGK/VT(+SPY-EFA splice)/GLD in GBP, gilts = IGLS.L←IGLT.L←SHY splice")
    else:
        raise ValueError(universe)

    close_df = pd.DataFrame(closes).sort_index()
    open_df = pd.DataFrame(opens).reindex(close_df.index)
    sig_df = pd.DataFrame(sigs).reindex(close_df.index)
    # Only keep dates where every instrument has ever started trading, then forward-fill gaps (stale-price risk noted)
    first_common = max(close_df[t].first_valid_index() for t in close_df)
    close_df = close_df.loc[first_common:].ffill()
    open_df = open_df.loc[first_common:].ffill().fillna(close_df)
    sig_df = sig_df.loc[first_common:].ffill()
    return Market(close_df, open_df, sig_df, notes)


# ---------------------------------------------------------------------------
# Parameter patching — keeps the *live* functions on the call path
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def patched_params(cfg: Config):
    patches = []
    if cfg.ma_len != 200:
        patches.append(mock.patch.object(regime_mod, "moving_average", lambda p, window=200: moving_average(p, cfg.ma_len)))
    if cfg.momentum != "single" or cfg.growth_lookback != 252 or cfg.def_lookback != 63:
        def _tr(p, days):
            if days == 63:            # defensive ranking path
                return trailing_return(p, cfg.def_lookback)
            # growth ranking path (live passes days=252)
            if cfg.momentum == "single":
                return trailing_return(p, cfg.growth_lookback)
            if cfg.momentum == "six_month":
                return trailing_return(p, 126)
            if cfg.momentum == "blend_3_6_12":
                return float(np.mean([trailing_return(p, 63), trailing_return(p, 126), trailing_return(p, 252)]))
            if cfg.momentum == "skip_month":  # 12-1: exclude the most recent ~21 trading days
                return float(p.iloc[-22] / p.iloc[-252] - 1)
            raise ValueError(cfg.momentum)
        patches.append(mock.patch.object(momentum_mod, "trailing_return", _tr))
    if cfg.vol_window != 20 or cfg.vol_estimator != "live":
        def _rv(p, window=20):
            if cfg.vol_estimator == "ewma":
                r = p.pct_change().dropna()
                return float(r.ewm(span=cfg.vol_window, min_periods=10).std().iloc[-1] * math.sqrt(252))
            return rolling_volatility(p, cfg.vol_window)
        patches.append(mock.patch.object(allocator_mod, "rolling_volatility", _rv))
    if cfg.crash_window != 10 or abs(cfg.crash_thr + 0.07) > 1e-12:
        # check_fast_crash compares rolling_return_window(prices, 10) < -0.07 (both literals).  Scaling the
        # return by 0.07/|thr| makes "scaled < -0.07" equivalent to "raw < thr" without touching the live file.
        k = 0.07 / abs(cfg.crash_thr)
        patches.append(mock.patch.object(regime_mod, "rolling_return_window", lambda p, window=10: rolling_return_window(p, cfg.crash_window) * k))
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
@dataclass
class Result:
    cfg: Config
    equity: pd.DataFrame
    orders: pd.DataFrame
    roundtrips: pd.DataFrame
    signals: pd.DataFrame
    events: dict
    notes: list[str]


def _erc_weights(rets: pd.DataFrame, iters: int = 500) -> dict[str, float]:
    cov = rets.cov().values * 252
    n = cov.shape[0]
    w = np.ones(n) / n
    for _ in range(iters):
        mrc = cov @ w
        rc = w * mrc
        target = rc.mean()
        w = w * (target / np.maximum(rc, 1e-12)) ** 0.5
        w = w / w.sum()
    return dict(zip(rets.columns, w))


class Backtester:
    def __init__(self, mkt: Market, cfg: Config):
        self.m = mkt
        self.cfg = cfg
        self.px = mkt.close_gbp
        self.opn = mkt.open_gbp
        self.sig = mkt.signal_px
        self.dates = self.px.index
        self.qty_calc = T212Client.calculate_order_quantity  # unbound; does not use self

    # ---- schedule ------------------------------------------------------------
    def _fill_date_on_or_after(self, d: date):
        pos = self.dates.searchsorted(pd.Timestamp(d))
        return self.dates[pos] if pos < len(self.dates) else None

    def _schedule(self, start: pd.Timestamp, end: pd.Timestamp):
        monthly, weekly = {}, {}
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            rd = get_nth_trading_day_of_month(y, m, self.cfg.rebalance_nth_day)
            fd = self._fill_date_on_or_after(rd)
            if fd is not None and start <= fd <= end:
                monthly[fd] = pd.Timestamp(rd)
            m += 1
            if m == 13:
                y, m = y + 1, 1
        d = start.date()
        while d <= end.date():
            if d.weekday() == 0:                         # live cron: every Monday 09:00 (holiday or not)
                fd = self._fill_date_on_or_after(d)
                if fd is not None and fd <= end:
                    weekly.setdefault(fd, pd.Timestamp(d))
            d += timedelta(days=1)
        return monthly, weekly

    # ---- signals -------------------------------------------------------------
    def _windows(self, sdate: pd.Timestamp) -> dict[str, pd.Series]:
        hist = self.sig.loc[:sdate].tail(self.cfg.hist_rows)
        return {t: hist[t].dropna().reset_index(drop=True) for t in hist.columns}

    def _regime(self, win: dict, prev_regime: str) -> str:
        cs = win["CSPX.L"]
        live = check_regime(cs)
        if self.cfg.regime_mode == "live" or self.cfg.regime_mode == "per_asset":
            return live
        if self.cfg.regime_mode == "hysteresis":
            ma = regime_mod.moving_average(cs, window=200)
            p = cs.iloc[-1]
            if p > ma * (1 + self.cfg.hysteresis_band):
                return "healthy"
            if p < ma * (1 - self.cfg.hysteresis_band):
                return "unhealthy"
            return prev_regime if prev_regime else live
        if self.cfg.regime_mode == "dual":
            return "healthy" if (live == "healthy" and trailing_return(cs, 252) > 0) else "unhealthy"
        raise ValueError(self.cfg.regime_mode)

    # ---- allocation ----------------------------------------------------------
    def _target(self, regime, top_growth, ranked_def, win, pv, sdate) -> dict:
        cfg = self.cfg
        if cfg.weighting == "live" or not top_growth or regime != "healthy":
            target = build_target_allocation(regime, top_growth, ranked_def, win, pv)
        else:
            # Proposal: replace inverse-vol growth weights with ERC / equal (keeps 80/15/5 skeleton + 98% buffer)
            target = build_target_allocation(regime, top_growth, ranked_def, win, pv)
            gsum = sum(v["weight"] for k, v in target.items() if k in top_growth)
            if cfg.weighting == "equal":
                w = {t: 1 / len(top_growth) for t in top_growth}
            else:
                rets = self.px.loc[:sdate, top_growth].tail(cfg.vol_target_window).pct_change().dropna()
                w = _erc_weights(rets)
            for t in top_growth:
                target[t] = {"weight": gsum * w[t], "gbp_amount": gsum * w[t] * pv}
        if cfg.fix_least_negative and regime == "unhealthy" and ranked_def and all(s < 0 for _, s in ranked_def):
            worst, best = ranked_def[-1][0], ranked_def[0][0]
            if worst != best and worst in target and best not in target:
                target[best] = target.pop(worst)
        if not cfg.unhealthy_growth and regime == "unhealthy":
            for t in list(target):
                if t in GROWTH_TICKERS:
                    target.pop(t)
        if cfg.vol_target and regime == "healthy":
            risky = [t for t in target if t not in ("CASH", "IGLS.L")]
            w = pd.Series({t: target[t]["weight"] for t in risky})
            rets = self.px.loc[:sdate, risky].tail(cfg.vol_target_window).pct_change().dropna()
            port_vol = float(np.sqrt(w.values @ rets.cov().values @ w.values) * np.sqrt(252))
            scale = min(1.0, cfg.vol_target / port_vol) if port_vol > 0 else 1.0
            for t in risky:
                target[t] = {"weight": target[t]["weight"] * scale, "gbp_amount": target[t]["gbp_amount"] * scale}
        return target

    # ---- main loop -----------------------------------------------------------
    def run(self) -> Result:
        cfg = self.cfg
        start = max(pd.Timestamp(cfg.start), self.dates[cfg.hist_rows + 1])
        end = min(pd.Timestamp(cfg.end), self.dates[-1])
        monthly, weekly = self._schedule(start, end)
        days = self.dates[(self.dates >= start) & (self.dates <= end)]

        units = {t: 0.0 for t in ALL_TICKERS}
        avg_cost = {t: 0.0 for t in ALL_TICKERS}
        trip = {t: None for t in ALL_TICKERS}
        cash = cfg.initial_capital
        crash_mode = False
        prev_regime = None
        peak = cfg.initial_capital
        eq_hist = []
        orders, roundtrips, signals = [], [], []
        events = {"cash_shortfall": 0, "oversell_clipped": 0, "crash_triggers": 0, "cb_triggers": 0,
                  "monthly_rebalances": 0, "crash_rebalances": 0, "event_rebalances": 0, "no_trade_months": 0}
        eq_rows = []

        def value_at(prices):
            return cash + sum(units[t] * prices[t] for t in ALL_TICKERS)

        def execute(t, kind, sdate, regime, top_growth, ranked_def, win, fill_px):
            nonlocal cash
            pv = value_at(fill_px)
            target = self._target(regime, top_growth, ranked_def, win, pv, sdate)
            positions = {tk: {"quantity": units[tk], "current_value": units[tk] * fill_px[tk], "avg_price": avg_cost[tk]}
                         for tk in ALL_TICKERS if units[tk] > 0}
            trades = generate_trade_list(target, positions, pv, cfg.min_trade_size)
            sells = [x for x in trades if x["action"] == "SELL"]
            buys = [x for x in trades if x["action"] == "BUY"]
            fee = cfg.cost_bps / 1e4
            for tr in sells:
                tk, px = tr["ticker"], fill_px[tr["ticker"]]
                q = self.qty_calc(None, tr["amount_gbp"], px, PRECISION.get(tk, 4))
                if q > units[tk] + 1e-9:
                    events["oversell_clipped"] += 1
                    q = units[tk]
                if q <= 0:
                    continue
                proceeds = q * px * (1 - fee)
                pnl = q * (px * (1 - fee) - avg_cost[tk])
                cash += proceeds
                units[tk] -= q
                orders.append(dict(date=t.date(), signal_date=sdate.date(), ticker=tk, side="SELL", amount_gbp=round(q * px, 2),
                                   price=round(px, 4), units=q, cost_gbp=round(q * px * fee, 2), realized_pnl=round(pnl, 2),
                                   realized_pct=round((px * (1 - fee) / avg_cost[tk] - 1) if avg_cost[tk] else 0, 4),
                                   reason=kind, regime=regime))
                tp = trip[tk]
                if tp is not None:
                    tp["proceeds"] += proceeds
                    if units[tk] < 1e-9:
                        units[tk] = 0.0
                        roundtrips.append(dict(ticker=tk, entry=tp["entry"], exit=t.date(), days=(t.date() - tp["entry"]).days,
                                               cost_basis=round(tp["cost"], 2), proceeds=round(tp["proceeds"], 2),
                                               pnl=round(tp["proceeds"] - tp["cost"], 2), ret=round(tp["proceeds"] / tp["cost"] - 1, 4),
                                               entry_reason=tp["reason"], exit_reason=kind, open=False))
                        trip[tk] = None
                        avg_cost[tk] = 0.0
            need = sum(b["amount_gbp"] for b in buys) * (1 + fee)
            scale = 1.0
            if need > cash and need > 0:
                events["cash_shortfall"] += 1
                scale = cash / need * 0.999
            for tr in buys:
                tk, px = tr["ticker"], fill_px[tr["ticker"]]
                amt = tr["amount_gbp"] * scale
                q = self.qty_calc(None, amt, px, PRECISION.get(tk, 4))
                if q <= 0:
                    continue
                total_cost = q * px * (1 + fee)
                if total_cost > cash:
                    q = self.qty_calc(None, cash / (1 + fee) * 0.999, px, PRECISION.get(tk, 4))
                    total_cost = q * px * (1 + fee)
                if q <= 0:
                    continue
                avg_cost[tk] = (avg_cost[tk] * units[tk] + total_cost) / (units[tk] + q)
                units[tk] += q
                cash -= total_cost
                if trip[tk] is None:
                    trip[tk] = {"entry": t.date(), "cost": 0.0, "proceeds": 0.0, "reason": kind}
                trip[tk]["cost"] += total_cost
                orders.append(dict(date=t.date(), signal_date=sdate.date(), ticker=tk, side="BUY", amount_gbp=round(q * px, 2),
                                   price=round(px, 4), units=q, cost_gbp=round(q * px * fee, 2), realized_pnl=0.0, realized_pct=0.0,
                                   reason=kind, regime=regime))
            return target, trades

        for t in days:
            i = self.dates.get_loc(t)
            if cfg.fill == "same_close":
                sdate, fill_px = t, self.px.loc[t]
            elif cfg.fill == "next_open":
                sdate, fill_px = self.dates[i - 1], self.opn.loc[t]
            else:
                sdate, fill_px = self.dates[i - 1], self.px.loc[t]

            did = None
            win = None
            if t in monthly or t in weekly:
                win = self._windows(sdate)
                growth_win = {k: win[k] for k in GROWTH_TICKERS}
                if cfg.regime_mode == "per_asset":
                    growth_win = {k: v for k, v in growth_win.items() if v.iloc[-1] > regime_mod.moving_average(v, window=200)}
                ranked_growth = rank_growth_assets(growth_win) if growth_win else []
                top_growth = select_top_growth(ranked_growth, n=cfg.top_n)
                ranked_def = rank_defensive_assets({k: win[k] for k in DEFENSIVE_TICKERS})
                regime = self._regime(win, prev_regime)
                fast_crash = check_fast_crash(win["CSPX.L"])
                dd = check_drawdown(eq_hist[-100:]) if eq_hist else 0.0
                cb = dd > cfg.circuit_breaker_thr
                signals.append(dict(date=t.date(), signal_date=sdate.date(), check="monthly" if t in monthly else "weekly",
                                    regime=regime, fast_crash=fast_crash, drawdown=round(dd, 4), circuit_breaker=cb,
                                    growth_rank=json.dumps([(k, round(v, 4)) for k, v in ranked_growth]),
                                    defensive_rank=json.dumps([(k, round(v, 4)) for k, v in ranked_def]),
                                    top_growth=",".join(top_growth), crash_mode=crash_mode))

                if t in monthly:
                    events["monthly_rebalances"] += 1
                    crash_mode = False
                    target, trades = execute(t, "monthly", sdate, regime, top_growth, ranked_def, win, fill_px)
                    did = "monthly"
                    if not trades:
                        events["no_trade_months"] += 1
                else:
                    trig = (cfg.crash_trigger and fast_crash) or (cfg.circuit_breaker and cb)
                    if trig:
                        events["crash_triggers" if fast_crash else "cb_triggers"] += 1
                    if trig and not crash_mode:
                        crash_mode = True
                        events["crash_rebalances"] += 1
                        # Intended action (test_crash_simulation.py): regime forced unhealthy, no growth sleeve
                        target, trades = execute(t, "crash", sdate, "unhealthy", [], ranked_def, win, fill_px)
                        did = "crash"
                    elif cfg.event_driven and prev_regime is not None and regime != prev_regime and not crash_mode:
                        events["event_rebalances"] += 1
                        target, trades = execute(t, "regime_flip", sdate, regime, top_growth, ranked_def, win, fill_px)
                        did = "regime_flip"
                prev_regime = regime

            close_px = self.px.loc[t]
            eq = value_at(close_px)
            eq_hist.append(eq)
            peak = max(peak, eq)
            row = {"date": t, "equity": eq, "cash": cash, "drawdown": eq / peak - 1, "action": did or "",
                   "regime": prev_regime or "", "crash_mode": crash_mode}
            for tk in ALL_TICKERS:
                row[f"w_{tk}"] = units[tk] * close_px[tk] / eq if eq else 0
            eq_rows.append(row)

        # open trips marked to market
        last = self.px.loc[:end].iloc[-1]
        for tk, tp in trip.items():
            if tp is not None and units[tk] > 0:
                mv = units[tk] * last[tk]
                roundtrips.append(dict(ticker=tk, entry=tp["entry"], exit=end.date(), days=(end.date() - tp["entry"]).days,
                                       cost_basis=round(tp["cost"], 2), proceeds=round(tp["proceeds"] + mv, 2),
                                       pnl=round(tp["proceeds"] + mv - tp["cost"], 2), ret=round((tp["proceeds"] + mv) / tp["cost"] - 1, 4),
                                       entry_reason=tp["reason"], exit_reason="open", open=True))
        equity = pd.DataFrame(eq_rows).set_index("date")
        return Result(cfg, equity, pd.DataFrame(orders), pd.DataFrame(roundtrips), pd.DataFrame(signals), events, list(self.m.notes))


def run_config(mkt_cache: dict, cfg: Config, refresh=False) -> Result:
    key = (cfg.universe, cfg.signal_currency)
    if key not in mkt_cache:
        mkt_cache[key] = load_market(cfg.universe, cfg.signal_currency, refresh)
    with patched_params(cfg):
        return Backtester(mkt_cache[key], cfg).run()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def metrics(eq: pd.Series, orders: pd.DataFrame | None = None, roundtrips: pd.DataFrame | None = None,
            weights: pd.DataFrame | None = None) -> dict:
    eq = eq.dropna()
    r = eq.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    vol = r.std() * math.sqrt(252)
    sharpe = r.mean() / r.std() * math.sqrt(252) if r.std() > 0 else np.nan
    dn = r[r < 0]
    sortino = r.mean() / (np.sqrt((np.minimum(r, 0) ** 2).mean()) + 1e-12) * math.sqrt(252)
    dd = eq / eq.cummax() - 1
    mdd = dd.min()
    mdd_end = dd.idxmin()
    mdd_start = eq.loc[:mdd_end].idxmax()
    rec = dd.loc[mdd_end:]
    rec_date = rec[rec >= 0].index[0] if (rec >= 0).any() else None
    calmar = cagr / abs(mdd) if mdd < 0 else np.nan
    mth = eq.resample("ME").last().pct_change().dropna()
    out = dict(start=str(eq.index[0].date()), end=str(eq.index[-1].date()), years=round(yrs, 2),
               final_equity=round(eq.iloc[-1], 0), total_return=round(eq.iloc[-1] / eq.iloc[0] - 1, 4),
               cagr=round(cagr, 4), ann_vol=round(vol, 4), sharpe=round(sharpe, 3), sortino=round(sortino, 3),
               max_dd=round(mdd, 4), max_dd_start=str(mdd_start.date()), max_dd_trough=str(mdd_end.date()),
               max_dd_recovered=str(rec_date.date()) if rec_date is not None else "not yet",
               max_dd_days=int((mdd_end - mdd_start).days), calmar=round(calmar, 3),
               monthly_win_rate=round((mth > 0).mean(), 4), best_month=round(mth.max(), 4), worst_month=round(mth.min(), 4))
    if weights is not None:
        inv = weights.sum(axis=1)
        out["exposure_avg"] = round(inv.mean(), 4)
        out["exposure_days_pct"] = round((inv > 0.01).mean(), 4)
    if orders is not None and len(orders):
        sells = orders[orders.side == "SELL"]
        out["orders"] = int(len(orders))
        out["turnover_ann"] = round(orders.amount_gbp.sum() / eq.mean() / yrs, 3)
        out["costs_gbp"] = round(orders.cost_gbp.sum(), 2)
        out["sell_win_rate"] = round((sells.realized_pct > 0).mean(), 4) if len(sells) else np.nan
        out["avg_sell_pct"] = round(sells.realized_pct.mean(), 4) if len(sells) else np.nan
    else:
        out["orders"] = 0
    if roundtrips is not None and len(roundtrips):
        closed = roundtrips[~roundtrips.open]
        out["roundtrips"] = int(len(roundtrips))
        out["rt_win_rate"] = round((closed.ret > 0).mean(), 4) if len(closed) else np.nan
        out["rt_avg_ret"] = round(closed.ret.mean(), 4) if len(closed) else np.nan
        out["rt_avg_pnl_gbp"] = round(closed.pnl.mean(), 2) if len(closed) else np.nan
        gp, gl = closed.pnl[closed.pnl > 0].sum(), -closed.pnl[closed.pnl < 0].sum()
        out["rt_profit_factor"] = round(gp / gl, 2) if gl > 0 else np.inf
        out["rt_avg_days"] = round(closed.days.mean(), 0) if len(closed) else np.nan
    return out


def bench_equity(mkt: Market, start, end, initial=20000.0) -> dict[str, pd.Series]:
    px = mkt.close_gbp.loc[start:end]
    out = {}
    for tk in ["CSPX.L", "VWRL.L", "SGLN.L", "IGLS.L"]:
        out[f"BH_{tk}"] = px[tk] / px[tk].iloc[0] * initial
    # static 80/15/5 (VWRL/SGLN/IGLS) monthly-rebalanced — what the healthy-regime skeleton holds with no signals
    r = px.pct_change().fillna(0)
    w = pd.Series({"VWRL.L": 0.80, "SGLN.L": 0.15, "IGLS.L": 0.05})
    eq, cur = [initial], w * initial
    for i in range(1, len(px)):
        cur = cur * (1 + r.iloc[i][w.index])
        tot = cur.sum()
        if px.index[i].month != px.index[i - 1].month:
            cur = w * tot
        eq.append(tot)
    out["STATIC_80_15_5"] = pd.Series(eq, index=px.index)
    w = pd.Series({"CSPX.L": 0.25, "EQQQ.L": 0.25, "VWRL.L": 0.25, "VEUR.L": 0.25})
    eq, cur = [initial], w * initial
    for i in range(1, len(px)):
        cur = cur * (1 + r.iloc[i][w.index])
        tot = cur.sum()
        if px.index[i].month != px.index[i - 1].month:
            cur = w * tot
        eq.append(tot)
    out["EQUAL_WEIGHT_GROWTH"] = pd.Series(eq, index=px.index)
    return out


# ---------------------------------------------------------------------------
# Analyses
# ---------------------------------------------------------------------------
def period_table(eq: pd.Series, bench: dict[str, pd.Series], periods: list[tuple[str, str, str]]) -> pd.DataFrame:
    rows = []
    for name, a, b in periods:
        s = eq.loc[a:b]
        if len(s) < 5:
            continue
        row = {"period": name, "from": a, "to": b, "strategy": round(s.iloc[-1] / s.iloc[0] - 1, 4),
               "strategy_maxdd": round((s / s.cummax() - 1).min(), 4)}
        for k, v in bench.items():
            vv = v.loc[a:b]
            if len(vv):
                row[k] = round(vv.iloc[-1] / vv.iloc[0] - 1, 4)
        rows.append(row)
    return pd.DataFrame(rows)


def block_bootstrap_sharpe(monthly_returns: pd.Series, n=5000, block=6, seed=7) -> dict:
    rng = np.random.default_rng(seed)
    x = monthly_returns.values
    L = len(x)
    sharpes, cagrs, mdds = [], [], []
    for _ in range(n):
        idx = []
        while len(idx) < L:
            s = rng.integers(0, L)
            b = rng.geometric(1 / block)
            idx.extend([(s + k) % L for k in range(b)])
        smp = x[idx[:L]]
        sharpes.append(smp.mean() / smp.std() * math.sqrt(12) if smp.std() > 0 else 0)
        eq = np.cumprod(1 + smp)
        cagrs.append(eq[-1] ** (12 / L) - 1)
        mdds.append((eq / np.maximum.accumulate(eq) - 1).min())
    q = lambda a: np.percentile(a, [5, 25, 50, 75, 95]).round(3).tolist()
    return {"sharpe_pct": q(sharpes), "cagr_pct": q(cagrs), "maxdd_pct": q(mdds),
            "p_sharpe_below_0": round(float(np.mean(np.array(sharpes) < 0)), 3),
            "p_sharpe_below_0.5": round(float(np.mean(np.array(sharpes) < 0.5)), 3)}


def trade_bootstrap(roundtrips: pd.DataFrame, n=5000, seed=11) -> dict:
    rng = np.random.default_rng(seed)
    closed = roundtrips[~roundtrips.open]
    x = closed.ret.values
    if len(x) < 5:
        return {}
    means, pfs = [], []
    for _ in range(n):
        s = rng.choice(x, size=len(x), replace=True)
        means.append(s.mean())
        gp, gl = s[s > 0].sum(), -s[s < 0].sum()
        pfs.append(gp / gl if gl > 0 else 10)
    return {"n_trades": int(len(x)), "mean_ret_pct": np.percentile(means, [5, 50, 95]).round(4).tolist(),
            "profit_factor_pct": np.percentile(pfs, [5, 50, 95]).round(2).tolist(),
            "p_mean_below_0": round(float(np.mean(np.array(means) < 0)), 3)}


# ---------------------------------------------------------------------------
# Variant definitions
# ---------------------------------------------------------------------------
def variant_configs(base: Config) -> dict[str, Config]:
    v = {}
    v["baseline"] = base
    # --- audit: timing / look-ahead / currency ---
    v["fill_same_close_LOOKAHEAD"] = replace(base, fill="same_close")
    v["fill_next_open"] = replace(base, fill="next_open")
    v["signals_in_gbp"] = replace(base, signal_currency="gbp")
    v["rebalance_1st_td"] = replace(base, rebalance_nth_day=1)
    v["rebalance_10th_td"] = replace(base, rebalance_nth_day=10)
    # --- costs ---
    v["cost_10bps"] = replace(base, cost_bps=10)
    v["cost_25bps"] = replace(base, cost_bps=25)
    # --- intended-but-unwired risk controls ---
    v["crash_trigger_on"] = replace(base, crash_trigger=True)
    v["crash_trigger_on_10bps"] = replace(base, crash_trigger=True, cost_bps=10)
    v["circuit_breaker_on"] = replace(base, circuit_breaker=True)
    v["crash_and_cb_on"] = replace(base, crash_trigger=True, circuit_breaker=True)
    v["crash_thr_-5.6pct"] = replace(base, crash_trigger=True, crash_thr=-0.056)
    v["crash_thr_-8.4pct"] = replace(base, crash_trigger=True, crash_thr=-0.084)
    v["crash_thr_-10pct"] = replace(base, crash_trigger=True, crash_thr=-0.10)
    # --- bug fixes ---
    v["fix_least_negative"] = replace(base, fix_least_negative=True)
    v["no_growth_when_unhealthy"] = replace(base, unhealthy_growth=False)
    # --- momentum lookbacks ---
    v["mom_6m"] = replace(base, momentum="six_month")
    v["mom_blend_3_6_12"] = replace(base, momentum="blend_3_6_12")
    v["mom_12-1_skip_month"] = replace(base, momentum="skip_month")
    v["mom_lookback_202"] = replace(base, growth_lookback=202)
    v["mom_lookback_302"] = replace(base, growth_lookback=302)   # capped by hist_rows? 302>275 → needs more rows
    v["mom_lookback_302"].hist_rows = 330
    # --- vol estimator / sizing ---
    v["vol_window_16"] = replace(base, vol_window=16)
    v["vol_window_24"] = replace(base, vol_window=24)
    v["vol_window_60"] = replace(base, vol_window=60)
    v["vol_ewma_30"] = replace(base, vol_estimator="ewma", vol_window=30)
    v["vol_target_10pct"] = replace(base, vol_target=0.10)
    v["vol_target_12pct"] = replace(base, vol_target=0.12)
    v["vol_target_15pct"] = replace(base, vol_target=0.15)
    v["weight_equal"] = replace(base, weighting="equal")
    v["top3_inverse_vol"] = replace(base, top_n=3)
    v["top3_erc"] = replace(base, top_n=3, weighting="erc")
    v["top4_erc_no_selection"] = replace(base, top_n=4, weighting="erc")
    # --- regime filter ---
    v["ma_160"] = replace(base, ma_len=160)
    v["ma_240"] = replace(base, ma_len=240)
    v["regime_hysteresis_2pct"] = replace(base, regime_mode="hysteresis", hysteresis_band=0.02)
    v["regime_hysteresis_3pct"] = replace(base, regime_mode="hysteresis", hysteresis_band=0.03)
    v["regime_dual_ma_and_12m"] = replace(base, regime_mode="dual")
    v["regime_per_asset_trend"] = replace(base, regime_mode="per_asset")
    v["regime_gbp_hysteresis"] = replace(base, signal_currency="gbp", regime_mode="hysteresis")
    v["event_driven_regime_flip"] = replace(base, event_driven=True)
    v["event_driven_10bps"] = replace(base, event_driven=True, cost_bps=10)
    # --- combos ---
    v["combo_gbp_hyst_blend"] = replace(base, signal_currency="gbp", regime_mode="hysteresis", momentum="blend_3_6_12")
    v["combo_gbp_hyst_blend_vt12"] = replace(base, signal_currency="gbp", regime_mode="hysteresis", momentum="blend_3_6_12", vol_target=0.12)
    v["combo_gbp_hyst_blend_10bps"] = replace(base, signal_currency="gbp", regime_mode="hysteresis", momentum="blend_3_6_12", cost_bps=10)
    v["combo_gbp_hyst_blend_fixbug_10bps"] = replace(base, signal_currency="gbp", regime_mode="hysteresis", momentum="blend_3_6_12",
                                                     cost_bps=10, fix_least_negative=True)
    v["min_trade_50"] = replace(base, min_trade_size=50)
    v["capital_5000"] = replace(base, initial_capital=5000)
    for k, c in v.items():
        c.name = k
    return v


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------
def md_table(df: pd.DataFrame, floatfmt="{:.4g}") -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(str(c) for c in cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            x = r[c]
            if isinstance(x, float):
                cells.append("nan" if np.isnan(x) else floatfmt.format(x))
            else:
                cells.append(str(x))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def save_result(res: Result, tag: str):
    os.makedirs(OUT_DIR, exist_ok=True)
    res.equity.to_csv(os.path.join(OUT_DIR, f"{tag}_equity.csv"))
    res.orders.to_csv(os.path.join(OUT_DIR, f"{tag}_orders.csv"), index=False)
    res.roundtrips.to_csv(os.path.join(OUT_DIR, f"{tag}_roundtrips.csv"), index=False)
    res.signals.to_csv(os.path.join(OUT_DIR, f"{tag}_signals.csv"), index=False)


def plot_equity(curves: dict[str, pd.Series], path: str, title: str):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        print("matplotlib not available; skipping plot")
        return
    fig, ax = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    for k, s in curves.items():
        ax[0].plot(s.index, s / s.iloc[0] * 100, label=k, lw=1.6 if k == "strategy" else 1.0)
        ax[1].plot(s.index, (s / s.cummax() - 1) * 100, lw=0.9)
    ax[0].set_yscale("log"); ax[0].set_ylabel("Growth of 100 (log)"); ax[0].legend(fontsize=8); ax[0].set_title(title)
    ax[1].set_ylabel("Drawdown %")
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="run every variant + walk-forward + bootstrap + proxy history")
    ap.add_argument("--variants", default="", help="comma list of variant names to run (besides baseline)")
    ap.add_argument("--start", default=Config.start)
    ap.add_argument("--end", default=Config.end)
    ap.add_argument("--capital", type=float, default=Config.initial_capital)
    ap.add_argument("--refresh", action="store_true", help="re-download yfinance data")
    ap.add_argument("--proxy", action="store_true", help="also run the 2006+ proxy-universe replay")
    ap.add_argument("--stage", default="", help="with --all: comma list of stages to run (variants,sensitivity,walkforward,proxy); default all")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    base = Config(start=args.start, end=args.end, initial_capital=args.capital)
    variants = variant_configs(base)
    stages = set(args.stage.split(",")) if args.stage else {"variants", "sensitivity", "walkforward", "proxy"}
    to_run = ["baseline"]
    if args.all and "variants" in stages:
        to_run = list(variants)
    elif args.variants:
        to_run += [x.strip() for x in args.variants.split(",") if x.strip()]

    mkts: dict = {}
    summary_rows = []
    results: dict[str, Result] = {}
    t0 = time.time()
    for name in to_run:
        cfg = variants[name]
        t1 = time.time()
        res = run_config(mkts, cfg, args.refresh)
        results[name] = res
        m = metrics(res.equity.equity, res.orders, res.roundtrips, res.equity[[f"w_{t}" for t in ALL_TICKERS]])
        m = {"variant": name, **m, **{f"ev_{k}": v for k, v in res.events.items()}}
        summary_rows.append(m)
        save_result(res, name)
        print(f"{name:38s} CAGR={m['cagr']:+.2%} vol={m['ann_vol']:.2%} Sharpe={m['sharpe']:.2f} Sortino={m['sortino']:.2f} "
              f"MaxDD={m['max_dd']:.1%} Calmar={m['calmar']:.2f} orders={m['orders']} ({time.time() - t1:.1f}s)")

    summary = pd.DataFrame(summary_rows)
    if "variants" in stages:   # don't clobber the full table when only a later stage is rerun
        summary.to_csv(os.path.join(OUT_DIR, "summary.csv"), index=False)
    key_cols = ["variant", "cagr", "ann_vol", "sharpe", "sortino", "max_dd", "calmar", "monthly_win_rate", "exposure_avg",
                "orders", "turnover_ann", "rt_win_rate", "rt_avg_ret", "rt_profit_factor", "ev_crash_rebalances", "ev_cash_shortfall"]
    if "variants" in stages:
        with open(os.path.join(OUT_DIR, "summary.md"), "w") as f:
            f.write(md_table(summary[[c for c in key_cols if c in summary.columns]]))
    for n in mkts.values():
        for note in n.notes:
            print("DATA NOTE:", note)

    # ---- baseline deep-dive: benchmarks, periods, plot, bootstrap -------------------------------------
    base_res = results["baseline"]
    mkt = mkts[("live", "live")]
    eq = base_res.equity.equity
    bench = bench_equity(mkt, eq.index[0], eq.index[-1], base.initial_capital)
    bench_metrics = {k: metrics(v) for k, v in bench.items()}
    pd.DataFrame(bench_metrics).T.to_csv(os.path.join(OUT_DIR, "benchmarks.csv"))
    curves = {"strategy": eq, **{k: v for k, v in bench.items() if k in ("BH_CSPX.L", "BH_VWRL.L", "STATIC_80_15_5")}}
    if "crash_trigger_on" in results:
        curves["crash_trigger_on"] = results["crash_trigger_on"].equity.equity
    if "combo_gbp_hyst_blend" in results:
        curves["combo_gbp_hyst_blend"] = results["combo_gbp_hyst_blend"].equity.equity
    plot_equity(curves, os.path.join(OUT_DIR, "baseline_equity.png"), "Live-logic replay vs benchmarks (GBP)")

    periods = [
        ("2015-08 China/flash crash", "2015-07-31", "2015-09-30"),
        ("2016-Q1 sell-off", "2015-12-31", "2016-02-29"),
        ("2016 Brexit vote", "2016-06-22", "2016-07-15"),
        ("2018-Q4 sell-off", "2018-09-28", "2018-12-31"),
        ("2020 COVID crash", "2020-02-19", "2020-03-23"),
        ("2020 V recovery", "2020-03-23", "2020-08-31"),
        ("2022 rate-hike bear", "2022-01-03", "2022-10-14"),
        ("2022 UK gilt crisis", "2022-09-22", "2022-10-14"),
        ("2023 sideways/chop", "2023-01-03", "2023-10-31"),
        ("2024-08 vol spike", "2024-07-15", "2024-08-09"),
        ("2025 tariff crash", "2025-02-19", "2025-04-08"),
        ("2025 tariff V recovery", "2025-04-08", "2025-07-31"),
        ("Calendar 2015", "2015-01-01", "2015-12-31"), ("Calendar 2016", "2016-01-01", "2016-12-31"),
        ("Calendar 2017", "2017-01-01", "2017-12-31"), ("Calendar 2018", "2018-01-01", "2018-12-31"),
        ("Calendar 2019", "2019-01-01", "2019-12-31"), ("Calendar 2020", "2020-01-01", "2020-12-31"),
        ("Calendar 2021", "2021-01-01", "2021-12-31"), ("Calendar 2022", "2022-01-01", "2022-12-31"),
        ("Calendar 2023", "2023-01-01", "2023-12-31"), ("Calendar 2024", "2024-01-01", "2024-12-31"),
        ("Calendar 2025", "2025-01-01", "2025-12-31"), ("2026 YTD", "2026-01-01", "2026-09-04"),
    ]
    pt = period_table(eq, {**bench, **({"crash_trigger_on": results["crash_trigger_on"].equity.equity} if "crash_trigger_on" in results else {})}, periods)
    pt.to_csv(os.path.join(OUT_DIR, "periods.csv"), index=False)
    with open(os.path.join(OUT_DIR, "periods.md"), "w") as f:
        f.write(md_table(pt))

    mth = eq.resample("ME").last().pct_change().dropna()
    boot = {"baseline_monthly_block_bootstrap": block_bootstrap_sharpe(mth), "baseline_trade_bootstrap": trade_bootstrap(base_res.roundtrips)}
    for k in ("crash_trigger_on", "combo_gbp_hyst_blend_10bps", "cost_10bps"):
        if k in results:
            mk = results[k].equity.equity.resample("ME").last().pct_change().dropna()
            boot[f"{k}_monthly_block_bootstrap"] = block_bootstrap_sharpe(mk)
    with open(os.path.join(OUT_DIR, "bootstrap.md"), "w") as f:
        f.write("```json\n" + json.dumps(boot, indent=2) + "\n```\n")

    # regime table: strategy monthly return by regime label held
    regs = base_res.equity.copy()
    regs["mret"] = regs.equity.pct_change()
    by_reg = regs.groupby("regime").agg(days=("mret", "size"), ann_ret=("mret", lambda x: (1 + x).prod() ** (252 / max(len(x), 1)) - 1),
                                         ann_vol=("mret", lambda x: x.std() * math.sqrt(252)))
    by_reg.to_csv(os.path.join(OUT_DIR, "by_regime.csv"))

    if args.all and "sensitivity" in stages:
        # ---- sensitivity (±20%) -----------------------------------------------------------------------
        sens = []
        for label, kw in [("baseline", {}), ("ma_len 160 (-20%)", {"ma_len": 160}), ("ma_len 240 (+20%)", {"ma_len": 240}),
                          ("vol_window 16 (-20%)", {"vol_window": 16}), ("vol_window 24 (+20%)", {"vol_window": 24}),
                          ("growth_lookback 202 (-20%)", {"growth_lookback": 202}), ("growth_lookback 302 (+20%)", {"growth_lookback": 302, "hist_rows": 330}),
                          ("def_lookback 50 (-20%)", {"def_lookback": 50}), ("def_lookback 76 (+20%)", {"def_lookback": 76}),
                          ("crash_thr -5.6% (trigger on)", {"crash_trigger": True, "crash_thr": -0.056}),
                          ("crash_thr -7% (trigger on)", {"crash_trigger": True}),
                          ("crash_thr -8.4% (trigger on)", {"crash_trigger": True, "crash_thr": -0.084}),
                          ("vol_target 0.096 (12%-20%)", {"vol_target": 0.096}), ("vol_target 0.12", {"vol_target": 0.12}), ("vol_target 0.144 (12%+20%)", {"vol_target": 0.144}),
                          ("min_trade 120", {"min_trade_size": 120}), ("min_trade 180", {"min_trade_size": 180})]:
            cfg = replace(base, name=label, **kw)
            r = run_config(mkts, cfg)
            m = metrics(r.equity.equity, r.orders, r.roundtrips)
            sens.append({"setting": label, "cagr": m["cagr"], "sharpe": m["sharpe"], "max_dd": m["max_dd"], "calmar": m["calmar"], "orders": m["orders"]})
        sdf = pd.DataFrame(sens)
        sdf.to_csv(os.path.join(OUT_DIR, "sensitivity.csv"), index=False)
        with open(os.path.join(OUT_DIR, "sensitivity.md"), "w") as f:
            f.write(md_table(sdf))
        print("\nSENSITIVITY\n" + md_table(sdf))

    if args.all and "walkforward" in stages:
        # ---- walk-forward ----------------------------------------------------------------------------
        wf_lines = []
        grid = [dict(ma_len=a, growth_lookback=b, vol_window=c) for a in (160, 200, 240) for b in (126, 189, 252) for c in (20, 60)]
        folds = [("IS 2014-06→2019-12 / OOS 2020-01→2026-09", "2014-06-01", "2019-12-31", "2020-01-01", "2026-09-04"),
                 ("IS 2014-06→2021-12 / OOS 2022-01→2026-09", "2014-06-01", "2021-12-31", "2022-01-01", "2026-09-04"),
                 ("IS 2018-01→2023-12 / OOS 2024-01→2026-09", "2018-01-01", "2023-12-31", "2024-01-01", "2026-09-04")]
        for label, a0, a1, b0, b1 in folds:
            rows = []
            for g in grid:
                cfg = replace(base, start=a0, end=a1, **g)
                r = run_config(mkts, cfg)
                m = metrics(r.equity.equity)
                rows.append({**g, "is_sharpe": m["sharpe"], "is_cagr": m["cagr"], "is_mdd": m["max_dd"]})
            rows = pd.DataFrame(rows).sort_values("is_sharpe", ascending=False)
            best = rows.iloc[0]
            g_best = {k: int(best[k]) for k in ("ma_len", "growth_lookback", "vol_window")}
            oos_best = metrics(run_config(mkts, replace(base, start=b0, end=b1, **g_best)).equity.equity)
            oos_live = metrics(run_config(mkts, replace(base, start=b0, end=b1)).equity.equity)
            is_live = metrics(run_config(mkts, replace(base, start=a0, end=a1)).equity.equity)
            live_rank = int((rows.is_sharpe > is_live["sharpe"]).sum()) + 1
            wf_lines.append(f"### {label}\n")
            wf_lines.append(f"- Live params IS Sharpe {is_live['sharpe']:.2f} (rank {live_rank}/{len(rows)} in grid), OOS Sharpe {oos_live['sharpe']:.2f}, OOS CAGR {oos_live['cagr']:.1%}, OOS MaxDD {oos_live['max_dd']:.1%}")
            wf_lines.append(f"- Best-IS params {g_best}: IS Sharpe {best['is_sharpe']:.2f} → OOS Sharpe {oos_best['sharpe']:.2f}, OOS CAGR {oos_best['cagr']:.1%}, OOS MaxDD {oos_best['max_dd']:.1%}")
            wf_lines.append(f"- Grid IS Sharpe range: {rows.is_sharpe.min():.2f} … {rows.is_sharpe.max():.2f} (median {rows.is_sharpe.median():.2f})\n")
            wf_lines.append(md_table(rows.head(6)) + "\n")
        with open(os.path.join(OUT_DIR, "walkforward.md"), "w") as f:
            f.write("\n".join(wf_lines))
        print("\nWALK-FORWARD\n" + "\n".join(wf_lines))

    if (args.all and "proxy" in stages) or args.proxy:
        # ---- proxy universe 2006+ (covers 2008) -------------------------------------------------------
        prox_rows = []
        for name, kw in [("proxy_baseline", {}), ("proxy_crash_trigger_on", {"crash_trigger": True}),
                         ("proxy_signals_gbp_hyst", {"signal_currency": "gbp", "regime_mode": "hysteresis"}),
                         ("proxy_combo_gbp_hyst_blend", {"signal_currency": "gbp", "regime_mode": "hysteresis", "momentum": "blend_3_6_12"}),
                         ("proxy_combo_gbp_hyst_blend_10bps", {"signal_currency": "gbp", "regime_mode": "hysteresis", "momentum": "blend_3_6_12", "cost_bps": 10}),
                         ("proxy_cost_10bps", {"cost_bps": 10})]:
            cfg = replace(base, name=name, universe="proxy", start="2006-04-01", **kw)
            r = run_config(mkts, cfg)
            save_result(r, name)
            m = metrics(r.equity.equity, r.orders, r.roundtrips, r.equity[[f"w_{t}" for t in ALL_TICKERS]])
            prox_rows.append({"variant": name, **m, **{f"ev_{k}": v for k, v in r.events.items()}})
            print(f"{name:38s} CAGR={m['cagr']:+.2%} Sharpe={m['sharpe']:.2f} MaxDD={m['max_dd']:.1%} Calmar={m['calmar']:.2f} orders={m['orders']}")
            if name == "proxy_baseline":
                pm = mkts[("proxy", "live")]
                pb = bench_equity(pm, r.equity.index[0], r.equity.index[-1], base.initial_capital)
                pp = period_table(r.equity.equity, pb, [("2007-10→2009-03 GFC bear", "2007-10-09", "2009-03-09"),
                                                        ("2008 calendar", "2008-01-01", "2008-12-31"), ("2009 recovery", "2009-03-09", "2009-12-31"),
                                                        ("2011 euro crisis", "2011-07-01", "2011-10-04"), ("2015-16 chop", "2015-05-01", "2016-06-30"),
                                                        ("2020 COVID", "2020-02-19", "2020-03-23"), ("2022 bear", "2022-01-03", "2022-10-14"),
                                                        ("2006-04→2014-05 (pre live data)", "2006-04-03", "2014-05-30"), ("2014-06→2026-09 (overlap)", "2014-06-02", "2026-09-04")])
                pp.to_csv(os.path.join(OUT_DIR, "proxy_periods.csv"), index=False)
                with open(os.path.join(OUT_DIR, "proxy_periods.md"), "w") as f:
                    f.write(md_table(pp))
                plot_equity({"strategy(proxy)": r.equity.equity, **{k: v for k, v in pb.items() if k in ("BH_CSPX.L", "BH_VWRL.L", "STATIC_80_15_5")}},
                            os.path.join(OUT_DIR, "proxy_equity.png"), "Proxy-universe replay 2006→2026 (US ETFs in GBP)")
                mthp = r.equity.equity.resample("ME").last().pct_change().dropna()
                with open(os.path.join(OUT_DIR, "proxy_bootstrap.md"), "w") as f:
                    f.write("```json\n" + json.dumps(block_bootstrap_sharpe(mthp), indent=2) + "\n```\n")
        pdf = pd.DataFrame(prox_rows)
        pdf.to_csv(os.path.join(OUT_DIR, "proxy_summary.csv"), index=False)
        with open(os.path.join(OUT_DIR, "proxy_summary.md"), "w") as f:
            f.write(md_table(pdf[[c for c in key_cols if c in pdf.columns]]))

    print(f"\nDone in {time.time() - t0:.0f}s. Outputs in {OUT_DIR}")


if __name__ == "__main__":
    main()
