import pytest
import pytest_twisted
from ctrader_open_api import Client, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAApplicationAuthReq, ProtoOAApplicationAuthRes,
    ProtoOAAccountAuthReq, ProtoOASymbolsListReq, ProtoOASymbolByIdReq)
from twisted.internet import defer, reactor

from copier.testing.fake_server import FakeCTraderServer


@pytest.fixture
def server():
    srv = FakeCTraderServer()
    srv.accounts = {1001: "tok-1001"}
    port = srv.listen(reactor)
    yield srv, port
    srv.shutdown()


@pytest_twisted.inlineCallbacks
def test_sdk_client_can_auth_and_list_symbols(server):
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
