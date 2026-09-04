"""Trading-day helpers: a day counts only if both LSE and NYSE are open."""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import pandas_market_calendars as mcal


@lru_cache(maxsize=8)
def _open_days(year: int) -> frozenset:
    lse = mcal.get_calendar("LSE").schedule(f"{year}-01-01", f"{year}-12-31").index.date
    nyse = mcal.get_calendar("NYSE").schedule(f"{year}-01-01", f"{year}-12-31").index.date
    return frozenset(set(lse) & set(nyse))


def is_trading_day(d: date) -> bool:
    try:
        return d in _open_days(d.year)
    except Exception as exc:                       # calendar package failure → weekday fallback
        print(f"⚠️  calendar error {exc}; weekday fallback")
        return d.weekday() < 5


def nth_trading_day(year: int, month: int, n: int = 1) -> date:
    d = date(year, month, 1)
    count = 0
    while True:
        if is_trading_day(d):
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)


def next_rebalance_date(today: date, n: int = 1) -> date:
    rd = nth_trading_day(today.year, today.month, n)
    if rd >= today:
        return rd
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return nth_trading_day(y, m, n)
