"""Per-org routing: which org an account belongs to, each org's master, and
each org's slave fleet. Pure data derived from the accounts table; rebuilt by
CopierApp on boot and on every reload()."""

from dataclasses import dataclass
from typing import Callable, Mapping

from copier.db.repo import AccountRow
from copier.domain.models import SlaveConfig, SymbolInfo


@dataclass(frozen=True)
class OrgRouting:
    org_by_account: Mapping[int, int]
    master_by_org: Mapping[int, int]
    slaves_by_org: Mapping[int, list[SlaveConfig]]


def build_routing(
    accounts: list[AccountRow],
    symbol_loader: Callable[[int], Mapping[str, SymbolInfo]],
) -> OrgRouting:
    org_by_account: dict[int, int] = {}
    master_by_org: dict[int, int] = {}
    slaves_by_org: dict[int, list[SlaveConfig]] = {}
    for a in accounts:
        org_by_account[a.account_id] = a.org_id
        if a.role == "master":
            master_by_org[a.org_id] = a.account_id
        elif a.role == "slave":
            slaves_by_org.setdefault(a.org_id, []).append(
                SlaveConfig(
                    account_id=a.account_id,
                    enabled=a.enabled and a.status != "paused",
                    multiplier=a.multiplier,
                    symbols=symbol_loader(a.account_id),
                )
            )
    return OrgRouting(
        org_by_account=org_by_account,
        master_by_org=master_by_org,
        slaves_by_org=slaves_by_org,
    )


class RoutingCache:
    """Serve build_routing()'s snapshot from a short-lived cache.

    Routing is resolved on EVERY execution event. Rebuilding it from the
    database each time -- the accounts list plus a full symbol cache per
    slave -- measured 320ms on production with 11 accounts, in front of
    every single copy. It was by far the largest latency in the path.

    Freshness is preserved for everything that decides money movement:
    the snapshot bakes in each slave's enabled/paused flag and multiplier,
    so the api calls /reload on any edit to those (and to role), and
    CopierApp.reload() invalidates this cache on the way in and on the way
    out. The kill switch and dry-run gate are not in here at all --
    Dispatcher re-reads get_org() on every single dispatch.

    What is left TTL-stale is a direct database edit that never tells the
    copier -- a manual UPDATE, or a future writer that forgets to reload.
    Those apply within ttl_s instead of on the next event.
    """

    def __init__(self, build, clock=None, ttl_s: float = 1.0):
        if clock is None:
            from twisted.internet import reactor as clock  # pragma: no cover
        self._build = build
        self._clock = clock
        self._ttl = ttl_s
        self._snapshot = None
        self._expires = 0.0

    def __call__(self):
        # The reactor clock is wall-clock, not monotonic: an NTP step
        # backwards would otherwise freeze the snapshot for the size of the
        # step. Treat any jump behind the build time as expiry.
        now = self._clock.seconds()
        stale = (
            self._snapshot is None
            or now >= self._expires
            or now < self._expires - self._ttl
        )
        if stale:
            self._snapshot = self._build()
            self._expires = self._clock.seconds() + self._ttl
        return self._snapshot

    def invalidate(self) -> None:
        """Drop the snapshot so the next call rebuilds from the database."""
        self._snapshot = None
