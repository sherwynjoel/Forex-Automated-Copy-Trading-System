-- VT HTF->LTF bridge: an hourly VT Screener reading is a standing
-- permission (direction + a stop-target band) for a lower-timeframe VT
-- Screener trigger, which places the actual order. See
-- docs/superpowers/specs/2026-09-09-vt-htf-ltf-bridge-design.md.
--
-- One row per (org, symbol) -- the LATEST htf reading only, no history.
-- Every valid htf alert overwrites the previous one.
CREATE TABLE vt_htf_snapshots (
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    symbol      TEXT NOT NULL,
    bias        TEXT NOT NULL CHECK (bias IN ('long', 'short')),
    entry       DOUBLE PRECISION NOT NULL,
    stop        DOUBLE PRECISION NOT NULL,
    target      DOUBLE PRECISION NOT NULL,
    -- HTF chart price at relay time. Informational only -- never gated on.
    price       DOUBLE PRECISION NOT NULL,
    -- e.g. "60". Informational, and the staleness math's minutes-per-tf.
    tf          TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, symbol)
);

-- The LTF timeframes (TradingView's own strings, e.g. "1", "5", "15") this
-- workspace currently allows to trade. Defaults to empty: an unconfigured
-- workspace trades no LTF timeframe until an admin turns one on, the same
-- safe-by-default posture as "no master account" already blocking trading.
ALTER TABLE org_webhooks
    ADD COLUMN vt_ltf_timeframes TEXT[] NOT NULL DEFAULT '{}'::TEXT[];
