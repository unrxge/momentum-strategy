"""Account figures must be Trading 212's own, and outside holdings must not be read in the wrong unit."""
from datetime import datetime, timezone

from src.broker import T212, resolve_other_units
from src.trading_calendar import minutes_to_close


def _pos(qty, price):
    return {"quantity": qty, "price_gbp": price, "value": qty * price, "t212": "x"}


def test_outside_holding_quoted_in_pence_is_not_read_as_pounds():
    # the real case: 3LUS at 14706p was valued at 1,559 instead of 15.59
    pos = {"NDX": _pos(3.31, 567.63), "OTHER:3LUS1l_EQ": _pos(0.10602954, 14706.0)}
    residual = resolve_other_units(pos, 1894.45)
    assert round(pos["OTHER:3LUS1l_EQ"]["value"], 2) == 15.59
    assert residual < 0.05


def test_outside_holding_quoted_in_pounds_is_left_alone():
    pos = {"NDX": _pos(3.31, 567.63), "OTHER:VUSAl_EQ": _pos(10, 80.0)}
    resolve_other_units(pos, 3.31 * 567.63 + 800.0)
    assert round(pos["OTHER:VUSAl_EQ"]["value"], 2) == 800.0


def test_mixed_units_are_each_resolved():
    pos = {"OTHER:A": _pos(2, 5000.0), "OTHER:B": _pos(3, 40.0), "WORLD": _pos(1, 140.0)}
    resolve_other_units(pos, 140.0 + 100.0 + 120.0)
    assert (round(pos["OTHER:A"]["value"], 2), round(pos["OTHER:B"]["value"], 2)) == (100.0, 120.0)


def test_snapshot_reports_the_brokers_own_totals():
    b = T212.__new__(T212)
    b.cash = lambda: {"free": 3214.98, "total": 5109.43, "invested": 1786.91, "ppl": 107.54, "result": 1.89,
                      "blocked": 0.0, "holdings_value": 1894.45, "raw": {}}
    b.positions = lambda: {"NDX": _pos(3.31, 567.63), "OTHER:3LUS1l_EQ": _pos(0.10602954, 14706.0)}
    s = b.snapshot()
    assert s["total"] == 5109.43 and s["invested"] == 1894.45
    assert abs(s["cash"]["free"] + s["invested"] - s["total"]) < 0.01


def test_minutes_to_close_is_none_outside_the_session():
    at = lambda t: minutes_to_close(datetime.fromisoformat(t).replace(tzinfo=timezone.utc))
    assert at("2026-10-01T16:37") is None          # the run that left a sell queued overnight
    assert at("2026-10-17T10:00") is None          # Saturday
    assert 350 < at("2026-10-13T09:30") < 370
