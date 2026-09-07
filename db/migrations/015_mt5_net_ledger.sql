-- Netting masters: the net position expanded into virtual master positions (spec "Netting accounts")
CREATE TABLE mt5_net_ledger (
    account_id   BIGINT NOT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE CASCADE,
    virtual_id   BIGINT NOT NULL,                 -- the IN deal's ticket
    symbol       TEXT NOT NULL,                   -- broker name
    side         TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    volume_open  INTEGER NOT NULL,                -- centilots at open
    volume_left  INTEGER NOT NULL,                -- centilots still open
    stop_loss    DOUBLE PRECISION,
    take_profit  DOUBLE PRECISION,
    opened_at_ms BIGINT NOT NULL,
    PRIMARY KEY (account_id, virtual_id)
);
CREATE INDEX mt5_net_ledger_by_symbol ON mt5_net_ledger (account_id, symbol, opened_at_ms);
