-- MPIN: a six-digit second factor every user enters after email+password.
-- The hash is argon2 like the password; the lock state lives here so it
-- survives api restarts and is shared across workers. NULL mpin_hash means
-- "never set" -- the dashboard forces the user to set one on first login.
-- See docs/superpowers/specs/2026-09-26-mpin-second-factor-design.md.
ALTER TABLE users
    ADD COLUMN mpin_hash            TEXT,
    ADD COLUMN mpin_failed_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN mpin_locked_until    TIMESTAMPTZ,
    ADD COLUMN mpin_set_at          TIMESTAMPTZ;
