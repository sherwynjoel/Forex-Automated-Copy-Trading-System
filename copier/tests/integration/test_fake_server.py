"""Integration tests for the fake cTrader protobuf server.

These drive the server exclusively through the real ctrader_open_api SDK
Client over TLS, proving wire compatibility. Trade requests (new order,
close, amend SL/TP, amend order, cancel order) are fire-and-forget: the real
cTrader server never sends a synchronous, clientMsgId-tagged reply to them,
so we never `yield` on `sdk.send()` for those. Instead we attach an errback
to swallow the eventual (correct) timeout, and drive assertions from
`sdk.setMessageReceivedCallback`, waiting on a Deferred fired by the
callback when the messages we care about have arrived.
"""

import pytest
import pytest_twisted
from ctrader_open_api import Client, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoMessage
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq,
    ProtoOAAmendOrderReq,
    ProtoOAAmendPositionSLTPReq,
    ProtoOAApplicationAuthReq,
    ProtoOAApplicationAuthRes,
    ProtoOACancelOrderReq,
    ProtoOAClosePositionReq,
    ProtoOAErrorRes,
    ProtoOAGetAccountListByAccessTokenReq,
    ProtoOANewOrderReq,
    ProtoOARefreshTokenReq,
    ProtoOAReconcileReq,
    ProtoOASpotEvent,
    ProtoOASubscribeSpotsReq,
    ProtoOASymbolByIdReq,
    ProtoOASymbolsListReq,
    ProtoOATraderReq,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType,
    ProtoOAOrderType,
    ProtoOATradeSide,
)
from twisted.internet import defer, reactor

from copier.testing.fake_server import FakeCTraderServer

ACCOUNT_ID = 1001
ACCESS_TOKEN = "tok-1001"


@pytest.fixture
def server():
    srv = FakeCTraderServer(auto_fill=True)
    srv.accounts = {ACCOUNT_ID: ACCESS_TOKEN}
    port = srv.listen(reactor)
    yield srv, port
    srv.shutdown()


class _Recorder:
    """Captures every message the SDK client receives, tagged or not.

    Lets a test wait for a predicate over the accumulated message list to
    become true, resolved from inside the message-received callback rather
    than by yielding on any particular `sdk.send()` Deferred.
    """

    def __init__(self):
        self.messages: list[tuple[int, str, object]] = []  # (payloadType, clientMsgId, extracted)
        self._waiter = None  # (predicate, Deferred)

    def __call__(self, _client, message: ProtoMessage):
        extracted = Protobuf.extract(message)
        self.messages.append((message.payloadType, message.clientMsgId, extracted))
        if self._waiter is not None:
            predicate, d = self._waiter
            if predicate(self.messages):
                self._waiter = None
                if not d.called:
                    d.callback(self.messages)

    def wait_until(self, predicate, timeout=5.0):
        if predicate(self.messages):
            return defer.succeed(self.messages)
        d = defer.Deferred()
        self._waiter = (predicate, d)
        d.addTimeout(timeout, reactor)
        return d

    def tagged(self, client_msg_id):
        return [m for m in self.messages if m[1] == client_msg_id]

    def of_type(self, proto_cls):
        return [m for m in self.messages if isinstance(m[2], proto_cls)]


def _fire_and_forget(sdk, message, client_msg_id):
    """Send a trade request without waiting on its Deferred.

    The real server sends nothing synchronous for trade requests, so the
    SDK's response Deferred will time out after 5s. That is correct
    behavior we must not treat as a test failure; swallow it.
    """
    d = sdk.send(message, clientMsgId=client_msg_id)
    d.addErrback(lambda failure: None)
    return d


@defer.inlineCallbacks
def _connect_and_auth(port, account_id=ACCOUNT_ID, token=ACCESS_TOKEN):
    sdk = Client("127.0.0.1", port, TcpProtocol)
    recorder = _Recorder()
    sdk.setMessageReceivedCallback(recorder)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    app_auth = ProtoOAApplicationAuthReq()
    app_auth.clientId, app_auth.clientSecret = "cid", "csecret"
    yield sdk.send(app_auth)

    acc_auth = ProtoOAAccountAuthReq()
    acc_auth.ctidTraderAccountId, acc_auth.accessToken = account_id, token
    yield sdk.send(acc_auth)

    return sdk, recorder


@pytest_twisted.inlineCallbacks
def test_sdk_client_can_auth_and_list_symbols(server):
    """App/account auth handshake and symbol wire-compat, including scripted lotSize."""
    srv, port = server
    sdk = Client("127.0.0.1", port, TcpProtocol)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    req = ProtoOAApplicationAuthReq()
    req.clientId, req.clientSecret = "cid", "csecret"
    res = yield sdk.send(req)
    assert isinstance(Protobuf.extract(res), ProtoOAApplicationAuthRes)
    assert srv.app_auths == [("cid", "csecret")]

    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId, auth.accessToken = ACCOUNT_ID, ACCESS_TOKEN
    yield sdk.send(auth)
    assert srv.account_auths == [ACCOUNT_ID]

    syms = ProtoOASymbolsListReq()
    syms.ctidTraderAccountId = ACCOUNT_ID
    light = Protobuf.extract((yield sdk.send(syms)))
    assert [s.symbolName for s in light.symbol] == ["EURUSD"]

    by_id = ProtoOASymbolByIdReq()
    by_id.ctidTraderAccountId = ACCOUNT_ID
    by_id.symbolId.append(light.symbol[0].symbolId)
    full = Protobuf.extract((yield sdk.send(by_id)))
    assert full.symbol[0].lotSize == 10_000_000

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_market_order_auto_fill_broadcasts_untagged_events(server):
    """CRITICAL: market order outcomes arrive only as untagged broadcasts.

    No synchronous tagged reply is ever sent for the trade request; the SDK
    must learn the outcome purely from ORDER_ACCEPTED / ORDER_FILLED
    broadcast events, both carrying an empty clientMsgId.
    """
    srv, port = server
    sdk, recorder = yield _connect_and_auth(port)

    order_req = ProtoOANewOrderReq()
    order_req.ctidTraderAccountId = ACCOUNT_ID
    order_req.symbolId = 1
    order_req.orderType = ProtoOAOrderType.MARKET
    order_req.tradeSide = ProtoOATradeSide.BUY
    order_req.volume = 100_000
    order_req.clientOrderId = "market-1"
    order_req.label = "test-label"

    req_client_msg_id = "market-req-1"
    _fire_and_forget(sdk, order_req, req_client_msg_id)

    def has_filled(messages):
        return any(getattr(m[2], "executionType", None) == ProtoOAExecutionType.ORDER_FILLED for m in messages)

    yield recorder.wait_until(has_filled)

    # Per-field recorded request assertions.
    assert len(srv.requests) == 1
    recorded = srv.requests[0]
    assert recorded.symbolId == 1
    assert recorded.volume == 100_000
    assert recorded.tradeSide == ProtoOATradeSide.BUY
    assert recorded.label == "test-label"

    exec_events = [m for m in recorder.messages if getattr(m[2], "executionType", None) is not None]
    accepted = [m for m in exec_events if m[2].executionType == ProtoOAExecutionType.ORDER_ACCEPTED]
    filled = [m for m in exec_events if m[2].executionType == ProtoOAExecutionType.ORDER_FILLED]
    assert len(accepted) == 1
    assert len(filled) == 1

    accepted_payload_type, accepted_client_id, accepted_evt = accepted[0]
    filled_payload_type, filled_client_id, filled_evt = filled[0]

    # Both broadcasts must be UNTAGGED.
    assert accepted_client_id == ""
    assert filled_client_id == ""

    # Coherent identifiers across the ACCEPTED/FILLED pair.
    assert accepted_evt.order.orderId == filled_evt.order.orderId == filled_evt.deal.orderId
    assert filled_evt.deal.positionId == filled_evt.position.positionId
    assert filled_evt.deal.filledVolume == order_req.volume

    # CRITICAL: no message ever tagged with the request's own clientMsgId.
    assert recorder.tagged(req_client_msg_id) == []

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_close_position_fill_matches_requested_volume(server):
    """Close position: fill broadcast's closePositionDetail.closedVolume == requested volume."""
    srv, port = server
    sdk, recorder = yield _connect_and_auth(port)

    close_req = ProtoOAClosePositionReq()
    close_req.ctidTraderAccountId = ACCOUNT_ID
    close_req.positionId = 5000
    close_req.volume = 100_000

    req_client_msg_id = "close-req-1"
    _fire_and_forget(sdk, close_req, req_client_msg_id)

    def has_filled(messages):
        return any(getattr(m[2], "executionType", None) == ProtoOAExecutionType.ORDER_FILLED for m in messages)

    yield recorder.wait_until(has_filled)

    assert len(srv.requests) == 1
    assert srv.requests[0].positionId == 5000
    assert srv.requests[0].volume == 100_000

    filled = [m for m in recorder.messages if getattr(m[2], "executionType", None) == ProtoOAExecutionType.ORDER_FILLED]
    assert len(filled) == 1
    _, client_id, evt = filled[0]

    assert client_id == ""  # untagged
    assert evt.deal.positionId == 5000
    assert evt.deal.HasField("closePositionDetail")
    assert evt.deal.closePositionDetail.closedVolume == close_req.volume
    # Other REQUIRED closePositionDetail fields present (non-default sentinel values).
    assert evt.deal.closePositionDetail.entryPrice
    assert evt.deal.closePositionDetail.grossProfit
    assert evt.deal.closePositionDetail.balance

    assert recorder.tagged(req_client_msg_id) == []

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_amend_position_sltp_broadcasts_untagged_event(server):
    """Amend SL/TP: outcome arrives as untagged broadcast, not tagged reply."""
    srv, port = server
    sdk, recorder = yield _connect_and_auth(port)

    amend_req = ProtoOAAmendPositionSLTPReq()
    amend_req.ctidTraderAccountId = ACCOUNT_ID
    amend_req.positionId = 5000
    amend_req.stopLoss = 1.0500
    amend_req.takeProfit = 1.1000

    req_client_msg_id = "amend-sltp-req-1"
    _fire_and_forget(sdk, amend_req, req_client_msg_id)

    def has_broadcast(messages):
        return any(getattr(m[2], "executionType", None) is not None for m in messages)

    yield recorder.wait_until(has_broadcast)

    assert len(srv.requests) == 1
    assert srv.requests[0].positionId == 5000
    assert srv.requests[0].stopLoss == pytest.approx(1.0500)
    assert srv.requests[0].takeProfit == pytest.approx(1.1000)

    evts = [m for m in recorder.messages if getattr(m[2], "executionType", None) is not None]
    assert len(evts) == 1
    _, client_id, evt = evts[0]
    assert client_id == ""  # untagged
    assert evt.position.positionId == 5000
    assert evt.position.stopLoss == pytest.approx(1.0500)
    assert evt.position.takeProfit == pytest.approx(1.1000)

    assert recorder.tagged(req_client_msg_id) == []

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_amend_order_broadcasts_untagged_event(server):
    """Amend order: outcome arrives as untagged broadcast."""
    srv, port = server
    sdk, recorder = yield _connect_and_auth(port)

    amend_req = ProtoOAAmendOrderReq()
    amend_req.ctidTraderAccountId = ACCOUNT_ID
    amend_req.orderId = 9000
    amend_req.limitPrice = 1.0800

    req_client_msg_id = "amend-order-req-1"
    _fire_and_forget(sdk, amend_req, req_client_msg_id)

    def has_broadcast(messages):
        return any(getattr(m[2], "executionType", None) is not None for m in messages)

    yield recorder.wait_until(has_broadcast)

    assert len(srv.requests) == 1
    assert srv.requests[0].orderId == 9000

    evts = [m for m in recorder.messages if getattr(m[2], "executionType", None) is not None]
    assert len(evts) == 1
    _, client_id, evt = evts[0]
    assert client_id == ""
    assert evt.order.orderId == 9000

    assert recorder.tagged(req_client_msg_id) == []

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_cancel_order_broadcasts_untagged_event(server):
    """Cancel order: outcome arrives as untagged broadcast."""
    srv, port = server
    sdk, recorder = yield _connect_and_auth(port)

    cancel_req = ProtoOACancelOrderReq()
    cancel_req.ctidTraderAccountId = ACCOUNT_ID
    cancel_req.orderId = 9000

    req_client_msg_id = "cancel-req-1"
    _fire_and_forget(sdk, cancel_req, req_client_msg_id)

    def has_broadcast(messages):
        return any(getattr(m[2], "executionType", None) is not None for m in messages)

    yield recorder.wait_until(has_broadcast)

    assert len(srv.requests) == 1
    assert srv.requests[0].orderId == 9000

    evts = [m for m in recorder.messages if getattr(m[2], "executionType", None) is not None]
    assert len(evts) == 1
    _, client_id, evt = evts[0]
    assert client_id == ""
    assert evt.executionType == ProtoOAExecutionType.ORDER_CANCELLED
    assert evt.order.orderId == 9000

    assert recorder.tagged(req_client_msg_id) == []

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_scripted_rejection_broadcasts_before_any_fill(server):
    """reject_next_order: ORDER_REJECTED broadcast observed with errorCode,
    and no ACCEPTED/FILLED events precede it for that order."""
    srv, port = server
    srv.reject_next_order = True

    sdk, recorder = yield _connect_and_auth(port)

    order_req = ProtoOANewOrderReq()
    order_req.ctidTraderAccountId = ACCOUNT_ID
    order_req.symbolId = 1
    order_req.orderType = ProtoOAOrderType.MARKET
    order_req.tradeSide = ProtoOATradeSide.BUY
    order_req.volume = 100_000
    order_req.clientOrderId = "reject-1"

    req_client_msg_id = "reject-req-1"
    _fire_and_forget(sdk, order_req, req_client_msg_id)

    def has_rejected(messages):
        return any(getattr(m[2], "executionType", None) == ProtoOAExecutionType.ORDER_REJECTED for m in messages)

    yield recorder.wait_until(has_rejected)

    exec_events = [m for m in recorder.messages if getattr(m[2], "executionType", None) is not None]
    # No ACCEPTED/FILLED event for this order preceded (or ever accompanied) the rejection.
    assert not any(
        m[2].executionType in (ProtoOAExecutionType.ORDER_ACCEPTED, ProtoOAExecutionType.ORDER_FILLED)
        for m in exec_events
    )

    rejected = [m for m in exec_events if m[2].executionType == ProtoOAExecutionType.ORDER_REJECTED]
    assert len(rejected) == 1
    _, client_id, evt = rejected[0]
    assert client_id == ""  # untagged
    assert evt.errorCode  # non-empty scripted errorCode
    assert evt.order.clientOrderId == "reject-1"

    assert recorder.tagged(req_client_msg_id) == []

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_bad_access_token_returns_tagged_error(server):
    """Bad access token on account auth: legit synchronous TAGGED ProtoOAErrorRes."""
    srv, port = server
    sdk = Client("127.0.0.1", port, TcpProtocol)
    recorder = _Recorder()
    sdk.setMessageReceivedCallback(recorder)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    app_auth = ProtoOAApplicationAuthReq()
    app_auth.clientId, app_auth.clientSecret = "cid", "csecret"
    yield sdk.send(app_auth)

    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId = ACCOUNT_ID
    auth.accessToken = "WRONG"

    bad_auth_client_msg_id = "bad-auth-1"
    res = yield sdk.send(auth, clientMsgId=bad_auth_client_msg_id)
    extracted = Protobuf.extract(res)
    assert isinstance(extracted, ProtoOAErrorRes)
    assert extracted.errorCode

    # The error truly arrived tagged with this request's clientMsgId.
    tagged = recorder.tagged(bad_auth_client_msg_id)
    assert len(tagged) == 1
    assert isinstance(tagged[0][2], ProtoOAErrorRes)

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_reconcile_and_account_list_and_subscribe_spots_serialize(server):
    """Non-trade RPCs stay tagged and serialize cleanly (broader wire coverage)."""
    srv, port = server
    srv.open_positions = {
        ACCOUNT_ID: [
            {"position_id": 5001, "symbol_id": 1, "volume": 100_000, "trade_side": ProtoOATradeSide.BUY, "price": 1.1}
        ]
    }
    srv.pending_orders = {
        ACCOUNT_ID: [
            {
                "order_id": 9001,
                "symbol_id": 1,
                "volume": 100_000,
                "trade_side": ProtoOATradeSide.SELL,
                "order_type": ProtoOAOrderType.LIMIT,
            }
        ]
    }

    sdk, recorder = yield _connect_and_auth(port)

    reconcile_req = ProtoOAReconcileReq()
    reconcile_req.ctidTraderAccountId = ACCOUNT_ID
    res = Protobuf.extract((yield sdk.send(reconcile_req)))
    assert [p.positionId for p in res.position] == [5001]
    assert [o.orderId for o in res.order] == [9001]

    acct_list_req = ProtoOAGetAccountListByAccessTokenReq()
    acct_list_req.accessToken = ACCESS_TOKEN
    res = Protobuf.extract((yield sdk.send(acct_list_req)))
    assert ACCOUNT_ID in [a.ctidTraderAccountId for a in res.ctidTraderAccount]

    sub_req = ProtoOASubscribeSpotsReq()
    sub_req.ctidTraderAccountId = ACCOUNT_ID
    sub_req.symbolId.append(1)
    res = Protobuf.extract((yield sdk.send(sub_req)))
    assert res.ctidTraderAccountId == ACCOUNT_ID

    # push_spot / ProtoOASpotEvent serialization coverage (untagged broadcast).
    def has_spot(messages):
        return any(isinstance(m[2], ProtoOASpotEvent) for m in messages)

    srv.push_spot(ACCOUNT_ID, 1, 10500, 10502)
    yield recorder.wait_until(has_spot)

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_refresh_token_response_has_required_fields(server):
    srv, port = server
    sdk, recorder = yield _connect_and_auth(port)

    refresh_req = ProtoOARefreshTokenReq()
    refresh_req.refreshToken = "old"

    res = Protobuf.extract((yield sdk.send(refresh_req)))
    assert res.accessToken
    assert res.tokenType == "Bearer"
    assert res.expiresIn == 3600

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_trader_response_has_required_fields(server):
    srv, port = server
    sdk, recorder = yield _connect_and_auth(port)

    trader_req = ProtoOATraderReq()
    trader_req.ctidTraderAccountId = ACCOUNT_ID

    res = Protobuf.extract((yield sdk.send(trader_req)))
    assert res.trader.ctidTraderAccountId == ACCOUNT_ID
    assert res.trader.depositAssetId == 1

    yield sdk.stopService()
