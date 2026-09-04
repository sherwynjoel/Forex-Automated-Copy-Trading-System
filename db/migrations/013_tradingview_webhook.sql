-- TradingView alerts placing the master's orders.
--
-- One webhook per org. TradingView signs nothing, so the secret the operator
-- pastes into the alert message IS the authentication; it is stored as a
-- sha256 (the same convention as org_invites.token_hash) because nothing
-- ever needs the plaintext back, and returned to the operator exactly once.
-- The URL carries an opaque random hook_id -- a mailbox address, not a
-- secret -- so a leaked URL lets an outsider write rejected rows into that
-- org's log and nothing more, while a stale secret in the operator's own
-- template still lands somewhere they can see it.
--
-- webhook_receipts is the duplicate guard as well as the audit surface. A
-- fingerprint of the alert's trading content, scoped to the org, blocks a
-- second identical alert inside a short window; TradingView resends on
-- non-2xx, two indicators on one chart can fire the same message in the
-- same second, and a double-trade is the failure this whole table exists to
-- prevent. Rows are what the Automation page shows the operator.

CREATE TABLE org_webhooks (
    org_id BIGINT PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
    -- Opaque routing id in the URL. Random, never derived from org_id.
    hook_id TEXT NOT NULL UNIQUE,
    -- sha256 hex of the secret; NULL until an admin generates one.
    secret_hash TEXT,
    secret_created_at TIMESTAMPTZ,
    -- Off by default. Enabling requires a secret and a master account.
    enabled BOOLEAN NOT NULL DEFAULT false,
    -- Per-alert size cap, in lots. Applied under the code's hard ceiling.
    max_lots NUMERIC(10, 4) NOT NULL DEFAULT 0.1 CHECK (max_lots > 0),
    -- Alerts that actually placed an order or close, per rolling minute.
    max_per_minute INTEGER NOT NULL DEFAULT 10 CHECK (max_per_minute BETWEEN 1 AND 60),
    -- Refuse to open when the master already holds this many positions
    -- opened by the webhook. Bounds what a leaked secret can do.
    max_open_positions INTEGER NOT NULL DEFAULT 3 CHECK (max_open_positions BETWEEN 1 AND 50),
    -- TradingView ticker -> broker symbol, for the cases stripping the
    -- exchange prefix is not enough (e.g. "XAUUSD" -> "GOLD").
    symbol_aliases JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE webhook_receipts (
    id BIGSERIAL PRIMARY KEY,
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- accepted | duplicate | unknown | rejected | failed | nothing_to_close
    outcome TEXT NOT NULL,
    reason TEXT,
    action TEXT,
    symbol TEXT,
    lots NUMERIC(10, 4),
    -- sha256 of the alert's TRADING content (org, action, symbol, lots,
    -- sl, tp) -- deliberately not the raw bytes, so a resend that
    -- re-rendered {{timenow}} still matches. NULL when the alert never
    -- authenticated: an outsider's bytes must not occupy a slot.
    fingerprint TEXT,
    alert_id TEXT,
    source_ip TEXT,
    latency_ms INTEGER,
    -- The body with every secret-shaped value scrubbed. NULL on a secret
    -- mismatch: a typo'd key ("Secret", "token") would otherwise write the
    -- real secret to disk in clear on every retry.
    body_redacted JSONB
);

-- The duplicate check: newest accepted rows for this fingerprint.
CREATE INDEX webhook_receipts_dedup
    ON webhook_receipts (org_id, fingerprint, received_at DESC)
    WHERE fingerprint IS NOT NULL AND outcome IN ('accepted', 'unknown');

-- The per-minute cap and the Automation page's recent list.
CREATE INDEX webhook_receipts_by_org_time
    ON webhook_receipts (org_id, received_at DESC);
