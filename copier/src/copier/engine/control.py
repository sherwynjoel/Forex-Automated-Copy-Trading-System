"""HTTP control endpoint for the copier service.

The control endpoint is internal-only (bound to the Docker-internal network;
host isolation comes from docker-compose NOT publishing the port, not from
binding loopback -- see main.CONTROL_BIND_INTERFACE), providing operational
commands for pause/resume/resync/state queries and drift remedies.

Every route delegates to the real CopierApp -- no route ever fabricates a
success response without having performed the underlying work; when a
CopierApp method's Deferred errbacks, the route reports an HTTP 500 with the
error, never a fake "ok".

Routes:
- GET /health: service status
- GET /state: account snapshots, master positions (with slave copies),
  pending orders, and drift
- POST /pause: pause copying (global or per-slave), body {"account_id": int|null}
- POST /resume: resume copying (global or per-slave), body {"account_id": int|null}
- POST /resync: trigger reconciliation
- POST /reload: reload accounts/settings
- POST /dry-run: toggle dry-run mode, body {"enabled": bool}
- POST /discover: discover accounts from a connection, body {"connection_id": int}
- POST /drift/close-orphan, /drift/adopt, /drift/dismiss: drift remedies,
  body {"id": str, "master_position_id": int?}
- GET /details?account_id: full broker-side account profile
- GET /history/deals, /history/orders (?account_id&from&to): trade history
- POST /order: place a manual order, body {account_id, symbol, side,
  order_type, volume_lots, limit_price?, stop_price?, stop_loss?, take_profit?}
- POST /positions/close: close a position, body {account_id, position_id, volume_lots?}
- POST /orders/cancel: cancel a working order, body {account_id, order_id}
- POST /close-all: kill switch, body {"account_id": int|null} (null = every
  enabled account, pausing copying first)
"""

import json
import logging
from typing import Any

from twisted.web import resource, server

from copier.engine.queries import QueryFailed

log = logging.getLogger(__name__)


def _write_json(request, payload: dict, code: int | None = None) -> None:
    if code is not None:
        request.setResponseCode(code)
    request.setHeader(b"Content-Type", b"application/json")
    request.write(json.dumps(payload, default=str).encode())
    request.finish()


def _read_json_body(request) -> dict:
    body = request.content.read()
    return json.loads(body) if body else {}


def _int_arg(request, name: bytes) -> int:
    """Parse a required integer query-string argument or raise ValueError."""
    values = request.args.get(name)
    if not values:
        raise ValueError(f"{name.decode()} query parameter required")
    try:
        return int(values[0])
    except (TypeError, ValueError):
        raise ValueError(f"{name.decode()} must be an integer")


class _JsonResource(resource.Resource):
    """Base class for control resources: JSON body in, JSON body out.

    Subclasses implement `_handle(request, body) -> dict | Deferred[dict]`.
    A Deferred result is awaited (NOT_DONE_YET) and its errback maps to an
    HTTP 500 with the error message -- routes never fabricate success.
    """

    isLeaf = True

    def __init__(self, app):
        super().__init__()
        self.app = app

    def _handle(self, request, body: dict):
        raise NotImplementedError

    def _render(self, request):
        body = _read_json_body(request) if request.method in (b"POST", b"PUT") else {}
        result = self._handle(request, body)
        if hasattr(result, "addCallback"):  # Deferred
            result.addCallback(lambda payload: _write_json(request, payload))
            result.addErrback(self._on_error, request)
            return server.NOT_DONE_YET
        _write_json(request, result)
        return server.NOT_DONE_YET

    def _on_error(self, failure, request):
        log.error("%s failed: %s", type(self).__name__, failure)
        error_msg = str(failure.value) if hasattr(failure, "value") else str(failure)
        # Validation problems and broker error RESPONSES are the caller's
        # 4xx, not a copier failure: the api proxy forwards 4xx detail
        # faithfully but collapses any copier 5xx into an opaque
        # "copier unreachable" 502 (see api routes/settings_control.py).
        code = 400 if failure.check(ValueError, QueryFailed) else 500
        _write_json(request, {"error": error_msg}, code=code)

    def render_GET(self, request):
        try:
            return self._render(request)
        except ValueError as e:
            _write_json(request, {"error": str(e)}, code=400)
            return server.NOT_DONE_YET
        except Exception as e:
            log.exception("%s failed", type(self).__name__)
            _write_json(request, {"error": str(e)}, code=500)
            return server.NOT_DONE_YET

    def render_POST(self, request):
        try:
            return self._render(request)
        except ValueError as e:
            _write_json(request, {"error": str(e)}, code=400)
            return server.NOT_DONE_YET
        except Exception as e:
            log.exception("%s failed", type(self).__name__)
            _write_json(request, {"error": str(e)}, code=500)
            return server.NOT_DONE_YET


class HealthResource(_JsonResource):
    """GET /health: service status and master account info."""

    def _handle(self, request, body):
        return self.app.get_health()


class StateResource(_JsonResource):
    """GET /state: account snapshots, master positions, pending orders, drift."""

    def _handle(self, request, body):
        return self.app.get_state()


class PauseResource(_JsonResource):
    """POST /pause: pause copying globally or per-slave."""

    def _handle(self, request, body):
        account_id = body.get("account_id")
        d = self.app.pause(account_id=account_id)
        d.addCallback(lambda _: {"status": "paused", "account_id": account_id})
        return d


class ResumeResource(_JsonResource):
    """POST /resume: resume copying globally or per-slave."""

    def _handle(self, request, body):
        account_id = body.get("account_id")
        d = self.app.resume(account_id=account_id)
        d.addCallback(lambda _: {"status": "resumed", "account_id": account_id})
        return d


class ResyncResource(_JsonResource):
    """POST /resync: trigger reconciliation."""

    def _handle(self, request, body):
        d = self.app.resync()
        d.addCallback(lambda items: {"status": "resynced", "drift_count": len(items or [])})
        return d


class ReloadResource(_JsonResource):
    """POST /reload: reload accounts and settings."""

    def _handle(self, request, body):
        d = self.app.reload()
        d.addCallback(lambda _: {"status": "reloaded"})
        return d


class DryRunResource(_JsonResource):
    """POST /dry-run: toggle dry-run mode."""

    def _handle(self, request, body):
        enabled = bool(body.get("enabled", False))
        self.app.set_dry_run(enabled)
        return {"status": "ok", "dry_run": enabled}


class DiscoverResource(_JsonResource):
    """POST /discover: discover accounts reachable via a connection's token."""

    def _handle(self, request, body):
        connection_id = body.get("connection_id")
        if connection_id is None:
            raise ValueError("connection_id required")
        d = self.app.discover(connection_id)
        d.addCallback(lambda accounts: {
            "status": "discovered",
            "account_ids": [a.ctidTraderAccountId for a in accounts],
        })
        return d


class DriftCloseOrphanResource(_JsonResource):
    """POST /drift/close-orphan: close an orphan slave position."""

    def _handle(self, request, body):
        item_id = body.get("id")
        if item_id is None:
            raise ValueError("id required")
        d = self.app.reconciler.close_orphan(item_id)
        d.addCallback(lambda _: {"status": "closed", "id": item_id})
        return d


class DriftAdoptResource(_JsonResource):
    """POST /drift/adopt: adopt an orphan slave position under a master position."""

    def _handle(self, request, body):
        item_id = body.get("id")
        master_position_id = body.get("master_position_id")
        if item_id is None:
            raise ValueError("id required")
        if master_position_id is None:
            raise ValueError("master_position_id required")
        d = self.app.reconciler.adopt(item_id, master_position_id)
        d.addCallback(lambda _: {"status": "adopted", "id": item_id})
        return d


class DriftDismissResource(_JsonResource):
    """POST /drift/dismiss: dismiss a drift item."""

    def _handle(self, request, body):
        item_id = body.get("id")
        if item_id is None:
            raise ValueError("id required")
        d = self.app.reconciler.dismiss(item_id)
        d.addCallback(lambda _: {"status": "dismissed", "id": item_id})
        return d


class PlaceOrderResource(_JsonResource):
    """POST /order: place a manual order on any connected account.

    Body: {account_id, symbol, side, order_type, volume_lots,
           limit_price?, stop_price?, stop_loss?, take_profit?}
    Validation lives in CopierApp.place_order; its ValueErrors map to 400.
    """

    def _handle(self, request, body):
        return self.app.place_order(body)


class ClosePositionResource(_JsonResource):
    """POST /positions/close: close (or partially close) one position.

    Body: {account_id, position_id, volume_lots?} -- omitting volume_lots
    closes the position's full live volume.
    """

    def _handle(self, request, body):
        account_id = body.get("account_id")
        position_id = body.get("position_id")
        if account_id is None or position_id is None:
            raise ValueError("account_id and position_id required")
        return self.app.close_position(
            int(account_id), int(position_id), body.get("volume_lots"))


class CancelOrderResource(_JsonResource):
    """POST /orders/cancel: cancel one working order.

    Body: {account_id, order_id}
    """

    def _handle(self, request, body):
        account_id = body.get("account_id")
        order_id = body.get("order_id")
        if account_id is None or order_id is None:
            raise ValueError("account_id and order_id required")
        return self.app.cancel_order(int(account_id), int(order_id))


class CloseAllResource(_JsonResource):
    """POST /close-all: the kill switch.

    Body {account_id} flattens one account; an empty body (or null
    account_id) flattens EVERY enabled account and pauses copying first.
    """

    def _handle(self, request, body):
        account_id = body.get("account_id")
        return self.app.close_all(
            int(account_id) if account_id is not None else None)


class PositionsResource(resource.Resource):
    """Parent resource for /positions/close."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.putChild(b"close", ClosePositionResource(app))


class OrdersResource(resource.Resource):
    """Parent resource for /orders/cancel."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.putChild(b"cancel", CancelOrderResource(app))


class DetailsResource(_JsonResource):
    """GET /details?account_id=N: full broker-side account profile."""

    def _handle(self, request, body):
        account_id = _int_arg(request, b"account_id")
        return self.app.get_account_details(account_id)


class DealHistoryResource(_JsonResource):
    """GET /history/deals?account_id=N&from=ms&to=ms: deal (fill) history."""

    def _handle(self, request, body):
        account_id = _int_arg(request, b"account_id")
        from_ms = _int_arg(request, b"from")
        to_ms = _int_arg(request, b"to")
        return self.app.get_deal_history(account_id, from_ms, to_ms)


class OrderHistoryResource(_JsonResource):
    """GET /history/orders?account_id=N&from=ms&to=ms: order history."""

    def _handle(self, request, body):
        account_id = _int_arg(request, b"account_id")
        from_ms = _int_arg(request, b"from")
        to_ms = _int_arg(request, b"to")
        return self.app.get_order_history(account_id, from_ms, to_ms)


class HistoryResource(resource.Resource):
    """Parent resource for /history/{deals,orders}."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.putChild(b"deals", DealHistoryResource(app))
        self.putChild(b"orders", OrderHistoryResource(app))


class DriftResource(resource.Resource):
    """Parent resource for /drift/{close-orphan,adopt,dismiss}."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.putChild(b"close-orphan", DriftCloseOrphanResource(app))
        self.putChild(b"adopt", DriftAdoptResource(app))
        self.putChild(b"dismiss", DriftDismissResource(app))


class RootResource(resource.Resource):
    """Root resource that dispatches to sub-resources."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.putChild(b"health", HealthResource(app))
        self.putChild(b"state", StateResource(app))
        self.putChild(b"pause", PauseResource(app))
        self.putChild(b"resume", ResumeResource(app))
        self.putChild(b"resync", ResyncResource(app))
        self.putChild(b"reload", ReloadResource(app))
        self.putChild(b"dry-run", DryRunResource(app))
        self.putChild(b"discover", DiscoverResource(app))
        self.putChild(b"drift", DriftResource(app))
        self.putChild(b"details", DetailsResource(app))
        self.putChild(b"history", HistoryResource(app))
        self.putChild(b"order", PlaceOrderResource(app))
        self.putChild(b"positions", PositionsResource(app))
        self.putChild(b"orders", OrdersResource(app))
        self.putChild(b"close-all", CloseAllResource(app))


def make_control_site(app: Any) -> server.Site:
    """Create the Twisted web Site for the control endpoint.

    Binding to the Docker-internal network only is a caller concern (see
    main.boot(), which binds main.CONTROL_BIND_INTERFACE); this function only
    builds the resource tree.
    """
    root = RootResource(app)
    return server.Site(root)
