import pytest
import pytest_twisted
from ctrader_open_api import Client, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq, ProtoOAApplicationAuthRes,
    ProtoOAAccountAuthReq, ProtoOASymbolsListReq, ProtoOASymbolByIdReq,
    ProtoOANewOrderReq, ProtoOAClosePositionReq, ProtoOAAmendPositionSLTPReq,
    ProtoOARefreshTokenReq, ProtoOATraderReq)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAOrderType, ProtoOATradeSide)
from twisted.internet import defer, reactor

from copier.testing.fake_server import FakeCTraderServer


@pytest.fixture
def server():
    srv = FakeCTraderServer(auto_fill=False)
    srv.accounts = {1001: "tok-1001"}
    port = srv.listen(reactor)
    yield srv, port
    srv.shutdown()


@pytest_twisted.inlineCallbacks
def test_sdk_client_can_auth_and_list_symbols(server):
    """Baseline test: auth and list symbols (from brief)."""
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
    auth.ctidTraderAccountId, auth.accessToken = 1001, "tok-1001"
    yield sdk.send(auth)
    assert srv.account_auths == [1001]

    syms = ProtoOASymbolsListReq()
    syms.ctidTraderAccountId = 1001
    light = Protobuf.extract((yield sdk.send(syms)))
    assert [s.symbolName for s in light.symbol] == ["EURUSD"]

    by_id = ProtoOASymbolByIdReq()
    by_id.ctidTraderAccountId = 1001
    by_id.symbolId.append(light.symbol[0].symbolId)
    full = Protobuf.extract((yield sdk.send(by_id)))
    assert full.symbol[0].lotSize == 10_000_000

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_market_order_auto_fill_records_request(server):
    """Test that MARKET orders are recorded by the server."""
    srv, port = server
    sdk = Client("127.0.0.1", port, TcpProtocol)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    # Auth
    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId, auth.accessToken = 1001, "tok-1001"
    yield sdk.send(auth)

    # Create market order request
    order_req = ProtoOANewOrderReq()
    order_req.ctidTraderAccountId = 1001
    order_req.symbolId = 1
    order_req.orderType = ProtoOAOrderType.MARKET
    order_req.tradeSide = ProtoOATradeSide.BUY
    order_req.volume = 100000
    order_req.clientOrderId = "market-1"
    order_req.label = "test-market"

    # Send order (SDK expects response)
    yield sdk.send(order_req)

    # Verify request was recorded
    assert len(srv.requests) == 1
    recorded_req = srv.requests[0]
    assert recorded_req.symbolId == 1
    assert recorded_req.volume == 100000
    assert recorded_req.clientOrderId == "market-1"
    assert recorded_req.orderType == ProtoOAOrderType.MARKET

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_close_position_records_request(server):
    """Test that close position requests are recorded by the server."""
    srv, port = server
    sdk = Client("127.0.0.1", port, TcpProtocol)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    # Auth
    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId, auth.accessToken = 1001, "tok-1001"
    yield sdk.send(auth)

    # Close position request
    close_req = ProtoOAClosePositionReq()
    close_req.ctidTraderAccountId = 1001
    close_req.positionId = 5000
    close_req.volume = 100000

    yield sdk.send(close_req)

    # Verify request was recorded
    assert len(srv.requests) == 1
    recorded_req = srv.requests[0]
    assert recorded_req.positionId == 5000
    assert recorded_req.volume == 100000

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_amend_position_sltp_records_request(server):
    """Test that amend position SL/TP requests are recorded."""
    srv, port = server
    sdk = Client("127.0.0.1", port, TcpProtocol)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    # Auth
    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId, auth.accessToken = 1001, "tok-1001"
    yield sdk.send(auth)

    # Amend position request
    amend_req = ProtoOAAmendPositionSLTPReq()
    amend_req.ctidTraderAccountId = 1001
    amend_req.positionId = 5000
    amend_req.stopLoss = 10050
    amend_req.takeProfit = 10100

    yield sdk.send(amend_req)

    # Verify request was recorded
    assert len(srv.requests) == 1
    recorded_req = srv.requests[0]
    assert recorded_req.positionId == 5000
    assert recorded_req.stopLoss == 10050
    assert recorded_req.takeProfit == 10100

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_refresh_token_response_has_required_fields(server):
    """Test that refresh token response has all required fields for serialization."""
    srv, port = server
    sdk = Client("127.0.0.1", port, TcpProtocol)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    # Auth first
    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId, auth.accessToken = 1001, "tok-1001"
    yield sdk.send(auth)

    # Refresh token request (no ctidTraderAccountId on this message type)
    refresh_req = ProtoOARefreshTokenReq()
    refresh_req.refreshToken = "old-refresh-token"

    res = yield sdk.send(refresh_req)
    extracted = Protobuf.extract(res)

    # Should have all required fields (won't throw EncodeError at serialization)
    assert extracted.accessToken == "new-access-token"
    assert extracted.refreshToken == "new-refresh-token"
    assert extracted.tokenType == "Bearer"
    assert extracted.expiresIn == 3600

    yield sdk.stopService()


@pytest_twisted.inlineCallbacks
def test_trader_response_has_required_fields(server):
    """Test that trader response has all required fields (depositAssetId, balance, etc.)."""
    srv, port = server
    sdk = Client("127.0.0.1", port, TcpProtocol)
    connected = defer.Deferred()
    sdk.setConnectedCallback(lambda c: connected.called or connected.callback(c))
    sdk.startService()
    yield connected

    # Auth
    auth = ProtoOAAccountAuthReq()
    auth.ctidTraderAccountId, auth.accessToken = 1001, "tok-1001"
    yield sdk.send(auth)

    # Trader request
    trader_req = ProtoOATraderReq()
    trader_req.ctidTraderAccountId = 1001

    res = yield sdk.send(trader_req)
    extracted = Protobuf.extract(res)

    # Verify required fields are set (all serialize without EncodeError)
    assert extracted.trader.ctidTraderAccountId == 1001
    assert extracted.trader.balance > 0
    assert extracted.trader.depositAssetId == 1

    yield sdk.stopService()
