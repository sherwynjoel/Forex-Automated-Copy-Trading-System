# api/src/api/portal_identity.py
"""Client portal, phase 2: the identity rules shared by the investor and
admin routes -- which profile fields exist and how each is cleaned, what a
complete profile needs, which edits send an approved profile back to
verification, the MT5 password policy, Fernet sealing for the two account
passwords, and the row serialisers. No routes here; one small database
helper (kyc_status)."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

import psycopg
from cryptography.fernet import Fernet

from .portal_ledger import LedgerError, clean_text, money

PROFILE_FIELDS: tuple[str, ...] = (
    "full_name", "gender", "date_of_birth", "phone", "address_line", "area", "landmark",
    "city", "state", "postal_code", "country_residence", "country_citizenship", "id_type",
    "id_number", "id_front_file_id", "id_back_file_id", "address_proof_file_id",
    "photo_file_id")

# Document slot -> the files.purpose an upload for it must carry.
FILE_SLOTS: dict[str, str] = {
    "id_front_file_id": "kyc_document",
    "id_back_file_id": "kyc_document",
    "address_proof_file_id": "kyc_document",
    "photo_file_id": "kyc_photo",
}
FILE_LABELS: dict[str, str] = {
    "id_front_file_id": "ID front",
    "id_back_file_id": "ID back",
    "address_proof_file_id": "address proof",
    "photo_file_id": "photo",
}
OPTIONAL_FIELDS = frozenset({"area", "landmark", "state"})
REQUIRED_FIELDS: tuple[str, ...] = tuple(f for f in PROFILE_FIELDS if f not in OPTIONAL_FIELDS)
# Spec section 4: editing these keeps an approved profile approved; any
# other change (name, date of birth, gender, countries, ID fields, any
# document) sends it back to draft for re-verification.
CONTACT_FIELDS = frozenset({"phone", "address_line", "area", "landmark", "city", "state",
                            "postal_code"})

TEXT_LIMITS: dict[str, int] = {
    "full_name": 128, "phone": 32, "address_line": 256, "area": 128, "landmark": 128,
    "city": 128, "state": 128, "postal_code": 16, "id_number": 64,
}
CHOICES: dict[str, tuple[str, ...]] = {
    "gender": ("male", "female", "other"),
    "id_type": ("passport", "national_id", "driving_licence"),
}
COUNTRY_FIELDS = ("country_residence", "country_citizenship")
COUNTRY_RE = re.compile(r"[A-Za-z]{2}")  # ASCII only: "ß".upper() is "SS"
MAX_FILE_ID = 2**63 - 1  # BIGINT


def clean_profile_field(field: str, raw: object) -> Any:
    """One profile value, normalised for storage. '' and None mean "clear
    it" (None). Raises LedgerError with the message the route returns as a
    400."""
    if field not in PROFILE_FIELDS:
        raise LedgerError(f"unknown field: {field}")
    if field in FILE_SLOTS:
        if raw is None or raw == "":
            return None
        if isinstance(raw, bool) or not isinstance(raw, int) or not 0 < raw <= MAX_FILE_ID:
            raise LedgerError(f"{field} must be a file id")
        return raw
    text = clean_text(raw, field, max_len=TEXT_LIMITS.get(field, 128), required=False)
    if text is None:
        return None
    if field in CHOICES:
        value = text.lower()
        if value not in CHOICES[field]:
            raise LedgerError(f"{field} must be one of {', '.join(CHOICES[field])}")
        return value
    if field in COUNTRY_FIELDS:
        if not COUNTRY_RE.fullmatch(text):
            raise LedgerError(f"{field} must be a two-letter country code")
        return text.upper()
    if field == "date_of_birth":
        try:
            born = date.fromisoformat(text)
        except ValueError:
            raise LedgerError("date_of_birth must be a date (YYYY-MM-DD)")
        if born >= date.today():
            raise LedgerError("date_of_birth must be in the past")
        return born
    return text


def missing_fields(profile: dict) -> list[str]:
    """The required fields still empty, in form order."""
    return [f for f in REQUIRED_FIELDS if profile.get(f) in (None, "")]


def _plain(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


def needs_reverification(before: dict, changes: dict) -> bool:
    """Whether these changes touch anything an admin verified. `before` is
    a profile_json dict (dates as ISO strings); `changes` holds cleaned
    values (dates as date objects)."""
    return any(key not in CONTACT_FIELDS and _plain(value) != before.get(key)
               for key, value in changes.items())


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


PROFILE_COLS = ("user_id, " + ", ".join(PROFILE_FIELDS)
                + ", status, submitted_at, decided_by, decided_at, decision_note, updated_at")


def profile_json(row) -> dict:
    """A kyc_profiles row (PROFILE_COLS order, optionally followed by the
    admin join's email and display_name) as the API returns it, with the
    list of required fields still missing."""
    n = len(PROFILE_FIELDS)
    values = dict(zip(PROFILE_FIELDS, row[1:1 + n]))
    values["date_of_birth"] = _iso(values["date_of_birth"])
    status, submitted_at, decided_by, decided_at, note, updated_at = row[1 + n:7 + n]
    out = {"user_id": row[0], **values, "status": status, "submitted_at": _iso(submitted_at),
           "decided_by": decided_by, "decided_at": _iso(decided_at), "decision_note": note,
           "updated_at": _iso(updated_at)}
    out["missing"] = missing_fields(out)
    if len(row) > 7 + n:
        out["email"], out["display_name"] = row[7 + n], row[8 + n]
    return out


def empty_profile(user_id: int) -> dict:
    """What GET investor/profile answers before the first save."""
    return {"user_id": user_id, **{f: None for f in PROFILE_FIELDS}, "status": "draft",
            "submitted_at": None, "decided_by": None, "decided_at": None,
            "decision_note": None, "updated_at": None, "missing": list(REQUIRED_FIELDS)}


def kyc_status(conn: psycopg.Connection, org_id: int, user_id: int) -> str:
    """The investor's verification status; 'draft' before the first save."""
    row = conn.execute("SELECT status FROM kyc_profiles WHERE org_id = %s AND user_id = %s",
                       (org_id, user_id)).fetchone()
    return row[0] if row else "draft"


# Printable ASCII without spaces, 8-32 long: what MT5 servers accept.
MT5_PASSWORD_RE = re.compile(r"[!-~]{8,32}")


def check_mt5_password(raw: object, field: str) -> str:
    if (not isinstance(raw, str) or not MT5_PASSWORD_RE.fullmatch(raw)
            or not re.search(r"[A-Z]", raw) or not re.search(r"[a-z]", raw)
            or not re.search(r"[0-9]", raw)):
        raise LedgerError(f"{field} must be 8-32 characters without spaces, with an "
                          "upper-case letter, a lower-case letter and a digit")
    return raw


MAX_LEVERAGE = 3000


def parse_leverage_options(raw: object) -> list[int]:
    message = f"leverage_options must be a list of whole numbers from 1 to {MAX_LEVERAGE}"
    if not isinstance(raw, list) or not raw or len(raw) > 12:
        raise LedgerError(message)
    out = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LEVERAGE:
            raise LedgerError(message)
        out.append(value)
    return sorted(set(out))


def seal(fernet_key: str, secret: str) -> str:
    """Encrypt with the app's FERNET_KEY, the same key oauth.py seals the
    cTrader tokens with."""
    return Fernet(fernet_key.encode()).encrypt(secret.encode()).decode()


def unseal(fernet_key: str, token: str) -> str:
    """Raises cryptography.fernet.InvalidToken when the key has changed."""
    return Fernet(fernet_key.encode()).decrypt(token.encode()).decode()


PACKAGE_COLS = "id, name, min_deposit, currency, spread_label, leverage_options, enabled, sort_order"


def package_json(row) -> dict:
    (package_id, name, min_deposit, currency, spread_label, leverage_options, enabled,
     sort_order) = row[:8]
    return {"id": package_id, "name": name, "min_deposit": money(min_deposit),
            "currency": currency, "spread_label": spread_label,
            "leverage_options": list(leverage_options), "enabled": bool(enabled),
            "sort_order": sort_order}


REQUEST_COLS = ("id, user_id, package_id, package_name, leverage, status, mt5_login, mt5_server, "
                "account_id, decided_by, decided_at, decision_note, created_at")


def request_json(row) -> dict:
    """An account request without its passwords (they are never selected)."""
    (req_id, user_id, package_id, package_name, leverage, status, mt5_login, mt5_server,
     account_id, decided_by, decided_at, decision_note, created_at) = row[:13]
    out = {"id": req_id, "user_id": user_id, "package_id": package_id,
           "package_name": package_name, "leverage": leverage, "status": status,
           "mt5_login": int(mt5_login) if mt5_login is not None else None,
           "mt5_server": mt5_server,
           "account_id": int(account_id) if account_id is not None else None,
           "decided_by": decided_by, "decided_at": _iso(decided_at),
           "decision_note": decision_note, "created_at": _iso(created_at)}
    if len(row) > 13:
        out["email"], out["display_name"] = row[13], row[14]
    return out
