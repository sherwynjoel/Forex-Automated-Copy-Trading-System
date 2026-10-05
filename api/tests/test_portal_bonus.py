# api/tests/test_portal_bonus.py
"""Bonuses: the rules (admin), the three triggers that pay at most once,
manual grants and claw-backs, and the investor's history. Every bonus is a
bonuses row plus a credit ledger row (ref_table 'bonuses'). Tasks 8-10 of
the phase 4 plan grow this file."""
from decimal import Decimal

import psycopg
import pytest

from portal_helpers import csrf, kyc_profile, member

ADMIN = {"email": "admin@example.com", "password": "a-solid-password"}
RULES_OFF = {"signup_enabled": False, "signup_amount": 0.0, "kyc_enabled": False,
             "kyc_amount": 0.0, "deposit_enabled": False, "deposit_pct": 0.0, "deposit_cap": None}


def _events(db, org_id, action):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload, actor_email FROM events WHERE org_id = %s "
            "AND payload->>'action' = %s ORDER BY id", (org_id, action)).fetchall()


def _rules(client, org_id, **over):
    body = {"signup_enabled": False, "signup_amount": "0", "kyc_enabled": False,
            "kyc_amount": "0", "deposit_enabled": False, "deposit_pct": "0",
            "deposit_cap": None, **over}
    return client.put(f"/api/orgs/{org_id}/bonus-rules", json=body, headers=csrf(client))


def _credit_entries(db, user_id):
    """(bonus source, amount) of every credit ledger row a bonus wrote, oldest first."""
    with psycopg.connect(db, autocommit=True) as conn:
        return [(r[0], float(r[1])) for r in conn.execute(
            "SELECT b.source, w.amount FROM wallet_entries w JOIN bonuses b ON b.id = w.ref_id "
            "WHERE w.ref_table = 'bonuses' AND w.user_id = %s AND w.wallet = 'credit' "
            "ORDER BY w.id", (user_id,)).fetchall()]


def _investor(make_user, db, org_id, email="inv@example.com"):
    investor = make_user(email=email)
    member(db, org_id, investor["id"], "investor")
    return investor


def _strip(rules):
    return {k: v for k, v in rules.items() if k != "updated_at"}


# ------------------------------------------------------------ rules


def test_rules_default_off_and_round_trip(org_client, db):
    client, org_id, _seed = org_client
    url = f"/api/orgs/{org_id}/bonus-rules"
    assert _strip(client.get(url).json()) == RULES_OFF
    r = _rules(client, org_id, signup_enabled=True, signup_amount="50", deposit_enabled=True,
               deposit_pct="12.5", deposit_cap="100")
    assert r.status_code == 200, r.text
    assert _strip(r.json()) == {**RULES_OFF, "signup_enabled": True, "signup_amount": 50.0,
                                "deposit_enabled": True, "deposit_pct": 12.5, "deposit_cap": 100.0}
    assert r.json()["updated_at"]
    assert client.get(url).json() == r.json()
    severity, payload, actor = _events(db, org_id, "bonus_rules_changed")[-1]
    assert (severity, actor) == ("info", "admin@example.com")
    assert payload["previous"]["signup_enabled"] is False and payload["signup_amount"] == 50.0


@pytest.mark.parametrize("over, detail", [
    ({"signup_enabled": "yes"}, "signup_enabled must be true or false"),
    ({"kyc_amount": "-1"}, "kyc_amount must be greater than 0"),
    ({"kyc_amount": "1.234"}, "kyc_amount may have at most two decimals"),
    ({"deposit_pct": "100.5"}, "deposit_pct must be between 0 and 100"),
    ({"deposit_pct": "1.2345"}, "deposit_pct may have at most three decimals"),
    ({"deposit_cap": "0"}, "deposit_cap must be greater than 0"),
    ({"signup_enabled": True}, "signup_amount must be above 0 while the signup rule is on"),
    ({"deposit_enabled": True}, "deposit_pct must be above 0 while the deposit rule is on"),
])
def test_bad_rules_are_refused(org_client, over, detail):
    client, org_id, _seed = org_client
    r = _rules(client, org_id, **over)
    assert r.status_code == 400 and r.json()["detail"] == detail
