"""
Supabase audit log.  Every function is best-effort: it prints and returns None on failure
so that logging can never abort a rebalance.  Uses the existing schema (sql/schema.sql);
strategy-specific detail goes into the JSONB columns.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import numpy as np


def _clean(x):
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if hasattr(x, "isoformat"):
        return x.isoformat()
    return x


def _client():
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
    from supabase import create_client
    return create_client(url, key)


def _insert(table: str, payload: dict):
    try:
        r = _client().table(table).insert(_clean(payload)).execute()
        if r.data:
            return r.data[0].get("id")
        print(f"✗ {table}: insert returned no data")
    except Exception as exc:
        print(f"✗ {table}: {exc}")
    return None


def env() -> str:
    return os.getenv("ENVIRONMENT", "demo").upper()


def log_snapshot(check_type: str, sig, target: dict, drawdown: float, extra: dict | None = None):
    risk_on = bool(sig.selected_equity)
    payload = {
        "environment": env(), "check_type": check_type,
        "regime": "risk_on" if risk_on else "risk_off",
        "cspx_price": None, "cspx_200ma": None, "fast_crash_triggered": False, "ten_day_return": None,
        "portfolio_drawdown_pct": round(drawdown * 100, 3), "circuit_breaker_triggered": False,
        "growth_rankings": {"date": str(sig.date), "momentum": sig.mom, "def_momentum": sig.def_mom,
                            "sma_ratio": sig.sma_ratio, "trend_on": sig.trend_on, "fallback": sig.fallback_asset,
                            **(extra or {})},
        "defensive_rankings": {"target_weights": target},
        "selected_growth": list(sig.selected_equity),
    }
    return _insert("signal_snapshots", payload)


def log_decision(snapshot_id, trades: list[dict], message: str, response: str):
    payload = {
        "signal_snapshot_id": snapshot_id, "environment": env(), "trade_list": trades,
        "total_buy_amount": float(sum(t["amount_gbp"] for t in trades if t["action"] == "BUY")),
        "total_sell_amount": float(sum(t["amount_gbp"] for t in trades if t["action"] == "SELL")),
        "telegram_message_sent": message, "user_response": response,
        "responded_at": datetime.now(timezone.utc).isoformat(),
    }
    if snapshot_id is None:
        print("⚠️  no snapshot id; trade decision not logged (FK)")
        return None
    return _insert("trade_decisions", payload)


def log_order(decision_id, key: str, action: str, amount_gbp: float, order_id, status: str, error: str | None = None):
    if decision_id is None:
        return None
    return _insert("executed_orders", {"trade_decision_id": decision_id, "environment": env(), "ticker": key,
                                       "action": action, "amount_gbp": float(amount_gbp), "t212_order_id": str(order_id) if order_id else None,
                                       "status": status, "error_message": error})


def log_portfolio_value(total: float, cash: float, invested: float):
    return _insert("portfolio_value_history", {"environment": env(), "total_value_gbp": float(total),
                                               "cash_gbp": float(cash), "invested_gbp": float(invested)})


def heartbeat():
    return _insert("heartbeat", {"status": "alive"})


def portfolio_history(limit: int = 400) -> list[float]:
    try:
        r = (_client().table("portfolio_value_history").select("total_value_gbp,created_at")
             .eq("environment", env()).order("created_at", desc=True).limit(limit).execute())
        vals = [float(x["total_value_gbp"]) for x in (r.data or [])]
        return list(reversed(vals))
    except Exception as exc:
        print(f"✗ portfolio_value_history: {exc}")
        return []


def drawdown(history: list[float]) -> float:
    if len(history) < 2:
        return 0.0
    peak = max(history)
    return float((peak - history[-1]) / peak) if peak > 0 else 0.0
