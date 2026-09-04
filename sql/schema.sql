-- Supabase schema used by src/store.py.
-- signal_snapshots: check_type = 'monthly' | 'weekly'; regime = 'risk_on' | 'risk_off';
--   growth_rankings JSONB holds the full signal set (momentum, sma_ratio, trend_on, fallback);
--   defensive_rankings JSONB holds {"target_weights": {...}}; selected_growth = held equity keys.
-- trade_decisions.user_response: 'AUTO' (executed), 'DRY_RUN', 'NO_TRADES' (no approval step exists).
-- Columns cspx_price/cspx_200ma/fast_crash_triggered/ten_day_return/circuit_breaker_triggered are legacy (always null/false).

-- Signal snapshots: capture regime, prices, signals, rankings at each check
CREATE TABLE signal_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    environment TEXT NOT NULL,
    check_type TEXT NOT NULL, -- 'weekly' or 'monthly'
    regime TEXT NOT NULL, -- 'healthy' or 'unhealthy'
    cspx_price NUMERIC,
    cspx_200ma NUMERIC,
    fast_crash_triggered BOOLEAN NOT NULL DEFAULT FALSE,
    ten_day_return NUMERIC,
    portfolio_drawdown_pct NUMERIC,
    circuit_breaker_triggered BOOLEAN NOT NULL DEFAULT FALSE,
    growth_rankings JSONB, -- [{"ticker": "EQQQ.L", "momentum": 0.435}, ...]
    defensive_rankings JSONB, -- [{"ticker": "IGLS.L", "momentum": 0.0127}, ...]
    selected_growth TEXT[] -- ["EQQQ.L", "VWRL.L"]
);

CREATE INDEX idx_signal_snapshots_created_at ON signal_snapshots(created_at DESC);
CREATE INDEX idx_signal_snapshots_environment ON signal_snapshots(environment);
CREATE INDEX idx_signal_snapshots_check_type ON signal_snapshots(check_type);

-- Trade decisions: log proposed trades and user response
CREATE TABLE trade_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    signal_snapshot_id UUID NOT NULL REFERENCES signal_snapshots(id) ON DELETE CASCADE,
    environment TEXT NOT NULL,
    trade_list JSONB NOT NULL, -- [{"ticker": "EQQQ.L", "action": "BUY", "amount_gbp": 1466.94}, ...]
    total_buy_amount NUMERIC NOT NULL DEFAULT 0,
    total_sell_amount NUMERIC NOT NULL DEFAULT 0,
    telegram_message_sent TEXT, -- the full message text sent
    user_response TEXT, -- 'YES', 'NO', 'TIMEOUT', or NULL
    responded_at TIMESTAMPTZ -- when user replied
);

CREATE INDEX idx_trade_decisions_created_at ON trade_decisions(created_at DESC);
CREATE INDEX idx_trade_decisions_environment ON trade_decisions(environment);
CREATE INDEX idx_trade_decisions_signal_snapshot_id ON trade_decisions(signal_snapshot_id);
CREATE INDEX idx_trade_decisions_user_response ON trade_decisions(user_response);

-- Executed orders: log what was actually submitted to T212
CREATE TABLE executed_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    trade_decision_id UUID NOT NULL REFERENCES trade_decisions(id) ON DELETE CASCADE,
    environment TEXT NOT NULL,
    ticker TEXT NOT NULL,
    action TEXT NOT NULL, -- 'BUY' or 'SELL'
    amount_gbp NUMERIC NOT NULL,
    t212_order_id TEXT, -- T212's order ID if available
    status TEXT NOT NULL DEFAULT 'submitted', -- 'submitted', 'filled', 'failed', 'rejected'
    error_message TEXT
);

CREATE INDEX idx_executed_orders_created_at ON executed_orders(created_at DESC);
CREATE INDEX idx_executed_orders_environment ON executed_orders(environment);
CREATE INDEX idx_executed_orders_trade_decision_id ON executed_orders(trade_decision_id);
CREATE INDEX idx_executed_orders_status ON executed_orders(status);

-- Portfolio value history: track total value at each check
CREATE TABLE portfolio_value_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    environment TEXT NOT NULL,
    total_value_gbp NUMERIC NOT NULL,
    cash_gbp NUMERIC NOT NULL,
    invested_gbp NUMERIC NOT NULL
);

CREATE INDEX idx_portfolio_value_history_created_at ON portfolio_value_history(created_at DESC);
CREATE INDEX idx_portfolio_value_history_environment ON portfolio_value_history(environment);

-- Heartbeat: simple monitoring to detect if the bot is running
CREATE TABLE heartbeat (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'alive'
);

CREATE INDEX idx_heartbeat_created_at ON heartbeat(created_at DESC);
