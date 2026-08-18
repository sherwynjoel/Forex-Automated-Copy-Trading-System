-- Operator-set display name for an account, shown alongside the broker's
-- numeric login in the dashboard.  The cTrader Open API exposes no
-- account-holder name or email (only a numeric cTID user id), so this is
-- the only human-friendly identity an account can carry.
ALTER TABLE accounts ADD COLUMN nickname TEXT;
