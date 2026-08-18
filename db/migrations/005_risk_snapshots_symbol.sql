-- 'risk' event category: broker margin calls (and future risk alerts) are
-- account-level risk facts, not connection or control noise.
ALTER TABLE events DROP CONSTRAINT events_category_check;
ALTER TABLE events ADD CONSTRAINT events_category_check
    CHECK (category IN ('master_event', 'slave_action', 'connection', 'auth',
                        'drift', 'control', 'risk'));

-- Daily portfolio snapshots, upserted by the copier's balance refresh loop
-- (last write of a day wins = that day's closing value). Powers the
-- Overview's portfolio-vs-yesterday comparison. Deliberately no FK to
-- accounts: the history survives a disconnect.
CREATE TABLE portfolio_snapshots (
    snapshot_date DATE NOT NULL,
    account_id BIGINT NOT NULL,
    balance NUMERIC(18, 2),
    equity NUMERIC(18, 2),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_date, account_id)
);

-- The symbol a mapping trades, stamped at creation, so copy feeds can show
-- it without a broker round trip. NULL on rows created before this
-- migration (and on adopted orphans, whose symbol the copier never chose).
ALTER TABLE mappings ADD COLUMN symbol TEXT;
