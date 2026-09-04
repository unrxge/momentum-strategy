# momentum-strategy

Hands-free monthly ETF strategy for a UK Stocks & Shares ISA on Trading 212, run from GitHub Actions.

**Rules** (see [docs/STRATEGY.md](docs/STRATEGY.md)): trend-filtered relative momentum. Each month, hold the two strongest of four equity ETFs by 12-1 momentum, but only while each is above its 10-month moving average; 15% gold and 15% UK gilts, each also trend-filtered; switched-off capital goes half to cash, half to the strongest defensive asset. No leverage, no intra-month trading, no approvals.

**Instruments** — six GBP/GBX London lines (ISA-eligible, no FX fee on T212), signals from the US-listed twins of the same indices so nothing is currency-converted:

| Key | Traded (LSE) | T212 | Signal series |
|---|---|---|---|
| SP500 | CSP1.L iShares Core S&P 500 | `CSP1_EQ` | SPY |
| NDX | EQQQ.L Invesco Nasdaq-100 | `EQQQl_EQ` | QQQ |
| WORLD | VWRL.L Vanguard FTSE All-World | `VWRLl_EQ` | VT |
| EUROPE | VEUR.L Vanguard FTSE Dev. Europe | `VEURl_EQ` | VGK |
| GOLD | SGLN.L iShares Physical Gold | `SGLNl_EQ` | itself |
| GILTS | IGLT.L iShares Core UK Gilts | `IGLTl_EQ` | itself |

## Layout

```
src/            runtime package (what GitHub Actions runs)
  config.py       instruments, parameters, schedule
  strategy.py     signals → target weights → trades (pure functions)
  data.py         yfinance fetch + data-quality gate
  broker.py       Trading 212 client
  executor.py     sells → wait → buys
  jobs.py         rebalance / weekly / heartbeat entry points
  notify.py       Telegram        store.py  Supabase audit log
backtest/       replay engine using the same strategy functions (python -m backtest.run --all)
tests/          unit tests (python -m pytest -q)
tools/          verify_t212_instruments.py — check the universe against T212 metadata
docs/           STRATEGY.md (rules, evidence), ANALYSIS_v1.md (audit of the previous version)
sql/            Supabase schema
.github/workflows/  rebalance (weekdays 09:30 UTC, acts on the 1st trading day), weekly_status (Mon), heartbeat (daily)
```

## Running

```bash
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt
cp .env.example .env            # fill in T212 demo/live keys, Supabase, Telegram
python -m pytest -q             # unit tests
python -m src.jobs rebalance --dry-run --force   # full pipeline, no orders
python -m src.jobs weekly
python -m backtest.run --all    # backtests → backtest_output/
```

In GitHub, the same secrets are configured as repository secrets. `ENVIRONMENT=demo` selects the demo API; set it to `live` (with `T212_LIVE_*` secrets) to trade the real ISA.

## Telegram

- Monthly (first trading day): what the rules see, the trades placed, positions after.
- Monday: weekly status — positions, drawdown, what a rebalance would do today, next rebalance date.
- Any failure (data-quality gate, broker error, pending orders) is reported and the job stops without trading.
