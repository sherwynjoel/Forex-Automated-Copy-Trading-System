import pytest
from ctrader_open_api import Client, TcpProtocol
from twisted.internet import defer, reactor

from copier.ctrader.client import CTraderClient
from copier.testing.fake_server import FakeCTraderServer

ACCOUNT_ID = 1001
ACCESS_TOKEN = "tok-1001"


@pytest.fixture
def server_and_client():
    """Fixture providing a FakeCTraderServer and authorized CTraderClient.

    The fixture:
    1. Starts FakeCTraderServer (listens on random TLS port)
    2. Connects real SDK Client to it
    3. Wraps SDK in CTraderClient (adds auth/heartbeat)
    4. Authorizes account 1001
    5. Cleans up both client.stop() and server.shutdown() (prevents reactor leaks)
    """
    # Setup fake server
    server = FakeCTraderServer(auto_fill=True)
    server.accounts = {ACCOUNT_ID: ACCESS_TOKEN}
    port = server.listen(reactor)

    # Setup SDK client connecting to the fake server
    sdk = Client("127.0.0.1", port, TcpProtocol)

    # Create CTraderClient wrapper
    client = CTraderClient(sdk, "test-cid", "test-csecret")
    client.start()

    yield server, client

    # Cleanup: must call client.stop() to prevent reactor resource leaks
    # (heartbeat LoopingCall runs every 8s if not stopped)
    client.stop()
    server.shutdown()
