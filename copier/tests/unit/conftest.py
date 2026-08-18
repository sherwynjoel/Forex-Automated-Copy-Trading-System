import pytest

from copier.engine.routing import OrgRouting


@pytest.fixture
def make_routing():
    """OrgRouting for a single-org test world:
    make_routing(master=100, slaves=[SlaveConfig...], org_id=1)"""
    def _make(master, slaves, org_id=1):
        org_by_account = {master: org_id, **{s.account_id: org_id for s in slaves}}
        return OrgRouting(
            org_by_account=org_by_account,
            master_by_org={org_id: master},
            slaves_by_org={org_id: list(slaves)},
        )
    return _make
