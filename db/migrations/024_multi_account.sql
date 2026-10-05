-- Client portal phase 3: an investor may hold several live accounts in one
-- workspace, up to a cap the admin sets. An account still has exactly one
-- owner (accounts.investor_user_id, unchanged). No row is rewritten: every
-- investor has 0 or 1 account today, which stays valid.
-- See docs/superpowers/specs/2026-10-05-client-portal-phase-3-multi-account-design.md.
DROP INDEX accounts_one_per_investor;

-- The per-investor lookups (portal_common.linked_accounts) keep an index.
CREATE INDEX accounts_by_investor
    ON accounts (org_id, investor_user_id) WHERE investor_user_id IS NOT NULL;

-- Live accounts (owned + open requests) per investor; lowering it below
-- what someone already has only blocks new requests and links.
ALTER TABLE portal_settings ADD COLUMN max_live_accounts INTEGER NOT NULL DEFAULT 5
    CHECK (max_live_accounts BETWEEN 1 AND 50);
