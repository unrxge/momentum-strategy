# Strategy — trend-filtered momentum with a diversified defensive sleeve

What runs in production (`src/strategy.py`, `src/config.py`), why, and the evidence. The previous version and its audit are in [ANALYSIS_v1.md](ANALYSIS_v1.md); the design iteration that led here is in [STRATEGY_v2_draft.md](STRATEGY_v2_draft.md).

## 1. Rules

Every rule is a published, decades-old one. There is no discretionary input and no approval step.

| Rule | Source | Setting |
|---|---|---|
| Relative momentum, 12-1 lookback | Jegadeesh & Titman (1993); Antonacci (2014) | rank the 4 equity ETFs by (price 1 month ago ÷ price 12 months ago − 1); hold the top 2, equal weight |
| Trend filter per instrument, 10-month SMA | Faber (2007) | hold an instrument only while its signal series is above its 210-day SMA |
| Strategic sleeves | Permanent-Portfolio / GTAA family | 70% equity sleeve, 15% gold, 15% UK gilts |
| Switched-off equity capital | Faber (cash) / Antonacci (bonds), blended | half to cash, half to the stronger of gold/gilts by 6-month momentum if it is in an uptrend and positive, else all cash |
| Rebalance tolerance bands | institutional practice | trade only when a holding is > 5 pp of portfolio from target; entries/exits always trade |
| Concentration cap / cash buffer | prudence / T212 order reservation | max 40% in one instrument; 2% uninvested |

Timing: signals on the previous trading day's close, orders on the **first trading day of each month** (LSE and NYSE both open) at 09:30 UTC. Nothing trades between rebalances; the Monday job only reports.

**No currency conversion anywhere.** Each equity ETF's trend and momentum are read from the US-listed total-return twin of the same index (SPY, QQQ, VT, VGK) in its own currency; gold and gilts use their own London GBP lines. The London GBP/GBX lines are what is bought and sold (no T212 FX fee inside the ISA). Backtest evidence for this choice: signals computed on GBP-converted prices were −0.17 Sharpe (LSE data) and −0.08 (2006→ proxy) with 4–7 pp deeper drawdowns, because sterling swings (2016, 2022) produced false exits and entries; signals on the underlying were the best variant in both windows (draft §4.5).

**Removed from the previous version, on evidence** (details in ANALYSIS_v1.md): the weekly fast-crash trigger (whipsawed 2020-03-02/06/09, cost 3.4% cumulative, no drawdown reduction); the automatic circuit breaker (could never fire, and would not have helped); inverse-volatility weighting (same Sharpe, +21% orders); the absolute-momentum gate (redundant with the trend filter, −0.08 Sharpe); the Telegram approval step (a 5-hour approval window on a strategy whose result moved ±0.15 Sharpe on one day of timing).

## 2. Instruments

| Key | Traded (LSE, T212 exchange 42, ISA-eligible) | Quote | T212 | Signal series |
|---|---|---|---|---|
| SP500 | CSP1.L iShares Core S&P 500 (Acc) | GBX | CSP1_EQ | SPY |
| NDX | EQQQ.L Invesco EQQQ Nasdaq-100 | GBX | EQQQl_EQ | QQQ |
| WORLD | VWRL.L Vanguard FTSE All-World | GBP | VWRLl_EQ | VT |
| EUROPE | VEUR.L Vanguard FTSE Developed Europe | GBP | VEURl_EQ | VGK |
| GOLD | SGLN.L iShares Physical Gold | GBX | SGLNl_EQ | SGLN.L |
| GILTS | IGLT.L iShares Core UK Gilts | GBP | IGLTl_EQ | IGLT.L |

Verified against T212 instrument metadata on 2026-09-04 (`tools/verify_t212_instruments.py`): ISIN, exchange 42, GBP/GBX. Fees: T212 charges no commission and no FX fee on these lines; no stamp duty on ETFs; the only cost is the bid-ask spread, modelled below at 10 and 25 bps per order.

## 3. Parameters (`StrategyParams`)

| Parameter | Value | Sensitivity (LSE data unless noted; see §5) |
|---|---|---|
| top_n | 2 | 1: Sharpe 0.94 (too concentrated); 3: 1.09; 4: 1.07 |
| mom_lookback / mom_skip | 252 / 21 | 202: 0.97, 302: 0.99; plain 12-month: 1.07 |
| trend_sma | 210 | 168: 1.05; 252: 1.04 with −15.4% max DD |
| trend_band (hysteresis) | 0 | 2%: same Sharpe, max DD −15.4% |
| def_lookback | 126 | 100: 1.03; 152: 1.00 |
| w_equity / w_gold / w_bonds | 0.70 / 0.15 / 0.15 | 80/10/10: CAGR +0.8 pp, DD −14.7%; 60/20/20: Sharpe 1.07, DD −11.1% |
| fallback | split | cash: 1.01; best_defensive: 1.04 |
| max_weight | 0.40 | 1.0: same |
| rebalance_band | 0.05 | 0: 25 orders/yr, same result; 0.10: same |
| rebalance day | 1st trading day | 5th: 1.02 (DD −15.5%); 10th: 1.05 (DD −16.6%); proxy 5th: 0.84 vs 0.72 — i.e. noise |

## 4. Backtest results

Engine: `backtest/` — uses the real `src/strategy.py`, `src/trading_calendar.py` and `src/broker.py` rounding. Signal on previous close, fill at the decision day's close, £5,000 start, no costs unless stated. Data: yfinance, LSE lines for P&L, US twins for signals; 70 broken Yahoo prints (2010–14 unit mixing) repaired.

### 4.1 LSE instruments, 2014-06 → 2026-09

| | CAGR | Vol | Sharpe | Sortino | Max DD | Calmar | Worst year | Orders/yr | Turnover/yr |
|---|---|---|---|---|---|---|---|---|---|
| **Strategy** | 10.8% | 10.4% | 1.03 | 1.51 | -12.8% | 0.85 | -7.7% | 14 | 354% |
| Static 70% World / 15% gold / 15% gilts, monthly | 11.0% | 10.5% | 1.04 | 1.50 | -17.2% | 0.64 | -7.8% | – | – |
| Static 60/40 World / gilts | 7.9% | 9.1% | 0.88 | 1.26 | -19.7% | 0.40 | -14.6% | – | – |
| S&P 500 (CSP1) buy & hold | 15.6% | 15.5% | 1.01 | 1.45 | -25.5% | 0.61 | -9.0% | – | – |
| All-World (VWRL) buy & hold | 12.4% | 14.4% | 0.89 | 1.25 | -25.0% | 0.50 | -8.4% | – | – |

Round trips: 71 closed, win rate 60.6%, average +4.9%, profit factor 7.0, average hold 187 days.

### 4.2 Proxy universe (US twins in GBP), 2006-04 → 2026-09 — includes 2008

| | CAGR | Vol | Sharpe | Sortino | Max DD | Calmar | Worst year |
|---|---|---|---|---|---|---|---|
| **Strategy** | 8.5% | 12.1% | 0.72 | 1.03 | -16.0% | 0.53 | -9.8% |
| Static 70/15/15 | 9.3% | 15.0% | 0.66 | 0.95 | -23.6% | 0.40 | -7.7% |
| Static 60/40 | 7.0% | 12.2% | 0.60 | 0.87 | -21.5% | 0.32 | -14.4% |
| S&P 500 buy & hold | 12.5% | 20.0% | 0.68 | 0.98 | -35.0% | 0.36 | -14.3% |
| All-World buy & hold | 9.8% | 20.4% | 0.55 | 0.79 | -37.0% | 0.27 | -18.0% |

### 4.3 Calendar years (LSE data)

| Year | Strategy | Static 70/15/15 | S&P 500 | All-World | UK CPI (approx.) |
|---|---|---|---|---|---|
| 2015 | -0.9% | 1.1% | 5.8% | 2.6% | 0.2% |
| 2016 | 20.4% | 27.5% | 34.1% | 29.9% | 1.6% |
| 2017 | 7.7% | 9.7% | 10.8% | 13.2% | 2.7% |
| 2018 | 3.0% | -2.3% | 0.0% | -4.7% | 2.1% |
| 2019 | 14.4% | 18.8% | 26.4% | 22.0% | 1.3% |
| 2020 | 18.9% | 13.5% | 13.7% | 12.1% | 0.6% |
| 2021 | 12.5% | 12.5% | 31.1% | 20.0% | 5.4% |
| 2022 | -7.7% | -7.8% | -9.0% | -8.4% | 10.5% |
| 2023 | 12.6% | 12.7% | 19.8% | 15.6% | 4.0% |
| 2024 | 22.3% | 17.6% | 27.3% | 19.6% | 2.5% |
| 2025 | 12.4% | 18.0% | 9.4% | 14.0% | 3.5% |
| 2026 YTD | 8.3% | 10.4% | 12.6% | 14.1% | 3.0% |

Beat UK CPI in 10 of 12 years (misses: 2015 −0.9%, 2022 −7.0% vs 10.5% CPI); beat the S&P 500 in 4 of 12 (2018, 2020, 2025 — the flat or crash years) and lagged it in every strong bull year.

### 4.4 Stress episodes

| Episode | Strategy | Peak-to-trough | Static 70/15/15 | S&P 500 |
|---|---|---|---|---|
| 2015-08 China/flash crash | -4.3% | -8.1% | -4.3% | -5.9% |
| 2016-Q1 sell-off | 2.9% | -3.8% | 3.7% | 1.1% |
| 2018-Q4 sell-off | -7.3% | -9.0% | -5.9% | -11.9% |
| 2020 COVID crash | -8.4% | -11.7% | -16.1% | -25.5% |
| 2020 V recovery | 16.3% | -3.1% | 22.4% | 35.3% |
| 2022 rate-hike bear | -4.1% | -7.9% | -10.2% | -7.9% |
| 2023 sideways/chop | 5.0% | -5.0% | 4.3% | 9.3% |
| 2025 tariff crash | -11.7% | -12.5% | -10.1% | -16.9% |
| 2025 tariff V recovery | 10.5% | -1.9% | 13.9% | 19.7% |
| (proxy) 2007-10→2009-03 GFC bear | -2.4% | -16.0% | -17.0% | -33.8% |
| (proxy) 2008 calendar | -8.5% | -16.0% | -6.3% | -13.8% |
| (proxy) 2009 recovery | 9.2% | -9.5% | 34.1% | 42.7% |
| (proxy) 2011 euro crisis | -5.7% | -11.5% | -9.0% | -12.3% |
| (proxy) 2015-16 chop | 3.8% | -10.1% | 11.2% | 16.4% |
| (proxy) 2020 COVID | -7.8% | -12.0% | -17.0% | -25.9% |
| (proxy) 2022 bear | -6.5% | -8.9% | -11.5% | -9.4% |

Reading: the trend filter roughly halves the loss in every crash that lasts more than a month (COVID −8.4% vs −25.5%, 2022 −4.1% vs −7.9% and −17.7% for a 60/40, GFC −2.4% vs −33.8%) and recovers about half of each V-shaped rebound. It cannot help with a crash that completes inside one month (2025 tariffs: −11.7%). It lags the index in strong bull years by 5–15 pp. That trade is the whole design.

### 4.5 Every variant (LSE data; Δ vs default)


| Variant | CAGR | Vol | Sharpe | Δ | Max DD | Calmar | Worst yr | Orders/yr |
|---|---|---|---|---|---|---|---|---|
| default | 10.8% | 10.4% | 1.03 | +0.00 | -12.8% | 0.85 | -7.7% | 14 |
| no_trend_filter | 13.3% | 11.8% | 1.11 | +0.08 | -15.3% | 0.87 | -11.1% | 6 |
| no_trend_no_selection | 12.2% | 11.1% | 1.09 | +0.06 | -16.8% | 0.73 | -10.9% | 1 |
| plain_12m_momentum | 11.2% | 10.4% | 1.07 | +0.04 | -12.8% | 0.88 | -6.9% | 15 |
| fallback_cash | 10.3% | 10.2% | 1.01 | -0.02 | -12.3% | 0.83 | -8.7% | 12 |
| fallback_best_defensive | 10.9% | 10.4% | 1.04 | +0.01 | -13.0% | 0.84 | -7.1% | 14 |
| no_max_weight_cap | 10.9% | 10.5% | 1.03 | -0.00 | -12.8% | 0.85 | -8.0% | 14 |
| top1 | 7.0% | 7.5% | 0.94 | -0.10 | -10.0% | 0.70 | -7.3% | 11 |
| top3 | 10.7% | 9.7% | 1.09 | +0.06 | -12.2% | 0.88 | -5.2% | 15 |
| top4_no_selection | 9.9% | 9.1% | 1.07 | +0.04 | -12.2% | 0.81 | -5.0% | 15 |
| mix_80_10_10 | 11.6% | 11.5% | 1.01 | -0.03 | -14.7% | 0.79 | -9.2% | 14 |
| mix_60_20_20 | 10.1% | 9.4% | 1.07 | +0.04 | -11.1% | 0.92 | -6.2% | 14 |
| sma_168 | 11.1% | 10.5% | 1.05 | +0.02 | -12.8% | 0.86 | -7.8% | 14 |
| sma_252 | 11.0% | 10.6% | 1.04 | +0.00 | -15.4% | 0.72 | -8.6% | 13 |
| mom_202 | 10.0% | 10.4% | 0.97 | -0.06 | -12.5% | 0.80 | -7.8% | 14 |
| mom_302 | 10.3% | 10.4% | 0.99 | -0.04 | -12.4% | 0.83 | -8.0% | 13 |
| def_lookback_100 | 10.8% | 10.4% | 1.03 | -0.00 | -12.8% | 0.84 | -7.3% | 14 |
| def_lookback_152 | 10.4% | 10.4% | 1.00 | -0.03 | -12.8% | 0.82 | -7.7% | 14 |
| hysteresis_2pct | 10.9% | 10.8% | 1.01 | -0.03 | -15.4% | 0.71 | -6.6% | 9 |
| rebal_band_0 | 10.8% | 10.2% | 1.05 | +0.02 | -12.5% | 0.86 | -7.5% | 25 |
| rebal_band_10pct | 10.9% | 10.5% | 1.03 | -0.00 | -13.2% | 0.82 | -7.6% | 13 |
| rebalance_5th_td | 10.9% | 10.6% | 1.02 | -0.01 | -15.5% | 0.70 | -6.2% | 12 |
| rebalance_10th_td | 11.0% | 10.4% | 1.05 | +0.02 | -16.6% | 0.66 | -6.9% | 13 |
| fill_same_close_LOOKAHEAD | 9.8% | 10.8% | 0.92 | -0.12 | -14.8% | 0.67 | -10.4% | 14 |
| cost_10bps | 10.4% | 10.4% | 1.00 | -0.03 | -12.9% | 0.81 | -8.0% | 14 |
| cost_25bps | 9.8% | 10.4% | 0.95 | -0.08 | -13.0% | 0.75 | -8.5% | 14 |
| capital_20000 | 10.8% | 10.4% | 1.03 | +0.00 | -12.8% | 0.85 | -7.7% | 14 |

Proxy 2006→:

| Variant | CAGR | Vol | Sharpe | Δ | Max DD | Calmar | Worst yr |
|---|---|---|---|---|---|---|---|
| proxy_default | 8.5% | 12.1% | 0.72 | +0.00 | -16.0% | 0.53 | -9.8% |
| proxy_cost_10bps | 8.0% | 12.1% | 0.69 | -0.03 | -16.3% | 0.49 | -10.1% |
| proxy_no_trend_filter | 11.2% | 15.0% | 0.77 | +0.05 | -22.3% | 0.50 | -11.2% |
| proxy_top3 | 8.4% | 11.5% | 0.75 | +0.03 | -13.0% | 0.65 | -7.3% |
| proxy_mix_80_10_10 | 8.9% | 13.2% | 0.70 | -0.02 | -18.6% | 0.48 | -11.5% |
| proxy_mix_60_20_20 | 8.1% | 11.0% | 0.75 | +0.03 | -13.9% | 0.59 | -8.1% |
| proxy_fallback_cash | 8.2% | 11.6% | 0.73 | +0.01 | -14.6% | 0.56 | -9.8% |
| proxy_rebalance_5th | 10.2% | 12.1% | 0.84 | +0.12 | -15.8% | 0.64 | -5.2% |

Notable: **no look-ahead benefit** — filling on the same close as the signal scores *lower* (0.92) than the honest next-close fill, so the result does not depend on fine timing (the previous version gained +0.12 Sharpe from look-ahead). Removing the trend filter raises CAGR by 2.5 pp at the cost of a −15% to −22% drawdown and a −11% worst year. Costs: 10 bps per order → Sharpe 1.00; 25 bps → 0.95.

## 5. Robustness

**Walk-forward** (27-point grid: SMA 168/210/252 × top 1/2/3 × equity 60/70/80%; best in-sample chosen per fold, both evaluated out-of-sample):

| Fold | Default IS Sharpe (rank/27) | Default OOS Sharpe / CAGR / MaxDD | Best-IS → OOS |
|---|---|---|---|
| IS 2014-06→2019-12, OOS 2020→ | 0.97 (14) | **1.08** / 11.4% / −12.8% | 0.89 / 6.8% / −10.0% |
| IS 2014-06→2021-12, OOS 2022→ | 1.05 (16) | **1.03** / 10.0% / −12.8% | 1.03 / 9.2% / −10.6% |
| IS 2018-01→2023-12, OOS 2024→ | 0.84 (15) | **1.47** / 16.2% / −12.7% | 1.50 / 14.2% / −9.3% |
| Proxy IS 2006-04→2014-05, OOS 2014→ | 0.54 (13) | **0.84** / 10.3% / −13.7% | 0.77 / 7.2% / −12.3% |

The defaults sit mid-grid in every in-sample fold and hold or improve out-of-sample in all four; re-optimising per fold would have changed OOS Sharpe by −0.19 to +0.03 and cost 1–5 pp of CAGR. (The previous version: best-in-grid in-sample, 1.30 → 0.78 out-of-sample.)

**Bootstrap** (6-month block bootstrap of monthly returns, 5,000 draws):

| | 5% | 25% | 50% | 75% | 95% | P(Sharpe < 0.5) |
|---|---|---|---|---|---|---|
| Sharpe, LSE 2014→ | 0.81 | 1.03 | 1.19 | 1.36 | 1.59 | 0.2% |
| Sharpe, LSE, 10 bps costs | 0.77 | 1.00 | 1.16 | 1.32 | 1.56 | 0.3% |
| CAGR, LSE | 7.0% | 9.3% | 10.8% | 12.4% | 14.7% | |
| Max DD, LSE | -15.4% | -11.6% | -9.6% | -9.1% | -7.0% | |
| Sharpe, proxy 2006→ | 0.64 | 0.82 | 0.96 | 1.09 | 1.27 | 1.0% |

## 6. What to expect

- ~10–11% CAGR at ~10% volatility on the last twelve years, ~8–9% over twenty; worst calendar year around −8% to −10%; max drawdown 12–16%; about 14 orders a year (roughly one rebalance touching 2–4 lines per month).
- It lags the S&P 500 in strong bull years and beats it in bears. It does not beat the S&P on raw return over either window (15.6% and 12.5% CAGR) — no drawdown-controlled multi-asset variant did; it wins on Sharpe, Calmar and worst year.
- Failure mode: a crash that completes within a month, followed by a V (2020, 2025) — it takes most of the loss and about half the rebound.
- Small accounts: at £5,000 each equity slot is £1,750 and the gold/gilt sleeves £750; orders are rounded down to 0.01 share, so rounding costs at most ~£6 per order. Below ~£2,000 the £50 minimum trade starts to skip re-weights.

## 7. Operations

- **Schedule**: `rebalance.yml` runs every weekday 09:30 UTC and acts only on the first trading day of the month; `weekly_status.yml` Mondays 09:30 UTC; `heartbeat.yml` daily 08:00 UTC. All jobs share a concurrency group so two never touch the account at once.
- **Safety gates (abort + Telegram, no trading)**: missing env vars; any pending order on the account; any instrument failing the data-quality gate (≥ 260 completed bars, last bar ≤ 6 days old, no |daily move| > 15%, no non-positive price); broker or unexpected errors.
- **Execution**: sells first (full exits with the exact held quantity, partials clipped to the holding), poll pending orders up to 15 min, then buys sized from re-read free cash and rounded down. Every order is logged to Supabase; a still-pending sell after 15 min is reported as ATTENTION and buys are left for the next run.
- **Drawdown alert**: the weekly and monthly messages flag a portfolio drawdown above 15% from the stored peak. Informational only — the backtest showed automatic crash exits lose money.
- **Non-universe holdings** in the ISA are ignored, never sold.
- **Going live**: set the `ENVIRONMENT` secret to `live` and add `T212_LIVE_API_KEY/SECRET/BASE_URL`. Nothing else changes.
