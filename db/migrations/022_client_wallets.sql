-- Client portal, phase 1: wallets backed by a ledger, payment methods the
-- org receives at, payout destinations investors receive at, deposits,
-- withdrawals and transfers as three request tables of one shape, and the
-- files table behind receipts and proofs. Replaces the three 2026-09-23
-- tables (org_investor_wallets, investor_deposits, investor_withdrawals):
-- their rows are copied first so a developer database survives the upgrade.
-- Production has no rows in any of them (the 2026-09-25 reset).
-- See docs/superpowers/specs/2026-09-29-client-portal-phase-1-money-design.md
-- section 5. Money is NUMERIC(18,2); every request table carries the audit
-- trio decided_by / decided_at / decision_note and a (org, status, created)
-- queue index.

-- One row per uploaded file; the bytes live on disk under UPLOAD_DIR at
-- storage_key. Every purpose of every later phase is in the CHECK now so
-- the constraint never has to move again.
CREATE TABLE files (
    id            BIGSERIAL PRIMARY KEY,
    org_id        BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose       TEXT NOT NULL CHECK (purpose IN ('deposit_receipt', 'payout_proof',
                      'kyc_document', 'kyc_photo', 'ticket_attachment', 'avatar')),
    content_type  TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL CHECK (size_bytes > 0),
    sha256        TEXT NOT NULL,
    storage_key   TEXT NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX files_by_user ON files (org_id, user_id, created_at DESC);

-- Where the org receives money: a crypto address on a network or a bank
-- account, configured by an admin. Replaces the single wallet card.
CREATE TABLE payment_methods (
    id            BIGSERIAL PRIMARY KEY,
    org_id        BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('crypto', 'bank')),
    label         TEXT NOT NULL,
    enabled       BOOLEAN NOT NULL DEFAULT true,
    currency      TEXT NOT NULL DEFAULT 'USD',
    details       JSONB NOT NULL,
    min_amount    NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (min_amount >= 0),
    fee_pct       NUMERIC(6,3) NOT NULL DEFAULT 0 CHECK (fee_pct >= 0 AND fee_pct < 100),
    instructions  TEXT NULL,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_by    BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX payment_methods_by_org ON payment_methods (org_id, sort_order, id);

-- One row per org, created on first read (portal_common.portal_settings).
CREATE TABLE portal_settings (
    org_id              BIGINT PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
    withdrawal_min      NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (withdrawal_min >= 0),
    withdrawal_fee_pct  NUMERIC(6,3) NOT NULL DEFAULT 0
                        CHECK (withdrawal_fee_pct >= 0 AND withdrawal_fee_pct < 100),
    updated_by          BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Where an investor receives money, saved by the investor and approved by
-- an admin. Removed rows stay so withdrawal history keeps its snapshot.
CREATE TABLE payout_destinations (
    id             BIGSERIAL PRIMARY KEY,
    org_id         BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('bank', 'crypto')),
    nickname       TEXT NOT NULL,
    details        JSONB NOT NULL,
    proof_file_id  BIGINT NULL REFERENCES files(id) ON DELETE SET NULL,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'approved', 'rejected', 'removed')),
    decided_by     BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    decided_at     TIMESTAMPTZ NULL,
    decision_note  TEXT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX payout_destinations_by_user ON payout_destinations (org_id, user_id, status);
CREATE INDEX payout_destinations_queue ON payout_destinations (org_id, status, created_at);

-- The ledger. A balance is the sum of a wallet's entries; nothing stores a
-- balance. Settlement rows name the request they settle, and the partial
-- unique index makes a replayed settlement a no-op (ON CONFLICT DO NOTHING)
-- so a double click can never double-credit.
CREATE TABLE wallet_entries (
    id          BIGSERIAL PRIMARY KEY,
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    wallet      TEXT NOT NULL CHECK (wallet IN ('main', 'credit', 'pamm', 'social')),
    amount      NUMERIC(18,2) NOT NULL CHECK (amount <> 0),
    kind        TEXT NOT NULL CHECK (kind IN ('deposit', 'withdrawal', 'transfer',
                    'adjustment', 'bonus', 'commission', 'fee')),
    ref_table   TEXT NULL,
    ref_id      BIGINT NULL,
    note        TEXT NULL,
    created_by  BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX wallet_entries_by_user
    ON wallet_entries (org_id, user_id, wallet, created_at DESC, id DESC);
CREATE INDEX wallet_entries_by_org_time ON wallet_entries (org_id, created_at DESC);
CREATE UNIQUE INDEX wallet_entries_one_per_ref ON wallet_entries (ref_table, ref_id, wallet)
    WHERE ref_table IS NOT NULL;

-- "I have sent it": method kind and label are snapshots taken at filing so
-- the row still reads right after the admin edits or deletes the method.
CREATE TABLE deposits (
    id                 BIGSERIAL PRIMARY KEY,
    org_id             BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id            BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    method_id          BIGINT NULL REFERENCES payment_methods(id) ON DELETE SET NULL,
    method_kind        TEXT NOT NULL CHECK (method_kind IN ('crypto', 'bank')),
    method_label       TEXT NOT NULL,
    amount             NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    fee                NUMERIC(18,2) NOT NULL DEFAULT 0,
    credited_amount    NUMERIC(18,2) NULL,
    reference          TEXT NOT NULL,
    receipt_file_id    BIGINT NULL REFERENCES files(id) ON DELETE SET NULL,
    target             TEXT NOT NULL DEFAULT 'wallet' CHECK (target IN ('wallet', 'account')),
    target_account_id  BIGINT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE SET NULL,
    note               TEXT NULL,
    status             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'confirmed', 'rejected', 'cancelled')),
    decided_by         BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    decided_at         TIMESTAMPTZ NULL,
    decision_note      TEXT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX deposits_queue ON deposits (org_id, status, created_at);
CREATE INDEX deposits_by_user ON deposits (org_id, user_id, created_at DESC);
-- One live notice per transaction reference per org: two pending rows
-- quoting the same transfer are the same money twice. Rejected and
-- cancelled rows leave the index so a mistake can be re-filed.
CREATE UNIQUE INDEX deposits_one_live_reference ON deposits (org_id, reference)
    WHERE status IN ('pending', 'confirmed');

-- A cash-out request, always from the main wallet. The destination is a
-- snapshot too; the row it points at is kept forever (RESTRICT).
CREATE TABLE withdrawals (
    id                   BIGSERIAL PRIMARY KEY,
    org_id               BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id              BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    destination_id       BIGINT NOT NULL REFERENCES payout_destinations(id) ON DELETE RESTRICT,
    destination_kind     TEXT NOT NULL,
    destination_summary  TEXT NOT NULL,
    amount               NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    fee                  NUMERIC(18,2) NOT NULL DEFAULT 0,
    net_amount           NUMERIC(18,2) NOT NULL,
    status               TEXT NOT NULL DEFAULT 'requested'
                         CHECK (status IN ('requested', 'approved', 'paid', 'rejected', 'cancelled')),
    decided_by           BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    decided_at           TIMESTAMPTZ NULL,
    decision_note        TEXT NULL,
    paid_by              BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    paid_at              TIMESTAMPTZ NULL,
    txid                 TEXT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX withdrawals_queue ON withdrawals (org_id, status, created_at);
CREATE INDEX withdrawals_by_user ON withdrawals (org_id, user_id, created_at DESC);

-- Money between wallets and the trading account. Each end is a wallet or
-- an account, never neither and never both; account -> account is not a
-- transfer the portal makes.
CREATE TABLE transfers (
    id                 BIGSERIAL PRIMARY KEY,
    org_id             BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id            BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_kind        TEXT NOT NULL CHECK (source_kind IN ('wallet', 'account')),
    source_wallet      TEXT NULL CHECK (source_wallet IN ('main', 'credit', 'pamm', 'social')),
    source_account_id  BIGINT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE SET NULL,
    target_kind        TEXT NOT NULL CHECK (target_kind IN ('wallet', 'account')),
    target_wallet      TEXT NULL CHECK (target_wallet IN ('main', 'credit', 'pamm', 'social')),
    target_account_id  BIGINT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE SET NULL,
    amount             NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    status             TEXT NOT NULL DEFAULT 'requested'
                       CHECK (status IN ('requested', 'approved', 'done', 'rejected', 'cancelled')),
    equity_at_request  NUMERIC(18,2) NULL,
    equity_verified    BOOLEAN NOT NULL DEFAULT false,
    decided_by         BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    decided_at         TIMESTAMPTZ NULL,
    decision_note      TEXT NULL,
    done_by            BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    done_at            TIMESTAMPTZ NULL,
    note               TEXT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((source_kind = 'wallet') = (source_wallet IS NOT NULL)),
    CHECK ((source_kind = 'account') = (source_account_id IS NOT NULL)),
    CHECK ((target_kind = 'wallet') = (target_wallet IS NOT NULL)),
    CHECK ((target_kind = 'account') = (target_account_id IS NOT NULL)),
    CHECK (NOT (source_kind = 'account' AND target_kind = 'account'))
);
CREATE INDEX transfers_queue ON transfers (org_id, status, created_at);
CREATE INDEX transfers_by_user ON transfers (org_id, user_id, created_at DESC);

-- ---------------------------------------------------------------------
-- Copy the 2026-09-23 rows, then drop their tables. Ids are kept for
-- deposits and withdrawals so the ledger rows can name them and the
-- sequences continue where the old ones left off.

-- The org's wallet card becomes its one crypto payment method.
INSERT INTO payment_methods (org_id, kind, label, currency, details, created_by, created_at, updated_at)
SELECT org_id, 'crypto', coin || ' on ' || network, 'USD',
       jsonb_strip_nulls(jsonb_build_object('coin', coin, 'network', network,
                                            'address', address, 'memo', memo)),
       updated_by, updated_at, updated_at
FROM org_investor_wallets;

INSERT INTO deposits (id, org_id, user_id, method_id, method_kind, method_label, amount, fee,
                      credited_amount, reference, target, note, status,
                      decided_by, decided_at, decision_note, created_at)
SELECT d.id, d.org_id, d.user_id,
       (SELECT pm.id FROM payment_methods pm WHERE pm.org_id = d.org_id ORDER BY pm.id LIMIT 1),
       'crypto', d.coin, d.amount, 0,
       CASE WHEN d.status = 'confirmed' THEN d.amount END,
       d.txid, 'wallet', d.note, d.status,
       d.decided_by, d.decided_at, d.decision_note, d.created_at
FROM investor_deposits d;

INSERT INTO wallet_entries (org_id, user_id, wallet, amount, kind, ref_table, ref_id, created_by, created_at)
SELECT org_id, user_id, 'main', amount, 'deposit', 'deposits', id, decided_by,
       COALESCE(decided_at, created_at)
FROM investor_deposits
WHERE status = 'confirmed';

-- One approved crypto destination per distinct address an investor used.
INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details, status, created_at)
SELECT org_id, user_id, 'crypto', 'Imported',
       jsonb_build_object('coin', '', 'network', '', 'address', destination),
       'approved', MIN(created_at)
FROM investor_withdrawals
GROUP BY org_id, user_id, destination;

INSERT INTO withdrawals (id, org_id, user_id, destination_id, destination_kind, destination_summary,
                         amount, fee, net_amount, status, decided_by, decided_at, decision_note,
                         paid_by, paid_at, txid, created_at)
SELECT w.id, w.org_id, w.user_id, pd.id, 'crypto', w.destination,
       w.amount, 0, w.amount, w.status, w.decided_by, w.decided_at, w.decision_note,
       w.paid_by, w.paid_at, w.txid, w.created_at
FROM investor_withdrawals w
JOIN payout_destinations pd
  ON pd.org_id = w.org_id AND pd.user_id = w.user_id
 AND pd.nickname = 'Imported' AND pd.details->>'address' = w.destination;

INSERT INTO wallet_entries (org_id, user_id, wallet, amount, kind, ref_table, ref_id, created_by, created_at)
SELECT org_id, user_id, 'main', -amount, 'withdrawal', 'withdrawals', id, paid_by,
       COALESCE(paid_at, created_at)
FROM investor_withdrawals
WHERE status = 'paid';

-- Explicit ids do not advance a BIGSERIAL; point the sequences past them.
SELECT setval('deposits_id_seq', COALESCE((SELECT max(id) FROM deposits), 1),
              (SELECT count(*) > 0 FROM deposits));
SELECT setval('withdrawals_id_seq', COALESCE((SELECT max(id) FROM withdrawals), 1),
              (SELECT count(*) > 0 FROM withdrawals));

DROP TABLE investor_withdrawals;
DROP TABLE investor_deposits;
DROP TABLE org_investor_wallets;
