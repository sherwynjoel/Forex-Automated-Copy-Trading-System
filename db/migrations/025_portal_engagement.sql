-- Client portal, phase 4: the in-app notification centre (with per-topic
-- email switches), appearance per user, support tickets between investors
-- and the desk, and bonuses paid into the Credit wallet. Additive only:
-- eight new tables, no existing table changes. files.purpose already allows
-- 'ticket_attachment', wallet_entries.kind 'bonus', wallet_entries.wallet
-- 'credit'.
-- See docs/superpowers/specs/2026-10-05-client-portal-phase-4-design.md
-- section 4.

-- One row per recipient per event. link is an in-app path (never a URL).
CREATE TABLE notifications (
    id          BIGSERIAL PRIMARY KEY,
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic       TEXT NOT NULL CHECK (topic IN ('money', 'identity', 'support', 'bonus')),
    title       TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120),
    body        TEXT NOT NULL CHECK (char_length(body) <= 500),
    link        TEXT NULL CHECK (link LIKE '/%'),
    read_at     TIMESTAMPTZ NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX notifications_by_user
    ON notifications (org_id, user_id, created_at DESC, id DESC);
CREATE INDEX notifications_unread ON notifications (org_id, user_id) WHERE read_at IS NULL;

-- Email switches per user per org; no row means every switch is on.
CREATE TABLE notification_prefs (
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    money       BOOLEAN NOT NULL DEFAULT true,
    identity    BOOLEAN NOT NULL DEFAULT true,
    support     BOOLEAN NOT NULL DEFAULT true,
    bonus       BOOLEAN NOT NULL DEFAULT true,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, user_id)
);

-- Appearance follows the user across orgs and browsers.
CREATE TABLE user_settings (
    user_id     BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    theme       TEXT NOT NULL DEFAULT 'system'
                CHECK (theme IN ('light', 'dim', 'dark', 'system')),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- What an investor may raise a ticket about, defined by an admin.
CREATE TABLE ticket_subjects (
    id          BIGSERIAL PRIMARY KEY,
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    label       TEXT NOT NULL CHECK (char_length(label) BETWEEN 1 AND 80),
    enabled     BOOLEAN NOT NULL DEFAULT true,
    sort        INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ticket_subjects_one_label ON ticket_subjects (org_id, lower(label));

-- One support thread. subject_label is a snapshot, so deleting a subject
-- never rewrites an old ticket.
CREATE TABLE tickets (
    id               BIGSERIAL PRIMARY KEY,
    org_id           BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id          BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subject_id       BIGINT NULL REFERENCES ticket_subjects(id) ON DELETE SET NULL,
    subject_label    TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'open', 'closed')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_message_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at        TIMESTAMPTZ NULL,
    closed_by        BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    CHECK ((status = 'closed') = (closed_at IS NOT NULL))
);
CREATE INDEX tickets_queue ON tickets (org_id, status, last_message_at DESC);
CREATE INDEX tickets_by_user ON tickets (org_id, user_id, last_message_at DESC);

-- The thread. file_ids are the investor's own ticket_attachment uploads;
-- routes/portal_files.file_belongs keeps each file to one message.
CREATE TABLE ticket_messages (
    id          BIGSERIAL PRIMARY KEY,
    ticket_id   BIGINT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    author_id   BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    from_desk   BOOLEAN NOT NULL,
    body        TEXT NOT NULL CHECK (char_length(body) BETWEEN 1 AND 4000),
    file_ids    BIGINT[] NOT NULL DEFAULT '{}' CHECK (cardinality(file_ids) <= 3),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ticket_messages_thread ON ticket_messages (ticket_id, created_at, id);

-- One row per org, created on first read (portal_common.bonus_rules).
CREATE TABLE bonus_rules (
    org_id           BIGINT PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
    signup_enabled   BOOLEAN NOT NULL DEFAULT false,
    signup_amount    NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (signup_amount >= 0),
    kyc_enabled      BOOLEAN NOT NULL DEFAULT false,
    kyc_amount       NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (kyc_amount >= 0),
    deposit_enabled  BOOLEAN NOT NULL DEFAULT false,
    deposit_pct      NUMERIC(6,3) NOT NULL DEFAULT 0
                     CHECK (deposit_pct >= 0 AND deposit_pct <= 100),
    deposit_cap      NUMERIC(18,2) NULL CHECK (deposit_cap > 0),
    updated_by       BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every bonus paid; its credit ledger row references it (ref_table
-- 'bonuses'). The partial unique indexes make each rule pay at most once:
-- portal_common.pay_bonus inserts with ON CONFLICT DO NOTHING.
CREATE TABLE bonuses (
    id          BIGSERIAL PRIMARY KEY,
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source      TEXT NOT NULL CHECK (source IN ('signup', 'kyc', 'deposit', 'manual')),
    source_id   BIGINT NULL,
    amount      NUMERIC(18,2) NOT NULL CHECK (amount <> 0),
    note        TEXT NULL,
    created_by  BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Only a hand-posted claw-back is negative; only a deposit bonus names a row.
    CHECK (amount > 0 OR source = 'manual'),
    CHECK ((source = 'deposit') = (source_id IS NOT NULL))
);
CREATE UNIQUE INDEX bonuses_once_per_user ON bonuses (org_id, user_id, source)
    WHERE source IN ('signup', 'kyc');
CREATE UNIQUE INDEX bonuses_once_per_deposit ON bonuses (org_id, source_id)
    WHERE source = 'deposit';
CREATE INDEX bonuses_by_user ON bonuses (org_id, user_id, created_at DESC, id DESC);
