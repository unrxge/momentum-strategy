"""
Fill reconciliation: match placed orders against Trading 212's order history so that the
price actually paid — not the price the sizing assumed — is recorded.

Without this, `executed_orders.amount_gbp` is only an intention and slippage, rounding loss
and true cost drag are unmeasurable.  Everything here is best-effort: a failure logs and
returns, never affecting the trades themselves.
"""
from __future__ import annotations

import time

from src.broker import T212
from src.config import BY_KEY
from src import store


def _num(d: dict, *names):
    for n in names:
        v = d.get(n)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _when(d: dict):
    for n in ("dateExecuted", "dateModified", "dateCreated"):
        if d.get(n):
            return d[n]
    return None


def slippage_bps(action: str, intended: float, fill: float) -> float | None:
    """Cost in basis points, signed so that positive always means worse than intended."""
    if not intended or not fill or intended <= 0:
        return None
    diff = (fill - intended) if action == "BUY" else (intended - fill)
    return round(diff / intended * 10_000, 2)


def reconcile(broker: T212, placed: list[dict], prices_gbp: dict[str, float],
              settle_seconds: int = 20, limit: int = 50) -> list[dict]:
    """
    placed: the executor's placed list ({key, action, amount_gbp, quantity, order_id}).
    Returns one dict per matched fill and writes it to Supabase.
    """
    if not placed:
        return []
    time.sleep(settle_seconds)                      # give the broker a moment to book the fills
    history = broker.order_history(limit=limit)
    by_id = {str(h.get("id")): h for h in history if h.get("id") is not None}
    out = []
    for p in placed:
        oid = p.get("order_id")
        row = by_id.get(str(oid)) if oid else None
        if row is None:
            print(f"  · no fill row yet for {p['action']} {p['key']} (order {oid})")
            continue
        inst = BY_KEY.get(p["key"])
        fill = _num(row, "fillPrice", "filledPrice", "averagePrice")
        if fill is not None and inst is not None and inst.quote == "GBX":
            fill /= 100.0                            # the API quotes GBX lines in pence
        qty = _num(row, "filledQuantity", "orderedQuantity")
        # value from our own fill × quantity: the API's fillCost mixes units across GBX lines
        value = abs(fill * qty) if (fill is not None and qty) else None
        intended = prices_gbp.get(p["key"])
        bps = slippage_bps(p["action"], intended, fill) if fill else None
        store.update_order_fill(oid, fill, qty, value, _when(row), bps)
        out.append({"key": p["key"], "action": p["action"], "order_id": oid, "intended_price_gbp": intended,
                    "fill_price_gbp": fill, "filled_quantity": qty, "slippage_bps": bps})
        tail = f" vs intended {intended:.4f} ({bps:+.1f} bps)" if bps is not None else ""
        print(f"  · fill {p['action']} {p['key']}: {fill}{tail}")
    return out


def sync_cashflows(broker: T212, limit: int = 50) -> int:
    """Copy any new deposits/withdrawals into Supabase so they don't read as strategy return."""
    rows = []
    for t in broker.transactions(limit=limit):
        ext = t.get("reference") or t.get("id") or t.get("dateTime")
        amt = _num(t, "amount")
        if ext is None or amt is None:
            continue
        kind = str(t.get("type") or "OTHER").upper()
        if "DEPOSIT" in kind:
            kind = "DEPOSIT"
        elif "WITHDRAW" in kind:
            kind = "WITHDRAWAL"
        else:
            kind = "OTHER"
        rows.append({"external_id": str(ext), "occurred_at": t.get("dateTime"), "kind": kind,
                     "amount_gbp": amt, "raw": t})
    n = store.log_cashflows(rows)
    if n:
        print(f"  · {n} cash movement(s) synced")
    return n
