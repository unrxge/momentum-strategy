"""Trading-day helpers: a day counts only if both LSE and NYSE are open."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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


def minutes_to_close(now: datetime | None = None) -> float | None:
    """Minutes until the London market closes, or None if it is not open right now."""
    now = now or datetime.now(timezone.utc)
    try:
        day = mcal.get_calendar("LSE").schedule(str(now.date()), str(now.date()))
        if day.empty:
            return None
        opens, closes = day.iloc[0]["market_open"].to_pydatetime(), day.iloc[0]["market_close"].to_pydatetime()
    except Exception as exc:                       # calendar failure: assume the usual 08:00-16:30 London day
        print(f"⚠️  calendar error {exc}; assuming standard London hours")
        from zoneinfo import ZoneInfo
        local = now.astimezone(ZoneInfo("Europe/London"))
        if local.weekday() >= 5:
            return None
        opens = local.replace(hour=8, minute=0, second=0, microsecond=0)
        closes = local.replace(hour=16, minute=30, second=0, microsecond=0)
    if not opens <= now <= closes:
        return None
    return (closes - now).total_seconds() / 60.0
