"""
Replay engine.  Uses the real strategy functions (src/strategy.py), the real calendar
(src/trading_calendar.py) and the real quantity rounding (src/broker.py), so the backtest
cannot silently diverge from the live logic.

Conventions: signal on the previous trading day's close; fill at the decision day's close
(or open); orders placed on the Nth trading day of each month.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace

import pandas as pd

from backtest.data import Market
from src.broker import T212
from src.strategy import StrategyParams, compute_signals, target_weights, generate_trades, CASH
from src.trading_calendar import nth_trading_day


@dataclass
class RunOpts:
    name: str = "default"
    universe: str = "lse"
    start: str = "2014-01-01"
    end: str = "2026-12-31"
    capital: float = 5_000.0
    fill: str = "next_close"       # next_close | next_open | same_close
    rebalance_day: int = 1
    cost_bps: float = 0.0
    hist_rows: int = 320


@dataclass
class Result:
    name: str
    equity: pd.DataFrame
    orders: pd.DataFrame
    roundtrips: pd.DataFrame
    signals: pd.DataFrame
    events: dict


class Backtester:
    def __init__(self, mkt: Market, params: StrategyParams, opts: RunOpts):
        self.m, self.p, self.o = mkt, params, opts
        self.px, self.sig = mkt.trade, mkt.signal
        self.dates = self.px.index
        self.keys = list(params.all_keys)

    def _schedule(self, start, end) -> set:
        out = set()
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            rd = nth_trading_day(y, m, self.o.rebalance_day)
            pos = self.dates.searchsorted(pd.Timestamp(rd))
            if pos < len(self.dates) and start <= self.dates[pos] <= end:
                out.add(self.dates[pos])
            m += 1
            if m == 13:
                y, m = y + 1, 1
        return out

    def run(self) -> Result:
        p, o = self.p, self.o
        first_ok = self.px[self.keys].dropna().index[0]
        start = max(pd.Timestamp(o.start), self.dates[min(self.dates.get_loc(first_ok) + p.min_history + 2, len(self.dates) - 1)])
        end = min(pd.Timestamp(o.end), self.dates[-1])
        sched = self._schedule(start, end)
        days = self.dates[(self.dates >= start) & (self.dates <= end)]
        units = {k: 0.0 for k in self.keys}
        avg = {k: 0.0 for k in self.keys}
        trip = {k: None for k in self.keys}
        cash = o.capital
        prev_trend: dict = {}
        peak = cash
        orders, trips, sigs, rows = [], [], [], []
        ev = {"rebalances": 0, "no_trade_months": 0, "cash_shortfall": 0, "signal_errors": 0}
        fee = o.cost_bps / 1e4

        for t in days:
            i = self.dates.get_loc(t)
            if o.fill == "same_close":
                sdate, fpx = t, self.px.loc[t]
            else:
                sdate, fpx = self.dates[i - 1], self.px.loc[t]
            action = ""
            if t in sched:
                ev["rebalances"] += 1
                hist = self.sig.loc[:sdate].tail(o.hist_rows)
                sp = {k: hist[k].dropna() for k in self.keys}
                try:
                    sg = compute_signals(sp, p, prev_trend, date=sdate.date())
                except ValueError as exc:
                    ev["signal_errors"] += 1
                    sigs.append({"date": t.date(), "error": str(exc)})
                    sg = None
                if sg is not None:
                    prev_trend = dict(sg.trend_on)
                    pv = cash + sum(units[k] * fpx[k] for k in self.keys)
                    tw = target_weights(sg, p)
                    positions = {k: {"quantity": units[k], "value": units[k] * fpx[k]} for k in self.keys if units[k] > 0}
                    trades = generate_trades(tw, positions, pv, p)
                    sigs.append({"date": t.date(), "signal_date": sdate.date(), "selected": ",".join(sg.selected_equity),
                                 "fallback": sg.fallback_asset, "trend_on": json.dumps(sg.trend_on),
                                 "mom": json.dumps({k: round(v, 4) for k, v in sg.mom.items()}), "target": json.dumps(tw), "n_trades": len(trades)})
                    if not trades:
                        ev["no_trade_months"] += 1
                    action = "rebalance" if trades else "check"
                    for tr in trades:
                        if tr["action"] != "SELL":
                            continue
                        k, px = tr["key"], fpx[tr["key"]]
                        q = float(tr["quantity"]) if tr["quantity"] else min(T212.round_quantity(tr["amount_gbp"], px), units[k])
                        if q <= 0:
                            continue
                        proceeds = q * px * (1 - fee)
                        cash += proceeds
                        units[k] -= q
                        orders.append(dict(date=t.date(), key=k, side="SELL", amount_gbp=round(q * px, 2), price=round(px, 4), units=q,
                                           cost_gbp=round(q * px * fee, 2), realized_pnl=round(q * (px * (1 - fee) - avg[k]), 2),
                                           realized_pct=round(px * (1 - fee) / avg[k] - 1, 4) if avg[k] else 0.0, reason=tr["reason"]))
                        tp = trip[k]
                        if tp:
                            tp["proceeds"] += proceeds
                            if units[k] < 1e-9:
                                units[k] = 0.0
                                trips.append(dict(key=k, entry=tp["entry"], exit=t.date(), days=(t.date() - tp["entry"]).days,
                                                  cost_basis=round(tp["cost"], 2), proceeds=round(tp["proceeds"], 2),
                                                  pnl=round(tp["proceeds"] - tp["cost"], 2), ret=round(tp["proceeds"] / tp["cost"] - 1, 4), open=False))
                                trip[k] = None
                                avg[k] = 0.0
                    buys = [x for x in trades if x["action"] == "BUY"]
                    need = sum(x["amount_gbp"] for x in buys) * (1 + fee)
                    scale = 1.0
                    if need > cash > 0:
                        ev["cash_shortfall"] += 1
                        scale = cash / need * 0.999
                    for tr in buys:
                        k, px = tr["key"], fpx[tr["key"]]
                        q = T212.round_quantity(tr["amount_gbp"] * scale, px)
                        cost = q * px * (1 + fee)
                        if q <= 0 or cost > cash:
                            continue
                        avg[k] = (avg[k] * units[k] + cost) / (units[k] + q)
                        units[k] += q
                        cash -= cost
                        if trip[k] is None:
                            trip[k] = {"entry": t.date(), "cost": 0.0, "proceeds": 0.0}
                        trip[k]["cost"] += cost
                        orders.append(dict(date=t.date(), key=k, side="BUY", amount_gbp=round(q * px, 2), price=round(px, 4), units=q,
                                           cost_gbp=round(q * px * fee, 2), realized_pnl=0.0, realized_pct=0.0, reason=tr["reason"]))
            cpx = self.px.loc[t]
            eq = cash + sum(units[k] * cpx[k] for k in self.keys)
            peak = max(peak, eq)
            row = {"date": t, "equity": eq, "cash": cash, "drawdown": eq / peak - 1, "action": action}
            for k in self.keys:
                row[f"w_{k}"] = units[k] * cpx[k] / eq if eq else 0
            rows.append(row)
        last = self.px.loc[:end].iloc[-1]
        for k, tp in trip.items():
            if tp and units[k] > 0:
                mv = units[k] * last[k]
                trips.append(dict(key=k, entry=tp["entry"], exit=end.date(), days=(end.date() - tp["entry"]).days, cost_basis=round(tp["cost"], 2),
                                  proceeds=round(tp["proceeds"] + mv, 2), pnl=round(tp["proceeds"] + mv - tp["cost"], 2),
                                  ret=round((tp["proceeds"] + mv) / tp["cost"] - 1, 4), open=True))
        return Result(o.name, pd.DataFrame(rows).set_index("date"), pd.DataFrame(orders), pd.DataFrame(trips), pd.DataFrame(sigs), ev)


_MARKETS: dict[str, Market] = {}


def run(params: StrategyParams, opts: RunOpts) -> Result:
    from backtest.data import load_market
    if opts.universe not in _MARKETS:
        _MARKETS[opts.universe] = load_market(opts.universe)
    return Backtester(_MARKETS[opts.universe], params, opts).run()
