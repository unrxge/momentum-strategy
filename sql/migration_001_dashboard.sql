-- Migration 001 — instrumentation for the dashboard.
-- Run once in the Supabase SQL editor.  Safe to re-run.
--
-- Adds (a) fill detail on executed_orders so slippage and real cost drag are measurable,
--      (b) a benchmark close series so live return can be compared like for like,
--      (c) a cashflow log so deposits/withdrawals do not read as strategy return.

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
