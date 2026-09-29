# Client Portal Phase 1 (Wallets, Money Movement, Requests Desk) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every investor real wallets backed by a ledger, deposits by bank or crypto with receipts, approved payout destinations, MPIN-confirmed withdrawals and transfers, one Transactions list, and give admins one Requests desk plus payment-method settings, all on the existing MirrorFleet stack.

**Architecture:** Migration 022 adds `files`, `payment_methods`, `portal_settings`, `payout_destinations`, `wallet_entries`, `deposits`, `withdrawals`, `transfers` and retires the three 2026-09-23 tables. A pure ledger module (`portal_ledger.py`) owns money rules and state machines; `portal_common.py` owns balances, holds, idempotent settlement, audit and email; two routers (`portal_investor.py`, `portal_admin.py`) expose the investor and admin APIs under `/api/orgs/{org_id}`; `mpin_core.py` lends the login MPIN to money actions as a step-up; `uploads.py` + `portal_files.py` store receipts on a docker volume. The dashboard gains `Money`, `FileInput`, `PinConfirmDialog`, a grouped investor navigation with a phone More button, eight investor pages and two admin pages (Investors rewrite, Requests).

**Tech Stack:** Python 3.12 / FastAPI / psycopg 3 / argon2-cffi / python-multipart / pytest against real Postgres 16; React 18 / TypeScript strict / react-router 7 / Tailwind 4 tokens / vitest + Testing Library; docker compose on the host.

**Spec:** `docs/superpowers/specs/2026-09-29-client-portal-phase-1-money-design.md` (read it first; it is the authority). **Interfaces:** `docs/superpowers/plans/2026-09-29-client-portal-phase-1-interfaces.md` (every name, signature, shape, refusal string and aria-label; binding). **Code facts:** `docs/reference/mirrorfleet-subsystem-maps.md`.

## Global Constraints

- Branch `client-portal` (off `main` da485e2; the spec, interfaces and this plan are on it). Commit on it after every task; do not create other branches.
- Every commit message ends with these two lines:

  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b
  ```

  Subjects: `feat(api): …`, `feat(dashboard): …`, `test: …`, `docs: …`, `chore: …`.
- `copier/` is never touched. Task 20 proves it with `git diff --stat main..HEAD -- copier/` printing nothing.
- Money: `NUMERIC(18,2)` in SQL, `Decimal` in Python, floats rounded to cents in JSON (`money()`), strings from forms; `available` is floored to cents (`floor_cents`, `ROUND_DOWN`), never rounded up; every JSON money object carries `"currency": "USD"`.
- MPIN step-up: routes that take `mpin` call `require_mpin` before any other check and return its Response when present (400 malformed, 409 `MPIN not set`, 423 `MPIN locked` + `locked_until`, 401 `Invalid MPIN` + `attempts_left`). The dashboard calls those routes with `{ redirectOn401: false }`.
- Settlement (`settle`) happens in the same transaction as the status change, guarded by `wallet_entries_one_per_ref`; never anywhere else.
- Every mutation audits an `events` row (category `control`) with the action names in spec section 13; investor-scoped actions start with `investor_` and carry `payload.user_id`.
- Every new table is in the `db` fixture TRUNCATE list in `api/tests/conftest.py`.
- API tests need Docker Desktop running and `docker compose up -d postgres`. From `api/` in Git Bash (password from repo-root `.env`, key `POSTGRES_PASSWORD`):

  ```bash
  export TEST_POSTGRES_ADMIN_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader"
  export TEST_POSTGRES_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader_test"
  export PYTHONPATH="$(pwd -W)/src"
  .venv/Scripts/python -m pytest <files> -q -p no:cacheprovider
  ```

  Use `127.0.0.1`, never `localhost`. On this Windows host 7 `test_events_ws.py` tests error (psycopg async on the ProactorEventLoop) and one EA-download test fails on CRLF; both pass in the Linux container; do not fix them. New dependencies: `.venv/Scripts/python -m pip install -e ".[dev]"` from `api/`.
- Dashboard: `npm test` from `dashboard/` runs the palette prover, `tsc --noEmit -p tsconfig.app.json` and `vitest run`; on this machine run vitest as `npx vitest run --maxWorkers=2 --minWorkers=1`; a single file is `npx vitest run <path>`.
- Dashboard rules: colour only through `--color-*` tokens and the primitives (Button, Input, Select, Badge, Banner, Card, Tabs, Drawer, ConfirmDialog, StatTile, PageHeader, Loading, Menu, PinInput, Money, FileInput, PinConfirmDialog); data never sits directly on `.glass`; tables are `stack-table` with `data-label` on every `td`; the one `h1` comes from `PageHeader`; the words "Slave"/"slave" never appear in copy (say "follower"); never a literal "Loading..."; any colour pair new to the prover gets a row in `scripts/palette_check.mjs`; `tsc` is strict with `noUnusedLocals`. Tests use `mockUseOrg` from `src/test/orgMock.tsx`, fixtures from `src/test/portalFixtures.ts`, and stub `fetch` by URL tail.
- Between Task 1 and Task 10 the old investor endpoints are broken at runtime (their tables are gone). Nothing deploys in between; Task 1 deletes their test file so the suite stays green throughout.
- Deploy is not part of this plan. The owner triggers it; Task 20 writes the runbook line into README.

---

## File map

| File | Change |
|---|---|
| `db/migrations/022_client_wallets.sql` (new) | eight tables, data copy, three drops |
| `api/tests/test_migration_022.py` (new), `api/tests/conftest.py`, `api/tests/portal_helpers.py` (new) | migration shape; TRUNCATE list; shared helpers |
| `api/tests/test_investor_portal.py`, `api/tests/test_investor_ledger.py` | deleted (Tasks 1 and 10) |
| `api/src/api/portal_ledger.py` (new) + `api/tests/test_portal_ledger.py` (new) | pure money rules and state machines |
| `api/src/api/mpin_core.py` (new), `api/src/api/routes/mpin.py`, `api/tests/test_mpin_core.py` (new) | MPIN check extracted; `require_mpin` |
| `api/src/api/uploads.py` (new), `api/src/api/routes/portal_files.py` (new), `api/src/api/config.py`, `api/src/api/main.py`, `api/Dockerfile`, `docker-compose.yml`, `ops/backup.sh`, `.env.example`, `api/pyproject.toml`, `api/tests/test_uploads.py` (new) | receipt and proof uploads |
| `api/src/api/portal_common.py` (new) + `api/tests/test_portal_common.py` (new) | balances, holds, settle, audit, notify, serialisers |
| `api/src/api/routes/portal_investor.py` (new), `api/src/api/routes/portal_admin.py` (new), `api/src/api/main.py` | the two routers, grown over Tasks 6–10; mounted in Task 6 before the retired routers |
| `api/tests/test_portal_methods.py`, `test_portal_deposits.py`, `test_portal_withdrawals.py`, `test_portal_transfers.py`, `test_portal_summary.py` (new) | behaviour tests per task |
| `api/src/api/routes/investor.py`, `api/src/api/investor_ledger.py` | deleted (Task 10) |
| `api/src/api/alerts.py`, `api/src/api/telegram.py` | new warning actions |
| `dashboard/src/lib/types.ts`, `lib/investor.ts`, `lib/api.ts`, `lib/hideBalances.ts` (new) | portal types, vocabulary, uploads, hide store |
| `dashboard/src/components/Money.tsx`, `FileInput.tsx`, `PinConfirmDialog.tsx` (new, + tests) | shared money UI |
| `dashboard/src/test/portalFixtures.ts` (new) | test fixtures |
| `dashboard/src/components/layout/nav.ts`, `NavRail.tsx`, `BottomBar.tsx`, `Layout.tsx`, `hooks/useRequestsBadge.ts` (new) (+ tests) | grouped investor nav, badge, More |
| `dashboard/src/pages/investor/InvestorDashboard.tsx`, `InvestorWallet.tsx`, `InvestorTransfer.tsx`, `InvestorTransactions.tsx`, `InvestorPayoutAccounts.tsx` (new, + tests) | new investor pages |
| `dashboard/src/pages/investor/InvestorDeposit.tsx`, `InvestorWithdraw.tsx`, `InvestorAccount.tsx` (+ tests) | rewritten / extended |
| `dashboard/src/pages/investor/InvestorOverview.tsx` (+ test) | deleted (Task 13) |
| `dashboard/src/pages/Investors.tsx`, `pages/investors/PaymentMethodsTab.tsx`, `LedgerDrawer.tsx`, `AdjustDialog.tsx` (+ tests) | admin Investors rewrite |
| `dashboard/src/pages/Requests.tsx`, `pages/requests/RequestTabs.tsx`, `RequestDetailsDrawer.tsx` (+ tests) | admin Requests desk |
| `dashboard/src/pages/groups/investor.ts`, `groups/admin.ts`, `App.tsx` | barrels and routes |
| `README.md`, spec status line | deploy notes, status |

## Tasks

| # | Task |
|---|---|
| 1 | Migration 022, migration test, conftest and helpers |
| 2 | `portal_ledger.py` money rules |
| 3 | `mpin_core.py` extraction and `require_mpin` |
| 4 | Uploads: store, routes, volume, backup |
| 5 | `portal_common.py` balances, settlement, audit, serialisers |
| 6 | Payment methods and portal settings routes |
| 7 | Deposits routes |
| 8 | Payout destinations and withdrawals routes |
| 9 | Transfers routes |
| 10 | Summary, wallet entries, investors list, adjustments, requests summary; retire the old router |
| 11 | Dashboard foundation: types, vocabulary, Money, FileInput, PinConfirmDialog, fixtures |
| 12 | Navigation: groups, badge, More, Requests link |
| 13 | Investor Dashboard |
| 14 | Investor Deposit |
| 15 | Investor Withdraw and Payout accounts |
| 16 | Investor Transfer and Wallet |
| 17 | Investor Transactions and Account |
| 18 | Admin Investors page |
| 19 | Admin Requests page |
| 20 | Gates and docs |

---

### Task 1: Migration 022 — client wallets, the data copy, and the test fallout

Before any API test in this plan (once per shell, Git Bash from `api/`, Docker Desktop running):

```bash
docker compose -f ../docker-compose.yml up -d postgres
export TEST_POSTGRES_ADMIN_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader"
export TEST_POSTGRES_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader_test"
export PYTHONPATH="$(pwd -W)/src"
```

(`POSTGRES_PASSWORD` is the value in the repo-root `.env`.)

**Files:**
- Create: `db/migrations/022_client_wallets.sql`
- Create: `api/tests/test_migration_022.py`
- Create: `api/tests/portal_helpers.py`
- Modify: `api/tests/conftest.py` (the `db` fixture's TRUNCATE list)
- Modify: `api/tests/test_migration_019.py` (drop the three tests that touch the dropped tables)
- Modify: `api/tests/test_rbac_matrix.py` (drop the old-table seeding and the fourteen 2026-09-23 investor rows)
- Delete: `api/tests/test_investor_portal.py`
- Test: `api/tests/test_migration_022.py`

**Interfaces:**
- Consumes: `apply_migrations` (`db/migrate.py`); conftest `db`, `database`, `make_user`, `make_org`, `ADMIN_DSN`, `default_mock_callback`.
- Produces: tables `files`, `payment_methods`, `portal_settings`, `payout_destinations`, `wallet_entries`, `deposits`, `withdrawals`, `transfers` exactly as spec section 5, with indexes `files_by_user`, `payment_methods_by_org`, `payout_destinations_by_user`, `payout_destinations_queue`, `wallet_entries_by_user`, `wallet_entries_by_org_time`, `wallet_entries_one_per_ref`, `deposits_queue`, `deposits_by_user`, `deposits_one_live_reference`, `withdrawals_queue`, `withdrawals_by_user`, `transfers_queue`, `transfers_by_user`; `api/tests/portal_helpers.py` with `csrf`, `member`, `add_method`, `credit`, `approved_destination`, `seed_file`, `link`, `set_state`, `set_copier_down`.

- [ ] **Step 1: Write the failing migration test**

Create `api/tests/test_migration_022.py`:

```python
# api/tests/test_migration_022.py
"""Migration 022: the client portal's money tables -- files, payment
methods, portal settings, payout destinations, the wallet ledger, deposits,
withdrawals and transfers -- and the copy-then-drop of the three
2026-09-23 tables. conftest applies EVERY migration, so the `db` tests
assert the post-migration shape; the copy is exercised on a scratch
database stopped at 021, like test_migration_020's upgrade test."""
import pathlib
from decimal import Decimal

import psycopg
import pytest
from psycopg.types.json import Jsonb

from conftest import ADMIN_DSN

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "db" / "migrations"
UPGRADE_DB = "copytrader_mig022"
UPGRADE_DSN = ADMIN_DSN.rsplit("/", 1)[0] + f"/{UPGRADE_DB}"

AUDIT = ["decided_by", "decided_at", "decision_note"]
COLUMNS = {
    "files": ["id", "org_id", "user_id", "purpose", "content_type", "size_bytes", "sha256",
              "storage_key", "created_at"],
    "payment_methods": ["id", "org_id", "kind", "label", "enabled", "currency", "details",
                        "min_amount", "fee_pct", "instructions", "sort_order", "created_by",
                        "created_at", "updated_at"],
    "portal_settings": ["org_id", "withdrawal_min", "withdrawal_fee_pct", "updated_by",
                        "updated_at"],
    "payout_destinations": ["id", "org_id", "user_id", "kind", "nickname", "details",
                            "proof_file_id", "status", *AUDIT, "created_at"],
    "wallet_entries": ["id", "org_id", "user_id", "wallet", "amount", "kind", "ref_table",
                       "ref_id", "note", "created_by", "created_at"],
    "deposits": ["id", "org_id", "user_id", "method_id", "method_kind", "method_label", "amount",
                 "fee", "credited_amount", "reference", "receipt_file_id", "target",
                 "target_account_id", "note", "status", *AUDIT, "created_at"],
    "withdrawals": ["id", "org_id", "user_id", "destination_id", "destination_kind",
                    "destination_summary", "amount", "fee", "net_amount", "status", *AUDIT,
                    "paid_by", "paid_at", "txid", "created_at"],
    "transfers": ["id", "org_id", "user_id", "source_kind", "source_wallet", "source_account_id",
                  "target_kind", "target_wallet", "target_account_id", "amount", "status",
                  "equity_at_request", "equity_verified", *AUDIT, "done_by", "done_at", "note",
                  "created_at"],
}
INDEXES = ["files_by_user", "payment_methods_by_org", "payout_destinations_by_user",
           "payout_destinations_queue", "wallet_entries_by_user", "wallet_entries_by_org_time",
           "wallet_entries_one_per_ref", "deposits_queue", "deposits_by_user",
           "deposits_one_live_reference", "withdrawals_queue", "withdrawals_by_user",
           "transfers_queue", "transfers_by_user"]


def _people(make_user, make_org):
    admin = make_user()
    investor = make_user(email="inv@example.com")
    org_id = make_org(members=[(admin, "admin"), (investor, "investor")])
    return org_id, admin, investor


def test_migration_022_is_recorded_right_after_021(db):
    with psycopg.connect(db, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY filename").fetchall()]
    assert "022_client_wallets.sql" in names
    assert names.index("022_client_wallets.sql") == names.index("021_mpin.sql") + 1


@pytest.mark.parametrize("table", list(COLUMNS))
def test_each_table_has_exactly_the_spec_columns_in_order(db, table):
    with psycopg.connect(db, autocommit=True) as conn:
        cols = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s ORDER BY ordinal_position", (table,)).fetchall()]
    assert cols == COLUMNS[table]


def test_the_three_2026_09_23_tables_are_gone(db):
    with psycopg.connect(db, autocommit=True) as conn:
        for old in ("investor_withdrawals", "investor_deposits", "org_investor_wallets"):
            (reg,) = conn.execute("SELECT to_regclass(%s)", (old,)).fetchone()
            assert reg is None, old


def test_the_named_indexes_exist_and_the_unique_ones_are_partial(db):
    with psycopg.connect(db, autocommit=True) as conn:
        defs = dict(conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY(%s)",
            (INDEXES,)).fetchall())
    assert sorted(defs) == sorted(INDEXES)
    live = defs["deposits_one_live_reference"]
    assert "UNIQUE" in live and "(org_id, reference)" in live
    assert "pending" in live and "confirmed" in live
    once = defs["wallet_entries_one_per_ref"]
    assert "UNIQUE" in once and "(ref_table, ref_id, wallet)" in once and "IS NOT NULL" in once
    for queue in ("deposits_queue", "withdrawals_queue", "transfers_queue",
                  "payout_destinations_queue"):
        assert "(org_id, status, created_at)" in defs[queue], queue
    assert "(org_id, user_id, wallet, created_at DESC, id DESC)" in defs["wallet_entries_by_user"]
    assert "(org_id, sort_order, id)" in defs["payment_methods_by_org"]
    assert "(org_id, user_id, created_at DESC)" in defs["files_by_user"]


def test_wallet_entries_refuse_zero_and_settle_once_per_reference(db, make_user, make_org):
    org_id, admin, investor = _people(make_user, make_org)
    entry = ("INSERT INTO wallet_entries (org_id, user_id, wallet, amount, kind, ref_table, ref_id) "
             "VALUES (%s, %s, %s, %s, %s, %s, %s)")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(entry, (org_id, investor["id"], "main", 100, "deposit", "deposits", 1))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(entry, (org_id, investor["id"], "main", 100, "deposit", "deposits", 1))
        # The settlement idiom every route uses: a replay is a no-op, not an error.
        conn.execute(entry + " ON CONFLICT (ref_table, ref_id, wallet) WHERE ref_table IS NOT NULL "
                     "DO NOTHING", (org_id, investor["id"], "main", 100, "deposit", "deposits", 1))
        # The two legs of a wallet->wallet transfer are different wallets, so both fit.
        conn.execute(entry, (org_id, investor["id"], "pamm", -100, "transfer", "transfers", 1))
        conn.execute(entry, (org_id, investor["id"], "main", 100, "transfer", "transfers", 1))
        # Adjustments carry no reference and may repeat.
        conn.execute(entry, (org_id, investor["id"], "main", 5, "adjustment", None, None))
        conn.execute(entry, (org_id, investor["id"], "main", 5, "adjustment", None, None))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(entry, (org_id, investor["id"], "main", 0, "adjustment", None, None))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(entry, (org_id, investor["id"], "savings", 1, "adjustment", None, None))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(entry, (org_id, investor["id"], "main", 1, "refund", None, None))
        (total,) = conn.execute(
            "SELECT sum(amount) FROM wallet_entries WHERE wallet = 'main'").fetchone()
    assert total == Decimal("210.00")


def test_deposits_keep_one_live_reference_per_org(db, make_user, make_org):
    org_id, admin, investor = _people(make_user, make_org)
    dep = ("INSERT INTO deposits (org_id, user_id, method_kind, method_label, amount, reference) "
           "VALUES (%s, %s, 'crypto', 'USDT on TRC20', %s, %s) RETURNING id, status, target, fee")
    with psycopg.connect(db, autocommit=True) as conn:
        row = conn.execute(dep, (org_id, investor["id"], 10, "same-tx")).fetchone()
        assert row[1:] == ("pending", "wallet", Decimal("0"))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(dep, (org_id, investor["id"], 20, "same-tx"))
        # A cancelled (or rejected) row leaves the index, so the reference is free again.
        conn.execute("UPDATE deposits SET status = 'cancelled' WHERE id = %s", (row[0],))
        conn.execute(dep, (org_id, investor["id"], 20, "same-tx"))
        # ...and the same reference in ANOTHER workspace was never in the way.
        other = make_org(name="Other", members=[(admin, "admin")])
        conn.execute(dep, (other, admin["id"], 30, "same-tx"))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(dep, (org_id, investor["id"], 0, "zero"))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO deposits (org_id, user_id, method_kind, method_label, amount, "
                "reference, status) VALUES (%s, %s, 'crypto', 'x', 1, 'st', 'done')",
                (org_id, investor["id"]))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO deposits (org_id, user_id, method_kind, method_label, amount, "
                "reference, target) VALUES (%s, %s, 'crypto', 'x', 1, 'tg', 'bank')",
                (org_id, investor["id"]))


def test_transfers_check_both_ends(db, make_user, make_org):
    org_id, admin, investor = _people(make_user, make_org)
    tr = ("INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, source_account_id, "
          "target_kind, target_wallet, target_account_id, amount) "
          "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING status, equity_verified")
    with psycopg.connect(db, autocommit=True) as conn:
        (aid,) = conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, platform, "
            "trader_login, is_live, role, enabled) "
            "VALUES (nextval('mt5_account_id_seq'), NULL, %s, 'mt5', 0, false, 'slave', true) "
            "RETURNING ctid_trader_account_id", (org_id,)).fetchone()
        row = conn.execute(tr, (org_id, investor["id"], "wallet", "main", None,
                                "account", None, aid, 50)).fetchone()
        assert row == ("requested", False)
        conn.execute(tr, (org_id, investor["id"], "account", None, aid, "wallet", "main", None, 50))
        conn.execute(tr, (org_id, investor["id"], "wallet", "pamm", None, "wallet", "main", None, 50))
        for bad in [
            ("account", None, aid, "account", None, aid),       # account -> account
            ("wallet", None, None, "wallet", "main", None),     # wallet kind without a wallet
            ("wallet", "main", aid, "wallet", "pamm", None),    # wallet kind carrying an account
            ("account", None, None, "wallet", "main", None),    # account kind without an account
            ("wallet", "savings", None, "wallet", "main", None),
        ]:
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(tr, (org_id, investor["id"], *bad, 50))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(tr, (org_id, investor["id"], "wallet", "main", None,
                              "wallet", "pamm", None, 0))


def test_a_destination_with_history_cannot_be_deleted(db, make_user, make_org):
    org_id, admin, investor = _people(make_user, make_org)
    with psycopg.connect(db, autocommit=True) as conn:
        (dest,) = conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details, status) "
            "VALUES (%s, %s, 'crypto', 'Main', %s, 'approved') RETURNING id",
            (org_id, investor["id"],
             Jsonb({"coin": "USDT", "network": "TRC20", "address": "TDest0987654321"}))).fetchone()
        wd = ("INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
              "destination_summary, amount, fee, net_amount) "
              "VALUES (%s, %s, %s, 'crypto', 'TRC20 T…21', 100, 1.5, 98.5) RETURNING status")
        (status,) = conn.execute(wd, (org_id, investor["id"], dest)).fetchone()
        assert status == "requested"
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute("DELETE FROM payout_destinations WHERE id = %s", (dest,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE withdrawals SET status = 'done'")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE payout_destinations SET status = 'deleted' WHERE id = %s", (dest,))


def test_files_methods_and_settings_constraints(db, make_user, make_org):
    org_id, admin, investor = _people(make_user, make_org)
    f = ("INSERT INTO files (org_id, user_id, purpose, content_type, size_bytes, sha256, storage_key) "
         "VALUES (%s, %s, %s, 'image/png', %s, 'abc', %s)")
    pm = ("INSERT INTO payment_methods (org_id, kind, label, details, fee_pct) "
          "VALUES (%s, %s, 'x', '{}', %s) RETURNING enabled, currency, min_amount, sort_order")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(f, (org_id, investor["id"], "deposit_receipt", 10, f"{org_id}/1.png"))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(f, (org_id, investor["id"], "payout_proof", 10, f"{org_id}/1.png"))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(f, (org_id, investor["id"], "deposit_receipt", 0, f"{org_id}/2.png"))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(f, (org_id, investor["id"], "selfie", 10, f"{org_id}/3.png"))
        assert conn.execute(pm, (org_id, "bank", 0)).fetchone() == (True, "USD", Decimal("0"), 0)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(pm, (org_id, "crypto", 100))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(pm, (org_id, "cash", 0))
        conn.execute("INSERT INTO portal_settings (org_id) VALUES (%s)", (org_id,))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO portal_settings (org_id) VALUES (%s)", (org_id,))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE portal_settings SET withdrawal_fee_pct = 100 WHERE org_id = %s",
                         (org_id,))


def test_upgrade_copies_the_three_old_tables_then_drops_them(database):
    """A developer database has a wallet card, a confirmed and a pending
    notice, and a paid withdrawal when 022 arrives. Each becomes a row of
    the new shape, the settled ones get their ledger entries, the ids are
    kept so the sequences continue, and the old tables are gone."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {UPGRADE_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {UPGRADE_DB}")
    try:
        with psycopg.connect(UPGRADE_DSN) as conn:
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name.startswith("022_"):
                    continue
                conn.execute(path.read_text())
            conn.execute(
                "INSERT INTO users (id, email, password_hash, display_name) VALUES "
                "(1, 'admin@x.com', 'h', 'A'), (2, 'inv@x.com', 'h', 'I')")
            conn.execute("INSERT INTO orgs (id, name) VALUES (1, 'Desk')")
            conn.execute(
                "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
                "platform, trader_login, is_live, role, enabled) "
                "VALUES (900, NULL, 1, 'mt5', 0, false, 'slave', true)")
            conn.execute(
                "INSERT INTO org_investor_wallets (org_id, coin, network, address, memo, updated_by) "
                "VALUES (1, 'USDT', 'TRC20', 'TAddr123', NULL, 1)")
            conn.execute(
                "INSERT INTO investor_deposits (org_id, user_id, account_id, amount, coin, txid, "
                "note, status, decided_by, decided_at, decision_note) VALUES "
                "(1, 2, 900, 250.00, 'USDT', 'tx-1', 'first', 'confirmed', 1, now(), 'seen on chain')")
            conn.execute(
                "INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid) "
                "VALUES (1, 2, 75.00, 'USDT', 'tx-2')")
            conn.execute(
                "INSERT INTO investor_withdrawals (org_id, user_id, account_id, amount, destination, "
                "status, decided_by, decided_at, paid_by, paid_at, txid) VALUES "
                "(1, 2, 900, 40.00, 'TDest999', 'paid', 1, now(), 1, now(), 'out-1')")
            conn.execute((MIGRATIONS_DIR / "022_client_wallets.sql").read_text())
            conn.commit()

        with psycopg.connect(UPGRADE_DSN, autocommit=True) as conn:
            methods = conn.execute(
                "SELECT org_id, kind, label, enabled, currency, details, min_amount, fee_pct, "
                "created_by FROM payment_methods").fetchall()
            assert methods == [(1, "crypto", "USDT on TRC20", True, "USD",
                                {"coin": "USDT", "network": "TRC20", "address": "TAddr123"},
                                Decimal("0"), Decimal("0"), 1)]
            (method_id,) = conn.execute("SELECT id FROM payment_methods").fetchone()
            deposits = conn.execute(
                "SELECT id, user_id, method_id, method_kind, method_label, amount, fee, "
                "credited_amount, reference, target, target_account_id, note, status, decided_by, "
                "decision_note FROM deposits ORDER BY id").fetchall()
            assert deposits == [
                (1, 2, method_id, "crypto", "USDT", Decimal("250.00"), Decimal("0"),
                 Decimal("250.00"), "tx-1", "wallet", None, "first", "confirmed", 1, "seen on chain"),
                (2, 2, method_id, "crypto", "USDT", Decimal("75.00"), Decimal("0"),
                 None, "tx-2", "wallet", None, None, "pending", None, None)]
            dests = conn.execute(
                "SELECT id, org_id, user_id, kind, nickname, details, status "
                "FROM payout_destinations").fetchall()
            assert dests == [(1, 1, 2, "crypto", "Imported",
                              {"coin": "", "network": "", "address": "TDest999"}, "approved")]
            withdrawals = conn.execute(
                "SELECT id, user_id, destination_id, destination_kind, destination_summary, amount, "
                "fee, net_amount, status, decided_by, paid_by, txid FROM withdrawals").fetchall()
            assert withdrawals == [(1, 2, 1, "crypto", "TDest999", Decimal("40.00"), Decimal("0"),
                                    Decimal("40.00"), "paid", 1, 1, "out-1")]
            entries = conn.execute(
                "SELECT wallet, amount, kind, ref_table, ref_id, created_by "
                "FROM wallet_entries ORDER BY id").fetchall()
            assert entries == [("main", Decimal("250.00"), "deposit", "deposits", 1, 1),
                               ("main", Decimal("-40.00"), "withdrawal", "withdrawals", 1, 1)]
            for old in ("investor_withdrawals", "investor_deposits", "org_investor_wallets"):
                (reg,) = conn.execute("SELECT to_regclass(%s)", (old,)).fetchone()
                assert reg is None, old
            (next_dep,) = conn.execute(
                "INSERT INTO deposits (org_id, user_id, method_kind, method_label, amount, reference) "
                "VALUES (1, 2, 'bank', 'ICICI', 5, 'tx-3') RETURNING id").fetchone()
            assert next_dep == 3
            (next_wd,) = conn.execute(
                "INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
                "destination_summary, amount, fee, net_amount) "
                "VALUES (1, 2, 1, 'crypto', 'TDest999', 1, 0, 1) RETURNING id").fetchone()
            assert next_wd == 2
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {UPGRADE_DB} WITH (FORCE)")
```

- [ ] **Step 2: Run it to verify it fails**

Run (from `api/`): `.venv/Scripts/python -m pytest tests/test_migration_022.py -q -p no:cacheprovider`
Expected: FAIL. The first test fails with `assert '022_client_wallets.sql' in names`; the column tests fail with `assert [] == [...]`; the upgrade test errors with `FileNotFoundError` on `022_client_wallets.sql`.

- [ ] **Step 3: Write the migration**

Create `db/migrations/022_client_wallets.sql`:

```sql
-- Client portal, phase 1: wallets backed by a ledger, payment methods the
-- org receives at, payout destinations investors receive at, deposits,
-- withdrawals and transfers as three request tables of one shape, and the
-- files table behind receipts and proofs. Replaces the three 2026-09-23
-- tables (org_investor_wallets, investor_deposits, investor_withdrawals):
-- their rows are copied first so a developer database survives the upgrade.
-- Production has no rows in any of them (the 2026-09-25 reset).
-- See docs/superpowers/specs/2026-09-29-client-portal-phase-1-money-design.md
-- section 5. Money is NUMERIC(18,2); every request table carries the audit
-- trio decided_by / decided_at / decision_note and a (org, status, created)
-- queue index.

-- One row per uploaded file; the bytes live on disk under UPLOAD_DIR at
-- storage_key. Every purpose of every later phase is in the CHECK now so
-- the constraint never has to move again.
CREATE TABLE files (
    id            BIGSERIAL PRIMARY KEY,
    org_id        BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose       TEXT NOT NULL CHECK (purpose IN ('deposit_receipt', 'payout_proof',
                      'kyc_document', 'kyc_photo', 'ticket_attachment', 'avatar')),
    content_type  TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL CHECK (size_bytes > 0),
    sha256        TEXT NOT NULL,
    storage_key   TEXT NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX files_by_user ON files (org_id, user_id, created_at DESC);

-- Where the org receives money: a crypto address on a network or a bank
-- account, configured by an admin. Replaces the single wallet card.
CREATE TABLE payment_methods (
    id            BIGSERIAL PRIMARY KEY,
    org_id        BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('crypto', 'bank')),
    label         TEXT NOT NULL,
    enabled       BOOLEAN NOT NULL DEFAULT true,
    currency      TEXT NOT NULL DEFAULT 'USD',
    details       JSONB NOT NULL,
    min_amount    NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (min_amount >= 0),
    fee_pct       NUMERIC(6,3) NOT NULL DEFAULT 0 CHECK (fee_pct >= 0 AND fee_pct < 100),
    instructions  TEXT NULL,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    created_by    BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX payment_methods_by_org ON payment_methods (org_id, sort_order, id);

-- One row per org, created on first read (portal_common.portal_settings).
CREATE TABLE portal_settings (
    org_id              BIGINT PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
    withdrawal_min      NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (withdrawal_min >= 0),
    withdrawal_fee_pct  NUMERIC(6,3) NOT NULL DEFAULT 0
                        CHECK (withdrawal_fee_pct >= 0 AND withdrawal_fee_pct < 100),
    updated_by          BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Where an investor receives money, saved by the investor and approved by
-- an admin. Removed rows stay so withdrawal history keeps its snapshot.
CREATE TABLE payout_destinations (
    id             BIGSERIAL PRIMARY KEY,
    org_id         BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('bank', 'crypto')),
    nickname       TEXT NOT NULL,
    details        JSONB NOT NULL,
    proof_file_id  BIGINT NULL REFERENCES files(id) ON DELETE SET NULL,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'approved', 'rejected', 'removed')),
    decided_by     BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    decided_at     TIMESTAMPTZ NULL,
    decision_note  TEXT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX payout_destinations_by_user ON payout_destinations (org_id, user_id, status);
CREATE INDEX payout_destinations_queue ON payout_destinations (org_id, status, created_at);

-- The ledger. A balance is the sum of a wallet's entries; nothing stores a
-- balance. Settlement rows name the request they settle, and the partial
-- unique index makes a replayed settlement a no-op (ON CONFLICT DO NOTHING)
-- so a double click can never double-credit.
CREATE TABLE wallet_entries (
    id          BIGSERIAL PRIMARY KEY,
    org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    wallet      TEXT NOT NULL CHECK (wallet IN ('main', 'credit', 'pamm', 'social')),
    amount      NUMERIC(18,2) NOT NULL CHECK (amount <> 0),
    kind        TEXT NOT NULL CHECK (kind IN ('deposit', 'withdrawal', 'transfer',
                    'adjustment', 'bonus', 'commission', 'fee')),
    ref_table   TEXT NULL,
    ref_id      BIGINT NULL,
    note        TEXT NULL,
    created_by  BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX wallet_entries_by_user
    ON wallet_entries (org_id, user_id, wallet, created_at DESC, id DESC);
CREATE INDEX wallet_entries_by_org_time ON wallet_entries (org_id, created_at DESC);
CREATE UNIQUE INDEX wallet_entries_one_per_ref ON wallet_entries (ref_table, ref_id, wallet)
    WHERE ref_table IS NOT NULL;

-- "I have sent it": method kind and label are snapshots taken at filing so
-- the row still reads right after the admin edits or deletes the method.
CREATE TABLE deposits (
    id                 BIGSERIAL PRIMARY KEY,
    org_id             BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id            BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    method_id          BIGINT NULL REFERENCES payment_methods(id) ON DELETE SET NULL,
    method_kind        TEXT NOT NULL CHECK (method_kind IN ('crypto', 'bank')),
    method_label       TEXT NOT NULL,
    amount             NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    fee                NUMERIC(18,2) NOT NULL DEFAULT 0,
    credited_amount    NUMERIC(18,2) NULL,
    reference          TEXT NOT NULL,
    receipt_file_id    BIGINT NULL REFERENCES files(id) ON DELETE SET NULL,
    target             TEXT NOT NULL DEFAULT 'wallet' CHECK (target IN ('wallet', 'account')),
    target_account_id  BIGINT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE SET NULL,
    note               TEXT NULL,
    status             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'confirmed', 'rejected', 'cancelled')),
    decided_by         BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    decided_at         TIMESTAMPTZ NULL,
    decision_note      TEXT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX deposits_queue ON deposits (org_id, status, created_at);
CREATE INDEX deposits_by_user ON deposits (org_id, user_id, created_at DESC);
-- One live notice per transaction reference per org: two pending rows
-- quoting the same transfer are the same money twice. Rejected and
-- cancelled rows leave the index so a mistake can be re-filed.
CREATE UNIQUE INDEX deposits_one_live_reference ON deposits (org_id, reference)
    WHERE status IN ('pending', 'confirmed');

-- A cash-out request, always from the main wallet. The destination is a
-- snapshot too; the row it points at is kept forever (RESTRICT).
CREATE TABLE withdrawals (
    id                   BIGSERIAL PRIMARY KEY,
    org_id               BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id              BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    destination_id       BIGINT NOT NULL REFERENCES payout_destinations(id) ON DELETE RESTRICT,
    destination_kind     TEXT NOT NULL,
    destination_summary  TEXT NOT NULL,
    amount               NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    fee                  NUMERIC(18,2) NOT NULL DEFAULT 0,
    net_amount           NUMERIC(18,2) NOT NULL,
    status               TEXT NOT NULL DEFAULT 'requested'
                         CHECK (status IN ('requested', 'approved', 'paid', 'rejected', 'cancelled')),
    decided_by           BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    decided_at           TIMESTAMPTZ NULL,
    decision_note        TEXT NULL,
    paid_by              BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    paid_at              TIMESTAMPTZ NULL,
    txid                 TEXT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX withdrawals_queue ON withdrawals (org_id, status, created_at);
CREATE INDEX withdrawals_by_user ON withdrawals (org_id, user_id, created_at DESC);

-- Money between wallets and the trading account. Each end is a wallet or
-- an account, never neither and never both; account -> account is not a
-- transfer the portal makes.
CREATE TABLE transfers (
    id                 BIGSERIAL PRIMARY KEY,
    org_id             BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    user_id            BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_kind        TEXT NOT NULL CHECK (source_kind IN ('wallet', 'account')),
    source_wallet      TEXT NULL CHECK (source_wallet IN ('main', 'credit', 'pamm', 'social')),
    source_account_id  BIGINT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE SET NULL,
    target_kind        TEXT NOT NULL CHECK (target_kind IN ('wallet', 'account')),
    target_wallet      TEXT NULL CHECK (target_wallet IN ('main', 'credit', 'pamm', 'social')),
    target_account_id  BIGINT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE SET NULL,
    amount             NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    status             TEXT NOT NULL DEFAULT 'requested'
                       CHECK (status IN ('requested', 'approved', 'done', 'rejected', 'cancelled')),
    equity_at_request  NUMERIC(18,2) NULL,
    equity_verified    BOOLEAN NOT NULL DEFAULT false,
    decided_by         BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    decided_at         TIMESTAMPTZ NULL,
    decision_note      TEXT NULL,
    done_by            BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    done_at            TIMESTAMPTZ NULL,
    note               TEXT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((source_kind = 'wallet') = (source_wallet IS NOT NULL)),
    CHECK ((source_kind = 'account') = (source_account_id IS NOT NULL)),
    CHECK ((target_kind = 'wallet') = (target_wallet IS NOT NULL)),
    CHECK ((target_kind = 'account') = (target_account_id IS NOT NULL)),
    CHECK (NOT (source_kind = 'account' AND target_kind = 'account'))
);
CREATE INDEX transfers_queue ON transfers (org_id, status, created_at);
CREATE INDEX transfers_by_user ON transfers (org_id, user_id, created_at DESC);

-- ---------------------------------------------------------------------
-- Copy the 2026-09-23 rows, then drop their tables. Ids are kept for
-- deposits and withdrawals so the ledger rows can name them and the
-- sequences continue where the old ones left off.

-- The org's wallet card becomes its one crypto payment method.
INSERT INTO payment_methods (org_id, kind, label, currency, details, created_by, created_at, updated_at)
SELECT org_id, 'crypto', coin || ' on ' || network, 'USD',
       jsonb_strip_nulls(jsonb_build_object('coin', coin, 'network', network,
                                            'address', address, 'memo', memo)),
       updated_by, updated_at, updated_at
FROM org_investor_wallets;

INSERT INTO deposits (id, org_id, user_id, method_id, method_kind, method_label, amount, fee,
                      credited_amount, reference, target, note, status,
                      decided_by, decided_at, decision_note, created_at)
SELECT d.id, d.org_id, d.user_id,
       (SELECT pm.id FROM payment_methods pm WHERE pm.org_id = d.org_id ORDER BY pm.id LIMIT 1),
       'crypto', d.coin, d.amount, 0,
       CASE WHEN d.status = 'confirmed' THEN d.amount END,
       d.txid, 'wallet', d.note, d.status,
       d.decided_by, d.decided_at, d.decision_note, d.created_at
FROM investor_deposits d;

INSERT INTO wallet_entries (org_id, user_id, wallet, amount, kind, ref_table, ref_id, created_by, created_at)
SELECT org_id, user_id, 'main', amount, 'deposit', 'deposits', id, decided_by,
       COALESCE(decided_at, created_at)
FROM investor_deposits
WHERE status = 'confirmed';

-- One approved crypto destination per distinct address an investor used.
INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details, status, created_at)
SELECT org_id, user_id, 'crypto', 'Imported',
       jsonb_build_object('coin', '', 'network', '', 'address', destination),
       'approved', MIN(created_at)
FROM investor_withdrawals
GROUP BY org_id, user_id, destination;

INSERT INTO withdrawals (id, org_id, user_id, destination_id, destination_kind, destination_summary,
                         amount, fee, net_amount, status, decided_by, decided_at, decision_note,
                         paid_by, paid_at, txid, created_at)
SELECT w.id, w.org_id, w.user_id, pd.id, 'crypto', w.destination,
       w.amount, 0, w.amount, w.status, w.decided_by, w.decided_at, w.decision_note,
       w.paid_by, w.paid_at, w.txid, w.created_at
FROM investor_withdrawals w
JOIN payout_destinations pd
  ON pd.org_id = w.org_id AND pd.user_id = w.user_id
 AND pd.nickname = 'Imported' AND pd.details->>'address' = w.destination;

INSERT INTO wallet_entries (org_id, user_id, wallet, amount, kind, ref_table, ref_id, created_by, created_at)
SELECT org_id, user_id, 'main', -amount, 'withdrawal', 'withdrawals', id, paid_by,
       COALESCE(paid_at, created_at)
FROM investor_withdrawals
WHERE status = 'paid';

-- Explicit ids do not advance a BIGSERIAL; point the sequences past them.
SELECT setval('deposits_id_seq', COALESCE((SELECT max(id) FROM deposits), 1),
              (SELECT count(*) > 0 FROM deposits));
SELECT setval('withdrawals_id_seq', COALESCE((SELECT max(id) FROM withdrawals), 1),
              (SELECT count(*) > 0 FROM withdrawals));

DROP TABLE investor_withdrawals;
DROP TABLE investor_deposits;
DROP TABLE org_investor_wallets;
```

- [ ] **Step 4: Point the `db` fixture at the new tables**

The `db` fixture TRUNCATEs the three dropped tables, so from this step every test would error until it is updated. In `api/tests/conftest.py` replace:

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

with:

```python
        conn.execute(
            "TRUNCATE transfers, withdrawals, deposits, wallet_entries, payout_destinations, "
            "portal_settings, payment_methods, files, "
            "events, portfolio_snapshots, mappings, symbol_cache, "
            "executions, positions, deals, deal_backfill_state, balance_samples, "
            "accounts, ctid_connections, "
            "oauth_states, org_invites, org_memberships, orgs, users "
            "RESTART IDENTITY CASCADE"
        )
```

- [ ] **Step 5: Run the migration test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_migration_022.py -q -p no:cacheprovider`
Expected: PASS, 17 passed.

- [ ] **Step 6: Trim the two tests that seeded the dropped tables**

Replace the whole of `api/tests/test_migration_019.py` with (the first three tests are unchanged; the three that inserted into `investor_deposits` and `org_investor_wallets` are gone because 022 dropped those tables and test_migration_022 covers their successors):

```python
# api/tests/test_migration_019.py
"""Migration 019: the investor portal -- investor role and the
account->investor link. conftest applies EVERY migration, so these assert
the post-migration shape. The three tables 019 also created
(org_investor_wallets, investor_deposits, investor_withdrawals) were
replaced and dropped by 022_client_wallets.sql; see test_migration_022.py."""
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
    org_id = make_org(members=[(owner, "admin"), (investor, "investor")])
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
    org_id = make_org(members=[(owner, "admin"), (investor, "investor")])
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
```

In `api/tests/test_rbac_matrix.py`, three edits. First, the module docstring's second paragraph. Replace:

```python
The mutating investor rows aim at the seeded deposit 1 and withdrawal 1
(ids are deterministic: the db fixture TRUNCATEs with RESTART IDENTITY, and
every parametrised case gets its own fixture). They are written so that the
FIRST allowed role really performs the change and the later ones get a 409
from the row's state — never a 403/404 — so what the row proves is
authorization, never business rules. `{investor}` in a path is the investor
member's user id, substituted per test.
"""
```

with:

```python
The client-portal rows (investor/*, investors, payment-methods, requests,
deposits, withdrawals, transfers, payout-destinations, files) are added in
Task 10 of the client-portal plan once their routers exist; migration 022
dropped the tables the 2026-09-23 rows seeded. `{investor}` in a path is
the investor member's user id, substituted per test.
"""
```

Second, the fourteen 2026-09-23 rows at the end of `MATRIX`. Replace:

```python
    ("DELETE", "",                               None,                           "admin"),
    ("GET",    "investor/summary",                None,                          "investor"),
    ("GET",    "investor/deposits",               None,                          "investor"),
    ("GET",    "investor/withdrawals",            None,                          "investor"),
    ("GET",    "investor-wallet",                 None,                          "admin"),
    ("GET",    "investors",                       None,                          "admin"),
    ("GET",    "investor-deposits",               None,                          "admin"),
    ("GET",    "investor-withdrawals",            None,                          "admin"),
    ("POST",   "investor/deposits",               {"amount": "10", "coin": "USDT",
                                                   "txid": "matrix-filed"},      "investor"),
    ("POST",   "investor/withdrawals",            {"amount": "10",
                                                   "destination": "TDest"},      "investor"),
    ("PUT",    "investor-wallet",                 {"coin": "USDT", "network": "TRC20",
                                                   "address": "TAddr456"},       "admin"),
    ("PUT",    "investors/{investor}/account",    {"account_id": None},           "admin"),
    ("POST",   "investor-deposits/1/decision",    {"status": "rejected",
                                                   "note": "matrix"},            "admin"),
    ("POST",   "investor-withdrawals/1/decision", {"status": "rejected",
                                                   "note": "matrix"},            "admin"),
    ("POST",   "investor-withdrawals/1/paid",     {"txid": "matrix"},             "admin"),
]
```

with:

```python
    ("DELETE", "",                               None,                           "admin"),
]
```

Third, the fixture's seeding of the dropped tables. Replace:

```python
        conn.execute(
            """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                   org_id, trader_login, is_live, role)
               VALUES (100, %s, %s, 100, false, 'master')""",
            (connection_id, org_id))
        conn.execute(
            "INSERT INTO org_investor_wallets (org_id, coin, network, address) "
            "VALUES (%s, 'USDT', 'TRC20', 'TAddr123')", (org_id,))
        # A pending notice and a requested withdrawal belonging to the
        # investor member, so the decision/paid rows of the matrix have a
        # real row to aim at. RESTART IDENTITY makes both ids 1. The
        # withdrawal hangs off an MT5-style account (no cTrader connection)
        # rather than account 100: investor_withdrawals.account_id is ON
        # DELETE RESTRICT, and account 100 is the one
        # `DELETE accounts/100/connection` cascades away.
        (mt5_id,) = conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
            "platform, trader_login, is_live, role, enabled) "
            "VALUES (nextval('mt5_account_id_seq'), NULL, %s, 'mt5', 0, false, 'slave', true) "
            "RETURNING ctid_trader_account_id", (org_id,)).fetchone()
        conn.execute(
            "INSERT INTO investor_deposits (org_id, user_id, amount, coin, txid) "
            "VALUES (%s, %s, 100, 'USDT', 'matrix-seeded')",
            (org_id, users["investor"]["id"]))
        conn.execute(
            "INSERT INTO investor_withdrawals (org_id, user_id, account_id, amount, destination) "
            "VALUES (%s, %s, %s, 50, 'TDest')",
            (org_id, users["investor"]["id"], mt5_id))
    return app_client, org_id, users, outsider
```

with:

```python
        conn.execute(
            """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                   org_id, trader_login, is_live, role)
               VALUES (100, %s, %s, 100, false, 'master')""",
            (connection_id, org_id))
    return app_client, org_id, users, outsider
```

- [ ] **Step 7: Delete the 2026-09-23 portal test file**

Its routes still exist until Task 10 but every table they read is gone, and `TestClient` re-raises the resulting `UndefinedTable` errors. Its helpers live on in `portal_helpers.py` (next step); its behaviours are re-tested per feature in Tasks 6–10.

```bash
git rm api/tests/test_investor_portal.py
```

- [ ] **Step 8: Write the shared portal test helpers**

Create `api/tests/portal_helpers.py`:

```python
# api/tests/portal_helpers.py
"""Shared helpers for the client-portal API tests (Tasks 1-10). Every
helper writes straight to the scratch database the way an admin or the
migration would, so a test can start from any state without walking the
API. Import with `from portal_helpers import ...` (the tests directory is
on sys.path, as `from conftest import ...` already relies on)."""
import uuid
from decimal import Decimal

import httpx
import psycopg
from psycopg.types.json import Jsonb

from conftest import default_mock_callback

CRYPTO_DETAILS = {"coin": "USDT", "network": "TRC20", "address": "TAddr1234567890"}
BANK_DETAILS = {"bank_name": "ICICI Bank", "holder": "Desk Ltd",
                "account_number": "000123454543", "code": "ICIC0000001"}
DEST_CRYPTO = {"coin": "USDT", "network": "TRC20", "address": "TDest0987654321"}
DEST_BANK = {"bank_name": "HDFC Bank", "holder": "Investor One",
             "account_number": "50100011114543", "code": "HDFC0000123"}


def csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("csrf")}


def member(db, org_id, user_id, role) -> None:
    """Add an existing user to an org (make_org only takes members at creation)."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
            (org_id, user_id, role))


def add_method(db, org_id, *, kind="crypto", label="USDT on TRC20", details=None,
               min_amount="0", fee_pct="0", enabled=True) -> int:
    """A payment method the org receives at; details default per kind."""
    if details is None:
        details = CRYPTO_DETAILS if kind == "crypto" else BANK_DETAILS
    with psycopg.connect(db, autocommit=True) as conn:
        (method_id,) = conn.execute(
            "INSERT INTO payment_methods (org_id, kind, label, details, min_amount, fee_pct, enabled) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (org_id, kind, label, Jsonb(details), Decimal(str(min_amount)),
             Decimal(str(fee_pct)), enabled)).fetchone()
    return int(method_id)


def credit(db, org_id, user_id, amount, *, wallet="main", kind="adjustment") -> int:
    """A direct ledger row (signed amount). Returns the wallet_entries id."""
    with psycopg.connect(db, autocommit=True) as conn:
        (entry_id,) = conn.execute(
            "INSERT INTO wallet_entries (org_id, user_id, wallet, amount, kind) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (org_id, user_id, wallet, Decimal(str(amount)), kind)).fetchone()
    return int(entry_id)


def approved_destination(db, org_id, user_id, *, kind="crypto") -> int:
    """A payout destination an admin has already approved."""
    details = DEST_CRYPTO if kind == "crypto" else DEST_BANK
    with psycopg.connect(db, autocommit=True) as conn:
        (dest_id,) = conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details, status, "
            "decided_at) VALUES (%s, %s, %s, 'Test payout', %s, 'approved', now()) RETURNING id",
            (org_id, user_id, kind, Jsonb(details))).fetchone()
    return int(dest_id)


def seed_file(db, org_id, user_id, *, purpose="deposit_receipt") -> int:
    """A files row with a fake storage key (no bytes on disk)."""
    key = f"{org_id}/seed-{uuid.uuid4().hex}.png"
    with psycopg.connect(db, autocommit=True) as conn:
        (file_id,) = conn.execute(
            "INSERT INTO files (org_id, user_id, purpose, content_type, size_bytes, sha256, "
            "storage_key) VALUES (%s, %s, %s, 'image/png', 1, 'seed', %s) RETURNING id",
            (org_id, user_id, purpose, key)).fetchone()
    return int(file_id)


def link(db, org_id, user_id, account_id) -> None:
    """Make account_id the investor's linked trading account."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "UPDATE accounts SET investor_user_id = %s "
            "WHERE org_id = %s AND ctid_trader_account_id = %s",
            (user_id, org_id, account_id))


def set_state(client, payload: dict) -> None:
    """Fake the copier's /state through the app's mock transport. `payload`
    is either the full /state body (it has an "accounts" key) or just the
    accounts mapping {account_id: {equity, balance, open_pnl, positions}};
    account ids are stringified the way the copier serialises them."""
    if "accounts" in payload:
        body = dict(payload)
        body["accounts"] = {str(k): v for k, v in (payload["accounts"] or {}).items()}
    else:
        body = {"status": "ok",
                "accounts": {str(k): v for k, v in payload.items()},
                "master_positions": [], "pending_orders": [], "drift": []}

    def callback(request):
        url = str(request.url)
        if "copier.test" in url and "/state" in url:
            return httpx.Response(200, json=body)
        return default_mock_callback(request)
    client.app.state.mock_transport.set_callback(callback)


def set_copier_down(client) -> None:
    """Make every copier /state call answer 502 (equity falls back to 'last known')."""
    def callback(request):
        url = str(request.url)
        if "copier.test" in url and "/state" in url:
            return httpx.Response(502, json={"detail": "down"})
        return default_mock_callback(request)
    client.app.state.mock_transport.set_callback(callback)
```

- [ ] **Step 9: Run the touched suites**

Run: `.venv/Scripts/python -m pytest tests/test_migration_019.py tests/test_migration_020.py tests/test_migration_021.py tests/test_migration_022.py tests/test_rbac_matrix.py tests/test_investor_ledger.py -q -p no:cacheprovider`
Expected: PASS, 86 passed (3 + 5 + 2 + 17 + 35 + 24; `test_investor_ledger.py` is 24 collected items because two of its tests are parametrised).

Then the full suite: `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider`
Expected: everything passes except the 7 pre-existing `test_events_ws.py` errors and the one EA-download CRLF failure.

- [ ] **Step 10: Commit**

```bash
git add db/migrations/022_client_wallets.sql api/tests/test_migration_022.py api/tests/portal_helpers.py api/tests/conftest.py api/tests/test_migration_019.py api/tests/test_rbac_matrix.py
git commit -m "feat(db): migration 022 -- client wallets, payment methods, payout destinations, deposits, withdrawals, transfers and files; copy and drop the 2026-09-23 tables

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

(`git rm` in Step 7 already staged the deletion of `api/tests/test_investor_portal.py`.)

---

### Task 2: `portal_ledger.py` — the pure ledger rules

**Files:**
- Create: `api/src/api/portal_ledger.py`
- Create: `api/tests/test_portal_ledger.py`
- Test: `api/tests/test_portal_ledger.py`

`api/src/api/investor_ledger.py` and `api/tests/test_investor_ledger.py` stay until Task 10 deletes them together with `routes/investor.py` (the interfaces doc's task map); `parse_amount` and `clean_text` are copied here verbatim now.

**Interfaces:**
- Consumes: nothing from the codebase (pure module).
- Produces: `WALLETS`, `CENT`, `LedgerError`, `parse_amount`, `clean_text`, `floor_cents`, `round_cents`, `fee_for`, `balance`, `holds`, `available`, `DEPOSIT_TRANSITIONS`, `WITHDRAWAL_TRANSITIONS`, `TRANSFER_TRANSITIONS`, `DESTINATION_TRANSITIONS`, `can_transition`, `INVESTOR_MOVES`, `TRANSFER_PAIRS`, `transfer_pair`, `money` — all in `api.portal_ledger`, exactly as the interfaces doc lists them.

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_portal_ledger.py`:

```python
# api/tests/test_portal_ledger.py
"""Ledger rules with no I/O: what an amount may look like, balances and
holds per wallet, the floored available figure, fees, the four transition
tables and the transfer pair rule."""
from decimal import Decimal

import pytest

from api.portal_ledger import (
    CENT, DEPOSIT_TRANSITIONS, DESTINATION_TRANSITIONS, INVESTOR_MOVES, TRANSFER_PAIRS,
    TRANSFER_TRANSITIONS, WALLETS, WITHDRAWAL_TRANSITIONS, LedgerError, available, balance,
    can_transition, clean_text, fee_for, floor_cents, holds, money, parse_amount, round_cents,
    transfer_pair)

D = Decimal


class TestAmount:
    @pytest.mark.parametrize("raw, expected", [
        (100, D("100")), ("250.5", D("250.5")), (0.01, D("0.01")),
        ("1000000.00", D("1000000.00")),
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
        assert clean_text("  abc123  ", "reference") == "abc123"

    def test_required_text_may_not_be_blank(self):
        with pytest.raises(LedgerError, match="reference"):
            clean_text("   ", "reference")

    def test_optional_text_returns_none_when_blank(self):
        assert clean_text("  ", "note", required=False) is None

    def test_too_long_is_refused(self):
        with pytest.raises(LedgerError, match="128"):
            clean_text("x" * 129, "nickname")


class TestCents:
    def test_floor_cents_never_rounds_up(self):
        assert floor_cents(D("5120.506")) == D("5120.50")
        assert floor_cents(D("5120.509")) == D("5120.50")
        assert floor_cents(D("5120.5")) == D("5120.50")

    def test_floor_and_round_disagree_on_a_half_cent(self):
        assert floor_cents(D("0.005")) == D("0.00")
        assert round_cents(D("0.005")) == D("0.01")
        assert round_cents(D("133.333")) == D("133.33")

    def test_floor_cents_of_a_negative_moves_toward_zero(self):
        assert floor_cents(D("-10.009")) == D("-10.00")

    def test_available_is_the_floored_difference(self):
        # The parked bug of the previous design: 5120.506 - 100 must give
        # 5020.50, the figure "Use max" fills, never 5020.51.
        assert available(D("5120.506"), D("100")) == D("5020.50")
        assert available(D("100"), D("0")) == D("100.00")

    def test_available_may_be_negative_after_an_adjustment(self):
        assert available(D("10"), D("25")) == D("-15.00")

    def test_cent_is_the_quantum(self):
        assert CENT == D("0.01")


class TestFees:
    @pytest.mark.parametrize("amount, pct, fee", [
        ("250", "1.5", "3.75"), ("100", "0.333", "0.33"), ("100", "0", "0.00"),
        ("1", "0.5", "0.01"), ("1000000", "99.999", "999990.00"),
    ])
    def test_fee_is_rounded_half_up_to_cents(self, amount, pct, fee):
        assert fee_for(D(amount), D(pct)) == D(fee)


class TestBalances:
    def test_every_wallet_is_present_even_when_empty(self):
        assert balance([]) == {w: D("0") for w in WALLETS}
        assert WALLETS == ("main", "credit", "pamm", "social")

    def test_entries_sum_per_wallet(self):
        b = balance([("main", D("5000")), ("main", D("-500")), ("main", D("620.50")),
                     ("pamm", D("12.25")), ("credit", D("100")), ("credit", D("-100"))])
        assert b == {"main": D("5120.50"), "credit": D("0"), "pamm": D("12.25"), "social": D("0")}


class TestHolds:
    def test_withdrawals_hold_on_main_and_transfers_on_their_source(self):
        h = holds([D("60"), D("40")], [("main", D("25")), ("pamm", D("5"))])
        assert h == {"main": D("125"), "credit": D("0"), "pamm": D("5"), "social": D("0")}

    def test_nothing_open_holds_nothing(self):
        assert holds([], []) == {w: D("0") for w in WALLETS}


class TestTransitions:
    def test_deposits(self):
        assert DEPOSIT_TRANSITIONS == {"pending": {"confirmed", "rejected", "cancelled"}}
        for new in ("confirmed", "rejected", "cancelled"):
            assert can_transition("deposits", "pending", new)
        assert not can_transition("deposits", "confirmed", "pending")
        assert not can_transition("deposits", "confirmed", "rejected")
        assert not can_transition("deposits", "cancelled", "confirmed")

    def test_withdrawals(self):
        assert WITHDRAWAL_TRANSITIONS == {
            "requested": {"approved", "rejected", "cancelled"}, "approved": {"paid", "rejected"}}
        assert can_transition("withdrawals", "requested", "approved")
        assert can_transition("withdrawals", "requested", "cancelled")
        assert can_transition("withdrawals", "approved", "paid")
        assert can_transition("withdrawals", "approved", "rejected")
        assert not can_transition("withdrawals", "requested", "paid")
        assert not can_transition("withdrawals", "approved", "cancelled")
        assert not can_transition("withdrawals", "paid", "rejected")

    def test_transfers(self):
        assert TRANSFER_TRANSITIONS == {
            "requested": {"approved", "done", "rejected", "cancelled"},
            "approved": {"done", "rejected"}}
        assert can_transition("transfers", "requested", "done")
        assert can_transition("transfers", "approved", "done")
        assert not can_transition("transfers", "approved", "cancelled")
        assert not can_transition("transfers", "done", "rejected")

    def test_destinations(self):
        assert DESTINATION_TRANSITIONS == {
            "pending": {"approved", "rejected", "removed"}, "approved": {"removed"}}
        assert can_transition("payout_destinations", "approved", "removed")
        assert not can_transition("payout_destinations", "rejected", "approved")
        assert not can_transition("payout_destinations", "removed", "approved")

    def test_the_investor_moves_are_cancel_and_remove(self):
        assert INVESTOR_MOVES == {"cancelled", "removed"}


class TestTransferPairs:
    @pytest.mark.parametrize("source, target, pair", [
        ({"kind": "wallet", "wallet": "main"}, {"kind": "account", "account_id": 1001},
         ("main", "account")),
        ({"kind": "account", "account_id": 1001}, {"kind": "wallet", "wallet": "main"},
         ("account", "main")),
        ({"kind": "wallet", "wallet": "pamm"}, {"kind": "wallet", "wallet": "main"},
         ("pamm", "main")),
        ({"kind": "wallet", "wallet": "social"}, {"kind": "wallet", "wallet": "main"},
         ("social", "main")),
    ])
    def test_the_four_allowed_pairs(self, source, target, pair):
        assert transfer_pair(source, target) == pair
        assert pair in TRANSFER_PAIRS

    @pytest.mark.parametrize("source, target", [
        ({"kind": "wallet", "wallet": "main"}, {"kind": "wallet", "wallet": "pamm"}),
        ({"kind": "wallet", "wallet": "credit"}, {"kind": "wallet", "wallet": "main"}),
        ({"kind": "wallet", "wallet": "main"}, {"kind": "wallet", "wallet": "main"}),
        ({"kind": "account", "account_id": 1}, {"kind": "account", "account_id": 2}),
        ({"kind": "wallet", "wallet": "savings"}, {"kind": "wallet", "wallet": "main"}),
        ({"kind": "wallet"}, {"kind": "wallet", "wallet": "main"}),
        ({"kind": "account"}, {"kind": "wallet", "wallet": "main"}),
        ({"kind": "cash", "wallet": "main"}, {"kind": "wallet", "wallet": "main"}),
    ])
    def test_everything_else_is_refused_with_one_message(self, source, target):
        with pytest.raises(LedgerError, match="that transfer is not allowed"):
            transfer_pair(source, target)


class TestMoney:
    def test_money_is_a_two_decimal_float_or_none(self):
        assert money(D("133.333")) == 133.33
        assert money(D("0.005")) == 0.01
        assert money(D("-40")) == -40.0
        assert money(None) is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_portal_ledger.py -q -p no:cacheprovider`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'api.portal_ledger'`.

- [ ] **Step 3: Write the module**

Create `api/src/api/portal_ledger.py`:

```python
"""Ledger rules for the client portal, with no I/O.

Everything an endpoint must agree on lives here so it can be tested
without a database and reused identically: what an amount may look like,
how a wallet's balance, holds and available figure are computed, how a fee
is taken, which status changes are legal, and which transfer pairs exist.
Money is Decimal throughout; the API turns it into cents-rounded floats
only at the edge (`money`).

The one rule that matters most: `available` is FLOORED to cents, never
rounded half-up, and the API reports and checks caps against that same
floored figure, so "Use max" can never be refused for rounding.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Optional

WALLETS: tuple[str, ...] = ("main", "credit", "pamm", "social")
CENT = Decimal("0.01")
MAX_TEXT = 128
MAX_INTEGER_DIGITS = 12


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
    try:
        quantized = value.quantize(CENT)
    except InvalidOperation:
        raise LedgerError(f"{field} is too large")
    if value != quantized:
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


# ------------------------------------------------------------ cents


def floor_cents(value: Decimal) -> Decimal:
    """Toward zero to the cent. What the investor is told is available."""
    return value.quantize(CENT, rounding=ROUND_DOWN)


def round_cents(value: Decimal) -> Decimal:
    """Half-up to the cent. For fees and for the JSON edge."""
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def fee_for(amount: Decimal, fee_pct: Decimal) -> Decimal:
    return round_cents(amount * fee_pct / Decimal(100))


def money(value: Optional[Decimal]) -> Optional[float]:
    """The JSON form: a float rounded to cents, or None."""
    return None if value is None else float(round_cents(value))


# ------------------------------------------------------------ figures


def balance(entries: list[tuple[str, Decimal]]) -> dict[str, Decimal]:
    """`entries` are (wallet, amount) pairs; every wallet in WALLETS is a
    key of the result, Decimal("0") when it has no entries. The database
    CHECK guarantees the wallet names."""
    out = {w: Decimal("0") for w in WALLETS}
    for wallet, amount in entries:
        out[wallet] = out[wallet] + amount
    return out


def holds(open_withdrawals: list[Decimal],
          open_transfers: list[tuple[str, Decimal]]) -> dict[str, Decimal]:
    """Money spoken for by open requests. Withdrawals always hold on
    `main`; `open_transfers` are (source_wallet, amount) pairs of transfers
    in `requested` or `approved` whose source is a wallet."""
    out = {w: Decimal("0") for w in WALLETS}
    for amount in open_withdrawals:
        out["main"] = out["main"] + amount
    for wallet, amount in open_transfers:
        out[wallet] = out[wallet] + amount
    return out


def available(balance: Decimal, hold: Decimal) -> Decimal:
    """Floored, and allowed to be negative: an admin adjustment can take a
    wallet below what is on hold, and the figure must say so."""
    return floor_cents(balance - hold)


# ------------------------------------------------------------ transitions

DEPOSIT_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"confirmed", "rejected", "cancelled"},
}
WITHDRAWAL_TRANSITIONS: dict[str, set[str]] = {
    "requested": {"approved", "rejected", "cancelled"},
    "approved": {"paid", "rejected"},
}
TRANSFER_TRANSITIONS: dict[str, set[str]] = {
    "requested": {"approved", "done", "rejected", "cancelled"},
    "approved": {"done", "rejected"},
}
DESTINATION_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"approved", "rejected", "removed"},
    "approved": {"removed"},
}
_TRANSITIONS: dict[str, dict[str, set[str]]] = {
    "deposits": DEPOSIT_TRANSITIONS,
    "withdrawals": WITHDRAWAL_TRANSITIONS,
    "transfers": TRANSFER_TRANSITIONS,
    "payout_destinations": DESTINATION_TRANSITIONS,
}
# The owner's own moves; every other transition is an admin's.
INVESTOR_MOVES: set[str] = {"cancelled", "removed"}


def can_transition(table: str, current: str, new: str) -> bool:
    """`table` is deposits, withdrawals, transfers or payout_destinations."""
    return new in _TRANSITIONS[table].get(current, set())


# ------------------------------------------------------------ transfers

TRANSFER_PAIRS: frozenset[tuple[str, str]] = frozenset({
    ("main", "account"), ("account", "main"), ("pamm", "main"), ("social", "main"),
})
_NOT_ALLOWED = "that transfer is not allowed"


def _end(ref: dict) -> str:
    """A wallet name, or "account", from {"kind":"wallet","wallet":...} |
    {"kind":"account","account_id":...}; anything else is refused."""
    if not isinstance(ref, dict):
        raise LedgerError(_NOT_ALLOWED)
    kind = ref.get("kind")
    if kind == "wallet" and ref.get("wallet") in WALLETS:
        return str(ref["wallet"])
    if kind == "account" and ref.get("account_id") is not None:
        return "account"
    raise LedgerError(_NOT_ALLOWED)


def transfer_pair(source: dict, target: dict) -> tuple[str, str]:
    """("main", "account") and friends; raises LedgerError for every pair
    outside TRANSFER_PAIRS (credit never moves in phase 1)."""
    pair = (_end(source), _end(target))
    if pair not in TRANSFER_PAIRS:
        raise LedgerError(_NOT_ALLOWED)
    return pair
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_portal_ledger.py tests/test_investor_ledger.py -q -p no:cacheprovider`
Expected: PASS, 76 passed (52 new + the 24 old ones, which keep passing until Task 10 deletes them).

Then the full suite: `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider`
Expected: everything passes except the 7 pre-existing `test_events_ws.py` errors and the one EA-download CRLF failure.

- [ ] **Step 5: Commit**

```bash
git add api/src/api/portal_ledger.py api/tests/test_portal_ledger.py
git commit -m "feat(api): portal_ledger -- balances, holds, floored available, fees, transition tables and transfer pairs, with no I/O

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 3: `mpin_core.py` — the MPIN check the money routes can share

**Files:**
- Create: `api/src/api/mpin_core.py`
- Modify: `api/src/api/routes/mpin.py` (imports the moved functions; every route and body unchanged)
- Create: `api/tests/test_mpin_core.py`
- Test: `api/tests/test_mpin_core.py`, `api/tests/test_mpin.py`

**Interfaces:**
- Consumes: `_DUMMY_HASH`, `verify_password` (`api.auth`); `users.mpin_hash`, `users.mpin_failed_attempts`, `users.mpin_locked_until` (migration 021).
- Produces: `MPIN_RE`, `MPIN_MAX_ATTEMPTS`, `MPIN_LOCK_MINUTES`, `check_mpin(conn, user_id, mpin) -> Optional[Response]`, `require_mpin(conn, user_id, mpin) -> Optional[Response]`, `audit_auth(conn, user_id, action) -> None`, plus the helper `lock_state(conn, user_id) -> tuple[Optional[str], int, Optional[datetime]]` (the old `_lock_state`, needed by `routes/mpin.py`'s set route), all in `api.mpin_core`.

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_mpin_core.py`:

```python
# api/tests/test_mpin_core.py
"""mpin_core: the MPIN check shared by the login step and the money
step-up routes. Bodies and the lock are the login route's, unchanged;
require_mpin adds the shape gate in front of it."""
import json

import psycopg
import pytest

from api.mpin_core import (MPIN_LOCK_MINUTES, MPIN_MAX_ATTEMPTS, MPIN_RE, audit_auth,
                           check_mpin, require_mpin)


def _body(resp):
    return json.loads(resp.body)


def _attempts(db, user_id):
    with psycopg.connect(db, autocommit=True) as conn:
        (attempts,) = conn.execute(
            "SELECT mpin_failed_attempts FROM users WHERE id = %s", (user_id,)).fetchone()
    return attempts


def test_constants_are_the_login_ones():
    assert MPIN_MAX_ATTEMPTS == 5 and MPIN_LOCK_MINUTES == 15
    assert MPIN_RE.fullmatch("123456")
    assert not MPIN_RE.fullmatch("12345") and not MPIN_RE.fullmatch("12345a")


@pytest.mark.parametrize("bad", [None, "", "12345", "1234567", "12345a", 123456, True, ["123456"]])
def test_require_mpin_refuses_anything_but_six_digits_without_spending_a_try(db, make_user, bad):
    user = make_user()
    with psycopg.connect(db, autocommit=True) as conn:
        resp = require_mpin(conn, user["id"], bad)
    assert resp is not None and resp.status_code == 400
    assert _body(resp) == {"detail": "MPIN must be exactly 6 digits"}
    assert _attempts(db, user["id"]) == 0


def test_require_mpin_accepts_the_right_mpin_and_clears_the_counter(db, make_user):
    user = make_user()
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE users SET mpin_failed_attempts = 2 WHERE id = %s", (user["id"],))
        assert require_mpin(conn, user["id"], "123456") is None
    assert _attempts(db, user["id"]) == 0


def test_require_mpin_counts_wrong_tries_then_locks(db, make_user):
    user = make_user()
    with psycopg.connect(db, autocommit=True) as conn:
        for left in (4, 3, 2, 1):
            resp = require_mpin(conn, user["id"], "000000")
            assert resp.status_code == 401
            assert _body(resp) == {"detail": "Invalid MPIN", "attempts_left": left}
        resp = require_mpin(conn, user["id"], "000000")
        assert resp.status_code == 423
        body = _body(resp)
        assert body["detail"] == "MPIN locked" and body["locked_until"]
        # Even the right MPIN is refused while locked.
        resp = require_mpin(conn, user["id"], "123456")
        assert resp.status_code == 423
        (mins,) = conn.execute(
            "SELECT EXTRACT(EPOCH FROM (mpin_locked_until - now())) / 60 FROM users WHERE id = %s",
            (user["id"],)).fetchone()
    assert 14 < float(mins) <= 15


def test_require_mpin_without_an_mpin_is_409(db, make_user):
    user = make_user(mpin=None)
    with psycopg.connect(db, autocommit=True) as conn:
        resp = require_mpin(conn, user["id"], "123456")
    assert resp.status_code == 409 and _body(resp) == {"detail": "MPIN not set"}


def test_the_login_route_uses_the_same_check():
    from api.routes import mpin as routes
    assert routes.check_mpin is check_mpin


def test_audit_auth_writes_an_org_less_auth_event(db, make_user):
    user = make_user()
    with psycopg.connect(db, autocommit=True) as conn:
        audit_auth(conn, user["id"], "mpin_locked")
        rows = conn.execute(
            "SELECT org_id, account_id, category, severity, payload FROM events").fetchall()
    assert rows == [(None, None, "auth", "info", {"action": "mpin_locked", "user_id": user["id"]})]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_mpin_core.py -q -p no:cacheprovider`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'api.mpin_core'`.

- [ ] **Step 3: Write `mpin_core.py`**

Create `api/src/api/mpin_core.py` (the bodies of `_audit`, `_locked_response`, `_lock_state`, `_lock` and `_check_mpin` are moved from `routes/mpin.py` unchanged; only the names lose their underscore where another module needs them):

```python
"""The MPIN check, shared by the login step (routes/mpin.py) and every
money action that re-confirms with the MPIN (the client portal's step-up
routes: withdrawal request, transfer request, payout destination add,
admin adjustment).

One lock for both: five wrong guesses anywhere lock the MPIN everywhere
for fifteen minutes. The lock lives on the users row so it survives
restarts and is shared across workers; every path does exactly one argon2
verification whatever the outcome, so timing does not leak state.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg
from fastapi import HTTPException
from fastapi.responses import JSONResponse, Response
from psycopg.types.json import Jsonb

from .auth import _DUMMY_HASH, verify_password

logger = logging.getLogger(__name__)

MPIN_RE = re.compile(r"^[0-9]{6}$")
MPIN_MAX_ATTEMPTS = 5
MPIN_LOCK_MINUTES = 15


def audit_auth(conn: psycopg.Connection, user_id: int, action: str) -> None:
    """Account-level security events carry no org; they reach the operator
    log, not an org's feed. Best-effort like the other audit writers."""
    if action in ("mpin_locked", "mpin_reset"):
        logger.warning("mpin %s user_id=%s", action, user_id)
    else:
        logger.info("mpin %s user_id=%s", action, user_id)
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


def lock_state(conn: psycopg.Connection, user_id: int) -> tuple[Optional[str], int, Optional[datetime]]:
    """(mpin_hash, failed_attempts, locked_until) for a user; 401 when the
    session names a user that no longer exists."""
    row = conn.execute(
        "SELECT mpin_hash, mpin_failed_attempts, mpin_locked_until FROM users WHERE id = %s",
        (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return row[0], row[1], row[2]


def _lock(conn: psycopg.Connection, user_id: int, now: datetime) -> datetime:
    until = now + timedelta(minutes=MPIN_LOCK_MINUTES)
    conn.execute("UPDATE users SET mpin_failed_attempts = 0, mpin_locked_until = %s "
                 "WHERE id = %s", (until, user_id))
    audit_auth(conn, user_id, "mpin_locked")
    return until


def check_mpin(conn: psycopg.Connection, user_id: int, mpin: str) -> Optional[Response]:
    """Reserve a try, then verify. One argon2 verify on every path. The
    reservation is a single UPDATE guarded by the lock and the cap, so a
    burst of concurrent guesses gets at most MPIN_MAX_ATTEMPTS verifies per
    window. Returns None when the MPIN is right (counter cleared), otherwise
    the error response: 409 no MPIN, 423 locked, 401 with attempts_left."""
    now = datetime.now(timezone.utc)
    reserved = conn.execute(
        "UPDATE users SET mpin_failed_attempts = mpin_failed_attempts + 1 "
        "WHERE id = %s AND mpin_hash IS NOT NULL "
        "  AND (mpin_locked_until IS NULL OR mpin_locked_until <= now()) "
        "  AND mpin_failed_attempts < %s "
        "RETURNING mpin_failed_attempts, mpin_hash",
        (user_id, MPIN_MAX_ATTEMPTS)).fetchone()
    if reserved is None:
        mpin_hash, _, locked_until = lock_state(conn, user_id)
        verify_password(_DUMMY_HASH, mpin)
        if mpin_hash is None:
            return JSONResponse(status_code=409, content={"detail": "MPIN not set"})
        # Judge the lock by the database clock, the one the reservation used;
        # a skewed api clock must not extend a lock the database still holds.
        (db_now,) = conn.execute("SELECT now()").fetchone()
        if locked_until is None or locked_until <= db_now:
            # The cap was reached before a lock was written (a burst of
            # concurrent tries); start the window now.
            locked_until = _lock(conn, user_id, db_now)
        return _locked_response(locked_until)
    attempts, mpin_hash = reserved
    if verify_password(mpin_hash, mpin):
        conn.execute("UPDATE users SET mpin_failed_attempts = 0, mpin_locked_until = NULL "
                     "WHERE id = %s", (user_id,))
        return None
    if attempts >= MPIN_MAX_ATTEMPTS:
        return _locked_response(_lock(conn, user_id, now))
    return JSONResponse(status_code=401,
                        content={"detail": "Invalid MPIN",
                                 "attempts_left": MPIN_MAX_ATTEMPTS - attempts})


def require_mpin(conn: psycopg.Connection, user_id: int, mpin: object) -> Optional[Response]:
    """The step-up gate for a money route: the body's `mpin` field must be
    a six-digit string (400 otherwise, and no try is spent), then it goes
    through check_mpin. Routes call this first and return the Response
    when there is one."""
    if not isinstance(mpin, str) or not MPIN_RE.fullmatch(mpin):
        return JSONResponse(status_code=400, content={"detail": "MPIN must be exactly 6 digits"})
    return check_mpin(conn, user_id, mpin)
```

- [ ] **Step 4: Make `routes/mpin.py` import the moved functions**

Replace the whole of `api/src/api/routes/mpin.py` with (the four routes, their bodies, `_validate_pair` and `_store` are byte-for-byte what they were; `_audit`, `_locked_response`, `_lock_state`, `_lock` and `_check_mpin` are gone, imported from `mpin_core` instead):

```python
"""MPIN: the six-digit second factor every user passes after email+password.

Set once (first login), verified on every later login, reset by proving the
password, changed from Account security. The check itself (reservation,
5-try / 15-minute lock, constant-time verify) lives in mpin_core so the
client portal's money routes can re-confirm with the same MPIN and share
the same lock.
"""
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ..auth import (LoginRateLimiter, SessionInfo, _is_proxy_address, _issue_session,
                    get_client_ip, hash_password, require_half_session, require_user,
                    verify_password)
from ..config import ApiConfig
from ..db import get_conn
from ..mpin_core import MPIN_RE, audit_auth, check_mpin, lock_state


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
        mpin_hash, _, _ = lock_state(conn, info.user_id)
        if mpin_hash is not None:
            raise HTTPException(status_code=409, detail="MPIN already set")
        _store(conn, info.user_id, body.mpin)
        audit_auth(conn, info.user_id, "mpin_set")
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
        failure = check_mpin(conn, info.user_id, body.mpin)
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
        # A reset signs out every other session (whoever forgot, or stole,
        # the MPIN); this browser is handed a freshly versioned cookie.
        (new_sv,) = conn.execute(
            "UPDATE users SET session_version = session_version + 1 WHERE id = %s "
            "RETURNING session_version", (info.user_id,)).fetchone()
        response = Response(status_code=204)
        _issue_session(response, cfg, info.user_id, new_sv, pin=True)
        from ..ws import broadcaster
        await broadcaster.close_for_user(info.user_id)
        audit_auth(conn, info.user_id, "mpin_reset")
        return response

    @router.post("/api/me/mpin", status_code=204)
    async def change_mpin(body: ChangeRequest,
                          user_id: int = Depends(require_user),
                          cfg: ApiConfig = Depends(ApiConfig.from_env),
                          conn: psycopg.Connection = Depends(get_conn)):
        _validate_pair(body.mpin, body.mpin_confirm)
        if not MPIN_RE.fullmatch(body.current_mpin):
            raise HTTPException(status_code=400, detail="MPIN must be exactly 6 digits")
        failure = check_mpin(conn, user_id, body.current_mpin)
        if failure is not None:
            return failure
        _store(conn, user_id, body.mpin)
        # Like a password change: every other session is signed out, this
        # one keeps working on a freshly versioned cookie.
        (new_sv,) = conn.execute(
            "UPDATE users SET session_version = session_version + 1 WHERE id = %s "
            "RETURNING session_version", (user_id,)).fetchone()
        response = Response(status_code=204)
        _issue_session(response, cfg, user_id, new_sv, pin=True)
        from ..ws import broadcaster
        await broadcaster.close_for_user(user_id)
        audit_auth(conn, user_id, "mpin_changed")
        return response

    return router
```

- [ ] **Step 5: Run both MPIN suites to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_mpin_core.py tests/test_mpin.py -q -p no:cacheprovider`
Expected: PASS, 30 passed (14 new, 16 existing — the login routes behave exactly as before).

Then the full suite: `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider`
Expected: everything passes except the 7 pre-existing `test_events_ws.py` errors and the one EA-download CRLF failure.

- [ ] **Step 6: Commit**

```bash
git add api/src/api/mpin_core.py api/src/api/routes/mpin.py api/tests/test_mpin_core.py
git commit -m "feat(api): mpin_core -- check_mpin and require_mpin shared by login and the money step-up routes

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 4: Uploads — the file store, the files routes and the ops plumbing

**Files:**
- Modify: `api/pyproject.toml` (add `python-multipart`)
- Modify: `api/src/api/config.py` (`ApiConfig.upload_dir`)
- Create: `api/src/api/uploads.py`
- Create: `api/src/api/routes/portal_files.py`
- Modify: `api/src/api/main.py` (`app.state.uploads`, include the files router)
- Modify: `api/Dockerfile` (`ENV UPLOAD_DIR`, the directory owned by `appuser`)
- Modify: `docker-compose.yml` (named volume `uploads` on `api`)
- Modify: `ops/backup.sh` (tar the uploads next to the dump)
- Modify: `.env.example` (document `UPLOAD_DIR`)
- Modify: `.gitignore` (`data/`, the local default upload directory)
- Create: `api/tests/test_uploads.py`
- Test: `api/tests/test_uploads.py`

**Interfaces:**
- Consumes: `require_org_role`, `OrgContext` (`api.rbac`); `get_conn` (`api.db`); `LoginRateLimiter` (`api.auth`); the `files` table (Task 1); `portal_helpers.csrf`, `portal_helpers.member` (Task 1).
- Produces: `ApiConfig.upload_dir: str`; `api.uploads`: `MAX_UPLOAD_BYTES`, `ALLOWED`, `PHASE1_PURPOSES`, `ALL_PURPOSES`, `UPLOADS_PER_HOUR`, `detect_type`, `UploadStore` (`key`, `write`, `path`, `read`); `api.routes.portal_files`: `create_portal_files_router`, `file_belongs`; `app.state.uploads: UploadStore`; routes `POST /api/orgs/{org_id}/investor/files`, `GET /api/orgs/{org_id}/investor/files/{file_id}`, `GET /api/orgs/{org_id}/files/{file_id}`.

No `events` row is written by these routes: spec section 13 defines no file action, and the deposit or destination that references the file is audited when it is filed (Tasks 7 and 8).

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_uploads.py`:

```python
# api/tests/test_uploads.py
"""Receipts and proofs: multipart upload with type sniffing and a size cap,
owner-only and admin reads with safe headers, the on-disk layout and the
hourly limit. Every test points the store at its own temp directory."""
import hashlib

import psycopg
import pytest
from psycopg.types.json import Jsonb

from portal_helpers import DEST_CRYPTO, csrf, member

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"\x00" * 64
# org_client's admin, as a login_as() dict (it has no id; none is needed).
ADMIN = {"email": "admin@example.com", "password": "a-solid-password", "mpin": "123456"}


@pytest.fixture
def portal(org_client, make_user, login_as, db, tmp_path):
    """org_client's org with an investor member logged in, and the app's
    upload store swapped for a per-test directory."""
    from api.uploads import UploadStore
    client, org_id, seed = org_client
    root = tmp_path / "uploads"
    client.app.state.uploads = UploadStore(root)
    investor = make_user(email="inv@example.com", display_name="Inv One")
    member(db, org_id, investor["id"], "investor")
    login_as(client, investor)
    return client, org_id, investor, root


def upload(client, org_id, data=PNG, *, purpose="deposit_receipt", name="receipt.png",
           claimed="image/png"):
    return client.post(f"/api/orgs/{org_id}/investor/files",
                       data={"purpose": purpose},
                       files={"file": (name, data, claimed)},
                       headers=csrf(client))


# ---------- upload ----------

def test_a_png_receipt_is_stored_under_the_org(portal, db):
    client, org_id, investor, root = portal
    r = upload(client, org_id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["purpose"] == "deposit_receipt" and body["content_type"] == "image/png"
    assert body["size_bytes"] == len(PNG) and body["created_at"]
    file_id = body["id"]
    with psycopg.connect(db, autocommit=True) as conn:
        key, sha, size, owner = conn.execute(
            "SELECT storage_key, sha256, size_bytes, user_id FROM files WHERE id = %s",
            (file_id,)).fetchone()
    assert key == f"{org_id}/{file_id}.png" and owner == investor["id"] and size == len(PNG)
    assert sha == hashlib.sha256(PNG).hexdigest()
    assert (root / str(org_id) / f"{file_id}.png").read_bytes() == PNG
    assert not list(root.rglob("*.part"))


@pytest.mark.parametrize("data, claimed, expected", [
    (JPEG, "text/plain", "image/jpeg"),
    (WEBP, "application/octet-stream", "image/webp"),
    (PDF, "image/png", "application/pdf"),
])
def test_the_type_comes_from_the_bytes_not_the_header(portal, data, claimed, expected):
    client, org_id, _, _ = portal
    r = upload(client, org_id, data, claimed=claimed, name="whatever.bin")
    assert r.status_code == 201 and r.json()["content_type"] == expected


def test_bytes_that_are_not_an_image_or_a_pdf_are_refused(portal, db):
    client, org_id, _, root = portal
    r = upload(client, org_id, b"<html>not a receipt</html>" * 4, claimed="image/png")
    assert r.status_code == 400 and r.json()["detail"] == "unsupported file type"
    assert not list(root.rglob("*"))
    with psycopg.connect(db, autocommit=True) as conn:
        (n,) = conn.execute("SELECT count(*) FROM files").fetchone()
    assert n == 0


def test_the_size_cap_is_five_megabytes(portal):
    from api.uploads import MAX_UPLOAD_BYTES
    client, org_id, _, _ = portal
    exact = PNG + b"\x00" * (MAX_UPLOAD_BYTES - len(PNG))
    assert upload(client, org_id, exact).status_code == 201
    r = upload(client, org_id, exact + b"\x00")
    assert r.status_code == 400 and r.json()["detail"] == "file too large (5 MB max)"


def test_an_empty_file_is_refused(portal):
    client, org_id, _, _ = portal
    r = upload(client, org_id, b"")
    assert r.status_code == 400 and r.json()["detail"] == "file is empty"


@pytest.mark.parametrize("purpose", ["kyc_document", "kyc_photo", "ticket_attachment", "avatar",
                                     "selfie", ""])
def test_only_the_phase_1_purposes_are_accepted(portal, purpose):
    client, org_id, _, _ = portal
    r = upload(client, org_id, purpose=purpose)
    assert r.status_code == 400 and r.json()["detail"] == "purpose is not accepted yet"


def test_thirty_uploads_an_hour_per_investor(portal):
    client, org_id, _, _ = portal
    for _ in range(30):
        assert upload(client, org_id).status_code == 201
    r = upload(client, org_id)
    assert r.status_code == 429 and r.json()["detail"] == "too many uploads; try again later"


# ---------- read ----------

def test_the_owner_reads_it_back_with_safe_headers(portal):
    client, org_id, _, _ = portal
    file_id = upload(client, org_id).json()["id"]
    r = client.get(f"/api/orgs/{org_id}/investor/files/{file_id}")
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["content-disposition"] == f'inline; filename="file-{file_id}.png"'
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "private, max-age=0"


def test_a_pdf_is_served_as_an_attachment(portal):
    client, org_id, _, _ = portal
    file_id = upload(client, org_id, PDF, name="r.pdf", claimed="application/pdf").json()["id"]
    r = client.get(f"/api/orgs/{org_id}/investor/files/{file_id}")
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == f'attachment; filename="file-{file_id}.pdf"'


def test_another_investor_cannot_read_it(portal, make_user, login_as, db):
    client, org_id, _, _ = portal
    file_id = upload(client, org_id).json()["id"]
    other = make_user(email="other@example.com")
    member(db, org_id, other["id"], "investor")
    login_as(client, other)
    r = client.get(f"/api/orgs/{org_id}/investor/files/{file_id}")
    assert r.status_code == 404 and r.json()["detail"] == "File not found"
    assert client.get(f"/api/orgs/{org_id}/investor/files/999").status_code == 404


def test_an_admin_reads_any_file_in_the_org(portal, make_user, login_as, db):
    client, org_id, _, _ = portal
    file_id = upload(client, org_id).json()["id"]
    # The investor may not use the admin route.
    assert client.get(f"/api/orgs/{org_id}/files/{file_id}").status_code == 403
    login_as(client, ADMIN)
    r = client.get(f"/api/orgs/{org_id}/files/{file_id}")
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-disposition"] == f'inline; filename="file-{file_id}.png"'
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/api/orgs/{org_id}/files/999").status_code == 404
    # A viewer is below admin.
    viewer = make_user(email="viewer@example.com")
    member(db, org_id, viewer["id"], "viewer")
    login_as(client, viewer)
    assert client.get(f"/api/orgs/{org_id}/files/{file_id}").status_code == 403


def test_a_file_from_another_org_is_not_found(portal, make_user, make_org, login_as):
    client, org_id, investor, _ = portal
    file_id = upload(client, org_id).json()["id"]
    boss = make_user(email="boss@example.com")
    other_org = make_org(name="Other", members=[(boss, "admin"), (investor, "investor")])
    assert client.get(f"/api/orgs/{other_org}/investor/files/{file_id}").status_code == 404
    login_as(client, boss)
    assert client.get(f"/api/orgs/{other_org}/files/{file_id}").status_code == 404


def test_a_row_whose_bytes_are_gone_is_not_found(portal):
    client, org_id, _, root = portal
    file_id = upload(client, org_id).json()["id"]
    (root / str(org_id) / f"{file_id}.png").unlink()
    r = client.get(f"/api/orgs/{org_id}/investor/files/{file_id}")
    assert r.status_code == 404 and r.json()["detail"] == "File not found"


def test_file_belongs_checks_org_owner_and_purpose(portal, make_user, db):
    from api.routes.portal_files import file_belongs
    client, org_id, investor, _ = portal
    file_id = upload(client, org_id).json()["id"]
    proof_id = upload(client, org_id, purpose="payout_proof").json()["id"]
    other = make_user(email="other@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        assert file_belongs(conn, org_id, investor["id"], file_id, "deposit_receipt")
        assert file_belongs(conn, org_id, investor["id"], proof_id, "payout_proof")
        assert file_belongs(conn, org_id, investor["id"], None, "deposit_receipt")
        assert not file_belongs(conn, org_id, investor["id"], file_id, "payout_proof")
        assert not file_belongs(conn, org_id, other["id"], file_id, "deposit_receipt")
        assert not file_belongs(conn, org_id + 1, investor["id"], file_id, "deposit_receipt")
        assert not file_belongs(conn, org_id, investor["id"], 999, "deposit_receipt")
        # Spec section 9: a file is referenced by at most one request row.
        # Once a deposit carries the receipt, or a payout destination the
        # proof, the same file cannot be attached to a second request.
        conn.execute(
            "INSERT INTO deposits (org_id, user_id, method_kind, method_label, amount, reference, "
            "receipt_file_id) VALUES (%s, %s, 'crypto', 'x', 1, 'r1', %s)",
            (org_id, investor["id"], file_id))
        assert not file_belongs(conn, org_id, investor["id"], file_id, "deposit_receipt")
        conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details, "
            "proof_file_id) VALUES (%s, %s, 'crypto', 'Main', %s, %s)",
            (org_id, investor["id"], Jsonb(DEST_CRYPTO), proof_id))
        assert not file_belongs(conn, org_id, investor["id"], proof_id, "payout_proof")


# ---------- config ----------

def test_upload_dir_defaults_when_unset_or_empty(portal, monkeypatch):
    """`portal` (via app_client) sets the rest of the env from_env needs.
    An `UPLOAD_DIR=` line in .env must mean "default", never the cwd."""
    from api.config import ApiConfig
    monkeypatch.delenv("UPLOAD_DIR", raising=False)
    assert ApiConfig.from_env().upload_dir == "./data/uploads"
    monkeypatch.setenv("UPLOAD_DIR", "")
    assert ApiConfig.from_env().upload_dir == "./data/uploads"
    monkeypatch.setenv("UPLOAD_DIR", "/data/uploads")
    assert ApiConfig.from_env().upload_dir == "/data/uploads"


# ---------- the sniffer and the store, without the app ----------

@pytest.mark.parametrize("head, expected", [
    (PNG[:16], ("image/png", "png")), (JPEG[:16], ("image/jpeg", "jpg")),
    (WEBP[:16], ("image/webp", "webp")), (PDF[:16], ("application/pdf", "pdf")),
    (b"GIF89a" + b"\x00" * 10, None), (b"RIFF\x00\x00\x00\x00WAVEfmt ", None),
    (b"", None), (b"\x89PN", None),
])
def test_detect_type_reads_magic_bytes(head, expected):
    from api.uploads import detect_type
    assert detect_type(head) == expected


def test_the_store_lays_files_out_by_org_and_refuses_escaping_keys(tmp_path):
    from api.uploads import UploadStore
    store = UploadStore(tmp_path / "up")
    assert (tmp_path / "up").is_dir()
    assert store.key(7, 12, "png") == "7/12.png"
    store.write("7/12.png", b"abc")
    assert store.read("7/12.png") == b"abc"
    assert store.path("7/12.png") == tmp_path / "up" / "7" / "12.png"
    assert not list((tmp_path / "up").rglob("*.part"))
    for bad in ["../x.png", "7/../../x.png", "/etc/passwd", "C:/Windows/x", "", "7\\..\\x.png"]:
        with pytest.raises(ValueError):
            store.path(bad)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_uploads.py -q -p no:cacheprovider`
Expected: FAIL — every `portal` test errors with `ModuleNotFoundError: No module named 'api.uploads'`, and the two direct ones fail the same way inside the test.

- [ ] **Step 3: Add `python-multipart` (FastAPI needs it for `Form`/`File`)**

In `api/pyproject.toml` replace:

```toml
    "httpx>=0.27",
    "cryptography>=42",
]
```

with:

```toml
    "httpx>=0.27",
    "cryptography>=42",
    # Multipart form parsing for the client portal's receipt and proof
    # uploads (routes/portal_files.py). FastAPI imports it lazily and
    # refuses to define a Form/File route without it.
    "python-multipart>=0.0.18",
]
```

Then install it (from `api/`): `.venv/Scripts/python -m pip install -e ".[dev]"`
Expected: the output ends with `Successfully installed ... python-multipart-0.0.x ...` (the api package reinstalls in editable mode too).

- [ ] **Step 4: `ApiConfig.upload_dir`**

In `api/src/api/config.py` replace:

```python
    # Self-service signup. Off by default: this platform moves real money
    # and every account is created by an operator or an invite.
    registration_enabled: bool

    @classmethod
```

with:

```python
    # Self-service signup. Off by default: this platform moves real money
    # and every account is created by an operator or an invite.
    registration_enabled: bool
    # Where uploaded receipts and proofs are written (routes/portal_files.py).
    # The api image sets /data/uploads, a named compose volume; a bare
    # `uvicorn` run gets ./data/uploads. An empty value means "default" so
    # an `UPLOAD_DIR=` line in .env cannot point the store at the cwd.
    upload_dir: str

    @classmethod
```

and replace:

```python
            registration_enabled=os.environ.get(
                "REGISTRATION_ENABLED", "false").lower() in ("true", "1", "yes"),
        )
```

with:

```python
            registration_enabled=os.environ.get(
                "REGISTRATION_ENABLED", "false").lower() in ("true", "1", "yes"),
            upload_dir=os.environ.get("UPLOAD_DIR") or "./data/uploads",
        )
```

- [ ] **Step 5: Write `uploads.py`**

Create `api/src/api/uploads.py`:

```python
"""The upload store: bytes on disk under UPLOAD_DIR, one directory per org,
one file per `files` row, and the type sniffer that decides what a file is
from its first bytes (never from the client's header or extension).

Phase 1 stores deposit receipts and payout proofs; the other purposes are
in the database CHECK already and are accepted here in their phase.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
# content_type -> extension used in the storage key and the download name.
ALLOWED = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}
PHASE1_PURPOSES = {"deposit_receipt", "payout_proof"}
ALL_PURPOSES = PHASE1_PURPOSES | {"kyc_document", "kyc_photo", "ticket_attachment", "avatar"}
UPLOADS_PER_HOUR = 30


def detect_type(head: bytes) -> tuple[str, str] | None:
    """(content_type, ext) from the magic bytes of the first 16 bytes of a
    file, or None when it is none of the four accepted types."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ALLOWED["image/jpeg"]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ALLOWED["image/png"]
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", ALLOWED["image/webp"]
    if head.startswith(b"%PDF-"):
        return "application/pdf", ALLOWED["application/pdf"]
    return None


class UploadStore:
    """Files under `root`, addressed by a relative key `<org_id>/<file_id>.<ext>`."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def key(self, org_id: int, file_id: int, ext: str) -> str:
        return f"{org_id}/{file_id}.{ext}"

    def path(self, key: str) -> Path:
        """The on-disk path for a key. Keys come from our own rows, but a
        row is still data: anything that could leave `root` is refused."""
        if not key or key.startswith(("/", "\\")) or "\\" in key or ":" in key:
            raise ValueError(f"refusing storage key {key!r}")
        parts = PurePosixPath(key).parts
        if PurePosixPath(key).is_absolute() or ".." in parts or any(p in ("", ".") for p in parts):
            raise ValueError(f"refusing storage key {key!r}")
        target = self.root.joinpath(*parts)
        try:
            target.resolve().relative_to(self.root.resolve())
        except ValueError:
            raise ValueError(f"refusing storage key {key!r}")
        return target

    def write(self, key: str, data: bytes) -> None:
        """Atomic: the bytes land in a .part file next to the target and are
        renamed into place, so a reader never sees a half-written file and
        a crash leaves at most a .part to sweep."""
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        with open(partial, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(partial, target)

    def read(self, key: str) -> bytes:
        return self.path(key).read_bytes()
```

- [ ] **Step 6: Write `routes/portal_files.py`**

Create `api/src/api/routes/portal_files.py`:

```python
"""Files for the client portal: the receipts and proofs an investor uploads
with a deposit notice or a payout destination, served back to their owner
and to the org's admins. Bytes live in app.state.uploads (uploads.py); the
`files` row holds the type, size, hash and storage key.

The type is decided by sniffing the bytes, never by the client's header
or file name; a PDF is served as an attachment so a browser never renders
it inline from our origin.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Optional

import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from ..auth import LoginRateLimiter
from ..db import get_conn
from ..rbac import OrgContext, require_org_role
from ..uploads import (ALLOWED, MAX_UPLOAD_BYTES, PHASE1_PURPOSES, UPLOADS_PER_HOUR,
                       UploadStore, detect_type)

logger = logging.getLogger(__name__)

FILE_COLS = "id, org_id, user_id, purpose, content_type, size_bytes, storage_key, created_at"


def file_belongs(conn: psycopg.Connection, org_id: int, user_id: int,
                 file_id: Optional[int], purpose: str) -> bool:
    """Whether file_id is this investor's file of this purpose in this org,
    and not yet attached to any request row (spec section 9: a file is
    referenced by at most one request row). None (no file attached) is
    fine. Request routes call this before storing a file id on a deposit
    or a payout destination, so this is the one gate that rule needs."""
    if file_id is None:
        return True
    row = conn.execute(
        "SELECT 1 FROM files WHERE id = %s AND org_id = %s AND user_id = %s AND purpose = %s "
        "  AND NOT EXISTS (SELECT 1 FROM deposits d WHERE d.receipt_file_id = files.id) "
        "  AND NOT EXISTS (SELECT 1 FROM payout_destinations p WHERE p.proof_file_id = files.id)",
        (file_id, org_id, user_id, purpose)).fetchone()
    return row is not None


def _serve(request: Request, conn: psycopg.Connection, org_id: int, file_id: int,
           user_id: Optional[int] = None) -> Response:
    """The bytes of one file with the headers spec section 9 asks for.
    `user_id` narrows the lookup to the owner (investor route); None means
    any file in the org (admin route). Missing rows and missing bytes are
    both 404 -- the caller learns nothing about which."""
    sql = f"SELECT {FILE_COLS} FROM files WHERE id = %s AND org_id = %s"
    params: list = [file_id, org_id]
    if user_id is not None:
        sql += " AND user_id = %s"
        params.append(user_id)
    row = conn.execute(sql, params).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="File not found")
    content_type, storage_key = row[4], row[6]
    store: UploadStore = request.app.state.uploads
    try:
        data = store.read(storage_key)
    except (FileNotFoundError, ValueError):
        logger.error("file %s has a row but no bytes at %s", file_id, storage_key)
        raise HTTPException(status_code=404, detail="File not found")
    ext = ALLOWED.get(content_type, "bin")
    disposition = "attachment" if content_type == "application/pdf" else "inline"
    return Response(content=data, media_type=content_type, headers={
        "Content-Disposition": f'{disposition}; filename="file-{row[0]}.{ext}"',
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=0",
    })


def create_portal_files_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-files"])
    limiter = LoginRateLimiter(max_attempts=UPLOADS_PER_HOUR, window_s=3600)

    @router.post("/investor/files", status_code=201)
    async def upload_file(request: Request,
                          # An empty default, not Form(...): FastAPI treats a blank
                          # Form value as missing, and a required field would then
                          # 422 before our own check. With "" both a blank and an
                          # absent purpose fall through to the 400 below.
                          purpose: str = Form(""),
                          file: UploadFile = File(...),
                          ctx: OrgContext = Depends(require_org_role("investor")),
                          conn: psycopg.Connection = Depends(get_conn)):
        if purpose not in PHASE1_PURPOSES:
            raise HTTPException(status_code=400, detail="purpose is not accepted yet")
        # One byte past the cap is enough to know it is too big; never
        # buffer an unbounded body.
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="file too large (5 MB max)")
        if not data:
            raise HTTPException(status_code=400, detail="file is empty")
        detected = detect_type(data[:16])
        if detected is None:
            raise HTTPException(status_code=400, detail="unsupported file type")
        content_type, ext = detected
        # Only a well-formed upload spends a slot in the hourly budget.
        if limiter.is_limited(f"portal-upload:{ctx.org_id}:{ctx.user_id}"):
            raise HTTPException(status_code=429, detail="too many uploads; try again later")
        store: UploadStore = request.app.state.uploads
        digest = hashlib.sha256(data).hexdigest()
        # The key needs the row id, so: insert with a placeholder key, write
        # the bytes, then set the real key -- all in one transaction, so a
        # failed disk write leaves no row behind.
        with conn.transaction():
            file_id, created_at = conn.execute(
                "INSERT INTO files (org_id, user_id, purpose, content_type, size_bytes, sha256, "
                "storage_key) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, created_at",
                (ctx.org_id, ctx.user_id, purpose, content_type, len(data), digest,
                 f"pending/{ctx.org_id}/{secrets.token_hex(8)}")).fetchone()
            key = store.key(ctx.org_id, int(file_id), ext)
            store.write(key, data)
            conn.execute("UPDATE files SET storage_key = %s WHERE id = %s", (key, file_id))
        return {"id": int(file_id), "purpose": purpose, "content_type": content_type,
                "size_bytes": len(data), "created_at": created_at.isoformat()}

    @router.get("/investor/files/{file_id}")
    async def read_own_file(file_id: int, request: Request,
                            ctx: OrgContext = Depends(require_org_role("investor")),
                            conn: psycopg.Connection = Depends(get_conn)):
        return _serve(request, conn, ctx.org_id, file_id, user_id=ctx.user_id)

    @router.get("/files/{file_id}")
    async def read_any_file(file_id: int, request: Request,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)):
        return _serve(request, conn, ctx.org_id, file_id)

    return router
```

- [ ] **Step 7: Wire the store and the router in `main.py`**

In `api/src/api/main.py` replace:

```python
import asyncio
import httpx
import os
from contextlib import asynccontextmanager
from typing import Optional
```

with:

```python
import asyncio
import httpx
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
```

replace:

```python
from .alerts import EmailAlerter
from .telegram import TelegramNotifier
from .ws import create_ws_router, broadcaster
```

with:

```python
from .alerts import EmailAlerter
from .telegram import TelegramNotifier
from .uploads import UploadStore
from .ws import create_ws_router, broadcaster
```

replace:

```python
    # Store rate limiter in app state
    app.state.rate_limiter = rate_limiter
```

with:

```python
    # Store rate limiter in app state
    app.state.rate_limiter = rate_limiter

    # Uploaded receipts and proofs live on disk under UPLOAD_DIR (a named
    # volume in compose). Created here rather than in lifespan so a
    # TestClient app, which skips lifespan, has it too; tests swap it for
    # a temp directory. The one value is read straight from the env
    # (mirroring ApiConfig.upload_dir, same default and same "empty means
    # default" rule) rather than through ApiConfig.from_env(): create_app
    # runs at import time (`app = create_app()` below) and from_env would
    # make importing api.main demand SESSION_SECRET, FERNET_KEY and the
    # rest -- test_events_ws.py builds an app with only STATIC_DIR set.
    app.state.uploads = UploadStore(Path(os.environ.get("UPLOAD_DIR") or "./data/uploads"))
```

(`ApiConfig.upload_dir` from Step 4 stays: it is the documented config field, `test_upload_dir_defaults_when_unset_or_empty` covers it, and later tasks may read it through `cfg`.)

and replace:

```python
    # Include insights router (margin, candles, analytics, overview stats)
    insights_router = create_insights_router()
    app.include_router(insights_router)
```

with:

```python
    # Include insights router (margin, candles, analytics, overview stats)
    insights_router = create_insights_router()
    app.include_router(insights_router)

    # Client portal files: receipts and proofs (multipart upload; owner and
    # admin reads). The bytes live in app.state.uploads.
    from .routes.portal_files import create_portal_files_router
    app.include_router(create_portal_files_router())
```

- [ ] **Step 8: Run the upload tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_uploads.py -q -p no:cacheprovider`
Expected: PASS, 31 passed (1 + 3 + 1 + 1 + 1 + 6 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 8 + 1; the six `purpose` cases include `""`, which now proves the blank-purpose path lands on the 400, not a 422). (Running the suite creates `api/data/uploads/`; Step 9 ignores it in git.)

- [ ] **Step 9: The ops plumbing — image, volume, backup, env, gitignore**

In `api/Dockerfile` replace:

```dockerfile
ENV STATIC_DIR=/app/static
# Unprivileged runtime user: the api binds 8000 (not a privileged port) and
# writes nothing to disk, so root buys nothing and costs blast radius.
RUN useradd --system --no-create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser
```

with:

```dockerfile
ENV STATIC_DIR=/app/static
# Uploaded receipts and proofs (routes/portal_files.py). The directory is
# created in the image, owned by appuser, so the named volume compose
# mounts here inherits that ownership when it is first created.
ENV UPLOAD_DIR=/data/uploads
# Unprivileged runtime user: the api binds 8000 (not a privileged port) and
# writes only under /data/uploads, so root buys nothing and costs blast
# radius.
RUN useradd --system --no-create-home --uid 10001 appuser \
    && mkdir -p /data/uploads \
    && chown -R appuser:appuser /app /data/uploads
USER appuser
```

In `docker-compose.yml` replace:

```yaml
      BOOTSTRAP_ADMIN_EMAIL: ${BOOTSTRAP_ADMIN_EMAIL:-}
      BOOTSTRAP_ADMIN_PASSWORD: ${BOOTSTRAP_ADMIN_PASSWORD:-}
    depends_on:
      migrate: { condition: service_completed_successfully }

volumes:
  pgdata: {}
```

with:

```yaml
      BOOTSTRAP_ADMIN_EMAIL: ${BOOTSTRAP_ADMIN_EMAIL:-}
      BOOTSTRAP_ADMIN_PASSWORD: ${BOOTSTRAP_ADMIN_PASSWORD:-}
    volumes:
      # Uploaded receipts and proofs (the image's UPLOAD_DIR). Backed up by
      # ops/backup.sh next to the nightly dump; created on first `up`.
      - uploads:/data/uploads
    depends_on:
      migrate: { condition: service_completed_successfully }

volumes:
  pgdata: {}
  uploads: {}
```

In `ops/backup.sh` replace the header lines:

```bash
# Nightly Postgres backup for MirrorFleet.
#
# The database is the only irreplaceable state on the host: broker OAuth
# grants (encrypted), account roles and multipliers, position mappings, and
# the audit trail. Container images rebuild from git; this does not.
```

with:

```bash
# Nightly Postgres backup for MirrorFleet, plus the uploads volume.
#
# Two things on the host cannot be rebuilt from git: the database (broker
# OAuth grants, encrypted; account roles and multipliers; position
# mappings; the wallet ledger; the audit trail) and the uploads volume
# (the receipts and proofs investors attached to their requests). Each
# nightly run writes one .sql.gz and one uploads .tar.gz.
```

replace:

```bash
# Restore:
#   gunzip -c mirrorfleet-YYYY-MM-DD.sql.gz \
#     | sudo docker compose exec -T postgres psql -U copytrader -d copytrader
```

with:

```bash
# Restore the database:
#   gunzip -c mirrorfleet-YYYY-MM-DD_HHMM.sql.gz \
#     | sudo docker compose exec -T postgres psql -U copytrader -d copytrader
# Restore the uploads (into the running api container's volume):
#   gunzip -c mirrorfleet-uploads-YYYY-MM-DD_HHMM.tar.gz \
#     | sudo docker compose exec -T api tar -C /data -xf -
```

and replace the tail:

```bash
mv "$partial" "$out"
trap - EXIT

# Keep the newest N by COUNT, never by age: pruning on age alone would
# delete every backup after KEEP_DAYS of silent failures.
ls -1t "$BACKUP_DIR"/mirrorfleet-*.sql.gz 2>/dev/null \
    | tail -n "+$((KEEP_DAYS + 1))" | xargs -r rm -f
echo "ok: $out ($size bytes), keeping the newest $KEEP_DAYS"
```

with:

```bash
mv "$partial" "$out"
trap - EXIT

# The uploads volume, tarred from inside the running api container (it is
# the one that owns the mount). An empty directory still yields a valid
# small archive, so there is no size floor here. If the api container is
# not running this fails loudly (set -e) AFTER the dump is already safe.
uploads_out="$BACKUP_DIR/mirrorfleet-uploads-$stamp.tar.gz"
uploads_partial="$uploads_out.part"
trap 'rm -f "$uploads_partial"' EXIT
$DOCKER compose exec -T api tar -C /data -cf - uploads | gzip -9 > "$uploads_partial"
mv "$uploads_partial" "$uploads_out"
trap - EXIT

# Keep the newest N by COUNT, never by age: pruning on age alone would
# delete every backup after KEEP_DAYS of silent failures.
ls -1t "$BACKUP_DIR"/mirrorfleet-*.sql.gz 2>/dev/null \
    | tail -n "+$((KEEP_DAYS + 1))" | xargs -r rm -f
ls -1t "$BACKUP_DIR"/mirrorfleet-uploads-*.tar.gz 2>/dev/null \
    | tail -n "+$((KEEP_DAYS + 1))" | xargs -r rm -f
echo "ok: $out ($size bytes) and $uploads_out, keeping the newest $KEEP_DAYS of each"
```

In `.env.example` replace:

```
# Internal wiring
COPIER_CONTROL_URL=http://copier:8080
SHARDS=1
```

with:

```
# Internal wiring
COPIER_CONTROL_URL=http://copier:8080
SHARDS=1

# Where the api writes uploaded receipts and proofs. The api image already
# sets /data/uploads (the `uploads` named volume in docker-compose.yml), so
# leave this line commented out under Docker; an EMPTY value also means
# "use the default". Only a bare `uvicorn` run outside Docker needs it, and
# there the default is ./data/uploads.
# UPLOAD_DIR=/data/uploads
```

In `.gitignore` replace:

```
.pytest_cache/
node_modules/
```

with:

```
.pytest_cache/
node_modules/
# Local upload store (UPLOAD_DIR default ./data/uploads; the api tests
# create api/data/uploads).
data/
```

- [ ] **Step 10: Check the image still builds and the full suite is green**

Run (from the repo root): `docker compose build api`
Expected: the build ends with `=> exporting to image` and no error; the `RUN useradd ... mkdir -p /data/uploads ...` layer succeeds.

Run (from `api/`): `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider`
Expected: everything passes except the 7 pre-existing `test_events_ws.py` errors and the one EA-download CRLF failure.

Run: `git status --short`
Expected: no `api/data/` entry (ignored); the listed changes are exactly the files of this task.

- [ ] **Step 11: Commit**

```bash
git add api/pyproject.toml api/src/api/config.py api/src/api/uploads.py api/src/api/routes/portal_files.py api/src/api/main.py api/Dockerfile docker-compose.yml ops/backup.sh .env.example .gitignore api/tests/test_uploads.py
git commit -m "feat(api): receipt and proof uploads -- sniffed types, 5 MB cap, owner and admin reads, uploads volume, backup and env plumbing

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---


### Task 5: `portal_common.py` balances, settlement, audit, serialisers

The one module both routers import. It re-exports the pure rules of `portal_ledger` and adds everything that needs a database or the app: wallet figures, the idempotent settlement writer, the linked account and equity resolvers moved from `routes/investor.py`, audit and email, the per-org `portal_settings` row, and the row serialisers. Nothing here is a route.

**Files:**
- Create: `api/src/api/portal_common.py`
- Test: `api/tests/test_portal_common.py`

**Interfaces:**
- Consumes: `portal_ledger.WALLETS`, `balance`, `holds`, `available`, `floor_cents`, `round_cents`, `money`, `clean_text`, `LedgerError` (Task 2); tables `wallet_entries`, `withdrawals`, `transfers`, `portal_settings`, `payout_destinations`, `deposits` (Task 1); test helpers `portal_helpers.credit`, `portal_helpers.approved_destination` (Task 1); `routes.settings_control._proxy_to_copier`, `routes.mt5.MT5_OFFLINE_AFTER_S`, `ws.broadcaster`.
- Produces: `audit_control`, `notify_investor`, `linked_account`, `account_card`, `org_state`, `equity_from`, `equity_for`, `wallet_balances`, `wallet_holds`, `wallet_figures`, `open_account_transfers_out`, `net_funded`, `settle`, `portal_settings`, `destination_summary`, `short_address`, `qualify`, `CURRENCY`, `DEPOSIT_COLS`, `WITHDRAWAL_COLS`, `TRANSFER_COLS`, `DESTINATION_COLS`, `ENTRY_COLS`, `METHOD_COLS`, `deposit_json`, `withdrawal_json`, `transfer_json`, `destination_json`, `entry_json`, `method_json`, `Decision`, `require_note_on_reject`; every `portal_ledger` name is reachable as `portal_common.<name>`. (`account_card`, `org_state`, `equity_from` and `qualify` are private helpers this task adds beyond the interfaces doc; Task 10 uses the first three for the summary and the investors list.)

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_portal_common.py`:

```python
# api/tests/test_portal_common.py
"""portal_common: the figures every portal router reads, the one settlement
writer, audit and email plumbing, the settings row and the serialisers.
Real Postgres; the copier's /state is faked through the app's mock
transport only where equity_for needs it."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from conftest import default_mock_callback
from portal_helpers import approved_destination, credit

from api import portal_common as pc
from api import ws as ws_module


@pytest.fixture
def org_user(db, make_user, make_org):
    user = make_user(email="inv@example.com")
    org_id = make_org(name="Desk", members=[(user, "investor")])
    return org_id, user["id"]


def _fake_state(client, accounts=None, down=False):
    """Fake the copier's /state. `accounts` is {account_id: {equity, ...}}
    exactly as the copier serialises it (string keys)."""
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


# ------------------------------------------------------------ re-exports


def test_the_ledger_rules_are_reachable_through_portal_common():
    assert pc.parse_amount("1.50") == Decimal("1.50")
    assert pc.WALLETS == ("main", "credit", "pamm", "social")
    assert pc.can_transition("deposits", "pending", "cancelled") is True
    assert pc.money(Decimal("2.005")) == 2.01
    assert pc.CURRENCY == "USD"


# ------------------------------------------------------------ settlement


def test_settle_writes_once_per_reference(db, org_user):
    org_id, user_id = org_user
    with psycopg.connect(db, autocommit=True) as conn:
        first = pc.settle(conn, org_id=org_id, user_id=user_id, wallet="main",
                          amount=Decimal("100.00"), kind="deposit",
                          ref_table="deposits", ref_id=7)
        replay = pc.settle(conn, org_id=org_id, user_id=user_id, wallet="main",
                           amount=Decimal("100.00"), kind="deposit",
                           ref_table="deposits", ref_id=7)
        # An adjustment has no reference, so two of them are two rows.
        adj1 = pc.settle(conn, org_id=org_id, user_id=user_id, wallet="credit",
                         amount=Decimal("-5.00"), kind="adjustment",
                         ref_table=None, ref_id=None, note="fix", created_by=user_id)
        adj2 = pc.settle(conn, org_id=org_id, user_id=user_id, wallet="credit",
                         amount=Decimal("-5.00"), kind="adjustment",
                         ref_table=None, ref_id=None, note="fix", created_by=user_id)
        rows = conn.execute(
            "SELECT wallet, amount, kind, ref_table, ref_id, note, created_by "
            "FROM wallet_entries WHERE org_id = %s ORDER BY id", (org_id,)).fetchall()
    assert (first, replay, adj1, adj2) == (True, False, True, True)
    assert rows == [("main", Decimal("100.00"), "deposit", "deposits", 7, None, None),
                    ("credit", Decimal("-5.00"), "adjustment", None, None, "fix", user_id),
                    ("credit", Decimal("-5.00"), "adjustment", None, None, "fix", user_id)]
    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.wallet_balances(conn, org_id, user_id) == {
            "main": Decimal("100.00"), "credit": Decimal("-10.00"),
            "pamm": Decimal("0"), "social": Decimal("0")}


# ------------------------------------------------------------ figures


def test_wallet_figures_hold_open_requests_and_floor_available(db, org_user):
    org_id, user_id = org_user
    credit(db, org_id, user_id, "5120.50")
    credit(db, org_id, user_id, "10", wallet="pamm")
    dest = approved_destination(db, org_id, user_id)
    with psycopg.connect(db, autocommit=True) as conn:
        for amount, status in (("100", "requested"), ("30", "approved"),
                               ("999", "rejected"), ("999", "paid"), ("999", "cancelled")):
            conn.execute(
                "INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
                "destination_summary, amount, fee, net_amount, status) "
                "VALUES (%s, %s, %s, 'crypto', 'TRC20 T…23', %s, 0, %s, %s)",
                (org_id, user_id, dest, amount, amount, status))
        for src, tgt, amount, status in (("main", "pamm", "20.25", "requested"),
                                         ("pamm", "main", "4", "approved"),
                                         ("main", "pamm", "999", "done"),
                                         ("main", "pamm", "999", "rejected")):
            conn.execute(
                "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, "
                "target_kind, target_wallet, amount, status) "
                "VALUES (%s, %s, 'wallet', %s, 'wallet', %s, %s, %s)",
                (org_id, user_id, src, tgt, amount, status))
        holds = pc.wallet_holds(conn, org_id, user_id)
        figures = pc.wallet_figures(conn, org_id, user_id)
    assert holds == {"main": Decimal("150.25"), "credit": Decimal("0"),
                     "pamm": Decimal("4"), "social": Decimal("0")}
    assert figures["main"] == {"balance": Decimal("5120.50"), "on_hold": Decimal("150.25"),
                               "available": Decimal("4970.25")}
    assert figures["pamm"] == {"balance": Decimal("10.00"), "on_hold": Decimal("4"),
                               "available": Decimal("6.00")}
    assert figures["credit"]["available"] == Decimal("0.00")
    assert set(figures) == set(pc.WALLETS)
    # available is floored, never rounded up: the pure rule the figures use.
    assert pc.available(Decimal("5120.506"), Decimal("0")) == Decimal("5120.50")


def test_net_funded_and_open_account_transfers_out(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, 'investor')",
                     (org_id, investor["id"]))
        conn.execute("UPDATE accounts SET investor_user_id = %s WHERE ctid_trader_account_id = 1001",
                     (investor["id"],))
        rows = [("wallet", "main", None, "account", None, 1001, "500", "done"),
                ("account", None, 1001, "wallet", "main", None, "120", "done"),
                ("account", None, 1001, "wallet", "main", None, "30", "requested"),
                ("account", None, 1001, "wallet", "main", None, "12.50", "approved"),
                ("account", None, 1001, "wallet", "main", None, "999", "rejected"),
                ("wallet", "main", None, "account", None, 1001, "999", "cancelled")]
        for sk, sw, sa, tk, tw, ta, amount, status in rows:
            conn.execute(
                "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, "
                "source_account_id, target_kind, target_wallet, target_account_id, amount, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (org_id, investor["id"], sk, sw, sa, tk, tw, ta, amount, status))
        assert pc.linked_account(conn, org_id, investor["id"]) == 1001
        assert pc.linked_account(conn, org_id, 999999) is None
        assert pc.net_funded(conn, org_id, investor["id"], 1001) == Decimal("380.00")
        assert pc.open_account_transfers_out(conn, org_id, investor["id"], 1001) == Decimal("42.50")
        card = pc.account_card(conn, org_id, 1001)
    assert card == {"account_id": 1001, "nickname": None, "platform": "ctrader",
                    "status": "ok", "last_error": None, "connected": True}


def test_equity_for_prefers_live_then_last_known_then_unknown(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    request = SimpleNamespace(app=client.app)
    with psycopg.connect(db, autocommit=True) as conn:
        (mt5_id,) = conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
            "platform, trader_login, is_live, role, enabled, nickname) "
            "VALUES (nextval('mt5_account_id_seq'), NULL, %s, 'mt5', 0, false, 'slave', "
            "true, 'Inv') RETURNING ctid_trader_account_id", (org_id,)).fetchone()
        conn.execute("INSERT INTO mt5_links (account_id, key_hash, equity, balance) "
                     "VALUES (%s, 'h', 4990.25, 4990.25)", (mt5_id,))
        _fake_state(client, {1001: {"balance": 5000.0, "equity": 5120.5, "open_pnl": 120.5,
                                    "positions": [{"position_id": 7}]}})
        live = asyncio.run(pc.equity_for(request, conn, org_id, 1001))
        _fake_state(client, down=True)
        last_known = asyncio.run(pc.equity_for(request, conn, org_id, int(mt5_id)))
        unknown = asyncio.run(pc.equity_for(request, conn, org_id, 1001))
        none = asyncio.run(pc.equity_for(request, conn, org_id, None))
    assert live == (Decimal("5120.5"), "live", [{"position_id": 7}])
    assert last_known == (Decimal("4990.25"), "last known", [])
    assert unknown == (None, "unknown", [])
    assert none == (None, "unknown", [])


# ------------------------------------------------------------ settings


def test_portal_settings_creates_the_default_row_once(db, org_user):
    org_id, _user_id = org_user
    with psycopg.connect(db, autocommit=True) as conn:
        first = pc.portal_settings(conn, org_id)
        conn.execute("UPDATE portal_settings SET withdrawal_min = 50, withdrawal_fee_pct = 1.5 "
                     "WHERE org_id = %s", (org_id,))
        second = pc.portal_settings(conn, org_id)
        (count,) = conn.execute("SELECT count(*) FROM portal_settings WHERE org_id = %s",
                                (org_id,)).fetchone()
    assert first == {"withdrawal_min": Decimal("0.00"), "withdrawal_fee_pct": Decimal("0.000")}
    assert second == {"withdrawal_min": Decimal("50.00"), "withdrawal_fee_pct": Decimal("1.500")}
    assert count == 1


# ------------------------------------------------------------ text helpers


def test_destination_summary_and_short_address():
    assert pc.short_address("TAddr123") == "TAddr123"
    assert pc.short_address("TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE") == "T…SE"
    assert pc.destination_summary("bank", {"bank_name": "ICICI Bank", "holder": "S",
                                           "account_number": "000401234543", "code": "X"}) \
        == "ICICI Bank ••4543"
    assert pc.destination_summary("crypto", {"coin": "USDT", "network": "TRC20",
                                             "address": "TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE"}) \
        == "TRC20 T…SE"
    assert pc.qualify("id, user_id", "d") == "d.id, d.user_id"


def test_require_note_on_reject():
    assert pc.require_note_on_reject("confirmed", None) is None
    assert pc.require_note_on_reject("confirmed", " ok ") == "ok"
    assert pc.require_note_on_reject("rejected", "no such tx") == "no such tx"
    with pytest.raises(pc.LedgerError, match="note is required"):
        pc.require_note_on_reject("rejected", "  ")
    with pytest.raises(pc.LedgerError, match="at most 500"):
        pc.require_note_on_reject("rejected", "x" * 501)


# ------------------------------------------------------------ serialisers


_TS = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def test_deposit_json_rounds_to_cents_and_carries_the_currency():
    row = (12, 5, 3, "crypto", "USDT on TRC20", Decimal("5000.00"), Decimal("50.00"), None,
           "tx-1", None, "wallet", None, "sent", "pending", None, None, None, _TS)
    out = pc.deposit_json(row)
    assert out == {"id": 12, "user_id": 5, "method_id": 3, "method_kind": "crypto",
                   "method_label": "USDT on TRC20", "amount": 5000.0, "fee": 50.0,
                   "credited_amount": None, "reference": "tx-1", "receipt_file_id": None,
                   "target": "wallet", "target_account_id": None, "note": "sent",
                   "status": "pending", "decided_by": None, "decided_at": None,
                   "decision_note": None, "created_at": _TS.isoformat(), "currency": "USD"}
    admin = pc.deposit_json(row + ("inv@example.com", "Inv"))
    assert admin["email"] == "inv@example.com" and admin["display_name"] == "Inv"


def test_withdrawal_transfer_entry_and_method_json():
    wd = pc.withdrawal_json((4, 5, 9, "bank", "ICICI Bank ••4543", Decimal("250.00"),
                             Decimal("2.50"), Decimal("247.50"), "requested", None, None, None,
                             None, None, None, _TS))
    assert wd["net_amount"] == 247.5 and wd["destination_summary"] == "ICICI Bank ••4543"
    assert wd["currency"] == "USD" and wd["paid_at"] is None and wd["created_at"] == _TS.isoformat()
    tr = pc.transfer_json((9, 5, "wallet", "main", None, "account", None, 1001, Decimal("100.00"),
                           "requested", Decimal("5120.50"), True, None, None, None, None, None,
                           None, _TS, "inv@example.com", "Inv"))
    assert tr["source"] == {"kind": "wallet", "wallet": "main"}
    assert tr["target"] == {"kind": "account", "account_id": 1001}
    assert tr["equity_at_request"] == 5120.5 and tr["equity_verified"] is True
    assert tr["email"] == "inv@example.com" and tr["currency"] == "USD"
    entry = pc.entry_json((1, "main", Decimal("-100.00"), "withdrawal", "withdrawals", 4, None, _TS))
    assert entry == {"id": 1, "wallet": "main", "amount": -100.0, "kind": "withdrawal",
                     "ref_table": "withdrawals", "ref_id": 4, "note": None,
                     "created_at": _TS.isoformat(), "currency": "USD"}
    method = pc.method_json((3, "crypto", "USDT on TRC20", True, "USD",
                             {"coin": "USDT", "network": "TRC20", "address": "T1"},
                             Decimal("50.00"), Decimal("1.500"), None, 2), public=True)
    assert method == {"id": 3, "kind": "crypto", "label": "USDT on TRC20", "enabled": True,
                      "currency": "USD", "details": {"coin": "USDT", "network": "TRC20",
                                                     "address": "T1"},
                      "min_amount": 50.0, "fee_pct": 1.5, "instructions": None, "sort_order": 2}


def test_destination_json_masks_bank_account_numbers_unless_full():
    details = {"bank_name": "ICICI Bank", "holder": "S", "account_number": "000401234543",
               "code": "ICIC0000004"}
    row = (9, 5, "bank", "Salary account", details, None, "approved", 1, _TS, None, _TS)
    masked = pc.destination_json(row, full=False)
    assert masked["details"]["account_number"] == "••4543"
    assert masked["summary"] == "ICICI Bank ••4543" and masked["nickname"] == "Salary account"
    assert masked["decided_at"] == _TS.isoformat() and "email" not in masked
    full = pc.destination_json(row + ("inv@example.com", "Inv"), full=True)
    assert full["details"]["account_number"] == "000401234543"
    assert full["email"] == "inv@example.com"
    assert details["account_number"] == "000401234543", "the caller's dict is not mutated"


# ------------------------------------------------------------ audit + email


def test_audit_control_writes_one_control_event(db, org_user):
    org_id, user_id = org_user
    with psycopg.connect(db, autocommit=True) as conn:
        asyncio.run(pc.audit_control(
            conn, org_id=org_id, action="investor_deposit_noticed", actor_email="inv@example.com",
            user_id=user_id, severity="warning", account_id=None, deposit_id=12,
            summary="Deposit notice: 5000.00 USD via USDT on TRC20 from inv@example.com"))
        asyncio.run(pc.audit_control(
            conn, org_id=org_id, action="investor_deposit_decided", actor_email="admin@example.com",
            user_id=user_id, account_id=1001, deposit_id=12, status="confirmed"))
        rows = conn.execute(
            "SELECT category, severity, account_id, actor_email, payload FROM events "
            "WHERE org_id = %s ORDER BY id", (org_id,)).fetchall()
    assert rows[0][:4] == ("control", "warning", None, "inv@example.com")
    assert rows[0][4] == {"action": "investor_deposit_noticed", "user_id": user_id,
                          "deposit_id": 12,
                          "summary": "Deposit notice: 5000.00 USD via USDT on TRC20 from inv@example.com"}
    assert rows[1][:4] == ("control", "info", 1001, "admin@example.com")
    assert rows[1][4]["status"] == "confirmed" and rows[1][4]["user_id"] == user_id


class _FakeAlerter:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_to(self, to_addr, subject, text):
        if self.fail:
            raise RuntimeError("resend down")
        self.sent.append((to_addr, subject, text))
        return True


def test_notify_investor_is_best_effort(db, org_user, monkeypatch):
    org_id, user_id = org_user
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    fake = _FakeAlerter()
    with psycopg.connect(db, autocommit=True) as conn:
        monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
        asyncio.run(pc.notify_investor(conn, request, user_id, "Subject", "Body"))
        asyncio.run(pc.notify_investor(conn, request, 999999, "Nobody", "Body"))
        monkeypatch.setattr(ws_module.broadcaster, "alerter", _FakeAlerter(fail=True), raising=False)
        asyncio.run(pc.notify_investor(conn, request, user_id, "Fails quietly", "Body"))
        monkeypatch.setattr(ws_module.broadcaster, "alerter", None, raising=False)
        asyncio.run(pc.notify_investor(conn, request, user_id, "No alerter", "Body"))
    assert fake.sent == [("inv@example.com", "Subject", "Body")]
```

- [ ] **Step 2: Run them to verify they fail**

Run (from `api/`, environment exported as in the Global Constraints):

```bash
.venv/Scripts/python -m pytest tests/test_portal_common.py -q -p no:cacheprovider
```

Expected: collection ERROR, `ModuleNotFoundError: No module named 'api.portal_common'` (1 error).

- [ ] **Step 3: Write the module**

Create `api/src/api/portal_common.py`:

```python
# api/src/api/portal_common.py
"""Shared plumbing for the client-portal routers (phase 1).

Everything the investor and admin routers need in common lives here so the
two route files stay about their routes: the ledger figures read from the
database, the ONE idempotent settlement writer, the linked-account and
equity resolvers moved from routes/investor.py, audit and email, the
per-org portal settings row, and the row serialisers. Pure money rules
(parsing, rounding, fees, state machines) live in portal_ledger and are
re-exported from here so a router imports a single module.

The app never holds keys and never moves money: every transfer of value
happens outside it and is recorded, approved and reconciled here.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

import psycopg
from fastapi import HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from .config import ApiConfig
from .portal_ledger import *  # noqa: F401,F403  -- re-exported for the routers
from .portal_ledger import WALLETS, available, balance, clean_text, holds, money
from .routes.mt5 import MT5_OFFLINE_AFTER_S
from .routes.settings_control import _proxy_to_copier
from .ws import broadcaster

logger = logging.getLogger(__name__)

CURRENCY = "USD"

OPEN_WITHDRAWAL_STATUSES = ("requested", "approved")
OPEN_TRANSFER_STATUSES = ("requested", "approved")


# ------------------------------------------------------------ audit + email


async def audit_control(conn: psycopg.Connection, *, org_id: int, action: str,
                        actor_email: str, user_id: int, severity: str = "info",
                        account_id: Optional[int] = None, **detail: Any) -> None:
    """One `events` row per state change (category control). Warnings reach
    the admin email and Telegram channels through ALERT_RULES and
    TELEGRAM_RULES; info rows only drive the live refresh. `user_id` is the
    investor the row is ABOUT so the alerters cool down per investor.
    Best effort: a failed audit write is logged, never surfaced as a failed
    request."""
    try:
        conn.execute(
            "INSERT INTO events (org_id, account_id, category, severity, payload, actor_email) "
            "VALUES (%s, %s, 'control', %s, %s, %s)",
            (org_id, account_id, severity,
             Jsonb({"action": action, "user_id": user_id, **detail}), actor_email))
    except Exception:
        logger.exception("failed to write portal audit event %s", action)


async def notify_investor(conn: psycopg.Connection, request: Request, user_id: int,
                          subject: str, text: str) -> None:
    """Best-effort email to the investor's own address. The alerter lives on
    the event broadcaster (main.py wires it at startup); with none
    configured this is a no-op, and a failing send never fails the
    request that triggered it."""
    alerter = getattr(broadcaster, "alerter", None)
    if alerter is None:
        alerter = getattr(request.app.state, "alerter", None)
    if alerter is None:
        return
    row = conn.execute("SELECT email FROM users WHERE id = %s", (user_id,)).fetchone()
    if not row:
        return
    try:
        await alerter.send_to(row[0], subject, text)
    except Exception:
        logger.exception("investor email failed for user %s", user_id)


# ------------------------------------------------------------ accounts + equity


def linked_account(conn: psycopg.Connection, org_id: int, user_id: int) -> Optional[int]:
    """The ONE trading account linked to this investor in this workspace
    (accounts.investor_user_id), or None while unlinked."""
    row = conn.execute(
        "SELECT ctid_trader_account_id FROM accounts "
        "WHERE org_id = %s AND investor_user_id = %s", (org_id, user_id)).fetchone()
    return int(row[0]) if row else None


def account_card(conn: psycopg.Connection, org_id: int, account_id: int) -> dict:
    """The account card the investor summary shows (moved from
    routes/investor.py). 404 when the account is not this org's."""
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


async def org_state(client, cfg: ApiConfig, org_id: int) -> Optional[dict]:
    """One /state round trip for the whole org. None when the copier is
    down or answers with something that isn't a JSON object -- callers
    then fall back to last-known equity for every account, not just one."""
    try:
        state = await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/state?org_id={org_id}", method="GET")
    except HTTPException:
        return None
    return state if isinstance(state, dict) else None


def equity_from(state: Optional[dict], conn: psycopg.Connection,
                account_id: int) -> tuple[Optional[Decimal], str, list]:
    """Live equity from an already-fetched /state snapshot, else the last
    equity the MT5 terminal reported, else unknown."""
    accounts = state.get("accounts") if isinstance(state, dict) else None
    entry = accounts.get(str(account_id)) if isinstance(accounts, dict) else None
    if isinstance(entry, dict) and entry.get("equity") is not None:
        return Decimal(str(entry["equity"])), "live", list(entry.get("positions") or [])
    row = conn.execute("SELECT equity FROM mt5_links WHERE account_id = %s",
                       (account_id,)).fetchone()
    if row and row[0] is not None:
        return Decimal(str(row[0])), "last known", []
    return None, "unknown", []


async def equity_for(request: Request, conn: psycopg.Connection, org_id: int,
                     account_id: Optional[int]) -> tuple[Optional[Decimal], str, list]:
    """(equity, source, positions) for one account: 'live' from the copier,
    'last known' from mt5_links, 'unknown' otherwise. None account_id (no
    link) is simply unknown. Never raises for an unreachable copier."""
    if account_id is None:
        return None, "unknown", []
    state = await org_state(request.app.state.http, ApiConfig.from_env(), org_id)
    return equity_from(state, conn, account_id)


# ------------------------------------------------------------ wallet figures


def wallet_balances(conn: psycopg.Connection, org_id: int, user_id: int) -> dict[str, Decimal]:
    """Sum of every wallet's entries; every WALLETS key present."""
    rows = conn.execute(
        "SELECT wallet, SUM(amount) FROM wallet_entries "
        "WHERE org_id = %s AND user_id = %s GROUP BY wallet", (org_id, user_id)).fetchall()
    summed = balance([(w, Decimal(a)) for w, a in rows])
    return {w: summed.get(w, Decimal("0")) for w in WALLETS}


def wallet_holds(conn: psycopg.Connection, org_id: int, user_id: int) -> dict[str, Decimal]:
    """Money spoken for by open requests: withdrawals in requested/approved
    hold on main; transfers in requested/approved hold on their source
    wallet."""
    withdrawals = [Decimal(a) for (a,) in conn.execute(
        "SELECT amount FROM withdrawals WHERE org_id = %s AND user_id = %s "
        "AND status = ANY(%s)", (org_id, user_id, list(OPEN_WITHDRAWAL_STATUSES))).fetchall()]
    transfers = [(w, Decimal(a)) for w, a in conn.execute(
        "SELECT source_wallet, amount FROM transfers WHERE org_id = %s AND user_id = %s "
        "AND source_kind = 'wallet' AND status = ANY(%s)",
        (org_id, user_id, list(OPEN_TRANSFER_STATUSES))).fetchall()]
    held = holds(withdrawals, transfers)
    return {w: held.get(w, Decimal("0")) for w in WALLETS}


def wallet_figures(conn: psycopg.Connection, org_id: int,
                   user_id: int) -> dict[str, dict[str, Decimal]]:
    """{wallet: {balance, on_hold, available}} with available FLOORED to
    cents -- the same Decimal every cap check compares against."""
    balances = wallet_balances(conn, org_id, user_id)
    held = wallet_holds(conn, org_id, user_id)
    return {w: {"balance": balances[w], "on_hold": held[w],
                "available": available(balances[w], held[w])} for w in WALLETS}


def open_account_transfers_out(conn: psycopg.Connection, org_id: int, user_id: int,
                               account_id: int) -> Decimal:
    """Requested + approved account->wallet transfers: equity already spoken for."""
    (total,) = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM transfers WHERE org_id = %s AND user_id = %s "
        "AND source_kind = 'account' AND source_account_id = %s AND status = ANY(%s)",
        (org_id, user_id, account_id, list(OPEN_TRANSFER_STATUSES))).fetchone()
    return Decimal(total)


def net_funded(conn: psycopg.Connection, org_id: int, user_id: int, account_id: int) -> Decimal:
    """Done wallet->account minus done account->wallet for the linked
    account: what the investor has put into the broker account, net."""
    (total,) = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN target_kind = 'account' AND target_account_id = %s THEN amount "
        "                        WHEN source_kind = 'account' AND source_account_id = %s THEN -amount "
        "                        ELSE 0 END), 0) "
        "FROM transfers WHERE org_id = %s AND user_id = %s AND status = 'done'",
        (account_id, account_id, org_id, user_id)).fetchone()
    return Decimal(total)


# ------------------------------------------------------------ settlement


def settle(conn: psycopg.Connection, *, org_id: int, user_id: int, wallet: str, amount: Decimal,
           kind: str, ref_table: Optional[str], ref_id: Optional[int],
           note: Optional[str] = None, created_by: Optional[int] = None) -> bool:
    """The only writer of wallet_entries. Called inside the transaction that
    changes a request's status. The partial unique index
    wallet_entries_one_per_ref (ref_table, ref_id, wallet) makes a retried
    settlement a no-op, so a double click or a replayed request cannot
    double-credit. Returns True when a row was written. Adjustments carry
    no reference and are never deduplicated."""
    row = conn.execute(
        "INSERT INTO wallet_entries (org_id, user_id, wallet, amount, kind, ref_table, ref_id, "
        "note, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (ref_table, ref_id, wallet) WHERE ref_table IS NOT NULL DO NOTHING "
        "RETURNING id",
        (org_id, user_id, wallet, amount, kind, ref_table, ref_id, note, created_by)).fetchone()
    return row is not None


# ------------------------------------------------------------ settings


def portal_settings(conn: psycopg.Connection, org_id: int) -> dict:
    """{withdrawal_min, withdrawal_fee_pct} as Decimals; the row is created
    with the zero defaults on first read."""
    conn.execute("INSERT INTO portal_settings (org_id) VALUES (%s) ON CONFLICT (org_id) DO NOTHING",
                 (org_id,))
    row = conn.execute(
        "SELECT withdrawal_min, withdrawal_fee_pct FROM portal_settings WHERE org_id = %s",
        (org_id,)).fetchone()
    return {"withdrawal_min": Decimal(row[0]), "withdrawal_fee_pct": Decimal(row[1])}


# ------------------------------------------------------------ text helpers


def short_address(a: str) -> str:
    return a if len(a) <= 8 else f"{a[0]}…{a[-2:]}"


def destination_summary(kind: str, details: dict) -> str:
    """bank -> "<bank_name> ••<last 4>", crypto -> "<network> <shortAddress>"."""
    details = details or {}
    if kind == "bank":
        number = str(details.get("account_number") or "")
        return f"{details.get('bank_name') or ''} ••{number[-4:]}".strip()
    return f"{details.get('network') or ''} {short_address(str(details.get('address') or ''))}".strip()


def qualify(cols: str, alias: str) -> str:
    """'id, user_id' -> 'd.id, d.user_id' for the admin joins."""
    return ", ".join(f"{alias}.{c.strip()}" for c in cols.split(","))


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


# ------------------------------------------------------------ serialisers


DEPOSIT_COLS = ("id, user_id, method_id, method_kind, method_label, amount, fee, credited_amount, "
                "reference, receipt_file_id, target, target_account_id, note, status, decided_by, "
                "decided_at, decision_note, created_at")
WITHDRAWAL_COLS = ("id, user_id, destination_id, destination_kind, destination_summary, amount, "
                   "fee, net_amount, status, decided_by, decided_at, decision_note, paid_by, "
                   "paid_at, txid, created_at")
TRANSFER_COLS = ("id, user_id, source_kind, source_wallet, source_account_id, target_kind, "
                 "target_wallet, target_account_id, amount, status, equity_at_request, "
                 "equity_verified, decided_by, decided_at, decision_note, done_by, done_at, "
                 "note, created_at")
DESTINATION_COLS = ("id, user_id, kind, nickname, details, proof_file_id, status, decided_by, "
                    "decided_at, decision_note, created_at")
ENTRY_COLS = "id, wallet, amount, kind, ref_table, ref_id, note, created_at"
METHOD_COLS = ("id, kind, label, enabled, currency, details, min_amount, fee_pct, instructions, "
               "sort_order")


def _with_person(out: dict, row, width: int) -> dict:
    """Admin queries append u.email, u.display_name after the row columns."""
    if len(row) > width:
        out["email"], out["display_name"] = row[width], row[width + 1]
    return out


def deposit_json(row) -> dict:
    (dep_id, user_id, method_id, method_kind, method_label, amount, fee, credited, reference,
     receipt_file_id, target, target_account_id, note, status, decided_by, decided_at,
     decision_note, created_at) = row[:18]
    out = {"id": dep_id, "user_id": user_id, "method_id": method_id,
           "method_kind": method_kind, "method_label": method_label,
           "amount": money(amount), "fee": money(fee), "credited_amount": money(credited),
           "reference": reference, "receipt_file_id": receipt_file_id, "target": target,
           "target_account_id": int(target_account_id) if target_account_id is not None else None,
           "note": note, "status": status, "decided_by": decided_by,
           "decided_at": _iso(decided_at), "decision_note": decision_note,
           "created_at": _iso(created_at), "currency": CURRENCY}
    return _with_person(out, row, 18)


def withdrawal_json(row) -> dict:
    (wd_id, user_id, destination_id, destination_kind, destination_summary_, amount, fee,
     net_amount, status, decided_by, decided_at, decision_note, paid_by, paid_at, txid,
     created_at) = row[:16]
    out = {"id": wd_id, "user_id": user_id, "destination_id": destination_id,
           "destination_kind": destination_kind, "destination_summary": destination_summary_,
           "amount": money(amount), "fee": money(fee), "net_amount": money(net_amount),
           "status": status, "decided_by": decided_by, "decided_at": _iso(decided_at),
           "decision_note": decision_note, "paid_by": paid_by, "paid_at": _iso(paid_at),
           "txid": txid, "created_at": _iso(created_at), "currency": CURRENCY}
    return _with_person(out, row, 16)


def _money_ref(kind: str, wallet: Optional[str], account_id: Optional[int]) -> dict:
    if kind == "wallet":
        return {"kind": "wallet", "wallet": wallet}
    return {"kind": "account", "account_id": int(account_id) if account_id is not None else None}


def transfer_json(row) -> dict:
    (tr_id, user_id, source_kind, source_wallet, source_account_id, target_kind, target_wallet,
     target_account_id, amount, status, equity_at_request, equity_verified, decided_by,
     decided_at, decision_note, done_by, done_at, note, created_at) = row[:19]
    out = {"id": tr_id, "user_id": user_id,
           "source": _money_ref(source_kind, source_wallet, source_account_id),
           "target": _money_ref(target_kind, target_wallet, target_account_id),
           "amount": money(amount), "status": status,
           "equity_at_request": money(equity_at_request),
           "equity_verified": bool(equity_verified), "decided_by": decided_by,
           "decided_at": _iso(decided_at), "decision_note": decision_note, "done_by": done_by,
           "done_at": _iso(done_at), "note": note, "created_at": _iso(created_at),
           "currency": CURRENCY}
    return _with_person(out, row, 19)


def destination_json(row, *, full: bool) -> dict:
    """full=False masks a bank account number to its last four digits: the
    owner and admins see it whole, nobody else ever sees the row at all."""
    (dest_id, user_id, kind, nickname, details, proof_file_id, status, decided_by, decided_at,
     decision_note, created_at) = row[:11]
    details = dict(details or {})
    summary = destination_summary(kind, details)
    if not full and kind == "bank" and details.get("account_number"):
        details["account_number"] = "••" + str(details["account_number"])[-4:]
    out = {"id": dest_id, "user_id": user_id, "kind": kind, "nickname": nickname,
           "details": details, "proof_file_id": proof_file_id, "status": status,
           "decided_by": decided_by, "decided_at": _iso(decided_at),
           "decision_note": decision_note, "created_at": _iso(created_at), "summary": summary}
    return _with_person(out, row, 11)


def entry_json(row) -> dict:
    entry_id, wallet, amount, kind, ref_table, ref_id, note, created_at = row[:8]
    return {"id": entry_id, "wallet": wallet, "amount": money(amount), "kind": kind,
            "ref_table": ref_table, "ref_id": ref_id, "note": note,
            "created_at": _iso(created_at), "currency": CURRENCY}


def method_json(row, *, public: bool) -> dict:
    """One shape for admins and investors (the interfaces doc: public omits
    nothing; what protects investors is that disabled rows are never
    listed to them). `public` documents the caller's intent."""
    (method_id, kind, label, enabled, currency, details, min_amount, fee_pct, instructions,
     sort_order) = row[:10]
    return {"id": method_id, "kind": kind, "label": label, "enabled": bool(enabled),
            "currency": currency, "details": dict(details or {}),
            "min_amount": money(min_amount), "fee_pct": float(fee_pct),
            "instructions": instructions, "sort_order": sort_order}


# ------------------------------------------------------------ decisions


class Decision(BaseModel):
    status: str
    note: Optional[str] = None


def require_note_on_reject(status: str, note: Optional[str]) -> Optional[str]:
    """A rejection must say why; any other decision may carry a note."""
    return clean_text(note, "note", max_len=500, required=(status == "rejected"))
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/Scripts/python -m pytest tests/test_portal_common.py tests/test_portal_ledger.py -q -p no:cacheprovider
```

Expected: PASS, all tests green (13 in `test_portal_common.py` plus the Task 2 file).

- [ ] **Step 5: Run the whole API suite**

```bash
.venv/Scripts/python -m pytest tests -q -p no:cacheprovider
```

Expected: green apart from the 7 pre-existing `test_events_ws.py` errors and the one EA-download CRLF failure.

- [ ] **Step 6: Commit**

```bash
git add api/src/api/portal_common.py api/tests/test_portal_common.py
git commit -m "feat(api): portal_common -- wallet figures, idempotent settlement, audit, email and serialisers for the client portal

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 6: Payment methods and portal settings routes

Creates the two portal routers and mounts them. The admin router gets payment-method CRUD and the portal settings pair; the investor router gets the enabled-methods read. Every later API task grows these two files.

**Files:**
- Create: `api/src/api/routes/portal_admin.py`
- Create: `api/src/api/routes/portal_investor.py`
- Modify: `api/src/api/main.py` (mount both routers BEFORE the 2026-09-23 investor routers)
- Test: `api/tests/test_portal_methods.py`

**Interfaces:**
- Consumes: `portal_common` (`METHOD_COLS`, `method_json`, `audit_control`, `portal_settings`, `money`, `clean_text`, `parse_amount`, `LedgerError`) (Task 5); `require_org_role`, `OrgContext`, `get_conn`; tables `payment_methods`, `portal_settings`, `deposits` (Task 1); test helpers `portal_helpers.csrf`, `portal_helpers.add_method` (Task 1).
- Produces: `create_portal_admin_router() -> APIRouter` with `MethodBody`, `MethodPatch`, `SettingsBody`, `clean_details(kind, raw) -> dict`, `parse_min(raw, field) -> Decimal`, `parse_pct(raw, field) -> Decimal`, `REQUIRED_DETAILS`, `OPTIONAL_DETAILS`; routes `GET/POST payment-methods`, `PATCH/DELETE payment-methods/{method_id}`, `GET/PUT portal-settings`. `create_portal_investor_router() -> APIRouter` with `RATE_LIMITED = "too many requests; try again later"` and route `GET investor/payment-methods`. Both mounted in `main.py`.

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_portal_methods.py`:

```python
# api/tests/test_portal_methods.py
"""Payment methods: admin CRUD over every row, investors read the enabled
ones in display order; the per-org portal settings pair. Every mutation
audits, and desk roles below admin are refused on the admin routes."""
import psycopg
import pytest
from portal_helpers import add_method, csrf

CRYPTO = {"coin": "USDT", "network": "TRC20", "address": "TAddr123"}
BANK = {"bank_name": "ICICI Bank", "holder": "Desk Ltd", "account_number": "000401234543",
        "code": "ICIC0000004"}


def _member(db, org_id, user, role):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
            (org_id, user["id"], role))


def _post_method(client, org_id, **over):
    body = {"kind": "crypto", "label": "USDT on TRC20", "details": CRYPTO, **over}
    return client.post(f"/api/orgs/{org_id}/payment-methods", json=body, headers=csrf(client))


def _events(db, org_id, action):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload, actor_email FROM events WHERE org_id = %s "
            "AND payload->>'action' = %s ORDER BY id", (org_id, action)).fetchall()


# ------------------------------------------------------------ create + list


def test_admin_creates_a_method_and_both_lists_show_it(org_client, db):
    client, org_id, seed = org_client
    r = _post_method(client, org_id, min_amount="50", fee_pct="1.5",
                     instructions=" TRC20 only ", sort_order=2)
    assert r.status_code == 201
    body = r.json()
    assert body == {"id": body["id"], "kind": "crypto", "label": "USDT on TRC20",
                    "enabled": True, "currency": "USD", "details": CRYPTO,
                    "min_amount": 50.0, "fee_pct": 1.5, "instructions": "TRC20 only",
                    "sort_order": 2}
    assert client.get(f"/api/orgs/{org_id}/payment-methods").json() == [body]
    with psycopg.connect(db, autocommit=True) as conn:
        (created_by,) = conn.execute(
            "SELECT created_by FROM payment_methods WHERE id = %s", (body["id"],)).fetchone()
        (admin_id,) = conn.execute(
            "SELECT id FROM users WHERE email = 'admin@example.com'").fetchone()
    assert created_by == admin_id
    rows = _events(db, org_id, "payment_method_changed")
    assert len(rows) == 1
    severity, payload, actor = rows[0]
    assert severity == "warning" and actor == "admin@example.com"
    assert payload["method_id"] == body["id"] and payload["change"] == "created"
    assert payload["kind"] == "crypto" and payload["label"] == "USDT on TRC20"
    assert payload["summary"] == "Payment method created: USDT on TRC20 (crypto) by admin@example.com"


def test_a_bank_method_keeps_its_optional_fields_and_drops_unknown_ones(org_client):
    client, org_id, seed = org_client
    r = _post_method(client, org_id, kind="bank", label="ICICI Bank",
                     details={**BANK, "bank_address": " Mumbai ", "country": "IN",
                              "swift_secret": "dropped"})
    assert r.status_code == 201
    assert r.json()["details"] == {**BANK, "bank_address": "Mumbai", "country": "IN"}
    assert r.json()["min_amount"] == 0.0 and r.json()["fee_pct"] == 0.0


@pytest.mark.parametrize("over, needle", [
    ({"kind": "cash"}, "kind must be crypto or bank"),
    ({"details": {"coin": "USDT", "network": "TRC20"}}, "address is required"),
    ({"kind": "bank", "details": {"bank_name": "ICICI"}}, "holder is required"),
    ({"fee_pct": "100"}, "fee_pct must be between 0 and 99.999"),
    ({"fee_pct": "-1"}, "fee_pct must be between 0 and 99.999"),
    ({"fee_pct": "1.2345"}, "fee_pct must be between 0 and 99.999"),
    ({"fee_pct": "abc"}, "fee_pct must be between 0 and 99.999"),
    ({"min_amount": "-5"}, "min_amount must be greater than 0"),
    ({"min_amount": "5.001"}, "min_amount may have at most two decimals"),
    ({"label": "  "}, "label is required"),
])
def test_a_bad_method_is_refused_with_the_reason(org_client, over, needle):
    client, org_id, seed = org_client
    r = _post_method(client, org_id, **over)
    assert r.status_code == 400 and r.json()["detail"] == needle, r.text


def test_investors_see_only_enabled_methods_in_display_order(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    second = _post_method(client, org_id, label="B second", sort_order=2).json()["id"]
    first = _post_method(client, org_id, label="A first", sort_order=1).json()["id"]
    off = _post_method(client, org_id, kind="bank", label="Bank off", details=BANK).json()["id"]
    r = client.patch(f"/api/orgs/{org_id}/payment-methods/{off}", json={"enabled": False},
                     headers=csrf(client))
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert [m["id"] for m in client.get(f"/api/orgs/{org_id}/payment-methods").json()] \
        == [off, first, second]

    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    login_as(client, investor)
    mine = client.get(f"/api/orgs/{org_id}/investor/payment-methods").json()
    assert [m["id"] for m in mine] == [first, second]
    assert mine[0]["details"] == CRYPTO and mine[0]["enabled"] is True
    assert client.get(f"/api/orgs/{org_id}/payment-methods").status_code == 403
    assert _post_method(client, org_id).status_code == 403


# ------------------------------------------------------------ patch


def test_patch_replaces_details_and_validates_against_the_rows_kind(org_client, make_org, db):
    client, org_id, seed = org_client
    method_id = _post_method(client, org_id, instructions="old").json()["id"]
    url = f"/api/orgs/{org_id}/payment-methods/{method_id}"
    r = client.patch(url, json={"details": {"coin": "USDC", "network": "ERC20"}},
                     headers=csrf(client))
    assert r.status_code == 400 and r.json()["detail"] == "address is required"
    r = client.patch(url, json={"details": {"coin": "USDC", "network": "ERC20",
                                            "address": "0xabc", "memo": " tag "},
                                "label": "USDC on ERC20", "min_amount": "0", "fee_pct": "0.5",
                                "instructions": "", "sort_order": 9}, headers=csrf(client))
    assert r.status_code == 200
    assert r.json()["details"] == {"coin": "USDC", "network": "ERC20", "address": "0xabc",
                                   "memo": "tag"}
    assert r.json()["label"] == "USDC on ERC20" and r.json()["instructions"] is None
    assert r.json()["min_amount"] == 0.0 and r.json()["fee_pct"] == 0.5
    assert r.json()["sort_order"] == 9 and r.json()["kind"] == "crypto"
    r = client.patch(url, json={}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["label"] == "USDC on ERC20"
    r = client.patch(url, json={"fee_pct": "100"}, headers=csrf(client))
    assert r.status_code == 400
    rows = _events(db, org_id, "payment_method_changed")
    assert [p["change"] for _, p, _ in rows] == ["created", "updated"]

    other_org = make_org(name="Other")
    foreign = add_method(db, other_org, kind="crypto", label="Foreign", details=CRYPTO)
    r = client.patch(f"/api/orgs/{org_id}/payment-methods/{foreign}", json={"label": "x"},
                     headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Payment method not found"
    assert client.patch(f"/api/orgs/{org_id}/payment-methods/999", json={"label": "x"},
                        headers=csrf(client)).status_code == 404


# ------------------------------------------------------------ delete


def test_delete_is_refused_while_a_pending_deposit_uses_the_method(org_client, make_user, db):
    client, org_id, seed = org_client
    method_id = _post_method(client, org_id).json()["id"]
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    with psycopg.connect(db, autocommit=True) as conn:
        (dep_id,) = conn.execute(
            "INSERT INTO deposits (org_id, user_id, method_id, method_kind, method_label, "
            "amount, reference) VALUES (%s, %s, %s, 'crypto', 'USDT on TRC20', 100, 'tx-1') "
            "RETURNING id", (org_id, investor["id"], method_id)).fetchone()
    url = f"/api/orgs/{org_id}/payment-methods/{method_id}"
    r = client.delete(url, headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "a pending deposit still uses this method"
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE deposits SET status = 'rejected' WHERE id = %s", (dep_id,))
    r = client.delete(url, headers=csrf(client))
    assert r.status_code == 204
    assert client.get(f"/api/orgs/{org_id}/payment-methods").json() == []
    with psycopg.connect(db, autocommit=True) as conn:
        assert conn.execute("SELECT method_id, method_label FROM deposits WHERE id = %s",
                            (dep_id,)).fetchone() == (None, "USDT on TRC20")
    assert client.delete(url, headers=csrf(client)).status_code == 404
    rows = _events(db, org_id, "payment_method_changed")
    assert rows[-1][1]["change"] == "deleted" and rows[-1][1]["method_id"] == method_id


# ------------------------------------------------------------ settings


def test_portal_settings_default_to_zero_and_update_with_audit(org_client, db):
    client, org_id, seed = org_client
    url = f"/api/orgs/{org_id}/portal-settings"
    assert client.get(url).json() == {"withdrawal_min": 0.0, "withdrawal_fee_pct": 0.0}
    with psycopg.connect(db, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM portal_settings WHERE org_id = %s",
                            (org_id,)).fetchone() == (1,)
    r = client.put(url, json={"withdrawal_min": "50", "withdrawal_fee_pct": "2.5"},
                   headers=csrf(client))
    assert r.status_code == 200 and r.json() == {"withdrawal_min": 50.0, "withdrawal_fee_pct": 2.5}
    assert client.get(url).json() == {"withdrawal_min": 50.0, "withdrawal_fee_pct": 2.5}
    r = client.put(url, json={"withdrawal_min": "1.234", "withdrawal_fee_pct": "0"},
                   headers=csrf(client))
    assert r.status_code == 400 and r.json()["detail"] == "withdrawal_min may have at most two decimals"
    r = client.put(url, json={"withdrawal_min": "0", "withdrawal_fee_pct": "100"},
                   headers=csrf(client))
    assert r.status_code == 400 and r.json()["detail"] == "withdrawal_fee_pct must be between 0 and 99.999"
    rows = _events(db, org_id, "portal_settings_changed")
    assert len(rows) == 1 and rows[0][0] == "info"
    payload = rows[0][1]
    assert payload["withdrawal_min"] == 50.0 and payload["withdrawal_fee_pct"] == 2.5
    assert payload["previous"] == {"withdrawal_min": 0.0, "withdrawal_fee_pct": 0.0}
    with psycopg.connect(db, autocommit=True) as conn:
        (updated_by,) = conn.execute(
            "SELECT updated_by FROM portal_settings WHERE org_id = %s", (org_id,)).fetchone()
        (admin_id,) = conn.execute(
            "SELECT id FROM users WHERE email = 'admin@example.com'").fetchone()
    assert updated_by == admin_id


# ------------------------------------------------------------ roles


@pytest.mark.parametrize("role", ["viewer", "investor"])
def test_roles_below_admin_are_refused_on_every_admin_route(org_client, make_user, login_as, db, role):
    client, org_id, seed = org_client
    method_id = _post_method(client, org_id).json()["id"]
    user = make_user(email=f"{role}@example.com")
    _member(db, org_id, user, role)
    login_as(client, user)
    for method, tail, body in [
        ("GET", "payment-methods", None),
        ("POST", "payment-methods", {"kind": "crypto", "label": "x", "details": CRYPTO}),
        ("PATCH", f"payment-methods/{method_id}", {"enabled": False}),
        ("DELETE", f"payment-methods/{method_id}", None),
        ("GET", "portal-settings", None),
        ("PUT", "portal-settings", {"withdrawal_min": "0", "withdrawal_fee_pct": "0"}),
    ]:
        kwargs = {"headers": csrf(client)}
        if body is not None:
            kwargs["json"] = body
        r = client.request(method, f"/api/orgs/{org_id}/{tail}", **kwargs)
        assert r.status_code == 403, f"{role} {method} {tail} -> {r.status_code}"
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv/Scripts/python -m pytest tests/test_portal_methods.py -q -p no:cacheprovider
```

Expected: FAIL. Every test that calls a portal route gets 404 `Not Found` where it expects 2xx/400/403 (the routes do not exist yet).

- [ ] **Step 3: Create the investor router**

Create `api/src/api/routes/portal_investor.py`:

```python
# api/src/api/routes/portal_investor.py
"""The client portal, investor side (phase 1).

One router under /api/orgs/{org_id}. Every route resolves the caller's OWN
rows through ctx.user_id and never takes a user id from the request, so an
investor cannot name anyone else's money. Grown over Tasks 6-10 of the
phase-1 plan: payment methods here; deposits, payout destinations,
withdrawals, transfers, wallet entries and the summary follow.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

import psycopg
from fastapi import APIRouter, Depends

from ..db import get_conn
from ..rbac import OrgContext, require_org_role
from .. import portal_common as pc

logger = logging.getLogger(__name__)

REQUESTS_PER_HOUR = 10
RATE_LIMITED = "too many requests; try again later"


def create_portal_investor_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-investor"])

    @router.get("/investor/payment-methods", response_model=List[Dict[str, Any]])
    async def my_payment_methods(ctx: OrgContext = Depends(require_org_role("investor")),
                                 conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        """Where this workspace receives money: enabled methods only, in
        the admin's display order."""
        rows = conn.execute(
            f"SELECT {pc.METHOD_COLS} FROM payment_methods WHERE org_id = %s AND enabled "
            "ORDER BY sort_order, id", (ctx.org_id,)).fetchall()
        return [pc.method_json(r, public=True) for r in rows]

    return router
```

- [ ] **Step 4: Create the admin router**

Create `api/src/api/routes/portal_admin.py`:

```python
# api/src/api/routes/portal_admin.py
"""The client portal, admin side (phase 1).

One router under /api/orgs/{org_id}, every route require_org_role("admin").
Payment methods (where the org receives money) and the portal settings pair
live here from Task 6; the Requests desk (deposits, withdrawals, transfers,
payout destinations), the investors list, ledger reads and adjustments are
added by Tasks 7-10.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..db import get_conn
from ..rbac import OrgContext, require_org_role
from .. import portal_common as pc

logger = logging.getLogger(__name__)

METHOD_KINDS = ("crypto", "bank")
# The detail keys each kind must carry (400 "<field> is required" when
# missing) and the ones it may carry. Anything else is dropped so the JSON
# column only ever holds what the dashboard renders.
REQUIRED_DETAILS = {"crypto": ("coin", "network", "address"),
                    "bank": ("bank_name", "holder", "account_number", "code")}
OPTIONAL_DETAILS = {"crypto": ("memo",), "bank": ("bank_address", "country")}


class MethodBody(BaseModel):
    kind: str
    label: str
    currency: str = "USD"
    details: dict
    min_amount: Any = "0"
    fee_pct: Any = "0"
    instructions: Optional[str] = None
    sort_order: int = 0


class MethodPatch(BaseModel):
    label: Optional[str] = None
    currency: Optional[str] = None
    details: Optional[dict] = None
    min_amount: Any = None
    fee_pct: Any = None
    instructions: Optional[str] = None   # "" clears it
    sort_order: Optional[int] = None
    enabled: Optional[bool] = None


class SettingsBody(BaseModel):
    withdrawal_min: Any
    withdrawal_fee_pct: Any


def clean_details(kind: str, raw: Any) -> dict:
    """Trimmed details for `kind`: every required key present and non-blank
    (else LedgerError "<field> is required"), optional keys kept when
    given, unknown keys dropped."""
    if not isinstance(raw, dict):
        raise pc.LedgerError(f"{REQUIRED_DETAILS[kind][0]} is required")
    out: dict = {}
    for key in REQUIRED_DETAILS[kind]:
        out[key] = pc.clean_text(raw.get(key), key, max_len=128)
    for key in OPTIONAL_DETAILS[kind]:
        value = pc.clean_text(raw.get(key), key, max_len=256, required=False)
        if value is not None:
            out[key] = value
    return out


def parse_min(raw: object, field: str) -> Decimal:
    """A minimum may be zero (meaning none); above zero it follows
    parse_amount and its messages."""
    if raw is None or raw == "":
        return Decimal("0")
    try:
        if not isinstance(raw, bool) and Decimal(str(raw)) == 0:
            return Decimal("0")
    except (InvalidOperation, ValueError):
        pass
    return pc.parse_amount(raw, field)


def parse_pct(raw: object, field: str) -> Decimal:
    """0 <= pct < 100 with at most three decimals (NUMERIC(6,3))."""
    message = f"{field} must be between 0 and 99.999"
    if isinstance(raw, bool) or raw is None or raw == "":
        raise pc.LedgerError(message)
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise pc.LedgerError(message)
    if not value.is_finite() or value < 0 or value >= 100:
        raise pc.LedgerError(message)
    if value != value.quantize(Decimal("0.001")):
        raise pc.LedgerError(message)
    return value


def create_portal_admin_router() -> APIRouter:
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-admin"])

    def _method_row(conn: psycopg.Connection, org_id: int, method_id: int):
        row = conn.execute(
            f"SELECT {pc.METHOD_COLS} FROM payment_methods WHERE id = %s AND org_id = %s",
            (method_id, org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Payment method not found")
        return row

    async def _audit_method(conn: psycopg.Connection, ctx: OrgContext, method_id: int,
                            change: str, kind: str, label: str) -> None:
        # A warning, not an info: this is where every investor is told to
        # send money, and an attacker with an admin session would change
        # exactly this. Both alerters are wired to warnings.
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="payment_method_changed",
            actor_email=ctx.user_email, user_id=ctx.user_id, severity="warning",
            method_id=method_id, change=change, kind=kind, label=label,
            summary=f"Payment method {change}: {label} ({kind}) by {ctx.user_email}")

    # ------------------------------------------------------------ payment methods

    @router.get("/payment-methods", response_model=List[Dict[str, Any]])
    async def list_methods(ctx: OrgContext = Depends(require_org_role("admin")),
                           conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {pc.METHOD_COLS} FROM payment_methods WHERE org_id = %s "
            "ORDER BY sort_order, id", (ctx.org_id,)).fetchall()
        return [pc.method_json(r, public=False) for r in rows]

    @router.post("/payment-methods", status_code=201, response_model=Dict[str, Any])
    async def create_method(body: MethodBody,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        kind = body.kind.strip().lower()
        if kind not in METHOD_KINDS:
            raise HTTPException(status_code=400, detail="kind must be crypto or bank")
        try:
            label = pc.clean_text(body.label, "label", max_len=64)
            currency = (pc.clean_text(body.currency, "currency", max_len=8,
                                      required=False) or "USD").upper()
            details = clean_details(kind, body.details)
            min_amount = parse_min(body.min_amount, "min_amount")
            fee_pct = parse_pct(body.fee_pct, "fee_pct")
            instructions = pc.clean_text(body.instructions, "instructions", max_len=1000,
                                         required=False)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        row = conn.execute(
            "INSERT INTO payment_methods (org_id, kind, label, currency, details, min_amount, "
            "fee_pct, instructions, sort_order, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            f"RETURNING {pc.METHOD_COLS}",
            (ctx.org_id, kind, label, currency, Jsonb(details), min_amount, fee_pct,
             instructions, body.sort_order, ctx.user_id)).fetchone()
        out = pc.method_json(row, public=False)
        await _audit_method(conn, ctx, out["id"], "created", kind, label)
        return out

    @router.patch("/payment-methods/{method_id}", response_model=Dict[str, Any])
    async def update_method(method_id: int, body: MethodPatch,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        current = _method_row(conn, ctx.org_id, method_id)
        kind = current[1]
        sets: list[str] = []
        params: list[Any] = []
        try:
            if body.label is not None:
                sets.append("label = %s")
                params.append(pc.clean_text(body.label, "label", max_len=64))
            if body.currency is not None:
                sets.append("currency = %s")
                params.append((pc.clean_text(body.currency, "currency", max_len=8,
                                             required=False) or "USD").upper())
            if body.details is not None:
                sets.append("details = %s")
                params.append(Jsonb(clean_details(kind, body.details)))
            if body.min_amount is not None:
                sets.append("min_amount = %s")
                params.append(parse_min(body.min_amount, "min_amount"))
            if body.fee_pct is not None:
                sets.append("fee_pct = %s")
                params.append(parse_pct(body.fee_pct, "fee_pct"))
            if body.instructions is not None:
                sets.append("instructions = %s")
                params.append(pc.clean_text(body.instructions, "instructions", max_len=1000,
                                            required=False))
            if body.sort_order is not None:
                sets.append("sort_order = %s")
                params.append(int(body.sort_order))
            if body.enabled is not None:
                sets.append("enabled = %s")
                params.append(bool(body.enabled))
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if not sets:
            return pc.method_json(current, public=False)
        sets.append("updated_at = now()")
        row = conn.execute(
            f"UPDATE payment_methods SET {', '.join(sets)} WHERE id = %s AND org_id = %s "
            f"RETURNING {pc.METHOD_COLS}", (*params, method_id, ctx.org_id)).fetchone()
        out = pc.method_json(row, public=False)
        await _audit_method(conn, ctx, method_id, "updated", kind, out["label"])
        return out

    @router.delete("/payment-methods/{method_id}", status_code=204)
    async def delete_method(method_id: int,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)):
        current = _method_row(conn, ctx.org_id, method_id)
        pending = conn.execute(
            "SELECT 1 FROM deposits WHERE method_id = %s AND status = 'pending'",
            (method_id,)).fetchone()
        if pending:
            # The admin still has to decide that notice against the method's
            # details. Decided rows keep their snapshot (method_kind,
            # method_label) and lose only the id (ON DELETE SET NULL).
            raise HTTPException(status_code=409, detail="a pending deposit still uses this method")
        conn.execute("DELETE FROM payment_methods WHERE id = %s AND org_id = %s",
                     (method_id, ctx.org_id))
        await _audit_method(conn, ctx, method_id, "deleted", current[1], current[2])
        return Response(status_code=204)

    # ------------------------------------------------------------ portal settings

    def _settings_json(settings: dict) -> Dict[str, Any]:
        return {"withdrawal_min": pc.money(settings["withdrawal_min"]),
                "withdrawal_fee_pct": float(settings["withdrawal_fee_pct"])}

    @router.get("/portal-settings", response_model=Dict[str, Any])
    async def get_settings(ctx: OrgContext = Depends(require_org_role("admin")),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        return _settings_json(pc.portal_settings(conn, ctx.org_id))

    @router.put("/portal-settings", response_model=Dict[str, Any])
    async def put_settings(body: SettingsBody,
                           ctx: OrgContext = Depends(require_org_role("admin")),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            withdrawal_min = parse_min(body.withdrawal_min, "withdrawal_min")
            fee_pct = parse_pct(body.withdrawal_fee_pct, "withdrawal_fee_pct")
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        previous = _settings_json(pc.portal_settings(conn, ctx.org_id))
        conn.execute(
            "UPDATE portal_settings SET withdrawal_min = %s, withdrawal_fee_pct = %s, "
            "updated_by = %s, updated_at = now() WHERE org_id = %s",
            (withdrawal_min, fee_pct, ctx.user_id, ctx.org_id))
        out = {"withdrawal_min": pc.money(withdrawal_min), "withdrawal_fee_pct": float(fee_pct)}
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="portal_settings_changed",
            actor_email=ctx.user_email, user_id=ctx.user_id, previous=previous, **out,
            summary=f"Withdrawal rules set to min {withdrawal_min:.2f} USD, fee {fee_pct}% "
                    f"by {ctx.user_email}")
        return out

    return router
```

- [ ] **Step 5: Mount both routers in `main.py`**

In `api/src/api/main.py`, the current block reads:

```python
    # Investor portal: a sub-viewer role that sees only its own linked
    # account, plus the admin queues that decide its deposits/withdrawals.
    from .routes.investor import create_investor_admin_router, create_investor_router
    app.include_router(create_investor_router())
    app.include_router(create_investor_admin_router())
```

Replace it with:

```python
    # Client portal (phase 1): wallets, payment methods, deposits, payout
    # destinations, withdrawals, transfers and the Requests desk. Mounted
    # BEFORE the 2026-09-23 investor routers on purpose: until Task 10 of
    # the phase-1 plan deletes those, a path both declare (investor/deposits,
    # investors, ...) must resolve to the new handler -- FastAPI matches the
    # first route registered.
    from .routes.portal_admin import create_portal_admin_router
    from .routes.portal_investor import create_portal_investor_router
    app.include_router(create_portal_investor_router())
    app.include_router(create_portal_admin_router())

    # Investor portal: a sub-viewer role that sees only its own linked
    # account, plus the admin queues that decide its deposits/withdrawals.
    from .routes.investor import create_investor_admin_router, create_investor_router
    app.include_router(create_investor_router())
    app.include_router(create_investor_admin_router())
```

(If Task 4 already placed `app.include_router(create_portal_files_router())` in this area, keep it; only the relative order of the new portal routers and the old investor routers matters.)

- [ ] **Step 6: Run the tests to verify they pass**

```bash
.venv/Scripts/python -m pytest tests/test_portal_methods.py tests/test_portal_common.py -q -p no:cacheprovider
```

Expected: PASS, all green (18 in `test_portal_methods.py`: 6 plain tests, 10 parametrised refusals, 2 parametrised role cases, plus Task 5's 13).

- [ ] **Step 7: Run the whole API suite**

```bash
.venv/Scripts/python -m pytest tests -q -p no:cacheprovider
```

Expected: green apart from the 7 pre-existing `test_events_ws.py` errors and the one EA-download CRLF failure.

- [ ] **Step 8: Commit**

```bash
git add api/src/api/routes/portal_admin.py api/src/api/routes/portal_investor.py api/src/api/main.py api/tests/test_portal_methods.py
git commit -m "feat(api): payment methods and portal settings routes; the client portal routers are mounted

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 7: Deposits routes

The investor files a deposit notice against a payment method (kind and label snapshotted, fee computed from the method's `fee_pct`, receipt required for bank, one live reference per workspace, optional trading-account target), lists and cancels their own; the admin lists the queue and confirms (crediting `main` exactly once, editable credited amount, and an approved `main -> account` transfer when the notice targets the trading account) or rejects with a note. Every move audits; decisions email the investor.

**Files:**
- Modify: `api/src/api/routes/portal_investor.py` (imports, `DepositNotice`, the hourly limiter, three routes)
- Modify: `api/src/api/routes/portal_admin.py` (imports, `DepositDecision`, two routes)
- Test: `api/tests/test_portal_deposits.py`

**Interfaces:**
- Consumes: `portal_common` (`DEPOSIT_COLS`, `METHOD_COLS`, `deposit_json`, `qualify`, `parse_amount`, `clean_text`, `fee_for`, `can_transition`, `linked_account`, `settle`, `wallet_figures`, `audit_control`, `notify_investor`, `require_note_on_reject`, `LedgerError`) (Task 5); `routes.portal_files.file_belongs` (Task 4); `LoginRateLimiter` (`api.auth`); tables `deposits`, `transfers`, `wallet_entries`, `files` (Task 1); test helpers `portal_helpers.csrf`, `add_method`, `link`, `seed_file` (Task 1).
- Produces: routes `GET investor/deposits`, `POST investor/deposits` (201), `POST investor/deposits/{deposit_id}/cancel`, `GET deposits?status=`, `POST deposits/{deposit_id}/decision`; `DepositNotice`, `DepositDecision`; the investor router's `hourly = LoginRateLimiter(max_attempts=REQUESTS_PER_HOUR, window_s=3600)` that Tasks 8 and 9 reuse with keys `portal-withdrawal:{org}:{user}`, `portal-transfer:{org}:{user}`, `portal-destination:{org}:{user}`.

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_portal_deposits.py`:

```python
# api/tests/test_portal_deposits.py
"""Deposits end to end: the investor files a notice against a payment
method, may cancel it while pending, and an admin confirms (crediting the
wallet exactly once, and parking the money in an approved transfer when the
notice targets the trading account) or rejects with a note."""
from decimal import Decimal

import psycopg
import pytest
from portal_helpers import add_method, csrf, link, seed_file

from api import portal_common as pc
from api import ws as ws_module

CRYPTO = {"coin": "USDT", "network": "TRC20", "address": "TAddr123"}
BANK = {"bank_name": "ICICI Bank", "holder": "Desk Ltd", "account_number": "000401234543",
        "code": "ICIC0000004"}
ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}


def _member(db, org_id, user, role):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
            (org_id, user["id"], role))


def _investor(org_client, make_user, login_as, db, *, link_to=None, **method):
    """An investor member logged in, with one enabled crypto method charging
    1 % unless overridden. Returns (client, org_id, investor, method_id)."""
    client, org_id, seed = org_client
    opts = {"kind": "crypto", "label": "USDT on TRC20", "details": CRYPTO, "fee_pct": "1",
            **method}
    method_id = add_method(db, org_id, **opts)
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    if link_to is not None:
        seed(link_to, role="slave")
        link(db, org_id, investor["id"], link_to)
    login_as(client, investor)
    return client, org_id, investor, method_id


def _notice(client, org_id, method_id, **over):
    body = {"method_id": method_id, "amount": "5000", "reference": "chain-tx-1", **over}
    return client.post(f"/api/orgs/{org_id}/investor/deposits", json=body, headers=csrf(client))


def _decide(client, org_id, deposit_id, **body):
    return client.post(f"/api/orgs/{org_id}/deposits/{deposit_id}/decision", json=body,
                       headers=csrf(client))


def _events(db, org_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload, actor_email, account_id FROM events WHERE org_id = %s "
            "ORDER BY id", (org_id,)).fetchall()


def _entries(db, org_id, user_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT wallet, amount, kind, ref_table, ref_id, created_by FROM wallet_entries "
            "WHERE org_id = %s AND user_id = %s ORDER BY id", (org_id, user_id)).fetchall()


def _figures(db, org_id, user_id, wallet="main"):
    with psycopg.connect(db, autocommit=True) as conn:
        return pc.wallet_figures(conn, org_id, user_id)[wallet]


def _admin_id(db):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute("SELECT id FROM users WHERE email = 'admin@example.com'").fetchone()[0]


# ------------------------------------------------------------ filing


def test_an_investor_files_a_notice_with_the_method_snapshot_and_fee(
        org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    r = _notice(client, org_id, method_id, reference=" chain-tx-1 ", note="sent from Binance")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["amount"] == 5000.0 and body["fee"] == 50.0 and body["credited_amount"] is None
    assert body["method_id"] == method_id and body["method_kind"] == "crypto"
    assert body["method_label"] == "USDT on TRC20" and body["currency"] == "USD"
    assert body["reference"] == "chain-tx-1" and body["status"] == "pending"
    assert body["target"] == "wallet" and body["target_account_id"] is None
    assert body["receipt_file_id"] is None and body["note"] == "sent from Binance"
    assert body["decided_by"] is None and body["decision_note"] is None
    assert "email" not in body
    assert client.get(f"/api/orgs/{org_id}/investor/deposits").json() == [body]
    severity, payload, actor, account_id = _events(db, org_id)[-1]
    assert severity == "warning" and payload["action"] == "investor_deposit_noticed"
    assert payload["user_id"] == investor["id"] and payload["deposit_id"] == body["id"]
    assert payload["amount"] == 5000.0 and payload["fee"] == 50.0
    assert payload["summary"] == "Deposit notice: 5000.00 USD via USDT on TRC20 from inv@example.com"
    assert actor == "inv@example.com" and account_id is None
    assert _entries(db, org_id, investor["id"]) == [], "a notice moves no money"


def test_notices_come_back_newest_first(org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    first = _notice(client, org_id, method_id, reference="a").json()["id"]
    second = _notice(client, org_id, method_id, reference="b").json()["id"]
    assert [d["id"] for d in client.get(f"/api/orgs/{org_id}/investor/deposits").json()] \
        == [second, first]


@pytest.mark.parametrize("over, status, detail", [
    ({"amount": "-5"}, 400, "amount must be greater than 0"),
    ({"amount": "5.001"}, 400, "amount may have at most two decimals"),
    ({"reference": "  "}, 400, "reference is required"),
    ({"target": "bank"}, 400, "target must be wallet or account"),
    ({"method_id": 999}, 404, "Payment method not found"),
    ({"target": "account"}, 409, "no account linked yet"),
])
def test_a_bad_notice_is_refused_with_the_reason(org_client, make_user, login_as, db,
                                                 over, status, detail):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    r = _notice(client, org_id, method_id, **over)
    assert r.status_code == status and r.json()["detail"] == detail, r.text


def test_the_method_minimum_is_enforced_with_the_figure(org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db,
                                                    min_amount="500")
    r = _notice(client, org_id, method_id, amount="499.99")
    assert r.status_code == 400
    assert r.json()["detail"] == "minimum deposit for this method is 500.00"
    assert _notice(client, org_id, method_id, amount="500").status_code == 201


def test_a_disabled_or_foreign_method_is_not_found(org_client, make_user, login_as, make_org, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    off = add_method(db, org_id, kind="crypto", label="Off", details=CRYPTO, enabled=False)
    foreign = add_method(db, make_org(name="Other"), kind="crypto", label="Foreign", details=CRYPTO)
    for bad in (off, foreign):
        r = _notice(client, org_id, bad)
        assert r.status_code == 404 and r.json()["detail"] == "Payment method not found"


def test_bank_deposits_need_a_receipt_that_is_the_investors_own(
        org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db,
                                                    kind="bank", label="ICICI Bank",
                                                    details=BANK, fee_pct="0")
    r = _notice(client, org_id, method_id)
    assert r.status_code == 400 and r.json()["detail"] == "receipt is required for bank deposits"
    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    theirs = seed_file(db, org_id, other["id"])
    wrong_purpose = seed_file(db, org_id, investor["id"], purpose="payout_proof")
    for bad in (theirs, wrong_purpose, 999):
        r = _notice(client, org_id, method_id, receipt_file_id=bad)
        assert r.status_code == 400 and r.json()["detail"] == "receipt file not found", bad
    mine = seed_file(db, org_id, investor["id"])
    r = _notice(client, org_id, method_id, receipt_file_id=mine)
    assert r.status_code == 201 and r.json()["receipt_file_id"] == mine
    assert r.json()["method_kind"] == "bank" and r.json()["fee"] == 0.0
    r = _notice(client, org_id, method_id, reference="chain-tx-2", receipt_file_id=mine)
    assert r.status_code == 400
    assert r.json()["detail"] == "receipt file is already attached to another notice"


def test_a_notice_may_target_the_linked_trading_account(org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db,
                                                    link_to=1001)
    r = _notice(client, org_id, method_id, target="account", target_account_id=1002)
    assert r.status_code == 404 and r.json()["detail"] == "Account not found"
    r = _notice(client, org_id, method_id, target="account")
    assert r.status_code == 201
    assert r.json()["target"] == "account" and r.json()["target_account_id"] == 1001
    _severity, payload, _actor, account_id = _events(db, org_id)[-1]
    assert payload["target"] == "account" and account_id == 1001


def test_one_live_reference_per_workspace(org_client, make_user, login_as, db):
    """Two notices quoting one transaction are the same money twice. A
    cancelled or rejected row frees the reference; a confirmed one keeps it."""
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    first = _notice(client, org_id, method_id)
    assert first.status_code == 201
    r = _notice(client, org_id, method_id, amount="4000", reference=" chain-tx-1 ")
    assert r.status_code == 409 and r.json()["detail"] == "A notice with this reference already exists"

    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    login_as(client, other)
    assert _notice(client, org_id, method_id).status_code == 409

    login_as(client, investor)
    r = client.post(f"/api/orgs/{org_id}/investor/deposits/{first.json()['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    second = _notice(client, org_id, method_id, amount="4000")
    assert second.status_code == 201 and second.json()["amount"] == 4000.0

    login_as(client, ADMIN)
    assert _decide(client, org_id, second.json()["id"], status="confirmed").status_code == 200
    login_as(client, investor)
    assert _notice(client, org_id, method_id).status_code == 409


def test_ten_notices_an_hour_then_429(org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    for i in range(10):
        assert _notice(client, org_id, method_id, amount="1", reference=f"t{i}").status_code == 201
    r = _notice(client, org_id, method_id, amount="1", reference="t10")
    assert r.status_code == 429 and r.json()["detail"] == "too many requests; try again later"


# ------------------------------------------------------------ cancel


def test_the_investor_cancels_a_pending_notice_only(org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    dep = _notice(client, org_id, method_id).json()
    url = f"/api/orgs/{org_id}/investor/deposits/{dep['id']}/cancel"

    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    login_as(client, other)
    r = client.post(url, headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Deposit not found"

    login_as(client, investor)
    r = client.post(url, headers=csrf(client))
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled" and r.json()["decided_at"] is not None
    assert r.json()["decided_by"] == investor["id"]
    r = client.post(url, headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "deposit is already cancelled"
    assert client.post(f"/api/orgs/{org_id}/investor/deposits/999/cancel",
                       headers=csrf(client)).status_code == 404

    severity, payload, actor, _ = _events(db, org_id)[-1]
    assert severity == "info" and payload["action"] == "investor_deposit_cancelled"
    assert payload["deposit_id"] == dep["id"] and payload["user_id"] == investor["id"]
    assert actor == "inv@example.com"

    login_as(client, ADMIN)
    r = _decide(client, org_id, dep["id"], status="confirmed")
    assert r.status_code == 409 and r.json()["detail"] == "deposit is already cancelled"
    assert _entries(db, org_id, investor["id"]) == []


# ------------------------------------------------------------ admin decisions


def test_admin_confirms_once_and_the_wallet_is_credited_exactly_once(
        org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    dep = _notice(client, org_id, method_id).json()
    login_as(client, ADMIN)
    queue = client.get(f"/api/orgs/{org_id}/deposits?status=pending").json()
    assert [d["id"] for d in queue] == [dep["id"]]
    assert queue[0]["email"] == "inv@example.com" and queue[0]["display_name"] == "User"
    assert queue[0]["fee"] == 50.0 and queue[0]["currency"] == "USD"

    r = _decide(client, org_id, dep["id"], status="confirmed")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "confirmed" and body["credited_amount"] == 4950.0
    assert body["decided_by"] == _admin_id(db) and body["decided_at"] is not None
    assert body["decision_note"] is None
    assert _entries(db, org_id, investor["id"]) == [
        ("main", Decimal("4950.00"), "deposit", "deposits", dep["id"], _admin_id(db))]
    assert _figures(db, org_id, investor["id"]) == {
        "balance": Decimal("4950.00"), "on_hold": Decimal("0"), "available": Decimal("4950.00")}

    # A replayed decision (double click, retried request) is refused and
    # the ledger still holds ONE entry.
    r = _decide(client, org_id, dep["id"], status="confirmed")
    assert r.status_code == 409 and r.json()["detail"] == "deposit is already confirmed"
    r = _decide(client, org_id, dep["id"], status="rejected", note="changed my mind")
    assert r.status_code == 409 and r.json()["detail"] == "deposit is already confirmed"
    assert len(_entries(db, org_id, investor["id"])) == 1

    events = _events(db, org_id)
    assert [e[1]["action"] for e in events[-2:]] == ["investor_deposit_noticed",
                                                     "investor_deposit_decided"]
    severity, payload, actor, _ = events[-1]
    assert severity == "info" and actor == "admin@example.com"
    assert payload["deposit_id"] == dep["id"] and payload["status"] == "confirmed"
    assert payload["credited_amount"] == 4950.0 and payload["user_id"] == investor["id"]
    assert payload["transfer_id"] is None
    assert _decide(client, org_id, 999, status="confirmed").status_code == 404


def test_admin_may_edit_the_credited_amount(org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    dep = _notice(client, org_id, method_id).json()
    login_as(client, ADMIN)
    r = _decide(client, org_id, dep["id"], status="confirmed", credited_amount="0")
    assert r.status_code == 400 and r.json()["detail"] == "credited_amount must be greater than 0"
    r = _decide(client, org_id, dep["id"], status="confirmed", credited_amount="4900.005")
    assert r.status_code == 400 and r.json()["detail"] == "credited_amount may have at most two decimals"
    r = _decide(client, org_id, dep["id"], status="confirmed", credited_amount="4900",
                note="fee was higher on chain")
    assert r.status_code == 200
    assert r.json()["credited_amount"] == 4900.0 and r.json()["amount"] == 5000.0
    assert r.json()["decision_note"] == "fee was higher on chain"
    assert _entries(db, org_id, investor["id"])[0][1] == Decimal("4900.00")


def test_a_rejection_needs_a_note_and_credits_nothing(org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    dep = _notice(client, org_id, method_id).json()
    login_as(client, ADMIN)
    r = _decide(client, org_id, dep["id"], status="rejected")
    assert r.status_code == 400 and r.json()["detail"] == "note is required"
    r = _decide(client, org_id, dep["id"], status="maybe")
    assert r.status_code == 400 and r.json()["detail"] == "status must be confirmed or rejected"
    r = _decide(client, org_id, dep["id"], status="rejected", note="no such transaction",
                credited_amount="4950")
    assert r.status_code == 200
    assert r.json()["status"] == "rejected" and r.json()["credited_amount"] is None
    assert r.json()["decision_note"] == "no such transaction"
    assert _entries(db, org_id, investor["id"]) == []
    assert _events(db, org_id)[-1][1]["status"] == "rejected"


def test_confirming_an_account_deposit_parks_the_money_in_an_approved_transfer(
        org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db,
                                                    link_to=1001)
    dep = _notice(client, org_id, method_id, target="account").json()
    login_as(client, ADMIN)
    r = _decide(client, org_id, dep["id"], status="confirmed")
    assert r.status_code == 200 and r.json()["credited_amount"] == 4950.0
    with psycopg.connect(db, autocommit=True) as conn:
        transfers = conn.execute(
            "SELECT source_kind, source_wallet, target_kind, target_account_id, amount, status, "
            "decided_by, decided_at IS NOT NULL, decision_note, user_id "
            "FROM transfers WHERE org_id = %s", (org_id,)).fetchall()
    assert transfers == [("wallet", "main", "account", 1001, Decimal("4950.00"), "approved",
                          _admin_id(db), True, f"funded from deposit #{dep['id']}",
                          investor["id"])]
    # Credited to main and held there until the admin funds the broker
    # account and marks the transfer done (Task 9).
    assert _figures(db, org_id, investor["id"]) == {
        "balance": Decimal("4950.00"), "on_hold": Decimal("4950.00"), "available": Decimal("0.00")}
    _severity, payload, _actor, account_id = _events(db, org_id)[-1]
    assert payload["action"] == "investor_deposit_decided" and account_id == 1001
    with psycopg.connect(db, autocommit=True) as conn:
        (transfer_id,) = conn.execute("SELECT id FROM transfers WHERE org_id = %s",
                                      (org_id,)).fetchone()
    assert payload["transfer_id"] == transfer_id
    # The replayed decision creates no second transfer either.
    assert _decide(client, org_id, dep["id"], status="confirmed").status_code == 409
    with psycopg.connect(db, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM transfers WHERE org_id = %s",
                            (org_id,)).fetchone() == (1,)


def test_an_account_deposit_whose_link_is_gone_is_credited_to_the_wallet(
        org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db,
                                                    link_to=1001)
    dep = _notice(client, org_id, method_id, target="account").json()
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE accounts SET investor_user_id = NULL WHERE ctid_trader_account_id = 1001")
    login_as(client, ADMIN)
    r = _decide(client, org_id, dep["id"], status="confirmed", note="seen on chain")
    assert r.status_code == 200
    assert r.json()["decision_note"] == "seen on chain (no account linked; credited to wallet)"
    assert r.json()["target"] == "account" and r.json()["target_account_id"] == 1001
    with psycopg.connect(db, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM transfers WHERE org_id = %s",
                            (org_id,)).fetchone() == (0,)
    assert _figures(db, org_id, investor["id"]) == {
        "balance": Decimal("4950.00"), "on_hold": Decimal("0"), "available": Decimal("4950.00")}


def test_the_queue_lists_open_notices_first_and_refuses_investors(
        org_client, make_user, login_as, db):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    older = _notice(client, org_id, method_id, reference="a").json()["id"]
    newer = _notice(client, org_id, method_id, reference="b").json()["id"]
    login_as(client, ADMIN)
    assert _decide(client, org_id, newer, status="confirmed").status_code == 200
    queue = client.get(f"/api/orgs/{org_id}/deposits").json()
    assert [(d["id"], d["status"]) for d in queue] == [(older, "pending"), (newer, "confirmed")]
    assert [d["id"] for d in client.get(f"/api/orgs/{org_id}/deposits?status=confirmed").json()] \
        == [newer]
    assert client.get(f"/api/orgs/{org_id}/deposits?status=rejected").json() == []

    login_as(client, investor)
    assert client.get(f"/api/orgs/{org_id}/deposits").status_code == 403
    assert _decide(client, org_id, older, status="confirmed").status_code == 403
    viewer = make_user(email="viewer@example.com")
    _member(db, org_id, viewer, "viewer")
    login_as(client, viewer)
    assert client.get(f"/api/orgs/{org_id}/deposits").status_code == 403
    assert _decide(client, org_id, older, status="confirmed").status_code == 403


# ------------------------------------------------------------ email


class _FakeAlerter:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_to(self, to_addr, subject, text):
        if self.fail:
            raise RuntimeError("resend down")
        self.sent.append((to_addr, subject, text))
        return True


def test_a_decision_emails_the_investor_and_a_failed_email_never_fails_the_request(
        org_client, make_user, login_as, db, monkeypatch):
    client, org_id, investor, method_id = _investor(org_client, make_user, login_as, db)
    dep = _notice(client, org_id, method_id, amount="250", reference="a").json()
    dep2 = _notice(client, org_id, method_id, amount="6", reference="b").json()
    login_as(client, ADMIN)
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    assert _decide(client, org_id, dep["id"], status="confirmed").status_code == 200
    assert [(to, subject) for to, subject, _ in fake.sent] == [
        ("inv@example.com", "Your deposit of 250.00 USD was confirmed")]
    assert "Credited: 247.50 USD" in fake.sent[0][2]
    monkeypatch.setattr(ws_module.broadcaster, "alerter", _FakeAlerter(fail=True), raising=False)
    r = _decide(client, org_id, dep2["id"], status="rejected", note="no")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.venv/Scripts/python -m pytest tests/test_portal_deposits.py -q -p no:cacheprovider
```

Expected: FAIL. `POST investor/deposits` is still answered by the retired 2026-09-23 router (its table is gone, so the TestClient re-raises `psycopg.errors.UndefinedTable: relation "org_investor_wallets" does not exist`), and the admin `deposits` routes answer 404.

- [ ] **Step 3: Add the investor deposit routes**

In `api/src/api/routes/portal_investor.py`, replace the import block:

```python
import logging
from typing import Any, Dict, List

import psycopg
from fastapi import APIRouter, Depends

from ..db import get_conn
from ..rbac import OrgContext, require_org_role
from .. import portal_common as pc
```

with:

```python
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import LoginRateLimiter
from ..db import get_conn
from ..rbac import OrgContext, require_org_role
from .. import portal_common as pc
from .portal_files import file_belongs
```

Directly above `def create_portal_investor_router() -> APIRouter:` add the body model:

```python
class DepositNotice(BaseModel):
    method_id: int
    amount: Any
    reference: str
    receipt_file_id: Optional[int] = None
    target: str = "wallet"
    target_account_id: Optional[int] = None
    note: Optional[str] = None


```

Replace the first line of the factory body:

```python
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-investor"])
```

with:

```python
    router = APIRouter(prefix="/api/orgs/{org_id}", tags=["portal-investor"])
    # Ten money requests of each kind per investor per hour, keyed
    # portal-<kind>:<org>:<user>. A separate instance because the shared
    # login limiter's window is one minute.
    hourly = LoginRateLimiter(max_attempts=REQUESTS_PER_HOUR, window_s=3600)
```

Then insert the three routes immediately above the final `    return router` line of `create_portal_investor_router`:

```python
    # ------------------------------------------------------------ deposits

    @router.get("/investor/deposits", response_model=List[Dict[str, Any]])
    async def my_deposits(ctx: OrgContext = Depends(require_org_role("investor")),
                          conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {pc.DEPOSIT_COLS} FROM deposits WHERE org_id = %s AND user_id = %s "
            "ORDER BY created_at DESC, id DESC", (ctx.org_id, ctx.user_id)).fetchall()
        return [pc.deposit_json(r) for r in rows]

    @router.post("/investor/deposits", status_code=201, response_model=Dict[str, Any])
    async def file_deposit(body: DepositNotice,
                           ctx: OrgContext = Depends(require_org_role("investor")),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """'I have sent it': a notice against one enabled payment method.
        The method's kind and label are snapshotted and its fee_pct applied
        now, so a later edit of the method never rewrites history. Nothing
        moves until an admin confirms."""
        try:
            amount = pc.parse_amount(body.amount)
            reference = pc.clean_text(body.reference, "reference")
            note = pc.clean_text(body.note, "note", max_len=500, required=False)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        target = (body.target or "wallet").strip().lower()
        if target not in ("wallet", "account"):
            raise HTTPException(status_code=400, detail="target must be wallet or account")
        method = conn.execute(
            f"SELECT {pc.METHOD_COLS} FROM payment_methods "
            "WHERE id = %s AND org_id = %s AND enabled", (body.method_id, ctx.org_id)).fetchone()
        if not method:
            raise HTTPException(status_code=404, detail="Payment method not found")
        (method_id, kind, label, _enabled, _currency, _details, min_amount, fee_pct,
         _instructions, _sort_order) = method
        min_amount = Decimal(min_amount)
        if amount < min_amount:
            raise HTTPException(status_code=400,
                                detail=f"minimum deposit for this method is {min_amount:.2f}")
        if kind == "bank" and body.receipt_file_id is None:
            raise HTTPException(status_code=400, detail="receipt is required for bank deposits")
        if body.receipt_file_id is not None:
            if not file_belongs(conn, ctx.org_id, ctx.user_id, body.receipt_file_id,
                                "deposit_receipt"):
                raise HTTPException(status_code=400, detail="receipt file not found")
            used = conn.execute("SELECT 1 FROM deposits WHERE receipt_file_id = %s",
                                (body.receipt_file_id,)).fetchone()
            if used:
                raise HTTPException(
                    status_code=400, detail="receipt file is already attached to another notice")
        target_account_id: Optional[int] = None
        if target == "account":
            linked = pc.linked_account(conn, ctx.org_id, ctx.user_id)
            if linked is None:
                raise HTTPException(status_code=409, detail="no account linked yet")
            if body.target_account_id is not None and body.target_account_id != linked:
                raise HTTPException(status_code=404, detail="Account not found")
            target_account_id = linked
        if hourly.is_limited(f"portal-deposit:{ctx.org_id}:{ctx.user_id}"):
            raise HTTPException(status_code=429, detail=RATE_LIMITED)
        fee = pc.fee_for(amount, Decimal(fee_pct))
        try:
            row = conn.execute(
                "INSERT INTO deposits (org_id, user_id, method_id, method_kind, method_label, "
                "amount, fee, reference, receipt_file_id, target, target_account_id, note) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                f"RETURNING {pc.DEPOSIT_COLS}",
                (ctx.org_id, ctx.user_id, method_id, kind, label, amount, fee, reference,
                 body.receipt_file_id, target, target_account_id, note)).fetchone()
        except psycopg.errors.UniqueViolation:
            # deposits_one_live_reference: the same transaction is already
            # pending or confirmed in this workspace (possibly another
            # investor's). Two rows for one transfer is the same money
            # counted twice. Rejected and cancelled rows are not in the
            # index, so a re-file after a mistake still works.
            raise HTTPException(status_code=409,
                                detail="A notice with this reference already exists")
        out = pc.deposit_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_deposit_noticed",
            actor_email=ctx.user_email, user_id=ctx.user_id, severity="warning",
            account_id=target_account_id, deposit_id=out["id"], amount=out["amount"],
            fee=out["fee"], method_id=method_id, method_label=label, reference=reference,
            target=target,
            summary=f"Deposit notice: {amount:.2f} USD via {label} from {ctx.user_email}")
        return out

    @router.post("/investor/deposits/{deposit_id}/cancel", response_model=Dict[str, Any])
    async def cancel_deposit(deposit_id: int,
                             ctx: OrgContext = Depends(require_org_role("investor")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """The investor's own move, allowed only while pending. The row is
        kept as cancelled so the history stays whole; the reference is
        free again."""
        current = conn.execute(
            "SELECT status FROM deposits WHERE id = %s AND org_id = %s AND user_id = %s",
            (deposit_id, ctx.org_id, ctx.user_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Deposit not found")
        if not pc.can_transition("deposits", current[0], "cancelled"):
            raise HTTPException(status_code=409, detail=f"deposit is already {current[0]}")
        row = conn.execute(
            "UPDATE deposits SET status = 'cancelled', decided_by = %s, decided_at = now() "
            "WHERE id = %s AND org_id = %s AND status = 'pending' "
            f"RETURNING {pc.DEPOSIT_COLS}", (ctx.user_id, deposit_id, ctx.org_id)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        out = pc.deposit_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_deposit_cancelled",
            actor_email=ctx.user_email, user_id=ctx.user_id,
            account_id=out["target_account_id"], deposit_id=deposit_id, amount=out["amount"],
            reference=out["reference"],
            summary=f"Deposit notice #{deposit_id} cancelled by {ctx.user_email}")
        return out

```

- [ ] **Step 4: Add the admin deposit routes**

In `api/src/api/routes/portal_admin.py`, replace the import line:

```python
from fastapi import APIRouter, Depends, HTTPException, Response
```

with:

```python
from fastapi import APIRouter, Depends, HTTPException, Request, Response
```

Directly below the `SettingsBody` class add:

```python
class DepositDecision(BaseModel):
    status: str
    credited_amount: Any = None
    note: Optional[str] = None
```

Then insert the two routes immediately above the final `    return router` line of `create_portal_admin_router`:

```python
    # ------------------------------------------------------------ deposits

    @router.get("/deposits", response_model=List[Dict[str, Any]])
    async def deposit_queue(status: Optional[str] = None,
                            ctx: OrgContext = Depends(require_org_role("admin")),
                            conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "d.org_id = %s" + (" AND d.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        rows = conn.execute(
            f"SELECT {pc.qualify(pc.DEPOSIT_COLS, 'd')}, u.email, u.display_name "
            "FROM deposits d JOIN users u ON u.id = d.user_id "
            f"WHERE {where} ORDER BY (d.status = 'pending') DESC, d.created_at DESC, d.id DESC",
            params).fetchall()
        return [pc.deposit_json(r) for r in rows]

    @router.post("/deposits/{deposit_id}/decision", response_model=Dict[str, Any])
    async def decide_deposit(deposit_id: int, body: DepositDecision, http_request: Request,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        """Confirm (credit main by credited_amount, default amount - fee) or
        reject (note required). The status change, the ledger row and, for
        a trading-account deposit, the approved main -> account transfer
        are one transaction; the UPDATE carries `AND status = 'pending'` so
        a lost race or a replay changes nothing."""
        new_status = body.status.strip().lower()
        if new_status not in ("confirmed", "rejected"):
            raise HTTPException(status_code=400, detail="status must be confirmed or rejected")
        try:
            note = pc.require_note_on_reject(new_status, body.note)
        except pc.LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            "SELECT status, user_id, amount, fee, target FROM deposits "
            "WHERE id = %s AND org_id = %s", (deposit_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Deposit not found")
        status_now, investor_id, amount, fee, target = current
        if not pc.can_transition("deposits", status_now, new_status):
            raise HTTPException(status_code=409, detail=f"deposit is already {status_now}")
        credited: Optional[Decimal] = None
        if new_status == "confirmed":
            try:
                credited = (Decimal(amount) - Decimal(fee) if body.credited_amount is None
                            else pc.parse_amount(body.credited_amount, "credited_amount"))
            except pc.LedgerError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            if credited <= 0:
                raise HTTPException(status_code=400,
                                    detail="credited_amount must be greater than 0")
        linked: Optional[int] = None
        transfer_id: Optional[int] = None
        with conn.transaction():
            if new_status == "confirmed" and target == "account":
                # The link as it is NOW, not as it was when the notice was
                # filed: the admin funds the account the investor has today.
                linked = pc.linked_account(conn, ctx.org_id, investor_id)
                if linked is None:
                    suffix = "(no account linked; credited to wallet)"
                    note = f"{note} {suffix}" if note else suffix
            row = conn.execute(
                "UPDATE deposits SET status = %s, decided_by = %s, decided_at = now(), "
                "decision_note = %s, credited_amount = %s "
                "WHERE id = %s AND org_id = %s AND status = 'pending' "
                f"RETURNING {pc.DEPOSIT_COLS}",
                (new_status, ctx.user_id, note, credited, deposit_id, ctx.org_id)).fetchone()
            if not row:
                raise HTTPException(status_code=409, detail="decided by someone else")
            if new_status == "confirmed":
                pc.settle(conn, org_id=ctx.org_id, user_id=investor_id, wallet="main",
                          amount=credited, kind="deposit", ref_table="deposits",
                          ref_id=deposit_id, created_by=ctx.user_id)
                if linked is not None:
                    # Held in main until the admin funds the broker account
                    # and marks the transfer done (Task 9 settles it).
                    (transfer_id,) = conn.execute(
                        "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, "
                        "target_kind, target_account_id, amount, status, decided_by, "
                        "decided_at, decision_note) "
                        "VALUES (%s, %s, 'wallet', 'main', 'account', %s, %s, 'approved', %s, "
                        "now(), %s) RETURNING id",
                        (ctx.org_id, investor_id, linked, credited, ctx.user_id,
                         f"funded from deposit #{deposit_id}")).fetchone()
        out = pc.deposit_json(row)
        await pc.audit_control(
            conn, org_id=ctx.org_id, action="investor_deposit_decided",
            actor_email=ctx.user_email, user_id=investor_id, account_id=linked,
            deposit_id=deposit_id, status=new_status, amount=out["amount"],
            credited_amount=out["credited_amount"], note=note, transfer_id=transfer_id,
            summary=f"Deposit #{deposit_id} {new_status} by {ctx.user_email}")
        credited_line = (f"Credited: {out['credited_amount']:.2f} USD\n"
                         if out["credited_amount"] is not None else "")
        await pc.notify_investor(
            conn, http_request, investor_id,
            f"Your deposit of {out['amount']:.2f} USD was {new_status}",
            f"Status: {new_status}\nAmount: {out['amount']:.2f} USD via {out['method_label']}\n"
            f"{credited_line}Note: {note or '—'}\n\nOpen the portal for details.")
        return out

```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
.venv/Scripts/python -m pytest tests/test_portal_deposits.py tests/test_portal_methods.py tests/test_portal_common.py -q -p no:cacheprovider
```

Expected: PASS, all green (`test_portal_deposits.py`: 16 plain tests plus 6 parametrised refusals = 22).

- [ ] **Step 6: Run the whole API suite**

```bash
.venv/Scripts/python -m pytest tests -q -p no:cacheprovider
```

Expected: green apart from the 7 pre-existing `test_events_ws.py` errors and the one EA-download CRLF failure.

- [ ] **Step 7: Commit**

```bash
git add api/src/api/routes/portal_investor.py api/src/api/routes/portal_admin.py api/tests/test_portal_deposits.py
git commit -m "feat(api): deposits -- investor notices with method snapshot and fee, cancel, admin decisions that settle exactly once

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---


### Task 8: Payout destinations and withdrawals (MPIN step-up, caps, fees; admin decisions and paid)

**Files:**
- Modify: `api/src/api/routes/portal_investor.py` (body classes `DestinationBody`, `WithdrawalRequest`; helper `clean_destination_details`; routes `GET/POST investor/payout-destinations`, `POST investor/payout-destinations/{id}/remove`, `GET/POST investor/withdrawals`, `POST investor/withdrawals/{id}/cancel`)
- Modify: `api/src/api/routes/portal_admin.py` (body class `PaidBody`; routes `GET withdrawals`, `POST withdrawals/{id}/decision`, `POST withdrawals/{id}/paid`, `GET payout-destinations`, `POST payout-destinations/{id}/decision`)
- Test: `api/tests/test_portal_withdrawals.py`

**Interfaces:**
- Consumes: `require_mpin` (`mpin_core`), `parse_amount`, `clean_text`, `LedgerError`, `fee_for`, `can_transition` (`portal_ledger`); `audit_control`, `notify_investor`, `wallet_figures`, `settle`, `portal_settings`, `destination_summary`, `destination_json`, `withdrawal_json`, `DESTINATION_COLS`, `WITHDRAWAL_COLS`, `Decision`, `require_note_on_reject` (`portal_common`); `file_belongs` (`routes/portal_files`); the router's hourly `LoginRateLimiter(max_attempts=10, window_s=3600)` created in Task 7 (this task calls it `hourly`); test helpers `csrf`, `credit`, `approved_destination`, `seed_file` (`tests/portal_helpers.py`); `PUT portal-settings` (Task 6).
- Produces: routes above; `clean_destination_details(kind, details) -> dict` (module-level in `portal_investor.py`); refusal strings exactly as the interfaces doc lists them.

Conventions this task relies on from Tasks 5–7 (verify each with a quick grep before Step 3; the interfaces doc fixes them): the serialisers `withdrawal_json(row)` / `destination_json(row, full=)` append `email` and `display_name` to the dict when the row carries two extra trailing columns (the old `_withdrawal_json` did the same); `require_note_on_reject` raises `LedgerError`; `wallet_figures(...)[wallet]["available"]` is already floored; `portal_settings` returns Decimals.

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_portal_withdrawals.py`:

```python
"""Payout destinations and withdrawals: the investor saves where money goes,
an admin approves it, the investor asks (with the MPIN) for money out of
`main`, the admin approves, pays outside the app and marks it paid, which
is the one moment the ledger moves."""
from decimal import Decimal

import httpx
import psycopg
import pytest
from conftest import default_mock_callback
from portal_helpers import approved_destination, credit, csrf, seed_file

from api import ws as ws_module

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}
BANK = {"bank_name": "ICICI Bank", "holder": "Sherwyn Joel", "account_number": "000401234543",
        "code": "ICIC0000004", "bank_address": "Mumbai", "country": "IN"}
CRYPTO = {"coin": "USDT", "network": "TRC20", "address": "TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE"}


def _member(db, org_id, user, role):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
            (org_id, user["id"], role))


def _investor(org_client, make_user, login_as, db, email="inv@example.com"):
    client, org_id, seed = org_client
    investor = make_user(email=email)
    _member(db, org_id, investor, "investor")
    login_as(client, investor)
    return client, org_id, investor


def _add_destination(client, org_id, kind="bank", details=None, nickname="Salary account",
                     mpin="123456", proof=None):
    body = {"kind": kind, "nickname": nickname,
            "details": details if details is not None else (BANK if kind == "bank" else CRYPTO),
            "mpin": mpin}
    if proof is not None:
        body["proof_file_id"] = proof
    return client.post(f"/api/orgs/{org_id}/investor/payout-destinations", json=body,
                       headers=csrf(client))


def _decide_destination(client, org_id, dest_id, status, note=None):
    return client.post(f"/api/orgs/{org_id}/payout-destinations/{dest_id}/decision",
                       json={"status": status, "note": note}, headers=csrf(client))


def _withdraw(client, org_id, destination_id, amount, mpin="123456"):
    return client.post(f"/api/orgs/{org_id}/investor/withdrawals",
                       json={"destination_id": destination_id, "amount": amount, "mpin": mpin},
                       headers=csrf(client))


def _decide_withdrawal(client, org_id, wd_id, status, note=None):
    return client.post(f"/api/orgs/{org_id}/withdrawals/{wd_id}/decision",
                       json={"status": status, "note": note}, headers=csrf(client))


def _events(db, org_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload, actor_email FROM events WHERE org_id = %s ORDER BY id",
            (org_id,)).fetchall()


def _entries(db, org_id, user_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return [(r[0], float(r[1]), r[2], r[3], r[4]) for r in conn.execute(
            "SELECT wallet, amount, kind, ref_table, ref_id FROM wallet_entries "
            "WHERE org_id = %s AND user_id = %s ORDER BY id", (org_id, user_id)).fetchall()]


def _figures(db, org_id, user_id, wallet="main"):
    from api.portal_common import wallet_figures
    with psycopg.connect(db, autocommit=True) as conn:
        figures = wallet_figures(conn, org_id, user_id)[wallet]
    return {k: float(v) for k, v in figures.items()}


class _FakeAlerter:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_to(self, to_addr, subject, text):
        if self.fail:
            raise RuntimeError("resend down")
        self.sent.append((to_addr, subject))
        return True


# ------------------------------------------------------ payout destinations


def test_an_investor_adds_a_bank_payout_account_with_the_mpin(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    r = _add_destination(client, org_id)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "bank" and body["nickname"] == "Salary account"
    assert body["status"] == "pending" and body["summary"] == "ICICI Bank ••4543"
    assert body["details"]["account_number"] == "000401234543", "the owner sees the full number"
    assert body["proof_file_id"] is None and body["decided_by"] is None
    assert client.get(f"/api/orgs/{org_id}/investor/payout-destinations").json() == [body]
    severity, payload, actor = _events(db, org_id)[-1]
    assert severity == "warning" and payload["action"] == "investor_destination_added"
    assert payload["user_id"] == investor["id"] and payload["destination_id"] == body["id"]
    assert "ICICI Bank ••4543" in payload["summary"] and actor == "inv@example.com"


def test_a_wrong_mpin_is_refused_and_counted(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    r = _add_destination(client, org_id, mpin="000000")
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid MPIN", "attempts_left": 4}
    assert client.get(f"/api/orgs/{org_id}/investor/payout-destinations").json() == []


def test_five_wrong_mpins_lock_money_actions(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    for left in (4, 3, 2, 1):
        r = _add_destination(client, org_id, mpin="000000")
        assert r.status_code == 401 and r.json()["attempts_left"] == left
    r = _add_destination(client, org_id, mpin="000000")
    assert r.status_code == 423 and r.json()["detail"] == "MPIN locked"
    assert r.json()["locked_until"]
    r = _add_destination(client, org_id, mpin="123456")
    assert r.status_code == 423, "the right MPIN does not open a locked step-up"
    assert client.get(f"/api/orgs/{org_id}/investor/payout-destinations").json() == []


def test_a_malformed_mpin_is_400_and_an_unset_mpin_is_409(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    r = _add_destination(client, org_id, mpin="12")
    assert r.status_code == 400 and r.json()["detail"] == "MPIN must be exactly 6 digits"
    r = _add_destination(client, org_id, mpin=None)
    assert r.status_code == 400 and r.json()["detail"] == "MPIN must be exactly 6 digits"
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE users SET mpin_hash = NULL WHERE id = %s", (investor["id"],))
    r = _add_destination(client, org_id)
    assert r.status_code == 409 and r.json()["detail"] == "MPIN not set"


def test_missing_details_are_named(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    bank = {k: v for k, v in BANK.items() if k != "account_number"}
    r = _add_destination(client, org_id, details=bank)
    assert r.status_code == 400 and r.json()["detail"] == "account_number is required"
    crypto = {k: v for k, v in CRYPTO.items() if k != "address"}
    r = _add_destination(client, org_id, kind="crypto", details=crypto)
    assert r.status_code == 400 and r.json()["detail"] == "address is required"
    r = _add_destination(client, org_id, kind="paypal")
    assert r.status_code == 400 and r.json()["detail"] == "kind must be bank or crypto"
    r = _add_destination(client, org_id, nickname="   ")
    assert r.status_code == 400 and r.json()["detail"] == "nickname is required"


def test_a_crypto_destination_summarises_network_and_address(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    r = _add_destination(client, org_id, kind="crypto", nickname="Binance")
    assert r.status_code == 201
    assert r.json()["summary"] == "TRC20 T…SE"
    assert r.json()["details"] == CRYPTO


def test_a_proof_must_be_the_callers_payout_proof(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    theirs = seed_file(db, org_id, other["id"], purpose="payout_proof")
    r = _add_destination(client, org_id, proof=theirs)
    assert r.status_code == 400 and r.json()["detail"] == "proof file not found"
    receipt = seed_file(db, org_id, investor["id"], purpose="deposit_receipt")
    r = _add_destination(client, org_id, proof=receipt)
    assert r.status_code == 400 and r.json()["detail"] == "proof file not found"
    proof = seed_file(db, org_id, investor["id"], purpose="payout_proof")
    r = _add_destination(client, org_id, proof=proof)
    assert r.status_code == 201 and r.json()["proof_file_id"] == proof


def test_admin_approves_or_rejects_with_a_note(org_client, make_user, login_as, db, monkeypatch):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest = _add_destination(client, org_id).json()
    login_as(client, ADMIN)
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    queue = client.get(f"/api/orgs/{org_id}/payout-destinations?status=pending").json()
    assert [d["id"] for d in queue] == [dest["id"]] and queue[0]["email"] == "inv@example.com"
    assert queue[0]["display_name"] == "User"
    r = _decide_destination(client, org_id, dest["id"], "rejected")
    assert r.status_code == 400 and "note" in r.json()["detail"]
    r = _decide_destination(client, org_id, dest["id"], "shredded")
    assert r.status_code == 400 and r.json()["detail"] == "status must be approved or rejected"
    r = _decide_destination(client, org_id, dest["id"], "approved")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["decided_at"] is not None
    r = _decide_destination(client, org_id, dest["id"], "approved")
    assert r.status_code == 409 and r.json()["detail"] == "payout account is already approved"
    assert _decide_destination(client, org_id, 999, "approved").status_code == 404
    assert _events(db, org_id)[-1][1]["action"] == "investor_destination_decided"
    assert fake.sent == [("inv@example.com", "Your payout account ICICI Bank ••4543 was approved")]


def test_removal_keeps_the_row_but_hides_it(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest = _add_destination(client, org_id).json()
    r = client.post(f"/api/orgs/{org_id}/investor/payout-destinations/{dest['id']}/remove",
                    headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "removed"
    assert client.get(f"/api/orgs/{org_id}/investor/payout-destinations").json() == []
    r = client.post(f"/api/orgs/{org_id}/investor/payout-destinations/{dest['id']}/remove",
                    headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "payout account is already removed"
    assert _events(db, org_id)[-1][1]["action"] == "investor_destination_removed"
    with psycopg.connect(db, autocommit=True) as conn:
        (status,) = conn.execute("SELECT status FROM payout_destinations WHERE id = %s",
                                 (dest["id"],)).fetchone()
    assert status == "removed"


def test_removal_is_refused_while_a_withdrawal_uses_it(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("1000"))
    wd = _withdraw(client, org_id, dest_id, "100").json()
    r = client.post(f"/api/orgs/{org_id}/investor/payout-destinations/{dest_id}/remove",
                    headers=csrf(client))
    assert r.status_code == 409
    assert r.json()["detail"] == "a withdrawal is still using this payout account"
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals/{wd['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    r = client.post(f"/api/orgs/{org_id}/investor/payout-destinations/{dest_id}/remove",
                    headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "removed"


def test_other_investors_cannot_see_or_remove_it(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest = _add_destination(client, org_id).json()
    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    login_as(client, other)
    assert client.get(f"/api/orgs/{org_id}/investor/payout-destinations").json() == []
    r = client.post(f"/api/orgs/{org_id}/investor/payout-destinations/{dest['id']}/remove",
                    headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Payout account not found"


# --------------------------------------------------------------- withdrawals


def test_a_withdrawal_needs_an_approved_destination(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    credit(db, org_id, investor["id"], Decimal("1000"))
    pending = _add_destination(client, org_id).json()
    r = _withdraw(client, org_id, pending["id"], "100")
    assert r.status_code == 404 and r.json()["detail"] == "Payout account not found"
    dest_id = approved_destination(db, org_id, investor["id"])
    r = _withdraw(client, org_id, dest_id, "100")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "requested" and body["destination_id"] == dest_id
    assert body["amount"] == 100.0 and body["fee"] == 0.0 and body["net_amount"] == 100.0
    assert body["currency"] == "USD" and body["destination_kind"] == "crypto"
    assert body["destination_summary"] and body["txid"] is None
    assert client.get(f"/api/orgs/{org_id}/investor/withdrawals").json() == [body]
    severity, payload, actor = _events(db, org_id)[-1]
    assert severity == "warning" and payload["action"] == "investor_withdrawal_requested"
    assert payload["user_id"] == investor["id"] and payload["withdrawal_id"] == body["id"]
    assert payload["summary"] == (
        f"Withdrawal request: 100.00 USD to {body['destination_summary']} from inv@example.com")


def test_a_wrong_mpin_on_a_withdrawal_is_refused_before_anything_else(
        org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    r = _withdraw(client, org_id, dest_id, "-5", mpin="000000")
    assert r.status_code == 401 and r.json()["attempts_left"] == 4
    r = _withdraw(client, org_id, dest_id, "-5")
    assert r.status_code == 400 and "amount" in r.json()["detail"]


def test_the_cap_is_the_floored_available_and_use_max_is_never_refused(
        org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("5120.50"))
    assert _withdraw(client, org_id, dest_id, "100").status_code == 201
    assert _figures(db, org_id, investor["id"]) == {
        "balance": 5120.5, "on_hold": 100.0, "available": 5020.5}
    r = _withdraw(client, org_id, dest_id, "5020.51")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds what is available (5020.50)"
    assert _withdraw(client, org_id, dest_id, "5020.50").status_code == 201, "Use max"
    r = _withdraw(client, org_id, dest_id, "0.01")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds what is available (0.00)"


def test_fee_and_minimum_come_from_portal_settings(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("1000"))
    login_as(client, ADMIN)
    r = client.put(f"/api/orgs/{org_id}/portal-settings",
                   json={"withdrawal_min": "50", "withdrawal_fee_pct": "2.5"},
                   headers=csrf(client))
    assert r.status_code == 200, r.text
    login_as(client, investor)
    r = _withdraw(client, org_id, dest_id, "49.99")
    assert r.status_code == 400 and r.json()["detail"] == "minimum withdrawal is 50.00"
    r = _withdraw(client, org_id, dest_id, "200")
    assert r.status_code == 201
    assert r.json()["amount"] == 200.0 and r.json()["fee"] == 5.0
    assert r.json()["net_amount"] == 195.0
    assert _figures(db, org_id, investor["id"])["on_hold"] == 200.0, "the hold is the gross amount"


def test_cancel_releases_the_hold(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("1000"))
    wd = _withdraw(client, org_id, dest_id, "300").json()
    assert _figures(db, org_id, investor["id"])["available"] == 700.0
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals/{wd['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert r.json()["decided_at"] is not None
    assert _figures(db, org_id, investor["id"]) == {
        "balance": 1000.0, "on_hold": 0.0, "available": 1000.0}
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals/{wd['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "withdrawal is already cancelled"
    assert client.post(f"/api/orgs/{org_id}/investor/withdrawals/999/cancel",
                       headers=csrf(client)).status_code == 404
    assert _events(db, org_id)[-1][1]["action"] == "investor_withdrawal_cancelled"
    login_as(client, ADMIN)
    r = _decide_withdrawal(client, org_id, wd["id"], "approved")
    assert r.status_code == 409 and r.json()["detail"] == "withdrawal is already cancelled"


def test_approve_then_paid_debits_main_exactly_once(org_client, make_user, login_as, db,
                                                     monkeypatch):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("1000"))
    wd = _withdraw(client, org_id, dest_id, "250").json()
    login_as(client, ADMIN)
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    queue = client.get(f"/api/orgs/{org_id}/withdrawals?status=requested").json()
    assert [w["id"] for w in queue] == [wd["id"]] and queue[0]["email"] == "inv@example.com"

    r = client.post(f"/api/orgs/{org_id}/withdrawals/{wd['id']}/paid", json={"txid": "x"},
                    headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "withdrawal is requested, not approved"
    r = _decide_withdrawal(client, org_id, wd["id"], "approved")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert _figures(db, org_id, investor["id"])["on_hold"] == 250.0, "approved still holds"
    r = client.post(f"/api/orgs/{org_id}/withdrawals/{wd['id']}/paid", json={"txid": "  "},
                    headers=csrf(client))
    assert r.status_code == 400 and "txid" in r.json()["detail"]
    r = client.post(f"/api/orgs/{org_id}/withdrawals/{wd['id']}/paid",
                    json={"txid": "chain-tx-1"}, headers=csrf(client))
    assert r.status_code == 200
    assert r.json()["status"] == "paid" and r.json()["txid"] == "chain-tx-1"
    assert r.json()["paid_at"] is not None
    assert _entries(db, org_id, investor["id"])[-1] == (
        "main", -250.0, "withdrawal", "withdrawals", wd["id"])
    assert _figures(db, org_id, investor["id"]) == {
        "balance": 750.0, "on_hold": 0.0, "available": 750.0}

    r = client.post(f"/api/orgs/{org_id}/withdrawals/{wd['id']}/paid",
                    json={"txid": "chain-tx-1"}, headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "withdrawal is paid, not approved"
    r = _decide_withdrawal(client, org_id, wd["id"], "rejected", "too late")
    assert r.status_code == 409 and r.json()["detail"] == "withdrawal is already paid"
    assert len([e for e in _entries(db, org_id, investor["id"]) if e[2] == "withdrawal"]) == 1
    actions = [e[1]["action"] for e in _events(db, org_id)]
    assert actions[-3:] == ["investor_withdrawal_requested", "investor_withdrawal_decided",
                            "investor_withdrawal_paid"]
    assert fake.sent == [("inv@example.com", "Your withdrawal of 250.00 USD was approved"),
                         ("inv@example.com", "Your withdrawal of 250.00 USD was paid")]


def test_an_approved_request_can_still_be_rejected_with_a_note(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("100"))
    wd = _withdraw(client, org_id, dest_id, "10").json()
    login_as(client, ADMIN)
    assert _decide_withdrawal(client, org_id, wd["id"], "approved").status_code == 200
    r = _decide_withdrawal(client, org_id, wd["id"], "rejected")
    assert r.status_code == 400 and "note" in r.json()["detail"]
    r = _decide_withdrawal(client, org_id, wd["id"], "paid")
    assert r.status_code == 400 and r.json()["detail"] == "status must be approved or rejected"
    r = _decide_withdrawal(client, org_id, wd["id"], "rejected", "address did not match")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert r.json()["decision_note"] == "address did not match"
    assert _figures(db, org_id, investor["id"])["on_hold"] == 0.0
    assert _decide_withdrawal(client, org_id, 999, "approved").status_code == 404


def test_a_failed_email_never_fails_the_decision(org_client, make_user, login_as, db, monkeypatch):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("100"))
    wd = _withdraw(client, org_id, dest_id, "10").json()
    login_as(client, ADMIN)
    monkeypatch.setattr(ws_module.broadcaster, "alerter", _FakeAlerter(fail=True), raising=False)
    r = _decide_withdrawal(client, org_id, wd["id"], "approved")
    assert r.status_code == 200 and r.json()["status"] == "approved"


def test_ten_requests_an_hour_then_429(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("100"))
    for _ in range(10):
        assert _withdraw(client, org_id, dest_id, "1").status_code == 201
    r = _withdraw(client, org_id, dest_id, "1")
    assert r.status_code == 429 and r.json()["detail"] == "too many requests; try again later"


def test_investors_only_see_their_own_withdrawals_and_cannot_work_the_queue(
        org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    dest_id = approved_destination(db, org_id, investor["id"])
    credit(db, org_id, investor["id"], Decimal("100"))
    wd = _withdraw(client, org_id, dest_id, "10").json()
    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    login_as(client, other)
    assert client.get(f"/api/orgs/{org_id}/investor/withdrawals").json() == []
    r = client.post(f"/api/orgs/{org_id}/investor/withdrawals/{wd['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Withdrawal not found"
    assert _decide_withdrawal(client, org_id, wd["id"], "approved").status_code == 403
    assert client.get(f"/api/orgs/{org_id}/withdrawals").status_code == 403
    assert client.get(f"/api/orgs/{org_id}/payout-destinations").status_code == 403
    assert _decide_destination(client, org_id, dest_id, "approved").status_code == 403
```

- [ ] **Step 2: Run it to verify it fails**

Run (from `api/`, env exported as in the Global Constraints):
`.venv/Scripts/python -m pytest tests/test_portal_withdrawals.py -q -p no:cacheprovider`
Expected: 21 FAIL. Every test fails on a status assertion because the routes do not exist yet (`assert 404 == 201`, `assert 404 == 401`, …); no ImportError.

- [ ] **Step 3: Body classes, imports and the details helper in `portal_investor.py`**

At the top of `api/src/api/routes/portal_investor.py` make sure every one of these imports is present (add the missing ones next to the existing import lines; Task 7 already has most of them):

```python
from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ..auth import LoginRateLimiter
from ..config import ApiConfig
from ..db import get_conn
from ..mpin_core import require_mpin
from ..portal_common import (
    DESTINATION_COLS, WITHDRAWAL_COLS, audit_control, destination_json, destination_summary,
    fee_for, portal_settings, wallet_figures, withdrawal_json)
from ..portal_ledger import LedgerError, can_transition, clean_text, parse_amount
from ..rbac import OrgContext, require_org_role
from .portal_files import file_belongs
```

Directly after the `class DepositNotice(BaseModel):` block add (if Task 7 already declared either class with exactly these fields, keep the one copy):

```python
class DestinationBody(BaseModel):
    kind: str
    nickname: str
    details: dict
    proof_file_id: Optional[int] = None
    mpin: Any = None


class WithdrawalRequest(BaseModel):
    destination_id: int
    amount: Any
    mpin: Any = None
```

Then, above `def create_portal_investor_router()`, add the module-level helper:

```python
BANK_REQUIRED = ("bank_name", "holder", "account_number", "code")
BANK_OPTIONAL = ("bank_address", "country")
CRYPTO_REQUIRED = ("coin", "network", "address")


def clean_destination_details(kind: str, details: object) -> dict:
    """Keep only the keys a destination of this kind carries, each trimmed
    and bounded. A missing required key raises LedgerError('<key> is
    required'), which the route turns into the 400 the dashboard shows
    under that field."""
    raw = details if isinstance(details, dict) else {}
    out: Dict[str, str] = {}
    required = BANK_REQUIRED if kind == "bank" else CRYPTO_REQUIRED
    for key in required:
        out[key] = clean_text(raw.get(key), key, max_len=128)
    if kind == "bank":
        for key in BANK_OPTIONAL:
            value = clean_text(raw.get(key), key, max_len=256, required=False)
            if value is not None:
                out[key] = value
    return out
```

- [ ] **Step 4: Investor routes for destinations and withdrawals**

Inside `create_portal_investor_router()`, immediately before its final `return router` line, add. (`hourly` is the router's `LoginRateLimiter(max_attempts=10, window_s=3600)` from Task 7; if Task 7 gave it another name, use that name in the two `is_limited` calls.)

```python
    # ------------------------------------------------- payout destinations

    @router.get("/investor/payout-destinations", response_model=List[Dict[str, Any]])
    async def my_destinations(ctx: OrgContext = Depends(require_org_role("investor")),
                              conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {DESTINATION_COLS} FROM payout_destinations "
            "WHERE org_id = %s AND user_id = %s AND status <> 'removed' "
            "ORDER BY created_at DESC, id DESC", (ctx.org_id, ctx.user_id)).fetchall()
        return [destination_json(r, full=True) for r in rows]

    @router.post("/investor/payout-destinations", status_code=201, response_model=Dict[str, Any])
    async def add_destination(body: DestinationBody,
                              ctx: OrgContext = Depends(require_org_role("investor")),
                              conn: psycopg.Connection = Depends(get_conn)):
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        kind = (body.kind or "").strip().lower()
        if kind not in ("bank", "crypto"):
            raise HTTPException(status_code=400, detail="kind must be bank or crypto")
        try:
            nickname = clean_text(body.nickname, "nickname", max_len=64)
            details = clean_destination_details(kind, body.details)
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if body.proof_file_id is not None and not file_belongs(
                conn, ctx.org_id, ctx.user_id, body.proof_file_id, "payout_proof"):
            raise HTTPException(status_code=400, detail="proof file not found")
        if hourly.is_limited(f"portal-destination:{ctx.org_id}:{ctx.user_id}"):
            raise HTTPException(status_code=429, detail="too many requests; try again later")
        row = conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details, "
            "proof_file_id) VALUES (%s, %s, %s, %s, %s, %s) "
            f"RETURNING {DESTINATION_COLS}",
            (ctx.org_id, ctx.user_id, kind, nickname, Jsonb(details),
             body.proof_file_id)).fetchone()
        out = destination_json(row, full=True)
        await audit_control(
            conn, org_id=ctx.org_id, action="investor_destination_added",
            actor_email=ctx.user_email, user_id=ctx.user_id, severity="warning",
            destination_id=out["id"], kind=kind, destination=out["summary"],
            summary=f"Payout account added: {out['summary']} by {ctx.user_email}")
        return out

    @router.post("/investor/payout-destinations/{dest_id}/remove", response_model=Dict[str, Any])
    async def remove_destination(dest_id: int,
                                 ctx: OrgContext = Depends(require_org_role("investor")),
                                 conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        current = conn.execute(
            "SELECT status FROM payout_destinations WHERE id = %s AND org_id = %s AND user_id = %s",
            (dest_id, ctx.org_id, ctx.user_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Payout account not found")
        if not can_transition("payout_destinations", current[0], "removed"):
            raise HTTPException(status_code=409,
                                detail=f"payout account is already {current[0]}")
        in_use = conn.execute(
            "SELECT 1 FROM withdrawals WHERE destination_id = %s "
            "AND status IN ('requested', 'approved') LIMIT 1", (dest_id,)).fetchone()
        if in_use:
            raise HTTPException(status_code=409,
                                detail="a withdrawal is still using this payout account")
        row = conn.execute(
            "UPDATE payout_destinations SET status = 'removed', decided_by = %s, "
            "decided_at = now() WHERE id = %s AND org_id = %s AND status = %s "
            f"RETURNING {DESTINATION_COLS}",
            (ctx.user_id, dest_id, ctx.org_id, current[0])).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        out = destination_json(row, full=True)
        await audit_control(
            conn, org_id=ctx.org_id, action="investor_destination_removed",
            actor_email=ctx.user_email, user_id=ctx.user_id,
            destination_id=dest_id, destination=out["summary"])
        return out

    # ------------------------------------------------------------ withdrawals

    @router.get("/investor/withdrawals", response_model=List[Dict[str, Any]])
    async def my_withdrawals(ctx: OrgContext = Depends(require_org_role("investor")),
                             conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {WITHDRAWAL_COLS} FROM withdrawals "
            "WHERE org_id = %s AND user_id = %s ORDER BY created_at DESC, id DESC",
            (ctx.org_id, ctx.user_id)).fetchall()
        return [withdrawal_json(r) for r in rows]

    @router.post("/investor/withdrawals", status_code=201, response_model=Dict[str, Any])
    async def request_withdrawal(body: WithdrawalRequest,
                                 ctx: OrgContext = Depends(require_org_role("investor")),
                                 conn: psycopg.Connection = Depends(get_conn)):
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        try:
            amount = parse_amount(body.amount)
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        dest = conn.execute(
            "SELECT kind, details FROM payout_destinations "
            "WHERE id = %s AND org_id = %s AND user_id = %s AND status = 'approved'",
            (body.destination_id, ctx.org_id, ctx.user_id)).fetchone()
        if not dest:
            raise HTTPException(status_code=404, detail="Payout account not found")
        settings = portal_settings(conn, ctx.org_id)
        if amount < settings["withdrawal_min"]:
            raise HTTPException(
                status_code=400,
                detail=f"minimum withdrawal is {settings['withdrawal_min']:.2f}")
        # The cap is the same floored figure the summary reports, so the
        # dashboard's "Use max" can never be refused for rounding.
        available = wallet_figures(conn, ctx.org_id, ctx.user_id)["main"]["available"]
        if amount > available:
            raise HTTPException(
                status_code=400, detail=f"amount exceeds what is available ({available:.2f})")
        fee = fee_for(amount, settings["withdrawal_fee_pct"])
        net = amount - fee
        if net <= 0:
            raise HTTPException(status_code=400, detail="amount is too small to cover the fee")
        if hourly.is_limited(f"portal-withdrawal:{ctx.org_id}:{ctx.user_id}"):
            raise HTTPException(status_code=429, detail="too many requests; try again later")
        summary = destination_summary(dest[0], dest[1])
        row = conn.execute(
            "INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
            "destination_summary, amount, fee, net_amount) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            f"RETURNING {WITHDRAWAL_COLS}",
            (ctx.org_id, ctx.user_id, body.destination_id, dest[0], summary, amount, fee,
             net)).fetchone()
        out = withdrawal_json(row)
        await audit_control(
            conn, org_id=ctx.org_id, action="investor_withdrawal_requested",
            actor_email=ctx.user_email, user_id=ctx.user_id, severity="warning",
            withdrawal_id=out["id"], amount=out["amount"], fee=out["fee"],
            net_amount=out["net_amount"], destination=summary,
            summary=f"Withdrawal request: {amount:.2f} USD to {summary} from {ctx.user_email}")
        return out

    @router.post("/investor/withdrawals/{wd_id}/cancel", response_model=Dict[str, Any])
    async def cancel_withdrawal(wd_id: int,
                                ctx: OrgContext = Depends(require_org_role("investor")),
                                conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        current = conn.execute(
            "SELECT status, amount FROM withdrawals WHERE id = %s AND org_id = %s AND user_id = %s",
            (wd_id, ctx.org_id, ctx.user_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Withdrawal not found")
        if not can_transition("withdrawals", current[0], "cancelled"):
            raise HTTPException(status_code=409, detail=f"withdrawal is already {current[0]}")
        row = conn.execute(
            "UPDATE withdrawals SET status = 'cancelled', decided_by = %s, decided_at = now() "
            "WHERE id = %s AND org_id = %s AND status = %s "
            f"RETURNING {WITHDRAWAL_COLS}",
            (ctx.user_id, wd_id, ctx.org_id, current[0])).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        out = withdrawal_json(row)
        await audit_control(
            conn, org_id=ctx.org_id, action="investor_withdrawal_cancelled",
            actor_email=ctx.user_email, user_id=ctx.user_id,
            withdrawal_id=wd_id, amount=out["amount"])
        return out
```

- [ ] **Step 5: Admin routes for the withdrawal and destination queues**

At the top of `api/src/api/routes/portal_admin.py` make sure these imports are present (add the missing ones):

```python
from decimal import Decimal
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..db import get_conn
from ..portal_common import (
    DESTINATION_COLS, WITHDRAWAL_COLS, Decision, audit_control, destination_json,
    destination_summary, notify_investor, require_note_on_reject, settle, withdrawal_json)
from ..portal_ledger import LedgerError, can_transition, clean_text
from ..rbac import OrgContext, require_org_role
```

Next to the other body classes add (keep a single copy if Task 6 already declared it):

```python
class PaidBody(BaseModel):
    txid: str
```

Inside `create_portal_admin_router()`, immediately before its final `return router`, add:

```python
    # ------------------------------------------------------------ withdrawals

    @router.get("/withdrawals", response_model=List[Dict[str, Any]])
    async def withdrawal_queue(status: Optional[str] = None,
                               ctx: OrgContext = Depends(require_org_role("admin")),
                               conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "w.org_id = %s" + (" AND w.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        # A derived table so the bare column list of WITHDRAWAL_COLS is
        # unambiguous next to users (both carry id and created_at).
        rows = conn.execute(
            f"SELECT {WITHDRAWAL_COLS}, email, display_name FROM ("
            "  SELECT w.*, u.email, u.display_name FROM withdrawals w "
            f"  JOIN users u ON u.id = w.user_id WHERE {where}) AS q "
            "ORDER BY (status IN ('requested', 'approved')) DESC, created_at DESC, id DESC",
            params).fetchall()
        return [withdrawal_json(r) for r in rows]

    @router.post("/withdrawals/{wd_id}/decision", response_model=Dict[str, Any])
    async def decide_withdrawal(wd_id: int, body: Decision, http_request: Request,
                                ctx: OrgContext = Depends(require_org_role("admin")),
                                conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        new_status = body.status.strip().lower()
        if new_status not in ("approved", "rejected"):
            raise HTTPException(status_code=400, detail="status must be approved or rejected")
        try:
            note = require_note_on_reject(new_status, body.note)
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            "SELECT status, user_id, amount, destination_summary FROM withdrawals "
            "WHERE id = %s AND org_id = %s", (wd_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Withdrawal not found")
        status_now, user_id, amount, summary = current
        if not can_transition("withdrawals", status_now, new_status):
            raise HTTPException(status_code=409, detail=f"withdrawal is already {status_now}")
        row = conn.execute(
            "UPDATE withdrawals SET status = %s, decided_by = %s, decided_at = now(), "
            "decision_note = %s WHERE id = %s AND org_id = %s AND status = %s "
            f"RETURNING {WITHDRAWAL_COLS}",
            (new_status, ctx.user_id, note, wd_id, ctx.org_id, status_now)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        out = withdrawal_json(row)
        await audit_control(
            conn, org_id=ctx.org_id, action="investor_withdrawal_decided",
            actor_email=ctx.user_email, user_id=user_id,
            withdrawal_id=wd_id, status=new_status, note=note, amount=out["amount"])
        await notify_investor(
            conn, http_request, user_id,
            f"Your withdrawal of {amount:.2f} USD was {new_status}",
            f"Status: {new_status}\nAmount: {amount:.2f} USD\nTo: {summary}\n"
            f"Note: {note or '—'}\n\nOpen the portal for details.")
        return out

    @router.post("/withdrawals/{wd_id}/paid", response_model=Dict[str, Any])
    async def mark_withdrawal_paid(wd_id: int, body: PaidBody, http_request: Request,
                                   ctx: OrgContext = Depends(require_org_role("admin")),
                                   conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            txid = clean_text(body.txid, "txid")
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            "SELECT status, user_id, amount, destination_summary FROM withdrawals "
            "WHERE id = %s AND org_id = %s", (wd_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Withdrawal not found")
        status_now, user_id, amount, summary = current
        if not can_transition("withdrawals", status_now, "paid"):
            raise HTTPException(status_code=409,
                                detail=f"withdrawal is {status_now}, not approved")
        # Status change and ledger debit in ONE transaction; the unique
        # index on (ref_table, ref_id, wallet) makes a replay a no-op.
        with conn.transaction():
            row = conn.execute(
                "UPDATE withdrawals SET status = 'paid', paid_by = %s, paid_at = now(), "
                "txid = %s WHERE id = %s AND org_id = %s AND status = 'approved' "
                f"RETURNING {WITHDRAWAL_COLS}",
                (ctx.user_id, txid, wd_id, ctx.org_id)).fetchone()
            if not row:
                raise HTTPException(status_code=409, detail="decided by someone else")
            settle(conn, org_id=ctx.org_id, user_id=user_id, wallet="main",
                   amount=-Decimal(amount), kind="withdrawal", ref_table="withdrawals",
                   ref_id=wd_id)
        out = withdrawal_json(row)
        await audit_control(
            conn, org_id=ctx.org_id, action="investor_withdrawal_paid",
            actor_email=ctx.user_email, user_id=user_id,
            withdrawal_id=wd_id, txid=txid, amount=out["amount"])
        await notify_investor(
            conn, http_request, user_id,
            f"Your withdrawal of {amount:.2f} USD was paid",
            f"Amount: {amount:.2f} USD\nTo: {summary}\nTransaction: {txid}\n\n"
            "Open the portal for details.")
        return out

    # ---------------------------------------------------- payout destinations

    @router.get("/payout-destinations", response_model=List[Dict[str, Any]])
    async def destination_queue(status: Optional[str] = None,
                                ctx: OrgContext = Depends(require_org_role("admin")),
                                conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "d.org_id = %s" + (" AND d.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        rows = conn.execute(
            f"SELECT {DESTINATION_COLS}, email, display_name FROM ("
            "  SELECT d.*, u.email, u.display_name FROM payout_destinations d "
            f"  JOIN users u ON u.id = d.user_id WHERE {where}) AS q "
            "ORDER BY (status = 'pending') DESC, created_at DESC, id DESC", params).fetchall()
        return [destination_json(r, full=True) for r in rows]

    @router.post("/payout-destinations/{dest_id}/decision", response_model=Dict[str, Any])
    async def decide_destination(dest_id: int, body: Decision, http_request: Request,
                                 ctx: OrgContext = Depends(require_org_role("admin")),
                                 conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        new_status = body.status.strip().lower()
        if new_status not in ("approved", "rejected"):
            raise HTTPException(status_code=400, detail="status must be approved or rejected")
        try:
            note = require_note_on_reject(new_status, body.note)
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            "SELECT status, user_id, kind, details FROM payout_destinations "
            "WHERE id = %s AND org_id = %s", (dest_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Payout account not found")
        status_now, user_id, kind, details = current
        if not can_transition("payout_destinations", status_now, new_status):
            raise HTTPException(status_code=409,
                                detail=f"payout account is already {status_now}")
        row = conn.execute(
            "UPDATE payout_destinations SET status = %s, decided_by = %s, decided_at = now(), "
            "decision_note = %s WHERE id = %s AND org_id = %s AND status = %s "
            f"RETURNING {DESTINATION_COLS}",
            (new_status, ctx.user_id, note, dest_id, ctx.org_id, status_now)).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        out = destination_json(row, full=True)
        summary = destination_summary(kind, details)
        await audit_control(
            conn, org_id=ctx.org_id, action="investor_destination_decided",
            actor_email=ctx.user_email, user_id=user_id,
            destination_id=dest_id, status=new_status, note=note, destination=summary)
        await notify_investor(
            conn, http_request, user_id,
            f"Your payout account {summary} was {new_status}",
            f"Status: {new_status}\nPayout account: {summary}\nNote: {note or '—'}\n\n"
            "Open the portal for details.")
        return out
```

- [ ] **Step 6: Run the task's tests**

Run: `.venv/Scripts/python -m pytest tests/test_portal_withdrawals.py -q -p no:cacheprovider`
Expected: PASS, 21 passed.

- [ ] **Step 7: Run every portal test file together**

Run: `.venv/Scripts/python -m pytest tests/test_portal_ledger.py tests/test_portal_common.py tests/test_portal_methods.py tests/test_portal_deposits.py tests/test_portal_withdrawals.py tests/test_mpin_core.py tests/test_uploads.py -q -p no:cacheprovider`
Expected: all pass, 0 failed.

- [ ] **Step 8: Commit**

```bash
git add api/src/api/routes/portal_investor.py api/src/api/routes/portal_admin.py api/tests/test_portal_withdrawals.py
git commit -m "feat(api): payout destinations and withdrawals with MPIN step-up, floored cap, fees, admin decision and paid settlement

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 9: Transfers (pair rules, caps, instant wallet-to-wallet, admin decision with done settlement)

**Files:**
- Modify: `api/src/api/routes/portal_investor.py` (body classes `MoneyRef`, `TransferRequest`; `WALLET_LABELS`; helper `money_ref_label`; routes `GET/POST investor/transfers`, `POST investor/transfers/{id}/cancel`)
- Modify: `api/src/api/routes/portal_admin.py` (routes `GET transfers`, `POST transfers/{id}/decision`)
- Test: `api/tests/test_portal_transfers.py`

**Interfaces:**
- Consumes: `require_mpin`; `parse_amount`, `LedgerError`, `transfer_pair`, `TRANSFER_PAIRS`, `floor_cents`, `can_transition` (`portal_ledger`); `audit_control`, `notify_investor`, `linked_account`, `equity_for`, `wallet_figures`, `open_account_transfers_out`, `settle`, `transfer_json`, `TRANSFER_COLS`, `Decision`, `require_note_on_reject` (`portal_common`); `hourly`; test helpers `csrf`, `credit`, `link`.
- Produces: routes above; `WALLET_LABELS: dict[str, str]` and `money_ref_label(kind, account_id) -> str` (module-level in `portal_investor.py`, reused by Task 10's adjustment email).

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_portal_transfers.py`:

```python
"""Transfers: four allowed pairs. pamm->main and social->main settle at
once; main->account holds wallet money until an admin funds the broker
account and marks done; account->main is capped by the account's
available equity and credits the wallet when marked done."""
from decimal import Decimal

import httpx
import psycopg
import pytest
from conftest import default_mock_callback
from portal_helpers import credit, csrf, link

from api import ws as ws_module

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}


def W(wallet):
    return {"kind": "wallet", "wallet": wallet}


def A(account_id):
    return {"kind": "account", "account_id": account_id}


def _member(db, org_id, user, role):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
            (org_id, user["id"], role))


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


def _funded(org_client, make_user, login_as, db, *, main="1000", link_to=None, equity=None):
    """An investor with `main` credited; optionally linked to a freshly
    seeded follower account whose live equity the fake copier reports."""
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    if Decimal(main) != 0:
        credit(db, org_id, investor["id"], Decimal(main))
    if link_to is not None:
        seed(link_to, role="slave")
        link(db, org_id, investor["id"], link_to)
        if equity is None:
            _state(client, down=True)
        else:
            _state(client, {link_to: {"balance": float(equity), "equity": float(equity),
                                      "open_pnl": 0.0, "positions": []}})
    login_as(client, investor)
    return client, org_id, investor


def _transfer(client, org_id, source, target, amount, mpin="123456"):
    return client.post(f"/api/orgs/{org_id}/investor/transfers",
                       json={"source": source, "target": target, "amount": amount, "mpin": mpin},
                       headers=csrf(client))


def _decide(client, org_id, tr_id, status, note=None):
    return client.post(f"/api/orgs/{org_id}/transfers/{tr_id}/decision",
                       json={"status": status, "note": note}, headers=csrf(client))


def _events(db, org_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload FROM events WHERE org_id = %s ORDER BY id",
            (org_id,)).fetchall()


def _entries(db, org_id, user_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return [(r[0], float(r[1]), r[2], r[3], r[4]) for r in conn.execute(
            "SELECT wallet, amount, kind, ref_table, ref_id FROM wallet_entries "
            "WHERE org_id = %s AND user_id = %s ORDER BY id", (org_id, user_id)).fetchall()]


def _figures(db, org_id, user_id, wallet="main"):
    from api.portal_common import wallet_figures
    with psycopg.connect(db, autocommit=True) as conn:
        figures = wallet_figures(conn, org_id, user_id)[wallet]
    return {k: float(v) for k, v in figures.items()}


class _FakeAlerter:
    def __init__(self):
        self.sent = []

    async def send_to(self, to_addr, subject, text):
        self.sent.append((to_addr, subject))
        return True


# --------------------------------------------------------- wallet to wallet


def test_pamm_to_main_completes_at_once(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, main="0")
    credit(db, org_id, investor["id"], Decimal("300"), wallet="pamm")
    r = _transfer(client, org_id, W("pamm"), W("main"), "120.50")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "done" and body["done_at"] is not None and body["done_by"] is None
    assert body["source"]["kind"] == "wallet" and body["source"]["wallet"] == "pamm"
    assert body["target"]["kind"] == "wallet" and body["target"]["wallet"] == "main"
    assert body["amount"] == 120.5 and body["currency"] == "USD"
    assert body["equity_at_request"] is None and body["equity_verified"] is False
    assert _entries(db, org_id, investor["id"])[-2:] == [
        ("pamm", -120.5, "transfer", "transfers", body["id"]),
        ("main", 120.5, "transfer", "transfers", body["id"])]
    assert _figures(db, org_id, investor["id"], "pamm")["balance"] == 179.5
    assert _figures(db, org_id, investor["id"], "main")["available"] == 120.5
    assert client.get(f"/api/orgs/{org_id}/investor/transfers").json() == [body]
    severity, payload = _events(db, org_id)[-1]
    assert severity == "info" and payload["action"] == "investor_transfer_requested"
    assert payload["instant"] is True and payload["user_id"] == investor["id"]


def test_social_to_main_is_capped_by_the_social_wallet(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db)
    credit(db, org_id, investor["id"], Decimal("40"), wallet="social")
    r = _transfer(client, org_id, W("social"), W("main"), "40.01")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds what is available (40.00)"
    assert _transfer(client, org_id, W("social"), W("main"), "40").status_code == 201


@pytest.mark.parametrize("source, target", [
    (W("credit"), W("main")), (W("main"), W("pamm")), (W("main"), W("main")),
    (W("pamm"), W("social")), (A(1001), A(1001)), (W("main"), W("credit")),
])
def test_disallowed_pairs_are_refused(org_client, make_user, login_as, db, source, target):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001,
                                       equity="100")
    credit(db, org_id, investor["id"], Decimal("100"), wallet="credit")
    r = _transfer(client, org_id, source, target, "1")
    assert r.status_code == 400 and r.json()["detail"] == "that transfer is not allowed"


def test_a_wrong_mpin_is_refused_first(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db)
    r = _transfer(client, org_id, W("credit"), W("main"), "1", mpin="000000")
    assert r.status_code == 401 and r.json()["attempts_left"] == 4


# --------------------------------------------------------- wallet to account


def test_account_pairs_need_the_linked_account(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db)
    r = _transfer(client, org_id, W("main"), A(1001), "10")
    assert r.status_code == 409 and r.json()["detail"] == "no account linked yet"
    _, _, seed = org_client
    seed(1001, role="slave")
    seed(1002, role="slave")
    link(db, org_id, investor["id"], 1001)
    _state(client, {1001: {"balance": 0.0, "equity": 0.0, "open_pnl": 0.0, "positions": []}})
    r = _transfer(client, org_id, W("main"), A(1002), "10")
    assert r.status_code == 404 and r.json()["detail"] == "Account not found"
    r = _transfer(client, org_id, A(1002), W("main"), "10")
    assert r.status_code == 404 and r.json()["detail"] == "Account not found"
    assert _transfer(client, org_id, W("main"), A(1001), "10").status_code == 201


def test_main_to_account_holds_the_amount_and_caps_at_available(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001,
                                       equity="0")
    r = _transfer(client, org_id, W("main"), A(1001), "1000.01")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds what is available (1000.00)"
    r = _transfer(client, org_id, W("main"), A(1001), "600")
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "requested" and body["done_at"] is None
    assert body["target"] == {"kind": "account", "account_id": 1001} or (
        body["target"]["kind"] == "account" and body["target"]["account_id"] == 1001)
    assert body["equity_at_request"] is None and body["equity_verified"] is False
    assert _figures(db, org_id, investor["id"]) == {
        "balance": 1000.0, "on_hold": 600.0, "available": 400.0}
    assert _entries(db, org_id, investor["id"])[-1][2] == "adjustment", "nothing settled yet"
    r = _transfer(client, org_id, W("main"), A(1001), "500")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds what is available (400.00)"
    severity, payload = _events(db, org_id)[-1]
    assert severity == "warning" and payload["action"] == "investor_transfer_requested"
    assert payload["summary"] == (
        "Transfer request: 600.00 USD from My wallet to trading account 1001 from inv@example.com")

    r = client.post(f"/api/orgs/{org_id}/investor/transfers/{body['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert _figures(db, org_id, investor["id"])["on_hold"] == 0.0
    r = client.post(f"/api/orgs/{org_id}/investor/transfers/{body['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "transfer is already cancelled"
    assert _events(db, org_id)[-1][1]["action"] == "investor_transfer_cancelled"


# --------------------------------------------------------- account to wallet


def test_account_to_main_caps_at_the_accounts_available_equity(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001,
                                       equity="5120.5")
    r = _transfer(client, org_id, A(1001), W("main"), "100")
    assert r.status_code == 201
    assert r.json()["equity_at_request"] == 5120.5 and r.json()["equity_verified"] is True
    assert r.json()["status"] == "requested"
    assert _figures(db, org_id, investor["id"])["on_hold"] == 0.0, "the account holds it, not main"
    r = _transfer(client, org_id, A(1001), W("main"), "5020.51")
    assert r.status_code == 400
    assert r.json()["detail"] == "amount exceeds the account's available equity (5020.50)"
    assert _transfer(client, org_id, A(1001), W("main"), "5020.50").status_code == 201


def test_unknown_equity_is_accepted_but_flagged(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001)
    r = _transfer(client, org_id, A(1001), W("main"), "99999")
    assert r.status_code == 201
    assert r.json()["equity_verified"] is False and r.json()["equity_at_request"] is None


def test_last_known_equity_caps_but_is_never_verified(org_client, make_user, login_as, db):
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
    r = _transfer(client, org_id, A(int(aid)), W("main"), "99999")
    assert r.status_code == 400 and "4990.25" in r.json()["detail"]
    r = _transfer(client, org_id, A(int(aid)), W("main"), "1000")
    assert r.status_code == 201
    assert r.json()["equity_at_request"] == 4990.25 and r.json()["equity_verified"] is False


# ------------------------------------------------------------------- admin


def test_admin_approves_then_marks_done_and_main_is_debited(org_client, make_user, login_as, db,
                                                            monkeypatch):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001,
                                       equity="0")
    tr = _transfer(client, org_id, W("main"), A(1001), "600").json()
    login_as(client, ADMIN)
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    queue = client.get(f"/api/orgs/{org_id}/transfers?status=requested").json()
    assert [t["id"] for t in queue] == [tr["id"]] and queue[0]["email"] == "inv@example.com"

    r = _decide(client, org_id, tr["id"], "approved")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["decided_at"] is not None and r.json()["done_at"] is None
    assert _figures(db, org_id, investor["id"])["on_hold"] == 600.0, "approved still holds"
    r = _decide(client, org_id, tr["id"], "done")
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert r.json()["done_at"] is not None and r.json()["done_by"] is not None
    assert _entries(db, org_id, investor["id"])[-1] == (
        "main", -600.0, "transfer", "transfers", tr["id"])
    assert _figures(db, org_id, investor["id"]) == {
        "balance": 400.0, "on_hold": 0.0, "available": 400.0}
    r = _decide(client, org_id, tr["id"], "done")
    assert r.status_code == 409 and r.json()["detail"] == "transfer is already done"
    r = _decide(client, org_id, tr["id"], "rejected", "no")
    assert r.status_code == 409
    assert len([e for e in _entries(db, org_id, investor["id"]) if e[2] == "transfer"]) == 1
    actions = [p["action"] for _, p in _events(db, org_id)]
    assert actions[-3:] == ["investor_transfer_requested", "investor_transfer_decided",
                            "investor_transfer_decided"]
    assert fake.sent == [("inv@example.com", "Your transfer of 600.00 USD was approved"),
                         ("inv@example.com", "Your transfer of 600.00 USD was done")]


def test_account_to_main_done_credits_main(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, main="0",
                                       link_to=1001, equity="800")
    tr = _transfer(client, org_id, A(1001), W("main"), "100").json()
    login_as(client, ADMIN)
    r = _decide(client, org_id, tr["id"], "done")
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert r.json()["decided_by"] is not None
    assert _entries(db, org_id, investor["id"]) == [
        ("main", 100.0, "transfer", "transfers", tr["id"])]
    assert _figures(db, org_id, investor["id"])["available"] == 100.0


def test_a_rejection_needs_a_note_and_releases_the_hold(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001,
                                       equity="0")
    tr = _transfer(client, org_id, W("main"), A(1001), "300").json()
    login_as(client, ADMIN)
    r = _decide(client, org_id, tr["id"], "rejected")
    assert r.status_code == 400 and "note" in r.json()["detail"]
    r = _decide(client, org_id, tr["id"], "paid")
    assert r.status_code == 400 and r.json()["detail"] == "status must be approved, done or rejected"
    assert _decide(client, org_id, 999, "approved").status_code == 404
    r = _decide(client, org_id, tr["id"], "rejected", "broker account closed")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert _figures(db, org_id, investor["id"])["on_hold"] == 0.0
    assert _entries(db, org_id, investor["id"])[-1][2] == "adjustment", "nothing settled"


def test_ten_requests_an_hour_then_429(org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, main="0")
    credit(db, org_id, investor["id"], Decimal("20"), wallet="pamm")
    for _ in range(10):
        assert _transfer(client, org_id, W("pamm"), W("main"), "1").status_code == 201
    r = _transfer(client, org_id, W("pamm"), W("main"), "1")
    assert r.status_code == 429 and r.json()["detail"] == "too many requests; try again later"


def test_investors_only_see_their_own_transfers_and_cannot_work_the_queue(
        org_client, make_user, login_as, db):
    client, org_id, investor = _funded(org_client, make_user, login_as, db, link_to=1001,
                                       equity="0")
    tr = _transfer(client, org_id, W("main"), A(1001), "10").json()
    other = make_user(email="other@example.com")
    _member(db, org_id, other, "investor")
    login_as(client, other)
    assert client.get(f"/api/orgs/{org_id}/investor/transfers").json() == []
    r = client.post(f"/api/orgs/{org_id}/investor/transfers/{tr['id']}/cancel",
                    headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Transfer not found"
    assert _decide(client, org_id, tr["id"], "approved").status_code == 403
    assert client.get(f"/api/orgs/{org_id}/transfers").status_code == 403
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_portal_transfers.py -q -p no:cacheprovider`
Expected: 19 FAIL (6 parametrised cases of `test_disallowed_pairs_are_refused` plus 13 others), each on a status assertion such as `assert 404 == 201` because the routes do not exist.

- [ ] **Step 3: Body classes, labels and imports in `portal_investor.py`**

Extend the import block of `api/src/api/routes/portal_investor.py` so it also has:

```python
from ..portal_common import (
    DESTINATION_COLS, TRANSFER_COLS, WITHDRAWAL_COLS, audit_control, destination_json,
    destination_summary, equity_for, fee_for, linked_account, open_account_transfers_out,
    portal_settings, settle, transfer_json, wallet_figures, withdrawal_json)
from ..portal_ledger import (
    WALLETS, LedgerError, can_transition, clean_text, floor_cents, parse_amount,
    transfer_pair)
```

After `class WithdrawalRequest(BaseModel):` add (single copy, as in Task 8):

```python
class MoneyRef(BaseModel):
    kind: str
    wallet: Optional[str] = None
    account_id: Optional[int] = None


class TransferRequest(BaseModel):
    source: MoneyRef
    target: MoneyRef
    amount: Any
    mpin: Any = None
```

After the `clean_destination_details` helper add:

```python
WALLET_LABELS: Dict[str, str] = {
    "main": "My wallet", "credit": "Credit wallet", "pamm": "PAMM wallet",
    "social": "Social wallet"}


def money_ref_label(kind: str, account_id: Optional[int]) -> str:
    """'My wallet' / 'PAMM wallet' / … or 'trading account <id>', for audit
    summaries and investor emails."""
    if kind == "account":
        return f"trading account {account_id}"
    return WALLET_LABELS.get(kind, kind)
```

- [ ] **Step 4: Investor transfer routes**

Inside `create_portal_investor_router()`, before `return router`, add:

```python
    # -------------------------------------------------------------- transfers

    @router.get("/investor/transfers", response_model=List[Dict[str, Any]])
    async def my_transfers(ctx: OrgContext = Depends(require_org_role("investor")),
                           conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            f"SELECT {TRANSFER_COLS} FROM transfers "
            "WHERE org_id = %s AND user_id = %s ORDER BY created_at DESC, id DESC",
            (ctx.org_id, ctx.user_id)).fetchall()
        return [transfer_json(r) for r in rows]

    @router.post("/investor/transfers", status_code=201, response_model=Dict[str, Any])
    async def request_transfer(body: TransferRequest, http_request: Request,
                               ctx: OrgContext = Depends(require_org_role("investor")),
                               conn: psycopg.Connection = Depends(get_conn)):
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        try:
            amount = parse_amount(body.amount)
            source_kind, target_kind = transfer_pair(body.source.model_dump(),
                                                     body.target.model_dump())
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        account_id: Optional[int] = None
        if "account" in (source_kind, target_kind):
            account_id = linked_account(conn, ctx.org_id, ctx.user_id)
            if account_id is None:
                raise HTTPException(status_code=409, detail="no account linked yet")
            named = body.source.account_id if source_kind == "account" else body.target.account_id
            if named != account_id:
                raise HTTPException(status_code=404, detail="Account not found")
        equity: Optional[Decimal] = None
        equity_source = "unknown"
        if source_kind == "account":
            equity, equity_source, _positions = await equity_for(
                http_request, conn, ctx.org_id, account_id)
            if equity is not None:
                account_available = floor_cents(
                    equity - open_account_transfers_out(conn, ctx.org_id, ctx.user_id, account_id))
                if amount > account_available:
                    raise HTTPException(
                        status_code=400,
                        detail="amount exceeds the account's available equity "
                               f"({account_available:.2f})")
        else:
            available = wallet_figures(conn, ctx.org_id, ctx.user_id)[source_kind]["available"]
            if amount > available:
                raise HTTPException(
                    status_code=400,
                    detail=f"amount exceeds what is available ({available:.2f})")
        if hourly.is_limited(f"portal-transfer:{ctx.org_id}:{ctx.user_id}"):
            raise HTTPException(status_code=429, detail="too many requests; try again later")
        instant = source_kind != "account" and target_kind != "account"
        source_wallet = None if source_kind == "account" else source_kind
        target_wallet = None if target_kind == "account" else target_kind
        # Wallet-to-wallet moves need no admin: inserted as done and settled
        # in the same transaction. Account moves wait for the admin.
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, "
                "source_account_id, target_kind, target_wallet, target_account_id, amount, "
                "status, equity_at_request, equity_verified, done_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "CASE WHEN %s THEN now() END) "
                f"RETURNING {TRANSFER_COLS}",
                (ctx.org_id, ctx.user_id,
                 "wallet" if source_wallet else "account", source_wallet,
                 account_id if source_kind == "account" else None,
                 "wallet" if target_wallet else "account", target_wallet,
                 account_id if target_kind == "account" else None,
                 amount, "done" if instant else "requested", equity,
                 equity_source == "live", instant)).fetchone()
            out = transfer_json(row)
            if instant:
                settle(conn, org_id=ctx.org_id, user_id=ctx.user_id, wallet=source_wallet,
                       amount=-amount, kind="transfer", ref_table="transfers", ref_id=out["id"])
                settle(conn, org_id=ctx.org_id, user_id=ctx.user_id, wallet=target_wallet,
                       amount=amount, kind="transfer", ref_table="transfers", ref_id=out["id"])
        from_label = money_ref_label(source_kind, account_id)
        to_label = money_ref_label(target_kind, account_id)
        await audit_control(
            conn, org_id=ctx.org_id, action="investor_transfer_requested",
            actor_email=ctx.user_email, user_id=ctx.user_id,
            severity="info" if instant else "warning",
            account_id=account_id, transfer_id=out["id"], amount=out["amount"],
            source=from_label, target=to_label, instant=instant,
            equity_verified=out["equity_verified"],
            summary=f"Transfer request: {amount:.2f} USD from {from_label} to {to_label} "
                    f"from {ctx.user_email}")
        return out

    @router.post("/investor/transfers/{tr_id}/cancel", response_model=Dict[str, Any])
    async def cancel_transfer(tr_id: int,
                              ctx: OrgContext = Depends(require_org_role("investor")),
                              conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        current = conn.execute(
            "SELECT status FROM transfers WHERE id = %s AND org_id = %s AND user_id = %s",
            (tr_id, ctx.org_id, ctx.user_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Transfer not found")
        if not can_transition("transfers", current[0], "cancelled"):
            raise HTTPException(status_code=409, detail=f"transfer is already {current[0]}")
        row = conn.execute(
            "UPDATE transfers SET status = 'cancelled', decided_by = %s, decided_at = now() "
            "WHERE id = %s AND org_id = %s AND status = %s "
            f"RETURNING {TRANSFER_COLS}",
            (ctx.user_id, tr_id, ctx.org_id, current[0])).fetchone()
        if not row:
            raise HTTPException(status_code=409, detail="decided by someone else")
        out = transfer_json(row)
        await audit_control(
            conn, org_id=ctx.org_id, action="investor_transfer_cancelled",
            actor_email=ctx.user_email, user_id=ctx.user_id,
            transfer_id=tr_id, amount=out["amount"])
        return out
```

- [ ] **Step 5: Admin transfer routes**

Extend the `portal_common` import in `api/src/api/routes/portal_admin.py` with `TRANSFER_COLS` and `transfer_json`, and add `from .portal_investor import money_ref_label` below the `..rbac` import. Then, inside `create_portal_admin_router()` before `return router`, add:

```python
    # -------------------------------------------------------------- transfers

    @router.get("/transfers", response_model=List[Dict[str, Any]])
    async def transfer_queue(status: Optional[str] = None,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn)) -> List[Dict[str, Any]]:
        where = "t.org_id = %s" + (" AND t.status = %s" if status else "")
        params = (ctx.org_id, status) if status else (ctx.org_id,)
        rows = conn.execute(
            f"SELECT {TRANSFER_COLS}, email, display_name FROM ("
            "  SELECT t.*, u.email, u.display_name FROM transfers t "
            f"  JOIN users u ON u.id = t.user_id WHERE {where}) AS q "
            "ORDER BY (status IN ('requested', 'approved')) DESC, created_at DESC, id DESC",
            params).fetchall()
        return [transfer_json(r) for r in rows]

    @router.post("/transfers/{tr_id}/decision", response_model=Dict[str, Any])
    async def decide_transfer(tr_id: int, body: Decision, http_request: Request,
                              ctx: OrgContext = Depends(require_org_role("admin")),
                              conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        new_status = body.status.strip().lower()
        if new_status not in ("approved", "done", "rejected"):
            raise HTTPException(status_code=400,
                                detail="status must be approved, done or rejected")
        try:
            note = require_note_on_reject(new_status, body.note)
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        current = conn.execute(
            "SELECT status, user_id, source_kind, source_wallet, source_account_id, "
            "target_kind, target_wallet, target_account_id, amount FROM transfers "
            "WHERE id = %s AND org_id = %s", (tr_id, ctx.org_id)).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="Transfer not found")
        (status_now, user_id, source_kind, source_wallet, source_account, target_kind,
         target_wallet, target_account, amount) = current
        if not can_transition("transfers", status_now, new_status):
            raise HTTPException(status_code=409, detail=f"transfer is already {status_now}")
        amount = Decimal(amount)
        with conn.transaction():
            if new_status == "done":
                # Done straight from requested also records the decision;
                # done after approved keeps the earlier decision.
                row = conn.execute(
                    "UPDATE transfers SET status = 'done', "
                    "decided_by = COALESCE(decided_by, %s), "
                    "decided_at = COALESCE(decided_at, now()), "
                    "decision_note = COALESCE(%s, decision_note), "
                    "done_by = %s, done_at = now() "
                    "WHERE id = %s AND org_id = %s AND status = %s "
                    f"RETURNING {TRANSFER_COLS}",
                    (ctx.user_id, note, ctx.user_id, tr_id, ctx.org_id, status_now)).fetchone()
            else:
                row = conn.execute(
                    "UPDATE transfers SET status = %s, decided_by = %s, decided_at = now(), "
                    "decision_note = %s WHERE id = %s AND org_id = %s AND status = %s "
                    f"RETURNING {TRANSFER_COLS}",
                    (new_status, ctx.user_id, note, tr_id, ctx.org_id, status_now)).fetchone()
            if not row:
                raise HTTPException(status_code=409, detail="decided by someone else")
            if new_status == "done":
                if source_wallet is not None:
                    settle(conn, org_id=ctx.org_id, user_id=user_id, wallet=source_wallet,
                           amount=-amount, kind="transfer", ref_table="transfers", ref_id=tr_id)
                if target_wallet is not None:
                    settle(conn, org_id=ctx.org_id, user_id=user_id, wallet=target_wallet,
                           amount=amount, kind="transfer", ref_table="transfers", ref_id=tr_id)
        out = transfer_json(row)
        from_label = money_ref_label(source_kind if source_kind == "account" else source_wallet,
                                     source_account)
        to_label = money_ref_label(target_kind if target_kind == "account" else target_wallet,
                                   target_account)
        await audit_control(
            conn, org_id=ctx.org_id, action="investor_transfer_decided",
            actor_email=ctx.user_email, user_id=user_id,
            account_id=source_account if source_account is not None else target_account,
            transfer_id=tr_id, status=new_status, note=note, amount=out["amount"],
            source=from_label, target=to_label)
        await notify_investor(
            conn, http_request, user_id,
            f"Your transfer of {amount:.2f} USD was {new_status}",
            f"Status: {new_status}\nAmount: {amount:.2f} USD\nFrom: {from_label}\n"
            f"To: {to_label}\nNote: {note or '—'}\n\nOpen the portal for details.")
        return out
```

- [ ] **Step 6: Run the task's tests**

Run: `.venv/Scripts/python -m pytest tests/test_portal_transfers.py -q -p no:cacheprovider`
Expected: PASS, 19 passed.

- [ ] **Step 7: Run every portal test file together**

Run: `.venv/Scripts/python -m pytest tests/test_portal_ledger.py tests/test_portal_common.py tests/test_portal_methods.py tests/test_portal_deposits.py tests/test_portal_withdrawals.py tests/test_portal_transfers.py tests/test_mpin_core.py tests/test_uploads.py -q -p no:cacheprovider`
Expected: all pass, 0 failed.

- [ ] **Step 8: Commit**

```bash
git add api/src/api/routes/portal_investor.py api/src/api/routes/portal_admin.py api/tests/test_portal_transfers.py
git commit -m "feat(api): transfers -- pair rules, wallet and account caps, instant wallet moves, admin decision with done settlement

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 10: Summary, wallet entries, investors list, adjustments, requests summary; retire the old router

> **Controller ruling (cross-part):** Task 5 already exports `account_card(conn, org_id, account_id)`, `org_state(client, cfg, org_id)` and `equity_from(state, conn, account_id)` from `api/src/api/portal_common.py` with the bodies this task re-creates as `_account_card`, `_org_state` and `_equity_from`. Do not define them again: import them under the underscore names (`from ..portal_common import account_card as _account_card, org_state as _org_state, equity_from as _equity_from`) and delete the private definitions from the code below; every call site stays as written. Likewise, where Tasks 6–7 wrote the routers with `from .. import portal_common as pc` and `pc.`-prefixed calls, keep that style: add the bare names this task uses to an explicit `from ..portal_common import (...)` block, or prefix them with `pc.`; both are correct as long as `tsc`-style strictness is not at stake here (Python does not care). The reviewer confirmed the bodies are byte-identical.

**Files:**
- Modify: `api/src/api/main.py` (drop the two old `include_router` lines and their import)
- Delete: `api/src/api/routes/investor.py`
- Delete: `api/src/api/investor_ledger.py`
- Delete: `api/tests/test_investor_ledger.py`
- Modify: `api/src/api/routes/portal_investor.py` (helpers `_account_card`, `_require_linked`, `pending_counts`, `entries_page`; routes `GET investor/summary`, `GET investor/wallet-entries`, `GET investor/positions`, `GET investor/analytics`, `GET investor/history/{kind}`)
- Modify: `api/src/api/routes/portal_admin.py` (body classes `AdjustmentBody`, `LinkBody`; helpers `_org_state`, `_equity_from`, `_require_investor`; routes `GET investors`, `PUT investors/{user_id}/account`, `GET investors/{user_id}/wallet-entries`, `POST investors/{user_id}/adjustments`, `GET requests/summary`)
- Modify: `api/src/api/alerts.py` (`ALERT_RULES`), `api/src/api/telegram.py` (`TELEGRAM_RULES`)
- Modify: `api/tests/test_rbac_matrix.py` (rows and seeds for the portal routes)
- Test: `api/tests/test_portal_summary.py`

**Interfaces:**
- Consumes: `require_mpin`; `WALLETS`, `parse_amount`, `clean_text`, `LedgerError`, `floor_cents`, `money` (`portal_ledger`); `audit_control`, `notify_investor`, `linked_account`, `equity_for`, `wallet_figures`, `open_account_transfers_out`, `net_funded`, `portal_settings`, `entry_json`, `ENTRY_COLS` (`portal_common`); `WALLET_LABELS` (Task 9); `_proxy_to_copier`, `COPIER_SLOW_COMMAND_TIMEOUT_S` (`routes/settings_control`), `MT5_OFFLINE_AFTER_S` (`routes/mt5`); test helpers `csrf`, `credit`, `approved_destination`, `link`, `add_method`.
- Produces: the complete API of spec section 8; `pending_counts(conn, org_id, user_id) -> dict` and `entries_page(conn, org_id, user_id, *, wallet, kind, date_from, date_to, limit, before) -> dict` (module-level in `portal_investor.py`, imported by `portal_admin.py`); `ALERT_RULES` / `TELEGRAM_RULES` with the six warning actions.

- [ ] **Step 1: Write the failing tests**

Create `api/tests/test_portal_summary.py`:

```python
"""The investor's summary and ledger pages, the admin's investor list,
ledger view, adjustments and requests summary, the read-throughs that
moved from the old router, the alert rules, and the proof that the old
router and ledger are gone."""
import importlib.util
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import psycopg
import pytest
from conftest import default_mock_callback
from portal_helpers import add_method, approved_destination, credit, csrf, link

from api import ws as ws_module
from api.alerts import ALERT_RULES
from api.telegram import TELEGRAM_RULES

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}
WARNING_ACTIONS = ("investor_deposit_noticed", "investor_withdrawal_requested",
                   "investor_transfer_requested", "investor_destination_added",
                   "investor_ledger_adjusted", "payment_method_changed")


def _member(db, org_id, user, role):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO org_memberships (org_id, user_id, role) VALUES (%s, %s, %s)",
            (org_id, user["id"], role))


def _state(client, accounts=None, down=False):
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


def _investor(org_client, make_user, login_as, db, email="inv@example.com", display_name="User"):
    client, org_id, seed = org_client
    investor = make_user(email=email, display_name=display_name)
    _member(db, org_id, investor, "investor")
    login_as(client, investor)
    return client, org_id, investor


def _insert_transfer(db, org_id, user_id, *, source, target, amount, status, account_id=None):
    """source/target are wallet names or 'account'."""
    with psycopg.connect(db, autocommit=True) as conn:
        (tr_id,) = conn.execute(
            "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, "
            "source_account_id, target_kind, target_wallet, target_account_id, amount, status, "
            "done_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "CASE WHEN %s = 'done' THEN now() END) RETURNING id",
            (org_id, user_id,
             "account" if source == "account" else "wallet",
             None if source == "account" else source,
             account_id if source == "account" else None,
             "account" if target == "account" else "wallet",
             None if target == "account" else target,
             account_id if target == "account" else None,
             Decimal(amount), status, status)).fetchone()
    return tr_id


def _events(db, org_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload FROM events WHERE org_id = %s ORDER BY id",
            (org_id,)).fetchall()


class _FakeAlerter:
    def __init__(self):
        self.sent = []

    async def send_to(self, to_addr, subject, text):
        self.sent.append((to_addr, subject))
        return True


ZERO = {"balance": 0.0, "on_hold": 0.0, "available": 0.0}


# ----------------------------------------------------------------- summary


def test_summary_before_an_account_is_linked(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db,
                                         display_name="Sherwyn Joel")
    r = client.get(f"/api/orgs/{org_id}/investor/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["org"] == {"id": org_id, "name": "Desk"} and body["currency"] == "USD"
    assert body["investor"]["display_name"] == "Sherwyn Joel"
    assert body["investor"]["first_name"] == "Sherwyn"
    assert body["investor"]["member_since"] is not None
    assert body["wallets"] == {"main": ZERO, "credit": ZERO, "pamm": ZERO, "social": ZERO}
    assert body["totals"] == {"deposited": 0.0, "withdrawn": 0.0, "transferred_in": 0.0,
                              "transferred_out": 0.0}
    assert body["cash_flow"] == []
    assert body["pending"] == {"deposits": 0, "withdrawals": 0, "transfers": 0,
                               "payout_destinations": 0}
    assert body["deposits_open"] is False
    assert body["withdrawal_rules"] == {"min": 0.0, "fee_pct": 0.0}
    assert body["link_state"] == "unlinked" and body["account"] is None
    assert body["equity_source"] == "unknown" and body["equity"] is None
    assert body["net_funded"] == 0.0 and body["profit"] is None
    assert body["account_available"] is None and body["open_positions"] == 0


def test_summary_figures_from_the_ledger_and_live_equity(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    uid = investor["id"]
    credit(db, org_id, uid, Decimal("5120.50"), kind="deposit")
    credit(db, org_id, uid, Decimal("-100"), kind="withdrawal")
    credit(db, org_id, uid, Decimal("30"), wallet="pamm", kind="bonus")
    seed(1001, role="slave")
    link(db, org_id, uid, 1001)
    _insert_transfer(db, org_id, uid, source="main", target="account", amount="2000",
                     status="done", account_id=1001)
    _insert_transfer(db, org_id, uid, source="account", target="main", amount="20.50",
                     status="requested", account_id=1001)
    _insert_transfer(db, org_id, uid, source="main", target="account", amount="100",
                     status="requested", account_id=1001)
    dest_id = approved_destination(db, org_id, uid)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
            "destination_summary, amount, fee, net_amount, status) "
            "VALUES (%s, %s, %s, 'crypto', 'TRC20 T…st', 50, 0, 50, 'approved')",
            (org_id, uid, dest_id))
        conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details) "
            "VALUES (%s, %s, 'crypto', 'Pending', '{\"coin\": \"USDT\", \"network\": \"TRC20\", "
            "\"address\": \"TPending\"}')", (org_id, uid))
    _state(client, {1001: {"balance": 2000.0, "equity": 2120.5, "open_pnl": 120.5,
                           "positions": [{"position_id": 7, "symbol": "XAUUSD", "side": "BUY",
                                          "volume": 1, "entry_price": 4350.0,
                                          "pnl_quote": 120.5}]}})
    login_as(client, investor)
    body = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    # main: 5120.50 - 100 - 2000 (done transfer is settled by the caller in
    # real life; here the ledger row is what counts) -> the ledger only
    # holds what was written: 5020.50, with 150 on hold (100 transfer + 50
    # withdrawal).
    assert body["wallets"]["main"] == {"balance": 5020.5, "on_hold": 150.0, "available": 4870.5}
    assert body["wallets"]["pamm"] == {"balance": 30.0, "on_hold": 0.0, "available": 30.0}
    assert body["totals"] == {"deposited": 5120.5, "withdrawn": 100.0,
                              "transferred_in": 0.0, "transferred_out": 2000.0}
    today = datetime.now(timezone.utc).date().isoformat()
    assert body["cash_flow"] == [{"date": today, "deposits": 5120.5, "withdrawals": 100.0}]
    assert body["pending"] == {"deposits": 0, "withdrawals": 1, "transfers": 2,
                               "payout_destinations": 1}
    assert body["link_state"] == "linked" and body["account"]["account_id"] == 1001
    assert body["equity"] == 2120.5 and body["equity_source"] == "live"
    assert body["net_funded"] == 2000.0 and body["profit"] == 120.5
    assert body["account_available"] == 2100.0, "equity minus the open account->wallet transfer"
    assert body["open_positions"] == 1


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
    assert body["account_available"] == 4990.25 and body["profit"] == 4990.25


def test_deposits_open_and_withdrawal_rules_reflect_admin_settings(
        org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    add_method(db, org_id)
    r = client.put(f"/api/orgs/{org_id}/portal-settings",
                   json={"withdrawal_min": "50", "withdrawal_fee_pct": "2.5"},
                   headers=csrf(client))
    assert r.status_code == 200
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    body = client.get(f"/api/orgs/{org_id}/investor/summary").json()
    assert body["deposits_open"] is True
    assert body["withdrawal_rules"] == {"min": 50.0, "fee_pct": 2.5}


# ---------------------------------------------------------- wallet entries


def test_wallet_entries_page_filters_and_cursor(org_client, make_user, login_as, db):
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    uid = investor["id"]
    ids = [credit(db, org_id, uid, Decimal("10")), credit(db, org_id, uid, Decimal("20")),
           credit(db, org_id, uid, Decimal("30")),
           credit(db, org_id, uid, Decimal("5"), wallet="pamm", kind="deposit")]
    base = f"/api/orgs/{org_id}/investor/wallet-entries"
    page = client.get(base).json()
    assert [e["id"] for e in page["entries"]] == list(reversed(ids))
    assert page["has_more"] is False and page["next_before"] is None
    first = page["entries"][0]
    assert first["wallet"] == "pamm" and first["amount"] == 5.0 and first["kind"] == "deposit"
    assert first["currency"] == "USD" and first["ref_table"] is None and first["note"] is None

    page = client.get(f"{base}?limit=2").json()
    assert [e["id"] for e in page["entries"]] == [ids[3], ids[2]]
    assert page["has_more"] is True and page["next_before"] == ids[2]
    page = client.get(f"{base}?limit=2&before={page['next_before']}").json()
    assert [e["id"] for e in page["entries"]] == [ids[1], ids[0]] and page["has_more"] is False

    assert [e["id"] for e in client.get(f"{base}?wallet=pamm").json()["entries"]] == [ids[3]]
    assert [e["id"] for e in client.get(f"{base}?kind=adjustment").json()["entries"]] == \
        [ids[2], ids[1], ids[0]]
    today = datetime.now(timezone.utc).date()
    assert len(client.get(f"{base}?from={today}&to={today}").json()["entries"]) == 4
    assert client.get(f"{base}?to={today - timedelta(days=1)}").json()["entries"] == []
    assert client.get(f"{base}?from={today + timedelta(days=1)}").json()["entries"] == []
    assert len(client.get(f"{base}?limit=999").json()["entries"]) == 4

    r = client.get(f"{base}?wallet=gold")
    assert r.status_code == 400
    assert r.json()["detail"] == "wallet must be one of main, credit, pamm, social"
    r = client.get(f"{base}?kind=refund")
    assert r.status_code == 400 and r.json()["detail"].startswith("kind must be one of")
    r = client.get(f"{base}?from=2026-13-01")
    assert r.status_code == 400 and r.json()["detail"] == "from must be a date (YYYY-MM-DD)"


# ----------------------------------------------------------- admin: list


def test_the_investor_list_has_figures_and_asks_the_copier_once(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    seed(1002, role="slave")
    inv1 = make_user(email="inv1@example.com", display_name="Ann")
    inv2 = make_user(email="inv2@example.com", display_name="Bob")
    viewer = make_user(email="v@example.com")
    _member(db, org_id, inv1, "investor")
    _member(db, org_id, inv2, "investor")
    _member(db, org_id, viewer, "viewer")
    link(db, org_id, inv1["id"], 1001)
    credit(db, org_id, inv1["id"], Decimal("500"))
    credit(db, org_id, inv1["id"], Decimal("25"), wallet="credit", kind="bonus")
    _insert_transfer(db, org_id, inv1["id"], source="main", target="account", amount="100",
                     status="requested", account_id=1001)
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details) "
            "VALUES (%s, %s, 'crypto', 'P', '{\"coin\": \"USDT\", \"network\": \"TRC20\", "
            "\"address\": \"T1\"}')", (org_id, inv2["id"]))
    calls = {"state": 0}

    def callback(request):
        url = str(request.url)
        if "copier.test" in url and "/state" in url:
            calls["state"] += 1
            return httpx.Response(200, json={
                "status": "ok",
                "accounts": {"1001": {"balance": 100.0, "equity": 100.0, "open_pnl": 0.0,
                                      "positions": []}},
                "master_positions": [], "pending_orders": [], "drift": []})
        return default_mock_callback(request)
    client.app.state.mock_transport.set_callback(callback)

    rows = client.get(f"/api/orgs/{org_id}/investors").json()
    assert calls["state"] == 1
    assert [r["email"] for r in rows] == ["inv1@example.com", "inv2@example.com"]
    ann, bob = rows
    assert ann["display_name"] == "Ann" and ann["joined_at"] is not None
    assert ann["account_id"] == 1001 and ann["nickname"] is None
    assert ann["equity"] == 100.0 and ann["equity_source"] == "live"
    assert ann["balances"] == {"main": 500.0, "credit": 25.0, "pamm": 0.0, "social": 0.0}
    assert ann["on_hold"] == 100.0 and ann["available"] == 400.0
    assert ann["pending"] == {"deposits": 0, "withdrawals": 0, "transfers": 1,
                              "payout_destinations": 0}
    assert bob["account_id"] is None and bob["equity"] is None
    assert bob["equity_source"] == "unknown"
    assert bob["balances"] == {"main": 0.0, "credit": 0.0, "pamm": 0.0, "social": 0.0}
    assert bob["pending"]["payout_destinations"] == 1


def test_admin_links_and_unlinks_an_account(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(100, role="master")
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    _state(client, {1001: {"balance": 100.0, "equity": 100.0, "open_pnl": 0.0, "positions": []}})
    r = client.put(f"/api/orgs/{org_id}/investors/{investor['id']}/account",
                   json={"account_id": 1001}, headers=csrf(client))
    assert r.status_code == 200 and r.json() == {"user_id": investor["id"], "account_id": 1001}
    assert client.get(f"/api/orgs/{org_id}/investors").json()[0]["account_id"] == 1001
    r = client.put(f"/api/orgs/{org_id}/investors/{investor['id']}/account",
                   json={"account_id": 100}, headers=csrf(client))
    assert r.status_code == 400 and "master" in r.json()["detail"]
    assert client.get(f"/api/orgs/{org_id}/investors").json()[0]["account_id"] == 1001
    r = client.put(f"/api/orgs/{org_id}/investors/{investor['id']}/account",
                   json={"account_id": None}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["account_id"] is None
    assert client.get(f"/api/orgs/{org_id}/investors").json()[0]["account_id"] is None
    viewer = make_user(email="v@example.com")
    _member(db, org_id, viewer, "viewer")
    r = client.put(f"/api/orgs/{org_id}/investors/{viewer['id']}/account",
                   json={"account_id": 1001}, headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Investor not found"
    actions = [p["action"] for _, p in _events(db, org_id)]
    assert actions == ["investor_account_linked", "investor_account_linked"]


def test_admin_reads_an_investors_ledger(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    viewer = make_user(email="v@example.com")
    _member(db, org_id, investor, "investor")
    _member(db, org_id, viewer, "viewer")
    entry_id = credit(db, org_id, investor["id"], Decimal("10"))
    page = client.get(f"/api/orgs/{org_id}/investors/{investor['id']}/wallet-entries").json()
    assert [e["id"] for e in page["entries"]] == [entry_id] and page["has_more"] is False
    assert client.get(f"/api/orgs/{org_id}/investors/{investor['id']}/wallet-entries"
                      "?wallet=pamm").json()["entries"] == []
    r = client.get(f"/api/orgs/{org_id}/investors/{viewer['id']}/wallet-entries")
    assert r.status_code == 404 and r.json()["detail"] == "Investor not found"
    login_as(client, investor)
    r = client.get(f"/api/orgs/{org_id}/investors/{investor['id']}/wallet-entries")
    assert r.status_code == 403


# ------------------------------------------------------ admin: adjustments


def test_admin_posts_an_adjustment_with_their_own_mpin(org_client, make_user, login_as, db,
                                                        monkeypatch):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    credit(db, org_id, investor["id"], Decimal("1000"))
    fake = _FakeAlerter()
    monkeypatch.setattr(ws_module.broadcaster, "alerter", fake, raising=False)
    url = f"/api/orgs/{org_id}/investors/{investor['id']}/adjustments"

    def post(**body):
        return client.post(url, json={"wallet": "main", "amount": "-250.00",
                                      "note": "correction", "mpin": "123456", **body},
                           headers=csrf(client))

    r = post(mpin="000000")
    assert r.status_code == 401 and r.json()["attempts_left"] == 4
    r = post(amount="0")
    assert r.status_code == 400 and r.json()["detail"] == "amount must not be zero"
    r = post(amount="abc")
    assert r.status_code == 400 and "amount" in r.json()["detail"]
    r = post(wallet="gold")
    assert r.status_code == 400
    assert r.json()["detail"] == "wallet must be one of main, credit, pamm, social"
    r = post(note="  ")
    assert r.status_code == 400 and r.json()["detail"] == "note is required"
    r = post()
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["wallet"] == "main" and body["amount"] == -250.0
    assert body["kind"] == "adjustment" and body["note"] == "correction"
    assert body["ref_table"] is None and body["currency"] == "USD"
    r = post(wallet="credit", amount="+100", note="welcome bonus")
    assert r.status_code == 201 and r.json()["amount"] == 100.0 and r.json()["wallet"] == "credit"
    rows = client.get(f"/api/orgs/{org_id}/investors").json()
    assert rows[0]["balances"] == {"main": 750.0, "credit": 100.0, "pamm": 0.0, "social": 0.0}
    severity, payload = _events(db, org_id)[-2]
    assert severity == "warning" and payload["action"] == "investor_ledger_adjusted"
    assert payload["user_id"] == investor["id"] and payload["amount"] == -250.0
    assert payload["wallet"] == "main" and payload["note"] == "correction"
    assert fake.sent == [("inv@example.com", "Your My wallet was adjusted by -250.00 USD"),
                         ("inv@example.com", "Your Credit wallet was adjusted by +100.00 USD")]
    viewer = make_user(email="v@example.com")
    _member(db, org_id, viewer, "viewer")
    r = client.post(f"/api/orgs/{org_id}/investors/{viewer['id']}/adjustments",
                    json={"wallet": "main", "amount": "1", "note": "n", "mpin": "123456"},
                    headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Investor not found"


# -------------------------------------------------- admin: requests summary


def test_requests_summary_counts_open_rows(org_client, make_user, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    investor = make_user(email="inv@example.com")
    _member(db, org_id, investor, "investor")
    uid = investor["id"]
    link(db, org_id, uid, 1001)
    assert client.get(f"/api/orgs/{org_id}/requests/summary").json() == {
        "deposits": 0, "withdrawals": 0, "transfers": 0, "payout_destinations": 0, "total": 0}
    dest_id = approved_destination(db, org_id, uid)
    with psycopg.connect(db, autocommit=True) as conn:
        for reference, status in (("r1", "pending"), ("r2", "confirmed"), ("r3", "rejected")):
            conn.execute(
                "INSERT INTO deposits (org_id, user_id, method_kind, method_label, amount, "
                "reference, status) VALUES (%s, %s, 'crypto', 'USDT on TRC20', 10, %s, %s)",
                (org_id, uid, reference, status))
        for status in ("requested", "approved", "paid", "cancelled"):
            conn.execute(
                "INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
                "destination_summary, amount, fee, net_amount, status) "
                "VALUES (%s, %s, %s, 'crypto', 'TRC20 T…st', 5, 0, 5, %s)",
                (org_id, uid, dest_id, status))
        conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details) "
            "VALUES (%s, %s, 'crypto', 'P', '{\"coin\": \"USDT\", \"network\": \"TRC20\", "
            "\"address\": \"T1\"}')", (org_id, uid))
    for status in ("requested", "approved", "done", "rejected"):
        _insert_transfer(db, org_id, uid, source="main", target="account", amount="1",
                         status=status, account_id=1001)
    assert client.get(f"/api/orgs/{org_id}/requests/summary").json() == {
        "deposits": 1, "withdrawals": 2, "transfers": 2, "payout_destinations": 1, "total": 6}


# ------------------------------------------------------------ read-throughs


def _recording_copier(client, accounts=None):
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
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    for tail in ("investor/positions", "investor/analytics",
                 "investor/history/deals?from=0&to=1"):
        r = client.get(f"/api/orgs/{org_id}/{tail}")
        assert r.status_code == 409 and "no account linked" in r.json()["detail"], tail


def test_positions_come_from_the_linked_accounts_state(org_client, make_user, login_as, db):
    client, org_id, seed = org_client
    seed(1001, role="slave")
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    link(db, org_id, investor["id"], 1001)
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
    client, org_id, seed = org_client
    seed(1001, role="slave")
    client, org_id, investor = _investor(org_client, make_user, login_as, db)
    link(db, org_id, investor["id"], 1001)
    seen = _recording_copier(client)
    r = client.get(f"/api/orgs/{org_id}/investor/analytics?weeks=99")
    assert r.status_code == 200 and r.json()["net_pnl"] == 120.5
    assert any(u.endswith("/analytics?account_id=1001&weeks=12") for u in seen)
    r = client.get(f"/api/orgs/{org_id}/investor/history/deals?from=5&to=9")
    assert r.status_code == 200 and r.json() == {"deals": [], "has_more": False}
    assert any(u.endswith("/history/deals?account_id=1001&from=5&to=9") for u in seen)
    assert client.get(f"/api/orgs/{org_id}/investor/history/trades?from=0&to=1").status_code == 400


# ------------------------------------------------------- rules and retirement


def test_the_six_warning_actions_reach_both_alerters():
    for action in WARNING_ACTIONS:
        assert ("control", "warning", action) in ALERT_RULES, action
        assert ("control", "warning", action) in TELEGRAM_RULES, action


def test_the_old_router_and_ledger_are_gone(org_client):
    assert importlib.util.find_spec("api.routes.investor") is None
    assert importlib.util.find_spec("api.investor_ledger") is None
    client, org_id, seed = org_client
    for tail in ("investor-wallet", "investor-deposits", "investor-withdrawals"):
        assert client.get(f"/api/orgs/{org_id}/{tail}").status_code == 404, tail
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_portal_summary.py -q -p no:cacheprovider`
Expected: FAIL. The summary, wallet-entries, investors, adjustments, requests/summary and read-through tests fail on status assertions (404s from missing routes, or old-router answers such as a missing `wallets` key); `test_the_six_warning_actions_reach_both_alerters` fails on `investor_transfer_requested`; `test_the_old_router_and_ledger_are_gone` fails on `find_spec(...) is None`.

- [ ] **Step 3: Retire the old router and ledger**

In `api/src/api/main.py` delete this block (Task 6 kept the new `create_portal_*_router()` includes elsewhere in the function; leave those):

```python
    # Investor portal: a sub-viewer role that sees only its own linked
    # account, plus the admin queues that decide its deposits/withdrawals.
    from .routes.investor import create_investor_admin_router, create_investor_router
    app.include_router(create_investor_router())
    app.include_router(create_investor_admin_router())
```

If Task 6 already removed it, there is nothing to delete. Then remove the files:

```bash
git rm api/src/api/routes/investor.py api/src/api/investor_ledger.py api/tests/test_investor_ledger.py
grep -rn "routes.investor\|investor_ledger\|create_investor_router\|create_investor_admin_router" api/src api/tests
```

Expected: `git rm` removes three files; the `grep` prints nothing.

- [ ] **Step 4: Summary, ledger page and read-throughs in `portal_investor.py`**

Extend the imports of `api/src/api/routes/portal_investor.py` with:

```python
from datetime import date
from ..portal_common import (
    DESTINATION_COLS, ENTRY_COLS, TRANSFER_COLS, WITHDRAWAL_COLS, audit_control,
    destination_json, destination_summary, entry_json, equity_for, fee_for, linked_account,
    net_funded, open_account_transfers_out, portal_settings, settle, transfer_json,
    wallet_figures, withdrawal_json)
from ..portal_ledger import (
    WALLETS, LedgerError, can_transition, clean_text, floor_cents, money, parse_amount,
    transfer_pair)
from .mt5 import MT5_OFFLINE_AFTER_S
from .settings_control import COPIER_SLOW_COMMAND_TIMEOUT_S, _proxy_to_copier
```

(Merge with the existing `portal_common` / `portal_ledger` import lines: one import statement per module.)

Below `money_ref_label` add the module-level helpers:

```python
ENTRY_KINDS = ("deposit", "withdrawal", "transfer", "adjustment", "bonus", "commission", "fee")
ENTRIES_DEFAULT_LIMIT = 50
ENTRIES_MAX_LIMIT = 200


def _account_card(conn: psycopg.Connection, org_id: int, account_id: int) -> Dict[str, Any]:
    """The linked account as the portal shows it. Moved from the old
    investor router unchanged."""
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


def pending_counts(conn: psycopg.Connection, org_id: int, user_id: int) -> Dict[str, int]:
    """Open requests of one investor, per type: what the dashboard's
    Pending row and the admin list's chips show."""
    row = conn.execute(
        """SELECT
             (SELECT count(*) FROM deposits
               WHERE org_id = %(o)s AND user_id = %(u)s AND status = 'pending'),
             (SELECT count(*) FROM withdrawals
               WHERE org_id = %(o)s AND user_id = %(u)s AND status IN ('requested', 'approved')),
             (SELECT count(*) FROM transfers
               WHERE org_id = %(o)s AND user_id = %(u)s AND status IN ('requested', 'approved')),
             (SELECT count(*) FROM payout_destinations
               WHERE org_id = %(o)s AND user_id = %(u)s AND status = 'pending')""",
        {"o": org_id, "u": user_id}).fetchone()
    return {"deposits": int(row[0]), "withdrawals": int(row[1]), "transfers": int(row[2]),
            "payout_destinations": int(row[3])}


def _parse_day(raw: Optional[str], field: str) -> Optional[date]:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return date.fromisoformat(str(raw).strip())
    except ValueError:
        raise LedgerError(f"{field} must be a date (YYYY-MM-DD)")


def entries_page(conn: psycopg.Connection, org_id: int, user_id: int, *,
                 wallet: Optional[str] = None, kind: Optional[str] = None,
                 date_from: Optional[str] = None, date_to: Optional[str] = None,
                 limit: Optional[int] = None, before: Optional[int] = None) -> Dict[str, Any]:
    """One page of an investor's ledger, newest first, keyed by id (ids are
    monotonic: every entry is written with created_at = now()). Raises
    LedgerError for a bad filter; both routers turn that into a 400."""
    if wallet is not None and wallet not in WALLETS:
        raise LedgerError("wallet must be one of main, credit, pamm, social")
    if kind is not None and kind not in ENTRY_KINDS:
        raise LedgerError("kind must be one of " + ", ".join(ENTRY_KINDS))
    start = _parse_day(date_from, "from")
    end = _parse_day(date_to, "to")
    size = ENTRIES_DEFAULT_LIMIT if limit is None else max(1, min(int(limit), ENTRIES_MAX_LIMIT))
    where = ["org_id = %s", "user_id = %s"]
    params: List[Any] = [org_id, user_id]
    if wallet is not None:
        where.append("wallet = %s")
        params.append(wallet)
    if kind is not None:
        where.append("kind = %s")
        params.append(kind)
    if start is not None:
        where.append("created_at >= %s::date")
        params.append(start)
    if end is not None:
        where.append("created_at < %s::date + interval '1 day'")
        params.append(end)
    if before is not None:
        where.append("id < %s")
        params.append(before)
    rows = conn.execute(
        f"SELECT {ENTRY_COLS} FROM wallet_entries WHERE {' AND '.join(where)} "
        "ORDER BY id DESC LIMIT %s", (*params, size + 1)).fetchall()
    has_more = len(rows) > size
    entries = [entry_json(r) for r in rows[:size]]
    return {"entries": entries, "has_more": has_more,
            "next_before": entries[-1]["id"] if has_more and entries else None}
```

Inside `create_portal_investor_router()`, before `return router`, add:

```python
    # ---------------------------------------------------------------- summary

    def _require_linked(conn: psycopg.Connection, ctx: OrgContext) -> int:
        account_id = linked_account(conn, ctx.org_id, ctx.user_id)
        if account_id is None:
            raise HTTPException(status_code=409, detail="no account linked yet")
        return account_id

    @router.get("/investor/summary", response_model=Dict[str, Any])
    async def investor_summary(http_request: Request,
                               ctx: OrgContext = Depends(require_org_role("investor")),
                               conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        org_name, display_name, member_since = conn.execute(
            "SELECT o.name, u.display_name, m.created_at FROM org_memberships m "
            "JOIN orgs o ON o.id = m.org_id JOIN users u ON u.id = m.user_id "
            "WHERE m.org_id = %s AND m.user_id = %s", (ctx.org_id, ctx.user_id)).fetchone()
        figures = wallet_figures(conn, ctx.org_id, ctx.user_id)
        deposited, withdrawn = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN kind = 'deposit' THEN amount END), 0), "
            "COALESCE(SUM(CASE WHEN kind = 'withdrawal' THEN -amount END), 0) "
            "FROM wallet_entries WHERE org_id = %s AND user_id = %s",
            (ctx.org_id, ctx.user_id)).fetchone()
        # Transfers between the wallets and the trading account, settled.
        transferred_out, transferred_in = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN target_kind = 'account' THEN amount END), 0), "
            "COALESCE(SUM(CASE WHEN source_kind = 'account' THEN amount END), 0) "
            "FROM transfers WHERE org_id = %s AND user_id = %s AND status = 'done'",
            (ctx.org_id, ctx.user_id)).fetchone()
        flow = conn.execute(
            "SELECT (created_at AT TIME ZONE 'UTC')::date AS day, "
            "COALESCE(SUM(CASE WHEN kind = 'deposit' THEN amount END), 0), "
            "COALESCE(SUM(CASE WHEN kind = 'withdrawal' THEN -amount END), 0) "
            "FROM wallet_entries WHERE org_id = %s AND user_id = %s "
            "AND kind IN ('deposit', 'withdrawal') AND created_at >= now() - interval '90 days' "
            "GROUP BY day ORDER BY day", (ctx.org_id, ctx.user_id)).fetchall()
        settings = portal_settings(conn, ctx.org_id)
        deposits_open = conn.execute(
            "SELECT 1 FROM payment_methods WHERE org_id = %s AND enabled LIMIT 1",
            (ctx.org_id,)).fetchone() is not None
        account_id = linked_account(conn, ctx.org_id, ctx.user_id)
        card = None
        equity: Optional[Decimal] = None
        source = "unknown"
        positions: list = []
        funded = Decimal("0")
        profit: Optional[Decimal] = None
        account_available: Optional[Decimal] = None
        if account_id is not None:
            card = _account_card(conn, ctx.org_id, account_id)
            equity, source, positions = await equity_for(http_request, conn, ctx.org_id, account_id)
            funded = net_funded(conn, ctx.org_id, ctx.user_id, account_id)
            if equity is not None:
                profit = equity - funded
                account_available = floor_cents(
                    equity - open_account_transfers_out(conn, ctx.org_id, ctx.user_id, account_id))
        words = (display_name or "").split()
        return {
            "org": {"id": ctx.org_id, "name": org_name},
            "currency": "USD",
            "investor": {"display_name": display_name,
                         "first_name": words[0] if words else display_name,
                         "member_since": member_since.isoformat()},
            "wallets": {w: {"balance": money(figures[w]["balance"]),
                            "on_hold": money(figures[w]["on_hold"]),
                            "available": money(figures[w]["available"])} for w in WALLETS},
            "totals": {"deposited": money(Decimal(deposited)),
                       "withdrawn": money(Decimal(withdrawn)),
                       "transferred_in": money(Decimal(transferred_in)),
                       "transferred_out": money(Decimal(transferred_out))},
            "cash_flow": [{"date": day.isoformat(), "deposits": money(Decimal(dep)),
                           "withdrawals": money(Decimal(wd))} for day, dep, wd in flow],
            "pending": pending_counts(conn, ctx.org_id, ctx.user_id),
            "deposits_open": deposits_open,
            "withdrawal_rules": {"min": money(settings["withdrawal_min"]),
                                 "fee_pct": float(settings["withdrawal_fee_pct"])},
            "link_state": "linked" if account_id is not None else "unlinked",
            "account": card,
            "equity_source": source,
            "equity": money(equity),
            "net_funded": money(funded),
            "profit": money(profit),
            "account_available": money(account_available),
            "open_positions": len([p for p in positions if isinstance(p, dict)]),
        }

    @router.get("/investor/wallet-entries", response_model=Dict[str, Any])
    async def my_wallet_entries(wallet: Optional[str] = None, kind: Optional[str] = None,
                                date_from: Optional[str] = Query(None, alias="from"),
                                date_to: Optional[str] = Query(None, alias="to"),
                                limit: int = ENTRIES_DEFAULT_LIMIT,
                                before: Optional[int] = None,
                                ctx: OrgContext = Depends(require_org_role("investor")),
                                conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        try:
            return entries_page(conn, ctx.org_id, ctx.user_id, wallet=wallet, kind=kind,
                                date_from=date_from, date_to=date_to, limit=limit, before=before)
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    # ---------------------------------------------------------- read-throughs
    # Moved from the old investor router unchanged in behaviour.

    @router.get("/investor/positions", response_model=Dict[str, Any])
    async def my_positions(http_request: Request,
                           ctx: OrgContext = Depends(require_org_role("investor")),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        account_id = _require_linked(conn, ctx)
        _equity, source, positions = await equity_for(http_request, conn, ctx.org_id, account_id)
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

- [ ] **Step 5: Investors list, link, ledger view, adjustments and requests summary in `portal_admin.py`**

Extend the imports of `api/src/api/routes/portal_admin.py` with:

```python
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from ..config import ApiConfig
from ..mpin_core import require_mpin
from ..portal_common import (
    DESTINATION_COLS, ENTRY_COLS, TRANSFER_COLS, WITHDRAWAL_COLS, Decision, audit_control,
    destination_json, destination_summary, entry_json, notify_investor, require_note_on_reject,
    settle, transfer_json, wallet_figures, withdrawal_json)
from ..portal_ledger import WALLETS, LedgerError, can_transition, clean_text, money, parse_amount
from .portal_investor import WALLET_LABELS, entries_page, money_ref_label, pending_counts
from .settings_control import _proxy_to_copier
```

(Again one import statement per module, merged with what is there.) Next to the other body classes add (single copy each):

```python
class AdjustmentBody(BaseModel):
    wallet: str
    amount: Any
    note: str
    mpin: Any = None


class LinkBody(BaseModel):
    account_id: Optional[int] = None
```

Above `def create_portal_admin_router()` add the module-level helpers:

```python
async def _org_state(client, cfg: ApiConfig, org_id: int) -> Optional[dict]:
    """One /state round trip for the whole org. None when the copier is
    down or answers with something that isn't a JSON object -- callers then
    fall back to last-known equity for every account, not just one."""
    try:
        state = await _proxy_to_copier(
            client, f"{cfg.copier_control_url}/state?org_id={org_id}", method="GET")
    except HTTPException:
        return None
    return state if isinstance(state, dict) else None


def _equity_from(state: Optional[dict], conn: psycopg.Connection,
                 account_id: int) -> tuple[Optional[Decimal], str, list]:
    """Live equity from an already-fetched /state snapshot, else the last
    equity the MT5 terminal reported, else unknown."""
    accounts = state.get("accounts") if isinstance(state, dict) else None
    entry = accounts.get(str(account_id)) if isinstance(accounts, dict) else None
    if isinstance(entry, dict) and entry.get("equity") is not None:
        return Decimal(str(entry["equity"])), "live", list(entry.get("positions") or [])
    row = conn.execute("SELECT equity FROM mt5_links WHERE account_id = %s",
                       (account_id,)).fetchone()
    if row and row[0] is not None:
        return Decimal(str(row[0])), "last known", []
    return None, "unknown", []


def _require_investor(conn: psycopg.Connection, org_id: int, user_id: int) -> str:
    """The investor member's email, or 404 'Investor not found'."""
    row = conn.execute(
        "SELECT u.email FROM org_memberships m JOIN users u ON u.id = m.user_id "
        "WHERE m.org_id = %s AND m.user_id = %s AND m.role = 'investor'",
        (org_id, user_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Investor not found")
    return row[0]
```

Inside `create_portal_admin_router()`, before `return router`, add:

```python
    # ---------------------------------------------------------------- investors

    @router.get("/investors", response_model=List[Dict[str, Any]])
    async def list_investors(http_request: Request,
                             ctx: OrgContext = Depends(require_org_role("admin")),
                             conn: psycopg.Connection = Depends(get_conn),
                             cfg: ApiConfig = Depends(ApiConfig.from_env)) -> List[Dict[str, Any]]:
        rows = conn.execute(
            """SELECT u.id, u.email, u.display_name, m.created_at, a.ctid_trader_account_id,
                      a.nickname
               FROM org_memberships m
               JOIN users u ON u.id = m.user_id
               LEFT JOIN accounts a ON a.org_id = m.org_id AND a.investor_user_id = u.id
               WHERE m.org_id = %s AND m.role = 'investor'
               ORDER BY u.display_name, u.id""", (ctx.org_id,)).fetchall()
        # One /state round trip for the whole list, never one per investor.
        state = await _org_state(http_request.app.state.http, cfg, ctx.org_id)
        out = []
        for user_id, email, name, joined_at, account_id, nickname in rows:
            figures = wallet_figures(conn, ctx.org_id, user_id)
            equity, source = None, "unknown"
            if account_id is not None:
                equity, source, _positions = _equity_from(state, conn, int(account_id))
            out.append({
                "user_id": user_id, "email": email, "display_name": name,
                "joined_at": joined_at.isoformat(),
                "account_id": int(account_id) if account_id is not None else None,
                "nickname": nickname, "equity": money(equity), "equity_source": source,
                "balances": {w: money(figures[w]["balance"]) for w in WALLETS},
                "on_hold": money(figures["main"]["on_hold"]),
                "available": money(figures["main"]["available"]),
                "pending": pending_counts(conn, ctx.org_id, user_id),
            })
        return out

    @router.put("/investors/{user_id}/account", response_model=Dict[str, Any])
    async def link_account(user_id: int, body: LinkBody,
                           ctx: OrgContext = Depends(require_org_role("admin")),
                           conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        _require_investor(conn, ctx.org_id, user_id)
        with conn.transaction():
            conn.execute("UPDATE accounts SET investor_user_id = NULL "
                         "WHERE org_id = %s AND investor_user_id = %s", (ctx.org_id, user_id))
            if body.account_id is not None:
                # The master is the desk's own account. Linked to an investor
                # it would show them the desk's equity as their balance.
                # Checked inside the transaction, so the unlink rolls back.
                role_row = conn.execute(
                    "SELECT role FROM accounts WHERE ctid_trader_account_id = %s AND org_id = %s",
                    (body.account_id, ctx.org_id)).fetchone()
                if role_row and role_row[0] == "master":
                    raise HTTPException(
                        status_code=400,
                        detail="The master account cannot be linked to an investor")
                updated = conn.execute(
                    "UPDATE accounts SET investor_user_id = %s "
                    "WHERE ctid_trader_account_id = %s AND org_id = %s "
                    "AND investor_user_id IS NULL RETURNING ctid_trader_account_id",
                    (user_id, body.account_id, ctx.org_id)).fetchone()
                if not updated:
                    raise HTTPException(
                        status_code=404,
                        detail="Account not found in this workspace, or already linked")
        await audit_control(
            conn, org_id=ctx.org_id, action="investor_account_linked",
            actor_email=ctx.user_email, user_id=user_id, account_id=body.account_id)
        return {"user_id": user_id, "account_id": body.account_id}

    @router.get("/investors/{user_id}/wallet-entries", response_model=Dict[str, Any])
    async def investor_wallet_entries(user_id: int, wallet: Optional[str] = None,
                                      kind: Optional[str] = None,
                                      date_from: Optional[str] = Query(None, alias="from"),
                                      date_to: Optional[str] = Query(None, alias="to"),
                                      limit: int = 50, before: Optional[int] = None,
                                      ctx: OrgContext = Depends(require_org_role("admin")),
                                      conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        _require_investor(conn, ctx.org_id, user_id)
        try:
            return entries_page(conn, ctx.org_id, user_id, wallet=wallet, kind=kind,
                                date_from=date_from, date_to=date_to, limit=limit, before=before)
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/investors/{user_id}/adjustments", status_code=201,
                 response_model=Dict[str, Any])
    async def post_adjustment(user_id: int, body: AdjustmentBody, http_request: Request,
                              ctx: OrgContext = Depends(require_org_role("admin")),
                              conn: psycopg.Connection = Depends(get_conn)):
        # The ADMIN's own MPIN confirms a hand-posted ledger row.
        failure = require_mpin(conn, ctx.user_id, body.mpin)
        if failure is not None:
            return failure
        email = _require_investor(conn, ctx.org_id, user_id)
        wallet = (body.wallet or "").strip().lower()
        if wallet not in WALLETS:
            raise HTTPException(status_code=400,
                                detail="wallet must be one of main, credit, pamm, social")
        raw = "" if body.amount is None or isinstance(body.amount, bool) else str(body.amount).strip()
        try:
            if raw and Decimal(raw) == 0:
                raise HTTPException(status_code=400, detail="amount must not be zero")
        except InvalidOperation:
            pass  # parse_amount below names the problem
        negative = raw.startswith("-")
        try:
            magnitude = parse_amount(raw.lstrip("+-") if raw else body.amount)
            note = clean_text(body.note, "note", max_len=500)
        except LedgerError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        amount = -magnitude if negative else magnitude
        # An adjustment has no request row to reference, so it is a plain
        # insert rather than settle(): nothing to make idempotent against.
        row = conn.execute(
            "INSERT INTO wallet_entries (org_id, user_id, wallet, amount, kind, note, created_by) "
            "VALUES (%s, %s, %s, %s, 'adjustment', %s, %s) "
            f"RETURNING {ENTRY_COLS}",
            (ctx.org_id, user_id, wallet, amount, note, ctx.user_id)).fetchone()
        out = entry_json(row)
        label = WALLET_LABELS[wallet]
        await audit_control(
            conn, org_id=ctx.org_id, action="investor_ledger_adjusted",
            actor_email=ctx.user_email, user_id=user_id, severity="warning",
            entry_id=out["id"], wallet=wallet, amount=out["amount"], note=note,
            summary=f"Ledger adjusted: {amount:+.2f} USD on {label} of {email} by {ctx.user_email}")
        await notify_investor(
            conn, http_request, user_id,
            f"Your {label} was adjusted by {amount:+.2f} USD",
            f"Wallet: {label}\nAmount: {amount:+.2f} USD\nNote: {note}\n\n"
            "Open the portal for details.")
        return out

    # ---------------------------------------------------------------- requests

    @router.get("/requests/summary", response_model=Dict[str, Any])
    async def requests_summary(ctx: OrgContext = Depends(require_org_role("admin")),
                               conn: psycopg.Connection = Depends(get_conn)) -> Dict[str, Any]:
        row = conn.execute(
            """SELECT
                 (SELECT count(*) FROM deposits WHERE org_id = %(o)s AND status = 'pending'),
                 (SELECT count(*) FROM withdrawals
                   WHERE org_id = %(o)s AND status IN ('requested', 'approved')),
                 (SELECT count(*) FROM transfers
                   WHERE org_id = %(o)s AND status IN ('requested', 'approved')),
                 (SELECT count(*) FROM payout_destinations
                   WHERE org_id = %(o)s AND status = 'pending')""",
            {"o": ctx.org_id}).fetchone()
        counts = {"deposits": int(row[0]), "withdrawals": int(row[1]),
                  "transfers": int(row[2]), "payout_destinations": int(row[3])}
        return {**counts, "total": sum(counts.values())}
```

- [ ] **Step 6: Alert and Telegram rules**

In `api/src/api/alerts.py` replace:

```python
    ("control", "warning", "investor_deposit_noticed"): "Investor deposit notice",
    ("control", "warning", "investor_withdrawal_requested"): "Investor withdrawal request",
```

with:

```python
    ("control", "warning", "investor_deposit_noticed"): "Investor deposit notice",
    ("control", "warning", "investor_withdrawal_requested"): "Investor withdrawal request",
    ("control", "warning", "investor_transfer_requested"): "Investor transfer request",
    ("control", "warning", "investor_destination_added"): "Investor payout account added",
    # An admin hand-posted a ledger row, or changed where investors are told
    # to send money. Both are what a stolen admin session would do.
    ("control", "warning", "investor_ledger_adjusted"): "Investor ledger adjusted",
    ("control", "warning", "payment_method_changed"): "Payment method changed",
```

(The `investor_wallet_set` line below stays: `test_alerts.py` and `test_telegram.py` still exercise it as a rule.)

In `api/src/api/telegram.py` replace:

```python
    ("control", "warning", "investor_deposit_noticed"),
    ("control", "warning", "investor_withdrawal_requested"),
```

with:

```python
    ("control", "warning", "investor_deposit_noticed"),
    ("control", "warning", "investor_withdrawal_requested"),
    ("control", "warning", "investor_transfer_requested"),
    ("control", "warning", "investor_destination_added"),
    ("control", "warning", "investor_ledger_adjusted"),
    ("control", "warning", "payment_method_changed"),
```

- [ ] **Step 7: Point the RBAC matrix at the portal routes**

Replace `api/tests/test_rbac_matrix.py` in full with:

```python
"""Spec §4's permission matrix, executed literally: every org-scoped endpoint
is called as every role, plus non-member and anonymous.

ok() = any status that proves AUTHORIZATION passed (2xx, or a 4xx/5xx that
can only come from AFTER the role check — 400 validation, 404 for a
nonexistent account, 409 for a row whose state forbids the change, 502
copier). 401/403/404-membership are the denials under test. Endpoints listed
with a seeded account id 100 where needed.

The mutating portal rows aim at seeded rows with deterministic ids (the db
fixture TRUNCATEs with RESTART IDENTITY, and every parametrised case gets its
own fixture): payment method 1, the investor's approved payout destination 1
and pending destination 2, deposit 1, withdrawal 1, transfer 1. They are
written so that the FIRST allowed role really performs the change and the
later ones get a 409 or 400 from the row's state or the body — never a
403/404 — so what the row proves is authorization, never business rules.
`{investor}` in a path is the investor member's user id, substituted per test.
Investor routes take `require_org_role("investor")`, the lowest rank, so
every member passes them; the bodies are chosen so a desk member's call
still answers after the role check (a 400 from validation, or a 409
duplicate).
"""
import psycopg
import pytest
from psycopg.types.json import Jsonb
from portal_helpers import add_method, approved_destination

ROLES = ["investor", "viewer", "admin"]

MPIN = "123456"

# (method, path_tail, body, min_role)
MATRIX = [
    ("GET",    "accounts",                       None,                          "viewer"),
    ("GET",    "accounts/100/details",           None,                          "viewer"),
    ("GET",    "accounts/100/history/deals?from=0&to=1", None,                  "viewer"),
    ("GET",    "accounts/100/symbols",           None,                          "viewer"),
    ("GET",    "accounts/100/margin-estimate?symbol=EURUSD&volume_lots=0.01",
                                                 None,                          "viewer"),
    ("GET",    "accounts/100/trendbars?symbol=EURUSD&period=M1&from=0&to=1",
                                                 None,                          "viewer"),
    ("GET",    "accounts/100/positions/1/deals?from=0&to=1", None,              "viewer"),
    ("GET",    "accounts/100/analytics",         None,                          "viewer"),
    ("GET",    "overview",                       None,                          "viewer"),
    ("GET",    "settings",                       None,                          "viewer"),
    ("GET",    "state",                          None,                          "viewer"),
    ("GET",    "events",                         None,                          "viewer"),
    ("GET",    "members",                        None,                          "viewer"),
    ("POST",   "orders",                         {"account_id": 100, "symbol": "EURUSD",
                                                  "side": "BUY", "order_type": "MARKET",
                                                  "volume_lots": 0.01},         "admin"),
    ("POST",   "positions/close",                {"account_id": 100, "position_id": 1}, "admin"),
    ("POST",   "orders/cancel",                  {"account_id": 100, "order_id": 1},    "admin"),
    ("PUT",    "settings",                       {"copying_enabled": False},     "admin"),
    ("POST",   "control/pause",                  {},                             "admin"),
    ("POST",   "control/resume",                 {},                             "admin"),
    ("POST",   "control/resync",                 {},                             "admin"),
    ("POST",   "control/close-all",              {},                             "admin"),
    ("POST",   "drift/dismiss",                  {"id": "abc"},                  "admin"),
    ("PATCH",  "accounts/100",                   {"enabled": True},              "admin"),
    ("DELETE", "accounts/100/connection",        None,                           "admin"),
    ("GET",    "accounts/100/symbol-aliases",    None,                           "admin"),
    ("PUT",    "accounts/100/symbol-aliases",    {"aliases": {}},                "admin"),
    ("POST",   "mt5/accounts",                   {"nickname": "VPS"},            "admin"),
    ("POST",   "mt5/accounts/100/key",           None,                           "admin"),
    ("GET",    "oauth/connect",                  None,                           "admin"),
    ("POST",   "invites",                        {"role": "viewer"},             "admin"),
    ("GET",    "invites",                        None,                           "admin"),
    ("PATCH",  "",                               {"name": "Renamed"},            "admin"),
    ("DELETE", "",                               None,                           "admin"),
    # ---- investor portal, investor side
    ("GET",    "investor/summary",               None,                          "investor"),
    ("GET",    "investor/payment-methods",       None,                          "investor"),
    ("GET",    "investor/deposits",              None,                          "investor"),
    ("GET",    "investor/payout-destinations",   None,                          "investor"),
    ("GET",    "investor/withdrawals",           None,                          "investor"),
    ("GET",    "investor/transfers",             None,                          "investor"),
    ("GET",    "investor/wallet-entries",        None,                          "investor"),
    ("GET",    "investor/positions",             None,                          "investor"),
    ("POST",   "investor/deposits",              {"method_id": 1, "amount": "10",
                                                  "reference": "matrix-filed",
                                                  "target": "wallet"},          "investor"),
    ("POST",   "investor/payout-destinations",   {"kind": "crypto", "nickname": "Matrix",
                                                  "details": {"coin": "USDT", "network": "TRC20",
                                                              "address": "TMatrix"},
                                                  "mpin": MPIN},                "investor"),
    ("POST",   "investor/withdrawals",           {"destination_id": 1, "amount": "0",
                                                  "mpin": MPIN},                "investor"),
    ("POST",   "investor/transfers",             {"source": {"kind": "wallet", "wallet": "main"},
                                                  "target": {"kind": "wallet", "wallet": "pamm"},
                                                  "amount": "10", "mpin": MPIN}, "investor"),
    # ---- investor portal, admin side
    ("GET",    "investors",                      None,                          "admin"),
    ("GET",    "investors/{investor}/wallet-entries", None,                     "admin"),
    ("PUT",    "investors/{investor}/account",   {"account_id": None},           "admin"),
    ("POST",   "investors/{investor}/adjustments", {"wallet": "main", "amount": "1",
                                                    "note": "matrix", "mpin": MPIN}, "admin"),
    ("GET",    "payment-methods",                None,                          "admin"),
    ("POST",   "payment-methods",                {"kind": "crypto", "label": "BTC",
                                                  "details": {"coin": "BTC", "network": "BTC",
                                                              "address": "bc1matrix"}}, "admin"),
    ("PATCH",  "payment-methods/1",              {"label": "Renamed"},           "admin"),
    ("DELETE", "payment-methods/1",              None,                           "admin"),
    ("GET",    "portal-settings",                None,                          "admin"),
    ("PUT",    "portal-settings",                {"withdrawal_min": "0",
                                                  "withdrawal_fee_pct": "0"},   "admin"),
    ("GET",    "requests/summary",               None,                          "admin"),
    ("GET",    "deposits",                       None,                          "admin"),
    ("GET",    "withdrawals",                    None,                          "admin"),
    ("GET",    "transfers",                      None,                          "admin"),
    ("GET",    "payout-destinations",            None,                          "admin"),
    ("POST",   "deposits/1/decision",            {"status": "rejected", "note": "matrix"}, "admin"),
    ("POST",   "withdrawals/1/decision",         {"status": "rejected", "note": "matrix"}, "admin"),
    ("POST",   "withdrawals/1/paid",             {"txid": "matrix"},             "admin"),
    ("POST",   "transfers/1/decision",           {"status": "rejected", "note": "matrix"}, "admin"),
    ("POST",   "payout-destinations/2/decision", {"status": "rejected", "note": "matrix"}, "admin"),
]

RANK = {"investor": -1, "viewer": 0, "admin": 1}


@pytest.fixture
def matrix_org(app_client, make_user, make_org, db, login_as):
    """One org, one user per role, one seeded master account 100, and one
    open row of every portal request type belonging to the investor."""
    users = {role: make_user(email=f"{role}@example.com") for role in ROLES}
    org_id = make_org(name="Matrix", members=[(users[r], r) for r in ROLES])
    outsider = make_user(email="outsider@example.com")
    investor_id = users["investor"]["id"]
    method_id = add_method(db, org_id)                                    # id 1
    approved_id = approved_destination(db, org_id, investor_id)           # id 1
    with psycopg.connect(db, autocommit=True) as conn:
        (connection_id,) = conn.execute(
            """INSERT INTO ctid_connections
               (org_id, access_token_enc, refresh_token_enc, granted_at, expires_at)
               VALUES (%s, 'enc', 'enc', now(), now() + interval '30 days')
               RETURNING id""", (org_id,)).fetchone()
        conn.execute(
            """INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id,
                   org_id, trader_login, is_live, role)
               VALUES (100, %s, %s, 100, false, 'master')""",
            (connection_id, org_id))
        # The transfer targets an MT5-style account (no cTrader connection)
        # rather than account 100: `DELETE accounts/100/connection` cascades
        # account 100 away, and transfers.target_account_id would be set
        # NULL against its CHECK constraint.
        (mt5_id,) = conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, ctid_connection_id, org_id, "
            "platform, trader_login, is_live, role, enabled) "
            "VALUES (nextval('mt5_account_id_seq'), NULL, %s, 'mt5', 0, false, 'slave', true) "
            "RETURNING ctid_trader_account_id", (org_id,)).fetchone()
        conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details) "
            "VALUES (%s, %s, 'crypto', 'Pending', %s)",                        # id 2
            (org_id, investor_id,
             Jsonb({"coin": "USDT", "network": "TRC20", "address": "TPending"})))
        conn.execute(
            "INSERT INTO deposits (org_id, user_id, method_id, method_kind, method_label, "
            "amount, reference) VALUES (%s, %s, %s, 'crypto', 'USDT on TRC20', 100, "
            "'matrix-seeded')", (org_id, investor_id, method_id))                # id 1
        conn.execute(
            "INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
            "destination_summary, amount, fee, net_amount) "
            "VALUES (%s, %s, %s, 'crypto', 'TRC20 T…st', 50, 0, 50)",
            (org_id, investor_id, approved_id))                                 # id 1
        conn.execute(
            "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, target_kind, "
            "target_account_id, amount) VALUES (%s, %s, 'wallet', 'main', 'account', %s, 10)",
            (org_id, investor_id, mt5_id))                                      # id 1
    return app_client, org_id, users, outsider


def _call(client, method, org_id, tail, body):
    url = f"/api/orgs/{org_id}/{tail}" if tail else f"/api/orgs/{org_id}"
    headers = {"X-CSRF-Token": client.cookies.get("csrf") or ""}
    kwargs = {"headers": headers}
    if body is not None:
        kwargs["json"] = body
    if method == "GET":
        return client.get(url, follow_redirects=False)
    return getattr(client, method.lower())(url, **kwargs)


@pytest.mark.parametrize("method,tail,body,min_role", MATRIX,
                         ids=[f"{m} {t or '(org)'}" for m, t, _, _ in MATRIX])
def test_role_thresholds(matrix_org, login_as, method, tail, body, min_role):
    client, org_id, users, outsider = matrix_org
    tail = tail.replace("{investor}", str(users["investor"]["id"]))

    # Anonymous: always 401 (or 403 from CSRF middleware on mutations — both
    # prove denial before any org logic).
    client.cookies.clear()
    r = _call(client, method, org_id, tail, body)
    assert r.status_code in (401, 403), f"anonymous got {r.status_code}"

    # Non-member: 404 — org existence never leaks.
    login_as(client, outsider)
    r = _call(client, method, org_id, tail, body)
    assert r.status_code == 404, f"outsider got {r.status_code}"

    # DELETE rows are destructive — only probe DENIED roles for them, and
    # prove the allowed role separately in test_destructive_rows_allowed to
    # keep the fixture intact per param.
    destructive = (method == "DELETE")
    for role in ROLES:
        allowed = RANK[role] >= RANK[min_role]
        if destructive and allowed:
            continue
        login_as(client, users[role])
        r = _call(client, method, org_id, tail, body)
        if allowed:
            assert r.status_code not in (401, 403, 404), \
                f"{role} should pass {method} {tail}, got {r.status_code}"
        else:
            assert r.status_code == 403, \
                f"{role} should be 403 on {method} {tail}, got {r.status_code}"


def test_destructive_rows_allowed(matrix_org, login_as):
    """The allowed-role half of the destructive rows, run last against a
    dedicated fixture instance. The payment method is still used by the
    pending deposit, so its DELETE answers 409 -- authorization passed."""
    client, org_id, users, _ = matrix_org
    login_as(client, users["admin"])
    r = _call(client, "DELETE", org_id, "payment-methods/1", None)
    assert r.status_code == 409
    r = _call(client, "DELETE", org_id, "accounts/100/connection", None)
    assert r.status_code == 200
    r = _call(client, "DELETE", org_id, "", None)
    assert r.status_code == 204


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

If Task 1 (Part A) already rewrote this file to keep the suite green, this version replaces it: it is the final matrix for the portal.

- [ ] **Step 8: Run the task's tests and the matrix**

Run: `.venv/Scripts/python -m pytest tests/test_portal_summary.py tests/test_rbac_matrix.py -q -p no:cacheprovider`
Expected: PASS. `test_portal_summary.py` 15 passed; `test_rbac_matrix.py` all rows pass (63 parametrised cases plus the two extra tests).

- [ ] **Step 9: Run the full API suite**

Run: `.venv/Scripts/python -m pytest tests -q -p no:cacheprovider`
Expected: everything passes except the pre-existing Windows-only cases: 7 errors in `test_events_ws.py` and 1 EA-download CRLF failure. No other failure, and no `ImportError` anywhere (nothing imports `api.investor_ledger` or `api.routes.investor` any more).

- [ ] **Step 10: Commit**

```bash
git add -A api/src/api api/tests
git commit -m "feat(api): investor summary, ledger pages, admin investors list, adjustments and requests summary; retire the old investor router

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```


### Task 11: Dashboard foundation — portal types, vocabulary, hide-balances store, `Money`, `apiUpload`, `FileInput`, `PinConfirmDialog`, fixtures

The shared pieces every portal page (Tasks 13–19) imports. Nothing here renders a page; every piece has its own test. The five 2026-09-23 types leave `lib/types.ts`; the four pages that still use them (rewritten in Tasks 13, 14, 15 and 18) keep compiling by importing them from a clearly marked legacy module instead — no field access changes, no behaviour changes, so their existing tests keep passing untouched.

**Files:**
- Modify: `dashboard/src/lib/types.ts` (delete `InvestorSummary`, `InvestorWallet`, `InvestorDeposit`, `InvestorWithdrawal`, `InvestorRow`; add the portal types)
- Create: `dashboard/src/lib/legacyInvestorTypes.ts` (the five deleted types, verbatim, for the pages rewritten later)
- Modify: `dashboard/src/pages/investor/InvestorOverview.tsx`, `InvestorDeposit.tsx`, `InvestorWithdraw.tsx`, `dashboard/src/pages/Investors.tsx` (one import line each)
- Modify: `dashboard/src/lib/investor.ts` (wallet and status vocabulary, `BADGE_TONE`, `entryLabel`)
- Modify: `dashboard/src/lib/investor.test.ts`
- Create: `dashboard/src/lib/hideBalances.ts`, `dashboard/src/lib/hideBalances.test.ts`
- Create: `dashboard/src/components/Money.tsx`, `dashboard/src/components/Money.test.tsx`
- Modify: `dashboard/src/lib/api.ts` (`apiUpload`, `orgUpload`, `orgApi` passes `opts` through), `dashboard/src/lib/api.test.ts`
- Create: `dashboard/src/components/FileInput.tsx`, `dashboard/src/components/FileInput.test.tsx`
- Create: `dashboard/src/components/PinConfirmDialog.tsx`, `dashboard/src/components/PinConfirmDialog.test.tsx`
- Create: `dashboard/src/test/portalFixtures.ts`, `dashboard/src/test/portalFixtures.test.ts`

**Interfaces:**
- Consumes: `money`, `signed`, `errorText` (`lib/format.ts`); `ApiError`, `api` (`lib/api.ts`); `ConfirmDialog`, `PinInput`, `Button`, `Badge`/`BadgeTone` primitives.
- Produces: types `WalletKind`, `RequestStatus`, `MoneyRef`, `PaymentMethod`, `PortalDeposit`, `PortalWithdrawal`, `PortalTransfer`, `PayoutDestination`, `WalletEntry`, `WalletEntriesPage`, `WalletFigures`, `InvestorSummary`, `InvestorRow`, `PortalSettings`, `RequestsSummary`, `UploadedFile`; `ACCOUNT_CURRENCY`, `WALLETS`, `walletLabel`, `statusLabel`, `statusTone`, `approvedLabel`, `RequestKind`, `BADGE_TONE`, `moneyOrDash`, `shortAddress`, `entryLabel`; `HIDE_KEY`, `readHidden`, `setHidden`, `useHiddenBalances`; `Money` (default), `MoneyProps`, `HideBalancesToggle`; `apiUpload`, `orgUpload`; `FileInput` (default), `FileInputProps`, `RECEIPT_ACCEPT`, `MAX_UPLOAD_BYTES`, `formatBytes`; `PinConfirmDialog` (default), `PinConfirmDialogProps`, `mpinErrorText`; fixtures `summaryFixture`, `depositFixture`, `withdrawalFixture`, `transferFixture`, `destinationFixture`, `entryFixture`, `methodFixture`, `investorRowFixture`.
- Deviation from the interfaces doc: `statusLabel`/`statusTone` gain an optional `kind?: RequestKind` second argument; bare `approved` keeps 'Approved, payment pending' (the existing test and the Requests desk rely on it); `approvedLabel(kind)` and `RequestKind` are the way to get 'Approved, in progress' (transfer) or 'Approved' (destination). Later tasks call `statusLabel(status, 'transfer')` / `statusTone(status, 'transfer')` for transfer rows and `statusLabel(status, 'destination')` / `statusTone(status, 'destination')` for payout-account rows. Also: `orgApi` gains the same optional `opts?: { redirectOn401?: boolean }` fourth argument as `api()`, so step-up POSTs (withdrawal, transfer, destination, adjustment) can go through `orgApi(..., { redirectOn401: false })` as spec section 10 says, instead of spelling out the `/api/orgs/{id}/` prefix through `api()`.

- [ ] **Step 1: Move the five old types out of `lib/types.ts`**

Create `dashboard/src/lib/legacyInvestorTypes.ts` with exactly this content (it is the text being removed from `types.ts`, unchanged):

```ts
/**
 * The 2026-09-23 investor-portal shapes. Kept ONLY so the pages that
 * Tasks 13-18 rewrite or delete (InvestorOverview, InvestorDeposit,
 * InvestorWithdraw, pages/Investors) keep compiling until their turn.
 * Nothing new may import this file; Task 18 deletes it together with its
 * last importer. The live shapes are in ./types.ts.
 */

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
```

In `dashboard/src/lib/types.ts` delete the block that begins with the comment

```ts
/** What the investor portal shows on its Overview; every figure is money
 *  in the workspace's account currency, `null` when not knowable. */
export interface InvestorSummary {
```

and ends with the closing brace of

```ts
/** One row of the admin's Investors table. */
export interface InvestorRow {
  ...
  pending_withdrawals: number
}
```

(five declarations: `InvestorSummary`, `InvestorWallet`, `InvestorDeposit`, `InvestorWithdrawal`, `InvestorRow` — lines 497–575 of the current file). Keep `InvestorPositions`, which follows it, and everything above.

Then append at the end of `types.ts`:

```ts
// ---------------------------------------------------------------------------
// Client portal, phase 1 (spec docs/superpowers/specs/2026-09-29-client-portal-
// phase-1-money-design.md, section 8). Every money figure is a float rounded
// to cents; every row and summary carries `currency` ("USD" in phase 1).
// ---------------------------------------------------------------------------

export type WalletKind = 'main' | 'credit' | 'pamm' | 'social'

export type RequestStatus =
  | 'pending' | 'confirmed' | 'rejected' | 'cancelled'
  | 'requested' | 'approved' | 'paid' | 'done' | 'removed'

/** One end of a transfer: a wallet or the linked trading account. */
export interface MoneyRef { kind: 'wallet' | 'account'; wallet?: WalletKind; account_id?: number }

export interface PaymentMethod {
  id: number
  kind: 'crypto' | 'bank'
  label: string
  enabled: boolean
  currency: string
  details: Record<string, string>
  min_amount: number
  fee_pct: number
  instructions: string | null
  sort_order: number
}

export interface PortalDeposit {
  id: number
  user_id: number
  method_id: number | null
  method_kind: 'crypto' | 'bank'
  method_label: string
  amount: number
  fee: number
  credited_amount: number | null
  reference: string
  receipt_file_id: number | null
  target: 'wallet' | 'account'
  target_account_id: number | null
  note: string | null
  status: RequestStatus
  decided_by: number | null
  decided_at: string | null
  decision_note: string | null
  created_at: string
  currency: string
  /** Present in the admin queue only. */
  email?: string
  display_name?: string
}

export interface PortalWithdrawal {
  id: number
  user_id: number
  destination_id: number
  destination_kind: 'bank' | 'crypto'
  destination_summary: string
  amount: number
  fee: number
  net_amount: number
  status: RequestStatus
  decided_by: number | null
  decided_at: string | null
  decision_note: string | null
  paid_by: number | null
  paid_at: string | null
  txid: string | null
  created_at: string
  currency: string
  email?: string
  display_name?: string
}

export interface PortalTransfer {
  id: number
  user_id: number
  source: MoneyRef
  target: MoneyRef
  amount: number
  status: RequestStatus
  equity_at_request: number | null
  equity_verified: boolean
  decided_by: number | null
  decided_at: string | null
  decision_note: string | null
  done_by: number | null
  done_at: string | null
  note: string | null
  created_at: string
  currency: string
  email?: string
  display_name?: string
}

export interface PayoutDestination {
  id: number
  user_id: number
  kind: 'bank' | 'crypto'
  nickname: string
  details: Record<string, string>
  proof_file_id: number | null
  status: RequestStatus
  decided_by: number | null
  decided_at: string | null
  decision_note: string | null
  created_at: string
  /** Built server-side: "ICICI Bank ••4543" or "TRC20 T…9f". */
  summary: string
  email?: string
  display_name?: string
}

export interface WalletEntry {
  id: number
  wallet: WalletKind
  /** Signed: credits positive, debits negative. */
  amount: number
  kind: 'deposit' | 'withdrawal' | 'transfer' | 'adjustment' | 'bonus' | 'commission' | 'fee'
  ref_table: string | null
  ref_id: number | null
  note: string | null
  created_at: string
  currency: string
}

export interface WalletEntriesPage { entries: WalletEntry[]; has_more: boolean; next_before: number | null }

export interface WalletFigures { balance: number; on_hold: number; available: number }

/** GET investor/summary. `available` figures are floored to cents server-side. */
export interface InvestorSummary {
  org: { id: number; name: string }
  currency: string
  investor: { display_name: string; first_name: string; member_since: string }
  wallets: Record<WalletKind, WalletFigures>
  totals: { deposited: number; withdrawn: number; transferred_in: number; transferred_out: number }
  cash_flow: { date: string; deposits: number; withdrawals: number }[]
  pending: { deposits: number; withdrawals: number; transfers: number; payout_destinations: number }
  deposits_open: boolean
  withdrawal_rules: { min: number; fee_pct: number }
  link_state: 'linked' | 'unlinked'
  account: {
    account_id: number; nickname: string | null; platform: string
    status: string; last_error: string | null; connected: boolean
  } | null
  equity_source: 'live' | 'last known' | 'unknown'
  equity: number | null
  net_funded: number
  profit: number | null
  account_available: number | null
  open_positions: number
}

/** One row of the admin's Investors table (GET investors). */
export interface InvestorRow {
  user_id: number
  email: string
  display_name: string
  joined_at: string
  account_id: number | null
  nickname: string | null
  equity: number | null
  equity_source: string
  balances: Record<WalletKind, number>
  on_hold: number
  available: number
  pending: { deposits: number; withdrawals: number; transfers: number; payout_destinations: number }
}

export interface PortalSettings { withdrawal_min: number; withdrawal_fee_pct: number }

export interface RequestsSummary { deposits: number; withdrawals: number; transfers: number; payout_destinations: number; total: number }

/** What POST investor/files answers. */
export interface UploadedFile { id: number; purpose: string; content_type: string; size_bytes: number; created_at: string }
```

- [ ] **Step 2: Point the four old pages at the legacy module**

`dashboard/src/pages/investor/InvestorOverview.tsx` — replace

```ts
import type {
  Analytics, InvestorDeposit, InvestorPositions, InvestorSummary, InvestorWithdrawal,
} from '../../lib/types'
```

with

```ts
import type { Analytics, InvestorPositions } from '../../lib/types'
import type { InvestorDeposit, InvestorSummary, InvestorWithdrawal } from '../../lib/legacyInvestorTypes'
```

`dashboard/src/pages/investor/InvestorWithdraw.tsx` — replace

```ts
import type { InvestorSummary, InvestorWallet, InvestorWithdrawal } from '../../lib/types'
```

with

```ts
import type { InvestorSummary, InvestorWallet, InvestorWithdrawal } from '../../lib/legacyInvestorTypes'
```

`dashboard/src/pages/investor/InvestorDeposit.tsx` — replace

```ts
import type { InvestorDeposit as Deposit, InvestorWallet } from '../../lib/types'
```

with

```ts
import type { InvestorDeposit as Deposit, InvestorWallet } from '../../lib/legacyInvestorTypes'
```

`dashboard/src/pages/Investors.tsx` — replace

```ts
import type {
  Account, InvestorDeposit, InvestorRow, InvestorWallet, InvestorWithdrawal,
} from '../lib/types'
```

with

```ts
import type { Account } from '../lib/types'
import type { InvestorDeposit, InvestorRow, InvestorWallet, InvestorWithdrawal } from '../lib/legacyInvestorTypes'
```

- [ ] **Step 3: Prove the type move compiles**

Run from `dashboard/`: `npx tsc --noEmit -p tsconfig.app.json`
Expected: no output, exit code 0. (If it lists `InvestorSummary` or `InvestorRow` errors, an import in Step 2 was missed.)

- [ ] **Step 4: Write the failing vocabulary tests**

Replace `dashboard/src/lib/investor.test.ts` with:

```ts
import { describe, expect, test } from 'vitest'
import {
  ACCOUNT_CURRENCY, BADGE_TONE, WALLETS, approvedLabel, entryLabel, moneyOrDash, shortAddress,
  statusLabel, statusTone, walletLabel,
} from './investor'
import type { WalletEntry } from './types'

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
    expect(moneyOrDash(1234.5)).toBe('1,234.50')
  })

  test('money carries its unit, and the dash never does', () => {
    expect(moneyOrDash(1234.5, 'USD')).toBe('1,234.50 USD')
    expect(moneyOrDash(null, 'USD')).toBe('—')
    expect(ACCOUNT_CURRENCY).toBe('USD')
  })

  test('long addresses shorten to first character and last two; short ones stay whole', () => {
    expect(shortAddress('TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE9f')).toBe('T…9f')
    expect(shortAddress('TDest')).toBe('TDest')
    expect(shortAddress('  TDest  ')).toBe('TDest')
  })
})

describe('portal vocabulary', () => {
  test('the four wallets, in display order, with their names', () => {
    expect(WALLETS).toEqual(['main', 'credit', 'pamm', 'social'])
    expect(walletLabel('main')).toBe('My wallet')
    expect(walletLabel('credit')).toBe('Credit wallet')
    expect(walletLabel('pamm')).toBe('PAMM wallet')
    expect(walletLabel('social')).toBe('Social wallet')
  })

  test('the phase-1 statuses: cancelled and removed are quiet, done is ok', () => {
    expect(statusLabel('cancelled')).toBe('Cancelled')
    expect(statusTone('cancelled')).toBe('quiet')
    expect(statusLabel('done')).toBe('Done')
    expect(statusTone('done')).toBe('ok')
    expect(statusLabel('removed')).toBe('Removed')
    expect(statusTone('removed')).toBe('quiet')
  })

  test('approved reads per request kind: a transfer is in progress, a payout account is simply approved', () => {
    expect(approvedLabel('withdrawal')).toBe('Approved, payment pending')
    expect(approvedLabel('transfer')).toBe('Approved, in progress')
    expect(approvedLabel('destination')).toBe('Approved')
    expect(statusLabel('approved', 'transfer')).toBe('Approved, in progress')
    expect(statusLabel('approved', 'withdrawal')).toBe('Approved, payment pending')
    expect(statusLabel('approved', 'destination')).toBe('Approved')
    expect(statusTone('approved', 'destination')).toBe('ok')
    expect(statusTone('approved', 'transfer')).toBe('warn')
    expect(statusLabel('requested', 'transfer')).toBe('Awaiting approval')
  })

  test('the four tones map onto the Badge tones', () => {
    expect(BADGE_TONE).toEqual({ ok: 'profit', warn: 'warn', bad: 'loss', quiet: 'neutral' })
  })

  test('ledger entries are named by kind and reference', () => {
    const entry = (over: Partial<WalletEntry>): WalletEntry => ({
      id: 1, wallet: 'main', amount: 250, kind: 'deposit', ref_table: 'deposits', ref_id: 12,
      note: null, created_at: '2026-09-29T10:05:06Z', currency: 'USD', ...over,
    })
    expect(entryLabel(entry({}))).toBe('Deposit #12')
    expect(entryLabel(entry({ kind: 'withdrawal', ref_table: 'withdrawals', ref_id: 4, amount: -250 }))).toBe('Withdrawal #4')
    expect(entryLabel(entry({ kind: 'transfer', ref_table: 'transfers', ref_id: 9 }))).toBe('Transfer #9')
    expect(entryLabel(entry({ kind: 'adjustment', ref_table: null, ref_id: null }))).toBe('Adjustment')
    expect(entryLabel(entry({ kind: 'bonus', ref_table: null, ref_id: null }))).toBe('Bonus')
    expect(entryLabel(entry({ kind: 'commission', ref_table: null, ref_id: null }))).toBe('Commission')
    expect(entryLabel(entry({ kind: 'fee', ref_table: null, ref_id: null }))).toBe('Fee')
  })
})
```

Run: `npx vitest run src/lib/investor.test.ts`
Expected: FAIL — the four legacy tests pass; the five 'portal vocabulary' tests fail (`TypeError: walletLabel is not a function`, `expected undefined to deeply equal ['main', ...]`, `statusLabel('cancelled')` returning 'cancelled'). Vitest turns a missing named export into `undefined` rather than a SyntaxError, so the file loads and each test fails on its own line.

- [ ] **Step 5: Extend `lib/investor.ts`**

Replace `dashboard/src/lib/investor.ts` with:

```ts
import type { BadgeTone } from '../components/Badge'
import { money } from './format'
import type { WalletEntry, WalletKind } from './types'

export type StatusTone = 'ok' | 'warn' | 'bad' | 'quiet'

/**
 * The unit of every wallet and account figure in phase 1: the portal is
 * USD-only (spec section 3). Every API row and summary also carries a
 * `currency` field; pages prefer the row's value and fall back to this.
 */
export const ACCOUNT_CURRENCY = 'USD'

/** The four wallets in display order. Only `main` moves in phase 1. */
export const WALLETS: WalletKind[] = ['main', 'credit', 'pamm', 'social']

const WALLET_LABELS: Record<WalletKind, string> = {
  main: 'My wallet',
  credit: 'Credit wallet',
  pamm: 'PAMM wallet',
  social: 'Social wallet',
}

export function walletLabel(kind: WalletKind): string {
  return WALLET_LABELS[kind]
}

/** Which request table a status belongs to; only `approved` reads differently per table. */
export type RequestKind = 'deposit' | 'withdrawal' | 'transfer' | 'destination'

/** An approved withdrawal still has to be paid; an approved transfer is
 *  being funded at the broker; an approved payout account is just usable. */
export function approvedLabel(kind: RequestKind): string {
  if (kind === 'transfer') return 'Approved, in progress'
  if (kind === 'destination') return 'Approved'
  return 'Approved, payment pending'
}

const LABELS: Record<string, string> = {
  pending: 'Pending review',
  confirmed: 'Confirmed',
  requested: 'Awaiting approval',
  approved: 'Approved, payment pending',
  paid: 'Paid',
  rejected: 'Rejected',
  cancelled: 'Cancelled',
  done: 'Done',
  removed: 'Removed',
}

export function statusLabel(status: string, kind?: RequestKind): string {
  if (status === 'approved' && kind) return approvedLabel(kind)
  return LABELS[status] ?? status
}

export function statusTone(status: string, kind?: RequestKind): StatusTone {
  if (status === 'confirmed' || status === 'paid' || status === 'done') return 'ok'
  if (status === 'approved') return kind === 'destination' ? 'ok' : 'warn'
  if (status === 'pending' || status === 'requested') return 'warn'
  if (status === 'rejected') return 'bad'
  return 'quiet'
}

/** The status tones on the desk's one chip. */
export const BADGE_TONE: Record<StatusTone, BadgeTone> = {
  ok: 'profit',
  warn: 'warn',
  bad: 'loss',
  quiet: 'neutral',
}

export function moneyOrDash(n: number | null | undefined, unit?: string): string {
  return n == null ? '—' : money(n, unit)
}

/**
 * "T…9f": the first character and the last two, for a one-line summary
 * such as the Withdraw review title. The full address is always shown next
 * to it; short strings stay whole.
 */
export function shortAddress(address: string): string {
  const a = address.trim()
  return a.length <= 8 ? a : `${a.slice(0, 1)}…${a.slice(-2)}`
}

const ENTRY_KINDS: Record<WalletEntry['kind'], string> = {
  deposit: 'Deposit',
  withdrawal: 'Withdrawal',
  transfer: 'Transfer',
  adjustment: 'Adjustment',
  bonus: 'Bonus',
  commission: 'Commission',
  fee: 'Fee',
}

/** "Deposit #12", "Withdrawal #4", "Transfer #9", "Adjustment" -- what a ledger row was for. */
export function entryLabel(e: WalletEntry): string {
  const word = ENTRY_KINDS[e.kind] ?? e.kind
  return e.ref_id != null ? `${word} #${e.ref_id}` : word
}

/** Tailwind classes for a status pill, matching Automation's OutcomePill.
 *  Used by the Withdraw Timeline until Task 15 rewrites that page; new code
 *  uses <Badge tone={BADGE_TONE[statusTone(s)]}>. */
export function pillClass(status: string): string {
  const tone = statusTone(status)
  return tone === 'ok' ? 'bg-profit-wash text-profit-deep'
    : tone === 'warn' ? 'bg-warn-wash text-warn-deep'
    : tone === 'bad' ? 'bg-loss-wash text-loss-deep'
    : 'bg-paper text-ink-soft'
}
```

Run: `npx vitest run src/lib/investor.test.ts`
Expected: PASS — Tests 9 passed (9).

- [ ] **Step 6: Write the failing hide-balances and `Money` tests**

Create `dashboard/src/lib/hideBalances.test.ts`:

```ts
import { act, renderHook } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import { HIDE_KEY, readHidden, setHidden, useHiddenBalances } from './hideBalances'

afterEach(() => {
  setHidden(false)
  localStorage.clear()
})

test('the key is mf.hideBalances and the default is shown', () => {
  expect(HIDE_KEY).toBe('mf.hideBalances')
  expect(readHidden()).toBe(false)
})

test('setHidden persists to localStorage and readHidden reads it back', () => {
  setHidden(true)
  expect(localStorage.getItem('mf.hideBalances')).toBe('1')
  expect(readHidden()).toBe(true)
  setHidden(false)
  expect(localStorage.getItem('mf.hideBalances')).toBe('0')
  expect(readHidden()).toBe(false)
})

test('every mounted hook follows a change made anywhere', () => {
  const a = renderHook(() => useHiddenBalances())
  const b = renderHook(() => useHiddenBalances())
  expect(a.result.current[0]).toBe(false)
  act(() => { b.result.current[1](true) })
  expect(a.result.current[0]).toBe(true)
  expect(b.result.current[0]).toBe(true)
  act(() => { setHidden(false) })
  expect(a.result.current[0]).toBe(false)
})
```

Create `dashboard/src/components/Money.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test } from 'vitest'
import Money, { HideBalancesToggle } from './Money'
import { setHidden } from '../lib/hideBalances'

afterEach(() => {
  setHidden(false)
  localStorage.clear()
})

test('renders two-decimal money with its unit in the tabular figure style', () => {
  render(<Money value={5120.5} unit="USD" />)
  const el = screen.getByText('5,120.50 USD')
  expect(el.tagName).toBe('SPAN')
  expect(el).toHaveClass('num')
})

test('a form string renders as money; nothing renders as a dash', () => {
  const { rerender } = render(<Money value="250" />)
  expect(screen.getByText('250.00')).toBeInTheDocument()
  rerender(<Money value={null} unit="USD" />)
  expect(screen.getByText('—')).toBeInTheDocument()
  rerender(<Money value="" unit="USD" />)
  expect(screen.getByText('—')).toBeInTheDocument()
})

test('signed money keeps its sign and unit', () => {
  const { rerender } = render(<Money value={-250} unit="USD" signed />)
  expect(screen.getByText('-250.00 USD')).toBeInTheDocument()
  rerender(<Money value={1000} signed />)
  expect(screen.getByText('+1,000.00')).toBeInTheDocument()
  rerender(<Money value="" signed />)
  expect(screen.getByText('—')).toBeInTheDocument()
})

test('hidden balances render as dots with an accessible name and no figure', () => {
  setHidden(true)
  render(<Money value={5120.5} unit="USD" className="text-2xl" />)
  const masked = screen.getByRole('img', { name: 'Hidden amount' })
  expect(masked).toHaveTextContent('••••')
  expect(masked).toHaveClass('text-2xl')
  expect(screen.queryByText(/5,120/)).toBeNull()
})

test('the toggle is a pressed ghost button that flips every Money on the page and persists', async () => {
  render(
    <>
      <HideBalancesToggle />
      <Money value={100} unit="USD" />
      <Money value={200} unit="USD" />
    </>,
  )
  const toggle = screen.getByRole('button', { name: 'Hide balances' })
  expect(toggle).toHaveAttribute('aria-pressed', 'false')
  expect(screen.getByText('100.00 USD')).toBeInTheDocument()

  await userEvent.click(toggle)
  expect(screen.getByRole('button', { name: 'Show balances' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getAllByRole('img', { name: 'Hidden amount' })).toHaveLength(2)
  expect(screen.queryByText('200.00 USD')).toBeNull()
  expect(localStorage.getItem('mf.hideBalances')).toBe('1')

  await userEvent.click(screen.getByRole('button', { name: 'Show balances' }))
  expect(screen.getByText('200.00 USD')).toBeInTheDocument()
  expect(localStorage.getItem('mf.hideBalances')).toBe('0')
})
```

Run: `npx vitest run src/lib/hideBalances.test.ts src/components/Money.test.tsx`
Expected: FAIL — both files fail to load (`Failed to resolve import "./hideBalances"` / `"./Money"`).

- [ ] **Step 7: Implement `lib/hideBalances.ts` and `components/Money.tsx`**

Create `dashboard/src/lib/hideBalances.ts`:

```ts
import { useEffect, useState } from 'react'

/**
 * The "hide balances" switch: one flag every Money on the page follows,
 * persisted per browser so it survives a reload (localStorage
 * `mf.hideBalances`, '1' hidden / '0' shown). A module store like
 * settingsBus: writers call setHidden, readers mount useHiddenBalances.
 * When storage is unavailable (private mode) the choice still holds for
 * the life of the page through `memory`.
 */
export const HIDE_KEY = 'mf.hideBalances'

type Listener = (hidden: boolean) => void

const listeners = new Set<Listener>()
let memory = false

export function readHidden(): boolean {
  try {
    const stored = localStorage.getItem(HIDE_KEY)
    if (stored !== null) return stored === '1'
  } catch {
    // Storage blocked: fall through to the in-memory value.
  }
  return memory
}

export function setHidden(v: boolean): void {
  memory = v
  try {
    localStorage.setItem(HIDE_KEY, v ? '1' : '0')
  } catch {
    // Private mode or a full store: the page still honours the choice.
  }
  listeners.forEach((l) => l(v))
}

export function useHiddenBalances(): [boolean, (v: boolean) => void] {
  const [hidden, set] = useState<boolean>(readHidden)
  useEffect(() => {
    listeners.add(set)
    set(readHidden())
    // Another tab flipping the switch reaches this one through the storage event.
    const onStorage = (e: StorageEvent) => { if (e.key === HIDE_KEY) set(readHidden()) }
    window.addEventListener('storage', onStorage)
    return () => {
      listeners.delete(set)
      window.removeEventListener('storage', onStorage)
    }
  }, [])
  return [hidden, setHidden]
}
```

Create `dashboard/src/components/Money.tsx`:

```tsx
import Button from './Button'
import { money, signed } from '../lib/format'
import { useHiddenBalances } from '../lib/hideBalances'

export interface MoneyProps {
  value: number | string | null | undefined
  unit?: string
  /** "+1,000.00" / "-250.00" for ledger amounts; the plain form otherwise. */
  signed?: boolean
  className?: string
}

/** A form string ("250"), a number, or nothing. Empty and unparseable read as nothing. */
function asNumber(value: MoneyProps['value']): number | null {
  if (value == null) return null
  const n = typeof value === 'string' ? (value.trim() === '' ? Number.NaN : Number(value)) : value
  return Number.isFinite(n) ? n : null
}

/**
 * The one way a money figure reaches the screen. Honours the hide-balances
 * switch: when hidden it renders four dots named "Hidden amount" and the
 * real value is nowhere in the DOM, so a screen reader hears "hidden" and
 * a shoulder-surfer sees nothing.
 */
export default function Money({ value, unit, signed: withSign, className }: MoneyProps) {
  const [hidden] = useHiddenBalances()
  if (hidden) {
    return <span role="img" aria-label="Hidden amount" className={className}>••••</span>
  }
  let text: string
  if (withSign) {
    const s = signed(asNumber(value))
    text = s === '—' || !unit ? s : `${s} ${unit}`
  } else {
    text = money(value, unit)
  }
  return <span className={['num', className ?? ''].filter(Boolean).join(' ')}>{text}</span>
}

/** The switch itself: a quiet ghost button that says what it will do next. */
export function HideBalancesToggle() {
  const [hidden, setHidden] = useHiddenBalances()
  return (
    <Button variant="ghost" tone="neutral" size="sm" aria-pressed={hidden} onClick={() => setHidden(!hidden)}>
      {hidden ? 'Show balances' : 'Hide balances'}
    </Button>
  )
}
```

Run: `npx vitest run src/lib/hideBalances.test.ts src/components/Money.test.tsx`
Expected: PASS — Test Files 2 passed; Tests 8 passed (8).

- [ ] **Step 8: Write the failing `apiUpload` tests**

Append to `dashboard/src/lib/api.test.ts` (add `apiUpload, orgApi, orgUpload` to the existing import: `import { api, apiUpload, orgApi, orgUpload, type ApiError } from './api'`):

```ts
test('apiUpload posts FormData with the CSRF header and never sets a Content-Type', async () => {
  document.cookie = 'csrf=tok123'
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ id: 5, purpose: 'deposit_receipt', content_type: 'image/png', size_bytes: 10 }), {
      status: 201, headers: { 'Content-Type': 'application/json' },
    }),
  )
  vi.stubGlobal('fetch', fetchMock)
  const form = new FormData()
  form.append('purpose', 'deposit_receipt')
  const result = await apiUpload<{ id: number }>('/api/orgs/1/investor/files', form)
  expect(result.id).toBe(5)
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
  expect(url).toBe('/api/orgs/1/investor/files')
  expect(init.method).toBe('POST')
  expect(init.body).toBe(form)
  expect(init.credentials).toBe('same-origin')
  const headers = init.headers as Record<string, string>
  expect(headers['X-CSRF-Token']).toBe('tok123')
  expect(headers['Content-Type']).toBeUndefined()
})

test('apiUpload surfaces the server detail with the status prefix', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: 'file too large (5 MB max)' }), {
      status: 400, headers: { 'Content-Type': 'application/json' },
    }),
  ))
  await expect(apiUpload('/api/orgs/1/investor/files', new FormData())).rejects.toThrow('400: file too large (5 MB max)')
})

test('orgUpload posts to the org-scoped tail', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
  vi.stubGlobal('fetch', fetchMock)
  await orgUpload(7, 'investor/files', new FormData())
  expect(fetchMock.mock.calls[0][0]).toBe('/api/orgs/7/investor/files')
})

test('orgApi passes redirectOn401 through so a step-up 401 stays inline', async () => {
  // A wrong MPIN on a step-up POST (withdrawal, transfer, destination,
  // adjustment) answers 401 and must surface as an inline error on the
  // page that asked, never as a bounce to /login (spec section 10).
  Object.defineProperty(window, 'location', {
    value: { href: '/org/1/invest/withdraw' },
    writable: true,
  })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: 'Invalid MPIN', attempts_left: 3 }), {
      status: 401, headers: { 'content-type': 'application/json' },
    }),
  ))
  await expect(
    orgApi(1, 'investor/withdrawals', { method: 'POST', body: '{}' }, { redirectOn401: false }),
  ).rejects.toThrow('401: Invalid MPIN')
  expect(window.location.href).toBe('/org/1/invest/withdraw')
})
```

Run: `npx vitest run src/lib/api.test.ts`
Expected: FAIL — the four new tests fail: three with `TypeError: apiUpload is not a function` / `orgUpload is not a function` (a missing named export is `undefined` under vitest, not a SyntaxError), and the `orgApi` step-up test with `expected '/login' to be '/org/1/invest/withdraw'` (the current `orgApi` drops the opts, so the 401 still redirects); the nine existing tests pass.

- [ ] **Step 9: Add `apiUpload` and `orgUpload` to `lib/api.ts`, and let `orgApi` pass `opts` through**

Replace `dashboard/src/lib/api.ts` with (the 401, error and parse logic is the existing code, moved into `finish` so both request shapes share it; `orgApi` gains the same optional `opts` as `api()`):

```ts
/** Every non-2xx throws one of these; `response` carries what the server said. */
export type ApiError = Error & { response?: { status: number; body?: Record<string, unknown> } }

/**
 * Get CSRF token from cookie
 */
function getCsrfToken(): string | null {
  const cookies = document.cookie.split('; ')
  for (const cookie of cookies) {
    const [name, value] = cookie.split('=')
    if (name === 'csrf') {
      return decodeURIComponent(value)
    }
  }
  return null
}

/** Mutations carry the CSRF token; GET and HEAD never need it. */
function addCsrf(headers: Record<string, string>, method: string | undefined): void {
  const m = (method || 'GET').toUpperCase()
  if (m !== 'GET' && m !== 'HEAD') {
    const csrfToken = getCsrfToken()
    if (csrfToken) headers['X-CSRF-Token'] = csrfToken
  }
}

/**
 * What every request does with its answer: the 401 redirect rules, the
 * ApiError on non-2xx, and JSON parsing.
 *
 * A 401 sends the browser to /login, except: /api/login propagates its own
 * 401 as an inline error; a half session's 401 (server detail "MPIN
 * required") goes to /mpin instead of /login; and the MPIN routes
 * (/api/mpin/* and /api/me/mpin) own their 401s ("Invalid MPIN" etc.) as
 * inline errors, never a redirect. Callers that pass
 * `opts.redirectOn401: false` handle every 401 themselves.
 */
async function finish<T>(
  path: string,
  response: Response,
  opts?: { redirectOn401?: boolean },
): Promise<T> {
  // A 401 means "go sign in" -- except when it means "finish signing in":
  // a half session (email+password done, MPIN owed) is told exactly that by
  // the server, and belongs on /mpin. The MPIN routes (/api/mpin/* and
  // /api/me/mpin, the full-session change-MPIN route) own their own 401s
  // ("Invalid MPIN" etc.) as inline errors, like /api/login's.
  if (response.status === 401 && path !== '/api/login' && path !== '/api/me/mpin'
      && !path.startsWith('/api/mpin/') && opts?.redirectOn401 !== false) {
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

  // Throw on non-2xx, surfacing the server's `detail` when the body has one
  // (status prefix kept so callers that pattern-match on the status code —
  // e.g. Join.tsx's `.includes('410')` — keep working).
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

  // Parse JSON if there's content
  if (response.status === 204) {
    return undefined as T
  }

  const contentType = response.headers.get('content-type')
  if (contentType && contentType.includes('application/json')) {
    return response.json() as Promise<T>
  }

  return undefined as T
}

/**
 * Make an API request with CSRF protection and automatic redirect on 401
 * (see `finish` for the redirect rules). A body defaults to JSON.
 */
export async function api<T>(
  path: string,
  init?: RequestInit,
  opts?: { redirectOn401?: boolean },
): Promise<T> {
  const headers = { ...init?.headers } as Record<string, string>
  addCsrf(headers, init?.method)

  // Add default Content-Type for requests with body
  if (init?.body && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json'
  }

  const response = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers,
  })
  return finish<T>(path, response, opts)
}

/**
 * POST a multipart form (a receipt or proof upload). Same CSRF, 401 and
 * error rules as api(); never sets Content-Type, so the browser writes
 * multipart/form-data with its own boundary.
 */
export async function apiUpload<T>(
  path: string,
  form: FormData,
  opts?: { redirectOn401?: boolean },
): Promise<T> {
  const headers: Record<string, string> = {}
  addCsrf(headers, 'POST')
  const response = await fetch(path, {
    method: 'POST',
    body: form,
    credentials: 'same-origin',
    headers,
  })
  return finish<T>(path, response, opts)
}

/**
 * Make an org-scoped API request: GET/POST/etc. against
 * /api/orgs/{orgId}/{tail}. `opts` is api()'s: step-up POSTs (withdrawal,
 * transfer, destination, adjustment) pass `{ redirectOn401: false }` so a
 * wrong MPIN stays an inline error instead of a bounce to /login.
 */
export function orgApi<T>(
  orgId: number,
  tail: string,
  init?: RequestInit,
  opts?: { redirectOn401?: boolean },
): Promise<T> {
  return api<T>(`/api/orgs/${orgId}/${tail}`, init, opts)
}

/** Org-scoped multipart upload: POST /api/orgs/{orgId}/{tail}. */
export function orgUpload<T>(orgId: number, tail: string, form: FormData): Promise<T> {
  return apiUpload<T>(`/api/orgs/${orgId}/${tail}`, form)
}

/**
 * Create a WebSocket connection to the events stream, scoped to an org.
 */
export function eventsSocket(orgId: number): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${protocol}//${window.location.host}/api/ws?org_id=${orgId}`
  return new WebSocket(url)
}
```

Run: `npx vitest run src/lib/api.test.ts`
Expected: PASS — Tests 13 passed (13) (the nine existing plus four new).

- [ ] **Step 10: Write the failing `FileInput` tests**

Create `dashboard/src/components/FileInput.test.tsx`:

```tsx
import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import FileInput, { MAX_UPLOAD_BYTES, RECEIPT_ACCEPT, formatBytes } from './FileInput'

// jsdom has no object URLs; the thumbnail needs one.
const urlApi = URL as unknown as {
  createObjectURL?: (f: Blob) => string
  revokeObjectURL?: (u: string) => void
}

beforeEach(() => {
  urlApi.createObjectURL = vi.fn(() => 'blob:preview')
  urlApi.revokeObjectURL = vi.fn()
})

afterEach(() => {
  delete urlApi.createObjectURL
  delete urlApi.revokeObjectURL
  vi.restoreAllMocks()
})

function Harness({ onChange, required, error, hint }: {
  onChange?: (f: File | null) => void; required?: boolean; error?: string | null; hint?: string
}) {
  const [value, setValue] = useState<File | null>(null)
  return (
    <FileInput
      id="receipt" label="Receipt" accept={RECEIPT_ACCEPT} maxBytes={MAX_UPLOAD_BYTES}
      value={value} onChange={(f) => { setValue(f); onChange?.(f) }}
      required={required} error={error} hint={hint}
    />
  )
}

const png = () => new File([new Uint8Array([137, 80, 78, 71])], 'receipt.png', { type: 'image/png' })

test('the constants match the server limits', () => {
  expect(RECEIPT_ACCEPT).toEqual(['image/jpeg', 'image/png', 'image/webp', 'application/pdf'])
  expect(MAX_UPLOAD_BYTES).toBe(5 * 1024 * 1024)
  expect(formatBytes(512)).toBe('512 B')
  expect(formatBytes(48 * 1024)).toBe('48 KB')
  expect(formatBytes(5 * 1024 * 1024)).toBe('5 MB')
  expect(formatBytes(1.5 * 1024 * 1024)).toBe('1.5 MB')
})

test('the input is labelled, accepts the receipt types and Choose file opens the native picker', async () => {
  render(<Harness />)
  const input = screen.getByLabelText('Receipt') as HTMLInputElement
  expect(input.type).toBe('file')
  expect(input).toHaveAttribute('accept', 'image/jpeg,image/png,image/webp,application/pdf')
  expect(screen.getByText('No file chosen')).toBeInTheDocument()
  // click() lives on HTMLElement.prototype in jsdom; the button forwards it
  // to the hidden input, so the input must be among the spy's receivers.
  const open = vi.spyOn(HTMLElement.prototype, 'click')
  await userEvent.click(screen.getByRole('button', { name: 'Choose file' }))
  expect(open.mock.instances).toContain(input)
})

test('an accepted image shows its name, size and a thumbnail; Remove clears it and revokes the URL', async () => {
  const onChange = vi.fn()
  const { container } = render(<Harness onChange={onChange} />)
  const file = png()
  await userEvent.upload(screen.getByLabelText('Receipt'), file)
  expect(onChange).toHaveBeenLastCalledWith(file)
  expect(screen.getByText('receipt.png')).toBeInTheDocument()
  expect(screen.getByText('(4 B)')).toBeInTheDocument()
  expect(urlApi.createObjectURL).toHaveBeenCalledWith(file)
  expect(container.querySelector('img')).toHaveAttribute('src', 'blob:preview')
  expect(screen.queryByRole('alert')).toBeNull()

  await userEvent.click(screen.getByRole('button', { name: 'Remove' }))
  expect(onChange).toHaveBeenLastCalledWith(null)
  expect(screen.getByText('No file chosen')).toBeInTheDocument()
  expect(container.querySelector('img')).toBeNull()
  expect(urlApi.revokeObjectURL).toHaveBeenCalledWith('blob:preview')
})

test('a type outside the accept list is refused before anything is chosen', async () => {
  const onChange = vi.fn()
  render(<Harness onChange={onChange} />)
  // user-event discards non-matching files itself unless told not to; the
  // component's own check is what is under test here.
  const user = userEvent.setup({ applyAccept: false })
  await user.upload(screen.getByLabelText('Receipt'), new File(['hi'], 'notes.txt', { type: 'text/plain' }))
  expect(screen.getByRole('alert')).toHaveTextContent('That file type is not accepted')
  expect(onChange).toHaveBeenLastCalledWith(null)
  expect(screen.getByText('No file chosen')).toBeInTheDocument()
  expect(screen.getByLabelText('Receipt')).toHaveAttribute('aria-invalid', 'true')
})

test('a file over the limit is refused with the limit named', async () => {
  const onChange = vi.fn()
  render(<Harness onChange={onChange} />)
  const big = new File([new Uint8Array(MAX_UPLOAD_BYTES + 1)], 'big.png', { type: 'image/png' })
  await userEvent.upload(screen.getByLabelText('Receipt'), big)
  expect(screen.getByRole('alert')).toHaveTextContent('File is larger than 5 MB')
  expect(onChange).toHaveBeenLastCalledWith(null)
})

test('required marks the input and shows in the hint; a caller error is announced', () => {
  render(<Harness required hint="JPEG, PNG, WebP or PDF up to 5 MB." error="Upload the receipt first" />)
  const input = screen.getByLabelText('Receipt')
  expect(input).toBeRequired()
  expect(screen.getByText('Required. JPEG, PNG, WebP or PDF up to 5 MB.')).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('Upload the receipt first')
  expect(input).toHaveAccessibleDescription(/Upload the receipt first/)
})
```

Run: `npx vitest run src/components/FileInput.test.tsx`
Expected: FAIL — `Failed to resolve import "./FileInput"`.

- [ ] **Step 11: Implement `components/FileInput.tsx`**

Create `dashboard/src/components/FileInput.tsx`:

```tsx
import { useEffect, useId, useRef, useState, type ChangeEvent } from 'react'
import Button from './Button'

/** What a deposit receipt or payout proof may be. The server sniffs the
 *  bytes and refuses anything else; this list only saves a round trip. */
export const RECEIPT_ACCEPT = ['image/jpeg', 'image/png', 'image/webp', 'application/pdf']
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024

export interface FileInputProps {
  id: string
  label: string
  accept: string[]
  maxBytes: number
  value: File | null
  onChange: (file: File | null) => void
  required?: boolean
  hint?: string
  /** The caller's own refusal (e.g. "Upload the receipt first"); shown in
   *  place of the component's type/size message. */
  error?: string | null
  disabled?: boolean
}

/** "512 B", "48 KB", "5 MB", "1.5 MB". */
export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  const mb = n / (1024 * 1024)
  return `${Number.isInteger(mb) ? mb : mb.toFixed(1)} MB`
}

/**
 * The desk's one file field: a visually hidden native input behind a
 * secondary "Choose file" button, the chosen file's name and size, a
 * thumbnail for images (an object URL, revoked when the file changes) and
 * a ghost Remove. Type and size are refused here first so the investor sees
 * why before a byte is sent; the server checks again by sniffing.
 */
export default function FileInput({
  id, label, accept, maxBytes, value, onChange, required, hint, error, disabled,
}: FileInputProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const hintId = useId()
  const errorId = useId()

  useEffect(() => {
    if (!value || !value.type.startsWith('image/') || typeof URL.createObjectURL !== 'function') {
      setPreview(null)
      return
    }
    const url = URL.createObjectURL(value)
    setPreview(url)
    return () => { URL.revokeObjectURL(url) }
  }, [value])

  const pick = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.currentTarget.files?.[0] ?? null
    // Cleared so the same file can be chosen again after Remove.
    e.currentTarget.value = ''
    if (!file) return
    if (!accept.includes(file.type)) {
      setRefusal('That file type is not accepted')
      onChange(null)
      return
    }
    if (file.size > maxBytes) {
      setRefusal(`File is larger than ${formatBytes(maxBytes)}`)
      onChange(null)
      return
    }
    setRefusal(null)
    onChange(file)
  }

  const remove = () => {
    setRefusal(null)
    onChange(null)
  }

  const problem = error ?? refusal
  const hintLine = [required ? 'Required.' : null, hint].filter(Boolean).join(' ')
  const describedBy = [hintLine ? hintId : null, problem ? errorId : null].filter(Boolean).join(' ')

  return (
    <div>
      <label htmlFor={id} className="desk-label block mb-1">{label}</label>
      <input
        ref={inputRef}
        id={id}
        type="file"
        accept={accept.join(',')}
        className="sr-only"
        onChange={pick}
        disabled={disabled}
        required={required}
        aria-required={required || undefined}
        aria-invalid={problem ? true : undefined}
        aria-describedby={describedBy || undefined}
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="secondary" size="sm" disabled={disabled} onClick={() => inputRef.current?.click()}>
          Choose file
        </Button>
        {value ? (
          <>
            {preview && (
              <img src={preview} alt="" className="h-12 w-12 rounded-inset border border-line object-cover" />
            )}
            <span className="text-sm text-ink">
              {value.name}{' '}
              <span className="text-ink-faint">({formatBytes(value.size)})</span>
            </span>
            <Button variant="ghost" tone="neutral" size="sm" disabled={disabled} onClick={remove}>
              Remove
            </Button>
          </>
        ) : (
          <span className="text-sm text-ink-faint">No file chosen</span>
        )}
      </div>
      {hintLine && <p id={hintId} className="mt-1 text-xs text-ink-soft">{hintLine}</p>}
      {problem && <p id={errorId} role="alert" className="mt-1 text-sm text-loss-deep">{problem}</p>}
    </div>
  )
}
```

Run: `npx vitest run src/components/FileInput.test.tsx`
Expected: PASS — Tests 6 passed (6).

- [ ] **Step 12: Write the failing `PinConfirmDialog` tests**

Create `dashboard/src/components/PinConfirmDialog.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test, vi } from 'vitest'
import PinConfirmDialog, { mpinErrorText } from './PinConfirmDialog'
import type { ApiError } from '../lib/api'

function apiError(status: number, body: Record<string, unknown>, detail = 'refused'): ApiError {
  const err = new Error(`${status}: ${detail}`) as ApiError
  err.response = { status, body }
  return err
}

function renderDialog(onConfirm: (mpin: string) => Promise<void>, open = true) {
  const onCancel = vi.fn()
  const view = render(
    <PinConfirmDialog open={open} title="Send 250.00 USD to ICICI Bank ••4543?" confirmLabel="Send 250.00 USD"
                      onConfirm={onConfirm} onCancel={onCancel}>
      <p>Fee 2.50 USD, you receive 247.50 USD.</p>
    </PinConfirmDialog>,
  )
  return { onCancel, ...view }
}

async function typePin(pin: string) {
  await userEvent.click(screen.getByLabelText('Your MPIN digit 1 of 6'))
  await userEvent.keyboard(pin)
}

const boxes = () => screen.getAllByLabelText(/Your MPIN digit \d of 6/) as HTMLInputElement[]

test('the summary and the MPIN boxes sit inside the dialog; confirm waits for six digits, then passes the MPIN', async () => {
  const onConfirm = vi.fn(async () => {})
  renderDialog(onConfirm)
  const dialog = screen.getByRole('dialog', { name: 'Send 250.00 USD to ICICI Bank ••4543?' })
  expect(dialog).toHaveTextContent('Fee 2.50 USD, you receive 247.50 USD.')
  expect(boxes()).toHaveLength(6)
  const confirm = screen.getByRole('button', { name: 'Send 250.00 USD' })
  expect(confirm).toBeDisabled()

  await typePin('12345')
  expect(confirm).toBeDisabled()
  await userEvent.keyboard('6')
  expect(confirm).toBeEnabled()

  await userEvent.click(confirm)
  await waitFor(() => expect(onConfirm).toHaveBeenCalledWith('123456'))
  expect(onConfirm).toHaveBeenCalledTimes(1)
})

test('a wrong MPIN names the tries left, clears the boxes and keeps the dialog open', async () => {
  const onConfirm = vi.fn().mockRejectedValue(apiError(401, { detail: 'Invalid MPIN', attempts_left: 2 }, 'Invalid MPIN'))
  renderDialog(onConfirm)
  await typePin('111111')
  await userEvent.click(screen.getByRole('button', { name: 'Send 250.00 USD' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Wrong MPIN, 2 tries left')
  expect(boxes().map((b) => b.value).join('')).toBe('')
  expect(screen.getByRole('button', { name: 'Send 250.00 USD' })).toBeDisabled()
  expect(screen.getByRole('dialog')).toBeInTheDocument()
})

test('the last try is singular', async () => {
  const onConfirm = vi.fn().mockRejectedValue(apiError(401, { attempts_left: 1 }))
  renderDialog(onConfirm)
  await typePin('111111')
  await userEvent.click(screen.getByRole('button', { name: 'Send 250.00 USD' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Wrong MPIN, 1 try left')
})

test('a locked MPIN says about how many minutes to wait', async () => {
  const until = new Date(Date.now() + 14 * 60_000 - 5_000).toISOString()
  const onConfirm = vi.fn().mockRejectedValue(apiError(423, { detail: 'MPIN locked', locked_until: until }, 'MPIN locked'))
  renderDialog(onConfirm)
  await typePin('111111')
  await userEvent.click(screen.getByRole('button', { name: 'Send 250.00 USD' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('MPIN locked. Try again in about 14 minutes')
})

test('an unset MPIN and any other refusal read plainly', async () => {
  expect(mpinErrorText(apiError(409, { detail: 'MPIN not set' }, 'MPIN not set'), 'Could not confirm')).toBe('Set your MPIN first')
  expect(mpinErrorText(apiError(400, { detail: 'amount exceeds what is available (1,250.00)' },
    'amount exceeds what is available (1,250.00)'), 'Could not confirm')).toBe('amount exceeds what is available (1,250.00)')
  expect(mpinErrorText('boom', 'Could not confirm')).toBe('Could not confirm')

  const onConfirm = vi.fn().mockRejectedValue(apiError(400, { detail: 'minimum withdrawal is 50.00' }, 'minimum withdrawal is 50.00'))
  renderDialog(onConfirm)
  await typePin('123456')
  await userEvent.click(screen.getByRole('button', { name: 'Send 250.00 USD' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('minimum withdrawal is 50.00')
})

test('Cancel is the first focus and reports to the caller; closing forgets the PIN and the error', async () => {
  const onConfirm = vi.fn().mockRejectedValue(apiError(401, { attempts_left: 4 }))
  const { onCancel, rerender } = renderDialog(onConfirm)
  expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
  await typePin('111111')
  await userEvent.click(screen.getByRole('button', { name: 'Send 250.00 USD' }))
  await screen.findByRole('alert')
  await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(onCancel).toHaveBeenCalledTimes(1)

  rerender(
    <PinConfirmDialog open={false} title="Send 250.00 USD to ICICI Bank ••4543?" confirmLabel="Send 250.00 USD"
                      onConfirm={onConfirm} onCancel={onCancel}><p>x</p></PinConfirmDialog>,
  )
  expect(screen.queryByRole('dialog')).toBeNull()
  rerender(
    <PinConfirmDialog open title="Send 250.00 USD to ICICI Bank ••4543?" confirmLabel="Send 250.00 USD"
                      onConfirm={onConfirm} onCancel={onCancel}><p>x</p></PinConfirmDialog>,
  )
  expect(screen.queryByRole('alert')).toBeNull()
  expect(boxes().map((b) => b.value).join('')).toBe('')
})
```

Run: `npx vitest run src/components/PinConfirmDialog.test.tsx`
Expected: FAIL — `Failed to resolve import "./PinConfirmDialog"`.

- [ ] **Step 13: Implement `components/PinConfirmDialog.tsx`**

Create `dashboard/src/components/PinConfirmDialog.tsx`:

```tsx
import { useEffect, useState, type ReactNode } from 'react'
import type { ApiError } from '../lib/api'
import { errorText } from '../lib/format'
import ConfirmDialog from './ConfirmDialog'
import PinInput from './PinInput'

export interface PinConfirmDialogProps {
  open: boolean
  title: string
  /** The summary of what is about to happen ("Fee 2.50 USD, you receive 247.50 USD."). */
  children: ReactNode
  confirmLabel: string
  busy?: boolean
  /** Runs the request with the MPIN. Reject to keep the dialog open with the
   *  refusal shown; resolve and the CALLER closes the dialog. */
  onConfirm: (mpin: string) => Promise<void>
  onCancel: () => void
}

const MPIN_LENGTH = 6

/**
 * The words for a refused MPIN step-up, the same ones AccountSecurity uses
 * for a refused MPIN change: 401 with attempts_left, 423 with locked_until,
 * 409 when no MPIN is set, otherwise the server's detail without its code.
 */
export function mpinErrorText(err: unknown, fallback: string): string {
  const res = (err as ApiError | undefined)?.response
  const left = res?.body?.attempts_left
  const until = res?.body?.locked_until
  if (res?.status === 401 && typeof left === 'number') {
    return `Wrong MPIN, ${left} ${left === 1 ? 'try' : 'tries'} left`
  }
  if (res?.status === 423 && typeof until === 'string') {
    const minutes = Math.max(1, Math.ceil((Date.parse(until) - Date.now()) / 60000))
    return `MPIN locked. Try again in about ${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`
  }
  if (res?.status === 409) return 'Set your MPIN first'
  return errorText(err, fallback)
}

/**
 * ConfirmDialog with the MPIN as the confirmation: the summary the caller
 * passes, six masked boxes, and a confirm button that only unlocks on the
 * sixth digit. A refused MPIN is announced under the boxes and the boxes
 * are cleared, so a second guess starts clean. The dialog never closes
 * itself: the caller flips `open` on success (and refreshes its list).
 */
export default function PinConfirmDialog({
  open, title, children, confirmLabel, busy, onConfirm, onCancel,
}: PinConfirmDialogProps) {
  const [pin, setPin] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)

  // A closed dialog forgets the PIN and the last refusal.
  useEffect(() => {
    if (!open) {
      setPin('')
      setError(null)
      setWorking(false)
    }
  }, [open])

  const confirm = async () => {
    if (pin.length !== MPIN_LENGTH || working) return
    setError(null)
    setWorking(true)
    try {
      await onConfirm(pin)
    } catch (err) {
      setError(mpinErrorText(err, 'Could not confirm'))
      setPin('')
    } finally {
      setWorking(false)
    }
  }

  const inFlight = Boolean(busy) || working

  return (
    <ConfirmDialog
      open={open}
      title={title}
      confirmLabel={confirmLabel}
      busy={inFlight}
      disabled={pin.length !== MPIN_LENGTH}
      onConfirm={() => { void confirm() }}
      onCancel={onCancel}
    >
      {children}
      <PinInput id="confirm-mpin" label="Your MPIN" value={pin} onChange={setPin} disabled={inFlight} error={error} />
    </ConfirmDialog>
  )
}
```

Run: `npx vitest run src/components/PinConfirmDialog.test.tsx`
Expected: PASS — Tests 6 passed (6).

- [ ] **Step 14: Add the portal fixtures with a shape test**

Create `dashboard/src/test/portalFixtures.ts`:

```ts
import type {
  InvestorRow, InvestorSummary, PayoutDestination, PaymentMethod, PortalDeposit, PortalTransfer,
  PortalWithdrawal, WalletEntry,
} from '../lib/types'

/**
 * Realistic portal rows for page tests. Every builder takes overrides so a
 * test states only what it cares about. Figures: main balance 5,120.50,
 * on hold 100.00, available 5,020.50; a 250.00 USDT deposit pending; an
 * approved ICICI bank payout account; a 250.00 withdrawal requested.
 */

const WHEN = '2026-09-29T10:05:06Z'

export function summaryFixture(overrides: Partial<InvestorSummary> = {}): InvestorSummary {
  return {
    org: { id: 1, name: 'Acme' },
    currency: 'USD',
    investor: { display_name: 'Sherwyn Joel', first_name: 'Sherwyn', member_since: '2026-09-01T09:00:00Z' },
    wallets: {
      main: { balance: 5120.5, on_hold: 100, available: 5020.5 },
      credit: { balance: 0, on_hold: 0, available: 0 },
      pamm: { balance: 250, on_hold: 0, available: 250 },
      social: { balance: 0, on_hold: 0, available: 0 },
    },
    totals: { deposited: 6000, withdrawn: 500, transferred_in: 0, transferred_out: 1000 },
    cash_flow: [
      { date: '2026-09-27', deposits: 1000, withdrawals: 0 },
      { date: '2026-09-28', deposits: 0, withdrawals: 500 },
      { date: '2026-09-29', deposits: 250, withdrawals: 0 },
    ],
    pending: { deposits: 1, withdrawals: 0, transfers: 0, payout_destinations: 0 },
    deposits_open: true,
    withdrawal_rules: { min: 50, fee_pct: 1 },
    link_state: 'linked',
    account: {
      account_id: 555, nickname: 'Growth', platform: 'ctrader', status: 'ok', last_error: null, connected: true,
    },
    equity_source: 'live',
    equity: 1240.25,
    net_funded: 1000,
    profit: 240.25,
    account_available: 1240.25,
    open_positions: 2,
    ...overrides,
  }
}

export function depositFixture(overrides: Partial<PortalDeposit> = {}): PortalDeposit {
  return {
    id: 12, user_id: 1, method_id: 3, method_kind: 'crypto', method_label: 'USDT on TRC20',
    amount: 250, fee: 2.5, credited_amount: null, reference: 'abc123txhash', receipt_file_id: null,
    target: 'wallet', target_account_id: null, note: null, status: 'pending',
    decided_by: null, decided_at: null, decision_note: null, created_at: WHEN, currency: 'USD',
    ...overrides,
  }
}

export function withdrawalFixture(overrides: Partial<PortalWithdrawal> = {}): PortalWithdrawal {
  return {
    id: 4, user_id: 1, destination_id: 2, destination_kind: 'bank', destination_summary: 'ICICI Bank ••4543',
    amount: 250, fee: 2.5, net_amount: 247.5, status: 'requested',
    decided_by: null, decided_at: null, decision_note: null, paid_by: null, paid_at: null, txid: null,
    created_at: WHEN, currency: 'USD',
    ...overrides,
  }
}

export function transferFixture(overrides: Partial<PortalTransfer> = {}): PortalTransfer {
  return {
    id: 9, user_id: 1,
    source: { kind: 'wallet', wallet: 'main' },
    target: { kind: 'account', account_id: 555 },
    amount: 500, status: 'requested', equity_at_request: null, equity_verified: false,
    decided_by: null, decided_at: null, decision_note: null, done_by: null, done_at: null, note: null,
    created_at: WHEN, currency: 'USD',
    ...overrides,
  }
}

export function destinationFixture(overrides: Partial<PayoutDestination> = {}): PayoutDestination {
  return {
    id: 2, user_id: 1, kind: 'bank', nickname: 'Salary account',
    details: {
      bank_name: 'ICICI Bank', holder: 'Sherwyn Joel', account_number: '000401234543', code: 'ICIC0000004',
      bank_address: 'Mumbai', country: 'IN',
    },
    proof_file_id: null, status: 'approved',
    decided_by: 7, decided_at: '2026-09-20T12:00:00Z', decision_note: null, created_at: '2026-09-19T12:00:00Z',
    summary: 'ICICI Bank ••4543',
    ...overrides,
  }
}

export function entryFixture(overrides: Partial<WalletEntry> = {}): WalletEntry {
  return {
    id: 31, wallet: 'main', amount: 250, kind: 'deposit', ref_table: 'deposits', ref_id: 12, note: null,
    created_at: WHEN, currency: 'USD',
    ...overrides,
  }
}

export function methodFixture(overrides: Partial<PaymentMethod> = {}): PaymentMethod {
  return {
    id: 3, kind: 'crypto', label: 'USDT on TRC20', enabled: true, currency: 'USD',
    details: { coin: 'USDT', network: 'TRC20', address: 'TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE9f' },
    min_amount: 50, fee_pct: 1, instructions: null, sort_order: 0,
    ...overrides,
  }
}

export function investorRowFixture(overrides: Partial<InvestorRow> = {}): InvestorRow {
  return {
    user_id: 1, email: 'investor@example.com', display_name: 'Sherwyn Joel', joined_at: '2026-09-01T09:00:00Z',
    account_id: 555, nickname: 'Growth', equity: 1240.25, equity_source: 'live',
    balances: { main: 5120.5, credit: 0, pamm: 250, social: 0 }, on_hold: 100, available: 5020.5,
    pending: { deposits: 1, withdrawals: 0, transfers: 0, payout_destinations: 0 },
    ...overrides,
  }
}
```

Create `dashboard/src/test/portalFixtures.test.ts`:

```ts
import { expect, test } from 'vitest'
import {
  depositFixture, destinationFixture, entryFixture, investorRowFixture, methodFixture, summaryFixture,
  transferFixture, withdrawalFixture,
} from './portalFixtures'

test('the summary carries the documented figures and every wallet', () => {
  const s = summaryFixture()
  expect(s.wallets.main).toEqual({ balance: 5120.5, on_hold: 100, available: 5020.5 })
  expect(Object.keys(s.wallets)).toEqual(['main', 'credit', 'pamm', 'social'])
  expect(s.currency).toBe('USD')
  expect(summaryFixture({ link_state: 'unlinked', account: null }).account).toBeNull()
})

test('every row builder merges overrides over a complete row', () => {
  expect(depositFixture({ status: 'confirmed', credited_amount: 247.5 }).credited_amount).toBe(247.5)
  expect(withdrawalFixture().net_amount).toBe(247.5)
  expect(transferFixture().source).toEqual({ kind: 'wallet', wallet: 'main' })
  expect(destinationFixture().summary).toBe('ICICI Bank ••4543')
  expect(entryFixture({ amount: -250, kind: 'withdrawal' }).amount).toBe(-250)
  expect(methodFixture({ kind: 'bank', label: 'ICICI Bank' }).label).toBe('ICICI Bank')
  expect(investorRowFixture().available).toBe(5020.5)
})
```

Run: `npx vitest run src/test/portalFixtures.test.ts`
Expected: PASS — Tests 2 passed (2).

- [ ] **Step 15: Full dashboard gate**

Run from `dashboard/`: `npm test`
Expected: palette prover prints its pass line and exits 0; `tsc` prints nothing; vitest ends with every file passed (the existing InvestorOverview / InvestorDeposit / InvestorWithdraw / Investors tests still pass because their pages and their behaviour are unchanged). If vitest is slow on this machine, run it as `npx vitest run --maxWorkers=2 --minWorkers=1` after `node scripts/palette_check.mjs && npx tsc --noEmit -p tsconfig.app.json`.

- [ ] **Step 16: Commit**

```bash
git add dashboard/src/lib/types.ts dashboard/src/lib/legacyInvestorTypes.ts dashboard/src/lib/investor.ts dashboard/src/lib/investor.test.ts dashboard/src/lib/hideBalances.ts dashboard/src/lib/hideBalances.test.ts dashboard/src/lib/api.ts dashboard/src/lib/api.test.ts dashboard/src/components/Money.tsx dashboard/src/components/Money.test.tsx dashboard/src/components/FileInput.tsx dashboard/src/components/FileInput.test.tsx dashboard/src/components/PinConfirmDialog.tsx dashboard/src/components/PinConfirmDialog.test.tsx dashboard/src/test/portalFixtures.ts dashboard/src/test/portalFixtures.test.ts dashboard/src/pages/investor/InvestorOverview.tsx dashboard/src/pages/investor/InvestorDeposit.tsx dashboard/src/pages/investor/InvestorWithdraw.tsx dashboard/src/pages/Investors.tsx
git commit -m "feat(dashboard): portal types and vocabulary, hide-balances store, Money, FileInput, PinConfirmDialog, multipart upload helper

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 12: Navigation — grouped investor rail, phone tab bar with More for every role, the admin Requests link and its live badge

The shell learns the portal's shape before the pages exist. After this task the rail links `invest/wallet`, `invest/transfer`, `invest/transactions`, `invest/payout-accounts` and the admin `requests` link render the org-scoped `NotFound` page (the `*` route inside `/org/:orgId` in `App.tsx`) until Tasks 13–19 add their routes; that is expected and harmless — Layout keeps investors inside the portal because those paths start with `/org/:id/invest/`.

**Files:**
- Modify: `dashboard/src/components/layout/nav.ts` (`NavItem.badge`, three investor groups, four-item investor tab bar, Requests in the Org group with the badge)
- Create: `dashboard/src/components/layout/nav.test.ts`
- Modify: `dashboard/src/components/layout/NavRail.tsx` (badge pill after the label), `NavRail.test.tsx`
- Modify: `dashboard/src/components/layout/BottomBar.tsx` (More for every role), `BottomBar.test.tsx`
- Modify: `dashboard/src/hooks/useLiveRefresh.ts` (`orgId: number | null` — null opens no socket), `useLiveRefresh.test.tsx`
- Create: `dashboard/src/hooks/useRequestsBadge.ts`, `dashboard/src/hooks/useRequestsBadge.test.tsx`
- Modify: `dashboard/src/components/Layout.tsx` (call the hook, pass the badge), `Layout.test.tsx`

**Interfaces:**
- Consumes: `RequestsSummary` (Task 11 types), `orgApi`, `eventsSocket`, `can`/`Role`, `Badge`, `useLiveRefresh`.
- Produces: `NavItem { path; label; end?; badge? }`, `adminNav(orgId, role, requestsBadge?)`, `investorNav(orgId)` (groups `''`, `Money`, `Trading`), `bottomBarItems(orgId, role)` (investors: Dashboard, Deposit, Withdraw, Transactions), `useRequestsBadge(orgId, role): number | undefined`, `REQUESTS_POLL_MS`; NavRail renders `<Badge tone="neutral">` after the label and names the link `"<label>, <n> open"` when `badge > 0`; BottomBar shows More for every role.

- [ ] **Step 1: Write the failing nav data tests**

Create `dashboard/src/components/layout/nav.test.ts`:

```ts
import { expect, test } from 'vitest'
import { adminNav, bottomBarItems, investorNav } from './nav'

test('investorNav is three groups: the Dashboard alone, Money, and Trading', () => {
  const groups = investorNav(7)
  expect(groups.map((g) => g.name)).toEqual(['', 'Money', 'Trading'])
  expect(groups[0].items).toEqual([{ path: '/org/7/invest', label: 'Dashboard', end: true }])
  expect(groups[1].items.map((i) => [i.label, i.path])).toEqual([
    ['Wallet', '/org/7/invest/wallet'],
    ['Deposit', '/org/7/invest/deposit'],
    ['Withdraw', '/org/7/invest/withdraw'],
    ['Transfer', '/org/7/invest/transfer'],
    ['Transactions', '/org/7/invest/transactions'],
    ['Payout accounts', '/org/7/invest/payout-accounts'],
  ])
  expect(groups[2].items.map((i) => [i.label, i.path])).toEqual([
    ['Account', '/org/7/invest/account'],
    ['History', '/org/7/invest/history'],
  ])
  expect(groups.flatMap((g) => g.items)).toHaveLength(9)
})

test('the investor tab bar is the four money-first pages, Dashboard exact-matched', () => {
  expect(bottomBarItems(7, 'investor')).toEqual([
    { path: '/org/7/invest', label: 'Dashboard', end: true },
    { path: '/org/7/invest/deposit', label: 'Deposit' },
    { path: '/org/7/invest/withdraw', label: 'Withdraw' },
    { path: '/org/7/invest/transactions', label: 'Transactions' },
  ])
  expect(bottomBarItems(7, 'admin').map((i) => i.label)).toEqual(['Overview', 'Positions', 'Trade', 'Accounts'])
  expect(bottomBarItems(7, 'viewer').map((i) => i.label)).toEqual(['Overview', 'Positions', 'History', 'Accounts'])
})

test('the admin Org group is Members, Investors, Requests, Logs; viewers get Members and Logs', () => {
  const org = adminNav(7, 'admin').find((g) => g.name === 'Org')!
  expect(org.items.map((i) => [i.label, i.path])).toEqual([
    ['Members', '/org/7/members'],
    ['Investors', '/org/7/investors'],
    ['Requests', '/org/7/requests'],
    ['Logs', '/org/7/logs'],
  ])
  const viewerOrg = adminNav(7, 'viewer').find((g) => g.name === 'Org')!
  expect(viewerOrg.items.map((i) => i.label)).toEqual(['Members', 'Logs'])
  expect(adminNav(7, 'admin').flatMap((g) => g.items)).toHaveLength(11)
})

test('the Requests item carries the open count it was given, and nothing else does', () => {
  const items = adminNav(7, 'admin', 3).flatMap((g) => g.items)
  expect(items.find((i) => i.label === 'Requests')?.badge).toBe(3)
  expect(items.filter((i) => i.badge !== undefined)).toHaveLength(1)
  expect(adminNav(7, 'admin').flatMap((g) => g.items).find((i) => i.label === 'Requests')?.badge).toBeUndefined()
})
```

Run: `npx vitest run src/components/layout/nav.test.ts`
Expected: FAIL — the first test fails with `expected [ '' ] to deeply equal [ '', 'Money', 'Trading' ]`; the others fail on the Requests item and the investor tab bar.

- [ ] **Step 2: Rewrite `nav.ts`**

Replace `dashboard/src/components/layout/nav.ts` with:

```ts
import { can, type Role } from '../../lib/roles'

export interface NavItem {
  path: string
  label: string
  /** Match only the exact path (the org root), not every child. */
  end?: boolean
  /** Open items waiting behind the link (the admin Requests desk); the rail
   *  shows it as a neutral pill and folds it into the link's name. */
  badge?: number
}

export interface NavGroup {
  name: string
  items: NavItem[]
}

/** The admin/viewer rail: three groups, eleven links at most.
 *  `requestsBadge` is the open-request total from useRequestsBadge. */
export function adminNav(orgId: number, role: Role, requestsBadge?: number): NavGroup[] {
  const o = `/org/${orgId}`
  const trade = can(role, 'trade')
  return [
    {
      name: 'Desk',
      items: [
        { path: o, label: 'Overview', end: true },
        { path: `${o}/positions`, label: 'Positions' },
        ...(trade ? [{ path: `${o}/trade`, label: 'Trade' }] : []),
        { path: `${o}/history`, label: 'History' },
      ],
    },
    {
      name: 'Fleet',
      items: [
        { path: `${o}/accounts`, label: 'Accounts' },
        ...(trade ? [{ path: `${o}/automation`, label: 'Automation' }] : []),
        { path: `${o}/performance`, label: 'Performance' },
      ],
    },
    {
      name: 'Org',
      items: [
        { path: `${o}/members`, label: 'Members' },
        ...(can(role, 'control') ? [
          { path: `${o}/investors`, label: 'Investors' },
          { path: `${o}/requests`, label: 'Requests', badge: requestsBadge },
        ] : []),
        { path: `${o}/logs`, label: 'Logs' },
      ],
    },
  ]
}

/** The investor portal: the dashboard, their money, their trading account. */
export function investorNav(orgId: number): NavGroup[] {
  const p = `/org/${orgId}/invest`
  return [
    {
      name: '',
      items: [{ path: p, label: 'Dashboard', end: true }],
    },
    {
      name: 'Money',
      items: [
        { path: `${p}/wallet`, label: 'Wallet' },
        { path: `${p}/deposit`, label: 'Deposit' },
        { path: `${p}/withdraw`, label: 'Withdraw' },
        { path: `${p}/transfer`, label: 'Transfer' },
        { path: `${p}/transactions`, label: 'Transactions' },
        { path: `${p}/payout-accounts`, label: 'Payout accounts' },
      ],
    },
    {
      name: 'Trading',
      items: [
        { path: `${p}/account`, label: 'Account' },
        { path: `${p}/history`, label: 'History' },
      ],
    },
  ]
}

/** The phone tab bar: four links in thumb reach for every role; More opens
 *  the full menu with every group. */
export function bottomBarItems(orgId: number, role: Role): NavItem[] {
  if (role === 'investor') {
    const p = `/org/${orgId}/invest`
    return [
      { path: p, label: 'Dashboard', end: true },
      { path: `${p}/deposit`, label: 'Deposit' },
      { path: `${p}/withdraw`, label: 'Withdraw' },
      { path: `${p}/transactions`, label: 'Transactions' },
    ]
  }
  const o = `/org/${orgId}`
  return [
    { path: o, label: 'Overview', end: true },
    { path: `${o}/positions`, label: 'Positions' },
    can(role, 'trade') ? { path: `${o}/trade`, label: 'Trade' } : { path: `${o}/history`, label: 'History' },
    { path: `${o}/accounts`, label: 'Accounts' },
  ]
}
```

Run: `npx vitest run src/components/layout/nav.test.ts`
Expected: PASS — Tests 4 passed (4).

- [ ] **Step 3: Write the failing NavRail and BottomBar tests**

Replace `dashboard/src/components/layout/NavRail.test.tsx` with:

```tsx
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'
import NavRail from './NavRail'
import { adminNav, investorNav } from './nav'

test('admin groups are Desk, Fleet and Org, and the current page carries aria-current', () => {
  render(
    <MemoryRouter initialEntries={['/org/7/accounts']}>
      <NavRail groups={adminNav(7, 'admin')} />
    </MemoryRouter>
  )
  expect(screen.getByRole('navigation', { name: 'Main' })).toBeInTheDocument()
  for (const g of ['Desk', 'Fleet', 'Org']) expect(screen.getByText(g)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Accounts' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: 'Overview' })).not.toHaveAttribute('aria-current')
  expect(screen.getByRole('link', { name: 'Requests' })).toHaveAttribute('href', '/org/7/requests')
})

test('a viewer sees no Trade, Automation, Investors or Requests; an investor sees nine portal links in Money and Trading groups', () => {
  const { unmount } = render(
    <MemoryRouter initialEntries={['/org/7']}>
      <NavRail groups={adminNav(7, 'viewer')} />
    </MemoryRouter>
  )
  for (const name of ['Trade', 'Automation', 'Investors', 'Requests']) {
    expect(screen.queryByRole('link', { name })).toBeNull()
  }
  unmount()
  render(
    <MemoryRouter initialEntries={['/org/7/invest']}>
      <NavRail groups={investorNav(7)} />
    </MemoryRouter>
  )
  expect(screen.getAllByRole('link')).toHaveLength(9)
  expect(screen.getByText('Money')).toBeInTheDocument()
  expect(screen.getByText('Trading')).toBeInTheDocument()
  expect(screen.queryByText('Desk')).toBeNull()
  expect(screen.getByRole('link', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: 'Wallet' })).not.toHaveAttribute('aria-current')
  expect(screen.getByRole('link', { name: 'Payout accounts' })).toHaveAttribute('href', '/org/7/invest/payout-accounts')
})

test('the Requests link carries its open count as a neutral pill and says so in its name', () => {
  render(
    <MemoryRouter initialEntries={['/org/7']}>
      <NavRail groups={adminNav(7, 'admin', 3)} />
    </MemoryRouter>
  )
  const link = screen.getByRole('link', { name: 'Requests, 3 open' })
  expect(link).toHaveAttribute('href', '/org/7/requests')
  const pill = within(link).getByText('3')
  expect(pill.className).toContain('bg-line')
  expect(pill.className).toContain('rounded-full')
})

test('a zero or unknown count shows no pill and leaves the name alone', () => {
  const { unmount } = render(
    <MemoryRouter initialEntries={['/org/7']}>
      <NavRail groups={adminNav(7, 'admin', 0)} />
    </MemoryRouter>
  )
  expect(screen.getByRole('link', { name: 'Requests' })).toBeInTheDocument()
  expect(screen.queryByText('0')).toBeNull()
  unmount()
  render(
    <MemoryRouter initialEntries={['/org/7']}>
      <NavRail groups={adminNav(7, 'admin')} />
    </MemoryRouter>
  )
  expect(screen.getByRole('link', { name: 'Requests' })).toBeInTheDocument()
})
```

Replace `dashboard/src/components/layout/BottomBar.test.tsx` with:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi } from 'vitest'
import BottomBar from './BottomBar'

test('admins get four links and More; More opens the menu', async () => {
  const onMore = vi.fn()
  render(
    <MemoryRouter initialEntries={['/org/7/positions']}>
      <BottomBar orgId={7} role="admin" onMore={onMore} />
    </MemoryRouter>
  )
  const nav = screen.getByRole('navigation', { name: 'Quick navigation' })
  expect(nav.className).toContain('glass')
  // Below drawers (z-40) and dialogs (z-50), so an open drawer covers it.
  expect(nav.className).toContain('z-30')
  expect(nav.className).not.toContain('z-40')
  const links = screen.getAllByRole('link')
  expect(links.map((l) => l.textContent)).toEqual(['Overview', 'Positions', 'Trade', 'Accounts'])
  expect(screen.getByRole('link', { name: 'Positions' })).toHaveAttribute('aria-current', 'page')
  await userEvent.click(screen.getByRole('button', { name: 'More' }))
  expect(onMore).toHaveBeenCalledTimes(1)
})

test('viewers get History instead of Trade; investors get Dashboard, Deposit, Withdraw, Transactions and More too', async () => {
  const { unmount } = render(
    <MemoryRouter initialEntries={['/org/7']}>
      <BottomBar orgId={7} role="viewer" onMore={() => {}} />
    </MemoryRouter>
  )
  expect(screen.getAllByRole('link').map((l) => l.textContent)).toEqual(['Overview', 'Positions', 'History', 'Accounts'])
  unmount()
  const onMore = vi.fn()
  render(
    <MemoryRouter initialEntries={['/org/7/invest']}>
      <BottomBar orgId={7} role="investor" onMore={onMore} />
    </MemoryRouter>
  )
  expect(screen.getAllByRole('link').map((l) => l.textContent)).toEqual(['Dashboard', 'Deposit', 'Withdraw', 'Transactions'])
  expect(screen.getByRole('link', { name: 'Dashboard' })).toHaveAttribute('aria-current', 'page')
  await userEvent.click(screen.getByRole('button', { name: 'More' }))
  expect(onMore).toHaveBeenCalledTimes(1)
})
```

Run: `npx vitest run src/components/layout/NavRail.test.tsx src/components/layout/BottomBar.test.tsx`
Expected: FAIL — NavRail: `Unable to find an accessible element with the role "link" and name "Requests, 3 open"`; BottomBar: `Unable to find an accessible element with the role "button" and name "More"` in the investor test.

- [ ] **Step 4: Update `NavRail.tsx` and `BottomBar.tsx`**

Replace `dashboard/src/components/layout/NavRail.tsx` with:

```tsx
import { NavLink } from 'react-router-dom'
import Badge from '../Badge'
import type { NavGroup } from './nav'

/**
 * Grouped navigation for the rail and the phone drawer. NavLink sets
 * aria-current="page" on the active link; `end` keeps the org root from
 * matching every child route. An item with a badge (open requests) shows
 * it as a neutral pill after the label and folds the count into the
 * link's accessible name ("Requests, 3 open").
 */
export default function NavRail({ groups, onNavigate, dense = true }: {
  groups: NavGroup[]
  onNavigate?: () => void
  dense?: boolean
}) {
  return (
    <nav aria-label="Main" className="flex-1 overflow-y-auto px-3 py-2">
      {groups.map((g, gi) => (
        <div key={g.name || gi} className={gi > 0 ? 'mt-4' : ''}>
          {g.name && <div className="px-3 pb-1 text-xs font-semibold text-ink-faint">{g.name}</div>}
          <ul className="space-y-0.5">
            {g.items.map((item) => {
              const badge = item.badge && item.badge > 0 ? item.badge : null
              return (
                <li key={item.path}>
                  <NavLink
                    to={item.path}
                    end={item.end}
                    onClick={onNavigate}
                    aria-label={badge ? `${item.label}, ${badge} open` : undefined}
                    className={({ isActive }) =>
                      `flex items-center justify-between gap-2 rounded-control px-3 text-sm transition-colors duration-150 min-h-11 md:min-h-0 ${
                        dense ? 'py-2' : 'py-3'
                      } ${isActive ? 'bg-brand-wash text-brand-deep font-semibold' : 'text-ink-soft hover:bg-brand-wash/60 hover:text-ink'}`
                    }
                  >
                    <span>{item.label}</span>
                    {badge && <Badge tone="neutral">{badge}</Badge>}
                  </NavLink>
                </li>
              )
            })}
          </ul>
        </div>
      ))}
    </nav>
  )
}
```

Replace `dashboard/src/components/layout/BottomBar.tsx` with:

```tsx
import { NavLink } from 'react-router-dom'
import type { Role } from '../../lib/roles'
import { bottomBarItems } from './nav'

/**
 * The phone tab bar: glass, fixed to the bottom edge in thumb reach, hidden
 * from lg up where the rail takes over. Every role gets four links plus
 * More, which opens the full menu drawer with every group (the investor
 * portal has nine pages; four fit a thumb).
 */
export default function BottomBar({ orgId, role, onMore }: {
  orgId: number
  role: Role
  onMore: () => void
}) {
  const items = bottomBarItems(orgId, role)
  const cell = 'flex flex-1 flex-col items-center justify-center gap-0.5 min-h-14 text-xs font-semibold transition-colors duration-150'
  return (
    <nav
      aria-label="Quick navigation"
      className="glass fixed inset-x-0 bottom-0 z-30 flex border-t pb-[env(safe-area-inset-bottom)] lg:hidden"
    >
      {items.map((item) => (
        <NavLink
          key={item.path}
          to={item.path}
          end={item.end}
          className={({ isActive }) => `${cell} ${isActive ? 'text-brand' : 'text-ink-soft'}`}
        >
          {item.label}
        </NavLink>
      ))}
      <button type="button" onClick={onMore} className={`${cell} text-ink-soft`}>
        More
      </button>
    </nav>
  )
}
```

Run: `npx vitest run src/components/layout/NavRail.test.tsx src/components/layout/BottomBar.test.tsx`
Expected: PASS — Test Files 2 passed; Tests 6 passed (6).

- [ ] **Step 5: Let `useLiveRefresh` skip the socket for a null org**

The events socket refuses investors outright (`ws.py` closes with 4403), so the badge hook must open no socket for them; a hook cannot be called conditionally, so the hook itself learns to stand down. Append to `dashboard/src/hooks/useLiveRefresh.test.tsx`:

```tsx
test('a null org opens no socket and never refetches', () => {
  const refetch = vi.fn()
  render(<Probe onRefetch={refetch} orgId={null} />)
  expect(apiModule.eventsSocket).not.toHaveBeenCalled()
  act(() => { vi.advanceTimersByTime(5000) })
  expect(refetch).not.toHaveBeenCalled()
})
```

and change the `Probe` signature at the top of that file from

```tsx
function Probe({ onRefetch, orgId = 1 }: { onRefetch: () => void; orgId?: number }) {
```

to

```tsx
function Probe({ onRefetch, orgId = 1 }: { onRefetch: () => void; orgId?: number | null }) {
```

Run: `npx vitest run src/hooks/useLiveRefresh.test.tsx`
Expected: FAIL — the new test: `expected "spy" to not be called at all, but actually been called 1 times`.

In `dashboard/src/hooks/useLiveRefresh.ts` replace

```ts
export function useLiveRefresh(
  refetch: () => void,
  orgId: number,
  onEvent?: (evt: LiveEvent) => void,
) {
  const refetchRef = useRef(refetch)
  refetchRef.current = refetch
  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent

  useEffect(() => {
    let ws: WebSocket | null = null
```

with

```ts
export function useLiveRefresh(
  refetch: () => void,
  /** null opens no socket at all: the caller has no org to watch, or is an
   *  investor, whom the events feed refuses (it is the whole org's feed). */
  orgId: number | null,
  onEvent?: (evt: LiveEvent) => void,
) {
  const refetchRef = useRef(refetch)
  refetchRef.current = refetch
  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent

  useEffect(() => {
    const id = orgId
    if (id === null) return
    let ws: WebSocket | null = null
```

and, inside `connect`, replace `ws = eventsSocket(orgId)` with `ws = eventsSocket(id)`. Nothing else changes; every existing caller passes a number.

Run: `npx vitest run src/hooks/useLiveRefresh.test.tsx`
Expected: PASS — Tests 4 passed (4).

- [ ] **Step 6: Write the failing `useRequestsBadge` tests**

Create `dashboard/src/hooks/useRequestsBadge.test.tsx`:

```tsx
import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { REQUESTS_POLL_MS, useRequestsBadge } from './useRequestsBadge'
import * as apiModule from '../lib/api'
import type { Role } from '../lib/roles'

class MockWebSocket {
  static instance: MockWebSocket | null = null
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  constructor() { MockWebSocket.instance = this }
  close() { /* no-op */ }
  emit(json: object) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(json) }))
  }
}

function Probe({ role, orgId = 1 }: { role: Role; orgId?: number }) {
  const badge = useRequestsBadge(orgId, role)
  return <span data-testid="badge">{badge === undefined ? 'none' : String(badge)}</span>
}

let total = 3
let summaryCalls: string[] = []

beforeEach(() => {
  total = 3
  summaryCalls = []
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.spyOn(apiModule, 'eventsSocket').mockImplementation(() => new MockWebSocket() as unknown as WebSocket)
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/requests/summary')) {
      summaryCalls.push(url)
      return new Response(JSON.stringify({ deposits: total, withdrawals: 0, transfers: 0, payout_destinations: 0, total }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  MockWebSocket.instance = null
})

test('an admin reads the total from requests/summary and polls it every 30 s', async () => {
  render(<Probe role="admin" />)
  expect(await screen.findByText('3')).toBeInTheDocument()
  expect(summaryCalls).toEqual(['/api/orgs/1/requests/summary'])
  expect(REQUESTS_POLL_MS).toBe(30000)

  total = 5
  await act(async () => { await vi.advanceTimersByTimeAsync(REQUESTS_POLL_MS) })
  await waitFor(() => expect(summaryCalls).toHaveLength(2))
  expect(await screen.findByText('5')).toBeInTheDocument()
})

test('a control event refetches the total within the live-refresh burst', async () => {
  render(<Probe role="admin" />)
  await screen.findByText('3')
  total = 4
  act(() => { MockWebSocket.instance!.emit({ category: 'control', payload: { action: 'investor_deposit_noticed' } }) })
  await act(async () => { await vi.advanceTimersByTimeAsync(300) })
  await waitFor(() => expect(summaryCalls).toHaveLength(2))
  expect(await screen.findByText('4')).toBeInTheDocument()
})

test('viewers and investors get undefined, no request and no socket', async () => {
  const { unmount } = render(<Probe role="viewer" />)
  expect(screen.getByTestId('badge')).toHaveTextContent('none')
  await act(async () => { await vi.advanceTimersByTimeAsync(REQUESTS_POLL_MS) })
  expect(summaryCalls).toEqual([])
  expect(apiModule.eventsSocket).not.toHaveBeenCalled()
  unmount()
  render(<Probe role="investor" />)
  expect(screen.getByTestId('badge')).toHaveTextContent('none')
  await act(async () => { await vi.advanceTimersByTimeAsync(REQUESTS_POLL_MS) })
  expect(summaryCalls).toEqual([])
  expect(apiModule.eventsSocket).not.toHaveBeenCalled()
})
```

Run: `npx vitest run src/hooks/useRequestsBadge.test.tsx`
Expected: FAIL — `Failed to resolve import "./useRequestsBadge"`.

- [ ] **Step 7: Implement `hooks/useRequestsBadge.ts`**

Create `dashboard/src/hooks/useRequestsBadge.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from 'react'
import { orgApi } from '../lib/api'
import { can, type Role } from '../lib/roles'
import type { RequestsSummary } from '../lib/types'
import { useLiveRefresh } from './useLiveRefresh'

export const REQUESTS_POLL_MS = 30000

/**
 * The open-request total behind the admin rail's Requests pill: polled
 * every 30 s and refetched on `control` events, which every request
 * mutation writes. Non-admins get undefined and no traffic at all -- the
 * endpoint is admin-only and the events socket refuses investors.
 */
export function useRequestsBadge(orgId: number, role: Role): number | undefined {
  const admin = can(role, 'control')
  const [total, setTotal] = useState<number | undefined>(undefined)
  // Layout stays mounted across an org switch; an answer for the previous
  // org must not land on the new one's pill.
  const currentOrg = useRef(orgId)
  currentOrg.current = orgId

  const refetch = useCallback(() => {
    if (!admin) return
    const forOrg = orgId
    orgApi<RequestsSummary>(orgId, 'requests/summary').then(
      (s) => {
        if (currentOrg.current !== forOrg) return
        setTotal(typeof s?.total === 'number' ? s.total : undefined)
      },
      () => {
        // The pill is a hint, not a record: a failed poll leaves it as it was.
      },
    )
  }, [orgId, admin])

  useEffect(() => {
    setTotal(undefined)
    if (!admin) return
    refetch()
    const timer = window.setInterval(refetch, REQUESTS_POLL_MS)
    return () => window.clearInterval(timer)
  }, [refetch, admin])

  useLiveRefresh(refetch, admin ? orgId : null)

  return admin ? total : undefined
}
```

Run: `npx vitest run src/hooks/useRequestsBadge.test.tsx`
Expected: PASS — Tests 3 passed (3).

- [ ] **Step 8: Write the failing Layout tests**

In `dashboard/src/components/Layout.test.tsx` make these edits.

(a) In `mockRoutes`, add the requests branch as the first `if` inside the fetch mock — replace

```ts
    if (url.includes('category=reminder')) return respond(overrides['reminderEvents'] ?? [])
```

with

```ts
    if (url.includes('/requests/summary')) {
      return respond(overrides['requests']
        ?? { deposits: 0, withdrawals: 0, transfers: 0, payout_destinations: 0, total: 0 })
    }
    if (url.includes('category=reminder')) return respond(overrides['reminderEvents'] ?? [])
```

(b) Layout now opens a second socket for admins (the Requests badge), after the strip's. The two tests that push a frame must send it to every socket the render opened, not just the last one. In the test `'the desk strip shows each open contract with its live price'` replace

```ts
  renderLayout()

  expect(await screen.findByText('BTCUSD')).toBeInTheDocument()
  expect(screen.getByText('77717.95')).toBeInTheDocument()

  // A quotes frame moves the strip price live, no refetch involved.
  const ws = fakeSockets[fakeSockets.length - 1]
  act(() => {
    ws.onmessage?.({
      data: JSON.stringify({
        category: 'quotes',
        payload: {
          quotes: {},
          accounts: {
            '1': {
              equity: 9999.9, open_pnl: 0.1,
              positions: [{ position_id: 42, symbol: 'BTCUSD',
                            current_price: 77801.5, pnl_quote: 0.1 }],
            },
          },
        },
      }),
    })
  })
```

with

```ts
  const socketsBefore = fakeSockets.length
  renderLayout()

  expect(await screen.findByText('BTCUSD')).toBeInTheDocument()
  expect(screen.getByText('77717.95')).toBeInTheDocument()

  // A quotes frame moves the strip price live, no refetch involved. The
  // frame goes to every socket this render opened (the strip's and the
  // Requests badge's); only the strip acts on quotes.
  const frame = JSON.stringify({
    category: 'quotes',
    payload: {
      quotes: {},
      accounts: {
        '1': {
          equity: 9999.9, open_pnl: 0.1,
          positions: [{ position_id: 42, symbol: 'BTCUSD',
                        current_price: 77801.5, pnl_quote: 0.1 }],
        },
      },
    },
  })
  act(() => {
    for (const ws of fakeSockets.slice(socketsBefore)) ws.onmessage?.({ data: frame })
  })
```

and in the test `"switching org never shows, or lets a late answer restore, the previous org's copying state"` replace

```ts
  const { rerender } = render(tree())
  expect(await screen.findByRole('button', { name: 'Stop copying' })).toBeInTheDocument()

  // A live event starts a second org-1 refresh whose settings answer is held.
  const ws = fakeSockets[fakeSockets.length - 1]
  act(() => { ws.onmessage?.({ data: JSON.stringify({ category: 'control' }) }) })
  await waitFor(() => expect(org1SettingsCalls).toBe(2))
```

with

```ts
  const socketsBefore = fakeSockets.length
  const { rerender } = render(tree())
  expect(await screen.findByRole('button', { name: 'Stop copying' })).toBeInTheDocument()

  // A live event starts a second org-1 refresh whose settings answer is held
  // (sent to every socket this render opened: the strip's and the badge's).
  act(() => {
    for (const ws of fakeSockets.slice(socketsBefore)) {
      ws.onmessage?.({ data: JSON.stringify({ category: 'control' }) })
    }
  })
  await waitFor(() => expect(org1SettingsCalls).toBe(2))
```

(c) Replace the test `'an investor sees the portal nav and no desk strip'` with:

```tsx
test('an investor sees the grouped portal nav, no desk strip and no requests poll', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('investor'))
  const fetchMock = mockRoutes()
  renderShell('/org/1/invest')
  expect(await screen.findByText('investor home')).toBeInTheDocument()
  for (const label of ['Dashboard', 'Wallet', 'Deposit', 'Withdraw', 'Transfer', 'Transactions',
                       'Payout accounts', 'Account', 'History']) {
    expect(screen.getAllByRole('link', { name: label }).length).toBeGreaterThan(0)
  }
  expect(screen.getByText('Money')).toBeInTheDocument()
  expect(screen.getByText('Trading')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Overview' })).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Accounts' })).not.toBeInTheDocument()
  expect(screen.queryByText(/Close all positions/)).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/state'))).toBe(false)
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/requests/summary'))).toBe(false)
  // The phone tab bar has More for investors too.
  expect(screen.getByRole('button', { name: 'More' })).toBeInTheDocument()
})
```

(d) Append these three tests after `'an investor opening the admin Investors path is sent to the portal'`:

```tsx
test('admins get a Requests nav link that carries the open count from requests/summary', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  const fetchMock = mockRoutes({
    requests: { deposits: 2, withdrawals: 1, transfers: 0, payout_destinations: 0, total: 3 },
  })
  renderShell('/org/1')
  const links = await screen.findAllByRole('link', { name: 'Requests, 3 open' })
  expect(links[0]).toHaveAttribute('href', '/org/1/requests')
  expect(fetchMock.mock.calls.some(([u]) => String(u) === '/api/orgs/1/requests/summary')).toBe(true)
})

test('viewers get no Requests link and the summary is never asked for', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('viewer'))
  const fetchMock = mockRoutes()
  renderShell('/org/1')
  expect(await screen.findByText('desk home')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /^Requests/ })).toBeNull()
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/requests/summary'))).toBe(false)
})

test('a control event refreshes the Requests count without waiting for the poll', async () => {
  useOrgMock.mockReturnValue(makeOrgValue('admin'))
  let total = 1
  const fetchMock = mockRoutes()
  const base = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    if (String(input).includes('/requests/summary')) {
      return new Response(JSON.stringify({ deposits: total, withdrawals: 0, transfers: 0, payout_destinations: 0, total }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    }
    return base(input)
  })
  const socketsBefore = fakeSockets.length
  renderShell('/org/1')
  await screen.findAllByRole('link', { name: 'Requests, 1 open' })

  total = 4
  act(() => {
    for (const ws of fakeSockets.slice(socketsBefore)) {
      ws.onmessage?.({ data: JSON.stringify({ category: 'control', payload: { action: 'investor_deposit_noticed' } }) })
    }
  })
  expect((await screen.findAllByRole('link', { name: 'Requests, 4 open' }))[0]).toHaveAttribute('href', '/org/1/requests')
})
```

Run: `npx vitest run src/components/Layout.test.tsx`
Expected: FAIL — the three new admin tests fail (`Unable to find an accessible element with the role "link" and name "Requests, 3 open"`, and likewise 'Requests, 1 open'); the rewritten investor test and every pre-existing test pass (nav.ts and BottomBar are already in place from Steps 2 and 4, so Wallet/Money/Trading and More already render and no `/requests/summary` call happens until Step 9 wires the hook; the socket edits are behaviour-neutral until Layout opens the second socket).

- [ ] **Step 9: Wire the badge into `Layout.tsx`**

In `dashboard/src/components/Layout.tsx` replace

```ts
import { useTheme } from '../hooks/useTheme'
```

with

```ts
import { useTheme } from '../hooks/useTheme'
import { useRequestsBadge } from '../hooks/useRequestsBadge'
```

and replace

```ts
  const investor = role === 'investor'
  const groups = investor ? investorNav(orgId) : adminNav(orgId, role)
```

with

```ts
  const investor = role === 'investor'
  // Open requests behind the Requests link; undefined for everyone below admin.
  const requestsBadge = useRequestsBadge(orgId, role)
  const groups = investor ? investorNav(orgId) : adminNav(orgId, role, requestsBadge)
```

Also update the shell's doc comment: replace

```ts
 * change (a CSS enter animation, .page-enter). Investors are held inside their portal.
```

with

```ts
 * change (a CSS enter animation, .page-enter). Investors are held inside
 * their portal; admins see the open-request count on the Requests link.
```

Run: `npx vitest run src/components/Layout.test.tsx`
Expected: PASS — Tests 56 passed (56) (the 53 existing plus three new).

- [ ] **Step 10: Full dashboard gate**

Run from `dashboard/`: `npm test`
Expected: palette prover passes, `tsc` prints nothing, vitest ends with every file passed (the `nav.test.ts`, `NavRail`, `BottomBar`, `useLiveRefresh`, `useRequestsBadge` and `Layout` files included). If vitest is slow on this machine: `node scripts/palette_check.mjs && npx tsc --noEmit -p tsconfig.app.json && npx vitest run --maxWorkers=2 --minWorkers=1`.

- [ ] **Step 11: Commit**

```bash
git add dashboard/src/components/layout/nav.ts dashboard/src/components/layout/nav.test.ts dashboard/src/components/layout/NavRail.tsx dashboard/src/components/layout/NavRail.test.tsx dashboard/src/components/layout/BottomBar.tsx dashboard/src/components/layout/BottomBar.test.tsx dashboard/src/hooks/useLiveRefresh.ts dashboard/src/hooks/useLiveRefresh.test.tsx dashboard/src/hooks/useRequestsBadge.ts dashboard/src/hooks/useRequestsBadge.test.tsx dashboard/src/components/Layout.tsx dashboard/src/components/Layout.test.tsx
git commit -m "feat(dashboard): grouped investor navigation, Requests link with a live open-count badge, More on the phone bar for every role

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```


### Task 13: Investor Dashboard (replaces InvestorOverview)

**Files:**
- Create: `dashboard/src/pages/investor/InvestorDashboard.tsx`
- Create: `dashboard/src/pages/investor/InvestorDashboard.test.tsx`
- Modify: `dashboard/src/components/charts.tsx` (`PnlBars` gains optional `label`, `bucketLabel`, `countNoun` props; defaults keep today's output)
- Modify: `dashboard/src/pages/groups/investor.ts` (InvestorOverview line becomes InvestorDashboard)
- Modify: `dashboard/src/App.tsx` (pick + route `invest`)
- Delete: `dashboard/src/pages/investor/InvestorOverview.tsx`
- Delete: `dashboard/src/pages/investor/InvestorOverview.test.tsx`
- Test: `dashboard/src/pages/investor/InvestorDashboard.test.tsx`

**Interfaces:**
- Consumes: `InvestorSummary`, `WalletEntriesPage`, `WalletEntry` (`lib/types.ts`); `entryLabel`, `walletLabel`, `ACCOUNT_CURRENCY` (`lib/investor.ts`); `useHiddenBalances`, `setHidden` (`lib/hideBalances.ts`); `Money`, `HideBalancesToggle` (`components/Money.tsx`); `summaryFixture`, `entryFixture` (`src/test/portalFixtures.ts`); `PnlBars` (`components/charts.tsx`); `NextStep`; `orgApi`; `GET investor/summary`, `GET investor/wallet-entries?limit=8`.
- Produces: page `InvestorDashboard` (default export, route `invest`, title `Dashboard`); exported helpers `greeting(hour)`, `flowWindow(flow, days, now?)`, `last7(flow, pick, now?)`; `PnlBars` optional props `label`, `bucketLabel`, `countNoun`.

- [ ] **Step 1: Write the failing test**

Create `dashboard/src/pages/investor/InvestorDashboard.test.tsx`:

```tsx
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorDashboard, { flowWindow, greeting, last7 } from './InvestorDashboard'
import { mockUseOrg } from '../../test/orgMock'
import { entryFixture, summaryFixture } from '../../test/portalFixtures'
import { setHidden } from '../../lib/hideBalances'
import type { InvestorSummary } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))
// NextStep reads LANDING_FACTS; pin it so the real support address cannot
// make the "no contact" case pass or fail by accident.
const facts = vi.hoisted(() => ({ legalName: 'MirrorFleet', address: '', supportEmail: '' }))
vi.mock('../Landing', () => ({ LANDING_FACTS: facts }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const DAY = 24 * 3600 * 1000
const daysAgo = (n: number) => new Date(Date.now() - n * DAY).toISOString().slice(0, 10)
const zero = { balance: 0, on_hold: 0, available: 0 }

const linked: InvestorSummary = {
  ...summaryFixture(),
  org: { id: 1, name: 'Desk' }, currency: 'USD',
  investor: { display_name: 'Sherwyn Joel', first_name: 'Sherwyn', member_since: '2026-09-01T00:00:00Z' },
  wallets: { main: { balance: 5120.5, on_hold: 100, available: 5020.5 }, credit: zero, pamm: zero, social: zero },
  totals: { deposited: 5000, withdrawn: 0, transferred_in: 0, transferred_out: 3000 },
  cash_flow: [
    { date: daysAgo(3), deposits: 5000, withdrawals: 0 },
    { date: daysAgo(40), deposits: 0, withdrawals: 1000 },
  ],
  pending: { deposits: 0, withdrawals: 2, transfers: 0, payout_destinations: 1 },
  deposits_open: true, withdrawal_rules: { min: 0, fee_pct: 0 },
  link_state: 'linked',
  account: { account_id: 1001, nickname: 'Inv', platform: 'mt5', status: 'ok', last_error: null, connected: true },
  equity_source: 'live', equity: 5120.5, net_funded: 3000, profit: 2120.5, account_available: 5120.5,
  open_positions: 1,
}
const unlinked: InvestorSummary = {
  ...linked, link_state: 'unlinked', account: null, equity_source: 'unknown',
  equity: null, profit: null, account_available: null, open_positions: 0,
}
const entry = entryFixture({
  id: 1, wallet: 'main', amount: 5000, kind: 'deposit', ref_table: 'deposits', ref_id: 1,
  note: null, created_at: '2026-09-20T10:00:00Z', currency: 'USD',
})

function mockRoutes(summaries: InvestorSummary | InvestorSummary[], entries: unknown[] = [entry], fail = false) {
  const queue = Array.isArray(summaries) ? [...summaries] : [summaries]
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (fail) return jsonResponse({ detail: 'database unavailable' }, 500)
    if (url.endsWith('/investor/summary')) {
      return jsonResponse(queue.length > 1 ? queue.shift() : queue[0])
    }
    if (url.includes('/investor/wallet-entries')) {
      return jsonResponse({ entries, has_more: false, next_before: null })
    }
    return jsonResponse({})
  }))
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => {
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers()
  setHidden(false)
  facts.supportEmail = ''
})

test.each([[9, 'Good morning'], [15, 'Good afternoon'], [20, 'Good evening']])(
  'hour %i greets with "%s"', (hour, text) => {
    expect(greeting(hour)).toBe(text)
  })

test('flowWindow keeps the last N days oldest first; last7 fills quiet days with zero', () => {
  const now = Date.parse('2026-09-29T12:00:00Z')
  const flow = [
    { date: '2026-09-29', deposits: 10, withdrawals: 0 },
    { date: '2026-09-25', deposits: 0, withdrawals: 4 },
    { date: '2026-08-01', deposits: 100, withdrawals: 0 },
  ]
  expect(flowWindow(flow, 7, now).map((f) => f.date)).toEqual(['2026-09-25', '2026-09-29'])
  expect(flowWindow(flow, 90, now)).toHaveLength(3)
  expect(last7(flow, (f) => f.deposits - f.withdrawals, now)).toEqual([0, 0, -4, 0, 0, 0, 10])
})

test('the page has its heading and title, greets by the hour, and shows Loading until the summary lands', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(2026, 8, 29, 15, 0, 0))
  mockRoutes(linked)
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument()
  // Loading announces through aria-label; its skeleton bars carry no text.
  expect(screen.getByRole('status')).toHaveAccessibleName(/loading/i)
  expect(await screen.findByText('Good afternoon, Sherwyn')).toBeInTheDocument()
  expect(document.title).toBe('Dashboard · MirrorFleet')
})

test('linked investors see the balance, the tiles, the account card, pending lines and recent activity', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  // The big figure and the My wallet tile both show the floored available.
  expect((await screen.findAllByText('5,020.50 USD')).length).toBeGreaterThan(0)
  expect(screen.getByText('100.00 USD')).toBeInTheDocument()
  expect(screen.getByText('Total deposited')).toBeInTheDocument()
  expect(screen.getAllByText('5,000.00 USD').length).toBeGreaterThan(0)
  expect(screen.getByText('Inv')).toBeInTheDocument()
  expect(screen.getByText('connected')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'View account' })).toHaveAttribute('href', '/org/1/invest/account')
  expect(screen.getByText('2 withdrawals in progress')).toBeInTheDocument()
  expect(screen.getByText('1 payout account awaiting approval')).toBeInTheDocument()
  expect(screen.queryByText(/deposits? awaiting confirmation/)).not.toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Withdrawals' })).toHaveAttribute('href', '/org/1/invest/withdraw')
  expect(screen.getByText('Deposit #1')).toBeInTheDocument()
  expect(screen.getByText(/\+5,000\.00/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Deposit' })).toHaveAttribute('href', '/org/1/invest/deposit')
  expect(screen.getByRole('link', { name: 'View all' })).toHaveAttribute('href', '/org/1/invest/transactions')
})

test('Hide balances masks every figure and flips to Show balances', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  await screen.findAllByText('5,020.50 USD')
  await userEvent.click(screen.getByRole('button', { name: 'Hide balances' }))
  expect(screen.queryByText('5,020.50 USD')).not.toBeInTheDocument()
  expect(screen.getAllByText('••••').length).toBeGreaterThan(3)
  expect(screen.getByRole('button', { name: 'Show balances' })).toHaveAttribute('aria-pressed', 'true')
})

test('unlinked investors see the setup notice with the new copy and no account card', async () => {
  mockRoutes(unlinked)
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.getByText(/deposits, withdrawals and wallet transfers work now/i)).toBeInTheDocument()
  expect(screen.queryByText('Inv')).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'View account' })).not.toBeInTheDocument()
  expect(screen.queryByText(/questions\?/i)).not.toBeInTheDocument()
})

test('the setup notice shows the support contact when there is one', async () => {
  facts.supportEmail = 'help@desk.example'
  mockRoutes(unlinked)
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  const link = await screen.findByRole('link', { name: 'help@desk.example' })
  expect(link).toHaveAttribute('href', 'mailto:help@desk.example')
})

test('the cash flow card switches range with the 7D / 30D / 90D tabs', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  const panel = await screen.findByRole('tabpanel')
  expect(screen.getByRole('tab', { name: '30D' })).toHaveAttribute('aria-selected', 'true')
  // 30 days back: the 5,000 deposit is in, the 1,000 withdrawal (40 days) is not.
  expect(within(panel).getByText('0.00 USD')).toBeInTheDocument()
  expect(within(panel).getByRole('img', { name: 'Deposits and withdrawals by day' })).toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: '90D' }))
  expect(await within(panel).findByText('1,000.00 USD')).toBeInTheDocument()
  expect(within(panel).queryByText('0.00 USD')).not.toBeInTheDocument()
})

test('the account card goes away when the account is unlinked later', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockRoutes([linked, unlinked])
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  expect(await screen.findByText('Inv')).toBeInTheDocument()
  await vi.advanceTimersByTimeAsync(10000)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.queryByText('Inv')).not.toBeInTheDocument()
})

test('dismissing a load error shows the empty state, not an endless skeleton', async () => {
  mockRoutes(linked, [], true)
  render(<MemoryRouter><InvestorDashboard /></MemoryRouter>)
  const alert = await screen.findByRole('alert')
  await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('Nothing yet')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run it to verify it fails**

Run (from `dashboard/`): `npx vitest run src/pages/investor/InvestorDashboard.test.tsx`
Expected: FAIL — `Error: Failed to load url ./InvestorDashboard` (the module does not exist yet).

- [ ] **Step 3: Let `PnlBars` name its buckets**

In `dashboard/src/components/charts.tsx` replace the `PnlBars` signature:

```tsx
export function PnlBars({ buckets, height = 180 }: {
  buckets: PnlBucket[]
  height?: number
}) {
```

with

```tsx
export function PnlBars({
  buckets, height = 180, label = 'Weekly profit and loss',
  bucketLabel = (ms) => `Week of ${shortDate(ms)}`, countNoun = 'trade',
}: {
  buckets: PnlBucket[]
  height?: number
  /** The chart's accessible name. */
  label?: string
  /** Tooltip heading for the bucket that starts at `ms`. */
  bucketLabel?: (ms: number) => string
  /** What `trades` counts (singular); an "s" is added above one. */
  countNoun?: string
}) {
```

In the same function replace

```tsx
      <svg width={width} height={height} role="img" aria-label="Weekly profit and loss">
```

with

```tsx
      <svg width={width} height={height} role="img" aria-label={label}>
```

and, inside the tooltip content, replace

```tsx
                        <div className="text-ink-soft">Week of {shortDate(b.week_start)}</div>
```

with

```tsx
                        <div className="text-ink-soft">{bucketLabel(b.week_start)}</div>
```

and

```tsx
                        <div className="text-ink-faint">{b.trades} trade{b.trades === 1 ? '' : 's'}</div>
```

with

```tsx
                        <div className="text-ink-faint">{b.trades} {countNoun}{b.trades === 1 ? '' : 's'}</div>
```

`Performance.tsx` calls `<PnlBars buckets={a.weekly} />` and keeps every default.

- [ ] **Step 4: Write the page**

Create `dashboard/src/pages/investor/InvestorDashboard.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { ACCOUNT_CURRENCY, entryLabel, walletLabel } from '../../lib/investor'
import { useHiddenBalances } from '../../lib/hideBalances'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import Loading from '../../components/Loading'
import Money, { HideBalancesToggle } from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import StatTile from '../../components/StatTile'
import Tabs from '../../components/Tabs'
import { PnlBars } from '../../components/charts'
import NextStep from './NextStep'
import type { InvestorSummary, WalletEntriesPage, WalletEntry } from '../../lib/types'

const POLL_MS = 10000
const DAY_MS = 24 * 3600 * 1000
const RANGES = [
  { key: '7', label: '7D', days: 7 },
  { key: '30', label: '30D', days: 30 },
  { key: '90', label: '90D', days: 90 },
] as const
type RangeKey = typeof RANGES[number]['key']
type Flow = InvestorSummary['cash_flow'][number]

/** "Good morning" before noon, "Good afternoon" before six, then "Good evening". */
export function greeting(hour: number): string {
  return hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
}

/** ISO date (UTC) of the day `daysBack` days before `now`. */
function isoDay(now: number, daysBack: number): string {
  return new Date(now - daysBack * DAY_MS).toISOString().slice(0, 10)
}

/** cash_flow rows inside the last `days` days (today counts), oldest first. */
export function flowWindow(flow: Flow[], days: number, now = Date.now()): Flow[] {
  const from = isoDay(now, days - 1)
  return flow.filter((f) => f.date >= from).sort((a, b) => (a.date < b.date ? -1 : 1))
}

/** One figure per day for the last seven days, zero on a day without movement. */
export function last7(flow: Flow[], pick: (f: Flow) => number, now = Date.now()): number[] {
  const byDate = new Map(flow.map((f) => [f.date, f]))
  const out: number[] = []
  for (let back = 6; back >= 0; back--) {
    const f = byDate.get(isoDay(now, back))
    out.push(f ? pick(f) : 0)
  }
  return out
}

function dayLabel(ms: number): string {
  return new Date(ms).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' })
}

/** A 96x24 line over seven daily figures with a drawn zero baseline; the
 *  tile's number carries the value, the line only shows the shape. */
function Sparkline({ values, label }: { values: number[]; label: string }) {
  const w = 96, h = 24, pad = 2
  const lo = Math.min(0, ...values)
  const hi = Math.max(0, ...values)
  const span = hi - lo || 1
  const yOf = (v: number) => pad + (h - 2 * pad) * (1 - (v - lo) / span)
  const step = values.length > 1 ? w / (values.length - 1) : 0
  const points = values.map((v, i) => `${(i * step).toFixed(1)},${yOf(v).toFixed(1)}`).join(' ')
  return (
    <svg width={w} height={h} role="img" aria-label={label} className="mt-1 block">
      <line x1={0} x2={w} y1={yOf(0)} y2={yOf(0)} stroke="var(--color-line)" strokeWidth={1} />
      <polyline points={points} fill="none" stroke="var(--color-brand)" strokeWidth={1.5}
                strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

export default function InvestorDashboard() {
  const { orgId } = useOrg()
  const base = `/org/${orgId}/invest`
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [entries, setEntries] = useState<WalletEntry[]>([])
  const [range, setRange] = useState<RangeKey>('30')
  const [error, setError] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)
  const [hidden] = useHiddenBalances()

  const refresh = useCallback(async () => {
    try {
      const [s, page] = await Promise.all([
        orgApi<InvestorSummary>(orgId, 'investor/summary'),
        orgApi<WalletEntriesPage>(orgId, 'investor/wallet-entries?limit=8'),
      ])
      setSummary(s)
      setEntries(page.entries)
      setError(null)
    } catch (err) {
      setError(errorText(err, 'Could not load your dashboard'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => {
    refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [refresh])

  const unit = summary?.currency ?? ACCOUNT_CURRENCY
  // StatTile takes a string, so the hide toggle is honoured here by hand;
  // every other figure goes through <Money>.
  const tile = (v: number) => (hidden ? '••••' : money(v, unit))

  const days = RANGES.find((r) => r.key === range)!.days
  const flow = summary ? flowWindow(summary.cash_flow, days) : []
  const buckets = flow.map((f) => ({
    week_start: Date.parse(`${f.date}T00:00:00Z`),
    gross_pnl: f.deposits - f.withdrawals,
    trades: (f.deposits > 0 ? 1 : 0) + (f.withdrawals > 0 ? 1 : 0),
  }))
  const totalIn = flow.reduce((s, f) => s + f.deposits, 0)
  const totalOut = flow.reduce((s, f) => s + f.withdrawals, 0)

  const pendingLines = summary ? [
    { n: summary.pending.deposits, noun: 'deposit', what: 'awaiting confirmation', link: 'Deposits', to: 'deposit' },
    { n: summary.pending.withdrawals, noun: 'withdrawal', what: 'in progress', link: 'Withdrawals', to: 'withdraw' },
    { n: summary.pending.transfers, noun: 'transfer', what: 'in progress', link: 'Transfers', to: 'transfer' },
    { n: summary.pending.payout_destinations, noun: 'payout account', what: 'awaiting approval', link: 'Payout accounts', to: 'payout-accounts' },
  ].filter((p) => p.n > 0) : []

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader title="Dashboard" subtitle={summary?.org.name} />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {!loaded && <Loading lines={4} />}

      {summary && (
        <>
          <Card>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0 flex-1 space-y-3">
                <p className="text-lg font-semibold text-ink">
                  {`${greeting(new Date().getHours())}, ${summary.investor.first_name}`}
                </p>
                <div className="inset p-4 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="desk-label">Wallet balance</span>
                    <HideBalancesToggle />
                  </div>
                  <div className="text-3xl font-semibold text-ink">
                    <Money value={summary.wallets.main.available} unit={unit} />
                  </div>
                  {summary.wallets.main.on_hold > 0 && (
                    <p className="text-xs text-ink-soft">
                      <Money value={summary.wallets.main.on_hold} unit={unit} /> on hold for open requests
                    </p>
                  )}
                  {summary.profit != null && (
                    <Badge tone={summary.profit < 0 ? 'loss' : 'profit'}>
                      <Money value={summary.profit} unit={unit} signed />{' '}lifetime P&L
                    </Badge>
                  )}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button to={`${base}/deposit`}>Deposit</Button>
                <Button variant="secondary" to={`${base}/withdraw`}>Withdraw</Button>
                <Button variant="secondary" to={`${base}/transfer`}>Transfer</Button>
              </div>
            </div>
          </Card>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatTile label="My wallet" value={tile(summary.wallets.main.available)} tone="brand"
                      sub={<Sparkline values={last7(summary.cash_flow, (f) => f.deposits - f.withdrawals)}
                                      label="Net flow, last 7 days" />} />
            <StatTile label="Total deposited" value={tile(summary.totals.deposited)}
                      sub={<Sparkline values={last7(summary.cash_flow, (f) => f.deposits)}
                                      label="Deposits, last 7 days" />} />
            <StatTile label="Total withdrawn" value={tile(summary.totals.withdrawn)}
                      sub={<Sparkline values={last7(summary.cash_flow, (f) => f.withdrawals)}
                                      label="Withdrawals, last 7 days" />} />
            {/* cash_flow carries no transfer series, so this tile says the split in words. */}
            <StatTile label="Total transferred"
                      value={tile(summary.totals.transferred_in + summary.totals.transferred_out)}
                      sub={hidden ? '••••'
                        : `${money(summary.totals.transferred_out, unit)} to trading · ${money(summary.totals.transferred_in, unit)} back`} />
          </div>

          {summary.link_state === 'linked' && summary.account ? (
            <Card title="Trading account"
                  actions={<Button variant="ghost" size="sm" to={`${base}/account`}>View account</Button>}>
              <dl className="inset p-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
                <div><dt className="desk-label">Account</dt>
                  <dd className="text-ink">{summary.account.nickname ?? summary.account.account_id}</dd></div>
                <div><dt className="desk-label">Platform</dt>
                  <dd className="text-ink uppercase">{summary.account.platform}</dd></div>
                <div><dt className="desk-label">Connection</dt>
                  <dd className={summary.account.connected ? 'text-profit' : 'text-warn-deep'}>
                    {summary.account.connected ? 'connected' : 'terminal offline'}
                  </dd></div>
                <div><dt className="desk-label">Open positions</dt>
                  <dd className="num text-ink">{summary.open_positions}</dd></div>
                <div><dt className="desk-label">Equity</dt>
                  <dd className="text-ink"><Money value={summary.equity} unit={unit} />
                    <span className="text-xs text-ink-soft"> {summary.equity_source}</span></dd></div>
                <div><dt className="desk-label">Net funded</dt>
                  <dd className="text-ink"><Money value={summary.net_funded} unit={unit} /></dd></div>
                <div><dt className="desk-label">Profit</dt>
                  <dd className={summary.profit != null && summary.profit < 0 ? 'text-loss' : 'text-profit'}>
                    <Money value={summary.profit} unit={unit} /></dd></div>
                <div><dt className="desk-label">Available to move</dt>
                  <dd className="text-ink"><Money value={summary.account_available} unit={unit} /></dd></div>
              </dl>
            </Card>
          ) : (
            <NextStep title="Your account is being set up">
              Your admin links your trading account. Deposits, withdrawals and wallet transfers work now;
              moving money into the trading account opens once it is linked.
            </NextStep>
          )}

          <Card title="Cash flow"
                actions={<Tabs items={RANGES.map((r) => ({ key: r.key, label: r.label }))} value={range}
                               onChange={(k) => setRange(k as RangeKey)} label="Cash flow range" idBase="cashflow" />}>
            <div id="cashflow-panel" role="tabpanel" aria-labelledby={`cashflow-tab-${range}`} className="space-y-3">
              <div className="inset p-3">
                {buckets.length === 0 ? (
                  <p className="py-8 text-center text-xs text-ink-faint">No deposits or withdrawals in this range</p>
                ) : (
                  // Deposits rise above the baseline, withdrawals fall below it; a
                  // day with both shows its net, the strip below gives the split.
                  <PnlBars buckets={buckets} height={160} label="Deposits and withdrawals by day"
                           bucketLabel={dayLabel} countNoun="movement" />
                )}
              </div>
              <dl className="inset p-3 grid grid-cols-3 gap-3 text-sm">
                <div><dt className="desk-label">Deposits</dt>
                  <dd className="text-ink"><Money value={totalIn} unit={unit} /></dd></div>
                <div><dt className="desk-label">Withdrawals</dt>
                  <dd className="text-ink"><Money value={totalOut} unit={unit} /></dd></div>
                <div><dt className="desk-label">Net</dt>
                  <dd className={totalIn - totalOut < 0 ? 'text-loss' : 'text-profit'}>
                    <Money value={totalIn - totalOut} unit={unit} signed /></dd></div>
              </dl>
            </div>
          </Card>

          {pendingLines.length > 0 && (
            <Card title="Pending requests">
              <ul className="inset divide-y divide-line text-sm">
                {pendingLines.map((p) => (
                  <li key={p.to} className="px-4 py-2.5 flex items-center justify-between gap-3">
                    <span className="text-ink">{`${p.n} ${p.noun}${p.n === 1 ? '' : 's'} ${p.what}`}</span>
                    <Button variant="ghost" size="sm" to={`${base}/${p.to}`}>{p.link}</Button>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </>
      )}

      <Card title="Recent activity" inset
            actions={<Button variant="ghost" size="sm" to={`${base}/transactions`}>View all</Button>}>
        <ul className="divide-y divide-line">
          {entries.length === 0 && <li className="text-center py-8 text-ink-faint">Nothing yet</li>}
          {entries.map((e) => (
            <li key={e.id} className="px-4 py-2.5 text-sm flex flex-wrap items-center gap-x-4 gap-y-1">
              {/* Fluid, not a fixed width: on a 360px phone the date takes its
                  own line and the rest wraps beneath it. */}
              <time dateTime={e.created_at} className="num text-ink-soft min-w-0 basis-full sm:basis-auto">
                {formatWhen(e.created_at)}
              </time>
              <span className="text-ink flex-1 min-w-0">{entryLabel(e)}</span>
              <span className="text-xs text-ink-soft">{walletLabel(e.wallet)}</span>
              <Money value={e.amount} unit={e.currency} signed
                     className={e.amount < 0 ? 'text-loss' : 'text-profit'} />
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npx vitest run src/pages/investor/InvestorDashboard.test.tsx`
Expected: PASS, 12 passed.

- [ ] **Step 6: Route it and retire InvestorOverview**

In `dashboard/src/pages/groups/investor.ts` replace

```ts
export { default as InvestorOverview } from '../investor/InvestorOverview'
```

with

```ts
export { default as InvestorDashboard } from '../investor/InvestorDashboard'
```

In `dashboard/src/App.tsx` replace

```tsx
const InvestorOverview = pick(investor, 'InvestorOverview')
```

with

```tsx
const InvestorDashboard = pick(investor, 'InvestorDashboard')
```

and

```tsx
              <Route path="invest" element={<InvestorOverview />} />
```

with

```tsx
              <Route path="invest" element={<InvestorDashboard />} />
```

Delete the old page and its test:

```bash
git rm dashboard/src/pages/investor/InvestorOverview.tsx dashboard/src/pages/investor/InvestorOverview.test.tsx
```

- [ ] **Step 7: Full dashboard gate**

Run (from `dashboard/`): `npm test`
Expected: prover ALL PASS, `tsc` clean (nothing imports InvestorOverview any more), every vitest file green (run vitest as `npx vitest run --maxWorkers=2 --minWorkers=1` if the plain run is slow on this machine). The nav and Layout tests already say "Dashboard" since Task 12.

- [ ] **Step 8: Commit**

```bash
git add dashboard/src/pages/investor/InvestorDashboard.tsx dashboard/src/pages/investor/InvestorDashboard.test.tsx dashboard/src/components/charts.tsx dashboard/src/pages/groups/investor.ts dashboard/src/App.tsx
git commit -m "feat(dashboard): investor Dashboard -- greeting, hide toggle, wallet tiles with sparklines, cash flow card, pending requests, recent ledger activity; InvestorOverview retired

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 14: Investor Deposit rewrite

**Files:**
- Modify: `dashboard/src/pages/investor/InvestorDeposit.tsx` (full rewrite: Crypto/Bank tabs, method cards, details pane, notice form with chips, receipt upload, deposit-to radio, history with cancel)
- Modify: `dashboard/src/pages/investor/InvestorDeposit.test.tsx` (full rewrite)
- Test: `dashboard/src/pages/investor/InvestorDeposit.test.tsx`

**Interfaces:**
- Consumes: `InvestorSummary`, `PaymentMethod`, `PortalDeposit`, `UploadedFile` (`lib/types.ts`); `ACCOUNT_CURRENCY`, `BADGE_TONE`, `statusLabel`, `statusTone` (`lib/investor.ts`); `Money`; `FileInput`, `RECEIPT_ACCEPT`, `MAX_UPLOAD_BYTES` (`components/FileInput.tsx`); `orgApi`, `orgUpload` (`lib/api.ts`); `Tabs`, `ConfirmDialog`, `NextStep`; fixtures `summaryFixture`, `methodFixture`, `depositFixture`; routes `GET investor/summary`, `GET investor/payment-methods`, `GET investor/deposits`, `POST investor/files` (multipart `purpose`, `file`), `POST investor/deposits` `{method_id, amount, reference, receipt_file_id, target, target_account_id, note}`, `POST investor/deposits/{id}/cancel`.
- Produces: page `InvestorDeposit` (title `Deposit`, Tabs `idBase="deposit-kind"`, chips `50 100 250 500 Min`, submit `File deposit notice`, NextStep `Deposits are not open yet`, cancel button `aria-label="Cancel deposit {id}"`).
- Deviation from spec 11.3: the notices table's Receipt column is an `Open` link to `GET investor/files/{id}` (new tab), not a thumbnail. PDF receipts have no thumbnail and the file route needs the session cookie, so one link serves both kinds; the tests assert the link's `href`.

- [ ] **Step 1: Write the failing test**

Replace the whole of `dashboard/src/pages/investor/InvestorDeposit.test.tsx` with:

```tsx
import { readFileSync } from 'node:fs'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorDeposit from './InvestorDeposit'
import { mockUseOrg } from '../../test/orgMock'
import { depositFixture, methodFixture, summaryFixture } from '../../test/portalFixtures'
import type { InvestorSummary, PaymentMethod, PortalDeposit } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))
// The page loads the encoder with `await import('qrcode')` and takes the
// named export; keep `default` too so either shape resolves.
vi.mock('qrcode', () => {
  const toDataURL = vi.fn(async () => 'data:image/png;base64,QR')
  return { toDataURL, default: { toDataURL } }
})
const facts = vi.hoisted(() => ({ legalName: 'MirrorFleet', address: '', supportEmail: '' }))
vi.mock('../Landing', () => ({ LANDING_FACTS: facts }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const summary: InvestorSummary = {
  ...summaryFixture(), currency: 'USD', deposits_open: true, link_state: 'linked',
  account: { account_id: 1001, nickname: 'Inv', platform: 'mt5', status: 'ok', last_error: null, connected: true },
}
const crypto: PaymentMethod = methodFixture({
  id: 5, kind: 'crypto', label: 'USDT on TRC20', enabled: true, currency: 'USD',
  details: { coin: 'USDT', network: 'TRC20', address: 'TAddr123' },
  min_amount: 0, fee_pct: 0, instructions: null, sort_order: 0,
})
const bank: PaymentMethod = methodFixture({
  id: 6, kind: 'bank', label: 'ICICI Bank', enabled: true, currency: 'USD',
  details: { bank_name: 'ICICI Bank', holder: 'MirrorFleet Ltd', account_number: '000112344543', code: 'ICIC0001' },
  min_amount: 500, fee_pct: 1.5, instructions: 'Quote the reference in the transfer remarks.', sort_order: 1,
})
const notice: PortalDeposit = depositFixture({
  id: 1, user_id: 1, method_id: 5, method_kind: 'crypto', method_label: 'USDT on TRC20',
  amount: 250, fee: 0, credited_amount: null, reference: 'abc', receipt_file_id: null,
  target: 'wallet', target_account_id: null, note: null, status: 'pending',
  decided_by: null, decided_at: null, decision_note: null, created_at: '2026-09-23T10:00:00Z', currency: 'USD',
})

function mockRoutes(opts: { open?: boolean; linked?: boolean; rows?: PortalDeposit[]; fail?: boolean } = {}) {
  const deposits: PortalDeposit[] = [...(opts.rows ?? [])]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (opts.fail) return jsonResponse({ detail: 'database unavailable' }, 500)
    if (url.endsWith('/investor/summary')) {
      return jsonResponse({
        ...summary, deposits_open: opts.open ?? true,
        ...(opts.linked === false ? { link_state: 'unlinked', account: null } : {}),
      })
    }
    if (url.endsWith('/investor/payment-methods')) return jsonResponse(opts.open === false ? [] : [crypto, bank])
    if (url.endsWith('/investor/files') && init?.method === 'POST') {
      return jsonResponse({ id: 77, purpose: 'deposit_receipt', content_type: 'image/png', size_bytes: 3,
                            created_at: '2026-09-23T10:00:00Z' }, 201)
    }
    if (url.endsWith('/investor/deposits') && init?.method === 'POST') {
      deposits.unshift(notice)
      return jsonResponse(notice, 201)
    }
    if (url.endsWith('/investor/deposits')) return jsonResponse(deposits)
    if (/\/investor\/deposits\/\d+\/cancel$/.test(url) && init?.method === 'POST') {
      deposits[0] = { ...deposits[0], status: 'cancelled' }
      return jsonResponse(deposits[0])
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function posts(fetchMock: ReturnType<typeof mockRoutes>) {
  return fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
}

beforeEach(() => {
  useOrgMock.mockReturnValue(mockUseOrg('investor'))
  // FileInput previews images through an object URL; jsdom has none.
  Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:preview'), configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true })
})
afterEach(() => {
  vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers()
  facts.supportEmail = ''
})

test('shows the crypto method with a QR and files a notice with the exact payload', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  expect(await screen.findByText('TAddr123')).toBeInTheDocument()
  expect(screen.getAllByText(/USDT on TRC20/).length).toBeGreaterThan(0)
  expect((await screen.findByRole('img', { name: /QR/ })).getAttribute('src')).toContain('data:image')
  expect(screen.getByRole('heading', { level: 1, name: 'Deposit' })).toBeInTheDocument()
  expect(document.title).toBe('Deposit · MirrorFleet')
  expect(screen.getByRole('tab', { name: 'Crypto' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tab', { name: 'Bank' })).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: '250' }))
  expect(screen.getByLabelText('Amount in USD')).toHaveValue('250')
  await userEvent.type(screen.getByLabelText('Transaction hash'), 'abc')
  await userEvent.click(screen.getByRole('button', { name: 'File deposit notice' }))

  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  const post = posts(fetchMock)[0]
  expect(String(post[0])).toMatch(/\/investor\/deposits$/)
  expect(JSON.parse((post[1] as RequestInit).body as string)).toEqual({
    method_id: 5, amount: '250', reference: 'abc', receipt_file_id: null,
    target: 'wallet', target_account_id: null, note: null,
  })
  await waitFor(() => expect(screen.getByText('Pending review')).toBeInTheDocument())
  expect(screen.getByText('250.00 USD')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Cancel deposit 1' })).toBeInTheDocument()
})

test('the Bank tab lists bank details with a Copy button each, needs a receipt, and uploads it before filing', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')
  await userEvent.click(screen.getByRole('tab', { name: 'Bank' }))
  expect(await screen.findByText('000112344543')).toBeInTheDocument()
  expect(screen.getByText('Quote the reference in the transfer remarks.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Copy Account number' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Copy SWIFT / IFSC code' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Copy address' })).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: 'Min' }))
  expect(screen.getByLabelText('Amount in USD')).toHaveValue('500.00')
  await userEvent.type(screen.getByLabelText('Bank transaction ID'), 'UTR123')
  await userEvent.click(screen.getByRole('button', { name: 'File deposit notice' }))
  expect(await screen.findByText('A receipt is required for bank deposits')).toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)

  const file = new File(['png'], 'receipt.png', { type: 'image/png' })
  await userEvent.upload(screen.getByLabelText(/^Receipt/), file)
  await userEvent.click(screen.getByRole('button', { name: 'File deposit notice' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(2))
  const [upload, filed] = posts(fetchMock)
  expect(String(upload[0])).toMatch(/\/investor\/files$/)
  const form = (upload[1] as RequestInit).body as FormData
  expect(form.get('purpose')).toBe('deposit_receipt')
  expect((form.get('file') as File).name).toBe('receipt.png')
  expect(String(filed[0])).toMatch(/\/investor\/deposits$/)
  expect(JSON.parse((filed[1] as RequestInit).body as string)).toEqual({
    method_id: 6, amount: '500.00', reference: 'UTR123', receipt_file_id: 77,
    target: 'wallet', target_account_id: null, note: null,
  })
})

test('Trading account is offered as the target when an account is linked, and is posted with its id', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')
  await userEvent.click(screen.getByRole('radio', { name: 'Trading account' }))
  await userEvent.click(screen.getByRole('button', { name: '250' }))
  await userEvent.type(screen.getByLabelText('Transaction hash'), 'abc')
  await userEvent.click(screen.getByRole('button', { name: 'File deposit notice' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string)).toEqual({
    method_id: 5, amount: '250', reference: 'abc', receipt_file_id: null,
    target: 'account', target_account_id: 1001, note: null,
  })
})

test('without a linked account only My wallet is offered', async () => {
  mockRoutes({ linked: false })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')
  expect(screen.getByRole('radio', { name: 'My wallet' })).toBeChecked()
  expect(screen.queryByRole('radio', { name: 'Trading account' })).not.toBeInTheDocument()
})

test('Copy address copies, says so, and reverts after two seconds', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')

  // Installed AFTER render: user-event swaps in its own clipboard stub on
  // first use, and this one must be the one the page hits.
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  fireEvent.click(screen.getByRole('button', { name: 'Copy address' }))

  await waitFor(() => expect(writeText).toHaveBeenCalledWith('TAddr123'))
  expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument()
  expect(screen.getByText('Address copied to the clipboard.')).toBeInTheDocument()

  await vi.advanceTimersByTimeAsync(2000)
  expect(await screen.findByRole('button', { name: 'Copy address' })).toBeInTheDocument()
})

test('without a clipboard API, Copy address selects the address instead', async () => {
  mockRoutes()
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await screen.findByText('TAddr123')

  Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
  fireEvent.click(screen.getByRole('button', { name: 'Copy address' }))

  expect(await screen.findByText(/address selected/i)).toBeInTheDocument()
  expect(window.getSelection()?.toString()).toBe('TAddr123')
  expect(screen.getByRole('button', { name: 'Copy address' })).toBeInTheDocument()
})

test('says deposits are not open when no method is enabled, and what happens next', async () => {
  mockRoutes({ open: false })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  expect(await screen.findByText(/Deposits are not open yet/)).toBeInTheDocument()
  expect(screen.getByText(/Your admin has not added a payment method yet/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'File deposit notice' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Copy address' })).not.toBeInTheDocument()
  expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  expect(screen.queryByText(/questions\?/i)).not.toBeInTheDocument()
})

test('the closed state shows the support contact when there is one', async () => {
  facts.supportEmail = 'help@desk.example'
  mockRoutes({ open: false })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  const link = await screen.findByRole('link', { name: 'help@desk.example' })
  expect(link).toHaveAttribute('href', 'mailto:help@desk.example')
})

test('the QR encoder is loaded on demand, not in the main bundle', () => {
  const source = readFileSync('src/pages/investor/InvestorDeposit.tsx', 'utf8')
  expect(source).not.toMatch(/^import .* from 'qrcode'/m)
  expect(source).toContain("await import('qrcode')")
})

test('a pending notice can be cancelled after a confirmation', async () => {
  const fetchMock = mockRoutes({ rows: [notice] })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  await userEvent.click(await screen.findByRole('button', { name: 'Cancel deposit 1' }))
  const dialog = await screen.findByRole('dialog', { name: 'Cancel deposit notice #1?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Yes, cancel it' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(String(posts(fetchMock)[0][0])).toMatch(/\/investor\/deposits\/1\/cancel$/)
  expect(await screen.findByText('Cancelled')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Cancel deposit 1' })).not.toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('dismissing a load error shows the empty state, not an endless skeleton', async () => {
  mockRoutes({ fail: true })
  render(<MemoryRouter><InvestorDeposit /></MemoryRouter>)
  const alert = await screen.findByRole('alert')
  await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('No notices yet')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run src/pages/investor/InvestorDeposit.test.tsx`
Expected: FAIL — the QR source-grep test passes, every other test fails (the Task 11 stub of this page still calls `investor/wallet` and has no tabs, chips or radio; e.g. `Unable to find role="tab" and name "Crypto"`).

- [ ] **Step 3: Write the page**

Replace the whole of `dashboard/src/pages/investor/InvestorDeposit.tsx` with:

```tsx
import { useCallback, useEffect, useRef, useState } from 'react'
import { orgApi, orgUpload } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { ACCOUNT_CURRENCY, BADGE_TONE, statusLabel, statusTone } from '../../lib/investor'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import FileInput, { MAX_UPLOAD_BYTES, RECEIPT_ACCEPT } from '../../components/FileInput'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import Money from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import Tabs from '../../components/Tabs'
import NextStep from './NextStep'
import type { InvestorSummary, PaymentMethod, PortalDeposit, UploadedFile } from '../../lib/types'

const COPIED_MS = 2000
const AMOUNT_RE = /^\d+(\.\d{1,2})?$/
const TWO_DECIMALS = 'Enter an amount with at most two decimals, digits only (for example 250.00).'
const QUICK = [50, 100, 250, 500]
const KIND_LABEL = { crypto: 'Crypto', bank: 'Bank' } as const
type Kind = PaymentMethod['kind']
type Target = PortalDeposit['target']

// Bank details in the order the reference portal shows them; a key the
// admin left blank is skipped.
const BANK_FIELDS: { key: string; label: string }[] = [
  { key: 'bank_name', label: 'Bank name' },
  { key: 'holder', label: 'Account holder' },
  { key: 'account_number', label: 'Account number' },
  { key: 'code', label: 'SWIFT / IFSC code' },
  { key: 'bank_address', label: 'Bank address' },
  { key: 'country', label: 'Country' },
]

const EMPTY_FORM = { amount: '', reference: '', note: '', target: 'wallet' as Target }

type Copied = { key: string; label: string; how: 'copied' | 'selected' }

export default function InvestorDeposit() {
  const { orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [methods, setMethods] = useState<PaymentMethod[]>([])
  const [deposits, setDeposits] = useState<PortalDeposit[]>([])
  const [kind, setKind] = useState<Kind>('crypto')
  const [methodId, setMethodId] = useState<number | null>(null)
  const [qr, setQr] = useState<string | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [receipt, setReceipt] = useState<File | null>(null)
  const [receiptError, setReceiptError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [copied, setCopied] = useState<Copied | null>(null)
  const [cancelling, setCancelling] = useState<PortalDeposit | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)
  const valueRefs = useRef<Record<string, HTMLElement | null>>({})

  const refresh = useCallback(async () => {
    try {
      const [s, m, d] = await Promise.all([
        orgApi<InvestorSummary>(orgId, 'investor/summary'),
        orgApi<PaymentMethod[]>(orgId, 'investor/payment-methods'),
        orgApi<PortalDeposit[]>(orgId, 'investor/deposits'),
      ])
      setSummary(s); setMethods(m); setDeposits(d)
    } catch (err) {
      setError(errorText(err, 'Could not load your deposits'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  const kinds = (['crypto', 'bank'] as Kind[]).filter((k) => methods.some((m) => m.kind === k))
  const activeKind: Kind | null = kinds.includes(kind) ? kind : (kinds[0] ?? null)
  const ofKind = methods.filter((m) => m.kind === activeKind)
  const selected = ofKind.find((m) => m.id === methodId) ?? ofKind[0] ?? null
  const address = selected?.kind === 'crypto' ? selected.details.address : null
  const linked = summary?.link_state === 'linked' && summary.account != null
  const unit = selected?.currency ?? summary?.currency ?? ACCOUNT_CURRENCY
  const refLabel = selected?.kind === 'bank' ? 'Bank transaction ID' : 'Transaction hash'

  useEffect(() => {
    let alive = true
    if (!address) { setQr(null); return }
    void (async () => {
      try {
        // Loaded on demand so the QR encoder stays out of the main bundle.
        const { toDataURL } = await import('qrcode')
        const url = await toDataURL(address, { width: 192, margin: 1 })
        if (alive) setQr(url)
      } catch {
        // The address and the Copy button still work without the picture.
        if (alive) setQr(null)
      }
    })()
    return () => { alive = false }
  }, [address])

  useEffect(() => {
    if (copied?.how !== 'copied') return
    const id = window.setTimeout(() => setCopied(null), COPIED_MS)
    return () => window.clearTimeout(id)
  }, [copied])

  // Fallback when there is no clipboard API (plain http, some in-app
  // browsers) or the browser refuses it: select the value so the
  // investor's own copy command takes it.
  const selectValue = (key: string, label: string) => {
    const node = valueRefs.current[key]
    const selection = window.getSelection()
    if (!node || !selection) return
    const range = document.createRange()
    range.selectNodeContents(node)
    selection.removeAllRanges()
    selection.addRange(range)
    setCopied({ key, label, how: 'selected' })
  }

  const copyValue = async (key: string, label: string, text: string) => {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text)
        setCopied({ key, label, how: 'copied' })
        return
      } catch {
        // Permission refused: fall through to selecting the text.
      }
    }
    selectValue(key, label)
  }

  const chooseKind = (k: string) => { setKind(k as Kind); setMethodId(null); setCopied(null) }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selected) return
    setError(null); setNotice(null); setReceiptError(null)
    // Digits with at most two decimals, so the row shows exactly the number
    // that was posted ("1000.005" would read as 1,000.01).
    if (!AMOUNT_RE.test(form.amount.trim())) { setError(TWO_DECIMALS); return }
    if (!(Number(form.amount) > 0)) { setError('Enter an amount above zero'); return }
    if (!form.reference.trim()) { setError(`${refLabel} is required`); return }
    if (selected.kind === 'bank' && !receipt) { setReceiptError('A receipt is required for bank deposits'); return }
    setBusy(true)
    try {
      let receipt_file_id: number | null = null
      if (receipt) {
        const fd = new FormData()
        fd.append('purpose', 'deposit_receipt')
        fd.append('file', receipt)
        receipt_file_id = (await orgUpload<UploadedFile>(orgId, 'investor/files', fd)).id
      }
      await orgApi<PortalDeposit>(orgId, 'investor/deposits', {
        method: 'POST',
        body: JSON.stringify({
          method_id: selected.id,
          amount: form.amount.trim(),
          reference: form.reference.trim(),
          receipt_file_id,
          target: form.target,
          target_account_id: form.target === 'account' ? (summary?.account?.account_id ?? null) : null,
          note: form.note.trim() || null,
        }),
      })
      setForm(EMPTY_FORM)
      setReceipt(null)
      setNotice('Notice filed. An admin will confirm it once the transfer is seen.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not file the notice'))
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (!cancelling) return
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi<PortalDeposit>(orgId, `investor/deposits/${cancelling.id}/cancel`, { method: 'POST' })
      setNotice('Notice cancelled.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not cancel the notice'))
    } finally {
      setBusy(false)
      setCancelling(null)
    }
  }

  const copyStatus = !copied ? ''
    : copied.how === 'copied' ? `${copied.label} copied to the clipboard.`
    : `${copied.label} selected. Copy it with Ctrl+C, or long-press on a phone.`

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Deposit"
        subtitle="Send the money to one of the workspace's payment methods, then file a notice so an admin can confirm it."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {!loaded && <Loading lines={4} />}

      {summary && !summary.deposits_open && (
        <NextStep title="Deposits are not open yet">
          Your admin has not added a payment method yet. Deposits open as soon as one is enabled; you can save a payout account meanwhile.
        </NextStep>
      )}

      {activeKind && selected && (
        <>
          <Card title="Choose a payment method">
            <Tabs items={kinds.map((k) => ({ key: k, label: KIND_LABEL[k] }))} value={activeKind}
                  onChange={chooseKind} label="Payment method kind" idBase="deposit-kind" />
            <div id="deposit-kind-panel" role="tabpanel" aria-labelledby={`deposit-kind-tab-${activeKind}`}
                 className="mt-4 grid gap-4 md:grid-cols-[240px_1fr]">
              <ul className="space-y-2" aria-label="Payment methods">
                {ofKind.map((m) => {
                  const on = m.id === selected.id
                  return (
                    <li key={m.id}>
                      <Button variant={on ? 'primary' : 'secondary'} block aria-pressed={on}
                              className="justify-start text-left"
                              onClick={() => { setMethodId(m.id); setCopied(null) }}>
                        <span className="flex flex-col items-start">
                          <span>{m.label}</span>
                          <span className="text-xs font-normal">
                            {m.min_amount > 0 ? `Min ${money(m.min_amount, m.currency)}` : 'No minimum'}
                            {' · '}
                            {m.fee_pct > 0 ? `Fee ${m.fee_pct}%` : 'No fee'}
                          </span>
                        </span>
                      </Button>
                    </li>
                  )
                })}
              </ul>

              <div className="inset p-4 space-y-3 min-w-0">
                {selected.kind === 'crypto' ? (
                  <div className="grid gap-5 sm:grid-cols-[192px_1fr]">
                    {qr && (
                      <img src={qr} alt="QR code of the deposit address" width={192} height={192}
                           className="rounded-inset border border-line bg-card" />
                    )}
                    <div className="space-y-3 min-w-0">
                      <div>
                        <div className="desk-label">Send only</div>
                        <div className="text-lg font-semibold text-ink">
                          {selected.details.coin} on {selected.details.network}
                        </div>
                      </div>
                      <div>
                        <div className="desk-label">Address</div>
                        <div className="flex flex-wrap items-start gap-3">
                          <div ref={(n) => { valueRefs.current.address = n }}
                               className="num text-sm text-ink break-all min-w-0 flex-1">
                            {selected.details.address}
                          </div>
                          <Button variant="secondary" size="sm"
                                  onClick={() => copyValue('address', 'Address', selected.details.address)}>
                            {copied?.key === 'address' && copied.how === 'copied' ? 'Copied' : 'Copy address'}
                          </Button>
                        </div>
                      </div>
                      {selected.details.memo && (
                        <div>
                          <div className="desk-label">Memo / tag</div>
                          <div className="num text-sm text-ink">{selected.details.memo}</div>
                        </div>
                      )}
                      <Banner kind="warn" announce={false}>
                        Sending any other coin or network to this address will lose the funds.
                      </Banner>
                    </div>
                  </div>
                ) : (
                  <dl className="grid gap-3 sm:grid-cols-2">
                    {BANK_FIELDS.filter((f) => selected.details[f.key]).map((f) => (
                      <div key={f.key} className="min-w-0">
                        <dt className="desk-label">{f.label}</dt>
                        <dd className="flex flex-wrap items-start gap-2">
                          <span ref={(n) => { valueRefs.current[f.key] = n }}
                                className="num text-sm text-ink break-all min-w-0 flex-1">
                            {selected.details[f.key]}
                          </span>
                          <Button variant="secondary" size="sm" aria-label={`Copy ${f.label}`}
                                  onClick={() => copyValue(f.key, f.label, selected.details[f.key])}>
                            {copied?.key === f.key && copied.how === 'copied' ? 'Copied' : 'Copy'}
                          </Button>
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
                {selected.instructions && <p className="text-sm text-ink-soft">{selected.instructions}</p>}
                {/* Always mounted so screen readers hear the change. */}
                <p role="status" className="text-xs text-ink-soft min-h-4">{copyStatus}</p>
              </div>
            </div>
          </Card>

          <Card title="File a deposit notice">
            <form onSubmit={submit} noValidate className="space-y-4">
              <div className="flex gap-3 flex-wrap items-end">
                <label className="block w-40">
                  <span className="desk-label block mb-1">Amount ({unit})</span>
                  <Input aria-label={`Amount in ${unit}`} num value={form.amount}
                         onChange={(e) => setForm({ ...form, amount: e.target.value })} />
                </label>
                <div className="flex flex-wrap gap-1" aria-label="Quick amounts">
                  {QUICK.map((n) => (
                    <Button key={n} variant="ghost" size="sm" onClick={() => setForm({ ...form, amount: String(n) })}>
                      {n}
                    </Button>
                  ))}
                  <Button variant="ghost" size="sm"
                          onClick={() => setForm({ ...form, amount: selected.min_amount.toFixed(2) })}>
                    Min
                  </Button>
                </div>
              </div>
              <label className="block">
                <span className="desk-label block mb-1">{refLabel}</span>
                <Input aria-label={refLabel} num value={form.reference}
                       onChange={(e) => setForm({ ...form, reference: e.target.value })} />
              </label>
              <FileInput id="receipt" label={selected.kind === 'bank' ? 'Receipt' : 'Receipt (optional)'}
                         accept={RECEIPT_ACCEPT} maxBytes={MAX_UPLOAD_BYTES} value={receipt}
                         onChange={(f) => { setReceipt(f); setReceiptError(null) }}
                         required={selected.kind === 'bank'} hint="JPEG, PNG, WebP or PDF, up to 5 MB"
                         error={receiptError} disabled={busy} />
              <fieldset>
                <legend className="desk-label mb-1">Deposit to</legend>
                <div className="flex flex-wrap gap-4 text-sm">
                  <label className="flex items-center gap-2 text-ink">
                    <input type="radio" name="target" value="wallet" className="accent-brand"
                           checked={form.target === 'wallet'} onChange={() => setForm({ ...form, target: 'wallet' })} />
                    My wallet
                  </label>
                  {linked && (
                    <label className="flex items-center gap-2 text-ink">
                      <input type="radio" name="target" value="account" className="accent-brand"
                             checked={form.target === 'account'} onChange={() => setForm({ ...form, target: 'account' })} />
                      Trading account
                    </label>
                  )}
                </div>
              </fieldset>
              <label className="block">
                <span className="desk-label block mb-1">Note (optional)</span>
                <Input aria-label="Note" value={form.note}
                       onChange={(e) => setForm({ ...form, note: e.target.value })} />
              </label>
              <Button type="submit" disabled={busy}>File deposit notice</Button>
            </form>
          </Card>
        </>
      )}

      <ConfirmDialog
        open={cancelling != null}
        title={`Cancel deposit notice #${cancelling?.id ?? ''}?`}
        confirmLabel="Yes, cancel it"
        danger
        busy={busy}
        onConfirm={cancel}
        onCancel={() => setCancelling(null)}
      >
        <p>The notice is withdrawn and the admin will not confirm it. Money you already sent stays where it is; file a new notice if it arrives.</p>
      </ConfirmDialog>

      <Card title="Your deposit notices" inset>
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-4 py-2 font-semibold">Filed</th>
                <th className="desk-label px-4 py-2 font-semibold">Method</th>
                <th className="desk-label px-4 py-2 font-semibold text-right">Amount</th>
                <th className="desk-label px-4 py-2 font-semibold text-right">Credited</th>
                <th className="desk-label px-4 py-2 font-semibold">Reference</th>
                <th className="desk-label px-4 py-2 font-semibold">Receipt</th>
                <th className="desk-label px-4 py-2 font-semibold">Status</th>
                <th className="px-4 py-2"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {deposits.length === 0 && (
                <tr><td colSpan={8} className="text-center py-8 text-ink-faint">No notices yet</td></tr>
              )}
              {deposits.map((d) => (
                <tr key={d.id} className="border-b border-line last:border-0">
                  <td data-label="Filed" className="num px-4 py-2.5">{formatWhen(d.created_at)}</td>
                  <td data-label="Method" className="px-4 py-2.5 text-ink">{d.method_label}</td>
                  <td data-label="Amount" className="px-4 py-2.5 text-right">
                    <Money value={d.amount} unit={d.currency} />
                  </td>
                  <td data-label="Credited" className="px-4 py-2.5 text-right">
                    <Money value={d.credited_amount} unit={d.currency} />
                  </td>
                  <td data-label="Reference" className="num px-4 py-2.5 break-all">{d.reference}</td>
                  <td data-label="Receipt" className="px-4 py-2.5">
                    {d.receipt_file_id != null ? (
                      <a href={`/api/orgs/${orgId}/investor/files/${d.receipt_file_id}`} target="_blank" rel="noopener"
                         className="text-brand underline underline-offset-2 hover:text-brand-deep">
                        Open
                      </a>
                    ) : '—'}
                  </td>
                  <td data-label="Status" className="px-4 py-2.5">
                    <Badge tone={BADGE_TONE[statusTone(d.status)]}>{statusLabel(d.status)}</Badge>
                    {d.decision_note && <div className="text-xs text-ink-soft mt-1">Admin: {d.decision_note}</div>}
                  </td>
                  <td data-label="Actions" className="px-4 py-2.5 text-right">
                    {d.status === 'pending' && (
                      <Button variant="ghost" tone="loss" size="sm" aria-label={`Cancel deposit ${d.id}`}
                              onClick={() => setCancelling(d)} disabled={busy}>
                        Cancel
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/pages/investor/InvestorDeposit.test.tsx`
Expected: PASS, 11 passed.

- [ ] **Step 5: Full dashboard gate**

Run (from `dashboard/`): `npm test`
Expected: prover ALL PASS, `tsc` clean, every vitest file green. (`vocabulary.test.ts` scans this file: no `<h1`, no literal "Loading...".)

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/pages/investor/InvestorDeposit.tsx dashboard/src/pages/investor/InvestorDeposit.test.tsx
git commit -m "feat(dashboard): Deposit page -- crypto and bank payment methods, copyable details, receipt upload, deposit-to target, quick amounts, cancel while pending

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 15: Investor Withdraw rewrite and Payout accounts

**Files:**
- Modify: `dashboard/src/pages/investor/InvestorWithdraw.tsx` (full rewrite: destination Select, Use max on the floored available, fee/net preview, `PinConfirmDialog`, Timeline, cancel)
- Modify: `dashboard/src/pages/investor/InvestorWithdraw.test.tsx` (full rewrite)
- Create: `dashboard/src/pages/investor/InvestorPayoutAccounts.tsx`
- Create: `dashboard/src/pages/investor/InvestorPayoutAccounts.test.tsx`
- Modify: `dashboard/src/pages/groups/investor.ts` (add InvestorPayoutAccounts)
- Modify: `dashboard/src/App.tsx` (pick + route `invest/payout-accounts`)
- Test: `dashboard/src/pages/investor/InvestorWithdraw.test.tsx`, `dashboard/src/pages/investor/InvestorPayoutAccounts.test.tsx`

**Interfaces:**
- Consumes: `InvestorSummary`, `PayoutDestination`, `PortalWithdrawal`, `UploadedFile`; `ACCOUNT_CURRENCY`, `BADGE_TONE`, `statusLabel`, `statusTone`; `Money`; `PinConfirmDialog` (`{open, title, children, confirmLabel, busy?, onConfirm(mpin), onCancel}`; PinInput `id="confirm-mpin" label="Your MPIN"`, boxes labelled `Your MPIN digit N of 6`); `FileInput`, `RECEIPT_ACCEPT`, `MAX_UPLOAD_BYTES`; `Drawer`, `ConfirmDialog`, `Select`; `orgApi` (step-up POSTs with `{ redirectOn401: false }`), `orgUpload`; fixtures `summaryFixture`, `destinationFixture`, `withdrawalFixture`; routes `GET investor/payout-destinations`, `POST investor/payout-destinations` `{kind, nickname, details, proof_file_id, mpin}`, `POST investor/payout-destinations/{id}/remove`, `GET investor/withdrawals`, `POST investor/withdrawals` `{destination_id, amount, mpin}`, `POST investor/withdrawals/{id}/cancel`, `POST investor/files` (purpose `payout_proof`).
- Produces: pages `InvestorWithdraw` (title `Withdraw`, submit `Request withdrawal`, dialog title `Send {money(amount, 'USD')} to {destination_summary}?`, `STEPS = ['requested','approved','paid']`, cancel `aria-label="Cancel withdrawal {id}"`) and `InvestorPayoutAccounts` (title `Payout accounts`, route `invest/payout-accounts`, Drawer titles `Add bank account` / `Add crypto address`, save `Save payout account`, remove `aria-label="Remove {nickname}"`); exported `feePreview(amount, feePct)`.

- [ ] **Step 1: Write the failing Withdraw test**

Replace the whole of `dashboard/src/pages/investor/InvestorWithdraw.test.tsx` with:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorWithdraw, { feePreview } from './InvestorWithdraw'
import { mockUseOrg } from '../../test/orgMock'
import { destinationFixture, summaryFixture, withdrawalFixture } from '../../test/portalFixtures'
import type { InvestorSummary, PayoutDestination, PortalWithdrawal } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const summary: InvestorSummary = {
  ...summaryFixture(), currency: 'USD',
  wallets: {
    main: { balance: 5120.5, on_hold: 100, available: 5020.5 },
    credit: { balance: 0, on_hold: 0, available: 0 },
    pamm: { balance: 0, on_hold: 0, available: 0 },
    social: { balance: 0, on_hold: 0, available: 0 },
  },
  withdrawal_rules: { min: 50, fee_pct: 1.5 },
}
const salary: PayoutDestination = destinationFixture({
  id: 12, user_id: 1, kind: 'bank', nickname: 'Salary', summary: 'ICICI ••4543', status: 'approved',
  details: { bank_name: 'ICICI Bank', holder: 'S Joel', account_number: '000112344543', code: 'ICIC0001' },
  proof_file_id: null, decided_by: 2, decided_at: '2026-09-22T10:00:00Z', decision_note: null,
  created_at: '2026-09-21T10:00:00Z',
})
const pendingOne: PayoutDestination = destinationFixture({
  ...salary, id: 13, nickname: 'Pending one', summary: 'HDFC ••0001', status: 'pending',
  decided_by: null, decided_at: null,
})
const request: PortalWithdrawal = withdrawalFixture({
  id: 3, user_id: 1, destination_id: 12, destination_kind: 'bank', destination_summary: 'ICICI ••4543',
  amount: 1000, fee: 15, net_amount: 985, status: 'approved', decided_by: 2,
  decided_at: '2026-09-23T11:00:00Z', decision_note: null, paid_by: null, paid_at: null, txid: null,
  created_at: '2026-09-23T10:00:00Z', currency: 'USD',
})

function mockRoutes(opts: {
  refuse?: { status: number; body: unknown }; destinations?: PayoutDestination[]
  rows?: PortalWithdrawal[]; fail?: boolean; available?: number
} = {}) {
  const rows: PortalWithdrawal[] = [...(opts.rows ?? [])]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (opts.fail) return jsonResponse({ detail: 'database unavailable' }, 500)
    if (url.endsWith('/investor/summary')) {
      return jsonResponse(opts.available === undefined ? summary
        : { ...summary, wallets: { ...summary.wallets, main: { ...summary.wallets.main, available: opts.available } } })
    }
    if (url.endsWith('/investor/payout-destinations')) return jsonResponse(opts.destinations ?? [salary, pendingOne])
    if (url.endsWith('/investor/withdrawals') && init?.method === 'POST') {
      if (opts.refuse) return jsonResponse(opts.refuse.body, opts.refuse.status)
      rows.unshift(request)
      return jsonResponse(request, 201)
    }
    if (url.endsWith('/investor/withdrawals')) return jsonResponse(rows)
    if (/\/investor\/withdrawals\/\d+\/cancel$/.test(url) && init?.method === 'POST') {
      rows[0] = { ...rows[0], status: 'cancelled' }
      return jsonResponse(rows[0])
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function posts(fetchMock: ReturnType<typeof mockRoutes>) {
  return fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
}

async function enterPin(dialog: HTMLElement, pin: string) {
  within(dialog).getByLabelText('Your MPIN digit 1 of 6').focus()
  await userEvent.keyboard(pin)
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('feePreview rounds the fee half-up to cents and nets it off', () => {
  expect(feePreview('1000', 1.5)).toEqual({ fee: 15, net: 985 })
  expect(feePreview('333.33', 1.5)).toEqual({ fee: 5, net: 328.33 })
  expect(feePreview('250', 0)).toEqual({ fee: 0, net: 250 })
  expect(feePreview('1,000', 1.5)).toBeNull()
})

test('shows what is available, previews the fee, and files a request only after the MPIN', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  expect(await screen.findByText('5,020.50 USD')).toBeInTheDocument()
  expect(screen.getByText('100.00 USD')).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: 'Withdraw' })).toBeInTheDocument()
  expect(document.title).toBe('Withdraw · MirrorFleet')
  // Only approved payout accounts are offered.
  expect(screen.getByRole('option', { name: 'Salary · ICICI ••4543' })).toBeInTheDocument()
  expect(screen.queryByRole('option', { name: /Pending one/ })).not.toBeInTheDocument()

  await userEvent.type(screen.getByLabelText('Amount in USD'), '1000')
  expect(screen.getByText(/Fee 15\.00 USD \(1\.5%\) · You receive 985\.00 USD/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))

  const dialog = await screen.findByRole('dialog', { name: 'Send 1,000.00 USD to ICICI ••4543?' })
  expect(within(dialog).getByText('985.00 USD')).toBeInTheDocument()
  expect(within(dialog).getByRole('button', { name: 'Send request' })).toBeDisabled()
  expect(posts(fetchMock)).toHaveLength(0)

  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  const post = posts(fetchMock)[0]
  expect(String(post[0])).toMatch(/\/investor\/withdrawals$/)
  expect(JSON.parse((post[1] as RequestInit).body as string))
    .toEqual({ destination_id: 12, amount: '1000', mpin: '123456' })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.getByText('Approved, payment pending')).toBeInTheDocument()
  expect(screen.getByText('1,000.00 USD')).toBeInTheDocument()
})

test('Cancel on the review sends nothing and keeps the form', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USD'), '1000')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const dialog = await screen.findByRole('dialog', { name: 'Send 1,000.00 USD to ICICI ••4543?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
  expect(screen.getByLabelText('Amount in USD')).toHaveValue('1000')
})

test('Use max fills the floored available amount, and that is what is posted', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await screen.findByLabelText('Amount in USD')
  await userEvent.click(screen.getByRole('button', { name: 'Use max' }))
  expect(screen.getByLabelText('Amount in USD')).toHaveValue('5020.50')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const dialog = await screen.findByRole('dialog', { name: 'Send 5,020.50 USD to ICICI ••4543?' })
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string))
    .toEqual({ destination_id: 12, amount: '5020.50', mpin: '123456' })
})

test('an amount that is not above zero never reaches the review', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USD'), '0')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  expect(await screen.findByText('Enter an amount above zero')).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
})

test('a wrong MPIN stays in the dialog with the tries left, and never bounces to /login', async () => {
  const fetchMock = mockRoutes({ refuse: { status: 401, body: { detail: 'Invalid MPIN', attempts_left: 2 } } })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USD'), '1000')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const dialog = await screen.findByRole('dialog')
  await enterPin(dialog, '111111')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))
  expect(await screen.findByText('Wrong MPIN, 2 tries left')).toBeInTheDocument()
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(1)
  expect(screen.queryByText('Unauthorized')).not.toBeInTheDocument()
})

test("the server's money refusal is shown as written, inside the dialog", async () => {
  mockRoutes({ refuse: { status: 400, body: { detail: 'amount exceeds what is available (1,250.00)' } } })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USD'), '9999')
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  const dialog = await screen.findByRole('dialog')
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Send request' }))
  expect(await within(dialog).findByText(/exceeds what is available \(1,250\.00\)/)).toBeInTheDocument()
})

test('the timeline names the current step in words and with aria-current', async () => {
  mockRoutes({ rows: [request] })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  const progress = await screen.findByRole('list', { name: 'Withdrawal progress' })
  const steps = within(progress).getAllByRole('listitem')
  expect(steps).toHaveLength(3)
  expect(steps[0]).not.toHaveAttribute('aria-current')
  expect(steps[1]).toHaveAttribute('aria-current', 'step')
  expect(steps[1]).toHaveTextContent('Approved, payment pending')
  expect(steps[1]).toHaveTextContent('current')
  expect(steps[2]).not.toHaveAttribute('aria-current')
  expect(steps[2]).not.toHaveTextContent('current')
  expect(screen.getByText(/Fee 15\.00 USD · Net 985\.00 USD/)).toBeInTheDocument()
})

test('with no approved payout account the form is replaced by a link to add one', async () => {
  mockRoutes({ destinations: [pendingOne] })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  const link = await screen.findByRole('link', { name: 'Add a payout account' })
  expect(link).toHaveAttribute('href', '/org/1/invest/payout-accounts')
  expect(screen.queryByRole('button', { name: 'Request withdrawal' })).not.toBeInTheDocument()
})

const TWO_DECIMALS = 'Enter an amount with at most two decimals, digits only (for example 250.00).'

test.each(['1000.005', '1,000'])('%s is refused before the review, and nothing is posted', async (amount) => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USD'), amount)
  await userEvent.click(screen.getByRole('button', { name: 'Request withdrawal' }))
  expect(await screen.findByText(TWO_DECIMALS)).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
})

test('Use max is disabled when nothing is available', async () => {
  mockRoutes({ available: 0 })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await screen.findByLabelText('Amount in USD')
  expect(screen.getByRole('button', { name: 'Use max' })).toBeDisabled()
})

test('a requested withdrawal can be cancelled after a confirmation', async () => {
  const fetchMock = mockRoutes({ rows: [{ ...request, status: 'requested', decided_by: null, decided_at: null }] })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  await userEvent.click(await screen.findByRole('button', { name: 'Cancel withdrawal 3' }))
  const dialog = await screen.findByRole('dialog', { name: 'Cancel withdrawal #3?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Yes, cancel it' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(String(posts(fetchMock)[0][0])).toMatch(/\/investor\/withdrawals\/3\/cancel$/)
  expect(await screen.findByText('Cancelled')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Cancel withdrawal 3' })).not.toBeInTheDocument()
})

test('dismissing a load error shows the empty state, not an endless skeleton', async () => {
  mockRoutes({ fail: true })
  render(<MemoryRouter><InvestorWithdraw /></MemoryRouter>)
  const alert = await screen.findByRole('alert')
  await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('No requests yet')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run src/pages/investor/InvestorWithdraw.test.tsx`
Expected: FAIL — `TypeError: feePreview is not a function` in the feePreview test (under vitest a missing named export is `undefined`, not a SyntaxError); every page test fails on `Unable to find a label with the text of: Amount in USD` (the Task 11 stub keeps the old form).

- [ ] **Step 3: Write the Withdraw page**

Replace the whole of `dashboard/src/pages/investor/InvestorWithdraw.tsx` with:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { ACCOUNT_CURRENCY, BADGE_TONE, statusLabel, statusTone } from '../../lib/investor'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import Money from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import PinConfirmDialog from '../../components/PinConfirmDialog'
import Select from '../../components/Select'
import type { InvestorSummary, PayoutDestination, PortalWithdrawal } from '../../lib/types'

const STEPS = ['requested', 'approved', 'paid'] as const
type Step = typeof STEPS[number]
// The withdrawal timeline's own words: "approved" here means the admin has
// agreed and the payment is still to be made (a transfer's "approved" reads
// differently, see InvestorTransfer).
const STEP_LABELS: Record<Step, string> = {
  requested: 'Awaiting approval', approved: 'Approved, payment pending', paid: 'Paid',
}
const AMOUNT_RE = /^\d+(\.\d{1,2})?$/
const TWO_DECIMALS = 'Enter an amount with at most two decimals, digits only (for example 250.00).'

/** Fee and net for the typed amount, rounded half-up to cents like the
 *  server's fee_for; null while the amount is not a valid figure. The
 *  server's figures rule; this only previews them. */
export function feePreview(amount: string, feePct: number): { fee: number; net: number } | null {
  if (!AMOUNT_RE.test(amount.trim())) return null
  const cents = Math.round(Number(amount) * 100)
  const fee = Math.round((cents * feePct) / 100)
  return { fee: fee / 100, net: (cents - fee) / 100 }
}

function Timeline({ w }: { w: PortalWithdrawal }) {
  const reached = STEPS.indexOf(w.status as Step)
  if (reached === -1) {
    return <Badge tone={BADGE_TONE[statusTone(w.status)]}>{statusLabel(w.status)}</Badge>
  }
  return (
    <ol aria-label="Withdrawal progress" className="flex flex-wrap items-center gap-2 text-xs">
      {STEPS.map((step, i) => {
        const current = i === reached
        return (
          <li key={step} aria-current={current ? 'step' : undefined} className={current ? 'font-semibold' : ''}>
            <Badge tone={i <= reached ? BADGE_TONE[statusTone(step)] : 'neutral'}>
              {STEP_LABELS[step]}
              {/* Colour is never the only signal: the current step says so.
                  Its own element, so the Badge's direct text stays the bare
                  label and getByText('Approved, payment pending') matches. */}
              {current && <span> · current</span>}
            </Badge>
          </li>
        )
      })}
    </ol>
  )
}

export default function InvestorWithdraw() {
  const { orgId } = useOrg()
  const base = `/org/${orgId}/invest`
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [destinations, setDestinations] = useState<PayoutDestination[]>([])
  const [rows, setRows] = useState<PortalWithdrawal[]>([])
  const [form, setForm] = useState({ amount: '', destination_id: '' })
  const [reviewing, setReviewing] = useState(false)
  const [cancelling, setCancelling] = useState<PortalWithdrawal | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [s, d, list] = await Promise.all([
        orgApi<InvestorSummary>(orgId, 'investor/summary'),
        orgApi<PayoutDestination[]>(orgId, 'investor/payout-destinations'),
        orgApi<PortalWithdrawal[]>(orgId, 'investor/withdrawals'),
      ])
      setSummary(s); setDestinations(d); setRows(list)
    } catch (err) {
      setError(errorText(err, 'Could not load your withdrawals'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  const approved = destinations.filter((d) => d.status === 'approved')
  const unit = summary?.currency ?? ACCOUNT_CURRENCY
  // Already floored to cents by the server, so "Use max" is never refused.
  const available = summary?.wallets.main.available ?? 0
  const onHold = summary?.wallets.main.on_hold ?? 0
  const rules = summary?.withdrawal_rules ?? { min: 0, fee_pct: 0 }
  const destination = approved.find((d) => String(d.id) === form.destination_id) ?? approved[0] ?? null
  const preview = feePreview(form.amount, rules.fee_pct)

  const review = (e: React.FormEvent) => {
    e.preventDefault()
    setError(null); setNotice(null)
    // Digits with at most two decimals, so the review shows exactly the
    // number that will be posted ("1000.005" would read as 1,000.01).
    if (!AMOUNT_RE.test(form.amount.trim())) { setError(TWO_DECIMALS); return }
    if (!(Number(form.amount) > 0)) { setError('Enter an amount above zero'); return }
    if (!destination) { setError('Add a payout account first'); return }
    setReviewing(true)
  }

  // Rejections propagate: PinConfirmDialog shows 401/423/409 and any other
  // refusal inline and clears the PIN; the dialog closes only on success.
  const send = async (mpin: string) => {
    if (!destination) return
    setBusy(true)
    try {
      await orgApi<PortalWithdrawal>(orgId, 'investor/withdrawals', {
        method: 'POST',
        body: JSON.stringify({ destination_id: destination.id, amount: form.amount.trim(), mpin }),
      }, { redirectOn401: false })
      setReviewing(false)
      setForm({ amount: '', destination_id: form.destination_id })
      setNotice('Request sent. You will be emailed when an admin decides.')
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (!cancelling) return
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi<PortalWithdrawal>(orgId, `investor/withdrawals/${cancelling.id}/cancel`, { method: 'POST' })
      setNotice('Request cancelled.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not cancel the request'))
    } finally {
      setBusy(false)
      setCancelling(null)
    }
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Withdraw"
        subtitle="Ask for an amount from your wallet and where to send it. An admin approves, pays to your payout account, and records the transaction."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {!loaded && <Loading lines={3} />}

      {summary && (
        <Card title="Available to withdraw"
              actions={<span className="text-2xl font-semibold text-ink"><Money value={available} unit={unit} /></span>}>
          <div className="space-y-4">
            {onHold > 0 && (
              <p className="text-xs text-ink-soft">
                <Money value={onHold} unit={unit} /> is on hold for open requests.
              </p>
            )}
            {approved.length === 0 ? (
              <Banner kind="notice" announce={false}>
                <span>Add a payout account first; an admin approves it before it can be used.</span>{' '}
                <Button variant="ghost" size="sm" to={`${base}/payout-accounts`}>Add a payout account</Button>
              </Banner>
            ) : (
              <form onSubmit={review} noValidate className="space-y-4">
                <label className="block">
                  <span className="desk-label block mb-1">Payout account</span>
                  <Select aria-label="Payout account" block value={destination ? String(destination.id) : ''}
                          onChange={(e) => setForm({ ...form, destination_id: e.target.value })}>
                    {approved.map((d) => (
                      <option key={d.id} value={d.id}>{`${d.nickname} · ${d.summary}`}</option>
                    ))}
                  </Select>
                </label>
                <div className="flex gap-3 flex-wrap items-end">
                  <label className="block w-40">
                    <span className="desk-label block mb-1">Amount ({unit})</span>
                    <Input aria-label={`Amount in ${unit}`} num value={form.amount}
                           onChange={(e) => setForm({ ...form, amount: e.target.value })} />
                  </label>
                  <Button variant="ghost" size="sm" disabled={available <= 0}
                          onClick={() => setForm({ ...form, amount: available.toFixed(2) })}>
                    Use max
                  </Button>
                </div>
                <p className="text-xs text-ink-soft">
                  {rules.min > 0 && <>Minimum {money(rules.min, unit)} · </>}
                  {preview
                    ? <>Fee {money(preview.fee, unit)} ({rules.fee_pct}%) · You receive {money(preview.net, unit)}</>
                    : <>Fee {rules.fee_pct}% of the amount</>}
                </p>
                <Button type="submit" disabled={busy}>Request withdrawal</Button>
              </form>
            )}
          </div>
        </Card>
      )}

      <PinConfirmDialog
        open={reviewing}
        title={`Send ${money(form.amount, unit)} to ${destination?.summary ?? ''}?`}
        confirmLabel="Send request"
        busy={busy}
        onConfirm={send}
        onCancel={() => setReviewing(false)}
      >
        <p>An admin reviews the request, pays it to your payout account and records the transaction.</p>
        <dl className="grid grid-cols-2 gap-2">
          <div><dt className="desk-label">Amount</dt><dd className="num text-ink">{money(form.amount, unit)}</dd></div>
          <div><dt className="desk-label">Fee</dt><dd className="num text-ink">{money(preview?.fee ?? 0, unit)}</dd></div>
          <div><dt className="desk-label">You receive</dt><dd className="num text-ink">{money(preview?.net ?? 0, unit)}</dd></div>
          <div><dt className="desk-label">Payout account</dt>
            <dd className="text-ink">{destination ? `${destination.nickname} · ${destination.summary}` : ''}</dd></div>
        </dl>
      </PinConfirmDialog>

      <ConfirmDialog
        open={cancelling != null}
        title={`Cancel withdrawal #${cancelling?.id ?? ''}?`}
        confirmLabel="Yes, cancel it"
        danger
        busy={busy}
        onConfirm={cancel}
        onCancel={() => setCancelling(null)}
      >
        <p>The amount on hold returns to your available balance and no payment is made.</p>
      </ConfirmDialog>

      <Card title="Your requests" inset>
        <ul className="divide-y divide-line">
          {rows.length === 0 && <li className="text-center py-8 text-ink-faint">No requests yet</li>}
          {rows.map((w) => (
            <li key={w.id} className="px-4 py-3 text-sm space-y-1">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="num text-ink-soft">{formatWhen(w.created_at)}</span>
                <span className="font-semibold text-ink"><Money value={w.amount} unit={w.currency} /></span>
                <span className="num text-ink-soft min-w-0">to {w.destination_summary}</span>
                {w.status === 'requested' && (
                  <Button variant="ghost" tone="loss" size="sm" aria-label={`Cancel withdrawal ${w.id}`}
                          onClick={() => setCancelling(w)} disabled={busy}>
                    Cancel
                  </Button>
                )}
              </div>
              <p className="text-xs text-ink-soft">{`Fee ${money(w.fee, w.currency)} · Net ${money(w.net_amount, w.currency)}`}</p>
              <Timeline w={w} />
              {w.decision_note && <p className="text-xs text-ink-soft">Admin: {w.decision_note}</p>}
              {w.txid && <p className="num text-xs text-ink-soft break-all">Transaction: {w.txid}</p>}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
```

- [ ] **Step 4: Run the Withdraw test to verify it passes**

Run: `npx vitest run src/pages/investor/InvestorWithdraw.test.tsx`
Expected: PASS, 14 passed.

- [ ] **Step 5: Write the failing Payout accounts test**

Create `dashboard/src/pages/investor/InvestorPayoutAccounts.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorPayoutAccounts from './InvestorPayoutAccounts'
import { mockUseOrg } from '../../test/orgMock'
import { destinationFixture } from '../../test/portalFixtures'
import type { PayoutDestination } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const salary: PayoutDestination = destinationFixture({
  id: 12, user_id: 1, kind: 'bank', nickname: 'Salary', summary: 'ICICI ••4543', status: 'approved',
  details: { bank_name: 'ICICI Bank', holder: 'S Joel', account_number: '000112344543', code: 'ICIC0001' },
  proof_file_id: null, decided_by: 2, decided_at: '2026-09-22T10:00:00Z', decision_note: null,
  created_at: '2026-09-21T10:00:00Z',
})
const tron: PayoutDestination = destinationFixture({
  ...salary, id: 14, kind: 'crypto', nickname: 'Tron', summary: 'TRC20 T…9f', status: 'pending',
  details: { coin: 'USDT', network: 'TRC20', address: 'TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE9f' },
  decided_by: null, decided_at: null,
})

function mockRoutes(opts: { rows?: PayoutDestination[]; fail?: boolean; removeRefused?: string } = {}) {
  const rows: PayoutDestination[] = [...(opts.rows ?? [salary, tron])]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (opts.fail) return jsonResponse({ detail: 'database unavailable' }, 500)
    if (url.endsWith('/investor/files') && init?.method === 'POST') {
      return jsonResponse({ id: 78, purpose: 'payout_proof', content_type: 'image/png', size_bytes: 3,
                            created_at: '2026-09-23T10:00:00Z' }, 201)
    }
    if (url.endsWith('/investor/payout-destinations') && init?.method === 'POST') {
      const body = JSON.parse(init.body as string) as { kind: 'bank' | 'crypto'; nickname: string; details: Record<string, string> }
      const created: PayoutDestination = destinationFixture({
        ...salary, id: 20, kind: body.kind, nickname: body.nickname, details: body.details, status: 'pending',
        summary: body.kind === 'bank' ? 'ICICI ••4543' : 'TRC20 T…9f', decided_by: null, decided_at: null,
      })
      rows.unshift(created)
      return jsonResponse(created, 201)
    }
    if (url.endsWith('/investor/payout-destinations')) return jsonResponse(rows)
    if (/\/investor\/payout-destinations\/\d+\/remove$/.test(url) && init?.method === 'POST') {
      if (opts.removeRefused) return jsonResponse({ detail: opts.removeRefused }, 409)
      const id = Number(url.match(/\/(\d+)\/remove$/)![1])
      const idx = rows.findIndex((r) => r.id === id)
      const removed = { ...rows[idx], status: 'removed' as const }
      rows.splice(idx, 1)
      return jsonResponse(removed)
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function posts(fetchMock: ReturnType<typeof mockRoutes>) {
  return fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
}

async function enterPin(dialog: HTMLElement, pin: string) {
  within(dialog).getByLabelText('Your MPIN digit 1 of 6').focus()
  await userEvent.keyboard(pin)
}

beforeEach(() => {
  useOrgMock.mockReturnValue(mockUseOrg('investor'))
  Object.defineProperty(URL, 'createObjectURL', { value: vi.fn(() => 'blob:preview'), configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: vi.fn(), configurable: true })
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('lists bank and crypto payout accounts with their status, heading and title', async () => {
  mockRoutes()
  render(<MemoryRouter><InvestorPayoutAccounts /></MemoryRouter>)
  expect(await screen.findByText('Salary')).toBeInTheDocument()
  expect(screen.getByText('ICICI ••4543')).toBeInTheDocument()
  expect(screen.getByText('Approved')).toBeInTheDocument()
  expect(screen.getByText('Tron')).toBeInTheDocument()
  expect(screen.getByText('Pending review')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Remove Salary' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 1, name: 'Payout accounts' })).toBeInTheDocument()
  expect(document.title).toBe('Payout accounts · MirrorFleet')
})

test('adding a bank account asks for the MPIN and posts the exact payload', async () => {
  const fetchMock = mockRoutes({ rows: [] })
  render(<MemoryRouter><InvestorPayoutAccounts /></MemoryRouter>)
  expect(await screen.findByText('No bank accounts yet')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Add bank account' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add bank account' })
  await userEvent.type(within(drawer).getByLabelText('Nickname'), 'Salary')
  await userEvent.type(within(drawer).getByLabelText('Bank name'), 'ICICI Bank')
  await userEvent.type(within(drawer).getByLabelText('Account holder'), 'S Joel')
  await userEvent.type(within(drawer).getByLabelText('Account number'), '000112344543')
  await userEvent.type(within(drawer).getByLabelText('SWIFT / IFSC code'), 'ICIC0001')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save payout account' }))

  const confirm = await screen.findByRole('dialog', { name: 'Save Salary as a payout account?' })
  expect(posts(fetchMock)).toHaveLength(0)
  await enterPin(confirm, '123456')
  await userEvent.click(within(confirm).getByRole('button', { name: 'Confirm' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  const post = posts(fetchMock)[0]
  expect(String(post[0])).toMatch(/\/investor\/payout-destinations$/)
  expect(JSON.parse((post[1] as RequestInit).body as string)).toEqual({
    kind: 'bank', nickname: 'Salary',
    details: { bank_name: 'ICICI Bank', holder: 'S Joel', account_number: '000112344543', code: 'ICIC0001' },
    proof_file_id: null, mpin: '123456',
  })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(await screen.findByText('Salary')).toBeInTheDocument()
  expect(screen.getByText(/An admin approves it before it can be used/)).toBeInTheDocument()
})

test('adding a crypto address uploads the proof first and posts its file id', async () => {
  const fetchMock = mockRoutes({ rows: [] })
  render(<MemoryRouter><InvestorPayoutAccounts /></MemoryRouter>)
  await screen.findByText('No crypto addresses yet')
  await userEvent.click(screen.getByRole('button', { name: 'Add crypto address' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add crypto address' })
  await userEvent.type(within(drawer).getByLabelText('Nickname'), 'Tron')
  await userEvent.type(within(drawer).getByLabelText('Coin'), 'USDT')
  await userEvent.type(within(drawer).getByLabelText('Network'), 'TRC20')
  await userEvent.type(within(drawer).getByLabelText('Address'), 'TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE9f')
  await userEvent.upload(within(drawer).getByLabelText(/^Proof/), new File(['png'], 'wallet.png', { type: 'image/png' }))
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save payout account' }))

  const confirm = await screen.findByRole('dialog', { name: 'Save Tron as a payout account?' })
  // The proof went up before the MPIN was asked for, so a wrong PIN never re-uploads it.
  expect(posts(fetchMock)).toHaveLength(1)
  expect((posts(fetchMock)[0][1] as RequestInit).body).toBeInstanceOf(FormData)
  expect(((posts(fetchMock)[0][1] as RequestInit).body as FormData).get('purpose')).toBe('payout_proof')
  await enterPin(confirm, '123456')
  await userEvent.click(within(confirm).getByRole('button', { name: 'Confirm' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(2))
  expect(JSON.parse((posts(fetchMock)[1][1] as RequestInit).body as string)).toEqual({
    kind: 'crypto', nickname: 'Tron',
    details: { coin: 'USDT', network: 'TRC20', address: 'TQn9Y2khEsLJW1ChVWFMSMeRDow5KcbLSE9f' },
    proof_file_id: 78, mpin: '123456',
  })
})

test('a required field that is blank stops the save before any MPIN is asked for', async () => {
  const fetchMock = mockRoutes({ rows: [] })
  render(<MemoryRouter><InvestorPayoutAccounts /></MemoryRouter>)
  await screen.findByText('No bank accounts yet')
  await userEvent.click(screen.getByRole('button', { name: 'Add bank account' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add bank account' })
  await userEvent.type(within(drawer).getByLabelText('Nickname'), 'Salary')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save payout account' }))
  expect(await within(drawer).findByText('Bank name is required')).toBeInTheDocument()
  expect(screen.queryByRole('dialog', { name: /as a payout account\?/ })).not.toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
})

test('Remove asks first, posts the remove route, and drops the row', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorPayoutAccounts /></MemoryRouter>)
  await userEvent.click(await screen.findByRole('button', { name: 'Remove Salary' }))
  const dialog = await screen.findByRole('dialog', { name: 'Remove Salary?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Remove' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(String(posts(fetchMock)[0][0])).toMatch(/\/investor\/payout-destinations\/12\/remove$/)
  await waitFor(() => expect(screen.queryByText('Salary')).not.toBeInTheDocument())
  expect(screen.getByText('Tron')).toBeInTheDocument()
})

test("the server's refusal to remove is shown as written", async () => {
  mockRoutes({ removeRefused: 'a withdrawal is still using this payout account' })
  render(<MemoryRouter><InvestorPayoutAccounts /></MemoryRouter>)
  await userEvent.click(await screen.findByRole('button', { name: 'Remove Salary' }))
  const dialog = await screen.findByRole('dialog', { name: 'Remove Salary?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Remove' }))
  expect(await screen.findByText('a withdrawal is still using this payout account')).toBeInTheDocument()
  expect(screen.getByText('Salary')).toBeInTheDocument()
})

test('dismissing a load error shows the empty states, not an endless skeleton', async () => {
  mockRoutes({ fail: true })
  render(<MemoryRouter><InvestorPayoutAccounts /></MemoryRouter>)
  const alert = await screen.findByRole('alert')
  await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('No bank accounts yet')).toBeInTheDocument()
  expect(screen.getByText('No crypto addresses yet')).toBeInTheDocument()
})
```

- [ ] **Step 6: Run it to verify it fails**

Run: `npx vitest run src/pages/investor/InvestorPayoutAccounts.test.tsx`
Expected: FAIL — `Error: Failed to load url ./InvestorPayoutAccounts`.

- [ ] **Step 7: Write the Payout accounts page**

Create `dashboard/src/pages/investor/InvestorPayoutAccounts.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi, orgUpload } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText } from '../../lib/format'
import { BADGE_TONE, statusLabel, statusTone } from '../../lib/investor'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import Drawer from '../../components/Drawer'
import FileInput, { MAX_UPLOAD_BYTES, RECEIPT_ACCEPT } from '../../components/FileInput'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import PageHeader from '../../components/PageHeader'
import PinConfirmDialog from '../../components/PinConfirmDialog'
import type { PayoutDestination, UploadedFile } from '../../lib/types'

type Kind = PayoutDestination['kind']

const EMPTY_BANK = { nickname: '', bank_name: '', holder: '', account_number: '', code: '', bank_address: '', country: '' }
const EMPTY_CRYPTO = { nickname: '', coin: '', network: '', address: '' }
type BankForm = typeof EMPTY_BANK
type CryptoForm = typeof EMPTY_CRYPTO

interface Field { key: string; label: string; required: boolean }
const BANK_FIELDS: Field[] = [
  { key: 'nickname', label: 'Nickname', required: true },
  { key: 'bank_name', label: 'Bank name', required: true },
  { key: 'holder', label: 'Account holder', required: true },
  { key: 'account_number', label: 'Account number', required: true },
  { key: 'code', label: 'SWIFT / IFSC code', required: true },
  { key: 'bank_address', label: 'Bank address (optional)', required: false },
  { key: 'country', label: 'Country (optional)', required: false },
]
const CRYPTO_FIELDS: Field[] = [
  { key: 'nickname', label: 'Nickname', required: true },
  { key: 'coin', label: 'Coin', required: true },
  { key: 'network', label: 'Network', required: true },
  { key: 'address', label: 'Address', required: true },
]
const DRAWER_TITLE: Record<Kind, string> = { bank: 'Add bank account', crypto: 'Add crypto address' }

/** What the PIN dialog will post: everything but the MPIN. */
interface Pending { kind: Kind; nickname: string; details: Record<string, string>; proof_file_id: number | null }

export default function InvestorPayoutAccounts() {
  const { orgId } = useOrg()
  const [rows, setRows] = useState<PayoutDestination[]>([])
  const [drawer, setDrawer] = useState<Kind | null>(null)
  const [bank, setBank] = useState<BankForm>(EMPTY_BANK)
  const [crypto, setCrypto] = useState<CryptoForm>(EMPTY_CRYPTO)
  const [proof, setProof] = useState<File | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [removing, setRemoving] = useState<PayoutDestination | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setRows(await orgApi<PayoutDestination[]>(orgId, 'investor/payout-destinations'))
    } catch (err) {
      setError(errorText(err, 'Could not load your payout accounts'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  const banks = rows.filter((d) => d.kind === 'bank')
  const cryptos = rows.filter((d) => d.kind === 'crypto')

  const openDrawer = (k: Kind) => {
    setDrawer(k); setFormError(null); setProof(null)
    setBank(EMPTY_BANK); setCrypto(EMPTY_CRYPTO)
  }
  const closeDrawer = () => { setDrawer(null); setFormError(null) }

  // Validates, uploads the optional proof, then hands over to the PIN
  // dialog. The upload happens here so a wrong PIN never re-uploads it.
  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!drawer) return
    setFormError(null)
    const fields = drawer === 'bank' ? BANK_FIELDS : CRYPTO_FIELDS
    const values: Record<string, string> = drawer === 'bank' ? { ...bank } : { ...crypto }
    const missing = fields.find((f) => f.required && !values[f.key].trim())
    if (missing) { setFormError(`${missing.label} is required`); return }
    const details: Record<string, string> = {}
    for (const f of fields) {
      if (f.key === 'nickname') continue
      const v = values[f.key].trim()
      if (v) details[f.key] = v
    }
    setBusy(true)
    try {
      let proof_file_id: number | null = null
      if (proof) {
        const fd = new FormData()
        fd.append('purpose', 'payout_proof')
        fd.append('file', proof)
        proof_file_id = (await orgUpload<UploadedFile>(orgId, 'investor/files', fd)).id
      }
      setPending({ kind: drawer, nickname: values.nickname.trim(), details, proof_file_id })
    } catch (err) {
      setFormError(errorText(err, 'Could not upload the proof'))
    } finally {
      setBusy(false)
    }
  }

  // Rejections propagate: PinConfirmDialog shows them inline and clears the PIN.
  const confirm = async (mpin: string) => {
    if (!pending) return
    setBusy(true)
    try {
      await orgApi<PayoutDestination>(orgId, 'investor/payout-destinations', {
        method: 'POST',
        body: JSON.stringify({ ...pending, mpin }),
      }, { redirectOn401: false })
      setPending(null)
      setDrawer(null)
      setBank(EMPTY_BANK); setCrypto(EMPTY_CRYPTO); setProof(null)
      setNotice('Payout account saved. An admin approves it before it can be used for a withdrawal.')
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!removing) return
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi<PayoutDestination>(orgId, `investor/payout-destinations/${removing.id}/remove`, { method: 'POST' })
      setNotice('Payout account removed.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not remove the payout account'))
    } finally {
      setBusy(false)
      setRemoving(null)
    }
  }

  const list = (items: PayoutDestination[], empty: string) => (
    <ul className="divide-y divide-line">
      {items.length === 0 && <li className="text-center py-8 text-ink-faint">{empty}</li>}
      {items.map((d) => (
        <li key={d.id} className="px-4 py-3 text-sm flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="font-semibold text-ink">{d.nickname}</span>
          <span className="num text-ink-soft flex-1 min-w-0">{d.summary}</span>
          <Badge tone={BADGE_TONE[statusTone(d.status, 'destination')]}>{statusLabel(d.status, 'destination')}</Badge>
          {d.status !== 'rejected' && (
            <Button variant="ghost" tone="loss" size="sm" aria-label={`Remove ${d.nickname}`}
                    onClick={() => setRemoving(d)} disabled={busy}>
              Remove
            </Button>
          )}
          {d.decision_note && <p className="basis-full text-xs text-ink-soft">Admin: {d.decision_note}</p>}
        </li>
      ))}
    </ul>
  )

  const fields = drawer === 'bank' ? BANK_FIELDS : CRYPTO_FIELDS
  const values: Record<string, string> = drawer === 'bank' ? bank : crypto
  const setValue = (key: string, v: string) => {
    if (drawer === 'bank') setBank({ ...bank, [key]: v })
    else setCrypto({ ...crypto, [key]: v })
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Payout accounts"
        subtitle="Where withdrawals are sent. An admin approves each account before it can be used."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {!loaded && <Loading lines={3} />}

      <Card title="Bank accounts" inset
            actions={<Button size="sm" onClick={() => openDrawer('bank')}>Add bank account</Button>}>
        {list(banks, 'No bank accounts yet')}
      </Card>

      <Card title="Crypto addresses" inset
            actions={<Button size="sm" onClick={() => openDrawer('crypto')}>Add crypto address</Button>}>
        {list(cryptos, 'No crypto addresses yet')}
      </Card>

      <Drawer open={drawer != null} title={drawer ? DRAWER_TITLE[drawer] : ''} onClose={closeDrawer} busy={busy}>
        <form onSubmit={save} noValidate className="space-y-4">
          {formError && <Banner kind="error" onDismiss={() => setFormError(null)}>{formError}</Banner>}
          {fields.map((f) => (
            <div key={f.key}>
              <label htmlFor={`payout-${f.key}`} className="desk-label block mb-1">{f.label}</label>
              <Input id={`payout-${f.key}`} num={f.key === 'account_number' || f.key === 'code' || f.key === 'address'}
                     value={values[f.key]} onChange={(e) => setValue(f.key, e.target.value)} disabled={busy} />
            </div>
          ))}
          <FileInput id="payout-proof" label="Proof (optional)" accept={RECEIPT_ACCEPT} maxBytes={MAX_UPLOAD_BYTES}
                     value={proof} onChange={setProof} disabled={busy}
                     hint="A statement header or wallet screenshot, JPEG, PNG, WebP or PDF up to 5 MB" />
          <Button type="submit" disabled={busy}>Save payout account</Button>
        </form>
      </Drawer>

      <PinConfirmDialog
        open={pending != null}
        title={`Save ${pending?.nickname ?? ''} as a payout account?`}
        confirmLabel="Confirm"
        busy={busy}
        onConfirm={confirm}
        onCancel={() => setPending(null)}
      >
        <p>An admin checks the details and approves the account before it can receive a withdrawal.</p>
        {pending && (
          <dl className="grid grid-cols-2 gap-2">
            {Object.entries(pending.details).map(([k, v]) => (
              <div key={k}>
                <dt className="desk-label">{k.replace(/_/g, ' ')}</dt>
                <dd className="num text-ink break-all">{v}</dd>
              </div>
            ))}
          </dl>
        )}
      </PinConfirmDialog>

      <ConfirmDialog
        open={removing != null}
        title={`Remove ${removing?.nickname ?? ''}?`}
        confirmLabel="Remove"
        danger
        busy={busy}
        onConfirm={remove}
        onCancel={() => setRemoving(null)}
      >
        <p>Past withdrawals keep their record of it; it just cannot be picked for a new one.</p>
      </ConfirmDialog>
    </div>
  )
}
```

- [ ] **Step 8: Run the Payout accounts test to verify it passes**

Run: `npx vitest run src/pages/investor/InvestorPayoutAccounts.test.tsx`
Expected: PASS, 7 passed.

- [ ] **Step 9: Route it**

In `dashboard/src/pages/groups/investor.ts` add, after the `InvestorWithdraw` line:

```ts
export { default as InvestorPayoutAccounts } from '../investor/InvestorPayoutAccounts'
```

In `dashboard/src/App.tsx` add, after `const InvestorWithdraw = pick(investor, 'InvestorWithdraw')`:

```tsx
const InvestorPayoutAccounts = pick(investor, 'InvestorPayoutAccounts')
```

and, after the `invest/withdraw` route line:

```tsx
              <Route path="invest/payout-accounts" element={<InvestorPayoutAccounts />} />
```

- [ ] **Step 10: Full dashboard gate**

Run (from `dashboard/`): `npm test`
Expected: prover ALL PASS, `tsc` clean, every vitest file green.

- [ ] **Step 11: Commit**

```bash
git add dashboard/src/pages/investor/InvestorWithdraw.tsx dashboard/src/pages/investor/InvestorWithdraw.test.tsx dashboard/src/pages/investor/InvestorPayoutAccounts.tsx dashboard/src/pages/investor/InvestorPayoutAccounts.test.tsx dashboard/src/pages/groups/investor.ts dashboard/src/App.tsx
git commit -m "feat(dashboard): Withdraw to an approved payout account with fee preview and MPIN confirm; Payout accounts page with bank and crypto drawers

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 16: Investor Transfer and Wallet

**Files:**
- Create: `dashboard/src/pages/investor/InvestorTransfer.tsx`
- Create: `dashboard/src/pages/investor/InvestorTransfer.test.tsx`
- Create: `dashboard/src/pages/investor/InvestorWallet.tsx`
- Create: `dashboard/src/pages/investor/InvestorWallet.test.tsx`
- Modify: `dashboard/src/pages/groups/investor.ts` (add both)
- Modify: `dashboard/src/App.tsx` (picks + routes `invest/transfer`, `invest/wallet`)
- Test: `dashboard/src/pages/investor/InvestorTransfer.test.tsx`, `dashboard/src/pages/investor/InvestorWallet.test.tsx`

**Interfaces:**
- Consumes: `InvestorSummary`, `MoneyRef`, `PortalTransfer`, `WalletEntriesPage`, `WalletEntry`, `WalletKind`; `ACCOUNT_CURRENCY`, `WALLETS`, `walletLabel`, `entryLabel`, `BADGE_TONE`, `statusLabel`, `statusTone`; `Money`; `PinConfirmDialog`; `ConfirmDialog`, `Select`; `orgApi` (step-up POST with `{ redirectOn401: false }`); fixtures `summaryFixture`, `transferFixture`, `entryFixture`; routes `GET investor/transfers`, `POST investor/transfers` `{source, target, amount, mpin}` (`{"kind":"wallet","wallet":"main"}` | `{"kind":"account","account_id":N}`), `POST investor/transfers/{id}/cancel`, `GET investor/wallet-entries?limit=20`.
- Produces: pages `InvestorTransfer` (title `Transfer`, `Select`s `aria-label="From"` / `"To"` with option values `wallet:main`, `wallet:pamm`, `wallet:social`, `account:<id>`, submit `Request transfer`, dialog title `Move {money(amount, 'USD')} from {from} to {to}?`, cancel `aria-label="Cancel transfer {id}"`) and `InvestorWallet` (title `Wallet`); exported `transferOptions(summary)`, `pairAllowed(from, to)`.

- [ ] **Step 1: Write the failing Transfer test**

Create `dashboard/src/pages/investor/InvestorTransfer.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorTransfer, { pairAllowed, transferOptions } from './InvestorTransfer'
import { mockUseOrg } from '../../test/orgMock'
import { summaryFixture, transferFixture } from '../../test/portalFixtures'
import type { InvestorSummary, PortalTransfer } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const linked: InvestorSummary = {
  ...summaryFixture(), currency: 'USD', link_state: 'linked',
  account: { account_id: 1001, nickname: 'Inv', platform: 'mt5', status: 'ok', last_error: null, connected: true },
  wallets: {
    main: { balance: 5120.5, on_hold: 100, available: 5020.5 },
    credit: { balance: 0, on_hold: 0, available: 0 },
    pamm: { balance: 300, on_hold: 0, available: 300 },
    social: { balance: 0, on_hold: 0, available: 0 },
  },
  equity_source: 'live', equity: 2500, account_available: 2500,
}
const unlinked: InvestorSummary = {
  ...linked, link_state: 'unlinked', account: null, equity_source: 'unknown', equity: null, account_available: null,
}
const requested: PortalTransfer = transferFixture({
  id: 9, user_id: 1, source: { kind: 'wallet', wallet: 'main' }, target: { kind: 'account', account_id: 1001 },
  amount: 1000, status: 'requested', equity_at_request: null, equity_verified: false,
  decided_by: null, decided_at: null, decision_note: null, done_by: null, done_at: null, note: null,
  created_at: '2026-09-24T10:00:00Z', currency: 'USD',
})
const done: PortalTransfer = transferFixture({
  ...requested, id: 10, source: { kind: 'wallet', wallet: 'pamm' }, target: { kind: 'wallet', wallet: 'main' },
  amount: 300, status: 'done', done_at: '2026-09-24T10:00:01Z',
})

function mockRoutes(opts: {
  summary?: InvestorSummary; rows?: PortalTransfer[]; created?: PortalTransfer
  refuse?: { status: number; body: unknown }; fail?: boolean
} = {}) {
  const rows: PortalTransfer[] = [...(opts.rows ?? [])]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (opts.fail) return jsonResponse({ detail: 'database unavailable' }, 500)
    if (url.endsWith('/investor/summary')) return jsonResponse(opts.summary ?? linked)
    if (url.endsWith('/investor/transfers') && init?.method === 'POST') {
      if (opts.refuse) return jsonResponse(opts.refuse.body, opts.refuse.status)
      const created = opts.created ?? requested
      rows.unshift(created)
      return jsonResponse(created, 201)
    }
    if (url.endsWith('/investor/transfers')) return jsonResponse(rows)
    if (/\/investor\/transfers\/\d+\/cancel$/.test(url) && init?.method === 'POST') {
      rows[0] = { ...rows[0], status: 'cancelled' }
      return jsonResponse(rows[0])
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function posts(fetchMock: ReturnType<typeof mockRoutes>) {
  return fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
}

async function enterPin(dialog: HTMLElement, pin: string) {
  within(dialog).getByLabelText('Your MPIN digit 1 of 6').focus()
  await userEvent.keyboard(pin)
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('transferOptions lists the three movable wallets plus the linked account; pairAllowed follows the phase 1 rules', () => {
  expect(transferOptions(linked).map((o) => o.value)).toEqual(['wallet:main', 'wallet:pamm', 'wallet:social', 'account:1001'])
  expect(transferOptions(unlinked).map((o) => o.value)).toEqual(['wallet:main', 'wallet:pamm', 'wallet:social'])
  expect(pairAllowed('wallet:main', 'account:1001')).toBe(true)
  expect(pairAllowed('account:1001', 'wallet:main')).toBe(true)
  expect(pairAllowed('wallet:pamm', 'wallet:main')).toBe(true)
  expect(pairAllowed('wallet:social', 'wallet:main')).toBe(true)
  expect(pairAllowed('wallet:main', 'wallet:pamm')).toBe(false)
  expect(pairAllowed('wallet:pamm', 'account:1001')).toBe(false)
  expect(pairAllowed('wallet:main', 'wallet:main')).toBe(false)
})

test('moves wallet money to the trading account after the MPIN, with the exact payload', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  expect(await screen.findByRole('heading', { level: 1, name: 'Transfer' })).toBeInTheDocument()
  expect(document.title).toBe('Transfer · MirrorFleet')
  const from = await screen.findByLabelText('From')
  expect(from).toHaveValue('wallet:main')
  expect(screen.getByLabelText('To')).toHaveValue('account:1001')
  expect(screen.getByText('5,020.50 USD')).toBeInTheDocument()

  await userEvent.type(screen.getByLabelText('Amount in USD'), '1000')
  await userEvent.click(screen.getByRole('button', { name: 'Request transfer' }))
  const dialog = await screen.findByRole('dialog', { name: 'Move 1,000.00 USD from My wallet to Trading account?' })
  expect(posts(fetchMock)).toHaveLength(0)
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm transfer' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  const post = posts(fetchMock)[0]
  expect(String(post[0])).toMatch(/\/investor\/transfers$/)
  expect(JSON.parse((post[1] as RequestInit).body as string)).toEqual({
    source: { kind: 'wallet', wallet: 'main' }, target: { kind: 'account', account_id: 1001 },
    amount: '1000', mpin: '123456',
  })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.getByText('Request sent. An admin moves the money and marks it done.')).toBeInTheDocument()
  expect(screen.getByText('Awaiting approval')).toBeInTheDocument()
  expect(screen.getByText('My wallet → Trading account')).toBeInTheDocument()
})

test('PAMM to My wallet completes at once and says so', async () => {
  const fetchMock = mockRoutes({ created: done })
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  await userEvent.selectOptions(await screen.findByLabelText('From'), 'wallet:pamm')
  const to = screen.getByLabelText('To')
  expect(within(to).getAllByRole('option').map((o) => (o as HTMLOptionElement).value)).toEqual(['wallet:main'])
  expect(screen.getByText('300.00 USD')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Use max' }))
  expect(screen.getByLabelText('Amount in USD')).toHaveValue('300.00')
  await userEvent.click(screen.getByRole('button', { name: 'Request transfer' }))
  const dialog = await screen.findByRole('dialog', { name: 'Move 300.00 USD from PAMM wallet to My wallet?' })
  await enterPin(dialog, '123456')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm transfer' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(JSON.parse((posts(fetchMock)[0][1] as RequestInit).body as string)).toEqual({
    source: { kind: 'wallet', wallet: 'pamm' }, target: { kind: 'wallet', wallet: 'main' },
    amount: '300.00', mpin: '123456',
  })
  expect(await screen.findByText('Transfer done. Your wallets are updated.')).toBeInTheDocument()
  expect(screen.getByText('Done')).toBeInTheDocument()
})

test('without a linked account My wallet has nowhere to go, but PAMM still moves to My wallet', async () => {
  mockRoutes({ summary: unlinked })
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  const from = await screen.findByLabelText('From')
  expect(within(screen.getByLabelText('To')).queryAllByRole('option')).toHaveLength(0)
  expect(screen.getByText(/Link a trading account to move wallet money into it/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Request transfer' })).toBeDisabled()
  await userEvent.selectOptions(from, 'wallet:pamm')
  expect(screen.getByLabelText('To')).toHaveValue('wallet:main')
  expect(screen.getByRole('button', { name: 'Request transfer' })).toBeEnabled()
})

test('a wrong MPIN stays in the dialog with the tries left', async () => {
  const fetchMock = mockRoutes({ refuse: { status: 401, body: { detail: 'Invalid MPIN', attempts_left: 1 } } })
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USD'), '10')
  await userEvent.click(screen.getByRole('button', { name: 'Request transfer' }))
  const dialog = await screen.findByRole('dialog')
  await enterPin(dialog, '111111')
  await userEvent.click(within(dialog).getByRole('button', { name: 'Confirm transfer' }))
  expect(await screen.findByText('Wrong MPIN, 1 try left')).toBeInTheDocument()
  expect(screen.getByRole('dialog')).toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(1)
})

test('an amount that is not above zero never reaches the review', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  await userEvent.type(await screen.findByLabelText('Amount in USD'), '0')
  await userEvent.click(screen.getByRole('button', { name: 'Request transfer' }))
  expect(await screen.findByText('Enter an amount above zero')).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(posts(fetchMock)).toHaveLength(0)
})

test('a requested transfer can be cancelled after a confirmation; a done one cannot', async () => {
  const fetchMock = mockRoutes({ rows: [requested, done] })
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  expect(await screen.findByText('Done')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Cancel transfer 10' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Cancel transfer 9' }))
  const dialog = await screen.findByRole('dialog', { name: 'Cancel transfer #9?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Yes, cancel it' }))
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(1))
  expect(String(posts(fetchMock)[0][0])).toMatch(/\/investor\/transfers\/9\/cancel$/)
  expect(await screen.findByText('Cancelled')).toBeInTheDocument()
})

test('a transfer that the admin acknowledged reads "Approved, in progress"', async () => {
  mockRoutes({ rows: [{ ...requested, status: 'approved', decided_by: 2, decided_at: '2026-09-24T11:00:00Z' }] })
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  expect(await screen.findByText('Approved, in progress')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Cancel transfer 9' })).not.toBeInTheDocument()
})

test('dismissing a load error shows the empty state, not an endless skeleton', async () => {
  mockRoutes({ fail: true })
  render(<MemoryRouter><InvestorTransfer /></MemoryRouter>)
  const alert = await screen.findByRole('alert')
  await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('No transfers yet')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run src/pages/investor/InvestorTransfer.test.tsx`
Expected: FAIL — `Error: Failed to load url ./InvestorTransfer`.

- [ ] **Step 3: Write the Transfer page**

Create `dashboard/src/pages/investor/InvestorTransfer.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen, money } from '../../lib/format'
import { ACCOUNT_CURRENCY, BADGE_TONE, statusLabel, statusTone, walletLabel } from '../../lib/investor'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import Money from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import PinConfirmDialog from '../../components/PinConfirmDialog'
import Select from '../../components/Select'
import type { InvestorSummary, MoneyRef, PortalTransfer, WalletKind } from '../../lib/types'

const AMOUNT_RE = /^\d+(\.\d{1,2})?$/
const TWO_DECIMALS = 'Enter an amount with at most two decimals, digits only (for example 250.00).'
// The pairs phase 1 allows (spec section 7); "account" is the linked
// trading account, the credit wallet never moves.
const PAIRS: ReadonlyArray<readonly [string, string]> = [
  ['main', 'account'], ['account', 'main'], ['pamm', 'main'], ['social', 'main'],
]

export interface TransferOption {
  /** `wallet:<kind>` or `account:<id>`: the Select's option value. */
  value: string
  label: string
  ref: MoneyRef
  available: number | null
}

/** The side of a pair an option value stands for. */
function sideOf(value: string): string {
  return value.startsWith('account:') ? 'account' : value.slice('wallet:'.length)
}

export function pairAllowed(from: string, to: string): boolean {
  const a = sideOf(from)
  const b = sideOf(to)
  return PAIRS.some(([x, y]) => x === a && y === b)
}

export function transferOptions(s: InvestorSummary): TransferOption[] {
  const wallets: WalletKind[] = ['main', 'pamm', 'social']
  const opts: TransferOption[] = wallets.map((w) => ({
    value: `wallet:${w}`, label: walletLabel(w), ref: { kind: 'wallet', wallet: w },
    available: s.wallets[w].available,
  }))
  if (s.link_state === 'linked' && s.account) {
    opts.push({
      value: `account:${s.account.account_id}`, label: 'Trading account',
      ref: { kind: 'account', account_id: s.account.account_id }, available: s.account_available,
    })
  }
  return opts
}

function refLabel(r: MoneyRef): string {
  return r.kind === 'wallet' && r.wallet ? walletLabel(r.wallet) : 'Trading account'
}

export default function InvestorTransfer() {
  const { orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [rows, setRows] = useState<PortalTransfer[]>([])
  const [form, setForm] = useState({ from: 'wallet:main', to: '', amount: '' })
  const [reviewing, setReviewing] = useState(false)
  const [cancelling, setCancelling] = useState<PortalTransfer | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [s, list] = await Promise.all([
        orgApi<InvestorSummary>(orgId, 'investor/summary'),
        orgApi<PortalTransfer[]>(orgId, 'investor/transfers'),
      ])
      setSummary(s); setRows(list)
    } catch (err) {
      setError(errorText(err, 'Could not load your transfers'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  const unit = summary?.currency ?? ACCOUNT_CURRENCY
  const options = summary ? transferOptions(summary) : []
  const fromOpt = options.find((o) => o.value === form.from) ?? options[0] ?? null
  // The To list follows the pair rules; a stale choice falls back to the
  // first allowed target, so switching From never leaves an illegal pair.
  const targets = fromOpt ? options.filter((o) => pairAllowed(fromOpt.value, o.value)) : []
  const toOpt = targets.find((o) => o.value === form.to) ?? targets[0] ?? null
  const available = fromOpt?.available ?? null

  const review = (e: React.FormEvent) => {
    e.preventDefault()
    setError(null); setNotice(null)
    if (!AMOUNT_RE.test(form.amount.trim())) { setError(TWO_DECIMALS); return }
    if (!(Number(form.amount) > 0)) { setError('Enter an amount above zero'); return }
    if (!fromOpt || !toOpt) return
    setReviewing(true)
  }

  // Rejections propagate: PinConfirmDialog shows them inline and clears the PIN.
  const send = async (mpin: string) => {
    if (!fromOpt || !toOpt) return
    setBusy(true)
    try {
      const t = await orgApi<PortalTransfer>(orgId, 'investor/transfers', {
        method: 'POST',
        body: JSON.stringify({ source: fromOpt.ref, target: toOpt.ref, amount: form.amount.trim(), mpin }),
      }, { redirectOn401: false })
      setReviewing(false)
      setForm({ ...form, amount: '' })
      setNotice(t.status === 'done'
        ? 'Transfer done. Your wallets are updated.'
        : 'Request sent. An admin moves the money and marks it done.')
      await refresh()
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (!cancelling) return
    setBusy(true); setError(null); setNotice(null)
    try {
      await orgApi<PortalTransfer>(orgId, `investor/transfers/${cancelling.id}/cancel`, { method: 'POST' })
      setNotice('Transfer cancelled.')
      await refresh()
    } catch (err) {
      setError(errorText(err, 'Could not cancel the transfer'))
    } finally {
      setBusy(false)
      setCancelling(null)
    }
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Transfer"
        subtitle="Move money between your wallets and your trading account. Wallet-to-wallet moves complete at once; moves to or from the trading account are done by an admin."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}
      {!loaded && <Loading lines={3} />}

      {summary && fromOpt && (
        <Card title="New transfer">
          <form onSubmit={review} noValidate className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="block">
                <span className="desk-label block mb-1">From</span>
                <Select aria-label="From" block value={fromOpt.value}
                        onChange={(e) => setForm({ ...form, from: e.target.value, to: '' })}>
                  {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </Select>
                <span className="block mt-1 text-xs text-ink-soft">
                  {available == null
                    ? 'Available: unknown while the account is offline; an admin will check it.'
                    : <>Available: <Money value={available} unit={unit} /></>}
                </span>
              </label>
              <label className="block">
                <span className="desk-label block mb-1">To</span>
                <Select aria-label="To" block value={toOpt?.value ?? ''} disabled={targets.length === 0}
                        onChange={(e) => setForm({ ...form, to: e.target.value })}>
                  {targets.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </Select>
                {targets.length === 0 && (
                  <span className="block mt-1 text-xs text-warn-deep">
                    Link a trading account to move wallet money into it; PAMM and Social wallets can still move to My wallet.
                  </span>
                )}
              </label>
            </div>
            <div className="flex gap-3 flex-wrap items-end">
              <label className="block w-40">
                <span className="desk-label block mb-1">Amount ({unit})</span>
                <Input aria-label={`Amount in ${unit}`} num value={form.amount}
                       onChange={(e) => setForm({ ...form, amount: e.target.value })} />
              </label>
              <Button variant="ghost" size="sm" disabled={available == null || available <= 0}
                      onClick={() => { if (available != null) setForm({ ...form, amount: available.toFixed(2) }) }}>
                Use max
              </Button>
            </div>
            <Button type="submit" disabled={busy || !toOpt}>Request transfer</Button>
          </form>
        </Card>
      )}

      <PinConfirmDialog
        open={reviewing}
        title={`Move ${money(form.amount, unit)} from ${fromOpt?.label ?? ''} to ${toOpt?.label ?? ''}?`}
        confirmLabel="Confirm transfer"
        busy={busy}
        onConfirm={send}
        onCancel={() => setReviewing(false)}
      >
        <p>
          {toOpt?.ref.kind === 'wallet' && fromOpt?.ref.kind === 'wallet'
            ? 'This completes at once.'
            : 'The amount is held until an admin moves it at the broker and marks the transfer done.'}
        </p>
        <dl className="grid grid-cols-2 gap-2">
          <div><dt className="desk-label">From</dt><dd className="text-ink">{fromOpt?.label}</dd></div>
          <div><dt className="desk-label">To</dt><dd className="text-ink">{toOpt?.label}</dd></div>
          <div><dt className="desk-label">Amount</dt><dd className="num text-ink">{money(form.amount, unit)}</dd></div>
        </dl>
      </PinConfirmDialog>

      <ConfirmDialog
        open={cancelling != null}
        title={`Cancel transfer #${cancelling?.id ?? ''}?`}
        confirmLabel="Yes, cancel it"
        danger
        busy={busy}
        onConfirm={cancel}
        onCancel={() => setCancelling(null)}
      >
        <p>The amount on hold returns to its wallet and nothing is moved.</p>
      </ConfirmDialog>

      <Card title="Your transfers" inset>
        <ul className="divide-y divide-line">
          {rows.length === 0 && <li className="text-center py-8 text-ink-faint">No transfers yet</li>}
          {rows.map((t) => (
            <li key={t.id} className="px-4 py-3 text-sm space-y-1">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="num text-ink-soft">{formatWhen(t.created_at)}</span>
                <span className="text-ink">{`${refLabel(t.source)} → ${refLabel(t.target)}`}</span>
                <span className="font-semibold text-ink"><Money value={t.amount} unit={t.currency} /></span>
                <Badge tone={BADGE_TONE[statusTone(t.status, 'transfer')]}>{statusLabel(t.status, 'transfer')}</Badge>
                {t.status === 'requested' && (
                  <Button variant="ghost" tone="loss" size="sm" aria-label={`Cancel transfer ${t.id}`}
                          onClick={() => setCancelling(t)} disabled={busy}>
                    Cancel
                  </Button>
                )}
              </div>
              {t.note && <p className="text-xs text-ink-soft">{t.note}</p>}
              {t.decision_note && <p className="text-xs text-ink-soft">Admin: {t.decision_note}</p>}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
```

- [ ] **Step 4: Run the Transfer test to verify it passes**

Run: `npx vitest run src/pages/investor/InvestorTransfer.test.tsx`
Expected: PASS, 9 passed.

- [ ] **Step 5: Write the failing Wallet test**

Create `dashboard/src/pages/investor/InvestorWallet.test.tsx`:

```tsx
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorWallet from './InvestorWallet'
import { mockUseOrg } from '../../test/orgMock'
import { entryFixture, summaryFixture } from '../../test/portalFixtures'
import type { InvestorSummary } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const summary: InvestorSummary = {
  ...summaryFixture(), currency: 'USD',
  wallets: {
    main: { balance: 3000, on_hold: 100, available: 2900 },
    credit: { balance: 0, on_hold: 0, available: 0 },
    pamm: { balance: 1000, on_hold: 0, available: 1000 },
    social: { balance: 0, on_hold: 0, available: 0 },
  },
}
const entry = entryFixture({
  id: 1, wallet: 'main', amount: 5000, kind: 'deposit', ref_table: 'deposits', ref_id: 1,
  note: null, created_at: '2026-09-20T10:00:00Z', currency: 'USD',
})

function mockRoutes(opts: { fail?: boolean } = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (opts.fail) return jsonResponse({ detail: 'database unavailable' }, 500)
    if (url.endsWith('/investor/summary')) return jsonResponse(summary)
    if (url.includes('/investor/wallet-entries')) return jsonResponse({ entries: [entry], has_more: true, next_before: 1 })
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('shows the four wallets with their share, quick actions and the last entries', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorWallet /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Wallet' })).toBeInTheDocument()
  expect(document.title).toBe('Wallet · MirrorFleet')
  const table = await screen.findByRole('table', { name: 'Your wallets' })
  const rows = within(table).getAllByRole('row').slice(1)
  expect(rows).toHaveLength(4)
  expect(rows[0]).toHaveTextContent('My wallet')
  expect(rows[0]).toHaveTextContent('3,000.00 USD')
  expect(rows[0]).toHaveTextContent('75%')
  expect(rows[0]).toHaveTextContent('2,900.00 USD')
  expect(rows[2]).toHaveTextContent('PAMM wallet')
  expect(rows[2]).toHaveTextContent('25%')
  expect(screen.getByRole('link', { name: 'Deposit' })).toHaveAttribute('href', '/org/1/invest/deposit')
  expect(screen.getByRole('link', { name: 'Withdraw' })).toHaveAttribute('href', '/org/1/invest/withdraw')
  expect(screen.getByRole('link', { name: 'Transfer' })).toHaveAttribute('href', '/org/1/invest/transfer')
  expect(screen.getByRole('link', { name: 'Transactions' })).toHaveAttribute('href', '/org/1/invest/transactions')
  expect(screen.getByText('Deposit #1')).toBeInTheDocument()
  expect(screen.getByText(/\+5,000\.00/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'View all' })).toHaveAttribute('href', '/org/1/invest/transactions')
  const entriesCall = fetchMock.mock.calls.map(([u]) => String(u)).find((u) => u.includes('/investor/wallet-entries'))
  expect(entriesCall).toMatch(/limit=20$/)
})

test('dismissing a load error shows the empty state, not an endless skeleton', async () => {
  mockRoutes({ fail: true })
  render(<MemoryRouter><InvestorWallet /></MemoryRouter>)
  const alert = await screen.findByRole('alert')
  await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('Nothing yet')).toBeInTheDocument()
})
```

- [ ] **Step 6: Run it to verify it fails**

Run: `npx vitest run src/pages/investor/InvestorWallet.test.tsx`
Expected: FAIL — `Error: Failed to load url ./InvestorWallet`.

- [ ] **Step 7: Write the Wallet page**

Create `dashboard/src/pages/investor/InvestorWallet.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen } from '../../lib/format'
import { ACCOUNT_CURRENCY, WALLETS, entryLabel, walletLabel } from '../../lib/investor'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import Loading from '../../components/Loading'
import Money from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import type { InvestorSummary, WalletEntriesPage, WalletEntry } from '../../lib/types'

const ACTIONS = [
  { slug: 'deposit', title: 'Deposit', text: 'Bank transfer or crypto, with a receipt.' },
  { slug: 'withdraw', title: 'Withdraw', text: 'To a payout account an admin approved.' },
  { slug: 'transfer', title: 'Transfer', text: 'Between your wallets and your trading account.' },
  { slug: 'transactions', title: 'Transactions', text: 'Every movement, with a CSV download.' },
]

export default function InvestorWallet() {
  const { orgId } = useOrg()
  const base = `/org/${orgId}/invest`
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [entries, setEntries] = useState<WalletEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [s, page] = await Promise.all([
        orgApi<InvestorSummary>(orgId, 'investor/summary'),
        orgApi<WalletEntriesPage>(orgId, 'investor/wallet-entries?limit=20'),
      ])
      setSummary(s); setEntries(page.entries)
    } catch (err) {
      setError(errorText(err, 'Could not load your wallet'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => { refresh() }, [refresh])

  const unit = summary?.currency ?? ACCOUNT_CURRENCY
  // Share of the money you hold: negative balances (after an adjustment)
  // do not shrink the others' share.
  const total = summary ? WALLETS.reduce((s, w) => s + Math.max(0, summary.wallets[w].balance), 0) : 0

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader title="Wallet" subtitle="Your four wallets and what each one holds." />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {!loaded && <Loading lines={4} />}

      {summary && (
        <Card title="Your wallets" inset>
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm" aria-label="Your wallets">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-4 py-2 font-semibold">Wallet</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Balance</th>
                  <th className="desk-label px-4 py-2 font-semibold">Share</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">On hold</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Available</th>
                </tr>
              </thead>
              <tbody>
                {WALLETS.map((w) => {
                  const f = summary.wallets[w]
                  const pct = total > 0 ? (Math.max(0, f.balance) / total) * 100 : 0
                  return (
                    <tr key={w} className="border-b border-line last:border-0">
                      <td data-label="Wallet" className="px-4 py-2.5 text-ink font-semibold">{walletLabel(w)}</td>
                      <td data-label="Balance" className="px-4 py-2.5 text-right"><Money value={f.balance} unit={unit} /></td>
                      <td data-label="Share" className="px-4 py-2.5">
                        <div className="flex items-center gap-2">
                          <div className="h-1.5 w-24 rounded-full bg-line overflow-hidden" aria-hidden="true">
                            <div className="h-full rounded-full bg-brand" style={{ width: `${pct}%` }} />
                          </div>
                          <span className="num text-xs text-ink-soft">{`${pct.toFixed(0)}%`}</span>
                        </div>
                      </td>
                      <td data-label="On hold" className="px-4 py-2.5 text-right"><Money value={f.on_hold} unit={unit} /></td>
                      <td data-label="Available" className="px-4 py-2.5 text-right"><Money value={f.available} unit={unit} /></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {ACTIONS.map((a) => (
          <Card key={a.slug} title={a.title}>
            <p className="text-sm text-ink-soft mb-3">{a.text}</p>
            <Button variant="secondary" size="sm" to={`${base}/${a.slug}`}>{a.title}</Button>
          </Card>
        ))}
      </div>

      <Card title="Recent entries" inset
            actions={<Button variant="ghost" size="sm" to={`${base}/transactions`}>View all</Button>}>
        <ul className="divide-y divide-line">
          {entries.length === 0 && <li className="text-center py-8 text-ink-faint">Nothing yet</li>}
          {entries.map((e) => (
            <li key={e.id} className="px-4 py-2.5 text-sm flex flex-wrap items-center gap-x-4 gap-y-1">
              <time dateTime={e.created_at} className="num text-ink-soft min-w-0 basis-full sm:basis-auto">
                {formatWhen(e.created_at)}
              </time>
              <span className="text-ink flex-1 min-w-0">{entryLabel(e)}</span>
              <span className="text-xs text-ink-soft">{walletLabel(e.wallet)}</span>
              <Money value={e.amount} unit={e.currency} signed
                     className={e.amount < 0 ? 'text-loss' : 'text-profit'} />
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}
```

- [ ] **Step 8: Run the Wallet test to verify it passes**

Run: `npx vitest run src/pages/investor/InvestorWallet.test.tsx`
Expected: PASS, 2 passed.

- [ ] **Step 9: Route both pages**

In `dashboard/src/pages/groups/investor.ts` add, after the `InvestorPayoutAccounts` line:

```ts
export { default as InvestorTransfer } from '../investor/InvestorTransfer'
export { default as InvestorWallet } from '../investor/InvestorWallet'
```

In `dashboard/src/App.tsx` add, after `const InvestorPayoutAccounts = pick(investor, 'InvestorPayoutAccounts')`:

```tsx
const InvestorTransfer = pick(investor, 'InvestorTransfer')
const InvestorWallet = pick(investor, 'InvestorWallet')
```

and, after the `invest/payout-accounts` route line:

```tsx
              <Route path="invest/transfer" element={<InvestorTransfer />} />
              <Route path="invest/wallet" element={<InvestorWallet />} />
```

- [ ] **Step 10: Full dashboard gate**

Run (from `dashboard/`): `npm test`
Expected: prover ALL PASS, `tsc` clean, every vitest file green.

- [ ] **Step 11: Commit**

```bash
git add dashboard/src/pages/investor/InvestorTransfer.tsx dashboard/src/pages/investor/InvestorTransfer.test.tsx dashboard/src/pages/investor/InvestorWallet.tsx dashboard/src/pages/investor/InvestorWallet.test.tsx dashboard/src/pages/groups/investor.ts dashboard/src/App.tsx
git commit -m "feat(dashboard): Transfer page with pair rules and MPIN confirm; Wallet page with the four wallets, shares and quick actions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 17: Investor Transactions and the Account page extension

**Files:**
- Create: `dashboard/src/pages/investor/InvestorTransactions.tsx`
- Create: `dashboard/src/pages/investor/InvestorTransactions.test.tsx`
- Modify: `dashboard/src/pages/investor/InvestorAccount.tsx` (trading account card, open positions table and 4-week analytics moved from the old Overview; profile and `AccountSecurity` kept)
- Modify: `dashboard/src/pages/investor/InvestorAccount.test.tsx` (fetch stubs; linked/unlinked cases)
- Modify: `dashboard/src/pages/groups/investor.ts` (add InvestorTransactions)
- Modify: `dashboard/src/App.tsx` (pick + route `invest/transactions`)
- Test: `dashboard/src/pages/investor/InvestorTransactions.test.tsx`, `dashboard/src/pages/investor/InvestorAccount.test.tsx`

**Interfaces:**
- Consumes: `WalletEntry`, `WalletEntriesPage`, `InvestorSummary`, `InvestorPositions`, `Analytics`; `ACCOUNT_CURRENCY`, `WALLETS`, `walletLabel`, `entryLabel`, `moneyOrDash`; `Money`; `Tabs`, `Select`, `Input`, `EquityCurve`, `AccountSecurity`, `NextStep`; `orgApi`; fixtures `summaryFixture`, `entryFixture`; routes `GET investor/wallet-entries?wallet=&kind=&from=&to=&limit=&before=` → `{entries, has_more, next_before}`, `GET investor/summary`, `GET investor/positions`, `GET investor/analytics?weeks=4`.
- Produces: page `InvestorTransactions` (title `Transactions`, Tabs `idBase="wallet"` All + WALLETS, filters `From date` / `To date` / `Kind`, `Load more`, `Download CSV` → `transactions.csv` with columns Date, Wallet, Kind, Amount, Reference, Note); exported `entriesQuery(filter)`, `toCsv(rows)`; `InvestorAccount` extended (title `Account`).

- [ ] **Step 1: Write the failing Transactions test**

Create `dashboard/src/pages/investor/InvestorTransactions.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorTransactions, { entriesQuery, toCsv } from './InvestorTransactions'
import { mockUseOrg } from '../../test/orgMock'
import { entryFixture } from '../../test/portalFixtures'
import type { WalletEntry } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const deposit: WalletEntry = entryFixture({
  id: 50, wallet: 'main', amount: 5000, kind: 'deposit', ref_table: 'deposits', ref_id: 1,
  note: null, created_at: '2026-09-20T10:00:00Z', currency: 'USD',
})
const withdrawal: WalletEntry = entryFixture({
  id: 40, wallet: 'main', amount: -1000, kind: 'withdrawal', ref_table: 'withdrawals', ref_id: 4,
  note: 'paid to ICICI ••4543', created_at: '2026-09-21T10:00:00Z', currency: 'USD',
})
const adjustment: WalletEntry = entryFixture({
  id: 30, wallet: 'pamm', amount: 12.5, kind: 'adjustment', ref_table: null, ref_id: null,
  note: 'welcome bonus, "phase 4" later', created_at: '2026-09-22T10:00:00Z', currency: 'USD',
})

function mockRoutes(opts: { fail?: boolean } = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (opts.fail) return jsonResponse({ detail: 'database unavailable' }, 500)
    if (url.includes('/investor/wallet-entries')) {
      const q = new URL(url, 'http://x').searchParams
      if (q.get('before') === '40') return jsonResponse({ entries: [adjustment], has_more: false, next_before: null })
      if (q.get('wallet') === 'pamm') return jsonResponse({ entries: [adjustment], has_more: false, next_before: null })
      return jsonResponse({ entries: [deposit, withdrawal], has_more: true, next_before: 40 })
    }
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function queries(fetchMock: ReturnType<typeof mockRoutes>) {
  return fetchMock.mock.calls.map(([u]) => String(u)).filter((u) => u.includes('/investor/wallet-entries'))
    .map((u) => new URL(u, 'http://x').searchParams)
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

test('entriesQuery carries only the filters that are set', () => {
  expect(entriesQuery({ wallet: 'all', kind: '', from: '', to: '', before: null }))
    .toBe('investor/wallet-entries?limit=50')
  expect(entriesQuery({ wallet: 'pamm', kind: 'withdrawal', from: '2026-09-01', to: '2026-09-30', before: 40 }))
    .toBe('investor/wallet-entries?limit=50&wallet=pamm&kind=withdrawal&from=2026-09-01&to=2026-09-30&before=40')
})

test('toCsv writes the six columns, quotes commas and doubles quotes', () => {
  const csv = toCsv([deposit, withdrawal, adjustment])
  const lines = csv.split('\r\n')
  expect(lines[0]).toBe('Date,Wallet,Kind,Amount,Reference,Note')
  expect(lines[1]).toBe('2026-09-20T10:00:00Z,My wallet,deposit,5000.00,deposits/1,')
  expect(lines[2]).toBe('2026-09-21T10:00:00Z,My wallet,withdrawal,-1000.00,withdrawals/4,paid to ICICI ••4543')
  expect(lines[3]).toBe('2026-09-22T10:00:00Z,PAMM wallet,adjustment,12.50,,"welcome bonus, ""phase 4"" later"')
  expect(lines[4]).toBe('')
})

test('lists entries with a signed amount and a link to the request, and has its heading and title', async () => {
  mockRoutes()
  render(<MemoryRouter><InvestorTransactions /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Transactions' })).toBeInTheDocument()
  expect(document.title).toBe('Transactions · MirrorFleet')
  expect(await screen.findByRole('link', { name: 'Deposit #1' })).toHaveAttribute('href', '/org/1/invest/deposit')
  expect(screen.getByRole('link', { name: 'Withdrawal #4' })).toHaveAttribute('href', '/org/1/invest/withdraw')
  expect(screen.getByText(/\+5,000\.00/)).toBeInTheDocument()
  expect(screen.getByText(/-1,000\.00/)).toBeInTheDocument()
  expect(screen.getAllByText('My wallet').length).toBeGreaterThan(0)
  expect(screen.getByText('paid to ICICI ••4543')).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'All' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tab', { name: 'Credit wallet' })).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'Social wallet' })).toBeInTheDocument()
})

test('a wallet tab reloads with that wallet', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorTransactions /></MemoryRouter>)
  await screen.findByRole('link', { name: 'Deposit #1' })
  await userEvent.click(screen.getByRole('tab', { name: 'PAMM wallet' }))
  // The note is the one text only the adjustment row carries: 'Adjustment'
  // also sits in the Kind filter's <option> from the first paint.
  expect(await screen.findByText('welcome bonus, "phase 4" later')).toBeInTheDocument()
  expect(within(screen.getByRole('table')).getAllByText('Adjustment').length).toBeGreaterThan(0)
  expect(screen.queryByRole('link', { name: 'Deposit #1' })).not.toBeInTheDocument()
  const last = queries(fetchMock).at(-1)!
  expect(last.get('wallet')).toBe('pamm')
  expect(last.get('before')).toBeNull()
})

test('the date and kind filters are sent only after Apply, and Clear drops them', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorTransactions /></MemoryRouter>)
  await screen.findByRole('link', { name: 'Deposit #1' })
  await userEvent.type(screen.getByLabelText('From date'), '2026-09-01')
  await userEvent.type(screen.getByLabelText('To date'), '2026-09-30')
  await userEvent.selectOptions(screen.getByLabelText('Kind'), 'withdrawal')
  expect(queries(fetchMock)).toHaveLength(1)
  await userEvent.click(screen.getByRole('button', { name: 'Apply' }))
  await waitFor(() => expect(queries(fetchMock)).toHaveLength(2))
  const applied = queries(fetchMock)[1]
  expect(applied.get('from')).toBe('2026-09-01')
  expect(applied.get('to')).toBe('2026-09-30')
  expect(applied.get('kind')).toBe('withdrawal')
  await userEvent.click(screen.getByRole('button', { name: 'Clear' }))
  await waitFor(() => expect(queries(fetchMock)).toHaveLength(3))
  expect(queries(fetchMock)[2].get('kind')).toBeNull()
  expect(screen.getByLabelText('Kind')).toHaveValue('')
})

test('Load more appends the next page using next_before', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><InvestorTransactions /></MemoryRouter>)
  await screen.findByRole('link', { name: 'Deposit #1' })
  await userEvent.click(screen.getByRole('button', { name: 'Load more' }))
  expect(await screen.findByText('welcome bonus, "phase 4" later')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Deposit #1' })).toBeInTheDocument()
  expect(queries(fetchMock).at(-1)!.get('before')).toBe('40')
  expect(screen.queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()
})

test('Download CSV hands the browser a transactions.csv built from the loaded rows', async () => {
  mockRoutes()
  const createObjectURL = vi.fn((_blob: Blob) => 'blob:csv')
  const revokeObjectURL = vi.fn()
  Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, configurable: true })
  let downloaded: HTMLAnchorElement | null = null
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    downloaded = this
  })
  render(<MemoryRouter><InvestorTransactions /></MemoryRouter>)
  await screen.findByRole('link', { name: 'Deposit #1' })
  await userEvent.click(screen.getByRole('button', { name: 'Download CSV' }))
  expect(createObjectURL).toHaveBeenCalledTimes(1)
  expect(createObjectURL.mock.calls[0][0].type).toContain('text/csv')
  expect(downloaded).not.toBeNull()
  expect(downloaded!.download).toBe('transactions.csv')
  expect(downloaded!.href).toContain('blob:csv')
  expect(revokeObjectURL).toHaveBeenCalledWith('blob:csv')
})

test('dismissing a load error shows the empty state, not an endless skeleton', async () => {
  mockRoutes({ fail: true })
  render(<MemoryRouter><InvestorTransactions /></MemoryRouter>)
  const alert = await screen.findByRole('alert')
  await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  expect(screen.getByText('No transactions yet')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npx vitest run src/pages/investor/InvestorTransactions.test.tsx`
Expected: FAIL — `Error: Failed to load url ./InvestorTransactions`.

- [ ] **Step 3: Write the Transactions page**

Create `dashboard/src/pages/investor/InvestorTransactions.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, formatWhen } from '../../lib/format'
import { WALLETS, entryLabel, walletLabel } from '../../lib/investor'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import Input from '../../components/Input'
import Loading from '../../components/Loading'
import Money from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import Select from '../../components/Select'
import Tabs from '../../components/Tabs'
import type { WalletEntriesPage, WalletEntry } from '../../lib/types'

const PAGE = 50
const KINDS: WalletEntry['kind'][] = ['deposit', 'withdrawal', 'transfer', 'adjustment', 'bonus', 'commission', 'fee']
// Which investor page shows the request a ledger row settled.
const REF_PAGE: Record<string, string> = { deposits: 'deposit', withdrawals: 'withdraw', transfers: 'transfer' }

export interface EntriesFilter {
  wallet: string   // 'all' or a WalletKind
  kind: string     // '' or a kind
  from: string     // '' or YYYY-MM-DD
  to: string
  before: number | null
}

/** The wallet-entries tail with only the filters that are set. */
export function entriesQuery(f: EntriesFilter): string {
  const p = new URLSearchParams()
  p.set('limit', String(PAGE))
  if (f.wallet !== 'all') p.set('wallet', f.wallet)
  if (f.kind) p.set('kind', f.kind)
  if (f.from) p.set('from', f.from)
  if (f.to) p.set('to', f.to)
  if (f.before != null) p.set('before', String(f.before))
  return `investor/wallet-entries?${p.toString()}`
}

/** transactions.csv from the rows on screen: RFC 4180 quoting, CRLF, a
 *  plain two-decimal number for the amount so spreadsheets read it as one. */
export function toCsv(rows: WalletEntry[]): string {
  const esc = (v: string) => (/[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v)
  const lines = [['Date', 'Wallet', 'Kind', 'Amount', 'Reference', 'Note'].join(',')]
  for (const e of rows) {
    lines.push([
      e.created_at, walletLabel(e.wallet), e.kind, e.amount.toFixed(2),
      e.ref_table && e.ref_id != null ? `${e.ref_table}/${e.ref_id}` : '',
      e.note ?? '',
    ].map(esc).join(','))
  }
  return lines.join('\r\n') + '\r\n'
}

function kindLabel(k: string): string {
  return k.charAt(0).toUpperCase() + k.slice(1)
}

const NO_FILTER = { from: '', to: '', kind: '' }

export default function InvestorTransactions() {
  const { orgId } = useOrg()
  const base = `/org/${orgId}/invest`
  const [wallet, setWallet] = useState('all')
  const [draft, setDraft] = useState(NO_FILTER)
  const [applied, setApplied] = useState(NO_FILTER)
  const [rows, setRows] = useState<WalletEntry[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [nextBefore, setNextBefore] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(async (before: number | null) => {
    setLoading(true)
    try {
      const page = await orgApi<WalletEntriesPage>(orgId, entriesQuery({ wallet, ...applied, before }))
      setRows((r) => (before == null ? page.entries : [...r, ...page.entries]))
      setHasMore(page.has_more)
      setNextBefore(page.next_before)
      setError(null)
    } catch (err) {
      setError(errorText(err, 'Could not load your transactions'))
    } finally {
      setLoading(false)
      setLoaded(true)
    }
  }, [orgId, wallet, applied])

  useEffect(() => { load(null) }, [load])

  const apply = (e: React.FormEvent) => { e.preventDefault(); setApplied(draft) }
  const clear = () => { setDraft(NO_FILTER); setApplied(NO_FILTER) }

  const download = () => {
    const blob = new Blob([toCsv(rows)], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'transactions.csv'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  const tabs = [{ key: 'all', label: 'All' }, ...WALLETS.map((w) => ({ key: w, label: walletLabel(w) }))]

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Transactions"
        subtitle="Every movement on your wallets: deposits, withdrawals, transfers and adjustments."
        actions={<Button variant="secondary" size="sm" onClick={download} disabled={rows.length === 0}>Download CSV</Button>}
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}

      <Card>
        <Tabs items={tabs} value={wallet} onChange={setWallet} label="Wallet" idBase="wallet" />
        <div id="wallet-panel" role="tabpanel" aria-labelledby={`wallet-tab-${wallet}`} className="mt-4 space-y-4">
          <form onSubmit={apply} noValidate className="flex flex-wrap items-end gap-3">
            <label className="block">
              <span className="desk-label block mb-1">From date</span>
              <Input aria-label="From date" type="date" value={draft.from}
                     onChange={(e) => setDraft({ ...draft, from: e.target.value })} />
            </label>
            <label className="block">
              <span className="desk-label block mb-1">To date</span>
              <Input aria-label="To date" type="date" value={draft.to}
                     onChange={(e) => setDraft({ ...draft, to: e.target.value })} />
            </label>
            <label className="block">
              <span className="desk-label block mb-1">Kind</span>
              <Select aria-label="Kind" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>
                <option value="">All kinds</option>
                {KINDS.map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}
              </Select>
            </label>
            <Button type="submit" variant="secondary" size="sm">Apply</Button>
            <Button variant="ghost" size="sm" onClick={clear}>Clear</Button>
          </form>

          {!loaded ? <Loading lines={4} /> : (
            <div className="inset overflow-x-auto">
              <table className="stack-table w-full text-sm">
                <thead>
                  <tr className="text-left border-b border-line">
                    <th className="desk-label px-4 py-2 font-semibold">Date</th>
                    <th className="desk-label px-4 py-2 font-semibold">Wallet</th>
                    <th className="desk-label px-4 py-2 font-semibold">Kind</th>
                    <th className="desk-label px-4 py-2 font-semibold text-right">Amount</th>
                    <th className="desk-label px-4 py-2 font-semibold">Reference</th>
                    <th className="desk-label px-4 py-2 font-semibold">Note</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 && (
                    <tr><td colSpan={6} className="text-center py-8 text-ink-faint">No transactions yet</td></tr>
                  )}
                  {rows.map((e) => {
                    const page = e.ref_table ? REF_PAGE[e.ref_table] : undefined
                    return (
                      <tr key={e.id} className="border-b border-line last:border-0">
                        <td data-label="Date" className="num px-4 py-2.5">{formatWhen(e.created_at)}</td>
                        <td data-label="Wallet" className="px-4 py-2.5 text-ink">{walletLabel(e.wallet)}</td>
                        <td data-label="Kind" className="px-4 py-2.5">{kindLabel(e.kind)}</td>
                        <td data-label="Amount" className="px-4 py-2.5 text-right">
                          <Money value={e.amount} unit={e.currency} signed
                                 className={e.amount < 0 ? 'text-loss' : 'text-profit'} />
                        </td>
                        <td data-label="Reference" className="px-4 py-2.5">
                          {page
                            ? <Button variant="ghost" size="sm" to={`${base}/${page}`}>{entryLabel(e)}</Button>
                            : <span className="text-ink-soft">{entryLabel(e)}</span>}
                        </td>
                        <td data-label="Note" className="px-4 py-2.5 text-ink-soft">{e.note ?? '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {hasMore && (
            <div className="flex justify-center">
              <Button variant="secondary" onClick={() => load(nextBefore)} busy={loading}>Load more</Button>
            </div>
          )}
        </div>
      </Card>
    </div>
  )
}
```

- [ ] **Step 4: Run the Transactions test to verify it passes**

Run: `npx vitest run src/pages/investor/InvestorTransactions.test.tsx`
Expected: PASS, 8 passed.

- [ ] **Step 5: Write the failing Account test**

Replace the whole of `dashboard/src/pages/investor/InvestorAccount.test.tsx` with:

```tsx
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import InvestorAccount from './InvestorAccount'
import { mockUseOrg } from '../../test/orgMock'
import { summaryFixture } from '../../test/portalFixtures'
import type { InvestorSummary } from '../../lib/types'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../../lib/org', () => ({ useOrg: useOrgMock }))
const facts = vi.hoisted(() => ({ legalName: 'MirrorFleet', address: '', supportEmail: '' }))
vi.mock('../Landing', () => ({ LANDING_FACTS: facts }))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const linked: InvestorSummary = {
  ...summaryFixture(), currency: 'USD', link_state: 'linked',
  account: { account_id: 1001, nickname: 'Inv', platform: 'mt5', status: 'ok', last_error: null, connected: true },
  equity_source: 'live', equity: 5120.5, net_funded: 5000, profit: 120.5, account_available: 5120.5, open_positions: 1,
}
const unlinked: InvestorSummary = {
  ...linked, link_state: 'unlinked', account: null, equity_source: 'unknown',
  equity: null, profit: null, account_available: null, open_positions: 0,
}

function mockRoutes(summary: InvestorSummary) {
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
    return jsonResponse({})
  }))
}

beforeEach(() => { useOrgMock.mockReturnValue(mockUseOrg('investor')) })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); facts.supportEmail = '' })

test('the Account page shows who you are, the login forms and its title', async () => {
  mockRoutes(linked)
  // AccountSecurity calls useNavigate, so the page needs a router.
  render(<MemoryRouter><InvestorAccount /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Account' })).toBeInTheDocument()
  expect(screen.getByText('Test User')).toBeInTheDocument()
  expect(screen.getByText('user@example.com')).toBeInTheDocument()
  expect(screen.getByText('Acme')).toBeInTheDocument()
  expect(screen.getByText('Investor')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Your login' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Change MPIN' })).toBeInTheDocument()
  expect(document.title).toBe('Account · MirrorFleet')
  await screen.findByText('XAUUSD')
})

test('linked investors see the trading account, open positions and the 4-week snapshot', async () => {
  mockRoutes(linked)
  render(<MemoryRouter><InvestorAccount /></MemoryRouter>)
  expect(await screen.findByText('XAUUSD')).toBeInTheDocument()
  expect(screen.getByText('Inv')).toBeInTheDocument()
  expect(screen.getAllByText('5,120.50 USD').length).toBeGreaterThan(0)
  expect(screen.getAllByText('120.50 USD').length).toBeGreaterThan(0)
  expect(screen.getByText(/66\.7%/)).toBeInTheDocument()
  expect(screen.getByRole('img', { name: 'Equity curve' })).toBeInTheDocument()
  expect(screen.getByText('Live P&L (quote currency)')).toBeInTheDocument()
})

test('unlinked investors see the setup notice instead of positions', async () => {
  mockRoutes(unlinked)
  render(<MemoryRouter><InvestorAccount /></MemoryRouter>)
  expect(await screen.findByText(/your account is being set up/i)).toBeInTheDocument()
  expect(screen.queryByText('XAUUSD')).not.toBeInTheDocument()
  expect(screen.queryByText(/66\.7%/)).not.toBeInTheDocument()
  expect(screen.queryByText(/questions\?/i)).not.toBeInTheDocument()
})
```

- [ ] **Step 6: Run it to verify it fails**

Run: `npx vitest run src/pages/investor/InvestorAccount.test.tsx`
Expected: FAIL — the first and third tests fail on `Unable to find an element with the text: XAUUSD` / `/your account is being set up/i` (the page fetches nothing yet); the second fails the same way.

- [ ] **Step 7: Extend the Account page**

Replace the whole of `dashboard/src/pages/investor/InvestorAccount.tsx` with:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { useOrg } from '../../lib/org'
import { errorText, money, signed } from '../../lib/format'
import { ACCOUNT_CURRENCY } from '../../lib/investor'
import { useHiddenBalances } from '../../lib/hideBalances'
import AccountSecurity from '../../components/AccountSecurity'
import Banner from '../../components/Banner'
import Card from '../../components/Card'
import Loading from '../../components/Loading'
import Money from '../../components/Money'
import PageHeader from '../../components/PageHeader'
import StatTile from '../../components/StatTile'
import { EquityCurve } from '../../components/charts'
import NextStep from './NextStep'
import type { Analytics, InvestorPositions, InvestorSummary } from '../../lib/types'

const POLL_MS = 10000

export default function InvestorAccount() {
  const { me, org, orgId } = useOrg()
  const [summary, setSummary] = useState<InvestorSummary | null>(null)
  const [positions, setPositions] = useState<InvestorPositions | null>(null)
  const [analytics, setAnalytics] = useState<Analytics | null>(null)
  // Spec section 11: every money figure honours the hide-balances toggle.
  // <Money> does it itself; the StatTile strings below do it by hand.
  const [hidden] = useHiddenBalances()
  const [error, setError] = useState<string | null>(null)
  // True once the first load has settled, success or failure: the skeleton
  // is for "not asked yet", never for "asked and failed".
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const s = await orgApi<InvestorSummary>(orgId, 'investor/summary')
      setSummary(s)
      if (s.link_state === 'linked') {
        const [p, a] = await Promise.all([
          orgApi<InvestorPositions>(orgId, 'investor/positions'),
          orgApi<Analytics>(orgId, 'investor/analytics?weeks=4'),
        ])
        setPositions(p); setAnalytics(a)
      } else {
        setPositions(null); setAnalytics(null)
      }
      setError(null)
    } catch (err) {
      setError(errorText(err, 'Could not load your account'))
    }
    setLoaded(true)
  }, [orgId])

  useEffect(() => {
    refresh()
    const id = window.setInterval(refresh, POLL_MS)
    return () => window.clearInterval(id)
  }, [refresh])

  const unit = summary?.currency ?? ACCOUNT_CURRENCY

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader title="Account" />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}

      <Card title="Profile">
        <dl className="grid gap-3 md:grid-cols-2 text-sm">
          <div><dt className="desk-label">Name</dt><dd className="text-ink">{me.user.display_name}</dd></div>
          <div><dt className="desk-label">Email</dt><dd className="text-ink">{me.user.email}</dd></div>
          <div><dt className="desk-label">Workspace</dt><dd className="text-ink">{org.name}</dd></div>
          <div><dt className="desk-label">Role</dt><dd className="text-ink">Investor</dd></div>
        </dl>
      </Card>

      {!loaded && <Loading lines={3} />}

      {summary && summary.link_state !== 'linked' && (
        <NextStep title="Your account is being set up">
          Your admin links your trading account; positions and performance appear here once it is linked.
        </NextStep>
      )}

      {summary?.account && (
        <Card title="Trading account">
          <dl className="inset p-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 text-sm">
            <div><dt className="desk-label">Account</dt>
              <dd className="text-ink">{summary.account.nickname ?? summary.account.account_id}</dd></div>
            <div><dt className="desk-label">Platform</dt>
              <dd className="text-ink uppercase">{summary.account.platform}</dd></div>
            <div><dt className="desk-label">Connection</dt>
              <dd className={summary.account.connected ? 'text-profit' : 'text-warn-deep'}>
                {summary.account.connected ? 'connected' : 'terminal offline'}
              </dd></div>
            <div><dt className="desk-label">Open positions</dt>
              <dd className="num text-ink">{summary.open_positions}</dd></div>
            <div><dt className="desk-label">Equity</dt>
              <dd className="text-ink"><Money value={summary.equity} unit={unit} />
                <span className="text-xs text-ink-soft"> {summary.equity_source}</span></dd></div>
            <div><dt className="desk-label">Net funded</dt>
              <dd className="text-ink"><Money value={summary.net_funded} unit={unit} /></dd></div>
            <div><dt className="desk-label">Profit</dt>
              <dd className={summary.profit != null && summary.profit < 0 ? 'text-loss' : 'text-profit'}>
                <Money value={summary.profit} unit={unit} /></dd></div>
            <div><dt className="desk-label">Available to move</dt>
              <dd className="text-ink"><Money value={summary.account_available} unit={unit} /></dd></div>
          </dl>
        </Card>
      )}

      {positions && (
        <Card title="Open positions" inset
              actions={<span className="text-xs text-ink-soft">{positions.equity_source}</span>}>
          <div className="overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-4 py-2 font-semibold">Symbol</th>
                  <th className="desk-label px-4 py-2 font-semibold">Side</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Volume</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Entry</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">Current</th>
                  <th className="desk-label px-4 py-2 font-semibold text-right">SL / TP</th>
                  {/* pnl_quote is in each symbol's quote currency, which is
                      not always the account currency, so the header says so. */}
                  <th className="desk-label px-4 py-2 font-semibold text-right">Live P&L (quote currency)</th>
                </tr>
              </thead>
              <tbody>
                {positions.positions.length === 0 && (
                  <tr><td colSpan={7} className="text-center py-8 text-ink-faint">No open positions</td></tr>
                )}
                {positions.positions.map((p) => (
                  <tr key={p.position_id} className="border-b border-line last:border-0">
                    <td data-label="Symbol" className="px-4 py-2.5 text-ink">{p.symbol ?? '—'}</td>
                    <td data-label="Side" className="px-4 py-2.5">{p.side}</td>
                    <td data-label="Volume" className="tnum px-4 py-2.5 text-right">{p.volume}</td>
                    <td data-label="Entry" className="tnum px-4 py-2.5 text-right">{p.entry_price ?? '—'}</td>
                    <td data-label="Current" className="tnum px-4 py-2.5 text-right">{p.current_price ?? '—'}</td>
                    <td data-label="SL / TP" className="tnum px-4 py-2.5 text-right">{p.stop_loss ?? '—'} / {p.take_profit ?? '—'}</td>
                    <td data-label="Live P&L (quote currency)" className={`tnum px-4 py-2.5 text-right ${(p.pnl_quote ?? 0) < 0 ? 'text-loss' : 'text-profit'}`}>
                      {p.pnl_quote == null ? '—' : signed(p.pnl_quote)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {analytics && (
        <Card title={`Performance, last ${analytics.weeks} weeks`}
              actions={<span className="text-xs text-ink-soft">{analytics.closed_trades} closed trades</span>}>
          <div className="space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <StatTile label="Net P&L" value={hidden ? '••••' : money(analytics.net_pnl, unit)}
                        tone={analytics.net_pnl < 0 ? 'loss' : 'profit'} />
              <StatTile label="Win rate"
                        value={analytics.win_rate == null ? '—' : `${analytics.win_rate.toFixed(1)}%`}
                        sub={`${analytics.wins} won · ${analytics.losses} lost`} />
              <StatTile label="Max drawdown" value={hidden ? '••••' : money(analytics.max_drawdown, unit)} tone="loss"
                        sub={`${analytics.max_drawdown_pct.toFixed(1)}% from peak`} />
              <StatTile label="Profit factor"
                        value={analytics.profit_factor == null ? '—' : analytics.profit_factor.toFixed(2)} />
            </div>
            {analytics.equity_curve.length > 1 && (
              <div className="inset p-3">
                <EquityCurve points={analytics.equity_curve} height={160} />
              </div>
            )}
          </div>
        </Card>
      )}

      <AccountSecurity />
    </div>
  )
}
```

- [ ] **Step 8: Run the Account test to verify it passes**

Run: `npx vitest run src/pages/investor/InvestorAccount.test.tsx`
Expected: PASS, 3 passed.

- [ ] **Step 9: Route Transactions**

In `dashboard/src/pages/groups/investor.ts` add, after the `InvestorWallet` line:

```ts
export { default as InvestorTransactions } from '../investor/InvestorTransactions'
```

The barrel now reads, in full:

```ts
export { default as InvestorDashboard } from '../investor/InvestorDashboard'
export { default as InvestorDeposit } from '../investor/InvestorDeposit'
export { default as InvestorWithdraw } from '../investor/InvestorWithdraw'
export { default as InvestorPayoutAccounts } from '../investor/InvestorPayoutAccounts'
export { default as InvestorTransfer } from '../investor/InvestorTransfer'
export { default as InvestorWallet } from '../investor/InvestorWallet'
export { default as InvestorTransactions } from '../investor/InvestorTransactions'
export { default as InvestorHistory } from '../investor/InvestorHistory'
export { default as InvestorAccount } from '../investor/InvestorAccount'
```

In `dashboard/src/App.tsx` add, after `const InvestorWallet = pick(investor, 'InvestorWallet')`:

```tsx
const InvestorTransactions = pick(investor, 'InvestorTransactions')
```

and, after the `invest/wallet` route line:

```tsx
              <Route path="invest/transactions" element={<InvestorTransactions />} />
```

The investor routes in `App.tsx` now read, in full:

```tsx
              <Route path="invest" element={<InvestorDashboard />} />
              <Route path="invest/deposit" element={<InvestorDeposit />} />
              <Route path="invest/withdraw" element={<InvestorWithdraw />} />
              <Route path="invest/payout-accounts" element={<InvestorPayoutAccounts />} />
              <Route path="invest/transfer" element={<InvestorTransfer />} />
              <Route path="invest/wallet" element={<InvestorWallet />} />
              <Route path="invest/transactions" element={<InvestorTransactions />} />
              <Route path="invest/history" element={<InvestorHistory />} />
              <Route path="invest/account" element={<InvestorAccount />} />
```

- [ ] **Step 10: Full dashboard gate**

Run (from `dashboard/`): `npm test`
Expected: prover ALL PASS, `tsc` clean, every vitest file green — including `vocabulary.test.ts` (no `<h1` outside PageHeader, no literal "Loading...", no "Slave" in any of the seven investor pages) and every colocated investor test: `InvestorDashboard` 12, `InvestorDeposit` 11, `InvestorWithdraw` 14, `InvestorPayoutAccounts` 7, `InvestorTransfer` 9, `InvestorWallet` 2, `InvestorTransactions` 8, `InvestorAccount` 3, `InvestorHistory` unchanged.

- [ ] **Step 11: Commit**

```bash
git add dashboard/src/pages/investor/InvestorTransactions.tsx dashboard/src/pages/investor/InvestorTransactions.test.tsx dashboard/src/pages/investor/InvestorAccount.tsx dashboard/src/pages/investor/InvestorAccount.test.tsx dashboard/src/pages/groups/investor.ts dashboard/src/App.tsx
git commit -m "feat(dashboard): Transactions page with wallet tabs, filters, paging and CSV; Account page gains the trading account card, open positions and the 4-week snapshot

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---


### Task 18: Investors page rewrite — wallet figures, ledger drawer, MPIN-gated adjustments, payment methods

The admin's Investors page becomes two tabs. *Investors* lists every investor with
their `main` wallet figures, equity, the linked-account Select (unchanged), open-request
chips that link into the Requests desk, and a row Menu with **View ledger** (a Drawer
over `investors/{user_id}/wallet-entries` with a wallet filter and Load more) and
**Adjust balance** (`PinConfirmDialog` posting `investors/{user_id}/adjustments`).
*Payment methods* lists the org's methods with Enable/Disable, Add/Edit (Drawer per
kind) and Delete (ConfirmDialog), plus the Withdrawal settings card with the
dirty-guard pattern the old wallet card used. The money-request tables leave this page;
Task 19 gives them the Requests desk.

Whatever Task 11 left in `pages/Investors.tsx` and `pages/Investors.test.tsx` (import
renames or stubs) is replaced wholesale here: both files are rewritten from scratch.

**Files:**
- Create: `dashboard/src/pages/investors/PaymentMethodsTab.tsx`
- Create: `dashboard/src/pages/investors/LedgerDrawer.tsx`
- Create: `dashboard/src/pages/investors/AdjustDialog.tsx`
- Modify: `dashboard/src/pages/Investors.tsx` (full rewrite)
- Modify: `dashboard/src/pages/Investors.test.tsx` (full rewrite)
- Test: `dashboard/src/pages/Investors.test.tsx`

**Interfaces:**
- Consumes: `InvestorRow`, `Account`, `PaymentMethod`, `PortalSettings`, `WalletEntry`, `WalletEntriesPage`, `WalletKind` (`lib/types.ts`, Task 11); `WALLETS`, `walletLabel`, `entryLabel`, `moneyOrDash`, `shortAddress` (`lib/investor.ts`, Task 11); `PinConfirmDialog` (Task 11); `investorRowFixture`, `methodFixture`, `entryFixture` (`src/test/portalFixtures.ts`, Task 11); `api`, `orgApi`, `eventsSocket` (`lib/api.ts`); `useLiveRefresh`; `money`, `signed`, `formatWhen`, `errorText` (`lib/format.ts`); primitives `Badge`, `Banner`, `Button`, `Card`, `ConfirmDialog`, `Drawer`, `Input`, `Loading`, `Menu`, `PageHeader`, `Select`, `Tabs`; API routes `GET investors`, `GET accounts`, `PUT investors/{user_id}/account`, `GET investors/{user_id}/wallet-entries?wallet=&limit=&before=`, `POST investors/{user_id}/adjustments`, `GET/POST payment-methods`, `PATCH/DELETE payment-methods/{id}`, `GET/PUT portal-settings` (Tasks 6 and 10).
- Produces: `Runner` type (exported from `PaymentMethodsTab.tsx`), `DETAIL_FIELDS`, `methodSummary` (same file); the open-request chip links `/org/{orgId}/requests?tab=<kind>` that Task 19's Requests page honours.

- [ ] **Step 1: Write the failing test**

Replace the whole of `dashboard/src/pages/Investors.test.tsx` with:

```tsx
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import Investors from './Investors'
import * as apiModule from '../lib/api'
import { mockUseOrg } from '../test/orgMock'
import { entryFixture, investorRowFixture, methodFixture } from '../test/portalFixtures'

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

const investor = investorRowFixture({
  user_id: 5, email: 'inv@example.com', display_name: 'Ada Investor',
  joined_at: '2026-09-01T00:00:00Z', account_id: 1001, nickname: 'Inv',
  equity: 6000, equity_source: 'live',
  balances: { main: 5120.5, credit: 0, pamm: 0, social: 0 }, on_hold: 100, available: 5020.5,
  pending: { deposits: 2, withdrawals: 1, transfers: 0, payout_destinations: 0 },
})
const usdt = methodFixture({
  id: 3, kind: 'crypto', label: 'USDT on TRC20', enabled: true, currency: 'USD',
  details: { coin: 'USDT', network: 'TRC20', address: 'TXYZ1234567890abcdef' },
  min_amount: 10, fee_pct: 0, instructions: null, sort_order: 0,
})
const entry = entryFixture({
  id: 900, wallet: 'main', amount: 5000, kind: 'deposit', ref_table: 'deposits', ref_id: 11,
  note: null, created_at: '2026-09-20T10:00:00Z', currency: 'USD',
})
const older = entryFixture({
  id: 899, wallet: 'main', amount: -25, kind: 'adjustment', ref_table: null, ref_id: null,
  note: 'Correction', created_at: '2026-09-19T10:00:00Z', currency: 'USD',
})

function mockRoutes(options: { settings?: unknown[] } = {}) {
  const queue = options.settings ? [...options.settings] : [{ withdrawal_min: 0, withdrawal_fee_pct: 0 }]
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    const path = url.split('?')[0]
    if (path.endsWith('/investors')) return jsonResponse([investor])
    if (path.endsWith('/accounts')) {
      return jsonResponse([{ ctid_trader_account_id: 1001, trader_login: 1001, is_live: false,
        role: 'slave', enabled: true, multiplier: 1, status: 'ok', connection_status: 'active' },
        { ctid_trader_account_id: 1002, trader_login: 1002, is_live: false, role: 'slave',
          enabled: true, multiplier: 1, status: 'ok', connection_status: 'active' }])
    }
    if (path.endsWith('/payment-methods') && method === 'GET') return jsonResponse([usdt])
    if (path.endsWith('/payment-methods') && method === 'POST') {
      return jsonResponse({ ...usdt, id: 4, ...JSON.parse(init!.body as string) }, 201)
    }
    if (path.includes('/payment-methods/') && method === 'PATCH') {
      return jsonResponse({ ...usdt, ...JSON.parse(init!.body as string) })
    }
    if (path.includes('/payment-methods/') && method === 'DELETE') return new Response(null, { status: 204 })
    if (path.endsWith('/portal-settings') && method === 'GET') {
      return jsonResponse(queue.length > 1 ? queue.shift() : queue[0])
    }
    if (path.endsWith('/portal-settings')) return jsonResponse(JSON.parse(init!.body as string))
    if (path.endsWith('/investors/5/wallet-entries')) {
      if (url.includes('before=')) return jsonResponse({ entries: [older], has_more: false, next_before: null })
      return jsonResponse({ entries: [entry], has_more: true, next_before: 900 })
    }
    if (path.endsWith('/investors/5/adjustments')) return jsonResponse({ ...older, id: 901 }, 201)
    if (path.endsWith('/investors/5/account')) return jsonResponse({ user_id: 5, account_id: null })
    return jsonResponse({})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const bodyOf = (fetchMock: ReturnType<typeof vi.fn>, fragment: string, method: string) => {
  const call = fetchMock.mock.calls.find(([u, init]) =>
    String(u).includes(fragment) && (init as RequestInit)?.method === method)
  return JSON.parse((call![1] as RequestInit).body as string)
}

function rowFor(text: string): HTMLElement {
  const row = screen.getAllByRole('row').find((r) => r.textContent?.includes(text))
  if (!row) throw new Error(`no row contains ${text}`)
  return row
}

async function chooseFromMenu(rowText: string, item: string) {
  await userEvent.click(within(rowFor(rowText)).getByRole('button', { name: 'Actions for inv@example.com' }))
  const menu = await screen.findByRole('menu')
  await userEvent.click(within(menu).getByRole('menuitem', { name: item }))
}

beforeEach(() => {
  vi.spyOn(apiModule, 'eventsSocket').mockImplementation(() => new MockWebSocket() as never)
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

test('shows the heading and a loading state, then the investors with their wallet figures', async () => {
  mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  expect(screen.getByRole('heading', { level: 1, name: 'Investors' })).toBeInTheDocument()
  expect(screen.getByRole('status', { name: 'Loading investors' })).toBeInTheDocument()
  expect(screen.queryByText(/No investors yet/)).not.toBeInTheDocument()
  expect(await screen.findByText('Ada Investor')).toBeInTheDocument()
  await waitFor(() => expect(document.title).toBe('Investors · MirrorFleet'))
  expect(screen.queryByRole('status', { name: 'Loading investors' })).not.toBeInTheDocument()
  expect(screen.getByText('5,120.50')).toBeInTheDocument()
  expect(screen.getByText('on hold 100.00 · available 5,020.50')).toBeInTheDocument()
  expect(screen.getByText('6,000.00')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '2 deposits' })).toHaveAttribute('href', '/org/1/requests?tab=deposits')
  expect(screen.getByRole('link', { name: '1 withdrawal' })).toHaveAttribute('href', '/org/1/requests?tab=withdrawals')
  expect(screen.queryByRole('link', { name: /transfer/ })).not.toBeInTheDocument()
})

test('links an account from the row select', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.selectOptions(screen.getByLabelText('Account for inv@example.com'), '1002')
  await waitFor(() => expect(bodyOf(fetchMock, '/investors/5/account', 'PUT')).toEqual({ account_id: 1002 }))
  expect(await screen.findByText('Account linked')).toBeInTheDocument()
})

test('View ledger opens the drawer, loads more and filters by wallet', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await chooseFromMenu('Ada Investor', 'View ledger')
  const drawer = await screen.findByRole('dialog', { name: "Ada Investor's ledger" })
  expect(await within(drawer).findByText('Deposit #11')).toBeInTheDocument()
  expect(within(drawer).getByText('+5,000.00')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) =>
    String(u).endsWith('/investors/5/wallet-entries?limit=50'))).toBe(true)

  await userEvent.click(within(drawer).getByRole('button', { name: 'Load more' }))
  expect(await within(drawer).findByText('Correction')).toBeInTheDocument()
  expect(within(drawer).getByText('-25.00')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) =>
    String(u).endsWith('/investors/5/wallet-entries?limit=50&before=900'))).toBe(true)
  // The last page is in: nothing more to load.
  expect(within(drawer).queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument()

  await userEvent.selectOptions(within(drawer).getByLabelText('Wallet'), 'pamm')
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) =>
    String(u).endsWith('/investors/5/wallet-entries?limit=50&wallet=pamm'))).toBe(true))
})

test('Adjust balance posts a signed entry with the admin MPIN', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await chooseFromMenu('Ada Investor', 'Adjust balance')
  const dialog = await screen.findByRole('dialog', { name: "Adjust Ada Investor's wallet" })
  await userEvent.type(within(dialog).getByLabelText('Amount'), '-25.00')
  await userEvent.type(within(dialog).getByLabelText('Note'), 'Correction')
  const post = within(dialog).getByRole('button', { name: 'Post adjustment' })
  expect(post).toBeDisabled()
  within(dialog).getByLabelText('Your MPIN digit 1 of 6').focus()
  await userEvent.keyboard('123456')
  expect(post).toBeEnabled()
  await userEvent.click(post)
  await waitFor(() => expect(bodyOf(fetchMock, '/investors/5/adjustments', 'POST'))
    .toEqual({ wallet: 'main', amount: '-25.00', note: 'Correction', mpin: '123456' }))
  expect(await screen.findByText('Adjustment of -25.00 posted to My wallet')).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('Payment methods tab lists the methods and disables one', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Payment methods' }))
  expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'investors-tab-methods')
  expect(screen.getByText('USDT on TRC20')).toBeInTheDocument()
  expect(screen.getByText('USDT · TRC20 · T…ef')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Disable USDT on TRC20' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/payment-methods/3', 'PATCH')).toEqual({ enabled: false }))
  expect(await screen.findByText('Method disabled')).toBeInTheDocument()
})

test('adds a bank method from the drawer', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Payment methods' }))
  await userEvent.click(screen.getByRole('button', { name: 'Add method' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add payment method' })
  await userEvent.selectOptions(within(drawer).getByLabelText('Kind'), 'bank')
  await userEvent.type(within(drawer).getByLabelText('Label'), 'ICICI Bank')
  await userEvent.type(within(drawer).getByLabelText('Bank name'), 'ICICI')
  await userEvent.type(within(drawer).getByLabelText('Account holder'), 'MirrorFleet Ltd')
  await userEvent.type(within(drawer).getByLabelText('Account number'), '000123456789')
  await userEvent.type(within(drawer).getByLabelText('SWIFT / IFSC code'), 'ICIC0000001')
  await userEvent.clear(within(drawer).getByLabelText('Minimum deposit'))
  await userEvent.type(within(drawer).getByLabelText('Minimum deposit'), '500')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save method' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/payment-methods', 'POST')).toEqual({
    kind: 'bank', label: 'ICICI Bank', currency: 'USD',
    details: { bank_name: 'ICICI', holder: 'MirrorFleet Ltd', account_number: '000123456789', code: 'ICIC0000001' },
    min_amount: '500', fee_pct: '0', instructions: null, sort_order: 0,
  }))
  expect(await screen.findByText('Payment method added')).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('a missing required detail is refused before anything is sent', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Payment methods' }))
  await userEvent.click(screen.getByRole('button', { name: 'Add method' }))
  const drawer = await screen.findByRole('dialog', { name: 'Add payment method' })
  await userEvent.type(within(drawer).getByLabelText('Label'), 'USDT on BEP20')
  await userEvent.click(within(drawer).getByRole('button', { name: 'Save method' }))
  expect(await within(drawer).findByRole('alert')).toHaveTextContent('Coin is required')
  expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit)?.method === 'POST')).toBe(false)
})

test('deleting a method asks first', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Payment methods' }))
  await userEvent.click(screen.getByRole('button', { name: 'Delete USDT on TRC20' }))
  const dialog = await screen.findByRole('dialog', { name: 'Delete USDT on TRC20?' })
  await userEvent.click(within(dialog).getByRole('button', { name: 'Delete method' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([u, init]) =>
    String(u).endsWith('/payment-methods/3') && (init as RequestInit)?.method === 'DELETE')).toBe(true))
  expect(await screen.findByText('Payment method deleted')).toBeInTheDocument()
})

test('saves the withdrawal settings', async () => {
  const fetchMock = mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  await userEvent.click(screen.getByRole('tab', { name: 'Payment methods' }))
  const save = screen.getByRole('button', { name: 'Save withdrawal settings' })
  expect(save).toBeDisabled()
  await userEvent.clear(screen.getByLabelText('Minimum withdrawal'))
  await userEvent.type(screen.getByLabelText('Minimum withdrawal'), '25')
  await userEvent.clear(screen.getByLabelText('Withdrawal fee %'))
  await userEvent.type(screen.getByLabelText('Withdrawal fee %'), '1.5')
  expect(save).toBeEnabled()
  await userEvent.click(save)
  await waitFor(() => expect(bodyOf(fetchMock, '/portal-settings', 'PUT'))
    .toEqual({ withdrawal_min: '25', withdrawal_fee_pct: '1.5' }))
  expect(await screen.findByText('Withdrawal settings saved')).toBeInTheDocument()
})

test('the withdrawal settings follow the server while the form is untouched', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  mockRoutes({ settings: [
    { withdrawal_min: 0, withdrawal_fee_pct: 0 },
    { withdrawal_min: 50, withdrawal_fee_pct: 1 },
  ] })
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  fireEvent.click(screen.getByRole('tab', { name: 'Payment methods' }))
  expect(screen.getByLabelText('Minimum withdrawal')).toHaveValue('0')
  await vi.advanceTimersByTimeAsync(10000)
  await waitFor(() => expect(screen.getByLabelText('Minimum withdrawal')).toHaveValue('50'))
  expect(screen.getByLabelText('Withdrawal fee %')).toHaveValue('1')
})

test('a viewer sees the figures but no actions', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('viewer'))
  mockRoutes()
  render(<MemoryRouter><Investors /></MemoryRouter>)
  await screen.findByText('Ada Investor')
  expect(screen.queryByRole('button', { name: 'Actions for inv@example.com' })).not.toBeInTheDocument()
  expect(screen.getByText('Inv')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: 'Payment methods' }))
  expect(screen.queryByRole('button', { name: 'Add method' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Save withdrawal settings' })).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run it to verify it fails**

Run (from `dashboard/`): `npx vitest run src/pages/Investors.test.tsx`
Expected: FAIL — the Task 11 version of `Investors.tsx` still compiles and renders (it imports nothing from `./investors/`, so no module-resolution error is possible yet; only the `../test/portalFixtures` import must resolve, and it does), and the tests fail on missing DOM: `Unable to find role="tab" and name "Payment methods"`, `Unable to find role="button" and name "Actions for inv@example.com"`, `Unable to find an element with the text: 5,120.50`. The first test's heading and `Loading investors` assertions may pass; every test that reaches a tab, a row menu or a wallet figure must be red.

- [ ] **Step 3: Write `PaymentMethodsTab.tsx`**

Create `dashboard/src/pages/investors/PaymentMethodsTab.tsx`:

```tsx
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { orgApi } from '../../lib/api'
import { errorText, money } from '../../lib/format'
import { shortAddress } from '../../lib/investor'
import Badge from '../../components/Badge'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Card from '../../components/Card'
import ConfirmDialog from '../../components/ConfirmDialog'
import Drawer from '../../components/Drawer'
import Input from '../../components/Input'
import Select from '../../components/Select'
import type { PaymentMethod, PortalSettings } from '../../lib/types'

/** The action envelope the Investors page owns (busy flag, error and
 *  notice banners, then a refresh); the tab borrows it for its mutations. */
export type Runner = (fn: () => Promise<void>, done: string) => Promise<void>

type Kind = PaymentMethod['kind']

interface DetailField { key: string; label: string; optional?: boolean }

/** The detail keys the server requires per kind (portal_admin MethodBody:
 *  crypto coin/network/address, bank bank_name/holder/account_number/code),
 *  in the order the form shows them. */
export const DETAIL_FIELDS: Record<Kind, DetailField[]> = {
  crypto: [
    { key: 'coin', label: 'Coin' },
    { key: 'network', label: 'Network' },
    { key: 'address', label: 'Address' },
    { key: 'memo', label: 'Memo', optional: true },
  ],
  bank: [
    { key: 'bank_name', label: 'Bank name' },
    { key: 'holder', label: 'Account holder' },
    { key: 'account_number', label: 'Account number' },
    { key: 'code', label: 'SWIFT / IFSC code' },
    { key: 'bank_address', label: 'Bank address', optional: true },
    { key: 'country', label: 'Country', optional: true },
  ],
}

interface MethodForm {
  kind: Kind
  label: string
  currency: string
  min_amount: string
  fee_pct: string
  instructions: string
  sort_order: string
  details: Record<string, string>
}

const EMPTY_FORM: MethodForm = {
  kind: 'crypto', label: '', currency: 'USD', min_amount: '0', fee_pct: '0',
  instructions: '', sort_order: '0', details: {},
}

function formOf(m: PaymentMethod): MethodForm {
  return {
    kind: m.kind, label: m.label, currency: m.currency,
    min_amount: String(m.min_amount), fee_pct: String(m.fee_pct),
    instructions: m.instructions ?? '', sort_order: String(m.sort_order),
    details: { ...m.details },
  }
}

/** One line under the label: "USDT · TRC20 · T…ef" or "ICICI Bank ••4543". */
export function methodSummary(m: PaymentMethod): string {
  if (m.kind === 'crypto') {
    return [m.details.coin, m.details.network, m.details.address ? shortAddress(m.details.address) : '']
      .filter(Boolean).join(' · ')
  }
  const acct = m.details.account_number ?? ''
  return `${m.details.bank_name ?? ''} ••${acct.slice(-4)}`
}

type Editing = { mode: 'add' } | { mode: 'edit'; method: PaymentMethod }

const TEXTAREA = 'w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink'

export default function PaymentMethodsTab({ orgId, control, methods, settings, busy, run }: {
  orgId: number
  control: boolean
  methods: PaymentMethod[]
  /** Null until the page's first load lands. */
  settings: PortalSettings | null
  busy: boolean
  run: Runner
}) {
  const [editing, setEditing] = useState<Editing | null>(null)
  const [form, setForm] = useState<MethodForm>(EMPTY_FORM)
  const [formError, setFormError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState<PaymentMethod | null>(null)

  // Withdrawal settings: the same dirty guard the old wallet card had. Once
  // the admin touches the form, the 10 s poll leaves it alone until a save
  // clears the flag; read through a ref so the effect never sees a stale value.
  const [rules, setRules] = useState({ withdrawal_min: '0', withdrawal_fee_pct: '0' })
  const [rulesDirty, setRulesDirty] = useState(false)
  const rulesDirtyRef = useRef(false)
  rulesDirtyRef.current = rulesDirty
  useEffect(() => {
    if (settings && !rulesDirtyRef.current) {
      setRules({
        withdrawal_min: String(settings.withdrawal_min),
        withdrawal_fee_pct: String(settings.withdrawal_fee_pct),
      })
    }
  }, [settings])

  const editRules = (patch: Partial<typeof rules>) => {
    setRules((r) => ({ ...r, ...patch }))
    setRulesDirty(true)
  }

  const saveRules = (e: FormEvent) => {
    e.preventDefault()
    run(async () => {
      const saved = await orgApi<PortalSettings>(orgId, 'portal-settings', {
        method: 'PUT', body: JSON.stringify(rules) })
      setRules({
        withdrawal_min: String(saved.withdrawal_min),
        withdrawal_fee_pct: String(saved.withdrawal_fee_pct),
      })
      setRulesDirty(false)
    }, 'Withdrawal settings saved')
  }

  const openAdd = () => { setForm(EMPTY_FORM); setFormError(null); setEditing({ mode: 'add' }) }
  const openEdit = (m: PaymentMethod) => { setForm(formOf(m)); setFormError(null); setEditing({ mode: 'edit', method: m }) }
  const patch = (p: Partial<MethodForm>) => setForm((f) => ({ ...f, ...p }))
  const patchDetail = (key: string, value: string) =>
    setForm((f) => ({ ...f, details: { ...f.details, [key]: value } }))

  const payload = () => {
    const details: Record<string, string> = {}
    for (const f of DETAIL_FIELDS[form.kind]) {
      const v = (form.details[f.key] ?? '').trim()
      if (v) details[f.key] = v
    }
    return {
      label: form.label.trim(),
      currency: form.currency.trim() || 'USD',
      details,
      min_amount: form.min_amount.trim() || '0',
      fee_pct: form.fee_pct.trim() || '0',
      instructions: form.instructions.trim() || null,
      sort_order: Number(form.sort_order) || 0,
    }
  }

  const save = async (e: FormEvent) => {
    e.preventDefault()
    if (!editing) return
    const body = payload()
    if (!body.label) { setFormError('Label is required'); return }
    const missing = DETAIL_FIELDS[form.kind].find((f) => !f.optional && !body.details[f.key])
    if (missing) { setFormError(`${missing.label} is required`); return }
    setFormError(null)
    setSaving(true)
    try {
      // The request runs here, not inside run(): a refusal (400 field
      // missing, 404) must show inside the drawer, which covers the page
      // banner while it is open.
      if (editing.mode === 'add') {
        await orgApi(orgId, 'payment-methods', {
          method: 'POST', body: JSON.stringify({ kind: form.kind, ...body }) })
      } else {
        await orgApi(orgId, `payment-methods/${editing.method.id}`, {
          method: 'PATCH', body: JSON.stringify(body) })
      }
      setEditing(null)
      // The request already succeeded; run() now only announces and refreshes.
      await run(async () => {}, editing.mode === 'add' ? 'Payment method added' : 'Payment method saved')
    } catch (err) {
      setFormError(errorText(err, 'Could not save the method'))
    } finally {
      setSaving(false)
    }
  }

  const toggle = (m: PaymentMethod) => run(async () => {
    await orgApi(orgId, `payment-methods/${m.id}`, {
      method: 'PATCH', body: JSON.stringify({ enabled: !m.enabled }) })
  }, m.enabled ? 'Method disabled' : 'Method enabled')

  const remove = (m: PaymentMethod) => run(async () => {
    await orgApi(orgId, `payment-methods/${m.id}`, { method: 'DELETE' })
  }, 'Payment method deleted')

  return (
    <div className="space-y-6">
      <Card title="Payment methods" inset
            actions={control && <Button size="sm" disabled={busy} onClick={openAdd}>Add method</Button>}>
        <div className="overflow-x-auto">
          <table className="stack-table w-full text-sm">
            <thead>
              <tr className="text-left border-b border-line">
                <th className="desk-label px-5 py-2 font-semibold">Method</th>
                <th className="desk-label px-5 py-2 font-semibold">Details</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Minimum</th>
                <th className="desk-label px-5 py-2 font-semibold text-right">Fee</th>
                <th className="desk-label px-5 py-2 font-semibold">Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {methods.length === 0 && (
                <tr><td colSpan={6} className="text-center py-8 text-ink-faint">
                  No payment methods yet — add one so investors can deposit.
                </td></tr>
              )}
              {methods.map((m) => (
                <tr key={m.id} className="border-b border-line last:border-0 align-top">
                  <td data-label="Method" className="px-5 py-2.5">
                    <div className="min-w-0">
                      <div className="text-ink">{m.label}</div>
                      <Badge tone="neutral" className="mt-1">{m.kind === 'crypto' ? 'Crypto' : 'Bank'}</Badge>
                    </div>
                  </td>
                  <td data-label="Details" className="num px-5 py-2.5 text-ink-soft break-all">{methodSummary(m)}</td>
                  <td data-label="Minimum" className="num px-5 py-2.5 text-right">{money(m.min_amount, m.currency)}</td>
                  <td data-label="Fee" className="num px-5 py-2.5 text-right">{m.fee_pct}%</td>
                  <td data-label="Status" className="px-5 py-2.5">
                    <Badge tone={m.enabled ? 'profit' : 'neutral'}>{m.enabled ? 'Enabled' : 'Disabled'}</Badge>
                  </td>
                  <td className="px-5 py-2.5 text-right">
                    {control && (
                      <div className="flex flex-wrap justify-end gap-2">
                        <Button variant="secondary" size="sm" disabled={busy}
                                aria-label={`${m.enabled ? 'Disable' : 'Enable'} ${m.label}`}
                                onClick={() => toggle(m)}>
                          {m.enabled ? 'Disable' : 'Enable'}
                        </Button>
                        <Button variant="secondary" size="sm" disabled={busy}
                                aria-label={`Edit ${m.label}`} onClick={() => openEdit(m)}>
                          Edit
                        </Button>
                        <Button variant="ghost" tone="loss" size="sm" disabled={busy}
                                aria-label={`Delete ${m.label}`} onClick={() => setDeleting(m)}>
                          Delete
                        </Button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Withdrawal settings">
        <form onSubmit={saveRules} className="space-y-4">
          <p className="text-sm text-ink-soft">
            Applied to every withdrawal request: the smallest amount an investor may ask
            for, and the fee the workspace keeps. Both default to 0.
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <label className="block w-40">
              <span className="desk-label block mb-1">Minimum withdrawal</span>
              <Input aria-label="Minimum withdrawal" num inputMode="decimal" disabled={!control}
                     value={rules.withdrawal_min}
                     onChange={(e) => editRules({ withdrawal_min: e.target.value })} />
            </label>
            <label className="block w-40">
              <span className="desk-label block mb-1">Withdrawal fee %</span>
              <Input aria-label="Withdrawal fee %" num inputMode="decimal" disabled={!control}
                     value={rules.withdrawal_fee_pct}
                     onChange={(e) => editRules({ withdrawal_fee_pct: e.target.value })} />
            </label>
            {control && (
              <Button type="submit" disabled={busy || !rulesDirty}>Save withdrawal settings</Button>
            )}
          </div>
        </form>
      </Card>

      <Drawer open={editing != null} busy={saving}
              title={editing?.mode === 'edit' ? `Edit ${editing.method.label}` : 'Add payment method'}
              onClose={() => setEditing(null)}>
        <form onSubmit={save} className="space-y-4">
          {formError && <Banner kind="error">{formError}</Banner>}
          {editing?.mode === 'add' && (
            <label className="block">
              <span className="desk-label block mb-1">Kind</span>
              <Select block aria-label="Kind" value={form.kind}
                      onChange={(e) => patch({ kind: e.target.value as Kind, details: {} })}>
                <option value="crypto">Crypto</option>
                <option value="bank">Bank</option>
              </Select>
            </label>
          )}
          <label className="block">
            <span className="desk-label block mb-1">Label</span>
            <Input aria-label="Label" value={form.label}
                   placeholder={form.kind === 'crypto' ? 'USDT on TRC20' : 'ICICI Bank'}
                   onChange={(e) => patch({ label: e.target.value })} />
          </label>
          {DETAIL_FIELDS[form.kind].map((f) => (
            <label key={f.key} className="block">
              <span className="desk-label block mb-1">{f.label}{f.optional ? ' (optional)' : ''}</span>
              <Input aria-label={f.label}
                     num={f.key === 'address' || f.key === 'account_number' || f.key === 'code'}
                     value={form.details[f.key] ?? ''}
                     onChange={(e) => patchDetail(f.key, e.target.value)} />
            </label>
          ))}
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="desk-label block mb-1">Currency</span>
              <Input aria-label="Currency" value={form.currency}
                     onChange={(e) => patch({ currency: e.target.value })} />
            </label>
            <label className="block">
              <span className="desk-label block mb-1">Sort order</span>
              <Input aria-label="Sort order" num inputMode="numeric" value={form.sort_order}
                     onChange={(e) => patch({ sort_order: e.target.value })} />
            </label>
            <label className="block">
              <span className="desk-label block mb-1">Minimum deposit</span>
              <Input aria-label="Minimum deposit" num inputMode="decimal" value={form.min_amount}
                     onChange={(e) => patch({ min_amount: e.target.value })} />
            </label>
            <label className="block">
              <span className="desk-label block mb-1">Fee %</span>
              <Input aria-label="Fee %" num inputMode="decimal" value={form.fee_pct}
                     onChange={(e) => patch({ fee_pct: e.target.value })} />
            </label>
          </div>
          <label className="block">
            <span className="desk-label block mb-1">Instructions (optional)</span>
            <textarea aria-label="Instructions" rows={3} value={form.instructions}
                      onChange={(e) => patch({ instructions: e.target.value })}
                      className={TEXTAREA} />
          </label>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setEditing(null)} disabled={saving}>Cancel</Button>
            <Button type="submit" busy={saving}>Save method</Button>
          </div>
        </form>
      </Drawer>

      <ConfirmDialog
        open={deleting != null}
        title={`Delete ${deleting?.label ?? ''}?`}
        confirmLabel="Delete method"
        danger
        busy={busy}
        onConfirm={async () => {
          if (!deleting) return
          const m = deleting
          setDeleting(null)
          await remove(m)
        }}
        onCancel={() => setDeleting(null)}
      >
        <p>
          Investors stop seeing it at once. History keeps its own copy of the label, so
          past deposits still read correctly. A method with a deposit still pending cannot
          be deleted — decide that deposit first.
        </p>
      </ConfirmDialog>
    </div>
  )
}
```

- [ ] **Step 4: Write `LedgerDrawer.tsx`**

Create `dashboard/src/pages/investors/LedgerDrawer.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { orgApi } from '../../lib/api'
import { errorText, formatWhen, signed } from '../../lib/format'
import { WALLETS, entryLabel, walletLabel } from '../../lib/investor'
import Banner from '../../components/Banner'
import Button from '../../components/Button'
import Drawer from '../../components/Drawer'
import Loading from '../../components/Loading'
import Select from '../../components/Select'
import type { InvestorRow, WalletEntriesPage, WalletEntry, WalletKind } from '../../lib/types'

const PAGE = 50

/**
 * One investor's ledger, newest first, in a Drawer: a wallet filter and a
 * Load more button driven by the API's `has_more` / `next_before` cursor.
 * The parent mounts it with `key={investor.user_id}` so every investor
 * starts with a clean filter and an empty list.
 */
export default function LedgerDrawer({ orgId, investor, onClose }: {
  orgId: number
  investor: InvestorRow | null
  onClose: () => void
}) {
  const userId = investor?.user_id ?? null
  const [wallet, setWallet] = useState<'' | WalletKind>('')
  const [entries, setEntries] = useState<WalletEntry[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [nextBefore, setNextBefore] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (before: number | null) => {
    if (userId == null) return
    const q = new URLSearchParams({ limit: String(PAGE) })
    if (wallet) q.set('wallet', wallet)
    if (before != null) q.set('before', String(before))
    setLoading(true)
    try {
      const page = await orgApi<WalletEntriesPage>(orgId, `investors/${userId}/wallet-entries?${q.toString()}`)
      setEntries((cur) => (before == null ? page.entries : [...cur, ...page.entries]))
      setHasMore(page.has_more)
      setNextBefore(page.next_before)
      setError(null)
      setLoaded(true)
    } catch (err) {
      setError(errorText(err, 'Could not load the ledger'))
    } finally {
      setLoading(false)
    }
  }, [orgId, userId, wallet])

  // The first page whenever the drawer opens or the wallet filter changes.
  useEffect(() => {
    setEntries([]); setHasMore(false); setNextBefore(null); setLoaded(false)
    load(null)
  }, [load])

  return (
    <Drawer open={investor != null} title={investor ? `${investor.display_name}'s ledger` : ''} onClose={onClose}>
      <div className="space-y-4">
        <label className="block">
          <span className="desk-label block mb-1">Wallet</span>
          <Select block aria-label="Wallet" value={wallet}
                  onChange={(e) => setWallet(e.target.value as '' | WalletKind)}>
            <option value="">All wallets</option>
            {WALLETS.map((w) => <option key={w} value={w}>{walletLabel(w)}</option>)}
          </Select>
        </label>
        {error && <Banner kind="error">{error}</Banner>}
        {!loaded ? (
          !error && <Loading lines={4} label="Loading ledger" />
        ) : (
          <div className="inset overflow-x-auto">
            <table className="stack-table w-full text-sm">
              <thead>
                <tr className="text-left border-b border-line">
                  <th className="desk-label px-3 py-2 font-semibold">Date</th>
                  <th className="desk-label px-3 py-2 font-semibold">Wallet</th>
                  <th className="desk-label px-3 py-2 font-semibold">Kind</th>
                  <th className="desk-label px-3 py-2 font-semibold text-right">Amount</th>
                </tr>
              </thead>
              <tbody>
                {entries.length === 0 && (
                  <tr><td colSpan={4} className="text-center py-6 text-ink-faint">No entries yet</td></tr>
                )}
                {entries.map((e) => (
                  <tr key={e.id} className="border-b border-line last:border-0 align-top">
                    <td data-label="Date" className="num px-3 py-2">{formatWhen(e.created_at)}</td>
                    <td data-label="Wallet" className="px-3 py-2">{walletLabel(e.wallet)}</td>
                    <td data-label="Kind" className="px-3 py-2">
                      <div className="min-w-0">
                        <div className="text-ink">{entryLabel(e)}</div>
                        {e.note && <div className="text-xs text-ink-soft">{e.note}</div>}
                      </div>
                    </td>
                    <td data-label="Amount"
                        className={`num px-3 py-2 text-right ${e.amount < 0 ? 'text-loss' : 'text-profit'}`}>
                      {signed(e.amount)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {hasMore && (
          <Button variant="secondary" size="sm" disabled={loading} onClick={() => load(nextBefore)}>
            Load more
          </Button>
        )}
      </div>
    </Drawer>
  )
}
```

- [ ] **Step 5: Write `AdjustDialog.tsx`**

Create `dashboard/src/pages/investors/AdjustDialog.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { money } from '../../lib/format'
import { WALLETS, walletLabel } from '../../lib/investor'
import Input from '../../components/Input'
import PinConfirmDialog from '../../components/PinConfirmDialog'
import Select from '../../components/Select'
import type { InvestorRow, WalletEntry, WalletKind } from '../../lib/types'

/** A signed amount with at most two decimals: "-25.00", "100", "+12.5". */
const SIGNED_AMOUNT = /^[-+]?\d+(\.\d{1,2})?$/

/**
 * Posts a signed ledger entry to one of the investor's wallets, confirmed
 * with the ADMIN's own MPIN. Validation failures are thrown so the
 * PinConfirmDialog shows them in its own error line and clears the PIN,
 * the same path a wrong MPIN takes.
 */
export default function AdjustDialog({ orgId, investor, onCancel, onPosted }: {
  orgId: number
  investor: InvestorRow | null
  onCancel: () => void
  onPosted: (entry: WalletEntry) => void | Promise<void>
}) {
  const [wallet, setWallet] = useState<WalletKind>('main')
  const [amount, setAmount] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  // A fresh form for every investor; nothing typed for one carries to the next.
  useEffect(() => { setWallet('main'); setAmount(''); setNote('') }, [investor?.user_id])

  const confirm = async (mpin: string) => {
    if (!investor) return
    const raw = amount.trim()
    if (!SIGNED_AMOUNT.test(raw)) {
      throw new Error('Enter a signed amount with at most two decimals, for example -25.00 or 100')
    }
    if (Number(raw) === 0) throw new Error('amount must not be zero')
    if (note.trim() === '') throw new Error('A note is required')
    setBusy(true)
    try {
      // Straight through api() with redirectOn401 off: a wrong MPIN answers
      // 401 and must stay an inline error, never a bounce to /login.
      const entry = await api<WalletEntry>(
        `/api/orgs/${orgId}/investors/${investor.user_id}/adjustments`,
        { method: 'POST', body: JSON.stringify({ wallet, amount: raw, note: note.trim(), mpin }) },
        { redirectOn401: false },
      )
      await onPosted(entry)
    } finally {
      setBusy(false)
    }
  }

  return (
    <PinConfirmDialog
      open={investor != null}
      title={investor ? `Adjust ${investor.display_name}'s wallet` : ''}
      confirmLabel="Post adjustment"
      busy={busy}
      onConfirm={confirm}
      onCancel={onCancel}
    >
      <p>
        Posts a signed ledger entry. A negative amount debits the wallet, a positive one
        credits it, and the investor is emailed either way.
      </p>
      <label className="block">
        <span className="desk-label block mb-1">Wallet</span>
        <Select block aria-label="Wallet" value={wallet} onChange={(e) => setWallet(e.target.value as WalletKind)}>
          {WALLETS.map((w) => (
            <option key={w} value={w}>
              {walletLabel(w)}{investor ? ` — ${money(investor.balances[w])}` : ''}
            </option>
          ))}
        </Select>
      </label>
      <label className="block">
        <span className="desk-label block mb-1">Amount (signed)</span>
        <Input aria-label="Amount" num inputMode="decimal" placeholder="-25.00" value={amount}
               onChange={(e) => setAmount(e.target.value)} />
      </label>
      <label className="block">
        <span className="desk-label block mb-1">Note</span>
        <Input aria-label="Note" value={note} onChange={(e) => setNote(e.target.value)} />
      </label>
    </PinConfirmDialog>
  )
}
```

- [ ] **Step 6: Rewrite `Investors.tsx`**

Replace the whole of `dashboard/src/pages/Investors.tsx` with:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import { errorText, money, signed } from '../lib/format'
import { moneyOrDash, walletLabel } from '../lib/investor'
import Badge from '../components/Badge'
import Banner from '../components/Banner'
import Card from '../components/Card'
import Loading from '../components/Loading'
import Menu from '../components/Menu'
import PageHeader from '../components/PageHeader'
import Select from '../components/Select'
import Tabs from '../components/Tabs'
import AdjustDialog from './investors/AdjustDialog'
import LedgerDrawer from './investors/LedgerDrawer'
import PaymentMethodsTab, { type Runner } from './investors/PaymentMethodsTab'
import type { Account, InvestorRow, PaymentMethod, PortalSettings } from '../lib/types'

const POLL_MS = 10000

type Tab = 'investors' | 'methods'

type PendingKind = keyof InvestorRow['pending']

const PENDING_WORD: Record<PendingKind, [string, string]> = {
  deposits: ['deposit', 'deposits'],
  withdrawals: ['withdrawal', 'withdrawals'],
  transfers: ['transfer', 'transfers'],
  payout_destinations: ['payout account', 'payout accounts'],
}

/** "2 deposits" "1 withdrawal": one chip per non-zero open count, each a
 *  link into the Requests desk on that tab. */
function OpenRequests({ orgId, pending }: { orgId: number; pending: InvestorRow['pending'] }) {
  const kinds = (Object.keys(PENDING_WORD) as PendingKind[]).filter((k) => pending[k] > 0)
  if (kinds.length === 0) return <span className="text-ink-faint">—</span>
  return (
    <div className="flex flex-wrap gap-1.5">
      {kinds.map((k) => (
        <Link key={k} to={`/org/${orgId}/requests?tab=${k}`} className="inline-flex rounded-full">
          <Badge tone="warn">{pending[k]} {PENDING_WORD[k][pending[k] === 1 ? 0 : 1]}</Badge>
        </Link>
      ))}
    </div>
  )
}

export default function Investors() {
  const { orgId, role } = useOrg()
  const [rows, setRows] = useState<InvestorRow[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [methods, setMethods] = useState<PaymentMethod[]>([])
  const [settings, setSettings] = useState<PortalSettings | null>(null)
  const [tab, setTab] = useState<Tab>('investors')
  const [ledgerFor, setLedgerFor] = useState<InvestorRow | null>(null)
  const [adjustFor, setAdjustFor] = useState<InvestorRow | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // Until the first load lands, the empty lists are unknown, not empty.
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [r, a, m, s] = await Promise.all([
        orgApi<InvestorRow[]>(orgId, 'investors'),
        orgApi<Account[]>(orgId, 'accounts'),
        orgApi<PaymentMethod[]>(orgId, 'payment-methods'),
        orgApi<PortalSettings>(orgId, 'portal-settings'),
      ])
      setRows(r); setAccounts(a); setMethods(m); setSettings(s)
      setError(null)
      setLoaded(true)
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

  const run: Runner = async (fn, done) => {
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

  const linkAccount = (userId: number, accountId: number | null) => run(async () => {
    await orgApi(orgId, `investors/${userId}/account`, {
      method: 'PUT', body: JSON.stringify({ account_id: accountId }) })
  }, accountId == null ? 'Account unlinked' : 'Account linked')

  const linkedIds = new Set(rows.map((r) => r.account_id).filter((id) => id != null))
  const unlinked = accounts.filter((a) => !linkedIds.has(a.ctid_trader_account_id) && a.role !== 'master')

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Investors"
        subtitle="Who invests through this workspace, what their wallets hold, and where their deposits land. Decisions on their requests live on the Requests desk."
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      <Tabs
        idBase="investors"
        label="Investors"
        value={tab}
        onChange={(k) => setTab(k as Tab)}
        items={[
          { key: 'investors', label: 'Investors' },
          { key: 'methods', label: 'Payment methods' },
        ]}
      />

      {!loaded ? (
        // An error before the first load shows the banner above, not an
        // endless skeleton.
        !error && <Loading lines={6} label="Loading investors" />
      ) : (
        <div id="investors-panel" role="tabpanel" aria-labelledby={`investors-tab-${tab}`}>
          {tab === 'investors' ? (
            <Card title="Investor accounts" inset>
              <div className="overflow-x-auto">
                <table className="stack-table w-full text-sm">
                  <thead>
                    <tr className="text-left border-b border-line">
                      <th className="desk-label px-5 py-2 font-semibold">Name</th>
                      <th className="desk-label px-5 py-2 font-semibold">Email</th>
                      <th className="desk-label px-5 py-2 font-semibold text-right">My wallet</th>
                      <th className="desk-label px-5 py-2 font-semibold text-right">Equity</th>
                      <th className="desk-label px-5 py-2 font-semibold">Linked account</th>
                      <th className="desk-label px-5 py-2 font-semibold">Open requests</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.length === 0 && (
                      <tr><td colSpan={7} className="text-center py-8 text-ink-faint">
                        No investors yet — invite one from Members with the Investor role.
                      </td></tr>
                    )}
                    {rows.map((r) => (
                      <tr key={r.user_id} className="border-b border-line last:border-0 align-top">
                        <td data-label="Name" className="px-5 py-2.5 text-ink">{r.display_name}</td>
                        <td data-label="Email" className="px-5 py-2.5 text-ink-soft">{r.email}</td>
                        <td data-label="My wallet" className="px-5 py-2.5 text-right">
                          {/* One child per cell: the stacked phone layout is a
                              flex row of label and value. */}
                          <div className="min-w-0">
                            <div className="num text-ink">{money(r.balances.main)}</div>
                            <div className="num text-xs text-ink-soft">
                              {`${r.on_hold > 0 ? `on hold ${money(r.on_hold)} · ` : ''}available ${money(r.available)}`}
                            </div>
                          </div>
                        </td>
                        <td data-label="Equity" className="num px-5 py-2.5 text-right">{moneyOrDash(r.equity)}</td>
                        <td data-label="Linked account" className="px-5 py-2.5">
                          {control ? (
                            <Select aria-label={`Account for ${r.email}`}
                                    value={r.account_id ?? ''}
                                    disabled={busy}
                                    onChange={(e) => linkAccount(r.user_id, e.target.value ? Number(e.target.value) : null)}>
                              <option value="">not linked</option>
                              {r.account_id != null && (
                                <option value={r.account_id}>{r.nickname ?? r.account_id}</option>
                              )}
                              {unlinked.map((a) => (
                                <option key={a.ctid_trader_account_id} value={a.ctid_trader_account_id}>
                                  {a.nickname ?? a.trader_login} ({a.platform ?? 'ctrader'})
                                </option>
                              ))}
                            </Select>
                          ) : (r.nickname ?? r.account_id ?? 'not linked')}
                        </td>
                        <td data-label="Open requests" className="px-5 py-2.5">
                          <OpenRequests orgId={orgId} pending={r.pending} />
                        </td>
                        <td className="px-5 py-2.5 text-right">
                          {control && (
                            <Menu label={`Actions for ${r.email}`} items={[
                              { key: 'ledger', label: 'View ledger', onSelect: () => setLedgerFor(r) },
                              { key: 'adjust', label: 'Adjust balance', disabled: busy, onSelect: () => setAdjustFor(r) },
                            ]} />
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ) : (
            <PaymentMethodsTab orgId={orgId} control={control} methods={methods}
                               settings={settings} busy={busy} run={run} />
          )}
        </div>
      )}

      {/* Keyed on the investor so each opening starts with a clean filter. */}
      <LedgerDrawer key={ledgerFor?.user_id ?? 'none'} orgId={orgId} investor={ledgerFor}
                    onClose={() => setLedgerFor(null)} />
      <AdjustDialog orgId={orgId} investor={adjustFor} onCancel={() => setAdjustFor(null)}
                    onPosted={async (entry) => {
                      setAdjustFor(null)
                      setError(null)
                      setNotice(`Adjustment of ${signed(entry.amount)} posted to ${walletLabel(entry.wallet)}`)
                      await refresh()
                    }} />
    </div>
  )
}
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `npx vitest run src/pages/Investors.test.tsx`
Expected: PASS, 11 passed.

Then the whole gate: `node scripts/palette_check.mjs && npx tsc --noEmit -p tsconfig.app.json && npx vitest run --maxWorkers=2 --minWorkers=1`
Expected: the palette prover prints no failing pair, `tsc` prints nothing, vitest all green (the `vocabulary.test.ts` and `theme-css.test.ts` scans included). If `tsc` names an unused import in `Investors.tsx`, delete that import line; nothing else in this task is optional.

- [ ] **Step 8: Commit**

```bash
git add dashboard/src/pages/Investors.tsx dashboard/src/pages/Investors.test.tsx dashboard/src/pages/investors/PaymentMethodsTab.tsx dashboard/src/pages/investors/LedgerDrawer.tsx dashboard/src/pages/investors/AdjustDialog.tsx
git commit -m "feat(dashboard): Investors page -- wallet figures, ledger drawer, MPIN-gated adjustments and payment methods

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 19: Requests desk — deposits, withdrawals, transfers and payout accounts

A new admin page at `/org/:orgId/requests` (the `Requests` link and badge that Task 12
wired into `adminNav`). Four tabs with open counts from `requests/summary`, an Open / All
Select, one table per tab, per-row actions through the Investors page's `Pending`
descriptor and one shared `ConfirmDialog` (the Confirm-deposit dialog adds a credited
amount input prefilled with `amount − fee`), and a Details drawer per row showing the
request, the investor, the receipt or proof image straight from `GET files/{id}`, the
destination details and the audit trail. Polls every 10 s and refetches on `control`
events through `useLiveRefresh`. The page always fetches the full lists and filters
Open / All client-side, so one refresh serves both views and the Select flips instantly.

**Files:**
- Create: `dashboard/src/pages/requests/RequestTabs.tsx`
- Create: `dashboard/src/pages/requests/RequestDetailsDrawer.tsx`
- Create: `dashboard/src/pages/Requests.tsx`
- Create: `dashboard/src/pages/Requests.test.tsx`
- Modify: `dashboard/src/pages/groups/admin.ts` (export `Requests`)
- Modify: `dashboard/src/App.tsx` (`pick` const and `<Route path="requests">`)
- Test: `dashboard/src/pages/Requests.test.tsx`

**Interfaces:**
- Consumes: `PortalDeposit`, `PortalWithdrawal`, `PortalTransfer`, `PayoutDestination`, `MoneyRef`, `RequestsSummary` (`lib/types.ts`, Task 11); `BADGE_TONE`, `statusLabel`, `statusTone`, `walletLabel`, `moneyOrDash` (`lib/investor.ts`, Task 11); `depositFixture`, `withdrawalFixture`, `transferFixture`, `destinationFixture` (`src/test/portalFixtures.ts`, Task 11); `TabItem` (`components/Tabs.tsx`); `orgApi`, `eventsSocket`; `useLiveRefresh`; API routes `GET requests/summary`, `GET deposits`, `POST deposits/{id}/decision`, `GET withdrawals`, `POST withdrawals/{id}/decision`, `POST withdrawals/{id}/paid`, `GET transfers`, `POST transfers/{id}/decision`, `GET payout-destinations`, `POST payout-destinations/{id}/decision`, `GET files/{id}` (Tasks 4, 7, 8, 9, 10); the `?tab=<kind>` query the Investors chips (Task 18) link with.
- Produces: `RequestKind`, `REQUEST_KINDS`, `isOpen`, `KIND_WORD`, `tabItems`, `moneyRefLabel`, `DepositsTable`, `WithdrawalsTable`, `TransfersTable`, `DestinationsTable` (`RequestTabs.tsx`); `Details` type and the default export of `RequestDetailsDrawer.tsx` with props `{ orgId, details, destinations: PayoutDestination[], onClose }` — the page hands it the full `GET payout-destinations` list (`full=True` for admins, so bank `account_number` is unmasked) and the withdrawal drawer looks its row's `destination_id` up in it to show the account number / crypto address the admin must pay to; page `Requests` in the admin chunk at route `requests`. Both admin pages use the page-contract root `<div className="space-y-6 max-w-5xl">`.

- [ ] **Step 1: Write the failing test**

Create `dashboard/src/pages/Requests.test.tsx`:

```tsx
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { expect, test, vi, afterEach, beforeEach } from 'vitest'
import Requests from './Requests'
import * as apiModule from '../lib/api'
import { mockUseOrg } from '../test/orgMock'
import {
  depositFixture, destinationFixture, transferFixture, withdrawalFixture,
} from '../test/portalFixtures'

const { useOrgMock } = vi.hoisted(() => ({ useOrgMock: vi.fn() }))
vi.mock('../lib/org', () => ({ useOrg: useOrgMock }))

class MockWebSocket {
  static instance: MockWebSocket | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  constructor() { MockWebSocket.instance = this }
  close() { /* no-op */ }
  emit(json: object) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(json) }))
  }
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'Content-Type': 'application/json' },
  })
}

const who = { user_id: 5, email: 'inv@example.com', display_name: 'Ada Investor', currency: 'USD' }

const deposit = depositFixture({
  ...who, id: 11, method_id: 3, method_kind: 'bank', method_label: 'ICICI Bank',
  amount: 5000, fee: 50, credited_amount: null, reference: 'UTR123', receipt_file_id: 77,
  target: 'wallet', target_account_id: null, note: null, status: 'pending',
  decided_by: null, decided_at: null, decision_note: null, created_at: '2026-09-23T10:00:00Z',
})
const settled = depositFixture({
  ...deposit, id: 12, reference: 'UTR122', status: 'confirmed', credited_amount: 4950,
  decided_by: 1, decided_at: '2026-09-22T11:00:00Z', decision_note: 'seen on statement',
  created_at: '2026-09-22T10:00:00Z',
})
const approvedWd = withdrawalFixture({
  ...who, id: 21, destination_id: 41, destination_kind: 'bank', destination_summary: 'ICICI ••4543',
  amount: 1000, fee: 10, net_amount: 990, status: 'approved',
  decided_by: 1, decided_at: '2026-09-23T11:00:00Z', decision_note: null,
  paid_by: null, paid_at: null, txid: null, created_at: '2026-09-23T10:30:00Z',
})
const requestedWd = withdrawalFixture({
  ...approvedWd, id: 22, amount: 200, fee: 2, net_amount: 198, status: 'requested',
  decided_by: null, decided_at: null, created_at: '2026-09-23T12:00:00Z',
})
const transfer = transferFixture({
  ...who, id: 31, source: { kind: 'wallet', wallet: 'main' }, target: { kind: 'account', account_id: 1001 },
  amount: 250, status: 'requested', equity_at_request: null, equity_verified: true,
  decided_by: null, decided_at: null, decision_note: null, done_by: null, done_at: null,
  note: null, created_at: '2026-09-23T13:00:00Z',
})
const destination = destinationFixture({
  ...who, id: 41, kind: 'bank', nickname: 'Salary account',
  details: { bank_name: 'ICICI', holder: 'Ada Investor', account_number: '000123454543', code: 'ICIC0000001' },
  proof_file_id: 78, status: 'pending', decided_by: null, decided_at: null, decision_note: null,
  created_at: '2026-09-23T09:00:00Z', summary: 'ICICI ••4543',
})

type Row = { id: number; status: string }

/** Every list is served from mutable copies: a decision POST flips the
 *  row's status, so the refetch that follows shows the queue drained. */
function mockRoutes() {
  const rows: Record<string, Row[]> = {
    deposits: [{ ...deposit }, { ...settled }],
    withdrawals: [{ ...approvedWd }, { ...requestedWd }],
    transfers: [{ ...transfer }],
    'payout-destinations': [{ ...destination }],
  }
  const summary = () => {
    const deposits = rows.deposits.filter((r) => r.status === 'pending').length
    const withdrawals = rows.withdrawals.filter((r) => r.status === 'requested' || r.status === 'approved').length
    const transfers = rows.transfers.filter((r) => r.status === 'requested' || r.status === 'approved').length
    const payout_destinations = rows['payout-destinations'].filter((r) => r.status === 'pending').length
    return { deposits, withdrawals, transfers, payout_destinations,
             total: deposits + withdrawals + transfers + payout_destinations }
  }
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method || 'GET'
    if (url.endsWith('/requests/summary')) return jsonResponse(summary())
    for (const key of Object.keys(rows)) {
      if (url.endsWith(`/${key}`) && method === 'GET') return jsonResponse(rows[key])
    }
    const m = url.match(/\/(deposits|withdrawals|transfers|payout-destinations)\/(\d+)\/(decision|paid)$/)
    if (m && method === 'POST') {
      const body = JSON.parse(init!.body as string) as { status?: string }
      const row = rows[m[1]].find((r) => r.id === Number(m[2]))!
      row.status = m[3] === 'paid' ? 'paid' : body.status!
      return jsonResponse(row)
    }
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

function renderPage(path = '/org/1/requests') {
  return render(<MemoryRouter initialEntries={[path]}><Requests /></MemoryRouter>)
}

beforeEach(() => {
  MockWebSocket.instance = null
  vi.spyOn(apiModule, 'eventsSocket').mockImplementation(() => new MockWebSocket() as never)
  useOrgMock.mockReturnValue(mockUseOrg('admin'))
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

test('shows the heading and a loading state, then the tabs with their open counts', async () => {
  mockRoutes()
  renderPage()
  expect(screen.getByRole('heading', { level: 1, name: 'Requests' })).toBeInTheDocument()
  expect(screen.getByRole('status', { name: 'Loading requests' })).toBeInTheDocument()
  expect(await screen.findByText('UTR123')).toBeInTheDocument()
  await waitFor(() => expect(document.title).toBe('Requests · MirrorFleet'))
  expect(screen.queryByRole('status', { name: 'Loading requests' })).not.toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'Deposits (1)' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tab', { name: 'Withdrawals (2)' })).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'Transfers (1)' })).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'Payout accounts (1)' })).toBeInTheDocument()
  expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'requests-tab-deposits')
  // The Open view leaves out the settled deposit.
  expect(screen.getByLabelText('Show')).toHaveValue('open')
  expect(screen.queryByText('UTR122')).not.toBeInTheDocument()
})

test('All shows settled rows too', async () => {
  mockRoutes()
  renderPage()
  await screen.findByText('UTR123')
  await userEvent.selectOptions(screen.getByLabelText('Show'), 'all')
  expect(await screen.findByText('UTR122')).toBeInTheDocument()
  expect(screen.getByText('Confirmed')).toBeInTheDocument()
  expect(screen.getByText('seen on statement')).toBeInTheDocument()
})

test('opens on the tab named in the query string', async () => {
  mockRoutes()
  renderPage('/org/1/requests?tab=withdrawals')
  expect(await screen.findByRole('tab', { name: 'Withdrawals (2)' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('button', { name: 'Mark withdrawal 21 paid' })).toBeInTheDocument()
})

test('confirms a deposit with an edited credited amount, then the open queue is empty', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  await screen.findByText('UTR123')
  await userEvent.click(screen.getByRole('button', { name: 'Confirm deposit 11' }))
  const credited = screen.getByLabelText('Credited amount')
  expect(credited).toHaveValue('4950.00')
  await userEvent.clear(credited)
  await userEvent.type(credited, '4900.00')
  await userEvent.click(screen.getByRole('button', { name: 'Confirm' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/deposits/11/decision'))
    .toEqual({ status: 'confirmed', credited_amount: '4900.00', note: '' }))
  expect(await screen.findByText('Deposit confirmed')).toBeInTheDocument()
  expect(await screen.findByText('No open deposits')).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: 'Deposits (0)' })).toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

test('a malformed credited amount blocks Confirm; a rejection needs a note', async () => {
  mockRoutes()
  renderPage()
  await screen.findByText('UTR123')
  await userEvent.click(screen.getByRole('button', { name: 'Confirm deposit 11' }))
  const confirm = screen.getByRole('button', { name: 'Confirm' })
  await userEvent.clear(screen.getByLabelText('Credited amount'))
  expect(confirm).toBeDisabled()
  await userEvent.type(screen.getByLabelText('Credited amount'), '4950')
  expect(confirm).toBeEnabled()
  await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))

  await userEvent.click(screen.getByRole('button', { name: 'Reject deposit 11' }))
  expect(screen.queryByLabelText('Credited amount')).not.toBeInTheDocument()
  const reject = screen.getByRole('button', { name: 'Reject' })
  expect(reject).toBeDisabled()
  await userEvent.type(screen.getByLabelText('Note'), 'no such transaction')
  expect(reject).toBeEnabled()
})

test('approves a withdrawal and marks another paid with a transaction id', async () => {
  const fetchMock = mockRoutes()
  renderPage()
  await screen.findByText('UTR123')
  await userEvent.click(screen.getByRole('tab', { name: 'Withdrawals (2)' }))
  expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'requests-tab-withdrawals')
  await userEvent.click(await screen.findByRole('button', { name: 'Approve withdrawal 22' }))
  await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/withdrawals/22/decision'))
    .toEqual({ status: 'approved', note: '' }))
  expect(await screen.findByText('Withdrawal approved')).toBeInTheDocument()

  await userEvent.click(screen.getByRole('button', { name: 'Mark withdrawal 21 paid' }))
  const paid = screen.getByRole('button', { name: 'Mark paid' })
  expect(paid).toBeDisabled()
  await userEvent.type(screen.getByLabelText('Transaction ID'), 'chain-tx-1')
  await userEvent.click(paid)
  await waitFor(() => expect(bodyOf(fetchMock, '/withdrawals/21/paid')).toEqual({ txid: 'chain-tx-1' }))
  expect(await screen.findByText('Withdrawal marked paid')).toBeInTheDocument()
  // Paid and approved rows have left the open queue: only the approved one remains.
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Mark withdrawal 21 paid' })).not.toBeInTheDocument())
  expect(screen.getByRole('button', { name: 'Mark withdrawal 22 paid' })).toBeInTheDocument()
})

test('marks a transfer done', async () => {
  const fetchMock = mockRoutes()
  renderPage('/org/1/requests?tab=transfers')
  expect(await screen.findByText('My wallet → Trading account 1001')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Mark transfer 31 done' }))
  await userEvent.click(screen.getByRole('button', { name: 'Mark done' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/transfers/31/decision')).toEqual({ status: 'done', note: '' }))
  expect(await screen.findByText('Transfer done')).toBeInTheDocument()
  expect(await screen.findByText('No open transfers')).toBeInTheDocument()
})

test('approves a payout account', async () => {
  const fetchMock = mockRoutes()
  renderPage('/org/1/requests?tab=payout_destinations')
  expect(await screen.findByText('Salary account')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'Approve payout account 41' }))
  await userEvent.click(screen.getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(bodyOf(fetchMock, '/payout-destinations/41/decision'))
    .toEqual({ status: 'approved', note: '' }))
  expect(await screen.findByText('Payout account approved')).toBeInTheDocument()
  expect(await screen.findByText('No open payout accounts')).toBeInTheDocument()
})

test('the details drawer shows the request, the investor, the receipt, the payout details and the audit trail', async () => {
  mockRoutes()
  renderPage()
  await screen.findByText('UTR123')
  await userEvent.click(screen.getByRole('button', { name: 'Details of deposit 11' }))
  const drawer = await screen.findByRole('dialog', { name: 'Deposit #11' })
  expect(within(drawer).getByRole('img', { name: 'Receipt' })).toHaveAttribute('src', '/api/orgs/1/files/77')
  expect(within(drawer).getByRole('link', { name: 'Open file #77 in a new tab' })).toHaveAttribute('href', '/api/orgs/1/files/77')
  expect(within(drawer).getByText('inv@example.com')).toBeInTheDocument()
  expect(within(drawer).getByText('5,000.00 USD')).toBeInTheDocument()
  expect(within(drawer).getByText('Filed')).toBeInTheDocument()
  expect(within(drawer).queryByText('Decided')).not.toBeInTheDocument()
  await userEvent.click(within(drawer).getByRole('button', { name: 'Close' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

  // A withdrawal's drawer shows the payout destination it pays to (fixture
  // destination 41 = approvedWd.destination_id), unmasked, not just the summary.
  await userEvent.click(screen.getByRole('tab', { name: 'Withdrawals (2)' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Details of withdrawal 21' }))
  const wd = await screen.findByRole('dialog', { name: 'Withdrawal #21' })
  expect(within(wd).getByText('ICICI ••4543 (bank)')).toBeInTheDocument()
  expect(within(wd).getByText('Salary account')).toBeInTheDocument()
  expect(within(wd).getByText('000123454543')).toBeInTheDocument()
  expect(within(wd).getByText('ICIC0000001')).toBeInTheDocument()
  expect(within(wd).getByText('Decided')).toBeInTheDocument()
  await userEvent.click(within(wd).getByRole('button', { name: 'Close' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole('tab', { name: 'Payout accounts (1)' }))
  await userEvent.click(await screen.findByRole('button', { name: 'Details of payout account 41' }))
  const proof = await screen.findByRole('dialog', { name: 'Payout account #41' })
  expect(within(proof).getByRole('img', { name: 'Proof' })).toHaveAttribute('src', '/api/orgs/1/files/78')
  expect(within(proof).getByText('000123454543')).toBeInTheDocument()
  expect(within(proof).getByText('ICIC0000001')).toBeInTheDocument()
})

test('a control event refetches the queues', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  const fetchMock = mockRoutes()
  renderPage()
  await screen.findByText('UTR123')
  const summaryCalls = () => fetchMock.mock.calls.filter(([u]) => String(u).endsWith('/requests/summary')).length
  const before = summaryCalls()
  MockWebSocket.instance!.emit({ category: 'control', payload: { action: 'investor_deposit_noticed', user_id: 5 } })
  await vi.advanceTimersByTimeAsync(300)
  await waitFor(() => expect(summaryCalls()).toBeGreaterThan(before))
})

test('a viewer sees the queues and the details but no decisions', async () => {
  useOrgMock.mockReturnValue(mockUseOrg('viewer'))
  mockRoutes()
  renderPage()
  await screen.findByText('UTR123')
  expect(screen.queryByRole('button', { name: 'Confirm deposit 11' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Details of deposit 11' })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run it to verify it fails**

Run (from `dashboard/`): `npx vitest run src/pages/Requests.test.tsx`
Expected: FAIL — `Error: Failed to resolve import "./Requests" from "src/pages/Requests.test.tsx"`.

- [ ] **Step 3: Write `RequestTabs.tsx` — the four tables**

Create `dashboard/src/pages/requests/RequestTabs.tsx`:

```tsx
import type { ReactNode } from 'react'
import Badge from '../../components/Badge'
import Button from '../../components/Button'
import type { TabItem } from '../../components/Tabs'
import { formatWhen, money } from '../../lib/format'
import { BADGE_TONE, statusLabel, statusTone, walletLabel } from '../../lib/investor'
import type {
  MoneyRef, PayoutDestination, PortalDeposit, PortalTransfer, PortalWithdrawal, RequestsSummary,
} from '../../lib/types'

export type RequestKind = 'deposits' | 'withdrawals' | 'transfers' | 'payout_destinations'
export const REQUEST_KINDS: RequestKind[] = ['deposits', 'withdrawals', 'transfers', 'payout_destinations']

/** Statuses that still need an admin: what the Open view shows and what
 *  requests/summary counts (withdrawals and transfers count approved too,
 *  because paying or funding is still owed). */
const OPEN_STATUSES: Record<RequestKind, ReadonlySet<string>> = {
  deposits: new Set(['pending']),
  withdrawals: new Set(['requested', 'approved']),
  transfers: new Set(['requested', 'approved']),
  payout_destinations: new Set(['pending']),
}

export function isOpen(kind: RequestKind, status: string): boolean {
  return OPEN_STATUSES[kind].has(status)
}

/** What a row is called in aria-labels, notices and drawer titles. */
export const KIND_WORD: Record<RequestKind, string> = {
  deposits: 'deposit',
  withdrawals: 'withdrawal',
  transfers: 'transfer',
  payout_destinations: 'payout account',
}

export function tabItems(summary: RequestsSummary | null): TabItem[] {
  const n = (k: RequestKind) => (summary ? summary[k] : 0)
  return [
    { key: 'deposits', label: `Deposits (${n('deposits')})` },
    { key: 'withdrawals', label: `Withdrawals (${n('withdrawals')})` },
    { key: 'transfers', label: `Transfers (${n('transfers')})` },
    { key: 'payout_destinations', label: `Payout accounts (${n('payout_destinations')})` },
  ]
}

/** "My wallet" or "Trading account 1001". */
export function moneyRefLabel(ref: MoneyRef): string {
  return ref.kind === 'wallet' && ref.wallet ? walletLabel(ref.wallet) : `Trading account ${ref.account_id ?? '?'}`
}

const TH = 'desk-label px-4 py-2 font-semibold'
const TD = 'px-4 py-2.5'

interface Common {
  control: boolean
  busy: boolean
  show: 'open' | 'all'
}

function Head({ cols }: { cols: (string | [string, 'right'])[] }) {
  return (
    <thead>
      <tr className="text-left border-b border-line">
        {cols.map((c) => Array.isArray(c)
          ? <th key={c[0]} className={`${TH} text-right`}>{c[0]}</th>
          : <th key={c} className={TH}>{c}</th>)}
        <th></th>
      </tr>
    </thead>
  )
}

function EmptyRow({ colSpan, text }: { colSpan: number; text: string }) {
  return <tr><td colSpan={colSpan} className="text-center py-8 text-ink-faint">{text}</td></tr>
}

function emptyText(show: 'open' | 'all', plural: string): string {
  return show === 'open' ? `No open ${plural}` : `No ${plural} yet`
}

function Who({ name, email }: { name?: string; email?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-ink">{name ?? '—'}</div>
      <div className="text-xs text-ink-soft">{email ?? ''}</div>
    </div>
  )
}

/** The status chip plus the admin's note under it. A transfer's `approved`
 *  means "acknowledged, funding in progress", not "payment pending". */
function Status({ kind, status, note }: { kind: RequestKind; status: string; note: string | null }) {
  const label = kind === 'transfers' && status === 'approved' ? 'Approved, in progress' : statusLabel(status)
  return (
    <div className="min-w-0">
      <Badge tone={BADGE_TONE[statusTone(status)]}>{label}</Badge>
      {note && <div className="text-xs text-ink-soft mt-1">{note}</div>}
    </div>
  )
}

function Actions({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center justify-end gap-2">{children}</div>
}

export function DepositsTable({ rows, control, busy, show, onConfirm, onReject, onDetails }: Common & {
  rows: PortalDeposit[]
  onConfirm: (d: PortalDeposit) => void
  onReject: (d: PortalDeposit) => void
  onDetails: (d: PortalDeposit) => void
}) {
  return (
    <table className="stack-table w-full text-sm">
      <Head cols={['Filed', 'Investor', ['Amount', 'right'], 'Method', 'Reference', 'Status']} />
      <tbody>
        {rows.length === 0 && <EmptyRow colSpan={7} text={emptyText(show, 'deposits')} />}
        {rows.map((d) => (
          <tr key={d.id} className="border-b border-line last:border-0 align-top">
            <td data-label="Filed" className={`num ${TD}`}>{formatWhen(d.created_at)}</td>
            <td data-label="Investor" className={TD}><Who name={d.display_name} email={d.email} /></td>
            <td data-label="Amount" className={`${TD} text-right`}>
              <div className="min-w-0">
                <div className="num text-ink">{money(d.amount, d.currency)}</div>
                <div className="num text-xs text-ink-soft">
                  {`${d.fee > 0 ? `fee ${money(d.fee)} · ` : ''}${
                    d.status === 'confirmed' ? `credited ${money(d.credited_amount)}`
                    : `to ${d.target === 'account' ? 'trading account' : 'wallet'}`}`}
                </div>
              </div>
            </td>
            <td data-label="Method" className={TD}>{d.method_label}</td>
            <td data-label="Reference" className={`num ${TD} break-all`}>
              <div className="min-w-0">
                <div>{d.reference}</div>
                {d.note && <div className="text-xs text-ink-soft">{d.note}</div>}
              </div>
            </td>
            <td data-label="Status" className={TD}><Status kind="deposits" status={d.status} note={d.decision_note} /></td>
            <td className={`${TD} text-right`}>
              <Actions>
                {control && d.status === 'pending' && (
                  <>
                    <Button size="sm" disabled={busy} aria-label={`Confirm deposit ${d.id}`} onClick={() => onConfirm(d)}>Confirm</Button>
                    <Button variant="secondary" tone="loss" size="sm" disabled={busy} aria-label={`Reject deposit ${d.id}`} onClick={() => onReject(d)}>Reject</Button>
                  </>
                )}
                <Button variant="ghost" tone="neutral" size="sm" aria-label={`Details of deposit ${d.id}`} onClick={() => onDetails(d)}>Details</Button>
              </Actions>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function WithdrawalsTable({ rows, control, busy, show, onApprove, onReject, onPaid, onDetails }: Common & {
  rows: PortalWithdrawal[]
  onApprove: (w: PortalWithdrawal) => void
  onReject: (w: PortalWithdrawal) => void
  onPaid: (w: PortalWithdrawal) => void
  onDetails: (w: PortalWithdrawal) => void
}) {
  return (
    <table className="stack-table w-full text-sm">
      <Head cols={['Requested', 'Investor', ['Amount', 'right'], 'Destination', 'Status']} />
      <tbody>
        {rows.length === 0 && <EmptyRow colSpan={6} text={emptyText(show, 'withdrawals')} />}
        {rows.map((w) => (
          <tr key={w.id} className="border-b border-line last:border-0 align-top">
            <td data-label="Requested" className={`num ${TD}`}>{formatWhen(w.created_at)}</td>
            <td data-label="Investor" className={TD}><Who name={w.display_name} email={w.email} /></td>
            <td data-label="Amount" className={`${TD} text-right`}>
              <div className="min-w-0">
                <div className="num text-ink">{money(w.amount, w.currency)}</div>
                <div className="num text-xs text-ink-soft">
                  {`${w.fee > 0 ? `fee ${money(w.fee)} · ` : ''}pay ${money(w.net_amount)}`}
                </div>
              </div>
            </td>
            <td data-label="Destination" className={`${TD} break-all`}>
              <div className="min-w-0">
                <div className="text-ink">{w.destination_summary}</div>
                {w.txid && <div className="num text-xs text-ink-soft">tx {w.txid}</div>}
              </div>
            </td>
            <td data-label="Status" className={TD}><Status kind="withdrawals" status={w.status} note={w.decision_note} /></td>
            <td className={`${TD} text-right`}>
              <Actions>
                {control && w.status === 'requested' && (
                  <Button size="sm" disabled={busy} aria-label={`Approve withdrawal ${w.id}`} onClick={() => onApprove(w)}>Approve</Button>
                )}
                {control && w.status === 'approved' && (
                  <Button size="sm" disabled={busy} aria-label={`Mark withdrawal ${w.id} paid`} onClick={() => onPaid(w)}>Mark paid</Button>
                )}
                {control && (w.status === 'requested' || w.status === 'approved') && (
                  <Button variant="secondary" tone="loss" size="sm" disabled={busy} aria-label={`Reject withdrawal ${w.id}`} onClick={() => onReject(w)}>Reject</Button>
                )}
                <Button variant="ghost" tone="neutral" size="sm" aria-label={`Details of withdrawal ${w.id}`} onClick={() => onDetails(w)}>Details</Button>
              </Actions>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function TransfersTable({ rows, control, busy, show, onApprove, onDone, onReject, onDetails }: Common & {
  rows: PortalTransfer[]
  onApprove: (t: PortalTransfer) => void
  onDone: (t: PortalTransfer) => void
  onReject: (t: PortalTransfer) => void
  onDetails: (t: PortalTransfer) => void
}) {
  return (
    <table className="stack-table w-full text-sm">
      <Head cols={['Requested', 'Investor', ['Amount', 'right'], 'Movement', 'Status']} />
      <tbody>
        {rows.length === 0 && <EmptyRow colSpan={6} text={emptyText(show, 'transfers')} />}
        {rows.map((t) => (
          <tr key={t.id} className="border-b border-line last:border-0 align-top">
            <td data-label="Requested" className={`num ${TD}`}>{formatWhen(t.created_at)}</td>
            <td data-label="Investor" className={TD}><Who name={t.display_name} email={t.email} /></td>
            <td data-label="Amount" className={`${TD} text-right`}>
              <div className="min-w-0">
                <div className="num text-ink">{money(t.amount, t.currency)}</div>
                {t.source.kind === 'account' && !t.equity_verified && (
                  <div className="text-xs text-warn-deep">equity unverified</div>
                )}
                {t.source.kind === 'account' && t.equity_verified && t.equity_at_request != null && (
                  <div className="num text-xs text-ink-soft">of {money(t.equity_at_request)} equity</div>
                )}
              </div>
            </td>
            <td data-label="Movement" className={TD}>
              <div className="min-w-0">
                <div className="text-ink">{`${moneyRefLabel(t.source)} → ${moneyRefLabel(t.target)}`}</div>
                {t.note && <div className="text-xs text-ink-soft">{t.note}</div>}
              </div>
            </td>
            <td data-label="Status" className={TD}><Status kind="transfers" status={t.status} note={t.decision_note} /></td>
            <td className={`${TD} text-right`}>
              <Actions>
                {control && t.status === 'requested' && (
                  <Button variant="secondary" size="sm" disabled={busy} aria-label={`Approve transfer ${t.id}`} onClick={() => onApprove(t)}>Approve</Button>
                )}
                {control && (t.status === 'requested' || t.status === 'approved') && (
                  <>
                    <Button size="sm" disabled={busy} aria-label={`Mark transfer ${t.id} done`} onClick={() => onDone(t)}>Mark done</Button>
                    <Button variant="secondary" tone="loss" size="sm" disabled={busy} aria-label={`Reject transfer ${t.id}`} onClick={() => onReject(t)}>Reject</Button>
                  </>
                )}
                <Button variant="ghost" tone="neutral" size="sm" aria-label={`Details of transfer ${t.id}`} onClick={() => onDetails(t)}>Details</Button>
              </Actions>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function DestinationsTable({ rows, control, busy, show, onApprove, onReject, onDetails }: Common & {
  rows: PayoutDestination[]
  onApprove: (p: PayoutDestination) => void
  onReject: (p: PayoutDestination) => void
  onDetails: (p: PayoutDestination) => void
}) {
  return (
    <table className="stack-table w-full text-sm">
      <Head cols={['Added', 'Investor', 'Nickname', 'Destination', 'Status']} />
      <tbody>
        {rows.length === 0 && <EmptyRow colSpan={6} text={emptyText(show, 'payout accounts')} />}
        {rows.map((p) => (
          <tr key={p.id} className="border-b border-line last:border-0 align-top">
            <td data-label="Added" className={`num ${TD}`}>{formatWhen(p.created_at)}</td>
            <td data-label="Investor" className={TD}><Who name={p.display_name} email={p.email} /></td>
            <td data-label="Nickname" className={TD}>
              <div className="min-w-0">
                <div className="text-ink">{p.nickname}</div>
                <Badge tone="neutral" className="mt-1">{p.kind === 'bank' ? 'Bank' : 'Crypto'}</Badge>
              </div>
            </td>
            <td data-label="Destination" className={`num ${TD} break-all`}>{p.summary}</td>
            <td data-label="Status" className={TD}><Status kind="payout_destinations" status={p.status} note={p.decision_note} /></td>
            <td className={`${TD} text-right`}>
              <Actions>
                {control && p.status === 'pending' && (
                  <>
                    <Button size="sm" disabled={busy} aria-label={`Approve payout account ${p.id}`} onClick={() => onApprove(p)}>Approve</Button>
                    <Button variant="secondary" tone="loss" size="sm" disabled={busy} aria-label={`Reject payout account ${p.id}`} onClick={() => onReject(p)}>Reject</Button>
                  </>
                )}
                <Button variant="ghost" tone="neutral" size="sm" aria-label={`Details of payout account ${p.id}`} onClick={() => onDetails(p)}>Details</Button>
              </Actions>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
```

- [ ] **Step 4: Write `RequestDetailsDrawer.tsx`**

Create `dashboard/src/pages/requests/RequestDetailsDrawer.tsx`:

```tsx
import { useState, type ReactNode } from 'react'
import Badge from '../../components/Badge'
import Drawer from '../../components/Drawer'
import { formatWhen, money } from '../../lib/format'
import { BADGE_TONE, moneyOrDash, statusLabel, statusTone } from '../../lib/investor'
import type { PayoutDestination, PortalDeposit, PortalTransfer, PortalWithdrawal } from '../../lib/types'
import { KIND_WORD, moneyRefLabel } from './RequestTabs'

export type Details =
  | { kind: 'deposits'; row: PortalDeposit }
  | { kind: 'withdrawals'; row: PortalWithdrawal }
  | { kind: 'transfers'; row: PortalTransfer }
  | { kind: 'payout_destinations'; row: PayoutDestination }

/** Labels for the JSON detail keys a payout destination carries. */
const DETAIL_LABELS: Record<string, string> = {
  bank_name: 'Bank name',
  holder: 'Account holder',
  account_number: 'Account number',
  code: 'SWIFT / IFSC code',
  bank_address: 'Bank address',
  country: 'Country',
  coin: 'Coin',
  network: 'Network',
  address: 'Address',
}

function Row({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-ink-soft shrink-0">{label}</dt>
      <dd className={`text-ink text-right break-all ${mono ? 'num' : ''}`}>{value}</dd>
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="desk-label mb-2">{title}</h3>
      <dl className="inset p-3 space-y-1.5 text-sm">{children}</dl>
    </section>
  )
}

/**
 * The receipt or proof. The browser fetches it like any same-origin image,
 * so the session cookie travels with the request and the admin route
 * (`GET files/{id}`) answers. A PDF is served as a download, never inline,
 * so the <img> fails and the link underneath is the way in.
 */
function FilePreview({ orgId, fileId, label }: { orgId: number; fileId: number; label: string }) {
  const [failed, setFailed] = useState(false)
  const src = `/api/orgs/${orgId}/files/${fileId}`
  return (
    <section>
      <h3 className="desk-label mb-2">{label}</h3>
      <div className="inset p-3 space-y-2">
        {!failed && (
          <img src={src} alt={label} onError={() => setFailed(true)}
               className="max-h-96 w-auto max-w-full rounded-control border border-line" />
        )}
        {failed && (
          <p className="text-sm text-ink-soft">No inline preview for this file (a PDF downloads instead).</p>
        )}
        <a href={src} target="_blank" rel="noreferrer"
           className="block text-sm text-brand underline underline-offset-2 hover:text-brand-deep">
          Open file #{fileId} in a new tab
        </a>
      </div>
    </section>
  )
}

function InvestorSection({ name, email, userId }: { name?: string; email?: string; userId: number }) {
  return (
    <Section title="Investor">
      <Row label="Name" value={name ?? '—'} />
      <Row label="Email" value={email ?? '—'} />
      <Row label="User id" value={String(userId)} mono />
    </Section>
  )
}

function Audit({ row, extra }: {
  row: { created_at: string; decided_at: string | null; decided_by: number | null; decision_note: string | null }
  extra?: ReactNode
}) {
  return (
    <Section title="Audit trail">
      <Row label="Filed" value={formatWhen(row.created_at)} mono />
      {row.decided_at && (
        <Row label="Decided" mono
             value={`${formatWhen(row.decided_at)}${row.decided_by != null ? ` by user #${row.decided_by}` : ''}`} />
      )}
      {row.decision_note && <Row label="Decision note" value={row.decision_note} />}
      {extra}
    </Section>
  )
}

function DepositBody({ orgId, d }: { orgId: number; d: PortalDeposit }) {
  return (
    <div className="space-y-5">
      <Section title="Request">
        <Row label="Amount" value={money(d.amount, d.currency)} mono />
        <Row label="Fee" value={money(d.fee, d.currency)} mono />
        <Row label="Credited" value={d.credited_amount != null ? money(d.credited_amount, d.currency) : '—'} mono />
        <Row label="Method" value={`${d.method_label} (${d.method_kind})`} />
        <Row label="Reference" value={d.reference} mono />
        <Row label="Deposit to" value={d.target === 'account' ? `Trading account ${d.target_account_id ?? ''}` : 'My wallet'} />
        {d.note && <Row label="Investor note" value={d.note} />}
      </Section>
      <InvestorSection name={d.display_name} email={d.email} userId={d.user_id} />
      {d.receipt_file_id != null && <FilePreview orgId={orgId} fileId={d.receipt_file_id} label="Receipt" />}
      <Audit row={d} />
    </div>
  )
}

/** The JSON detail keys of a payout destination, one Row each, labelled
 *  through DETAIL_LABELS; account numbers, codes and addresses in the
 *  numeric face so they can be read digit by digit. */
function DetailRows({ details }: { details: Record<string, string> }) {
  return (
    <>
      {Object.entries(details).map(([key, value]) => (
        <Row key={key} label={DETAIL_LABELS[key] ?? key} value={value}
             mono={key === 'account_number' || key === 'code' || key === 'address'} />
      ))}
    </>
  )
}

/**
 * `destination` is the withdrawal's payout account, looked up by
 * `destination_id` in the page's `GET payout-destinations` list (admins get
 * `full=True`, so the bank account number is unmasked). It is what the admin
 * must pay to before clicking "Mark paid"; the summary alone ("ICICI ••4543")
 * is not enough to make a payment. Undefined when the list has no such row
 * (the destination was removed after the withdrawal settled), in which case
 * the summary is all that is left to show.
 */
function WithdrawalBody({ w, destination }: { w: PortalWithdrawal; destination: PayoutDestination | undefined }) {
  return (
    <div className="space-y-5">
      <Section title="Request">
        <Row label="Amount" value={money(w.amount, w.currency)} mono />
        <Row label="Fee" value={money(w.fee, w.currency)} mono />
        <Row label="Net to pay" value={money(w.net_amount, w.currency)} mono />
        <Row label="Destination" value={`${w.destination_summary} (${w.destination_kind})`} />
        {w.txid && <Row label="Transaction id" value={w.txid} mono />}
      </Section>
      <Section title="Destination details">
        {destination ? (
          <>
            <Row label="Nickname" value={destination.nickname} />
            <Row label="Kind" value={destination.kind === 'bank' ? 'Bank account' : 'Crypto address'} />
            <DetailRows details={destination.details} />
          </>
        ) : (
          <Row label="Payout account" value={w.destination_summary} mono />
        )}
      </Section>
      <InvestorSection name={w.display_name} email={w.email} userId={w.user_id} />
      <Audit row={w} extra={w.paid_at && (
        <Row label="Paid" mono
             value={`${formatWhen(w.paid_at)}${w.paid_by != null ? ` by user #${w.paid_by}` : ''}`} />
      )} />
    </div>
  )
}

function TransferBody({ t }: { t: PortalTransfer }) {
  return (
    <div className="space-y-5">
      <Section title="Request">
        <Row label="Amount" value={money(t.amount, t.currency)} mono />
        <Row label="From" value={moneyRefLabel(t.source)} />
        <Row label="To" value={moneyRefLabel(t.target)} />
        <Row label="Equity at request" value={moneyOrDash(t.equity_at_request, t.currency)} mono />
        <Row label="Equity verified" value={t.equity_verified ? 'Yes' : 'No'} />
        {t.note && <Row label="Investor note" value={t.note} />}
      </Section>
      <InvestorSection name={t.display_name} email={t.email} userId={t.user_id} />
      <Audit row={t} extra={t.done_at && (
        <Row label="Done" mono
             value={`${formatWhen(t.done_at)}${t.done_by != null ? ` by user #${t.done_by}` : ''}`} />
      )} />
    </div>
  )
}

function DestinationBody({ orgId, p }: { orgId: number; p: PayoutDestination }) {
  return (
    <div className="space-y-5">
      <Section title="Payout account">
        <Row label="Nickname" value={p.nickname} />
        <Row label="Kind" value={p.kind === 'bank' ? 'Bank account' : 'Crypto address'} />
        <Row label="Summary" value={p.summary} mono />
      </Section>
      <Section title="Destination details">
        <DetailRows details={p.details} />
      </Section>
      <InvestorSection name={p.display_name} email={p.email} userId={p.user_id} />
      {p.proof_file_id != null && <FilePreview orgId={orgId} fileId={p.proof_file_id} label="Proof" />}
      <Audit row={p} />
    </div>
  )
}

function Body({ orgId, details, destinations }: {
  orgId: number
  details: Details
  destinations: PayoutDestination[]
}) {
  switch (details.kind) {
    case 'deposits': return <DepositBody orgId={orgId} d={details.row} />
    case 'withdrawals': {
      const destination = destinations.find((p) => p.id === details.row.destination_id)
      return <WithdrawalBody w={details.row} destination={destination} />
    }
    case 'transfers': return <TransferBody t={details.row} />
    case 'payout_destinations': return <DestinationBody orgId={orgId} p={details.row} />
  }
}

/** One row's full story in the desk's Drawer; stays mounted (open=false)
 *  so the focus trap can hand focus back to the Details button on close.
 *  `destinations` is the page's full payout-destination list, which the
 *  withdrawal body searches for the account it pays to. */
export default function RequestDetailsDrawer({ orgId, details, destinations, onClose }: {
  orgId: number
  details: Details | null
  destinations: PayoutDestination[]
  onClose: () => void
}) {
  const word = details ? KIND_WORD[details.kind] : ''
  const title = details ? `${word.charAt(0).toUpperCase()}${word.slice(1)} #${details.row.id}` : ''
  return (
    <Drawer
      open={details != null}
      title={title}
      onClose={onClose}
      headerExtra={details && (
        <Badge tone={BADGE_TONE[statusTone(details.row.status)]}>{statusLabel(details.row.status)}</Badge>
      )}
    >
      {details && (
        <Body key={`${details.kind}-${details.row.id}`} orgId={orgId} details={details} destinations={destinations} />
      )}
    </Drawer>
  )
}
```

- [ ] **Step 5: Write `Requests.tsx`**

Create `dashboard/src/pages/Requests.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { orgApi } from '../lib/api'
import { useOrg } from '../lib/org'
import { can } from '../lib/roles'
import { useLiveRefresh } from '../hooks/useLiveRefresh'
import { errorText, money } from '../lib/format'
import Banner from '../components/Banner'
import Card from '../components/Card'
import ConfirmDialog from '../components/ConfirmDialog'
import Input from '../components/Input'
import Loading from '../components/Loading'
import PageHeader from '../components/PageHeader'
import Select from '../components/Select'
import Tabs from '../components/Tabs'
import {
  DepositsTable, DestinationsTable, REQUEST_KINDS, TransfersTable, WithdrawalsTable,
  isOpen, moneyRefLabel, tabItems, type RequestKind,
} from './requests/RequestTabs'
import RequestDetailsDrawer, { type Details } from './requests/RequestDetailsDrawer'
import type {
  PayoutDestination, PortalDeposit, PortalTransfer, PortalWithdrawal, RequestsSummary,
} from '../lib/types'

const POLL_MS = 10000
const AMOUNT = /^\d+(\.\d{1,2})?$/

/** What the admin is being asked to confirm. `requireText` blocks the
 *  confirm button until the note/txid box has something in it; `credited`
 *  (Confirm deposit only) adds the credited amount input, prefilled. */
interface Pending {
  title: string
  confirmLabel: string
  textLabel: 'Note' | 'Transaction ID'
  requireText: boolean
  danger?: boolean
  credited?: string
  run: (text: string, credited: string) => Promise<void>
}

function isKind(v: string | null): v is RequestKind {
  return REQUEST_KINDS.includes(v as RequestKind)
}

export default function Requests() {
  const { orgId, role } = useOrg()
  const [searchParams] = useSearchParams()
  // The Investors page's chips deep-link here with ?tab=<kind>.
  const [tab, setTab] = useState<RequestKind>(() => {
    const t = searchParams.get('tab')
    return isKind(t) ? t : 'deposits'
  })
  const [show, setShow] = useState<'open' | 'all'>('open')
  const [summary, setSummary] = useState<RequestsSummary | null>(null)
  const [deposits, setDeposits] = useState<PortalDeposit[]>([])
  const [withdrawals, setWithdrawals] = useState<PortalWithdrawal[]>([])
  const [transfers, setTransfers] = useState<PortalTransfer[]>([])
  const [destinations, setDestinations] = useState<PayoutDestination[]>([])
  const [pending, setPending] = useState<Pending | null>(null)
  const [text, setText] = useState('')
  const [credited, setCredited] = useState('')
  const [details, setDetails] = useState<Details | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // Until the first load lands, the empty lists are unknown, not empty.
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [s, d, w, t, p] = await Promise.all([
        orgApi<RequestsSummary>(orgId, 'requests/summary'),
        orgApi<PortalDeposit[]>(orgId, 'deposits'),
        orgApi<PortalWithdrawal[]>(orgId, 'withdrawals'),
        orgApi<PortalTransfer[]>(orgId, 'transfers'),
        orgApi<PayoutDestination[]>(orgId, 'payout-destinations'),
      ])
      setSummary(s); setDeposits(d); setWithdrawals(w); setTransfers(t); setDestinations(p)
      setError(null)
      setLoaded(true)
    } catch (err) {
      setError(errorText(err, 'Could not load the requests'))
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

  const open = (p: Pending) => { setPending(p); setText(''); setCredited(p.credited ?? '') }
  const closeDialog = () => { setPending(null); setText(''); setCredited('') }

  const decideDeposit = (d: PortalDeposit, status: 'confirmed' | 'rejected') => open({
    title: `${status === 'confirmed' ? 'Confirm' : 'Reject'} deposit of ${money(d.amount, d.currency)} from ${d.email}`,
    confirmLabel: status === 'confirmed' ? 'Confirm' : 'Reject',
    textLabel: 'Note', requireText: status === 'rejected', danger: status === 'rejected',
    credited: status === 'confirmed' ? (d.amount - d.fee).toFixed(2) : undefined,
    run: (note, creditedAmount) => act(async () => {
      const body = status === 'confirmed'
        ? { status, credited_amount: creditedAmount, note }
        : { status, note }
      await orgApi(orgId, `deposits/${d.id}/decision`, { method: 'POST', body: JSON.stringify(body) })
    }, `Deposit ${status}`),
  })

  const decideWithdrawal = (w: PortalWithdrawal, status: 'approved' | 'rejected') => open({
    title: `${status === 'approved' ? 'Approve' : 'Reject'} withdrawal of ${money(w.net_amount, w.currency)} to ${w.destination_summary} for ${w.email}`,
    confirmLabel: status === 'approved' ? 'Approve' : 'Reject',
    textLabel: 'Note', requireText: status === 'rejected', danger: status === 'rejected',
    run: (note) => act(async () => {
      await orgApi(orgId, `withdrawals/${w.id}/decision`, {
        method: 'POST', body: JSON.stringify({ status, note }) })
    }, `Withdrawal ${status}`),
  })

  const markPaid = (w: PortalWithdrawal) => open({
    title: `Record payment of ${money(w.net_amount, w.currency)} to ${w.destination_summary}`,
    confirmLabel: 'Mark paid', textLabel: 'Transaction ID', requireText: true,
    run: (txid) => act(async () => {
      await orgApi(orgId, `withdrawals/${w.id}/paid`, { method: 'POST', body: JSON.stringify({ txid }) })
    }, 'Withdrawal marked paid'),
  })

  const decideTransfer = (t: PortalTransfer, status: 'approved' | 'done' | 'rejected') => {
    const movement = `${money(t.amount, t.currency)} from ${moneyRefLabel(t.source)} to ${moneyRefLabel(t.target)}`
    open({
      title: status === 'approved' ? `Acknowledge transfer of ${movement}`
        : status === 'done' ? `Mark transfer of ${movement} done`
        : `Reject transfer of ${movement}`,
      confirmLabel: status === 'approved' ? 'Approve' : status === 'done' ? 'Mark done' : 'Reject',
      textLabel: 'Note', requireText: status === 'rejected', danger: status === 'rejected',
      run: (note) => act(async () => {
        await orgApi(orgId, `transfers/${t.id}/decision`, {
          method: 'POST', body: JSON.stringify({ status, note }) })
      }, `Transfer ${status}`),
    })
  }

  const decideDestination = (p: PayoutDestination, status: 'approved' | 'rejected') => open({
    title: `${status === 'approved' ? 'Approve' : 'Reject'} payout account ${p.nickname} (${p.summary}) for ${p.email}`,
    confirmLabel: status === 'approved' ? 'Approve' : 'Reject',
    textLabel: 'Note', requireText: status === 'rejected', danger: status === 'rejected',
    run: (note) => act(async () => {
      await orgApi(orgId, `payout-destinations/${p.id}/decision`, {
        method: 'POST', body: JSON.stringify({ status, note }) })
    }, `Payout account ${status}`),
  })

  const visible = <T extends { status: string }>(kind: RequestKind, rows: T[]): T[] =>
    show === 'open' ? rows.filter((r) => isOpen(kind, r.status)) : rows

  const confirmBlocked =
    (Boolean(pending?.requireText) && text.trim() === '') ||
    (pending?.credited != null && !AMOUNT.test(credited.trim()))

  return (
    <div className="space-y-6 max-w-5xl">
      <PageHeader
        title="Requests"
        subtitle="Every money request waiting on you: deposit notices to confirm, withdrawals to approve and pay, transfers to fund, payout accounts to vet. The app records; you move the funds."
        actions={
          <Select aria-label="Show" value={show} onChange={(e) => setShow(e.target.value as 'open' | 'all')}>
            <option value="open">Open</option>
            <option value="all">All</option>
          </Select>
        }
      />
      {error && <Banner kind="error" onDismiss={() => setError(null)}>{error}</Banner>}
      {notice && <Banner kind="notice" onDismiss={() => setNotice(null)}>{notice}</Banner>}

      <Tabs
        idBase="requests"
        label="Request types"
        value={tab}
        onChange={(k) => setTab(k as RequestKind)}
        items={tabItems(summary)}
      />

      {!loaded ? (
        // An error before the first load shows the banner above, not an
        // endless skeleton.
        !error && <Loading lines={6} label="Loading requests" />
      ) : (
        <div id="requests-panel" role="tabpanel" aria-labelledby={`requests-tab-${tab}`}>
          <Card inset>
            <div className="overflow-x-auto">
              {tab === 'deposits' && (
                <DepositsTable rows={visible('deposits', deposits)} control={control} busy={busy} show={show}
                               onConfirm={(d) => decideDeposit(d, 'confirmed')}
                               onReject={(d) => decideDeposit(d, 'rejected')}
                               onDetails={(row) => setDetails({ kind: 'deposits', row })} />
              )}
              {tab === 'withdrawals' && (
                <WithdrawalsTable rows={visible('withdrawals', withdrawals)} control={control} busy={busy} show={show}
                                  onApprove={(w) => decideWithdrawal(w, 'approved')}
                                  onReject={(w) => decideWithdrawal(w, 'rejected')}
                                  onPaid={markPaid}
                                  onDetails={(row) => setDetails({ kind: 'withdrawals', row })} />
              )}
              {tab === 'transfers' && (
                <TransfersTable rows={visible('transfers', transfers)} control={control} busy={busy} show={show}
                                onApprove={(t) => decideTransfer(t, 'approved')}
                                onDone={(t) => decideTransfer(t, 'done')}
                                onReject={(t) => decideTransfer(t, 'rejected')}
                                onDetails={(row) => setDetails({ kind: 'transfers', row })} />
              )}
              {tab === 'payout_destinations' && (
                <DestinationsTable rows={visible('payout_destinations', destinations)} control={control} busy={busy} show={show}
                                   onApprove={(p) => decideDestination(p, 'approved')}
                                   onReject={(p) => decideDestination(p, 'rejected')}
                                   onDetails={(row) => setDetails({ kind: 'payout_destinations', row })} />
              )}
            </div>
          </Card>
        </div>
      )}

      <ConfirmDialog
        open={pending != null}
        title={pending?.title ?? ''}
        confirmLabel={pending?.confirmLabel ?? 'Confirm'}
        danger={pending?.danger}
        busy={busy}
        disabled={confirmBlocked}
        onConfirm={async () => {
          if (!pending) return
          const run = pending.run
          const value = text.trim()
          const creditedValue = credited.trim()
          closeDialog()
          await run(value, creditedValue)
        }}
        onCancel={closeDialog}
      >
        {pending?.credited != null && (
          <label className="block">
            <span className="desk-label block mb-1">Credited amount</span>
            <Input aria-label="Credited amount" num inputMode="decimal" value={credited}
                   onChange={(e) => setCredited(e.target.value)} />
            <span className="block mt-1 text-xs text-ink-soft">
              Prefilled with the amount less the method fee. Change it when what arrived differs.
            </span>
          </label>
        )}
        <label className="block">
          <span className="desk-label block mb-1">
            {pending?.textLabel}{pending?.requireText ? '' : ' (optional)'}
          </span>
          <textarea aria-label={pending?.textLabel} value={text} rows={2}
                    onChange={(e) => setText(e.target.value)}
                    className="w-full rounded border border-line-strong px-3 py-2 text-sm bg-card text-ink" />
        </label>
      </ConfirmDialog>

      {/* The full destination list rides along so a withdrawal's drawer can
          show the unmasked account the admin pays to. */}
      <RequestDetailsDrawer orgId={orgId} details={details} destinations={destinations}
                            onClose={() => setDetails(null)} />
    </div>
  )
}
```

- [ ] **Step 6: Export the page from the admin chunk and route it**

In `dashboard/src/pages/groups/admin.ts`, after the line

```ts
export { default as Investors } from '../Investors'
```

add:

```ts
export { default as Requests } from '../Requests'
```

In `dashboard/src/App.tsx`, after the line

```ts
const Investors = pick(admin, 'Investors')
```

add:

```ts
const Requests = pick(admin, 'Requests')
```

and after the route line

```tsx
              <Route path="investors" element={<Investors />} />
```

add:

```tsx
              <Route path="requests" element={<Requests />} />
```

(Tasks 12–17 have already renamed the investor `pick` consts and routes in this file; the two `Investors` anchor lines above are untouched by them.)

- [ ] **Step 7: Run the test to verify it passes**

Run: `npx vitest run src/pages/Requests.test.tsx`
Expected: PASS, 11 passed.

- [ ] **Step 8: Run the whole dashboard gate**

Run (from `dashboard/`): `node scripts/palette_check.mjs && npx tsc --noEmit -p tsconfig.app.json && npx vitest run --maxWorkers=2 --minWorkers=1`
Expected: the palette prover prints no failing pair, `tsc` prints nothing, vitest all green. The nav tests from Task 12 already expect the `Requests` link; `Layout.test.tsx` renders the route tree lazily and needs no change.

- [ ] **Step 9: Commit**

```bash
git add dashboard/src/pages/Requests.tsx dashboard/src/pages/Requests.test.tsx dashboard/src/pages/requests/RequestTabs.tsx dashboard/src/pages/requests/RequestDetailsDrawer.tsx dashboard/src/pages/groups/admin.ts dashboard/src/App.tsx
git commit -m "feat(dashboard): Requests desk -- deposits, withdrawals, transfers and payout accounts with a details drawer

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
```

---

### Task 20: Gates — full suites, copier untouched, spec status, deploy notes, plan checkboxes

Nothing new is built here. Every gate below must pass before the branch is offered for
review; each step names the exact command and what "green" looks like. Deploy itself is
not part of this task: the owner triggers it, and the README paragraph written here is
what they will follow.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-29-client-portal-phase-1-money-design.md` (the `**Status:**` line)
- Modify: `README.md` (the Backups bullet; a new "Upgrading with a migration" subsection before `## 6. Development`)
- Modify: `.env.example` (only if Task 4's `UPLOAD_DIR` block is missing)
- Modify: `docs/superpowers/plans/2026-09-29-client-portal-phase-1.md` (every step checkbox ticked)

**Interfaces:**
- Consumes: the API test command and the dashboard test command from the plan's Global constraints; `UPLOAD_DIR` (`ApiConfig.upload_dir`, Task 4); the `uploads` compose volume and `ops/backup.sh` tar step (Task 4); migration `022_client_wallets.sql` (Task 1).
- Produces: nothing for later tasks; the branch is ready for `superpowers:finishing-a-development-branch`.

- [ ] **Step 1: Run the full API suite**

From `api/` in Git Bash, with Docker Desktop running and `docker compose up -d postgres` done from the repo root (password from the repo-root `.env`, key `POSTGRES_PASSWORD`):

```bash
export TEST_POSTGRES_ADMIN_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader"
export TEST_POSTGRES_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader_test"
export PYTHONPATH="$(pwd -W)/src"
.venv/Scripts/python -m pytest tests -q -p no:cacheprovider
```

Expected: the summary line reads `<N> passed, 1 failed, 7 errors` where the 7 errors are all in `tests/test_events_ws.py` (psycopg async on the Windows ProactorEventLoop) and the 1 failure is the EA-download CRLF test; both are pre-existing on Windows and pass in the Linux api container. Any other red line is a defect in Tasks 1–10 and must be fixed in that task's files (with a `fix(api): …` commit) before going on. In particular, every test file this plan added must be listed green: `test_migration_022.py`, `test_portal_ledger.py`, `test_mpin_core.py`, `test_uploads.py`, `test_portal_common.py`, `test_portal_methods.py`, `test_portal_deposits.py`, `test_portal_withdrawals.py`, `test_portal_transfers.py`, `test_portal_summary.py`, and `test_mpin.py` must still pass against `mpin_core`.

To confirm the count of pre-existing reds is exactly eight:

```bash
.venv/Scripts/python -m pytest tests -q -p no:cacheprovider 2>&1 | grep -E '^(FAILED|ERROR) ' | grep -v -E 'test_events_ws.py|download' | wc -l
```

Expected output: `0`.

`pytest -q` prints dots, not file names, so the "every file this plan added is green" check above needs its own run naming those files (same env vars as the first command):

```bash
.venv/Scripts/python -m pytest tests/test_migration_022.py tests/test_portal_ledger.py tests/test_mpin_core.py tests/test_uploads.py tests/test_portal_common.py tests/test_portal_methods.py tests/test_portal_deposits.py tests/test_portal_withdrawals.py tests/test_portal_transfers.py tests/test_portal_summary.py tests/test_mpin.py -q -p no:cacheprovider
```

Expected: the summary line reads `<N> passed` with no `failed`, no `error` and no `ERROR: file or directory not found` (a missing file means a task's test file was never written; go back to that task). Any red here is a defect in Tasks 1–10, fixed in that task's files with a `fix(api): …` commit before going on.

- [ ] **Step 2: Run the full dashboard gate**

From `dashboard/`:

```bash
npm test
```

Expected: `palette_check.mjs` prints its pass line for both themes, `tsc --noEmit -p tsconfig.app.json` prints nothing, and `vitest run` ends with every file green (`Test Files  N passed (N)`). If vitest runs out of memory on this machine, run the three parts by hand instead, which is the same gate:

```bash
node scripts/palette_check.mjs && npx tsc --noEmit -p tsconfig.app.json && npx vitest run --maxWorkers=2 --minWorkers=1
```

Expected: the same three green results. Two source scans must also be clean (they are what `vocabulary.test.ts` enforces, restated so a failure is understood at once):

```bash
grep -rn "Loading\.\.\." src/pages src/components --include='*.tsx' | grep -v '\.test\.tsx' ; echo "exit=$?"
```

Expected: no lines, then `exit=1` (grep found nothing).

- [ ] **Step 3: Prove the copier is untouched**

From the repo root:

```bash
git diff --stat main.. -- copier/
git diff --stat main.. -- copier/ | wc -l
```

Expected: the first command prints nothing; the second prints `0`. If either shows a file, revert it (`git checkout main -- copier/<file>` and commit `chore: revert stray copier change`), because no task in this plan may touch `copier/`.

- [ ] **Step 4: Flip the spec's status line**

In `docs/superpowers/specs/2026-09-29-client-portal-phase-1-money-design.md` line 4 currently reads (it may have been edited by the owner since; replace whatever the `**Status:**` line says):

```markdown
**Status:** draft, awaiting the owner's review
```

Replace it with:

```markdown
**Status:** implemented on branch `client-portal` on 2026-09-29 (Tasks 1–20 of `docs/superpowers/plans/2026-09-29-client-portal-phase-1.md`, API and dashboard suites green locally); not yet deployed — migration 022 and the `uploads` volume mean the "Upgrading with a migration" sequence in README.md, on the owner's say-so.
```

Verify: `grep -n '^\*\*Status:\*\*' docs/superpowers/specs/2026-09-29-client-portal-phase-1-money-design.md` prints exactly one line, the new one.

- [ ] **Step 5: README — the uploads volume and the migration sequence**

In `README.md`, replace the Backups bullet (currently):

```markdown
- **Backups** — all durable state lives in the `postgres` Docker volume
  (`pgdata`). Back that volume up (or the underlying Postgres data via
  `pg_dump`) on whatever schedule matches your risk tolerance; there is no
  other persistent state to capture.
```

with:

```markdown
- **Backups** — durable state lives in two Docker volumes: `pgdata` (Postgres)
  and `uploads` (the deposit receipts and payout proofs the client portal
  stores under `/data/uploads` on the `api` service; `UPLOAD_DIR` in
  `.env.example`). `ops/backup.sh` dumps the database and tars the uploads
  volume next to it every night; there is no other persistent state to
  capture.
```

Then, directly above the line `## 6. Development`, insert this subsection (a blank line on each side; the outer fence below is four backticks only so the inner `bash` block survives):

````markdown
### Upgrading with a migration

`docker compose build api` alone never applies a new file in `db/migrations/`:
the migrations are baked into the `migrate` image, so that image must be rebuilt
and run, and the `api` container must be stopped first so the old code never
runs against the new schema. The full sequence on the host is:

```bash
cd ~/mirrorfleet && git pull
sudo docker compose build migrate api
sudo docker compose stop api
sudo docker compose run --rm migrate          # prints: applied: ['022_client_wallets.sql']
sudo docker compose up -d api
```

Then hard-reload any open dashboard tab (the old bundle is stale). Without a
new migration the short form is `sudo docker compose build api && sudo docker
compose up -d api`. The first deploy of the client portal also creates the
`uploads` volume (compose does it; nothing to add to `.env`, `UPLOAD_DIR` is
optional), and the admin must add at least one payment method on the
Investors page before investors can deposit.
````

Verify: `grep -n 'Upgrading with a migration\|uploads' README.md` prints the new heading and the two volume mentions.

- [ ] **Step 6: Check `.env.example` documents `UPLOAD_DIR`**

```bash
grep -n 'UPLOAD_DIR' .env.example
```

Expected: at least one line, the block Task 4 added. If it prints nothing, append to `.env.example` (after the `COOKIE_SECURE=true` line):

```bash
# Where the api stores uploaded files (deposit receipts, payout proofs).
# Inside the container this is the `uploads` named volume mounted at
# /data/uploads; leave empty to use the image default. For a local
# `uvicorn` run outside Docker the default is ./data/uploads.
UPLOAD_DIR=
```

and include the file in Step 8's commit. Also confirm the volume is in compose: `grep -n 'uploads' docker-compose.yml` prints the `uploads:/data/uploads` mount on `api` and the `uploads: {}` entry under `volumes:`.

- [ ] **Step 7: Tick every step in the plan**

The plan this part belongs to is `docs/superpowers/plans/2026-09-29-client-portal-phase-1.md`. That file is the assembled plan (`.superpowers/plan-parts/header.md` + `part-A.md` … `part-F.md`, in order); `.superpowers/` is gitignored, so the assembled copy must exist at that path and be committed on the branch — it is what Step 4's status line and Step 8's commit reference. Check it is there before touching it, then tick every step:

```bash
test -f docs/superpowers/plans/2026-09-29-client-portal-phase-1.md || { echo 'assemble the plan from .superpowers/plan-parts/part-*.md into that path first'; exit 1; }
git ls-files --error-unmatch docs/superpowers/plans/2026-09-29-client-portal-phase-1.md
sed -i 's/^- \[ \] \*\*Step/- [x] **Step/' docs/superpowers/plans/2026-09-29-client-portal-phase-1.md
grep -c '^- \[ \]' docs/superpowers/plans/2026-09-29-client-portal-phase-1.md
```

Expected: the `test -f` prints nothing (if it prints the assemble message and exits 1, build the file with `cat .superpowers/plan-parts/header.md .superpowers/plan-parts/part-{A,B,C,D,E,F}.md > docs/superpowers/plans/2026-09-29-client-portal-phase-1.md` and `git add` it, then rerun); `git ls-files --error-unmatch` prints the path (it is tracked — if it errors with `did not match any file(s) known to git`, `git add` it now, Step 8 commits it); the `grep -c` prints `0`. (A step left unticked on purpose, because it was skipped, is a defect: go back and do it.)

- [ ] **Step 8: Final commit and branch check**

```bash
git add README.md docs/superpowers/specs/2026-09-29-client-portal-phase-1-money-design.md docs/superpowers/plans/2026-09-29-client-portal-phase-1.md
git add .env.example   # only if Step 6 changed it
git commit -m "docs: client portal phase 1 -- spec status, upgrade runbook with the uploads volume, plan checkboxes ticked

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b"
git status --short
git log --oneline main..HEAD | wc -l
```

Expected: `git status --short` prints nothing (clean tree); the commit count is at least 20 (one per task plus the three docs commits already on the branch). The branch is ready for `superpowers:finishing-a-development-branch`; the deploy waits for the owner.

