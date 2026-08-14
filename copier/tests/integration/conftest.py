import os

import pytest
from ctrader_open_api import Client, TcpProtocol
from twisted.internet import defer, reactor

from copier.ctrader.client import CTraderClient, TLS_INSECURE_ENV
from copier.testing.fake_server import FakeCTraderServer

ACCOUNT_ID = 1001
ACCESS_TOKEN = "tok-1001"


@pytest.fixture(autouse=True)
def allow_self_signed_fake_server(monkeypatch):
    """TEST-ONLY (T1): accept FakeCTraderServer's self-signed certificate.

    `make_sdk_client` now builds a VERIFYING TLS endpoint by default
    (platformTrust + hostname checking, see
    copier/src/copier/ctrader/client.py:client_tls_options), which is
    exactly what should happen for demo.ctraderapi.com/live.ctraderapi.com
    -- and exactly what no CA can vouch for when the far end is a
    certificate this test process minted for itself moments earlier
    (copier/src/copier/testing/tls.py) on 127.0.0.1.

    monkeypatch (not a bare os.environ assignment) so the variable is
    removed again after each test: nothing outside this package's tests can
    inherit it. Note this is deliberately narrow -- it lives in
    tests/integration/conftest.py, so unit tests never see it, and the only
    other place it is ever set is the `fake-ctrader`-facing copier service
    in docker-compose.test.yml. See test_client_integration.py's
    test_default_tls_path_rejects_the_self_signed_fake_server for the proof
    that the DEFAULT path really does refuse this connection.
    """
    monkeypatch.setenv(TLS_INSECURE_ENV, "1")
    yield
    os.environ.pop(TLS_INSECURE_ENV, None)


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
