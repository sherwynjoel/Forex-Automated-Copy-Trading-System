"""HTTP control endpoint for the copier service.

The control endpoint is internal-only (bound to the Docker-internal network;
host isolation comes from docker-compose NOT publishing the port, not from
binding loopback -- see main.CONTROL_BIND_INTERFACE), providing operational
commands for pause/resume/resync/state queries and drift remedies.

Every route delegates to the real CopierApp -- no route ever fabricates a
success response without having performed the underlying work; when a
CopierApp method's Deferred errbacks, the route reports an HTTP 500 with the
error, never a fake "ok".

Every scoped route below requires "org_id" in its body (or, for /state, in
the query string) -- a scoped command with no org never falls through to
anything global; a missing/invalid org_id is a 400 "org_id required".

Routes:
- GET /health: service status
- GET /state?org_id=N: one org's account snapshots, master positions (with
  slave copies), pending orders, and drift
- POST /pause: pause copying for an org (or one of its slaves), body
  {"org_id": int, "account_id": int|null}
- POST /resume: resume copying for an org (or one of its slaves), body
  {"org_id": int, "account_id": int|null}
- POST /resync: trigger reconciliation for one org, body {"org_id": int}
- POST /reload: reload accounts/settings
- POST /dry-run: toggle an org's dry-run mode, body {"org_id": int, "enabled": bool}
- POST /discover: discover accounts from a connection, body {"connection_id": int}
- POST /drift/close-orphan, /drift/adopt, /drift/dismiss: drift remedies for
  one org, body {"org_id": int, "id": str, "master_position_id": int?}
- GET /details?account_id: full broker-side account profile
- GET /history/deals, /history/orders, /history/cashflow
  (?account_id&from&to): trade and cash-flow history
- GET /margin-estimate?account_id&symbol&volume_lots: pre-trade margin
- GET /trendbars?account_id&symbol&period&from&to: historical candles
- GET /position-deals?account_id&position_id&from&to: one position's deals
- GET /analytics?account_id&weeks: performance aggregation
- POST /order: place a manual order, body {account_id, symbol, side,
  order_type, volume_lots, limit_price?, stop_price?, stop_loss?, take_profit?}
- POST /positions/close: close a position, body {account_id, position_id, volume_lots?}
- POST /orders/cancel: cancel a working order, body {account_id, order_id}
- POST /close-all: kill switch for one org, body {"org_id": int, "account_id": int?}
  (no account_id = every enabled account in that org; copying is paused only
  while the flatten runs and restored before the response)
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


def _int_arg(request, name: bytes, default: int | None = None) -> int:
    """Parse an integer query-string argument; required unless a default is
    given. Raises ValueError on absence (without default) or non-integers."""
    values = request.args.get(name)
    if not values:
        if default is not None:
            return default
        raise ValueError(f"{name.decode()} query parameter required")
    try:
        return int(values[0])
    except (TypeError, ValueError):
        raise ValueError(f"{name.decode()} must be an integer")


def _float_arg(request, name: bytes) -> float:
    values = request.args.get(name)
    if not values:
        raise ValueError(f"{name.decode()} query parameter required")
    try:
        return float(values[0])
    except (TypeError, ValueError):
        raise ValueError(f"{name.decode()} must be a number")


def _str_arg(request, name: bytes) -> str:
    values = request.args.get(name)
    if not values or not values[0]:
        raise ValueError(f"{name.decode()} query parameter required")
    return values[0].decode()


def _org_id_from(body: dict) -> int:
    """Parse the required "org_id" from a JSON body or raise ValueError.

    Every scoped command requires org_id -- there is no fall-through to a
    global default, so a missing org fails loudly here before anything is
    read or written.
    """
    org_id = body.get("org_id")
    if org_id is None:
        raise ValueError("org_id required")
    try:
        return int(org_id)
    except (TypeError, ValueError):
        raise ValueError("org_id must be an integer")


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
    """GET /state?org_id=N: one org's account snapshots, master positions,
    pending orders, and drift."""

    def _handle(self, request, body):
        org_id = _int_arg(request, b"org_id")
        return self.app.get_state(org_id)


class TicksResource(_JsonResource):
    """GET /ticks?org_id=N: live quotes + per-account marks, in-memory only.

    The api polls this several times a second for orgs with dashboard
    sockets open and pushes changed payloads down the events WebSocket as
    category='quotes' -- see api ws.EventBroadcaster.start_ticker.
    """

    def _handle(self, request, body):
        org_id = _int_arg(request, b"org_id")
        return self.app.get_ticks(org_id)


class PauseResource(_JsonResource):
    """POST /pause: pause copying for an org, or one of its slaves."""

    def _handle(self, request, body):
        org_id = _org_id_from(body)
        account_id = body.get("account_id")
        d = self.app.pause(org_id, account_id=account_id)
        d.addCallback(lambda _: {"status": "paused", "org_id": org_id,
                                 "account_id": account_id})
        return d


class ResumeResource(_JsonResource):
    """POST /resume: resume copying for an org, or one of its slaves."""

    def _handle(self, request, body):
        org_id = _org_id_from(body)
        account_id = body.get("account_id")
        d = self.app.resume(org_id, account_id=account_id)
        d.addCallback(lambda _: {"status": "resumed", "org_id": org_id,
                                 "account_id": account_id})
        return d


class ResyncResource(_JsonResource):
    """POST /resync: trigger reconciliation for one org."""

    def _handle(self, request, body):
        org_id = _org_id_from(body)
        d = self.app.resync(org_id)
        d.addCallback(lambda items: {"status": "resynced", "drift_count": len(items or [])})
        return d


class ReloadResource(_JsonResource):
    """POST /reload: reload accounts and settings."""

    def _handle(self, request, body):
        d = self.app.reload()
        d.addCallback(lambda _: {"status": "reloaded"})
        return d


class DryRunResource(_JsonResource):
    """POST /dry-run: toggle an org's dry-run mode."""

    def _handle(self, request, body):
        org_id = _org_id_from(body)
        enabled = bool(body.get("enabled", False))
        self.app.set_dry_run(org_id, enabled)
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
    """POST /drift/close-orphan: close an orphan slave position in one org."""

    def _handle(self, request, body):
        org_id = _org_id_from(body)
        item_id = body.get("id")
        if item_id is None:
            raise ValueError("id required")
        d = self.app.reconciler_for(org_id).close_orphan(item_id)
        d.addCallback(lambda _: {"status": "closed", "id": item_id})
        return d


class DriftAdoptResource(_JsonResource):
    """POST /drift/adopt: adopt an orphan slave position under a master
    position, in one org."""

    def _handle(self, request, body):
        org_id = _org_id_from(body)
        item_id = body.get("id")
        master_position_id = body.get("master_position_id")
        if item_id is None:
            raise ValueError("id required")
        if master_position_id is None:
            raise ValueError("master_position_id required")
        d = self.app.reconciler_for(org_id).adopt(item_id, master_position_id)
        d.addCallback(lambda _: {"status": "adopted", "id": item_id})
        return d


class DriftDismissResource(_JsonResource):
    """POST /drift/dismiss: dismiss a drift item in one org."""

    def _handle(self, request, body):
        org_id = _org_id_from(body)
        item_id = body.get("id")
        if item_id is None:
            raise ValueError("id required")
        d = self.app.reconciler_for(org_id).dismiss(item_id)
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
    """POST /close-all: the kill switch for one org.

    Body {org_id, account_id} flattens one account of that org; body
    {org_id} (no account_id, or a null one) flattens EVERY enabled account
    in that org, pausing its copying only for the duration of the flatten
    (restored before the response -- see CopierApp.close_all).
    """

    def _handle(self, request, body):
        org_id = _org_id_from(body)
        account_id = body.get("account_id")
        return self.app.close_all(
            org_id, int(account_id) if account_id is not None else None)


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


class CashFlowHistoryResource(_JsonResource):
    """GET /history/cashflow?account_id=N&from=ms&to=ms: deposits/withdrawals."""

    def _handle(self, request, body):
        account_id = _int_arg(request, b"account_id")
        from_ms = _int_arg(request, b"from")
        to_ms = _int_arg(request, b"to")
        return self.app.get_cash_flow(account_id, from_ms, to_ms)


class MarginEstimateResource(_JsonResource):
    """GET /margin-estimate?account_id&symbol&volume_lots: what margin the
    broker would require for that order, both directions."""

    def _handle(self, request, body):
        account_id = _int_arg(request, b"account_id")
        symbol = _str_arg(request, b"symbol")
        volume_lots = _float_arg(request, b"volume_lots")
        return self.app.get_expected_margin(account_id, symbol, volume_lots)


class QuoteResource(_JsonResource):
    """GET /quote?account_id&symbol: live bid/ask for the trade ticket.
    Null bid/ask before the first tick; asking subscribes the symbol."""

    def _handle(self, request, body):
        account_id = _int_arg(request, b"account_id")
        symbol = _str_arg(request, b"symbol")
        return self.app.get_quote(account_id, symbol)


class TrendbarsResource(_JsonResource):
    """GET /trendbars?account_id&symbol&period&from&to: historical candles."""

    def _handle(self, request, body):
        account_id = _int_arg(request, b"account_id")
        symbol = _str_arg(request, b"symbol")
        period = _str_arg(request, b"period")
        from_ms = _int_arg(request, b"from")
        to_ms = _int_arg(request, b"to")
        return self.app.get_trendbars(account_id, symbol, period, from_ms, to_ms)


class PositionDealsResource(_JsonResource):
    """GET /position-deals?account_id&position_id&from&to: one position's
    full deal lifecycle."""

    def _handle(self, request, body):
        account_id = _int_arg(request, b"account_id")
        position_id = _int_arg(request, b"position_id")
        from_ms = _int_arg(request, b"from")
        to_ms = _int_arg(request, b"to")
        return self.app.get_position_deals(account_id, position_id, from_ms, to_ms)


class AnalyticsResource(_JsonResource):
    """GET /analytics?account_id&weeks: performance aggregation over the
    last N weeks of deal history (default 4, capped in the app)."""

    def _handle(self, request, body):
        account_id = _int_arg(request, b"account_id")
        weeks = _int_arg(request, b"weeks", default=4)
        return self.app.get_analytics(account_id, weeks)


class HistoryResource(resource.Resource):
    """Parent resource for /history/{deals,orders,cashflow}."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.putChild(b"deals", DealHistoryResource(app))
        self.putChild(b"orders", OrderHistoryResource(app))
        self.putChild(b"cashflow", CashFlowHistoryResource(app))


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
        self.putChild(b"ticks", TicksResource(app))
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
        self.putChild(b"margin-estimate", MarginEstimateResource(app))
        self.putChild(b"quote", QuoteResource(app))
        self.putChild(b"trendbars", TrendbarsResource(app))
        self.putChild(b"position-deals", PositionDealsResource(app))
        self.putChild(b"analytics", AnalyticsResource(app))
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
