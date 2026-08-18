from decimal import Decimal

from copier.db.repo import AccountRow
from copier.engine.routing import build_routing


def _row(account_id, org_id, role, enabled=True, status="ok"):
    return AccountRow(
        account_id=account_id, org_id=org_id, connection_id=1, trader_login=account_id,
        is_live=False, role=role, enabled=enabled, multiplier=Decimal("1.0"),
        status=status, last_error=None)


def test_two_orgs_route_independently():
    accounts = [
        _row(100, 1, "master"), _row(101, 1, "slave"), _row(102, 1, "slave"),
        _row(200, 2, "master"), _row(201, 2, "slave"),
        _row(300, 2, "ignored"),
    ]
    routing = build_routing(accounts, symbol_loader=lambda account_id: {})
    assert routing.org_by_account[101] == 1 and routing.org_by_account[201] == 2
    assert routing.master_by_org == {1: 100, 2: 200}
    assert [s.account_id for s in routing.slaves_by_org[1]] == [101, 102]
    assert [s.account_id for s in routing.slaves_by_org[2]] == [201]
    assert 300 in routing.org_by_account  # ignored accounts still resolve to their org


def test_paused_or_disabled_slaves_are_not_enabled():
    accounts = [
        _row(100, 1, "master"),
        _row(101, 1, "slave", enabled=False),
        _row(102, 1, "slave", status="paused"),
    ]
    routing = build_routing(accounts, symbol_loader=lambda account_id: {})
    flags = {s.account_id: s.enabled for s in routing.slaves_by_org[1]}
    assert flags == {101: False, 102: False}


def test_org_without_master():
    routing = build_routing([_row(201, 2, "slave")], symbol_loader=lambda a: {})
    assert 2 not in routing.master_by_org
    assert routing.org_by_account[201] == 2
