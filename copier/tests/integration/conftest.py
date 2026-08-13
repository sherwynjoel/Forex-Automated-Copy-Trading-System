import pytest
import pytest_twisted
from ctrader_open_api import Protobuf
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOASymbolsListReq, ProtoOASymbolByIdReq,
    ProtoOASymbolsListRes, ProtoOASymbolByIdRes
)
from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoMessage
from twisted.internet import defer

from copier.ctrader.client import CTraderClient


class MockServer:
    """Fake server that responds to symbol requests."""

    def __init__(self):
        self.sent = []
        self.running = False
        self._connected_cb = self._disconnected_cb = self._message_cb = None
        # Symbol data
        self.symbols = {
            1: {
                "name": "EURUSD",
                "digits": 5,
                "pipPosition": 5,
                "lotSize": 10_000_000,
                "minVolume": 100_000,
                "stepVolume": 100_000
            },
        }

    def setConnectedCallback(self, cb):
        self._connected_cb = cb

    def setDisconnectedCallback(self, cb):
        self._disconnected_cb = cb

    def setMessageReceivedCallback(self, cb):
        self._message_cb = cb

    def startService(self):
        self.running = True

    def stopService(self):
        self.running = False

    def send(self, msg, **kwargs):
        self.sent.append(msg)

        if isinstance(msg, ProtoOASymbolsListReq):
            return self._handle_symbols_list_req(msg)
        elif isinstance(msg, ProtoOASymbolByIdReq):
            return self._handle_symbol_by_id_req(msg)
        else:
            return defer.succeed(None)

    def _handle_symbols_list_req(self, req):
        """Respond to ProtoOASymbolsListReq with list of symbol names and IDs."""
        res = ProtoOASymbolsListRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId

        for symbol_id, data in self.symbols.items():
            sym = res.symbol.add()
            sym.symbolId = symbol_id
            sym.symbolName = data["name"]

        # Wrap in envelope
        envelope = self._wrap_response(res)
        return defer.succeed(envelope)

    def _handle_symbol_by_id_req(self, req):
        """Respond to ProtoOASymbolByIdReq with full symbol details."""
        res = ProtoOASymbolByIdRes()
        res.ctidTraderAccountId = req.ctidTraderAccountId

        for symbol_id in req.symbolId:
            if symbol_id in self.symbols:
                data = self.symbols[symbol_id]
                sym = res.symbol.add()
                sym.symbolId = symbol_id
                # Required fields
                sym.digits = data["digits"]
                sym.pipPosition = data["pipPosition"]
                # Additional details
                sym.lotSize = data["lotSize"]
                sym.minVolume = data["minVolume"]
                sym.stepVolume = data["stepVolume"]

        # Wrap in envelope
        envelope = self._wrap_response(res)
        return defer.succeed(envelope)

    def _wrap_response(self, message):
        """Wrap a message in a ProtoMessage envelope."""
        Protobuf.populate()  # Ensure mappings are initialized
        envelope = ProtoMessage()
        envelope.payloadType = message.payloadType
        envelope.payload = message.SerializeToString()
        return envelope

    # test helpers
    def connect(self):
        self._connected_cb(self)

    def disconnect(self):
        self._disconnected_cb(self, "lost")


@pytest.fixture
def server_and_client():
    """Fixture providing a mock server and authorized CTraderClient."""
    server = MockServer()
    client = CTraderClient(server, "test-cid", "test-csecret")
    client.start()
    server.connect()
    client.authorize_account(1001, "test-token-1001")
    return server, client
