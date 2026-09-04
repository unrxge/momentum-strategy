#!/usr/bin/env python3
"""
backtest_v2.py — replay of Strategy v2 (src/strategy/v2.py) on the same data, engine
conventions and analysis tooling as backtest.py (v1 / live-logic replay).

    python backtest_v2.py                # default params, live universe 2014→
    python backtest_v2.py --all          # variants, proxy 2006→, walk-forward, sensitivity, bootstrap
    python backtest_v2.py --stage variants,proxy

Signals: prior trading day's close, fills at the decision day's close (same as v1 baseline).
Outputs: backtest_output/v2_*.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import warnings
from dataclasses import dataclass, replace, asdict

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
for extra in os.environ.get("BACKTEST_PYLIB", "").split(os.pathsep):
    if extra:
        sys.path.append(extra)

import backtest as bt  # noqa: E402  (data loading, metrics, benchmarks, bootstrap, plotting)
from src.strategy.v2 import StrategyParams, compute_signals, target_weights, generate_trades, signal_prices, CASH  # noqa: E402
from src.execution.t212_client import T212Client  # noqa: E402

OUT = bt.OUT_DIR
PRECISION = {"SGLN.L": 2}


@dataclass
class RunOpts:
    name: str = "v2_default"
    universe: str = "live"          # live | proxy
    start: str = "2014-06-01"
    end: str = "2026-09-04"
    capital: float = 20_000.0
    fill: str = "next_close"        # next_close | next_open | same_close
    rebalance_nth_day: int = 1      # literature default: first trading day (month-end signal)
    cost_bps: float = 0.0
    signal_currency: str = "mixed"  # mixed (equities USD, defensives GBP) | underlying | gbp | live (CSPX in USD, as v1)
    hist_rows: int = 320


_MKT: dict = {}


def market(universe: str, signal_currency: str) -> bt.Market:
    key = (universe, signal_currency)
    if key in _MKT:
        return _MKT[key]
    m = bt.load_market(universe, "gbp" if signal_currency in ("underlying", "mixed") else signal_currency)
    if signal_currency in ("underlying", "mixed"):
        # Same code path as the live pipeline: src.strategy.v2.signal_prices()
        fx = bt.fetch_yf("GBPUSD=X")["Close"]
        fx = fx[~fx.index.duplicated()]
        m._sig_mode = signal_currency
        m._fx = fx
    if False:
        # Signals measured in each fund's base currency (USD for the five non-gilt funds): GBP price × GBPUSD.
        # Removes sterling moves from the trend/momentum tests; the LSE lines are still what is traded in GBP.
        fx = bt.fetch_yf("GBPUSD=X")["Close"]
        fx = fx[~fx.index.duplicated()].reindex(m.signal_px.index.union(fx.index)).ffill().reindex(m.signal_px.index)
        # "mixed": equities on the underlying (USD) — filters equity bear markets without sterling noise;
        #          defensives (gold, gilts) in GBP — their job is to protect GBP wealth, so their trend is GBP.
        for tk in (("CSPX.L", "EQQQ.L", "VWRL.L", "VEUR.L") if signal_currency == "mixed" else ("CSPX.L", "EQQQ.L", "VWRL.L", "VEUR.L", "SGLN.L")):
            m.signal_px[tk] = m.close_gbp[tk] * fx
    # optional 7th instrument: all-maturity gilts (IGLT.L, GBP) / IEF as its proxy (USD, bond-only returns)
    if universe == "live":
        x = bt.fetch_yf("IGLT.L")
        c, o = x["Close"], x["Open"]
    else:
        x = bt.fetch_yf("IEF")
        c, o = x["Close"], x["Open"]
    for df, s in ((m.close_gbp, c), (m.open_gbp, o), (m.signal_px, c)):
        df["IGLT.L"] = s.reindex(df.index).ffill()
    if getattr(m, "_sig_mode", None):
        sp = signal_prices({c: m.close_gbp[c] for c in m.close_gbp.columns}, m._fx, StrategyParams(signal_currency=m._sig_mode))
        m.signal_px = pd.DataFrame(sp).reindex(m.close_gbp.index)
    _MKT[key] = m
    return m


class V2Backtester:
    def __init__(self, mkt: bt.Market, params: StrategyParams, opts: RunOpts):
        self.m, self.p, self.o = mkt, params, opts
        self.px, self.opn, self.sig = mkt.close_gbp, mkt.open_gbp, mkt.signal_px
        self.dates = self.px.index
        self.tickers = list(params.all_tickers)
        self.qty = T212Client.calculate_order_quantity

    def _schedule(self, start, end):
        out = {}
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            rd = bt.get_nth_trading_day_of_month(y, m, self.o.rebalance_nth_day)
            pos = self.dates.searchsorted(pd.Timestamp(rd))
            if pos < len(self.dates) and start <= self.dates[pos] <= end:
                out[self.dates[pos]] = pd.Timestamp(rd)
            m += 1
            if m == 13:
                y, m = y + 1, 1
        return out

    def run(self) -> bt.Result:
        p, o = self.p, self.o
        first_ok = self.px[self.tickers].dropna().index[0]
        start = max(pd.Timestamp(o.start), self.dates[self.dates.get_loc(first_ok) + p.min_history + 2])
        end = min(pd.Timestamp(o.end), self.dates[-1])
        sched = self._schedule(start, end)
        days = self.dates[(self.dates >= start) & (self.dates <= end)]
        units = {t: 0.0 for t in self.tickers}
        avg = {t: 0.0 for t in self.tickers}
        trip = {t: None for t in self.tickers}
        cash = o.capital
        prev_trend = {}
        peak = cash
        orders, trips, sigs, rows = [], [], [], []
        ev = {"rebalances": 0, "no_trade_months": 0, "cash_shortfall": 0, "signal_errors": 0}
        fee = o.cost_bps / 1e4

        for t in days:
            i = self.dates.get_loc(t)
            if o.fill == "same_close":
                sdate, fpx = t, self.px.loc[t]
            elif o.fill == "next_open":
                sdate, fpx = self.dates[i - 1], self.opn.loc[t]
            else:
                sdate, fpx = self.dates[i - 1], self.px.loc[t]
            action = ""
            if t in sched:
                ev["rebalances"] += 1
                hist = self.sig.loc[:sdate].tail(o.hist_rows)
                pdata = {k: hist[k].dropna().reset_index(drop=True) for k in self.tickers}
                try:
                    sg = compute_signals(pdata, p, prev_trend, date=sdate.date())
                except ValueError as exc:
                    ev["signal_errors"] += 1
                    sigs.append({"date": t.date(), "error": str(exc)})
                    sg = None
                if sg is not None:
                    prev_trend = dict(sg.trend_on)
                    pv = cash + sum(units[k] * fpx[k] for k in self.tickers)
                    tw = target_weights(sg, p, pdata)
                    positions = {k: {"quantity": units[k], "current_value": units[k] * fpx[k]} for k in self.tickers if units[k] > 0}
                    trades = generate_trades(tw, positions, pv, p)
                    sigs.append({"date": t.date(), "signal_date": sdate.date(), "cash_ret": round(sg.cash_ret, 4),
                                 "selected": ",".join(sg.selected_equity), "fallback": sg.fallback_asset,
                                 "trend_on": json.dumps({k: v for k, v in sg.trend_on.items()}),
                                 "mom": json.dumps({k: round(v, 4) for k, v in sg.mom.items()}),
                                 "target": json.dumps(tw), "n_trades": len(trades)})
                    if not trades:
                        ev["no_trade_months"] += 1
                    action = "rebalance" if trades else "check"
                    for tr in trades:
                        if tr["action"] != "SELL":
                            continue
                        k, px = tr["ticker"], fpx[tr["ticker"]]
                        q = tr["quantity"] if tr["quantity"] else min(self.qty(None, tr["amount_gbp"], px, PRECISION.get(k, 4)), units[k])
                        if q <= 0:
                            continue
                        proceeds = q * px * (1 - fee)
                        cash += proceeds
                        units[k] -= q
                        orders.append(dict(date=t.date(), signal_date=sdate.date(), ticker=k, side="SELL", amount_gbp=round(q * px, 2),
                                           price=round(px, 4), units=q, cost_gbp=round(q * px * fee, 2),
                                           realized_pnl=round(q * (px * (1 - fee) - avg[k]), 2),
                                           realized_pct=round(px * (1 - fee) / avg[k] - 1, 4) if avg[k] else 0.0, reason=tr["reason"]))
                        tp = trip[k]
                        if tp:
                            tp["proceeds"] += proceeds
                            if units[k] < 1e-9:
                                units[k] = 0.0
                                trips.append(dict(ticker=k, entry=tp["entry"], exit=t.date(), days=(t.date() - tp["entry"]).days,
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
                        k, px = tr["ticker"], fpx[tr["ticker"]]
                        q = self.qty(None, tr["amount_gbp"] * scale, px, PRECISION.get(k, 4))
                        cost = q * px * (1 + fee)
                        if q <= 0 or cost > cash:
                            continue
                        avg[k] = (avg[k] * units[k] + cost) / (units[k] + q)
                        units[k] += q
                        cash -= cost
                        if trip[k] is None:
                            trip[k] = {"entry": t.date(), "cost": 0.0, "proceeds": 0.0}
                        trip[k]["cost"] += cost
                        orders.append(dict(date=t.date(), signal_date=sdate.date(), ticker=k, side="BUY", amount_gbp=round(q * px, 2),
                                           price=round(px, 4), units=q, cost_gbp=round(q * px * fee, 2), realized_pnl=0.0, realized_pct=0.0, reason=tr["reason"]))
            cpx = self.px.loc[t]
            eq = cash + sum(units[k] * cpx[k] for k in self.tickers)
            peak = max(peak, eq)
            row = {"date": t, "equity": eq, "cash": cash, "drawdown": eq / peak - 1, "action": action}
            for k in bt.ALL_TICKERS + ["IGLT.L"]:
                row[f"w_{k}"] = units.get(k, 0.0) * cpx[k] / eq if eq else 0
            rows.append(row)
        last = self.px.loc[:end].iloc[-1]
        for k, tp in trip.items():
            if tp and units[k] > 0:
                mv = units[k] * last[k]
                trips.append(dict(ticker=k, entry=tp["entry"], exit=end.date(), days=(end.date() - tp["entry"]).days, cost_basis=round(tp["cost"], 2),
                                  proceeds=round(tp["proceeds"] + mv, 2), pnl=round(tp["proceeds"] + mv - tp["cost"], 2),
                                  ret=round((tp["proceeds"] + mv) / tp["cost"] - 1, 4), open=True))
        equity = pd.DataFrame(rows).set_index("date")
        cfg = bt.Config(name=o.name)
        return bt.Result(cfg, equity, pd.DataFrame(orders), pd.DataFrame(trips), pd.DataFrame(sigs), ev, [])


def run(params: StrategyParams, opts: RunOpts) -> bt.Result:
    return V2Backtester(market(opts.universe, opts.signal_currency), params, opts).run()


def summarize(res: bt.Result, label: str) -> dict:
    wcols = [c for c in res.equity.columns if c.startswith("w_")]
    m = bt.metrics(res.equity.equity, res.orders, res.roundtrips, res.equity[wcols])
    return {"variant": label, **m, **{f"ev_{k}": v for k, v in res.events.items()}}


def save(res: bt.Result, tag: str):
    res.equity.to_csv(os.path.join(OUT, f"{tag}_equity.csv"))
    res.orders.to_csv(os.path.join(OUT, f"{tag}_orders.csv"), index=False)
    res.roundtrips.to_csv(os.path.join(OUT, f"{tag}_roundtrips.csv"), index=False)
    res.signals.to_csv(os.path.join(OUT, f"{tag}_signals.csv"), index=False)


def fmt_table(df: pd.DataFrame) -> str:
    cols = ["variant", "cagr", "ann_vol", "sharpe", "sortino", "max_dd", "calmar", "monthly_win_rate", "turnover_ann", "orders", "rt_win_rate", "rt_profit_factor", "exposure_avg"]
    t = df[[c for c in cols if c in df.columns]].copy()
    for c in ("cagr", "ann_vol", "max_dd", "monthly_win_rate", "rt_win_rate", "exposure_avg"):
        if c in t:
            t[c] = (t[c] * 100).round(1).astype(str) + "%"
    for c in ("sharpe", "sortino", "calmar", "rt_profit_factor"):
        if c in t:
            t[c] = t[c].round(2)
    if "turnover_ann" in t:
        t["turnover_ann"] = t.turnover_ann.round(2)
    return bt.md_table(t)


def variants(base: StrategyParams, bo: RunOpts) -> list[tuple[str, StrategyParams, RunOpts]]:
    V = []
    add = lambda n, p=None, o=None: V.append((n, p or base, replace(o or bo, name=n)))
    add("v2_default")
    # --- ablation: what each rule contributes ---
    add("v2_abs_momentum_on", replace(base, abs_momentum=True))
    add("v2_no_trend_filter", replace(base, trend_filter=False))
    add("v2_no_trend_no_selection", replace(base, trend_filter=False, top_n=4))
    add("v2_plain_12m_momentum", replace(base, mom_skip=0))
    add("v2_bonds_IGLS_short", replace(base, bonds="IGLS.L"))
    add("v2_fallback_cash", replace(base, fallback="cash"))
    add("v2_fallback_best_defensive", replace(base, fallback="best_defensive"))
    add("v2_no_max_weight_cap", replace(base, max_weight=1.0))
    add("v2_top1_GEM", replace(base, top_n=1))
    add("v2_top3", replace(base, top_n=3))
    add("v2_top4_no_selection", replace(base, top_n=4))
    # --- strategic mix (risk dial) ---
    add("v2_mix_80_10_10_growth", replace(base, w_equity=0.80, w_gold=0.10, w_bonds=0.10))
    add("v2_mix_60_20_20_defensive", replace(base, w_equity=0.60, w_gold=0.20, w_bonds=0.20))
    add("v2_mix_75_15_10", replace(base, w_equity=0.75, w_gold=0.15, w_bonds=0.10))
    # --- parameter sensitivity ±20% ---
    add("v2_sma_168", replace(base, trend_sma=168))
    add("v2_sma_252", replace(base, trend_sma=252))
    add("v2_mom_202", replace(base, mom_lookback=202))
    add("v2_mom_302", replace(base, mom_lookback=302))
    add("v2_def_lookback_100", replace(base, def_lookback=100))
    add("v2_def_lookback_152", replace(base, def_lookback=152))
    add("v2_hysteresis_2pct", replace(base, trend_band=0.02))
    add("v2_rebal_band_0", replace(base, rebalance_band=0.0))
    add("v2_rebal_band_10pct", replace(base, rebalance_band=0.10))
    add("v2_vol_target_10", replace(base, vol_target=0.10))
    # --- timing / currency / costs ---
    add("v2_rebalance_5th_td", None, replace(bo, rebalance_nth_day=5))
    add("v2_rebalance_10th_td", None, replace(bo, rebalance_nth_day=10))
    add("v2_fill_next_open", None, replace(bo, fill="next_open"))
    add("v2_fill_same_close_LOOKAHEAD", None, replace(bo, fill="same_close"))
    add("v2_signals_underlying_all_usd", replace(base, signal_currency="underlying"), replace(bo, signal_currency="underlying"))
    add("v2_signals_gbp_all", replace(base, signal_currency="gbp"), replace(bo, signal_currency="gbp"))
    add("v2_cost_10bps", None, replace(bo, cost_bps=10))
    add("v2_cost_25bps", None, replace(bo, cost_bps=25))
    add("v2_capital_5000", None, replace(bo, capital=5000))
    return V


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--stage", default="", help="variants,proxy,walkforward,bootstrap (default all with --all)")
    ap.add_argument("--start", default=RunOpts.start)
    ap.add_argument("--end", default=RunOpts.end)
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    stages = set(args.stage.split(",")) if args.stage else ({"variants", "proxy", "walkforward", "bootstrap"} if args.all else {"default"})
    base = StrategyParams()
    bo = RunOpts(start=args.start, end=args.end)
    t0 = time.time()

    # ---------------- default + variants (live universe) ----------------
    rows = []
    todo = variants(base, bo) if "variants" in stages else [("v2_default", base, bo)]
    for name, p, o in todo:
        r = run(p, o)
        save(r, name)
        s = summarize(r, name)
        rows.append(s)
        print(f"{name:34s} CAGR={s['cagr']:+.2%} vol={s['ann_vol']:.2%} Sharpe={s['sharpe']:.2f} Sortino={s['sortino']:.2f} MaxDD={s['max_dd']:.1%} "
              f"Calmar={s['calmar']:.2f} orders={s['orders']} turnover={s.get('turnover_ann', 0):.2f}")
    df = pd.DataFrame(rows)
    if "variants" in stages:
        df.to_csv(os.path.join(OUT, "v2_summary.csv"), index=False)
        open(os.path.join(OUT, "v2_summary.md"), "w").write(fmt_table(df))

    # default deep-dive
    r0 = run(base, bo)
    mkt = market("live", "gbp")
    eq = r0.equity.equity
    bench = bt.bench_equity(mkt, eq.index[0], eq.index[-1], bo.capital)
    v1 = pd.read_csv(os.path.join(OUT, "baseline_equity.csv"), parse_dates=["date"]).set_index("date").equity if os.path.exists(os.path.join(OUT, "baseline_equity.csv")) else None
    curves = {"v2": eq, "BH_CSPX.L": bench["BH_CSPX.L"], "STATIC_80_15_5": bench["STATIC_80_15_5"]}
    if v1 is not None:
        curves["v1_live_logic"] = v1.reindex(eq.index).ffill()
    bt.plot_equity(curves, os.path.join(OUT, "v2_equity.png"), "Strategy v2 vs v1 and benchmarks (GBP, 2014→)")
    pt = bt.period_table(eq, {**bench, **({"v1_live_logic": v1} if v1 is not None else {})}, [
        ("2015-08 China/flash crash", "2015-07-31", "2015-09-30"), ("2016-Q1 sell-off", "2015-12-31", "2016-02-29"),
        ("2018-Q4 sell-off", "2018-09-28", "2018-12-31"), ("2020 COVID crash", "2020-02-19", "2020-03-23"),
        ("2020 V recovery", "2020-03-23", "2020-08-31"), ("2022 rate-hike bear", "2022-01-03", "2022-10-14"),
        ("2023 sideways/chop", "2023-01-03", "2023-10-31"), ("2025 tariff crash", "2025-02-19", "2025-04-08"),
        ("2025 tariff V recovery", "2025-04-08", "2025-07-31")] +
        [(f"Calendar {y}", f"{y}-01-01", f"{y}-12-31") for y in range(2015, 2026)] + [("2026 YTD", "2026-01-01", "2026-09-04")])
    open(os.path.join(OUT, "v2_periods.md"), "w").write(bt.md_table(pt))
    yr = pd.DataFrame({"v2": eq.resample("YE").last().pct_change(), **{k: v.resample("YE").last().pct_change() for k, v in bench.items()},
                       **({"v1": v1.resample("YE").last().pct_change()} if v1 is not None else {})}).dropna()
    yr.index = yr.index.year
    open(os.path.join(OUT, "v2_yearly.md"), "w").write(bt.md_table(yr.reset_index().rename(columns={"date": "year"})))
    print("\nYEARLY\n" + bt.md_table((yr * 100).round(1).reset_index().rename(columns={"date": "year"})))

    # ---------------- proxy 2006→ ----------------
    if "proxy" in stages:
        prow = []
        po = replace(bo, universe="proxy", start="2006-04-01")
        for name, p, o in [("v2_proxy_default", base, po), ("v2_proxy_cost_10bps", base, replace(po, cost_bps=10)),
                           ("v2_proxy_no_trend_filter", replace(base, trend_filter=False), po), ("v2_proxy_abs_momentum_on", replace(base, abs_momentum=True), po),
                           ("v2_proxy_top3", replace(base, top_n=3), po), ("v2_proxy_bonds_IGLS", replace(base, bonds="IGLS.L"), po),
                           ("v2_proxy_signals_gbp_all", replace(base, signal_currency="gbp"), replace(po, signal_currency="gbp")),
                           ("v2_proxy_top1_GEM", replace(base, top_n=1), po), ("v2_proxy_mix_80_10_10", replace(base, w_equity=0.8, w_gold=0.1, w_bonds=0.1), po),
                           ("v2_proxy_fallback_cash", replace(base, fallback="cash"), po), ("v2_proxy_mix_60_20_20", replace(base, w_equity=0.6, w_gold=0.2, w_bonds=0.2), po),
                           ("v2_proxy_vol_target_12", replace(base, vol_target=0.12), po), ("v2_proxy_rebalance_5th", base, replace(po, rebalance_nth_day=5)),
                           ("v2_proxy_hysteresis_2pct", replace(base, trend_band=0.02), po), ("v2_proxy_cost_25bps", base, replace(po, cost_bps=25))]:
            r = run(p, replace(o, name=name))
            save(r, name)
            s = summarize(r, name)
            prow.append(s)
            print(f"{name:34s} CAGR={s['cagr']:+.2%} vol={s['ann_vol']:.2%} Sharpe={s['sharpe']:.2f} MaxDD={s['max_dd']:.1%} Calmar={s['calmar']:.2f} orders={s['orders']}")
            if name == "v2_proxy_default":
                pm = market("proxy", "gbp")
                pb = bt.bench_equity(pm, r.equity.index[0], r.equity.index[-1], bo.capital)
                v1p = os.path.join(OUT, "proxy_baseline_equity.csv")
                extra = {"v1_live_logic": pd.read_csv(v1p, parse_dates=["date"]).set_index("date").equity} if os.path.exists(v1p) else {}
                pp = bt.period_table(r.equity.equity, {**pb, **extra}, [
                    ("2007-10→2009-03 GFC bear", "2007-10-09", "2009-03-09"), ("2008 calendar", "2008-01-01", "2008-12-31"),
                    ("2009 recovery", "2009-03-09", "2009-12-31"), ("2011 euro crisis", "2011-07-01", "2011-10-04"),
                    ("2015-16 chop", "2015-05-01", "2016-06-30"), ("2020 COVID", "2020-02-19", "2020-03-23"), ("2022 bear", "2022-01-03", "2022-10-14"),
                    ("2006-04→2014-05", "2006-04-03", "2014-05-30"), ("2014-06→2026-09", "2014-06-02", "2026-09-04")] +
                    [(f"Calendar {y}", f"{y}-01-01", f"{y}-12-31") for y in range(2007, 2026)])
                open(os.path.join(OUT, "v2_proxy_periods.md"), "w").write(bt.md_table(pp))
                bm = {k: bt.metrics(v) for k, v in pb.items()}
                pd.DataFrame(bm).T.to_csv(os.path.join(OUT, "v2_proxy_benchmarks.csv"))
                bt.plot_equity({"v2(proxy)": r.equity.equity, **{k: v for k, v in pb.items() if k in ("BH_CSPX.L", "STATIC_80_15_5")}, **extra},
                               os.path.join(OUT, "v2_proxy_equity.png"), "Strategy v2 on proxy universe 2006→2026 (GBP)")
                mth = r.equity.equity.resample("ME").last().pct_change().dropna()
                open(os.path.join(OUT, "v2_proxy_bootstrap.md"), "w").write("```json\n" + json.dumps(bt.block_bootstrap_sharpe(mth), indent=2) + "\n```\n")
        pdf = pd.DataFrame(prow)
        pdf.to_csv(os.path.join(OUT, "v2_proxy_summary.csv"), index=False)
        open(os.path.join(OUT, "v2_proxy_summary.md"), "w").write(fmt_table(pdf))

    # ---------------- bootstrap ----------------
    if "bootstrap" in stages:
        mth = eq.resample("ME").last().pct_change().dropna()
        b = {"v2_default_monthly_block_bootstrap": bt.block_bootstrap_sharpe(mth), "v2_default_trade_bootstrap": bt.trade_bootstrap(r0.roundtrips)}
        rc = run(base, replace(bo, cost_bps=10))
        b["v2_cost_10bps_monthly_block_bootstrap"] = bt.block_bootstrap_sharpe(rc.equity.equity.resample("ME").last().pct_change().dropna())
        open(os.path.join(OUT, "v2_bootstrap.md"), "w").write("```json\n" + json.dumps(b, indent=2) + "\n```\n")
        print("\nBOOTSTRAP", json.dumps(b["v2_default_monthly_block_bootstrap"]))

    # ---------------- walk-forward ----------------
    if "walkforward" in stages:
        lines = []
        grid = [dict(trend_sma=a, top_n=b, w_equity=c, w_gold=(1 - c) / 2, w_bonds=(1 - c) / 2) for a in (168, 210, 252) for b in (1, 2, 3) for c in (0.6, 0.7, 0.8)]
        folds = [("IS 2014-06→2019-12 / OOS 2020-01→2026-09", "2014-06-01", "2019-12-31", "2020-01-01", "2026-09-04"),
                 ("IS 2014-06→2021-12 / OOS 2022-01→2026-09", "2014-06-01", "2021-12-31", "2022-01-01", "2026-09-04"),
                 ("IS 2018-01→2023-12 / OOS 2024-01→2026-09", "2018-01-01", "2023-12-31", "2024-01-01", "2026-09-04"),
                 ("PROXY IS 2006-04→2014-05 / OOS 2014-06→2026-09", "2006-04-01", "2014-05-31", "2014-06-01", "2026-09-04")]
        for label, a0, a1, b0, b1 in folds:
            uni = "proxy" if label.startswith("PROXY") else "live"
            rs = []
            for g in grid:
                m = bt.metrics(run(replace(base, **g), replace(bo, universe=uni, start=a0, end=a1)).equity.equity)
                rs.append({**g, "is_sharpe": m["sharpe"], "is_cagr": m["cagr"], "is_mdd": m["max_dd"]})
            rs = pd.DataFrame(rs).sort_values("is_sharpe", ascending=False)
            best = rs.iloc[0]
            gb = {k: (int(best[k]) if k in ("trend_sma", "top_n") else float(best[k])) for k in ("trend_sma", "top_n", "w_equity", "w_gold", "w_bonds")}
            oos_b = bt.metrics(run(replace(base, **gb), replace(bo, universe=uni, start=b0, end=b1)).equity.equity)
            oos_d = bt.metrics(run(base, replace(bo, universe=uni, start=b0, end=b1)).equity.equity)
            is_d = bt.metrics(run(base, replace(bo, universe=uni, start=a0, end=a1)).equity.equity)
            rank = int((rs.is_sharpe > is_d["sharpe"]).sum()) + 1
            lines += [f"### {label}\n",
                      f"- Default params IS Sharpe {is_d['sharpe']:.2f} (rank {rank}/{len(rs)}), OOS Sharpe {oos_d['sharpe']:.2f}, OOS CAGR {oos_d['cagr']:.1%}, OOS MaxDD {oos_d['max_dd']:.1%}",
                      f"- Best-IS {gb}: IS Sharpe {best['is_sharpe']:.2f} → OOS Sharpe {oos_b['sharpe']:.2f}, OOS CAGR {oos_b['cagr']:.1%}, OOS MaxDD {oos_b['max_dd']:.1%}",
                      f"- Grid IS Sharpe range {rs.is_sharpe.min():.2f} … {rs.is_sharpe.max():.2f} (median {rs.is_sharpe.median():.2f})\n", bt.md_table(rs.head(5)) + "\n"]
        open(os.path.join(OUT, "v2_walkforward.md"), "w").write("\n".join(lines))
        print("\nWALK-FORWARD\n" + "\n".join(lines))
    print(f"\nDone in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
