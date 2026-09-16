-- Server-side risk engine: any alert that omits its own stop/target/
-- trailing gets them filled in from a per-org-per-symbol rule, and
-- trailing (moving the stop as price moves favourably) is managed here
-- instead of inside individual Pine scripts. See
-- docs/superpowers/specs/2026-09-16-server-side-risk-engine-design.md.

-- One row per org+symbol the owner has configured. No row = no default,
-- no trailing -- a symbol left unconfigured behaves exactly as before
-- this feature existed.
CREATE TABLE org_risk_rules (
    org_id              BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    symbol              TEXT NOT NULL,
    -- Raw price-distance in the instrument's own quote units, same
    -- convention as the Pine bots' own points inputs. NULL = no default
    -- for that side (the alert's own value, or nothing, is used).
    stop_points         DOUBLE PRECISION,
    target_points       DOUBLE PRECISION,
    trailing_enabled    BOOLEAN NOT NULL DEFAULT false,
    -- Required (enforced in the api layer, not here) when trailing_enabled.
    trail_start_points  DOUBLE PRECISION,
    trail_step_points   DOUBLE PRECISION,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, symbol)
);

-- The trailing ratchet's memory: a stop must only ever move in the
-- favourable direction, which requires remembering the best price a
-- position has reached, not just its current price. One row per
-- currently-tracked position; created when a trailing-enabled position
-- opens, deleted when it closes.
CREATE TABLE position_trailing_state (
    account_id    BIGINT NOT NULL,
    position_id   BIGINT NOT NULL,
    best_price    DOUBLE PRECISION NOT NULL,
    current_stop  DOUBLE PRECISION NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, position_id)
);
