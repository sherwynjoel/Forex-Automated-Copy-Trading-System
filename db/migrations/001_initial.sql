CREATE TABLE ctid_connections (
    id BIGSERIAL PRIMARY KEY,
    access_token_enc TEXT NOT NULL,
    refresh_token_enc TEXT NOT NULL,
    granted_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    scope TEXT NOT NULL DEFAULT 'trading',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'invalid', 'refresh_failed'))
);

CREATE TABLE accounts (
    ctid_trader_account_id BIGINT PRIMARY KEY,
    ctid_connection_id BIGINT NOT NULL REFERENCES ctid_connections(id) ON DELETE CASCADE,
    trader_login BIGINT NOT NULL,
    is_live BOOLEAN NOT NULL,
    role TEXT NOT NULL DEFAULT 'ignored' CHECK (role IN ('master', 'slave', 'ignored')),
    enabled BOOLEAN NOT NULL DEFAULT true,
    multiplier NUMERIC(10, 4) NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'paused', 'degraded')),
    last_error TEXT
);
-- spec §7: exactly one master enforced
CREATE UNIQUE INDEX accounts_single_master ON accounts ((TRUE)) WHERE role = 'master';

CREATE TABLE symbol_cache (
    account_id BIGINT NOT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    symbol_id BIGINT NOT NULL,
    digits INT NOT NULL,
    lot_size BIGINT NOT NULL,
    min_volume BIGINT NOT NULL,
    step_volume BIGINT NOT NULL,
    PRIMARY KEY (account_id, name)
);

CREATE TABLE mappings (
    id BIGSERIAL PRIMARY KEY,
    master_position_id BIGINT,
    master_order_id BIGINT,
    slave_account_id BIGINT NOT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    slave_position_id BIGINT,
    slave_order_id BIGINT,
    slave_volume BIGINT,
    client_order_id TEXT UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'closed', 'failed')),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (master_position_id IS NOT NULL OR master_order_id IS NOT NULL)
);
CREATE INDEX mappings_by_master_position ON mappings (master_position_id)
    WHERE master_position_id IS NOT NULL;
CREATE INDEX mappings_by_master_order ON mappings (master_order_id)
    WHERE master_order_id IS NOT NULL;

CREATE TABLE events (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    account_id BIGINT,
    category TEXT NOT NULL
        CHECK (category IN ('master_event', 'slave_action', 'connection', 'auth', 'drift', 'control')),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    latency_ms INT,
    payload JSONB NOT NULL
);
CREATE INDEX events_by_ts ON events (ts DESC);
CREATE INDEX events_by_account ON events (account_id, ts DESC);

CREATE FUNCTION notify_event() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('events', NEW.id::text);
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER events_notify AFTER INSERT ON events
    FOR EACH ROW EXECUTE FUNCTION notify_event();

CREATE TABLE settings (
    id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    copying_enabled BOOLEAN NOT NULL DEFAULT true,
    dry_run BOOLEAN NOT NULL DEFAULT false,
    shards INT NOT NULL DEFAULT 1
);
INSERT INTO settings DEFAULT VALUES;

CREATE TABLE admin (
    id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    password_hash TEXT NOT NULL
);
