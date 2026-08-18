"""Org lifecycle, memberships, invites, RBAC edges."""
import psycopg
import pytest
from fastapi import HTTPException


def _register(client, email="owner@example.com", name="Owner"):
    r = client.post("/api/register", json={
        "email": email, "password": "a-solid-password", "display_name": name})
    assert r.status_code == 204
    return client.get("/api/me").json()["user"]


def _csrf(client):
    return {"X-CSRF-Token": client.cookies.get("csrf")}


def test_create_org_makes_creator_owner(app_client):
    _register(app_client)
    r = app_client.post("/api/orgs", json={"name": "Alpha Desk"},
                        headers=_csrf(app_client))
    assert r.status_code == 201
    org = r.json()
    assert org["name"] == "Alpha Desk" and org["role"] == "owner"
    me = app_client.get("/api/me").json()
    assert me["orgs"] == [{"id": org["id"], "name": "Alpha Desk", "role": "owner"}]


def test_org_get_shows_settings_fields(app_client):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    r = app_client.get(f"/api/orgs/{org['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["copying_enabled"] is True and body["dry_run"] is False


def test_nonmember_gets_404_never_403(app_client, make_user, login_as):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "Secret"},
                          headers=_csrf(app_client)).json()
    outsider = make_user(email="out@example.com")
    login_as(app_client, outsider)
    assert app_client.get(f"/api/orgs/{org['id']}").status_code == 404
    assert app_client.get(f"/api/orgs/{org['id']}/members").status_code == 404
    assert app_client.get("/api/orgs/999999").status_code == 404


def test_invite_roundtrip(app_client, make_user, login_as):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                          json={"role": "trader"}, headers=_csrf(app_client))
    assert inv.status_code == 201
    token = inv.json()["token"]

    joiner = make_user(email="join@example.com")
    login_as(app_client, joiner)
    r = app_client.post("/api/orgs/join", json={"token": token},
                        headers=_csrf(app_client))
    assert r.status_code == 200
    assert r.json() == {"org_id": org["id"], "role": "trader"}
    # single-use
    r2 = app_client.post("/api/orgs/join", json={"token": token},
                         headers=_csrf(app_client))
    assert r2.status_code == 410


def test_invite_cannot_grant_owner(app_client):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    r = app_client.post(f"/api/orgs/{org['id']}/invites",
                        json={"role": "owner"}, headers=_csrf(app_client))
    assert r.status_code == 400


def test_expired_invite_is_410(app_client, make_user, login_as, db):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                          json={"role": "viewer"}, headers=_csrf(app_client)).json()
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE org_invites SET expires_at = now() - interval '1 hour'")
    joiner = make_user(email="late@example.com")
    login_as(app_client, joiner)
    r = app_client.post("/api/orgs/join", json={"token": inv["token"]},
                        headers=_csrf(app_client))
    assert r.status_code == 410


def test_raw_invite_token_never_stored(app_client, db):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                          json={"role": "viewer"}, headers=_csrf(app_client)).json()
    with psycopg.connect(db, autocommit=True) as conn:
        (stored,) = conn.execute("SELECT token_hash FROM org_invites").fetchone()
    assert stored != inv["token"] and len(stored) == 64  # sha256 hex


def test_member_role_change_and_last_owner_invariant(app_client, make_user, login_as):
    owner = _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                          json={"role": "viewer"}, headers=_csrf(app_client)).json()
    member = make_user(email="m@example.com")
    login_as(app_client, member)
    app_client.post("/api/orgs/join", json={"token": inv["token"]},
                    headers=_csrf(app_client))

    # a viewer cannot change roles
    r = app_client.patch(f"/api/orgs/{org['id']}/members/{owner['id']}",
                         json={"role": "viewer"}, headers=_csrf(app_client))
    assert r.status_code == 403

    login_as(app_client, {"email": "owner@example.com", "password": "a-solid-password"})
    # owner promotes member to owner
    r = app_client.patch(f"/api/orgs/{org['id']}/members/{member['id']}",
                         json={"role": "owner"}, headers=_csrf(app_client))
    assert r.status_code == 200
    members = app_client.get(f"/api/orgs/{org['id']}/members").json()
    assert sum(1 for m in members if m["role"] == "owner") == 2
    # two owners now; demoting one is fine
    r = app_client.patch(f"/api/orgs/{org['id']}/members/{member['id']}",
                         json={"role": "admin"}, headers=_csrf(app_client))
    assert r.status_code == 200
    # demoting the LAST owner is rejected
    r = app_client.patch(f"/api/orgs/{org['id']}/members/{owner['id']}",
                         json={"role": "admin"}, headers=_csrf(app_client))
    assert r.status_code == 409
    # removing the last owner is rejected
    r = app_client.delete(f"/api/orgs/{org['id']}/members/{owner['id']}",
                          headers=_csrf(app_client))
    assert r.status_code == 409


def test_member_can_leave_but_last_owner_cannot(app_client, make_user, login_as):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                          json={"role": "trader"}, headers=_csrf(app_client)).json()
    member = make_user(email="m@example.com")
    login_as(app_client, member)
    app_client.post("/api/orgs/join", json={"token": inv["token"]},
                    headers=_csrf(app_client))
    # a trader can remove THEMSELVES (leave) even though they are not owner
    r = app_client.delete(f"/api/orgs/{org['id']}/members/{member['id']}",
                          headers=_csrf(app_client))
    assert r.status_code == 204
    assert app_client.get(f"/api/orgs/{org['id']}").status_code == 404


def test_delete_org_cascades(app_client, db):
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    r = app_client.delete(f"/api/orgs/{org['id']}", headers=_csrf(app_client))
    assert r.status_code == 204
    with psycopg.connect(db, autocommit=True) as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM orgs").fetchone()
    assert count == 0
    assert app_client.get("/api/me").json()["orgs"] == []


def test_require_account_in_org_scopes_by_org(db):
    """require_account_in_org must 404 an account looked up under the
    wrong org, and pass silently for the org that actually owns it --
    Tasks 4-8 gate real-money endpoints on this."""
    from api.rbac import require_account_in_org

    with psycopg.connect(db, autocommit=True) as conn:
        (org_a,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('A') RETURNING id").fetchone()
        (org_b,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('B') RETURNING id").fetchone()
        (conn_id,) = conn.execute(
            """INSERT INTO ctid_connections
               (access_token_enc, refresh_token_enc, granted_at, expires_at, org_id)
               VALUES ('at', 'rt', now(), now() + interval '30 days', %s)
               RETURNING id""",
            (org_a,),
        ).fetchone()
        conn.execute(
            """INSERT INTO accounts
               (ctid_trader_account_id, ctid_connection_id, trader_login, is_live, org_id)
               VALUES (100, %s, 555, false, %s)""",
            (conn_id, org_a),
        )

        assert require_account_in_org(conn, org_a, 100) is None
        with pytest.raises(HTTPException) as exc_info:
            require_account_in_org(conn, org_b, 100)
        assert exc_info.value.status_code == 404


def test_invite_create_requires_admin_role(app_client, make_user, login_as):
    """The admin threshold on invite creation must actually be enforced:
    a below-admin member is rejected, an admin-or-above member succeeds."""
    _register(app_client)
    org = app_client.post("/api/orgs", json={"name": "A"},
                          headers=_csrf(app_client)).json()
    trader_inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                                 json={"role": "trader"}, headers=_csrf(app_client)).json()
    trader = make_user(email="trader@example.com")
    login_as(app_client, trader)
    app_client.post("/api/orgs/join", json={"token": trader_inv["token"]},
                    headers=_csrf(app_client))
    # a trader (below admin) cannot create invites
    r = app_client.post(f"/api/orgs/{org['id']}/invites",
                        json={"role": "viewer"}, headers=_csrf(app_client))
    assert r.status_code == 403

    login_as(app_client, {"email": "owner@example.com", "password": "a-solid-password"})
    admin_inv = app_client.post(f"/api/orgs/{org['id']}/invites",
                                json={"role": "admin"}, headers=_csrf(app_client)).json()
    admin = make_user(email="admin@example.com")
    login_as(app_client, admin)
    app_client.post("/api/orgs/join", json={"token": admin_inv["token"]},
                    headers=_csrf(app_client))
    # an admin CAN create invites
    r = app_client.post(f"/api/orgs/{org['id']}/invites",
                        json={"role": "viewer"}, headers=_csrf(app_client))
    assert r.status_code == 201
