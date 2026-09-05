"""
Order execution: sells first, wait for them to clear, then buys.

Sizing rules (v1 audit fixes): full exits use the exact held quantity; partial sells are
clipped to the holding; buys are scaled down pro rata if cash is short; every quantity is
rounded to QUANTITY_DP.  Every order is logged to Supabase whether it succeeded or not.
"""
from __future__ import annotations

import time

from src.broker import T212, BrokerError
from src.config import BY_KEY, SETTLEMENT_WAIT_SECONDS
from src import store


def wait_for_clear(broker: T212, max_wait: int = SETTLEMENT_WAIT_SECONDS, poll: int = 15) -> bool:
    waited = 0
    while True:
        try:
            pending = broker.pending_orders()
        except BrokerError as exc:
            print(f"⚠️  pending-orders check failed: {exc}")
            pending = []
        if not pending:
            return True
        if waited >= max_wait:
            return False
        time.sleep(poll)
        waited += poll
        print(f"  … {len(pending)} order(s) still pending after {waited}s")


def execute(broker: T212, trades: list[dict], prices_gbp: dict[str, float], decision_id=None,
            pace_seconds: float = 2.0) -> dict:
    """
    trades: from strategy.generate_trades (keys, GBP amounts, optional exact quantity).
    prices_gbp: {key: last close in GBP} for sizing.
    Returns {"placed": [...], "failed": [...], "sells_cleared": bool, "buys_skipped": bool}.
    """
    placed, failed = [], []
    sells = [t for t in trades if t["action"] == "SELL"]
    buys = [t for t in trades if t["action"] == "BUY"]
    held = broker.positions()

    def place(t, qty):
        key = t["key"]
        t212 = BY_KEY[key].t212
        signed = -abs(qty) if t["action"] == "SELL" else abs(qty)
        intended = prices_gbp.get(key)      # the price the sizing assumed; fills are measured against it
        try:
            resp = broker.market_order(t212, signed)
            oid = resp.get("id") if isinstance(resp, dict) else None
            print(f"  ✓ {t['action']} {key} qty={signed} → order {oid}")
            store.log_order(decision_id, key, t["action"], t["amount_gbp"], oid, "submitted",
                            intended_price_gbp=intended)
            placed.append({**t, "quantity": signed, "order_id": oid, "intended_price_gbp": intended})
        except BrokerError as exc:
            print(f"  ✗ {t['action']} {key} qty={signed}: {exc}")
            store.log_order(decision_id, key, t["action"], t["amount_gbp"], None, "failed", str(exc),
                            intended_price_gbp=intended)
            failed.append({**t, "quantity": signed, "error": str(exc)})
        time.sleep(pace_seconds)

    # ---- phase 1: sells
    for t in sells:
        key = t["key"]
        held_qty = float(held.get(key, {}).get("quantity", 0.0))
        if t.get("quantity"):
            qty = round(min(float(t["quantity"]), held_qty) if held_qty else float(t["quantity"]), 2)
        else:
            qty = broker.round_quantity(t["amount_gbp"], prices_gbp[key])
            if held_qty and qty > held_qty:
                qty = round(held_qty, 2)
        if qty <= 0:
            failed.append({**t, "quantity": 0, "error": "nothing to sell"})
            continue
        place(t, qty)

    sells_cleared = True
    if sells:
        sells_cleared = wait_for_clear(broker)
        if not sells_cleared:
            print("⏸️  sells still pending after the wait window — buys NOT placed")
            return {"placed": placed, "failed": failed, "sells_cleared": False, "buys_skipped": True}

    # ---- phase 2: buys (re-read free cash; scale down if short)
    if buys:
        try:
            free = broker.cash()["free"]
        except BrokerError as exc:
            print(f"⚠️  cash read failed before buys: {exc}")
            free = sum(b["amount_gbp"] for b in buys)
        need = sum(b["amount_gbp"] for b in buys)
        scale = min(1.0, (free * 0.985) / need) if need > 0 else 1.0
        if scale < 1.0:
            print(f"  cash short: scaling buys by {scale:.3f}")
        for t in buys:
            qty = broker.round_quantity(t["amount_gbp"] * scale, prices_gbp[t["key"]])
            if qty <= 0:
                failed.append({**t, "quantity": 0, "error": "rounded to zero"})
                continue
            place({**t, "amount_gbp": round(t["amount_gbp"] * scale, 2)}, qty)
    return {"placed": placed, "failed": failed, "sells_cleared": sells_cleared, "buys_skipped": False}
