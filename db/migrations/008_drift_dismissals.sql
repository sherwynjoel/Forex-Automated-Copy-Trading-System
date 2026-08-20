-- Operator drift dismissals, persisted so a deploy/restart no longer
-- resurfaces every dismissed item (Reconciler._dismissed used to be
-- in-memory only; on 2026-08-20 two prod restarts brought back 11
-- already-dismissed items). Rows are pruned when the underlying drift
-- condition clears, so the same condition RETURNING later alerts again
-- instead of staying muted forever. drift_id is the reconciler's
-- _stable_id() digest: the same condition maps to the same id across
-- runs and restarts.
CREATE TABLE drift_dismissals (
    org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    drift_id TEXT NOT NULL,
    dismissed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, drift_id)
);
