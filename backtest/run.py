#!/usr/bin/env python3
"""
Backtest CLI.

    python -m backtest.run                 # default parameters, LSE universe
    python -m backtest.run --all           # + variants, proxy 2006→, walk-forward, bootstrap
    python -m backtest.run --stage proxy   # one stage

Outputs go to backtest_output/ (gitignored).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings
from dataclasses import replace

import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for extra in os.environ.get("BACKTEST_PYLIB", "").split(os.pathsep):
    if extra:
        sys.path.append(extra)

from backtest.data import OUT_DIR, load_market  # noqa: E402
from backtest.engine import RunOpts, run  # noqa: E402
from backtest.metrics import metrics, bench_equity, period_table, block_bootstrap, md_table, plot_equity  # noqa: E402
from src.strategy import StrategyParams  # noqa: E402

KEYS = {"SP500": "SP500", "WORLD": "WORLD", "GOLD": "GOLD", "GILTS": "GILTS"}
EPISODES = [("2015-08 China/flash crash", "2015-07-31", "2015-09-30"), ("2016-Q1 sell-off", "2015-12-31", "2016-02-29"),
            ("2018-Q4 sell-off", "2018-09-28", "2018-12-31"), ("2020 COVID crash", "2020-02-19", "2020-03-23"),
            ("2020 V recovery", "2020-03-23", "2020-08-31"), ("2022 rate-hike bear", "2022-01-03", "2022-10-14"),
            ("2023 sideways/chop", "2023-01-03", "2023-10-31"), ("2025 tariff crash", "2025-02-19", "2025-04-08"),
            ("2025 tariff V recovery", "2025-04-08", "2025-07-31")]
PROXY_EPISODES = [("2007-10→2009-03 GFC bear", "2007-10-09", "2009-03-09"), ("2008 calendar", "2008-01-01", "2008-12-31"),
                  ("2009 recovery", "2009-03-09", "2009-12-31"), ("2011 euro crisis", "2011-07-01", "2011-10-04"),
                  ("2015-16 chop", "2015-05-01", "2016-06-30"), ("2020 COVID", "2020-02-19", "2020-03-23"), ("2022 bear", "2022-01-03", "2022-10-14")]


def summarize(res, label):
    w = res.equity[[c for c in res.equity.columns if c.startswith("w_")]]
    return {"variant": label, **metrics(res.equity.equity, res.orders, res.roundtrips, w), **{f"ev_{k}": v for k, v in res.events.items()}}


def save(res, tag):
    res.equity.to_csv(os.path.join(OUT_DIR, f"{tag}_equity.csv"))
    res.orders.to_csv(os.path.join(OUT_DIR, f"{tag}_orders.csv"), index=False)
    res.roundtrips.to_csv(os.path.join(OUT_DIR, f"{tag}_roundtrips.csv"), index=False)
    res.signals.to_csv(os.path.join(OUT_DIR, f"{tag}_signals.csv"), index=False)


def fmt(df: pd.DataFrame) -> str:
    cols = ["variant", "cagr", "ann_vol", "sharpe", "sortino", "max_dd", "calmar", "worst_year", "monthly_win_rate", "orders_per_year", "turnover_ann", "rt_win_rate", "rt_profit_factor"]
    t = df[[c for c in cols if c in df.columns]].copy()
    for c in ("cagr", "ann_vol", "max_dd", "worst_year", "monthly_win_rate", "rt_win_rate"):
        if c in t:
            t[c] = (t[c] * 100).round(1).astype(str) + "%"
    for c in ("sharpe", "sortino", "calmar", "rt_profit_factor", "turnover_ann"):
        if c in t:
            t[c] = t[c].round(2)
    return md_table(t)


def yearly(eq: pd.Series, bench: dict) -> pd.DataFrame:
    y = pd.DataFrame({"strategy": eq.resample("YE").last().pct_change(), **{k: v.resample("YE").last().pct_change() for k, v in bench.items()}}).dropna()
    y.index = y.index.year
    return (y * 100).round(1).reset_index().rename(columns={"date": "year"})


def variants(base: StrategyParams, bo: RunOpts):
    V = []
    add = lambda n, p=None, o=None: V.append((n, p or base, replace(o or bo, name=n)))
    add("default")
    add("no_trend_filter", replace(base, trend_filter=False))
    add("no_trend_no_selection", replace(base, trend_filter=False, top_n=4))
    add("plain_12m_momentum", replace(base, mom_skip=0))
    add("fallback_cash", replace(base, fallback="cash"))
    add("fallback_best_defensive", replace(base, fallback="best_defensive"))
    add("no_max_weight_cap", replace(base, max_weight=1.0))
    add("top1", replace(base, top_n=1)); add("top3", replace(base, top_n=3)); add("top4_no_selection", replace(base, top_n=4))
    add("mix_80_10_10", replace(base, w_equity=0.80, w_gold=0.10, w_bonds=0.10))
    add("mix_60_20_20", replace(base, w_equity=0.60, w_gold=0.20, w_bonds=0.20))
    add("sma_168", replace(base, trend_sma=168)); add("sma_252", replace(base, trend_sma=252))
    add("mom_202", replace(base, mom_lookback=202)); add("mom_302", replace(base, mom_lookback=302))
    add("def_lookback_100", replace(base, def_lookback=100)); add("def_lookback_152", replace(base, def_lookback=152))
    add("hysteresis_2pct", replace(base, trend_band=0.02))
    add("rebal_band_0", replace(base, rebalance_band=0.0)); add("rebal_band_10pct", replace(base, rebalance_band=0.10))
    add("rebalance_5th_td", None, replace(bo, rebalance_day=5)); add("rebalance_10th_td", None, replace(bo, rebalance_day=10))
    add("fill_same_close_LOOKAHEAD", None, replace(bo, fill="same_close"))
    add("cost_10bps", None, replace(bo, cost_bps=10)); add("cost_25bps", None, replace(bo, cost_bps=25))
    add("capital_20000", None, replace(bo, capital=20_000))
    return V


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--stage", default="", help="variants,proxy,walkforward,bootstrap")
    ap.add_argument("--capital", type=float, default=RunOpts.capital)
    a = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    stages = set(a.stage.split(",")) if a.stage else ({"variants", "proxy", "walkforward", "bootstrap"} if a.all else set())
    base, bo = StrategyParams(), RunOpts(capital=a.capital)
    t0 = time.time()

    rows = []
    for name, p, o in (variants(base, bo) if "variants" in stages else [("default", base, bo)]):
        r = run(p, o)
        save(r, name)
        s = summarize(r, name)
        rows.append(s)
        print(f"{name:28s} CAGR={s['cagr']:+.2%} vol={s['ann_vol']:.2%} Sharpe={s['sharpe']:.2f} MaxDD={s['max_dd']:.1%} Calmar={s['calmar']:.2f} "
              f"worst_yr={s['worst_year']:+.1%} orders/yr={s.get('orders_per_year', 0)} turnover={s.get('turnover_ann', 0):.2f}")
    if "variants" in stages:
        pd.DataFrame(rows).to_csv(os.path.join(OUT_DIR, "summary.csv"), index=False)
        open(os.path.join(OUT_DIR, "summary.md"), "w").write(fmt(pd.DataFrame(rows)))

    r0 = run(base, bo)
    mkt = load_market("lse")
    eq = r0.equity.equity
    bench = bench_equity(mkt.trade, eq.index[0], eq.index[-1], bo.capital, KEYS)
    pd.DataFrame({k: metrics(v) for k, v in bench.items()}).T.to_csv(os.path.join(OUT_DIR, "benchmarks.csv"))
    plot_equity({"strategy": eq, **bench}, os.path.join(OUT_DIR, "equity.png"), "Strategy vs benchmarks, LSE instruments (GBP)")
    pt = period_table(eq, bench, EPISODES + [(f"Calendar {y}", f"{y}-01-01", f"{y}-12-31") for y in range(2015, 2027)])
    open(os.path.join(OUT_DIR, "periods.md"), "w").write(md_table(pt))
    yr = yearly(eq, bench)
    open(os.path.join(OUT_DIR, "yearly.md"), "w").write(md_table(yr))
    print("\nYEARLY (%)\n" + md_table(yr))
    for n in mkt.notes:
        print("DATA NOTE:", n)

    if "proxy" in stages:
        prow = []
        po = replace(bo, universe="proxy", start="2006-04-01")
        for name, p, o in [("proxy_default", base, po), ("proxy_cost_10bps", base, replace(po, cost_bps=10)),
                           ("proxy_no_trend_filter", replace(base, trend_filter=False), po), ("proxy_top3", replace(base, top_n=3), po),
                           ("proxy_mix_80_10_10", replace(base, w_equity=0.8, w_gold=0.1, w_bonds=0.1), po),
                           ("proxy_mix_60_20_20", replace(base, w_equity=0.6, w_gold=0.2, w_bonds=0.2), po),
                           ("proxy_fallback_cash", replace(base, fallback="cash"), po), ("proxy_rebalance_5th", base, replace(po, rebalance_day=5))]:
            r = run(p, replace(o, name=name))
            save(r, name)
            s = summarize(r, name)
            prow.append(s)
            print(f"{name:28s} CAGR={s['cagr']:+.2%} vol={s['ann_vol']:.2%} Sharpe={s['sharpe']:.2f} MaxDD={s['max_dd']:.1%} Calmar={s['calmar']:.2f} worst_yr={s['worst_year']:+.1%}")
            if name == "proxy_default":
                pm = load_market("proxy")
                pb = bench_equity(pm.trade, r.equity.index[0], r.equity.index[-1], bo.capital, KEYS)
                pd.DataFrame({k: metrics(v) for k, v in pb.items()}).T.to_csv(os.path.join(OUT_DIR, "proxy_benchmarks.csv"))
                open(os.path.join(OUT_DIR, "proxy_periods.md"), "w").write(md_table(period_table(r.equity.equity, pb, PROXY_EPISODES + [(f"Calendar {y}", f"{y}-01-01", f"{y}-12-31") for y in range(2007, 2027)])))
                open(os.path.join(OUT_DIR, "proxy_yearly.md"), "w").write(md_table(yearly(r.equity.equity, pb)))
                plot_equity({"strategy": r.equity.equity, **pb}, os.path.join(OUT_DIR, "proxy_equity.png"), "Strategy on proxy universe 2006→ (GBP)")
                open(os.path.join(OUT_DIR, "proxy_bootstrap.md"), "w").write("```json\n" + json.dumps(block_bootstrap(r.equity.equity.resample("ME").last().pct_change().dropna()), indent=2) + "\n```\n")
        pd.DataFrame(prow).to_csv(os.path.join(OUT_DIR, "proxy_summary.csv"), index=False)
        open(os.path.join(OUT_DIR, "proxy_summary.md"), "w").write(fmt(pd.DataFrame(prow)))

    if "bootstrap" in stages:
        b = {"default": block_bootstrap(eq.resample("ME").last().pct_change().dropna()),
             "cost_10bps": block_bootstrap(run(base, replace(bo, cost_bps=10)).equity.equity.resample("ME").last().pct_change().dropna())}
        open(os.path.join(OUT_DIR, "bootstrap.md"), "w").write("```json\n" + json.dumps(b, indent=2) + "\n```\n")
        print("\nBOOTSTRAP", json.dumps(b["default"]))

    if "walkforward" in stages:
        lines = []
        grid = [dict(trend_sma=s, top_n=n, w_equity=w, w_gold=(1 - w) / 2, w_bonds=(1 - w) / 2) for s in (168, 210, 252) for n in (1, 2, 3) for w in (0.6, 0.7, 0.8)]
        folds = [("IS 2014-06→2019-12 / OOS 2020-01→2026-09", "lse", "2014-06-01", "2019-12-31", "2020-01-01", "2026-12-31"),
                 ("IS 2014-06→2021-12 / OOS 2022-01→2026-09", "lse", "2014-06-01", "2021-12-31", "2022-01-01", "2026-12-31"),
                 ("IS 2018-01→2023-12 / OOS 2024-01→2026-09", "lse", "2018-01-01", "2023-12-31", "2024-01-01", "2026-12-31"),
                 ("PROXY IS 2006-04→2014-05 / OOS 2014-06→2026-09", "proxy", "2006-04-01", "2014-05-31", "2014-06-01", "2026-12-31")]
        for label, uni, a0, a1, b0, b1 in folds:
            rs = []
            for g in grid:
                m = metrics(run(replace(base, **g), replace(bo, universe=uni, start=a0, end=a1)).equity.equity)
                rs.append({**g, "is_sharpe": m["sharpe"], "is_cagr": m["cagr"], "is_mdd": m["max_dd"]})
            rs = pd.DataFrame(rs).sort_values("is_sharpe", ascending=False)
            best = rs.iloc[0]
            gb = {k: (int(best[k]) if k in ("trend_sma", "top_n") else float(best[k])) for k in ("trend_sma", "top_n", "w_equity", "w_gold", "w_bonds")}
            oos_b = metrics(run(replace(base, **gb), replace(bo, universe=uni, start=b0, end=b1)).equity.equity)
            oos_d = metrics(run(base, replace(bo, universe=uni, start=b0, end=b1)).equity.equity)
            is_d = metrics(run(base, replace(bo, universe=uni, start=a0, end=a1)).equity.equity)
            rank = int((rs.is_sharpe > is_d["sharpe"]).sum()) + 1
            lines += [f"### {label}\n", f"- Default IS Sharpe {is_d['sharpe']:.2f} (rank {rank}/{len(rs)}), OOS Sharpe {oos_d['sharpe']:.2f}, OOS CAGR {oos_d['cagr']:.1%}, OOS MaxDD {oos_d['max_dd']:.1%}",
                      f"- Best-IS {gb}: IS {best['is_sharpe']:.2f} → OOS Sharpe {oos_b['sharpe']:.2f}, OOS CAGR {oos_b['cagr']:.1%}, OOS MaxDD {oos_b['max_dd']:.1%}",
                      f"- Grid IS Sharpe range {rs.is_sharpe.min():.2f} … {rs.is_sharpe.max():.2f}\n"]
        open(os.path.join(OUT_DIR, "walkforward.md"), "w").write("\n".join(lines))
        print("\nWALK-FORWARD\n" + "\n".join(lines))
    print(f"\nDone in {time.time() - t0:.0f}s → {OUT_DIR}")


if __name__ == "__main__":
    main()
