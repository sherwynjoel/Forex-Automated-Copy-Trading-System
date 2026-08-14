from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoHeartbeatEvent
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq, ProtoOAApplicationAuthReq, ProtoOAExecutionEvent)
from twisted.internet import defer
from twisted.internet.task import Clock

from copier.ctrader.client import (
    HEARTBEAT_INTERVAL_S, SEND_HANDOFF_TIMEOUT_S, CTraderClient)


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
    assert not isinstance(d.result, Exception)
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
    assert not isinstance(d.result, Exception)


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
