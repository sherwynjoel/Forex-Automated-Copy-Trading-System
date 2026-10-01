# api/tests/test_portal_identity_rules.py
"""The identity rules with no database: field cleaning, completeness,
re-verification, the MT5 password policy, leverage lists and sealing."""
from datetime import date, timedelta

import pytest
from cryptography.fernet import Fernet, InvalidToken

from api import portal_identity as pid
from api.portal_ledger import LedgerError


def test_the_field_lists_line_up_with_the_table():
    assert pid.PROFILE_FIELDS[0] == "full_name" and pid.PROFILE_FIELDS[-1] == "photo_file_id"
    assert len(pid.PROFILE_FIELDS) == 18
    assert set(pid.FILE_SLOTS) == {"id_front_file_id", "id_back_file_id",
                                   "address_proof_file_id", "photo_file_id"}
    assert pid.REQUIRED_FIELDS == tuple(f for f in pid.PROFILE_FIELDS
                                        if f not in ("area", "landmark", "state"))
    assert pid.PROFILE_COLS.startswith("user_id, full_name, ")
    assert pid.PROFILE_COLS.endswith("photo_file_id, status, submitted_at, decided_by, "
                                     "decided_at, decision_note, updated_at")


@pytest.mark.parametrize("field,raw,expected", [
    ("full_name", "  Ada Lovelace ", "Ada Lovelace"),
    ("full_name", "", None),
    ("area", None, None),
    ("gender", "Female", "female"),
    ("id_type", "DRIVING_LICENCE", "driving_licence"),
    ("country_residence", " in ", "IN"),
    ("date_of_birth", "1990-04-02", date(1990, 4, 2)),
    ("photo_file_id", 12, 12),
    ("photo_file_id", None, None),
])
def test_clean_profile_field_normalises(field, raw, expected):
    assert pid.clean_profile_field(field, raw) == expected


@pytest.mark.parametrize("field,raw,message", [
    ("gender", "x", "gender must be one of male, female, other"),
    ("id_type", "visa", "id_type must be one of passport, national_id, driving_licence"),
    ("country_citizenship", "IND", "country_citizenship must be a two-letter country code"),
    ("date_of_birth", "02/04/1990", "date_of_birth must be a date (YYYY-MM-DD)"),
    ("date_of_birth", (date.today() + timedelta(days=1)).isoformat(),
     "date_of_birth must be in the past"),
    ("id_front_file_id", "12", "id_front_file_id must be a file id"),
    ("id_front_file_id", True, "id_front_file_id must be a file id"),
    ("postal_code", "1" * 17, "postal_code must be at most 16 characters"),
])
def test_clean_profile_field_refuses(field, raw, message):
    with pytest.raises(LedgerError) as exc:
        pid.clean_profile_field(field, raw)
    assert str(exc.value) == message


def test_missing_fields_names_the_required_gaps_in_order():
    profile = pid.empty_profile(5)
    assert profile["missing"] == list(pid.REQUIRED_FIELDS)
    profile.update(full_name="Ada", phone="1", state=None)
    assert "full_name" not in pid.missing_fields(profile)
    assert "state" not in pid.missing_fields(profile)
    assert pid.missing_fields(profile)[0] == "gender"


def test_only_identity_changes_need_reverification():
    before = {**pid.empty_profile(5), "phone": "1", "full_name": "Ada",
              "date_of_birth": "1990-04-02"}
    assert not pid.needs_reverification(before, {"phone": "2", "city": "Chennai"})
    assert not pid.needs_reverification(before, {"full_name": "Ada"})
    assert not pid.needs_reverification(before, {"date_of_birth": date(1990, 4, 2)})
    assert pid.needs_reverification(before, {"full_name": "Ada L"})
    assert pid.needs_reverification(before, {"photo_file_id": 9})
    assert pid.needs_reverification(before, {"country_residence": "GB"})


@pytest.mark.parametrize("raw", ["Abcdefg1", "Z9" + "x" * 30, "Pa$$w0rd!"])
def test_mt5_passwords_that_pass(raw):
    assert pid.check_mt5_password(raw, "main_password") == raw


@pytest.mark.parametrize("raw", ["Abcdef1", "A1" + "x" * 31, "abcdefg1", "ABCDEFG1",
                                 "Abcdefgh", "Abc defg1", None, 12345678])
def test_mt5_passwords_that_fail(raw):
    with pytest.raises(LedgerError) as exc:
        pid.check_mt5_password(raw, "investor_password")
    assert str(exc.value) == ("investor_password must be 8-32 characters without spaces, "
                              "with an upper-case letter, a lower-case letter and a digit")


def test_leverage_options_are_sorted_and_deduplicated():
    assert pid.parse_leverage_options([500, 100, 100, 200]) == [100, 200, 500]
    for bad in ([], None, "100", [0], [3001], [True], [1.5], list(range(1, 14))):
        with pytest.raises(LedgerError) as exc:
            pid.parse_leverage_options(bad)
        assert str(exc.value) == "leverage_options must be a list of whole numbers from 1 to 3000"


def test_seal_round_trips_and_a_foreign_key_cannot_read_it():
    key = Fernet.generate_key().decode()
    token = pid.seal(key, "Main1234")
    assert token != "Main1234" and pid.unseal(key, token) == "Main1234"
    with pytest.raises(InvalidToken):
        pid.unseal(Fernet.generate_key().decode(), token)


def test_profile_json_reads_a_row_and_lists_what_is_missing():
    n = len(pid.PROFILE_FIELDS)
    values = [None] * n
    values[pid.PROFILE_FIELDS.index("full_name")] = "Ada"
    values[pid.PROFILE_FIELDS.index("date_of_birth")] = date(1990, 4, 2)
    row = (5, *values, "draft", None, None, None, None, None, "ada@example.com", "Ada")
    out = pid.profile_json(row)
    assert out["user_id"] == 5 and out["full_name"] == "Ada"
    assert out["date_of_birth"] == "1990-04-02" and out["status"] == "draft"
    assert out["email"] == "ada@example.com" and out["display_name"] == "Ada"
    assert "full_name" not in out["missing"] and "gender" in out["missing"]


def test_package_and_request_json_shapes():
    from decimal import Decimal
    assert pid.package_json((1, "Standard", Decimal("100"), "USD", "20-25", [100, 200], True, 0)) == {
        "id": 1, "name": "Standard", "min_deposit": 100.0, "currency": "USD",
        "spread_label": "20-25", "leverage_options": [100, 200], "enabled": True, "sort_order": 0}
    row = (7, 5, 1, "Standard", 200, "requested", None, None, None, None, None, None, None)
    out = pid.request_json(row)
    assert out == {"id": 7, "user_id": 5, "package_id": 1, "package_name": "Standard",
                   "leverage": 200, "status": "requested", "mt5_login": None, "mt5_server": None,
                   "account_id": None, "decided_by": None, "decided_at": None,
                   "decision_note": None, "created_at": None}
    assert "main_password" not in str(out)
