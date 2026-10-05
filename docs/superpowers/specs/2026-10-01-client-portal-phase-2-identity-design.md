# Client portal, phase 2: verification, profile, security and account requests

**Date:** 2026-10-01
**Status:** implemented on branch client-portal-phase-2 (plan docs/superpowers/plans/2026-10-01-client-portal-phase-2.md); awaiting deploy
**Builds on:** `2026-09-29-client-portal-phase-1-money-design.md` (live since 2026-09-30).
**Reference:** `docs/reference/aiprime-portal-survey.md` (KYC, Profile, Password,
Authentication, Open Live Account, Activity Logs).

## 1. Goal

An investor completes their profile and identity verification (KYC) in the portal; an
admin reviews it. Once verified, the investor can request a live MT5 trading account
from packages the admin defines; the admin creates the login at the broker and hands it
over through the portal. The investor sees their sign-in history and manages password,
MPIN and sessions in one Security page.

## 2. Decisions (owner, 2026-10-01)

- KYC approval is required **only to request a trading account**. Deposits, withdrawals
  and transfers do not wait for KYC.
- No new second factor: the mandatory 6-digit MPIN stays the only one. No authenticator
  app, no email OTP.
- Sign-up stays invite-only.
- Account requests are **live only**, from admin-defined packages. The admin creates the
  MT5 login at the broker by hand (no Manager API), as decided in phase 1.

## 3. Non-goals

- Automated identity checks (OCR, liveness, sanctions screening). A person reviews.
- Demo accounts, multiple accounts per investor (phase 3), account opening through a
  broker API.
- Email OTP, authenticator apps, password reset by email.
- Avatar upload (the purpose exists in `files`; no UI in this phase).

## 4. Data (migration `023_portal_identity.sql`)

**kyc_profiles** — one row per investor per org; the profile and the verification
share it, because the reference's KYC step 1 *is* the profile.

    org_id, user_id  PK (org_id, user_id), FKs CASCADE
    full_name TEXT NULL, gender TEXT NULL CHECK (gender IN ('male','female','other')),
    date_of_birth DATE NULL, phone TEXT NULL,
    address_line TEXT NULL, area TEXT NULL, landmark TEXT NULL, city TEXT NULL,
    state TEXT NULL, postal_code TEXT NULL,
    country_residence TEXT NULL, country_citizenship TEXT NULL,   -- ISO 3166 alpha-2
    id_type TEXT NULL CHECK (id_type IN ('passport','national_id','driving_licence')),
    id_number TEXT NULL,
    id_front_file_id, id_back_file_id, address_proof_file_id, photo_file_id
        BIGINT NULL REFERENCES files(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','submitted','approved','rejected')),
    submitted_at TIMESTAMPTZ NULL,
    decided_by BIGINT NULL REFERENCES users(id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ NULL, decision_note TEXT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    INDEX kyc_profiles_queue (org_id, status, submitted_at)

Transitions: draft → submitted (investor; every required field and the four files
present) → approved | rejected (admin; note required on reject); rejected → submitted
(investor resubmits after editing). An **approved** profile stays approved when the
investor edits contact details (phone, address lines, city, state, postal code);
editing name, date of birth, citizenship, ID fields or any document returns it to
`draft` (re-verification).

**account_packages** — what an investor may request.

    id BIGSERIAL PK, org_id FK CASCADE,
    name TEXT NOT NULL, min_deposit NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (>= 0),
    currency TEXT NOT NULL DEFAULT 'USD', spread_label TEXT NULL,      -- e.g. "20-25"
    leverage_options INTEGER[] NOT NULL CHECK (cardinality(leverage_options) > 0),
    enabled BOOLEAN NOT NULL DEFAULT true, sort_order INTEGER NOT NULL DEFAULT 0,
    created_at, updated_at
    INDEX account_packages_by_org (org_id, sort_order, id)

**account_requests**

    id BIGSERIAL PK, org_id, user_id (FKs CASCADE),
    package_id BIGINT NULL REFERENCES account_packages(id) ON DELETE SET NULL,
    package_name TEXT NOT NULL,                      -- snapshot
    leverage INTEGER NOT NULL CHECK (leverage > 0),
    main_password_enc TEXT NULL, investor_password_enc TEXT NULL,   -- Fernet; wiped on decision
    status TEXT NOT NULL DEFAULT 'requested'
        CHECK (status IN ('requested','fulfilled','rejected','cancelled')),
    mt5_login BIGINT NULL, mt5_server TEXT NULL,     -- set on fulfil
    account_id BIGINT NULL REFERENCES accounts(ctid_trader_account_id) ON DELETE SET NULL,
    decided_by, decided_at, decision_note, created_at
    INDEX account_requests_queue (org_id, status, created_at)
    UNIQUE INDEX account_requests_one_open (org_id, user_id) WHERE status = 'requested'

**login_events** — sign-in history.

    id BIGSERIAL PK, user_id FK CASCADE, ip TEXT NOT NULL, user_agent TEXT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('password_ok','mpin_ok','failed')),
    created_at  INDEX login_events_by_user (user_id, created_at DESC)

Written by `/api/login` (`password_ok` or `failed` for a known email; nothing for an
unknown email, so the table cannot be used to probe accounts) and by `/api/mpin/verify`
(`mpin_ok`). Rows older than 180 days are deleted on write
(`# ponytail: delete-on-write, a nightly job if writes ever get hot`).

## 5. Rules

- **Uploads:** `PHASE1_PURPOSES` becomes the accepted set `{deposit_receipt,
  payout_proof, kyc_document, kyc_photo}`. Same 5 MB cap, sniffing and owner-only reads.
  A KYC file belongs to at most one profile slot (a file reused in another slot or row is
  refused, as receipts are today).
- **KYC gate:** `POST investor/account-requests` answers 409 `verify your identity
  first` unless the caller's profile is `approved`.
- **One account at a time:** a request is refused with 409 `you already have a trading
  account` when the investor already has a linked account (phase 1 links one account per
  investor), and with 409 `a request is already open` when one is `requested`.
- **Passwords:** the investor sets a main and an investor password (8–32 chars, at
  least one upper, lower and digit; the dashboard can generate one). Both are encrypted
  with the existing Fernet key and stored until the request is decided. The admin sees
  them **once** when opening the fulfil dialog (an audited `reveal`), to create the login
  at the broker; on fulfil, reject or cancel both columns are set to NULL.
- **Fulfil:** the admin enters `mt5_login` and `mt5_server` and may pick an existing
  MirrorFleet MT5 account to link to the investor (the phase 1 link rule applies: not
  the master, not linked to someone else). The investor sees login, server and package on
  their Account page; the passwords are theirs already.
- **MPIN step-up:** submitting KYC, requesting an account and changing the password
  require the MPIN (same `require_mpin` contract as phase 1). `POST /api/me/password`
  gains a required `mpin` field.
- **Audit and alerts:** `investor_kyc_submitted` (warning), `investor_kyc_decided`,
  `investor_account_requested` (warning), `investor_account_request_decided`,
  `account_request_passwords_revealed` (warning), `account_package_changed`; investors
  are emailed on every decision.

## 6. API (under `/api/orgs/{org_id}`; investor routes investor-only, admin routes admin)

Investor:

    GET  investor/profile                    -> KycProfile (fields, file ids, status, decision)
    PUT  investor/profile                    {fields...}            -> KycProfile  (draft/rejected/approved-contact rules above)
    POST investor/profile/submit             {mpin}                 -> KycProfile  (400 lists missing fields)
    GET  investor/account-packages           -> enabled packages
    GET  investor/account-requests           -> own requests (no passwords)
    POST investor/account-requests           {package_id, leverage, main_password, investor_password, mpin} -> 201
    POST investor/account-requests/{id}/cancel

Admin:

    GET  kyc?status=                         -> profiles + email, display_name (open first, LIMIT 500)
    POST kyc/{user_id}/decision              {status: approved|rejected, note?}
    GET/POST account-packages, PATCH/DELETE account-packages/{id}   (409 on delete while a request references it and is open)
    GET  account-requests?status=
    POST account-requests/{id}/reveal        {mpin}  -> {main_password, investor_password}  (admin's MPIN; audited; 409 once decided)
    POST account-requests/{id}/fulfil        {mt5_login, mt5_server, account_id?, note?}
    POST account-requests/{id}/reject        {note}
    GET  investors                           gains kyc_status per row

Self: `POST /api/me/password` gains `mpin`; `GET /api/me/sign-ins` (any role; the investor Security page and the
desk's own Security card both use it).

## 7. Dashboard

Investor nav, Trading group becomes **Account**: Profile & verification, Open account,
Security, History. Dashboard gains a verification card (status and a link) like the
reference's KYC ring.

- **Profile & verification** (`invest/profile`): stepper Profile → Identity → Address →
  Photo → Review, each a form section with `FileInput` uploads; Save draft; Submit with
  `PinConfirmDialog`; status banner (draft / submitted / approved / rejected with note).
- **Open account** (`invest/open-account`): NextStep "Verify your identity first" until
  approved; otherwise package cards → leverage select + two passwords with Generate and
  show/hide → `PinConfirmDialog` → request status card (requested / fulfilled with login
  and server / rejected with note), Cancel while requested.
- **Security** (`invest/security`): the existing `AccountSecurity` (password change now
  asks for the MPIN), plus a sign-in history table (time, IP, device, outcome).
- **Admin Requests desk** gains tabs **Verification** (profile details, the four
  document images, Approve / Reject with note) and **Account requests** (Reveal
  passwords with MPIN, Fulfil with login, server and optional account link, Reject).
- **Admin Investors** gains a KYC column and an **Account packages** tab (list, add/edit
  drawer, enable toggle, delete).
- The desk's own Members/Security area shows the admin's sign-in history.

## 8. Testing

API: migration 023 shape; KYC field rules, submit completeness, re-verification on
identity edits, decisions; uploads accept the KYC purposes; account request gates (KYC,
existing account, open request), password policy, encryption at rest, reveal once and
audited, fulfil linking, wipe on decision; login_events written for password and MPIN,
not for unknown emails; password change requires the MPIN; RBAC rows for every route.
Dashboard: a test per page and tab, zero act() warnings, full gate green.

## 9. Deploy

Migration 023 (additive, no drops): the README "Upgrading with a migration" sequence.
Admins must add at least one account package before investors can request accounts.
