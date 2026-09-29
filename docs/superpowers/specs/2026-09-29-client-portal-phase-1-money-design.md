# Client portal, phase 1: wallets, money movement and the Requests desk

**Date:** 2026-09-29
**Status:** implemented on branch `client-portal` on 2026-09-30 (Tasks 1–20 of `docs/superpowers/plans/2026-09-29-client-portal-phase-1.md`, API and dashboard suites green locally); deploy follows the "Upgrading with a migration" sequence in README.md.
**Reference material:** `docs/reference/aiprime-portal-survey.md` (the portal being
reproduced, screen by screen) and `docs/reference/mirrorfleet-subsystem-maps.md`
(what MirrorFleet has today, read by eight code readers). This spec supersedes the
money half of `2026-09-23-investor-portal-design.md`; the roles, sessions and MPIN
parts of that design and of `2026-09-26-mpin-second-factor-design.md` stay in force.

## 1. Goal

Turn MirrorFleet's investor pages into the money half of a broker-style client portal.
Every investor gets wallets backed by a real ledger. They deposit by bank transfer or
crypto to payment methods the admin configures, with a receipt. They withdraw to payout
destinations they saved and an admin approved. They move money between their wallets
and their trading account, confirm every money action with their MPIN, and see every
movement in one Transactions list. Admins work one Requests desk for deposits,
withdrawals, transfers and payout destinations, configure payment methods, and can post
a ledger adjustment.

The principle from the 2026-09-23 design is unchanged: the app never holds keys and
never moves money. Every transfer of value happens outside the app, done by a person,
and is recorded, approved and reconciled inside it.

## 2. The programme

The owner asked for everything in the reference portal. It ships in four phases, each
with its own spec and plan, each merged and deployed only on the owner's say-so.
Decisions already taken on 2026-09-29: MT5 account opening is a request an admin
fulfils by hand (no Manager API); payments are bank plus manual crypto with admin
approval (no automated gateway); the full scope is in.

| Phase | Delivers | Reference screens |
|---|---|---|
| 1 (this spec) | Ledger, wallets, payment methods, deposits with receipts, payout destinations, withdrawals, transfers, transactions, MPIN step-up, file uploads, admin Requests desk, investor Dashboard | Dashboard, My Wallet, Deposit, Withdraw, Transfer, Transactions, Withdrawal Accounts, wallet dropdown |
| 2 | KYC (four steps, document upload, admin review), Profile, Security (2FA method choice, password change gated by MPIN), MT5 account request wizard with admin-configured packages, activity logs | KYC, Profile, Password, Authentication, Open Live/Demo Account, Activity Logs |
| 3 | Manager marketplace: browse managers, manager statistics, follow (creates a follower account request), many accounts per investor, manager accounts and requests, subscribers, commissions, PAMM and Social wallets earning | PAMM, Social Trading, Browse, Manager statistics, Follow, Create Manager Account, Commissions, Subscribers |
| 4 | Bonus and credit wallet rules, IB programme and salary tiers, support tickets with chat, notification centre and bell, settings with keyboard shortcuts and custom shortcuts, downloads page | Bonus, IB Dashboard, Salary, Support Chat, Notifications, Settings, Download Links |

Phase 1 lays the data model the later phases extend: wallets already have `credit`,
`pamm` and `social` kinds, files already have every purpose, and every request table
follows one shape.

## 3. Non-goals for phase 1

- Automatic deposit detection from a chain or a gateway. Deposits are noticed by the
  investor and confirmed by an admin, as today.
- Creating MT5 accounts. Phase 2 adds the request wizard; phase 1 shows the one account
  an admin has linked, as today.
- More than one trading account per investor (phase 3).
- Bonus credit rules, commissions, salary tiers (phases 3 and 4). The `credit`, `pamm`
  and `social` wallets exist from phase 1 but nothing writes to them yet except an admin
  adjustment.
- Open self-signup. Investors still join by invite link. Opening registration into a
  public workspace is decided in phase 2 together with KYC.
- Multi-currency. Every wallet figure is USD. Crypto deposits are recorded at the USD
  value the admin confirms.
- In-app notification centre (phase 4). Phase 1 uses email to the investor and the
  admin alert channels that exist.

## 4. Vocabulary and roles

- The client-facing role stays `investor` (rank -1). The UI keeps saying "Investor" and,
  for copied accounts, "Follower". Nothing in this phase says "client" or "user" in copy.
- **Wallets**: My wallet (`main`), Credit wallet (`credit`), PAMM wallet (`pamm`),
  Social wallet (`social`). Only `main` moves in phase 1.
- **Payment method**: a place the org receives money (a crypto address on a network, or a
  bank account), configured by an admin. Replaces the single org wallet card.
- **Payout destination**: a place the investor receives money (their bank account or
  crypto address), saved by the investor, approved by an admin.
- **Deposit notice**, **withdrawal request**, **transfer request**: the three request
  types. "Request" is the word for all of them on the admin side.
- **On hold**: money spoken for by open requests. **Available** = balance minus on hold.

## 5. Data model (migration `022_client_wallets.sql`)

All new tables carry `org_id BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE` and
`user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE` unless stated, plus
`created_at TIMESTAMPTZ NOT NULL DEFAULT now()`. Money is `NUMERIC(18,2)`. Every request
table has the audit trio `decided_by BIGINT NULL REFERENCES users(id) ON DELETE SET
NULL, decided_at TIMESTAMPTZ NULL, decision_note TEXT NULL` and a queue index
`(org_id, status, created_at)`.

**files** — one row per uploaded file; bytes live on disk (section 9).

    id BIGSERIAL PK; org_id; user_id (uploader and owner);
    purpose TEXT NOT NULL CHECK (purpose IN ('deposit_receipt','payout_proof',
      'kyc_document','kyc_photo','ticket_attachment','avatar'));
    content_type TEXT NOT NULL; size_bytes INTEGER NOT NULL CHECK (size_bytes > 0);
    sha256 TEXT NOT NULL; storage_key TEXT NOT NULL UNIQUE; created_at
    INDEX files_by_user (org_id, user_id, created_at DESC)

**payment_methods** — where the org receives money.

    id BIGSERIAL PK; org_id;
    kind TEXT NOT NULL CHECK (kind IN ('crypto','bank'));
    label TEXT NOT NULL;                       -- "USDT on TRC20", "ICICI Bank"
    enabled BOOLEAN NOT NULL DEFAULT true;
    currency TEXT NOT NULL DEFAULT 'USD';
    details JSONB NOT NULL;
      -- crypto: {"coin","network","address","memo"?}
      -- bank:   {"bank_name","holder","account_number","code","bank_address"?,"country"?}
    min_amount NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (min_amount >= 0);
    fee_pct NUMERIC(6,3) NOT NULL DEFAULT 0 CHECK (fee_pct >= 0 AND fee_pct < 100);
    instructions TEXT NULL;                    -- shown under the details
    sort_order INTEGER NOT NULL DEFAULT 0;
    created_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL;
    created_at; updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    INDEX payment_methods_by_org (org_id, sort_order, id)

**portal_settings** — one row per org, created on first read.

    org_id BIGINT PK REFERENCES orgs(id) ON DELETE CASCADE;
    withdrawal_min NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (withdrawal_min >= 0);
    withdrawal_fee_pct NUMERIC(6,3) NOT NULL DEFAULT 0 CHECK (>= 0 AND < 100);
    updated_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL;
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

**payout_destinations** — where an investor receives money.

    id BIGSERIAL PK; org_id; user_id;
    kind TEXT NOT NULL CHECK (kind IN ('bank','crypto'));
    nickname TEXT NOT NULL;
    details JSONB NOT NULL;
      -- bank:   {"bank_name","holder","account_number","code","bank_address"?,"country"?}
      -- crypto: {"coin","network","address"}
    proof_file_id BIGINT NULL REFERENCES files(id) ON DELETE SET NULL;
    status TEXT NOT NULL DEFAULT 'pending'
      CHECK (status IN ('pending','approved','rejected','removed'));
    decided_by; decided_at; decision_note; created_at
    INDEX payout_destinations_by_user (org_id, user_id, status)
    INDEX payout_destinations_queue (org_id, status, created_at)

**wallet_entries** — the ledger. A balance is the sum of a wallet's entries; nothing
stores a balance.

    id BIGSERIAL PK; org_id; user_id;
    wallet TEXT NOT NULL CHECK (wallet IN ('main','credit','pamm','social'));
    amount NUMERIC(18,2) NOT NULL CHECK (amount <> 0);    -- signed
    kind TEXT NOT NULL CHECK (kind IN ('deposit','withdrawal','transfer',
      'adjustment','bonus','commission','fee'));
    ref_table TEXT NULL; ref_id BIGINT NULL;             -- deposits / withdrawals / transfers
    note TEXT NULL;
    created_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL;  -- admin for adjustments
    created_at
    CHECK ((ref_table IS NULL) = (ref_id IS NULL))       -- a reference is a pair or nothing
    INDEX wallet_entries_by_user (org_id, user_id, wallet, created_at DESC, id DESC)
    INDEX wallet_entries_by_org_time (org_id, created_at DESC)
    UNIQUE INDEX wallet_entries_one_per_ref (ref_table, ref_id, wallet)
      WHERE ref_table IS NOT NULL                        -- settlement is idempotent

**deposits**

    id BIGSERIAL PK; org_id; user_id;
    method_id BIGINT NULL REFERENCES payment_methods(id) ON DELETE SET NULL;
    method_kind TEXT NOT NULL CHECK (method_kind IN ('crypto','bank'));   -- snapshot
    method_label TEXT NOT NULL;                                            -- snapshot
    amount NUMERIC(18,2) NOT NULL CHECK (amount > 0);     -- what the investor says they sent, USD
    fee NUMERIC(18,2) NOT NULL DEFAULT 0;                 -- method fee_pct applied at filing
    credited_amount NUMERIC(18,2) NULL;                   -- what the admin credited
    reference TEXT NOT NULL;                              -- tx hash or bank transaction id
    receipt_file_id BIGINT NULL REFERENCES files(id) ON DELETE SET NULL;
    target TEXT NOT NULL DEFAULT 'wallet' CHECK (target IN ('wallet','account'));
    target_account_id BIGINT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE SET NULL;
    note TEXT NULL;
    status TEXT NOT NULL DEFAULT 'pending'
      CHECK (status IN ('pending','confirmed','rejected','cancelled'));
    decided_by; decided_at; decision_note; created_at
    INDEX deposits_queue (org_id, status, created_at)
    INDEX deposits_by_user (org_id, user_id, created_at DESC)
    UNIQUE INDEX deposits_one_live_reference (org_id, lower(reference))
      WHERE status IN ('pending','confirmed')             -- case-insensitive
    UNIQUE INDEX deposits_one_receipt (receipt_file_id)
      WHERE receipt_file_id IS NOT NULL                   -- a receipt backs one notice

**withdrawals** — always from `main`.

    id BIGSERIAL PK; org_id; user_id;
    destination_id BIGINT NOT NULL REFERENCES payout_destinations(id) ON DELETE RESTRICT;
    destination_kind TEXT NOT NULL; destination_summary TEXT NOT NULL;   -- snapshots
    amount NUMERIC(18,2) NOT NULL CHECK (amount > 0);     -- debited from the wallet
    fee NUMERIC(18,2) NOT NULL DEFAULT 0;
    net_amount NUMERIC(18,2) NOT NULL;                    -- amount - fee, what the admin pays
    status TEXT NOT NULL DEFAULT 'requested'
      CHECK (status IN ('requested','approved','paid','rejected','cancelled'));
    decided_by; decided_at; decision_note;
    paid_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL; paid_at TIMESTAMPTZ NULL;
    txid TEXT NULL; created_at
    INDEX withdrawals_queue (org_id, status, created_at)
    INDEX withdrawals_by_user (org_id, user_id, created_at DESC)

**transfers**

    id BIGSERIAL PK; org_id; user_id;
    source_kind TEXT NOT NULL CHECK (source_kind IN ('wallet','account'));
    source_wallet TEXT NULL CHECK (source_wallet IN ('main','credit','pamm','social'));
    source_account_id BIGINT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE SET NULL;
    target_kind TEXT NOT NULL CHECK (target_kind IN ('wallet','account'));
    target_wallet TEXT NULL CHECK (target_wallet IN ('main','credit','pamm','social'));
    target_account_id BIGINT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE SET NULL;
    amount NUMERIC(18,2) NOT NULL CHECK (amount > 0);
    status TEXT NOT NULL DEFAULT 'requested'
      CHECK (status IN ('requested','approved','done','rejected','cancelled'));
    equity_at_request NUMERIC(18,2) NULL; equity_verified BOOLEAN NOT NULL DEFAULT false;
    decided_by; decided_at; decision_note;
    done_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL; done_at TIMESTAMPTZ NULL;
    note TEXT NULL; created_at
    CHECK ((source_kind = 'wallet') = (source_wallet IS NOT NULL))
    CHECK (source_kind = 'account' OR source_account_id IS NULL)
    CHECK ((target_kind = 'wallet') = (target_wallet IS NOT NULL))
    CHECK (target_kind = 'account' OR target_account_id IS NULL)
    CHECK (NOT (source_kind = 'account' AND target_kind = 'account'))
    CHECK (NOT (source_kind = 'wallet' AND target_kind = 'wallet'
                AND source_wallet = target_wallet))
    -- An account end may carry a NULL id: the account FKs are ON DELETE SET
    -- NULL, so removing an MT5 account or disconnecting a cTrader grant keeps
    -- the transfer history instead of failing on a CHECK.
    INDEX transfers_queue (org_id, status, created_at)
    INDEX transfers_by_user (org_id, user_id, created_at DESC)

**Carried over and retired.** `accounts.investor_user_id` and its unique index stay.
The migration copies the three 2026-09-23 tables into the new ones, then drops them:

- `org_investor_wallets` → one `payment_methods` row per org: kind `crypto`, label
  `"<coin> on <network>"`, details from coin/network/address/memo.
- `investor_deposits` → `deposits`: method_kind `crypto`, method_label = coin, reference
  = txid, target `wallet`, `fee` 0, `credited_amount` = amount for confirmed rows, same
  status and audit columns; every `confirmed` row also gets a `wallet_entries` row
  (+amount, `main`, kind `deposit`, ref `deposits`).
- `investor_withdrawals` → `withdrawals`: a `payout_destinations` row per distinct
  (org, user, destination) with kind `crypto`, nickname "Imported", details
  `{"coin":"","network":"","address":<destination>}`, status `approved`; the withdrawal
  row copies amount, status, audit, paid and txid columns, `net_amount = amount`;
  every `paid` row gets a `wallet_entries` row (−amount, `main`, kind `withdrawal`).
- Then `DROP TABLE investor_withdrawals, investor_deposits, org_investor_wallets`.

Production has no rows in any of the three (the 2026-09-25 reset). The copy exists so
a developer database survives the upgrade. `api/tests/test_migration_022.py` asserts
order after `021_mpin.sql`, every column and constraint above, and the copy of one
seeded row per old table. The `db` fixture's TRUNCATE list gains the seven new tables
and loses the three old ones.

## 6. Ledger rules (`api/src/api/portal_ledger.py`)

A pure module, tested without a database, replacing `investor_ledger.py` (which is
deleted; `parse_amount` and `clean_text` move here unchanged).

- `balance(entries) -> Decimal` per wallet: the sum of `amount`.
- `holds(open_withdrawals, open_transfers) -> Decimal` per wallet: the sum of amounts
  of withdrawals in `requested` or `approved` plus transfers in `requested` or
  `approved` whose source is that wallet.
- `available = floor_cents(balance - holds)`. **Floor, never round-half-up.** The API
  reports `available` floored to cents and every cap check compares against that same
  floored Decimal, so "Use max" can never be refused for rounding (the parked bug in
  the previous design).
- `fee_for(amount, fee_pct) = (amount * fee_pct / 100).quantize(Decimal('0.01'),
  ROUND_HALF_UP)`; `net = amount - fee`.
- Transitions, each with `can_transition(table, current, new)`:

      deposits:     pending   -> confirmed | rejected | cancelled
      withdrawals:  requested -> approved | rejected | cancelled ; approved -> paid | rejected
      transfers:    requested -> approved | done | rejected | cancelled ; approved -> done | rejected
      destinations: pending   -> approved | rejected | removed ; approved -> removed

  `cancelled` and `removed` are the investor's own moves; every other move is an admin's.
- Settlement writes ledger rows in the same transaction as the status change and never
  anywhere else:

      deposit confirmed:      +credited_amount  main  kind deposit     ref deposits/<id>
      withdrawal paid:        -amount           main  kind withdrawal  ref withdrawals/<id>
      transfer done, wallet->wallet: -amount source_wallet, +amount target_wallet  ref transfers/<id>
      transfer done, wallet->account: -amount source_wallet
      transfer done, account->wallet: +amount target_wallet
      adjustment:             ±amount           any   kind adjustment  ref NULL, created_by admin

  The unique index on `(ref_table, ref_id, wallet)` makes a retried settlement a no-op
  (`ON CONFLICT DO NOTHING`), so a double click or a replayed request cannot double-credit.
- Trading-account figures keep the 2026-09-23 definitions with one change of source:
  `net_funded = Σ done transfers wallet→account − Σ done transfers account→wallet` (for
  the linked account) replaces `net_deposits`; `profit = equity − net_funded`;
  `account_available = floor_cents(equity − open account→wallet transfers)`. Equity
  resolution (`live` / `last known` / `unknown`) is unchanged.
- Money in API JSON is a float rounded to cents, as today, plus a `currency: "USD"`
  field on every summary and row so the dashboard stops hard-coding the unit.

## 7. Workflows

**Deposit.**

1. Investor opens Deposit, picks a method (Crypto or Bank tab, then a method card),
   reads the details (address with QR and Copy, or bank name / holder / account number /
   code with Copy on each), and sends the money outside the app.
2. Investor files a notice: amount (USD, at least `min_amount`; quick chips 50 / 100 /
   250 / 500 / Min), reference (tx hash or bank transaction id), receipt image (required
   for bank, optional for crypto), deposit to (My wallet, or Trading account when one is
   linked), optional note. The server snapshots method kind and label, computes `fee`
   from the method's `fee_pct`, and files it `pending`. At most 10 notices per investor
   per hour; one live reference per org.
3. Investor may cancel while `pending`.
4. Admin confirms (credited amount defaults to `amount − fee` and can be edited; optional
   note) or rejects (note required). Confirmation credits `main`. When the target is the
   trading account, the same transaction also creates a transfer `main → account` for
   `credited_amount` in status `approved`, so the ledger holds the money until the admin
   funds the broker account and marks the transfer done.

**Withdrawal.** Needs at least one approved payout destination.

1. Investor picks a destination, enters an amount (at least `withdrawal_min`, at most
   `available` of `main`; "Use max" fills the floored available), sees fee and net, and
   confirms with their MPIN in the review dialog.
2. Server: MPIN step-up (section 10), cap check against the floored available, fee from
   `portal_settings`, insert `requested`. 10 per hour per investor.
3. Investor may cancel while `requested`.
4. Admin approves or rejects (note required); pays outside the app and marks paid with
   the transaction id, which debits `main` by `amount` (the org keeps the fee).

**Transfer.** Allowed pairs in phase 1: `main → account`, `account → main`, `pamm →
main`, `social → main`. `credit` cannot move (phase 4 defines its rules). The linked
account is the only account.

1. Investor picks From and To, enters an amount, confirms with MPIN.
2. `pamm → main` and `social → main` complete at once: status `done`, two ledger rows,
   no admin step.
3. `main → account`: cap is `available(main)`; status `requested`; the amount is on hold.
   Admin funds the broker account outside the app and marks done (or approves first as
   an acknowledgement, or rejects). Done debits `main`.
4. `account → main`: cap is `account_available` (live equity when known; accepted with
   `equity_verified=false` when unknown, as withdrawals were). Admin withdraws at the
   broker and marks done, which credits `main`.
5. Investor may cancel while `requested`.

**Payout destination.**

1. Investor adds a bank (nickname, bank name, holder, account number, SWIFT/IFSC code,
   bank address, country) or a crypto address (nickname, coin, network, address), with
   an optional proof image, confirmed with MPIN. Status `pending`.
2. Admin approves or rejects (note required). Only `approved` destinations appear in the
   Withdraw picker.
3. Investor may remove a destination that has no open withdrawal; the row is kept as
   `removed` so history keeps its snapshot.

**Adjustment.** Admin posts a signed amount to any wallet of any investor with a note,
confirmed with the admin's own MPIN. Used for corrections and, until phase 4, bonuses.

## 8. API

All under `/api/orgs/{org_id}`. Investor routes use `require_org_role("investor")` and
resolve the caller's own rows; admin routes use `require_org_role("admin")`. Money in
and out is the string form accepted by `parse_amount` and floats out. Every mutation
audits an `events` row (section 13). `"mpin"` is a body field on step-up routes.

**Investor**

    GET  investor/summary            -> InvestorSummary (section 11.1)
    GET  investor/payment-methods    -> [{id, kind, label, currency, details, min_amount, fee_pct, instructions}]  (enabled only)
    GET  investor/deposits           -> [Deposit]  newest first
    POST investor/deposits           {method_id, amount, reference, receipt_file_id?, target, target_account_id?, note?} -> 201 Deposit
    POST investor/deposits/{id}/cancel -> Deposit
    GET  investor/payout-destinations -> [Destination]  (all statuses except removed)
    POST investor/payout-destinations {kind, nickname, details, proof_file_id?, mpin} -> 201 Destination
    POST investor/payout-destinations/{id}/remove -> Destination
    GET  investor/withdrawals        -> [Withdrawal]
    POST investor/withdrawals        {destination_id, amount, mpin} -> 201 Withdrawal
    POST investor/withdrawals/{id}/cancel -> Withdrawal
    GET  investor/transfers          -> [Transfer]
    POST investor/transfers          {source, target, amount, mpin} -> 201 Transfer
         source/target: {"kind":"wallet","wallet":"main"} | {"kind":"account","account_id":N}
    POST investor/transfers/{id}/cancel -> Transfer
    GET  investor/wallet-entries?wallet=&kind=&from=&to=&limit=  -> {entries:[Entry], has_more}
    POST investor/files  (multipart: purpose, file)  -> 201 {id, purpose, content_type, size_bytes}
    GET  investor/files/{id}         -> the bytes (owner only)
    GET  investor/positions | investor/analytics | investor/history/{kind}   unchanged

**Admin**

    GET  investors                   -> [InvestorRow] (adds balances {main,credit,pamm,social}, on_hold, available, pending counts per type)
    PUT  investors/{user_id}/account {account_id|null}   unchanged
    GET  investors/{user_id}/wallet-entries?wallet=&limit=  -> {entries, has_more}
    POST investors/{user_id}/adjustments {wallet, amount (signed), note, mpin} -> 201 Entry
    GET  payment-methods             -> [PaymentMethod]  (all, incl. disabled)
    POST payment-methods             {kind, label, currency?, details, min_amount?, fee_pct?, instructions?, sort_order?} -> 201
    PATCH payment-methods/{id}       any of the above plus enabled -> PaymentMethod
    DELETE payment-methods/{id}      -> 204  (409 while a pending deposit references it)
    GET  portal-settings / PUT portal-settings {withdrawal_min, withdrawal_fee_pct}
    GET  requests/summary            -> {deposits, withdrawals, transfers, payout_destinations, total}  (open counts)
    GET  deposits?status=            -> [Deposit + email, display_name]  open first
    POST deposits/{id}/decision      {status: confirmed|rejected, credited_amount?, note?}
    GET  withdrawals?status=         -> [Withdrawal + email, display_name]
    POST withdrawals/{id}/decision   {status: approved|rejected, note?}
    POST withdrawals/{id}/paid       {txid}
    GET  transfers?status=           -> [Transfer + email, display_name]
    POST transfers/{id}/decision     {status: approved|done|rejected, note?}
    GET  payout-destinations?status= -> [Destination + email, display_name]
    POST payout-destinations/{id}/decision {status: approved|rejected, note?}
    GET  files/{id}                  -> the bytes (any file in the org)

Refusals follow the existing house style: 400 for validation with the ledger's message
text, 404 for a row that is not the caller's, 409 for an illegal transition
(`deposit is already confirmed`), 409 `decided by someone else` on a lost race (every
decision UPDATE carries `AND status = <expected>`), 429 for the hourly limits, and the
MPIN bodies of section 10. Money rules refuse with the figure they applied:
`amount exceeds what is available (1,250.00)`, `minimum deposit for this method is
500.00`, `minimum withdrawal is 50.00`.

Row shapes (`dashboard/src/lib/types.ts`):

    Deposit      {id, user_id, method_id, method_kind, method_label, amount, fee, credited_amount, reference, receipt_file_id, target, target_account_id, note, status, decided_by, decided_at, decision_note, created_at, currency, email?, display_name?}
    Withdrawal   {id, user_id, destination_id, destination_kind, destination_summary, amount, fee, net_amount, status, decided_by, decided_at, decision_note, paid_by, paid_at, txid, created_at, currency, email?, display_name?}
    Transfer     {id, user_id, source:{kind, wallet?, account_id?}, target:{...}, amount, status, equity_at_request, equity_verified, decided_by, decided_at, decision_note, done_by, done_at, note, created_at, currency, email?, display_name?}
    Destination  {id, user_id, kind, nickname, details, proof_file_id, status, decided_by, decided_at, decision_note, created_at, summary, email?, display_name?}
    Entry        {id, wallet, amount, kind, ref_table, ref_id, note, created_at, currency}
    PaymentMethod{id, kind, label, enabled, currency, details, min_amount, fee_pct, instructions, sort_order}

`Destination.summary` and `withdrawals.destination_summary` are built server-side:
bank → `"<bank_name> ••<last 4 of account_number>"`, crypto → `"<network> <shortAddress>"`.
Bank account numbers are returned in full only to the owner and to admins.

## 9. Files and uploads

- `POST investor/files` accepts `multipart/form-data` (`python-multipart` is added to
  `api/pyproject.toml`) with `purpose` and one `file`. Phase 1 accepts purposes
  `deposit_receipt` and `payout_proof`; the others are refused with 400 until their
  phase. Limits: 5 MB; `image/jpeg`, `image/png`, `image/webp`, `application/pdf`,
  decided by sniffing the first bytes, never by the client's header or extension.
  30 uploads per investor per hour.
- Bytes are written to `UPLOAD_DIR/<org_id>/<file_id>.<ext>` (`UPLOAD_DIR` env, default
  `/data/uploads` in the container, `./data/uploads` locally, created on start). The
  row's `storage_key` is that relative path; `sha256` is recorded for backup checks.
- Serving: `GET investor/files/{id}` (owner) and `GET files/{id}` (admin) stream the
  bytes with `Content-Type` from the row, `Content-Disposition: inline; filename=`,
  `X-Content-Type-Options: nosniff`, `Cache-Control: private, max-age=0`. A PDF is
  served as `attachment`, never inline.
- A file is referenced by at most one request row; the request routes check that the
  file belongs to the caller and carries the right purpose, else 400.
- Ops: `docker-compose.yml` adds a named volume `uploads` mounted at `/data/uploads` on
  the api service; the api Dockerfile creates `/data/uploads` owned by `appuser`;
  `ops/backup.sh` tars the volume next to the nightly dump; `.env.example` documents
  `UPLOAD_DIR`. `README.md` deploy section mentions the volume.

## 10. MPIN step-up

The login MPIN becomes the confirmation for money actions. `_check_mpin` moves from
`routes/mpin.py` to a new `api/src/api/mpin_core.py` as `check_mpin(conn, user_id,
mpin) -> Optional[Response]` with its atomic attempt reservation, 5-try / 15-minute lock
and constant-time behaviour unchanged; `routes/mpin.py` imports it. A helper
`require_mpin(conn, user_id, body_mpin) -> Optional[Response]` validates the shape
(`^[0-9]{6}$`, else 400 `MPIN must be exactly 6 digits`) then calls `check_mpin`. Step-up
routes call it first and return its Response when present: 409 `MPIN not set`, 423
`MPIN locked` with `locked_until`, 401 `Invalid MPIN` with `attempts_left`. The lock is
shared with login on purpose: five wrong guesses anywhere lock the MPIN everywhere.

Step-up routes: withdrawal request, transfer request, payout destination add, admin
adjustment. Deposit notices and cancellations are not gated.

Dashboard: `components/PinConfirmDialog.tsx` wraps `ConfirmDialog` with a `PinInput`,
a summary slot (children), `confirmLabel`, and the same 401 / 423 messaging as
`AccountSecurity` ("Wrong MPIN, 2 tries left", "MPIN locked. Try again in about 14
minutes"); the confirm button is disabled until six digits are entered and the dialog
clears the PIN on every failure. Calls go through `orgApi` with `{ redirectOn401:
false }`, so a wrong PIN never bounces to `/login`.

## 11. Investor UI

Routes stay under `/org/:orgId/invest/*`, pages in `src/pages/investor/`, exported from
`pages/groups/investor.ts`. `investorNav(orgId)` becomes three groups: `''`
[Dashboard], `Money` [Wallet, Deposit, Withdraw, Transfer, Transactions, Payout
accounts], `Trading` [Account, History]. `bottomBarItems` for investors returns
Dashboard, Deposit, Withdraw, Transactions, and `BottomBar` shows the More button for
investors too (the Drawer menu lists every group). Layout, NavRail and BottomBar tests
change accordingly.

Every money figure passes through one `Money` component that honours the hide-balances
toggle (localStorage `mf.hideBalances`; masked figures render `••••` with
`aria-label` of the real value removed, so a screen reader hears "hidden"). Amount
inputs keep the `^\d+(\.\d{1,2})?$` gate and post the typed string.

**11.1 Dashboard** (`invest`, replaces InvestorOverview). `InvestorSummary`:

    {org:{id,name}, currency:'USD',
     investor:{display_name, first_name, member_since},
     wallets:{main:{balance,on_hold,available}, credit:{...}, pamm:{...}, social:{...}},
     totals:{deposited, withdrawn, transferred_in, transferred_out},       -- lifetime, settled
     cash_flow:[{date:'2026-09-29', deposits, withdrawals}],               -- last 90 days, daily
     pending:{deposits, withdrawals, transfers, payout_destinations},
     link_state, account|null, equity_source, equity|null, net_funded, profit|null,
     account_available|null, open_positions:int}

Layout, top to bottom: greeting card ("Good afternoon, Sherwyn" by the browser's hour;
"Wallet balance" with the hide toggle, the big `main.available`, "On hold" when non-zero,
lifetime P&L chip = `profit`, buttons Deposit / Withdraw / Transfer); four StatTiles (My
wallet, Total deposited, Total withdrawn, Total transferred) with 7-day sparklines from
`cash_flow`; Trading account card (existing account card plus equity, net funded, profit,
open positions count, link to Account; the NextStep "being set up" card when unlinked,
with copy that now says deposits and wallet transfers work before an account is linked);
Cash flow card (7D / 30D / 90D, deposits and withdrawals bars from `charts.tsx`
`PnlBars`, totals strip: deposits, withdrawals, net); Pending requests row (one line per
non-zero count linking to the page); Recent activity (last 8 `wallet_entries`). Polls
every 10 s as today.

**11.2 Wallet** (`invest/wallet`). Four wallet rows (name, balance, share of total as a
bar, on hold, available); Quick actions cards (Deposit, Withdraw, Transfer,
Transactions); Recent entries (last 20) with View all.

**11.3 Deposit** (`invest/deposit`). Tabs Crypto / Bank (hidden when no method of that
kind is enabled); method cards on the left (label, min, fee); details pane on the right:
crypto shows QR (lazy `qrcode`, as today), address with Copy and select fallback, memo
and instructions; bank shows a definition list with a Copy button per value. Below, the
notice form: amount with quick chips, reference, receipt upload (`FileInput` component:
accept list, size hint, preview thumbnail, required flag for bank), deposit to (radio:
My wallet / Trading account, the second only when linked), note; submit "File deposit
notice". History table: Date, Method, Amount, Credited, Reference, Receipt (thumbnail
opens the file), Status, and a Cancel button while pending. NextStep "Deposits are not
open yet" when no method is enabled.

**11.4 Withdraw** (`invest/withdraw`). Available card (`main.available`, on hold).
Destination Select of approved destinations with "Add a payout account" link when none;
amount with Use max; fee / net preview from `portal_settings` (fetched with the
summary); "Request withdrawal" opens `PinConfirmDialog` ("Send 250.00 USD to ICICI
••4543?"). History: Timeline requested → approved → paid, Rejected or Cancelled badge,
Cancel while requested.

**11.5 Transfer** (`invest/transfer`). From and To pickers (My wallet, PAMM wallet,
Social wallet, Trading account when linked; the pair rules of section 7 disable
impossible targets), amount, "Available" under From (wallet available or
`account_available`), `PinConfirmDialog`; history with statuses and Cancel.

**11.6 Transactions** (`invest/transactions`). Tabs All / My wallet / Credit / PAMM /
Social; filters From date, To date, Kind; table Date, Wallet, Kind, Amount (signed,
profit/loss tone), Reference (links to the deposit / withdrawal / transfer row's page),
Note; "Download CSV" builds the file client-side from the loaded rows; paging by
`has_more` with a Load more button.

**11.7 Payout accounts** (`invest/payout-accounts`). Two cards, Bank and Crypto, each a
list (nickname, summary, status badge, Remove) and an Add button opening a Drawer with
the form and `PinConfirmDialog` on save.

**11.8 Account** (`invest/account`). Gains the open positions table and the 4-week
analytics snapshot moved from the old Overview; keeps the profile list and
`AccountSecurity`.

Status vocabulary (`lib/investor.ts`) gains: cancelled → "Cancelled" (quiet), done →
"Done" (ok), removed → "Removed" (quiet); transfer `approved` → "Approved, in progress".

## 12. Admin UI

- **Investors** (`investors`, rewritten). Tabs Investors / Payment methods.
  *Investors*: table Name, Email, My wallet (balance, on hold), Equity, Linked account
  (Select, as today), Open requests (count chips linking to Requests), row Menu: View
  ledger (Drawer with the entries table and wallet filter), Adjust balance
  (`PinConfirmDialog` with wallet Select, signed amount, note). *Payment methods*: list
  with enabled toggle, Add method (Drawer: kind, label, details fields per kind, min,
  fee %, instructions), Edit, Delete; Withdrawal settings card (min, fee %) with the
  dirty-guard pattern.
- **Requests** (`requests`, new). Tabs Deposits / Withdrawals / Transfers / Payout
  accounts with open counts; Open / All toggle. Each table follows the Investors page's
  `Pending` descriptor + shared `ConfirmDialog` pattern with these additions: the
  Confirm-deposit dialog has a credited amount input prefilled with `amount − fee`; a
  Details drawer per row shows the full request, the investor, the receipt or proof
  image (inline, via `GET files/{id}`), destination details, and the audit trail.
  Actions: Confirm / Reject (deposits), Approve / Reject / Mark paid (withdrawals),
  Approve / Mark done / Reject (transfers), Approve / Reject (destinations). Every row
  action carries the unique `aria-label` pattern the existing tests rely on.
- **Navigation.** `NavItem` gains `badge?: number`; `NavRail` renders it as a neutral
  pill after the label. `adminNav` Org group becomes Members, Investors, Requests, Logs;
  `Layout` fetches `requests/summary` every 30 s for admins (and on `control` events
  through `useLiveRefresh`) and passes the total as the Requests badge. The rail comment
  "ten links at most" becomes eleven.

## 13. Notifications and audit

Every mutation writes `events` (category `control`, `actor_email` from the context,
`payload.action` below, `payload.user_id` = the investor so alert cooldowns scope per
investor). Warnings reach the admin email and Telegram channels through `ALERT_RULES`
and `TELEGRAM_RULES`; info rows only drive live refresh.

    investor_deposit_noticed (warning)     investor_deposit_decided        investor_deposit_cancelled
    investor_withdrawal_requested (warning) investor_withdrawal_decided    investor_withdrawal_paid    investor_withdrawal_cancelled
    investor_transfer_requested (warning)  investor_transfer_decided       investor_transfer_cancelled
    investor_destination_added (warning)   investor_destination_decided    investor_destination_removed
    investor_ledger_adjusted (warning)     payment_method_changed (warning) portal_settings_changed

Investor emails (`alerter.send_to`, best effort) on: deposit decided, withdrawal
decided, withdrawal paid, transfer decided or done, destination decided, adjustment
posted. Subject pattern "Your deposit of 250.00 USD was confirmed".

## 14. Testing

- `api/tests/test_portal_ledger.py` (replaces `test_investor_ledger.py`): balances,
  holds, floor-cents available (including the 5120.506 case), fees, every transition
  table.
- `api/tests/test_migration_022.py`: order, columns, constraints, the row copy.
- `api/tests/test_portal_money.py` (replaces `test_investor_portal.py`, keeping its
  helpers): payment methods CRUD and visibility; deposit notice → confirm credits the
  ledger once even when the decision is replayed; target account creates the approved
  transfer; cancel; duplicate reference; withdrawal needs an approved destination, cap
  against floored available, fee and net, MPIN 401/423/409, approve → paid debits;
  transfers per pair rule including instant `pamm → main` and cap on `account → main`;
  destination add with MPIN, approve, remove refused with an open withdrawal;
  adjustments; investors list figures; requests summary; audit actions and emails;
  role refusals (viewer on investor routes, investor on admin routes).
- `api/tests/test_uploads.py`: size and type sniffing, owner-only read, admin read,
  purpose check, storage path, hourly limit.
- `api/tests/test_mpin.py` keeps passing against `mpin_core`.
- Dashboard: a colocated test per page following the `mockUseOrg` + fetch-stub pattern;
  `PinConfirmDialog.test.tsx`, `FileInput.test.tsx`, `Money.test.tsx`;
  `Requests.test.tsx`, `Investors.test.tsx`; nav tests for the new groups, badge and the
  investor More button; `vocabulary.test.ts` unchanged; `npm test` green with
  `--maxWorkers=2` on this machine.
- The copier is untouched; the plan's final task proves it with `git diff --stat main..
  -- copier/` being empty.

## 15. Deploy and ops

Migration 022 means the full sequence on the host: `git pull`, `docker compose build
migrate api`, `docker compose stop api`, `docker compose run --rm migrate`, `docker
compose up -d api`, then a hard reload. First deploy also creates the `uploads` volume
(compose does it). `.env` needs nothing new; `UPLOAD_DIR` is optional. After deploy the
admin must add at least one payment method before investors can deposit.

## 16. Decisions for the owner

1. Withdrawal fee and minimum default to 0; the admin sets them on the Investors page.
2. Transfer pairs are limited to the four listed; `credit` waits for phase 4.
3. Investors can deposit and hold wallet money before any trading account is linked.
4. The role keeps the name Investor in the UI.
5. Open self-signup is decided in phase 2 with KYC.
