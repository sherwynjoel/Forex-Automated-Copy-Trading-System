"""Unit tests for the operator-action control routes: POST /order,
POST /positions/close, POST /orders/cancel, POST /close-all.

Driven with DummyRequest against a fake app: proves body parsing, JSON
output, and the 400 mapping for validation failures.  The real trade paths
behind these routes are covered end to end by
tests/integration/test_manual_actions.py.
"""

import json
from io import BytesIO

from twisted.internet import defer
from twisted.web.test.requesthelper import DummyRequest

from copier.engine.control import (
    CancelOrderResource, CloseAllResource, ClosePositionResource,
    PlaceOrderResource)


def _written_json(request):
    return json.loads(b"".join(request.written))


def _post(resource, path_segments, body: dict):
    request = DummyRequest(path_segments)
    request.method = b"POST"
    request.content = BytesIO(json.dumps(body).encode())
    resource.render_POST(request)
    return request


class _ActionApp:
    """Fake app whose method signatures MIRROR CopierApp's exactly.

    A stub that keeps an outdated signature certifies a dead contract: while
    close_all() here still took (account_id=None), the route's positional
    call bound account_id to CopierApp's org_id and the tests stayed green
    over a kill switch that would have flattened nothing (or the wrong org).
    Any signature drift in CopierApp must show up here as a TypeError.
    """

    def __init__(self):
        self.calls = []
        self.actors = []

    def place_order(self, params):
        self.calls.append(("place_order", params))
        if params.get("side") == "HOLD":
            raise ValueError("side must be BUY or SELL")
        return {"status": "submitted", "account_id": params["account_id"]}

    # `actor` is the audit attribution the api forwards; the routes pass it
    # to every money-moving call, so the stubs record it too.
    def close_position(self, account_id, position_id, volume_lots=None, actor=None):
        self.calls.append(("close_position", account_id, position_id, volume_lots))
        self.actors.append(actor)
        return defer.succeed({"status": "submitted", "position_id": position_id})

    def cancel_order(self, account_id, order_id, actor=None):
        self.calls.append(("cancel_order", account_id, order_id))
        self.actors.append(actor)
        return defer.succeed({"status": "submitted", "order_id": order_id})

    def close_all(self, org_id, account_id=None, actor=None):
        self.calls.append(("close_all", org_id, account_id))
        self.actors.append(actor)
        return defer.succeed({"status": "flattened", "paused": account_id is None,
                              "accounts": []})


def test_place_order_route_forwards_body():
    app = _ActionApp()
    body = {"account_id": 100, "symbol": "EURUSD", "side": "BUY",
            "order_type": "MARKET", "volume_lots": 0.5}
    request = _post(PlaceOrderResource(app), [b"order"], body)

    assert app.calls == [("place_order", body)]
    assert _written_json(request)["status"] == "submitted"


def test_place_order_route_validation_error_is_400():
    app = _ActionApp()
    request = _post(PlaceOrderResource(app), [b"order"], {"side": "HOLD"})

    assert request.responseCode == 400
    assert "side" in _written_json(request)["error"]


def test_close_position_route_parses_body():
    app = _ActionApp()
    request = _post(ClosePositionResource(app), [b"positions", b"close"],
                    {"account_id": 100, "position_id": 7001, "volume_lots": 0.5})

    assert app.calls == [("close_position", 100, 7001, 0.5)]
    assert _written_json(request)["status"] == "submitted"


def test_close_position_route_requires_ids():
    app = _ActionApp()
    request = _post(ClosePositionResource(app), [b"positions", b"close"],
                    {"account_id": 100})

    assert request.responseCode == 400
    assert app.calls == []


def test_cancel_order_route_parses_body():
    app = _ActionApp()
    request = _post(CancelOrderResource(app), [b"orders", b"cancel"],
                    {"account_id": 100, "order_id": 9001})

    assert app.calls == [("cancel_order", 100, 9001)]
    assert _written_json(request)["status"] == "submitted"


def test_close_all_route_single_account():
    """The kill switch is org-scoped: the route must pass the org through
    and the account as the SECOND argument. (Red until Task 14 converts
    CloseAllResource -- today's positional call binds the account id to
    CopierApp.close_all's org_id.)"""
    app = _ActionApp()
    request = _post(CloseAllResource(app), [b"close-all"],
                    {"org_id": 7, "account_id": 100})

    assert app.calls == [("close_all", 7, 100)]
    assert _written_json(request)["status"] == "flattened"


def test_close_all_route_org_wide_when_no_account():
    """No account_id means "every account in THIS org" -- never "every
    account everywhere", and never an org-less call. (Red until Task 14.)"""
    app = _ActionApp()
    request = _post(CloseAllResource(app), [b"close-all"], {"org_id": 7})

    assert app.calls == [("close_all", 7, None)]
    assert _written_json(request)["paused"] is True


def test_money_moving_routes_carry_the_actor_to_the_app():
    """The api stamps actor_email on every command it proxies; the control
    routes must hand it to the app so it lands on the audit event."""
    app = _ActionApp()
    _post(ClosePositionResource(app), [b"positions", b"close"],
          {"account_id": 100, "position_id": 7001,
           "actor_email": "ada@example.com"})
    _post(CancelOrderResource(app), [b"orders", b"cancel"],
          {"account_id": 100, "order_id": 9001,
           "actor_email": "ada@example.com"})
    _post(CloseAllResource(app), [b"close-all"],
          {"org_id": 1, "actor_email": "ada@example.com"})

    assert app.actors == ["ada@example.com"] * 3


def test_actor_is_absent_when_the_caller_supplies_none():
    """Autonomous or unattributed calls record no actor rather than a
    fabricated one."""
    app = _ActionApp()
    _post(CloseAllResource(app), [b"close-all"], {"org_id": 1})
    assert app.actors == [None]
