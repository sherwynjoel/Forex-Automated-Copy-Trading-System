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


# ------------------------------------------------------------ phase 2

COMPLETE_PROFILE = {
    "full_name": "Investor One", "gender": "male", "date_of_birth": "1990-04-02",
    "phone": "+91 98765 43210", "address_line": "12 Lake Road", "city": "Coimbatore",
    "state": "Tamil Nadu", "postal_code": "641001", "country_residence": "IN",
    "country_citizenship": "IN", "id_type": "passport", "id_number": "P1234567"}


def add_package(db, org_id, *, name="Standard", min_deposit="100", leverage=(100, 200, 500),
                spread_label="20-25", enabled=True, sort_order=0) -> int:
    """An account package the org offers."""
    with psycopg.connect(db, autocommit=True) as conn:
        (package_id,) = conn.execute(
            "INSERT INTO account_packages (org_id, name, min_deposit, spread_label, "
            "leverage_options, enabled, sort_order) VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id",
            (org_id, name, Decimal(str(min_deposit)), spread_label, list(leverage), enabled,
             sort_order)).fetchone()
    return int(package_id)


def kyc_profile(db, org_id, user_id, *, status="approved", **over) -> dict:
    """A complete profile with its four documents seeded as files rows.
    Returns the column values written (file ids included)."""
    files = {
        "id_front_file_id": seed_file(db, org_id, user_id, purpose="kyc_document"),
        "id_back_file_id": seed_file(db, org_id, user_id, purpose="kyc_document"),
        "address_proof_file_id": seed_file(db, org_id, user_id, purpose="kyc_document"),
        "photo_file_id": seed_file(db, org_id, user_id, purpose="kyc_photo"),
    }
    row = {**COMPLETE_PROFILE, **files, **over}
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(
            f"INSERT INTO kyc_profiles (org_id, user_id, status, submitted_at, {cols}) "
            f"VALUES (%s, %s, %s, CASE WHEN %s::text = 'draft' THEN NULL ELSE now() END, {marks})",
            (org_id, user_id, status, status, *row.values()))
    return row


def open_account_request(db, org_id, user_id, package_id, *, main="Main1234",
                         investor="Inv12345", leverage=100, package_name="Standard") -> int:
    """A 'requested' account request with both passwords sealed under the
    app's Fernet key (os.environ['FERNET_KEY'], which app_client sets)."""
    import os
    from cryptography.fernet import Fernet
    cipher = Fernet(os.environ["FERNET_KEY"].encode())
    with psycopg.connect(db, autocommit=True) as conn:
        (req_id,) = conn.execute(
            "INSERT INTO account_requests (org_id, user_id, package_id, package_name, leverage, "
            "main_password_enc, investor_password_enc) VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id",
            (org_id, user_id, package_id, package_name, leverage,
             cipher.encrypt(main.encode()).decode(),
             cipher.encrypt(investor.encode()).decode())).fetchone()
    return int(req_id)
