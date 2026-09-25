# Single Admin Role Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the `owner`, `admin` and `trader` membership roles into one stored role, `admin`, across the API, the database constraints, the dashboard, the tests and the README.

**Architecture:** The API's rank table shrinks to `investor < viewer < admin` and the nine routes that required `trader` or `owner` now require `admin`; the last-owner guard becomes a last-admin guard. One migration rewrites existing rows and tightens the CHECK constraints so the old names cannot return. The dashboard's role type drops to three values and every `can()` gate resolves to Admin, so page gates need no edits; only the Members page's own-row rule and pickers change. The copier is untouched.

**Tech Stack:** Python 3.12 / FastAPI / psycopg 3 / pytest (real Postgres); React 18 / TypeScript 5 / vitest; plain SQL migrations applied by `db/migrate.py`.

**Spec:** `docs/superpowers/specs/2026-09-25-single-admin-role-design.md`

## Global Constraints

- Work on branch `single-admin-role` (already created off `main`; the spec commit is on it).
- Stored role values after this change: exactly `admin`, `viewer`, `investor`. Never write `owner` or `trader` anywhere except in the migration and in tests that prove they are rejected.
- Rank table: `{"investor": -1, "viewer": 0, "admin": 1}` on the server; `{ investor: -1, viewer: 0, admin: 1 }` in the dashboard.
- Error text for the guard: `An org must keep at least one admin` (server), `An organization must keep at least one admin.` (dashboard).
- API tests need Docker Desktop running and `docker compose up -d postgres`. Run them from `api/` with these three variables set (Git Bash; substitute `POSTGRES_PASSWORD` from the repo-root `.env`). Use `127.0.0.1`, never `localhost` (IPv6 resolution hangs psycopg on this machine):

  ```bash
  export TEST_POSTGRES_ADMIN_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader"
  export TEST_POSTGRES_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader_test"
  export PYTHONPATH="$(pwd -W)/src"
  .venv/Scripts/python -m pytest <files> -q -p no:cacheprovider
  ```

  Never run the api suite while a copier suite is running (they share `copytrader_test`).
- Dashboard tests run from `dashboard/` with `npm test` (typecheck then vitest). A single file: `npx vitest run src/lib/roles.test.ts`.
- Every commit message ends with the line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- The copier service and `copier/` tests are not touched by any task.

---

## File map

| File | Change |
|---|---|
| `api/src/api/rbac.py` | `ROLE_RANK` becomes three entries |
| `api/src/api/routes/orgs.py` | creator is `admin`; last-admin guard; invite roles; retired names rejected |
| `api/src/api/routes/trading.py` | four decorators `trader` → `admin` |
| `api/src/api/routes/webhooks.py` | `GET webhook` decorator `trader` → `admin` |
| `api/src/api/routes/mt5.py` | `GET symbol-aliases` decorator `trader` → `admin` |
| `api/src/api/auth.py` | bootstrap inserts `admin` |
| `api/tests/test_rbac_matrix.py`, `test_orgs.py`, `test_mt5.py`, `test_trading.py`, `test_webhooks.py`, `test_risk_rules.py`, `test_auth.py` + 10 seed-only files | roles rewritten |
| `db/migrations/020_single_admin.sql` (new) | data rewrite + constraints |
| `api/tests/test_migration_020.py` (new) | post-migration shape + upgrade path |
| `dashboard/src/lib/roles.ts`, `roles.test.ts` | three roles |
| `dashboard/src/pages/Members.tsx`, `Members.test.tsx`, `Members.investor.test.tsx` | own-row rule, pickers, messages |
| 12 other dashboard test files | mocked roles swapped |
| `e2e/test_full_stack.py` | seed `admin` |
| `README.md`, `docs/superpowers/specs/2026-08-18-multi-org-rbac-design.md` | role docs |

---

### Task 1: API — three roles, last-admin guard, tests rewritten

The API and its tests change together so the suite is green at the end of the task: `admin` is already a valid stored value under the current CHECK constraint, so nothing here depends on the migration.

**Files:**
- Modify: `api/src/api/rbac.py:13`
- Modify: `api/src/api/routes/orgs.py:38-60, 102-125, 144-232`
- Modify: `api/src/api/routes/trading.py:50,65,80,97`
- Modify: `api/src/api/routes/webhooks.py:878`
- Modify: `api/src/api/routes/mt5.py:391`
- Modify: `api/src/api/auth.py:55-58, 88-91`
- Modify: `api/tests/test_rbac_matrix.py`
- Modify: `api/tests/test_orgs.py`
- Modify: `api/tests/test_mt5.py:456-467, 532-543, 641-660`
- Modify: `api/tests/test_trading.py:232-246`
- Modify: `api/tests/test_webhooks.py:781-790`
- Modify: `api/tests/test_risk_rules.py:82-84`
- Modify (seed swap only): `api/tests/test_accounts.py`, `test_auth.py`, `test_events_ws.py`, `test_insights.py`, `test_investor_portal.py`, `test_migration_014.py`, `test_migration_017.py`, `test_migration_019.py`, `test_mt5.py`, `test_trading.py`

**Interfaces:**
- Produces: `api.rbac.ROLE_RANK == {"investor": -1, "viewer": 0, "admin": 1}`; `POST /api/orgs` returns `{"id", "name", "role": "admin"}`; `POST /api/orgs/{id}/invites` accepts `role` in `("admin", "viewer", "investor")` and answers 400 with detail `Invites can grant admin, viewer, or investor` otherwise; `PATCH members/{uid}` answers 400 `Unknown role` for anything outside `ROLE_RANK` and 409 `An org must keep at least one admin` for the last admin.

- [ ] **Step 1: Rewrite the role tests in `api/tests/test_orgs.py`**

Replace these six tests in place (keep `_register`, `_csrf` and the other tests unchanged):

```python
def test_create_org_makes_creator_admin(app_client):
    _register(app_client)
    r = app_client.post("/api/orgs", json={"name": "Alpha Desk"},
                        headers=_csrf(app_client))
    assert r.status_code == 201
    org = r.json()
    assert org["name"] == "Alpha Desk" and org["role"] == "admin"
    me = app_client.get("/api/me").json()
    assert me["orgs"] == [{"id": org["id"], "name": "Alpha Desk", "role": "admin"}]
```

```python
def test_invite_roundtrip(app_client, make_user, login_as):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                          json={"role": "admin"}, headers=_csrf(app_client))
    assert inv.status_code == 201
    token = inv.json()["token"]

    joiner = make_user(email="join@example.com")
    login_as(app_client, joiner)
    r = app_client.post("/api/orgs/join", json={"token": token},
                        headers=_csrf(app_client))
    assert r.status_code == 200
    assert r.json() == {"org_id": org["id"], "role": "admin"}
    # single-use
    r2 = app_client.post("/api/orgs/join", json={"token": token},
                         headers=_csrf(app_client))
    assert r2.status_code == 410
```

Replace `test_invite_cannot_grant_owner` with:

```python
@pytest.mark.parametrize("role", ["owner", "trader", "guest"])
def test_invite_rejects_retired_and_unknown_roles(app_client, role):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    r = app_client.post(f"/api/orgs/{org['id']}/invites",
                        json={"role": role}, headers=_csrf(app_client))
    assert r.status_code == 400
```

Replace `test_member_role_change_and_last_owner_invariant` with:

```python
def test_member_role_change_and_last_admin_invariant(app_client, make_user, login_as):
    admin = _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                          json={"role": "viewer"}, headers=_csrf(app_client)).json()
    member = make_user(email="m@example.com")
    login_as(app_client, member)
    app_client.post("/api/orgs/join", json={"token": inv["token"]},
                    headers=_csrf(app_client))

    # a viewer cannot change roles
    r = app_client.patch(f"/api/orgs/{org['id']}/members/{admin['id']}",
                         json={"role": "viewer"}, headers=_csrf(app_client))
    assert r.status_code == 403

    login_as(app_client, {"email": "owner@example.com", "password": "a-solid-password"})
    # the admin promotes the member to admin
    r = app_client.patch(f"/api/orgs/{org['id']}/members/{member['id']}",
                         json={"role": "admin"}, headers=_csrf(app_client))
    assert r.status_code == 200
    members = app_client.get(f"/api/orgs/{org['id']}/members").json()
    assert sum(1 for m in members if m["role"] == "admin") == 2
    # two admins now; demoting one is fine
    r = app_client.patch(f"/api/orgs/{org['id']}/members/{member['id']}",
                         json={"role": "viewer"}, headers=_csrf(app_client))
    assert r.status_code == 200
    # demoting the LAST admin is rejected
    r = app_client.patch(f"/api/orgs/{org['id']}/members/{admin['id']}",
                         json={"role": "viewer"}, headers=_csrf(app_client))
    assert r.status_code == 409
    # removing the last admin is rejected
    r = app_client.delete(f"/api/orgs/{org['id']}/members/{admin['id']}",
                          headers=_csrf(app_client))
    assert r.status_code == 409
    # retired names are not a target role
    r = app_client.patch(f"/api/orgs/{org['id']}/members/{member['id']}",
                         json={"role": "owner"}, headers=_csrf(app_client))
    assert r.status_code == 400
```

Replace `test_member_can_leave_but_last_owner_cannot` with:

```python
def test_member_can_leave_but_last_admin_cannot(app_client, make_user, login_as):
    admin = _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                          json={"role": "viewer"}, headers=_csrf(app_client)).json()
    member = make_user(email="m@example.com")
    login_as(app_client, member)
    app_client.post("/api/orgs/join", json={"token": inv["token"]},
                    headers=_csrf(app_client))
    # a viewer can remove THEMSELVES (leave) even though they are not admin
    r = app_client.delete(f"/api/orgs/{org['id']}/members/{member['id']}",
                          headers=_csrf(app_client))
    assert r.status_code == 204
    assert app_client.get(f"/api/orgs/{org['id']}").status_code == 404
    # the last admin cannot leave
    login_as(app_client, {"email": "owner@example.com", "password": "a-solid-password"})
    r = app_client.delete(f"/api/orgs/{org['id']}/members/{admin['id']}",
                          headers=_csrf(app_client))
    assert r.status_code == 409
```

Replace `test_invite_create_requires_admin_role` with:

```python
def test_invite_create_requires_admin_role(app_client, make_user, login_as):
    """The admin threshold on invite creation must actually be enforced:
    a viewer is rejected, an invited admin succeeds."""
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    viewer_inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                                 json={"role": "viewer"}, headers=_csrf(app_client)).json()
    viewer = make_user(email="viewer@example.com")
    login_as(app_client, viewer)
    app_client.post("/api/orgs/join", json={"token": viewer_inv["token"]},
                    headers=_csrf(app_client))
    # a viewer cannot create invites
    r = app_client.post(f"/api/orgs/{org['id']}/invites",
                        json={"role": "viewer"}, headers=_csrf(app_client))
    assert r.status_code == 403

    login_as(app_client, {"email": "owner@example.com", "password": "a-solid-password"})
    admin_inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                                json={"role": "admin"}, headers=_csrf(app_client)).json()
    admin = make_user(email="admin@example.com")
    login_as(app_client, admin)
    app_client.post("/api/orgs/join", json={"token": admin_inv["token"]},
                    headers=_csrf(app_client))
    # an invited admin CAN create invites
    r = app_client.post(f"/api/orgs/{org['id']}/invites",
                        json={"role": "viewer"}, headers=_csrf(app_client))
    assert r.status_code == 201
```

- [ ] **Step 2: Rewrite `api/tests/test_rbac_matrix.py` for three roles**

Change the two constants:

```python
ROLES = ["investor", "viewer", "admin"]
```

```python
RANK = {"investor": -1, "viewer": 0, "admin": 1}
```

In `MATRIX`, change the `min_role` of these six rows from `"trader"` or `"owner"` to `"admin"`:

```python
    ("POST",   "orders",                         {"account_id": 100, "symbol": "EURUSD",
                                                  "side": "BUY", "order_type": "MARKET",
                                                  "volume_lots": 0.01},         "admin"),
    ("POST",   "positions/close",                {"account_id": 100, "position_id": 1}, "admin"),
    ("POST",   "orders/cancel",                  {"account_id": 100, "order_id": 1},    "admin"),
```

```python
    ("GET",    "accounts/100/symbol-aliases",    None,                           "admin"),
```

```python
    ("PATCH",  "",                               {"name": "Renamed"},            "admin"),
    ("DELETE", "",                               None,                           "admin"),
```

Replace the last test:

```python
def test_destructive_rows_allowed(matrix_org, login_as):
    """The allowed-role half of the destructive rows, run last against a
    dedicated fixture instance."""
    client, org_id, users, _ = matrix_org
    login_as(client, users["admin"])
    r = _call(client, "DELETE", org_id, "accounts/100/connection", None)
    assert r.status_code == 200
    r = _call(client, "DELETE", org_id, "", None)
    assert r.status_code == 204
```

- [ ] **Step 3: Rewrite the trader-specific tests in `test_mt5.py`, `test_trading.py`, `test_webhooks.py`, `test_risk_rules.py`**

`api/tests/test_mt5.py` — replace `test_a_trader_can_neither_add_nor_rotate` with:

```python
def test_a_viewer_can_neither_add_nor_rotate(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    mine = seed_mt5(db, org_id, KEY)
    viewer = make_user(email="viewer@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'viewer')",
                     (org_id, viewer["id"]))
    login_as(client, viewer)

    assert _create(client, org_id).status_code == 403
    assert client.post(f"/api/orgs/{org_id}/mt5/accounts/{mine}/key",
                       headers=_csrf(client)).status_code == 403
```

Replace `test_a_trader_cannot_remove` with:

```python
def test_a_viewer_cannot_remove(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    mine = seed_mt5(db, org_id, KEY)
    viewer = make_user(email="viewer2@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'viewer')",
                     (org_id, viewer["id"]))
    login_as(client, viewer)

    assert client.delete(f"/api/orgs/{org_id}/mt5/accounts/{mine}",
                         headers=_csrf(client)).status_code == 403
```

Replace `test_a_trader_reads_aliases_but_only_an_admin_writes_them` with:

```python
def test_only_an_admin_reads_or_writes_aliases(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    account_id = seed_mt5(db, org_id, KEY)
    viewer = make_user(email="viewer@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'viewer')",
                     (org_id, viewer["id"]))
    path = f"/api/orgs/{org_id}/accounts/{account_id}/symbol-aliases"

    # org_client is signed in as the org's admin: both directions pass the
    # role check (the PUT may still be a 400 for an unknown broker name --
    # that is validation, after authorization).
    assert client.get(path).status_code == 200
    assert client.put(path, json={"aliases": {"XAUUSD": "GOLD.r"}},
                      headers=_csrf(client)).status_code not in (401, 403, 404)
    login_as(client, viewer)
    assert client.get(path).status_code == 403
    assert client.put(path, json={"aliases": {"XAUUSD": "GOLD.r"}},
                      headers=_csrf(client)).status_code == 403
```

`api/tests/test_trading.py` — replace `test_trader_can_order_but_not_close_all` with:

```python
def test_viewer_cannot_order(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    seed(100, role="master")
    viewer = make_user(email="v@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'viewer')",
            (org_id, viewer["id"]))
    login_as(client, viewer)
    r = client.post(f"/api/orgs/{org_id}/orders",
                    json={"account_id": 100, "symbol": "EURUSD", "side": "BUY",
                          "order_type": "MARKET", "volume_lots": 0.01},
                    headers=_csrf(client))
    assert r.status_code == 403
```

`api/tests/test_webhooks.py` — replace `test_a_trader_can_read_but_not_rotate_or_enable` with:

```python
def test_a_viewer_can_neither_read_nor_change_the_webhook(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    viewer = make_user(email="viewer@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'viewer')",
                     (org_id, viewer["id"]))
    login_as(client, viewer)
    assert client.get(f"/api/orgs/{org_id}/webhook").status_code == 403
    assert client.post(f"/api/orgs/{org_id}/webhook/secret", headers=_csrf(client)).status_code == 403
    assert client.put(f"/api/orgs/{org_id}/webhook", json={"enabled": True},
                      headers=_csrf(client)).status_code == 403
```

`api/tests/test_risk_rules.py` — in the docstring of `test_viewer_role_cannot_write`, replace the last sentence fragment

```
    the same way test_webhooks.py's test_a_trader_can_read_but_not_rotate_or_enable
    does for "trader"."""
```

with

```
    the same way test_webhooks.py's test_a_viewer_can_neither_read_nor_change_the_webhook
    does."""
```

- [ ] **Step 4: Swap the remaining `"owner"` seeds to `"admin"`**

From `api/tests/` in Git Bash:

```bash
sed -i 's/"owner")/"admin")/g; s/"owner",/"admin",/g' \
  test_accounts.py test_auth.py test_events_ws.py test_insights.py \
  test_investor_portal.py test_migration_014.py test_migration_017.py \
  test_migration_019.py test_mt5.py test_trading.py
grep -rnE "'(owner|trader)'|\"(owner|trader)\"" . --include='*.py'
```

Expected grep output: only the three parametrised strings in `test_orgs.py` (`"owner"`, `"trader"` in `test_invite_rejects_retired_and_unknown_roles` and the `{"role": "owner"}` body in the last-admin test). Nothing else.

- [ ] **Step 5: Run the rewritten test files to verify they fail**

Run (from `api/`, env as in Global Constraints):

```bash
.venv/Scripts/python -m pytest tests/test_orgs.py tests/test_rbac_matrix.py tests/test_auth.py -q -p no:cacheprovider
```

Expected: failures such as `assert 'owner' == 'admin'`, `KeyError: 'admin'`-free 403s where 200 is expected, and the `RANK`-driven matrix rows for `admin` on `POST orders` etc. Do not proceed if the run errors at collection.

- [ ] **Step 6: Change the rank table**

`api/src/api/rbac.py` line 10-13, replace the comment and constant:

```python
# 'investor' sits BELOW viewer on purpose: every desk endpoint asks for
# viewer or higher, so an investor is refused everywhere except the
# investor router, which resolves the caller's own linked account itself.
# 'admin' is the one desk-running role: what used to be owner, admin and
# trader (docs/superpowers/specs/2026-09-25-single-admin-role-design.md).
ROLE_RANK = {"investor": -1, "viewer": 0, "admin": 1}
```

- [ ] **Step 7: Change the nine route minimums**

`api/src/api/routes/trading.py` lines 50, 65, 80, 97: `require_org_role("trader")` → `require_org_role("admin")`.

`api/src/api/routes/webhooks.py` line 878: `require_org_role("trader")` → `require_org_role("admin")`.

`api/src/api/routes/mt5.py` line 391: `require_org_role("trader")` → `require_org_role("admin")`.

`api/src/api/routes/orgs.py` lines 105, 119, 148: `require_org_role("owner")` → `require_org_role("admin")`.

Verify with:

```bash
grep -rn 'require_org_role("\(trader\|owner\)")' api/src
```

Expected: no output.

- [ ] **Step 8: Rewrite the membership logic in `api/src/api/routes/orgs.py`**

In `create_org` (lines 53-59), change the insert and the return:

```python
            conn.execute(
                "INSERT INTO org_memberships (org_id, user_id, role) "
                "VALUES (%s, %s, 'admin')",
                (org_id, user_id),
            )
        return {"id": org_id, "name": name, "role": "admin"}
```

In `patch_member` (lines 151-181) replace the body after the `if body.role not in ROLE_RANK` check so it reads:

```python
        with conn.transaction():
            # Lock the whole admin set (not just the target row) so two
            # concurrent demotions of two different admins can't both see
            # count=2 and both proceed to zero admins. Under READ COMMITTED
            # the second transaction blocks here until the first commits,
            # then re-reads and sees the reduced admin set.
            admin_rows = conn.execute(
                "SELECT user_id FROM org_memberships "
                "WHERE org_id = %s AND role = 'admin' FOR UPDATE",
                (ctx.org_id,),
            ).fetchall()
            admin_ids = {r[0] for r in admin_rows}
            row = conn.execute(
                "SELECT role FROM org_memberships WHERE org_id = %s AND user_id = %s "
                "FOR UPDATE",
                (ctx.org_id, member_user_id),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Member not found")
            if row[0] == "admin" and body.role != "admin" and len(admin_ids) == 1:
                raise HTTPException(
                    status_code=409, detail="An org must keep at least one admin")
            conn.execute(
                "UPDATE org_memberships SET role = %s WHERE org_id = %s AND user_id = %s",
                (body.role, ctx.org_id, member_user_id),
            )
        return {"user_id": member_user_id, "role": body.role}
```

In `remove_member` (lines 187-214) replace the comment, the role check and the guard:

```python
        # Admins may remove anyone; anyone may remove THEMSELVES (leave).
        if ctx.role != "admin" and member_user_id != ctx.user_id:
            raise HTTPException(status_code=403, detail="Insufficient role")
        with conn.transaction():
            # Lock the whole admin set (not just the target row) so two
            # concurrent removals of two different admins can't both see
            # count=2 and both proceed to zero admins. Under READ COMMITTED
            # the second transaction blocks here until the first commits,
            # then re-reads and sees the reduced admin set.
            admin_rows = conn.execute(
                "SELECT user_id FROM org_memberships "
                "WHERE org_id = %s AND role = 'admin' FOR UPDATE",
                (ctx.org_id,),
            ).fetchall()
            admin_ids = {r[0] for r in admin_rows}
            row = conn.execute(
                "SELECT role FROM org_memberships WHERE org_id = %s AND user_id = %s "
                "FOR UPDATE",
                (ctx.org_id, member_user_id),
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Member not found")
            if row[0] == "admin" and len(admin_ids) == 1:
                raise HTTPException(
                    status_code=409, detail="An org must keep at least one admin")
            conn.execute(
                "DELETE FROM org_memberships WHERE org_id = %s AND user_id = %s",
                (ctx.org_id, member_user_id),
            )
```

Keep the `await broadcaster.close_for(...)` line and its comment; change the words "an owner removing someone else" in that comment to "an admin removing someone else".

In `create_invite` (lines 228-230):

```python
        if body.role not in ("admin", "viewer", "investor"):
            raise HTTPException(
                status_code=400, detail="Invites can grant admin, viewer, or investor")
```

- [ ] **Step 9: Bootstrap inserts `admin`**

`api/src/api/auth.py` line 55-58 docstring: change "make them its Owner" to "make them its Admin". Line 90: `'owner'` → `'admin'` so the insert reads:

```python
            conn.execute(
                "INSERT INTO org_memberships (org_id, user_id, role) "
                "VALUES (%s, %s, 'admin') ON CONFLICT DO NOTHING",
                (org[0], user_id),
            )
```

- [ ] **Step 10: Run the whole api suite**

Run (from `api/`, env as in Global Constraints; about 8 minutes):

```bash
.venv/Scripts/python -m pytest tests -q -p no:cacheprovider
```

Expected: all pass. If `test_migration_019.py` or any `make_org` caller still fails with `CheckViolation` or `KeyError: 'owner'`, the Step 4 grep missed a seed; fix it, do not weaken the rank table.

- [ ] **Step 11: Commit**

```bash
git add api/src api/tests
git commit -m "feat(api): one admin role -- owner, admin and trader collapse into admin

ROLE_RANK is investor < viewer < admin. The four trading routes, the webhook
read, the symbol-alias read and the three owner-only org routes now require
admin; the last-owner guard is a last-admin guard; invites and role changes
accept admin, viewer and investor only. The bootstrap user claims the legacy
Default org as admin.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Migration 020 — rewrite rows, revoke open trader invites, tighten constraints

**Files:**
- Create: `db/migrations/020_single_admin.sql`
- Create: `api/tests/test_migration_020.py`

**Interfaces:**
- Consumes: the three-role API from Task 1 (the suite's `make_org` seeds only write `admin`, `viewer`, `investor` now).
- Produces: `org_memberships_role_check` and `org_invites_role_check` both `CHECK (role IN ('admin', 'viewer', 'investor'))`; no `owner`/`trader` rows remain.

- [ ] **Step 1: Write the failing migration test**

Create `api/tests/test_migration_020.py`:

```python
# api/tests/test_migration_020.py
"""Migration 020: Owner, Admin and Trader collapse into one 'admin' role.
conftest applies EVERY migration, so the `db` tests assert the post-migration
shape; the upgrade path (a live database whose rows still say owner and
trader) is exercised on a scratch database stopped at 019."""
import pathlib

import psycopg
import pytest

from conftest import ADMIN_DSN

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "db" / "migrations"
UPGRADE_DB = "copytrader_mig020"
UPGRADE_DSN = ADMIN_DSN.rsplit("/", 1)[0] + f"/{UPGRADE_DB}"


def test_migration_020_is_recorded_right_after_019(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "020_single_admin.sql" in names
    assert names.index("020_single_admin.sql") == names.index("019_investor_portal.sql") + 1


@pytest.mark.parametrize("role", ["owner", "trader"])
def test_retired_roles_are_rejected_by_both_tables(db, make_user, make_org, role):
    admin = make_user()
    other = make_user(email="other@example.com")
    org_id = make_org(members=[(admin, "admin")])
    with psycopg.connect(db, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
                (org_id, other["id"], role))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO org_invites (org_id, role, token_hash, created_by, expires_at) "
                "VALUES (%s, %s, 'h020', %s, now() + interval '1 day')",
                (org_id, role, admin["id"]))


def test_the_three_roles_are_still_accepted(db, make_user, make_org):
    admin = make_user()
    viewer = make_user(email="v@example.com")
    investor = make_user(email="i@example.com")
    org_id = make_org(members=[(admin, "admin"), (viewer, "viewer"), (investor, "investor")])
    with psycopg.connect(db, autocommit=True) as conn:
        roles = sorted(r[0] for r in conn.execute(
            "SELECT role FROM org_memberships WHERE org_id = %s", (org_id,)).fetchall())
        assert roles == ["admin", "investor", "viewer"]
        for role in ("admin", "viewer", "investor"):
            conn.execute(
                "INSERT INTO org_invites (org_id, role, token_hash, created_by, expires_at) "
                "VALUES (%s, %s, %s, %s, now() + interval '1 day')",
                (org_id, role, f"h020-{role}", admin["id"]))


def test_upgrade_rewrites_roles_and_revokes_open_trader_invites(database):
    """The live database has owner/admin/trader/viewer/investor rows and a
    few invites when 020 arrives. Owners and traders become admins, an
    unconsumed trader link is revoked (not upgraded), a consumed one is
    relabelled so it passes the new constraint, and nothing else moves."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {UPGRADE_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {UPGRADE_DB}")
    try:
        with psycopg.connect(UPGRADE_DSN) as conn:
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name.startswith("020_"):
                    continue
                conn.execute(path.read_text())
            conn.execute(
                "INSERT INTO users (id, email, password_hash, display_name) VALUES "
                "(1, 'o@x.com', 'h', 'O'), (2, 'a@x.com', 'h', 'A'), "
                "(3, 't@x.com', 'h', 'T'), (4, 'v@x.com', 'h', 'V'), "
                "(5, 'i@x.com', 'h', 'I')")
            conn.execute("INSERT INTO orgs (id, name) VALUES (1, 'Desk')")
            conn.execute(
                "INSERT INTO org_memberships (org_id, user_id, role) VALUES "
                "(1, 1, 'owner'), (1, 2, 'admin'), (1, 3, 'trader'), "
                "(1, 4, 'viewer'), (1, 5, 'investor')")
            conn.execute(
                "INSERT INTO org_invites "
                "(org_id, role, token_hash, created_by, expires_at, consumed_at) VALUES "
                "(1, 'trader', 'open-trader', 1, now() + interval '1 day', NULL), "
                "(1, 'trader', 'used-trader', 1, now() + interval '1 day', now()), "
                "(1, 'viewer', 'open-viewer', 1, now() + interval '1 day', NULL)")
            conn.execute((MIGRATIONS_DIR / "020_single_admin.sql").read_text())
            conn.commit()

        with psycopg.connect(UPGRADE_DSN, autocommit=True) as conn:
            roles = dict(conn.execute(
                "SELECT user_id, role FROM org_memberships ORDER BY user_id").fetchall())
            assert roles == {1: "admin", 2: "admin", 3: "admin", 4: "viewer", 5: "investor"}
            invites = dict(conn.execute(
                "SELECT token_hash, role FROM org_invites").fetchall())
            assert invites == {"used-trader": "admin", "open-viewer": "viewer"}
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {UPGRADE_DB} WITH (FORCE)")
```

- [ ] **Step 2: Run it to verify it fails**

Run (from `api/`):

```bash
.venv/Scripts/python -m pytest tests/test_migration_020.py -q -p no:cacheprovider
```

Expected: `test_migration_020_is_recorded_right_after_019` fails on `assert "020_single_admin.sql" in names`; the retired-role tests fail with `DID NOT RAISE`; the upgrade test fails with `FileNotFoundError` for the migration file.

- [ ] **Step 3: Write the migration**

Create `db/migrations/020_single_admin.sql`:

```sql
-- One person runs the desk. 'owner', 'admin' and 'trader' were three ranks
-- for the same human; they become one stored role, 'admin', carrying the
-- union of their permissions. See
-- docs/superpowers/specs/2026-09-25-single-admin-role-design.md.

UPDATE org_memberships SET role = 'admin' WHERE role IN ('owner', 'trader');

-- An unconsumed Trader link sitting in someone's inbox must not silently
-- become full control: revoke it, the admin re-issues it if still wanted.
-- Consumed ones only need to satisfy the new constraint.
DELETE FROM org_invites WHERE role = 'trader' AND consumed_at IS NULL;
UPDATE org_invites SET role = 'admin' WHERE role = 'trader';

ALTER TABLE org_memberships DROP CONSTRAINT org_memberships_role_check;
ALTER TABLE org_memberships ADD CONSTRAINT org_memberships_role_check
    CHECK (role IN ('admin', 'viewer', 'investor'));
ALTER TABLE org_invites DROP CONSTRAINT org_invites_role_check;
ALTER TABLE org_invites ADD CONSTRAINT org_invites_role_check
    CHECK (role IN ('admin', 'viewer', 'investor'));
```

- [ ] **Step 4: Run the migration tests to verify they pass**

The session-scoped `database` fixture recreates `copytrader_test` and applies every migration, so a fresh pytest process picks the new file up automatically.

```bash
.venv/Scripts/python -m pytest tests/test_migration_020.py tests/test_migration_019.py tests/test_orgs.py -q -p no:cacheprovider
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/020_single_admin.sql api/tests/test_migration_020.py
git commit -m "feat(db): migration 020 -- owner and trader rows become admin, constraints keep only admin/viewer/investor

Unconsumed trader invites are revoked rather than upgraded; consumed ones are
relabelled so they pass the new CHECK.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Dashboard — three roles, Members page own-row rule and pickers

The role type change makes every `'owner'` / `'trader'` in a typed test position a compile error, so the type change, the Members page and every test that mocks a role change in one task. The order below keeps `tsc` as the safety net: after Step 6 it must report zero errors.

**Files:**
- Modify: `dashboard/src/lib/roles.ts`
- Modify: `dashboard/src/lib/roles.test.ts`
- Modify: `dashboard/src/pages/Members.tsx:11-19, 48-58, 67-77, 113-121, 153`
- Modify: `dashboard/src/pages/Members.test.tsx`
- Modify: `dashboard/src/pages/Members.investor.test.tsx:18, 21`
- Modify (role swaps): `dashboard/src/App.test.tsx`, `components/Layout.test.tsx`, `pages/Accounts.test.tsx`, `pages/Automation.test.tsx`, `pages/Join.test.tsx`, `pages/Overview.test.tsx`, `pages/Performance.test.tsx`, `pages/Positions.test.tsx`, `pages/Trade.test.tsx`, `pages/Welcome.test.tsx`

**Interfaces:**
- Produces: `Role = 'investor' | 'viewer' | 'admin'`; `can(role, action)` true only for `'admin'` for all three actions; `roleLabel()` → `Admin` / `Viewer` / `Investor`; `OFFERED_ROLES = ['admin', 'viewer', 'investor']`.

- [ ] **Step 1: Rewrite `dashboard/src/lib/roles.test.ts`**

```ts
import { describe, expect, it } from 'vitest'
import { can, roleLabel, OFFERED_ROLES } from './roles'

describe('can', () => {
  it('gates trade, control and member management at admin', () => {
    for (const action of ['trade', 'control', 'manage_members'] as const) {
      expect(can('investor', action)).toBe(false)
      expect(can('viewer', action)).toBe(false)
      expect(can('admin', action)).toBe(true)
    }
  })
  it('denies for missing or unknown roles', () => {
    expect(can(null, 'trade')).toBe(false)
    expect(can(undefined, 'control')).toBe(false)
    expect(can('owner' as never, 'control')).toBe(false)
  })
})

describe('roleLabel', () => {
  it('names the three roles and passes anything else through', () => {
    expect(roleLabel('admin')).toBe('Admin')
    expect(roleLabel('viewer')).toBe('Viewer')
    expect(roleLabel('investor')).toBe('Investor')
    expect(roleLabel('owner')).toBe('owner')
  })
})

describe('OFFERED_ROLES', () => {
  it('offers exactly admin, viewer and investor', () => {
    expect(OFFERED_ROLES).toEqual(['admin', 'viewer', 'investor'])
  })
})
```

- [ ] **Step 2: Run it to verify it fails**

```bash
npx vitest run src/lib/roles.test.ts
```

Expected: FAIL — `can('viewer','trade')` passes today only for trader+, so the admin-gate test fails on the `OFFERED_ROLES` equality and on `roleLabel('owner')` returning `Admin`.

- [ ] **Step 3: Rewrite `dashboard/src/lib/roles.ts`**

```ts
export type Role = 'investor' | 'viewer' | 'admin'
export type Action = 'trade' | 'control' | 'manage_members'

// investor sits below viewer: it may open only the investor portal.
const RANK: Record<Role, number> = { investor: -1, viewer: 0, admin: 1 }

// One person runs the desk. Everything that used to need Trader, Admin or
// Owner needs the single Admin role; the action names stay so each call
// site still reads as what it gates.
const THRESHOLD: Record<Action, number> = {
  trade: RANK.admin,
  control: RANK.admin,
  manage_members: RANK.admin,
}

/** UI-side mirror of the server's role matrix — hides controls the server
 * would reject. The server enforces regardless. */
export function can(role: Role | null | undefined, action: Action): boolean {
  if (!role || !(role in RANK)) return false
  return RANK[role] >= THRESHOLD[action]
}

const ROLE_LABEL: Record<Role, string> = {
  admin: 'Admin',
  viewer: 'Viewer',
  investor: 'Investor',
}

export function roleLabel(role: string): string {
  return (ROLE_LABEL as Record<string, string>)[role] ?? role
}

/** Every role an admin can hand out, in the order the pickers show them. */
export const OFFERED_ROLES: Role[] = ['admin', 'viewer', 'investor']
```

- [ ] **Step 4: Run the role test to verify it passes**

```bash
npx vitest run src/lib/roles.test.ts
```

Expected: PASS (3 files' worth of describes, all green).

- [ ] **Step 5: Update `dashboard/src/pages/Members.tsx`**

Replace lines 11-19 (the comment, `ASSIGNABLE`, `INVITABLE`, `optionsFor`) with nothing — delete them. Then:

The role cell (was `{can(role, 'manage_members') && m.role !== 'owner' ? (`) becomes:

```tsx
                {/* Your own role is changed by another admin, never from here;
                    the server refuses to leave an org without one. */}
                {can(role, 'manage_members') && m.user_id !== me.user.id ? (
                  <select
                    aria-label={`Role for ${m.email}`}
                    value={m.role}
                    onChange={(e) => changeRole(m.user_id, e.target.value)}
                    className="border border-line-strong rounded bg-card px-2 py-1"
                  >
                    {OFFERED_ROLES.map((r) => (
                      <option key={r} value={r}>{roleLabel(r)}</option>
                    ))}
                  </select>
                ) : (
                  <span className="text-ink">{roleLabel(m.role)}</span>
                )}
```

The invite picker option list (was `{INVITABLE.map(...)}`) becomes:

```tsx
              {OFFERED_ROLES.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
```

Both error strings `'An organization must keep at least one owner.'` (in `changeRole` and `removeMember`) become `'An organization must keep at least one admin.'`.

- [ ] **Step 6: Update `dashboard/src/pages/Members.test.tsx`**

Replace `baseMembers`:

```ts
const baseMembers = [
  { user_id: 1, email: 'owner@x.com', display_name: 'Owner O', role: 'admin', joined_at: '2026-01-01T00:00:00Z' },
  { user_id: 2, email: 'second@x.com', display_name: 'Second S', role: 'admin', joined_at: '2026-01-02T00:00:00Z' },
  { user_id: 3, email: 'viewer@x.com', display_name: 'Viewer V', role: 'viewer', joined_at: '2026-01-03T00:00:00Z' },
]
```

Then change these tests (others stay as they are apart from the role argument swaps in the next step):

```tsx
test('lists members with roles', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  renderMembers()

  await waitFor(() => {
    expect(screen.getByText('Owner O')).toBeInTheDocument()
  })
  expect(screen.getByText('second@x.com')).toBeInTheDocument()
  expect(screen.getByText('viewer@x.com')).toBeInTheDocument()
  // Your own row is fixed: no select, just the label. Every other row,
  // including another admin's, gets the picker.
  expect(screen.queryByLabelText('Role for owner@x.com')).not.toBeInTheDocument()
  expect(screen.getAllByText('Admin').length).toBeGreaterThan(0)
  expect(screen.getByLabelText('Role for second@x.com')).toHaveValue('admin')
  expect(screen.getByLabelText('Role for viewer@x.com')).toHaveValue('viewer')
})

test('the pickers offer admin, viewer and investor', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  renderMembers()

  const assign = await screen.findByLabelText('Role for viewer@x.com')
  expect(Array.from((assign as HTMLSelectElement).options).map((o) => o.value))
    .toEqual(['admin', 'viewer', 'investor'])
  const invite = screen.getByLabelText('Invite role')
  expect(Array.from((invite as HTMLSelectElement).options).map((o) => o.value))
    .toEqual(['admin', 'viewer', 'investor'])
})

test('an admin can change another admin via the role select', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes()
  renderMembers()

  const select = await screen.findByLabelText('Role for second@x.com')
  await userEvent.selectOptions(select, 'viewer')

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/members/2',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ role: 'viewer' }) })
    )
  })
})

test('self-leave DELETEs the own membership and navigates away without an error banner', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer', 3))
  const fetchMock = mockRoutes()
  renderMembers()

  const leaveButton = await screen.findByRole('button', { name: 'Leave' })
  await userEvent.click(leaveButton)

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/orgs/1/members/3',
      expect.objectContaining({ method: 'DELETE' })
    )
    expect(navigateMock).toHaveBeenCalledWith('/welcome')
  })
  expect(screen.queryByText(/could not remove member/i)).not.toBeInTheDocument()
})

test('shows the last-admin error from the server', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes({
    'PATCH /api/orgs/1/members/2': () =>
      jsonResponse({ detail: 'An org must keep at least one admin' }, 409),
  })
  renderMembers()

  const select = await screen.findByLabelText('Role for second@x.com')
  await userEvent.selectOptions(select, 'viewer')

  await waitFor(() => {
    expect(screen.getByText(/must keep at least one admin/i)).toBeInTheDocument()
  })
})
```

Rename `test('owner can change a role via the role select', ...)` — it is replaced by the "another admin" test above (delete the old one). Rename `'non-owner sees read-only roles and no invite form'` to `'a viewer sees read-only roles and no invite form'` and inside it change `expect(screen.getByText('Admin'))` to `expect(screen.getAllByText('Admin').length).toBe(2)`. Rename `'owner can rename the org'` to `'an admin can rename the org'` and `'owner sees delete-org with type-to-confirm; others do not'` to `'an admin sees delete-org with type-to-confirm; a viewer does not'`; in that last test change the second render to `makeOrgValue('viewer', 3)`.

Finally, in the whole file replace every remaining `makeOrgValue('owner')` with `makeOrgValue('admin')` and every `makeOrgValue('admin', 4)` stays as it is.

`dashboard/src/pages/Members.investor.test.tsx`: line 18 `mockUseOrg('owner')` → `mockUseOrg('admin')`; line 21 the member fixture `user_id: 1` → `user_id: 2` (user 1 is the signed-in admin, whose own row has no picker).

- [ ] **Step 7: Swap the mocked roles in the other test files**

From `dashboard/src` in Git Bash:

```bash
sed -i "s/'owner'/'admin'/g" App.test.tsx components/Layout.test.tsx pages/Automation.test.tsx \
  pages/Overview.test.tsx pages/Performance.test.tsx pages/Positions.test.tsx pages/Welcome.test.tsx
sed -i "s/'trader'/'admin'/g" pages/Trade.test.tsx
sed -i "s/'trader'/'viewer'/g" pages/Join.test.tsx
```

Then by hand:

- `components/Layout.test.tsx` line 318 (`hides the close-all kill switch below admin`): `makeOrgValue('trader')` → `makeOrgValue('viewer')`.
- `pages/Accounts.test.tsx`: delete the whole test `'trader (below control) also gets read-only rows and no connect link'` (starts at line 637, ends at its closing `})`; the viewer version above it already covers these assertions). In `'trader sees no Add MT5 account button'` rename to `'a viewer sees no Add MT5 account button'` and `setRole('trader')` → `setRole('viewer')`. In `'below control, an MT5 row shows no Rotate key'`: `setRole('trader')` → `setRole('viewer')`.
- `pages/Overview.test.tsx` line 856-857: rename `'kill switch is hidden for a trader (below control)'` to `'kill switch is hidden for a viewer (below control)'` and `setRole('trader')` → `setRole('viewer')`.
- `pages/Positions.test.tsx`: delete the test `'trader sees close-orphan (trade) but not adopt/dismiss (control)'` (lines 237-255; no role has trade without control any more). Every remaining `setRole('trader')` in that file → `setRole('admin')` (the close-orphan, SL/TP and amend-dialog tests exercise abilities).

Verify:

```bash
grep -rnE "'(owner|trader)'" . --include='*.ts' --include='*.tsx'
```

Expected: only `src/lib/roles.test.ts` (the `'owner' as never` and `roleLabel('owner')` lines).

- [ ] **Step 8: Run the whole dashboard gate**

```bash
npm test
```

Expected: `tsc` clean, every vitest file PASS. A `tsc` error naming `'owner'` or `'trader'` means Step 7 missed a typed usage: fix that file, never widen `Role`.

- [ ] **Step 9: Commit**

```bash
git add src
git commit -m "feat(dashboard): one admin role -- three roles, own-row rule on Members, admin offered in pickers

Role is investor | viewer | admin and every can() action resolves to admin.
The Members page hides the role picker only on your own row and offers
Admin, Viewer and Investor in both pickers.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Compose e2e seed, README and spec pointer

**Files:**
- Modify: `e2e/test_full_stack.py:321, 328`
- Modify: `README.md:24-29, 40-53, 136-141, 161-168, 538, 543`
- Modify: `docs/superpowers/specs/2026-08-18-multi-org-rbac-design.md:5`

**Interfaces:**
- Consumes: the migration from Task 2 (the e2e stack applies it on boot, so a seeded `'owner'` row would violate the constraint).

- [ ] **Step 1: Seed `admin` in the compose e2e**

`e2e/test_full_stack.py` line 321: `'owner'` → `'admin'` in the SQL string. Line 328: `(org_id, "owner")` → `(org_id, "admin")`.

Verify:

```bash
grep -nE "'(owner|trader)'|\"(owner|trader)\"" e2e/*.py
```

Expected: no output.

- [ ] **Step 2: Rewrite the README's role sections**

Line numbers below are pre-edit; each edit shifts the ones after it, so locate every block by its quoted text.

Replace the paragraph at lines 24-29 (starts `Anyone can **register**`, ends `switches between them in the dashboard.`) with:

```markdown
Anyone can **register** an account on the instance (when `REGISTRATION_ENABLED`
is on; otherwise through an invite link); registering by itself grants access
to **nothing**. Access is always to a specific **organization** (org), and it
comes from a membership row: you either create an org (which makes you its
Admin) or you join one through an **invite link** an existing Admin generated
for you. A user can belong to any number of orgs and switches between them in
the dashboard.
```

Replace lines 40-53 (from `Within an org, four roles nest` through `except a last Owner.`) with:

```markdown
Within an org, three roles nest — `investor < viewer < admin`. One person
runs the desk, so Admin is the only desk-running role (it is what earlier
versions split into Owner, Admin and Trader):

| Action | Investor | Viewer | Admin |
|---|---|---|---|
| Investor portal: own linked account, deposits, withdrawals | ✓ | | |
| Overview, accounts list, positions, history, events, symbols, state, live feed | | ✓ | ✓ |
| Members list | | ✓ | ✓ |
| Manual orders, close position, cancel order, amend SL/TP | | | ✓ |
| Pause / resume / resync, copying toggle, dry-run, **close-all** | | | ✓ |
| Account role / multiplier / enable / nickname, OAuth connect & disconnect, drift remedies, MT5 accounts, symbol aliases | | | ✓ |
| Webhook settings, risk rules, investor wallet, deposit and withdrawal decisions | | | ✓ |
| Create / revoke invites, change member roles, remove members, rename org, delete org | | | ✓ |

An org always has at least one Admin: demoting or removing the last one is
rejected. Any member may leave an org themselves, except a last Admin.
```

Line 140: `its Owner — the only way to claim it.` → `its Admin — the only way to claim it.`

Replace steps 1-2 at lines 161-168 with:

```markdown
1. **Create an organization.** Whoever creates it is its Admin. Everything
   below happens inside that org, and you can create more later (one per
   customer, per desk, per master account — an org holds exactly one master).
2. **Invite your team** from the org's Members screen: generate an invite
   link for the role you want them to have (`admin`, `viewer`, or
   `investor`), and send it to them. They register (or log in) and open the
   link to join. Roles are per org, so the same person can be an Admin in one
   org and a Viewer in another. Change or revoke anyone's role from the same
   screen; your own role is changed by another Admin.
```

Line 538: `makes them the org's Owner` → `makes them the org's Admin`. Line 543: `each owner gets a \`404\`` → `each admin gets a \`404\``.

Verify no stale capitalised role names remain in the prose:

```bash
grep -nE "\bOwner\b|\bTrader\b|\btrader\b" README.md
```

Expected: no output (the lower-case `trader_login` field name does not match `\btrader\b` because of the underscore; if it appears, it is fine).

- [ ] **Step 3: Point the old RBAC spec at the new one**

`docs/superpowers/specs/2026-08-18-multi-org-rbac-design.md`: after line 5 (`**Branch:** \`worktree-multi-org\``) insert:

```markdown
**Superseded in part (2026-09-25):** the four-role ladder in §3 was
collapsed to `investor < viewer < admin`; see
`2026-09-25-single-admin-role-design.md`. Orgs, memberships, invites and the
404-not-403 rule are unchanged.
```

- [ ] **Step 4: Commit**

```bash
git add e2e/test_full_stack.py README.md docs/superpowers/specs/2026-08-18-multi-org-rbac-design.md
git commit -m "docs: README and e2e seed for the single admin role

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Full gates and hand-off

**Files:** none modified.

- [ ] **Step 1: Run the whole api suite once more**

From `api/` with the Global Constraints env (about 8 minutes):

```bash
.venv/Scripts/python -m pytest tests -q -p no:cacheprovider
```

Expected: all pass.

- [ ] **Step 2: Run the whole dashboard gate once more**

From `dashboard/`:

```bash
npm test
```

Expected: `tsc` clean, every file PASS.

- [ ] **Step 3: Confirm no retired role name is written anywhere outside the migration and the rejection tests**

From the repo root:

```bash
git grep -nE "'(owner|trader)'|\"(owner|trader)\"" -- api/src dashboard/src e2e db/migrations/020_single_admin.sql
```

Expected: only `db/migrations/020_single_admin.sql` and `dashboard/src/lib/roles.test.ts`.

- [ ] **Step 4: Hand off**

Use the superpowers:finishing-a-development-branch skill. The branch is `single-admin-role`, five commits ahead of `main` (spec + four tasks).

---

## Rollout (manual, on the owner's say-so, after merge)

The migrate image bakes in `db/migrations/`, so building only `api` silently skips 020.

```bash
cd ~/mirrorfleet && git pull
sudo docker compose build migrate api
sudo docker compose run --rm migrate          # prints: applied: ['020_single_admin.sql']
sudo docker compose up -d api
sudo docker compose exec postgres psql -U copytrader -d copytrader \
  -c "SELECT role, count(*) FROM org_memberships GROUP BY role"
```

Expected: only `admin`, `viewer`, `investor` rows. No copier restart is needed. Then open the Members page: the desk owner reads Admin, their own row has no picker, every other row does.
