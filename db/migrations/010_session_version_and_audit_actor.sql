-- Two security columns.
--
-- users.session_version makes sessions revocable. Session cookies are
-- stateless signed blobs, so before this there was no way to end one: a
-- stolen cookie stayed valid for its full 12 hours and logout only cleared
-- the browser that clicked it. The version is embedded in the cookie and
-- compared on every authenticated request; bumping it (password change,
-- "sign out everywhere", suspected compromise) invalidates every cookie
-- outstanding for that user without touching anyone else's.
--
-- events.actor_email records WHO performed a money-moving action. Events
-- were attributed only to an org and an account, so with more than one
-- member it was impossible to tell which person placed an order, flattened
-- the fleet, or changed a multiplier. Email rather than a user id so the
-- trail stays readable after a user row is deleted.

ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0;

-- Both ALTERs are metadata-only in Postgres 11+ (a nullable column with no
-- default rewrites nothing), so this migration takes its locks for
-- microseconds even on a live table. Deliberately NO index on actor_email:
-- building one holds the same ACCESS EXCLUSIVE lock for the length of the
-- build, and freezing the events table while the copier is trading costs
-- far more than a sequential scan over an audit log this size.
ALTER TABLE events ADD COLUMN actor_email TEXT;
