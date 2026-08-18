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
    def __init__(self):
        self.calls = []

    def place_order(self, params):
        self.calls.append(("place_order", params))
        if params.get("side") == "HOLD":
            raise ValueError("side must be BUY or SELL")
        return {"status": "submitted", "account_id": params["account_id"]}

    def close_position(self, account_id, position_id, volume_lots=None):
        self.calls.append(("close_position", account_id, position_id, volume_lots))
        return defer.succeed({"status": "submitted", "position_id": position_id})

    def cancel_order(self, account_id, order_id):
        self.calls.append(("cancel_order", account_id, order_id))
        return defer.succeed({"status": "submitted", "order_id": order_id})

    def close_all(self, account_id=None):
        self.calls.append(("close_all", account_id))
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
    app = _ActionApp()
    request = _post(CloseAllResource(app), [b"close-all"], {"account_id": 100})

    assert app.calls == [("close_all", 100)]
    assert _written_json(request)["status"] == "flattened"


def test_close_all_route_global_when_no_account():
    app = _ActionApp()
    request = _post(CloseAllResource(app), [b"close-all"], {})

    assert app.calls == [("close_all", None)]
    assert _written_json(request)["paused"] is True
