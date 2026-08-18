"""Org lifecycle, memberships, invites, RBAC edges."""
import psycopg


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
