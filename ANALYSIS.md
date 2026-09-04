# Momentum Strategy — Backtest, Vulnerability Audit and Recommendations

Date: 2026-09-04. Scope: read-only analysis of the code in this repo plus a new standalone backtest ([backtest.py](backtest.py)). No live files were modified. All outputs referenced below are in `backtest_output/` and can be regenerated with:

```bash
venv/bin/python backtest.py --all
```

(Runtime ~45 min. `python backtest.py` alone runs the baseline in ~15 s. `--stage variants|sensitivity|walkforward|proxy` reruns one stage. matplotlib is only needed for the PNGs; set `BACKTEST_PYLIB=<dir>` to add a side-installed copy without touching the venv.)

---

## 0. Executive summary

**Verdict: NO-GO for live money in its current form. Signal logic is defensible but adds nothing measurable; the execution layer has bugs that would lose money on the second rebalance.**

What the backtest shows (live logic replayed 2014-06-24 → 2026-09-04, £20k, no costs):

| | Strategy (live logic) | Static 80% VWRL / 15% SGLN / 5% IGLS, monthly rebalanced | CSPX buy & hold |
|---|---|---|---|
| CAGR | 11.9% | 12.1% | 15.7% |
| Ann. vol | 11.9% | 11.8% | 17.6% |
| Sharpe (rf=0) | 1.00 | 1.02 | 0.91 |
| Sortino | 1.44 | 1.47 | 1.33 |
| Max drawdown | −18.0% | −19.1% | −26.1% |
| Calmar | 0.66 | 0.63 | 0.60 |
| Orders | 415 | ~36 | 1 |

1. **The regime + momentum layers do not beat a signal-free static mix of the same ETFs.** Sharpe 1.00 vs 1.02, drawdown −18.0% vs −19.1%, with ten times the trading. The 90% bootstrap band on the strategy's Sharpe is **[0.73, 1.63]** — every variant tested in this report, good or bad, sits inside that band. Nothing here is statistically distinguishable from the static mix.
2. **The 200-day regime filter mistimed both fast crashes in the sample.** It flipped to "unhealthy" on 2020-04-07 and 2025-04-07 — 10 trading days and 0 trading days after the respective lows — then re-entered on 2020-06-05 / 2025-06-06 after 60–70% of the recovery. In 2025 the strategy took the full −16.3% crash and captured 7.5% of an 18.9% rebound.
3. **The crash trigger and circuit breaker exist only as Telegram messages.** `weekly_job()` sends "Exposure is being reduced automatically" but places no orders. When wired up as designed, the fast-crash trigger changed Sharpe by +0.02 (−0.02 after 10 bps costs), left max drawdown unchanged (−18.2%), and produced the classic whipsaw: sell 2020-03-02, buy back 2020-03-06, sell again 2020-03-09.
4. **Four execution-layer bugs would break the second live rebalance** regardless of signal quality (Section 2.4): portfolio value taken from *free cash*, drawdown compared in the wrong units, the "least negative defensive" picking the *most* negative, and buys after sells never retried until the next month.
5. **Walk-forward: in-sample 2014–2019 Sharpe 1.30 became 0.78 out-of-sample 2020–2026** with the live parameters, and re-optimising the parameters on any in-sample fold would not have helped (OOS change 0.00 / +0.14 / +0.01). The whole 18-point parameter grid moves together; there is nothing to tune, only a period effect.
6. **The 2006→2026 proxy replay shows what the filter is for**: +16.4% through the 2007–09 GFC bear vs −24.1% for the static mix, at the cost of most of the 2009 rebound. 20-year Sharpe 0.84 and max drawdown −21.4% versus 0.63 and −28.7% for the static mix — so the rules do add value over a window that contains a slow bear, and none over one that does not. The 2022-type bear (equities and gilts down together) is the failure mode in both datasets.
7. **Results are hypersensitive to timing.** Changing only the rebalance day of month moves Sharpe from 1.00 (5th trading day, current) to 1.18 (1st) or 1.13 (10th). Using same-day closes instead of next-day closes adds +0.12 Sharpe, almost all of it from the April 2025 crash week. A strategy whose 12-year Sharpe swings ±0.15 on a calendar-day choice does not have a robust edge to protect with a 5-hour Telegram approval window.

Highest-leverage fixes, in order: (i) fix the four execution bugs, (ii) cut turnover (equal-weight or 60-day vol, `12-1` momentum), (iii) decide whether the regime filter earns its keep at all versus the static mix, (iv) only then revisit crash logic.

---

## 1. What was tested and how

### 1.1 The strategy as actually coded

Read from [src/scheduler/jobs.py](src/scheduler/jobs.py), [src/signals/](src/signals/), [src/portfolio/allocator.py](src/portfolio/allocator.py), [src/execution/](src/execution/):

- **Universe** (`jobs.py:28-29`): growth = CSPX.L, EQQQ.L, VWRL.L, VEUR.L; defensive = SGLN.L, IGLS.L. Six LSE-listed UCITS ETFs, mapped to T212 GBP/GBX lines in [t212_tickers.py](src/execution/t212_tickers.py).
- **Regime** (`regime.py:7`): CSPX.L close > simple 200-day MA → "healthy", else "unhealthy". Evaluated only on CSPX.
- **Fast crash** (`regime.py:26`): CSPX 10-day return < −7%. **Logged and messaged by `weekly_job()`, no orders placed.**
- **Circuit breaker** (`jobs.py:148`): `check_drawdown()` returns a fraction (0.15), compared against `15.0`. **Can never fire.** The history it reads (`portfolio_value_history`) is also never written by any job.
- **Momentum** (`momentum.py`): growth ranked by 252-row trailing return (`iloc[-1]/iloc[-252]`, i.e. 251 trading days); top 2 selected. Defensives ranked by 63-row return.
- **Allocation** (`allocator.py:111`): healthy → top-2 growth inverse-20-day-vol weighted × 80%, SGLN 15%, IGLS 5%, then everything × 0.98 (2% cash buffer). Unhealthy → if both defensives' 3-month return < 0: 25% top growth, 10% "least negative" defensive, 65% cash; else 50% top defensive, 25% second, 10% top growth, 15% cash.
- **Rebalance** (`jobs.py:216`): monthly, on the 5th trading day where both LSE and NYSE are open (changed from 1st trading day in the latest commit `40a7c7e`). Trades generated by `generate_trade_list()` for any delta ≥ £150; market orders via T212; sells first, buys only if no pending orders.
- **Schedule**: GitHub Actions cron at 09:00 UTC daily (monthly job self-gates) and Mondays (weekly). `main.py` / `railway.json` still describe a Railway APScheduler deployment of the same jobs.

Parameters hard-coded in the code path (none were fit to data, because no backtest existed): 200, 252, 63, 20, 10, −7%, 15%, top-2, 80/15/5, 50/25/15/10, 65/25/10, 0.98, £150, 5th trading day, plus an unused 35% profit-take in `risk.py`. Sixteen numbers.

### 1.2 Backtest fidelity

[backtest.py](backtest.py) **imports and calls the live functions**: `check_regime`, `check_fast_crash`, `rank_growth_assets`, `rank_defensive_assets`, `select_top_growth`, `check_drawdown`, `build_target_allocation`, `generate_trade_list`, `T212Client.calculate_order_quantity` (4 dp, 2 dp for SGLN as in `rebalance_executor.py:152`), and the live calendar `get_nth_trading_day_of_month` / `is_trading_day` from `jobs.py`. Parameter variants are produced by monkey-patching the indicator functions those modules call (`patched_params()`), so the live functions still execute; the crash threshold, a literal in `check_fast_crash`, is varied by scaling the return it compares (documented in code).

Modelled, because the live code does not do it or cannot be replayed:

| Item | Live code | Backtest |
|---|---|---|
| Portfolio value | `account_cash["free_cash"]` (bug, §2.4) | total equity |
| Signal timestamp | yfinance call at 09:00 UTC, which returns a **partial intraday bar** (verified 09:24 UTC today: a 2026-09-04 row with 30k volume was present) | close of the prior trading day; fills at the rebalance day's close (`fill=next_close`). `next_open` and `same_close` variants bracket the timing effect |
| Crash / circuit-breaker action | none | when enabled: on the Monday check, sell to `build_target_allocation("unhealthy", top_growth=[])` as in `test_crash_simulation.py:123`, stay there until the next monthly rebalance |
| Costs | zero commission on T212; spread/slippage unmodelled | 0 / 10 / 25 bps one-way of traded value |
| Data | yfinance `period=400d`, `auto_adjust=True`, GBp/100, USD ÷ *today's* GBPUSD | same source and adjustments, full history, cached in `backtest_output/cache/` |

**Currency:** yfinance quotes CSPX.L in **USD** (the other five in GBP/GBp). `price_fetcher.py:83` divides the whole USD history by the *current* GBPUSD rate, so every CSPX signal (regime, momentum, vol) is a USD signal, while the portfolio is in GBP. The backtest reproduces this ("live" signal mode) and also runs a "gbp" mode. P&L is always in GBP, using CSPX_USD ÷ daily GBPUSD (validated against the CSP1.L GBX line: ratio std 0.5%, 21-day return correlation 0.98).

**Data cleaning:** Yahoo's EQQQ.L and SGLN.L histories mix USD and GBP prints in 2010–2014 (±60% one-day swings). 45 EQQQ and 25 SGLN closes were repaired against QQQ/GLD in GBP (last bad print 2014-10-29). The live pipeline has no such guard; see §2.4.

**Window:** full six-ETF history begins 2013-05-21 (VEUR.L listing); after the 275-row warm-up the first rebalance is 2014-07-08. 12.2 years, 146 monthly rebalances, 635 weekly checks. Does not contain 2008; a proxy replay from 2006 is in §5.3.

---

## 2. Vulnerability audit

### 2.1 Look-ahead bias and data leakage

**In the live code**

- **Partial-bar signal (confirmed).** At 09:00 UTC the yfinance call already returns today's row with the current intraday price as "Close". The 200-MA and the 10-day return therefore use a price that is 1–2 hours into the session, not a close. This is not look-ahead (the price exists) but it makes signals non-reproducible and slightly noisier than the backtest assumes. Backtest evidence of how much timing matters: `fill_same_close_LOOKAHEAD` (signal and fill on the same close) = Sharpe 1.13 vs 1.00; `fill_next_open` = 1.01. The +0.12 is concentrated in 2025 (+10 pp) and 2026 (+5.6 pp) — one day of timing around the tariff crash is worth a full year of return.
- **USD signals, GBP book (confirmed, material).** Sample decision dates where the two disagree: 2022-10-06 CSPX 12-month return −11.4% in USD vs +6.4% in GBP, regime "unhealthy" in USD vs "healthy" in GBP; 2025-06-06 regime "healthy" in USD vs "unhealthy" in GBP. In the backtest, GBP signals score *lower* (Sharpe 0.97, MaxDD −20.6%) because sterling's 2022 slide masked the equity bear in GBP terms. That is sample luck, not a reason to keep the mismatch; the ranking currently compares USD momentum for CSPX against GBP momentum for the other three.
- **Missing bars.** Yahoo has no 2026-09-03 bar for CSPX.L (yesterday) nor 2025-10-24 / 2026-03-06. `iloc[-252]` silently shifts by a day when a bar is missing. Minor, but there is no check.
- **Survivorship.** The universe was chosen in 2026 from ETFs that exist and are large today. All six survived the whole window, so the backtest cannot measure it. Inference beyond the data: with 6 broad index ETFs this is a small effect on returns, but the *choice* of EQQQ (the best-performing asset class of the decade) as one of four growth candidates is itself a hindsight decision — EQQQ was the top pick in 127 of 292 selection slots and contributed the two largest round-trip gains (£8,868 and £5,385).
- **Adjusted prices.** `auto_adjust=True` gives dividend-adjusted closes for both MA and returns, which is consistent. Accumulating ETFs (CSPX, EQQQ, SGLN) are unaffected; VWRL/VEUR/IGLS distribute, and using adjusted closes for the MA is the correct choice.

**In the backtest itself.** Signals use the prior day's close and fill at the next close; there is no future information in the windows (`Backtester._windows` slices `.loc[:sdate]`). The one deliberate leak is the `fill_same_close_LOOKAHEAD` variant, labelled as such. Data repair uses a centred rolling median (contemporaneous, ±63 days) on 2010–2014 prints only, all before the first rebalance.

### 2.2 Overfitting risk

None of the sixteen parameters were fit to data, so there is no in-sample optimisation to overfit. The risk is the opposite: they were never tested, and the backtest shows the result depends on some of them more than the underlying edge warrants:

| Parameter (±20%) | Sharpe range | Comment |
|---|---|---|
| Rebalance day of month (1st / 5th / 10th) | **1.18 / 1.00 / 1.13** | Largest single effect. The 5th was chosen yesterday without a backtest and is the worst of the three. 2025 return: 17.2% (1st), 4.8% (5th), 13.1% (10th) — because 2025-04-07 was the exact low. |
| MA length 160 / 200 / 240 | **1.09 / 1.00 / 0.94** | Monotonic; MaxDD −17.5% / −18.0% / −20.8%. |
| Growth lookback 202 / 252 / 302 | 1.00 / 1.00 / 1.01 | Flat. |
| Defensive lookback 50 / 63 / 76 | 1.00 / 1.00 / 1.02 | Flat. |
| Vol window 16 / 20 / 24 (and 60, EWMA-30) | 1.01 / 1.00 / 1.01 (1.01, 1.01) | Flat; weight ratio between two similar equity ETFs barely matters. |
| Crash threshold −5.6% / −7% / −8.4% / −10% (trigger on) | 1.00 / 1.02 / 1.02 / 1.01 | Flat, but the number of triggers goes 14 / 7 / 7 / 4. |
| Vol target 9.6% / 12% / 14.4% | 1.00 / 0.99 / 0.99 | Flat; only lowers CAGR. |
| Min trade £120 / £150 / £180 | 1.00 / 1.00 / 1.00 | Irrelevant at £20k. At £5k, 26 of 146 months generate no trades. |

Full table: `backtest_output/sensitivity.md`. **Conclusion:** the allocation-level parameters are robust in the sense that they do not matter; the timing parameters (rebalance day, MA length) matter more than the edge itself. 12 years / 146 rebalances is enough to characterise this but not enough to choose between MA 160 and 240 with confidence. Walk-forward results in §5.1.

### 2.3 Regime dependency

Regimes present in the window and how the live logic behaved (`backtest_output/periods.md`; benchmark = static 80/15/5):

| Episode | Type | Strategy | Static 80/15/5 | What happened in the log |
|---|---|---|---|---|
| 2015-08 | fast correction | −4.2% | −5.3% | 12-m momentum had VWRL; sold 2015-09-08 at −4.5% (round trip) |
| 2016-Q1 | slow bleed then V | +5.3% | +2.9% | went unhealthy 2016-01-08 → 50% SGLN; gold +24% |
| 2018-Q4 | slow bleed | −2.8% | −7.2% | unhealthy 2018-11-07; helped |
| 2020 COVID crash | fast crash | **−17.0%** | −19.1% | monthly check 2020-03-06 still "healthy" (price bounced above MA on 03-05); flipped 2020-04-07, after the low |
| 2020 V recovery | V | +21.8% | +26.1% | in 50% gold / 25% gilts / 10% EQQQ until 2020-06-05 |
| 2022 bear | slow bleed + rate shock | **−14.5%** | −7.0% | bought 48% VEUR on 2022-02-07 (12-m momentum), sold 2022-03-07 at −12.0%; then 25% IGLS in a gilt bear; four months on the "least negative" bug path (§2.4) |
| 2023 chop | sideways | +0.6% | +5.6% | 26-month healthy stretch, but the top-2 set changed 6 times (5 in calendar 2023); VWRL round trip −3.2%, VEUR −1.6% |
| 2025 tariff crash | fast crash | **−16.3%** | −11.5% | bought 43% VEUR 2025-03-07, sold 2025-04-07 at −12.4% (worst round trip, −£3,537); flipped unhealthy on the low day |
| 2025 V recovery | V | +7.5% | +15.7% | 49% gold / 24% gilts / 10% CSPX until 2025-06-06 |

Pattern: the filter helps in slow bleeds (2018, 2016) and is worse than useless in fast crash + V shapes (2020, 2025), where it sells at the bottom and misses the rebound. Momentum chasing into VEUR twice (Feb 2022, Mar 2025) produced the two worst round trips in the log. Underrepresented in the sample: a prolonged (>12 month) sideways market with repeated MA crossings (2011–2012 and 2015–2016 partially), and a real multi-year bear with a slow recovery (2000–2003, 2008–2009) — the proxy replay in §5.3 covers 2008.

Regime flips at the monthly check: 12 in 146 months; four unhealthy stints of 2–3 months (2015, 2016, 2020, 2025) are whipsaws in hindsight, two (2018-19 at 3 months and 2022-23 at 11 months) were justified.

### 2.4 Execution and infrastructure risk

Confirmed by reading the code, ranked by how fast they lose money:

1. **Portfolio value = free cash** ([jobs.py:247](src/scheduler/jobs.py#L247)). `account_value = account_cash["free_cash"]`. After the first deployment free cash is ~2% of the account, so on the second monthly run the target allocation is built on 2% of the portfolio, `generate_trade_list()` sees every position as over-target and **generates SELL orders for ~98% of every holding**. The `REBALANCE_TWO_PHASE_SUMMARY.md` test only passed because free == total on a fresh demo account. Fix: use `total` (or total cash + position values). The backtest uses total equity, so its numbers assume this is fixed.
2. **Buys never happen after sells** ([rebalance_executor.py:482-500](src/execution/rebalance_executor.py#L482), [jobs.py:439](src/scheduler/jobs.py#L439)). If any order is pending after Phase 1, the job returns "sells_placed_awaiting_settlement" and tells Telegram "BUY orders will execute automatically once settlement clears". Nothing schedules that: `monthly_job()` exits on any day that is not the 5th trading day, so the account sits in cash until next month's run recomputes from scratch. Every regime flip (12 in the backtest) is a sell-then-buy.
3. **Circuit breaker can never fire** ([jobs.py:148](src/scheduler/jobs.py#L148)): fraction vs percent. And `portfolio_value_history` is never populated (`log_portfolio_value` has no callers), so even with the units fixed the drawdown is always 0.
4. **"Least negative" defensive is the most negative** ([allocator.py:185](src/portfolio/allocator.py#L185)): `top_defensive[-1]` on a descending list. Hit on 2022-06-09, 07-08, 08-05, 09-08 in the backtest (e.g. 2022-06-09: IGLS −1.2% vs SGLN −1.75% → bought SGLN). P&L effect in-sample is nil (+0.00 Sharpe, `fix_least_negative`), but it is a sign-error in the only "defensive" branch.
5. **Sell quantity computed from yesterday's close** ([rebalance_executor.py:157](src/execution/rebalance_executor.py#L157)): `amount_gbp / price` with `price_data.iloc[-1]`. If the price is lower at execution, the quantity exceeds the holding and T212 rejects the sell; the position is not exited and there is no retry. The backtest clips these (`ev_oversell_clipped` = 0 only because it fills at the same price it sizes on).
6. **Precision heuristic** ([rebalance_executor.py:150](src/execution/rebalance_executor.py#L150)): 4 dp everywhere, 2 dp for SGLN "because T212 rejects it". Any other instrument that rejects 4 dp fails the order silently (logged, not retried, not surfaced beyond a summary count).
7. **Silent no-op months.** Any yfinance exception (rate limit, `info` lookup failure — `price_fetcher.py:75` calls `ticker_obj.info` on every fetch, the slowest and flakiest yfinance endpoint) or a short history (<252 rows → `ValueError` in `trailing_return`) makes `monthly_job()` `return` after a `print`. No Telegram message, no Supabase row. A missed month is invisible.
8. **No bad-print guard.** The unit-mixing prints found in 2010–2014 (§1.2) would have produced a +60% "momentum" for EQQQ. yfinance still serves them today. A single bad print in the last 252 rows flips the ranking.
9. **Scheduler**: GitHub Actions cron is best-effort (routinely 10–60 min late, occasionally skipped under load); the 6-hour job timeout and 5-hour Telegram wait mean a late start can hit the LSE close (16:30 BST = 15:30 UTC), leaving orders to fill at the next open. `main.py` + `railway.json` are still in the repo; if the Railway service is still deployed, both schedulers run the same job at 09:00 UTC — two Telegram prompts and, on "YES", **two sets of orders**.
10. **Telegram single point of failure.** `send_message()` returns `False` on failure and the caller ignores it. `wait_for_reply()` matches `"YES" in text.upper()` / `"NO" in text` — "NOT NOW" or "NO IDEA, YES" both parse as an answer (YES wins). The 24-hour text in the message is wrong; the timeout is 18,000 s (5 h). A missed Monday message means a crash trigger is never seen by a human, and nothing else acts on it.
11. **Secrets**: [.env.example](.env.example) contains what looks like a real Telegram bot token and chat id, committed to git. Rotate it.
12. **Missed rebalance day.** If the 5th-trading-day run fails, there is no catch-up; the next attempt is next month. Backtest proxy for the cost of a skipped month: `rebalance_10th_td` vs `rebalance_5th` differ by 0.13 Sharpe, i.e. the *timing* of a rebalance matters more than whether it happens (`weight_equal` with 328 orders vs 415 has identical Sharpe).

### 2.5 Cost realism

T212 charges no commission and no stamp duty applies to these ETFs; all six are GBP/GBX lines so the 0.15% FX fee does not apply. The cost is the bid–ask spread plus market-order slippage at 09:00–10:00 UK time (US-index ETFs price off futures before the US open; spreads on CSP1/EQQQ are typically 3–10 bps, VEUR/SGLN 5–15 bps, IGLS 3–5 bps). I modelled 10 bps and 25 bps one-way on every order.

| Cost | CAGR | Sharpe | £ paid over 12.2 yrs on £20k start | Annual turnover |
|---|---|---|---|---|
| 0 | 11.91% | 1.00 | 0 | 395% |
| 10 bps | 11.48% | 0.97 | £2,108 | 395% |
| 25 bps | 10.84% | 0.92 | £5,055 | 394% |

Turnover is the problem: **395% of equity traded per year** (34 orders/yr, £2.17M traded on a portfolio averaging ~£45k). Drivers, from the order log: (a) monthly re-weighting between the two growth ETFs from a 20-day inverse-vol estimate — mean absolute weight change in the growth sleeve is 25.5 pp per month; (b) 49 changes of the top-2 set in 146 months; (c) 12 regime flips, each a near-total turnover. Equal weighting cuts orders to 328 (turnover 333%) at identical Sharpe; a 4-ETF ERC portfolio without selection cuts turnover to 182% with 77% round-trip win rate, also at Sharpe 1.00. The crash trigger raises turnover to 444% and the event-driven variant to 657%.

### 2.6 ISA / T212-specific constraints

- **Fractional shares in an ISA.** The code relies on fractional quantities (4 dp) for every order. HMRC's position on fractional shares as qualifying ISA investments changed in 2024–25; T212 offers them, but confirm the current regulations and T212's ISA terms before relying on 0.0001-share fills. If fractional fills are disallowed for a line, a £980 IGLS order becomes 7 whole shares (£868) and a £245 SGLN order becomes 3 shares (£192); the £150 min-trade threshold then interacts with whole-share rounding. This is a rule check, not something the backtest can show — flagged as inference.
- **Cash inside the ISA** is fine (the 65% cash branch stays in the wrapper), but T212's ISA cash interest is a separate opt-in; the backtest assumes 0% on cash. In 2022–23 (11 months at 17–66% cash) that understates return by roughly 1–2% cumulative.
- **Market orders only** (`place_market_order`). No limit protection; on a Monday after a −7% week the spread at 09:00 UTC is at its widest. The two-phase design also means sells and buys can be an hour or a month apart (§2.4 item 2).
- **T212 over-reservation** is already handled by the 0.98 factor, which permanently keeps 2% in cash (cost ≈ 0.2%/yr at 12% returns).
- **ISA subscription limit** does not affect rebalancing, but the `initial_capital=5000` run shows the £150 min-trade rule leaves 26 of 146 months untraded at that size. Below ~£10k the strategy effectively becomes quarterly.
- **KID/PRIIPs and 'complex instrument' flags** do not apply to these UCITS ETFs. SGLN is an ETC, not an ETF, and is ISA-eligible; T212 confirms the line (`SGLNl_EQ`) in `t212_tickers.py`.

---

## 3. Sharpe-ratio optimisation (tested)

All deltas are vs baseline Sharpe 1.00; bootstrap 90% band on the baseline is [0.73, 1.63], so treat |Δ| < 0.15 as noise unless the mechanism is clear.

### 3.1 Momentum lookback

| Variant | Sharpe | Δ | CAGR | MaxDD | Turnover | RT win | Comment |
|---|---|---|---|---|---|---|---|
| 12-month (live) | 1.00 | — | 11.9% | −18.0% | 395% | 61% | |
| 6-month | 0.97 | −0.04 | 11.2% | −18.0% | 590% | 65% | 70 round trips; too twitchy |
| Blend mean(3, 6, 12 m) | 1.01 | +0.01 | 12.0% | −18.0% | 441% | 69% | no gain, more trading |
| **12-1 (skip last month)** | **1.06** | **+0.06** | 12.7% | −18.0% | 399% | 69% | profit factor 8.0 vs 5.2; standard academic fix for short-term reversal; no new parameter |
| 202 / 302 rows | 1.00 / 1.01 | 0 | | | | | flat |

Recommendation: switch `trailing_return(prices, 252)` to a 12-1 definition (`prices.iloc[-22] / prices.iloc[-252] - 1`). Small, consistent, parameter-free.

### 3.2 Volatility sizing

| Variant | Sharpe | Δ | CAGR | MaxDD | Exposure |
|---|---|---|---|---|---|
| 20-day inverse vol (live) | 1.00 | — | 11.9% | −18.0% | 94% |
| 60-day inverse vol | 1.01 | +0.01 | 12.0% | −18.2% | 94% |
| EWMA span 30 | 1.01 | +0.01 | 11.9% | −18.1% | 94% |
| Equal weight | 1.01 | +0.01 | 12.1% | −18.1% | 94% |
| Portfolio vol target 10% | 1.00 | 0 | 10.2% | −16.5% | 85% |
| Portfolio vol target 12% | 0.99 | −0.01 | 10.9% | −17.5% | 90% |
| Portfolio vol target 15% | 0.99 | −0.01 | 11.5% | −17.5% | 93% |

The inverse-vol step is decorative: two US/global equity ETFs have similar vol, so the weights oscillate around 50/50 on noise. Equal weight gives the same Sharpe with 21% fewer orders. A portfolio-level vol target reduces drawdown proportionally to the return it gives up (Calmar 0.62 vs 0.66) — no risk-adjusted gain, because the 2020 and 2025 drawdowns happened from low-vol states that a 60-day estimator cannot see coming. Event-driven vs fixed-schedule: see §3.5.

### 3.3 Fast-crash trigger: with vs without

| Variant | Sharpe | CAGR | MaxDD | Orders | Crash exits | Final £ |
|---|---|---|---|---|---|---|
| Baseline (trigger inert, as coded) | 1.00 | 11.91% | −18.0% | 415 | 0 | 78,907 |
| Trigger on (−7%, 10 d) | 1.02 | 11.59% | −18.2% | 433 | 5 | 76,222 |
| Trigger on + 10 bps | 0.98 | 11.10% | −18.9% | 430 | 5 | |
| Trigger −5.6% | 1.00 | 11.21% | −17.7% | 449 | 10 | |
| Trigger −10% | 1.01 | 11.67% | −18.7% | 426 | 3 | |
| Circuit breaker 15% (units fixed) | 1.00 | 11.58% | −18.0% | 418 | 2 | |

Each crash exit, relative to doing nothing until the next monthly rebalance (`crash_trigger_on_orders.csv`):

| Exit filled | Re-entry | Strategy | Baseline | Relative | Allocation after exit |
|---|---|---|---|---|---|
| 2018-02-12 | 2018-03-07 | −0.1% | +3.1% | **−3.1%** | 10% SGLN, 90% cash (both defensives negative → "least negative" bug path with `top_growth=[]` leaves 90% cash) |
| 2020-03-02 | 2020-03-06 | +1.4% | −2.1% | +3.6% | 50% SGLN / 25% IGLS |
| 2020-03-09 | 2020-04-07 | +2.7% | +4.8% | **−2.0%** | 50% SGLN / 25% IGLS (re-bought on 03-06 because the monthly check said "healthy", sold again 03-09) |
| 2022-06-20 | 2022-07-08 | −1.5% | +0.9% | **−2.4%** | 50% SGLN / 25% IGLS |
| 2022-09-26 | 2022-10-07 | +0.1% | −0.5% | +0.5% | 50% SGLN / 25% IGLS |

**Finding:** the trigger does not reduce max drawdown (the −18% troughs on 2020-03-23 and 2025-04-08 are reached *before* a 10-day −7% print on a Friday close, or the exit lands on the low), costs 3.4% of final equity, and after realistic costs lowers Sharpe. In the 2025 tariff crash the flag first appeared on the 2025-04-07 check (Friday 04-04 data, after the two-day −10% fall), which was also the monthly rebalance day — the monthly logic had already moved to 49% gold / 24% gilts / 10% CSPX on the low, so the trigger would have added nothing. Note that the weekly checks on 2025-03-10, 03-17, 03-24 and 03-31 all reported "unhealthy" while the book stayed 78% in equities, because the weekly job takes no action; the `event_driven_regime_flip` variant that acts on that signal exited on 2025-03-10 (Sharpe 1.10, but MaxDD −19.8% overall from extra whipsaws elsewhere). Inference beyond the data: a fast-crash trigger only pays if it fires *before* the bulk of the fall, which a 10-day trailing return by construction does not. Recommendation: do not wire it up as designed; if any intraday risk control is wanted, use a wider, monthly-evaluated one (e.g. the hysteresis MA in §3.5) or none.

### 3.4 Diversification within the rotation universe

| Variant | Sharpe | CAGR | Vol | MaxDD | Turnover | RT win |
|---|---|---|---|---|---|---|
| Top-2 inverse vol (live) | 1.00 | 11.9% | 11.9% | −18.0% | 395% | 61% |
| Top-3 inverse vol | 1.03 | 11.4% | 11.0% | −18.3% | 389% | 68% |
| Top-3 ERC (60 d cov) | 1.02 | 11.3% | 11.0% | −18.4% | 347% | 68% |
| All 4 ERC, no selection | 1.00 | 10.7% | 10.6% | −19.3% | **182%** | **77%** |

With two assets ERC equals inverse vol, so correlation-awareness only matters at 3+. The four growth ETFs are 0.85–0.95 correlated; there is no diversification to harvest inside the sleeve, only turnover to avoid. The honest read: selection (top-2 vs holding all four) buys +1.2% CAGR for double the turnover and no Sharpe. If costs come in at the 25 bps end, holding all four wins.

### 3.5 Regime filter / macro overlay

| Variant | Sharpe | Δ | CAGR | MaxDD | Turnover | Notes |
|---|---|---|---|---|---|---|
| CSPX > 200 MA, USD (live) | 1.00 | — | 11.9% | −18.0% | 395% | 12 flips |
| MA 160 | 1.09 | +0.09 | 12.4% | −17.5% | 448% | |
| MA 240 | 0.94 | −0.07 | 11.1% | −20.8% | 414% | |
| Hysteresis ±2% band | 1.02 | +0.01 | 11.6% | −17.5% | 411% | |
| Hysteresis ±3% band | 1.03 | +0.02 | 11.6% | −17.5% | 403% | fewer whipsaws, slower exits |
| MA and 12-m return > 0 (dual) | 1.00 | 0 | 11.8% | −18.0% | 396% | |
| Per-asset trend filter | 0.99 | −0.01 | 11.7% | −18.0% | 442% | |
| Signals in GBP + hysteresis | 0.95 | −0.05 | 11.1% | −17.6% | 415% | |
| Event-driven: also rebalance on weekly regime flip | 1.10 | +0.10 | 12.4% | **−19.8%** | 657% | 33 extra rebalances; +0.04 after 10 bps; deeper drawdown |
| No growth sleeve when unhealthy | 0.97 | −0.03 | 11.5% | −18.0% | 395% | the 10%/25% growth in unhealthy helps |
| Combo: GBP + hysteresis + blend | 0.89 | −0.11 | 10.3% | −17.0% | 481% | stacking "sensible" fixes made it worse |

No overlay cut drawdown without cutting return in proportion. The hysteresis band is the only one I would adopt, and for operational reasons (fewer flips = fewer sell-then-buy cycles through the broken two-phase path) rather than Sharpe. The combo result is a useful warning: three individually-reasonable changes compounded to −0.11.

---

## 4. Robustness testing

### 4.1 Walk-forward / out-of-sample

Grid of 18 combinations (MA 160/200/240 × growth lookback 126/189/252 × vol window 20/60), scored in-sample, then the best-IS combination and the live parameters both evaluated out-of-sample (`backtest_output/walkforward.md`):

| Fold | Live params IS Sharpe (rank in grid) | Live params OOS Sharpe / CAGR / MaxDD | Best-IS params | Best-IS OOS Sharpe / CAGR / MaxDD | Grid IS range |
|---|---|---|---|---|---|
| IS 2014-06→2019-12, OOS 2020-01→2026-09 | **1.30 (1/18)** | **0.78** / 9.4% / −18.1% | MA 200, 252, 20 (= live) | 0.78 / 9.4% / −18.1% | 1.15–1.30 |
| IS 2014-06→2021-12, OOS 2022-01→2026-09 | 1.21 (3/18) | 0.73 / 7.5% / −17.5% | MA 160, 252, 20 | 0.87 / 8.9% / −17.5% | 1.05–1.28 |
| IS 2018-01→2023-12, OOS 2024-01→2026-09 | 0.75 (10/18) | 1.23 / 14.7% / −17.5% | MA 160, 252, 60 | 1.24 / 14.8% / −17.6% | 0.60–0.92 |

Readings:

- **The in-sample → out-of-sample decay is large and is a property of the period, not of the parameters.** The live parameters were the best of 18 over 2014–2019 (Sharpe 1.30) and delivered 0.78 over 2020–2026. Choosing the best-IS parameters instead changes OOS Sharpe by 0.00, +0.14 and +0.01 across the three folds. There is nothing to tune; the whole grid moves together (IS range 0.15–0.32 wide, and the grid's best and worst swap order between folds).
- **2020–2026 is the strategy's weak regime** (two fast crashes with V recoveries and one bear where the defensive sleeve failed). Anyone looking only at 2014–2019 would have over-estimated the Sharpe by 0.5. The full-sample 1.00 already contains both halves.
- **MA 160 is best-IS in two of three folds** and better OOS in one. That is the strongest in-sample support any parameter change gets in this report, and it is still one fold. Treat as "not contradicted", not "confirmed".
- Lookback 252 beats 126/189 in every fold in-sample; the 12-month choice is the one parameter the data do support.

### 4.2 Parameter sensitivity

See the ±20% table in §2.2 (`backtest_output/sensitivity.md`). Robust: lookbacks, vol window, crash threshold, min trade. Fragile: rebalance day (Sharpe 1.00–1.18) and MA length (0.94–1.09).

### 4.3 Stress periods in the data

| Period | Strategy | Static 80/15/5 | CSPX | Regime response |
|---|---|---|---|---|
| 2020-02-19 → 03-23 (COVID) | −17.0% (MaxDD −18.0%) | −19.1% | −26.1% | still "healthy" at 03-06 check; exit 04-07 |
| 2022-01-03 → 10-14 (rate-hike bear) | −14.5% | −7.0% | −9.0% | exit 03-07 after −12% VEUR round trip; gilts sleeve lost |
| 2022-09-22 → 10-14 (gilt crisis) | −2.5% | −3.1% | −4.1% | 25% VWRL / 10% IGLS / 65% cash |
| 2025-02-19 → 04-08 (tariffs) | −16.3% (MaxDD −17.1%) | −11.5% | −16.2% | exit on the low, 04-07 |
| 2008 | not in live data | | | see §5.3 proxy |

### 4.4 Bootstrap confidence on Sharpe

Stationary block bootstrap of the 146 monthly returns (mean block 6 months, 5,000 draws), and i.i.d. resampling of the 36 closed round trips (`backtest_output/bootstrap.md`):

| | 5% | 25% | 50% | 75% | 95% | P(Sharpe < 0.5) |
|---|---|---|---|---|---|---|
| Baseline Sharpe | 0.73 | 0.99 | 1.17 | 1.36 | 1.63 | 0.7% |
| Baseline CAGR | 7.1% | 10.0% | 11.9% | 13.9% | 16.9% | |
| Baseline MaxDD | −23.2% | −17.1% | −14.7% | −13.6% | −7.9% | |
| Crash trigger on | 0.69 | 0.96 | 1.15 | 1.34 | 1.62 | 1.1% |
| 10 bps costs | 0.69 | 0.95 | 1.13 | 1.32 | 1.59 | 1.1% |
| Combo GBP+hyst+blend+10 bps | 0.55 | 0.82 | 0.99 | 1.18 | 1.45 | 3.3% |
| Round-trip mean return (36 trades) | 1.7% | | 3.9% | | 6.3% | P(<0) 0.3% |
| Round-trip profit factor | 1.8 | | 3.8 | | 9.5 | |

The strategy is very likely positive-Sharpe (it holds equities for 94% of the time in a decade when equities went up), but the width of the band (0.9 Sharpe points) is six times larger than any improvement measured in §3. The bootstrap median (1.17) sits above the point estimate (1.00) because the realised path had its two worst months adjacent (Feb–Mar 2020, Mar–Apr 2025).

---

## 5. Prioritised output

### 5.1 Top vulnerabilities by real-money severity

1. **Portfolio value from free cash** (`jobs.py:247`). Second live rebalance liquidates ~98% of the book. Evidence: code path; the backtest could only run at all by substituting total equity. Fix: `account_value = account_cash["total_cash"]` is *also* wrong (T212 `total` is cash total); compute `free_cash + Σ position current_value` from `get_current_positions()`.
2. **Sell-then-never-buy in the two-phase executor** (`rebalance_executor.py:482`, `jobs.py:439`). Every regime flip (12 in 12 years, plus 5 more if the crash trigger is wired) risks a month in cash. Evidence: `rebalance_10th_td` vs `5th` = 0.13 Sharpe from a 3-day shift; a month uninvested is far larger. Fix: after Phase 1, poll `get_pending_orders()` for up to N minutes and run Phase 2 in the same job; persist a "phase 2 pending" flag in Supabase that the daily 09:00 run checks *before* the 5th-trading-day gate.
3. **Timing fragility + 5-hour approval window.** Sharpe moves 0.12 for one day of fill timing and 0.18 for the day-of-month choice; the two worst episodes (2020, 2025) each cost ~10 pp from a few days' delay. A human approval that can slide into the afternoon, or to the next day, is a material cost, not a safety feature. Fix: pre-approve rule-based rebalances (approve the *rules* quarterly, not each trade), keep the human check for anomalies (order count, size, ticker outside the universe) and for the regime-flip months only.
4. **Silent failure modes**: yfinance `.info` call per ticker, `<252` rows, any exception → `print` and `return`; `send_message()` result ignored; GitHub cron skipped; Railway + Actions double-run; bad prints unguarded. Evidence: EQQQ/SGLN 2010–14 bad prints exist in yfinance today; CSPX.L is missing yesterday's bar. Fix: a data-sanity gate (row count ≥ 260, last bar date = previous trading day, max |daily return| < 15%, currency == expected) that *sends a Telegram alert and aborts*; assert `send_message()` returns True; delete `main.py`/`railway.json` or the workflows, not both; a heartbeat that checks the last `signal_snapshots` row age.
5. **Circuit breaker and crash trigger are inert** and, per §3.3, should stay inert in their current form. The real risk is that the operator believes the "Exposure is being reduced automatically" message. Fix: change the message to "NO ACTION TAKEN — manual review", fix `check_drawdown` units, and start writing `portfolio_value_history` on every job so a real drawdown control can be built later.
6. **Sign error in the defensive branch** (`allocator.py:185`) and **USD-denominated CSPX signals** (`price_fetcher.py:83`). Neither cost money in-sample; both are wrong. Fix: `top_defensive[0]`; fetch CSP1.L (GBX line, already what T212 trades) instead of CSPX.L, or convert with the historical daily rate.

### 5.2 Highest-leverage Sharpe changes to try first

| Change | Backtested Δ Sharpe | Δ CAGR | Δ MaxDD | Turnover | Cost / risk |
|---|---|---|---|---|---|
| Equal-weight the growth sleeve (delete inverse-vol) | +0.01 | +0.2 pp | −0.1 pp | −21% orders | none; removes a parameter |
| 12-1 momentum (skip most recent month) | +0.06 | +0.8 pp | 0 | 0 | none; standard, parameter-free |
| Hysteresis ±3% on the 200-MA | +0.02 | −0.4 pp | +0.5 pp | −3% | fewer flips; slower exits |
| MA 160 instead of 200 | +0.09 | +0.5 pp | +0.5 pp | +13% | **do not adopt on this evidence** — monotonic in-sample effect with no OOS support (§4.1); it is the kind of change the walk-forward exists to reject |
| Hold all four growth ETFs, ERC | 0.00 | −1.2 pp | −1.3 pp | **−54%** | best if realised costs ≥ 20 bps; 77% RT win rate |
| Rebalance on 1st trading day (revert `40a7c7e`) | +0.17 | +1.5 pp | +2.5 pp | +32% | two-episode luck (2020-04-01, 2025-04-01); do not chase, but note the 5th was not chosen on evidence either |
| Crash trigger (as designed) | +0.02 / −0.02 after costs | −0.3 pp | −0.2 pp | +12% | **do not wire up** |
| Vol target 10% | 0.00 | −1.7 pp | +1.5 pp | 0 | pure de-leveraging; a smaller position does the same |

Expected combined effect of the first three: roughly +0.05 to +0.10 Sharpe, mostly via fewer bad trades, well inside the noise band — the point is operational simplicity, not alpha.

### 5.3 Extended history (2006 →) with US-listed proxies

To reach 2008, the same live logic (via the same functions, same 5th-trading-day calendar, same USD-signal quirk) was replayed on US-listed proxies converted to GBP with the daily GBPUSD rate: SPY, QQQ, VGK, VT (spliced backwards with 0.55 SPY + 0.45 EFA returns before 2008-06), GLD, and a short-gilt chain IGLS.L ← IGLT.L ← SHY. Window 2006-04-03 → 2026-09-04 (20.4 years, 746 orders). Instruments, closes (US 21:00 UTC) and spreads differ from the LSE lines, so the numbers are indicative of the *rules*, not of the exact live book (`backtest_output/proxy_summary.md`, `proxy_periods.md`, `proxy_equity.png`).

| Variant (proxy, 2006→2026) | CAGR | Vol | Sharpe | MaxDD | Calmar | Turnover | Crash exits |
|---|---|---|---|---|---|---|---|
| Live logic | 11.3% | 13.6% | **0.84** | −21.4% | 0.53 | 430% | 0 |
| + 10 bps costs | 10.9% | 13.6% | 0.81 | −22.1% | 0.49 | 430% | 0 |
| Crash trigger on | 11.1% | 13.6% | 0.83 | −23.5% | 0.47 | 469% | 15 |
| GBP signals + hysteresis | 9.6% | 14.2% | 0.70 | −25.1% | 0.38 | 377% | 0 |
| GBP + hysteresis + blended momentum | 9.3% | 14.2% | 0.69 | −25.5% | 0.36 | 470% | 0 |
| … + 10 bps | 8.7% | 14.2% | 0.65 | −25.9% | 0.34 | 471% | 0 |

Bootstrap on the 244 proxy months: Sharpe 90% band [0.78, 1.42], median 1.11 (point estimate 0.84 — again the realised sequence of bad months is worse than a reshuffled one).

Stress periods only the proxy covers:

| Period | Strategy | Static 80/15/5 | S&P (SPY, GBP) | All-world (VT, GBP) | Gold |
|---|---|---|---|---|---|
| 2007-10-09 → 2009-03-09 (GFC bear) | **+16.4%** (MaxDD −6.8%) | −24.1% | −33.8% | −38.4% | +83.1% |
| Calendar 2008 | **+12.5%** | −10.3% | −13.8% | −19.3% | +37.9% |
| 2009-03-09 → 12-31 (recovery) | +15.5% (MaxDD −10.9%) | +40.7% | +42.7% | +52.2% | +1.0% |
| 2011-07 → 10 (euro crisis) | −5.8% | −12.0% | −12.3% | −17.2% | +13.1% |
| 2015-05 → 2016-06 (chop) | +6.8% (MaxDD −12.8%) | +10.2% | +16.4% | +7.0% | +27.5% |
| 2022-01-03 → 10-14 (bear) | **−19.4%** | −8.3% | −9.4% | −11.6% | +8.7% |
| 2006-04 → 2014-05 (pre live-data) | +121% | +69% | +85% | +57% | +112% |

Readings:

- **The regime filter earns its keep in a slow bear.** 2008 is the regime that the live-data window lacks: the 200-MA exit in late 2007 and the 50% gold / 25% gilt allocation turned a −24% static-mix drawdown into +16%. Gold did most of the work (its 3-month momentum kept it as top defensive), and the 2009 V recovery was again mostly missed (15.5% vs 40.7%). That is the whole thesis of the strategy in one line: it trades away V-recovery upside to buy slow-bear protection. Over the full 20 years that trade paid: strategy Sharpe 0.84 / MaxDD −21.4% versus the static 80/15/5 mix at 0.63 / −28.7% (CSPX proxy buy-and-hold 0.68 / −35.0%). All of that excess comes from 2006→2014 (+121% vs +69%); over the 2014→2026 overlap the proxy strategy and the static mix are level, as in the live-data replay.
- **2022-type bears (equities and gilts down together, gold flat) are the failure mode**, in both datasets: −19.4% proxy, −14.5% live vs −8.3% / −7.0% for the static mix. The "defensive" sleeve has no allocation that works when rates rise; the 65%-cash branch only engages if *both* defensives have negative 3-month momentum, which gold rarely shows.
- **The crash trigger is again negative** (15 exits, −0.01 Sharpe, MaxDD 2 pp worse), and the "sensible" GBP/hysteresis/blend combinations are again worse (−0.14 to −0.19). Same direction as the live-data window, which is the best robustness check available here.

### 5.4 Go / no-go

**No-go for live capital now.** Not because the signals are bad — they are ordinary trend-plus-momentum rules with a Sharpe indistinguishable from the static mix they are built on — but because:

- the execution path has a first-rebalance-only bug (free cash) and a sell-without-buy path;
- the risk controls the design relies on either cannot fire or, when made to fire, cost money in the backtest;
- there is no data-quality gate in front of a pipeline whose signal flips on a single bad print;
- the operational timing (cron drift, 5-hour approval, next-month retry) is exactly the dimension the backtest shows the result is most sensitive to.

**What "go" would look like:** the six fixes in §5.1 done; the three changes in §5.2 (equal-weight, 12-1, hysteresis) adopted or consciously rejected; one full demo-account cycle including a forced regime flip (sell → buy) executed end-to-end without manual intervention; a written rule for what the human check is *for*. Then the honest expectation is Sharpe ≈ 1.0 ± 0.4, CAGR 10–12%, drawdowns of 15–20% every few years, with turnover around 250–300%/yr — i.e. a static 80/15/5 portfolio with extra steps, plus the option value of the regime filter in a slow bear — which, on the 2006→2026 proxy evidence, is worth about +0.2 Sharpe and 7 pp of drawdown over a 20-year window that includes one.

### 5.5 Summary table of every variant run

Baseline = live logic, 2014-06-24 → 2026-09-04, £20k, no costs, signal on prior close, fill at next close. `dSharpe` is vs baseline. Full metrics (Sortino, Calmar, exposure, costs, round-trip stats, event counts) in `backtest_output/summary.csv`; each variant has `_equity.csv`, `_orders.csv`, `_roundtrips.csv`, `_signals.csv`.

| variant | cagr | ann_vol | sharpe | dSharpe | sortino | max_dd | calmar | monthly_win_rate | turnover_ann | orders | rt_win_rate | rt_profit_factor |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 11.9% | 11.9% | 1.0 | 0.0 | 1.44 | -18.0% | 0.66 | 64.6% | 4.0 | 415 | 61.1% | 5.22 |
| fill_same_close_LOOKAHEAD | 13.0% | 11.3% | 1.13 | 0.12 | 1.64 | -16.3% | 0.8 | 64.0% | 3.8 | 408 | 62.9% | 7.32 |
| fill_next_open | 12.0% | 11.8% | 1.01 | 0.01 | 1.46 | -18.1% | 0.66 | 64.0% | 4.0 | 409 | 61.1% | 5.69 |
| signals_in_gbp | 11.0% | 11.5% | 0.97 | -0.04 | 1.38 | -20.6% | 0.54 | 61.2% | 4.2 | 427 | 63.9% | 4.31 |
| rebalance_1st_td | 13.4% | 11.2% | 1.18 | 0.17 | 1.72 | -15.5% | 0.87 | 67.4% | 5.2 | 439 | 65.0% | 6.61 |
| rebalance_10th_td | 13.3% | 11.5% | 1.13 | 0.13 | 1.63 | -18.1% | 0.73 | 66.0% | 4.1 | 426 | 73.5% | 6.86 |
| cost_10bps | 11.5% | 11.9% | 0.97 | -0.03 | 1.39 | -18.1% | 0.64 | 64.6% | 3.9 | 413 | 58.3% | 4.86 |
| cost_25bps | 10.8% | 11.9% | 0.92 | -0.08 | 1.32 | -18.1% | 0.6 | 63.3% | 3.9 | 409 | 58.3% | 4.38 |
| crash_trigger_on | 11.6% | 11.4% | 1.02 | 0.02 | 1.47 | -18.2% | 0.64 | 64.0% | 4.4 | 433 | 55.6% | 4.54 |
| crash_trigger_on_10bps | 11.1% | 11.4% | 0.98 | -0.02 | 1.41 | -18.9% | 0.59 | 64.0% | 4.4 | 430 | 55.6% | 4.21 |
| circuit_breaker_on | 11.6% | 11.6% | 1.0 | -0.0 | 1.43 | -18.0% | 0.64 | 65.3% | 4.0 | 418 | 63.2% | 4.65 |
| crash_and_cb_on | 11.6% | 11.4% | 1.02 | 0.02 | 1.48 | -18.2% | 0.64 | 64.6% | 4.5 | 435 | 56.5% | 4.55 |
| crash_thr_-5.6pct | 11.2% | 11.2% | 1.0 | -0.01 | 1.44 | -17.7% | 0.64 | 64.0% | 4.8 | 449 | 58.5% | 4.23 |
| crash_thr_-8.4pct | 11.6% | 11.4% | 1.02 | 0.02 | 1.47 | -18.2% | 0.64 | 64.0% | 4.4 | 433 | 55.6% | 4.54 |
| crash_thr_-10pct | 11.7% | 11.5% | 1.01 | 0.01 | 1.45 | -18.7% | 0.62 | 64.0% | 4.3 | 426 | 61.0% | 4.31 |
| fix_least_negative | 11.9% | 11.9% | 1.01 | 0.0 | 1.44 | -18.0% | 0.66 | 64.6% | 4.0 | 415 | 58.3% | 5.17 |
| no_growth_when_unhealthy | 11.5% | 11.8% | 0.97 | -0.03 | 1.39 | -18.0% | 0.64 | 64.0% | 3.9 | 398 | 66.7% | 4.85 |
| mom_6m | 11.2% | 11.6% | 0.97 | -0.04 | 1.38 | -18.0% | 0.62 | 64.0% | 5.9 | 447 | 65.1% | 3.6 |
| mom_blend_3_6_12 | 12.0% | 11.8% | 1.01 | 0.01 | 1.46 | -18.0% | 0.66 | 64.0% | 4.4 | 425 | 69.0% | 4.13 |
| mom_12-1_skip_month | 12.7% | 11.9% | 1.06 | 0.06 | 1.52 | -18.0% | 0.7 | 64.0% | 4.0 | 421 | 69.2% | 7.99 |
| mom_lookback_202 | 11.7% | 11.7% | 1.0 | -0.01 | 1.43 | -18.0% | 0.65 | 64.6% | 4.9 | 431 | 68.1% | 4.76 |
| mom_lookback_302 | 12.0% | 12.0% | 1.01 | 0.0 | 1.44 | -18.0% | 0.67 | 62.5% | 4.5 | 412 | 65.8% | 10.25 |
| vol_window_16 | 11.9% | 11.9% | 1.01 | 0.0 | 1.44 | -18.0% | 0.66 | 65.3% | 4.1 | 417 | 61.1% | 5.25 |
| vol_window_24 | 12.0% | 11.9% | 1.01 | 0.0 | 1.44 | -18.1% | 0.66 | 64.6% | 3.9 | 417 | 61.1% | 5.31 |
| vol_window_60 | 12.0% | 11.9% | 1.01 | 0.0 | 1.44 | -18.2% | 0.66 | 65.3% | 3.7 | 404 | 61.1% | 5.3 |
| vol_ewma_30 | 11.9% | 11.9% | 1.01 | 0.0 | 1.44 | -18.1% | 0.66 | 65.3% | 3.8 | 412 | 61.1% | 5.27 |
| vol_target_10pct | 10.2% | 10.3% | 1.0 | -0.01 | 1.43 | -16.5% | 0.62 | 64.6% | 3.9 | 430 | 61.1% | 5.16 |
| vol_target_12pct | 10.9% | 11.1% | 0.99 | -0.01 | 1.42 | -17.5% | 0.62 | 64.0% | 3.9 | 424 | 61.1% | 5.07 |
| vol_target_15pct | 11.5% | 11.6% | 0.99 | -0.01 | 1.42 | -17.5% | 0.66 | 64.0% | 3.9 | 418 | 61.1% | 5.12 |
| weight_equal | 12.1% | 12.0% | 1.01 | 0.0 | 1.44 | -18.1% | 0.67 | 62.6% | 3.3 | 328 | 61.1% | 5.88 |
| top3_inverse_vol | 11.4% | 11.0% | 1.03 | 0.03 | 1.48 | -18.3% | 0.62 | 64.6% | 3.9 | 527 | 68.1% | 5.55 |
| top3_erc | 11.3% | 11.0% | 1.02 | 0.02 | 1.46 | -18.4% | 0.62 | 64.6% | 3.5 | 490 | 68.1% | 5.43 |
| top4_erc_no_selection | 10.7% | 10.6% | 1.0 | -0.0 | 1.43 | -19.3% | 0.55 | 62.6% | 1.8 | 513 | 77.3% | 25.27 |
| ma_160 | 12.4% | 11.2% | 1.09 | 0.09 | 1.58 | -17.5% | 0.71 | 66.0% | 4.5 | 422 | 65.8% | 6.87 |
| ma_240 | 11.1% | 12.0% | 0.94 | -0.07 | 1.33 | -20.8% | 0.54 | 63.3% | 4.1 | 413 | 56.8% | 4.27 |
| regime_hysteresis_2pct | 11.6% | 11.3% | 1.02 | 0.01 | 1.47 | -17.5% | 0.66 | 64.6% | 4.1 | 411 | 59.5% | 5.08 |
| regime_hysteresis_3pct | 11.6% | 11.2% | 1.03 | 0.02 | 1.48 | -17.5% | 0.66 | 65.3% | 4.0 | 405 | 60.0% | 5.18 |
| regime_dual_ma_and_12m | 11.8% | 11.8% | 1.0 | -0.0 | 1.44 | -18.0% | 0.66 | 63.3% | 4.0 | 415 | 62.9% | 5.46 |
| regime_per_asset_trend | 11.7% | 11.8% | 0.99 | -0.01 | 1.42 | -18.0% | 0.65 | 64.0% | 4.4 | 414 | 69.6% | 6.07 |
| regime_gbp_hysteresis | 11.1% | 11.8% | 0.95 | -0.05 | 1.36 | -17.6% | 0.63 | 61.2% | 4.2 | 417 | 62.5% | 5.03 |
| event_driven_regime_flip | 12.4% | 11.1% | 1.1 | 0.1 | 1.59 | -19.8% | 0.63 | 64.0% | 6.6 | 523 | 55.1% | 5.8 |
| event_driven_10bps | 11.6% | 11.1% | 1.04 | 0.04 | 1.5 | -20.5% | 0.57 | 64.0% | 6.6 | 513 | 55.1% | 5.05 |
| combo_gbp_hyst_blend | 10.3% | 11.7% | 0.89 | -0.11 | 1.27 | -17.0% | 0.61 | 61.9% | 4.8 | 423 | 67.4% | 3.44 |
| combo_gbp_hyst_blend_vt12 | 9.8% | 11.1% | 0.89 | -0.11 | 1.27 | -17.0% | 0.58 | 60.5% | 4.6 | 430 | 69.8% | 3.56 |
| combo_gbp_hyst_blend_10bps | 9.8% | 11.8% | 0.85 | -0.15 | 1.21 | -17.2% | 0.57 | 59.9% | 4.8 | 424 | 67.4% | 3.22 |
| combo_gbp_hyst_blend_fixbug_10bps | 9.8% | 11.8% | 0.85 | -0.16 | 1.2 | -17.2% | 0.57 | 59.9% | 4.8 | 424 | 69.8% | 3.55 |
| min_trade_50 | 11.9% | 11.9% | 1.01 | 0.0 | 1.44 | -18.0% | 0.66 | 64.6% | 4.0 | 491 | 61.1% | 5.23 |
| capital_5000 | 12.0% | 11.9% | 1.01 | 0.0 | 1.45 | -18.1% | 0.66 | 64.6% | 3.9 | 281 | 61.1% | 5.21 |

Benchmarks over the same window:

| Benchmark | CAGR | Vol | Sharpe | Sortino | MaxDD | Calmar |
|---|---|---|---|---|---|---|
| CSPX.L buy & hold | 15.7% | 17.6% | 0.91 | 1.33 | −26.1% | 0.60 |
| VWRL.L buy & hold | 12.4% | 14.4% | 0.88 | 1.25 | −25.0% | 0.50 |
| SGLN.L buy & hold | 12.4% | 15.9% | 0.81 | 1.21 | −24.9% | 0.50 |
| IGLS.L buy & hold | 1.2% | 2.1% | 0.58 | 0.85 | −9.5% | 0.13 |
| Static 80/15/5 (VWRL/SGLN/IGLS), monthly | 12.1% | 11.8% | 1.02 | 1.47 | −19.1% | 0.63 |
| Equal-weight 4 growth ETFs, monthly | 14.4% | 14.9% | 0.97 | 1.39 | −24.5% | 0.59 |

---

## Appendix A — Files produced

- [backtest.py](backtest.py) — engine, variants, analyses (new file; nothing under `src/` touched).
- `backtest_output/summary.csv|md` — all variants. `sensitivity.md`, `walkforward.md`, `bootstrap.md`, `periods.md`, `by_regime.csv`, `benchmarks.csv`.
- `backtest_output/baseline_equity.png` — equity and drawdown vs benchmarks; `proxy_equity.png` for 2006→.
- `backtest_output/<variant>_orders.csv` — every simulated order: date, signal date, ticker, side, £, price, units, cost, realised P&L, reason (monthly / crash / regime_flip), regime.
- `backtest_output/<variant>_roundtrips.csv` — entry → flat per ticker with P&L and holding days.
- `backtest_output/<variant>_signals.csv` — what the live functions returned at every weekly and monthly check (regime, fast-crash flag, drawdown, rankings, selection).
- `backtest_output/cache/` — raw yfinance downloads (re-fetch with `--refresh`).

## Appendix B — Things I could not verify and did not assume

- Whether the Railway service is still deployed alongside the GitHub Actions workflows (double-run risk) — check the Railway dashboard.
- T212's current ISA fractional-share terms and the HMRC regulation status (§2.6).
- T212 realised spreads at 09:00 UTC for these lines — 10 bps and 25 bps are assumptions bracketing typical values; measure from `executed_orders` once demo fills exist.
- Whether T212 makes unsettled sale proceeds available for immediate reinvestment in the ISA (this decides whether the two-phase executor's wait is ever needed).
