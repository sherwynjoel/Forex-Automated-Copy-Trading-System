-- Track OAuth states for single-use enforcement and session binding
CREATE TABLE oauth_states (
    state_hash TEXT PRIMARY KEY,
    session TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at TIMESTAMPTZ
);
CREATE INDEX oauth_states_by_created ON oauth_states (created_at DESC);
CREATE INDEX oauth_states_by_consumed ON oauth_states (consumed_at) WHERE consumed_at IS NULL;
