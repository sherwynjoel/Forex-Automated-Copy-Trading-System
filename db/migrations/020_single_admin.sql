-- One person runs the desk. 'owner', 'admin' and 'trader' were three ranks
-- for the same human; they become one stored role, 'admin', carrying the
-- union of their permissions. See
-- docs/superpowers/specs/2026-09-25-single-admin-role-design.md.

UPDATE org_memberships SET role = 'admin' WHERE role IN ('owner', 'trader');

-- An unconsumed Trader link sitting in someone's inbox must not silently
-- become full control: revoke it, the admin re-issues it if still wanted.
-- Consumed ones only need to satisfy the new constraint.
DELETE FROM org_invites WHERE role = 'trader' AND consumed_at IS NULL;
UPDATE org_invites SET role = 'admin' WHERE role = 'trader';

ALTER TABLE org_memberships DROP CONSTRAINT org_memberships_role_check;
ALTER TABLE org_memberships ADD CONSTRAINT org_memberships_role_check
    CHECK (role IN ('admin', 'viewer', 'investor'));
ALTER TABLE org_invites DROP CONSTRAINT org_invites_role_check;
ALTER TABLE org_invites ADD CONSTRAINT org_invites_role_check
    CHECK (role IN ('admin', 'viewer', 'investor'));
