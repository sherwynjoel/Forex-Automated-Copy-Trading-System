-- Tenancy for the daily portfolio snapshots added by
-- 005_risk_snapshots_symbol.sql. Each org's Overview compares today's live
-- portfolio value against yesterday's closing value, so the sum has to be
-- restricted to that org's own accounts.
--
-- Deliberately nullable and deliberately WITHOUT a foreign key, for exactly
-- the reason events.org_id has none (005_multi_org.sql): the snapshot
-- history is a record of what an account was worth on a day, and it has to
-- survive that account -- and its org -- being disconnected or deleted. A
-- cascade would silently erase the history the moment a desk was wound up.
ALTER TABLE portfolio_snapshots ADD COLUMN org_id BIGINT;

-- Backfill from the account each snapshot belongs to. Rows whose account no
-- longer exists keep org_id NULL and simply drop out of every per-org sum
-- (they are history for an account nobody can look at any more).
UPDATE portfolio_snapshots ps
   SET org_id = a.org_id
  FROM accounts a
 WHERE a.ctid_trader_account_id = ps.account_id
   AND ps.org_id IS NULL;

CREATE INDEX portfolio_snapshots_by_org ON portfolio_snapshots (org_id, snapshot_date);
