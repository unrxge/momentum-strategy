"""Price data fetching module using yfinance.

Bug fixes vs the original (see ANALYSIS.md §2.1 / §2.4):
  * USD-quoted lines are converted with the HISTORICAL daily GBP/USD rate, not a single
    "today" rate applied to the whole history (which made every CSPX signal a USD signal).
  * The partial intraday bar yfinance returns during the session is dropped, so signals
    always use completed closes.
  * Quote currency is looked up from a static map first; the slow/flaky `.info` endpoint is
    only used for unknown tickers, and the result is cached per process.
  * `validate_price_history()` is a hard data-quality gate (row count, staleness, broken
    prints) that callers should run before computing any signal.
"""

import time
import random
from datetime import date, timedelta
import pandas as pd
import yfinance as yf

# Known quote currencies on Yahoo for the strategy universe (verified 2026-09-04).
KNOWN_CURRENCY = {
    "CSPX.L": "USD", "CSP1.L": "GBp", "EQQQ.L": "GBp", "VWRL.L": "GBP", "VEUR.L": "GBP",
    "SGLN.L": "GBp", "IGLS.L": "GBP", "IGLT.L": "GBP",
}
_currency_cache: dict[str, str] = {}
_fx_cache: pd.Series | None = None


class PriceDataError(ValueError):
    """Raised when price history fails the data-quality gate."""


def get_gbpusd_history(period_days: int = 800) -> pd.Series:
    """Daily GBPUSD closes (USD per 1 GBP), forward-filled, cached per process."""
    global _fx_cache
    if _fx_cache is not None:
        return _fx_cache
    for attempt in range(3):
        try:
            fx = yf.Ticker("GBPUSD=X").history(period=f"{period_days}d", auto_adjust=True)["Close"]
            if fx.empty:
                raise ValueError("empty FX history")
            fx.index = pd.to_datetime(fx.index.astype(str).str[:10])
            fx = fx[~fx.index.duplicated(keep="last")].sort_index()
            _fx_cache = fx
            return fx
        except Exception as e:
            if attempt == 2:
                raise ValueError(f"Failed to fetch GBP/USD history: {e}")
            time.sleep(2)


def get_gbpusd_rate() -> float:
    """Most recent GBPUSD close (kept for backwards compatibility)."""
    return float(get_gbpusd_history().iloc[-1])


def get_currency(ticker: str, ticker_obj: yf.Ticker | None = None) -> str:
    if ticker in KNOWN_CURRENCY:
        return KNOWN_CURRENCY[ticker]
    if ticker in _currency_cache:
        return _currency_cache[ticker]
    try:
        cur = (ticker_obj or yf.Ticker(ticker)).info.get("currency", "unknown")
    except Exception:
        cur = "unknown"
    _currency_cache[ticker] = cur
    return cur


def validate_price_history(df: pd.DataFrame, ticker: str, min_rows: int = 260,
                           max_abs_daily_return: float = 0.15, max_stale_days: int = 5) -> None:
    """
    Data-quality gate. Raises PriceDataError with a clear message if:
      * fewer than `min_rows` completed bars (12-month signals need ≥ 253),
      * the last bar is older than `max_stale_days` calendar days,
      * any |daily return| exceeds `max_abs_daily_return` (Yahoo has served USD/GBP
        unit-mixing prints for LSE ETFs — a single one flips the momentum ranking),
      * non-positive prices.
    """
    if df is None or df.empty:
        raise PriceDataError(f"{ticker}: no data")
    if len(df) < min_rows:
        raise PriceDataError(f"{ticker}: only {len(df)} completed bars, need {min_rows}")
    last = pd.Timestamp(df["Date"].iloc[-1]).date()
    if (date.today() - last).days > max_stale_days:
        raise PriceDataError(f"{ticker}: last bar {last} is stale (> {max_stale_days} days old)")
    if (df["Close"] <= 0).any():
        raise PriceDataError(f"{ticker}: non-positive close present")
    r = df["Close"].pct_change().abs()
    if (r > max_abs_daily_return).any():
        bad = df.loc[r > max_abs_daily_return, "Date"].dt.date.tolist()[:5]
        raise PriceDataError(f"{ticker}: broken print(s) > {max_abs_daily_return:.0%} on {bad}")


def fetch_price_history(ticker: str, period_days: int = 400, apply_delay: bool = True,
                        validate: bool = True, min_rows: int = 260) -> pd.DataFrame:
    """
    Daily historical closes for a ticker in GBP, oldest first, completed bars only.

    Args:
        ticker: Yahoo ticker (e.g. "CSPX.L")
        period_days: history to request (yfinance treats this as ~trading days; 400 → ~395 rows)
        apply_delay: random 0.5–1.5 s pause to avoid rate limiting
        validate: run validate_price_history() (recommended for anything that feeds a signal)
        min_rows: minimum completed bars required by the validator

    Returns:
        DataFrame [Date, Close] in GBP.

    Raises:
        PriceDataError / ValueError if data is missing or fails the quality gate.
    """
    if apply_delay:
        time.sleep(random.uniform(0.5, 1.5))

    max_retries = 3
    for attempt in range(max_retries):
        try:
            ticker_obj = yf.Ticker(ticker)
            data = ticker_obj.history(period=f"{period_days}d", auto_adjust=True)
            if data.empty:
                raise ValueError(f"No price data found for ticker: {ticker}")
            data.index = pd.to_datetime(data.index.astype(str).str[:10])
            data = data[~data.index.duplicated(keep="last")].sort_index()

            # Drop the partial intraday bar (the job runs at 09:00 UTC while LSE is open).
            data = data[data.index.date < date.today()]

            currency = get_currency(ticker, ticker_obj)
            print(f"  {ticker}: currency={currency}", end="")
            close = data["Close"].copy()
            if currency in ["GBX", "GBp"]:
                close = close / 100
                print(" → GBX→GBP")
            elif currency == "USD":
                fx = get_gbpusd_history().reindex(close.index.union(get_gbpusd_history().index)).ffill().reindex(close.index)
                close = close / fx
                print(" → USD→GBP (historical daily rate)")
            elif currency == "GBP":
                print()
            else:
                raise ValueError(f"{ticker}: unsupported/unknown currency '{currency}'")

            df = close.rename("Close").reset_index()
            df.columns = ["Date", "Close"]
            df = df.dropna().sort_values("Date").reset_index(drop=True)
            if validate:
                validate_price_history(df, ticker, min_rows=min_rows)
            return df

        except PriceDataError:
            raise
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise ValueError(f"Failed to fetch data for {ticker} after {max_retries} attempts: {e}")
