"""
Market data via yfinance, with a hard data-quality gate.

No currency conversion.  GBX (pence) lines are divided by 100 so that valuation and
order sizing are in pounds; signal series are used exactly as quoted.
"""
from __future__ import annotations

import random
import time
from datetime import date

import pandas as pd
import yfinance as yf

from src.config import Instrument, INSTRUMENTS, PRICE_HISTORY_DAYS


class PriceDataError(ValueError):
    """Raised when a price series fails the data-quality gate."""


def _download(ticker: str, period_days: int, retries: int = 3) -> pd.Series:
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            h = yf.Ticker(ticker).history(period=f"{period_days}d", auto_adjust=True)
            if h.empty:
                raise ValueError("empty response")
            h.index = pd.to_datetime(h.index.astype(str).str[:10])      # avoid tz → date shifts
            s = h["Close"][~h.index.duplicated(keep="last")].sort_index()
            s = s[s.index.date < date.today()]                          # drop the partial intraday bar
            return s.astype(float).rename(ticker)
        except Exception as exc:                                        # network / rate limit / parse
            last_exc = exc
            time.sleep(2 + attempt * 3)
    raise PriceDataError(f"{ticker}: download failed after {retries} attempts: {last_exc}")


def validate(s: pd.Series, label: str, min_rows: int = 260, max_abs_daily_return: float = 0.15,
             max_stale_days: int = 6) -> None:
    if s is None or len(s) == 0:
        raise PriceDataError(f"{label}: no data")
    if len(s) < min_rows:
        raise PriceDataError(f"{label}: only {len(s)} completed bars, need {min_rows}")
    last = pd.Timestamp(s.index[-1]).date()
    if (date.today() - last).days > max_stale_days:
        raise PriceDataError(f"{label}: last bar {last} is stale")
    if (s <= 0).any() or s.isna().any():
        raise PriceDataError(f"{label}: non-positive or missing close")
    r = s.pct_change().abs()
    if (r > max_abs_daily_return).any():
        bad = [str(d.date()) for d in s.index[r > max_abs_daily_return]][:5]
        raise PriceDataError(f"{label}: broken print(s) > {max_abs_daily_return:.0%} on {bad}")


def fetch_series(ticker: str, quote: str = "GBP", period_days: int = PRICE_HISTORY_DAYS,
                 min_rows: int = 260, polite: bool = True) -> pd.Series:
    if polite:
        time.sleep(random.uniform(0.3, 0.9))
    s = _download(ticker, period_days)
    if quote == "GBX":
        s = s / 100.0
    validate(s, ticker, min_rows=min_rows)
    return s


def load_prices(instruments: tuple[Instrument, ...] = INSTRUMENTS, min_rows: int = 260
                ) -> tuple[dict[str, pd.Series], dict[str, pd.Series], list[str]]:
    """
    Returns (trade_px, signal_px, problems).  Both dicts are keyed by instrument key.
    trade_px is in GBP per share (for valuation/sizing); signal_px is as quoted.
    `problems` lists every failure; callers must abort if it is non-empty (a partial
    universe silently distorts the ranking — v1 audit finding).
    """
    trade, signal, problems = {}, {}, []
    cache: dict[str, pd.Series] = {}
    for inst in instruments:
        try:
            trade[inst.key] = fetch_series(inst.trade_ticker, inst.quote, min_rows=min_rows)
            cache[inst.trade_ticker] = trade[inst.key]
        except Exception as exc:
            problems.append(f"{inst.key} trade series {inst.trade_ticker}: {exc}")
        try:
            if inst.signal_ticker == inst.trade_ticker and inst.trade_ticker in cache:
                signal[inst.key] = cache[inst.trade_ticker]
            else:
                signal[inst.key] = fetch_series(inst.signal_ticker, "GBP", min_rows=min_rows)
        except Exception as exc:
            problems.append(f"{inst.key} signal series {inst.signal_ticker}: {exc}")
    return trade, signal, problems
