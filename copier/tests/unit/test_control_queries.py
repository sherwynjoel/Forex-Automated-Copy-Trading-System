"""Unit tests for the /details and /history/* control routes and the
CopierApp query wrappers behind them.

Route tests drive the Twisted resources with DummyRequest against a fake
app, proving query-string parsing, JSON output, and the 400 mapping for
missing/invalid parameters.  Wrapper tests prove CopierApp resolves the
account's client + symbol map and hands them to engine/queries, and that an
unknown account fails rather than silently querying nothing.
"""

import json

import pytest
import pytest_twisted
from twisted.internet import defer
from twisted.web.test.requesthelper import DummyRequest

import copier.main as main
from copier.engine import queries
from copier.engine.control import (
    DealHistoryResource, DetailsResource, OrderHistoryResource)

from test_control import make_stub_client_factory
from test_control import repo, token_store, db_seeded, fernet_key  # noqa: F401  (fixtures)


def _written_json(request):
    return json.loads(b"".join(request.written))


class _QueryApp:
    """Fake app recording query-wrapper calls and returning canned payloads."""

    def __init__(self):
        self.calls = []

    def get_account_details(self, account_id):
        self.calls.append(("details", account_id))
        return defer.succeed({"account_id": account_id, "balance": 1.0})

    def get_deal_history(self, account_id, from_ms, to_ms):
        self.calls.append(("deals", account_id, from_ms, to_ms))
        return defer.succeed({"deals": [], "has_more": False})

    def get_order_history(self, account_id, from_ms, to_ms):
        self.calls.append(("orders", account_id, from_ms, to_ms))
        return defer.succeed({"orders": [], "has_more": False})


# ---------- route wiring ----------

def test_details_route_parses_account_id_and_returns_payload():
    app = _QueryApp()
    request = DummyRequest([b"details"])
    request.method = b"GET"
    request.addArg(b"account_id", b"999")

    DetailsResource(app).render_GET(request)

    assert app.calls == [("details", 999)]
    assert _written_json(request)["account_id"] == 999


def test_details_route_missing_account_id_is_400():
    app = _QueryApp()
    request = DummyRequest([b"details"])
    request.method = b"GET"

    DetailsResource(app).render_GET(request)

    assert request.responseCode == 400
    assert "account_id" in _written_json(request)["error"]
    assert app.calls == []


def test_deal_history_route_parses_window():
    app = _QueryApp()
    request = DummyRequest([b"history", b"deals"])
    request.method = b"GET"
    request.addArg(b"account_id", b"100")
    request.addArg(b"from", b"1000")
    request.addArg(b"to", b"2000")

    DealHistoryResource(app).render_GET(request)

    assert app.calls == [("deals", 100, 1000, 2000)]
    assert _written_json(request) == {"deals": [], "has_more": False}


def test_deal_history_route_missing_window_is_400():
    app = _QueryApp()
    request = DummyRequest([b"history", b"deals"])
    request.method = b"GET"
    request.addArg(b"account_id", b"100")

    DealHistoryResource(app).render_GET(request)

    assert request.responseCode == 400
    assert app.calls == []


def test_order_history_route_parses_window():
    app = _QueryApp()
    request = DummyRequest([b"history", b"orders"])
    request.method = b"GET"
    request.addArg(b"account_id", b"100")
    request.addArg(b"from", b"5")
    request.addArg(b"to", b"9")

    OrderHistoryResource(app).render_GET(request)

    assert app.calls == [("orders", 100, 5, 9)]
    assert _written_json(request) == {"orders": [], "has_more": False}


def test_non_numeric_account_id_is_400():
    app = _QueryApp()
    request = DummyRequest([b"details"])
    request.method = b"GET"
    request.addArg(b"account_id", b"bogus")

    DetailsResource(app).render_GET(request)

    assert request.responseCode == 400
    assert app.calls == []


# ---------- CopierApp wrappers ----------

@pytest_twisted.inlineCallbacks
def test_app_get_account_details_resolves_client_and_symbols(repo, token_store, monkeypatch):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    seen = {}

    def fake_account_details(client, account_id, symbols_by_id):
        seen["client"] = client
        seen["account_id"] = account_id
        seen["symbols"] = symbols_by_id
        return defer.succeed({"account_id": account_id})

    monkeypatch.setattr(queries, "account_details", fake_account_details)

    result = yield app.get_account_details(999)

    assert result == {"account_id": 999}
    assert seen["account_id"] == 999
    # The client handed over is the master's actual client (shard 0, demo).
    assert seen["client"] is app.clients[False][0]
    assert isinstance(seen["symbols"], dict)


@pytest_twisted.inlineCallbacks
def test_app_query_for_unknown_account_fails(repo, token_store):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    with pytest.raises(ValueError):
        yield app.get_account_details(424242)


@pytest_twisted.inlineCallbacks
def test_app_get_deal_history_passes_window_through(repo, token_store, monkeypatch):
    app = main.build_app(repo, token_store, make_stub_client_factory(), shards=1)

    seen = {}

    def fake_deal_history(client, account_id, symbols_by_id, from_ms, to_ms):
        seen.update(account_id=account_id, from_ms=from_ms, to_ms=to_ms)
        return defer.succeed({"deals": [], "has_more": False})

    monkeypatch.setattr(queries, "deal_history", fake_deal_history)

    result = yield app.get_deal_history(100, 1_000, 2_000)

    assert result == {"deals": [], "has_more": False}
    assert seen == {"account_id": 100, "from_ms": 1_000, "to_ms": 2_000}
