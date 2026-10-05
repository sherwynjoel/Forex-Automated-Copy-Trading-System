-- Client portal, phase 2: identity. The investor's profile and identity
-- verification (one row per investor per org: the reference's KYC step 1
-- IS the profile), the account packages an admin offers, the live trading
-- account requests investors file against them, and the sign-in history.
-- Additive only: nothing is dropped or copied.
-- See docs/superpowers/specs/2026-10-01-client-portal-phase-2-identity-design.md
-- section 4.

CREATE TABLE kyc_profiles (
    org_id                BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id               BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    full_name             TEXT NULL,
    gender                TEXT NULL CHECK (gender IN ('male', 'female', 'other')),
    date_of_birth         DATE NULL,
    phone                 TEXT NULL,
    address_line          TEXT NULL,
    area                  TEXT NULL,
    landmark              TEXT NULL,
    city                  TEXT NULL,
    state                 TEXT NULL,
    postal_code           TEXT NULL,
    -- ISO 3166 alpha-2, upper case.
    country_residence     TEXT NULL CHECK (country_residence ~ '^[A-Z]{2}$'),
    country_citizenship   TEXT NULL CHECK (country_citizenship ~ '^[A-Z]{2}$'),
    id_type               TEXT NULL CHECK (id_type IN ('passport', 'national_id', 'driving_licence')),
    id_number             TEXT NULL,
    id_front_file_id      BIGINT NULL REFERENCES files(id) ON DELETE SET NULL,
    id_back_file_id       BIGINT NULL REFERENCES files(id) ON DELETE SET NULL,
    address_proof_file_id BIGINT NULL REFERENCES files(id) ON DELETE SET NULL,
    photo_file_id         BIGINT NULL REFERENCES files(id) ON DELETE SET NULL,
    status                TEXT NOT NULL DEFAULT 'draft'
                          CHECK (status IN ('draft', 'submitted', 'approved', 'rejected')),
    submitted_at          TIMESTAMPTZ NULL,
    decided_by            BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    decided_at            TIMESTAMPTZ NULL,
    decision_note         TEXT NULL,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, user_id)
);
CREATE INDEX kyc_profiles_queue ON kyc_profiles (org_id, status, submitted_at);

-- What an investor may request: a live MT5 account type the admin opens by
-- hand at the broker.
CREATE TABLE account_packages (
    id                BIGSERIAL PRIMARY KEY,
    org_id            BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    min_deposit       NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (min_deposit >= 0),
    currency          TEXT NOT NULL DEFAULT 'USD',
    spread_label      TEXT NULL,
    leverage_options  INTEGER[] NOT NULL CHECK (cardinality(leverage_options) > 0),
    enabled           BOOLEAN NOT NULL DEFAULT true,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX account_packages_by_org ON account_packages (org_id, sort_order, id);

-- One live account request. The two MT5 passwords the investor chose are
-- Fernet-sealed and exist only while an admin still has to act: the CHECK
-- makes "wiped on decision" a database rule, not a convention.
CREATE TABLE account_requests (
    id                     BIGSERIAL PRIMARY KEY,
    org_id                 BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id                BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    package_id             BIGINT NULL REFERENCES account_packages(id) ON DELETE SET NULL,
    package_name           TEXT NOT NULL,
    leverage               INTEGER NOT NULL CHECK (leverage > 0),
    main_password_enc      TEXT NULL,
    investor_password_enc  TEXT NULL,
    status                 TEXT NOT NULL DEFAULT 'requested'
                           CHECK (status IN ('requested', 'fulfilled', 'rejected', 'cancelled')),
    mt5_login              BIGINT NULL,
    mt5_server             TEXT NULL,
    account_id             BIGINT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE SET NULL,
    decided_by             BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    decided_at             TIMESTAMPTZ NULL,
    decision_note          TEXT NULL,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT account_requests_passwords_only_while_open CHECK (
        status = 'requested' OR (main_password_enc IS NULL AND investor_password_enc IS NULL)),
    CONSTRAINT account_requests_fulfilled_has_login CHECK (
        status <> 'fulfilled' OR (mt5_login IS NOT NULL AND mt5_server IS NOT NULL))
);
CREATE INDEX account_requests_queue ON account_requests (org_id, status, created_at);
CREATE UNIQUE INDEX account_requests_one_open ON account_requests (org_id, user_id)
    WHERE status = 'requested';

-- Sign-in history. Written by /api/login (password_ok, or failed for a
-- known email -- never for an unknown one, so this table cannot probe for
-- accounts) and /api/mpin/verify (mpin_ok). Rows older than 180 days are
-- deleted on write (auth.record_login).
CREATE TABLE login_events (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ip          TEXT NOT NULL,
    user_agent  TEXT NULL,
    outcome     TEXT NOT NULL CHECK (outcome IN ('password_ok', 'mpin_ok', 'failed')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX login_events_by_user ON login_events (user_id, created_at DESC);
