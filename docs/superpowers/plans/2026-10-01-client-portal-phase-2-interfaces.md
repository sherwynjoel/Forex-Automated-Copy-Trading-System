# Client portal phase 2 — interfaces and task map

Companion to `docs/superpowers/plans/2026-10-01-client-portal-phase-2.md`. Every task in
the plan consumes and produces exactly the names below. An implementer may add private
helpers, never rename or reshape anything here. Spec (binding):
`docs/superpowers/specs/2026-10-01-client-portal-phase-2-identity-design.md`. Code facts:
`docs/reference/mirrorfleet-subsystem-maps.md` and the phase 1 interfaces
`docs/superpowers/plans/2026-09-29-client-portal-phase-1-interfaces.md`.

## Global constraints (copied into the plan header)

- Branch `client-portal-phase-2`, created from `portal-followups` at or after commit
  `f9cb004` (it adds `rbac.require_investor`, which every investor route here uses).
  Work in a worktree (superpowers:using-git-worktrees): another agent uses the main
  checkout. Commit after every task; every commit ends with the two trailer lines
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_01UQPL1C8PgeZ12quFkw2nCM`.
  Subjects: `feat(api): …`, `feat(dashboard): …`, `test: …`, `docs: …`.
- `copier/` is never touched.
- API tests, Git Bash from `api/`, Docker Desktop running, `docker compose -f ../docker-compose.yml up -d postgres` first:
  `export TEST_POSTGRES_ADMIN_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader"; export TEST_POSTGRES_DSN="postgresql://copytrader:${POSTGRES_PASSWORD}@127.0.0.1:5433/copytrader_test_p2"; export PYTHONPATH="$(pwd -W)/src"; .venv/Scripts/python -m pytest <files> -q -p no:cacheprovider`
  (`POSTGRES_PASSWORD` from the repo-root `.env`; the `_p2` database name keeps this
  run from dropping another agent's scratch database). Use `127.0.0.1`, never
  `localhost`. Seven `test_events_ws.py` errors and one EA-download CRLF failure are
  pre-existing on Windows; ignore them.
- Dashboard gate from `dashboard/`: `node scripts/palette_check.mjs && npx tsc --noEmit -p tsconfig.app.json && npx vitest run --maxWorkers=2 --minWorkers=1`
  (= `npm test` with the worker cap this machine needs) and `npm run build`. There is no
  lint script. One file: `npx vitest run <path>`. Zero `act(...)` warnings in the output.
- Every new table is in the `db` fixture TRUNCATE list in `api/tests/conftest.py`.
- Every investor route takes `ctx: OrgContext = Depends(require_investor)` (403
  `Insufficient role` for viewers and admins); every admin route takes
  `Depends(require_org_role("admin"))`. Every new route gets a row in
  `api/tests/test_rbac_matrix.py` (Task 10).
- MPIN step-up: routes that take `mpin` call `mpin_core.require_mpin(conn, user_id, body.mpin)`
  FIRST and `return` its Response when it is not None (400 / 409 `MPIN not set` / 423 /
  401 `Invalid MPIN` + `attempts_left`). The dashboard calls them with
  `{ redirectOn401: false }` through `PinConfirmDialog` (or an inline `PinInput` +
  `mpinErrorText` where a dialog would nest inside a Drawer).
- Every mutation audits one `events` row through `portal_common.audit_control` (category
  `control`); investor-scoped actions start with `investor_` and carry `payload.user_id`
  (the investor the row is about). Audit payloads never carry profile values or
  passwords, only field names and ids.
- Investors are emailed on every decision through `portal_common.notify_investor`.
- Dashboard: colour only through `--color-*` tokens and the primitives; data never sits
  directly on `.glass`; tables are `stack-table` with `data-label` on every data `td`;
  the one `h1` comes from `PageHeader`; never the words "Slave"/"slave" in copy; never a
  literal "Loading..."; no colour pair new to `scripts/palette_check.mjs`. Tests use
  `mockUseOrg` from `src/test/orgMock.tsx`, fixtures from `src/test/portalFixtures.ts`,
  stub `fetch` by URL tail, and any test of a page that renders `Money` ends its
  `afterEach` with `cleanup()` BEFORE `setHidden(false)`.
- Deploy is not part of the plan; Task 17 writes the runbook lines.

## Task map

| # | Task | Produces for later tasks |
|---|---|---|
| 1 | Migration `023_portal_identity.sql`, `test_migration_023.py`, conftest TRUNCATE, `portal_helpers` additions | tables, `add_package`, `kyc_profile`, `open_account_request` |
| 2 | Uploads accept `kyc_document` / `kyc_photo`; `file_belongs` excludes files already in a KYC slot | upload purposes |
| 3 | `login_events` writes (login, MPIN verify), `GET /api/me/sign-ins`, `GET investor/sign-ins`, `POST /api/me/password` needs `mpin` | `auth.record_login`, `auth.sign_ins` |
| 4 | `api/src/api/portal_identity.py` rules + serialisers, `tests/test_portal_identity_rules.py` | every `pid.*` name |
| 5 | `routes/portal_identity.py` created; KYC investor routes; `main.py` mounts it; `tests/test_portal_kyc.py` | the router |
| 6 | KYC admin routes; `kyc_status` on `investor/summary` and admin `investors`; tests | |
| 7 | Account packages (admin CRUD, investor list); `tests/test_portal_packages.py` | |
| 8 | Account requests, investor side; `tests/test_portal_account_requests.py` | |
| 9 | Account requests, admin side (list, reveal, fulfil, reject); `requests/summary` gains `kyc`, `account_requests`; three alert rules | complete API |
| 10 | RBAC matrix rows for every new org route; full API suite | |
| 11 | Dashboard foundation: `types.ts`, `lib/identity.ts`, fixtures, `components/SignInHistory.tsx`, `FilePreview`/`Row`/`Section` exported | primitives |
| 12 | `pages/investor/InvestorSecurity.tsx`; `AccountSecurity` asks for the MPIN; Members shows sign-ins; Account page drops `AccountSecurity` | |
| 13 | `pages/investor/InvestorProfile.tsx` | |
| 14 | `pages/investor/InvestorOpenAccount.tsx`; Account page login card | |
| 15 | `pages/requests/VerificationTab.tsx`, `pages/requests/AccountRequestsTab.tsx`, Requests desk tabs | |
| 16 | `pages/investors/PackagesTab.tsx`, Investors KYC column, Dashboard verification card, investor nav regroup | |
| 17 | Gates and docs: README runbook, spec status, full suites, build | |

## Database (Task 1) — `db/migrations/023_portal_identity.sql`

Tables in this order, columns in exactly this order:

- `kyc_profiles`: `org_id, user_id, full_name, gender, date_of_birth, phone, address_line,
  area, landmark, city, state, postal_code, country_residence, country_citizenship,
  id_type, id_number, id_front_file_id, id_back_file_id, address_proof_file_id,
  photo_file_id, status, submitted_at, decided_by, decided_at, decision_note, updated_at`;
  `PRIMARY KEY (org_id, user_id)`; index `kyc_profiles_queue (org_id, status, submitted_at)`.
  Checks: gender in (`male`,`female`,`other`); id_type in (`passport`,`national_id`,
  `driving_licence`); status in (`draft`,`submitted`,`approved`,`rejected`) default
  `draft`; both country columns `~ '^[A-Z]{2}$'`. File columns `REFERENCES files(id) ON
  DELETE SET NULL`.
- `account_packages`: `id, org_id, name, min_deposit, currency, spread_label,
  leverage_options, enabled, sort_order, created_at, updated_at`; index
  `account_packages_by_org (org_id, sort_order, id)`.
- `account_requests`: `id, org_id, user_id, package_id, package_name, leverage,
  main_password_enc, investor_password_enc, status, mt5_login, mt5_server, account_id,
  decided_by, decided_at, decision_note, created_at`; indexes `account_requests_queue
  (org_id, status, created_at)` and UNIQUE `account_requests_one_open (org_id, user_id)
  WHERE status = 'requested'`; named checks `account_requests_passwords_only_while_open`
  (passwords NULL unless status `requested`) and `account_requests_fulfilled_has_login`
  (fulfilled ⇒ mt5_login and mt5_server NOT NULL).
- `login_events`: `id, user_id, ip, user_agent, outcome, created_at`; outcome in
  (`password_ok`,`mpin_ok`,`failed`); index `login_events_by_user (user_id, created_at DESC)`.

conftest `db` TRUNCATE list gains, at its head:
`login_events, account_requests, account_packages, kyc_profiles, `.

## Test helpers (`api/tests/portal_helpers.py`, Task 1)

```python
COMPLETE_PROFILE: dict   # every required text field filled, ISO date string, 'IN' countries, id_type 'passport'
def add_package(db, org_id, *, name="Standard", min_deposit="100", leverage=(100, 200, 500), spread_label="20-25", enabled=True, sort_order=0) -> int
def kyc_profile(db, org_id, user_id, *, status="approved", **over) -> dict
    # inserts COMPLETE_PROFILE + four seeded files (3 kyc_document, 1 kyc_photo) + over; returns the column values written (file ids included)
def open_account_request(db, org_id, user_id, package_id, *, main="Main1234", investor="Inv12345", leverage=100, package_name="Standard") -> int
    # status 'requested', passwords sealed with os.environ["FERNET_KEY"] (set by app_client)
```

## Python

### `api/src/api/uploads.py` (Task 2)

`PHASE1_PURPOSES` is renamed `ACCEPTED_PURPOSES = {"deposit_receipt", "payout_proof", "kyc_document", "kyc_photo"}`;
`ALL_PURPOSES = ACCEPTED_PURPOSES | {"ticket_attachment", "avatar"}`. The refusal string stays
`purpose is not accepted yet`.

### `api/src/api/routes/portal_files.py` (Task 2)

`file_belongs(conn, org_id, user_id, file_id, purpose) -> bool` additionally returns False
when the file sits in any of the four `kyc_profiles` file columns (of any row).

### `api/src/api/auth.py` (Task 3)

```python
LOGIN_HISTORY_DAYS = 180
SIGN_INS_MAX = 200
def record_login(conn, user_id: int, request: Request, cfg: ApiConfig, outcome: str) -> None
    # best effort; deletes this user's rows older than LOGIN_HISTORY_DAYS, then inserts (ip from get_client_ip(request, cfg.trust_proxy), user_agent[:256] or None)
def sign_ins(conn, user_id: int, limit: int = 50) -> list[dict]
    # newest first; limit clamped to 1..SIGN_INS_MAX; [{id, ip, user_agent, outcome, created_at}]
```
- `POST /api/login`: known email + wrong password → `record_login(..., "failed")` then 401;
  right password → `record_login(..., "password_ok")`. Unknown email and 429 write nothing.
- `POST /api/mpin/verify` (routes/mpin.py, gains `request: Request`): success →
  `record_login(..., "mpin_ok")`. Failures write nothing.
- `POST /api/me/password`: body gains `mpin: Any = None`; `require_mpin` runs first.
- `GET /api/me/sign-ins?limit=50` (require_user, any role) → `sign_ins(...)`.
- `GET /api/orgs/{org_id}/investor/sign-ins?limit=50` (portal_investor.py, require_investor) → `sign_ins(conn, ctx.user_id, limit)`.

### `api/src/api/portal_identity.py` (Task 4) — imported as `pid`

```python
PROFILE_FIELDS: tuple[str, ...]   # the 18 profile columns from full_name to photo_file_id, table order
FILE_SLOTS: dict[str, str]        # {"id_front_file_id": "kyc_document", "id_back_file_id": "kyc_document", "address_proof_file_id": "kyc_document", "photo_file_id": "kyc_photo"}
FILE_LABELS: dict[str, str]       # {"id_front_file_id": "ID front", "id_back_file_id": "ID back", "address_proof_file_id": "address proof", "photo_file_id": "photo"}
OPTIONAL_FIELDS = frozenset({"area", "landmark", "state"})
REQUIRED_FIELDS: tuple[str, ...]  # PROFILE_FIELDS minus OPTIONAL_FIELDS, in order
CONTACT_FIELDS = frozenset({"phone", "address_line", "area", "landmark", "city", "state", "postal_code"})
def clean_profile_field(field: str, raw: object) -> object   # raises LedgerError; '' / None -> None
def missing_fields(profile: dict) -> list[str]
def needs_reverification(before: dict, changes: dict) -> bool   # any changed key outside CONTACT_FIELDS
PROFILE_COLS: str                 # "user_id, <PROFILE_FIELDS>, status, submitted_at, decided_by, decided_at, decision_note, updated_at"
def profile_json(row) -> dict     # + "missing": list[str]; + email, display_name when the row carries them
def empty_profile(user_id: int) -> dict   # status 'draft', every field None, missing = list(REQUIRED_FIELDS)
def kyc_status(conn, org_id: int, user_id: int) -> str   # 'draft' when no row
def check_mt5_password(raw: object, field: str) -> str
def parse_leverage_options(raw: object) -> list[int]      # sorted, deduplicated
def seal(fernet_key: str, secret: str) -> str
def unseal(fernet_key: str, token: str) -> str             # raises cryptography.fernet.InvalidToken
PACKAGE_COLS: str = "id, name, min_deposit, currency, spread_label, leverage_options, enabled, sort_order"
def package_json(row) -> dict     # {id, name, min_deposit: float, currency, spread_label, leverage_options: list[int], enabled, sort_order}
REQUEST_COLS: str = "id, user_id, package_id, package_name, leverage, status, mt5_login, mt5_server, account_id, decided_by, decided_at, decision_note, created_at"
def request_json(row) -> dict     # never carries passwords; + email, display_name when the row carries them
```
Refusal strings (LedgerError text, surfaced as 400 detail):
`<field> must be at most <n> characters` (clean_text), `gender must be one of male, female, other`,
`id_type must be one of passport, national_id, driving_licence`,
`<field> must be a two-letter country code`, `date_of_birth must be a date (YYYY-MM-DD)`,
`date_of_birth must be in the past`, `<field> must be a file id`,
`<field> must be 8-32 characters without spaces, with an upper-case letter, a lower-case letter and a digit`,
`leverage_options must be a list of whole numbers from 1 to 3000`.

### `api/src/api/routes/portal_identity.py` (Tasks 5–9)

`create_portal_identity_router() -> APIRouter`, prefix `/api/orgs/{org_id}`, mounted in
`main.py` right after `create_portal_admin_router()`. Bodies:

```python
class MpinBody(BaseModel): mpin: Any = None
class PackageBody(BaseModel): name: str; min_deposit: Any = "0"; currency: str = "USD"; spread_label: Optional[str] = None; leverage_options: Any = None; enabled: bool = True; sort_order: int = 0
class PackagePatch(BaseModel): name/min_deposit/currency/spread_label/leverage_options/enabled/sort_order all Optional (spread_label "" clears)
class AccountRequestBody(BaseModel): package_id: int; leverage: Any = None; main_password: Any = None; investor_password: Any = None; mpin: Any = None
class FulfilBody(BaseModel): mt5_login: Any = None; mt5_server: Any = None; account_id: Optional[int] = None; note: Optional[str] = None
class RejectBody(BaseModel): note: Optional[str] = None
```

Investor routes (require_investor):

- `GET investor/profile` → profile_json, or empty_profile when no row.
- `PUT investor/profile` (body: a JSON object of PROFILE_FIELDS keys, any subset) → profile_json:
  400 `unknown field: <name>` (first unknown, sorted); 400 from clean_profile_field;
  409 `your profile is under review` (status submitted); 400 `each document needs its own file`;
  400 `<FILE_LABELS[slot]> file not found` (file_belongs false for a CHANGED slot);
  409 `your profile changed; reload it` (status moved under us). Approved + any change outside
  CONTACT_FIELDS → status `draft` and the decision trio cleared. Audit `investor_profile_saved`
  (info) with `fields` (names) and `reverify` (bool). An empty object returns the profile unchanged.
- `POST investor/profile/submit {mpin}` → profile_json: MPIN first; 409 `your profile is already <status>`
  (not draft/rejected); 400 JSON `{"detail": "complete your profile first: <a, b>", "missing": [...]}`;
  409 `your profile changed; reload it`. Sets submitted, submitted_at now, clears the decision trio.
  Audit `investor_kyc_submitted` (warning).
- `GET investor/account-packages` → enabled packages, `sort_order, id`.
- `GET investor/account-requests` → own requests, newest first.
- `POST investor/account-requests` → 201 request_json: MPIN first; 409 `verify your identity first`;
  409 `you already have a trading account`; 409 `a request is already open`; 404 `Package not found`;
  400 `leverage must be one of <a, b, c>`; 400 password policy; 400
  `the investor password must differ from the main password`. Audit `investor_account_requested` (warning).
- `POST investor/account-requests/{req_id}/cancel` → request_json: 404 `Request not found`;
  409 `request is already <status>`; 409 `decided by someone else`. Wipes both passwords.
  Audit `investor_account_request_cancelled` (info).

Admin routes (require_org_role("admin")):

- `GET kyc?status=` → `[profile_json + email, display_name]`, submitted first, then newest; LIMIT 500.
- `POST kyc/{user_id}/decision` (`pc.Decision`) → profile_json: 400 `status must be approved or rejected`;
  400 `note is required` (reject); 404 `Profile not found`; 409 `profile is <status>, not submitted`;
  409 `decided by someone else`. Audit `investor_kyc_decided` (info); email
  `Your identity verification was <status>`.
- `GET account-packages` → all, `sort_order, id`. `POST account-packages` → 201. `PATCH account-packages/{package_id}`.
  `DELETE account-packages/{package_id}` → 204. Refusals: 400 `name is required`,
  `name must be at most 64 characters`, min_deposit (parse_min messages), leverage_options message,
  `spread_label must be at most 32 characters`; 404 `Package not found`; DELETE 409
  `an open request still uses this package`. Audit `account_package_changed` (info) with
  `package_id`, `change: created|updated|deleted`, `name`.
- `GET account-requests?status=` → `[request_json + email, display_name]`, requested first, newest; LIMIT 500.
- `POST account-requests/{req_id}/reveal {mpin}` → `{"main_password", "investor_password"}`: the ADMIN's
  MPIN first; 404 `Request not found`; 409 `request is already <status>`; 409
  `the passwords can no longer be read; reject this request and ask for a new one` (InvalidToken).
  Allowed any number of times while requested; every call audits
  `account_request_passwords_revealed` (warning, payload.user_id = the investor).
- `POST account-requests/{req_id}/fulfil` → request_json: 400 `mt5_login must be a whole number above zero`;
  400 `mt5_server is required`; 404 `Request not found`; 409 `request is already <status>`;
  with `account_id`: 409 `the investor already has a linked account` (linked to a different one),
  400 `The master account cannot be linked to an investor`, 404
  `Account not found in this workspace, or already linked`; 409 `decided by someone else`.
  Wipes both passwords. Audit `investor_account_request_decided` (status fulfilled) and, when linked,
  `investor_account_linked`; email `Your trading account is ready`.
- `POST account-requests/{req_id}/reject {note}` → request_json: 400 `note is required`; 404; 409s as above.
  Wipes both passwords. Audit `investor_account_request_decided` (status rejected); email
  `Your trading account request was rejected`.

Changed phase 1 routes:

- `GET investor/summary` gains `"kyc_status": pid.kyc_status(...)` (Task 6).
- `GET investors` rows gain `"kyc_status"` (Task 6).
- `GET requests/summary` gains `"kyc"` (profiles `submitted`) and `"account_requests"`
  (requests `requested`), both inside `total` (Task 9).

`alerts.py` `ALERT_RULES` and `telegram.py` `TELEGRAM_RULES` gain
`("control", "warning", a)` for `investor_kyc_submitted` ("Investor verification submitted"),
`investor_account_requested` ("Investor trading account request"),
`account_request_passwords_revealed` ("Account request passwords revealed") (Task 9).

## Dashboard

### `src/lib/types.ts` (Task 11) — add, do not remove others

```ts
export type KycStatus = 'draft' | 'submitted' | 'approved' | 'rejected'
export type Gender = 'male' | 'female' | 'other'
export type IdType = 'passport' | 'national_id' | 'driving_licence'
export type KycTextField = 'full_name' | 'gender' | 'date_of_birth' | 'phone' | 'address_line' | 'area' | 'landmark' | 'city' | 'state' | 'postal_code' | 'country_residence' | 'country_citizenship' | 'id_type' | 'id_number'
export type KycFileField = 'id_front_file_id' | 'id_back_file_id' | 'address_proof_file_id' | 'photo_file_id'
export type KycProfile = { user_id: number } & Record<KycTextField, string | null> & Record<KycFileField, number | null> & {
  status: KycStatus; submitted_at: string | null; decided_by: number | null; decided_at: string | null
  decision_note: string | null; updated_at: string | null; missing: string[]; email?: string; display_name?: string }
export interface AccountPackage { id: number; name: string; min_deposit: number; currency: string; spread_label: string | null; leverage_options: number[]; enabled: boolean; sort_order: number }
export type AccountRequestStatus = 'requested' | 'fulfilled' | 'rejected' | 'cancelled'
export interface AccountRequest { id: number; user_id: number; package_id: number | null; package_name: string; leverage: number; status: AccountRequestStatus; mt5_login: number | null; mt5_server: string | null; account_id: number | null; decided_by: number | null; decided_at: string | null; decision_note: string | null; created_at: string; email?: string; display_name?: string }
export interface RevealedPasswords { main_password: string; investor_password: string }
export interface SignIn { id: number; ip: string; user_agent: string | null; outcome: 'password_ok' | 'mpin_ok' | 'failed'; created_at: string }
// InvestorSummary gains  kyc_status: KycStatus
// InvestorRow gains      kyc_status: KycStatus
// RequestsSummary gains  kyc: number; account_requests: number
```

### `src/lib/identity.ts` (Task 11)

```ts
export function kycLabel(s: KycStatus): string            // draft 'Not submitted' | submitted 'Under review' | approved 'Verified' | rejected 'Rejected'
export function kycBadge(s: KycStatus): BadgeTone          // quiet | warn | ok | bad through BADGE_TONE
export function requestLabel(s: AccountRequestStatus): string   // 'Requested' | 'Ready' | 'Rejected' | 'Cancelled'
export function requestBadge(s: AccountRequestStatus): BadgeTone // warn | ok | bad | quiet
export const OUTCOME_LABELS: Record<SignIn['outcome'], string>  // password_ok 'Password accepted', mpin_ok 'Signed in', failed 'Wrong password'
export function deviceLabel(userAgent: string | null): string   // 'Chrome on Windows', 'Safari on iOS', 'Unknown device'
export const PASSWORD_RULE = '8 to 32 characters, no spaces, with an upper-case letter, a lower-case letter and a digit'
export function passwordProblem(p: string): string | null       // null when the server policy passes, else `Use ${PASSWORD_RULE}`
export function generatePassword(length?: number): string       // 12 by default; always passes passwordProblem
export const FIELD_LABELS: Record<KycTextField | KycFileField, string>
export const GENDERS: readonly (readonly [Gender, string])[]
export const ID_TYPES: readonly (readonly [IdType, string])[]
export function fieldValue(field: KycTextField, value: string | null): string   // choice words ('Passport'), '—' for empty (added in Task 13)
```

### `src/test/portalFixtures.ts` (Task 11)

`summaryFixture` gains `kyc_status: 'approved'`; `investorRowFixture` gains
`kyc_status: 'approved'`. New: `profileFixture(overrides?)` (a complete draft, missing `[]`,
files 31–34), `packageFixture(overrides?)` (id 1 'Standard', min 100, spread '20-25',
leverage [100, 200, 500]), `accountRequestFixture(overrides?)` (id 7, requested, leverage
200), `signInFixture(overrides?)` (mpin_ok, Chrome on Windows UA).

### Components (Task 11)

```ts
// src/components/SignInHistory.tsx
export default function SignInHistory({ path }: { path: string }): JSX.Element
// Card "Sign-in history" (inset), stack-table Time | IP | Device | Outcome; empty 'No sign-ins recorded yet'; Loading label 'Loading sign-ins'
// src/pages/requests/RequestDetailsDrawer.tsx: `export` added to FilePreview, Row, Section (no other change)
```

### Routes and nav

Under `/org/:orgId`: `invest/profile` InvestorProfile (Task 13) · `invest/open-account`
InvestorOpenAccount (Task 14) · `invest/security` InvestorSecurity (Task 12). Each: default
export, re-export in `pages/groups/investor.ts`, `pick()` const, `<Route>` line.

`investorNav(orgId)` (Task 16): `['', 'Money', 'Account']` groups; Account items, in order:
`Trading account` /invest/account, `Profile & verification` /invest/profile,
`Open account` /invest/open-account, `Security` /invest/security, `History` /invest/history.

### Page contracts

- InvestorSecurity title `Security`; `AccountSecurity` then `SignInHistory path=/api/orgs/{org}/investor/sign-ins?limit=50`.
- AccountSecurity: the password form opens `PinConfirmDialog` title `Change your password?`,
  confirm `Confirm`; posts `{current_password, new_password, mpin}` with `{ redirectOn401: false }`.
  Its "Confirm new MPIN" PinInput id becomes `confirm-new-mpin`.
- Members renders `<SignInHistory path="/api/me/sign-ins?limit=50" />` right after `AccountSecurity`.
- InvestorAccount no longer renders `AccountSecurity`; gains Card `Your MT5 login` (Login,
  Server, Package) from the newest `fulfilled` row of `investor/account-requests`.
- InvestorProfile title `Profile & verification`; steps (buttons in `<ol aria-label="Verification steps">`,
  `aria-current="step"` on the active one) `1. Profile`, `2. Identity`, `3. Address`, `4. Photo`,
  `5. Review`; inputs `id="kyc-<field>"` labelled by FIELD_LABELS (`(optional)` suffix for
  area/landmark/state); buttons `Save and continue`, `Save draft`, `Submit for verification`;
  PIN dialog title `Submit your profile for verification?`, confirm `Submit`.
- InvestorOpenAccount title `Open account`; NextStep `Verify your identity first` until approved;
  per package Button aria-label `Choose <name>`; Select aria-label `Leverage`; inputs
  `Main password`, `Investor password`; buttons `Generate main password`,
  `Generate investor password`, `Show passwords` / `Hide passwords`, `Request account`;
  PIN dialog title `Request a <name> account at 1:<leverage>?`, confirm `Request`;
  Card `Your request`; `Cancel request` + ConfirmDialog `Cancel this request?` confirm `Cancel request`.
- Requests desk Tabs gain `Verification (n)` (key `kyc`) and `Account requests (n)` (key
  `account_requests`); `?tab=kyc|account_requests` deep-links. Verification aria-labels
  `Approve verification <user_id>`, `Reject verification <user_id>`,
  `Details of verification <user_id>`; Drawer title `Verification of <display_name>`.
  Account requests aria-labels `Fulfil account request <id>`, `Reject account request <id>`;
  Drawer title `Fulfil request #<id>`; inline PinInput id `reveal-mpin` label `Your MPIN`;
  button `Reveal passwords`; inputs `MT5 login`, `MT5 server`; Select `Link to account`;
  submit `Fulfil request`.
- Investors Tabs gain `Account packages` (key `packages`); table gains column `Verification`.
  PackagesTab: Card `Account packages` with `Add package`; row aria-labels `Edit <name>`,
  `Disable <name>` / `Enable <name>`, `Delete <name>`; Drawer `Add account package` /
  `Edit <name>`; inputs `Name`, `Minimum deposit`, `Currency`, `Spread`, `Leverage options`,
  `Sort order`; submit `Save package`; ConfirmDialog `Delete <name>?`.
- InvestorDashboard gains Card `Identity verification` (Badge kycLabel; button `Verify now`
  (draft/rejected), `View profile` (submitted), `Open account` (approved, unlinked), none when
  approved and linked).
