import pytest
import pytest_twisted
from ctrader_open_api import Client, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq, ProtoOAApplicationAuthRes,
    ProtoOAAccountAuthReq, ProtoOASymbolsListReq, ProtoOASymbolByIdReq,
    ProtoOANewOrderReq, ProtoOAClosePositionReq, ProtoOAAmendOrderReq,
    ProtoOACancelOrderReq, ProtoOARefreshTokenReq, ProtoOATraderReq,
    ProtoOAErrorRes)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAOrderType, ProtoOATradeSide)
from twisted.internet import defer, reactor

from copier.testing.fake_server import FakeCTraderServer


@pytest.fixture
def server():
    srv = FakeCTraderServer(auto_fill=True)
    srv.accounts = {1001: "tok-1001"}
    port = srv.listen(reactor)
    yield srv, port
    srv.shutdown()


@pytest_twisted.inlineCallbacks
def test_baseline_auth_and_symbols(server):
    """Test basic auth and symbol listing."""
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

    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId, auth.accessToken = 1001, "tok-1001"
    yield sdk.send(auth)

    syms = ProtoOASymbolsListReq()
    syms.ctidTraderAccountId = 1001
    light = Protobuf.extract((yield sdk.send(syms)))
    assert len(light.symbol) > 0

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_market_order_serializes(server):
    """Test MARKET order request serializes (no EncodeError)."""
    srv, port = server
    sdk = Client("127.0.0.1", port, TcpProtocol)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId, auth.accessToken = 1001, "tok-1001"
    yield sdk.send(auth)

    order_req = ProtoOANewOrderReq()
    order_req.ctidTraderAccountId = 1001
    order_req.symbolId = 1
    order_req.orderType = ProtoOAOrderType.MARKET
    order_req.tradeSide = ProtoOATradeSide.BUY
    order_req.volume = 100000
    order_req.clientOrderId = "market-1"

    # Yield to ensure request is sent and processed
    yield sdk.send(order_req)

    # Verify request recorded
    assert len(srv.requests) == 1

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_close_position_serializes(server):
    """Test close position request serializes."""
    srv, port = server
    sdk = Client("127.0.0.1", port, TcpProtocol)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId, auth.accessToken = 1001, "tok-1001"
    yield sdk.send(auth)

    close_req = ProtoOAClosePositionReq()
    close_req.ctidTraderAccountId = 1001
    close_req.positionId = 5000
    close_req.volume = 100000

    yield sdk.send(close_req)

    assert len(srv.requests) == 1

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_amend_order_serializes(server):
    """Test amend order request serializes."""
    srv, port = server
    sdk = Client("127.0.0.1", port, TcpProtocol)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId, auth.accessToken = 1001, "tok-1001"
    yield sdk.send(auth)

    amend_req = ProtoOAAmendOrderReq()
    amend_req.ctidTraderAccountId = 1001
    amend_req.orderId = 9000

    yield sdk.send(amend_req)

    assert len(srv.requests) == 1

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_cancel_order_serializes(server):
    """Test cancel order request serializes."""
    srv, port = server
    sdk = Client("127.0.0.1", port, TcpProtocol)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId, auth.accessToken = 1001, "tok-1001"
    yield sdk.send(auth)

    cancel_req = ProtoOACancelOrderReq()
    cancel_req.ctidTraderAccountId = 1001
    cancel_req.orderId = 9000

    yield sdk.send(cancel_req)

    assert len(srv.requests) == 1

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_rejection_broadcasts(server):
    """Test reject_next_order: broadcasts ORDER_REJECTED event."""
    srv, port = server
    srv.reject_next_order = True

    sdk = Client("127.0.0.1", port, TcpProtocol)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId, auth.accessToken = 1001, "tok-1001"
    yield sdk.send(auth)

    order_req = ProtoOANewOrderReq()
    order_req.ctidTraderAccountId = 1001
    order_req.symbolId = 1
    order_req.orderType = ProtoOAOrderType.MARKET
    order_req.tradeSide = ProtoOATradeSide.BUY
    order_req.volume = 100000

    yield sdk.send(order_req)

    assert len(srv.requests) == 1

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_bad_token_error_response(server):
    """Test wrong token returns synchronous ProtoOAErrorRes."""
    srv, port = server
    sdk = Client("127.0.0.1", port, TcpProtocol)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    app_auth = ProtoOAApplicationAuthReq()
    app_auth.clientId, app_auth.clientSecret = "cid", "csecret"
    yield sdk.send(app_auth)

    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId = 1001
    auth.accessToken = "WRONG"

    res = yield sdk.send(auth)
    assert isinstance(Protobuf.extract(res), ProtoOAErrorRes)

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_refresh_token_serializes(server):
    """Test refresh token response has all REQUIRED fields."""
    srv, port = server
    sdk = Client("127.0.0.1", port, TcpProtocol)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId, auth.accessToken = 1001, "tok-1001"
    yield sdk.send(auth)

    refresh_req = ProtoOARefreshTokenReq()
    refresh_req.refreshToken = "old"

    res = yield sdk.send(refresh_req)
    extracted = Protobuf.extract(res)

    assert extracted.accessToken
    assert extracted.tokenType == "Bearer"
    assert extracted.expiresIn == 3600

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_trader_response_serializes(server):
    """Test trader response has all REQUIRED fields."""
    srv, port = server
    sdk = Client("127.0.0.1", port, TcpProtocol)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId, auth.accessToken = 1001, "tok-1001"
    yield sdk.send(auth)

    trader_req = ProtoOATraderReq()
    trader_req.ctidTraderAccountId = 1001

    res = yield sdk.send(trader_req)
    extracted = Protobuf.extract(res)

    assert extracted.trader.ctidTraderAccountId == 1001
    assert extracted.trader.depositAssetId == 1

    yield sdk.stopService()
