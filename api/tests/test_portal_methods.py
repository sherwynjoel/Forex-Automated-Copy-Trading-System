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


def test_the_audit_shows_the_method_before_and_after_a_change(org_client, db):
    """An address swap must be visible in the alert, not just the label."""
    client, org_id, seed = org_client
    method_id = _post_method(client, org_id).json()["id"]
    swapped = {**CRYPTO, "address": "TAttacker999"}
    r = client.patch(f"/api/orgs/{org_id}/payment-methods/{method_id}",
                     json={"details": swapped}, headers=csrf(client))
    assert r.status_code == 200
    created, updated = [p for _, p, _ in _events(db, org_id, "payment_method_changed")]
    assert "before" not in created
    assert created["after"] == {"label": "USDT on TRC20", "kind": "crypto", "enabled": True,
                                "details": CRYPTO}
    assert updated["before"]["details"]["address"] == "TAddr123"
    assert updated["after"] == {"label": "USDT on TRC20", "kind": "crypto", "enabled": True,
                                "details": swapped}
    client.delete(f"/api/orgs/{org_id}/payment-methods/{method_id}", headers=csrf(client))
    deleted = _events(db, org_id, "payment_method_changed")[-1][1]
    assert deleted["before"]["details"] == swapped and "after" not in deleted


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
    assert client.get(url).json() == {"withdrawal_min": 0.0, "withdrawal_fee_pct": 0.0,
                                      "max_live_accounts": 5}
    with psycopg.connect(db, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM portal_settings WHERE org_id = %s",
                            (org_id,)).fetchone() == (1,)
    r = client.put(url, json={"withdrawal_min": "50", "withdrawal_fee_pct": "2.5"},
                   headers=csrf(client))
    assert r.status_code == 200 and r.json() == {"withdrawal_min": 50.0, "withdrawal_fee_pct": 2.5,
                                                 "max_live_accounts": 5}
    assert client.get(url).json() == {"withdrawal_min": 50.0, "withdrawal_fee_pct": 2.5,
                                      "max_live_accounts": 5}
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
    assert payload["previous"] == {"withdrawal_min": 0.0, "withdrawal_fee_pct": 0.0,
                                   "max_live_accounts": 5}
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
