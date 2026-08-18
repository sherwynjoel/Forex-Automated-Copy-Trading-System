-- Multi-org: users, orgs, memberships, invites; org_id on every tenant-owned
-- table; per-org master uniqueness; settings reduced to process config; the
-- single-row admin table replaced by real users.

CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    email TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX users_email_unique ON users (lower(email));

CREATE TABLE orgs (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    copying_enabled BOOLEAN NOT NULL DEFAULT true,
    dry_run BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE org_memberships (
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'trader', 'viewer')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, user_id)
);

CREATE TABLE org_invites (
    id BIGSERIAL PRIMARY KEY,
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('admin', 'trader', 'viewer')),
    token_hash TEXT NOT NULL UNIQUE,
    created_by BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    consumed_by BIGINT REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX org_invites_by_org ON org_invites (org_id);

ALTER TABLE ctid_connections ADD COLUMN org_id BIGINT REFERENCES orgs(id) ON DELETE CASCADE;
ALTER TABLE accounts ADD COLUMN org_id BIGINT REFERENCES orgs(id) ON DELETE CASCADE;
ALTER TABLE mappings ADD COLUMN org_id BIGINT REFERENCES orgs(id) ON DELETE CASCADE;
-- No FK on events: the audit log must survive org deletion.
ALTER TABLE events ADD COLUMN org_id BIGINT;
ALTER TABLE oauth_states ADD COLUMN org_id BIGINT REFERENCES orgs(id) ON DELETE CASCADE;

-- Legacy single-tenant deployments: gather everything into a 'Default' org,
-- carrying the old global kill-switch/dry-run values with it.
DO $$
DECLARE
    default_org_id BIGINT;
BEGIN
    IF EXISTS (SELECT 1 FROM ctid_connections) OR EXISTS (SELECT 1 FROM accounts) THEN
        INSERT INTO orgs (name, copying_enabled, dry_run)
        SELECT 'Default', s.copying_enabled, s.dry_run FROM settings s WHERE s.id = TRUE
        RETURNING id INTO default_org_id;
        UPDATE ctid_connections SET org_id = default_org_id;
        UPDATE accounts SET org_id = default_org_id;
        UPDATE mappings SET org_id = default_org_id;
        UPDATE events SET org_id = default_org_id;
    END IF;
END $$;

-- OAuth states are short-lived CSRF state; in-flight flows may fail during
-- the upgrade, harmlessly.
DELETE FROM oauth_states;

ALTER TABLE ctid_connections ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE accounts ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE mappings ALTER COLUMN org_id SET NOT NULL;
ALTER TABLE oauth_states ALTER COLUMN org_id SET NOT NULL;

DROP INDEX accounts_single_master;
CREATE UNIQUE INDEX accounts_single_master ON accounts (org_id) WHERE role = 'master';
CREATE INDEX mappings_by_org ON mappings (org_id);
CREATE INDEX events_by_org ON events (org_id, ts DESC);

ALTER TABLE settings DROP COLUMN copying_enabled;
ALTER TABLE settings DROP COLUMN dry_run;

DROP TABLE admin;
