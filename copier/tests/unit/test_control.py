"""Tests for the HTTP control endpoint (engine/control.py).

Every test here drives the REAL control site produced by
copier.engine.control against the REAL CopierApp produced by copier.main --
there is no hand-duplicated stub app. The CopierApp-level behaviour behind
these routes (pause/resume/dry-run/state/close-all/discover, boot wiring,
token refresh, the resync and balance loops) is covered in test_main.py,
whose fixtures and harness this module imports.
"""

import json
from io import BytesIO

import pytest_twisted
from twisted.internet import reactor as real_reactor
from twisted.web.client import Agent, readBody
from twisted.web.test.requesthelper import DummyRequest

import copier.main as main
from copier.domain.models import Side
from copier.engine.control import (
    make_control_site, HealthResource, StateResource, PauseResource, ResumeResource,
    DryRunResource, DriftCloseOrphanResource,
)
from copier.engine.reconcile import OrderSnapshot, PositionSnapshot

from test_main import (  # noqa: F401  (fixtures are used by name)
    ORG_A, SLAVE_A1, _wait_until, make_stub_client_factory,
    db_seeded, fernet_key, repo, token_store,
)


def _written_json(request):
    return json.loads(b"".join(request.written))


# ---------- /health ----------

def test_health_route_returns_the_apps_health(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    request = DummyRequest([b"health"])
    request.method = b"GET"
    HealthResource(app).render_GET(request)

    assert _written_json(request) == app.get_health()


@pytest_twisted.inlineCallbacks
def test_health_reachable_over_real_network_socket(repo, token_store):
    """Finding #5: prove /health actually answers over a real TCP socket when
    bound to an unspecified/all-interfaces address (as main.boot() does),
    not just via in-process resource calls."""
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)
    site = make_control_site(app)
    port = real_reactor.listenTCP(0, site, interface="0.0.0.0")
    try:
        agent = Agent(real_reactor)
        url = f"http://127.0.0.1:{port.getHost().port}/health".encode()
        response = yield agent.request(b"GET", url)
        body = yield readBody(response)
        assert response.code == 200
        assert json.loads(body)["status"] == "ok"
    finally:
        yield port.stopListening()


# ---------- pause / resume ----------

@pytest_twisted.inlineCallbacks
def test_control_site_post_pause_and_resume_over_dummy_request(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    request = DummyRequest([b"pause"])
    request.method = b"POST"
    request.content = BytesIO(json.dumps({"org_id": ORG_A, "account_id": None}).encode())
    PauseResource(app).render_POST(request)
    yield _wait_until(lambda: repo.get_org(ORG_A).copying_enabled is False)
    assert _written_json(request)["status"] == "paused"

    request = DummyRequest([b"resume"])
    request.method = b"POST"
    request.content = BytesIO(json.dumps({"org_id": ORG_A, "account_id": None}).encode())
    ResumeResource(app).render_POST(request)
    yield _wait_until(lambda: repo.get_org(ORG_A).copying_enabled is True)
    assert _written_json(request)["status"] == "resumed"


# ---------- dry-run ----------

def test_dry_run_route_toggles_the_orgs_flag(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    request = DummyRequest([b"dry-run"])
    request.method = b"POST"
    request.content = BytesIO(json.dumps({"org_id": ORG_A, "enabled": True}).encode())
    DryRunResource(app).render_POST(request)

    assert repo.get_org(ORG_A).dry_run is True
    assert _written_json(request) == {"status": "ok", "dry_run": True}


# ---------- /state ----------

def test_state_route_renders_the_orgs_state(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    repo.create_position_mapping(master_position_id=42, slave_account_id=SLAVE_A1,
                                 client_order_id="cm42.100", org_id=ORG_A)
    repo.activate_position_mapping(SLAVE_A1, "cm42.100", slave_position_id=5001,
                                   slave_volume=100_000, fill_price=1.10537)
    app.reconcilers[ORG_A].master_positions = [
        PositionSnapshot(position_id=42, symbol_id=1, side=Side.BUY, volume=100_000,
                         price=1.1, label="copy:m42")
    ]
    app.reconcilers[ORG_A].master_orders = [
        OrderSnapshot(order_id=7, symbol_id=1, volume=50_000, label="pending")
    ]

    request = DummyRequest([b"state"])
    request.method = b"GET"
    request.addArg(b"org_id", str(ORG_A).encode())
    StateResource(app).render_GET(request)

    rendered = _written_json(request)
    assert rendered["master_positions"][0]["position_id"] == 42
    assert rendered["master_positions"][0]["copies"][0]["slave_position_id"] == 5001
    assert rendered["pending_orders"][0]["order_id"] == 7


# ---------- drift routes never fake success ----------

def test_drift_close_orphan_on_unknown_id_reports_error_not_fake_success(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    request = DummyRequest([b"drift", b"close-orphan"])
    request.method = b"POST"
    request.content = BytesIO(json.dumps({"org_id": ORG_A, "id": "does-not-exist"}).encode())
    DriftCloseOrphanResource(app).render_POST(request)

    # Unknown id is the caller's error: ValueError now maps to 400 (see
    # _JsonResource._on_error) so the api proxy can forward the detail
    # instead of collapsing it into an opaque 502.
    assert request.responseCode == 400
    body = _written_json(request)
    assert "error" in body
    assert body.get("status") != "closed"
