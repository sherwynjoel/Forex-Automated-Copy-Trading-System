"""Standalone process entry point for the compose-level end-to-end test.

Runs two things in one reactor, inside the `fake-ctrader` compose service
(see docker-compose.test.yml):

1. `FakeCTraderServer` (see fake_server.py) listening with TLS on the real
   cTrader port (5035) -- the copier container connects to this exactly as
   it would to demo.ctraderapi.com (docker-compose.test.yml points
   CTRADER_DEMO_HOST at this service's hostname). `accounts` is left empty
   so account-auth accepts ANY (account_id, token) pair -- see
   FakeCTraderServer._handle_account_auth_req -- matching the brief's
   "accepts any token" and sidestepping the need to thread the e2e test's
   seeded tokens into this separate container's process.

2. A tiny HTTP "scenario-control" site on port 9000 (published to the host
   by docker-compose.test.yml) that the e2e tests drive directly:
   POST /fill, /close, /place-limit, /reset. These do NOT go through
   FakeCTraderServer's normal request handlers (_handle_new_order_req etc,
   which react to a ProtoOA*Req arriving FROM a connected client) -- a
   trade on the MASTER account is never actually requested by the copier
   (the copier only ever sends requests to SLAVE accounts; the master's
   activity happens on the master's own broker-side account and reaches
   the copier purely as pushed, untagged execution-event broadcasts). So
   the scenario-control handlers below construct and broadcast those
   ProtoOAExecutionEvents directly, mirroring the shape
   FakeCTraderServer's own handlers already produce for the analogous
   slave-side wire traffic (same field set, same "no tagged reply, only an
   untagged broadcast" contract as the real server).

Entry: `python -m copier.testing.fake_main`.
"""

import json
import logging
import time
from typing import Any

from ctrader_open_api.messages import OpenApiMessages_pb2 as oa
from ctrader_open_api.messages import OpenApiModelMessages_pb2 as model
from twisted.internet import reactor
from twisted.web import resource, server

from copier.testing.fake_server import FakeCTraderServer

log = logging.getLogger(__name__)

CTRADER_PORT = 5035
SCENARIO_CONTROL_PORT = 9000
# Container-internal bind; host exposure is docker-compose.test.yml's job
# (publishes 127.0.0.1:9000:9000), same convention as copier's own control
# port (see main.CONTROL_BIND_INTERFACE).
BIND_INTERFACE = "0.0.0.0"

# The fake server's only symbol (see FakeCTraderServer.__init__): EURUSD,
# lotSize 10_000_000 -- i.e. protocol volume = lots * 10_000_000.
SYMBOL_IDS_BY_NAME = {"EURUSD": 1}
LOT_SIZE = 10_000_000

SIDES_BY_NAME = {"BUY": model.ProtoOATradeSide.BUY, "SELL": model.ProtoOATradeSide.SELL}


def _lots_to_volume(volume_lots) -> int:
    """volume_lots arrives as a JSON string (e.g. "1.00") or number; protocol
    volume = lots * lotSize (see domain/sizing.py:lots_to_protocol_volume,
    which this mirrors for the one symbol the fake server knows)."""
    return int(round(float(volume_lots) * LOT_SIZE))


# ---------- scenario-side event construction (mirrors fake_server.py's own
# handlers; kept here rather than added to FakeCTraderServer because these
# are triggered by an HTTP scenario command, not a wire request) ----------

def _push_market_fill(fake: FakeCTraderServer, account_id: int, symbol_id: int,
                       side: int, volume: int, label: str = "") -> dict[str, int]:
    """Broadcast ORDER_ACCEPTED then ORDER_FILLED for a MARKET buy/sell that
    never arrived as a ProtoOANewOrderReq -- simulates the MASTER's own fill.
    Uses the server's own id counters/_position_volumes tracking (shared
    itertools.count sequences with every wire-triggered handler, so ids
    never collide with a real request-triggered fill) so a later /close call
    against the returned position_id gets correct partial-close semantics
    exactly like _handle_close_position_req's.

    Booking goes through FakeCTraderServer.register_market_fill, so a
    SECOND /fill on the same account+symbol+side is a position INCREASE
    (same position_id, delta volume) exactly as real cTrader aggregates it
    -- which is what lets e2e/test_full_stack.py drive the master-increase
    scenario at all."""
    order_id = next(fake._order_ids)

    accept_evt = oa.ProtoOAExecutionEvent()
    accept_evt.ctidTraderAccountId = account_id
    accept_evt.executionType = model.ProtoOAExecutionType.ORDER_ACCEPTED
    accept_evt.order.orderId = order_id
    accept_evt.order.orderStatus = model.ProtoOAOrderStatus.ORDER_STATUS_ACCEPTED
    accept_evt.order.orderType = model.ProtoOAOrderType.MARKET
    accept_evt.order.tradeData.symbolId = symbol_id
    accept_evt.order.tradeData.volume = volume
    accept_evt.order.tradeData.tradeSide = side
    accept_evt.order.tradeData.label = label
    fake.broadcast(accept_evt)

    position_id, position_total = fake.register_market_fill(
        account_id, symbol_id, side, volume, label,
    )
    deal_id = next(fake._deal_ids)

    fill_evt = oa.ProtoOAExecutionEvent()
    fill_evt.ctidTraderAccountId = account_id
    fill_evt.executionType = model.ProtoOAExecutionType.ORDER_FILLED
    fill_evt.deal.dealId = deal_id
    fill_evt.deal.orderId = order_id
    fill_evt.deal.positionId = position_id
    fill_evt.deal.volume = volume
    fill_evt.deal.filledVolume = volume
    fill_evt.deal.symbolId = symbol_id
    fill_evt.deal.tradeSide = side
    fill_evt.deal.dealStatus = model.ProtoOADealStatus.FILLED
    fill_evt.deal.createTimestamp = int(time.time() * 1000)
    fill_evt.deal.executionTimestamp = int(time.time() * 1000)
    # Real cTrader always reports the price a deal filled at (T9c).
    fill_evt.deal.executionPrice = fake.execution_price
    fill_evt.position.positionId = position_id
    fill_evt.position.tradeData.symbolId = symbol_id
    # TOTAL after this fill (deal.filledVolume above stays the delta).
    fill_evt.position.tradeData.volume = position_total
    fill_evt.position.tradeData.tradeSide = side
    fill_evt.position.tradeData.label = label
    fill_evt.position.positionStatus = model.ProtoOAPositionStatus.POSITION_STATUS_OPEN
    fill_evt.position.swap = 0
    fill_evt.order.orderId = order_id
    fill_evt.order.orderStatus = model.ProtoOAOrderStatus.ORDER_STATUS_FILLED
    fill_evt.order.orderType = model.ProtoOAOrderType.MARKET
    fill_evt.order.tradeData.symbolId = symbol_id
    fill_evt.order.tradeData.volume = volume
    fill_evt.order.tradeData.tradeSide = side
    fill_evt.order.tradeData.label = label
    fake.broadcast(fill_evt)

    # NB: register_market_fill above already recorded this in
    # fake.open_positions for _handle_reconcile_req -- without that, a
    # resync() run against the MASTER account after this fill would see it
    # as having zero open positions, so the master position would never show
    # up in CopierApp.get_state()'s master_positions (and every slave copy
    # would misreport as missing_slave_copy drift once the master position
    # itself is invisible to compute_drift).

    return {
        "order_id": order_id,
        "position_id": position_id,
        "deal_id": deal_id,
        "position_volume": position_total,
    }


def _push_close(fake: FakeCTraderServer, account_id: int, position_id: int, volume: int,
                 symbol_id: int = 1, side: int = model.ProtoOATradeSide.BUY) -> dict[str, Any]:
    """Broadcast an ORDER_FILLED with closePositionDetail for `position_id`,
    exactly mirroring _handle_close_position_req (including its remaining-
    volume tracking so a partial close correctly leaves the position OPEN)."""
    current_volume = fake._position_volumes.get(position_id, volume)
    remaining_volume = max(current_volume - volume, 0)
    fake._position_volumes[position_id] = remaining_volume
    is_full_close = remaining_volume == 0

    deal_id = next(fake._deal_ids)
    fill_evt = oa.ProtoOAExecutionEvent()
    fill_evt.ctidTraderAccountId = account_id
    fill_evt.executionType = model.ProtoOAExecutionType.ORDER_FILLED
    fill_evt.deal.dealId = deal_id
    fill_evt.deal.orderId = 0  # no order backs a close (see fake_server.py)
    fill_evt.deal.positionId = position_id
    fill_evt.deal.volume = volume
    fill_evt.deal.filledVolume = volume
    fill_evt.deal.symbolId = symbol_id
    fill_evt.deal.tradeSide = side
    fill_evt.deal.dealStatus = model.ProtoOADealStatus.FILLED
    fill_evt.deal.createTimestamp = int(time.time() * 1000)
    fill_evt.deal.executionTimestamp = int(time.time() * 1000)
    fill_evt.deal.executionPrice = fake.execution_price
    fill_evt.deal.closePositionDetail.closedVolume = volume
    fill_evt.deal.closePositionDetail.entryPrice = 10000
    fill_evt.deal.closePositionDetail.grossProfit = 1000
    fill_evt.deal.closePositionDetail.swap = 0
    fill_evt.deal.closePositionDetail.commission = 5
    fill_evt.deal.closePositionDetail.balance = 100000
    fill_evt.position.positionId = position_id
    fill_evt.position.tradeData.symbolId = symbol_id
    fill_evt.position.tradeData.volume = remaining_volume
    fill_evt.position.tradeData.tradeSide = side
    fill_evt.position.positionStatus = (
        model.ProtoOAPositionStatus.POSITION_STATUS_CLOSED if is_full_close
        else model.ProtoOAPositionStatus.POSITION_STATUS_OPEN
    )
    fill_evt.position.swap = 0
    fill_evt.order.orderId = 0
    fill_evt.order.orderStatus = model.ProtoOAOrderStatus.ORDER_STATUS_FILLED
    fill_evt.order.orderType = model.ProtoOAOrderType.MARKET
    fill_evt.order.tradeData.symbolId = symbol_id
    fill_evt.order.tradeData.volume = volume
    fill_evt.order.tradeData.tradeSide = side
    fake.broadcast(fill_evt)

    # Keep open_positions in sync (see _push_market_fill and
    # fake_server.py's _handle_close_position_req, which this mirrors):
    # drop the position on a full close, shrink it on a partial one.
    existing = next(
        (p for p in fake.open_positions.get(account_id, []) if p["position_id"] == position_id),
        None,
    )
    remaining = [p for p in fake.open_positions.get(account_id, []) if p["position_id"] != position_id]
    if not is_full_close:
        updated = dict(existing) if existing is not None else {
            "position_id": position_id, "symbol_id": symbol_id, "trade_side": side, "label": "",
        }
        updated["volume"] = remaining_volume
        remaining.append(updated)
    fake.open_positions[account_id] = remaining

    return {"position_id": position_id, "remaining_volume": remaining_volume, "closed": is_full_close}


def _push_limit_placed(fake: FakeCTraderServer, account_id: int, symbol_id: int,
                        side: int, volume: int, price: float, label: str = "") -> dict[str, int]:
    """Broadcast ORDER_ACCEPTED for a LIMIT order that never arrived as a
    ProtoOANewOrderReq -- simulates the MASTER placing a pending order."""
    order_id = next(fake._order_ids)
    accept_evt = oa.ProtoOAExecutionEvent()
    accept_evt.ctidTraderAccountId = account_id
    accept_evt.executionType = model.ProtoOAExecutionType.ORDER_ACCEPTED
    accept_evt.order.orderId = order_id
    accept_evt.order.orderStatus = model.ProtoOAOrderStatus.ORDER_STATUS_ACCEPTED
    accept_evt.order.orderType = model.ProtoOAOrderType.LIMIT
    accept_evt.order.tradeData.symbolId = symbol_id
    accept_evt.order.tradeData.volume = volume
    accept_evt.order.tradeData.tradeSide = side
    accept_evt.order.tradeData.label = label
    accept_evt.order.limitPrice = price
    fake.broadcast(accept_evt)
    return {"order_id": order_id}


# ---------- tiny JSON HTTP scenario-control site ----------

def _write_json(request, payload: dict, code: int = 200) -> None:
    request.setResponseCode(code)
    request.setHeader(b"Content-Type", b"application/json")
    request.write(json.dumps(payload).encode())
    request.finish()


def _read_json(request) -> dict:
    body = request.content.read()
    return json.loads(body) if body else {}


class _ScenarioResource(resource.Resource):
    """Base for /fill, /close, /place-limit: JSON body in, JSON body out,
    errors reported as HTTP 400 rather than raising into Twisted's request
    handling (this is a test double; a clear 400 with the exception message
    is far more useful to a failing e2e test than a generic 500 traceback)."""

    isLeaf = True

    def __init__(self, fake: FakeCTraderServer):
        super().__init__()
        self.fake = fake

    def _handle(self, body: dict) -> dict:
        raise NotImplementedError

    def render_POST(self, request):
        try:
            result = self._handle(_read_json(request))
            _write_json(request, result)
        except Exception as e:
            log.exception("scenario command failed")
            _write_json(request, {"error": str(e)}, code=400)
        return server.NOT_DONE_YET


class FillResource(_ScenarioResource):
    """POST /fill {"account_id": 100, "symbol": "EURUSD", "side": "BUY",
    "volume_lots": "1.00", "label": ""} -> pushes a MARKET ORDER_FILLED
    execution event for that account."""

    def _handle(self, body: dict) -> dict:
        account_id = int(body["account_id"])
        symbol_id = SYMBOL_IDS_BY_NAME[body.get("symbol", "EURUSD")]
        side = SIDES_BY_NAME[body.get("side", "BUY")]
        volume = _lots_to_volume(body["volume_lots"])
        label = body.get("label", "")
        result = _push_market_fill(self.fake, account_id, symbol_id, side, volume, label)
        return {"status": "filled", **result}


class CloseResource(_ScenarioResource):
    """POST /close {"account_id": 100, "position_id": 5000, "volume_lots":
    "1.00"} -> pushes an ORDER_FILLED (close) execution event for that
    position; a volume_lots less than the position's remaining volume is a
    genuine partial close (position stays open)."""

    def _handle(self, body: dict) -> dict:
        account_id = int(body["account_id"])
        position_id = int(body["position_id"])
        symbol_id = SYMBOL_IDS_BY_NAME[body.get("symbol", "EURUSD")]
        side = SIDES_BY_NAME[body.get("side", "BUY")]
        volume = _lots_to_volume(body["volume_lots"])
        result = _push_close(self.fake, account_id, position_id, volume, symbol_id, side)
        return {"status": "closed", **result}


class PlaceLimitResource(_ScenarioResource):
    """POST /place-limit {"account_id": 100, "symbol": "EURUSD", "side":
    "BUY", "volume_lots": "1.00", "price": 1.05, "label": ""} -> pushes a
    LIMIT ORDER_ACCEPTED execution event for that account."""

    def _handle(self, body: dict) -> dict:
        account_id = int(body["account_id"])
        symbol_id = SYMBOL_IDS_BY_NAME[body.get("symbol", "EURUSD")]
        side = SIDES_BY_NAME[body.get("side", "BUY")]
        volume = _lots_to_volume(body["volume_lots"])
        price = float(body["price"])
        label = body.get("label", "")
        result = _push_limit_placed(self.fake, account_id, symbol_id, side, volume, price, label)
        return {"status": "placed", **result}


class ResetResource(_ScenarioResource):
    """POST /reset {} -> flattens the fake broker's whole book (every
    account's open positions and working orders).

    This container outlives any single e2e test, and the fake MERGES a
    same-side market fill into an account's existing position
    (FakeCTraderServer.register_market_fill), so without this a second run --
    or the second of two tests that share account ids -- inherits the first
    one's volumes. Truncating the database does not help: this state lives
    here, not in Postgres. Connections, account-auth state and the symbol
    table are untouched, so a copier already connected stays connected.
    """

    def _handle(self, body: dict) -> dict:
        self.fake.reset_book()
        return {"status": "reset"}


class HealthResource(resource.Resource):
    """GET /health: trivial liveness probe for compose-level polling."""

    isLeaf = True

    def render_GET(self, request):
        _write_json(request, {"status": "ok"})
        return server.NOT_DONE_YET


def make_scenario_site(fake: FakeCTraderServer) -> server.Site:
    root = resource.Resource()
    root.putChild(b"health", HealthResource())
    root.putChild(b"fill", FillResource(fake))
    root.putChild(b"close", CloseResource(fake))
    root.putChild(b"place-limit", PlaceLimitResource(fake))
    root.putChild(b"reset", ResetResource(fake))
    return server.Site(root)


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    fake = FakeCTraderServer(auto_fill=True)
    # `accounts` stays {} (falsy) -- see _handle_account_auth_req: an empty
    # dict skips BOTH the "unknown account" and "wrong token" checks, so any
    # (account_id, token) pair auths successfully. This is what lets
    # e2e/test_full_stack.py seed arbitrary Fernet-encrypted tokens without
    # this separate container ever needing to know their plaintext value.
    #
    # enforce_auth=True: more faithful to the real server (rejects a trade
    # for an account not yet account-authed on the connection it arrived
    # on). Safe here because the e2e flow's POST /reload synchronously
    # authorizes all three accounts (CopierApp.reload -> _authorize_one,
    # yielded one at a time) before returning to the test -- by the time the
    # test's first POST /fill can possibly land, every account is already
    # confirmed authed on the one shared connection (shards=1, all-demo).
    # There is no reconnect in this flow's timeline for the NEW-1 race
    # (see fake_server.py's enforce_auth docstring) to open a window in.
    fake.enforce_auth = True

    port = fake.listen(reactor, port=CTRADER_PORT)
    log.info("fake cTrader server listening on TLS port %d", port)

    site = make_scenario_site(fake)
    reactor.listenTCP(SCENARIO_CONTROL_PORT, site, interface=BIND_INTERFACE)
    log.info("scenario-control HTTP listening on %s:%d", BIND_INTERFACE, SCENARIO_CONTROL_PORT)

    reactor.run()


if __name__ == "__main__":
    main()
