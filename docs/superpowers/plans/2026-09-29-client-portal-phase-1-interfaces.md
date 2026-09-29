# Client portal phase 1 — interfaces and task map

Companion to `docs/superpowers/plans/2026-09-29-client-portal-phase-1.md`. Every task in
the plan consumes and produces exactly the names below. A plan writer may add private
helpers, never rename or reshape anything here. Spec:
`docs/superpowers/specs/2026-09-29-client-portal-phase-1-money-design.md`. Code facts:
`docs/reference/mirrorfleet-subsystem-maps.md`.

## Global constraints (copied into the plan header)

- Branch `client-portal`, off main da485e2. Commit after every task; every commit ends
  with the two trailer lines
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01Lz2HHZEdtFN7DEfGj6Pi3b`.
  Subjects: `feat(api): …`, `feat(dashboard): …`, `test: …`, `docs: …`, `chore: …`.
- `copier/` is never touched. Task 20 proves it.
- API tests, Git Bash from `api/`, Docker Desktop running, `docker compose up -d postgres`
  first: `export TEST_POSTGRES_ADMIN_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader"; export TEST_POSTGRES_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader_test"; export PYTHONPATH="$(pwd -W)/src"; .venv/Scripts/python -m pytest tests -q -p no:cacheprovider`
  (`POSTGRES_PASSWORD` from the repo-root `.env`). Use `127.0.0.1`, never `localhost`.
  Seven `test_events_ws.py` errors and one EA-download CRLF failure are pre-existing on
  Windows; ignore them. New dependencies: `.venv/Scripts/python -m pip install -e ".[dev]"`.
- Dashboard tests from `dashboard/`: `npm test` (= palette prover, `tsc --noEmit -p
  tsconfig.app.json`, `vitest run`); on this machine run vitest as
  `npx vitest run --maxWorkers=2 --minWorkers=1`; a single file: `npx vitest run <path>`.
- Money: `NUMERIC(18,2)` in SQL, `Decimal` in Python, floats rounded to cents in JSON,
  strings from forms; `available` is floored to cents (`ROUND_DOWN`), never rounded up.
- Every new table gets a row in the `db` fixture TRUNCATE list in `api/tests/conftest.py`.
- Every new route audits an `events` row (category `control`) with the action names in
  section 13 of the spec; investor-scoped actions start with `investor_` and carry
  `payload.user_id`.
- Dashboard: colour only through `--color-*` tokens and the primitives (Button, Input,
  Select, Badge, Banner, Card, Tabs, Drawer, ConfirmDialog, StatTile, PageHeader,
  Loading, Menu, PinInput); data never sits directly on `.glass`; tables are
  `stack-table` with `data-label` on every `td`; the one `h1` comes from `PageHeader`;
  never the words "Slave"/"slave" in copy (say "follower"); never a literal
  "Loading..."; every colour pair new to the prover gets a row in
  `scripts/palette_check.mjs`. Test files use `mockUseOrg` from `src/test/orgMock.tsx`
  and stub `fetch` by URL tail.
- Deploy is not part of the plan; the owner triggers it. Task 20 documents the runbook.

## Task map

| # | Task | Part | Produces for later tasks |
|---|---|---|---|
| 1 | Migration `022_client_wallets.sql`, `test_migration_022.py`, conftest TRUNCATE list, delete `tests/test_investor_portal.py` | A | tables |
| 2 | `api/src/api/portal_ledger.py` + `tests/test_portal_ledger.py` | A | ledger rules |
| 3 | `api/src/api/mpin_core.py`, `routes/mpin.py` imports it, `tests/test_mpin_core.py` | A | `check_mpin`, `require_mpin` |
| 4 | Uploads: `config.upload_dir`, `api/src/api/uploads.py`, `routes/portal_files.py`, compose volume, Dockerfile dir, backup, `.env.example`, `pyproject` multipart, `tests/test_uploads.py` | A | `files` rows, `UploadStore` |
| 5 | `api/src/api/portal_common.py` (figures, settle, linked account, equity, audit, notify, serialisers) + `tests/test_portal_common.py` | B | shared helpers |
| 6 | `routes/portal_admin.py` and `routes/portal_investor.py` created with payment methods, portal settings, `investor/payment-methods`; `main.py` includes them; `tests/test_portal_methods.py` | B | the two routers |
| 7 | Deposits (investor list/create/cancel; admin list/decision incl. target-account transfer) + `tests/test_portal_deposits.py` | B | |
| 8 | Payout destinations + withdrawals (MPIN step-up, caps, fees; admin decisions, paid) + `tests/test_portal_withdrawals.py` | C | |
| 9 | Transfers (pair rules, caps, instant wallet→wallet, admin decision) + `tests/test_portal_transfers.py` | C | |
| 10 | `investor/summary`, `investor/wallet-entries`, admin `investors`, `investors/{id}/wallet-entries`, adjustments, `requests/summary`, positions/analytics/history moved; alert and Telegram rules; delete `routes/investor.py`, `investor_ledger.py`, `tests/test_investor_ledger.py`; `tests/test_portal_summary.py` | C | complete API |
| 11 | Dashboard foundation: `lib/types.ts`, `lib/investor.ts`, `lib/hideBalances.ts`, `components/Money.tsx`, `lib/api.ts` `apiUpload`, `components/FileInput.tsx`, `components/PinConfirmDialog.tsx`, `src/test/portalFixtures.ts`, tests | D | primitives |
| 12 | Navigation: `NavItem.badge`, `investorNav` groups, `bottomBarItems`, BottomBar More for investors, `hooks/useRequestsBadge.ts`, Layout wiring, `adminNav` Requests; nav/Layout/BottomBar tests | D | shell |
| 13 | `pages/investor/InvestorDashboard.tsx` (route `invest`), delete `InvestorOverview.tsx` + test | E | |
| 14 | `pages/investor/InvestorDeposit.tsx` rewrite | E | |
| 15 | `pages/investor/InvestorWithdraw.tsx` rewrite + `InvestorPayoutAccounts.tsx` | E | |
| 16 | `pages/investor/InvestorTransfer.tsx` + `InvestorWallet.tsx` | E | |
| 17 | `pages/investor/InvestorTransactions.tsx` + `InvestorAccount.tsx` extension (positions, analytics) | E | |
| 18 | `pages/Investors.tsx` rewrite with `pages/investors/{PaymentMethodsTab,LedgerDrawer,AdjustDialog}.tsx` | F | |
| 19 | `pages/Requests.tsx` + `pages/requests/{RequestTabs,RequestDetailsDrawer}.tsx` | F | |
| 20 | Gates: full API suite, `npm test`, copier untouched, spec status line, README deploy notes, `.env.example` check, plan checkboxes | F | |

Tasks run in number order. Between Task 1 and Task 10 the old investor endpoints are
broken at runtime because their tables are gone; nothing deploys in between, and Task 1
deletes their test file so the suite stays green.

## Database (Task 1)

Exactly the tables, columns, checks and indexes of spec section 5, in this order:
`files`, `payment_methods`, `portal_settings`, `payout_destinations`, `wallet_entries`,
`deposits`, `withdrawals`, `transfers`, then the data copy, then
`DROP TABLE investor_withdrawals; DROP TABLE investor_deposits; DROP TABLE org_investor_wallets;`.
Constraint names: `<table>_<column>_check` for CHECKs written inline is fine; the named
ones the tests assert are `deposits_one_live_reference`, `wallet_entries_one_per_ref`,
`transfers_queue`, `withdrawals_queue`, `deposits_queue`, `payout_destinations_queue`,
`payment_methods_by_org`, `wallet_entries_by_user`, `files_by_user`.

conftest `db` TRUNCATE list (new full list, in this order):
`transfers, withdrawals, deposits, wallet_entries, payout_destinations, portal_settings,
payment_methods, files, events, portfolio_snapshots, mappings, symbol_cache, executions,
positions, deals, deal_backfill_state, balance_samples, accounts, ctid_connections,
oauth_states, org_invites, org_memberships, orgs, users`.

## Python modules

### `api/src/api/portal_ledger.py` (Task 2) — pure, no DB

```python
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
WALLETS: tuple[str, ...] = ("main", "credit", "pamm", "social")
CENT = Decimal("0.01")
class LedgerError(ValueError): ...
def parse_amount(raw: object, field: str = "amount") -> Decimal      # moved verbatim from investor_ledger
def clean_text(raw: object, field: str, max_len: int = 128, required: bool = True) -> str | None  # moved verbatim
def floor_cents(value: Decimal) -> Decimal                            # quantize(CENT, ROUND_DOWN)
def round_cents(value: Decimal) -> Decimal                            # quantize(CENT, ROUND_HALF_UP)
def fee_for(amount: Decimal, fee_pct: Decimal) -> Decimal             # round_cents(amount * fee_pct / 100)
def balance(entries: list[tuple[str, Decimal]]) -> dict[str, Decimal] # [(wallet, amount)] -> every WALLETS key, Decimal("0") default
def holds(open_withdrawals: list[Decimal], open_transfers: list[tuple[str, Decimal]]) -> dict[str, Decimal]
    # withdrawals hold on 'main'; transfers are [(source_wallet, amount)]
def available(balance: Decimal, hold: Decimal) -> Decimal             # floor_cents(balance - hold), never below zero? NO: may be negative after an adjustment; return floor_cents(balance - hold)
DEPOSIT_TRANSITIONS = {"pending": {"confirmed", "rejected", "cancelled"}}
WITHDRAWAL_TRANSITIONS = {"requested": {"approved", "rejected", "cancelled"}, "approved": {"paid", "rejected"}}
TRANSFER_TRANSITIONS = {"requested": {"approved", "done", "rejected", "cancelled"}, "approved": {"done", "rejected"}}
DESTINATION_TRANSITIONS = {"pending": {"approved", "rejected", "removed"}, "approved": {"removed"}}
def can_transition(table: str, current: str, new: str) -> bool        # table in deposits|withdrawals|transfers|payout_destinations
INVESTOR_MOVES = {"cancelled", "removed"}                             # only the owner may make these
TRANSFER_PAIRS: frozenset[tuple[str, str]] = frozenset({("main", "account"), ("account", "main"), ("pamm", "main"), ("social", "main")})
def transfer_pair(source: dict, target: dict) -> tuple[str, str]      # ("main","account") etc.; raises LedgerError("that transfer is not allowed")
def money(value: Decimal | None) -> float | None                      # float(round_cents(value)) or None
```

### `api/src/api/mpin_core.py` (Task 3)

```python
MPIN_RE = re.compile(r"^[0-9]{6}$"); MPIN_MAX_ATTEMPTS = 5; MPIN_LOCK_MINUTES = 15
def check_mpin(conn, user_id: int, mpin: str) -> Optional[Response]    # moved from routes/mpin.py unchanged (same bodies: 409 "MPIN not set", 423 {"detail":"MPIN locked","locked_until"}, 401 {"detail":"Invalid MPIN","attempts_left"})
def require_mpin(conn, user_id: int, mpin: object) -> Optional[Response]
    # not a str or not MPIN_RE -> JSONResponse(400, {"detail": "MPIN must be exactly 6 digits"}); else check_mpin
def audit_auth(conn, user_id: int, action: str) -> None                # the old _audit, moved
```
`routes/mpin.py` keeps every route and body unchanged and imports these.

### `api/src/api/uploads.py` (Task 4)

```python
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "application/pdf": "pdf"}
PHASE1_PURPOSES = {"deposit_receipt", "payout_proof"}
ALL_PURPOSES = PHASE1_PURPOSES | {"kyc_document", "kyc_photo", "ticket_attachment", "avatar"}
UPLOADS_PER_HOUR = 30
def detect_type(head: bytes) -> tuple[str, str] | None                # (content_type, ext) from magic bytes; None if unknown
class UploadStore:
    def __init__(self, root: Path) -> None                            # mkdir parents
    def key(self, org_id: int, file_id: int, ext: str) -> str         # f"{org_id}/{file_id}.{ext}"
    def write(self, key: str, data: bytes) -> None                    # atomic (tmp + replace)
    def path(self, key: str) -> Path                                  # refuses '..' and absolute keys
    def read(self, key: str) -> bytes
```
`ApiConfig.upload_dir: str` from env `UPLOAD_DIR`, default `./data/uploads`; the api
Dockerfile sets `ENV UPLOAD_DIR=/data/uploads` and creates it for `appuser`;
`docker-compose.yml` mounts named volume `uploads:/data/uploads` on `api`;
`app.state.uploads = UploadStore(Path(cfg.upload_dir))` in `create_app`.

### `api/src/api/routes/portal_files.py` (Task 4)

```python
def create_portal_files_router() -> APIRouter    # prefix /api/orgs/{org_id}
# POST investor/files   (require_org_role("investor"), multipart fields purpose, file) -> 201 {id, purpose, content_type, size_bytes, created_at}
#   400 "unsupported file type" | "file too large (5 MB max)" | "purpose is not accepted yet" | "file is empty"; 429 "too many uploads; try again later"
# GET  investor/files/{file_id}  (investor; owner only, else 404 "File not found") -> bytes
# GET  files/{file_id}           (admin; any file in org, else 404) -> bytes
# Headers on both GETs: Content-Type from row; Content-Disposition inline (attachment for pdf); X-Content-Type-Options: nosniff; Cache-Control: private, max-age=0
def file_belongs(conn, org_id: int, user_id: int, file_id: int | None, purpose: str) -> bool   # helper other routers call before storing a file id
```

### `api/src/api/portal_common.py` (Task 5)

```python
from .portal_ledger import *  # re-export names for route modules
async def audit_control(conn, *, org_id: int, action: str, actor_email: str, user_id: int, severity: str = "info", account_id: int | None = None, **detail) -> None
    # INSERT INTO events (org_id, account_id, category 'control', severity, payload {action, user_id, **detail}, actor_email)
async def notify_investor(conn, request: Request, user_id: int, subject: str, text: str) -> None   # best effort; looks up email; broadcaster.alerter.send_to
def linked_account(conn, org_id: int, user_id: int) -> int | None       # accounts.investor_user_id
async def equity_for(request: Request, conn, org_id: int, account_id: int | None) -> tuple[Decimal | None, str, list]   # (equity, source 'live'|'last known'|'unknown', positions) moved from routes/investor.py _equity_from
def wallet_balances(conn, org_id: int, user_id: int) -> dict[str, Decimal]
def wallet_holds(conn, org_id: int, user_id: int) -> dict[str, Decimal]
def wallet_figures(conn, org_id: int, user_id: int) -> dict[str, dict[str, Decimal]]   # {wallet: {"balance","on_hold","available"}}
def open_account_transfers_out(conn, org_id: int, user_id: int, account_id: int) -> Decimal   # requested+approved account->wallet
def net_funded(conn, org_id: int, user_id: int, account_id: int) -> Decimal            # done wallet->account minus done account->wallet
def settle(conn, *, org_id: int, user_id: int, wallet: str, amount: Decimal, kind: str, ref_table: str | None, ref_id: int | None, note: str | None = None, created_by: int | None = None) -> bool
    # INSERT wallet_entries ... ON CONFLICT (ref_table, ref_id, wallet) WHERE ref_table IS NOT NULL DO NOTHING; returns True when a row was written
def portal_settings(conn, org_id: int) -> dict        # {"withdrawal_min": Decimal, "withdrawal_fee_pct": Decimal}; inserts the default row when missing
def destination_summary(kind: str, details: dict) -> str   # bank "<bank_name> ••<last4>", crypto "<network> <shortAddress>"
def short_address(a: str) -> str                      # len<=8 unchanged else f"{a[0]}…{a[-2:]}"
# Column lists and serialisers (every serialiser adds "currency": "USD"):
DEPOSIT_COLS, WITHDRAWAL_COLS, TRANSFER_COLS, DESTINATION_COLS, ENTRY_COLS, METHOD_COLS: str
def deposit_json(row) -> dict; def withdrawal_json(row) -> dict; def transfer_json(row) -> dict   # transfer_json nests source/target objects
def destination_json(row, *, full: bool) -> dict      # full=False masks bank account_number to last 4 in details
def entry_json(row) -> dict; def method_json(row, *, public: bool) -> dict   # public=True omits enabled/sort_order? NO: public omits nothing but disabled rows are never listed
class Decision(BaseModel): status: str; note: str | None = None
def require_note_on_reject(status: str, note: str | None) -> str | None   # clean_text(note, "note", 500, required=(status in {"rejected"}))
```

### `api/src/api/routes/portal_investor.py` (Tasks 6–10)

`create_portal_investor_router() -> APIRouter`, prefix `/api/orgs/{org_id}`, every route
`ctx: OrgContext = Depends(require_org_role("investor"))`. Pydantic bodies:

```python
class DepositNotice(BaseModel): method_id: int; amount: Any; reference: str; receipt_file_id: int | None = None; target: str = "wallet"; target_account_id: int | None = None; note: str | None = None
class DestinationBody(BaseModel): kind: str; nickname: str; details: dict; proof_file_id: int | None = None; mpin: Any = None
class WithdrawalRequest(BaseModel): destination_id: int; amount: Any; mpin: Any = None
class MoneyRef(BaseModel): kind: str; wallet: str | None = None; account_id: int | None = None
class TransferRequest(BaseModel): source: MoneyRef; target: MoneyRef; amount: Any; mpin: Any = None
```
Rate limits: one `LoginRateLimiter(max_attempts=10, window_s=3600)` per router with keys
`portal-deposit:{org}:{user}`, `portal-withdrawal:{org}:{user}`, `portal-transfer:{org}:{user}`,
`portal-destination:{org}:{user}`; message `too many requests; try again later`.

Routes and refusal strings (exact):

- `GET investor/payment-methods` → `[method_json]` where `enabled`, ordered `sort_order, id`.
- `GET investor/deposits` → newest first. `POST investor/deposits` → 201: 404 `Payment method not found` (unknown, disabled, other org); 400 `minimum deposit for this method is {min:.2f}`; 400 `receipt is required for bank deposits`; 400 `receipt file not found` (file_belongs false); 400 `target must be wallet or account`; 409 `no account linked yet` (target account without link); 409 `A notice with this reference already exists`; 429. `POST investor/deposits/{id}/cancel` → 404 `Deposit not found`, 409 `deposit is already {status}`.
- `GET investor/payout-destinations` (status ≠ removed, `full=True`). `POST` → 201: MPIN first; 400 `kind must be bank or crypto`; 400 `<field> is required` for missing detail keys (bank: bank_name, holder, account_number, code; crypto: coin, network, address); 400 `proof file not found`; 429. `POST investor/payout-destinations/{id}/remove` → 404 `Payout account not found`; 409 `a withdrawal is still using this payout account` (open withdrawal references it); 409 `payout account is already removed`.
- `GET investor/withdrawals`. `POST investor/withdrawals` → 201: MPIN first; 404 `Payout account not found` (not owner, not approved); 400 `minimum withdrawal is {min:.2f}`; 400 `amount exceeds what is available ({available:.2f})`; 429. `POST .../cancel` → 404 `Withdrawal not found`, 409 `withdrawal is already {status}`.
- `GET investor/transfers`. `POST investor/transfers` → 201: MPIN first; 400 `that transfer is not allowed`; 409 `no account linked yet`; 404 `Account not found` (account_id not the linked one); 400 `amount exceeds what is available ({available:.2f})` (wallet source) or `amount exceeds the account's available equity ({available:.2f})` (account source, when equity known); 429. Wallet→wallet pairs are inserted with status `done`, `done_at now()`, `done_by NULL`, and settled in the same transaction. `POST .../cancel` → 404 `Transfer not found`, 409 `transfer is already {status}`.
- `GET investor/wallet-entries?wallet=&kind=&from=&to=&limit=&before=` → `{"entries": [entry_json], "has_more": bool, "next_before": int | None}`; limit default 50, max 200; `from`/`to` ISO dates inclusive; 400 `wallet must be one of main, credit, pamm, social`.
- `GET investor/summary` → the InvestorSummary of spec 11.1 plus `"deposits_open": bool` (any enabled method) and `"withdrawal_rules": {"min": float, "fee_pct": float}`; `investor.first_name` = display_name split on whitespace, first token; `investor.member_since` = org_memberships.created_at ISO; `cash_flow` = last 90 days from `wallet_entries` kind deposit (positive) and withdrawal (negative, reported as a positive `withdrawals`) grouped by date, dates with no movement omitted.
- `GET investor/positions`, `GET investor/analytics`, `GET investor/history/{kind}` moved from `routes/investor.py` unchanged in behaviour.

### `api/src/api/routes/portal_admin.py` (Tasks 6–10)

`create_portal_admin_router() -> APIRouter`, prefix `/api/orgs/{org_id}`, every route
`ctx = Depends(require_org_role("admin"))`.

```python
class MethodBody(BaseModel): kind: str; label: str; currency: str = "USD"; details: dict; min_amount: Any = "0"; fee_pct: Any = "0"; instructions: str | None = None; sort_order: int = 0
class MethodPatch(BaseModel): label/currency/details/min_amount/fee_pct/instructions/sort_order/enabled all Optional
class SettingsBody(BaseModel): withdrawal_min: Any; withdrawal_fee_pct: Any
class DepositDecision(BaseModel): status: str; credited_amount: Any = None; note: str | None = None
class PaidBody(BaseModel): txid: str
class AdjustmentBody(BaseModel): wallet: str; amount: Any; note: str; mpin: Any = None
class LinkBody(BaseModel): account_id: int | None = None
```

- `GET/POST payment-methods`, `PATCH/DELETE payment-methods/{id}`: 400 `kind must be crypto or bank`; 400 `<field> is required` (crypto: coin, network, address; bank: bank_name, holder, account_number, code); 400 `fee_pct must be between 0 and 99.999`; 404 `Payment method not found`; DELETE 409 `a pending deposit still uses this method`. Audit `payment_method_changed` (warning) with `method_id`, `change: created|updated|deleted`.
- `GET/PUT portal-settings` → `{"withdrawal_min": float, "withdrawal_fee_pct": float}`; audit `portal_settings_changed`.
- `GET investors` → `[{user_id, email, display_name, joined_at, account_id, nickname, equity, equity_source, balances: {main, credit, pamm, social}, on_hold, available, pending: {deposits, withdrawals, transfers, payout_destinations}}]` with ONE copier `/state` round trip.
- `PUT investors/{user_id}/account` unchanged from today (moved).
- `GET investors/{user_id}/wallet-entries` same params/shape as the investor route; 404 `Investor not found`.
- `POST investors/{user_id}/adjustments` → 201 `entry_json`: MPIN of the ADMIN first; 400 `wallet must be one of …`; amount parsed as signed (`parse_amount` on the absolute value, sign kept; 400 `amount must not be zero`); note required ≤ 500; audit `investor_ledger_adjusted` (warning); email the investor.
- `GET requests/summary` → `{"deposits": n, "withdrawals": n, "transfers": n, "payout_destinations": n, "total": n}` counting `pending` / `requested` / `requested|approved` / `pending` rows respectively (withdrawals count requested+approved; transfers requested+approved).
- `GET deposits?status=` (open first: pending, then newest) + `email`, `display_name`; `POST deposits/{id}/decision`: 400 `status must be confirmed or rejected`; 404 `Deposit not found`; 409 `deposit is already {status}`; 409 `decided by someone else`; `credited_amount` defaults to `amount - fee`, must be > 0; on confirm: `settle(main, +credited, kind deposit, ref deposits/id)`; when `target = 'account'` and the investor's linked account exists, INSERT `transfers` (source wallet main, target that account, amount credited, status `approved`, decided_by admin, decided_at now, decision_note `funded from deposit #<id>`) in the same transaction (if the link is gone, confirm to the wallet only and add `decision_note` suffix `(no account linked; credited to wallet)`). Audit `investor_deposit_decided`; email.
- `GET withdrawals?status=`; `POST withdrawals/{id}/decision` (approved|rejected); `POST withdrawals/{id}/paid {txid}` → 409 `withdrawal is {status}, not approved`; settles `main` by `-amount` kind withdrawal. Audit `investor_withdrawal_decided` / `investor_withdrawal_paid`; email.
- `GET transfers?status=`; `POST transfers/{id}/decision` (approved|done|rejected): `done` settles per spec section 6. Audit `investor_transfer_decided`; email.
- `GET payout-destinations?status=` (`full=True`); `POST payout-destinations/{id}/decision` (approved|rejected). Audit `investor_destination_decided`; email.
- `GET files/{id}` lives in `portal_files.py`.

`main.py`: `app.include_router(create_portal_files_router())`,
`create_portal_investor_router()`, `create_portal_admin_router()`; the old
`create_investor_router` / `create_investor_admin_router` lines go in Task 10.

`alerts.py` `ALERT_RULES` and `telegram.py` `TELEGRAM_RULES` gain
`("control","warning",<action>)` for: `investor_deposit_noticed`,
`investor_withdrawal_requested`, `investor_transfer_requested`,
`investor_destination_added`, `investor_ledger_adjusted`, `payment_method_changed`
(the first two already exist; keep them).

## Test helpers (`api/tests/portal_helpers.py`, Task 1; extended by later tasks)

```python
def csrf(client) -> dict                                            # {"X-CSRF-Token": client.cookies["csrf"]}
def add_method(db, org_id, *, kind="crypto", label="USDT on TRC20", details=None, min_amount="0", fee_pct="0", enabled=True) -> int
def credit(db, org_id, user_id, amount, *, wallet="main", kind="adjustment") -> int   # direct wallet_entries insert
def approved_destination(db, org_id, user_id, *, kind="crypto") -> int
def seed_file(db, org_id, user_id, *, purpose="deposit_receipt") -> int   # files row with a fake storage key
def link(db, org_id, user_id, account_id) -> None                    # accounts.investor_user_id
def set_state(client, payload: dict) -> None                        # fake copier /state via client.app.state.mock_transport.set_callback
```
`make_user` already gives every user MPIN `123456`; `login_as` yields a full session.

## Dashboard

### `src/lib/types.ts` (Task 11) — add, do not remove others

```ts
export type WalletKind = 'main' | 'credit' | 'pamm' | 'social'
export type RequestStatus = 'pending'|'confirmed'|'rejected'|'cancelled'|'requested'|'approved'|'paid'|'done'|'removed'
export interface MoneyRef { kind: 'wallet' | 'account'; wallet?: WalletKind; account_id?: number }
export interface PaymentMethod { id: number; kind: 'crypto'|'bank'; label: string; enabled: boolean; currency: string; details: Record<string, string>; min_amount: number; fee_pct: number; instructions: string | null; sort_order: number }
export interface PortalDeposit { id: number; user_id: number; method_id: number | null; method_kind: 'crypto'|'bank'; method_label: string; amount: number; fee: number; credited_amount: number | null; reference: string; receipt_file_id: number | null; target: 'wallet'|'account'; target_account_id: number | null; note: string | null; status: RequestStatus; decided_by: number | null; decided_at: string | null; decision_note: string | null; created_at: string; currency: string; email?: string; display_name?: string }
export interface PortalWithdrawal { id: number; user_id: number; destination_id: number; destination_kind: 'bank'|'crypto'; destination_summary: string; amount: number; fee: number; net_amount: number; status: RequestStatus; decided_by: number | null; decided_at: string | null; decision_note: string | null; paid_by: number | null; paid_at: string | null; txid: string | null; created_at: string; currency: string; email?: string; display_name?: string }
export interface PortalTransfer { id: number; user_id: number; source: MoneyRef; target: MoneyRef; amount: number; status: RequestStatus; equity_at_request: number | null; equity_verified: boolean; decided_by: number | null; decided_at: string | null; decision_note: string | null; done_by: number | null; done_at: string | null; note: string | null; created_at: string; currency: string; email?: string; display_name?: string }
export interface PayoutDestination { id: number; user_id: number; kind: 'bank'|'crypto'; nickname: string; details: Record<string, string>; proof_file_id: number | null; status: RequestStatus; decided_by: number | null; decided_at: string | null; decision_note: string | null; created_at: string; summary: string; email?: string; display_name?: string }
export interface WalletEntry { id: number; wallet: WalletKind; amount: number; kind: 'deposit'|'withdrawal'|'transfer'|'adjustment'|'bonus'|'commission'|'fee'; ref_table: string | null; ref_id: number | null; note: string | null; created_at: string; currency: string }
export interface WalletEntriesPage { entries: WalletEntry[]; has_more: boolean; next_before: number | null }
export interface WalletFigures { balance: number; on_hold: number; available: number }
export interface InvestorSummary {
  org: { id: number; name: string }; currency: string
  investor: { display_name: string; first_name: string; member_since: string }
  wallets: Record<WalletKind, WalletFigures>
  totals: { deposited: number; withdrawn: number; transferred_in: number; transferred_out: number }
  cash_flow: { date: string; deposits: number; withdrawals: number }[]
  pending: { deposits: number; withdrawals: number; transfers: number; payout_destinations: number }
  deposits_open: boolean
  withdrawal_rules: { min: number; fee_pct: number }
  link_state: 'linked' | 'unlinked'
  account: { account_id: number; nickname: string | null; platform: string; status: string; last_error: string | null; connected: boolean } | null
  equity_source: 'live' | 'last known' | 'unknown'; equity: number | null; net_funded: number; profit: number | null; account_available: number | null; open_positions: number
}
export interface InvestorRow { user_id: number; email: string; display_name: string; joined_at: string; account_id: number | null; nickname: string | null; equity: number | null; equity_source: string; balances: Record<WalletKind, number>; on_hold: number; available: number; pending: { deposits: number; withdrawals: number; transfers: number; payout_destinations: number } }
export interface PortalSettings { withdrawal_min: number; withdrawal_fee_pct: number }
export interface RequestsSummary { deposits: number; withdrawals: number; transfers: number; payout_destinations: number; total: number }
export interface UploadedFile { id: number; purpose: string; content_type: string; size_bytes: number; created_at: string }
```
The old `InvestorSummary`, `InvestorWallet`, `InvestorDeposit`, `InvestorWithdrawal`,
`InvestorRow` types are deleted in Task 11 together with every import of them (the pages
that used them are rewritten in Tasks 13–19; Task 11 leaves those pages compiling by
switching their imports to the new names and adjusting field access minimally, or by
stubbing — the plan's Task 11 must say exactly which).

### `src/lib/investor.ts` (Task 11)

```ts
export const ACCOUNT_CURRENCY = 'USD'
export const WALLETS: WalletKind[] = ['main', 'credit', 'pamm', 'social']
export function walletLabel(kind: WalletKind): string   // 'My wallet' | 'Credit wallet' | 'PAMM wallet' | 'Social wallet'
export type RequestKind = 'deposit' | 'withdrawal' | 'transfer' | 'destination'
export function approvedLabel(kind: RequestKind): string   // transfer→'Approved, in progress', destination→'Approved', else 'Approved, payment pending'
export function statusLabel(status: string, kind?: RequestKind): string     // adds cancelled→'Cancelled', done→'Done', removed→'Removed'; bare approved keeps 'Approved, payment pending'; pass kind ('transfer' / 'destination') to get approvedLabel(kind)
export function statusTone(status: string, kind?: RequestKind): 'ok'|'warn'|'bad'|'quiet'   // done|confirmed|paid→ok; approved→warn, or ok when kind === 'destination'; cancelled|removed→quiet
export const BADGE_TONE: Record<'ok'|'warn'|'bad'|'quiet', BadgeTone>
export function moneyOrDash(v: number | null | undefined, unit?: string): string
export function shortAddress(a: string): string
export function entryLabel(e: WalletEntry): string      // 'Deposit #12', 'Withdrawal #4', 'Transfer #9', 'Adjustment', …
```

### `src/lib/hideBalances.ts` + `src/components/Money.tsx` (Task 11)

```ts
// hideBalances.ts — module store like settingsBus
export const HIDE_KEY = 'mf.hideBalances'
export function readHidden(): boolean; export function setHidden(v: boolean): void; export function useHiddenBalances(): [boolean, (v: boolean) => void]
// Money.tsx
export interface MoneyProps { value: number | string | null | undefined; unit?: string; signed?: boolean; className?: string }
export default function Money(props: MoneyProps): JSX.Element
// renders money(value, unit) (or signed()) inside <span class="num">; when hidden renders <span aria-label="Hidden amount">••••</span>
export function HideBalancesToggle(): JSX.Element   // ghost Button, aria-pressed, label 'Hide balances' / 'Show balances'
```

### `src/lib/api.ts` (Task 11)

```ts
export async function apiUpload<T>(path: string, form: FormData, opts?: { redirectOn401?: boolean }): Promise<T>
// same as api() but never sets Content-Type and passes the FormData as body; CSRF header included
export function orgUpload<T>(orgId: number, tail: string, form: FormData): Promise<T>
export function orgApi<T>(orgId: number, tail: string, init?: RequestInit, opts?: { redirectOn401?: boolean }): Promise<T>
// existing helper; gains api()'s opts so step-up POSTs pass { redirectOn401: false } (spec section 10)
```

### `src/components/FileInput.tsx` (Task 11)

```ts
export interface FileInputProps { id: string; label: string; accept: string[]; maxBytes: number; value: File | null; onChange: (file: File | null) => void; required?: boolean; hint?: string; error?: string | null; disabled?: boolean }
// native <input type="file"> styled as a secondary Button 'Choose file', shows name + size, image preview thumbnail (object URL, revoked on change), 'Remove' ghost button; client-side refusal messages: 'That file type is not accepted' / 'File is larger than 5 MB'
export const RECEIPT_ACCEPT = ['image/jpeg', 'image/png', 'image/webp', 'application/pdf']
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024
```

### `src/components/PinConfirmDialog.tsx` (Task 11)

```ts
export interface PinConfirmDialogProps { open: boolean; title: string; children: ReactNode; confirmLabel: string; busy?: boolean; onConfirm: (mpin: string) => Promise<void>; onCancel: () => void }
// Wraps ConfirmDialog; body = children + <PinInput id="confirm-mpin" label="Your MPIN">; confirm disabled until 6 digits; on reject of onConfirm: 401 attempts_left → 'Wrong MPIN, N tries left' (singular 'try'); 423 locked_until → 'MPIN locked. Try again in about N minutes'; 409 → 'Set your MPIN first'; else errorText(err, 'Could not confirm'); the PIN is cleared on every failure; the caller closes the dialog on success.
```

### `src/test/portalFixtures.ts` (Task 11)

Exports `summaryFixture(): InvestorSummary`, `depositFixture(overrides?)`,
`withdrawalFixture(overrides?)`, `transferFixture(overrides?)`, `destinationFixture(overrides?)`,
`entryFixture(overrides?)`, `methodFixture(overrides?)`, `investorRowFixture(overrides?)`
with realistic values (main balance 5120.5, on_hold 100, available 5020.5).

### Navigation (Task 12)

```ts
export interface NavItem { path: string; label: string; end?: boolean; badge?: number }
investorNav(orgId): [ { name: '', items: [Dashboard(end, /invest)] },
                     { name: 'Money', items: [Wallet /invest/wallet, Deposit, Withdraw, Transfer, Transactions /invest/transactions, Payout accounts /invest/payout-accounts] },
                     { name: 'Trading', items: [Account /invest/account, History /invest/history] } ]
bottomBarItems(orgId, 'investor'): Dashboard, Deposit, Withdraw, Transactions
adminNav Org group: Members, Investors, Requests (/requests, badge from the hook), Logs
export function useRequestsBadge(orgId: number, role: Role): number | undefined   // hooks/useRequestsBadge.ts; admins only; GET requests/summary every 30 s + useLiveRefresh on 'control'; undefined for non-admins
NavRail renders <Badge tone="neutral">{badge}</Badge> after the label when badge > 0
BottomBar shows More for every role
```

### Routes (Tasks 13–19), all under `/org/:orgId`

`invest` InvestorDashboard · `invest/wallet` InvestorWallet · `invest/deposit` · `invest/withdraw`
· `invest/transfer` InvestorTransfer · `invest/transactions` InvestorTransactions ·
`invest/payout-accounts` InvestorPayoutAccounts · `invest/history` · `invest/account` ·
`requests` Requests (admin chunk). Each page: default export, re-export in its group
barrel, `pick()` const, `<Route>` line.

### Page contracts

- Every page: `<div className="space-y-6 max-w-5xl">`, one `PageHeader`, `Banner`
  error/notice pair, `Loading` until `loaded`, `errorText(err, fallback)`.
- InvestorDashboard title `Dashboard`; greeting `Good morning|afternoon|evening,
  {first_name}` by `new Date().getHours()` (<12, <18, else); `HideBalancesToggle` in the
  greeting card; StatTiles `My wallet`, `Total deposited`, `Total withdrawn`, `Total
  transferred`; cash flow ranges `7D | 30D | 90D` as Tabs `idBase="cashflow"`.
- InvestorDeposit title `Deposit`; Tabs `idBase="deposit-kind"` Crypto/Bank; quick chips
  `50 100 250 500 Min`; submit label `File deposit notice`; NextStep title `Deposits are
  not open yet` when `deposits_open` is false.
- InvestorWithdraw title `Withdraw`; submit `Request withdrawal`; dialog title
  `Send {money(amount)} to {destination_summary}?`; Timeline STEPS `['requested','approved','paid']`.
- InvestorTransfer title `Transfer`; From/To `Select`s with option values `wallet:main`,
  `wallet:pamm`, `wallet:social`, `account:<id>`; submit `Request transfer` (instant
  pairs still go through the PIN dialog); dialog title `Move {money(amount)} from {from}
  to {to}?`.
- InvestorTransactions title `Transactions`; Tabs `idBase="wallet"` All + WALLETS;
  `Download CSV` builds `transactions.csv` (Date, Wallet, Kind, Amount, Reference, Note).
- InvestorPayoutAccounts title `Payout accounts`; Drawer titles `Add bank account`, `Add
  crypto address`; save label `Save payout account`.
- InvestorWallet title `Wallet`.
- Investors (admin) title `Investors`; Tabs `idBase="investors"` `Investors` /
  `Payment methods`; row Menu items `View ledger`, `Adjust balance`; adjust dialog title
  `Adjust {display_name}'s wallet`.
- Requests (admin) title `Requests`; Tabs `idBase="requests"` `Deposits (n)`,
  `Withdrawals (n)`, `Transfers (n)`, `Payout accounts (n)`; Open/All as a Select
  `aria-label="Show"`; per-row action aria-labels `Confirm deposit {id}`, `Reject deposit
  {id}`, `Approve withdrawal {id}`, `Reject withdrawal {id}`, `Mark withdrawal {id} paid`,
  `Approve transfer {id}`, `Mark transfer {id} done`, `Reject transfer {id}`, `Approve
  payout account {id}`, `Reject payout account {id}`, `Details of {type} {id}`; dialog
  textarea aria-labels `Note`, `Transaction ID`; credited amount input aria-label
  `Credited amount`.
