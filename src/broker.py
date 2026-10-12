"""
Trading 212 client (demo or live, chosen by ENVIRONMENT).

Only the endpoints the strategy needs.  Position values are normalised to GBP using the
instrument's quote unit from config (GBX ÷ 100) — no metadata call at runtime.
"""
from __future__ import annotations

import base64
import itertools
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
        f = lambda k: float(r.get(k) or 0)
        # invested is cost basis and ppl the unrealised gain on it, so invested + ppl is what
        # the holdings are worth now — the figure the T212 app shows as "Investments".
        return {"free": f("free"), "total": f("total"), "invested": f("invested"), "ppl": f("ppl"),
                "result": f("result"), "blocked": f("blocked"), "holdings_value": f("invested") + f("ppl"),
                "raw": r}

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
        """The account as Trading 212 itself reports it.

        total and invested are T212's own figures, not a price x quantity sum of ours, so they
        match the app to the penny.  Our per-position values are reconciled against them."""
        c = self.cash()
        pos = self.positions()
        residual = resolve_other_units(pos, c["holdings_value"])
        recomputed = sum(v["value"] for v in pos.values())
        return {"cash": c, "positions": pos, "invested": c["holdings_value"],
                "total": c["total"] or (c["free"] + recomputed),
                "recomputed_invested": recomputed, "reconcile_residual": residual}


def resolve_other_units(positions: dict, holdings_value: float) -> float:
    """Decide pence or pounds for holdings outside this bot's universe; return the residual.

    T212 quotes most London lines in pence and a few in pounds.  The universe's units are in
    config; for anything else (the other bot's holdings on a shared account) they are not, and
    reading pence as pounds overstates a holding a hundredfold.  T212's own valuation of all
    holdings settles it: pick the units under which our position values add up to it."""
    other = [k for k in positions if k.startswith("OTHER:")]
    if not other or len(other) > 10 or holdings_value <= 0:
        return abs(sum(v["value"] for v in positions.values()) - holdings_value)
    known = sum(v["value"] for k, v in positions.items() if k not in other)
    raw = [positions[k]["quantity"] * positions[k]["price_gbp"] for k in other]
    best = min(itertools.product((0.01, 1.0), repeat=len(other)),
               key=lambda u: abs(known + sum(a * b for a, b in zip(u, raw)) - holdings_value))
    for k, unit in zip(other, best):
        positions[k]["price_gbp"] *= unit
        positions[k]["value"] = positions[k]["quantity"] * positions[k]["price_gbp"]
    return abs(sum(v["value"] for v in positions.values()) - holdings_value)
