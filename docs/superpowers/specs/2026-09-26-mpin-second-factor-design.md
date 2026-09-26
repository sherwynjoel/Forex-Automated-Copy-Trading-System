# MPIN second factor after login — Design

**Date:** 2026-09-26
**Status:** implemented on branch mpin-second-factor (2026-09-26); rollout per §9
**Builds on:** `2026-08-18-multi-org-rbac-design.md` §3 (sessions) and
`2026-09-25-single-admin-role-design.md` (roles). Neither changes.

## 1. Goal

Every user, Admin and Investor alike, signs in with email and password and
then must enter a six-digit MPIN before reaching anything in the platform.
The MPIN is a second gate on the same session: without it the API answers
nothing but "MPIN required", and the dashboard shows nothing but the MPIN
screen. A user who has never set an MPIN is made to set one on that first
login and cannot skip it. Five wrong tries lock the step for fifteen
minutes; a forgotten MPIN is reset by proving the password again. The MPIN
is asked for on every new login only.

## 2. Non-goals (owner-confirmed)

- Re-prompting after idle time, or before risky actions (close-all, stop
  copying, withdrawals). Every new login only.
- Admin-side reset of another member's MPIN. Recovery is self-service
  through the password.
- MPIN as a password replacement on a trusted device.
- A server-side sessions table. Sessions stay stateless signed cookies.
- TOTP, SMS or email codes.

## 3. Session model

The signed cookie payload today is `{"user_id", "sv"}`. It gains `"pin"`:

| Cookie | Issued by | Accepted by |
|---|---|---|
| `pin: false` — half session | `POST /api/login`, `POST /api/register` | the MPIN routes, `POST /api/logout`, and `GET /api/me` in its trimmed form |
| `pin: true` — full session | `POST /api/mpin/set`, `/verify`, `/reset` | everything, as today |

- `_issue_session(response, cfg, user_id, session_version, pin)` writes the
  flag; the CSRF cookie is re-issued alongside every time, so a half
  session can make the CSRF-protected MPIN calls.
- `_unpack_session` returns `(user_id, version, pin)`; a cookie without the
  field (issued before this ships) reads as `pin: false`, so every existing
  session re-verifies once after the deploy.
- `require_user` keeps its signature and return value. It now raises
  **401 `MPIN required`** for a half session, after the existing
  `session_version` check. Every existing route is therefore gated with no
  per-route edit, and the RBAC dependency (`require_org_role` builds on
  `require_user`) inherits it.
- New `require_half_session` accepts either state and returns
  `(user_id, session_version, pin)`. Only the MPIN routes, logout and
  `/api/me` use it.
- `ws.py`: `session_identity` returns the flag; the handshake closes with
  **4401** for a half session, the same code it uses for no session.
- Password change and "sign out everywhere" still bump `session_version`,
  and so do MPIN reset and MPIN change (which re-issue the caller's cookie);
  the next login is a fresh half session, so the MPIN is asked again.
  Nothing else about sessions changes (12-hour age, SameSite=Lax, Secure).

## 4. Data (migration `021_mpin.sql`)

    ALTER TABLE users
        ADD COLUMN mpin_hash            TEXT,
        ADD COLUMN mpin_failed_attempts INTEGER NOT NULL DEFAULT 0,
        ADD COLUMN mpin_locked_until    TIMESTAMPTZ,
        ADD COLUMN mpin_set_at          TIMESTAMPTZ;

- `mpin_hash` is argon2 via the same `PasswordHasher` used for passwords
  (`hash_password` / `verify_password`). NULL means "never set".
- The lock state lives here, per user, so it survives api restarts and is
  shared across workers, unlike the in-memory login rate limiter.
- No new tables or indexes. The compose e2e `TRUNCATE` list and the api
  test `db` fixture need no change (they truncate `users`).

## 5. API (`api/src/api/auth.py`, plus a new `mpin.py` router)

Validation shared by every route that takes an MPIN: exactly six ASCII
digits (`^[0-9]{6}$`), else **400 `MPIN must be exactly 6 digits`**; where a
confirmation is sent it must match, else **400 `MPINs do not match`**.

| Route | Session | Behaviour |
|---|---|---|
| `GET /api/me` | half or full | Half: `{"mpin": {"pending": true, "set": <bool>}}` and nothing else. Full: today's `{user, orgs}` plus `"mpin": {"pending": false, "set": true}`. |
| `POST /api/mpin/set` `{mpin, mpin_confirm}` | half | **409 `MPIN already set`** if `mpin_hash` is not NULL. Otherwise hash, set `mpin_set_at = now()`, reset attempts and lock, issue a full session, audit `mpin_set`. 204. |
| `POST /api/mpin/verify` `{mpin}` | half | Reserve a try first: one `UPDATE ... SET mpin_failed_attempts += 1` guarded by `mpin_hash IS NOT NULL`, no active lock and `mpin_failed_attempts < 5`. No row reserved: **409 `MPIN not set`** if the hash is NULL, otherwise **423 `MPIN locked`** with `{"locked_until": <iso>}` (writing the lock first if a burst reached the cap before one existed). Row reserved: verify the hash. Wrong: if the reserved count reached **5**, set `mpin_locked_until = now() + 15 min`, reset attempts to 0, audit `mpin_locked`, answer 423 with the timestamp; otherwise **401 `Invalid MPIN`** with `{"attempts_left": n}`. Right: reset attempts, issue a full session, 204. |
| `POST /api/mpin/reset` `{password, mpin, mpin_confirm}` | half | The login rate limiter's per-credential bucket applies (`login:{email}:{ip}`). Verify the password (401 `Invalid password` on failure, with the dummy-hash timing equaliser). Set the new hash and `mpin_set_at`, clear attempts and lock, bump `session_version` and close the user's WebSockets (every other session is signed out), issue a full session with the new version, audit `mpin_reset`. 204. Works while locked: that is its purpose. |
| `POST /api/me/mpin` `{current_mpin, mpin, mpin_confirm}` | full | Refused with the same **423** while `mpin_locked_until` is in the future. Verify the current MPIN (401 `Invalid MPIN`, counting toward the same lock and locking on the 5th failure exactly like verify), set the new hash and `mpin_set_at`, clear attempts, bump `session_version` and close the user's WebSockets (every other session is signed out), re-issue this browser's full session with the new version, audit `mpin_changed`. 204. |

Timing: verify and reset run exactly one argon2 verification on every path
(wrong, locked, not set), the way login does, so responses do not reveal
state by duration. The try is reserved before the verify, in one UPDATE
guarded by the lock and the cap (`mpin_failed_attempts < 5`), so a burst of
concurrent guesses gets at most five verifies per lock window; a call that
finds the cap reached with no lock written starts the lock itself (423).

CSRF: the `/api/mpin/*` routes are ordinary mutations and are NOT added to
`CSRF_EXEMPT_PREFIXES`; the half session already carries a CSRF cookie.

Audit events (`events` table, category `auth`, `account_id` NULL, `org_id`
NULL, payload `{action, user_id}`): `mpin_set`, `mpin_reset`, `mpin_changed`,
`mpin_locked`. Each is inserted into the `events` table with `org_id` NULL
(category `auth`) and also written to the api log -- `mpin_locked` and
`mpin_reset` at WARNING, `mpin_set` and `mpin_changed` at INFO -- so they are
visible in `docker compose logs api`. Being org-less they do not appear in
any org's events feed, which is acceptable for account-level security events.

Rate limiting the verify route itself is unnecessary: the per-user lock is
the limiter, and an attacker without the password never reaches it.

## 6. Dashboard

**Routing.** `lib/api.ts`: a 401 whose body detail is `MPIN required`
redirects to `/mpin` (not `/login`), unless the caller passed
`redirectOn401: false`. `RootRedirect` and `OrgProvider` read `/api/me`;
when `mpin.pending` is true they navigate to `/mpin` before anything else.
`Login` keeps navigating to `/`. `Register` navigates to
`/mpin?next=/join/<token>` when it carried an invite and to
`/mpin?next=/welcome` otherwise, and no longer tries the join itself; `Join`
sends a half session to `/mpin?next=/join/<token>`; `Welcome` sends a half
session to `/mpin`. The `/mpin` page honours `next` only when it is a
same-origin path (starts with a single `/`), defaulting to `/`. The `Me` type gains `mpin: { pending:
boolean; set: boolean }`, and the half-session shape is a separate
`MpinPending` type so no page can mistake it for a signed-in user.

**The `/mpin` page** (`pages/Mpin.tsx`), one screen with three modes chosen
from `/api/me`:

- **Set** (`set: false`): "Choose your MPIN" — six-digit entry twice
  (MPIN and confirm), `POST /api/mpin/set`, then `/`.
- **Verify** (`set: true`): "Enter your MPIN" — six-digit entry, submits on
  the sixth digit or Enter, `POST /api/mpin/verify`, then `/`. A 401 clears
  the boxes and shows "Wrong MPIN, N tries left". A 423 shows "Locked. Try
  again in mm:ss" with a live countdown from `locked_until`, the boxes
  disabled, and the Forgot link still active. A "Forgot MPIN?" link switches
  to Forgot mode.
- **Forgot**: password field plus MPIN and confirm, `POST /api/mpin/reset`,
  then `/`. Wrong password shows the inline error and stays.
- A "Sign out" ghost button on every mode calls `POST /api/logout` and goes
  to `/login`, so a stuck user is never trapped.
- If `/api/me` says `pending: false` the page navigates to `/`; if it 401s
  outright, to `/login`.

**MPIN entry control** (`components/PinInput.tsx`): six single-digit boxes
(`inputmode="numeric"`, `autocomplete="one-time-code"`, `pattern="[0-9]*"`),
auto-advance on a digit, Backspace moves back, paste of six digits fills
all, arrow keys move, the group carries a visible label and an
`aria-describedby` error. Digits are masked (`type="password"`) with a
"Show" toggle. Built from `Input` sizing tokens; the boxes use the `num`
style and the `field-line` edge.

**Account security** (`components/AccountSecurity.tsx`): a "Change MPIN"
form (current, new, confirm) calling `POST /api/me/mpin`, beside the
existing password form, with the same success and error banners.

Copy avoids "PIN code" and "OTP": the word is **MPIN** everywhere.

## 7. Security notes

- The MPIN is never logged, never echoed by any response, and never stored
  in browser storage; the page keeps it in component state only.
- Lock state is server-side per user; the client countdown is a courtesy
  and the server re-checks `mpin_locked_until` on every attempt.
- A half session cannot read or change anything except its own MPIN state:
  `require_user` fails closed with the specific detail, and the WebSocket
  refuses it. The RBAC matrix test gains a row proving a half session is
  refused on a desk route and on an investor route.
- Registration issues a half session exactly like login, so a brand-new
  account sets its MPIN before it can join an invite or open the desk; the
  invite token travels through `next` and the join completes afterwards.
- The bootstrap user created from env has no MPIN and sets one on first
  login like everyone else.

## 8. Testing

API (`api/tests/test_mpin.py`, plus small additions elsewhere):

- half session: every sampled route (`GET accounts`, `GET investor/summary`,
  `PUT settings`) answers 401 `MPIN required`; `/api/me` answers the trimmed
  shape; the WebSocket handshake closes 4401.
- set: refused with 409 when already set; validation 400s; success issues a
  full session (the next `/api/me` is the full shape) and writes `mpin_set`.
- verify: right → full session; wrong → 401 with `attempts_left` 4, 3, 2, 1;
  fifth wrong → 423 with `locked_until` and `mpin_locked` audited; while
  locked even the right MPIN → 423; after the window (set `mpin_locked_until`
  into the past in SQL) → success; not set → 409.
- reset: wrong password → 401 and the lock stays; right password → new MPIN
  works, lock cleared, `mpin_reset` audited; rate-limited by the login bucket.
- change (`/api/me/mpin`): needs a full session; wrong current → 401 and
  counts toward the lock; success → old MPIN fails, new works.
- password change bumps `session_version` → the next login is a half
  session again (extend the existing test).
- `test_rbac_matrix.py`: one row class asserting a half session is 401 on
  every matrix route; `conftest.login_as` gains an MPIN step so the rest of
  the suite keeps passing: `make_user` sets an MPIN (`123456`) and
  `login_as` verifies it after the password login.
- `test_migration_021.py`: columns exist with the right defaults; recorded
  after 020.

Dashboard (`Mpin.test.tsx`, `PinInput.test.tsx`, `AccountSecurity` and
routing tests):

- PinInput: typing advances, Backspace retreats, paste fills six, non-digits
  ignored, completing six digits fires `onComplete`, the error is announced.
- Mpin page: Set mode posts both values and navigates to `/`; Verify mode
  posts on the sixth digit, shows tries left on 401, shows the countdown and
  disables entry on 423, Forgot switches mode and posts password plus MPIN;
  Sign out works from every mode; `pending: false` bounces to `/`.
- `api.test.ts`: a 401 with `MPIN required` redirects to `/mpin`; any other
  401 still goes to `/login`.
- `App.test.tsx` and `org.test.tsx`: `mpin.pending` sends `/` and an org
  route to `/mpin`.
- `Register.test.tsx`: success navigates to `/mpin?next=…` (welcome, or the
  join path when an invite was carried). `Join.test.tsx`: a pending
  `/api/me` sends to `/mpin?next=/join/<token>`.
- Existing page tests mock `/api/me`; the mock helper gains `mpin:
  {pending: false, set: true}` so nothing else changes.

Compose e2e (`e2e/test_full_stack.py`, `test_multi_org.py`): after the
register-and-login step, `POST /api/mpin/set` with `123456` once, then
continue as today.

## 9. Rollout

1. Merge to `main`, push.
2. On the host: `docker compose build migrate api`, `docker compose stop
   api`, `docker compose run --rm migrate` (must print
   `021_mpin.sql`), `docker compose up -d api`. The copier is untouched.
3. Every user, including the bootstrapped `mirrorfleet@gmail.com`, is asked
   to set an MPIN on their next login. Open dashboard tabs get a 401 `MPIN
   required` on their next request and land on `/mpin`.
4. Verify: sign in, set the MPIN, sign out, sign in again and confirm the
   Verify screen appears before the desk; `SELECT count(*) FROM users WHERE
   mpin_hash IS NOT NULL` grows as people set theirs.
