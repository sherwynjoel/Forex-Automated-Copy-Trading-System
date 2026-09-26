# MPIN Second Factor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After email-and-password login (and after registration), every user must set or enter a six-digit MPIN before any API route or dashboard page opens; five wrong tries lock the step for fifteen minutes; a forgotten MPIN is reset by re-proving the password.

**Architecture:** The signed session cookie gains a `pin` flag. Login and register issue a half session (`pin: false`); the MPIN routes re-issue it as a full session (`pin: true`). `require_user` refuses half sessions with 401 `MPIN required`, so every existing route is gated with no per-route edits; the WebSocket handshake refuses them too. MPIN state (argon2 hash, failed attempts, lock-until) lives on the `users` row. The dashboard reads a `mpin.pending` flag from `/api/me`, routes to a new `/mpin` page (Set / Verify / Forgot modes), and its API client turns a 401 `MPIN required` into a redirect there.

**Tech Stack:** Python 3.12 / FastAPI / psycopg 3 / argon2-cffi / itsdangerous / pytest against real Postgres; React 18 / TypeScript strict / react-router 7 / vitest + Testing Library; plain SQL migrations applied by `db/migrate.py`.

**Spec:** `docs/superpowers/specs/2026-09-26-mpin-second-factor-design.md` (read it first; it is the authority).

## Global Constraints

- Branch `mpin-second-factor` (already created off `main` at 81bdd73; the spec commits are on it). Commit on it; do not create other branches.
- MPIN: exactly six ASCII digits, regex `^[0-9]{6}$`. Validation text: `MPIN must be exactly 6 digits`; mismatch text: `MPINs do not match`.
- Gate answer for a half session: HTTP **401** with detail exactly `MPIN required`. Lock: after **5** wrong tries, **15 minutes**; locked answer is **423** with body `{"detail": "MPIN locked", "locked_until": "<iso8601>"}`. Wrong-try answer: **401** `Invalid MPIN` with `attempts_left`.
- Cookie payload keys: `user_id`, `sv`, `pin` (boolean). A cookie without `pin` reads as `false`.
- Test MPIN used by fixtures everywhere: `123456`.
- The word is **MPIN** in every user-facing string (never "PIN code", never "OTP").
- API tests need Docker Desktop running and `docker compose up -d postgres`. From `api/` in Git Bash (password from repo-root `.env`, key `POSTGRES_PASSWORD`; use `127.0.0.1`, never `localhost`):

  ```bash
  export TEST_POSTGRES_ADMIN_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader"
  export TEST_POSTGRES_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader_test"
  export PYTHONPATH="$(pwd -W)/src"
  .venv/Scripts/python -m pytest <files> -q -p no:cacheprovider
  ```

  On this Windows host 7 `test_events_ws.py` tests error (psycopg async cannot use the ProactorEventLoop) and one EA-download test fails on CRLF; both are pre-existing and are proven green in the Linux api container. Do not try to fix them.
- Dashboard: `npm test` from `dashboard/` runs the palette prover, `tsc --noEmit -p tsconfig.app.json` and `vitest run`; a single file is `npx vitest run <path>`. Use the design-system primitives (`Button`, `Input`, `Badge`, `Banner`) for every control; never hand-write button or input class recipes.
- Never touch `copier/`.
- Every commit message ends with these two lines:

  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b
  ```

---

## File map

| File | Change |
|---|---|
| `db/migrations/021_mpin.sql` (new) | four `users` columns |
| `api/tests/test_migration_021.py` (new) | column shape, ordering |
| `api/src/api/auth.py` | `pin` in the cookie; `SessionInfo`; `require_half_session`; `require_user` gate; trimmed `/api/me`; login/register issue half sessions |
| `api/src/api/routes/mpin.py` (new) | `/api/mpin/set`, `/verify`, `/reset`, `/api/me/mpin` |
| `api/src/api/main.py` | mount the MPIN router |
| `api/src/api/ws.py` | refuse half sessions |
| `api/tests/conftest.py` | `make_user(mpin=…)`, `login_as` verifies the MPIN |
| `api/tests/test_mpin.py` (new), `test_auth.py`, `test_events_ws.py`, `test_orgs.py`, `test_rbac_matrix.py` | behaviour tests and fixture fallout |
| `e2e/test_full_stack.py` | `_register_owner` sets the MPIN |
| `dashboard/src/lib/types.ts`, `lib/api.ts`, `App.tsx`, `lib/org.tsx`, `pages/Register.tsx`, `pages/Join.tsx`, `pages/Welcome.tsx` (+tests) | pending-state routing |
| `dashboard/src/components/PinInput.tsx` (+test) (new) | six-box masked entry |
| `dashboard/src/pages/Mpin.tsx` (+test) (new) | Set / Verify / Forgot |
| `dashboard/src/components/AccountSecurity.tsx` (+ `Members.test.tsx`) | Change MPIN form |
| `README.md` | one paragraph on the MPIN |

---

### Task 1: Migration 021 — MPIN columns on `users`

**Files:**
- Create: `db/migrations/021_mpin.sql`
- Create: `api/tests/test_migration_021.py`

**Interfaces:**
- Produces: columns `users.mpin_hash TEXT NULL`, `users.mpin_failed_attempts INTEGER NOT NULL DEFAULT 0`, `users.mpin_locked_until TIMESTAMPTZ NULL`, `users.mpin_set_at TIMESTAMPTZ NULL`.

- [ ] **Step 1: Write the failing test**

Create `api/tests/test_migration_021.py`:

```python
# api/tests/test_migration_021.py
"""Migration 021: MPIN state on users. conftest applies EVERY migration, so
these assert the post-migration shape."""
import psycopg


def test_migration_021_is_recorded_right_after_020(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "021_mpin.sql" in names
    assert names.index("021_mpin.sql") == names.index("020_single_admin.sql") + 1


def test_users_gain_the_four_mpin_columns_with_safe_defaults(db, make_user):
    user = make_user(mpin=None)
    with psycopg.connect(db, autocommit=True) as conn:
        cols = dict(conn.execute(
            """SELECT column_name, is_nullable FROM information_schema.columns
               WHERE table_name = 'users' AND column_name LIKE 'mpin_%'""").fetchall())
        assert cols == {"mpin_hash": "YES", "mpin_failed_attempts": "NO",
                        "mpin_locked_until": "YES", "mpin_set_at": "YES"}
        row = conn.execute(
            "SELECT mpin_hash, mpin_failed_attempts, mpin_locked_until, mpin_set_at "
            "FROM users WHERE id = %s", (user["id"],)).fetchone()
    assert row == (None, 0, None, None)
```

Note: `make_user(mpin=None)` is added in Task 3. Until then this test file uses the default `make_user()`; write it with `make_user()` now and change it to `make_user(mpin=None)` in Task 3 (Task 3's Step 9 says so).

- [ ] **Step 2: Run it to verify it fails**

Run (from `api/`): `.venv/Scripts/python -m pytest tests/test_migration_021.py -q -p no:cacheprovider`
Expected: both tests FAIL (`021_mpin.sql` not in names; the columns query returns `{}`).

- [ ] **Step 3: Write the migration**

Create `db/migrations/021_mpin.sql`:

```sql
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
```

- [ ] **Step 4: Run the test to verify it passes**

The session fixture recreates `copytrader_test` and applies every migration in a fresh pytest process.

Run: `.venv/Scripts/python -m pytest tests/test_migration_021.py tests/test_migration_020.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/021_mpin.sql api/tests/test_migration_021.py
git commit -m "feat(db): migration 021 -- MPIN hash, attempt counter and lock on users

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 2: Session plumbing — the `pin` flag, without enforcing it yet

Login and register start issuing half sessions and every reader understands the flag, but `require_user` does not refuse yet (that is Task 4), so the suite stays green.

**Files:**
- Modify: `api/src/api/auth.py` (`_unpack_session`, `require_user`, `session_identity`, `_issue_session`, `register`, `login`, `change_password`)
- Modify: `api/src/api/ws.py:347-350`
- Modify: `api/tests/test_auth.py` (append tests)

**Interfaces:**
- Produces: `_issue_session(response, cfg, user_id, session_version=0, *, pin: bool)`; `_unpack_session(...) -> Optional[tuple[int, int, bool]]`; `session_identity(...) -> Optional[tuple[int, int, bool]]`; `SessionInfo(user_id: int, session_version: int, pin: bool)`; `require_half_session(...) -> SessionInfo` (FastAPI dependency); `require_user` unchanged signature, still returns `int`.

- [ ] **Step 1: Write the failing tests**

Append to `api/tests/test_auth.py`:

```python
def _cookie_payload(client):
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer("test-secret", salt="session").loads(client.cookies["session"])


def test_login_issues_a_half_session(app_client, make_user):
    make_user(email="half@example.com", password="a-solid-password")
    r = app_client.post("/api/login", json={
        "email": "half@example.com", "password": "a-solid-password"})
    assert r.status_code == 204
    assert _cookie_payload(app_client)["pin"] is False


def test_register_issues_a_half_session(app_client):
    r = app_client.post("/api/register", json={
        "email": "new@example.com", "password": "correct-horse", "display_name": "N"})
    assert r.status_code == 204
    assert _cookie_payload(app_client)["pin"] is False


def test_a_cookie_without_the_pin_field_reads_as_half(app_client):
    from api.auth import _unpack_session
    from api.config import ApiConfig
    from itsdangerous import URLSafeTimedSerializer
    old = URLSafeTimedSerializer("test-secret", salt="session").dumps({"user_id": 7, "sv": 2})
    assert _unpack_session(old, ApiConfig.from_env()) == (7, 2, False)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_auth.py -q -p no:cacheprovider -k "half or pin_field"`
Expected: FAIL — `KeyError: 'pin'` on the first two, and the third gets a 2-tuple.

- [ ] **Step 3: Thread the flag through auth.py**

In `api/src/api/auth.py`:

Add after the `from .db import get_conn` import block:

```python
from dataclasses import dataclass
```

Replace `_unpack_session`:

```python
def _unpack_session(session: str, cfg: ApiConfig) -> Optional[tuple[int, int, bool]]:
    """(user_id, session_version, pin) from a signed cookie, or None if the
    signature is bad or it has aged out. Cookies issued before the version
    existed read as version 0; cookies issued before the MPIN existed read
    as pin=False, so every session re-verifies once after that deploy."""
    serializer = get_session_serializer(cfg)
    try:
        data = serializer.loads(session, max_age=SESSION_MAX_AGE_S)
    except (SignatureExpired, BadSignature):
        return None
    user_id = data.get("user_id")
    if not isinstance(user_id, int):
        return None
    version = data.get("sv")
    pin = data.get("pin")
    return user_id, (version if isinstance(version, int) else 0), pin is True
```

Add, directly above `require_user`:

```python
@dataclass(frozen=True)
class SessionInfo:
    user_id: int
    session_version: int
    pin: bool


def require_half_session(
    session: Optional[str] = Cookie(None),
    cfg: ApiConfig = Depends(ApiConfig.from_env),
    conn: psycopg.Connection = Depends(get_conn),
) -> SessionInfo:
    """Dependency for the few routes a user may reach BEFORE the MPIN gate:
    the MPIN routes themselves, logout, and the trimmed /api/me. Everything
    else goes through require_user, which insists on pin=True."""
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    unpacked = _unpack_session(session, cfg)
    if unpacked is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id, version, pin = unpacked
    if not session_version_matches(conn, user_id, version):
        raise HTTPException(status_code=401, detail="Session expired")
    return SessionInfo(user_id, version, pin)
```

Replace the body of `require_user` so it reuses that check (keep its signature and return type):

```python
def require_user(
    session: Optional[str] = Cookie(None),
    cfg: ApiConfig = Depends(ApiConfig.from_env),
    conn: psycopg.Connection = Depends(get_conn),
) -> int:
    """Dependency: the authenticated user's id from the session cookie."""
    info = require_half_session(session, cfg, conn)
    return info.user_id
```

Replace `session_identity`:

```python
def session_identity(session: str, cfg: ApiConfig) -> Optional[tuple[int, int, bool]]:
    """(user_id, session_version, pin) for callers outside Depends (the
    WebSocket), which must check the version against the database
    themselves -- they already hold a connection for the membership read."""
    return _unpack_session(session, cfg)
```

Replace `_issue_session`:

```python
def _issue_session(response, cfg: ApiConfig, user_id: int, session_version: int = 0,
                   *, pin: bool):
    """Set a fresh session + CSRF cookie pair (login-time re-issue prevents
    session fixation).

    The user's session_version is baked into the cookie so the server can
    disown it later: bumping the column invalidates every cookie already
    issued for that user (see require_user). `pin` records whether the MPIN
    gate has been passed: login and register issue pin=False (a half
    session), the MPIN routes re-issue pin=True.
    """
    serializer = URLSafeTimedSerializer(cfg.session_secret, salt="session")
    session_cookie = serializer.dumps({"user_id": user_id, "sv": session_version, "pin": pin})
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie("session", session_cookie, httponly=True, samesite="lax",
                        secure=cfg.cookie_secure, max_age=SESSION_MAX_AGE_S)
    response.set_cookie("csrf", csrf_token, httponly=False, samesite="lax",
                        secure=cfg.cookie_secure, max_age=SESSION_MAX_AGE_S)
```

Update the three callers:
- in `register` (the `_issue_session(response, cfg, user_id, …)` call after the INSERT): add `pin=False`.
- in `login`: `_issue_session(response, cfg, row[0], row[2], pin=False)`.
- in `change_password`: `_issue_session(response, cfg, user_id, new_version, pin=True)` (the caller already passed the MPIN to reach this route).

- [ ] **Step 4: Update the WebSocket reader**

In `api/src/api/ws.py` replace the line `user_id, session_ver = identity` with:

```python
        user_id, session_ver, _pin_verified = identity
```

(The refusal of half sessions is added in Task 4.)

- [ ] **Step 5: Run the auth tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_auth.py tests/test_orgs.py -q -p no:cacheprovider`
Expected: all pass (the new three included; nothing is gated yet).

- [ ] **Step 6: Commit**

```bash
git add api/src/api/auth.py api/src/api/ws.py api/tests/test_auth.py
git commit -m "feat(api): session cookie carries a pin flag; login and register issue half sessions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 3: The MPIN routes and the test fixtures that use them

**Files:**
- Create: `api/src/api/routes/mpin.py`
- Modify: `api/src/api/main.py:143-145` (mount after the auth router)
- Modify: `api/tests/conftest.py` (`make_user`, `login_as`)
- Create: `api/tests/test_mpin.py`
- Modify: `api/tests/test_migration_021.py` (use `make_user(mpin=None)`)

**Interfaces:**
- Consumes: `require_half_session -> SessionInfo`, `require_user -> int`, `_issue_session(..., pin=True)`, `hash_password`, `verify_password`, `_DUMMY_HASH`, `LoginRateLimiter`, `get_client_ip`, `_is_proxy_address` (all in `api.auth`).
- Produces: routes `POST /api/mpin/set`, `POST /api/mpin/verify`, `POST /api/mpin/reset`, `POST /api/me/mpin`; constants `MPIN_MAX_ATTEMPTS = 5`, `MPIN_LOCK_MINUTES = 15`; fixture `make_user(email=..., password=..., display_name=..., mpin="123456")` (pass `mpin=None` for a user with no MPIN); `login_as(client, user)` completes the MPIN step when the user has one.

- [ ] **Step 1: Update the fixtures**

In `api/tests/conftest.py` replace `make_user`:

```python
@pytest.fixture
def make_user(db):
    """Create a user directly in the DB; returns {id, email, password,
    display_name, mpin}. Every fixture user has the test MPIN 123456 unless
    mpin=None is passed (a user who has never set one)."""
    from api.auth import hash_password

    def _make(email="user@example.com", password="a-solid-password", display_name="User",
              mpin="123456"):
        with psycopg.connect(db, autocommit=True) as conn:
            (user_id,) = conn.execute(
                "INSERT INTO users (email, password_hash, display_name, mpin_hash, mpin_set_at) "
                "VALUES (%s, %s, %s, %s, CASE WHEN %s IS NULL THEN NULL ELSE now() END) "
                "RETURNING id",
                (email, hash_password(password), display_name,
                 hash_password(mpin) if mpin else None, mpin),
            ).fetchone()
        return {"id": user_id, "email": email, "password": password,
                "display_name": display_name, "mpin": mpin}

    return _make
```

Replace `login_as`:

```python
@pytest.fixture
def login_as():
    """Log a TestClient in as a make_user() user and pass the MPIN gate
    (sets session+csrf cookies twice: half session, then full)."""

    def _login(client, user):
        r = client.post("/api/login", json={
            "email": user["email"], "password": user["password"]})
        assert r.status_code == 204, f"login failed: {r.status_code} {r.text}"
        mpin = user.get("mpin", "123456")
        if mpin:
            r = client.post("/api/mpin/verify", json={"mpin": mpin},
                            headers={"X-CSRF-Token": client.cookies.get("csrf")})
            assert r.status_code == 204, f"mpin verify failed: {r.status_code} {r.text}"

    return _login
```

Note: `test_orgs.py` calls `login_as(app_client, {"email": ..., "password": ...})` with plain dicts for registered users (no `mpin` key). `user.get("mpin", "123456")` then verifies `123456`, which fails for a registered user without an MPIN until Task 4 makes `_register` set one; Task 4 fixes those call sites. In this task, `test_orgs.py` will show failures on those three tests; that is expected and listed in Step 8.

- [ ] **Step 2: Write the failing route tests**

Create `api/tests/test_mpin.py`:

```python
"""MPIN routes: set, verify (with lock), reset via password, change."""
import psycopg


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("csrf")}


def _half_login(client, user):
    r = client.post("/api/login", json={"email": user["email"], "password": user["password"]})
    assert r.status_code == 204


def _payload(client):
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer("test-secret", salt="session").loads(client.cookies["session"])


def _events(db, action):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT payload FROM events WHERE category = 'auth' AND payload->>'action' = %s",
            (action,)).fetchall()


# ---------- set ----------

def test_set_needs_six_digits_that_match(app_client, make_user):
    user = make_user(mpin=None)
    _half_login(app_client, user)
    r = app_client.post("/api/mpin/set", json={"mpin": "12345", "mpin_confirm": "12345"},
                        headers=_csrf(app_client))
    assert r.status_code == 400 and r.json()["detail"] == "MPIN must be exactly 6 digits"
    r = app_client.post("/api/mpin/set", json={"mpin": "12345a", "mpin_confirm": "12345a"},
                        headers=_csrf(app_client))
    assert r.status_code == 400 and r.json()["detail"] == "MPIN must be exactly 6 digits"
    r = app_client.post("/api/mpin/set", json={"mpin": "123456", "mpin_confirm": "654321"},
                        headers=_csrf(app_client))
    assert r.status_code == 400 and r.json()["detail"] == "MPINs do not match"


def test_set_stores_a_hash_issues_a_full_session_and_audits(app_client, make_user, db):
    user = make_user(mpin=None)
    _half_login(app_client, user)
    assert _payload(app_client)["pin"] is False
    r = app_client.post("/api/mpin/set", json={"mpin": "246810", "mpin_confirm": "246810"},
                        headers=_csrf(app_client))
    assert r.status_code == 204
    assert _payload(app_client)["pin"] is True
    with psycopg.connect(db, autocommit=True) as conn:
        h, set_at = conn.execute(
            "SELECT mpin_hash, mpin_set_at FROM users WHERE id = %s", (user["id"],)).fetchone()
    assert h and h != "246810" and set_at is not None
    assert len(_events(db, "mpin_set")) == 1


def test_set_is_refused_once_an_mpin_exists(app_client, make_user):
    user = make_user()
    _half_login(app_client, user)
    r = app_client.post("/api/mpin/set", json={"mpin": "111111", "mpin_confirm": "111111"},
                        headers=_csrf(app_client))
    assert r.status_code == 409 and r.json()["detail"] == "MPIN already set"


# ---------- verify ----------

def test_verify_right_mpin_issues_a_full_session(app_client, make_user):
    user = make_user()
    _half_login(app_client, user)
    r = app_client.post("/api/mpin/verify", json={"mpin": "123456"}, headers=_csrf(app_client))
    assert r.status_code == 204
    assert _payload(app_client)["pin"] is True


def test_verify_counts_wrong_tries_then_locks_for_fifteen_minutes(app_client, make_user, db):
    user = make_user()
    _half_login(app_client, user)
    for left in (4, 3, 2, 1):
        r = app_client.post("/api/mpin/verify", json={"mpin": "000000"}, headers=_csrf(app_client))
        assert r.status_code == 401
        assert r.json() == {"detail": "Invalid MPIN", "attempts_left": left}
    r = app_client.post("/api/mpin/verify", json={"mpin": "000000"}, headers=_csrf(app_client))
    assert r.status_code == 423
    body = r.json()
    assert body["detail"] == "MPIN locked" and body["locked_until"]
    # Even the right MPIN is refused while locked.
    r = app_client.post("/api/mpin/verify", json={"mpin": "123456"}, headers=_csrf(app_client))
    assert r.status_code == 423
    assert _payload(app_client)["pin"] is False
    with psycopg.connect(db, autocommit=True) as conn:
        attempts, until = conn.execute(
            "SELECT mpin_failed_attempts, mpin_locked_until FROM users WHERE id = %s",
            (user["id"],)).fetchone()
        assert attempts == 0 and until is not None
        (mins,) = conn.execute(
            "SELECT EXTRACT(EPOCH FROM (mpin_locked_until - now())) / 60 FROM users WHERE id = %s",
            (user["id"],)).fetchone()
    assert 14 < float(mins) <= 15
    assert len(_events(db, "mpin_locked")) == 1


def test_verify_works_again_once_the_lock_has_passed(app_client, make_user, db):
    user = make_user()
    _half_login(app_client, user)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE users SET mpin_locked_until = now() - interval '1 second' WHERE id = %s",
                     (user["id"],))
    r = app_client.post("/api/mpin/verify", json={"mpin": "123456"}, headers=_csrf(app_client))
    assert r.status_code == 204


def test_a_right_mpin_resets_the_counter(app_client, make_user, db):
    user = make_user()
    _half_login(app_client, user)
    app_client.post("/api/mpin/verify", json={"mpin": "000000"}, headers=_csrf(app_client))
    app_client.post("/api/mpin/verify", json={"mpin": "123456"}, headers=_csrf(app_client))
    with psycopg.connect(db, autocommit=True) as conn:
        (attempts,) = conn.execute(
            "SELECT mpin_failed_attempts FROM users WHERE id = %s", (user["id"],)).fetchone()
    assert attempts == 0


def test_verify_without_an_mpin_is_409(app_client, make_user):
    user = make_user(mpin=None)
    _half_login(app_client, user)
    r = app_client.post("/api/mpin/verify", json={"mpin": "123456"}, headers=_csrf(app_client))
    assert r.status_code == 409 and r.json()["detail"] == "MPIN not set"


# ---------- reset (forgot) ----------

def test_reset_needs_the_password_and_clears_the_lock(app_client, make_user, db):
    user = make_user()
    _half_login(app_client, user)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE users SET mpin_locked_until = now() + interval '10 minutes' WHERE id = %s",
                     (user["id"],))
    r = app_client.post("/api/mpin/reset", json={
        "password": "wrong-password!", "mpin": "999999", "mpin_confirm": "999999"},
        headers=_csrf(app_client))
    assert r.status_code == 401 and r.json()["detail"] == "Invalid password"
    r = app_client.post("/api/mpin/reset", json={
        "password": user["password"], "mpin": "999999", "mpin_confirm": "999999"},
        headers=_csrf(app_client))
    assert r.status_code == 204
    assert _payload(app_client)["pin"] is True
    with psycopg.connect(db, autocommit=True) as conn:
        attempts, until = conn.execute(
            "SELECT mpin_failed_attempts, mpin_locked_until FROM users WHERE id = %s",
            (user["id"],)).fetchone()
    assert attempts == 0 and until is None
    assert len(_events(db, "mpin_reset")) == 1
    # The new MPIN is the one that works now.
    app_client.cookies.clear()
    _half_login(app_client, user)
    assert app_client.post("/api/mpin/verify", json={"mpin": "123456"},
                           headers=_csrf(app_client)).status_code == 401
    assert app_client.post("/api/mpin/verify", json={"mpin": "999999"},
                           headers=_csrf(app_client)).status_code == 204


def test_reset_is_rate_limited_like_login(app_client, make_user):
    user = make_user()
    _half_login(app_client, user)
    for _ in range(5):
        app_client.post("/api/mpin/reset", json={
            "password": "wrong-password!", "mpin": "999999", "mpin_confirm": "999999"},
            headers=_csrf(app_client))
    r = app_client.post("/api/mpin/reset", json={
        "password": user["password"], "mpin": "999999", "mpin_confirm": "999999"},
        headers=_csrf(app_client))
    assert r.status_code == 429


# ---------- change (signed in) ----------

def test_change_needs_the_current_mpin_and_a_full_session(app_client, make_user, login_as, db):
    user = make_user()
    _half_login(app_client, user)
    r = app_client.post("/api/me/mpin", json={
        "current_mpin": "123456", "mpin": "222222", "mpin_confirm": "222222"},
        headers=_csrf(app_client))
    assert r.status_code == 401  # half session cannot reach it
    login_as(app_client, user)
    r = app_client.post("/api/me/mpin", json={
        "current_mpin": "000000", "mpin": "222222", "mpin_confirm": "222222"},
        headers=_csrf(app_client))
    assert r.status_code == 401 and r.json()["detail"] == "Invalid MPIN"
    r = app_client.post("/api/me/mpin", json={
        "current_mpin": "123456", "mpin": "222222", "mpin_confirm": "222222"},
        headers=_csrf(app_client))
    assert r.status_code == 204
    assert len(_events(db, "mpin_changed")) == 1
    app_client.cookies.clear()
    _half_login(app_client, user)
    assert app_client.post("/api/mpin/verify", json={"mpin": "222222"},
                           headers=_csrf(app_client)).status_code == 204


def test_change_is_refused_while_locked(app_client, make_user, login_as, db):
    user = make_user()
    login_as(app_client, user)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE users SET mpin_locked_until = now() + interval '10 minutes' WHERE id = %s",
                     (user["id"],))
    r = app_client.post("/api/me/mpin", json={
        "current_mpin": "123456", "mpin": "222222", "mpin_confirm": "222222"},
        headers=_csrf(app_client))
    assert r.status_code == 423
```

Note on the half-session `/api/me/mpin` assertion: in this task `require_user` does not yet refuse half sessions, so that single line will fail until Task 4. Mark it now with a comment and expect exactly that one failure in Step 7; Task 4 turns it green.

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_mpin.py -q -p no:cacheprovider`
Expected: every test FAILS with 404 (the routes do not exist).

- [ ] **Step 4: Write the router**

Create `api/src/api/routes/mpin.py`:

```python
"""MPIN: the six-digit second factor every user passes after email+password.

Set once (first login), verified on every later login, reset by proving the
password, changed from Account security. The lock lives on the users row so
it survives restarts and is shared across workers; the verify and reset
paths do exactly one argon2 verification whatever the outcome, so timing
does not leak state.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..auth import (LoginRateLimiter, SessionInfo, _DUMMY_HASH, _is_proxy_address,
                    _issue_session, get_client_ip, hash_password, require_half_session,
                    require_user, verify_password)
from ..config import ApiConfig
from ..db import get_conn

logger = logging.getLogger(__name__)

MPIN_RE = re.compile(r"^[0-9]{6}$")
MPIN_MAX_ATTEMPTS = 5
MPIN_LOCK_MINUTES = 15


class SetRequest(BaseModel):
    mpin: str
    mpin_confirm: str


class VerifyRequest(BaseModel):
    mpin: str


class ResetRequest(BaseModel):
    password: str
    mpin: str
    mpin_confirm: str


class ChangeRequest(BaseModel):
    current_mpin: str
    mpin: str
    mpin_confirm: str


def _validate_pair(mpin: str, confirm: str) -> None:
    if not MPIN_RE.fullmatch(mpin):
        raise HTTPException(status_code=400, detail="MPIN must be exactly 6 digits")
    if mpin != confirm:
        raise HTTPException(status_code=400, detail="MPINs do not match")


def _audit(conn: psycopg.Connection, user_id: int, action: str) -> None:
    """Account-level security events carry no org; they reach the operator
    log, not an org's feed. Best-effort like the other audit writers."""
    try:
        conn.execute(
            "INSERT INTO events (org_id, account_id, category, severity, payload) "
            "VALUES (NULL, NULL, 'auth', 'info', %s)",
            (Jsonb({"action": action, "user_id": user_id}),))
    except Exception:
        logger.exception("failed to write mpin audit event %s", action)


def _locked_response(until: datetime) -> JSONResponse:
    return JSONResponse(status_code=423,
                        content={"detail": "MPIN locked", "locked_until": until.isoformat()})


def _lock_state(conn: psycopg.Connection, user_id: int) -> tuple[Optional[str], int, Optional[datetime]]:
    row = conn.execute(
        "SELECT mpin_hash, mpin_failed_attempts, mpin_locked_until FROM users WHERE id = %s",
        (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return row[0], row[1], row[2]


def _check_mpin(conn: psycopg.Connection, user_id: int, mpin: str) -> Optional[Response]:
    """One argon2 verify on every path. Returns None when the MPIN is right
    (and the counter has been reset), otherwise the error response to send:
    409 when no MPIN exists, 423 while locked, 401 with attempts_left."""
    mpin_hash, attempts, locked_until = _lock_state(conn, user_id)
    now = datetime.now(timezone.utc)
    if mpin_hash is None:
        verify_password(_DUMMY_HASH, mpin)
        return JSONResponse(status_code=409, content={"detail": "MPIN not set"})
    if locked_until is not None and locked_until > now:
        verify_password(_DUMMY_HASH, mpin)
        return _locked_response(locked_until)
    if verify_password(mpin_hash, mpin):
        conn.execute("UPDATE users SET mpin_failed_attempts = 0, mpin_locked_until = NULL "
                     "WHERE id = %s", (user_id,))
        return None
    attempts += 1
    if attempts >= MPIN_MAX_ATTEMPTS:
        until = now + timedelta(minutes=MPIN_LOCK_MINUTES)
        conn.execute("UPDATE users SET mpin_failed_attempts = 0, mpin_locked_until = %s "
                     "WHERE id = %s", (until, user_id))
        _audit(conn, user_id, "mpin_locked")
        return _locked_response(until)
    conn.execute("UPDATE users SET mpin_failed_attempts = %s WHERE id = %s", (attempts, user_id))
    return JSONResponse(status_code=401,
                        content={"detail": "Invalid MPIN",
                                 "attempts_left": MPIN_MAX_ATTEMPTS - attempts})


def _store(conn: psycopg.Connection, user_id: int, mpin: str) -> None:
    conn.execute(
        "UPDATE users SET mpin_hash = %s, mpin_set_at = now(), mpin_failed_attempts = 0, "
        "mpin_locked_until = NULL WHERE id = %s",
        (hash_password(mpin), user_id))


def create_mpin_router(rate_limiter: LoginRateLimiter) -> APIRouter:
    router = APIRouter(tags=["mpin"])

    @router.post("/api/mpin/set", status_code=204)
    async def set_mpin(body: SetRequest,
                       info: SessionInfo = Depends(require_half_session),
                       cfg: ApiConfig = Depends(ApiConfig.from_env),
                       conn: psycopg.Connection = Depends(get_conn)):
        _validate_pair(body.mpin, body.mpin_confirm)
        mpin_hash, _, _ = _lock_state(conn, info.user_id)
        if mpin_hash is not None:
            raise HTTPException(status_code=409, detail="MPIN already set")
        _store(conn, info.user_id, body.mpin)
        _audit(conn, info.user_id, "mpin_set")
        response = Response(status_code=204)
        _issue_session(response, cfg, info.user_id, info.session_version, pin=True)
        return response

    @router.post("/api/mpin/verify", status_code=204)
    async def verify_mpin(body: VerifyRequest,
                          info: SessionInfo = Depends(require_half_session),
                          cfg: ApiConfig = Depends(ApiConfig.from_env),
                          conn: psycopg.Connection = Depends(get_conn)):
        if not MPIN_RE.fullmatch(body.mpin):
            raise HTTPException(status_code=400, detail="MPIN must be exactly 6 digits")
        failure = _check_mpin(conn, info.user_id, body.mpin)
        if failure is not None:
            return failure
        response = Response(status_code=204)
        _issue_session(response, cfg, info.user_id, info.session_version, pin=True)
        return response

    @router.post("/api/mpin/reset", status_code=204)
    async def reset_mpin(body: ResetRequest, request: Request,
                         info: SessionInfo = Depends(require_half_session),
                         cfg: ApiConfig = Depends(ApiConfig.from_env),
                         conn: psycopg.Connection = Depends(get_conn)):
        """Forgot MPIN: prove the password, get a new MPIN. Works while
        locked -- that is its purpose -- and shares login's per-credential
        rate-limit bucket so it cannot be used to guess the password."""
        _validate_pair(body.mpin, body.mpin_confirm)
        row = conn.execute("SELECT email, password_hash FROM users WHERE id = %s",
                           (info.user_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        email, password_hash = row[0].lower(), row[1]
        client_ip = get_client_ip(request, trust_proxy=cfg.trust_proxy)
        if rate_limiter.is_limited(f"login:{email}:{client_ip}"):
            raise HTTPException(status_code=429, detail="Too many requests")
        if not _is_proxy_address(client_ip) and rate_limiter.is_limited(
                f"login-ip:{client_ip}", max_attempts=20):
            raise HTTPException(status_code=429, detail="Too many requests")
        if not verify_password(password_hash, body.password):
            raise HTTPException(status_code=401, detail="Invalid password")
        _store(conn, info.user_id, body.mpin)
        _audit(conn, info.user_id, "mpin_reset")
        response = Response(status_code=204)
        _issue_session(response, cfg, info.user_id, info.session_version, pin=True)
        return response

    @router.post("/api/me/mpin", status_code=204)
    async def change_mpin(body: ChangeRequest,
                          user_id: int = Depends(require_user),
                          conn: psycopg.Connection = Depends(get_conn)):
        _validate_pair(body.mpin, body.mpin_confirm)
        if not MPIN_RE.fullmatch(body.current_mpin):
            raise HTTPException(status_code=400, detail="MPIN must be exactly 6 digits")
        failure = _check_mpin(conn, user_id, body.current_mpin)
        if failure is not None:
            return failure
        _store(conn, user_id, body.mpin)
        _audit(conn, user_id, "mpin_changed")
        return Response(status_code=204)

    return router
```

Check that `get_client_ip` and `_is_proxy_address` are module-level in `api/src/api/auth.py` (they are, at ~lines 192 and 211). `LoginRateLimiter.is_limited(key, max_attempts=5)` is the existing signature.

- [ ] **Step 5: Mount the router**

In `api/src/api/main.py`, directly after `app.include_router(auth_router)`:

```python
    # MPIN second factor: set / verify / reset on a half session, change on a
    # full one. Shares the login rate limiter for the password-reset path.
    from .routes.mpin import create_mpin_router
    app.include_router(create_mpin_router(rate_limiter))
```

- [ ] **Step 6: Update the migration test to a user without an MPIN**

In `api/tests/test_migration_021.py` change `user = make_user()` to `user = make_user(mpin=None)` (the assertion expects `mpin_hash` NULL).

- [ ] **Step 7: Run the MPIN, auth and migration tests**

Run: `.venv/Scripts/python -m pytest tests/test_mpin.py tests/test_auth.py tests/test_migration_021.py -q -p no:cacheprovider`
Expected: all pass EXCEPT the single assertion `assert r.status_code == 401  # half session cannot reach it` in `test_change_needs_the_current_mpin_and_a_full_session` (it gets 204 because `require_user` is not yet gating). Everything else green. Do not weaken the test; Task 4 fixes it.

- [ ] **Step 8: Run the whole api suite to see the fixture fallout**

Run: `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider` (about 13 minutes)
Expected: green except (a) the one Task-4 assertion above, (b) the three `test_orgs.py` tests that call `login_as(app_client, {"email": "owner@example.com", "password": ...})` for a REGISTERED user (no MPIN row) — their verify step now fails with 409 — and (c) the pre-existing Windows-only failures. Record the exact failing names in your report; Task 4 resolves (a) and (b).

- [ ] **Step 9: Commit**

```bash
git add api/src/api/routes/mpin.py api/src/api/main.py api/tests/conftest.py api/tests/test_mpin.py api/tests/test_migration_021.py
git commit -m "feat(api): MPIN routes -- set, verify with a 5-try 15-minute lock, reset via password, change

Fixture users carry the test MPIN 123456 and login_as passes the gate.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 4: Enforce the gate — API, WebSocket, fixtures, e2e

**Files:**
- Modify: `api/src/api/auth.py` (`require_user`, `/api/me`, `logout`)
- Modify: `api/src/api/ws.py:347-350`
- Modify: `api/tests/test_auth.py`, `api/tests/test_orgs.py`, `api/tests/test_events_ws.py`, `api/tests/test_rbac_matrix.py`
- Modify: `e2e/test_full_stack.py:288-330`

**Interfaces:**
- Consumes: `SessionInfo`, `require_half_session`, the MPIN routes from Task 3.
- Produces: `require_user` raises 401 `MPIN required` on a half session; `GET /api/me` on a half session returns `{"mpin": {"pending": true, "set": <bool>}}`; on a full session adds `"mpin": {"pending": false, "set": true}`; WebSocket closes 4401 on a half session; `POST /api/logout` accepts either session.

- [ ] **Step 1: Write the failing tests**

Append to `api/tests/test_auth.py`:

```python
def test_a_half_session_is_refused_with_mpin_required(app_client, make_user):
    user = make_user()
    app_client.post("/api/login", json={"email": user["email"], "password": user["password"]})
    r = app_client.get("/api/orgs/1")
    assert r.status_code == 401 and r.json()["detail"] == "MPIN required"
    me = app_client.get("/api/me")
    assert me.status_code == 200
    assert me.json() == {"mpin": {"pending": True, "set": True}}


def test_me_reports_set_false_for_a_user_without_an_mpin(app_client, make_user):
    user = make_user(mpin=None)
    app_client.post("/api/login", json={"email": user["email"], "password": user["password"]})
    assert app_client.get("/api/me").json() == {"mpin": {"pending": True, "set": False}}


def test_a_full_session_reports_mpin_not_pending(app_client, make_user, login_as):
    user = make_user()
    login_as(app_client, user)
    me = app_client.get("/api/me").json()
    assert me["user"]["id"] == user["id"]
    assert me["mpin"] == {"pending": False, "set": True}


def test_logout_works_from_a_half_session(app_client, make_user):
    user = make_user()
    app_client.post("/api/login", json={"email": user["email"], "password": user["password"]})
    r = app_client.post("/api/logout", headers={"X-CSRF-Token": app_client.cookies.get("csrf")})
    assert r.status_code == 204
```

Append to `api/tests/test_rbac_matrix.py`:

```python
def test_a_half_session_is_refused_on_every_matrix_route(matrix_org):
    """Email+password alone opens nothing: before the MPIN every org route,
    desk or investor, answers 401 MPIN required."""
    client, org_id, users, _ = matrix_org
    admin = users["admin"]
    client.cookies.clear()
    r = client.post("/api/login", json={"email": admin["email"], "password": admin["password"]})
    assert r.status_code == 204
    for method, tail, body, _min_role in MATRIX:
        if method == "DELETE":
            continue  # destructive rows are proven denied by the 401 below on GET/POST too
        tail = tail.replace("{investor}", str(users["investor"]["id"]))
        r = _call(client, method, org_id, tail, body)
        assert r.status_code == 401, f"{method} {tail} -> {r.status_code}"
        assert r.json()["detail"] == "MPIN required", f"{method} {tail}"
```

In `api/tests/test_events_ws.py`, inside `test_ws_accepts_authenticated` (line ~262) add a second, negative case right after it:

```python
    def test_ws_refuses_a_half_session(self, app_client_with_lifespan, make_user, make_org):
        user = make_user()
        make_org(name="Half", members=[(user, "viewer")])
        app_client_with_lifespan.post("/api/login", json={
            "email": user["email"], "password": user["password"]})
        import pytest
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect) as exc:
            with app_client_with_lifespan.websocket_connect("/api/ws?org_id=1"):
                pass
        assert exc.value.code == 4401
```

(Look at how the neighbouring accept test builds its URL and org id and mirror it exactly; `org_id=1` is right under `RESTART IDENTITY`.)

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_auth.py tests/test_rbac_matrix.py -q -p no:cacheprovider -k "half or mpin_not_pending or set_false or logout_works"`
Expected: FAIL — `/api/orgs/1` answers 404 not 401, `/api/me` answers the full shape, the matrix rows pass instead of 401.

- [ ] **Step 3: Enforce in `require_user`**

In `api/src/api/auth.py` replace `require_user`:

```python
def require_user(
    session: Optional[str] = Cookie(None),
    cfg: ApiConfig = Depends(ApiConfig.from_env),
    conn: psycopg.Connection = Depends(get_conn),
) -> int:
    """Dependency: the authenticated user's id from the session cookie.

    Email+password alone is a half session: it may set or verify the MPIN,
    log out, and ask /api/me what it is -- nothing else. Every route that
    depends on this (directly or through require_org_role) is therefore
    behind the MPIN with no per-route edit.
    """
    info = require_half_session(session, cfg, conn)
    if not info.pin:
        raise HTTPException(status_code=401, detail="MPIN required")
    return info.user_id
```

- [ ] **Step 4: Trim `/api/me` and open logout to half sessions**

Replace the `/api/me` route in `auth.py`:

```python
    @router.get("/me")
    async def me(
        info: SessionInfo = Depends(require_half_session),
        conn: psycopg.Connection = Depends(get_conn),
    ):
        (mpin_set,) = conn.execute(
            "SELECT mpin_hash IS NOT NULL FROM users WHERE id = %s", (info.user_id,)
        ).fetchone() or (False,)
        if not info.pin:
            # A half session learns only what it must do next.
            return {"mpin": {"pending": True, "set": bool(mpin_set)}}
        row = conn.execute(
            "SELECT id, email, display_name FROM users WHERE id = %s", (info.user_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Not authenticated")
        orgs = conn.execute(
            """SELECT o.id, o.name, m.role FROM org_memberships m
               JOIN orgs o ON o.id = m.org_id WHERE m.user_id = %s ORDER BY o.id""",
            (info.user_id,),
        ).fetchall()
        return {
            "user": {"id": row[0], "email": row[1], "display_name": row[2]},
            "orgs": [{"id": o[0], "name": o[1], "role": o[2]} for o in orgs],
            "mpin": {"pending": False, "set": True},
        }
```

`logout` has no dependency today (it only deletes cookies), so it already works from a half session; leave it.

- [ ] **Step 5: Refuse half sessions on the WebSocket**

In `api/src/api/ws.py` replace the line from Task 2 and add the check:

```python
        user_id, session_ver, pin_verified = identity
        if not pin_verified:
            await ws.close(code=4401, reason="Unauthorized")
            return
```

- [ ] **Step 6: Fix the tests that log in directly and then read a gated route**

`api/tests/test_orgs.py`: `_register` registers a user (half session) and reads `/api/me`. Replace it with:

```python
def _register(client, email="owner@example.com", name="Owner"):
    r = client.post("/api/register", json={
        "email": email, "password": "a-solid-password", "display_name": name})
    assert r.status_code == 204
    r = client.post("/api/mpin/set", json={"mpin": "123456", "mpin_confirm": "123456"},
                    headers=_csrf(client))
    assert r.status_code == 204
    return client.get("/api/me").json()["user"]
```

The three `login_as(app_client, {"email": "owner@example.com", "password": "a-solid-password"})` calls in that file work unchanged now (the registered user has MPIN `123456`, and `login_as` verifies `123456` by default).

`api/tests/test_auth.py`: go through every test that posts `/api/login` or `/api/register` directly and afterwards reads `/api/me` or another gated route, and add the MPIN step between them:
- after a direct `/api/register`: `client.post("/api/mpin/set", json={"mpin": "123456", "mpin_confirm": "123456"}, headers={"X-CSRF-Token": client.cookies.get("csrf")})`
- after a direct `/api/login` of a `make_user` user: `client.post("/api/mpin/verify", json={"mpin": "123456"}, headers={"X-CSRF-Token": client.cookies.get("csrf")})`

Known sites: `test_register_sets_session_and_csrf_cookies`, `test_login_with_email_and_password`, `test_logout_clears_session` (only if it reads a gated route after logging in), `test_password_change_*` and `test_logout_everywhere_*` (if they log in directly rather than via `login_as`), `test_an_invite_token_authorizes_signup_while_registration_is_closed` (reads `/api/me` after register). Tests that only assert the login response code need no change. Add a tiny helper at the top of the file to avoid repeating the header dance:

```python
def _pass_mpin(client, set_it=False):
    hdr = {"X-CSRF-Token": client.cookies.get("csrf")}
    if set_it:
        r = client.post("/api/mpin/set", json={"mpin": "123456", "mpin_confirm": "123456"}, headers=hdr)
    else:
        r = client.post("/api/mpin/verify", json={"mpin": "123456"}, headers=hdr)
    assert r.status_code == 204, r.text
```

`api/tests/test_events_ws.py`: `session_cookies(email)` registers and returns the cookies for a WebSocket connect; it must set the MPIN and return the FULL session's cookies:

```python
    def session_cookies(email):
        r = httpx.post(f"{base_url}/api/register", json={
            "email": email, "password": "a-solid-password", "display_name": email})
        assert r.status_code == 204
        cookies = {"session": r.cookies["session"], "csrf": r.cookies["csrf"]}
        r = httpx.post(f"{base_url}/api/mpin/set",
                       json={"mpin": "123456", "mpin_confirm": "123456"},
                       cookies=cookies, headers={"X-CSRF-Token": cookies["csrf"]})
        assert r.status_code == 204, r.text
        return {"session": r.cookies["session"], "csrf": r.cookies["csrf"]}
```

Any other direct `/api/login` in that file that is followed by a socket connect gets the same verify step.

- [ ] **Step 7: The compose e2e registers, then sets the MPIN**

In `e2e/test_full_stack.py`, inside `_register_owner`, immediately after the `client.headers["X-CSRF-Token"] = csrf_token` line and before the membership INSERT, add:

```python
    # Registration leaves a half session: nothing but the MPIN routes and a
    # trimmed /api/me answer until the six-digit MPIN is set. Every e2e user
    # uses the fixture MPIN.
    set_resp = client.post("/api/mpin/set", json={"mpin": E2E_MPIN, "mpin_confirm": E2E_MPIN})
    assert set_resp.status_code == 204, set_resp.text
    # The full session re-issued the CSRF cookie; carry the new one.
    client.headers["X-CSRF-Token"] = client.cookies.get("csrf")
```

and near the other module constants at the top of the file add:

```python
E2E_MPIN = "123456"
```

`e2e/test_multi_org.py` imports `_register_owner` and needs no change.

- [ ] **Step 8: Run the affected files, then the whole suite**

Run: `.venv/Scripts/python -m pytest tests/test_auth.py tests/test_orgs.py tests/test_mpin.py tests/test_rbac_matrix.py -q -p no:cacheprovider`
Expected: all pass, including Task 3's one deferred assertion.

Run: `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider`
Expected: green except the pre-existing Windows-only failures (7 errors in `test_events_ws.py`, 1 CRLF failure in `test_mt5.py`). If any other test fails with 401 `MPIN required`, it logs in directly without the MPIN step: add the step, never weaken the gate. Then run the suite once in the Linux container (memory note `api-tests-in-docker`) so the `test_events_ws.py` tests, including the new half-session refusal, actually execute.

- [ ] **Step 9: Commit**

```bash
git add api/src/api/auth.py api/src/api/ws.py api/tests/test_auth.py api/tests/test_orgs.py api/tests/test_events_ws.py api/tests/test_rbac_matrix.py e2e/test_full_stack.py
git commit -m "feat(api): the MPIN gate -- half sessions get 401 MPIN required everywhere, a trimmed /api/me, and a closed WebSocket

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 5: Dashboard plumbing — pending state, redirects, register and join

**Files:**
- Modify: `dashboard/src/lib/types.ts:9-12`
- Modify: `dashboard/src/lib/api.ts:46-55`
- Modify: `dashboard/src/App.tsx:30-52` (RootRedirect)
- Modify: `dashboard/src/lib/org.tsx`
- Modify: `dashboard/src/pages/Register.tsx:22-56`
- Modify: `dashboard/src/pages/Join.tsx:34-44`
- Modify: `dashboard/src/pages/Welcome.tsx:26-29`
- Modify tests: `dashboard/src/lib/api.test.ts`, `src/App.test.tsx`, `src/lib/org.test.tsx`, `src/pages/Register.test.tsx`, `src/pages/Join.test.tsx`, `src/pages/Welcome.test.tsx`

**Interfaces:**
- Produces: `MpinState = { pending: boolean; set: boolean }`; `Me.mpin?: MpinState`; `MpinPending = { mpin: MpinState }`; `isMpinPending(x: Me | MpinPending): x is MpinPending`; `api()` redirects to `/mpin` on a 401 whose detail is `MPIN required`, never redirects for paths under `/api/mpin/`; the `/mpin` route exists as a placeholder until Task 7 (`<div>mpin</div>` in App.tsx, replaced in Task 7).

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/src/lib/api.test.ts`:

```ts
test('a 401 with detail "MPIN required" redirects to /mpin, not /login', async () => {
  Object.defineProperty(window, 'location', { value: { href: '/org/1' }, writable: true })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: 'MPIN required' }), {
      status: 401, headers: { 'content-type': 'application/json' },
    })
  ))
  await expect(api('/api/orgs/1/accounts')).rejects.toThrow('Unauthorized')
  expect(window.location.href).toBe('/mpin')
})

test('401s from the MPIN routes are inline errors, never redirects', async () => {
  Object.defineProperty(window, 'location', { value: { href: '/mpin' }, writable: true })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: 'Invalid MPIN', attempts_left: 3 }), {
      status: 401, headers: { 'content-type': 'application/json' },
    })
  ))
  await expect(api('/api/mpin/verify', { method: 'POST', body: '{}' })).rejects.toThrow('401: Invalid MPIN')
  expect(window.location.href).toBe('/mpin')
})
```

Append to `dashboard/src/App.test.tsx`:

```ts
test('a pending MPIN sends / to /mpin', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(
    JSON.stringify({ mpin: { pending: true, set: true } }),
    { status: 200, headers: { 'content-type': 'application/json' } }))))
  window.history.pushState({}, '', '/')
  render(<App />)
  await waitFor(() => expect(window.location.pathname).toBe('/mpin'))
})
```

Append to `dashboard/src/lib/org.test.tsx` (reuse its `renderAtOrg`; add a `/mpin` route to that helper's `<Routes>` rendering `<div>mpin screen</div>`):

```ts
test('a pending MPIN sends an org route to /mpin', async () => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(
    JSON.stringify({ mpin: { pending: true, set: false } }),
    { status: 200, headers: { 'content-type': 'application/json' } }))))
  renderAtOrg('/org/1')
  expect(await screen.findByText('mpin screen')).toBeInTheDocument()
})
```

In `dashboard/src/pages/Register.test.tsx` change the success test's expectation and add the invite case. Add a route `<Route path="/mpin" element={<MpinLanding />} />` to `renderRegister` with:

```ts
function MpinLanding() {
  const [params] = useSearchParams()
  return <div>mpin next {params.get('next')}</div>
}
```

(import `useSearchParams` from react-router-dom), let `renderRegister(path = '/register')` take the initial entry, and replace the assertion `expect(screen.getByText('welcome'))` with:

```ts
  await waitFor(() => {
    expect(screen.getByText('mpin next /welcome')).toBeInTheDocument()
  })
```

Add:

```ts
test('with an invite, registration goes to /mpin carrying the join path', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
  vi.stubGlobal('fetch', fetchMock)
  renderRegister('/register?invite=tok123')

  await userEvent.type(screen.getByLabelText(/display name/i), 'Ada Trader')
  await userEvent.type(screen.getByLabelText(/email/i), 'ada@example.com')
  await userEvent.type(screen.getByLabelText(/^password$/i), 'correcthorsebattery')
  await userEvent.click(screen.getByRole('button', { name: /create account/i }))

  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({ invite_token: 'tok123' })
  // No join attempt yet: the MPIN comes first, Join finishes the invite afterwards.
  expect(fetchMock).toHaveBeenCalledTimes(1)
  await waitFor(() => {
    expect(screen.getByText('mpin next /join/tok123')).toBeInTheDocument()
  })
})
```

In `dashboard/src/pages/Join.test.tsx` add a `/mpin` route (`<div>mpin next {params.get('next')}</div>` as above) to `renderJoin` and:

```ts
test('a half session is sent to /mpin carrying the join path', async () => {
  stubFetch({
    me: new Response(JSON.stringify({ mpin: { pending: true, set: true } }), {
      status: 200, headers: { 'content-type': 'application/json' },
    }),
  })
  renderJoin('tok9')
  expect(await screen.findByText('mpin next /join/tok9')).toBeInTheDocument()
})
```

In `dashboard/src/pages/Welcome.test.tsx` add a test that a pending `/api/me` navigates to `/mpin` (render Welcome inside a `MemoryRouter` with a `/mpin` route rendering `<div>mpin screen</div>`, stub fetch for `/api/me` with the pending shape, assert `findByText('mpin screen')`). Mirror the file's existing render helper.

- [ ] **Step 2: Run them to verify they fail**

Run (from `dashboard/`): `npx vitest run src/lib/api.test.ts src/App.test.tsx src/lib/org.test.tsx src/pages/Register.test.tsx src/pages/Join.test.tsx src/pages/Welcome.test.tsx`
Expected: the new tests FAIL (redirect goes to `/login`; pending shapes crash on `me.orgs`; Register still lands on welcome).

- [ ] **Step 3: Types**

In `dashboard/src/lib/types.ts` replace the `Me` interface:

```ts
export interface MpinState {
  /** True on a half session: email and password were right, the MPIN is still owed. */
  pending: boolean
  /** False for a user who has never set an MPIN (the /mpin page shows Set mode). */
  set: boolean
}

export interface Me {
  user: { id: number; email: string; display_name: string }
  orgs: OrgSummary[]
  mpin?: MpinState
}

/** What /api/me answers on a half session: only what to do next. */
export interface MpinPending {
  mpin: MpinState
}

export function isMpinPending(me: Me | MpinPending): me is MpinPending {
  return !('user' in me) && me.mpin?.pending === true
}
```

- [ ] **Step 4: The API client**

In `dashboard/src/lib/api.ts` replace the 401 block:

```ts
  // A 401 means "go sign in" -- except when it means "finish signing in":
  // a half session (email+password done, MPIN owed) is told exactly that by
  // the server, and belongs on /mpin. The MPIN routes' own 401s ("Invalid
  // MPIN") are inline errors for the /mpin page, like /api/login's.
  if (response.status === 401 && path !== '/api/login' && !path.startsWith('/api/mpin/')
      && opts?.redirectOn401 !== false) {
    let detail: string | undefined
    try {
      const body = (await response.clone().json()) as { detail?: unknown }
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // not JSON
    }
    window.location.href = detail === 'MPIN required' ? '/mpin' : '/login'
    throw new Error('Unauthorized')
  }
```

- [ ] **Step 5: RootRedirect, OrgProvider, Welcome**

In `dashboard/src/App.tsx` import `isMpinPending` and type `MpinPending` from `./lib/types`, and change `RootRedirect`'s resolver:

```tsx
      try {
        const me = await api<Me | MpinPending>('/api/me', undefined, { redirectOn401: false })
        if (isMpinPending(me)) {
          setTarget('/mpin')
          return
        }
        const last = Number(localStorage.getItem(LAST_ORG_KEY))
        const org = me.orgs.find((o) => o.id === last) ?? me.orgs[0]
        setTarget(org ? `/org/${org.id}` : '/welcome')
      } catch {
        setTarget('landing')
      }
```

Add a placeholder route (Task 7 replaces the element): `<Route path="/mpin" element={<div>mpin</div>} />` next to `/login`.

In `dashboard/src/lib/org.tsx` import `isMpinPending` and `MpinPending`, add `const [mpinPending, setMpinPending] = useState(false)`, and change `refreshMe`:

```tsx
  const refreshMe = useCallback(async () => {
    try {
      const next = await api<Me | MpinPending>('/api/me')
      if (isMpinPending(next)) {
        setMpinPending(true)
        return
      }
      setMe(next)
    } catch {
      setFailed(true)
    }
  }, [])
```

and before `if (failed)` add `if (mpinPending) return <Navigate to="/mpin" replace />`.

In `dashboard/src/pages/Welcome.tsx` change the `/api/me` effect:

```tsx
    api<{ orgs?: MyOrg[]; mpin?: { pending: boolean } }>('/api/me')
      .then((me) => {
        if (cancelled) return
        if (me.mpin?.pending) {
          navigate('/mpin', { replace: true })
          return
        }
        setOrgs(me.orgs ?? [])
      })
      .catch(() => { if (!cancelled) setOrgs([]) })
```

(`navigate` already exists in that component; add it to the effect's dependency list.)

- [ ] **Step 6: Register and Join**

In `dashboard/src/pages/Register.tsx` replace the success branch of `handleSubmit` (everything after the `await api('/api/register', …)` call inside the `try`) with:

```tsx
      // Registration leaves a half session: the MPIN comes first, and the
      // MPIN page hands over to the invite (or the welcome screen) after.
      const next = invite ? `/join/${invite}` : '/welcome'
      navigate(`/mpin?next=${encodeURIComponent(next)}`, { replace: true })
```

(Delete the inner `try { const joined = await api(...'/api/orgs/join'...) }` block; Join owns that now.)

In `dashboard/src/pages/Join.tsx` import `isMpinPending`, `Me`, `MpinPending` and change the first step of `join()`:

```tsx
      let me: Me | MpinPending
      try {
        me = await api<Me | MpinPending>('/api/me', undefined, { redirectOn401: false })
      } catch {
        if (cancelled) return
        navigate(`/register?invite=${encodeURIComponent(token ?? '')}`, { replace: true })
        return
      }
      if (isMpinPending(me)) {
        if (!cancelled) {
          navigate(`/mpin?next=${encodeURIComponent(`/join/${token ?? ''}`)}`, { replace: true })
        }
        return
      }
```

- [ ] **Step 7: Run the six test files, then the full gate**

Run: `npx vitest run src/lib/api.test.ts src/App.test.tsx src/lib/org.test.tsx src/pages/Register.test.tsx src/pages/Join.test.tsx src/pages/Welcome.test.tsx` → all pass.
Run: `npm test` → prover, tsc and every file green (existing `/api/me` mocks lack `mpin` and are treated as not pending).

- [ ] **Step 8: Commit**

```bash
git add dashboard/src/lib/types.ts dashboard/src/lib/api.ts dashboard/src/App.tsx dashboard/src/lib/org.tsx dashboard/src/pages/Register.tsx dashboard/src/pages/Join.tsx dashboard/src/pages/Welcome.tsx dashboard/src/lib/api.test.ts dashboard/src/App.test.tsx dashboard/src/lib/org.test.tsx dashboard/src/pages/Register.test.tsx dashboard/src/pages/Join.test.tsx dashboard/src/pages/Welcome.test.tsx
git commit -m "feat(dashboard): a pending MPIN routes everything to /mpin; register and join hand over through next

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 6: `PinInput` — six masked boxes

**Files:**
- Create: `dashboard/src/components/PinInput.tsx`
- Create: `dashboard/src/components/PinInput.test.tsx`

**Interfaces:**
- Produces: `<PinInput id label value onChange onComplete? disabled? error? autoFocus? />` where `value` is a 0–6 digit string, `onChange(next: string)` fires on every edit, `onComplete(pin: string)` fires once when the sixth digit lands, `error` is rendered as `role="alert"` and linked by `aria-describedby`; boxes are `type="password"` with a "Show" toggle.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/src/components/PinInput.test.tsx`:

```tsx
import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import PinInput from './PinInput'

function Harness({ onComplete, error, disabled }: {
  onComplete?: (pin: string) => void; error?: string | null; disabled?: boolean
}) {
  const [value, setValue] = useState('')
  return (
    <PinInput id="mpin" label="MPIN" value={value} onChange={setValue}
              onComplete={onComplete} error={error} disabled={disabled} autoFocus />
  )
}

function boxes() {
  return screen.getAllByRole('textbox', { hidden: true }).length
    ? screen.getAllByRole('textbox', { hidden: true })
    : Array.from(document.querySelectorAll<HTMLInputElement>('input'))
}

test('renders six masked single-digit boxes under one label', () => {
  render(<Harness />)
  const inputs = boxes()
  expect(inputs).toHaveLength(6)
  for (const el of inputs) {
    expect(el).toHaveAttribute('type', 'password')
    expect(el).toHaveAttribute('inputmode', 'numeric')
    expect(el).toHaveAttribute('maxlength', '1')
  }
  expect(screen.getByRole('group', { name: 'MPIN' })).toBeInTheDocument()
  expect(inputs[0]).toHaveFocus()
})

test('typing advances, Backspace retreats, non-digits are ignored, six digits complete', async () => {
  const onComplete = vi.fn()
  render(<Harness onComplete={onComplete} />)
  const inputs = boxes()
  await userEvent.keyboard('1a2')
  expect(inputs[0]).toHaveValue('1')
  expect(inputs[1]).toHaveValue('2')
  expect(inputs[2]).toHaveFocus()
  await userEvent.keyboard('{Backspace}')
  expect(inputs[1]).toHaveValue('')
  expect(inputs[1]).toHaveFocus()
  await userEvent.keyboard('23456')
  expect(onComplete).toHaveBeenCalledWith('123456')
  expect(onComplete).toHaveBeenCalledTimes(1)
})

test('pasting six digits fills every box and completes', async () => {
  const onComplete = vi.fn()
  render(<Harness onComplete={onComplete} />)
  await userEvent.paste('987654')
  expect(boxes().map((b) => (b as HTMLInputElement).value).join('')).toBe('987654')
  expect(onComplete).toHaveBeenCalledWith('987654')
})

test('Show reveals the digits; the error is announced and linked', async () => {
  render(<Harness error="Wrong MPIN, 3 tries left" />)
  expect(screen.getByRole('alert')).toHaveTextContent('Wrong MPIN, 3 tries left')
  expect(boxes()[0]).toHaveAccessibleDescription(/3 tries left/)
  await userEvent.click(screen.getByRole('button', { name: /show/i }))
  expect(boxes()[0]).toHaveAttribute('type', 'text')
})

test('disabled boxes take no input', async () => {
  const onComplete = vi.fn()
  render(<Harness disabled onComplete={onComplete} />)
  await userEvent.keyboard('123456')
  expect(boxes()[0]).toHaveValue('')
  expect(onComplete).not.toHaveBeenCalled()
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/components/PinInput.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

Create `dashboard/src/components/PinInput.tsx`:

```tsx
import { useEffect, useId, useRef, useState, type ClipboardEvent, type KeyboardEvent } from 'react'
import Button from './Button'

const LENGTH = 6

/**
 * Six single-digit boxes for the MPIN. Digits are masked; typing advances,
 * Backspace retreats, paste fills, arrows move. `value` is the digits typed
 * so far (0-6 characters); `onComplete` fires once when the sixth lands.
 * The group is labelled and the error is announced and linked to every box.
 */
export default function PinInput({ id, label, value, onChange, onComplete, disabled, error, autoFocus }: {
  id: string
  label: string
  value: string
  onChange: (next: string) => void
  onComplete?: (pin: string) => void
  disabled?: boolean
  error?: string | null
  autoFocus?: boolean
}) {
  const refs = useRef<Array<HTMLInputElement | null>>([])
  const [show, setShow] = useState(false)
  const errorId = useId()
  const labelId = useId()
  const completedFor = useRef<string | null>(null)

  useEffect(() => {
    if (autoFocus) refs.current[0]?.focus()
  }, [autoFocus])

  useEffect(() => {
    if (value.length === LENGTH && completedFor.current !== value) {
      completedFor.current = value
      onComplete?.(value)
    }
    if (value.length < LENGTH) completedFor.current = null
  }, [value, onComplete])

  const focusBox = (i: number) => refs.current[Math.max(0, Math.min(LENGTH - 1, i))]?.focus()

  const onKeyDown = (i: number) => (e: KeyboardEvent<HTMLInputElement>) => {
    if (disabled) { e.preventDefault(); return }
    if (/^[0-9]$/.test(e.key)) {
      e.preventDefault()
      const next = value.slice(0, i) + e.key
      onChange(next.slice(0, LENGTH))
      focusBox(i + 1)
    } else if (e.key === 'Backspace') {
      e.preventDefault()
      if (value.length > i || i === value.length) {
        const target = value[i] ? i : Math.max(0, i - 1)
        onChange(value.slice(0, target))
        focusBox(target)
      }
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault(); focusBox(i - 1)
    } else if (e.key === 'ArrowRight') {
      e.preventDefault(); focusBox(i + 1)
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey) {
      // Letters and symbols never land in an MPIN box.
      e.preventDefault()
    }
  }

  const onPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    e.preventDefault()
    if (disabled) return
    const digits = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, LENGTH)
    if (!digits) return
    onChange(digits)
    focusBox(digits.length >= LENGTH ? LENGTH - 1 : digits.length)
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span id={labelId} className="desk-label">{label}</span>
        <Button variant="ghost" tone="neutral" size="sm" type="button" onClick={() => setShow((s) => !s)}>
          {show ? 'Hide' : 'Show'}
        </Button>
      </div>
      <div role="group" aria-labelledby={labelId} className="flex gap-2">
        {Array.from({ length: LENGTH }, (_, i) => (
          <input
            key={i}
            ref={(el) => { refs.current[i] = el }}
            id={i === 0 ? id : `${id}-${i}`}
            type={show ? 'text' : 'password'}
            inputMode="numeric"
            pattern="[0-9]*"
            autoComplete={i === 0 ? 'one-time-code' : 'off'}
            maxLength={1}
            value={value[i] ?? ''}
            onChange={() => { /* handled in onKeyDown / onPaste; controlled value */ }}
            onKeyDown={onKeyDown(i)}
            onPaste={onPaste}
            onFocus={(e) => e.currentTarget.select()}
            disabled={disabled}
            aria-label={`${label} digit ${i + 1}`}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            className={[
              'num h-12 w-11 rounded border bg-card text-center text-lg text-ink',
              'disabled:opacity-50',
              error ? 'border-loss' : 'border-field-line',
            ].join(' ')}
          />
        ))}
      </div>
      {error && (
        <p id={errorId} role="alert" className="mt-2 text-sm text-loss-deep">{error}</p>
      )}
    </div>
  )
}
```

The `onChange={() => {}}` on a controlled input is deliberate: digits arrive through `onKeyDown` so a single handler owns the rules; keep the comment.

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/components/PinInput.test.tsx` → all pass. If the paste test cannot fire on jsdom through `userEvent.paste` while no box is focused, focus the first box in the test before pasting (`boxes()[0].focus()`); do not change the component to satisfy it.

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/PinInput.tsx dashboard/src/components/PinInput.test.tsx
git commit -m "feat(dashboard): PinInput -- six masked digit boxes with paste, arrows and an announced error

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 7: The `/mpin` page — Set, Verify, Forgot

**Files:**
- Create: `dashboard/src/pages/Mpin.tsx`
- Create: `dashboard/src/pages/Mpin.test.tsx`
- Modify: `dashboard/src/App.tsx` (replace the placeholder route)

**Interfaces:**
- Consumes: `PinInput`, `api`, `isMpinPending`, `Me | MpinPending`, `Button`, `Input`, `Banner`, `Logo`.
- Produces: route `/mpin` honouring `?next=<same-origin path>`; copy: headings "Choose your MPIN" (Set), "Enter your MPIN" (Verify), "Reset your MPIN" (Forgot); error strings "Wrong MPIN, N tries left" and "Locked. Try again in mm:ss"; a "Forgot MPIN?" link; a "Sign out" button on every mode.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/src/pages/Mpin.test.tsx`:

```tsx
import { render, screen, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { afterEach, expect, test, vi } from 'vitest'
import Mpin from './Mpin'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

function json(payload: unknown, status = 200) {
  return new Response(payload == null ? null : JSON.stringify(payload), {
    status, headers: { 'content-type': 'application/json' },
  })
}

function renderMpin(path = '/mpin') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/mpin" element={<Mpin />} />
        <Route path="/" element={<div>desk root</div>} />
        <Route path="/welcome" element={<div>welcome</div>} />
        <Route path="/join/:token" element={<div>join page</div>} />
        <Route path="/login" element={<div>login page</div>} />
      </Routes>
    </MemoryRouter>
  )
}

function stub(me: unknown, handlers: Record<string, (init?: RequestInit) => Response> = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url === '/api/me') return json(me)
    const h = handlers[url]
    if (h) return h(init)
    throw new Error(`unexpected ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function typePin(pin: string) {
  const first = document.querySelector<HTMLInputElement>('input')!
  first.focus()
  await userEvent.keyboard(pin)
}

test('Verify mode: six digits submit, success goes to next', async () => {
  const fetchMock = stub({ mpin: { pending: true, set: true } }, {
    '/api/mpin/verify': () => json(null, 204),
  })
  renderMpin('/mpin?next=%2Fwelcome')
  expect(await screen.findByRole('heading', { name: 'Enter your MPIN' })).toBeInTheDocument()
  await typePin('123456')
  await waitFor(() => expect(screen.getByText('welcome')).toBeInTheDocument())
  const call = fetchMock.mock.calls.find(([u]) => String(u) === '/api/mpin/verify')!
  expect(JSON.parse(String((call[1] as RequestInit).body))).toEqual({ mpin: '123456' })
})

test('a wrong MPIN shows the tries left and clears the boxes', async () => {
  stub({ mpin: { pending: true, set: true } }, {
    '/api/mpin/verify': () => json({ detail: 'Invalid MPIN', attempts_left: 3 }, 401),
  })
  renderMpin()
  await screen.findByRole('heading', { name: 'Enter your MPIN' })
  await typePin('000000')
  expect(await screen.findByRole('alert')).toHaveTextContent('Wrong MPIN, 3 tries left')
  expect(document.querySelector<HTMLInputElement>('input')!.value).toBe('')
})

test('a lock disables entry and counts down; Forgot MPIN still works', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const until = new Date(Date.now() + 90_000).toISOString()
  stub({ mpin: { pending: true, set: true } }, {
    '/api/mpin/verify': () => json({ detail: 'MPIN locked', locked_until: until }, 423),
  })
  renderMpin()
  await screen.findByRole('heading', { name: 'Enter your MPIN' })
  await typePin('000000')
  expect(await screen.findByText(/Locked\. Try again in 01:2\d/)).toBeInTheDocument()
  expect(document.querySelector<HTMLInputElement>('input')).toBeDisabled()
  expect(screen.getByRole('button', { name: /forgot mpin/i })).toBeEnabled()
})

test('Set mode asks twice and posts to /api/mpin/set', async () => {
  const fetchMock = stub({ mpin: { pending: true, set: false } }, {
    '/api/mpin/set': () => json(null, 204),
  })
  renderMpin()
  expect(await screen.findByRole('heading', { name: 'Choose your MPIN' })).toBeInTheDocument()
  const inputs = () => Array.from(document.querySelectorAll<HTMLInputElement>('input'))
  inputs()[0].focus(); await userEvent.keyboard('246810')
  inputs()[6].focus(); await userEvent.keyboard('246810')
  await userEvent.click(screen.getByRole('button', { name: /save mpin/i }))
  await waitFor(() => expect(screen.getByText('desk root')).toBeInTheDocument())
  const call = fetchMock.mock.calls.find(([u]) => String(u) === '/api/mpin/set')!
  expect(JSON.parse(String((call[1] as RequestInit).body))).toEqual({ mpin: '246810', mpin_confirm: '246810' })
})

test('Set mode refuses a mismatch before calling the server', async () => {
  const fetchMock = stub({ mpin: { pending: true, set: false } })
  renderMpin()
  await screen.findByRole('heading', { name: 'Choose your MPIN' })
  const inputs = () => Array.from(document.querySelectorAll<HTMLInputElement>('input'))
  inputs()[0].focus(); await userEvent.keyboard('246810')
  inputs()[6].focus(); await userEvent.keyboard('111111')
  await userEvent.click(screen.getByRole('button', { name: /save mpin/i }))
  expect(await screen.findByRole('alert')).toHaveTextContent('MPINs do not match')
  expect(fetchMock.mock.calls.some(([u]) => String(u) === '/api/mpin/set')).toBe(false)
})

test('Forgot mode posts the password with the new MPIN', async () => {
  const fetchMock = stub({ mpin: { pending: true, set: true } }, {
    '/api/mpin/reset': () => json(null, 204),
  })
  renderMpin()
  await screen.findByRole('heading', { name: 'Enter your MPIN' })
  await userEvent.click(screen.getByRole('button', { name: /forgot mpin/i }))
  expect(screen.getByRole('heading', { name: 'Reset your MPIN' })).toBeInTheDocument()
  await userEvent.type(screen.getByLabelText(/^password$/i), 'a-solid-password')
  const inputs = () => Array.from(document.querySelectorAll<HTMLInputElement>('input'))
  inputs()[1].focus(); await userEvent.keyboard('999999')
  inputs()[7].focus(); await userEvent.keyboard('999999')
  await userEvent.click(screen.getByRole('button', { name: /reset mpin/i }))
  await waitFor(() => expect(screen.getByText('desk root')).toBeInTheDocument())
  const call = fetchMock.mock.calls.find(([u]) => String(u) === '/api/mpin/reset')!
  expect(JSON.parse(String((call[1] as RequestInit).body))).toEqual({
    password: 'a-solid-password', mpin: '999999', mpin_confirm: '999999',
  })
})

test('Sign out is available and goes to /login', async () => {
  stub({ mpin: { pending: true, set: true } }, { '/api/logout': () => json(null, 204) })
  renderMpin()
  await screen.findByRole('heading', { name: 'Enter your MPIN' })
  await userEvent.click(screen.getByRole('button', { name: /sign out/i }))
  await waitFor(() => expect(screen.getByText('login page')).toBeInTheDocument())
})

test('a session that is not pending bounces to /; no session bounces to /login', async () => {
  stub({ user: { id: 1, email: 'a@x', display_name: 'A' }, orgs: [], mpin: { pending: false, set: true } })
  const { unmount } = renderMpin()
  await waitFor(() => expect(screen.getByText('desk root')).toBeInTheDocument())
  unmount()
  vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'Not authenticated' }, 401)))
  renderMpin()
  await waitFor(() => expect(screen.getByText('login page')).toBeInTheDocument())
})

test('next must be a same-origin path; anything else falls back to /', async () => {
  stub({ mpin: { pending: true, set: true } }, { '/api/mpin/verify': () => json(null, 204) })
  renderMpin('/mpin?next=https%3A%2F%2Fevil.example')
  await screen.findByRole('heading', { name: 'Enter your MPIN' })
  await typePin('123456')
  await waitFor(() => expect(screen.getByText('desk root')).toBeInTheDocument())
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/pages/Mpin.test.tsx` → FAIL, module not found.

- [ ] **Step 3: Let `api()` expose the status and body of a failed response**

The verify flow needs the 423 body's `locked_until` and the 401 body's `attempts_left`. In `dashboard/src/lib/api.ts`, in the non-2xx block, keep the parsed body and attach it to the thrown error:

```ts
  if (!response.ok) {
    let detail: string | undefined
    let parsed: Record<string, unknown> | undefined
    try {
      parsed = (await response.json()) as Record<string, unknown>
      if (typeof parsed.detail === 'string') detail = parsed.detail
    } catch {
      // Body wasn't JSON (or was empty) — fall back to the bare status.
    }
    const error = new Error(detail ? `${response.status}: ${detail}` : `${response.status}`) as ApiError
    error.response = { status: response.status, body: parsed }
    throw error
  }
```

and export the type near the top of the file:

```ts
/** Every non-2xx throws one of these; `response` carries what the server said. */
export type ApiError = Error & { response?: { status: number; body?: Record<string, unknown> } }
```

Add to `dashboard/src/lib/api.test.ts`:

```ts
test('a failed response exposes its status and JSON body on the error', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: 'MPIN locked', locked_until: '2026-09-26T10:00:00Z' }), {
      status: 423, headers: { 'content-type': 'application/json' },
    })
  ))
  const err = await api('/api/mpin/verify', { method: 'POST', body: '{}' }).catch((e) => e as ApiError)
  expect(err.response?.status).toBe(423)
  expect(err.response?.body?.locked_until).toBe('2026-09-26T10:00:00Z')
})
```

(import `ApiError` in that test file.)

- [ ] **Step 4: Implement the page**

Create `dashboard/src/pages/Mpin.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api, type ApiError } from '../lib/api'
import { errorText } from '../lib/format'
import { isMpinPending, type Me, type MpinPending } from '../lib/types'
import Banner from '../components/Banner'
import Button from '../components/Button'
import Input from '../components/Input'
import Logo from '../components/Logo'
import PinInput from '../components/PinInput'

type Mode = 'loading' | 'set' | 'verify' | 'forgot'

/** Only a same-origin path may be the landing after the MPIN. */
function safeNext(raw: string | null): string {
  if (!raw) return '/'
  if (!raw.startsWith('/') || raw.startsWith('//')) return '/'
  return raw
}

function countdown(until: Date, now: Date): string {
  const s = Math.max(0, Math.ceil((until.getTime() - now.getTime()) / 1000))
  const mm = String(Math.floor(s / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${mm}:${ss}`
}

/**
 * The second gate. Email and password are already right (a half session);
 * nothing else in the platform answers until the six-digit MPIN is set (first
 * login), verified (every login after) or reset (through the password).
 */
export default function Mpin() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const [mode, setMode] = useState<Mode>('loading')
  const [pin, setPin] = useState('')
  const [confirm, setConfirm] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [lockedUntil, setLockedUntil] = useState<Date | null>(null)
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    let cancelled = false
    api<Me | MpinPending>('/api/me', undefined, { redirectOn401: false })
      .then((me) => {
        if (cancelled) return
        if (!isMpinPending(me)) { navigate(next, { replace: true }); return }
        setMode(me.mpin.set ? 'verify' : 'set')
      })
      .catch(() => { if (!cancelled) navigate('/login', { replace: true }) })
    return () => { cancelled = true }
  }, [navigate, next])

  // The lock countdown ticks once a second and releases itself.
  useEffect(() => {
    if (!lockedUntil) return
    const id = setInterval(() => {
      const t = new Date()
      setNow(t)
      if (t >= lockedUntil) { setLockedUntil(null); setError(null) }
    }, 1000)
    return () => clearInterval(id)
  }, [lockedUntil])

  const fail = (err: unknown) => setError(errorText(err, 'Something went wrong'))

  const verify = useCallback(async (value: string) => {
    setBusy(true); setError(null)
    try {
      await api('/api/mpin/verify', { method: 'POST', body: JSON.stringify({ mpin: value }) })
      navigate(next, { replace: true })
    } catch (err) {
      setPin('')
      const res = (err as ApiError).response
      if (res?.status === 423 && typeof res.body?.locked_until === 'string') {
        setLockedUntil(new Date(res.body.locked_until))
      } else if (res?.status === 401 && typeof res.body?.attempts_left === 'number') {
        const n = res.body.attempts_left
        setError(`Wrong MPIN, ${n} ${n === 1 ? 'try' : 'tries'} left`)
      } else {
        fail(err)
      }
    } finally {
      setBusy(false)
    }
  }, [navigate, next])

  const submitSet = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    if (pin.length !== 6) { setError('MPIN must be exactly 6 digits'); return }
    if (pin !== confirm) { setError('MPINs do not match'); return }
    setBusy(true)
    try {
      await api('/api/mpin/set', { method: 'POST', body: JSON.stringify({ mpin: pin, mpin_confirm: confirm }) })
      navigate(next, { replace: true })
    } catch (err) { fail(err) } finally { setBusy(false) }
  }

  const submitReset = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    if (pin.length !== 6) { setError('MPIN must be exactly 6 digits'); return }
    if (pin !== confirm) { setError('MPINs do not match'); return }
    setBusy(true)
    try {
      await api('/api/mpin/reset', {
        method: 'POST', body: JSON.stringify({ password, mpin: pin, mpin_confirm: confirm }),
      })
      navigate(next, { replace: true })
    } catch (err) { fail(err) } finally { setBusy(false) }
  }

  const signOut = async () => {
    try { await api('/api/logout', { method: 'POST' }) } catch { /* cookies may already be gone */ }
    navigate('/login', { replace: true })
  }

  const locked = lockedUntil != null && lockedUntil > now
  const lockText = lockedUntil ? `Locked. Try again in ${countdown(lockedUntil, now)}` : null

  if (mode === 'loading') {
    return <div className="flex items-center justify-center h-screen">Loading...</div>
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-paper py-12 px-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="flex justify-center"><Logo size={38} textClass="text-3xl" /></h1>
          <p className="mt-2 text-sm text-ink-soft">
            {mode === 'set' && 'One more step: a six-digit MPIN you will enter at every sign-in.'}
            {mode === 'verify' && 'Your password was right. Now your MPIN.'}
            {mode === 'forgot' && 'Prove your password to choose a new MPIN.'}
          </p>
        </div>
        <div className="bg-card rounded-lg border border-line p-8 space-y-5">
          {mode === 'verify' && (
            <>
              <h2 className="font-display text-lg text-ink">Enter your MPIN</h2>
              <PinInput id="mpin" label="MPIN" value={pin} onChange={setPin}
                        onComplete={verify} disabled={busy || locked}
                        error={locked ? lockText : error} autoFocus />
              <div className="flex items-center justify-between">
                <Button variant="ghost" tone="brand" size="sm"
                        onClick={() => { setMode('forgot'); setPin(''); setConfirm(''); setError(null) }}>
                  Forgot MPIN?
                </Button>
                <Button variant="ghost" tone="neutral" size="sm" onClick={signOut}>Sign out</Button>
              </div>
            </>
          )}
          {mode === 'set' && (
            <form onSubmit={submitSet} className="space-y-5">
              <h2 className="font-display text-lg text-ink">Choose your MPIN</h2>
              {error && <Banner kind="error">{error}</Banner>}
              <PinInput id="mpin" label="MPIN" value={pin} onChange={setPin} disabled={busy} autoFocus />
              <PinInput id="mpin-confirm" label="Confirm MPIN" value={confirm} onChange={setConfirm} disabled={busy} />
              <Button type="submit" block busy={busy}>Save MPIN</Button>
              <div className="text-center">
                <Button variant="ghost" tone="neutral" size="sm" onClick={signOut}>Sign out</Button>
              </div>
            </form>
          )}
          {mode === 'forgot' && (
            <form onSubmit={submitReset} className="space-y-5">
              <h2 className="font-display text-lg text-ink">Reset your MPIN</h2>
              {error && <Banner kind="error">{error}</Banner>}
              <div>
                <label htmlFor="password" className="desk-label block mb-1">Password</label>
                <Input id="password" type="password" autoComplete="current-password" required
                       value={password} onChange={(e) => setPassword(e.target.value)} disabled={busy} />
              </div>
              <PinInput id="mpin" label="New MPIN" value={pin} onChange={setPin} disabled={busy} />
              <PinInput id="mpin-confirm" label="Confirm new MPIN" value={confirm} onChange={setConfirm} disabled={busy} />
              <Button type="submit" block tone="loss" busy={busy}>Reset MPIN</Button>
              <div className="flex items-center justify-between">
                <Button variant="ghost" tone="brand" size="sm"
                        onClick={() => { setMode('verify'); setPin(''); setConfirm(''); setPassword(''); setError(null) }}>
                  Back
                </Button>
                <Button variant="ghost" tone="neutral" size="sm" onClick={signOut}>Sign out</Button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}
```

Replace the placeholder route in `dashboard/src/App.tsx` with `<Route path="/mpin" element={<Mpin />} />` and import `Mpin from './pages/Mpin'`.

- [ ] **Step 5: Run to verify it passes, then the full gate**

Run: `npx vitest run src/pages/Mpin.test.tsx src/lib/api.test.ts src/App.test.tsx` → all pass.
Run: `npm test` → prover, tsc, every file green.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/pages/Mpin.tsx dashboard/src/pages/Mpin.test.tsx dashboard/src/App.tsx dashboard/src/lib/api.ts dashboard/src/lib/api.test.ts
git commit -m "feat(dashboard): the /mpin gate -- set on first login, verify with tries-left and a lock countdown, reset through the password

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 8: Change MPIN in Account security, README

**Files:**
- Modify: `dashboard/src/components/AccountSecurity.tsx`
- Modify: `dashboard/src/pages/Members.test.tsx` (AccountSecurity is tested there)
- Modify: `README.md` (the "Multi-user, multi-org" section)

**Interfaces:**
- Consumes: `POST /api/me/mpin {current_mpin, mpin, mpin_confirm}`, `PinInput`.

- [ ] **Step 1: Write the failing test**

Append to `dashboard/src/pages/Members.test.tsx` (uses that file's `mockRoutes` / `renderMembers` / `makeOrgValue` helpers):

```tsx
test('every member can change their MPIN with the current one', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  const fetchMock = mockRoutes({ 'POST /api/me/mpin': () => jsonResponse(null, 204) })
  renderMembers()
  await screen.findByRole('heading', { name: /your login/i })
  const boxes = () => Array.from(document.querySelectorAll<HTMLInputElement>('input[inputmode="numeric"]'))
  boxes()[0].focus(); await userEvent.keyboard('123456')
  boxes()[6].focus(); await userEvent.keyboard('654321')
  boxes()[12].focus(); await userEvent.keyboard('654321')
  await userEvent.click(screen.getByRole('button', { name: /change mpin/i }))
  await waitFor(() => {
    const call = fetchMock.mock.calls.find(
      ([u, init]) => String(u) === '/api/me/mpin' && (init as RequestInit)?.method === 'POST')
    expect(call).toBeTruthy()
    expect(JSON.parse(String((call![1] as RequestInit).body))).toEqual({
      current_mpin: '123456', mpin: '654321', mpin_confirm: '654321',
    })
  })
  expect(await screen.findByText(/MPIN changed/i)).toBeInTheDocument()
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/pages/Members.test.tsx` → the new test FAILS (no MPIN boxes).

- [ ] **Step 3: Add the form**

In `dashboard/src/components/AccountSecurity.tsx` import `PinInput from './PinInput'`, add state:

```tsx
  const [currentMpin, setCurrentMpin] = useState('')
  const [newMpin, setNewMpin] = useState('')
  const [confirmMpin, setConfirmMpin] = useState('')
```

add the handler:

```tsx
  const submitMpin = async (e: React.FormEvent) => {
    e.preventDefault()
    setNotice(null)
    setProblem(null)
    if (newMpin !== confirmMpin) { setProblem('MPINs do not match'); return }
    setBusy(true)
    try {
      await api('/api/me/mpin', {
        method: 'POST',
        body: JSON.stringify({ current_mpin: currentMpin, mpin: newMpin, mpin_confirm: confirmMpin }),
      })
      setCurrentMpin(''); setNewMpin(''); setConfirmMpin('')
      setNotice('MPIN changed. Use the new one at your next sign-in.')
    } catch (err) {
      setProblem(err instanceof Error ? err.message : 'Could not change the MPIN')
    } finally {
      setBusy(false)
    }
  }
```

and, between the password form and the sign-out row, the form:

```tsx
      <form onSubmit={submitMpin} className="space-y-3">
        <div className="flex flex-wrap items-start gap-4">
          <PinInput id="current-mpin" label="Current MPIN" value={currentMpin} onChange={setCurrentMpin} disabled={busy} />
          <PinInput id="new-mpin" label="New MPIN" value={newMpin} onChange={setNewMpin} disabled={busy} />
          <PinInput id="confirm-mpin" label="Confirm new MPIN" value={confirmMpin} onChange={setConfirmMpin} disabled={busy} />
        </div>
        <Button type="submit" disabled={busy || currentMpin.length !== 6 || newMpin.length !== 6 || confirmMpin.length !== 6}>
          Change MPIN
        </Button>
      </form>
```

- [ ] **Step 4: README**

In `README.md`, directly after the paragraph that ends "switches between them in the dashboard." (the "Multi-user, multi-org" intro), add:

```markdown
Every sign-in has two steps. After the email and password, the dashboard
asks for a six-digit **MPIN**; nothing in the platform answers until it is
entered, and a first-time user is made to choose one before going further.
Five wrong tries lock the MPIN step for fifteen minutes; "Forgot MPIN?"
re-verifies the password and sets a new one. Members change their MPIN
from Members → Your login.
```

- [ ] **Step 5: Run the gate**

Run: `npm test` → all green.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/components/AccountSecurity.tsx dashboard/src/pages/Members.test.tsx README.md
git commit -m "feat(dashboard): change your MPIN from Account security; README explains the two-step sign-in

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 9: Full gates and hand-off

**Files:** none modified.

- [ ] **Step 1: API suite on Windows** — from `api/`: `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider`. Expected: green except the pre-existing Windows-only failures.
- [ ] **Step 2: API suite in the Linux container** (memory note `api-tests-in-docker`; about 14 minutes) so `test_events_ws.py`, including the new half-session refusal, actually runs. Expected: all pass.
- [ ] **Step 3: Dashboard gate** — from `dashboard/`: `npm test`. Expected: prover ALL PASS, tsc clean, every file green.
- [ ] **Step 4: Grep gates** — from the repo root:
  `grep -rn "PIN code\|OTP\|One-time" dashboard/src --include='*.tsx' | grep -v test` → nothing (the word is MPIN).
  `grep -rn "require_user\b" api/src/api/routes/mpin.py` → only the change route uses it; the other three use `require_half_session`.
- [ ] **Step 5: Hand off** — use the superpowers:finishing-a-development-branch skill. Branch `mpin-second-factor`, base `main`.

---

## Rollout (manual, on the owner's say-so, after merge)

The migrate image bakes in `db/migrations/`; the api must be stopped before migrate so the old container never writes against the new columns' absence.

```bash
cd ~/mirrorfleet && git pull
sudo docker compose build migrate api
sudo docker compose stop api
sudo docker compose run --rm migrate          # prints: applied: ['021_mpin.sql']
sudo docker compose up -d api
```

Then sign in, choose the MPIN, sign out, sign in again and confirm the "Enter your MPIN" screen precedes the desk. Open dashboard tabs get a 401 `MPIN required` on their next request and land on `/mpin`. The copier is untouched.
