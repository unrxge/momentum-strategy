"""
Trading 212 client (demo or live, chosen by ENVIRONMENT).

Only the endpoints the strategy needs.  Position values are normalised to GBP using the
instrument's quote unit from config (GBX ÷ 100) — no metadata call at runtime.
"""
from __future__ import annotations

import base64
import math
import os
import time

import requests

from src.config import BY_T212, QUANTITY_DP


class BrokerError(RuntimeError):
    pass


class T212:
    def __init__(self):
        env = os.getenv("ENVIRONMENT", "demo").lower()
        if env not in ("demo", "live"):
            raise BrokerError(f"ENVIRONMENT must be demo or live, got {env!r}")
        prefix = f"T212_{env.upper()}"
        key, secret, base = os.getenv(f"{prefix}_API_KEY"), os.getenv(f"{prefix}_API_SECRET"), os.getenv(f"{prefix}_BASE_URL")
        if not (key and secret and base):
            raise BrokerError(f"missing {prefix}_API_KEY / _API_SECRET / _BASE_URL")
        self.environment = env
        self.base = base.rstrip("/")
        token = base64.b64encode(f"{key}:{secret}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    # ------------------------------------------------------------ transport
    def _request(self, method: str, path: str, retries: int = 4, **kw):
        last = None
        for attempt in range(retries):
            try:
                r = requests.request(method, f"{self.base}/{path}", headers=self.headers, timeout=(20, 90), **kw)
            except requests.RequestException as exc:
                last = exc
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                last = BrokerError("429 rate limited")
                continue
            if r.status_code in (401, 403):
                raise BrokerError(f"authentication failed ({r.status_code}) for {self.environment}")
            if r.status_code >= 400:
                raise BrokerError(f"{method} {path} → {r.status_code}: {r.text[:300]}")
            return r.json() if r.text else {}
        raise BrokerError(f"{method} {path} failed after {retries} attempts: {last}")

    # ------------------------------------------------------------ account
    def cash(self) -> dict:
        r = self._request("GET", "equity/account/cash")
        return {"free": float(r.get("free", 0)), "total": float(r.get("total", 0)),
                "invested": float(r.get("invested", 0)), "blocked": float(r.get("blocked", 0)), "raw": r}

    def positions(self) -> dict[str, dict]:
        """{instrument key: {quantity, price_gbp, value, t212}} for universe holdings; others under 'OTHER:<ticker>'."""
        r = self._request("GET", "equity/portfolio")
        rows = r if isinstance(r, list) else r.get("positions", [])
        out = {}
        for p in rows:
            t = p.get("ticker")
            qty = float(p.get("quantity", 0) or 0)
            px = float(p.get("currentPrice", 0) or 0)
            inst = BY_T212.get(t)
            if inst is None:
                out[f"OTHER:{t}"] = {"quantity": qty, "price_gbp": px, "value": px * qty, "t212": t}
                continue
            if inst.quote == "GBX":
                px = px / 100.0
            out[inst.key] = {"quantity": qty, "price_gbp": px, "value": px * qty, "t212": t}
        return out

    def pending_orders(self) -> list[dict]:
        r = self._request("GET", "equity/orders")
        return r if isinstance(r, list) else r.get("orders", [])

    # ------------------------------------------------------------ history (dashboard instrumentation)
    def order_history(self, limit: int = 50) -> list[dict]:
        """Executed orders with fill price and filled quantity.  Best-effort: [] on failure."""
        try:
            r = self._request("GET", f"equity/history/orders?limit={int(limit)}", retries=2)
        except BrokerError as exc:
            print(f"\u26a0\ufe0f  order history unavailable: {exc}")
            return []
        return r if isinstance(r, list) else (r.get("items") or [])

    def transactions(self, limit: int = 50) -> list[dict]:
        """Cash movements (deposits/withdrawals).  Best-effort: [] on failure."""
        try:
            r = self._request("GET", f"equity/history/transactions?limit={int(limit)}", retries=2)
        except BrokerError as exc:
            print(f"\u26a0\ufe0f  transaction history unavailable: {exc}")
            return []
        return r if isinstance(r, list) else (r.get("items") or [])

    # ------------------------------------------------------------ orders
    @staticmethod
    def round_quantity(amount_gbp: float, price_gbp: float) -> float:
        if price_gbp <= 0:
            raise BrokerError(f"invalid price {price_gbp}")
        # floor, not round: a rounded-up buy can exceed available cash by pennies and be rejected
        factor = 10 ** QUANTITY_DP
        return math.floor(amount_gbp / price_gbp * factor) / factor

    def market_order(self, t212_ticker: str, quantity: float) -> dict:
        """quantity > 0 buys, < 0 sells.  Returns the order dict (with 'id') or raises BrokerError."""
        body = {"ticker": t212_ticker, "quantity": round(quantity, QUANTITY_DP)}
        return self._request("POST", "equity/orders/market", retries=2, json=body)

    def snapshot(self) -> dict:
        c = self.cash()
        pos = self.positions()
        invested = sum(v["value"] for v in pos.values())
        return {"cash": c, "positions": pos, "invested": invested, "total": c["free"] + invested}
