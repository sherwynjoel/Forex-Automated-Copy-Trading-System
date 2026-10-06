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


# ------------------------------------------------------------ triggers


def _invite(client, org_id, role="investor"):
    r = client.post(f"/api/orgs/{org_id}/invites", json={"role": role}, headers=csrf(client))
    return r.json()["token"]


def _join(client, login_as, user, token):
    client.cookies.clear()
    login_as(client, user)
    return client.post("/api/orgs/join", json={"token": token}, headers=csrf(client))


def test_the_signup_bonus_pays_once_on_join_and_on_a_role_change(org_client, make_user,
                                                                 login_as, db):
    client, org_id, _seed = org_client
    assert _rules(client, org_id, signup_enabled=True, signup_amount="50").status_code == 200
    token = _invite(client, org_id)
    joiner = make_user(email="joiner@example.com")
    assert _join(client, login_as, joiner, token).status_code == 200
    assert _credit_entries(db, joiner["id"]) == [("signup", 50.0)]
    client.cookies.clear()
    login_as(client, ADMIN)
    url = f"/api/orgs/{org_id}/members/{joiner['id']}"
    assert client.patch(url, json={"role": "viewer"}, headers=csrf(client)).status_code == 200
    assert client.patch(url, json={"role": "investor"}, headers=csrf(client)).status_code == 200
    assert _credit_entries(db, joiner["id"]) == [("signup", 50.0)]      # once per investor
    promoted = make_user(email="viewer@example.com")
    member(db, org_id, promoted["id"], "viewer")
    r = client.patch(f"/api/orgs/{org_id}/members/{promoted['id']}", json={"role": "investor"},
                     headers=csrf(client))
    assert r.status_code == 200
    assert _credit_entries(db, promoted["id"]) == [("signup", 50.0)]
    with psycopg.connect(db, autocommit=True) as conn:
        notes = conn.execute("SELECT topic, title, link FROM notifications WHERE user_id = %s",
                             (joiner["id"],)).fetchall()
    assert notes == [("bonus", "You received a 50.00 USD welcome bonus",
                      f"/org/{org_id}/invest/bonus")]
    severity, payload, actor = _events(db, org_id, "investor_bonus_paid")[0]
    assert (severity, actor, payload["source"], payload["amount"]) == (
        "info", "joiner@example.com", "signup", 50.0)


def test_no_bonus_while_off_none_back_paid_and_none_for_desk_roles(org_client, make_user,
                                                                   login_as, db):
    client, org_id, _seed = org_client
    token = _invite(client, org_id)
    early = make_user(email="early@example.com")
    assert _join(client, login_as, early, token).status_code == 200
    client.cookies.clear()
    login_as(client, ADMIN)
    _rules(client, org_id, signup_enabled=True, signup_amount="50")
    viewer_token = _invite(client, org_id, role="viewer")
    viewer = make_user(email="viewer@example.com")
    assert _join(client, login_as, viewer, viewer_token).status_code == 200
    assert _credit_entries(db, early["id"]) == [] and _credit_entries(db, viewer["id"]) == []


def test_the_kyc_bonus_pays_once_on_approval(org_client, make_user, db):
    client, org_id, _seed = org_client
    _rules(client, org_id, kyc_enabled=True, kyc_amount="25")
    investor = _investor(make_user, db, org_id)
    rejected = _investor(make_user, db, org_id, email="rej@example.com")
    kyc_profile(db, org_id, investor["id"], status="submitted")
    kyc_profile(db, org_id, rejected["id"], status="submitted")

    def decide(user_id, status, note=None):
        return client.post(f"/api/orgs/{org_id}/kyc/{user_id}/decision",
                           json={"status": status, "note": note}, headers=csrf(client))

    assert decide(investor["id"], "approved").status_code == 200
    assert _credit_entries(db, investor["id"]) == [("kyc", 25.0)]
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE kyc_profiles SET status = 'submitted' WHERE user_id = %s",
                     (investor["id"],))
    assert decide(investor["id"], "approved").status_code == 200
    assert _credit_entries(db, investor["id"]) == [("kyc", 25.0)]       # a re-approval pays nothing
    assert decide(rejected["id"], "rejected", "blurry").status_code == 200
    assert _credit_entries(db, rejected["id"]) == []


def test_a_failing_rule_bonus_never_fails_the_join_or_the_approval(org_client, make_user,
                                                                   login_as, db, monkeypatch):
    """award_rule_bonus runs after the primary change committed: a failure is
    logged and the request still answers as if no rule were on."""
    from api import portal_common

    def boom(*_args, **_kwargs):
        raise RuntimeError("ledger down")

    client, org_id, _seed = org_client
    _rules(client, org_id, signup_enabled=True, signup_amount="50", kyc_enabled=True,
           kyc_amount="25")
    token = _invite(client, org_id)
    monkeypatch.setattr(portal_common, "pay_bonus", boom)
    joiner = make_user(email="joiner@example.com")
    r = _join(client, login_as, joiner, token)
    assert r.status_code == 200 and r.json()["role"] == "investor"
    with psycopg.connect(db, autocommit=True) as conn:
        assert conn.execute("SELECT role FROM org_memberships WHERE org_id = %s AND user_id = %s",
                            (org_id, joiner["id"])).fetchone() == ("investor",)
    client.cookies.clear()
    login_as(client, ADMIN)
    kyc_profile(db, org_id, joiner["id"], status="submitted")
    r = client.post(f"/api/orgs/{org_id}/kyc/{joiner['id']}/decision",
                    json={"status": "approved", "note": None}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert _credit_entries(db, joiner["id"]) == []
    assert _events(db, org_id, "investor_bonus_paid") == []


def _pending_deposit(db, org_id, user_id, amount, reference):
    with psycopg.connect(db, autocommit=True) as conn:
        return conn.execute(
            "INSERT INTO deposits (org_id, user_id, method_kind, method_label, amount, reference) "
            "VALUES (%s, %s, 'crypto', 'USDT on TRC20', %s, %s) RETURNING id",
            (org_id, user_id, Decimal(amount), reference)).fetchone()[0]


def test_the_deposit_bonus_rounds_caps_and_pays_once(org_client, make_user, db):
    client, org_id, _seed = org_client
    uid = _investor(make_user, db, org_id)["id"]
    _rules(client, org_id, deposit_enabled=True, deposit_pct="12.5", deposit_cap="100")
    small = _pending_deposit(db, org_id, uid, "33.33", "d-1")
    mid = _pending_deposit(db, org_id, uid, "500", "d-mid")
    big = _pending_deposit(db, org_id, uid, "5000", "d-2")
    tiny = _pending_deposit(db, org_id, uid, "0.03", "d-3")

    def confirm(dep_id, **extra):
        return client.post(f"/api/orgs/{org_id}/deposits/{dep_id}/decision",
                           json={"status": "confirmed", **extra}, headers=csrf(client))

    assert confirm(small).status_code == 200
    assert confirm(small).status_code == 409          # a double confirm pays nothing more
    # Below the cap, so the base shows: 12.5 % of the credited 400 is 50.00,
    # where the 500 notice amount would have paid 62.50.
    assert confirm(mid, credited_amount="400").status_code == 200
    assert confirm(big, credited_amount="4000").status_code == 200   # capped at 100
    assert confirm(tiny).status_code == 200            # 0.00375 rounds to 0: nothing paid
    assert _credit_entries(db, uid) == [("deposit", 4.17), ("deposit", 50.0),
                                        ("deposit", 100.0)]
    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute("SELECT source_id, amount, note FROM bonuses WHERE user_id = %s "
                            "ORDER BY id", (uid,)).fetchall()
    assert rows == [(small, Decimal("4.17"), f"deposit #{small}"),
                    (mid, Decimal("50.00"), f"deposit #{mid}"),
                    (big, Decimal("100.00"), f"deposit #{big}")]


# ------------------------------------------------------------ manual + history


def test_manual_grant_and_the_claw_back_floor(org_client, make_user, db):
    client, org_id, _seed = org_client
    uid = _investor(make_user, db, org_id)["id"]
    url = f"/api/orgs/{org_id}/investors/{uid}/bonuses"

    def grant(amount, note="Promo", mpin="123456"):
        return client.post(url, json={"amount": amount, "note": note, "mpin": mpin},
                           headers=csrf(client))

    assert grant("20", mpin="000000").status_code == 401
    r = grant("20")
    assert r.status_code == 201, r.text
    assert {k: r.json()[k] for k in ("source", "source_id", "amount", "note", "currency")} == {
        "source": "manual", "source_id": None, "amount": 20.0, "note": "Promo", "currency": "USD"}
    r = grant("-25")
    assert r.status_code == 400
    assert r.json()["detail"] == "a claw-back cannot take the Credit wallet below zero (available 20.00)"
    assert grant("-20", note="Reversed").status_code == 201
    assert _credit_entries(db, uid) == [("manual", 20.0), ("manual", -20.0)]
    for amount, note, detail in (
            ("0", "x", "amount must not be zero"),
            ("--5", "x", "amount must be a signed number with at most two decimals, e.g. -25.00"),
            ("5", " ", "note is required")):
        r = grant(amount, note=note)
        assert (r.status_code, r.json()["detail"]) == (400, detail)
    r = client.post(f"/api/orgs/{org_id}/investors/999/bonuses",
                    json={"amount": "5", "note": "x", "mpin": "123456"}, headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Investor not found"
    with psycopg.connect(db, autocommit=True) as conn:
        (admin_id,) = conn.execute("SELECT id FROM users WHERE email = %s",
                                   (ADMIN["email"],)).fetchone()
    # Granting to the org's own admin (a member, but not an investor) is the
    # same 404 as an unknown id -- _require_investor's role check, not just
    # existence.
    r = client.post(f"/api/orgs/{org_id}/investors/{admin_id}/bonuses",
                    json={"amount": "5", "note": "x", "mpin": "123456"}, headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Investor not found"
    assert [e[0] for e in _events(db, org_id, "investor_bonus_paid")] == ["warning", "warning"]
    with psycopg.connect(db, autocommit=True) as conn:
        titles = [r[0] for r in conn.execute(
            "SELECT title FROM notifications WHERE user_id = %s ORDER BY id", (uid,)).fetchall()]
    assert titles == ["You received a 20.00 USD bonus", "A bonus of 20.00 USD was taken back"]


def test_the_adjustment_route_keeps_its_amount_rules(org_client, make_user, db):
    client, org_id, _seed = org_client
    uid = _investor(make_user, db, org_id)["id"]
    url = f"/api/orgs/{org_id}/investors/{uid}/adjustments"
    for amount, detail in (("0", "amount must not be zero"), (None, "amount is required, e.g. 250.00"),
                           ("+-5", "amount must be a signed number with at most two decimals, "
                                   "e.g. -25.00")):
        r = client.post(url, json={"wallet": "main", "amount": amount, "note": "x",
                                   "mpin": "123456"}, headers=csrf(client))
        assert (r.status_code, r.json()["detail"]) == (400, detail)
    r = client.post(url, json={"wallet": "main", "amount": "-12.5", "note": "x", "mpin": "123456"},
                    headers=csrf(client))
    assert r.status_code == 201 and r.json()["amount"] == -12.5


def test_the_investor_reads_their_own_bonus_history(org_client, make_user, login_as, db):
    client, org_id, _seed = org_client
    investor = _investor(make_user, db, org_id)
    other = _investor(make_user, db, org_id, email="other@example.com")
    with psycopg.connect(db, autocommit=True) as conn:
        for user_id, source, source_id, amount, when in (
                (investor["id"], "signup", None, 50, "2026-09-01T12:00:00Z"),
                (investor["id"], "deposit", 7, 10, "2026-09-15T12:00:00Z"),
                (investor["id"], "manual", None, -5, "2026-09-20T12:00:00Z"),
                (other["id"], "signup", None, 50, "2026-09-01T12:00:00Z")):
            conn.execute(
                "INSERT INTO bonuses (org_id, user_id, source, source_id, amount, note, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (org_id, user_id, source, source_id, amount, f"{source} note", when))
    client.cookies.clear()
    login_as(client, investor)
    url = f"/api/orgs/{org_id}/investor/bonuses"
    rows = client.get(url).json()
    assert [(b["source"], b["amount"]) for b in rows] == [
        ("manual", -5.0), ("deposit", 10.0), ("signup", 50.0)]
    assert rows[1] == {"id": rows[1]["id"], "source": "deposit", "source_id": 7, "amount": 10.0,
                       "note": "deposit note", "created_at": rows[1]["created_at"],
                       "currency": "USD"}
    assert [b["source"] for b in client.get(url + "?source=signup").json()] == ["signup"]
    assert [b["source"] for b in client.get(url + "?from=2026-09-10&to=2026-09-15").json()] == [
        "deposit"]
    # A cleared filter sends `?source=`, not a missing param: blank reads as
    # no filter, same as omitting source entirely.
    assert [b["source"] for b in client.get(url + "?source=").json()] == [
        "manual", "deposit", "signup"]
    r = client.get(url + "?source=bogus")
    assert r.status_code == 400
    assert r.json()["detail"] == "source must be one of signup, kyc, deposit, manual"
    r = client.get(url + "?from=yesterday")
    assert r.status_code == 400 and r.json()["detail"] == "from must be a date (YYYY-MM-DD)"


def test_a_failing_notify_after_a_manual_grant_still_succeeds_once(org_client, make_user, db,
                                                                   monkeypatch):
    """Fix-wave item 2: notify() raising deep inside announce_bonus (the
    grant route's one call after the money commits) must not turn an
    already-committed grant into a 500, and must not pay twice."""
    from api import portal_common

    async def boom(*_args, **_kwargs):
        raise RuntimeError("email down")

    client, org_id, _seed = org_client
    uid = _investor(make_user, db, org_id)["id"]
    monkeypatch.setattr(portal_common, "notify", boom)
    r = client.post(f"/api/orgs/{org_id}/investors/{uid}/bonuses",
                    json={"amount": "20", "note": "Promo", "mpin": "123456"}, headers=csrf(client))
    assert r.status_code == 201, r.text
    assert _credit_entries(db, uid) == [("manual", 20.0)]
    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute("SELECT amount FROM bonuses WHERE org_id = %s AND user_id = %s",
                            (org_id, uid)).fetchall()
    assert rows == [(Decimal("20.00"),)]


def test_a_bonus_paid_by_hand_reaches_both_alerters():
    from api.alerts import ALERT_RULES
    from api.telegram import TELEGRAM_RULES
    assert ("control", "warning", "investor_bonus_paid") in ALERT_RULES
    assert ("control", "warning", "investor_bonus_paid") in TELEGRAM_RULES
