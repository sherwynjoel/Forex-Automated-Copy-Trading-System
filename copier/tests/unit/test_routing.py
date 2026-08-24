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


class TestRoutingCache:
    """Routing is resolved on EVERY execution event; rebuilding it from the
    database each time (accounts plus a symbol cache per slave) was the
    single largest latency in the copy path. A short TTL trades at most one
    second of staleness for a sub-millisecond hot path; reload() invalidates
    explicitly so control-plane changes still apply immediately."""

    def _cache(self, ttl_s=1.0):
        from twisted.internet.task import Clock
        from copier.engine.routing import RoutingCache

        clock = Clock()
        builds = []

        def build():
            builds.append(1)
            return object()

        return RoutingCache(build, clock=clock, ttl_s=ttl_s), clock, builds

    def test_within_ttl_serves_the_same_snapshot(self):
        cache, clock, builds = self._cache()
        first = cache()
        clock.advance(0.5)
        assert cache() is first
        assert len(builds) == 1

    def test_after_ttl_a_fresh_snapshot_is_built(self):
        cache, clock, builds = self._cache()
        first = cache()
        clock.advance(1.01)
        assert cache() is not first
        assert len(builds) == 2

    def test_invalidate_forces_a_rebuild_within_ttl(self):
        cache, clock, builds = self._cache()
        first = cache()
        cache.invalidate()
        assert cache() is not first
        assert len(builds) == 2
