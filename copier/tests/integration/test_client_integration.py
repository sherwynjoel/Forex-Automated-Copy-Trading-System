"""Integration tests for CTraderClient wrapper against the fake cTrader server.

These tests drive the real CTraderClient wrapper (Task 8) through
FakeCTraderServer (Task 9), proving the wrapper's connection layer works
correctly over TLS, including auth chain ordering, heartbeat cadence,
reconnection re-auth, and execution event delivery.
"""

import pytest
import pytest_twisted
from ctrader_open_api import Protobuf
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAExecutionEvent,
    ProtoOANewOrderReq,
    ProtoOARefreshTokenReq,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAExecutionType,
    ProtoOAOrderType,
    ProtoOATradeSide,
)
from twisted.internet import defer, reactor

from copier.ctrader.client import (
    TLS_INSECURE_ENV, CTraderClient, make_sdk_client)
from copier.testing.fake_server import FakeCTraderServer

ACCOUNT_ID = 1001
ACCESS_TOKEN = "tok-1001"


@pytest.fixture
def server():
    """Start a fake cTrader server and yield it with its port."""
    srv = FakeCTraderServer(auto_fill=True)
    srv.accounts = {ACCOUNT_ID: ACCESS_TOKEN}
    port = srv.listen(reactor)
    yield srv, port
    srv.shutdown()


class _ExecutionRecorder:
    """Captures execution events received by CTraderClient callbacks."""

    def __init__(self):
        self.executions = []

    def record(self, account_id, event):
        self.executions.append((account_id, event))


def _wait_until(predicate, timeout=5.0):
    """Poll a predicate until true, with timeout."""
    from twisted.internet import task
    def check():
        if predicate():
            return defer.succeed(None)
        return task.deferLater(reactor, 0.05, check)
    d = check()
    d.addTimeout(timeout, reactor)
    return d


@pytest_twisted.inlineCallbacks
def test_full_auth_chain(server):
    """Auth chain: app auth on connect, then account auth on authorize_account.

    Verifies: app auth sent first, then account auth when authorized.
    """
    srv, port = server

    sdk = make_sdk_client("127.0.0.1", port)
    client = CTraderClient(sdk, "cid", "csecret")
    client.start()

    # Wait for the ready signal (app auth is done)
    yield client.ready

    # Verify app auth occurred first
    assert srv.app_auths == [("cid", "csecret")], f"Expected app auth, got {srv.app_auths}"
    # Account auth should not have happened yet
    assert srv.account_auths == [], f"Expected no account auth yet, got {srv.account_auths}"

    # Authorize the account
    yield client.authorize_account(ACCOUNT_ID, ACCESS_TOKEN)

    # Verify account auth was sent
    assert srv.account_auths == [ACCOUNT_ID], f"Expected account auth for {ACCOUNT_ID}, got {srv.account_auths}"

    client.stop()


@pytest_twisted.inlineCallbacks
def test_heartbeat_arrives_within_10s(server):
    """Heartbeat arrives within 10 seconds of connection.

    Verifies: server.heartbeats has at least one entry before timeout.
    """
    srv, port = server

    sdk = make_sdk_client("127.0.0.1", port)
    client = CTraderClient(sdk, "cid", "csecret")
    client.start()

    # Wait for ready + heartbeat
    yield client.ready

    # Wait for heartbeat to arrive (should be within 8s + epsilon)
    def has_heartbeat():
        return len(srv.heartbeats) > 0

    yield _wait_until(has_heartbeat, timeout=9.5)

    assert len(srv.heartbeats) > 0, "No heartbeat received"

    client.stop()


@pytest_twisted.inlineCallbacks
def test_reconnect_reauths_everything(server):
    """After reconnect, app auth and all account auths are resent.

    Verifies: drop_all_connections triggers reconnection that re-auths.
    """
    srv, port = server

    sdk = make_sdk_client("127.0.0.1", port)
    client = CTraderClient(sdk, "cid", "csecret")
    client.start()

    # Wait for initial ready
    yield client.ready
    yield client.authorize_account(ACCOUNT_ID, ACCESS_TOKEN)

    # Check initial auth counts
    initial_app_auths = len(srv.app_auths)
    initial_account_auths = len(srv.account_auths)
    assert initial_app_auths == 1
    assert initial_account_auths == 1

    # Drop all connections to force reconnect
    srv.drop_all_connections()

    # Wait for reconnection and re-auth (retryPolicy: initialDelay=1s)
    def has_reauthed():
        return (len(srv.app_auths) >= 2 and
                srv.account_auths.count(ACCOUNT_ID) >= 2)

    yield _wait_until(has_reauthed, timeout=5.0)

    assert len(srv.app_auths) >= 2, f"Expected at least 2 app auths, got {len(srv.app_auths)}"
    assert srv.account_auths.count(ACCOUNT_ID) >= 2, (
        f"Expected at least 2 account auths for {ACCOUNT_ID}, got {srv.account_auths}"
    )

    client.stop()


@pytest_twisted.inlineCallbacks
def test_execution_event_reaches_handler(server):
    """Execution events broadcast by server reach the client's on_execution callback.

    Verifies: server pushes execution event -> client callback records it.
    """
    srv, port = server

    sdk = make_sdk_client("127.0.0.1", port)
    client = CTraderClient(sdk, "cid", "csecret")

    # Set up execution recorder
    recorder = _ExecutionRecorder()
    client.on_execution(recorder.record)

    client.start()
    yield client.ready
    yield client.authorize_account(ACCOUNT_ID, ACCESS_TOKEN)

    # Server broadcasts a properly initialized execution event
    exec_event = ProtoOAExecutionEvent()
    exec_event.ctidTraderAccountId = ACCOUNT_ID
    exec_event.executionType = ProtoOAExecutionType.ORDER_ACCEPTED
    exec_event.order.orderId = 9000
    exec_event.order.orderType = ProtoOAOrderType.MARKET
    exec_event.order.orderStatus = 1  # ORDER_STATUS_ACCEPTED
    exec_event.order.tradeData.symbolId = 1
    exec_event.order.tradeData.volume = 100_000
    exec_event.order.tradeData.tradeSide = ProtoOATradeSide.BUY
    srv.push_execution(exec_event)

    # Wait for the callback to record it
    def has_execution():
        return len(recorder.executions) > 0

    yield _wait_until(has_execution, timeout=2.0)

    assert len(recorder.executions) == 1
    recorded_account_id, recorded_event = recorder.executions[0]
    assert recorded_account_id == ACCOUNT_ID
    assert recorded_event.executionType == ProtoOAExecutionType.ORDER_ACCEPTED
    assert recorded_event.order.orderId == 9000

    client.stop()


@pytest_twisted.inlineCallbacks
def test_refresh_token_roundtrip(server):
    """ProtoOARefreshTokenReq round-trip: server responds with new tokens.

    Verifies: send refresh token req -> receive new tokens in response.
    """
    srv, port = server
    srv.next_tokens = ("custom-access", "custom-refresh")

    sdk = make_sdk_client("127.0.0.1", port)
    client = CTraderClient(sdk, "cid", "csecret")
    client.start()

    yield client.ready

    # Send refresh token request through the client's send method
    refresh_req = ProtoOARefreshTokenReq()
    refresh_req.refreshToken = "old-refresh-token"

    res = yield client.send(refresh_req)
    extracted = Protobuf.extract(res)

    assert extracted.accessToken == "custom-access"
    assert extracted.refreshToken == "custom-refresh"
    assert extracted.tokenType == "Bearer"
    assert extracted.expiresIn == 3600

    client.stop()


# ---------- T1: the default TLS path really does verify ----------

@pytest_twisted.inlineCallbacks
def test_default_tls_path_rejects_the_self_signed_fake_server(server, monkeypatch):
    """The strongest available proof that T1's fix is live: with the
    insecure knob OFF, a real connection attempt to FakeCTraderServer's
    self-signed certificate must FAIL.

    Every other test in this package connects to that same certificate
    happily -- because tests/integration/conftest.py's autouse fixture sets
    CTRADER_TLS_INSECURE=1. Removing it here (and only here) exercises the
    exact code path a real demo/live cTrader connection takes: an endpoint
    built with `optionsForClientTLS(host, trustRoot=platformTrust())`, which
    no self-signed certificate can satisfy and whose CN ("localhost") does
    not match the host we dial ("127.0.0.1") either.

    RED before the fix: the vendored SDK's `clientFromString("ssl:host:port")`
    endpoint produced `CertificateOptions(trustRoot=None)` -- VERIFY_NONE,
    no hostname check -- so this connection SUCCEEDED, which is precisely
    the defect. Real-money OAuth access tokens and trade traffic were being
    handed to whatever answered the handshake.

    This is also residual-risk item 7's pre-Stage-4 check, made automatic:
    "confirm the copier FAILS to connect when pointed at a host with a
    mismatched certificate".
    """
    srv, port = server
    monkeypatch.delenv(TLS_INSECURE_ENV, raising=False)

    sdk = make_sdk_client("127.0.0.1", port)
    client = CTraderClient(sdk, "cid", "csecret")

    # Record why the connection dies. Twisted's TLS wrapper calls
    # makeConnection on the wrapped protocol optimistically, BEFORE the
    # handshake completes, so ClientService.whenConnected() still fires --
    # the verification verdict arrives as connectionLost's reason. That
    # reason is the direct evidence of what the peer certificate check did.
    disconnect_reasons = []
    inner_on_disconnected = client._on_disconnected

    def _record_disconnect(sdk_, reason):
        disconnect_reasons.append(reason)
        return inner_on_disconnected(sdk_, reason)

    sdk.setDisconnectedCallback(_record_disconnect)

    client.start()
    try:
        yield _wait_until(lambda: disconnect_reasons, timeout=20.0)

        assert "certificate verify failed" in str(disconnect_reasons[0]), (
            "expected the TLS handshake to be rejected for an untrusted "
            f"certificate; got {disconnect_reasons[0]!r}"
        )
        # The security property that actually matters: no cTrader protobuf
        # ever crossed this connection. `ready` -- which only fires once the
        # server has answered ProtoOAApplicationAuthReq -- is still pending.
        assert srv.app_auths == []
        assert not client.ready.called
    finally:
        client.stop()


@pytest_twisted.inlineCallbacks
def test_insecure_knob_reaches_the_same_server(server):
    """The other half of the pair: with the knob set (as
    tests/integration/conftest.py sets it, and as
    docker-compose.test.yml sets it for the fake-ctrader-facing copier),
    the very same server IS reachable -- so the test above is proving
    verification, not a broken fake."""
    srv, port = server                       # conftest's autouse fixture has the knob on

    sdk = make_sdk_client("127.0.0.1", port)
    client = CTraderClient(sdk, "cid", "csecret")
    client.start()
    try:
        yield client.ready
        assert srv.app_auths == [("cid", "csecret")]
    finally:
        client.stop()
