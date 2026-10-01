# api/tests/test_portal_packages.py
"""Account packages: the admin defines what an investor may request
(name, minimum deposit, spread label, leverage choices); investors list
the enabled ones in display order."""
import psycopg
import pytest

from portal_helpers import add_package, csrf, member, open_account_request


def _post(client, org_id, **over):
    body = {"name": "Standard", "min_deposit": "100", "spread_label": "20-25",
            "leverage_options": [500, 100, 200], **over}
    return client.post(f"/api/orgs/{org_id}/account-packages", json=body, headers=csrf(client))


def _events(db, org_id):
    with psycopg.connect(db, autocommit=True) as conn:
        return [r[0] for r in conn.execute(
            "SELECT payload FROM events WHERE org_id = %s "
            "AND payload->>'action' = 'account_package_changed' ORDER BY id", (org_id,)).fetchall()]


def test_an_admin_creates_lists_edits_and_disables_a_package(org_client, db):
    client, org_id, _seed = org_client
    r = _post(client, org_id)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created == {"id": created["id"], "name": "Standard", "min_deposit": 100.0,
                       "currency": "USD", "spread_label": "20-25",
                       "leverage_options": [100, 200, 500], "enabled": True, "sort_order": 0}
    assert client.get(f"/api/orgs/{org_id}/account-packages").json() == [created]
    r = client.patch(f"/api/orgs/{org_id}/account-packages/{created['id']}",
                     json={"name": "Pro", "spread_label": "", "enabled": False},
                     headers=csrf(client))
    assert r.status_code == 200
    assert r.json()["name"] == "Pro" and r.json()["spread_label"] is None
    assert r.json()["enabled"] is False
    changes = [(p["change"], p["name"]) for p in _events(db, org_id)]
    assert changes == [("created", "Standard"), ("updated", "Pro")]


@pytest.mark.parametrize("over,detail", [
    ({"name": " "}, "name is required"),
    ({"name": "x" * 65}, "name must be at most 64 characters"),
    ({"min_deposit": "-5"}, "min_deposit must be greater than 0"),
    ({"leverage_options": []}, "leverage_options must be a list of whole numbers from 1 to 3000"),
    ({"leverage_options": ["100"]}, "leverage_options must be a list of whole numbers from 1 to 3000"),
    ({"spread_label": "x" * 33}, "spread_label must be at most 32 characters"),
])
def test_a_bad_package_is_refused(org_client, over, detail):
    client, org_id, _seed = org_client
    r = _post(client, org_id, **over)
    assert r.status_code == 400 and r.json()["detail"] == detail


def test_a_zero_minimum_is_allowed(org_client):
    client, org_id, _seed = org_client
    assert _post(client, org_id, min_deposit="0").json()["min_deposit"] == 0.0


def test_unknown_packages_are_404(org_client):
    client, org_id, _seed = org_client
    r = client.patch(f"/api/orgs/{org_id}/account-packages/999", json={"name": "x"},
                     headers=csrf(client))
    assert r.status_code == 404 and r.json()["detail"] == "Package not found"
    r = client.delete(f"/api/orgs/{org_id}/account-packages/999", headers=csrf(client))
    assert r.status_code == 404


def test_delete_waits_for_open_requests(org_client, make_user, db):
    client, org_id, _seed = org_client
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    package_id = add_package(db, org_id)
    req_id = open_account_request(db, org_id, investor["id"], package_id)
    r = client.delete(f"/api/orgs/{org_id}/account-packages/{package_id}", headers=csrf(client))
    assert r.status_code == 409 and r.json()["detail"] == "an open request still uses this package"
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("UPDATE account_requests SET status = 'cancelled', main_password_enc = NULL, "
                     "investor_password_enc = NULL WHERE id = %s", (req_id,))
    r = client.delete(f"/api/orgs/{org_id}/account-packages/{package_id}", headers=csrf(client))
    assert r.status_code == 204
    assert _events(db, org_id)[-1]["change"] == "deleted"


def test_investors_see_only_enabled_packages_in_order(org_client, make_user, login_as, db):
    client, org_id, _seed = org_client
    add_package(db, org_id, name="Later", sort_order=2)
    add_package(db, org_id, name="Hidden", enabled=False)
    add_package(db, org_id, name="First", sort_order=1)
    investor = make_user(email="inv@example.com")
    member(db, org_id, investor["id"], "investor")
    assert client.get(f"/api/orgs/{org_id}/investor/account-packages").status_code == 403
    client.cookies.clear()
    login_as(client, investor)
    names = [p["name"] for p in client.get(f"/api/orgs/{org_id}/investor/account-packages").json()]
    assert names == ["First", "Later"]
    assert client.get(f"/api/orgs/{org_id}/account-packages").status_code == 403
    assert _post(client, org_id).status_code == 403
