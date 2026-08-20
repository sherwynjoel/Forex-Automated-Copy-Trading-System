from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
    ProtoErrorRes, ProtoHeartbeatEvent, ProtoMessage)
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq, ProtoOAAccountAuthRes, ProtoOAApplicationAuthReq,
    ProtoOAErrorRes, ProtoOAExecutionEvent)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAExecutionType
from twisted.internet import defer, ssl
from twisted.internet.task import Clock
from twisted.python.failure import Failure

from copier.ctrader.client import (
    DRAIN_BUDGET_PER_TICK, DRAIN_TICK_S, HEARTBEAT_INTERVAL_S,
    SEND_HANDOFF_TIMEOUT_S, TLS_CA_FILE_ENV, TLS_INSECURE_ENV, CTraderClient,
    _PerConnectionTcpProtocol, client_tls_options, make_sdk_client)
from copier.testing.tls import make_self_signed_context


class _StubTransport:
    """Minimal ITransport stand-in: just records what was written."""

    def __init__(self):
        self.written = []

    def write(self, data):
        self.written.append(data)


class _StubProtocolFactory:
    """Minimal factory for driving a real TcpProtocol's connectionMade()
    outside of an actual reactor/socket."""

    numberOfMessagesToSendPerSecond = 5

    def connected(self, protocol):
        pass


def _connected_real_protocol() -> _PerConnectionTcpProtocol:
    """Build a REAL _PerConnectionTcpProtocol (the vendored TcpProtocol
    subclass make_sdk_client actually installs), drive its real
    connectionMade() lifecycle with a stub transport/factory, and stop its
    LoopingCall immediately so the test's synchronous window is not
    disturbed by any further real-reactor ticks. Used to test the genuine
    send(instant=...) behavior rather than reimplementing it in a stub."""
    protocol = _PerConnectionTcpProtocol()
    protocol.factory = _StubProtocolFactory()
    protocol.transport = _StubTransport()
    protocol.connectionMade()   # shadows _send_queue per-instance; also
                                 # synchronously fires one heartbeat via
                                 # instant=True (queue is empty) -- clear
                                 # transport.written afterwards if that
                                 # matters to the caller's assertions.
    protocol._send_task.stop()
    return protocol


class StubProtocol:
    """Stand-in for the vendored SDK's TcpProtocol: the object whenConnected()
    resolves with, whose .send() (unlike Client.send()) hands a message to
    the transport and returns None -- no response Deferred, no timeout."""

    def __init__(self):
        self.sent = []  # list of (msg, clientMsgId)

    def send(self, msg, clientMsgId=None, **kwargs):
        self.sent.append((msg, clientMsgId))
        return None


class StubSdk:
    def __init__(self):
        self.sent = []
        self.running = False
        self._connected_cb = self._disconnected_cb = self._message_cb = None
        # whenConnected() support (used by CTraderClient.send_no_reply):
        self.protocol = StubProtocol()
        self._connected = False
        self._when_connected_waiters = []
        self._connect_failure = None

    def setConnectedCallback(self, cb): self._connected_cb = cb
    def setDisconnectedCallback(self, cb): self._disconnected_cb = cb
    def setMessageReceivedCallback(self, cb): self._message_cb = cb
    def startService(self): self.running = True
    def stopService(self): self.running = False

    def send(self, msg, **kwargs):
        self.sent.append(msg)
        return defer.succeed(None)

    def whenConnected(self, failAfterFailures=None):
        """Mirrors ClientService.whenConnected(): fires with the connected
        protocol, or errbacks on a connection-level failure."""
        d = defer.Deferred()
        if self._connect_failure is not None:
            d.errback(self._connect_failure)
        elif self._connected:
            d.callback(self.protocol)
        else:
            self._when_connected_waiters.append(d)
        return d

    # test helpers
    def connect(self):
        self._connected = True
        self._connected_cb(self)
        waiters, self._when_connected_waiters = self._when_connected_waiters, []
        for d in waiters:
            d.callback(self.protocol)

    def disconnect(self): self._connected = False; self._disconnected_cb(self, "lost")
    def deliver(self, payload): self._message_cb(self, payload)
    def fail_next_whenConnected(self, failure): self._connect_failure = failure


def make():
    sdk, clock = StubSdk(), Clock()
    client = CTraderClient(sdk, "cid", "csecret", clock=clock)
    client.start()
    return sdk, clock, client


def of_type(sent, t):
    return [s for s in sent if isinstance(s, t)]


def test_start_starts_sdk_and_connect_sends_app_auth():
    sdk, _, _ = make()
    assert sdk.running
    sdk.connect()
    reqs = of_type(sdk.sent, ProtoOAApplicationAuthReq)
    assert len(reqs) == 1
    assert (reqs[0].clientId, reqs[0].clientSecret) == ("cid", "csecret")


def test_heartbeat_every_8s_after_auth_stops_on_disconnect():
    sdk, clock, _ = make()
    sdk.connect()
    clock.advance(HEARTBEAT_INTERVAL_S)
    clock.advance(HEARTBEAT_INTERVAL_S)
    assert len(of_type(sdk.sent, ProtoHeartbeatEvent)) == 2
    assert HEARTBEAT_INTERVAL_S <= 10.0        # spec: at least every 10 s
    sdk.disconnect()
    clock.advance(HEARTBEAT_INTERVAL_S * 3)
    assert len(of_type(sdk.sent, ProtoHeartbeatEvent)) == 2


def test_authorize_account_sends_account_auth():
    sdk, _, client = make()
    sdk.connect()
    client.authorize_account(1001, "tok-1001")
    reqs = of_type(sdk.sent, ProtoOAAccountAuthReq)
    assert (reqs[0].ctidTraderAccountId, reqs[0].accessToken) == (1001, "tok-1001")


def test_reconnect_reauths_app_and_all_registered_accounts():
    sdk, _, client = make()
    sdk.connect()
    client.authorize_account(1001, "t1")
    client.authorize_account(1002, "t2")
    sdk.disconnect()
    sdk.connect()   # ClientService reconnected
    assert len(of_type(sdk.sent, ProtoOAApplicationAuthReq)) == 2
    reauthed = of_type(sdk.sent, ProtoOAAccountAuthReq)
    assert {r.ctidTraderAccountId for r in reauthed[-2:]} == {1001, 1002}


def test_execution_events_routed_with_account_id():
    sdk, _, client = make()
    seen = []
    client.on_execution(lambda account_id, evt: seen.append((account_id, evt)))
    sdk.connect()
    evt = ProtoOAExecutionEvent()
    evt.ctidTraderAccountId = 1001
    sdk.deliver(evt)
    assert seen and seen[0][0] == 1001


def test_trader_updated_and_margin_call_events_are_routed():
    """Pushed ProtoOATraderUpdatedEvent / ProtoOAMarginCallTriggerEvent reach
    their typed callback lists -- the broker pushes balance changes and
    margin calls; dropping them (the old behavior) forced balance polling
    and made margin calls invisible."""
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAMarginCallTriggerEvent, ProtoOATraderUpdatedEvent)
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
        ProtoOANotificationType)

    sdk, _, client = make()
    updates, margin_calls = [], []
    client.on_trader_updated(lambda evt: updates.append(evt))
    client.on_margin_call(lambda evt: margin_calls.append(evt))
    sdk.connect()

    evt = ProtoOATraderUpdatedEvent()
    evt.ctidTraderAccountId = 1001
    evt.trader.ctidTraderAccountId = 1001
    evt.trader.balance = 999_900
    sdk.deliver(evt)

    mc = ProtoOAMarginCallTriggerEvent()
    mc.ctidTraderAccountId = 1001
    mc.marginCall.marginCallType = ProtoOANotificationType.MARGIN_LEVEL_THRESHOLD_1
    mc.marginCall.marginLevelThreshold = 50.0
    sdk.deliver(mc)

    assert len(updates) == 1 and updates[0].trader.balance == 999_900
    assert len(margin_calls) == 1
    assert margin_calls[0].marginCall.marginLevelThreshold == 50.0


def test_ready_fires_after_first_app_auth():
    sdk, _, client = make()
    fired = []
    client.ready.addCallback(fired.append)
    sdk.connect()
    assert fired


def test_execution_callback_error_does_not_break_remaining_callbacks():
    sdk, _, client = make()
    seen = []

    def bad_cb(account_id, evt):
        raise ValueError("intentional error")

    def good_cb(account_id, evt):
        seen.append((account_id, evt))

    client.on_execution(bad_cb)
    client.on_execution(good_cb)
    sdk.connect()
    evt = ProtoOAExecutionEvent()
    evt.ctidTraderAccountId = 1001
    sdk.deliver(evt)
    # second callback should still run despite first raising
    assert seen and seen[0][0] == 1001


def test_stop_cancels_heartbeat():
    sdk, clock, client = make()
    sdk.connect()
    # trigger heartbeat
    clock.advance(HEARTBEAT_INTERVAL_S)
    hb_count = len(of_type(sdk.sent, ProtoHeartbeatEvent))
    assert hb_count == 1

    client.stop()
    # after stop, clock advancement should not produce more heartbeats
    clock.advance(HEARTBEAT_INTERVAL_S * 3)
    assert len(of_type(sdk.sent, ProtoHeartbeatEvent)) == 1


def test_ready_does_not_fire_twice_on_reconnect():
    sdk, _, client = make()
    fire_count = [0]

    def count_fires(result):
        fire_count[0] += 1
        return result

    client.ready.addCallback(count_fires)
    sdk.connect()
    assert fire_count[0] == 1

    # Reconnect
    sdk.disconnect()
    sdk.connect()
    # ready should still have fired only once
    assert fire_count[0] == 1


def test_deauthorize_account_removes_from_reauth_registry():
    sdk, _, client = make()
    sdk.connect()
    client.authorize_account(1001, "t1")
    client.authorize_account(1002, "t2")
    client.deauthorize_account(1001)

    # Reconnect
    sdk.disconnect()
    sdk.connect()

    # Only account 1002 should be re-authed
    reauthed = of_type(sdk.sent, ProtoOAAccountAuthReq)
    reauth_ids = {r.ctidTraderAccountId for r in reauthed[-1:]}
    assert reauth_ids == {1002}


# ---- send_no_reply: guards the every-trade-degrades regression ----
#
# The real cTrader server never sends a synchronous, clientMsgId-tagged
# reply to a trade request (new order, close, amend, cancel); send()
# registers a response Deferred with a ~5s timeout that such a reply would
# never satisfy, so every successful trade send used to time out ~5s later
# and get misread by Dispatcher as an ambiguous failure -> degraded slave
# account. send_no_reply() must resolve purely from the transport handoff
# (whenConnected() + protocol.send()), never by waiting on an incoming
# reply. It also bounds the wait for a *connection* itself (SEND_HANDOFF_
# TIMEOUT_S, much shorter than the old 5s reply-timeout): whenConnected()
# resolves with "the currently connected protocol, or the NEXT one to
# connect", so without a bound it can stay pending through an entire
# reconnect backoff (up to 60s) and then hand off a stale trade late.

def test_send_no_reply_resolves_on_transport_handoff_without_awaiting_a_reply():
    sdk, clock, client = make()
    sdk.connect()

    before = len(clock.getDelayedCalls())
    msg = ProtoOAExecutionEvent()
    d = client.send_no_reply(msg)

    # Resolved synchronously off the transport handoff alone -- no reply
    # message was ever delivered (sdk.deliver was never called).
    assert d.called
    # NEW-2: Failure derives from BaseException, not Exception, so
    # `isinstance(d.result, Exception)` is always False whether the
    # Deferred succeeded or failed -- vacuous. Checking against Failure
    # directly is the discriminating assertion.
    assert not isinstance(d.result, Failure)
    assert sdk.protocol.sent and sdk.protocol.sent[-1] == (msg, str(id(msg)))

    # The bounded-handoff timer is armed and (since the connection was
    # already up) immediately cancelled again within this same call -- by
    # the time send_no_reply() returns, nothing net-new is left scheduled
    # on the clock (contrast the never-connected case below, where the
    # timer is still pending afterwards).
    assert len(clock.getDelayedCalls()) == before

    # Advancing well past the SDK's real ~5s response-timeout window (the
    # unwanted timeout send_no_reply exists to avoid) must produce no late
    # failure -- the message already succeeded, and the local handoff timer
    # was cancelled, not merely not-yet-fired.
    clock.advance(10.0)
    assert d.called
    # NEW-2: Failure derives from BaseException, not Exception, so
    # `isinstance(d.result, Exception)` is always False whether the
    # Deferred succeeded or failed -- vacuous. Checking against Failure
    # directly is the discriminating assertion.
    assert not isinstance(d.result, Failure)


def test_send_no_reply_propagates_connection_level_failure():
    """A connection-level failure (never connected / all attempts failed)
    still fails send_no_reply's Deferred, exactly as it does for send() --
    both are gated by the same whenConnected() call."""
    sdk, _, client = make()
    sdk.fail_next_whenConnected(RuntimeError("connection refused"))

    d = client.send_no_reply(ProtoOAExecutionEvent())

    errors = []
    d.addErrback(lambda failure: errors.append(failure))
    assert len(errors) == 1
    assert isinstance(errors[0].value, RuntimeError)
    assert not sdk.protocol.sent   # never reached the transport


def test_send_no_reply_times_out_when_never_connected_without_enqueueing():
    """Guards C1: an unbounded wait for a connection is itself a bug (it can
    hold a trade request through a full ~60s reconnect backoff and then send
    it stale). If no connection becomes available within
    SEND_HANDOFF_TIMEOUT_S, send_no_reply must fail on its own -- and must
    never have called protocol.send() at all, so the caller can always
    safely treat this as SendNotAttempted-eligible (never reached a
    transport, let alone the wire)."""
    sdk, clock, client = make()
    # Deliberately never connect and never fail_next_whenConnected(): the
    # underlying whenConnected() Deferred stays genuinely pending, exactly
    # like a client stuck mid-reconnect-backoff.

    d = client.send_no_reply(ProtoOAExecutionEvent())
    assert not d.called   # still bounded-waiting

    clock.advance(SEND_HANDOFF_TIMEOUT_S)

    assert d.called
    errors = []
    d.addErrback(errors.append)
    assert len(errors) == 1
    assert isinstance(errors[0].value, defer.TimeoutError)
    assert sdk.protocol.sent == []   # never enqueued -- nothing to evict


def test_send_no_reply_ignores_a_late_whenconnected_after_timeout():
    """A whenConnected() Deferred that resolves AFTER send_no_reply already
    reported a timeout to the caller must be ignored: no enqueue (the
    caller has already moved on and may be retrying elsewhere) and no
    crash. This is the reason send_no_reply deliberately never calls
    .cancel() on whenConnected()'s own Deferred (see its docstring): the
    vendored SDK's waiter list only forgets an entry when it fires it, so a
    stray late firing is expected and must be handled, not prevented."""
    sdk, clock, client = make()

    d = client.send_no_reply(ProtoOAExecutionEvent())
    clock.advance(SEND_HANDOFF_TIMEOUT_S)
    assert d.called
    d.addErrback(lambda _f: None)   # already asserted the failure shape above

    # The connection "arrives" late -- must not raise (e.g. AlreadyCalledError)
    # and must not enqueue the now-abandoned message.
    sdk.connect()
    assert sdk.protocol.sent == []


def test_send_no_reply_writes_synchronously_to_the_transport_not_the_drain_queue():
    """Guards N2: TcpProtocol.send(instant=False) (the default) only
    *enqueues* onto _send_queue, drained by a DRAIN_TICK_S-cadence LoopingCall. With
    each connection now owning its own private queue (I3), a message
    enqueued on a connection that dies before that tick fires is silently
    dropped -- yet send_no_reply had already reported success: no retry, no
    event, no degraded status, and reconcile can't catch it either (it only
    inspects 'active' mappings; a never-activated 'pending' one is invisible
    to every drift check).

    Verified against the REAL vendored _PerConnectionTcpProtocol/
    TcpProtocol.send() (not the simplified StubProtocol used by the other
    send_no_reply tests in this file, which cannot distinguish queued from
    instant): send_no_reply's instant=True call must write straight to the
    transport in the SAME turn whenConnected() resolves, and the payload
    must never touch _send_queue at all -- there is no drain window for a
    dying connection to drop it in, because nothing was ever queued."""
    sdk, clock, client = make()

    real_protocol = _connected_real_protocol()
    real_protocol.transport.written.clear()   # drop connectionMade()'s own heartbeat
    sdk.protocol = real_protocol              # whenConnected() hands this out instead
    sdk.connect()

    # A real (fully-populated) message: the REAL TcpProtocol.send() actually
    # serializes it, unlike the StubProtocol used elsewhere in this file, so
    # proto2's required fields must be set or SerializeToString() itself
    # raises -- unrelated to what this test guards.
    msg = ProtoOAExecutionEvent()
    msg.ctidTraderAccountId = 1001
    msg.executionType = ProtoOAExecutionType.ORDER_ACCEPTED
    d = client.send_no_reply(msg)

    assert d.called
    # NEW-2: Failure derives from BaseException, not Exception, so
    # `isinstance(d.result, Exception)` is always False whether the
    # Deferred succeeded or failed -- vacuous. Checking against Failure
    # directly is the discriminating assertion.
    assert not isinstance(d.result, Failure)
    # Written straight to the transport, in this same synchronous turn...
    assert len(real_protocol.transport.written) == 1
    # ...and never touched the drain queue -- nothing sat there for a dying
    # connection to silently drop.
    assert list(real_protocol._send_queue) == []


def test_send_no_reply_errbacks_instead_of_hanging_on_a_synchronous_send_error():
    """Guards N1: if protocol.send() ever raised synchronously inside the
    whenConnected() success callback, the old code set settled[0] = True
    and then called protocol.send() -- so a raise there would leave
    `result` permanently unfired (settled already True blocks the errback
    path too) while addBoth(_cancel_pending_timeout) silently swallows the
    propagating exception. That's a permanent, silent hang: no event row,
    no alert, no degraded status, ever. Simulate it with a protocol whose
    send() raises, and assert the caller's Deferred fails instead of
    hanging."""
    sdk, clock, client = make()

    class _RaisingProtocol:
        def send(self, msg, **kwargs):
            raise ValueError("boom: simulated synchronous send failure")

    sdk.protocol = _RaisingProtocol()
    sdk.connect()

    d = client.send_no_reply(ProtoOAExecutionEvent())

    assert d.called   # not stranded
    errors = []
    d.addErrback(errors.append)
    assert len(errors) == 1
    assert isinstance(errors[0].value, ValueError)

    # The bounded-handoff timer must also have been cleaned up, not merely
    # left to fire later on an already-decided Deferred.
    clock.advance(SEND_HANDOFF_TIMEOUT_S + 1.0)
    assert len(errors) == 1   # no second, late failure


def test_per_connection_tcp_protocol_isolates_send_queues():
    """Guards I3/N4: the vendored TcpProtocol keeps its outbound queue as a
    CLASS attribute (`_send_queue = deque([])`), shared by every instance
    unless shadowed. Confirms _PerConnectionTcpProtocol -- wired into every
    connection via make_sdk_client -- gives each connection its own private
    queue: two connected instances must never share a deque object, and a
    message queued on one must never be visible via the other."""
    p1 = _connected_real_protocol()
    p2 = _connected_real_protocol()

    # Each connection owns a genuinely distinct queue -- not each other's,
    # and not the shared class-level default either.
    assert p1._send_queue is not p2._send_queue
    assert p1._send_queue is not _PerConnectionTcpProtocol._send_queue
    assert p2._send_queue is not _PerConnectionTcpProtocol._send_queue

    p1.send(b"payload-for-p1-only", clientMsgId="only-p1")

    assert list(p1._send_queue)          # queued on p1's own connection
    assert list(p2._send_queue) == []    # never visible on p2's -- the actual regression this guards


def _clocked_real_protocol(budget=1):
    """A REAL _PerConnectionTcpProtocol whose drain LoopingCall runs on a
    test Clock, so tests can assert exactly when queued messages hit the
    wire. `budget` is the factory's per-tick message allowance."""
    clock = Clock()
    protocol = _PerConnectionTcpProtocol()
    protocol._drain_clock = clock
    protocol.factory = _StubProtocolFactory()
    protocol.factory.numberOfMessagesToSendPerSecond = budget
    protocol.transport = _StubTransport()
    protocol.connectionMade()
    protocol.transport.written.clear()  # drop the start-tick heartbeat
    return protocol, clock


def test_queued_send_drains_on_a_100ms_tick_not_the_sdk_1s_tick():
    """The vendored TcpProtocol drains its queue on a ONCE-PER-SECOND
    LoopingCall, so every queued broker round trip paid up to 1s of pure
    queue wait -- a 3-round-trip query (/details) had a deterministic
    ~2.4s floor and /analytics ~4s (measured on prod, 2026-08-20; the
    Spotware-API slowness the operator reported). The per-connection
    subclass drains every DRAIN_TICK_S instead."""
    protocol, clock = _clocked_real_protocol()

    protocol.send(ProtoHeartbeatEvent())     # default instant=False: queued
    assert protocol.transport.written == []  # nothing until a drain tick

    clock.advance(DRAIN_TICK_S)
    assert len(protocol.transport.written) == 1

    # And decisively BEFORE the SDK's 1s cadence would have fired.
    assert DRAIN_TICK_S <= 0.2


def test_drain_budget_caps_queued_messages_per_tick():
    """DRAIN_BUDGET_PER_TICK bounds the queued path's wire rate at
    budget/DRAIN_TICK_S msg/s (10/s at the shipped 1-per-100ms): three
    queued messages drain across three consecutive ticks, one each."""
    protocol, clock = _clocked_real_protocol(budget=1)

    for _ in range(3):
        protocol.send(ProtoHeartbeatEvent())

    for expected in (1, 2, 3):
        clock.advance(DRAIN_TICK_S)
        assert len(protocol.transport.written) == expected


def test_make_sdk_client_sets_the_per_tick_budget():
    """make_sdk_client must wire DRAIN_BUDGET_PER_TICK into the client --
    the factory copies numberOfMessagesToSendPerSecond from it, and
    _sendStrings reads it as the PER-TICK allowance."""
    client = make_sdk_client("demo.example.invalid", 5035)
    assert client.numberOfMessagesToSendPerSecond == DRAIN_BUDGET_PER_TICK


# ---------- T1: TLS verification ----------

def test_default_tls_options_verify_chain_and_hostname(monkeypatch):
    """The DEFAULT path (nothing set in the environment) must produce a
    hostname-verifying, platform-trust-anchored client TLS creator.

    The vendored SDK built `clientFromString(reactor, f"ssl:{host}:{port}")`,
    whose parser only enables verification when a `hostname=`/`caCertsDir=`
    parameter is present -- neither ever was -- yielding
    `CertificateOptions(trustRoot=None)`: VERIFY_NONE, no hostname check,
    for every real-money cTrader connection.
    """
    monkeypatch.delenv(TLS_INSECURE_ENV, raising=False)
    monkeypatch.delenv(TLS_CA_FILE_ENV, raising=False)

    options = client_tls_options("demo.ctraderapi.com")

    # ClientTLSOptions is the type optionsForClientTLS() returns; a bare
    # CertificateOptions (what the old path produced) verifies nothing.
    assert type(options).__name__ == "ClientTLSOptions"
    assert not isinstance(options, ssl.CertificateOptions)
    # It carries the hostname it will check the peer certificate against.
    assert options._hostnameBytes == b"demo.ctraderapi.com"


def test_insecure_knob_is_inert_unless_explicitly_set(monkeypatch):
    """An unset OR empty/false-y value must not weaken the default path."""
    monkeypatch.delenv(TLS_CA_FILE_ENV, raising=False)
    for value in ("", "0", "false", "no"):
        monkeypatch.setenv(TLS_INSECURE_ENV, value)
        assert type(client_tls_options("demo.ctraderapi.com")).__name__ == "ClientTLSOptions", value


def test_insecure_knob_when_set_disables_verification(monkeypatch):
    """Set explicitly (only by tests/integration/conftest.py and
    docker-compose.test.yml's fake-ctrader-facing copier), it reproduces the
    old verify-nothing behaviour so a self-signed fake can be reached."""
    monkeypatch.setenv(TLS_INSECURE_ENV, "1")
    options = client_tls_options("127.0.0.1")
    assert isinstance(options, ssl.CertificateOptions)


def test_custom_ca_knob_still_checks_hostname(monkeypatch, tmp_path):
    monkeypatch.delenv(TLS_INSECURE_ENV, raising=False)
    ca_pem = tmp_path / "ca.pem"
    ca_pem.write_bytes(
        ssl.Certificate(make_self_signed_context().certificate).dumpPEM()
    )
    monkeypatch.setenv(TLS_CA_FILE_ENV, str(ca_pem))

    options = client_tls_options("demo.ctraderapi.com")

    assert type(options).__name__ == "ClientTLSOptions"
    assert options._hostnameBytes == b"demo.ctraderapi.com"


def test_make_sdk_client_connects_over_a_tls_wrapped_endpoint(monkeypatch):
    """The endpoint handed to ClientService must be our TLS wrapper, not the
    SDK's `ssl:host:port` string endpoint."""
    monkeypatch.delenv(TLS_INSECURE_ENV, raising=False)
    monkeypatch.delenv(TLS_CA_FILE_ENV, raising=False)
    from twisted.internet import reactor as real_reactor

    client = make_sdk_client("demo.ctraderapi.com", 5035, reactor_=real_reactor)

    endpoint = client._machine._endpoint
    assert type(endpoint).__name__ == "_WrapperEndpoint"
    # Still a working SDK Client in every other respect.
    assert client.numberOfMessagesToSendPerSecond == DRAIN_BUDGET_PER_TICK
    assert client.isConnected is False
    assert client._responseDeferreds == {}


# ---------- T2: post-reconnect bulk re-auth ----------

class _DeferredSdk(StubSdk):
    """StubSdk whose send() returns a Deferred the test fires by hand, so
    "how many auth requests are in flight at once" is observable."""

    def __init__(self):
        super().__init__()
        self.pending: list[defer.Deferred] = []

    def send(self, msg, **kwargs):
        self.sent.append(msg)
        d = defer.Deferred()
        self.pending.append(d)
        return d


def test_bulk_reauth_is_serialized_and_reaches_every_account():
    """T2: on a reconnect, every registered account must be re-authed, one
    at a time -- including accounts far past the SDK's 5 msg/s drain window.

    RED before the fix: `_on_app_authed` fired `_send_account_auth` for ALL
    accounts in a single reactor turn. The vendored SDK arms each request's
    5 s response timeout AT SEND TIME while TcpProtocol drains only 5
    messages per second, so everything at queue index >= ~25 timed out
    before it was ever written, was dropped by the SDK's `isCanceled` check
    instead of sent, and was never retried -- permanently un-authed for that
    connection's life. This test sees all 30 ProtoOAAccountAuthReqs handed
    to send() at once (in-flight count 30) rather than one at a time.
    """
    sdk = _DeferredSdk()
    client = CTraderClient(sdk, "cid", "csecret", clock=Clock())
    client.start()

    # First connect: app auth is the one pending request; answer it, then
    # register 30 accounts (each authorize_account sends immediately, so
    # answer each in turn).
    sdk.connect()
    sdk.pending.pop(0).callback(None)                 # ProtoOAApplicationAuthReq
    account_ids = list(range(1000, 1030))             # 30 > the ~25 ceiling
    for account_id in account_ids:
        client.authorize_account(account_id, f"tok-{account_id}")
        sdk.pending.pop(0).callback(None)
    assert sdk.pending == []

    sent_before = len(sdk.sent)

    # --- the reconnect ---
    sdk.disconnect()
    sdk.connect()
    app_auth = sdk.pending.pop(0)
    assert isinstance(sdk.sent[-1], ProtoOAApplicationAuthReq)
    app_auth.callback(None)

    reauthed_in_order = []
    while sdk.pending:
        assert len(sdk.pending) == 1, (
            f"{len(sdk.pending)} account-auth requests in flight at once; the re-auth "
            "loop must await each response before sending the next"
        )
        req = sdk.sent[-1]
        assert isinstance(req, ProtoOAAccountAuthReq)
        reauthed_in_order.append(req.ctidTraderAccountId)
        sdk.pending.pop(0).callback(None)

    assert reauthed_in_order == account_ids, (
        "every registered account must be re-authed on a reconnect, including "
        "those beyond the SDK's 5 msg/s send-timeout window"
    )
    assert len(sdk.sent) == sent_before + 1 + len(account_ids)


def test_bulk_reauth_continues_past_one_failing_account():
    """One account whose auth errbacks must not abandon the accounts queued
    behind it."""
    sdk = _DeferredSdk()
    client = CTraderClient(sdk, "cid", "csecret", clock=Clock())
    client.start()
    sdk.connect()
    sdk.pending.pop(0).callback(None)
    for account_id in (1001, 1002, 1003):
        client.authorize_account(account_id, "tok")
        sdk.pending.pop(0).callback(None)

    sdk.disconnect()
    sdk.connect()
    sdk.pending.pop(0).callback(None)                  # app auth

    seen = []
    while sdk.pending:
        seen.append(sdk.sent[-1].ctidTraderAccountId)
        d = sdk.pending.pop(0)
        if seen[-1] == 1002:
            d.errback(Failure(RuntimeError("auth blew up")))
        else:
            d.callback(None)

    assert seen == [1001, 1002, 1003]


# ---------- T3: auth response fail-closed ----------

def _auth_envelope(payload) -> ProtoMessage:
    """The raw ProtoMessage envelope the SDK's send() resolves with."""
    return ProtoMessage(payloadType=payload.payloadType,
                        payload=payload.SerializeToString(), clientMsgId="x")


def test_account_auth_success_marks_authed():
    sdk, _, client = make()
    sdk.connect()
    client.authorize_account(1001, "tok")
    client._on_account_auth_response(
        _auth_envelope(ProtoOAAccountAuthRes(ctidTraderAccountId=1001)), 1001,
    )
    assert client.is_account_authed(1001)


def test_common_protocol_error_res_is_a_rejection():
    """T3: ProtoErrorRes (common protocol, payloadType 50) is a rejection
    just as much as ProtoOAErrorRes (2142).

    RED before the fix: only ProtoOAErrorRes was treated as failure, so a
    common-protocol error left the account marked authed -- and every
    subsequent trade for it went to the wire on a connection the server does
    not consider authorized, where it is rejected UNTAGGED, i.e. silently:
    no response Deferred to fail, no retry, no degraded status.
    """
    sdk, _, client = make()
    sdk.connect()
    client.authorize_account(1001, "tok")

    error = ProtoErrorRes(errorCode="CH_ACCESS_TOKEN_INVALID")
    assert error.payloadType == 50
    client._on_account_auth_response(_auth_envelope(error), 1001)

    assert not client.is_account_authed(1001)


def test_oa_error_res_is_a_rejection():
    sdk, _, client = make()
    sdk.connect()
    client.authorize_account(1001, "tok")
    client._on_account_auth_response(
        _auth_envelope(ProtoOAErrorRes(errorCode="CH_UNKNOWN_ACCOUNT")), 1001,
    )
    assert not client.is_account_authed(1001)


def test_undecodable_auth_response_fails_closed():
    """T3: a response Protobuf.extract cannot decode is not evidence of
    success. RED before the fix: `except Exception: payload = None` fell
    through to `_authed_accounts.add(account_id)`."""
    sdk, _, client = make()
    sdk.connect()
    client.authorize_account(1001, "tok")

    client._on_account_auth_response(object(), 1001)   # not a ProtoMessage at all

    assert not client.is_account_authed(1001)
