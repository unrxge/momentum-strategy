"""Executor tests with a fake broker: sizing, clipping, ordering, cash scaling, settlement wait."""
from src import executor
from src.broker import BrokerError


class FakeBroker:
    def __init__(self, positions=None, free=10_000.0, pending_rounds=0, reject=()):
        self._positions = positions or {}
        self._free = free
        self._pending_rounds = pending_rounds
        self.reject = set(reject)
        self.orders = []

    def positions(self):
        return self._positions

    def cash(self):
        return {"free": self._free, "total": self._free, "invested": 0, "blocked": 0}

    def pending_orders(self):
        if self._pending_rounds > 0:
            self._pending_rounds -= 1
            return [{"id": 1}]
        return []

    @staticmethod
    def round_quantity(amount, price):
        import math
        return math.floor(amount / price * 100) / 100

    def market_order(self, t212, qty):
        if t212 in self.reject:
            raise BrokerError("rejected")
        self.orders.append((t212, qty))
        if qty < 0:
            self._free += -qty * 100
        else:
            self._free -= qty * 100
        return {"id": f"o{len(self.orders)}"}


def _no_sleep(monkeypatch):
    monkeypatch.setattr(executor.time, "sleep", lambda s: None)
    monkeypatch.setattr(executor.store, "log_order", lambda *a, **k: None)


def test_sells_first_exact_quantity_then_buys(monkeypatch):
    _no_sleep(monkeypatch)
    b = FakeBroker(positions={"WORLD": {"quantity": 3.5, "value": 350.0}}, free=100.0)
    trades = [{"key": "SP500", "action": "BUY", "amount_gbp": 300.0, "quantity": None, "reason": "entry"},
              {"key": "WORLD", "action": "SELL", "amount_gbp": 350.0, "quantity": 3.5, "reason": "exit"}]
    r = executor.execute(b, trades, {"SP500": 100.0, "WORLD": 100.0})
    assert b.orders[0] == ("VWRLl_EQ", -3.5) and b.orders[1][0] == "CSP1_EQ" and b.orders[1][1] > 0
    assert r["sells_cleared"] and not r["buys_skipped"] and not r["failed"]


def test_partial_sell_clipped_to_holding(monkeypatch):
    _no_sleep(monkeypatch)
    b = FakeBroker(positions={"GOLD": {"quantity": 2.0, "value": 200.0}})
    trades = [{"key": "GOLD", "action": "SELL", "amount_gbp": 500.0, "quantity": None, "reason": "rebalance"}]
    executor.execute(b, trades, {"GOLD": 100.0})
    assert b.orders == [("SGLNl_EQ", -2.0)]


def test_buys_scaled_when_cash_short(monkeypatch):
    _no_sleep(monkeypatch)
    b = FakeBroker(free=500.0)
    trades = [{"key": "SP500", "action": "BUY", "amount_gbp": 600.0, "quantity": None, "reason": "entry"},
              {"key": "NDX", "action": "BUY", "amount_gbp": 400.0, "quantity": None, "reason": "entry"}]
    r = executor.execute(b, trades, {"SP500": 100.0, "NDX": 100.0})
    total = sum(q for _, q in b.orders) * 100
    assert total <= 500.0 * 0.985 + 1e-6 and len(r["placed"]) == 2


def test_buys_skipped_if_sells_never_clear(monkeypatch):
    _no_sleep(monkeypatch)
    b = FakeBroker(positions={"WORLD": {"quantity": 1.0, "value": 100.0}}, pending_rounds=10_000)
    monkeypatch.setattr(executor, "SETTLEMENT_WAIT_SECONDS", 30)
    trades = [{"key": "WORLD", "action": "SELL", "amount_gbp": 100.0, "quantity": 1.0, "reason": "exit"},
              {"key": "SP500", "action": "BUY", "amount_gbp": 100.0, "quantity": None, "reason": "entry"}]
    r = executor.execute(b, trades, {"SP500": 100.0, "WORLD": 100.0})
    assert r["buys_skipped"] and len(b.orders) == 1


def test_rejected_order_is_recorded_not_raised(monkeypatch):
    _no_sleep(monkeypatch)
    b = FakeBroker(reject={"CSP1_EQ"})
    trades = [{"key": "SP500", "action": "BUY", "amount_gbp": 100.0, "quantity": None, "reason": "entry"}]
    r = executor.execute(b, trades, {"SP500": 100.0})
    assert r["failed"] and r["failed"][0]["error"] == "rejected" and not r["placed"]
