"""When the monthly rebalance may start.  Regression cover for 2026-10-01, when a run that began
after the London close sold one fund, could not buy its replacement, and was never retried."""
from datetime import date

import pytest

from src import jobs
from src.broker import BrokerError
from src.config import ENV_KEYS


class _Reached(Exception):
    pass


@pytest.fixture
def gate(monkeypatch):
    for k in ENV_KEYS:
        monkeypatch.setenv(k, "x")
    alerts = []
    monkeypatch.setattr(jobs, "_alert", lambda title, body: alerts.append(title))
    monkeypatch.setattr(jobs, "nth_trading_day", lambda y, m, n=1: date(2026, 10, 1))
    monkeypatch.setattr(jobs, "is_trading_day", lambda d: True)

    def broker():
        raise BrokerError("reached the broker")
    monkeypatch.setattr(jobs, "T212", broker)

    def run(today, done, minutes_left):
        class _Date(date):
            @classmethod
            def today(cls):
                return today
        monkeypatch.setattr(jobs, "date", _Date)
        monkeypatch.setattr(jobs.store, "rebalance_done", lambda y, m: done)
        monkeypatch.setattr(jobs, "minutes_to_close", lambda: minutes_left)
        alerts.clear()
        return jobs.rebalance(), list(alerts)
    return run


def test_before_the_due_day_nothing_happens(gate):
    assert gate(date(2026, 9, 30), False, 300) == (0, [])


def test_a_completed_month_is_not_traded_again(gate):
    assert gate(date(2026, 10, 2), True, 300) == (0, [])
    assert gate(date(2026, 10, 1), True, 300) == (0, [])


def test_an_unfinished_rebalance_is_picked_up_on_a_later_day(gate):
    code, alerts = gate(date(2026, 10, 2), False, 300)
    assert code == 4 and alerts == ["❌ REBALANCE ABORTED"]        # it got as far as the broker


def test_it_does_not_start_when_the_market_is_closed_or_about_to(gate):
    for left in (None, 10):
        code, alerts = gate(date(2026, 10, 1), False, left)
        assert code == 0 and alerts == ["⏳ REBALANCE DUE — NOT STARTED"]


def test_an_unreadable_audit_log_never_triggers_a_catch_up(gate):
    assert gate(date(2026, 10, 5), None, 300) == (0, [])
    code, _ = gate(date(2026, 10, 1), None, 300)                    # the due day itself still runs
    assert code == 4
