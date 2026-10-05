# api/tests/test_portal_settings.py
"""Appearance (per user, every org) and email switches (per user per org)."""
import psycopg

from portal_helpers import csrf, member

from api import portal_common as pc

ALL_ON = {"money": True, "identity": True, "support": True, "bonus": True}


def test_settings_round_trip_and_refusal(org_client):
    client, _org_id, _seed = org_client
    assert client.get("/api/me/settings").json() == {"theme": "system", "updated_at": None}
    r = client.put("/api/me/settings", json={"theme": "dim"}, headers=csrf(client))
    assert r.status_code == 200 and r.json()["theme"] == "dim" and r.json()["updated_at"]
    assert client.get("/api/me/settings").json() == r.json()
    for bad in ("neon", None, 3, ["dim"]):
        r = client.put("/api/me/settings", json={"theme": bad}, headers=csrf(client))
        assert r.status_code == 400
        assert r.json()["detail"] == "theme must be light, dim, dark or system"


def test_settings_need_a_full_session(app_client):
    assert app_client.get("/api/me/settings").status_code == 401


def test_prefs_default_on_round_trip_and_mute_the_email(org_client, make_user, login_as, db):
    client, org_id, _seed = org_client
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    login_as(client, investor)
    url = f"/api/orgs/{org_id}/notification-prefs"
    assert client.get(url).json() == ALL_ON
    muted = {**ALL_ON, "money": False, "support": False}
    r = client.put(url, json=muted, headers=csrf(client))
    assert r.status_code == 200 and r.json() == muted
    assert client.get(url).json() == muted
    for body, detail in (({**ALL_ON, "money": "no"}, "money must be true or false"),
                         ({"money": True}, "identity must be true or false")):
        r = client.put(url, json=body, headers=csrf(client))
        assert r.status_code == 400 and r.json()["detail"] == detail
    with psycopg.connect(db, autocommit=True) as conn:
        assert pc.email_wanted(conn, org_id, investor["id"], "money") is False
        assert pc.email_wanted(conn, org_id, investor["id"], "identity") is True


def test_prefs_are_per_org(org_client, make_user, make_org, login_as, db):
    client, org_id, _seed = org_client
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    other_org = make_org(name="Other", members=[(investor, "investor")])
    login_as(client, investor)
    client.put(f"/api/orgs/{org_id}/notification-prefs", json={**ALL_ON, "bonus": False},
               headers=csrf(client))
    assert client.get(f"/api/orgs/{other_org}/notification-prefs").json() == ALL_ON
