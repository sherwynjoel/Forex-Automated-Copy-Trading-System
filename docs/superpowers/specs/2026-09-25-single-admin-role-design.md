# Single Admin role: Owner, Admin and Trader become one — Design

**Date:** 2026-09-25
**Status:** approved in discussion (Option A of three), awaiting review of this document
**Supersedes:** the role ladder in `2026-08-18-multi-org-rbac-design.md` §4. That
spec stays as the record of how orgs, memberships and invites work; only the
set of roles changes here.

## 1. Goal

One person runs a MirrorFleet desk. Today that person holds three ranks at
once — `owner`, `admin` and `trader` were designed as separate deputies, but
in practice they are the same user, and the dashboard already labels the
owner "Admin" and stopped offering the other two on 2026-09-24 (commit
5445c62, presentation only). This design finishes the job: the three ranks
collapse into a single stored role, `admin`, with every permission the
three had between them. The backend, the database constraints, the tests
and the README all say the same thing afterwards.

## 2. Non-goals

- A hard "exactly one Admin per org" rule. The owner declined it: several
  Admins are allowed so the desk can be handed over.
- Per-account or per-page permissions inside an org.
- Any change to *account* roles (`master`, `slave`, `ignored` on
  `accounts.role`). Those describe trading accounts, not people, and are
  untouched.
- Any change to the Investor portal or to what Viewer and Investor can do.

## 3. Roles

Three membership roles, ranked:

    investor < viewer < admin

| Role | Sees | Can do |
|---|---|---|
| `investor` | Only the investor portal for their linked account | Deposit notices, withdrawal requests, own password |
| `viewer` | Every desk page | Read only |
| `admin` | Everything | Everything Owner, Admin and Trader could do: manual orders, close/amend/cancel, pause/resume, kill switch, dry run, account roles/multipliers/nicknames/cutoffs, OAuth connect/disconnect, drift remedies, MT5 accounts and keys, symbol aliases, webhook settings, risk rules, investor wallet and decisions, invites, member roles, rename and delete the org |

Rules:

- An org always keeps **at least one Admin**. The last Admin cannot be
  demoted, removed, or leave. Same guard as today's last-owner rule, same
  `FOR UPDATE` lock on the Admin set, same `409`.
- Any Admin may promote a Viewer or Investor to Admin, demote another Admin
  to Viewer or Investor, remove any other member, and create invites for
  any of the three roles.
- Any member may leave on their own, except a last Admin.
- Changing a member away from `investor` still does not unlink their
  account (unchanged from the investor portal spec).

## 4. Data (migration `020_single_admin.sql`)

Runs in the migrate service like every other file, in one transaction.

1. `UPDATE org_memberships SET role = 'admin' WHERE role IN ('owner', 'trader');`
   Existing `admin` rows are already right. Existing `viewer` and
   `investor` rows are untouched.
2. Invites: `DELETE FROM org_invites WHERE role = 'trader' AND consumed_at IS NULL;`
   An unconsumed Trader link sitting in someone's inbox must not silently
   become full control; the Admin re-issues it if it is still wanted.
   Then `UPDATE org_invites SET role = 'admin' WHERE role = 'trader';` for
   the consumed ones, so they satisfy the new constraint. (No `owner`
   invite rows can exist — invites never granted owner.)
3. Constraints: drop and re-add
   `org_memberships_role_check` as `CHECK (role IN ('admin', 'viewer', 'investor'))`
   and `org_invites_role_check` as the same set. The old names can never
   be written again.

No new tables, columns or indexes. The partial index that guarantees one
investor account per org is unaffected.

## 5. API

`api/src/api/rbac.py`:

    ROLE_RANK = {"investor": -1, "viewer": 0, "admin": 1}

`require_org_role` is unchanged in behaviour: non-members and unknown
orgs get 404, members below the minimum get 403.

Route minimums that change (everything else already says `admin`,
`viewer` or `investor` and stays as is):

| Route | Was | Now |
|---|---|---|
| `POST orders`, `POST positions/close`, `POST positions/amend`, `POST orders/cancel` (`routes/trading.py`) | trader | admin |
| `GET webhook` (`routes/webhooks.py`) | trader | admin |
| `GET accounts/{id}/symbol-aliases` (`routes/mt5.py`) | trader | admin |
| `PATCH /api/orgs/{id}`, `DELETE /api/orgs/{id}`, `PATCH members/{uid}` (`routes/orgs.py`) | owner | admin |

`routes/orgs.py` logic:

- `POST /api/orgs` inserts the creator as `admin` and returns `"role": "admin"`.
- `PATCH members/{uid}`: the locked set is `role = 'admin'`; refusing to
  demote the last Admin. `body.role` must be one of the three.
- `DELETE members/{uid}`: an Admin may remove anyone; anyone may remove
  themselves; the last Admin is refused. The check `ctx.role != "owner"`
  becomes `ctx.role != "admin"`.
- `POST invites`: accepted roles are `admin`, `viewer`, `investor`.

`auth.py` bootstrap (`ensure_bootstrap_user`) inserts `'admin'` instead of
`'owner'` when it claims the legacy Default org. `GET /api/me` needs no
change; it already returns whatever role the row holds.

`ws.py` needs no change: it refuses `investor` and admits everything
else.

The copier is untouched. It never reads `org_memberships`.

## 6. Dashboard

`src/lib/roles.ts`:

- `Role = 'investor' | 'viewer' | 'admin'`; `RANK` matches the server.
- The three `Action`s stay (`trade`, `control`, `manage_members`) so no
  call site changes; every threshold is `RANK.admin`.
- `ROLE_LABEL`: Admin, Viewer, Investor. The "Admin (deputy)" and
  "Trader" labels go.
- `OFFERED_ROLES = ['admin', 'viewer', 'investor']`.

`src/pages/Members.tsx`:

- The role `<select>` shows for every member **except the signed-in
  user's own row** (today it hides the owner's row because the owner was
  fixed). The server's last-Admin `409` is shown as the inline error the
  page already has for failed role changes. Self-demotion is therefore
  not offered in the UI; the API still allows it when another Admin
  exists, and refuses it with `409` when it would leave none.
- Invite picker offers the three roles.
- Remove / Leave buttons keep their current rule; a last Admin trying to
  leave sees the server's `409` message.

`src/pages/Welcome.tsx` and the org switcher only display `roleLabel()`
and need no change beyond the label table. Navigation and page gates
already key off `can()` and need no edits.

## 7. Safety and audit

- Nothing in this change widens what a Viewer or Investor can do.
- The only privilege *increase* is for existing Trader and deputy-Admin
  rows, which become full Admins. The owner confirmed these are the same
  person today; the migration is the deliberate act, not an accident.
- Unconsumed Trader invites are revoked rather than upgraded.
- The last-Admin guard keeps every org reachable. Concurrency handling is
  the existing `FOR UPDATE` lock, renamed.

## 8. Testing

API (`api/tests`, real Postgres, conftest applies every migration):

- New `test_migration_020.py`: `owner` and `trader` membership rows are
  `admin` afterwards; an unconsumed `trader` invite is gone and a consumed
  one reads `admin`; inserting `owner` or `trader` into either table raises
  `CheckViolation`; `020` is recorded right after `019`.
- Seeds: every `make_org(members=[(u, "owner")])` / `"trader"` in the
  suite becomes `"admin"`. Tests that specifically asserted a Trader could
  trade but not control, or that an Admin could not rename the org, are
  rewritten as Admin-can or deleted where the distinction no longer exists.
- `test_rbac_matrix.py`: Trader-only and Owner-only rows become Admin
  rows; the Viewer-refused rows stay; the Investor-refused rows stay.
- `test_orgs.py`: last-Admin demotion/removal/leave refused; an Admin can
  promote a Viewer to Admin and invite an Admin; invite role validation
  rejects `owner` and `trader`.
- `test_auth.py`: bootstrap claims the Default org as `admin`.

Dashboard (`npm test`):

- `roles.test.ts` rewritten for three roles.
- Every page test that mocks `useOrg('owner')` or `'trader'` mocks
  `'admin'` instead.
- `Members.test.tsx`: role picker present on other rows and absent on the
  self row; invite picker offers the three roles; the `409` message shows.

Compose e2e: `e2e/test_full_stack.py` seeds `'admin'` and asserts
`/api/me` reports `admin`.

## 9. Docs

- README: the roles table and every mention of Owner / Trader rewritten
  for the three-role model; the invite paragraph updated.
- `2026-08-18-multi-org-rbac-design.md`: a one-line note at the top
  pointing here for the current role set. History is not rewritten.

## 10. Rollout

1. Merge to `main`, push.
2. On the host: `docker compose build migrate api && docker compose run
   --rm migrate && docker compose up -d api`. The migrate image bakes in
   `db/migrations/`, so building only `api` would skip 020 (standing
   rule).
3. Verify: `SELECT role, count(*) FROM org_memberships GROUP BY role`
   shows only `admin`, `viewer`, `investor`; the Members page lists the
   desk owner as Admin with a working role picker on other rows.
4. No copier restart needed.
