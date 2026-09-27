-- Migration 001 — instrumentation for the dashboard.
-- Run once in the Supabase SQL editor.  Safe to re-run.
--
-- Adds (a) fill detail on executed_orders so slippage and real cost drag are measurable,
--      (b) a benchmark close series so live return can be compared like for like,
--      (c) a cashflow log so deposits/withdrawals do not read as strategy return.
--
-- Also removes the inflated portfolio_value_history rows recorded between 2026-09-07 and
-- 2026-09-25.  During that period the heartbeat was calling broker.snapshot()["total"] which
-- includes the leveraged-trend bot's positions (both bots share the demo account).  Those rows
-- overstate the momentum bot's equity by ~£1,500 and must be dropped before the equity curve
-- makes sense.  The 2026-09-04 row (£5,000 before leveraged-trend had any positions) is kept
-- as the starting point.
DELETE FROM portfolio_value_history
WHERE environment = 'DEMO'
  AND created_at >= '2026-09-07';
-- After running this migration, deploy the jobs.py fix (uses universe-positions + free cash
-- instead of the full account total) and wait for the next heartbeat to log the correct value.

-- Per-instrument holdings at each snapshot: the value history recorded totals only, so the
-- dashboard had no way to show what was actually held between rebalances.
ALTER TABLE portfolio_value_history
    ADD COLUMN IF NOT EXISTS positions JSONB;

ALTER TABLE executed_orders
    ADD COLUMN IF NOT EXISTS intended_price_gbp NUMERIC,
    ADD COLUMN IF NOT EXISTS fill_price_gbp     NUMERIC,
    ADD COLUMN IF NOT EXISTS filled_quantity    NUMERIC,
    ADD COLUMN IF NOT EXISTS fill_value_gbp     NUMERIC,
    ADD COLUMN IF NOT EXISTS slippage_bps       NUMERIC,
    ADD COLUMN IF NOT EXISTS filled_at          TIMESTAMPTZ;

-- Benchmark close, logged on the same days as portfolio_value_history so the two series
-- align date-for-date.  ticker is a Yahoo symbol (default VWRL.L, GBP total-market).
CREATE TABLE IF NOT EXISTS benchmark_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    as_of DATE NOT NULL,
    ticker TEXT NOT NULL,
    close NUMERIC NOT NULL,
    UNIQUE (as_of, ticker)
);
CREATE INDEX IF NOT EXISTS idx_benchmark_history_as_of ON benchmark_history(as_of DESC);

-- Deposits and withdrawals, read from T212's transaction history.  external_id is the
-- broker's reference, so re-running a job cannot double-count a movement.
CREATE TABLE IF NOT EXISTS cashflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    environment TEXT NOT NULL,
    external_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ,
    kind TEXT NOT NULL,             -- DEPOSIT | WITHDRAWAL | OTHER
    amount_gbp NUMERIC NOT NULL,    -- signed: + in, − out
    raw JSONB,
    UNIQUE (environment, external_id)
);
CREATE INDEX IF NOT EXISTS idx_cashflows_occurred_at ON cashflows(occurred_at DESC);
