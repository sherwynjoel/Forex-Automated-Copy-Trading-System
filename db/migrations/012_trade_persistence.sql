-- Full trade persistence. Until now MirrorFleet stored only the SLAVE half
-- of a copy: mappings carries each slave's fill price, but the master
-- trade's own economics were never written -- _handle_master_event logged
-- {execution_type, normalized} and nothing else. Open positions lived only
-- in memory, and closed-trade P&L was fetched live from the broker per
-- request. So disconnecting an account erased its history, the Performance
-- page could not outlive the broker's deal window, and realized copy
-- slippage was not computable from production at all.
--
-- events keeps its role as the OPERATOR audit log (control, auth,
-- connection, drift, risk, reminder). Trade facts get their own tables
-- rather than being crammed into a JSONB payload.
--
-- Every new org_id is nullable and carries NO foreign key, for the same
-- reason events.org_id has none (005_multi_org.sql): this history is a
-- record of what happened, and it must survive an account -- and its org --
-- being disconnected or deleted.

-- ---------- executions ----------
-- Every ProtoOAExecutionEvent from every account, master and slave, fully
-- decoded. Covers master trade details, broker-side order lifecycle, and
-- execution-time quotes in one table. `raw` keeps the full decoded protobuf
-- so nothing is lost to a field we did not anticipate needing.
CREATE TABLE executions (
    id BIGSERIAL,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    org_id BIGINT,
    account_id BIGINT NOT NULL,
    is_master BOOLEAN NOT NULL,
    execution_type TEXT NOT NULL,
    order_id BIGINT,
    position_id BIGINT,
    deal_id BIGINT,
    client_order_id TEXT,
    symbol_id BIGINT,
    symbol TEXT,
    side TEXT,
    order_type TEXT,
    order_status TEXT,
    volume BIGINT,
    filled_volume BIGINT,
    closed_volume BIGINT,
    execution_price DOUBLE PRECISION,
    limit_price DOUBLE PRECISION,
    stop_price DOUBLE PRECISION,
    stop_loss DOUBLE PRECISION,
    take_profit DOUBLE PRECISION,
    gross_profit NUMERIC(18, 2),
    swap NUMERIC(18, 2),
    commission NUMERIC(18, 2),
    balance_after NUMERIC(18, 2),
    execution_timestamp BIGINT,
    bid_at_exec DOUBLE PRECISION,
    ask_at_exec DOUBLE PRECISION,
    error_code TEXT,
    raw JSONB NOT NULL,
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

CREATE INDEX executions_by_account_ts ON executions (account_id, ts DESC);
CREATE INDEX executions_by_org_ts ON executions (org_id, ts DESC);
CREATE INDEX executions_by_position ON executions (position_id)
    WHERE position_id IS NOT NULL;

-- ---------- positions ----------
-- Event-driven position state, upserted from execution events AND from each
-- resync, so the Positions screen survives a restart instead of being blank
-- until the first broker reconcile.
CREATE TABLE positions (
    account_id BIGINT NOT NULL,
    position_id BIGINT NOT NULL,
    org_id BIGINT,
    symbol_id BIGINT,
    symbol TEXT,
    side TEXT,
    volume BIGINT NOT NULL,
    entry_price DOUBLE PRECISION,
    stop_loss DOUBLE PRECISION,
    take_profit DOUBLE PRECISION,
    label TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    opened_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    realized_pnl NUMERIC(18, 2),
    swap NUMERIC(18, 2),
    commission NUMERIC(18, 2),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, position_id)
);
CREATE INDEX positions_open_by_org ON positions (org_id) WHERE status = 'open';

-- ---------- deals ----------
-- Broker-truth closed-trade economics. Columns mirror queries._map_deal
-- exactly, so engine/analytics.compute_analytics runs over rows read from
-- here with NO change to its input shape. Keyed (account_id, deal_id) and
-- upserted, which is what makes the backfill re-runnable.
CREATE TABLE deals (
    account_id BIGINT NOT NULL,
    deal_id BIGINT NOT NULL,
    org_id BIGINT,
    order_id BIGINT,
    position_id BIGINT,
    symbol_id BIGINT,
    symbol TEXT,
    side TEXT,
    volume BIGINT,
    filled_volume BIGINT,
    execution_price DOUBLE PRECISION,
    status TEXT,
    commission NUMERIC(18, 2),
    create_timestamp BIGINT,
    execution_timestamp BIGINT NOT NULL,
    is_close BOOLEAN NOT NULL DEFAULT false,
    entry_price DOUBLE PRECISION,
    gross_profit NUMERIC(18, 2),
    swap NUMERIC(18, 2),
    balance_after NUMERIC(18, 2),
    closed_volume BIGINT,
    PRIMARY KEY (account_id, deal_id)
);
CREATE INDEX deals_by_account_exec ON deals (account_id, execution_timestamp DESC);
CREATE INDEX deals_by_org_exec ON deals (org_id, execution_timestamp DESC);

-- ---------- deal_backfill_state ----------
-- Watermark so the week-by-week broker walk never re-covers ground. The
-- broker caps DealList at one week and 500 rows per call, and the copier's
-- queued send path is 10 msg/s, so a naive re-walk would take days.
CREATE TABLE deal_backfill_state (
    account_id BIGINT PRIMARY KEY,
    backfilled_from_ms BIGINT,
    backfilled_to_ms BIGINT,
    exhausted BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- balance_samples ----------
-- Sampled balance/equity series (5-minute default), written by the existing
-- balance-refresh LoopingCall, which is already off the trade path.
-- portfolio_snapshots is deliberately untouched: Overview depends on its
-- daily-close semantics.
CREATE TABLE balance_samples (
    account_id BIGINT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    org_id BIGINT,
    balance NUMERIC(18, 2),
    equity NUMERIC(18, 2),
    margin_used NUMERIC(18, 2),
    unrealized_pnl NUMERIC(18, 2),
    PRIMARY KEY (account_id, ts)
) PARTITION BY RANGE (ts);

CREATE INDEX balance_samples_by_org_ts ON balance_samples (org_id, ts DESC);

-- ---------- partitions ----------
-- Twelve months created up front plus a DEFAULT safety net. The copier also
-- maintains three months ahead daily (main.ensure_partitions), so in
-- practice the DEFAULT never receives a row. It exists only so a missed
-- rollover degrades into "rows in the wrong partition" rather than
-- "inserts rejected".
--
-- NOTE for whoever extends this later: attaching a monthly partition whose
-- range overlaps rows already sitting in the DEFAULT partition requires
-- Postgres to scan and move them, and it takes a strong lock to do it. That
-- is why we create a year ahead rather than relying on the DEFAULT.
DO $$
DECLARE
    start_month DATE := date_trunc('month', now())::date;
    m INT;
    lo DATE;
    hi DATE;
BEGIN
    FOR m IN 0..11 LOOP
        lo := start_month + (m || ' months')::interval;
        hi := start_month + ((m + 1) || ' months')::interval;
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF executions FOR VALUES FROM (%L) TO (%L)',
            'executions_' || to_char(lo, 'YYYY_MM'), lo, hi);
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF balance_samples FOR VALUES FROM (%L) TO (%L)',
            'balance_samples_' || to_char(lo, 'YYYY_MM'), lo, hi);
    END LOOP;
END $$;

CREATE TABLE executions_default PARTITION OF executions DEFAULT;
CREATE TABLE balance_samples_default PARTITION OF balance_samples DEFAULT;

-- ---------- mappings ----------
-- The measurement gap from the 2026-08-22 slippage diagnosis: mappings
-- stored only the SLAVE fill price, so realized slippage and true copy
-- latency were not computable from production. With the master's fill price
-- and the broker's own execution timestamp alongside it, both become a
-- single query.
ALTER TABLE mappings ADD COLUMN master_fill_price DOUBLE PRECISION;
ALTER TABLE mappings ADD COLUMN master_exec_timestamp BIGINT;
