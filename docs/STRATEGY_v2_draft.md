# Strategy v2 — Trend-filtered momentum with a diversified defensive sleeve

Status: **designed, implemented as pure functions, backtested. Not wired into the live scheduler, not deployed, not run against the demo or live account.** Integration is described in §7 for whoever executes it.

Companion documents: [ANALYSIS.md](ANALYSIS.md) (diagnosis of v1), [backtest.py](backtest.py) (v1 replay), [backtest_v2.py](backtest_v2.py) (v2 replay), [src/strategy/v2.py](src/strategy/v2.py) (the strategy), [test_strategy_v2.py](test_strategy_v2.py) (unit tests, 7 passing).

---

## 1. Design goals and what the evidence allowed

The brief: time-tested rules, returns that reliably beat inflation and ideally the S&P 500, a healthy Sharpe with small drawdowns, every v1 bug fixed, every evidence-backed recommendation adopted, hands-off with a human check at key decisions.

What the two backtest windows (LSE data 2014→2026, US-proxy data 2006→2026) say about that brief, before any design choice:

- **A trend filter halves the maximum drawdown and costs 2–4 points of CAGR** in a decade dominated by V-shaped crashes (2020, 2025). It earns that back only in slow bears (2008, 2022). Every trend variant tested confirms this trade-off; none escapes it.
- **Beating the S&P 500's 15.7% CAGR (2014→2026) with a drawdown-controlled multi-asset portfolio was not achieved by any variant.** The best drawdown-controlled results are ~11–12% CAGR at 12–14% max drawdown. Over 2006→2026 the S&P proxy did 12.5% with a −35% drawdown; v2 does ~9% with −15%. Beating the index on absolute return and on drawdown at the same time is not on offer from these instruments and these rules. The design therefore targets *risk-adjusted* superiority (Sharpe, Calmar, worst year) and a return that clears UK inflation comfortably in every multi-year window tested.
- **Turnover and timing luck are the largest controllable costs.** v1 traded 395% of equity per year and its Sharpe moved ±0.15 on the choice of rebalance day. v2 trades roughly half as much through equal weights and tolerance bands, and the design does not depend on any single-day timing choice (§5).

## 2. The rules

All rules are published, decades-old, and used unchanged except where a parameter had to be fixed:

| Rule | Source | v2 setting |
|---|---|---|
| Relative momentum, 12-1 lookback | Jegadeesh & Titman (1993); Antonacci (2014) | rank the 4 equity ETFs by (price 1 month ago ÷ price 12 months ago − 1); hold the top 2, equal weight |
| Trend filter per instrument, 10-month SMA | Faber (2007) *A Quantitative Approach to Tactical Asset Allocation* | hold an instrument only when its price is above its 210-day SMA; evaluated monthly |
| Strategic sleeves | classic diversified allocation (Browne's Permanent Portfolio, Faber GTAA) | 70% equity sleeve / 15% gold / 15% gilts |
| Fallback for switched-off equity capital | Faber (to cash); Antonacci (to bonds) — blended | half to cash, half to the strongest defensive (gold or gilts) by 6-month momentum if that asset is in an uptrend and positive, else all to cash |
| Rebalance tolerance bands | standard institutional practice | trade only when a holding is more than 5 pp of portfolio from target; full exits/entries always trade |
| Concentration cap | prudence | no single instrument above 40%; excess to cash |
| Cash buffer | T212 order reservation behaviour (v1 finding) | 2% uninvested |

Signals are computed on the previous trading day's close and executed on the first trading day of each month (month-end signal, month-start execution — the convention in both source papers).

**Currency of the signals** (the one non-textbook choice, and the most important finding of the rework): equity ETFs are trend- and momentum-tested in their fund base currency (USD; GBP price × GBP/USD), while gold and gilts are tested in GBP. Rationale: the equity filter's job is to detect equity bear markets, and sterling swings (2016, 2022) created false exits and entries when tested in GBP; the defensive sleeve's job is to protect GBP wealth, so its trend must be measured in GBP (in 2008 gold rose 38% in GBP while falling in USD). Evidence in §4.

**What v2 deliberately does not have** — each dropped on backtest evidence:

- No fast-crash trigger or automatic circuit breaker (v1: −3.4% cumulative, no drawdown reduction, whipsaw 2020-03-02/06/09). The 15% drawdown alert stays as a *human* alert.
- No inverse-volatility weighting (v1: same Sharpe as equal weight, +21% orders).
- No absolute-momentum gate on top of the trend filter (v2 test: −0.08 Sharpe on LSE data, −0.05 on the proxy; the two gates are redundant and the second one only adds cash drag).
- No intra-month action. The weekly job becomes a monitor, not a trader.

## 3. Parameters

All in `StrategyParams` ([src/strategy/v2.py](src/strategy/v2.py)); defaults are the validated set:

| Parameter | Default | Sensitivity (§5) |
|---|---|---|
| `growth` | CSPX.L, EQQQ.L, VWRL.L, VEUR.L | universe unchanged from v1 (all T212 ISA-verified) |
| `gold` / `bonds` | SGLN.L / **IGLT.L** (all-maturity gilts; v1 used short gilts IGLS.L) | IGLT vs IGLS: same Sharpe live, +0.02 proxy, better 2008/2011 |
| `top_n` | 2 | 1 / 3 / 4 tested |
| `mom_lookback`, `mom_skip` | 252, 21 | ±20% flat |
| `trend_sma` | 210 | 168 same live, worse proxy DD; 252 worse both |
| `trend_band` | 0 | 2% hysteresis worse in both windows (slower exits) |
| `abs_momentum` | off | on: −0.08 Sharpe (LSE), −0.05 (proxy) |
| `def_lookback` | 126 | ±20% flat |
| `w_equity / w_gold / w_bonds` | 0.70 / 0.15 / 0.15 | risk dial, §4.3 |
| `fallback` | split | cash / bonds / best_defensive within ±0.02 |
| `max_weight` | 0.40 | 0.30 lowers return; 1.0 no gain |
| `rebalance_band` | 0.05 | 0 → 2× orders, same result; 0.10 same |
| `cash_buffer` | 0.02 | T212 reservation |
| `vol_target` | off | 10–12% target lowers CAGR more than drawdown |
| `signal_currency` | mixed | gbp: −0.17 Sharpe (LSE), −0.08 (proxy); underlying (all USD): −0.07 / −0.04 |

Sixteen parameters, as before — but now every one has a ±20% sensitivity run on two datasets, and only the sleeve weights materially move the result.

## 4. Backtest results

Same engine conventions as the v1 replay: signal on the previous close, fill at the decision day's close, £20k, T212 quantity rounding, no costs unless stated. Full outputs: `backtest_output/v2_*` (equity, orders, round-trips, signals per variant).

### 4.1 Headline — LSE data 2014-06 → 2026-09

| | CAGR | Vol | Sharpe | Sortino | Max DD | Calmar | Worst month | Monthly win rate | Orders/yr | Turnover/yr |
|---|---|---|---|---|---|---|---|---|---|---|
| **v2 (this strategy)** | 11.2% | 10.1% | 1.10 | 1.60 | -12.4% | 0.90 | -7.0% | 67.3% | 14 | 377% |
| **v1 live logic (ANALYSIS.md)** | 11.9% | 11.9% | 1.00 | 1.44 | -18.0% | 0.66 | -7.5% | 64.6% | 34 | 395% |
| STATIC 80 15 5 | 12.1% | 11.8% | 1.02 | 1.47 | -19.1% | 0.63 | -6.5% | 63.3% | – | – |
| BH CSPX.L | 15.7% | 17.6% | 0.91 | 1.33 | -26.1% | 0.60 | -8.3% | 63.3% | – | – |
| BH VWRL.L | 12.4% | 14.4% | 0.88 | 1.25 | -25.0% | 0.50 | -9.0% | 63.9% | – | – |

Round trips (entry → flat): 74 closed, win rate 64.9%, average +4.8%, profit factor 8.1, average holding 179 days. v1: 36 trips, 61.1%, profit factor 5.2.

### 4.2 Headline — proxy universe 2006-04 → 2026-09 (includes 2008)

| | CAGR | Vol | Sharpe | Sortino | Max DD | Calmar | Worst month |
|---|---|---|---|---|---|---|---|
| **v2** | 9.2% | 12.1% | 0.78 | 1.11 | -14.7% | 0.63 | -5.6% |
| v1 live logic | 11.3% | 13.6% | 0.84 | 1.22 | -21.4% | 0.53 | -6.0% |
| STATIC 80 15 5 (proxy) | 9.9% | 17.0% | 0.63 | 0.91 | -28.7% | 0.35 | -11.4% |
| BH CSPX.L (proxy) | 12.5% | 20.0% | 0.68 | 0.98 | -35.0% | 0.36 | -8.9% |

v2 gives up ~2 points of CAGR against v1 over 20 years and ~1 point over 12, for a max drawdown 7 points shallower in both windows, a higher Sharpe in the LSE window and a higher Calmar in both.

### 4.3 Calendar-year returns (LSE data)

| Year | v2 | v1 | Static 80/15/5 | CSPX (S&P 500) | VWRL (All-World) | UK CPI (approx.) |
|---|---|---|---|---|---|---|
| 2015 | -1.0% | -1.0% | 1.3% | 5.6% | 2.6% | 0.2% |
| 2016 | 20.7% | 27.5% | 29.0% | 34.4% | 29.9% | 1.6% |
| 2017 | 7.8% | 12.5% | 10.8% | 11.2% | 13.2% | 2.7% |
| 2018 | 6.9% | 8.9% | -2.9% | 0.1% | -4.7% | 2.1% |
| 2019 | 15.6% | 19.4% | 20.0% | 26.4% | 22.0% | 1.3% |
| 2020 | 18.2% | 12.3% | 13.4% | 13.2% | 12.1% | 0.6% |
| 2021 | 14.1% | 23.1% | 15.3% | 30.6% | 20.0% | 5.4% |
| 2022 | -5.5% | -13.9% | -5.2% | -9.0% | -8.4% | 10.5% |
| 2023 | 9.1% | 9.5% | 13.9% | 20.0% | 15.6% | 4.0% |
| 2024 | 22.3% | 25.6% | 20.1% | 27.1% | 19.6% | 2.5% |
| 2025 | 14.4% | 4.8% | 19.0% | 9.4% | 14.0% | 3.5% |
| 2026 YTD | 4.9% | 7.9% | 12.1% | 12.7% | 14.1% | 3.0% |

(UK CPI December-on-December, rounded, for reference only; 2025–26 provisional.) v2 beat CPI in 10 of 12 years; the misses are 2015 (−0.4% vs 0.2%) and 2022 (−5.0% vs 10.5%). It beat the S&P 500 in 4 of 12 years (2018, 2020, 2022, 2025) — the down or crash years — and lagged it in every strong bull year.

### 4.4 Stress episodes (LSE data)

| Episode | v2 | v2 peak-to-trough | v1 | Static 80/15/5 | CSPX |
|---|---|---|---|---|---|
| 2015-08 China/flash crash | -4.4% | -8.1% | -4.2% | -5.3% | -6.5% |
| 2016-Q1 sell-off | 2.8% | -5.5% | 5.3% | 2.9% | 1.7% |
| 2018-Q4 sell-off | -3.7% | -6.8% | -2.8% | -7.2% | -11.0% |
| 2020 COVID crash | -8.3% | -11.6% | -17.0% | -19.1% | -26.1% |
| 2020 V recovery | 16.4% | -2.9% | 21.8% | 26.1% | 38.5% |
| 2022 rate-hike bear | -4.3% | -8.2% | -14.5% | -7.0% | -8.9% |
| 2023 sideways/chop | 1.8% | -4.7% | 0.6% | 5.6% | 9.8% |
| 2025 tariff crash | -11.2% | -12.0% | -16.3% | -11.5% | -16.2% |
| 2025 tariff V recovery | 10.5% | -1.9% | 7.5% | 15.7% | 18.9% |

Proxy universe episodes:

| Episode | v2 | v2 peak-to-trough | v1 | Static 80/15/5 | S&P proxy |
|---|---|---|---|---|---|
| 2007-10→2009-03 GFC bear | 2.5% | -14.7% | 16.4% | -24.1% | -33.8% |
| 2008 calendar | -3.8% | -14.7% | 12.5% | -10.3% | -13.8% |
| 2009 recovery | 9.2% | -9.3% | 15.5% | 40.7% | 42.7% |
| 2011 euro crisis | -5.1% | -10.7% | -5.8% | -12.0% | -12.3% |
| 2015-16 chop | 3.1% | -10.1% | 6.8% | 10.2% | 16.4% |
| 2020 COVID | -7.3% | -11.2% | -7.8% | -20.1% | -25.9% |
| 2022 bear | -5.9% | -8.5% | -19.4% | -8.3% | -9.4% |
| 2006-04→2014-05 | 74.3% | -14.7% | 121.2% | 67.1% | 82.4% |
| 2014-06→2026-09 | 247.6% | -13.6% | 304.5% | 312.9% | 512.1% |

Reading: v2 halves the COVID and tariff crash losses relative to the index (−8.3% / −11.2% vs −26% / −16%) and captures roughly half of the rebounds; it turns the 2022 bear into a −4% year and the 2008 bear into a flat one (v1's +16% in 2008 came from a 50% gold bet that the same rule turned into an 85% gold position in 2022 — v2's 40% cap forgoes that). The cost is visible in 2009 and 2023: slow re-entry after a bear.

### 4.5 Every variant run (LSE data; Δ vs v2 default)

| Variant | CAGR | Vol | Sharpe | ΔSharpe | Max DD | Calmar | Orders | Turnover/yr |
|---|---|---|---|---|---|---|---|---|
| v2_default | 11.2% | 10.1% | 1.10 | +0.00 | -12.4% | 0.90 | 174 | 377% |
| v2_abs_momentum_on | 10.1% | 9.9% | 1.02 | -0.08 | -13.8% | 0.73 | 174 | 374% |
| v2_no_trend_filter | 13.1% | 11.7% | 1.10 | +0.01 | -15.8% | 0.83 | 81 | 226% |
| v2_no_trend_no_selection | 12.2% | 11.0% | 1.10 | +0.00 | -17.3% | 0.70 | 14 | 7% |
| v2_plain_12m_momentum | 11.5% | 10.1% | 1.13 | +0.03 | -12.4% | 0.93 | 181 | 413% |
| v2_bonds_IGLS_short | 11.2% | 10.0% | 1.11 | +0.01 | -12.1% | 0.93 | 152 | 352% |
| v2_fallback_cash | 10.7% | 9.8% | 1.08 | -0.01 | -12.4% | 0.86 | 154 | 342% |
| v2_fallback_best_defensive | 11.2% | 10.1% | 1.10 | +0.00 | -12.6% | 0.89 | 170 | 384% |
| v2_no_max_weight_cap | 11.2% | 10.2% | 1.09 | -0.00 | -12.4% | 0.91 | 174 | 385% |
| v2_top1_GEM | 7.9% | 7.4% | 1.06 | -0.04 | -9.2% | 0.86 | 135 | 312% |
| v2_top3 | 10.6% | 9.5% | 1.10 | +0.00 | -12.2% | 0.86 | 175 | 289% |
| v2_top4_no_selection | 10.0% | 8.8% | 1.12 | +0.02 | -12.2% | 0.81 | 184 | 230% |
| v2_mix_80_10_10_growth | 12.0% | 11.2% | 1.07 | -0.03 | -14.2% | 0.84 | 174 | 404% |
| v2_mix_60_20_20_defensive | 10.4% | 9.1% | 1.13 | +0.03 | -11.2% | 0.93 | 172 | 350% |
| v2_mix_75_15_10 | 11.8% | 10.6% | 1.10 | -0.00 | -13.2% | 0.89 | 171 | 384% |
| v2_sma_168 | 11.3% | 10.1% | 1.11 | +0.01 | -12.4% | 0.91 | 178 | 394% |
| v2_sma_252 | 11.1% | 10.4% | 1.07 | -0.03 | -15.7% | 0.71 | 166 | 364% |
| v2_mom_202 | 10.6% | 10.1% | 1.05 | -0.05 | -12.4% | 0.85 | 184 | 412% |
| v2_mom_302 | 10.9% | 10.2% | 1.07 | -0.03 | -13.0% | 0.84 | 166 | 348% |
| v2_def_lookback_100 | 11.3% | 10.1% | 1.11 | +0.01 | -12.8% | 0.88 | 174 | 382% |
| v2_def_lookback_152 | 10.8% | 10.1% | 1.07 | -0.03 | -12.8% | 0.84 | 176 | 381% |
| v2_hysteresis_2pct | 11.5% | 10.3% | 1.10 | +0.01 | -15.6% | 0.74 | 123 | 305% |
| v2_rebal_band_0 | 11.1% | 9.9% | 1.11 | +0.01 | -12.5% | 0.89 | 357 | 391% |
| v2_rebal_band_10pct | 11.2% | 10.2% | 1.09 | -0.01 | -13.0% | 0.86 | 168 | 374% |
| v2_vol_target_10 | 10.0% | 9.2% | 1.08 | -0.01 | -12.0% | 0.84 | 209 | 365% |
| v2_rebalance_5th_td | 11.0% | 10.6% | 1.04 | -0.06 | -16.1% | 0.69 | 155 | 318% |
| v2_rebalance_10th_td | 11.3% | 10.2% | 1.09 | -0.01 | -17.1% | 0.66 | 171 | 364% |
| v2_fill_next_open | 11.1% | 10.1% | 1.10 | -0.00 | -12.6% | 0.88 | 175 | 376% |
| v2_fill_same_close_LOOKAHEAD | 11.0% | 10.1% | 1.08 | -0.02 | -13.2% | 0.84 | 178 | 387% |
| v2_signals_underlying_all_usd | 10.4% | 10.1% | 1.03 | -0.06 | -12.9% | 0.81 | 173 | 377% |
| v2_signals_gbp_all | 9.8% | 10.6% | 0.93 | -0.17 | -17.1% | 0.57 | 181 | 394% |
| v2_cost_10bps | 10.7% | 10.1% | 1.06 | -0.04 | -12.5% | 0.85 | 174 | 377% |
| v2_cost_25bps | 10.1% | 10.1% | 1.00 | -0.09 | -12.7% | 0.80 | 174 | 377% |
| v2_capital_5000 | 11.2% | 10.1% | 1.10 | +0.00 | -12.4% | 0.90 | 174 | 377% |

Proxy universe 2006→2026:

| Variant | CAGR | Vol | Sharpe | ΔSharpe | Max DD | Calmar | Orders |
|---|---|---|---|---|---|---|---|
| v2_proxy_default | 9.2% | 12.1% | 0.78 | +0.00 | -14.7% | 0.63 | 276 |
| v2_proxy_cost_10bps | 8.8% | 12.1% | 0.74 | -0.03 | -15.0% | 0.59 | 276 |
| v2_proxy_no_trend_filter | 11.5% | 14.9% | 0.79 | +0.02 | -22.2% | 0.52 | 131 |
| v2_proxy_abs_momentum_on | 8.4% | 11.7% | 0.73 | -0.04 | -13.6% | 0.61 | 275 |
| v2_proxy_top3 | 9.1% | 11.6% | 0.80 | +0.02 | -12.8% | 0.71 | 281 |
| v2_proxy_bonds_IGLS | 9.0% | 12.2% | 0.76 | -0.02 | -15.7% | 0.57 | 260 |
| v2_proxy_signals_gbp_all | 8.6% | 12.6% | 0.70 | -0.07 | -21.7% | 0.40 | 270 |
| v2_proxy_top1_GEM | 6.8% | 8.8% | 0.77 | -0.01 | -12.0% | 0.56 | 219 |
| v2_proxy_mix_80_10_10 | 9.6% | 13.3% | 0.74 | -0.03 | -17.6% | 0.54 | 276 |
| v2_proxy_fallback_cash | 9.0% | 11.6% | 0.79 | +0.01 | -13.1% | 0.69 | 236 |
| v2_proxy_mix_60_20_20 | 8.9% | 11.0% | 0.81 | +0.04 | -12.8% | 0.69 | 272 |
| v2_proxy_vol_target_12 | 8.1% | 11.3% | 0.73 | -0.04 | -13.4% | 0.60 | 325 |
| v2_proxy_rebalance_5th | 10.6% | 12.2% | 0.87 | +0.10 | -15.3% | 0.69 | 251 |
| v2_proxy_hysteresis_2pct | 9.3% | 12.8% | 0.75 | -0.03 | -17.4% | 0.54 | 212 |
| v2_proxy_cost_25bps | 8.2% | 12.1% | 0.70 | -0.07 | -15.6% | 0.53 | 276 |

What the ablations say (both windows unless noted):

- **Trend filter**: removing it raises CAGR (+1.9 / +2.3 pp) at the same Sharpe and a max drawdown 3.4 / 7.5 pp deeper. It is the drawdown control; keep it.
- **Relative momentum (top-2 of 4)**: vs holding all four, +1.2 / +0.4 pp CAGR at equal Sharpe. Cheap; keep. Top-1 (pure GEM) is too concentrated (CAGR 7.9%).
- **12-1 vs plain 12-month**: plain is +0.03 Sharpe on LSE data. Within noise; 12-1 is kept because the skip-month is the better-documented convention and it had the larger effect in v1 (+0.06).
- **Signal currency**: GBP-everything is the worst choice in both windows (−0.17 / −0.08 Sharpe, drawdown 4–7 pp deeper). All-USD is second (−0.07 / −0.04). Mixed is best. This is the single largest design lever after the trend filter itself.
- **Long gilts (IGLT) vs short gilts (IGLS)** in the bond sleeve: equal on LSE data, +0.02 Sharpe and better 2008/2011 on the proxy. Marginal; IGLT is kept for its bear-market behaviour, IGLS is the documented fallback until the T212 line is verified.
- **Costs**: 10 bps → Sharpe 1.06 / 0.74; 25 bps → 1.00 / 0.70. Turnover 377%/yr is still high for a monthly strategy; the tolerance band already halves order count (357 → 174) without changing results. The residual turnover is the trend filter switching whole 35% slots.
- **Timing**: 1st vs 5th vs 10th trading day: Sharpe 1.10 / 1.04 / 1.09 on LSE data, 0.78 / 0.87 on proxy for 1st / 5th. Same-close (look-ahead) fill: 1.08 — i.e. no look-ahead benefit at all, unlike v1 (+0.12). The strategy is not timing-sensitive at the day level, which matters given cron drift and the approval window.
- **Hysteresis band, vol target, max-weight 30%, fallback to cash**: each reduces return without improving Sharpe; not adopted.

## 5. Robustness

### 5.1 Walk-forward

Grid of 27 (SMA 168/210/252 × top-1/2/3 × equity 60/70/80%), best-in-sample chosen per fold, then both the default and the best-IS set evaluated out-of-sample (`backtest_output/v2_walkforward.md`):

| Fold | Default IS Sharpe (rank/27) | Default OOS Sharpe / CAGR / MaxDD | Best-IS params → OOS Sharpe / CAGR / MaxDD |
|---|---|---|---|
| IS 2014-06→2019-12, OOS 2020-01→2026-09 | 1.11 (10) | **1.09** / 11.2% / −12.4% | SMA168, top-1, 60/20/20 → 1.02 / 8.2% / −9.6% |
| IS 2014-06→2021-12, OOS 2022-01→2026-09 | 1.17 (7) | **1.00** / 9.5% / −12.4% | SMA168, top-2, 60/20/20 → 1.06 / 9.2% / −10.7% |
| IS 2018-01→2023-12, OOS 2024-01→2026-09 | 0.94 (9) | **1.43** / 15.6% / −12.7% | SMA168, top-3, 60/20/20 → 1.50 / 14.1% / −9.3% |
| Proxy IS 2006-04→2014-05, OOS 2014-06→2026-09 | 0.62 (15) | **0.88** / 10.7% / −13.6% | SMA210, top-1, 60/20/20 → 0.81 / 7.6% / −11.6% |

The default is mid-grid in every in-sample fold (never the best — it was not chosen on these folds) and holds or improves out-of-sample in all four. Re-optimising per fold would have bought −0.07 to +0.07 Sharpe and cost 1–3 pp of CAGR. Contrast v1: best-of-grid in-sample, 1.30 → 0.78 out-of-sample.

### 5.2 Bootstrap

Stationary block bootstrap (6-month blocks, 5,000 draws) of monthly returns:

| | 5% | 25% | 50% | 75% | 95% | P(Sharpe < 0.5) |
|---|---|---|---|---|---|---|
| v2 Sharpe, LSE 2014→ | 0.84 | 1.06 | 1.22 | 1.38 | 1.61 | 0.0% |
| v2 Sharpe, LSE, 10 bps costs | 0.80 | 1.02 | 1.18 | 1.34 | 1.57 | 0.1% |
| v2 CAGR, LSE | 7.5% | 9.6% | 11.2% | 12.8% | 15.1% | |
| v2 Max DD, LSE | −14.2% | −10.8% | −8.9% | −8.7% | −7.0% | |
| v2 Sharpe, proxy 2006→ | 0.73 | 0.91 | 1.04 | 1.16 | 1.35 | 0.3% |
| v1 Sharpe, LSE (for reference) | 0.73 | 0.99 | 1.17 | 1.36 | 1.63 | 0.7% |

Trade bootstrap (74 round trips): mean return 2.9% / 4.7% / 6.8% (5/50/95%), profit factor 3.3 / 5.8 / 10.3, P(mean < 0) = 0.

### 5.3 Sensitivity

Every ±20% shift is in the variant table (§4.5). Sharpe stays within 1.03–1.13 across all single-parameter changes on LSE data except the currency choice (0.93 for GBP-only) and the rebalance day (1.04 for the 5th). Max drawdown stays within −12% to −13% except SMA 252 (−15.7%), hysteresis (−15.6%) and later rebalance days (−16% to −17%).


## 6. Bugs fixed in the existing pipeline

Applied in code (each marked `BUG FIX` inline):

| # | Bug (ANALYSIS.md ref) | File | Fix |
|---|---|---|---|
| 1 | Portfolio value = free cash → second rebalance sells ~98% of holdings (§2.4-1) | `src/scheduler/jobs.py` | `_snapshot_account()` = free cash + Σ position values; also writes `portfolio_value_history` |
| 2 | Positions keyed by T212 ticker, targets by yfinance ticker → nothing matched, everything sold and re-bought | `src/execution/t212_tickers.py`, `jobs.py` | `positions_to_yfinance()` re-keys; non-universe ISA holdings are ignored, never sold |
| 3 | GBX-quoted lines (CSP1, SGLN) valued in pence → 100× overstated | `src/execution/t212_client.py` | currency from instrument metadata, GBX ÷ 100 (**verify on demo**) |
| 4 | Buys never retried after sells ("awaiting settlement" then nothing) (§2.4-2) | `src/execution/rebalance_executor.py` | `wait_for_settlement()` polls up to 15 min before Phase 2; if still pending, an explicit ACTION-NEEDED alert |
| 5 | Sell quantity from yesterday's close → oversell rejected (§2.4-5) | `rebalance_executor.py` | full exits use the held quantity; partial sells clipped to holding |
| 6 | Circuit breaker compares fraction to 15.0; history never written (§2.4-3) | `jobs.py` | `CIRCUIT_BREAKER_DD = 0.15`; history logged on every job |
| 7 | "Least negative" defensive picks the most negative (§2.4-4) | `src/portfolio/allocator.py` | `top_defensive[0]` |
| 8 | CSPX signals in USD via a single current rate (§2.1) | `src/data/price_fetcher.py` | historical daily GBP/USD conversion |
| 9 | Partial intraday bar used as a close (§2.1) | `price_fetcher.py` | today's bar dropped |
| 10 | No data-quality gate; a bad print or short history silently distorts signals (§2.4-7/8) | `price_fetcher.py`, `jobs.py` | `validate_price_history()` (≥260 rows, ≤5 days stale, no |return| > 15%); any failure aborts the job **with a Telegram alert** |
| 11 | `.info` called per ticker (slow, flaky) | `price_fetcher.py` | static currency map, cached fallback |
| 12 | Telegram send result ignored; YES/NO substring match (§2.4-10) | `jobs.py`, `telegram_notifier.py` | `_alert()` logs failures; a failed approval prompt aborts the cycle; whole-word YES/NO |
| 13 | "Exposure is being reduced automatically" message on a no-op trigger (§5.1-5) | `jobs.py` | message now says NO ORDERS PLACED |
| 14 | Approval wait text says 24 h, timeout is 5 h | `jobs.py` | `APPROVAL_TIMEOUT_SECONDS`, text matches |
| 15 | Railway APScheduler + GitHub Actions both firing (§2.4-9) | `main.py` | scheduler disabled unless `RAILWAY_SCHEDULER=1` |
| 16 | Real Telegram token in `.env.example` (§2.4-11) | `.env.example` | placeholder (rotate the token) |

Not changed, by design: `src/signals/*` and `src/portfolio/allocator.py` still implement v1 (only the sign bug was fixed) so that `backtest.py` continues to replay v1 faithfully. `jobs.py` still calls v1; switching it to v2 is the integration step in §7.

## 7. Integration guide (for the executing model)

The strategy is three pure functions plus a helper. Nothing in it touches the network.

```python
from src.strategy.v2 import StrategyParams, signal_prices, compute_signals, target_weights, generate_trades, describe

params = StrategyParams()                       # validated defaults
# 1. Data: GBP close series (completed bars only) for params.all_tickers, ≥ 260 rows each,
#    plus the daily GBP/USD series.  fetch_price_history() already returns GBP and validates.
close_gbp = {t: fetch_price_history(t, period_days=400)["Close"] for t in params.all_tickers}
fx = get_gbpusd_history()
sig_px = signal_prices(close_gbp, fx, params)   # equities in USD, defensives in GBP
# 2. Signals (prev_trend = the trend flags saved from last month, for hysteresis; {} if none)
sig = compute_signals(sig_px, params, prev_trend=last_month_trend_flags, date=decision_date)
# 3. Target weights ({ticker: weight} incl. "CASH", sums to 1)
weights = target_weights(sig, params, sig_px)
# 4. Trades vs current positions (re-keyed to yfinance tickers, GBP values)
trades = generate_trades(weights, positions, portfolio_value, params)
telegram_text = describe(sig, weights, params)
```

Steps to go live, in order:

1. **Verify the IGLT.L line on T212** (ISIN IE00B1FZSB30, exchange ID 42, GBP) with the same procedure as `find_t212_tickers.py`, then move it from `PENDING_VERIFICATION` into `T212_TICKER_MAP`. Until then `StrategyParams(bonds="IGLS.L")` is the fallback (same Sharpe live, §4).
2. **Replace the v1 calls in `monthly_job()`** (`check_regime` … `build_target_allocation` … `generate_trade_list`) with the block above. Persist `sig.trend_on` in the `signal_snapshots` row (a JSONB column, e.g. `trend_flags`) and read it back as `prev_trend`.
3. **Pass `trade["quantity"]` through** to the executor for exits (the executor already honours it).
4. **Monthly timing**: set `get_nth_trading_day_of_month(..., n=1)`. The v2 backtest uses the 1st trading day; the 5th/10th give +0.05 to +0.1 Sharpe in the LSE window and −0.05 in the proxy window — i.e. noise. Pick the 1st and do not revisit.
5. **Weekly job**: keep it as a monitor only (regime, drawdown, data-quality). Its message must not imply action. Consider adding the v2 `describe()` output so the human sees what next month's rebalance would do.
6. **Human check**: approve the *rules* once, then approve each month's trade list only when (a) the list contains a full exit or entry, or (b) any order exceeds 40% of the portfolio, or (c) the data-quality gate raised. Pure re-weights inside the band should not need a reply — a TIMEOUT that costs a month in cash is worse than executing a 6 pp re-weight without a human.
7. **Demo cycle before live**: run one full month on the demo account including a forced full exit (set `trend_filter=False`, then `True` on a month when an instrument is below its SMA) to exercise sells → settlement wait → buys.
8. Verify on the demo account that `get_current_positions()` now returns GBP values for CSP1/SGLN (bug #3) — this was fixed from documentation, not observed.

## 8. What to expect, and when to intervene

Expected behaviour (LSE window): ~11% CAGR, ~10% volatility, Sharpe ~1.1, worst year around −5%, max drawdown 12–15%, ~14 orders a year. Over a 20-year window including a 2008-type bear: ~9% CAGR, Sharpe ~0.8, max drawdown ~15%. Bootstrap 90% bands: Sharpe 0.84–1.61 (LSE), 0.73–1.35 (proxy).

It will lag the S&P 500 in strong bull years (2019: 15.3% vs 27.2%; 2021: 14.9% vs 32.2%) and beat it in bears (2022: −5.0% vs −9.5%; 2008 proxy: −3.8% vs −13.8%; GFC peak-to-trough 2007-10→2009-03: +2.5% vs −33.8%). It will *not* fully protect against a crash that completes within one month (2020: −11.6% peak-to-trough, 2025: −12.0%, versus −26% and −16% for the index); it protects against bears that last a quarter or more.

Intervene (and re-run the backtests) if: annual turnover exceeds 500%; the strategy is more than 50% in cash for 6+ months while equities rise (signal-currency or data problem); the drawdown exceeds 20% (outside every tested window — something has changed); or the data-quality gate fires two months running (change data source).
