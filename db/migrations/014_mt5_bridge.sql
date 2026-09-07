-- MT5 bridge (docs/superpowers/specs/2026-09-07-mt5-bridge-design.md).
--
-- An MT5 account is an accounts row like any other -- same primary key
-- space, same role/enabled/multiplier/status columns -- so every existing
-- query keyed on ctid_trader_account_id works unchanged. What differs is
-- how it connects: there is no cTrader OAuth grant, so ctid_connection_id
-- becomes optional and the platform column says which door the account
-- uses. The CHECK keeps the two in step: a cTrader account always has a
-- link, an MT5 account never does. MT5 logins can collide with cTrader
-- ids across brokers, so MT5 accounts draw a synthetic id from a sequence
-- far above any cTrader id; the real login lives on mt5_links.

ALTER TABLE accounts ADD COLUMN platform TEXT NOT NULL DEFAULT 'ctrader'
    CHECK (platform IN ('ctrader', 'mt5'));
ALTER TABLE accounts ALTER COLUMN ctid_connection_id DROP NOT NULL;
ALTER TABLE accounts ADD CONSTRAINT accounts_platform_link
    CHECK ((platform = 'ctrader') = (ctid_connection_id IS NOT NULL));

CREATE SEQUENCE mt5_account_id_seq START 1000000000000;

-- One row per MT5 account: the key the terminal authenticates with
-- (sha256 only -- the api creates the row and the key, the copier only
-- ever updates the terminal-reported columns) and what the terminal has
-- said about itself.
CREATE TABLE mt5_links (
    account_id     BIGINT PRIMARY KEY REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    key_hash       TEXT NOT NULL UNIQUE,          -- sha256 hex of the key; the key itself is never stored
    key_created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    login          BIGINT,
    broker         TEXT,
    server         TEXT,
    currency       TEXT,
    hedging        BOOLEAN,
    trade_mode     TEXT,                          -- 'demo' | 'contest' | 'real'
    leverage       INTEGER,
    ea_version     TEXT,
    ea_build       INTEGER,
    last_seen_at   TIMESTAMPTZ,
    last_ip        TEXT,
    balance        DOUBLE PRECISION,
    equity         DOUBLE PRECISION,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The command outbox. The terminal polls; nothing can push to it, so
-- every open/close/amend/cancel the copier decides waits here (queued),
-- is handed over on the next poll (sent), and is settled by the
-- terminal's acknowledgement (done/failed). Postgres, not memory, so a
-- copier restart loses nothing that was decided.
CREATE TABLE mt5_commands (
    id              BIGSERIAL PRIMARY KEY,
    account_id      BIGINT NOT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    org_id          BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('open','close','amend','place_pending','amend_pending','cancel_pending')),
    payload         JSONB NOT NULL,
    client_order_id TEXT,
    status          TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','sent','done','failed')),
    result          JSONB,
    attempts        INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at         TIMESTAMPTZ,
    done_at         TIMESTAMPTZ
);
CREATE INDEX mt5_commands_open ON mt5_commands (account_id, status) WHERE status IN ('queued','sent');

-- canonical (what master events carry, e.g. XAUUSD) -> the MT5 broker's
-- name (e.g. XAUUSD.r), per account. 'auto' rows are the copier's guess
-- from the hello; 'manual' rows are the operator's and are never
-- overwritten by a guess.
CREATE TABLE symbol_aliases (
    account_id  BIGINT NOT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    canonical   TEXT NOT NULL,                    -- the name master events carry, e.g. XAUUSD
    broker_name TEXT NOT NULL,                    -- the MT5 broker's name, e.g. XAUUSD.r
    source      TEXT NOT NULL CHECK (source IN ('auto','manual')),
    PRIMARY KEY (account_id, canonical)
);

-- The last deal ticket ingested per account. The terminal reports deals
-- with ticket > watermark; a restarted EA resumes from the server's value
-- (hello response), so nothing is double-ingested and nothing is lost.
CREATE TABLE mt5_deal_watermark (
    account_id        BIGINT PRIMARY KEY REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    last_deal_ticket  BIGINT NOT NULL DEFAULT 0,
    last_deal_time_ms BIGINT NOT NULL DEFAULT 0,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
