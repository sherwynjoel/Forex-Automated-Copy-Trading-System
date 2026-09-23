-- Investor portal: a membership role that can only see its own linked
-- account, the workspace's receiving-wallet card, and the ledger of
-- deposit notices and withdrawal requests. The app records money movements
-- people make outside it; it never holds keys or moves funds. See
-- docs/superpowers/specs/2026-09-23-investor-portal-design.md.

-- 'investor' ranks below 'viewer' in the api (ROLE_RANK), so every existing
-- viewer-or-higher endpoint refuses it without per-route changes.
ALTER TABLE org_memberships DROP CONSTRAINT org_memberships_role_check;
ALTER TABLE org_memberships ADD CONSTRAINT org_memberships_role_check
    CHECK (role IN ('owner', 'admin', 'trader', 'viewer', 'investor'));
ALTER TABLE org_invites DROP CONSTRAINT org_invites_role_check;
ALTER TABLE org_invites ADD CONSTRAINT org_invites_role_check
    CHECK (role IN ('admin', 'trader', 'viewer', 'investor'));

-- The one link the whole portal reads: which member this account belongs
-- to. One investor has at most one account per workspace and an account
-- belongs to at most one investor. Unlinking sets it NULL.
ALTER TABLE accounts ADD COLUMN investor_user_id BIGINT NULL
    REFERENCES users(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX accounts_one_per_investor
    ON accounts (org_id, investor_user_id) WHERE investor_user_id IS NOT NULL;

-- Where investors send crypto. An address to RECEIVE at, nothing more; the
-- QR is drawn in the browser from it. No row = deposits not open yet.
CREATE TABLE org_investor_wallets (
    org_id      BIGINT PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
    coin        TEXT NOT NULL,
    network     TEXT NOT NULL,
    address     TEXT NOT NULL,
    memo        TEXT,
    updated_by  BIGINT REFERENCES users(id) ON DELETE SET NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- "I have sent it": the investor's own notice, confirmed or rejected by an
-- admin. Only confirmed rows count toward the ledger. account_id is filled
-- at confirmation from the investor's link at that moment (NULL when no
-- account exists yet); the summary sums by user_id, never by account.
CREATE TABLE investor_deposits (
    id             BIGSERIAL PRIMARY KEY,
    org_id         BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id     BIGINT REFERENCES accounts(ctid_trader_account_id) ON DELETE SET NULL,
    amount         NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    coin           TEXT NOT NULL,
    txid           TEXT NOT NULL,
    note           TEXT,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'confirmed', 'rejected')),
    decided_by     BIGINT REFERENCES users(id) ON DELETE SET NULL,
    decided_at     TIMESTAMPTZ,
    decision_note  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX investor_deposits_queue ON investor_deposits (org_id, status, created_at);
-- One live notice per transaction id. Two pending rows quoting the same
-- chain transaction are the same money twice, and an admin working the
-- queue would confirm both. Rejected rows are excluded so a genuine
-- mistake (wrong amount, wrong coin) can be rejected and re-filed with
-- the same txid.
CREATE UNIQUE INDEX investor_deposits_one_live_txid
    ON investor_deposits (org_id, txid) WHERE status <> 'rejected';

-- A cash-out request. approved means "admin will pay"; paid means the
-- crypto left the admin's wallet and txid says where. An account with
-- money-movement history cannot be deleted from under it (RESTRICT).
CREATE TABLE investor_withdrawals (
    id                 BIGSERIAL PRIMARY KEY,
    org_id             BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id            BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id         BIGINT NOT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE RESTRICT,
    amount             NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    destination        TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'requested'
                       CHECK (status IN ('requested', 'approved', 'paid', 'rejected')),
    equity_at_request  NUMERIC(18,2),
    equity_verified    BOOLEAN NOT NULL DEFAULT false,
    decided_by         BIGINT REFERENCES users(id) ON DELETE SET NULL,
    decided_at         TIMESTAMPTZ,
    decision_note      TEXT,
    paid_by            BIGINT REFERENCES users(id) ON DELETE SET NULL,
    paid_at            TIMESTAMPTZ,
    txid               TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX investor_withdrawals_queue ON investor_withdrawals (org_id, status, created_at);
