# api/tests/test_portal_notify_callers.py
"""Every decision that used to email the investor (the nine notify_investor
callers of phases 1-3) now also leaves one in-app notification with its
topic and an in-app link -- driven through the routes in one walk."""
import psycopg
from psycopg.types.json import Jsonb

from portal_helpers import (DEST_CRYPTO, add_method, add_package, approved_destination, credit,
                            csrf, kyc_profile, member, open_account_request)


def test_the_nine_decisions_each_write_one_notification(org_client, make_user, db):
    client, org_id, seed = org_client
    investor = make_user(email="inv@example.com")
    uid = investor["id"]
    member(db, org_id, uid, "investor")
    seed(1001, role="slave")
    method_id = add_method(db, org_id)
    dest_id = approved_destination(db, org_id, uid)
    credit(db, org_id, uid, "500")
    kyc_profile(db, org_id, uid, status="submitted")
    package_id = add_package(db, org_id)
    with psycopg.connect(db, autocommit=True) as conn:
        (dep_id,) = conn.execute(
            "INSERT INTO deposits (org_id, user_id, method_id, method_kind, method_label, amount, "
            "reference) VALUES (%s, %s, %s, 'crypto', 'USDT on TRC20', 100, 'n-1') RETURNING id",
            (org_id, uid, method_id)).fetchone()
        (wd_id,) = conn.execute(
            "INSERT INTO withdrawals (org_id, user_id, destination_id, destination_kind, "
            "destination_summary, amount, fee, net_amount) "
            "VALUES (%s, %s, %s, 'crypto', 'TRC20 T…21', 50, 0, 50) RETURNING id",
            (org_id, uid, dest_id)).fetchone()
        (pending_dest,) = conn.execute(
            "INSERT INTO payout_destinations (org_id, user_id, kind, nickname, details) "
            "VALUES (%s, %s, 'crypto', 'Second', %s) RETURNING id",
            (org_id, uid, Jsonb(DEST_CRYPTO))).fetchone()
        (tr_id,) = conn.execute(
            "INSERT INTO transfers (org_id, user_id, source_kind, source_wallet, target_kind, "
            "target_account_id, amount) VALUES (%s, %s, 'wallet', 'main', 'account', 1001, 10) "
            "RETURNING id", (org_id, uid)).fetchone()

    def post(tail, body):
        return client.post(f"/api/orgs/{org_id}/{tail}", json=body, headers=csrf(client))

    assert post(f"deposits/{dep_id}/decision", {"status": "confirmed"}).status_code == 200
    assert post(f"withdrawals/{wd_id}/decision", {"status": "approved"}).status_code == 200
    assert post(f"withdrawals/{wd_id}/paid", {"txid": "tx-1"}).status_code == 200
    assert post(f"payout-destinations/{pending_dest}/decision",
                {"status": "approved"}).status_code == 200
    assert post(f"transfers/{tr_id}/decision", {"status": "rejected", "note": "no"}).status_code == 200
    assert post(f"investors/{uid}/adjustments", {"wallet": "main", "amount": "5", "note": "fix",
                                                 "mpin": "123456"}).status_code == 201
    assert post(f"kyc/{uid}/decision", {"status": "approved"}).status_code == 200
    first = open_account_request(db, org_id, uid, package_id)
    assert post(f"account-requests/{first}/reject", {"note": "full"}).status_code == 200
    second = open_account_request(db, org_id, uid, package_id)
    assert post(f"account-requests/{second}/fulfil",
                {"mt5_login": 5001, "mt5_server": "Broker-Live"}).status_code == 200

    with psycopg.connect(db, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT org_id, topic, title, link FROM notifications WHERE user_id = %s ORDER BY id",
            (uid,)).fetchall()
    p = f"/org/{org_id}/invest"
    assert rows == [
        (org_id, "money", "Your deposit of 100.00 USD was confirmed", f"{p}/deposit"),
        (org_id, "money", "Your withdrawal of 50.00 USD was approved", f"{p}/withdraw"),
        (org_id, "money", "Your withdrawal of 50.00 USD was paid", f"{p}/withdraw"),
        (org_id, "money", "Your payout account TRC20 T…21 was approved", f"{p}/payout-accounts"),
        (org_id, "money", "Your transfer of 10.00 USD was rejected", f"{p}/transfer"),
        (org_id, "money", "Your My wallet was adjusted by +5.00 USD", f"{p}/transactions"),
        (org_id, "identity", "Your identity verification was approved", f"{p}/profile"),
        (org_id, "identity", "Your trading account request was rejected", f"{p}/open-account"),
        (org_id, "identity", "Your trading account is ready", f"{p}/open-account"),
    ]
