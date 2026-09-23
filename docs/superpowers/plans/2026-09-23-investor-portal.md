# Investor Portal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let investors deposit crypto to the workspace operator, have an MT5 account opened and linked for them, see only that account (equity, history, profit, deposits, withdrawals) in a restricted portal, and cash out through admin-approved withdrawal requests, with the app keeping the ledger and never moving money.

**Architecture:** A new `investor` membership role ranked below `viewer` is refused by every existing endpoint automatically; a dedicated `routes/investor.py` exposes investor endpoints that always resolve the caller's own linked account first, plus admin endpoints for wallet settings, account linking and the deposit/withdrawal queues. Ledger rules (amount parsing, summary maths, state transitions) live in a pure module. The dashboard gains an investor shell with five pages and an admin "Investors" page; equity comes from the copier's existing `/state` snapshot with an `mt5_links` fallback.

**Tech Stack:** FastAPI + psycopg (no ORM), Postgres migrations in `db/migrations`, pytest with the `org_client`/`make_user`/`make_org`/`login_as` fixtures; React 18 + TypeScript + react-router 7 + Tailwind 4, vitest + Testing Library; `qrcode` (new npm dependency) for the deposit QR.

**Spec:** `docs/superpowers/specs/2026-09-23-investor-portal-design.md`

## Global Constraints

- Migration file is `db/migrations/019_investor_portal.sql`; it must be applied via the rebuilt `migrate` service on deploy (see the deploy-migrations memory note).
- Role order is exactly `investor < viewer < trader < admin < owner`; `ROLE_RANK["investor"] == -1`, all existing ranks unchanged.
- Amounts are `NUMERIC(18,2)`, positive, at most two decimals and twelve integer digits; `txid` and `destination` are trimmed, non-empty, at most 128 characters.
- Deposit statuses: `pending → confirmed | rejected`. Withdrawal statuses: `requested → approved → paid`, `requested → rejected`, `approved → rejected`. Nothing moves backwards; every decision is a single conditional `UPDATE ... WHERE status = <expected>`, zero rows → 409.
- Investor endpoints never accept an account id; they resolve `accounts.investor_user_id = caller` for the path's org.
- Every state change writes an `events` row (`category='control'`) with the actor's email; the two "new request" actions are `severity='warning'`, all others `'info'`.
- Rate limit: 10 deposit notices and 10 withdrawal requests per investor per hour. The shared login limiter has a one-minute window, so the investor router constructs its own `LoginRateLimiter(max_attempts=REQUESTS_PER_HOUR, window_s=3600)` (Task 5).
- API tests run with the local venv against the compose Postgres on `127.0.0.1:5433` (see the api-tests memory note: export `TEST_POSTGRES_ADMIN_DSN`/`TEST_POSTGRES_DSN` with `127.0.0.1`, `PYTHONPATH=<repo>/api/src`). Never run api and copier suites concurrently.
- Dashboard: `npm test` runs `tsc --noEmit -p tsconfig.app.json && vitest run`. Pages call `orgApi(orgId, 'tail', init)` inline; there are no per-endpoint helpers, and this plan adds none.
- No wallet secrets, no signing, no on-chain calls anywhere.

---

## File structure

**Backend (create):**
- `db/migrations/019_investor_portal.sql` — role constraints, account link column, three new tables.
- `api/src/api/investor_ledger.py` — pure ledger rules: `parse_amount`, `clean_text`, `can_transition`, `Summary`, `summarise`, `summary_json`. No I/O.
- `api/src/api/routes/investor.py` — `create_investor_router(rate_limiter)` (investor endpoints) and `create_investor_admin_router()` (admin endpoints), plus module-level helpers `_linked_account`, `_wallet`, `_event`, `_equity_for`.
- `api/tests/test_migration_019.py`, `api/tests/test_investor_ledger.py`, `api/tests/test_investor_portal.py`.

**Backend (modify):**
- `api/src/api/rbac.py` — `ROLE_RANK` gains `investor`.
- `api/src/api/routes/orgs.py:654-656` — invites may grant `investor`.
- `api/src/api/main.py:143-196` — include the two new routers.
- `api/src/api/alerts.py` — two `ALERT_RULES` entries, `send_to()` method.
- `api/src/api/telegram.py` — two `TELEGRAM_RULES` entries, investor text branch in `consider()`.
- `api/tests/conftest.py:43-49` — TRUNCATE list gains the three tables.
- `api/tests/test_rbac_matrix.py` — `investor` joins `ROLES`/`RANK`; new endpoint rows.

**Frontend (create):**
- `dashboard/src/pages/investor/InvestorOverview.tsx`, `InvestorDeposit.tsx`, `InvestorWithdraw.tsx`, `InvestorHistory.tsx`, `InvestorAccount.tsx` (+ one `.test.tsx` each except Account).
- `dashboard/src/pages/Investors.tsx` (+ `.test.tsx`) — admin page.
- `dashboard/src/lib/investor.ts` — shared formatting for the investor pages (status pill, money with sign).

**Frontend (modify):**
- `dashboard/src/lib/roles.ts` — `investor` in `Role`/`RANK`.
- `dashboard/src/lib/types.ts` — investor types.
- `dashboard/src/pages/Members.tsx:10-11` — role lists.
- `dashboard/src/components/Layout.tsx` — investor nav, no desk strip for investors, redirect, Investors nav for admins.
- `dashboard/src/App.tsx` — routes.
- `dashboard/package.json` — `qrcode` + `@types/qrcode`.

---

### Task 1: Migration 019 and the test database

**Files:**
- Create: `db/migrations/019_investor_portal.sql`
- Modify: `api/tests/conftest.py:43-49`
- Test: `api/tests/test_migration_019.py`

**Interfaces:**
- Produces: tables `org_investor_wallets`, `investor_deposits`, `investor_withdrawals`; column `accounts.investor_user_id`; role `investor` accepted by `org_memberships.role` and `org_invites.role`.

- [ ] **Step 1: Write the failing migration test**

```python
# api/tests/test_migration_019.py
"""Migration 019: the investor portal -- investor role, the account->investor
link, the workspace wallet card, deposit notices and withdrawal requests.
conftest applies EVERY migration, so these assert the post-migration shape."""
import psycopg
import pytest


def test_migration_019_is_recorded_right_after_018(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "019_investor_portal.sql" in names
    assert names.index("019_investor_portal.sql") == names.index("018_risk_engine.sql") + 1


def test_investor_is_a_valid_membership_and_invite_role(db, make_user, make_org):
    owner = make_user()
    investor = make_user(email="inv@example.com")
    org_id = make_org(members=[(owner, "owner"), (investor, "investor")])
    with psycopg.connect(db, autocommit=True) as conn:
        (role,) = conn.execute(
            "SELECT role FROM org_memberships WHERE org_id = %s AND user_id = %s",
            (org_id, investor["id"])).fetchone()
        assert role == "investor"
        conn.execute(
            "INSERT INTO org_invites (org_id, role, token_hash, created_by, expires_at) "
            "VALUES (%s, 'investor', 'h019', %s, now() + interval '1 day')",
            (org_id, owner["id"]))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'guest')",
                (org_id, owner["id"]))


def test_one_account_per_investor_per_org(db, make_user, make_org):
    owner = make_user()
    investor = make_user(email="inv@example.com")
    org_id = make_org(members=[(owner, "owner"), (investor, "investor")])
    with psycopg.connect(db, autocommit=True) as conn:
        (cid,) = conn.execute(
            "INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc, "
            "granted_at, expires_at) VALUES (%s, 'e', 'e', now(), now() + interval '1 day') "
            "RETURNING id", (org_id,)).fetchone()
        for aid in (901, 902):
            conn.execute(
                "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
                "trader_login, is_live, role, investor_user_id) "
                "VALUES (%s, %s, %s, %s, false, 'slave', %s)",
                (aid, cid, org_id, aid, investor["id"] if aid == 901 else None))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "UPDATE accounts SET investor_user_id = %s WHERE ctid_trader_account_id = 902",
                (investor["id"],))


def test_amounts_must_be_positive_and_statuses_are_checked(db, make_user, make_org):
    owner = make_user()
    investor = make_user(email="inv@example.com")
    org_id = make_org(members=[(owner, "owner"), (investor, "investor")])
    with psycopg.connect(db, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid) "
                "VALUES (%s, %s, 0, 'USDT', 'tx')", (org_id, investor["id"]))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid, status) "
                "VALUES (%s, %s, 10, 'USDT', 'tx', 'done')", (org_id, investor["id"]))
        (status,) = conn.execute(
            "INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid) "
            "VALUES (%s, %s, 10.50, 'USDT', 'tx') RETURNING status",
            (org_id, investor["id"])).fetchone()
        assert status == "pending"


def test_wallet_card_is_one_row_per_org(db, make_user, make_org):
    owner = make_user()
    org_id = make_org(members=[(owner, "owner")])
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_investor_wallets (org_id, coin, network, address) "
            "VALUES (%s, 'USDT', 'TRC20', 'TAddr1')", (org_id,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO org_investor_wallets (org_id, coin, network, address) "
                "VALUES (%s, 'USDT', 'TRC20', 'TAddr2')", (org_id,))
```

- [ ] **Step 2: Run it to verify it fails**

Run (from `api/`, env as in Global Constraints): `.venv/Scripts/python -m pytest tests/test_migration_019.py -q -p no:cacheprovider`
Expected: FAIL — `019_investor_portal.sql` not in `schema_migrations`; later tests error with `UndefinedTable`/`UndefinedColumn`.

- [ ] **Step 3: Write the migration**

```sql
-- db/migrations/019_investor_portal.sql
-- Investor portal: a membership role that can only see its own linked
-- account, the workspace's receiving-wallet card, and the ledger of
-- deposit notices and withdrawal requests. The app records money movements
-- people make outside it; it never holds keys or moves funds. See
-- docs/superpowers/specs/2026-09-23-investor-portal-design.md.

-- 'investor' ranks below 'viewer' in the api (ROLE_RANK), so every existing
-- viewer-or-higher endpoint refuses it without per-route changes.
ALTER TABLE org_memberships DROP CONSTRAINT org_memberships_role_check;
ALTER TABLE org_memberships ADD CONSTRAINT org_memberships_role_check
    CHECK (role IN ('owner', 'admin', 'trader', 'viewer', 'investor'));
ALTER TABLE org_invites DROP CONSTRAINT org_invites_role_check;
ALTER TABLE org_invites ADD CONSTRAINT org_invites_role_check
    CHECK (role IN ('admin', 'trader', 'viewer', 'investor'));

-- The one link the whole portal reads: which member this account belongs
-- to. One investor has at most one account per workspace and an account
-- belongs to at most one investor. Unlinking sets it NULL.
ALTER TABLE accounts ADD COLUMN investor_user_id BIGINT NULL
    REFERENCES users(id) ON DELETE SET NULL;
CREATE UNIQUE INDEX accounts_one_per_investor
    ON accounts (org_id, investor_user_id) WHERE investor_user_id IS NOT NULL;

-- Where investors send crypto. An address to RECEIVE at, nothing more; the
-- QR is drawn in the browser from it. No row = deposits not open yet.
CREATE TABLE org_investor_wallets (
    org_id      BIGINT PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
    coin        TEXT NOT NULL,
    network     TEXT NOT NULL,
    address     TEXT NOT NULL,
    memo        TEXT,
    updated_by  BIGINT REFERENCES users(id) ON DELETE SET NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- "I have sent it": the investor's own notice, confirmed or rejected by an
-- admin. Only confirmed rows count toward the ledger. account_id is filled
-- at confirmation from the investor's link at that moment (NULL when no
-- account exists yet); the summary sums by user_id, never by account.
CREATE TABLE investor_deposits (
    id             BIGSERIAL PRIMARY KEY,
    org_id         BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id     BIGINT REFERENCES accounts(ctid_trader_account_id) ON DELETE SET NULL,
    amount         NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    coin           TEXT NOT NULL,
    txid           TEXT NOT NULL,
    note           TEXT,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'confirmed', 'rejected')),
    decided_by     BIGINT REFERENCES users(id) ON DELETE SET NULL,
    decided_at     TIMESTAMPTZ,
    decision_note  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX investor_deposits_queue ON investor_deposits (org_id, status, created_at);

-- A cash-out request. approved means "admin will pay"; paid means the
-- crypto left the admin's wallet and txid says where. An account with
-- money-movement history cannot be deleted from under it (RESTRICT).
CREATE TABLE investor_withdrawals (
    id                 BIGSERIAL PRIMARY KEY,
    org_id             BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id            BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id         BIGINT NOT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE RESTRICT,
    amount             NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    destination        TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'requested'
                       CHECK (status IN ('requested', 'approved', 'paid', 'rejected')),
    equity_at_request  NUMERIC(18,2),
    equity_verified    BOOLEAN NOT NULL DEFAULT false,
    decided_by         BIGINT REFERENCES users(id) ON DELETE SET NULL,
    decided_at         TIMESTAMPTZ,
    decision_note      TEXT,
    paid_by            BIGINT REFERENCES users(id) ON DELETE SET NULL,
    paid_at            TIMESTAMPTZ,
    txid               TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX investor_withdrawals_queue ON investor_withdrawals (org_id, status, created_at);
```

- [ ] **Step 4: Add the new tables to the test TRUNCATE list**

In `api/tests/conftest.py`, change the `db` fixture's TRUNCATE statement (lines 43-49) so the list starts with the new tables:

```python
        conn.execute(
            "TRUNCATE investor_withdrawals, investor_deposits, org_investor_wallets, "
            "events, portfolio_snapshots, mappings, symbol_cache, "
            "executions, positions, deals, deal_backfill_state, balance_samples, "
            "accounts, ctid_connections, "
            "oauth_states, org_invites, org_memberships, orgs, users "
            "RESTART IDENTITY CASCADE"
        )
```

- [ ] **Step 5: Run the migration test and the existing migration tests**

Run: `.venv/Scripts/python -m pytest tests/test_migration_019.py tests/test_migration_017.py -q -p no:cacheprovider`
Expected: all PASS (the session-scoped `database` fixture rebuilds the scratch DB, so the new file is applied).

- [ ] **Step 6: Commit**

```bash
git add db/migrations/019_investor_portal.sql api/tests/conftest.py api/tests/test_migration_019.py
git commit -m "feat(db): migration 019 for the investor portal (role, account link, wallet, ledger tables)"
```

---

### Task 2: The `investor` role

**Files:**
- Modify: `api/src/api/rbac.py:15`
- Modify: `api/src/api/routes/orgs.py:654-656`
- Modify: `api/tests/test_rbac_matrix.py:11-14, 54` (ROLES, RANK)
- Test: `api/tests/test_investor_portal.py` (new file, first tests)

**Interfaces:**
- Produces: `ROLE_RANK["investor"] == -1`; `POST /api/orgs/{org_id}/invites` accepts `{"role": "investor"}`; `PATCH members/{uid}` already accepts any `ROLE_RANK` key.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/test_investor_portal.py
"""The investor portal: a role that can see only its own linked account,
the workspace wallet card, deposit notices and withdrawal requests.
Every test that needs the copier's equity fakes /state through the
app's mock transport, the same way test_webhooks.py does."""
import json
from decimal import Decimal

import httpx
import psycopg
import pytest
from conftest import default_mock_callback


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("csrf")}


def _member(db, org_id, user, role):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
            (org_id, user["id"], role))


def _link(db, account_id, user):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE accounts SET investor_user_id = %s "
                     "WHERE ctid_trader_account_id = %s", (user["id"], account_id))


def _wallet(db, org_id, coin="USDT", network="TRC20", address="TAddr123"):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_investor_wallets (org_id, coin, network, address) "
            "VALUES (%s, %s, %s, %s)", (org_id, coin, network, address))


def _state(client, accounts=None, down=False):
    """Fake the copier's /state. `accounts` is {account_id: {equity, balance,
    open_pnl, positions}} exactly as the copier serialises it (string keys)."""
    def callback(request):
        url = str(request.url)
        if "copier.test" in url and "/state" in url:
            if down:
                return httpx.Response(502, json={"detail": "down"})
            return httpx.Response(200, json={
                "status": "ok",
                "accounts": {str(k): v for k, v in (accounts or {}).items()},
                "master_positions": [], "pending_orders": [], "drift": []})
        return default_mock_callback(request)
    client.app.state.mock_transport.set_callback(callback)


# ---------------------------------------------------------------- the role


def test_an_investor_invite_can_be_created_and_joined(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    r = client.post(f"/api/orgs/{org_id}/invites", json={"role": "investor"},
                    headers=_csrf(client))
    assert r.status_code == 201 and r.json()["role"] == "investor"
    token = r.json()["token"]
    investor = make_user(email="inv@example.com")
    login_as(client, investor)
    r = client.post("/api/orgs/join", json={"token": token}, headers=_csrf(client))
    assert r.status_code == 200 and r.json() == {"org_id": org_id, "role": "investor"}
    me = client.get("/api/me").json()
    assert me["orgs"] == [{"id": org_id, "name": "Desk", "role": "investor"}]


def test_an_investor_is_refused_by_every_desk_endpoint(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    seed(100, role="master")
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    login_as(client, investor)
    for method, tail in [
        ("GET", "accounts"), ("GET", "accounts/100/details"), ("GET", "state"),
        ("GET", "settings"), ("GET", "events"), ("GET", "members"), ("GET", "overview"),
        ("GET", "accounts/100/analytics"), ("GET", "accounts/100/history/deals?from=0&to=1"),
        ("GET", "webhook"), ("GET", "risk-rules"),
    ]:
        r = client.request(method, f"/api/orgs/{org_id}/{tail}", headers=_csrf(client))
        assert r.status_code == 403, f"{method} {tail} -> {r.status_code}"
    r = client.get(f"/api/orgs/{org_id}")
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_investor_portal.py -q -p no:cacheprovider`
Expected: first test FAILS with 400 "Invites can grant admin, trader, or viewer"; second FAILS at `require_org_role` with `KeyError: 'investor'` (500) because the rank table lacks the role.

- [ ] **Step 3: Add the rank and the invite role**

`api/src/api/rbac.py` line 15:

```python
# 'investor' sits BELOW viewer on purpose: every existing endpoint asks for
# viewer or higher, so an investor is refused everywhere except the
# investor router, which resolves the caller's own linked account itself.
ROLE_RANK = {"investor": -1, "viewer": 0, "trader": 1, "admin": 2, "owner": 3}
```

`api/src/api/routes/orgs.py` lines 654-656:

```python
        if body.role not in ("admin", "trader", "viewer", "investor"):
            raise HTTPException(
                status_code=400, detail="Invites can grant admin, trader, viewer, or investor")
```

- [ ] **Step 4: Teach the permission matrix the new role**

In `api/tests/test_rbac_matrix.py` change line 11 and line 54:

```python
ROLES = ["investor", "viewer", "trader", "admin", "owner"]
```
```python
RANK = {"investor": -1, "viewer": 0, "trader": 1, "admin": 2, "owner": 3}
```

The matrix loop already asserts 403 for every role below each row's `min_role`, so with no other change the investor is now checked against all 33 desk endpoints.

- [ ] **Step 5: Run the new tests, the matrix, and the orgs tests**

Run: `.venv/Scripts/python -m pytest tests/test_investor_portal.py tests/test_rbac_matrix.py tests/test_orgs.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/api/rbac.py api/src/api/routes/orgs.py api/tests/test_rbac_matrix.py api/tests/test_investor_portal.py
git commit -m "feat(api): investor membership role, ranked below viewer"
```

---

### Task 3: Ledger rules (pure module)

**Files:**
- Create: `api/src/api/investor_ledger.py`
- Test: `api/tests/test_investor_ledger.py`

**Interfaces:**
- Produces:
  - `class LedgerError(ValueError)`
  - `parse_amount(raw: object, field: str = "amount") -> Decimal` — positive, ≤ 2 decimals, ≤ 12 integer digits, else `LedgerError` with an operator-readable message.
  - `clean_text(raw: object, field: str, max_len: int = 128, required: bool = True) -> str | None`
  - `DEPOSIT_TRANSITIONS`, `WITHDRAWAL_TRANSITIONS: dict[str, frozenset[str]]`; `can_transition(transitions, current, new) -> bool`
  - `@dataclass(frozen=True) Summary(total_deposited, total_withdrawn, net_deposits, pending_withdrawn: Decimal; equity, profit, available: Decimal | None)`
  - `summarise(deposits: Iterable[tuple[str, Decimal]], withdrawals: Iterable[tuple[str, Decimal]], equity: Decimal | None) -> Summary` — inputs are `(status, amount)` pairs.
  - `summary_json(s: Summary) -> dict[str, float | None]` — two-decimal floats, `None` kept.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/test_investor_ledger.py
"""Ledger rules with no I/O: what an amount may look like, how a summary is
computed, and which status changes are legal."""
from decimal import Decimal

import pytest

from api.investor_ledger import (
    DEPOSIT_TRANSITIONS, WITHDRAWAL_TRANSITIONS, LedgerError, Summary,
    can_transition, clean_text, parse_amount, summarise, summary_json)


class TestAmount:
    @pytest.mark.parametrize("raw, expected", [
        (100, Decimal("100")), ("250.5", Decimal("250.5")), (0.01, Decimal("0.01")),
        ("1000000.00", Decimal("1000000.00")),
    ])
    def test_accepts_positive_money(self, raw, expected):
        assert parse_amount(raw) == expected

    @pytest.mark.parametrize("raw", [0, -5, "0.001", "abc", None, "", True, "1e400",
                                     "1234567890123", float("nan")])
    def test_refuses_what_a_ledger_cannot_hold(self, raw):
        with pytest.raises(LedgerError):
            parse_amount(raw)

    def test_message_names_the_field(self):
        with pytest.raises(LedgerError, match="amount"):
            parse_amount("-1")


class TestText:
    def test_trims_and_keeps(self):
        assert clean_text("  abc123  ", "txid") == "abc123"

    def test_required_text_may_not_be_blank(self):
        with pytest.raises(LedgerError, match="txid"):
            clean_text("   ", "txid")

    def test_optional_text_returns_none_when_blank(self):
        assert clean_text("  ", "note", required=False) is None

    def test_too_long_is_refused(self):
        with pytest.raises(LedgerError, match="128"):
            clean_text("x" * 129, "destination")


class TestTransitions:
    def test_deposits_only_move_forward(self):
        assert can_transition(DEPOSIT_TRANSITIONS, "pending", "confirmed")
        assert can_transition(DEPOSIT_TRANSITIONS, "pending", "rejected")
        assert not can_transition(DEPOSIT_TRANSITIONS, "confirmed", "pending")
        assert not can_transition(DEPOSIT_TRANSITIONS, "rejected", "confirmed")

    def test_withdrawals_follow_the_spec_diagram(self):
        assert can_transition(WITHDRAWAL_TRANSITIONS, "requested", "approved")
        assert can_transition(WITHDRAWAL_TRANSITIONS, "requested", "rejected")
        assert can_transition(WITHDRAWAL_TRANSITIONS, "approved", "paid")
        assert can_transition(WITHDRAWAL_TRANSITIONS, "approved", "rejected")
        assert not can_transition(WITHDRAWAL_TRANSITIONS, "requested", "paid")
        assert not can_transition(WITHDRAWAL_TRANSITIONS, "paid", "rejected")
        assert not can_transition(WITHDRAWAL_TRANSITIONS, "rejected", "approved")


class TestSummary:
    def test_only_confirmed_and_paid_rows_count(self):
        s = summarise(
            deposits=[("confirmed", Decimal("5000")), ("pending", Decimal("1000")),
                      ("rejected", Decimal("9"))],
            withdrawals=[("paid", Decimal("500")), ("requested", Decimal("200")),
                         ("approved", Decimal("100")), ("rejected", Decimal("7"))],
            equity=Decimal("5120.50"))
        assert s == Summary(
            total_deposited=Decimal("5000"), total_withdrawn=Decimal("500"),
            net_deposits=Decimal("4500"), pending_withdrawn=Decimal("300"),
            equity=Decimal("5120.50"), profit=Decimal("620.50"),
            available=Decimal("4820.50"))

    def test_unknown_equity_leaves_profit_and_available_unknown(self):
        s = summarise([("confirmed", Decimal("100"))], [], equity=None)
        assert s.net_deposits == Decimal("100")
        assert s.equity is None and s.profit is None and s.available is None

    def test_json_is_two_decimal_floats(self):
        s = summarise([("confirmed", Decimal("100"))], [], equity=Decimal("133.333"))
        assert summary_json(s) == {
            "total_deposited": 100.0, "total_withdrawn": 0.0, "net_deposits": 100.0,
            "pending_withdrawn": 0.0, "equity": 133.33, "profit": 33.33, "available": 133.33}
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_investor_ledger.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.investor_ledger'`.

- [ ] **Step 3: Write the module**

```python
# api/src/api/investor_ledger.py
"""Ledger rules for the investor portal, with no I/O.

Three things live here so they can be tested without a database and reused
by every endpoint identically: what an amount may look like, which status
changes are legal, and how an investor's summary is computed from rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable, Optional

MAX_TEXT = 128
MAX_INTEGER_DIGITS = 12
_CENT = Decimal("0.01")


class LedgerError(ValueError):
    """The input was understood and refused. The message is for the person."""


def parse_amount(raw: object, field: str = "amount") -> Decimal:
    """A positive money amount with at most two decimals. Strings are
    accepted because forms send strings; bools are refused because
    Decimal(True) is 1."""
    if isinstance(raw, bool) or raw is None or raw == "":
        raise LedgerError(f"{field} is required, e.g. 250.00")
    if isinstance(raw, float) and raw != raw:  # NaN
        raise LedgerError(f"{field} must be a number")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise LedgerError(f"{field} must be a number, got {raw!r}")
    if not value.is_finite() or value <= 0:
        raise LedgerError(f"{field} must be greater than 0")
    if value != value.quantize(_CENT):
        raise LedgerError(f"{field} may have at most two decimals")
    if value >= Decimal(10) ** MAX_INTEGER_DIGITS:
        raise LedgerError(f"{field} is too large")
    return value


def clean_text(raw: object, field: str, max_len: int = MAX_TEXT,
               required: bool = True) -> Optional[str]:
    text = "" if raw is None else str(raw).strip()
    if not text:
        if required:
            raise LedgerError(f"{field} is required")
        return None
    if len(text) > max_len:
        raise LedgerError(f"{field} must be at most {max_len} characters")
    return text


DEPOSIT_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"confirmed", "rejected"}),
}
WITHDRAWAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "requested": frozenset({"approved", "rejected"}),
    "approved": frozenset({"paid", "rejected"}),
}


def can_transition(transitions: dict[str, frozenset[str]], current: str, new: str) -> bool:
    return new in transitions.get(current, frozenset())


@dataclass(frozen=True)
class Summary:
    total_deposited: Decimal
    total_withdrawn: Decimal
    net_deposits: Decimal
    pending_withdrawn: Decimal
    equity: Optional[Decimal]
    profit: Optional[Decimal]
    available: Optional[Decimal]


def summarise(deposits: Iterable[tuple[str, Decimal]],
              withdrawals: Iterable[tuple[str, Decimal]],
              equity: Optional[Decimal]) -> Summary:
    """`deposits`/`withdrawals` are (status, amount) pairs. Only confirmed
    deposits and paid withdrawals are money that moved; requested and
    approved withdrawals are money spoken for."""
    total_dep = sum((a for s, a in deposits if s == "confirmed"), Decimal(0))
    paid = Decimal(0)
    pending = Decimal(0)
    for status, amount in withdrawals:
        if status == "paid":
            paid += amount
        elif status in ("requested", "approved"):
            pending += amount
    net = total_dep - paid
    profit = None if equity is None else equity - net
    available = None if equity is None else equity - pending
    return Summary(total_deposited=total_dep, total_withdrawn=paid, net_deposits=net,
                   pending_withdrawn=pending, equity=equity, profit=profit,
                   available=available)


def _money(value: Optional[Decimal]) -> Optional[float]:
    return None if value is None else float(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def summary_json(s: Summary) -> dict:
    return {
        "total_deposited": _money(s.total_deposited),
        "total_withdrawn": _money(s.total_withdrawn),
        "net_deposits": _money(s.net_deposits),
        "pending_withdrawn": _money(s.pending_withdrawn),
        "equity": _money(s.equity),
        "profit": _money(s.profit),
        "available": _money(s.available),
    }
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_investor_ledger.py -q -p no:cacheprovider`
Expected: PASS (26 tests).

- [ ] **Step 5: Commit**

```bash
git add api/src/api/investor_ledger.py api/tests/test_investor_ledger.py
git commit -m "feat(api): pure ledger rules for the investor portal"
```

---

### Task 4: Investor router scaffold — summary, wallet, admin wallet, investor list, account link

**Files:**
- Create: `api/src/api/routes/investor.py`
- Modify: `api/src/api/main.py:143-196` (include routers)
- Test: `api/tests/test_investor_portal.py` (append)

**Interfaces:**
- Consumes: `investor_ledger.summarise/summary_json`, `rbac.require_org_role`, `settings_control._proxy_to_copier`, `mt5.MT5_OFFLINE_AFTER_S`, `auth.LoginRateLimiter`.
- Produces (module-level, reused by Tasks 5-8):
  - `_event(conn, org_id, actor_email, action, severity, detail: dict, account_id=None) -> None`
  - `_linked_account(conn, org_id, user_id) -> int | None`
  - `_wallet(conn, org_id) -> dict | None` with keys `coin, network, address, memo`
  - `_account_card(conn, org_id, account_id) -> dict` with keys `account_id, nickname, platform, status, last_error, connected`
  - `async _equity_for(client, cfg, conn, org_id, account_id) -> tuple[Decimal | None, str, list]` → `(equity, source in {'live','last known','unknown'}, positions)`
  - `_ledger_rows(conn, org_id, user_id) -> tuple[list[tuple[str, Decimal]], list[tuple[str, Decimal]]]`
  - `create_investor_router(rate_limiter) -> APIRouter` and `create_investor_admin_router() -> APIRouter`
  - Endpoints: `GET /investor/summary`, `GET /investor/wallet`, `GET/PUT /investor-wallet`, `GET /investors`, `PUT /investors/{user_id}/account`.

- [ ] **Step 1: Write the failing tests** (append to `api/tests/test_investor_portal.py`)

```python
# ------------------------------------------------------- summary + wallet


def test_summary_before_an_account_is_linked_says_so(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    login_as(client, investor)
    r = client.get(f"/api/orgs/{org_id}/investor/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["link_state"] == "unlinked" and body["account"] is None
    assert body["equity"] is None and body["profit"] is None
    assert body["total_deposited"] == 0.0
    assert body["org"] == {"id": org_id, "name": "Desk"}


def test_summary_uses_the_linked_accounts_live_equity(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    _link(db, 1001, investor)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid, status) "
                     "VALUES (%s, %s, 5000, 'USDT', 't1', 'confirmed')", (org_id, investor["id"]))
    _state(client, {1001: {"balance": 5000.0, "equity": 5120.5, "open_pnl": 120.5,
                           "positions": []}})
    login_as(client, investor)
    body = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    assert body["link_state"] == "linked" and body["account"]["account_id"] == 1001
    assert body["equity"] == 5120.5 and body["equity_source"] == "live"
    assert body["net_deposits"] == 5000.0 and body["profit"] == 120.5


def test_summary_falls_back_to_last_known_equity_when_the_copier_is_down(
        org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    with psycopg.connect(db, autocommit=True) as conn:
        (aid,) = conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
            "platform, trader_login, is_live, role, enabled, nickname, investor_user_id) "
            "VALUES (nextval('mt5_account_id_seq'), NULL, %s, 'mt5', 0, false, 'slave', "
            "true, 'Inv', %s) RETURNING ctid_trader_account_id",
            (org_id, investor["id"])).fetchone()
        conn.execute("INSERT INTO mt5_links (account_id, key_hash, equity, balance) "
                     "VALUES (%s, 'h', 4990.25, 4990.25)", (aid,))
    _state(client, down=True)
    login_as(client, investor)
    body = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    assert body["equity"] == 4990.25 and body["equity_source"] == "last known"
    assert body["account"]["platform"] == "mt5" and body["account"]["connected"] is False


def test_wallet_is_404_until_an_admin_sets_it(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    r = client.put(f"/api/orgs/{org_id}/investor-wallet", json={
        "coin": "USDT", "network": "TRC20", "address": "TAddr123", "memo": None},
        headers=_csrf(client))
    assert r.status_code == 200
    assert client.get(f"/api/orgs/{org_id}/investor-wallet").json() == {
        "coin": "USDT", "network": "TRC20", "address": "TAddr123", "memo": None}
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    login_as(client, investor)
    assert client.get(f"/api/orgs/{org_id}/investor/wallet").json()["address"] == "TAddr123"
    assert client.put(f"/api/orgs/{org_id}/investor-wallet", json={
        "coin": "X", "network": "Y", "address": "Z"}, headers=_csrf(client)).status_code == 403


def test_wallet_with_no_row_is_404_for_investors(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    login_as(client, investor)
    assert client.get(f"/api/orgs/{org_id}/investor/wallet").status_code == 404


def test_wallet_address_is_required(org_client):
    client, org_id, seed = org_client
    r = client.put(f"/api/orgs/{org_id}/investor-wallet", json={
        "coin": "USDT", "network": "TRC20", "address": "  "}, headers=_csrf(client))
    assert r.status_code == 400 and "address" in r.json()["detail"]


# -------------------------------------------------- investors + linking


def test_admin_links_and_unlinks_an_account(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    _state(client, {1001: {"balance": 100.0, "equity": 100.0, "open_pnl": 0.0, "positions": []}})

    r = client.put(f"/api/orgs/{org_id}/investors/{investor['id']}/account",
                   json={"account_id": 1001}, headers=_csrf(client))
    assert r.status_code == 200 and r.json() == {"user_id": investor["id"], "account_id": 1001}
    rows = client.get(f"/api/orgs/{org_id}/investors").json()
    assert rows == [{"user_id": investor["id"], "email": "inv@example.com",
                     "display_name": "User", "account_id": 1001, "nickname": None,
                     "equity": 100.0, "net_deposits": 0.0, "profit": 100.0,
                     "pending_deposits": 0, "pending_withdrawals": 0}]

    r = client.put(f"/api/orgs/{org_id}/investors/{investor['id']}/account",
                   json={"account_id": None}, headers=_csrf(client))
    assert r.status_code == 200 and r.json()["account_id"] is None
    assert client.get(f"/api/orgs/{org_id}/investors").json()[0]["account_id"] is None
    with psycopg.connect(db, autocommit=True) as conn:
        actions = [r[0] for r in conn.execute(
            "SELECT payload->>'action' FROM events WHERE org_id = %s ORDER BY id",
            (org_id,)).fetchall()]
    assert actions == ["investor_account_linked", "investor_account_linked"]


def test_linking_refuses_an_account_from_another_workspace_or_a_non_investor(
        org_client, make_user, make_org, db):
    client, org_id, seed = org_client
    other_owner = make_user(email="o@example.com")
    other_org = make_org(name="Other", members=[(other_owner, "owner")])
    with psycopg.connect(db, autocommit=True) as conn:
        (cid,) = conn.execute(
            "INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc, "
            "granted_at, expires_at) VALUES (%s, 'e', 'e', now(), now() + interval '1 day') "
            "RETURNING id", (other_org,)).fetchone()
        conn.execute("INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
                     "trader_login, is_live, role) VALUES (2001, %s, %s, 2001, false, 'slave')",
                     (cid, other_org))
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    r = client.put(f"/api/orgs/{org_id}/investors/{investor['id']}/account",
                   json={"account_id": 2001}, headers=_csrf(client))
    assert r.status_code == 404
    viewer = make_user(email="v@example.com")
    _member(db, org_id, viewer, "viewer")
    seed(1001, role="slave")
    r = client.put(f"/api/orgs/{org_id}/investors/{viewer['id']}/account",
                   json={"account_id": 1001}, headers=_csrf(client))
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_investor_portal.py -q -p no:cacheprovider`
Expected: the new tests FAIL with 404 (routes do not exist yet); the two Task 2 tests still PASS.

- [ ] **Step 3: Write the router module**

```python
# api/src/api/routes/investor.py
"""The investor portal.

Two routers. `create_investor_router` serves a member whose role is
`investor`: every handler first resolves the ONE account linked to the
caller (accounts.investor_user_id) and never accepts an account id from the
request, so an investor cannot name anyone else's account. Any member may
call these -- a desk role simply has no linked account and gets the
"unlinked" answers. `create_investor_admin_router` serves admins: the
wallet card, the investor list, linking, and the deposit and withdrawal
queues. The app records money movements that people make outside it; it
holds no keys and moves nothing.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..auth import LoginRateLimiter
from ..config import ApiConfig
from ..db import get_conn
from ..investor_ledger import (
    DEPOSIT_TRANSITIONS, WITHDRAWAL_TRANSITIONS, LedgerError, can_transition,
    clean_text, parse_amount, summarise, summary_json)
from ..rbac import OrgContext, require_org_role
from ..ws import broadcaster
from .mt5 import MT5_OFFLINE_AFTER_S
from .settings_control import _proxy_to_copier

logger = logging.getLogger(__name__)

REQUESTS_PER_HOUR = 10


# ------------------------------------------------------------ helpers


def _event(conn: psycopg.Connection, org_id: int, actor_email: str, action: str,
           severity: str, detail: Dict[str, Any], account_id: Optional[int] = None) -> None:
    """One audit row per state change. The two "somebody asked" actions are
    warnings so the alerters ping an admin; decisions are info. Best-effort:
    a failed audit write is logged, never surfaced as a failed request."""
    try:
        conn.execute(
            "INSERT INTO events (org_id, account_id, category, severity, payload, actor_email) "
            "VALUES (%s, %s, 'control', %s, %s, %s)",
            (org_id, account_id, severity, Jsonb({"action": action, **detail}), actor_email))
    except Exception:
        logger.exception("failed to write investor audit event %s", action)


def _linked_account(conn: psycopg.Connection, org_id: int, user_id: int) -> Optional[int]:
    row = conn.execute(
        "SELECT ctid_trader_account_id FROM accounts "
        "WHERE org_id = %s AND investor_user_id = %s", (org_id, user_id)).fetchone()
    return int(row[0]) if row else None


def _wallet(conn: psycopg.Connection, org_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT coin, network, address, memo FROM org_investor_wallets WHERE org_id = %s",
        (org_id,)).fetchone()
    if not row:
        return None
    return {"coin": row[0], "network": row[1], "address": row[2], "memo": row[3]}


def _account_card(conn: psycopg.Connection, org_id: int, account_id: int) -> Dict[str, Any]:
    row = conn.execute(
        """SELECT a.nickname, a.platform, a.status, a.last_error,
                  COALESCE(l.last_seen_at > now() - make_interval(secs => %s), false),
                  c.status
           FROM accounts a
           LEFT JOIN mt5_links l ON l.account_id = a.ctid_trader_account_id
           LEFT JOIN ctid_connections c ON a.ctid_connection_id = c.id
           WHERE a.ctid_trader_account_id = %s AND a.org_id = %s""",
        (MT5_OFFLINE_AFTER_S, account_id, org_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    connected = bool(row[4]) if row[1] == "mt5" else row[5] == "active"
    return {"account_id": account_id, "nickname": row[0], "platform": row[1],
            "status": row[2], "last_error": row[3], "connected": connected}


async def _equity_for(client, cfg: ApiConfig, conn: psycopg.Connection, org_id: int,
                      account_id: int) -> tuple[Optional[Decimal], str, list]:
    """Live equity from the copier's state snapshot, else the last equity
    the MT5 terminal reported, else unknown. Never raises: an unreachable
    copier makes the figure 'last known', not the page an error."""
    state: Any = None
    try:
        state = await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/state?org_id={org_id}", method="GET")
    except HTTPException:
        state = None
    accounts = state.get("accounts") if isinstance(state, dict) else None
    entry = accounts.get(str(account_id)) if isinstance(accounts, dict) else None
    if isinstance(entry, dict) and entry.get("equity") is not None:
        return Decimal(str(entry["equity"])), "live", list(entry.get("positions") or [])
    row = conn.execute("SELECT equity FROM mt5_links WHERE account_id = %s",
                       (account_id,)).fetchone()
    if row and row[0] is not None:
        return Decimal(str(row[0])), "last known", []
    return None, "unknown", []


def _ledger_rows(conn: psycopg.Connection, org_id: int, user_id: int):
    deposits = [(r[0], Decimal(r[1])) for r in conn.execute(
        "SELECT status, amount FROM investor_deposits WHERE org_id = %s AND user_id = %s",
        (org_id, user_id)).fetchall()]
    withdrawals = [(r[0], Decimal(r[1])) for r in conn.execute(
        "SELECT status, amount FROM investor_withdrawals WHERE org_id = %s AND user_id = %s",
        (org_id, user_id)).fetchall()]
    return deposits, withdrawals


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


# ------------------------------------------------------------ investor


def create_investor_router(rate_limiter: LoginRateLimiter) -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["investor"])

    @router.get("/investor/summary", response_model=Dict[str, Any])
    async def investor_summary(
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("investor")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> Dict[str, Any]:
        (org_name,) = conn.execute("SELECT name FROM orgs WHERE id = %s", (ctx.org_id,)).fetchone()
        account_id = _linked_account(conn, ctx.org_id, ctx.user_id)
        deposits, withdrawals = _ledger_rows(conn, ctx.org_id, ctx.user_id)
        equity, source, _positions = (None, "unknown", [])
        card = None
        if account_id is not None:
            card = _account_card(conn, ctx.org_id, account_id)
            equity, source, _positions = await _equity_for(
                http_request.app.state.http, cfg, conn, ctx.org_id, account_id)
        summary = summarise(deposits, withdrawals, equity)
        return {
            "org": {"id": ctx.org_id, "name": org_name},
            "link_state": "linked" if account_id is not None else "unlinked",
            "account": card,
            "equity_source": source,
            "wallet_configured": _wallet(conn, ctx.org_id) is not None,
            **summary_json(summary),
        }

    @router.get("/investor/wallet", response_model=Dict[str, Any])
    async def investor_wallet(
        ctx: OrgContext = Depends(require_org_role("investor")),
        conn: psycopg.Connection = Depends(get_conn),
    ) -> Dict[str, Any]:
        wallet = _wallet(conn, ctx.org_id)
        if wallet is None:
            raise HTTPException(status_code=404, detail="Deposits are not open yet")
        return wallet

    return router


# ------------------------------------------------------------ admin


class WalletUpdate(BaseModel):
    coin: str
    network: str
    address: str
    memo: Optional[str] = None


class LinkBody(BaseModel):
    account_id: Optional[int] = None


def create_investor_admin_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["investor-admin"])

    @router.get("/investor-wallet", response_model=Dict[str, Any])
    async def get_wallet(ctx: OrgContext = Depends(require_org_role("admin")),
                         conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        wallet = _wallet(conn, ctx.org_id)
        if wallet is None:
            raise HTTPException(status_code=404, detail="No wallet configured")
        return wallet

    @router.put("/investor-wallet", response_model=Dict[str, Any])
    async def put_wallet(body: WalletUpdate,
                         ctx: OrgContext = Depends(require_org_role("admin")),
                         conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            coin = clean_text(body.coin, "coin", max_len=16)
            network = clean_text(body.network, "network", max_len=32)
            address = clean_text(body.address, "address")
            memo = clean_text(body.memo, "memo", required=False)
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        conn.execute(
            "INSERT INTO org_investor_wallets (org_id, coin, network, address, memo, "
            "updated_by, updated_at) VALUES (%s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (org_id) DO UPDATE SET coin = EXCLUDED.coin, "
            "network = EXCLUDED.network, address = EXCLUDED.address, memo = EXCLUDED.memo, "
            "updated_by = EXCLUDED.updated_by, updated_at = now()",
            (ctx.org_id, coin, network, address, memo, ctx.user_id))
        _event(conn, ctx.org_id, ctx.user_email, "investor_wallet_set", "info",
               {"coin": coin, "network": network})
        return {"coin": coin, "network": network, "address": address, "memo": memo}

    @router.get("/investors", response_model=List[Dict[str, Any]])
    async def list_investors(
        http_request: Request,
        ctx: OrgContext = Depends(require_org_role("admin")),
        conn: psycopg.Connection = Depends(get_conn),
        cfg: ApiConfig = Depends(ApiConfig.from_env),
    ) -> List[Dict[str, Any]]:
        rows = conn.execute(
            """SELECT u.id, u.email, u.display_name, a.ctid_trader_account_id, a.nickname,
                      (SELECT count(*) FROM investor_deposits d
                        WHERE d.org_id = m.org_id AND d.user_id = u.id AND d.status = 'pending'),
                      (SELECT count(*) FROM investor_withdrawals w
                        WHERE w.org_id = m.org_id AND w.user_id = u.id
                          AND w.status IN ('requested', 'approved'))
               FROM org_memberships m
               JOIN users u ON u.id = m.user_id
               LEFT JOIN accounts a ON a.org_id = m.org_id AND a.investor_user_id = u.id
               WHERE m.org_id = %s AND m.role = 'investor'
               ORDER BY u.display_name, u.id""", (ctx.org_id,)).fetchall()
        out = []
        for user_id, email, name, account_id, nickname, pend_dep, pend_wd in rows:
            deposits, withdrawals = _ledger_rows(conn, ctx.org_id, user_id)
            equity = None
            if account_id is not None:
                equity, _src, _pos = await _equity_for(
                    http_request.app.state.http, cfg, conn, ctx.org_id, int(account_id))
            s = summary_json(summarise(deposits, withdrawals, equity))
            out.append({"user_id": user_id, "email": email, "display_name": name,
                        "account_id": int(account_id) if account_id is not None else None,
                        "nickname": nickname, "equity": s["equity"],
                        "net_deposits": s["net_deposits"], "profit": s["profit"],
                        "pending_deposits": int(pend_dep), "pending_withdrawals": int(pend_wd)})
        return out

    @router.put("/investors/{user_id}/account", response_model=Dict[str, Any])
    async def link_account(user_id: int, body: LinkBody,
                           ctx: OrgContext = Depends(require_org_role("admin")),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        member = conn.execute(
            "SELECT 1 FROM org_memberships WHERE org_id = %s AND user_id = %s AND role = 'investor'",
            (ctx.org_id, user_id)).fetchone()
        if not member:
            raise HTTPException(status_code=404, detail="Investor not found")
        with conn.transaction():
            conn.execute("UPDATE accounts SET investor_user_id = NULL "
                         "WHERE org_id = %s AND investor_user_id = %s", (ctx.org_id, user_id))
            if body.account_id is not None:
                updated = conn.execute(
                    "UPDATE accounts SET investor_user_id = %s "
                    "WHERE ctid_trader_account_id = %s AND org_id = %s "
                    "AND investor_user_id IS NULL RETURNING ctid_trader_account_id",
                    (user_id, body.account_id, ctx.org_id)).fetchone()
                if not updated:
                    raise HTTPException(status_code=404,
                                        detail="Account not found in this workspace, or already linked")
        _event(conn, ctx.org_id, ctx.user_email, "investor_account_linked", "info",
               {"user_id": user_id, "account_id": body.account_id}, account_id=body.account_id)
        return {"user_id": user_id, "account_id": body.account_id}

    return router
```

- [ ] **Step 4: Register both routers**

In `api/src/api/main.py`, after the `insights_router` lines (around line 121), add:

```python
    # Investor portal: a sub-viewer role that sees only its own linked
    # account, plus the admin queues that decide its deposits/withdrawals.
    from .routes.investor import create_investor_admin_router, create_investor_router
    app.include_router(create_investor_router(rate_limiter))
    app.include_router(create_investor_admin_router())
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_investor_portal.py -q -p no:cacheprovider`
Expected: PASS (10 tests).

- [ ] **Step 6: Commit**

```bash
git add api/src/api/routes/investor.py api/src/api/main.py api/tests/test_investor_portal.py
git commit -m "feat(api): investor portal router -- summary, wallet card, investor list, account link"
```

---

### Task 5: Deposit notices and the admin deposit queue

**Files:**
- Modify: `api/src/api/routes/investor.py` (add endpoints + helpers), `api/src/api/main.py` (factory call)
- Test: `api/tests/test_investor_portal.py` (append)

**Interfaces:**
- Consumes: Task 4 helpers, `investor_ledger.parse_amount/clean_text/can_transition/DEPOSIT_TRANSITIONS`.
- Produces:
  - `_deposit_json(row) -> dict` with keys `id, user_id, account_id, amount, coin, txid, note, status, decided_by, decided_at, decision_note, created_at` (+ `email, display_name` in admin lists).
  - `create_investor_router() -> APIRouter` (no parameter; builds its own hourly limiter).
  - Endpoints: `GET/POST /investor/deposits`, `GET /investor-deposits?status=`, `POST /investor-deposits/{id}/decision`.
  - Event actions: `investor_deposit_noticed` (warning), `investor_deposit_decided` (info). The warning event's payload carries `summary` (a one-line sentence) for Telegram.

- [ ] **Step 1: Write the failing tests** (append)

```python
# ---------------------------------------------------------------- deposits


def _investor_with_wallet(org_client, make_user, login_as, db, link_to=None):
    client, org_id, seed = org_client
    _wallet(db, org_id)
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    if link_to is not None:
        seed(link_to, role="slave")
        _link(db, link_to, investor)
    login_as(client, investor)
    return client, org_id, investor


def test_an_investor_files_a_deposit_notice(org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    r = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "5000", "coin": "usdt", "txid": " abc123 ", "note": "sent from Binance"},
        headers=_csrf(client))
    assert r.status_code == 201
    body = r.json()
    assert body["amount"] == 5000.0 and body["coin"] == "USDT" and body["txid"] == "abc123"
    assert body["status"] == "pending" and body["account_id"] is None
    assert client.get(f"/api/orgs/{org_id}/investor/deposits").json() == [body]
    with psycopg.connect(db, autocommit=True) as conn:
        (severity, payload, actor) = conn.execute(
            "SELECT severity, payload, actor_email FROM events WHERE org_id = %s "
            "ORDER BY id DESC LIMIT 1", (org_id,)).fetchone()
    assert severity == "warning" and payload["action"] == "investor_deposit_noticed"
    assert payload["summary"] == "Deposit notice: 5000.00 USDT from inv@example.com"
    assert actor == "inv@example.com"


@pytest.mark.parametrize("body, needle", [
    ({"amount": "-5", "coin": "USDT", "txid": "t"}, "amount"),
    ({"amount": "5.001", "coin": "USDT", "txid": "t"}, "decimals"),
    ({"amount": "5", "coin": "USDT", "txid": "  "}, "txid"),
    ({"amount": "5", "coin": "BTC", "txid": "t"}, "USDT"),
])
def test_a_bad_notice_is_refused_with_the_reason(org_client, make_user, login_as, db, body, needle):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    r = client.post(f"/api/orgs/{org_id}/investor/deposits", json=body, headers=_csrf(client))
    assert r.status_code == 400 and needle in r.json()["detail"]


def test_notices_are_refused_while_no_wallet_is_configured(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    login_as(client, investor)
    r = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "5", "coin": "USDT", "txid": "t"}, headers=_csrf(client))
    assert r.status_code == 409 and "not open" in r.json()["detail"]


def test_ten_notices_an_hour_then_429(org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    for i in range(10):
        r = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
            "amount": "1", "coin": "USDT", "txid": f"t{i}"}, headers=_csrf(client))
        assert r.status_code == 201
    r = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "1", "coin": "USDT", "txid": "t10"}, headers=_csrf(client))
    assert r.status_code == 429


def test_admin_confirms_a_notice_once_and_it_counts(org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db,
                                                     link_to=1001)
    dep = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "5000", "coin": "USDT", "txid": "t"}, headers=_csrf(client)).json()
    admin = {"email": "admin@example.com", "password": "a-solid-password"}
    login_as(client, admin)
    queue = client.get(f"/api/orgs/{org_id}/investor-deposits?status=pending").json()
    assert [d["id"] for d in queue] == [dep["id"]] and queue[0]["email"] == "inv@example.com"

    r = client.post(f"/api/orgs/{org_id}/investor-deposits/{dep['id']}/decision",
                    json={"status": "confirmed", "note": "seen on chain"}, headers=_csrf(client))
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed" and r.json()["account_id"] == 1001
    assert r.json()["decision_note"] == "seen on chain"

    r = client.post(f"/api/orgs/{org_id}/investor-deposits/{dep['id']}/decision",
                    json={"status": "rejected", "note": "changed my mind"}, headers=_csrf(client))
    assert r.status_code == 409 and "confirmed" in r.json()["detail"]

    _state(client, {1001: {"balance": 5000.0, "equity": 5000.0, "open_pnl": 0.0, "positions": []}})
    login_as(client, investor)
    assert client.get(f"/api/orgs/{org_id}/investor/summary").json()["total_deposited"] == 5000.0


def test_a_rejection_needs_a_note(org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    dep = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "5", "coin": "USDT", "txid": "t"}, headers=_csrf(client)).json()
    login_as(client, {"email": "admin@example.com", "password": "a-solid-password"})
    r = client.post(f"/api/orgs/{org_id}/investor-deposits/{dep['id']}/decision",
                    json={"status": "rejected"}, headers=_csrf(client))
    assert r.status_code == 400 and "note" in r.json()["detail"]
    r = client.post(f"/api/orgs/{org_id}/investor-deposits/{dep['id']}/decision",
                    json={"status": "rejected", "note": "no such transaction"},
                    headers=_csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "rejected"


def test_investors_only_see_their_own_notices_and_cannot_decide(
        org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    dep = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "5", "coin": "USDT", "txid": "t"}, headers=_csrf(client)).json()
    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    login_as(client, other)
    assert client.get(f"/api/orgs/{org_id}/investor/deposits").json() == []
    r = client.post(f"/api/orgs/{org_id}/investor-deposits/{dep['id']}/decision",
                    json={"status": "confirmed"}, headers=_csrf(client))
    assert r.status_code == 403
    assert client.get(f"/api/orgs/{org_id}/investor-deposits").status_code == 403
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_investor_portal.py -q -p no:cacheprovider`
Expected: the deposit tests FAIL with 404 (no routes); earlier tests PASS.

- [ ] **Step 3: Make the factory own an hourly limiter**

The shared login limiter has a 60-second window; the portal needs an hour. Replace the factory signature in `api/src/api/routes/investor.py`:

```python
def create_investor_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["investor"])
    # Ten notices/requests per investor per hour. A separate instance
    # because the shared login limiter's window is one minute.
    hourly = LoginRateLimiter(max_attempts=REQUESTS_PER_HOUR, window_s=3600)
```

and in `api/src/api/main.py` change the include line to `app.include_router(create_investor_router())`.

`LoginRateLimiter.is_limited` records the current attempt and compares against the limit; check `api/src/api/auth.py:154-180` for whether it records before or after comparing. If `test_ten_notices_an_hour_then_429` gets a 429 on the tenth notice instead of the eleventh, the limiter counts the current call first: construct it with `max_attempts=REQUESTS_PER_HOUR + 1` and add a one-line comment saying why. The observable rule ("ten succeed, the eleventh is refused") is what the constraint means.

- [ ] **Step 4: Add the serializer and the endpoints**

Module-level helper (below `_iso`):

```python
def _deposit_json(row) -> Dict[str, Any]:
    (dep_id, user_id, account_id, amount, coin, txid, note, status, decided_by,
     decided_at, decision_note, created_at) = row[:12]
    out = {"id": dep_id, "user_id": user_id,
           "account_id": int(account_id) if account_id is not None else None,
           "amount": float(amount), "coin": coin, "txid": txid, "note": note,
           "status": status, "decided_by": decided_by, "decided_at": _iso(decided_at),
           "decision_note": decision_note, "created_at": _iso(created_at)}
    if len(row) > 12:
        out["email"], out["display_name"] = row[12], row[13]
    return out


_DEPOSIT_COLS = ("id, user_id, account_id, amount, coin, txid, note, status, decided_by, "
                 "decided_at, decision_note, created_at")


class DepositNotice(BaseModel):
    amount: Any
    coin: str
    txid: str
    note: Optional[str] = None


class Decision(BaseModel):
    status: str
    note: Optional[str] = None
```

Inside `create_investor_router`, after `investor_wallet`:

```python
    @router.get("/investor/deposits", response_model=List[Dict[str, Any]])
    async def my_deposits(ctx: OrgContext = Depends(require_org_role("investor")),
                          conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {_DEPOSIT_COLS} FROM investor_deposits "
            "WHERE org_id = %s AND user_id = %s ORDER BY created_at DESC, id DESC",
            (ctx.org_id, ctx.user_id)).fetchall()
        return [_deposit_json(r) for r in rows]

    @router.post("/investor/deposits", status_code=201, response_model=Dict[str, Any])
    async def file_deposit(body: DepositNotice,
                           ctx: OrgContext = Depends(require_org_role("investor")),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        wallet = _wallet(conn, ctx.org_id)
        if wallet is None:
            raise HTTPException(status_code=409, detail="Deposits are not open yet")
        try:
            amount = parse_amount(body.amount)
            coin = clean_text(body.coin, "coin", max_len=16).upper()
            txid = clean_text(body.txid, "txid")
            note = clean_text(body.note, "note", max_len=500, required=False)
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if coin != wallet["coin"].upper():
            raise HTTPException(
                status_code=400,
                detail=f"this workspace accepts {wallet['coin']} on {wallet['network']}, not {coin}")
        if hourly.is_limited(f"investor-deposit:{ctx.org_id}:{ctx.user_id}"):
            raise HTTPException(status_code=429, detail="too many deposit notices; try again later")
        row = conn.execute(
            "INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid, note) "
            f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_DEPOSIT_COLS}",
            (ctx.org_id, ctx.user_id, amount, coin, txid, note)).fetchone()
        out = _deposit_json(row)
        _event(conn, ctx.org_id, ctx.user_email, "investor_deposit_noticed", "warning",
               {"deposit_id": out["id"], "amount": out["amount"], "coin": coin, "txid": txid,
                "user_id": ctx.user_id,
                "summary": f"Deposit notice: {amount:.2f} {coin} from {ctx.user_email}"})
        return out
```

Inside `create_investor_admin_router`, after `link_account`:

```python
    @router.get("/investor-deposits", response_model=List[Dict[str, Any]])
    async def deposit_queue(status: Optional[str] = None,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "d.org_id = %s" + (" AND d.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        rows = conn.execute(
            "SELECT d.id, d.user_id, d.account_id, d.amount, d.coin, d.txid, d.note, d.status, "
            "d.decided_by, d.decided_at, d.decision_note, d.created_at, u.email, u.display_name "
            "FROM investor_deposits d JOIN users u ON u.id = d.user_id "
            f"WHERE {where} ORDER BY (d.status = 'pending') DESC, d.created_at DESC, d.id DESC",
            params).fetchall()
        return [_deposit_json(r) for r in rows]

    @router.post("/investor-deposits/{deposit_id}/decision", response_model=Dict[str, Any])
    async def decide_deposit(deposit_id: int, body: Decision,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        new_status = body.status.strip().lower()
        if not can_transition(DEPOSIT_TRANSITIONS, "pending", new_status):
            raise HTTPException(status_code=400, detail="status must be confirmed or rejected")
        try:
            note = clean_text(body.note, "note", max_len=500, required=(new_status == "rejected"))
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            "SELECT status, user_id FROM investor_deposits WHERE id = %s AND org_id = %s",
            (deposit_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Deposit not found")
        linked = _linked_account(conn, ctx.org_id, current[1]) if new_status == "confirmed" else None
        row = conn.execute(
            "UPDATE investor_deposits SET status = %s, decided_by = %s, decided_at = now(), "
            "decision_note = %s, account_id = COALESCE(%s, account_id) "
            "WHERE id = %s AND org_id = %s AND status = 'pending' "
            f"RETURNING {_DEPOSIT_COLS}",
            (new_status, ctx.user_id, note, linked, deposit_id, ctx.org_id)).fetchone()
        if not row:
            (status_now,) = conn.execute(
                "SELECT status FROM investor_deposits WHERE id = %s", (deposit_id,)).fetchone()
            raise HTTPException(status_code=409, detail=f"deposit is already {status_now}")
        out = _deposit_json(row)
        _event(conn, ctx.org_id, ctx.user_email, "investor_deposit_decided", "info",
               {"deposit_id": deposit_id, "status": new_status, "note": note,
                "user_id": current[1], "amount": out["amount"], "coin": out["coin"]},
               account_id=out["account_id"])
        return out
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_investor_portal.py -q -p no:cacheprovider`
Expected: PASS (20 tests).

- [ ] **Step 6: Commit**

```bash
git add api/src/api/routes/investor.py api/src/api/main.py api/tests/test_investor_portal.py
git commit -m "feat(api): investor deposit notices and the admin deposit queue"
```

---

### Task 6: Withdrawal requests, decisions and payment

**Files:**
- Modify: `api/src/api/routes/investor.py`
- Test: `api/tests/test_investor_portal.py` (append)

**Interfaces:**
- Consumes: Task 4/5 helpers, `WITHDRAWAL_TRANSITIONS`, `summarise`.
- Produces:
  - `_withdrawal_json(row) -> dict` with keys `id, user_id, account_id, amount, destination, status, equity_at_request, equity_verified, decided_by, decided_at, decision_note, paid_by, paid_at, txid, created_at` (+ `email, display_name` in admin lists).
  - Endpoints: `GET/POST /investor/withdrawals`, `GET /investor-withdrawals?status=`, `POST /investor-withdrawals/{id}/decision`, `POST /investor-withdrawals/{id}/paid`.
  - Event actions: `investor_withdrawal_requested` (warning, with `summary`), `investor_withdrawal_decided` (info), `investor_withdrawal_paid` (info).

- [ ] **Step 1: Write the failing tests** (append)

```python
# ------------------------------------------------------------- withdrawals


def _funded_investor(org_client, make_user, login_as, db, equity=5120.5):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db,
                                                     link_to=1001)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO investor_deposits (org_id, user_id, account_id, amount, coin, "
                     "txid, status) VALUES (%s, %s, 1001, 5000, 'USDT', 't', 'confirmed')",
                     (org_id, investor["id"]))
    if equity is None:
        _state(client, down=True)
    else:
        _state(client, {1001: {"balance": 5000.0, "equity": equity, "open_pnl": equity - 5000,
                               "positions": []}})
    return client, org_id, investor


def test_an_investor_requests_a_withdrawal_within_available(org_client, make_user, login_as, db):
    client, org_id, investor = _funded_investor(org_client, make_user, login_as, db)
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "1000", "destination": " TDest999 "}, headers=_csrf(client))
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "requested" and body["destination"] == "TDest999"
    assert body["equity_at_request"] == 5120.5 and body["equity_verified"] is True
    assert body["account_id"] == 1001
    summary = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    assert summary["pending_withdrawn"] == 1000.0 and summary["available"] == 4120.5
    with psycopg.connect(db, autocommit=True) as conn:
        (severity, payload) = conn.execute(
            "SELECT severity, payload FROM events WHERE org_id = %s ORDER BY id DESC LIMIT 1",
            (org_id,)).fetchone()
    assert severity == "warning" and payload["action"] == "investor_withdrawal_requested"
    assert payload["summary"] == "Withdrawal request: 1000.00 from inv@example.com"


def test_a_request_above_available_is_refused_with_the_figure(org_client, make_user, login_as, db):
    client, org_id, investor = _funded_investor(org_client, make_user, login_as, db)
    client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "4000", "destination": "TDest"}, headers=_csrf(client))
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "2000", "destination": "TDest"}, headers=_csrf(client))
    assert r.status_code == 400 and "1120.50" in r.json()["detail"]


def test_a_request_with_unknown_equity_is_accepted_but_flagged(org_client, make_user, login_as, db):
    client, org_id, investor = _funded_investor(org_client, make_user, login_as, db, equity=None)
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "99999", "destination": "TDest"}, headers=_csrf(client))
    assert r.status_code == 201
    assert r.json()["equity_verified"] is False and r.json()["equity_at_request"] is None


def test_no_account_no_withdrawal(org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "5", "destination": "TDest"}, headers=_csrf(client))
    assert r.status_code == 409 and "no account linked" in r.json()["detail"]


def test_approve_then_paid_and_the_ledger_moves(org_client, make_user, login_as, db):
    client, org_id, investor = _funded_investor(org_client, make_user, login_as, db)
    wd = client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "1000", "destination": "TDest"}, headers=_csrf(client)).json()
    admin = {"email": "admin@example.com", "password": "a-solid-password"}
    login_as(client, admin)
    queue = client.get(f"/api/orgs/{org_id}/investor-withdrawals?status=requested").json()
    assert [w["id"] for w in queue] == [wd["id"]] and queue[0]["email"] == "inv@example.com"

    r = client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/paid",
                    json={"txid": "x"}, headers=_csrf(client))
    assert r.status_code == 409 and "requested" in r.json()["detail"]

    r = client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/decision",
                    json={"status": "approved"}, headers=_csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "approved"

    r = client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/paid",
                    json={"txid": "  "}, headers=_csrf(client))
    assert r.status_code == 400 and "txid" in r.json()["detail"]
    r = client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/paid",
                    json={"txid": "chain-tx-1"}, headers=_csrf(client))
    assert r.status_code == 200
    assert r.json()["status"] == "paid" and r.json()["txid"] == "chain-tx-1"
    assert r.json()["paid_at"] is not None

    r = client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/decision",
                    json={"status": "rejected", "note": "too late"}, headers=_csrf(client))
    assert r.status_code == 409

    login_as(client, investor)
    summary = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    assert summary["total_withdrawn"] == 1000.0 and summary["net_deposits"] == 4000.0
    assert summary["pending_withdrawn"] == 0.0
    with psycopg.connect(db, autocommit=True) as conn:
        actions = [r[0] for r in conn.execute(
            "SELECT payload->>'action' FROM events WHERE org_id = %s ORDER BY id",
            (org_id,)).fetchall()]
    assert actions[-3:] == ["investor_withdrawal_requested", "investor_withdrawal_decided",
                            "investor_withdrawal_paid"]


def test_an_approved_request_can_still_be_rejected_with_a_note(org_client, make_user, login_as, db):
    client, org_id, investor = _funded_investor(org_client, make_user, login_as, db)
    wd = client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "10", "destination": "TDest"}, headers=_csrf(client)).json()
    login_as(client, {"email": "admin@example.com", "password": "a-solid-password"})
    client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/decision",
                json={"status": "approved"}, headers=_csrf(client))
    r = client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/decision",
                    json={"status": "rejected"}, headers=_csrf(client))
    assert r.status_code == 400 and "note" in r.json()["detail"]
    r = client.post(f"/api/orgs/{org_id}/investor-withdrawals/{wd['id']}/decision",
                    json={"status": "rejected", "note": "address did not match"},
                    headers=_csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "rejected"


def test_ten_withdrawal_requests_an_hour_then_429(org_client, make_user, login_as, db):
    client, org_id, investor = _funded_investor(org_client, make_user, login_as, db)
    for _ in range(10):
        assert client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
            "amount": "1", "destination": "TDest"}, headers=_csrf(client)).status_code == 201
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals", json={
        "amount": "1", "destination": "TDest"}, headers=_csrf(client))
    assert r.status_code == 429
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_investor_portal.py -q -p no:cacheprovider`
Expected: withdrawal tests FAIL with 404; the rest PASS.

- [ ] **Step 3: Add the serializer, models and endpoints**

Module-level, below `_deposit_json`:

```python
_WITHDRAWAL_COLS = ("id, user_id, account_id, amount, destination, status, equity_at_request, "
                    "equity_verified, decided_by, decided_at, decision_note, paid_by, paid_at, "
                    "txid, created_at")


def _withdrawal_json(row) -> Dict[str, Any]:
    (wd_id, user_id, account_id, amount, destination, status, eq_req, verified, decided_by,
     decided_at, decision_note, paid_by, paid_at, txid, created_at) = row[:15]
    out = {"id": wd_id, "user_id": user_id, "account_id": int(account_id),
           "amount": float(amount), "destination": destination, "status": status,
           "equity_at_request": float(eq_req) if eq_req is not None else None,
           "equity_verified": bool(verified), "decided_by": decided_by,
           "decided_at": _iso(decided_at), "decision_note": decision_note,
           "paid_by": paid_by, "paid_at": _iso(paid_at), "txid": txid,
           "created_at": _iso(created_at)}
    if len(row) > 15:
        out["email"], out["display_name"] = row[15], row[16]
    return out


class WithdrawalRequest(BaseModel):
    amount: Any
    destination: str


class PaidBody(BaseModel):
    txid: str
```

Inside `create_investor_router`, after `file_deposit`:

```python
    @router.get("/investor/withdrawals", response_model=List[Dict[str, Any]])
    async def my_withdrawals(ctx: OrgContext = Depends(require_org_role("investor")),
                             conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {_WITHDRAWAL_COLS} FROM investor_withdrawals "
            "WHERE org_id = %s AND user_id = %s ORDER BY created_at DESC, id DESC",
            (ctx.org_id, ctx.user_id)).fetchall()
        return [_withdrawal_json(r) for r in rows]

    @router.post("/investor/withdrawals", status_code=201, response_model=Dict[str, Any])
    async def request_withdrawal(body: WithdrawalRequest, http_request: Request,
                                 ctx: OrgContext = Depends(require_org_role("investor")),
                                 conn: psycopg.Connection = Depends(get_conn),
                                 cfg: ApiConfig = Depends(ApiConfig.from_env)) -> Dict[str, Any]:
        account_id = _linked_account(conn, ctx.org_id, ctx.user_id)
        if account_id is None:
            raise HTTPException(status_code=409, detail="no account linked yet")
        try:
            amount = parse_amount(body.amount)
            destination = clean_text(body.destination, "destination")
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        deposits, withdrawals = _ledger_rows(conn, ctx.org_id, ctx.user_id)
        equity, _source, _positions = await _equity_for(
            http_request.app.state.http, cfg, conn, ctx.org_id, account_id)
        available = summarise(deposits, withdrawals, equity).available
        if available is not None and amount > available:
            raise HTTPException(
                status_code=400,
                detail=f"amount exceeds what is available to withdraw ({available:.2f})")
        if hourly.is_limited(f"investor-withdrawal:{ctx.org_id}:{ctx.user_id}"):
            raise HTTPException(status_code=429, detail="too many withdrawal requests; try again later")
        row = conn.execute(
            "INSERT INTO investor_withdrawals (org_id, user_id, account_id, amount, destination, "
            "equity_at_request, equity_verified) VALUES (%s, %s, %s, %s, %s, %s, %s) "
            f"RETURNING {_WITHDRAWAL_COLS}",
            (ctx.org_id, ctx.user_id, account_id, amount, destination, equity,
             equity is not None)).fetchone()
        out = _withdrawal_json(row)
        _event(conn, ctx.org_id, ctx.user_email, "investor_withdrawal_requested", "warning",
               {"withdrawal_id": out["id"], "amount": out["amount"], "destination": destination,
                "user_id": ctx.user_id, "equity_verified": out["equity_verified"],
                "summary": f"Withdrawal request: {amount:.2f} from {ctx.user_email}"},
               account_id=account_id)
        return out
```

Inside `create_investor_admin_router`, after `decide_deposit`:

```python
    @router.get("/investor-withdrawals", response_model=List[Dict[str, Any]])
    async def withdrawal_queue(status: Optional[str] = None,
                               ctx: OrgContext = Depends(require_org_role("admin")),
                               conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "w.org_id = %s" + (" AND w.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        rows = conn.execute(
            "SELECT w.id, w.user_id, w.account_id, w.amount, w.destination, w.status, "
            "w.equity_at_request, w.equity_verified, w.decided_by, w.decided_at, "
            "w.decision_note, w.paid_by, w.paid_at, w.txid, w.created_at, u.email, u.display_name "
            "FROM investor_withdrawals w JOIN users u ON u.id = w.user_id "
            f"WHERE {where} ORDER BY (w.status IN ('requested', 'approved')) DESC, "
            "w.created_at DESC, w.id DESC", params).fetchall()
        return [_withdrawal_json(r) for r in rows]

    @router.post("/investor-withdrawals/{withdrawal_id}/decision", response_model=Dict[str, Any])
    async def decide_withdrawal(withdrawal_id: int, body: Decision,
                                ctx: OrgContext = Depends(require_org_role("admin")),
                                conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        new_status = body.status.strip().lower()
        if new_status not in ("approved", "rejected"):
            raise HTTPException(status_code=400, detail="status must be approved or rejected")
        try:
            note = clean_text(body.note, "note", max_len=500, required=(new_status == "rejected"))
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            "SELECT status, user_id FROM investor_withdrawals WHERE id = %s AND org_id = %s",
            (withdrawal_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Withdrawal not found")
        if not can_transition(WITHDRAWAL_TRANSITIONS, current[0], new_status):
            raise HTTPException(status_code=409, detail=f"withdrawal is already {current[0]}")
        row = conn.execute(
            "UPDATE investor_withdrawals SET status = %s, decided_by = %s, decided_at = now(), "
            "decision_note = %s WHERE id = %s AND org_id = %s AND status = %s "
            f"RETURNING {_WITHDRAWAL_COLS}",
            (new_status, ctx.user_id, note, withdrawal_id, ctx.org_id, current[0])).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="withdrawal was decided by someone else")
        out = _withdrawal_json(row)
        _event(conn, ctx.org_id, ctx.user_email, "investor_withdrawal_decided", "info",
               {"withdrawal_id": withdrawal_id, "status": new_status, "note": note,
                "user_id": current[1], "amount": out["amount"]}, account_id=out["account_id"])
        return out

    @router.post("/investor-withdrawals/{withdrawal_id}/paid", response_model=Dict[str, Any])
    async def mark_paid(withdrawal_id: int, body: PaidBody,
                        ctx: OrgContext = Depends(require_org_role("admin")),
                        conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            txid = clean_text(body.txid, "txid")
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            "SELECT status, user_id FROM investor_withdrawals WHERE id = %s AND org_id = %s",
            (withdrawal_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Withdrawal not found")
        if not can_transition(WITHDRAWAL_TRANSITIONS, current[0], "paid"):
            raise HTTPException(status_code=409, detail=f"withdrawal is {current[0]}, not approved")
        row = conn.execute(
            "UPDATE investor_withdrawals SET status = 'paid', paid_by = %s, paid_at = now(), "
            "txid = %s WHERE id = %s AND org_id = %s AND status = 'approved' "
            f"RETURNING {_WITHDRAWAL_COLS}",
            (ctx.user_id, txid, withdrawal_id, ctx.org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="withdrawal was changed by someone else")
        out = _withdrawal_json(row)
        _event(conn, ctx.org_id, ctx.user_email, "investor_withdrawal_paid", "info",
               {"withdrawal_id": withdrawal_id, "txid": txid, "user_id": current[1],
                "amount": out["amount"]}, account_id=out["account_id"])
        return out
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_investor_portal.py -q -p no:cacheprovider`
Expected: PASS (27 tests).

- [ ] **Step 5: Commit**

```bash
git add api/src/api/routes/investor.py api/tests/test_investor_portal.py
git commit -m "feat(api): investor withdrawal requests, admin decisions and payment records"
```

---

### Task 7: Investor read-throughs — positions, analytics, history

**Files:**
- Modify: `api/src/api/routes/investor.py`
- Test: `api/tests/test_investor_portal.py` (append)

**Interfaces:**
- Consumes: `_linked_account`, `_equity_for`, `_proxy_to_copier`, `COPIER_SLOW_COMMAND_TIMEOUT_S` (import from `.settings_control`).
- Produces: `GET /investor/positions` → `{"positions": [...], "equity_source": str}`; `GET /investor/analytics?weeks=` → the copier's analytics dict; `GET /investor/history/{deals|orders|cashflow}?from=&to=` → the copier's history dict. All three answer 409 `no account linked yet` when unlinked.

- [ ] **Step 1: Write the failing tests** (append)

```python
# ------------------------------------------------------------ read-throughs


def _recording_copier(client, accounts=None):
    """Answer the copier's read endpoints and remember every URL asked."""
    seen = []
    def callback(request):
        url = str(request.url)
        if "copier.test" not in url:
            return default_mock_callback(request)
        seen.append(url)
        if "/state" in url:
            return httpx.Response(200, json={
                "status": "ok", "accounts": {str(k): v for k, v in (accounts or {}).items()},
                "master_positions": [], "pending_orders": [], "drift": []})
        if "/analytics" in url:
            return httpx.Response(200, json={"closed_trades": 3, "wins": 2, "losses": 1,
                                             "net_pnl": 120.5, "weeks": 4})
        if "/history/" in url:
            return httpx.Response(200, json={"deals": [], "has_more": False})
        return default_mock_callback(request)
    client.app.state.mock_transport.set_callback(callback)
    return seen


def test_read_throughs_need_a_linked_account(org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    for tail in ("investor/positions", "investor/analytics",
                 "investor/history/deals?from=0&to=1"):
        r = client.get(f"/api/orgs/{org_id}/{tail}")
        assert r.status_code == 409 and "no account linked" in r.json()["detail"], tail


def test_positions_come_from_the_linked_accounts_state(org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db,
                                                     link_to=1001)
    _recording_copier(client, {1001: {"balance": 5000.0, "equity": 5010.0, "open_pnl": 10.0,
        "positions": [{"position_id": 7, "symbol_id": 41, "symbol": "XAUUSD", "side": "BUY",
                       "volume": 1, "stop_loss": 4300.0, "take_profit": 4400.0,
                       "entry_price": 4350.0, "pnl_quote": 10.0, "current_price": 4360.0}]},
        1002: {"balance": 1.0, "equity": 1.0, "open_pnl": 0.0,
               "positions": [{"position_id": 8, "symbol_id": 41, "symbol": "XAUUSD",
                              "side": "SELL", "volume": 1, "entry_price": 1.0}]}})
    body = client.get(f"/api/orgs/{org_id}/investor/positions").json()
    assert body["equity_source"] == "live"
    assert body["positions"] == [{"position_id": 7, "symbol": "XAUUSD", "side": "BUY",
                                  "volume": 1, "entry_price": 4350.0, "current_price": 4360.0,
                                  "stop_loss": 4300.0, "take_profit": 4400.0, "pnl_quote": 10.0}]


def test_analytics_and_history_are_asked_for_the_linked_account_only(
        org_client, make_user, login_as, db):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db,
                                                     link_to=1001)
    seen = _recording_copier(client)
    r = client.get(f"/api/orgs/{org_id}/investor/analytics?weeks=99")
    assert r.status_code == 200 and r.json()["net_pnl"] == 120.5
    assert any(u.endswith("/analytics?account_id=1001&weeks=12") for u in seen)
    r = client.get(f"/api/orgs/{org_id}/investor/history/deals?from=5&to=9")
    assert r.status_code == 200 and r.json() == {"deals": [], "has_more": False}
    assert any(u.endswith("/history/deals?account_id=1001&from=5&to=9") for u in seen)
    assert client.get(f"/api/orgs/{org_id}/investor/history/trades?from=0&to=1").status_code == 400
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_investor_portal.py -q -p no:cacheprovider`
Expected: the three new tests FAIL with 404.

- [ ] **Step 3: Add the endpoints**

Change the settings_control import at the top of `investor.py` to:

```python
from .settings_control import COPIER_SLOW_COMMAND_TIMEOUT_S, _proxy_to_copier
```

Add `from fastapi import Query` to the fastapi import line. Then inside `create_investor_router`, after `request_withdrawal`:

```python
    def _require_linked(conn: psycopg.Connection, ctx: OrgContext) -> int:
        account_id = _linked_account(conn, ctx.org_id, ctx.user_id)
        if account_id is None:
            raise HTTPException(status_code=409, detail="no account linked yet")
        return account_id

    @router.get("/investor/positions", response_model=Dict[str, Any])
    async def my_positions(http_request: Request,
                           ctx: OrgContext = Depends(require_org_role("investor")),
                           conn: psycopg.Connection = Depends(get_conn),
                           cfg: ApiConfig = Depends(ApiConfig.from_env)) -> Dict[str, Any]:
        account_id = _require_linked(conn, ctx)
        _equity, source, positions = await _equity_for(
            http_request.app.state.http, cfg, conn, ctx.org_id, account_id)
        keys = ("position_id", "symbol", "side", "volume", "entry_price", "current_price",
                "stop_loss", "take_profit", "pnl_quote")
        return {"equity_source": source,
                "positions": [{k: p.get(k) for k in keys} for p in positions if isinstance(p, dict)]}

    @router.get("/investor/analytics", response_model=Dict[str, Any])
    async def my_analytics(http_request: Request, weeks: int = 4,
                           ctx: OrgContext = Depends(require_org_role("investor")),
                           conn: psycopg.Connection = Depends(get_conn),
                           cfg: ApiConfig = Depends(ApiConfig.from_env)) -> Dict[str, Any]:
        account_id = _require_linked(conn, ctx)
        weeks = max(1, min(weeks, 12))
        return await _proxy_to_copier(
            http_request.app.state.http,
            f"{cfg.copier_control_url}/analytics?account_id={account_id}&weeks={weeks}",
            method="GET", timeout=COPIER_SLOW_COMMAND_TIMEOUT_S)

    @router.get("/investor/history/{kind}", response_model=Dict[str, Any])
    async def my_history(kind: str, http_request: Request,
                         from_ms: int = Query(..., alias="from"),
                         to_ms: int = Query(..., alias="to"),
                         ctx: OrgContext = Depends(require_org_role("investor")),
                         conn: psycopg.Connection = Depends(get_conn),
                         cfg: ApiConfig = Depends(ApiConfig.from_env)) -> Dict[str, Any]:
        if kind not in ("deals", "orders", "cashflow"):
            raise HTTPException(status_code=400, detail="kind must be deals, orders or cashflow")
        account_id = _require_linked(conn, ctx)
        return await _proxy_to_copier(
            http_request.app.state.http,
            f"{cfg.copier_control_url}/history/{kind}"
            f"?account_id={account_id}&from={from_ms}&to={to_ms}", method="GET")
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_investor_portal.py -q -p no:cacheprovider`
Expected: PASS (30 tests).

- [ ] **Step 5: Commit**

```bash
git add api/src/api/routes/investor.py api/tests/test_investor_portal.py
git commit -m "feat(api): investor positions, analytics and history read-throughs"
```

---

### Task 8: Notifications and the permission matrix

**Files:**
- Modify: `api/src/api/alerts.py:25-37, 87`, `api/src/api/telegram.py:25-29, 60-107`
- Modify: `api/src/api/routes/investor.py` (investor email on decisions)
- Modify: `api/tests/test_rbac_matrix.py:14-51` (new rows)
- Test: `api/tests/test_investor_portal.py` (append)

**Interfaces:**
- Produces: `EmailAlerter.send_to(to_addr: str, subject: str, text: str) -> bool`; both alerters match `("control","warning","investor_deposit_noticed")` and `("control","warning","investor_withdrawal_requested")`, cooled down per investor `user_id` rather than per account; `_notify_investor(conn, user_id, subject, text)` in `investor.py`, called after every deposit/withdrawal decision and payment.

- [ ] **Step 1: Write the failing tests** (append)

```python
# ------------------------------------------------------------ notifications
import asyncio

from api.alerts import ALERT_RULES, EmailAlerter
from api.telegram import TELEGRAM_RULES, TelegramNotifier
from api import ws as ws_module


def test_the_two_request_actions_reach_both_alerters():
    for rules in (ALERT_RULES, TELEGRAM_RULES):
        assert ("control", "warning", "investor_deposit_noticed") in rules
        assert ("control", "warning", "investor_withdrawal_requested") in rules


def _posted(callback_holder):
    posts = []
    def cb(request):
        posts.append(json.loads(request.content.decode()))
        return httpx.Response(200, json={"ok": True})
    callback_holder.append(cb)
    return posts


def test_telegram_text_for_investor_actions_is_the_summary_line():
    holder = []
    posts = _posted(holder)
    notifier = TelegramNotifier(http=httpx.AsyncClient(transport=httpx.MockTransport(holder[0])),
                                bot_token="t", chat_id="c")
    event = {"category": "control", "severity": "warning", "account_id": None,
             "payload": {"action": "investor_deposit_noticed", "user_id": 5,
                         "summary": "Deposit notice: 5000.00 USDT from inv@example.com"}}
    other = dict(event, payload={**event["payload"], "user_id": 6})

    async def scenario():
        # One event loop for all three calls: an httpx AsyncClient must not
        # be used across separate asyncio.run() loops.
        first = await notifier.consider(event)
        second = await notifier.consider(other)
        third = await notifier.consider(event)
        return first, second, third

    first, second, third = asyncio.run(scenario())
    assert first is True
    assert posts[0]["text"] == "💰 Copy Desk: Deposit notice: 5000.00 USDT from inv@example.com"
    assert second is True, "another investor is not cooled down"
    assert third is False, "same investor is cooled down"


def test_email_send_to_posts_to_the_given_address():
    holder = []
    posts = _posted(holder)
    alerter = EmailAlerter(http=httpx.AsyncClient(transport=httpx.MockTransport(holder[0])),
                           api_key="k", from_addr="Desk <d@example.com>", to_addr="")
    assert asyncio.run(alerter.send_to("inv@example.com", "Deposit confirmed", "hello")) is True
    assert posts[-1]["to"] == ["inv@example.com"] and posts[-1]["subject"] == "Deposit confirmed"


class _FakeAlerter:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail
    async def send_to(self, to_addr, subject, text):
        if self.fail:
            raise RuntimeError("resend down")
        self.sent.append((to_addr, subject))
        return True


def test_a_decision_emails_the_investor_and_a_failed_email_never_fails_the_request(
        org_client, make_user, login_as, db, monkeypatch):
    client, org_id, investor = _investor_with_wallet(org_client, make_user, login_as, db)
    dep = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "5", "coin": "USDT", "txid": "t"}, headers=_csrf(client)).json()
    dep2 = client.post(f"/api/orgs/{org_id}/investor/deposits", json={
        "amount": "6", "coin": "USDT", "txid": "t2"}, headers=_csrf(client)).json()
    login_as(client, {"email": "admin@example.com", "password": "a-solid-password"})
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    r = client.post(f"/api/orgs/{org_id}/investor-deposits/{dep['id']}/decision",
                    json={"status": "confirmed"}, headers=_csrf(client))
    assert r.status_code == 200
    assert fake.sent == [("inv@example.com", "Your deposit of 5.00 USDT was confirmed")]
    monkeypatch.setattr(ws_module.broadcaster, "alerter", _FakeAlerter(fail=True), raising=False)
    r = client.post(f"/api/orgs/{org_id}/investor-deposits/{dep2['id']}/decision",
                    json={"status": "rejected", "note": "no"}, headers=_csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "rejected"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_investor_portal.py -q -p no:cacheprovider`
Expected: rules test FAILS (tuples absent); Telegram test FAILS (cutoff text); `send_to` FAILS with `AttributeError`; the decision test FAILS (`fake.sent == []`).

- [ ] **Step 3: Extend the alerters**

`api/src/api/alerts.py` — add to `ALERT_RULES` after the `"TradingView alert refused"` line:

```python
    # An investor asked for something an admin must act on. Cooled down per
    # investor (payload.user_id), not per account -- see consider().
    ("control", "warning", "investor_deposit_noticed"): "Investor deposit notice",
    ("control", "warning", "investor_withdrawal_requested"): "Investor withdrawal request",
```

In `EmailAlerter.consider`, replace the line `cooldown_key = (action, event.get("account_id"))` with:

```python
        payload = event.get("payload") or {}
        scope = payload.get("user_id") if action.startswith("investor_") else event.get("account_id")
        cooldown_key = (action, scope)
```

Add this method to `EmailAlerter` (after `consider`):

```python
    async def send_to(self, to_addr: str, subject: str, text: str) -> bool:
        """One plain email to one address -- the investor's own -- for a
        decision made about their money. Needs only the API key: the admin
        recipient list is irrelevant here. Never raises."""
        if not self._api_key or not to_addr:
            return False
        try:
            response = await self._http.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"from": self._from, "to": [to_addr],
                      "subject": f"[Copy Desk] {subject}", "text": text},
            )
            if response.status_code >= 400:
                logger.error("Resend rejected investor email (%s): %s",
                             response.status_code, response.text[:500])
                return False
        except Exception as e:
            logger.error("Failed to send investor email: %s", e)
            return False
        return True
```

`api/src/api/telegram.py` — add to `TELEGRAM_RULES`:

```python
    ("control", "warning", "investor_deposit_noticed"),
    ("control", "warning", "investor_withdrawal_requested"),
```

In `TelegramNotifier.consider`, replace `cooldown_key = (action, event.get("account_id"))` with:

```python
        scope = payload.get("user_id") if action.startswith("investor_") else event.get("account_id")
        cooldown_key = (action, scope)
```

and replace the block that builds `text` (from `name = payload.get("nickname") ...` through the closing `)` of the `text = (...)` assignment) with:

```python
        if action.startswith("investor_"):
            text = f"💰 Copy Desk: {payload.get('summary') or action}"
        else:
            name = payload.get("nickname") or f"account {event.get('account_id')}"
            login = payload.get("trader_login")
            login_part = f" (login {login})" if login is not None else ""
            days = payload.get("days_left")
            days_part = (
                f" — {days} day{'' if days == 1 else 's'} left" if days is not None else "")
            text = (
                f"⏰ Copy Desk: {name}{login_part} reaches its cutoff on "
                f"{payload.get('cutoff_date')}{days_part}."
            )
```

- [ ] **Step 4: Email the investor on decisions**

In `api/src/api/routes/investor.py` add a module-level helper below `_iso`:

```python
async def _notify_investor(conn: psycopg.Connection, user_id: int, subject: str, text: str) -> None:
    """Best-effort email to the investor's own address. The alerter lives on
    the event broadcaster (main.py wires it); with none configured this is
    a no-op, and a failing send never fails the admin's request."""
    alerter = getattr(broadcaster, "alerter", None)
    if alerter is None:
        return
    row = conn.execute("SELECT email FROM users WHERE id = %s", (user_id,)).fetchone()
    if not row:
        return
    try:
        await alerter.send_to(row[0], subject, text)
    except Exception:
        logger.exception("investor email failed for user %s", user_id)
```

Then add one call before each `return out` in the three decision handlers:

In `decide_deposit`:
```python
        await _notify_investor(
            conn, current[1],
            f"Your deposit of {out['amount']:.2f} {out['coin']} was {new_status}",
            f"Status: {new_status}\nAmount: {out['amount']:.2f} {out['coin']}\n"
            f"Note: {note or '—'}\n\nOpen the portal for details.")
```

In `decide_withdrawal`:
```python
        await _notify_investor(
            conn, current[1],
            f"Your withdrawal of {out['amount']:.2f} was {new_status}",
            f"Status: {new_status}\nAmount: {out['amount']:.2f}\n"
            f"Destination: {out['destination']}\nNote: {note or '—'}\n\nOpen the portal for details.")
```

In `mark_paid`:
```python
        await _notify_investor(
            conn, current[1],
            f"Your withdrawal of {out['amount']:.2f} was paid",
            f"Amount: {out['amount']:.2f}\nDestination: {out['destination']}\n"
            f"Transaction: {txid}\n\nOpen the portal for details.")
```

- [ ] **Step 5: Add the new endpoints to the permission matrix**

In `api/tests/test_rbac_matrix.py` append to `MATRIX`:

```python
    ("GET",    "investor/summary",                None,                          "investor"),
    ("GET",    "investor/deposits",               None,                          "investor"),
    ("GET",    "investor/withdrawals",            None,                          "investor"),
    ("GET",    "investor-wallet",                 None,                          "admin"),
    ("GET",    "investors",                       None,                          "admin"),
    ("GET",    "investor-deposits",               None,                          "admin"),
    ("GET",    "investor-withdrawals",            None,                          "admin"),
```

- [ ] **Step 6: Run the portal tests, the matrix, and the existing alert tests**

Run: `.venv/Scripts/python -m pytest tests/test_investor_portal.py tests/test_rbac_matrix.py tests/test_alerts.py tests/test_telegram.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add api/src/api/alerts.py api/src/api/telegram.py api/src/api/routes/investor.py api/tests/test_rbac_matrix.py api/tests/test_investor_portal.py
git commit -m "feat(api): admin alerts and investor emails for deposits and withdrawals"
```

---

## Frontend

All dashboard commands run from `dashboard/`. Tests: `npx vitest run <file>`; the full gate is `npm test`.

### Task 9: Role, types, shared investor helpers, Members role lists

**Files:**
- Modify: `src/lib/roles.ts`, `src/lib/types.ts`, `src/pages/Members.tsx:10-11`
- Create: `src/lib/investor.ts`
- Test: `src/lib/investor.test.ts`, `src/pages/Members.investor.test.tsx`

**Interfaces:**
- Produces: `Role` includes `'investor'` with `RANK.investor === -1`; types `InvestorSummary, InvestorWallet, InvestorDeposit, InvestorWithdrawal, InvestorRow, InvestorPositions`; helpers `statusLabel(status): string`, `statusTone(status): 'ok' | 'warn' | 'bad' | 'quiet'`, `moneyOrDash(n: number | null | undefined): string`.

- [ ] **Step 1: Write the failing tests**

```ts
// src/lib/investor.test.ts
import { describe, expect, test } from 'vitest'
import { moneyOrDash, statusLabel, statusTone } from './investor'

describe('investor helpers', () => {
  test('statuses read as plain words with a tone', () => {
    expect(statusLabel('pending')).toBe('Pending review')
    expect(statusLabel('confirmed')).toBe('Confirmed')
    expect(statusLabel('requested')).toBe('Awaiting approval')
    expect(statusLabel('approved')).toBe('Approved, payment pending')
    expect(statusLabel('paid')).toBe('Paid')
    expect(statusLabel('rejected')).toBe('Rejected')
    expect(statusTone('confirmed')).toBe('ok')
    expect(statusTone('paid')).toBe('ok')
    expect(statusTone('pending')).toBe('warn')
    expect(statusTone('requested')).toBe('warn')
    expect(statusTone('approved')).toBe('warn')
    expect(statusTone('rejected')).toBe('bad')
    expect(statusTone('whatever')).toBe('quiet')
  })

  test('money shows a dash when unknown', () => {
    expect(moneyOrDash(null)).toBe('—')
    expect(moneyOrDash(undefined)).toBe('—')
    expect(moneyOrDash(1234.5)).toBe('$1,234.50')
  })
})
```

```tsx
// src/pages/Members.investor.test.tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import Members from './Members'
import { mockUseOrg } from '../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  useOrgMock.mockReturnValue(mockUseOrg('owner'))
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/members')) {
      return jsonResponse([{ user_id: 1, email: 'user@example.com', display_name: 'Test User',
                             role: 'owner', joined_at: '2026-09-01T00:00:00Z' }])
    }
    if (url.includes('/invites')) return jsonResponse([])
    return jsonResponse({})
  }))
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

test('investor is offered as an invite role and as an assignable role', async () => {
  render(<MemoryRouter><Members /></MemoryRouter>)
  const invite = await screen.findByLabelText('Invite role')
  expect(Array.from((invite as HTMLSelectElement).options).map((o) => o.value))
    .toContain('investor')
  const assign = await screen.findByLabelText('Role for user@example.com')
  expect(Array.from((assign as HTMLSelectElement).options).map((o) => o.value))
    .toContain('investor')
})
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/lib/investor.test.ts src/pages/Members.investor.test.tsx`
Expected: FAIL — `./investor` does not exist; the Members selects lack `investor`.

- [ ] **Step 3: Add the role, the types and the helpers**

`src/lib/roles.ts` — replace the first line and the `RANK` line:

```ts
export type Role = 'investor' | 'viewer' | 'trader' | 'admin' | 'owner'
```
```ts
// investor sits below viewer: it may open only the investor portal.
const RANK: Record<Role, number> = { investor: -1, viewer: 0, trader: 1, admin: 2, owner: 3 }
```

`src/lib/types.ts` — append:

```ts
/** What the investor portal shows on its Overview; every figure is money
 *  in the workspace's account currency, `null` when not knowable. */
export interface InvestorSummary {
  org: { id: number; name: string }
  link_state: 'linked' | 'unlinked'
  account: {
    account_id: number; nickname: string | null; platform: 'ctrader' | 'mt5' | string
    status: string; last_error: string | null; connected: boolean
  } | null
  equity_source: 'live' | 'last known' | 'unknown'
  wallet_configured: boolean
  total_deposited: number
  total_withdrawn: number
  net_deposits: number
  pending_withdrawn: number
  equity: number | null
  profit: number | null
  available: number | null
}

export interface InvestorWallet {
  coin: string
  network: string
  address: string
  memo: string | null
}

export interface InvestorDeposit {
  id: number
  user_id: number
  account_id: number | null
  amount: number
  coin: string
  txid: string
  note: string | null
  status: 'pending' | 'confirmed' | 'rejected' | string
  decided_by: number | null
  decided_at: string | null
  decision_note: string | null
  created_at: string
  /** Present in the admin queue only. */
  email?: string
  display_name?: string
}

export interface InvestorWithdrawal {
  id: number
  user_id: number
  account_id: number
  amount: number
  destination: string
  status: 'requested' | 'approved' | 'paid' | 'rejected' | string
  equity_at_request: number | null
  equity_verified: boolean
  decided_by: number | null
  decided_at: string | null
  decision_note: string | null
  paid_by: number | null
  paid_at: string | null
  txid: string | null
  created_at: string
  email?: string
  display_name?: string
}

/** One row of the admin's Investors table. */
export interface InvestorRow {
  user_id: number
  email: string
  display_name: string
  account_id: number | null
  nickname: string | null
  equity: number | null
  net_deposits: number
  profit: number | null
  pending_deposits: number
  pending_withdrawals: number
}

export interface InvestorPositions {
  equity_source: 'live' | 'last known' | 'unknown'
  positions: Array<{
    position_id: number; symbol: string | null; side: string; volume: number
    entry_price: number | null; current_price: number | null
    stop_loss: number | null; take_profit: number | null; pnl_quote: number | null
  }>
}
```

`src/lib/investor.ts`:

```ts
import { money } from './format'

export type StatusTone = 'ok' | 'warn' | 'bad' | 'quiet'

const LABELS: Record<string, string> = {
  pending: 'Pending review',
  confirmed: 'Confirmed',
  requested: 'Awaiting approval',
  approved: 'Approved, payment pending',
  paid: 'Paid',
  rejected: 'Rejected',
}

export function statusLabel(status: string): string {
  return LABELS[status] ?? status
}

export function statusTone(status: string): StatusTone {
  if (status === 'confirmed' || status === 'paid') return 'ok'
  if (status === 'pending' || status === 'requested' || status === 'approved') return 'warn'
  if (status === 'rejected') return 'bad'
  return 'quiet'
}

export function moneyOrDash(n: number | null | undefined): string {
  return n == null ? '—' : money(n)
}

/** Tailwind classes for a status pill, matching Automation's OutcomePill. */
export function pillClass(status: string): string {
  const tone = statusTone(status)
  return tone === 'ok' ? 'bg-profit-wash text-profit-deep'
    : tone === 'warn' ? 'bg-warn-wash text-warn-deep'
    : tone === 'bad' ? 'bg-loss-wash text-loss-deep'
    : 'bg-paper text-ink-soft'
}
```

(`money` is exported by `src/lib/format.ts` and formats `1234.5` as `$1,234.50`; if the existing formatter uses a different currency symbol, update the expected string in the test to match `money(1234.5)` rather than changing the formatter.)

`src/pages/Members.tsx` lines 10-11:

```tsx
const ASSIGNABLE: Role[] = ['investor', 'viewer', 'trader', 'admin', 'owner']
const INVITABLE: Role[] = ['investor', 'viewer', 'trader', 'admin']
```

- [ ] **Step 4: Run to verify they pass, plus the type check**

Run: `npx vitest run src/lib/investor.test.ts src/pages/Members.investor.test.tsx src/pages/Members.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: all PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add src/lib/roles.ts src/lib/types.ts src/lib/investor.ts src/lib/investor.test.ts src/pages/Members.tsx src/pages/Members.investor.test.tsx
git commit -m "feat(dashboard): investor role, portal types and shared helpers"
```

---

### Task 10: The investor shell in Layout

**Files:**
- Modify: `src/components/Layout.tsx:19-31, 380-390, 506-527`
- Test: `src/components/Layout.test.tsx` (append)

**Interfaces:**
- Produces: for `role === 'investor'` the sidebar shows Overview / Deposit / Withdraw / History / Account under `/org/:id/invest…`, no desk strip renders, and any non-`/invest` path redirects to `/org/:id/invest`. For admin and owner the desk nav gains "Investors" at `/org/:id/investors`.

- [ ] **Step 1: Write the failing tests** (append to `src/components/Layout.test.tsx`, using that file's existing `useOrgMock`, `makeOrgValue(role)` and `mockRoutes()` helpers)

```tsx
import { Route, Routes } from 'react-router-dom'

function renderShell(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/org/:orgId" element={<Layout />}>
          <Route index element={<div>desk home</div>} />
          <Route path="invest" element={<div>investor home</div>} />
          <Route path="invest/deposit" element={<div>investor deposit</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

test('an investor sees the portal nav and no desk strip', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('investor'))
  const fetchMock = mockRoutes()
  renderShell('/org/1/invest')
  expect(await screen.findByText('investor home')).toBeInTheDocument()
  for (const label of ['Overview', 'Deposit', 'Withdraw', 'History', 'Account']) {
    expect(screen.getAllByRole('link', { name: label }).length).toBeGreaterThan(0)
  }
  expect(screen.queryByRole('link', { name: 'Accounts' })).not.toBeInTheDocument()
  expect(screen.queryByText(/Close all positions/)).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/state'))).toBe(false)
})

test('an investor opening a desk path is sent to the portal', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('investor'))
  mockRoutes()
  renderShell('/org/1')
  expect(await screen.findByText('investor home')).toBeInTheDocument()
  expect(screen.queryByText('desk home')).not.toBeInTheDocument()
})

test('admins get an Investors nav item and viewers do not', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  mockRoutes()
  renderShell('/org/1')
  expect(await screen.findByText('desk home')).toBeInTheDocument()
  expect(screen.getAllByRole('link', { name: 'Investors' }).length).toBeGreaterThan(0)
  cleanup()
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  renderShell('/org/1')
  expect(await screen.findByText('desk home')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Investors' })).not.toBeInTheDocument()
})
```

(`cleanup` comes from `@testing-library/react`; add it to that import if the file does not already have it.)

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/components/Layout.test.tsx`
Expected: the three new tests FAIL (desk nav rendered for the investor; no redirect; no Investors link).

- [ ] **Step 3: Change the shell**

In `src/components/Layout.tsx`, change the react-router import to include `Navigate`:

```tsx
import { Outlet, Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
```

Replace `navItems` (lines 19-31) with:

```tsx
const navItems = (orgId: number, role: Role) => [
  { path: `/org/${orgId}`, label: 'Overview' },
  { path: `/org/${orgId}/accounts`, label: 'Accounts' },
  { path: `/org/${orgId}/positions`, label: 'Positions' },
  ...(can(role, 'trade') ? [
    { path: `/org/${orgId}/trade`, label: 'Trade' },
    { path: `/org/${orgId}/automation`, label: 'Automation' },
  ] : []),
  { path: `/org/${orgId}/history`, label: 'History' },
  { path: `/org/${orgId}/performance`, label: 'Performance' },
  { path: `/org/${orgId}/logs`, label: 'Logs' },
  ...(can(role, 'control') ? [
    { path: `/org/${orgId}/investors`, label: 'Investors' },
  ] : []),
  { path: `/org/${orgId}/members`, label: 'Members' },
]

/** The investor portal: only their own account and their own money. No
 *  desk strip, no kill switch, no prices -- none of it is theirs to see. */
const investorNavItems = (orgId: number) => [
  { path: `/org/${orgId}/invest`, label: 'Overview' },
  { path: `/org/${orgId}/invest/deposit`, label: 'Deposit' },
  { path: `/org/${orgId}/invest/withdraw`, label: 'Withdraw' },
  { path: `/org/${orgId}/invest/history`, label: 'History' },
  { path: `/org/${orgId}/invest/account`, label: 'Account' },
]
```

In the `Layout` component, replace `const items = navItems(orgId, role)` with:

```tsx
  const investor = role === 'investor'
  const items = investor ? investorNavItems(orgId) : navItems(orgId, role)
  const portalRoot = `/org/${orgId}/invest`
  const strayed = investor && !location.pathname.startsWith(portalRoot)
```

and replace the main-content block

```tsx
        <DeskStrip onAccounts={handleAccounts} />
        <main className="flex-1 overflow-y-auto p-4 md:p-6">
          <Outlet />
        </main>
```

with

```tsx
        {!investor && <DeskStrip onAccounts={handleAccounts} />}
        <main className="flex-1 overflow-y-auto p-4 md:p-6">
          {strayed ? <Navigate to={portalRoot} replace /> : <Outlet />}
        </main>
```

- [ ] **Step 4: Run the whole Layout test file and the type check**

Run: `npx vitest run src/components/Layout.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: all PASS, including the pre-existing "hides the Trade nav item for viewers" and "Members nav link is always present" tests.

- [ ] **Step 5: Commit**

```bash
git add src/components/Layout.tsx src/components/Layout.test.tsx
git commit -m "feat(dashboard): investor shell -- portal nav, no desk strip, redirect"
```

---

### Task 11: Deposit page with the QR wallet card

**Files:**
- Modify: `package.json` (dependencies)
- Create: `src/pages/investor/InvestorDeposit.tsx`
- Test: `src/pages/investor/InvestorDeposit.test.tsx`

**Interfaces:**
- Consumes: `GET investor/wallet` (404 when not configured), `GET/POST investor/deposits`; types `InvestorWallet`, `InvestorDeposit`; helpers `statusLabel`, `pillClass`; `errorText`, `formatWhen`, `money` from `../../lib/format`.
- Produces: default export `InvestorDeposit()`.

- [ ] **Step 1: Install the QR dependency**

Run: `npm install qrcode@^1.5.4 && npm install -D @types/qrcode@^1.5.5`
Expected: both appear in `package.json`; `npx tsc --noEmit -p tsconfig.app.json` still passes.

- [ ] **Step 2: Write the failing test**

```tsx
// src/pages/investor/InvestorDeposit.test.tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorDeposit from './InvestorDeposit'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))
vi.mock('qrcode', () => ({ default: { toDataURL: vi.fn(async () => 'data:image/png;base64,QR') } }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const wallet = { coin: 'USDT', network: 'TRC20', address: 'TAddr123', memo: null }
const notice = { id: 1, user_id: 1, account_id: null, amount: 5000, coin: 'USDT', txid: 'abc',
                 note: null, status: 'pending', decided_by: null, decided_at: null,
                 decision_note: null, created_at: '2026-09-23T10:00:00Z' }

function mockRoutes(opts: { wallet?: boolean } = {}) {
  const deposits: unknown[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/investor/wallet')) {
      return opts.wallet === false
        ? jsonResponse({ detail: 'Deposits are not open yet' }, 404)
        : jsonResponse(wallet)
    }
    if (url.endsWith('/investor/deposits') && init?.method === 'POST') {
      deposits.unshift(notice)
      return jsonResponse(notice, 201)
    }
    if (url.endsWith('/investor/deposits')) return jsonResponse(deposits)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('shows the wallet card with a QR and files a notice', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  expect(await screen.findByText('TAddr123')).toBeInTheDocument()
  expect(screen.getByText(/USDT on TRC20/)).toBeInTheDocument()
  expect((await screen.findByRole('img', { name: /QR/ })).getAttribute('src')).toContain('data:image')

  await userEvent.type(screen.getByLabelText('Amount'), '5000')
  await userEvent.type(screen.getByLabelText('Transaction ID'), 'abc')
  await userEvent.click(screen.getByRole('button', { name: 'I have sent it' }))

  const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'POST')
  expect(JSON.parse((post![1] as RequestInit).body as string)).toEqual(
    { amount: '5000', coin: 'USDT', txid: 'abc', note: '' })
  await waitFor(() => expect(screen.getByText('Pending review')).toBeInTheDocument())
})

test('says deposits are not open when there is no wallet', async () => {
  mockRoutes({ wallet: false })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  expect(await screen.findByText(/Deposits are not open yet/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'I have sent it' })).not.toBeInTheDocument()
})
```

- [ ] **Step 3: Run to verify it fails**

Run: `npx vitest run src/pages/investor/InvestorDeposit.test.tsx`
Expected: FAIL — module `./InvestorDeposit` not found.

- [ ] **Step 4: Write the page**

```tsx
// src/pages/investor/InvestorDeposit.tsx
import { useCallback, useEffect, useState } from 'react'
import QRCode from 'qrcode'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { pillClass, statusLabel } from '../../lib/investor'
import Banner from '../../components/Banner'
import type { InvestorDeposit as Deposit, InvestorWallet } from '../../lib/types'

export default function InvestorDeposit() {
  const { orgId } = useOrg()
  const [wallet, setWallet] = useState<InvestorWallet | null | 'closed'>(null)
  const [qr, setQr] = useState<string | null>(null)
  const [deposits, setDeposits] = useState<Deposit[]>([])
  const [form, setForm] = useState({ amount: '', txid: '', note: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setDeposits(await orgApi<Deposit[]>(orgId, 'investor/deposits'))
    } catch (err) {
      setError(errorText(err, 'Could not load your deposits'))
    }
    try {
      const w = await orgApi<InvestorWallet>(orgId, 'investor/wallet')
      setWallet(w)
      setQr(await QRCode.toDataURL(w.address, { width: 192, margin: 1 }))
    } catch (err) {
      if (err instanceof Error && err.message.startsWith('404')) setWallet('closed')
      else setError(errorText(err, 'Could not load the deposit address'))
    }
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (wallet === null || wallet === 'closed') return
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi<Deposit>(orgId, 'investor/deposits', {
        method: 'POST',
        body: JSON.stringify({ amount: form.amount, coin: wallet.coin, txid: form.txid,
                               note: form.note }),
      })
      setForm({ amount: '', txid: '', note: '' })
      setNotice('Notice filed. An admin will confirm it once the transfer is seen.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not file the notice'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6 max-w-4xl">
      <header>
        <h1 className="page-title">Deposit</h1>
        <p className="text-sm text-ink-soft mt-1">
          Send crypto to the address below, then tell us the amount and the transaction ID.
        </p>
      </header>
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      {wallet === 'closed' && (
        <section className="rounded-lg border border-line bg-card p-5">
          <p className="text-sm text-ink">Deposits are not open yet. Please check back later.</p>
        </section>
      )}

      {wallet !== null && wallet !== 'closed' && (
        <>
          <section className="rounded-lg border border-line bg-card p-5 grid gap-5 md:grid-cols-[192px_1fr]">
            {qr && <img src={qr} alt="QR code of the deposit address" width={192} height={192} />}
            <div className="space-y-3">
              <div>
                <div className="desk-label">Send only</div>
                <div className="text-lg font-semibold text-ink">{wallet.coin} on {wallet.network}</div>
              </div>
              <div>
                <div className="desk-label">Address</div>
                <div className="num text-sm text-ink break-all">{wallet.address}</div>
              </div>
              {wallet.memo && (
                <div>
                  <div className="desk-label">Memo / tag</div>
                  <div className="num text-sm text-ink">{wallet.memo}</div>
                </div>
              )}
              <p className="text-xs text-warn-deep bg-warn-wash rounded px-2 py-1">
                Sending any other coin or network to this address will lose the funds.
              </p>
            </div>
          </section>

          <form onSubmit={submit} className="rounded-lg border border-line bg-card p-5 space-y-4">
            <h2 className="desk-label">I have sent it</h2>
            <div className="flex gap-3 flex-wrap items-end">
              <label className="block w-40">
                <span className="desk-label block mb-1">Amount</span>
                <input aria-label="Amount" value={form.amount} required
                       onChange={(e) => setForm({ ...form, amount: e.target.value })}
                       className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
              </label>
              <label className="block flex-1 min-w-56">
                <span className="desk-label block mb-1">Transaction ID</span>
                <input aria-label="Transaction ID" value={form.txid} required
                       onChange={(e) => setForm({ ...form, txid: e.target.value })}
                       className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
              </label>
            </div>
            <label className="block">
              <span className="desk-label block mb-1">Note (optional)</span>
              <input aria-label="Note" value={form.note}
                     onChange={(e) => setForm({ ...form, note: e.target.value })}
                     className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
            </label>
            <button type="submit" disabled={busy}
                    className="px-4 py-2 text-sm font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep disabled:opacity-50">
              I have sent it
            </button>
          </form>
        </>
      )}

      <section className="rounded-lg border border-line bg-card overflow-hidden">
        <div className="px-5 pt-4 pb-3 flex items-baseline justify-between">
          <h2 className="desk-label">Your deposit notices</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-5 py-2 font-semibold">Filed</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Amount</th>
                <th className="desk-label px-5 py-2 font-semibold">Transaction</th>
                <th className="desk-label px-5 py-2 font-semibold">Status</th>
                <th className="desk-label px-5 py-2 font-semibold">Admin note</th>
              </tr>
            </thead>
            <tbody>
              {deposits.length === 0 && (
                <tr><td colSpan={5} className="text-center py-8 text-ink-faint">No notices yet</td></tr>
              )}
              {deposits.map((d) => (
                <tr key={d.id} className="border-b border-line last:border-0">
                  <td data-label="Filed" className="num px-5 py-2.5">{formatWhen(d.created_at)}</td>
                  <td data-label="Amount" className="tnum px-5 py-2.5 text-right">{money(d.amount)} {d.coin}</td>
                  <td data-label="Transaction" className="num px-5 py-2.5 break-all">{d.txid}</td>
                  <td data-label="Status" className="px-5 py-2.5">
                    <span className={`desk-label px-2 py-0.5 rounded ${pillClass(d.status)}`}>{statusLabel(d.status)}</span>
                  </td>
                  <td data-label="Admin note" className="px-5 py-2.5 text-ink-soft">{d.decision_note ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
```

- [ ] **Step 5: Run to verify it passes**

Run: `npx vitest run src/pages/investor/InvestorDeposit.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add package.json package-lock.json src/pages/investor/InvestorDeposit.tsx src/pages/investor/InvestorDeposit.test.tsx
git commit -m "feat(dashboard): investor Deposit page with wallet card and QR"
```

---

### Task 12: Investor Overview page

**Files:**
- Create: `src/pages/investor/InvestorOverview.tsx`
- Test: `src/pages/investor/InvestorOverview.test.tsx`

**Interfaces:**
- Consumes: `GET investor/summary`, `investor/positions`, `investor/analytics?weeks=4`, `investor/deposits`, `investor/withdrawals`; `StatTile`, `EquityCurve` from `../../components/charts` (takes `points: {timestamp, balance}[]`, the same shape as `Analytics.equity_curve`, and `height`), `useLiveRefresh`.
- Produces: default export `InvestorOverview()`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/pages/investor/InvestorOverview.test.tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorOverview from './InvestorOverview'
import * as apiModule from '../../lib/api'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

class MockWebSocket {
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  close() { /* no-op */ }
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const linked = {
  org: { id: 1, name: 'Desk' }, link_state: 'linked',
  account: { account_id: 1001, nickname: 'Inv', platform: 'mt5', status: 'ok', last_error: null, connected: true },
  equity_source: 'live', wallet_configured: true,
  total_deposited: 5000, total_withdrawn: 0, net_deposits: 5000, pending_withdrawn: 0,
  equity: 5120.5, profit: 120.5, available: 5120.5,
}
const unlinked = { ...linked, link_state: 'unlinked', account: null, equity_source: 'unknown',
                   equity: null, profit: null, available: null, net_deposits: 0, total_deposited: 0 }

function mockRoutes(summary: unknown) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/investor/summary')) return jsonResponse(summary)
    if (url.endsWith('/investor/positions')) {
      return jsonResponse({ equity_source: 'live', positions: [
        { position_id: 7, symbol: 'XAUUSD', side: 'BUY', volume: 1, entry_price: 4350,
          current_price: 4360, stop_loss: 4300, take_profit: 4400, pnl_quote: 10 }] })
    }
    if (url.includes('/investor/analytics')) {
      return jsonResponse({ closed_trades: 3, wins: 2, losses: 1, win_rate: 66.7,
        profit_factor: 2.1, best_trade: 50, worst_trade: -20, avg_win: 40, avg_loss: -20,
        net_pnl: 120.5, gross_wins: 140.5, gross_losses: 20, max_drawdown: 30,
        max_drawdown_pct: 0.6, equity_curve: [{ timestamp: 1, balance: 5000 },
        { timestamp: 2, balance: 5120.5 }], per_symbol: [], weekly: [], weeks: 4, truncated: false })
    }
    if (url.endsWith('/investor/deposits') || url.endsWith('/investor/withdrawals')) return jsonResponse([])
    return jsonResponse({})
  }))
}

beforeEach(() => {
  vi.spyOn(apiModule, 'eventsSocket').mockImplementation(() => new MockWebSocket() as never)
  useOrgMock.mockReturnValue(mockUseOrg('investor'))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('unlinked investors see the setup notice and no figures', async () => {
  mockRoutes(unlinked)
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.queryByText('XAUUSD')).not.toBeInTheDocument()
})

test('linked investors see equity, profit, positions and the snapshot', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorOverview /></MemoryRouter>)
  expect(await screen.findByText('$5,120.50')).toBeInTheDocument()
  expect(screen.getByText('$120.50')).toBeInTheDocument()
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  expect(await screen.findByText(/66\.7%/)).toBeInTheDocument()
  expect(screen.getByText(/live/i)).toBeInTheDocument()
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/pages/investor/InvestorOverview.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the page**

```tsx
// src/pages/investor/InvestorOverview.tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { useLiveRefresh } from '../../hooks/useLiveRefresh'
import { errorText, formatWhen, money, signed } from '../../lib/format'
import { moneyOrDash, pillClass, statusLabel } from '../../lib/investor'
import Banner from '../../components/Banner'
import StatTile from '../../components/StatTile'
import { EquityCurve } from '../../components/charts'
import type {
  Analytics, InvestorDeposit, InvestorPositions, InvestorSummary, InvestorWithdrawal,
} from '../../lib/types'

const POLL_MS = 10000

type Activity = { key: string; when: string; what: string; amount: number; status: string }

export default function InvestorOverview() {
  const { orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [positions, setPositions] = useState<InvestorPositions | null>(null)
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  const [activity, setActivity] = useState<Activity[]>([])
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const s = await orgApi<InvestorSummary>(orgId, 'investor/summary')
      setSummary(s)
      const [deps, wds] = await Promise.all([
        orgApi<InvestorDeposit[]>(orgId, 'investor/deposits'),
        orgApi<InvestorWithdrawal[]>(orgId, 'investor/withdrawals'),
      ])
      const rows: Activity[] = [
        ...deps.map((d) => ({ key: `d${d.id}`, when: d.created_at, what: `Deposit ${d.coin}`,
                              amount: d.amount, status: d.status })),
        ...wds.map((w) => ({ key: `w${w.id}`, when: w.created_at, what: 'Withdrawal',
                             amount: -w.amount, status: w.status })),
      ].sort((a, b) => (a.when < b.when ? 1 : -1)).slice(0, 8)
      setActivity(rows)
      if (s.link_state === 'linked') {
        const [p, a] = await Promise.all([
          orgApi<InvestorPositions>(orgId, 'investor/positions'),
          orgApi<Analytics>(orgId, 'investor/analytics?weeks=4'),
        ])
        setPositions(p); setAnalytics(a)
      }
      setError(null)
    } catch (err) {
      setError(errorText(err, 'Could not load your overview'))
    }
  }, [orgId])

  useEffect(() => {
    refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [refresh])
  useLiveRefresh(refresh, orgId)

  if (!summary && !error) return <div className="text-center py-12 text-ink-faint">Loading...</div>

  return (
    <div className="space-y-6 max-w-6xl">
      <header>
        <h1 className="page-title">Overview</h1>
        {summary && <p className="text-sm text-ink-soft mt-1">{summary.org.name}</p>}
      </header>
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}

      {summary?.link_state === 'unlinked' && (
        <section className="rounded-lg border border-line bg-card p-5 space-y-2">
          <h2 className="text-lg font-semibold text-ink">Your account is being set up</h2>
          <p className="text-sm text-ink-soft">
            Once your deposit is confirmed, an admin opens your trading account and links
            it here. You can file your deposit notice on the Deposit page now.
          </p>
        </section>
      )}

      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatTile label="Current equity" value={moneyOrDash(summary.equity)} tone="brand"
                    sub={summary.link_state === 'linked'
                      ? `${summary.equity_source} figure` : 'no account yet'} />
          <StatTile label="Profit" value={moneyOrDash(summary.profit)}
                    tone={summary.profit == null ? 'neutral' : summary.profit < 0 ? 'loss' : 'profit'}
                    sub="equity minus what you put in" />
          <StatTile label="Net deposits" value={money(summary.net_deposits)}
                    sub={`${money(summary.total_deposited)} in · ${money(summary.total_withdrawn)} out`} />
          <StatTile label="Pending withdrawals" value={money(summary.pending_withdrawn)}
                    sub={summary.available != null ? `${money(summary.available)} available` : undefined} />
        </div>
      )}

      {summary?.account && (
        <section className="rounded-lg border border-line bg-card p-5 flex flex-wrap gap-x-6 gap-y-2 text-sm">
          <div><span className="desk-label mr-2">Account</span>
            <span className="text-ink">{summary.account.nickname ?? summary.account.account_id}</span></div>
          <div><span className="desk-label mr-2">Platform</span>
            <span className="text-ink uppercase">{summary.account.platform}</span></div>
          <div><span className="desk-label mr-2">Connection</span>
            <span className={summary.account.connected ? 'text-profit' : 'text-warn-deep'}>
              {summary.account.connected ? 'connected' : 'terminal offline'}
            </span></div>
        </section>
      )}

      {positions && (
        <section className="rounded-lg border border-line bg-card overflow-hidden">
          <div className="px-5 pt-4 pb-3 flex items-baseline justify-between">
            <h2 className="desk-label">Open positions</h2>
            <span className="text-xs text-ink-soft">{positions.equity_source}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-5 py-2 font-semibold">Symbol</th>
                  <th className="desk-label px-5 py-2 font-semibold">Side</th>
                  <th className="desk-label px-5 py-2 font-semibold text-right">Volume</th>
                  <th className="desk-label px-5 py-2 font-semibold text-right">Entry</th>
                  <th className="desk-label px-5 py-2 font-semibold text-right">Current</th>
                  <th className="desk-label px-5 py-2 font-semibold text-right">SL / TP</th>
                  <th className="desk-label px-5 py-2 font-semibold text-right">Live P&L</th>
                </tr>
              </thead>
              <tbody>
                {positions.positions.length === 0 && (
                  <tr><td colSpan={7} className="text-center py-8 text-ink-faint">No open positions</td></tr>
                )}
                {positions.positions.map((p) => (
                  <tr key={p.position_id} className="border-b border-line last:border-0">
                    <td data-label="Symbol" className="px-5 py-2.5 text-ink">{p.symbol ?? '—'}</td>
                    <td data-label="Side" className="px-5 py-2.5">{p.side}</td>
                    <td data-label="Volume" className="tnum px-5 py-2.5 text-right">{p.volume}</td>
                    <td data-label="Entry" className="tnum px-5 py-2.5 text-right">{p.entry_price ?? '—'}</td>
                    <td data-label="Current" className="tnum px-5 py-2.5 text-right">{p.current_price ?? '—'}</td>
                    <td data-label="SL / TP" className="tnum px-5 py-2.5 text-right">{p.stop_loss ?? '—'} / {p.take_profit ?? '—'}</td>
                    <td data-label="Live P&L" className={`tnum px-5 py-2.5 text-right ${(p.pnl_quote ?? 0) < 0 ? 'text-loss' : 'text-profit'}`}>
                      {p.pnl_quote == null ? '—' : signed(p.pnl_quote)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {analytics && (
        <section className="rounded-lg border border-line bg-card p-5 space-y-4">
          <div className="flex items-baseline justify-between">
            <h2 className="desk-label">Performance, last {analytics.weeks} weeks</h2>
            <span className="text-xs text-ink-soft">{analytics.closed_trades} closed trades</span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatTile label="Net P&L" value={money(analytics.net_pnl)}
                      tone={analytics.net_pnl < 0 ? 'loss' : 'profit'} />
            <StatTile label="Win rate"
                      value={analytics.win_rate == null ? '—' : `${analytics.win_rate.toFixed(1)}%`}
                      sub={`${analytics.wins} won · ${analytics.losses} lost`} />
            <StatTile label="Max drawdown" value={money(analytics.max_drawdown)} tone="loss"
                      sub={`${analytics.max_drawdown_pct.toFixed(1)}% from peak`} />
            <StatTile label="Profit factor"
                      value={analytics.profit_factor == null ? '—' : analytics.profit_factor.toFixed(2)} />
          </div>
          {analytics.equity_curve.length > 1 && (
            <EquityCurve points={analytics.equity_curve} height={160} />
          )}
        </section>
      )}

      <section className="rounded-lg border border-line bg-card overflow-hidden">
        <div className="px-5 pt-4 pb-3"><h2 className="desk-label">Recent activity</h2></div>
        <ul className="divide-y divide-line">
          {activity.length === 0 && <li className="text-center py-8 text-ink-faint">Nothing yet</li>}
          {activity.map((a) => (
            <li key={a.key} className="px-5 py-2.5 text-sm flex items-center gap-4">
              <span className="num text-ink-soft w-40 shrink-0">{formatWhen(a.when)}</span>
              <span className="text-ink flex-1">{a.what}</span>
              <span className={`tnum ${a.amount < 0 ? 'text-loss' : 'text-ink'}`}>{signed(a.amount)}</span>
              <span className={`desk-label px-2 py-0.5 rounded ${pillClass(a.status)}`}>{statusLabel(a.status)}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
```

(`signed` from `src/lib/format.ts` formats a number with its sign; `EquityCurve`'s `points` prop accepts `Analytics.equity_curve` unchanged, exactly as `src/pages/Performance.tsx` passes it.)

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/pages/investor/InvestorOverview.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pages/investor/InvestorOverview.tsx src/pages/investor/InvestorOverview.test.tsx
git commit -m "feat(dashboard): investor Overview page"
```

---

### Task 13: Withdraw page

**Files:**
- Create: `src/pages/investor/InvestorWithdraw.tsx`
- Test: `src/pages/investor/InvestorWithdraw.test.tsx`

**Interfaces:**
- Consumes: `GET investor/summary` (for `available` and `link_state`), `GET/POST investor/withdrawals`; the server's refusal text (`amount exceeds what is available to withdraw (1120.50)`) is shown verbatim via `errorText`.
- Produces: default export `InvestorWithdraw()`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/pages/investor/InvestorWithdraw.test.tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorWithdraw from './InvestorWithdraw'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const summary = {
  org: { id: 1, name: 'Desk' }, link_state: 'linked',
  account: { account_id: 1001, nickname: 'Inv', platform: 'mt5', status: 'ok', last_error: null, connected: true },
  equity_source: 'live', wallet_configured: true, total_deposited: 5000, total_withdrawn: 0,
  net_deposits: 5000, pending_withdrawn: 0, equity: 5120.5, profit: 120.5, available: 5120.5,
}
const request = { id: 3, user_id: 1, account_id: 1001, amount: 1000, destination: 'TDest',
                  status: 'approved', equity_at_request: 5120.5, equity_verified: true,
                  decided_by: 2, decided_at: '2026-09-23T11:00:00Z', decision_note: null,
                  paid_by: null, paid_at: null, txid: null, created_at: '2026-09-23T10:00:00Z' }

function mockRoutes(opts: { refuse?: string; unlinked?: boolean } = {}) {
  const rows: unknown[] = []
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/investor/summary')) {
      return jsonResponse(opts.unlinked
        ? { ...summary, link_state: 'unlinked', account: null, available: null } : summary)
    }
    if (url.endsWith('/investor/withdrawals') && init?.method === 'POST') {
      if (opts.refuse) return jsonResponse({ detail: opts.refuse }, 400)
      rows.unshift(request)
      return jsonResponse(request, 201)
    }
    if (url.endsWith('/investor/withdrawals')) return jsonResponse(rows)
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('shows what is available and files a request', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  expect(await screen.findByText('$5,120.50')).toBeInTheDocument()
  await userEvent.type(screen.getByLabelText('Amount'), '1000')
  await userEvent.type(screen.getByLabelText('Destination address'), 'TDest')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const post = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'POST')
  expect(JSON.parse((post![1] as RequestInit).body as string))
    .toEqual({ amount: '1000', destination: 'TDest' })
  await waitFor(() => expect(screen.getByText('Approved, payment pending')).toBeInTheDocument())
})

test("the server's refusal is shown as written", async () => {
  mockRoutes({ refuse: 'amount exceeds what is available to withdraw (1120.50)' })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await screen.findByText('$5,120.50')
  await userEvent.type(screen.getByLabelText('Amount'), '9999')
  await userEvent.type(screen.getByLabelText('Destination address'), 'TDest')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  expect(await screen.findByText(/available to withdraw \(1120\.50\)/)).toBeInTheDocument()
})

test('unlinked investors cannot request yet', async () => {
  mockRoutes({ unlinked: true })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Request withdrawal' })).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/pages/investor/InvestorWithdraw.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the page**

```tsx
// src/pages/investor/InvestorWithdraw.tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { moneyOrDash, pillClass, statusLabel } from '../../lib/investor'
import Banner from '../../components/Banner'
import type { InvestorSummary, InvestorWithdrawal } from '../../lib/types'

const STEPS = ['requested', 'approved', 'paid'] as const

function Timeline({ w }: { w: InvestorWithdrawal }) {
  if (w.status === 'rejected') {
    return <span className={`desk-label px-2 py-0.5 rounded ${pillClass('rejected')}`}>Rejected</span>
  }
  const reached = STEPS.indexOf(w.status as typeof STEPS[number])
  return (
    <ol className="flex items-center gap-2 text-xs">
      {STEPS.map((step, i) => (
        <li key={step} className={`px-2 py-0.5 rounded ${i <= reached ? pillClass(step) : 'bg-paper text-ink-faint'}`}>
          {statusLabel(step)}
        </li>
      ))}
    </ol>
  )
}

export default function InvestorWithdraw() {
  const { orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [rows, setRows] = useState<InvestorWithdrawal[]>([])
  const [form, setForm] = useState({ amount: '', destination: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [s, list] = await Promise.all([
        orgApi<InvestorSummary>(orgId, 'investor/summary'),
        orgApi<InvestorWithdrawal[]>(orgId, 'investor/withdrawals'),
      ])
      setSummary(s); setRows(list)
    } catch (err) {
      setError(errorText(err, 'Could not load your withdrawals'))
    }
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi<InvestorWithdrawal>(orgId, 'investor/withdrawals', {
        method: 'POST', body: JSON.stringify(form),
      })
      setForm({ amount: '', destination: '' })
      setNotice('Request sent. You will be emailed when an admin decides.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not send the request'))
    } finally {
      setBusy(false)
    }
  }

  const linked = summary?.link_state === 'linked'

  return (
    <div className="space-y-6 max-w-4xl">
      <header>
        <h1 className="page-title">Withdraw</h1>
        <p className="text-sm text-ink-soft mt-1">
          Ask for an amount and where to send it. An admin approves, pays from the
          workspace wallet, and records the transaction.
        </p>
      </header>
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      {summary && !linked && (
        <section className="rounded-lg border border-line bg-card p-5">
          <p className="text-sm text-ink">Your account is being set up. Withdrawals open once it is linked.</p>
        </section>
      )}

      {summary && linked && (
        <form onSubmit={submit} className="rounded-lg border border-line bg-card p-5 space-y-4">
          <div className="flex items-baseline justify-between">
            <h2 className="desk-label">Available to withdraw</h2>
            <span className="num text-2xl font-semibold text-ink">{moneyOrDash(summary.available)}</span>
          </div>
          {summary.available == null && (
            <p className="text-xs text-warn-deep">
              Your account is offline right now, so the available figure is unknown; an admin will check it.
            </p>
          )}
          <div className="flex gap-3 flex-wrap items-end">
            <label className="block w-40">
              <span className="desk-label block mb-1">Amount</span>
              <input aria-label="Amount" value={form.amount} required
                     onChange={(e) => setForm({ ...form, amount: e.target.value })}
                     className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
            </label>
            <label className="block flex-1 min-w-56">
              <span className="desk-label block mb-1">Destination address</span>
              <input aria-label="Destination address" value={form.destination} required
                     onChange={(e) => setForm({ ...form, destination: e.target.value })}
                     className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
            </label>
          </div>
          <button type="submit" disabled={busy}
                  className="px-4 py-2 text-sm font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep disabled:opacity-50">
            Request withdrawal
          </button>
        </form>
      )}

      <section className="rounded-lg border border-line bg-card overflow-hidden">
        <div className="px-5 pt-4 pb-3"><h2 className="desk-label">Your requests</h2></div>
        <ul className="divide-y divide-line">
          {rows.length === 0 && <li className="text-center py-8 text-ink-faint">No requests yet</li>}
          {rows.map((w) => (
            <li key={w.id} className="px-5 py-3 text-sm space-y-1">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="num text-ink-soft">{formatWhen(w.created_at)}</span>
                <span className="tnum font-semibold text-ink">{money(w.amount)}</span>
                <span className="num text-ink-soft break-all">to {w.destination}</span>
              </div>
              <Timeline w={w} />
              {w.decision_note && <p className="text-xs text-ink-soft">Admin: {w.decision_note}</p>}
              {w.txid && <p className="num text-xs text-ink-soft break-all">Transaction: {w.txid}</p>}
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/pages/investor/InvestorWithdraw.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pages/investor/InvestorWithdraw.tsx src/pages/investor/InvestorWithdraw.test.tsx
git commit -m "feat(dashboard): investor Withdraw page with request timeline"
```

---

### Task 14: History and Account pages

**Files:**
- Create: `src/components/AccountSecurity.tsx` (moved out of Members), `src/pages/investor/InvestorHistory.tsx`, `src/pages/investor/InvestorAccount.tsx`
- Modify: `src/pages/Members.tsx` (import the moved component)
- Test: `src/pages/investor/InvestorHistory.test.tsx`

**Interfaces:**
- Consumes: `GET investor/history/deals?from=&to=` → `{ deals: Deal[]; has_more: boolean }` (`Deal` and `DealClose` from `../../lib/types`); `useOrg().me` for the Account page.
- Produces: default exports `InvestorHistory()`, `InvestorAccount()`; `AccountSecurity` as a default export from `src/components/AccountSecurity.tsx` with the same props it has today.

- [ ] **Step 1: Write the failing test**

```tsx
// src/pages/investor/InvestorHistory.test.tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorHistory from './InvestorHistory'
import { mockUseOrg } from '../../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const deal = { deal_id: 1, order_id: 1, position_id: 7, symbol_id: 41, symbol: 'XAUUSD',
               side: 'SELL', volume: 100, filled_volume: 100, volume_lots: '0.01',
               execution_price: 4360.0, status: 'FILLED', commission: -0.07,
               create_timestamp: 1758620000000, execution_timestamp: 1758620000000,
               close: { entry_price: 4350.0, gross_profit: 10.0, swap: -0.1, commission: -0.07,
                        balance: 5009.83, closed_volume: 100, closed_volume_lots: '0.01' } }

beforeEach(() => {
  useOrgMock.mockReturnValue(mockUseOrg('investor'))
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes('/investor/history/deals')) return jsonResponse({ deals: [deal], has_more: false })
    return jsonResponse({})
  }))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('lists closed deals for the last week and pages earlier', async () => {
  render(<MemoryRouter><InvestorHistory /></MemoryRouter>)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  expect(screen.getByText('$9.83')).toBeInTheDocument()
  const fetchMock = globalThis.fetch as unknown as ReturnType<typeof vi.fn>
  const first = String(fetchMock.mock.calls[0][0])
  await userEvent.click(screen.getByRole('button', { name: 'Earlier' }))
  const second = String(fetchMock.mock.calls[fetchMock.mock.calls.length - 1][0])
  const toOf = (u: string) => Number(new URL(u, 'http://x').searchParams.get('to'))
  expect(toOf(first) - toOf(second)).toBe(7 * 24 * 3600 * 1000)
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/pages/investor/InvestorHistory.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Move `AccountSecurity` into its own file**

In `src/pages/Members.tsx`, find the component that renders the "Your login" card (the change-password form posting to `/api/me/password` and the "Sign out everywhere" button posting to `/api/me/logout-all`). Cut it, with the imports it alone needs, into `src/components/AccountSecurity.tsx` as `export default function AccountSecurity(...)` with its props unchanged, and add `import AccountSecurity from '../components/AccountSecurity'` to `Members.tsx`. No behaviour changes; `npx vitest run src/pages/Members.test.tsx` must still pass.

- [ ] **Step 4: Write the two pages**

```tsx
// src/pages/investor/InvestorHistory.tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import Banner from '../../components/Banner'
import type { Deal } from '../../lib/types'

const WEEK_MS = 7 * 24 * 3600 * 1000

function netOf(d: Deal): number | null {
  if (!d.close) return null
  return d.close.gross_profit + d.close.swap + d.close.commission
}

export default function InvestorHistory() {
  const { orgId } = useOrg()
  const [windowEnd, setWindowEnd] = useState(() => Date.now())
  const [deals, setDeals] = useState<Deal[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const r = await orgApi<{ deals: Deal[]; has_more: boolean }>(
        orgId, `investor/history/deals?from=${windowEnd - WEEK_MS}&to=${windowEnd}`)
      setDeals(r.deals.filter((d) => d.close != null))
    } catch (err) {
      setError(errorText(err, 'Could not load your history'))
    } finally {
      setLoading(false)
    }
  }, [orgId, windowEnd])

  useEffect(() => { load() }, [load])

  const total = deals.reduce((sum, d) => sum + (netOf(d) ?? 0), 0)

  return (
    <div className="space-y-6 max-w-6xl">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="page-title">History</h1>
          <p className="text-sm text-ink-soft mt-1">
            Closed trades on your account, one week at a time, straight from the broker.
          </p>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <button onClick={() => setWindowEnd((t) => t - WEEK_MS)}
                  className="px-3 py-1.5 text-xs font-semibold rounded border border-line-strong text-ink-soft hover:text-ink">
            Earlier
          </button>
          <span className="num text-ink-soft">
            {formatWhen(windowEnd - WEEK_MS)} – {formatWhen(windowEnd)}
          </span>
          <button onClick={() => setWindowEnd((t) => Math.min(Date.now(), t + WEEK_MS))}
                  disabled={windowEnd >= Date.now()}
                  className="px-3 py-1.5 text-xs font-semibold rounded border border-line-strong text-ink-soft hover:text-ink disabled:opacity-50">
            Later
          </button>
        </div>
      </header>
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}

      <section className="rounded-lg border border-line bg-card overflow-hidden">
        <div className="px-5 pt-4 pb-3 flex items-baseline justify-between">
          <h2 className="desk-label">Closed trades</h2>
          <span className={`tnum text-sm font-semibold ${total < 0 ? 'text-loss' : 'text-profit'}`}>
            {money(total)} this week
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-5 py-2 font-semibold">Closed</th>
                <th className="desk-label px-5 py-2 font-semibold">Symbol</th>
                <th className="desk-label px-5 py-2 font-semibold">Side</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Lots</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Entry → Exit</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Gross</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Swap + fees</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Net</th>
              </tr>
            </thead>
            <tbody>
              {loading && <tr><td colSpan={8} className="text-center py-8 text-ink-faint">Loading...</td></tr>}
              {!loading && deals.length === 0 && (
                <tr><td colSpan={8} className="text-center py-8 text-ink-faint">No closed trades in this week</td></tr>
              )}
              {!loading && deals.map((d) => {
                const net = netOf(d) ?? 0
                return (
                  <tr key={d.deal_id} className="border-b border-line last:border-0">
                    <td data-label="Closed" className="num px-5 py-2.5">{formatWhen(d.execution_timestamp)}</td>
                    <td data-label="Symbol" className="px-5 py-2.5 text-ink">{d.symbol ?? d.symbol_id}</td>
                    <td data-label="Side" className="px-5 py-2.5">{d.side}</td>
                    <td data-label="Lots" className="tnum px-5 py-2.5 text-right">{d.close?.closed_volume_lots ?? d.volume_lots ?? '—'}</td>
                    <td data-label="Entry → Exit" className="tnum px-5 py-2.5 text-right">{d.close?.entry_price} → {d.execution_price ?? '—'}</td>
                    <td data-label="Gross" className="tnum px-5 py-2.5 text-right">{money(d.close!.gross_profit)}</td>
                    <td data-label="Swap + fees" className="tnum px-5 py-2.5 text-right">{money(d.close!.swap + d.close!.commission)}</td>
                    <td data-label="Net" className={`tnum px-5 py-2.5 text-right font-semibold ${net < 0 ? 'text-loss' : 'text-profit'}`}>{money(net)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
```

```tsx
// src/pages/investor/InvestorAccount.tsx
import { useOrg } from '../../lib/org'
import AccountSecurity from '../../components/AccountSecurity'

export default function InvestorAccount() {
  const { me, org } = useOrg()
  return (
    <div className="space-y-6 max-w-3xl">
      <header>
        <h1 className="page-title">Account</h1>
      </header>
      <section className="rounded-lg border border-line bg-card p-5 grid gap-3 md:grid-cols-2 text-sm">
        <div><div className="desk-label">Name</div><div className="text-ink">{me.user.display_name}</div></div>
        <div><div className="desk-label">Email</div><div className="text-ink">{me.user.email}</div></div>
        <div><div className="desk-label">Workspace</div><div className="text-ink">{org.name}</div></div>
        <div><div className="desk-label">Role</div><div className="text-ink">Investor</div></div>
      </section>
      <AccountSecurity />
    </div>
  )
}
```

(If `AccountSecurity` takes props in Members today, pass the same values Members passes; `useOrg()` provides `me` and `refreshMe` if it needs them.)

- [ ] **Step 5: Run the tests and the type check**

Run: `npx vitest run src/pages/investor/InvestorHistory.test.tsx src/pages/Members.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/components/AccountSecurity.tsx src/pages/Members.tsx src/pages/investor/InvestorHistory.tsx src/pages/investor/InvestorAccount.tsx src/pages/investor/InvestorHistory.test.tsx
git commit -m "feat(dashboard): investor History and Account pages"
```

---

### Task 15: Admin "Investors" page — table, linking, queues, wallet card

**Files:**
- Create: `src/pages/Investors.tsx`
- Test: `src/pages/Investors.test.tsx`

**Interfaces:**
- Consumes: `GET investors`, `PUT investors/{user_id}/account`, `GET investor-deposits`, `POST investor-deposits/{id}/decision`, `GET investor-withdrawals`, `POST investor-withdrawals/{id}/decision`, `POST investor-withdrawals/{id}/paid`, `GET/PUT investor-wallet` (GET 404 = none yet), `GET accounts` (for the link picker); `ConfirmDialog` (its `disabled` prop gates a required note), `useLiveRefresh`, `can(role, 'control')`.
- Produces: default export `Investors()`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/pages/Investors.test.tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import Investors from './Investors'
import * as apiModule from '../lib/api'
import { mockUseOrg } from '../test/orgMock'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

class MockWebSocket {
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  close() { /* no-op */ }
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const investor = { user_id: 5, email: 'inv@example.com', display_name: 'Ada Investor',
                   account_id: 1001, nickname: 'Inv', equity: 5120.5, net_deposits: 5000,
                   profit: 120.5, pending_deposits: 1, pending_withdrawals: 1 }
const deposit = { id: 11, user_id: 5, account_id: null, amount: 5000, coin: 'USDT', txid: 'abc',
                  note: null, status: 'pending', decided_by: null, decided_at: null,
                  decision_note: null, created_at: '2026-09-23T10:00:00Z',
                  email: 'inv@example.com', display_name: 'Ada Investor' }
const withdrawal = { id: 21, user_id: 5, account_id: 1001, amount: 1000, destination: 'TDest',
                     status: 'approved', equity_at_request: 5120.5, equity_verified: true,
                     decided_by: 1, decided_at: '2026-09-23T11:00:00Z', decision_note: null,
                     paid_by: null, paid_at: null, txid: null, created_at: '2026-09-23T10:30:00Z',
                     email: 'inv@example.com', display_name: 'Ada Investor' }

function mockRoutes() {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (url.endsWith('/investors')) return jsonResponse([investor])
    if (url.endsWith('/investor-deposits')) return jsonResponse([deposit])
    if (url.endsWith('/investor-withdrawals')) return jsonResponse([withdrawal])
    if (url.endsWith('/investor-wallet') && method === 'GET') return jsonResponse({ detail: 'none' }, 404)
    if (url.endsWith('/investor-wallet')) return jsonResponse(JSON.parse(init!.body as string))
    if (url.endsWith('/accounts')) {
      return jsonResponse([{ ctid_trader_account_id: 1001, trader_login: 1001, is_live: false,
        role: 'slave', enabled: true, multiplier: 1, status: 'ok', connection_status: 'active' },
        { ctid_trader_account_id: 1002, trader_login: 1002, is_live: false, role: 'slave',
          enabled: true, multiplier: 1, status: 'ok', connection_status: 'active' }])
    }
    if (url.includes('/decision') || url.includes('/paid')) return jsonResponse({ ...deposit, status: 'confirmed' })
    if (url.includes('/investors/5/account')) return jsonResponse({ user_id: 5, account_id: null })
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const bodyOf = (fetchMock: ReturnType<typeof vi.fn>, fragment: string) => {
  const call = fetchMock.mock.calls.find(([u, init]) =>
    String(u).includes(fragment) && (init as RequestInit)?.method === 'POST')
  return JSON.parse((call![1] as RequestInit).body as string)
}

beforeEach(() => {
  vi.spyOn(apiModule, 'eventsSocket').mockImplementation(() => new MockWebSocket() as never)
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('lists investors with their figures', async () => {
  mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  expect(await screen.findByText('Ada Investor')).toBeInTheDocument()
  expect(screen.getByText('$5,120.50')).toBeInTheDocument()
  expect(screen.getByText('$120.50')).toBeInTheDocument()
})

test('confirms a deposit, and a rejection needs a note', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('button', { name: 'Confirm deposit 11' }))
  await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/investor-deposits/11/decision'))
    .toEqual({ status: 'confirmed', note: '' }))

  await userEvent.click(screen.getByRole('button', { name: 'Reject deposit 11' }))
  const reject = screen.getByRole('button', { name: 'Reject' })
  expect(reject).toBeDisabled()
  await userEvent.type(screen.getByLabelText('Note'), 'no such transaction')
  expect(reject).toBeEnabled()
})

test('marks an approved withdrawal paid with a transaction id', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: /Withdrawals/ }))
  await userEvent.click(await screen.findByRole('button', { name: 'Mark withdrawal 21 paid' }))
  const paid = screen.getByRole('button', { name: 'Mark paid' })
  expect(paid).toBeDisabled()
  await userEvent.type(screen.getByLabelText('Transaction ID'), 'chain-tx-1')
  await userEvent.click(paid)
  await waitFor(() => expect(bodyOf(fetchMock, '/investor-withdrawals/21/paid'))
    .toEqual({ txid: 'chain-tx-1' }))
})

test('saves the wallet card', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.type(screen.getByLabelText('Coin'), 'USDT')
  await userEvent.type(screen.getByLabelText('Network'), 'TRC20')
  await userEvent.type(screen.getByLabelText('Address'), 'TAddr123')
  await userEvent.click(screen.getByRole('button', { name: 'Save wallet' }))
  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u, init]) =>
      String(u).endsWith('/investor-wallet') && (init as RequestInit)?.method === 'PUT')
    expect(JSON.parse((call![1] as RequestInit).body as string))
      .toEqual({ coin: 'USDT', network: 'TRC20', address: 'TAddr123', memo: '' })
  })
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/pages/Investors.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the page**

```tsx
// src/pages/Investors.tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import { errorText, formatWhen, money } from '../lib/format'
import { moneyOrDash, pillClass, statusLabel } from '../lib/investor'
import Banner from '../components/Banner'
import ConfirmDialog from '../components/ConfirmDialog'
import type {
  Account, InvestorDeposit, InvestorRow, InvestorWallet, InvestorWithdrawal,
} from '../lib/types'

const POLL_MS = 10000

/** What the admin is being asked to confirm. `requireText` blocks the
 *  confirm button until the note/txid box has something in it. */
interface Pending {
  title: string
  confirmLabel: string
  textLabel: 'Note' | 'Transaction ID'
  requireText: boolean
  danger?: boolean
  run: (text: string) => Promise<void>
}

export default function Investors() {
  const { orgId, role } = useOrg()
  const [rows, setRows] = useState<InvestorRow[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [deposits, setDeposits] = useState<InvestorDeposit[]>([])
  const [withdrawals, setWithdrawals] = useState<InvestorWithdrawal[]>([])
  const [wallet, setWallet] = useState({ coin: '', network: '', address: '', memo: '' })
  const [tab, setTab] = useState<'deposits' | 'withdrawals'>('deposits')
  const [pending, setPending] = useState<Pending | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [r, a, d, w] = await Promise.all([
        orgApi<InvestorRow[]>(orgId, 'investors'),
        orgApi<Account[]>(orgId, 'accounts'),
        orgApi<InvestorDeposit[]>(orgId, 'investor-deposits'),
        orgApi<InvestorWithdrawal[]>(orgId, 'investor-withdrawals'),
      ])
      setRows(r); setAccounts(a); setDeposits(d); setWithdrawals(w)
      try {
        const wl = await orgApi<InvestorWallet>(orgId, 'investor-wallet')
        setWallet((cur) => cur.address ? cur : { ...wl, memo: wl.memo ?? '' })
      } catch (err) {
        if (!(err instanceof Error && err.message.startsWith('404'))) throw err
      }
      setError(null)
    } catch (err) {
      setError(errorText(err, 'Could not load investors'))
    }
  }, [orgId])

  useEffect(() => {
    refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [refresh])
  useLiveRefresh(refresh, orgId)

  const control = can(role, 'control')

  const act = async (fn: () => Promise<void>, done: string) => {
    setBusy(true); setError(null); setNotice(null)
    try {
      await fn()
      setNotice(done)
      await refresh()
    } catch (err) {
      setError(errorText(err, 'The action failed'))
    } finally {
      setBusy(false)
    }
  }

  const linkAccount = (userId: number, accountId: number | null) => act(async () => {
    await orgApi(orgId, `investors/${userId}/account`, {
      method: 'PUT', body: JSON.stringify({ account_id: accountId }) })
  }, accountId == null ? 'Account unlinked' : 'Account linked')

  const saveWallet = (e: React.FormEvent) => {
    e.preventDefault()
    act(async () => {
      await orgApi(orgId, 'investor-wallet', { method: 'PUT', body: JSON.stringify(wallet) })
    }, 'Wallet saved')
  }

  const decideDeposit = (d: InvestorDeposit, status: 'confirmed' | 'rejected') => setPending({
    title: `${status === 'confirmed' ? 'Confirm' : 'Reject'} deposit of ${money(d.amount)} ${d.coin} from ${d.email}`,
    confirmLabel: status === 'confirmed' ? 'Confirm' : 'Reject',
    textLabel: 'Note', requireText: status === 'rejected', danger: status === 'rejected',
    run: (note) => act(async () => {
      await orgApi(orgId, `investor-deposits/${d.id}/decision`, {
        method: 'POST', body: JSON.stringify({ status, note }) })
    }, `Deposit ${status}`),
  })

  const decideWithdrawal = (w: InvestorWithdrawal, status: 'approved' | 'rejected') => setPending({
    title: `${status === 'approved' ? 'Approve' : 'Reject'} withdrawal of ${money(w.amount)} for ${w.email}`,
    confirmLabel: status === 'approved' ? 'Approve' : 'Reject',
    textLabel: 'Note', requireText: status === 'rejected', danger: status === 'rejected',
    run: (note) => act(async () => {
      await orgApi(orgId, `investor-withdrawals/${w.id}/decision`, {
        method: 'POST', body: JSON.stringify({ status, note }) })
    }, `Withdrawal ${status}`),
  })

  const markPaid = (w: InvestorWithdrawal) => setPending({
    title: `Record payment of ${money(w.amount)} to ${w.destination}`,
    confirmLabel: 'Mark paid', textLabel: 'Transaction ID', requireText: true,
    run: (txid) => act(async () => {
      await orgApi(orgId, `investor-withdrawals/${w.id}/paid`, {
        method: 'POST', body: JSON.stringify({ txid }) })
    }, 'Withdrawal marked paid'),
  })

  const linkedIds = new Set(rows.map((r) => r.account_id).filter((id) => id != null))
  const unlinked = accounts.filter((a) => !linkedIds.has(a.ctid_trader_account_id) && a.role !== 'master')

  const closeDialog = () => { setPending(null); setText('') }

  return (
    <div className="space-y-8 max-w-6xl">
      <header>
        <h1 className="page-title">Investors</h1>
        <p className="text-sm text-ink-soft mt-1">
          Who invests through this workspace, their linked accounts, and the money requests
          waiting on you. The app records; you move the funds.
        </p>
      </header>
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      <section className="rounded-lg border border-line bg-card overflow-hidden">
        <div className="px-5 pt-4 pb-3"><h2 className="desk-label">Investors</h2></div>
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-5 py-2 font-semibold">Investor</th>
                <th className="desk-label px-5 py-2 font-semibold">Account</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Equity</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Net deposits</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Profit</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Pending</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && <tr><td colSpan={6} className="text-center py-8 text-ink-faint">No investors yet — invite one from Members with the Investor role.</td></tr>}
              {rows.map((r) => (
                <tr key={r.user_id} className="border-b border-line last:border-0">
                  <td data-label="Investor" className="px-5 py-2.5">
                    <div className="text-ink">{r.display_name}</div>
                    <div className="text-xs text-ink-soft">{r.email}</div>
                  </td>
                  <td data-label="Account" className="px-5 py-2.5">
                    {control ? (
                      <select aria-label={`Account for ${r.email}`}
                              value={r.account_id ?? ''}
                              disabled={busy}
                              onChange={(e) => linkAccount(r.user_id, e.target.value ? Number(e.target.value) : null)}
                              className="border border-line-strong rounded bg-card px-2 py-1 text-sm">
                        <option value="">not linked</option>
                        {r.account_id != null && (
                          <option value={r.account_id}>{r.nickname ?? r.account_id}</option>
                        )}
                        {unlinked.map((a) => (
                          <option key={a.ctid_trader_account_id} value={a.ctid_trader_account_id}>
                            {a.nickname ?? a.trader_login} ({a.platform ?? 'ctrader'})
                          </option>
                        ))}
                      </select>
                    ) : (r.nickname ?? r.account_id ?? 'not linked')}
                  </td>
                  <td data-label="Equity" className="tnum px-5 py-2.5 text-right">{moneyOrDash(r.equity)}</td>
                  <td data-label="Net deposits" className="tnum px-5 py-2.5 text-right">{money(r.net_deposits)}</td>
                  <td data-label="Profit" className={`tnum px-5 py-2.5 text-right ${(r.profit ?? 0) < 0 ? 'text-loss' : 'text-profit'}`}>{moneyOrDash(r.profit)}</td>
                  <td data-label="Pending" className="tnum px-5 py-2.5 text-right">{r.pending_deposits} dep · {r.pending_withdrawals} wd</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="rounded-lg border border-line bg-card overflow-hidden">
        <div role="tablist" className="flex border-b border-line">
          {(['deposits', 'withdrawals'] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
                    className={`px-5 py-3 text-sm font-semibold ${tab === t ? 'text-brand border-b-2 border-brand' : 'text-ink-soft'}`}>
              {t === 'deposits' ? `Deposits (${deposits.filter((d) => d.status === 'pending').length} pending)`
                : `Withdrawals (${withdrawals.filter((w) => w.status === 'requested' || w.status === 'approved').length} open)`}
            </button>
          ))}
        </div>
        <div className="overflow-x-auto">
          {tab === 'deposits' ? (
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-5 py-2 font-semibold">Filed</th>
                  <th className="desk-label px-5 py-2 font-semibold">Investor</th>
                  <th className="desk-label px-5 py-2 font-semibold text-right">Amount</th>
                  <th className="desk-label px-5 py-2 font-semibold">Transaction</th>
                  <th className="desk-label px-5 py-2 font-semibold">Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {deposits.length === 0 && <tr><td colSpan={6} className="text-center py-8 text-ink-faint">No deposit notices</td></tr>}
                {deposits.map((d) => (
                  <tr key={d.id} className="border-b border-line last:border-0">
                    <td data-label="Filed" className="num px-5 py-2.5">{formatWhen(d.created_at)}</td>
                    <td data-label="Investor" className="px-5 py-2.5">{d.display_name}<div className="text-xs text-ink-soft">{d.email}</div></td>
                    <td data-label="Amount" className="tnum px-5 py-2.5 text-right">{money(d.amount)} {d.coin}</td>
                    <td data-label="Transaction" className="num px-5 py-2.5 break-all">{d.txid}{d.note && <div className="text-xs text-ink-soft">{d.note}</div>}</td>
                    <td data-label="Status" className="px-5 py-2.5"><span className={`desk-label px-2 py-0.5 rounded ${pillClass(d.status)}`}>{statusLabel(d.status)}</span>{d.decision_note && <div className="text-xs text-ink-soft">{d.decision_note}</div>}</td>
                    <td className="px-5 py-2.5 text-right whitespace-nowrap">
                      {control && d.status === 'pending' && (
                        <>
                          <button aria-label={`Confirm deposit ${d.id}`} disabled={busy} onClick={() => decideDeposit(d, 'confirmed')}
                                  className="px-3 py-1.5 text-xs font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep disabled:opacity-50 mr-2">Confirm</button>
                          <button aria-label={`Reject deposit ${d.id}`} disabled={busy} onClick={() => decideDeposit(d, 'rejected')}
                                  className="px-3 py-1.5 text-xs font-semibold rounded border border-loss text-loss hover:bg-loss hover:text-on-accent transition-colors disabled:opacity-50">Reject</button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-5 py-2 font-semibold">Requested</th>
                  <th className="desk-label px-5 py-2 font-semibold">Investor</th>
                  <th className="desk-label px-5 py-2 font-semibold text-right">Amount</th>
                  <th className="desk-label px-5 py-2 font-semibold">Destination</th>
                  <th className="desk-label px-5 py-2 font-semibold">Status</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {withdrawals.length === 0 && <tr><td colSpan={6} className="text-center py-8 text-ink-faint">No withdrawal requests</td></tr>}
                {withdrawals.map((w) => (
                  <tr key={w.id} className="border-b border-line last:border-0">
                    <td data-label="Requested" className="num px-5 py-2.5">{formatWhen(w.created_at)}</td>
                    <td data-label="Investor" className="px-5 py-2.5">{w.display_name}<div className="text-xs text-ink-soft">{w.email}</div></td>
                    <td data-label="Amount" className="tnum px-5 py-2.5 text-right">{money(w.amount)}
                      {!w.equity_verified && <div className="text-xs text-warn-deep">equity unverified</div>}
                      {w.equity_verified && w.equity_at_request != null && <div className="text-xs text-ink-soft">of {money(w.equity_at_request)} equity</div>}
                    </td>
                    <td data-label="Destination" className="num px-5 py-2.5 break-all">{w.destination}{w.txid && <div className="text-xs text-ink-soft">tx {w.txid}</div>}</td>
                    <td data-label="Status" className="px-5 py-2.5"><span className={`desk-label px-2 py-0.5 rounded ${pillClass(w.status)}`}>{statusLabel(w.status)}</span>{w.decision_note && <div className="text-xs text-ink-soft">{w.decision_note}</div>}</td>
                    <td className="px-5 py-2.5 text-right whitespace-nowrap">
                      {control && w.status === 'requested' && (
                        <button aria-label={`Approve withdrawal ${w.id}`} disabled={busy} onClick={() => decideWithdrawal(w, 'approved')}
                                className="px-3 py-1.5 text-xs font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep disabled:opacity-50 mr-2">Approve</button>
                      )}
                      {control && w.status === 'approved' && (
                        <button aria-label={`Mark withdrawal ${w.id} paid`} disabled={busy} onClick={() => markPaid(w)}
                                className="px-3 py-1.5 text-xs font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep disabled:opacity-50 mr-2">Mark paid</button>
                      )}
                      {control && (w.status === 'requested' || w.status === 'approved') && (
                        <button aria-label={`Reject withdrawal ${w.id}`} disabled={busy} onClick={() => decideWithdrawal(w, 'rejected')}
                                className="px-3 py-1.5 text-xs font-semibold rounded border border-loss text-loss hover:bg-loss hover:text-on-accent transition-colors disabled:opacity-50">Reject</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <form onSubmit={saveWallet} className="rounded-lg border border-line bg-card p-5 space-y-4">
        <h2 className="desk-label">Deposit wallet</h2>
        <p className="text-sm text-ink-soft">
          The address investors send to. Shown to them with a QR code. This is a receiving
          address only; MirrorFleet never holds a key.
        </p>
        <div className="flex gap-3 flex-wrap items-end">
          {([['coin', 'Coin', 'w-28'], ['network', 'Network', 'w-32'],
             ['address', 'Address', 'flex-1 min-w-64'], ['memo', 'Memo (optional)', 'w-40']] as const).map(([key, label, width]) => (
            <label key={key} className={`block ${width}`}>
              <span className="desk-label block mb-1">{label}</span>
              <input aria-label={label.replace(' (optional)', '')} value={wallet[key]} disabled={!control}
                     onChange={(e) => setWallet({ ...wallet, [key]: e.target.value })}
                     className="num w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
            </label>
          ))}
          {control && (
            <button type="submit" disabled={busy}
                    className="px-4 py-2 text-sm font-semibold rounded bg-brand text-on-accent hover:bg-brand-deep disabled:opacity-50">
              Save wallet
            </button>
          )}
        </div>
      </form>

      <ConfirmDialog
        open={pending != null}
        title={pending?.title ?? ''}
        confirmLabel={pending?.confirmLabel ?? 'Confirm'}
        danger={pending?.danger}
        busy={busy}
        disabled={Boolean(pending?.requireText) && text.trim() === ''}
        onConfirm={async () => {
          if (!pending) return
          const run = pending.run
          const value = text.trim()
          closeDialog()
          await run(value)
        }}
        onCancel={closeDialog}
      >
        <label className="block">
          <span className="desk-label block mb-1">
            {pending?.textLabel}{pending?.requireText ? '' : ' (optional)'}
          </span>
          <textarea aria-label={pending?.textLabel} value={text} rows={2}
                    onChange={(e) => setText(e.target.value)}
                    className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
        </label>
      </ConfirmDialog>
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npx vitest run src/pages/Investors.test.tsx && npx tsc --noEmit -p tsconfig.app.json`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/pages/Investors.tsx src/pages/Investors.test.tsx
git commit -m "feat(dashboard): admin Investors page with linking, queues and wallet card"
```

---

### Task 16: Routes, the full gates, and deployment

**Files:**
- Modify: `src/App.tsx`
- Deploy: production server via ssh (see steps)

**Interfaces:**
- Produces: routes `/org/:orgId/investors`, `/org/:orgId/invest`, `/invest/deposit`, `/invest/withdraw`, `/invest/history`, `/invest/account`.

- [ ] **Step 1: Wire the routes**

In `src/App.tsx`, add the imports after `import Logs from './pages/Logs'`:

```tsx
import Investors from './pages/Investors'
import InvestorOverview from './pages/investor/InvestorOverview'
import InvestorDeposit from './pages/investor/InvestorDeposit'
import InvestorWithdraw from './pages/investor/InvestorWithdraw'
import InvestorHistory from './pages/investor/InvestorHistory'
import InvestorAccount from './pages/investor/InvestorAccount'
```

and add these routes inside the `/org/:orgId` route, after `<Route path="members" element={<Members />} />`:

```tsx
          <Route path="investors" element={<Investors />} />
          <Route path="invest" element={<InvestorOverview />} />
          <Route path="invest/deposit" element={<InvestorDeposit />} />
          <Route path="invest/withdraw" element={<InvestorWithdraw />} />
          <Route path="invest/history" element={<InvestorHistory />} />
          <Route path="invest/account" element={<InvestorAccount />} />
```

- [ ] **Step 2: Run the whole dashboard gate**

Run (from `dashboard/`): `npm test`
Expected: type check clean, every vitest file PASS.

- [ ] **Step 3: Run the whole api suite** (from `api/`, env as in Global Constraints; never while a copier suite runs)

Run: `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider`
Expected: all PASS (the suite takes roughly ten minutes).

- [ ] **Step 4: Commit and push**

```bash
git add dashboard/src/App.tsx
git commit -m "feat(dashboard): investor portal routes"
git push origin main
```

- [ ] **Step 5: Deploy**

Migration 019 must be applied by the rebuilt `migrate` service; building `api` alone would skip it.

```bash
ssh -i ~/.ssh/mirrorfleet_deploy ubuntu@51.24.124.108 "cd ~/mirrorfleet && git pull --ff-only origin main && docker compose build migrate api && docker compose up migrate && docker compose up -d api && sleep 8 && docker compose ps --format '{{.Name}} {{.State}}' && docker compose exec -T postgres psql -U copytrader -d copytrader -c \"SELECT filename FROM schema_migrations ORDER BY filename DESC LIMIT 1;\" && docker compose logs --tail 5 api"
```

Expected: `migrate` prints `applied: ['019_investor_portal.sql']`, `api` is `running`, the latest migration row is `019_investor_portal.sql`, and the api log shows the EA's `/api/mt5/sync` polls answering 200.

- [ ] **Step 6: Manual end-to-end with a demo MT5 account, before any real money**

1. Members → create an **Investor** invite; register a second user in a private window and join with it.
2. As the investor: Overview shows "your account is being set up"; Deposit shows "not open yet".
3. As admin: Investors → save the wallet card; as the investor: Deposit shows the address and QR; file a notice with a made-up transaction ID.
4. Check the admin got the Telegram/email alert (if configured) and the notice appears in the Deposits queue; confirm it.
5. As admin: Accounts → add an MT5 account, connect a demo terminal with the EA; Investors → link that account to the investor.
6. As the investor: Overview shows live equity, the account as connected, positions once the master trades, and the performance snapshot; History lists closed trades; the confirmed deposit appears in Recent activity.
7. As the investor: request a withdrawal above `available` (refused with the figure), then a valid one.
8. As admin: approve, then mark paid with a transaction ID; the investor's timeline advances and (if configured) an email arrives at the investor's address.
9. Logs page: every step above appears with the actor's email.

- [ ] **Step 7: Sign off**

The feature is done when steps 2, 3 and 5 are green and every line of step 6 has been walked through on the demo account. Record anything that did not behave as this plan says as a note in the plan's execution ledger before calling it complete.

