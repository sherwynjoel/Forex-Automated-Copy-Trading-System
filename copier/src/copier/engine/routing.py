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
