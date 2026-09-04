"""Performance metrics, benchmarks, bootstrap and reporting helpers."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def metrics(eq: pd.Series, orders: pd.DataFrame | None = None, roundtrips: pd.DataFrame | None = None,
            weights: pd.DataFrame | None = None) -> dict:
    eq = eq.dropna()
    r = eq.pct_change().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    vol = r.std() * math.sqrt(252)
    sharpe = r.mean() / r.std() * math.sqrt(252) if r.std() > 0 else np.nan
    sortino = r.mean() / (np.sqrt((np.minimum(r, 0) ** 2).mean()) + 1e-12) * math.sqrt(252)
    dd = eq / eq.cummax() - 1
    mdd, trough = dd.min(), dd.idxmin()
    mdd_start = eq.loc[:trough].idxmax()
    rec = dd.loc[trough:]
    rec_date = rec[rec >= 0].index[0] if (rec >= 0).any() else None
    mth = eq.resample("ME").last().pct_change().dropna()
    yr = eq.resample("YE").last().pct_change().dropna()
    out = dict(start=str(eq.index[0].date()), end=str(eq.index[-1].date()), years=round(yrs, 2),
               final_equity=round(float(eq.iloc[-1]), 0), cagr=round(cagr, 4), ann_vol=round(vol, 4), sharpe=round(sharpe, 3),
               sortino=round(sortino, 3), max_dd=round(mdd, 4), max_dd_start=str(mdd_start.date()), max_dd_trough=str(trough.date()),
               max_dd_recovered=str(rec_date.date()) if rec_date is not None else "not yet", calmar=round(cagr / abs(mdd), 3) if mdd < 0 else np.nan,
               monthly_win_rate=round((mth > 0).mean(), 4), worst_month=round(mth.min(), 4), best_month=round(mth.max(), 4),
               worst_year=round(yr.min(), 4) if len(yr) else np.nan, years_negative=int((yr < 0).sum()) if len(yr) else 0)
    if weights is not None:
        out["exposure_avg"] = round(weights.sum(axis=1).mean(), 4)
    if orders is not None and len(orders):
        out["orders"] = int(len(orders))
        out["orders_per_year"] = round(len(orders) / yrs, 1)
        out["turnover_ann"] = round(orders.amount_gbp.sum() / eq.mean() / yrs, 3)
        out["costs_gbp"] = round(orders.cost_gbp.sum(), 2)
    else:
        out["orders"] = 0
    if roundtrips is not None and len(roundtrips):
        closed = roundtrips[~roundtrips.open]
        if len(closed):
            gp, gl = closed.pnl[closed.pnl > 0].sum(), -closed.pnl[closed.pnl < 0].sum()
            out.update(roundtrips=int(len(closed)), rt_win_rate=round((closed.ret > 0).mean(), 4), rt_avg_ret=round(closed.ret.mean(), 4),
                       rt_profit_factor=round(gp / gl, 2) if gl > 0 else np.inf, rt_avg_days=round(closed.days.mean(), 0))
    return out


def bench_equity(trade_px: pd.DataFrame, start, end, initial: float, keys: dict) -> dict[str, pd.Series]:
    """keys: {'SP500': .., 'WORLD': .., 'GOLD': .., 'GILTS': ..} instrument keys present in trade_px."""
    px = trade_px.loc[start:end]
    out = {f"BH_{k}": px[k] / px[k].iloc[0] * initial for k in (keys["SP500"], keys["WORLD"])}
    r = px.pct_change().fillna(0)

    def static(w: dict) -> pd.Series:
        ws = pd.Series(w)
        cur = ws * initial
        eq = [initial]
        for i in range(1, len(px)):
            cur = cur * (1 + r.iloc[i][ws.index])
            tot = cur.sum()
            if px.index[i].month != px.index[i - 1].month:
                cur = ws * tot
            eq.append(tot)
        return pd.Series(eq, index=px.index)

    out["STATIC_70_15_15"] = static({keys["WORLD"]: 0.70, keys["GOLD"]: 0.15, keys["GILTS"]: 0.15})
    out["STATIC_60_40"] = static({keys["WORLD"]: 0.60, keys["GILTS"]: 0.40})
    return out


def period_table(eq: pd.Series, bench: dict[str, pd.Series], periods: list[tuple[str, str, str]]) -> pd.DataFrame:
    rows = []
    for name, a, b in periods:
        s = eq.loc[a:b]
        if len(s) < 5:
            continue
        row = {"period": name, "strategy": round(s.iloc[-1] / s.iloc[0] - 1, 4), "strategy_maxdd": round((s / s.cummax() - 1).min(), 4)}
        for k, v in bench.items():
            vv = v.loc[a:b]
            if len(vv):
                row[k] = round(vv.iloc[-1] / vv.iloc[0] - 1, 4)
        rows.append(row)
    return pd.DataFrame(rows)


def block_bootstrap(monthly: pd.Series, n: int = 5000, block: int = 6, seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    x = monthly.values
    L = len(x)
    sh, cg, md = [], [], []
    for _ in range(n):
        idx = []
        while len(idx) < L:
            s = rng.integers(0, L)
            idx.extend([(s + k) % L for k in range(rng.geometric(1 / block))])
        smp = x[idx[:L]]
        sh.append(smp.mean() / smp.std() * math.sqrt(12) if smp.std() > 0 else 0)
        eq = np.cumprod(1 + smp)
        cg.append(eq[-1] ** (12 / L) - 1)
        md.append((eq / np.maximum.accumulate(eq) - 1).min())
    q = lambda a: np.percentile(a, [5, 25, 50, 75, 95]).round(3).tolist()
    return {"sharpe_pct": q(sh), "cagr_pct": q(cg), "maxdd_pct": q(md),
            "p_sharpe_below_0": round(float(np.mean(np.array(sh) < 0)), 3), "p_sharpe_below_0.5": round(float(np.mean(np.array(sh) < 0.5)), 3)}


def md_table(df: pd.DataFrame, floatfmt: str = "{:.4g}") -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        cells = [("nan" if isinstance(r[c], float) and np.isnan(r[c]) else floatfmt.format(r[c]) if isinstance(r[c], float) else str(r[c])) for c in cols]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def plot_equity(curves: dict[str, pd.Series], path: str, title: str) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        print("matplotlib unavailable; skipping plot")
        return
    fig, ax = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    for k, s in curves.items():
        ax[0].plot(s.index, s / s.iloc[0] * 100, label=k, lw=1.8 if k == "strategy" else 1.0)
        ax[1].plot(s.index, (s / s.cummax() - 1) * 100, lw=0.9)
    ax[0].set_yscale("log"); ax[0].set_ylabel("growth of 100 (log)"); ax[0].legend(fontsize=8); ax[0].set_title(title)
    ax[1].set_ylabel("drawdown %")
    fig.tight_layout(); fig.savefig(path, dpi=110); plt.close(fig)
