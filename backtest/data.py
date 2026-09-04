"""
Historical data for the backtest.  Two universes:

  lse   — the instruments actually traded (config.INSTRUMENTS): LSE lines for valuation,
          the configured signal tickers for signals.  Full history from 2013 (VEUR listing).
  proxy — US-listed twins converted to GBP with the daily GBP/USD rate, 2006 → today, so that
          2008 is covered.  Conversion happens ONLY here, for P&L on a longer history; the live
          system never converts anything.

yfinance downloads are cached in backtest_output/cache/.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.config import INSTRUMENTS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "backtest_output")
CACHE_DIR = os.path.join(OUT_DIR, "cache")


def _cache_path(t: str) -> str:
    return os.path.join(CACHE_DIR, f"{t.replace('=', '_').replace('^', '_')}.csv")


def fetch(t: str, refresh: bool = False) -> pd.DataFrame:
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = _cache_path(t)
    if not refresh and os.path.exists(p):
        df = pd.read_csv(p, index_col=0)
        df.index = pd.to_datetime(df.index.astype(str).str[:10])
        return df
    import yfinance as yf
    for attempt in range(3):
        try:
            h = yf.Ticker(t).history(period="max", auto_adjust=True)
            if h.empty:
                raise ValueError("empty")
            h.index = pd.to_datetime(h.index.astype(str).str[:10])
            h = h[~h.index.duplicated(keep="last")].sort_index()
            h.to_csv(p)
            time.sleep(0.6)
            return h
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)


def close(t: str, refresh: bool = False) -> pd.Series:
    s = fetch(t, refresh)["Close"].astype(float)
    return s[s > 0]


def gbpusd() -> pd.Series:
    fx = close("GBPUSD=X")
    return fx[~fx.index.duplicated()]


def to_gbp(usd: pd.Series) -> pd.Series:
    fx = gbpusd()
    f = fx.reindex(usd.index.union(fx.index)).ffill().reindex(usd.index)
    return usd / f


def repair_prints(s: pd.Series, ref: pd.Series, window: int = 63, tol: float = 0.20) -> tuple[pd.Series, int]:
    """Fix Yahoo unit-mixing prints (a day quoted in USD inside a GBP series) using a reference series."""
    j = pd.concat([s.rename("x"), ref.rename("r")], axis=1).dropna()
    ratio = j.x / j.r
    med = ratio.rolling(window, min_periods=10, center=True).median()
    bad = (np.log(ratio) - np.log(med)).abs() > tol
    fixed = s.copy()
    if bad.any():
        fixed.loc[bad[bad].index] = (med[bad] * j.r[bad]).values
    return fixed, int(bad.sum())


def splice_backwards(main: pd.Series, proxy_returns: pd.Series) -> pd.Series:
    """Extend `main` backwards by chaining `proxy_returns` before its first date."""
    pre = proxy_returns[proxy_returns.index < main.index[0]].dropna()
    chain = (1 + pre[::-1]).cumprod()[::-1]           # cumulative growth from each date to main start
    ext = main.iloc[0] / chain
    return pd.concat([ext, main]).sort_index()


def vt_spliced() -> pd.Series:
    vt = close("VT")
    spy, efa = close("SPY"), close("EFA")
    blend = (0.55 * spy.pct_change() + 0.45 * efa.reindex(spy.index).ffill().pct_change()).dropna()
    return splice_backwards(vt, blend)


def gilts_chain() -> pd.Series:
    """IGLT.L (2008→) extended backwards with IEF (USD 7-10y Treasuries, bond-only returns, no FX)."""
    iglt = close("IGLT.L")
    ief = close("IEF")
    return splice_backwards(iglt, ief.pct_change())


@dataclass
class Market:
    trade: pd.DataFrame        # GBP price per unit, keyed by instrument key
    signal: pd.DataFrame       # signal series, keyed by instrument key
    notes: list[str] = field(default_factory=list)


def load_market(universe: str = "lse", refresh: bool = False) -> Market:
    notes = []
    trade, signal = {}, {}
    if universe == "lse":
        refs = {"NDX": to_gbp(close("QQQ")), "GOLD": to_gbp(close("GLD")), "SP500": to_gbp(close("SPY"))}
        for inst in INSTRUMENTS:
            s = close(inst.trade_ticker, refresh)
            if inst.quote == "GBX":
                s = s / 100.0
            if inst.key in refs:
                s, n = repair_prints(s, refs[inst.key])
                if n:
                    notes.append(f"{inst.key} ({inst.trade_ticker}): repaired {n} broken prints")
            trade[inst.key] = s
            if inst.key == "WORLD":
                signal[inst.key] = vt_spliced()
            elif inst.signal_ticker == inst.trade_ticker:
                signal[inst.key] = s
            else:
                signal[inst.key] = close(inst.signal_ticker, refresh)
    elif universe == "proxy":
        vt = vt_spliced()
        usd = {"SP500": close("SPY"), "NDX": close("QQQ"), "WORLD": vt, "EUROPE": close("VGK"), "GOLD": close("GLD")}
        for k, s in usd.items():
            trade[k] = to_gbp(s)
        trade["GILTS"] = gilts_chain()
        signal = {"SP500": close("^GSPC"), "NDX": close("^NDX"), "WORLD": vt, "EUROPE": close("VGK"),
                  "GOLD": trade["GOLD"], "GILTS": trade["GILTS"]}
        notes.append("proxy universe: SPY/QQQ/VT(+SPY-EFA splice)/VGK/GLD in GBP; gilts IGLT.L←IEF splice")
    else:
        raise ValueError(universe)
    tdf = pd.DataFrame(trade).sort_index()
    sdf = pd.DataFrame(signal).reindex(tdf.index.union(pd.DataFrame(signal).index)).sort_index()
    first = max(tdf[c].first_valid_index() for c in tdf)
    tdf = tdf.loc[first:].ffill()
    sdf = sdf.loc[first:].ffill().reindex(tdf.index).ffill()
    return Market(tdf, sdf, notes)
