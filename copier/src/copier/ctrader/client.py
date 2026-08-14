"""Thin wrapper around the Spotware SDK client.

Owns: app auth on every (re)connect, per-account re-auth registry, 8 s heartbeat,
typed routing of pushed events. Reconnect scheduling itself is Twisted
ClientService's retryPolicy (see make_sdk_client).
"""
import logging
from collections import deque
from typing import Callable

from ctrader_open_api import Client, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoHeartbeatEvent
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq, ProtoOAAccountDisconnectEvent,
    ProtoOAAccountsTokenInvalidatedEvent, ProtoOAApplicationAuthReq,
    ProtoOAExecutionEvent, ProtoOASpotEvent)
from twisted.application.internet import backoffPolicy
from twisted.internet import defer, task
from twisted.python.failure import Failure

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S = 8.0   # server requires <= 10 s

# Max time send_no_reply() will wait for a connected transport before
# failing. Deliberately short: whenConnected() resolves with "the currently
# connected protocol, OR THE NEXT ONE TO CONNECT", so during a reconnect it
# can otherwise stay pending through the whole backoff (up to 60s, see
# make_sdk_client) and then hand a now-stale trade request to the wire at a
# since-moved price. A trade send must never wait out a reconnect backoff.
SEND_HANDOFF_TIMEOUT_S = 2.0


class _PerConnectionTcpProtocol(TcpProtocol):
    """TcpProtocol keeps its outbound queue (`_send_queue`) as a **class**
    attribute (`_send_queue = deque([])`, vendored, not ours to patch) that
    is mutated via .append()/.popleft() but never reassigned -- so by
    default every TCP connection in the process shares ONE process-global
    queue. A message queued on one connection can then be silently drained
    and written down a DIFFERENT, unrelated connection if the first
    disconnects before its 1s drain tick fires (e.g. a slave trade request
    flushed on the wrong shard/environment's socket). Shadowing
    `_send_queue` as an instance attribute on connect gives each connection
    its own private queue: a message can only ever be sent on the
    connection it was handed off to, and is simply dropped (never resent
    elsewhere) if that connection goes away first -- so this can never
    double-send.
    """

    def connectionMade(self):
        self._send_queue = deque()
        super().connectionMade()


def make_sdk_client(host: str, port: int) -> Client:
    return Client(host, port, _PerConnectionTcpProtocol,
                  retryPolicy=backoffPolicy(initialDelay=1.0, maxDelay=60.0, factor=2.0))


class CTraderClient:
    def __init__(self, sdk, client_id: str, client_secret: str, clock=None):
        if clock is None:
            from twisted.internet import reactor as clock  # pragma: no cover
        self._clock = clock
        self._sdk = sdk
        self._client_id = client_id
        self._client_secret = client_secret
        self._accounts: dict[int, str] = {}          # account_id -> access token
        self._exec_cbs: list[Callable] = []
        self._disc_cbs: list[Callable] = []
        self._invalid_cbs: list[Callable] = []
        self._spot_cbs: list[Callable] = []
        self.ready: defer.Deferred = defer.Deferred()
        self._hb = task.LoopingCall(self._heartbeat)
        self._hb.clock = clock
        sdk.setConnectedCallback(self._on_connected)
        sdk.setDisconnectedCallback(self._on_disconnected)
        sdk.setMessageReceivedCallback(self._on_message)

    def start(self) -> None:
        self._sdk.startService()

    def stop(self) -> None:
        if self._hb.running:
            self._hb.stop()
        self._sdk.stopService()

    def authorize_account(self, account_id: int, access_token: str) -> defer.Deferred:
        self._accounts[account_id] = access_token
        return self._send_account_auth(account_id)

    def deauthorize_account(self, account_id: int) -> None:
        self._accounts.pop(account_id, None)

    def send(self, msg) -> defer.Deferred:
        return self._sdk.send(msg)

    def send_no_reply(self, msg, timeout_s: float = SEND_HANDOFF_TIMEOUT_S) -> defer.Deferred:
        """Fire-and-forget send for trade requests (new order, close position,
        amend SL/TP, amend order, cancel order): the real cTrader server
        never sends a synchronous, clientMsgId-tagged reply to these --
        outcomes arrive later as untagged ProtoOAExecutionEvent broadcasts,
        routed through on_execution() (see testing/fake_server.py, which
        models this precisely).

        Unlike send(), this does NOT register a response Deferred/timeout
        that is guaranteed to never be satisfied by a reply: send() would
        eventually time out (its default responseTimeoutInSeconds) on every
        single trade request regardless of whether it actually succeeded,
        which is exactly what happened before this method existed -- every
        live-dispatched trade intent's underlying Deferred spuriously failed
        ~5s after being sent, which Dispatcher treats as an ambiguous
        failure and marks the account degraded.

        The wait for a connected transport is itself bounded to `timeout_s`
        (see SEND_HANDOFF_TIMEOUT_S): whenConnected() resolves with "the
        currently-connected protocol, or the NEXT one to connect", so
        without a bound this could otherwise stay pending through an entire
        reconnect backoff (up to 60s) and then hand off a now-stale trade at
        a since-moved price -- a caller (Dispatcher) chaining this with no
        timeout of its own would see total silence for that whole window: no
        event row, no alert, no degraded status. If no connection becomes
        available in time, this fails WITHOUT ever calling protocol.send()
        -- the message is never queued, so the caller can always safely
        treat this as "never reached the wire" and retry. A connection-level
        failure (e.g. all reconnect attempts exhausted) fails the same way.
        The timer is armed only *after* whenConnected() has been called (and
        returned a Deferred) rather than before: whenConnected() raises
        synchronously (automat's NoTransition) if called before the client
        has ever started, so arming it first would leak a DelayedCall on
        that path.

        Deliberately does NOT call .cancel() on the Deferred returned by
        whenConnected(): the vendored SDK's _awaitingConnected waiter list
        removes an entry only when it fires it (see
        twisted/application/internet.py _awaitingConnection/_unawait), not
        when the caller cancels the Deferred it was handed -- cancelling it
        directly would leave a phantom entry that the SDK later tries to
        fire a second time, raising AlreadyCalledError deep inside
        ClientService's state machine (which would also break firing of any
        OTHER, legitimately-still-waiting whenConnected() callers, since
        `_unawait` fires all waiters in a single unguarded loop). Instead,
        this races whenConnected() against its own independent timer: if the
        timer wins, this Deferred fails and protocol.send() is simply never
        invoked; if whenConnected() later fires anyway (late), the result is
        silently discarded rather than acted on.

        Sends with instant=True: TcpProtocol.send()'s default (instant=False)
        only *enqueues* onto _send_queue, drained by a <=1s-cadence
        LoopingCall -- if the connection dies inside that window (now that
        each connection has its own private queue, see
        _PerConnectionTcpProtocol, so the message can no longer be flushed
        by a *different* connection either) the message is silently
        dropped, yet this method had already reported success: no retry, no
        event, no degraded status, and reconcile can't catch it either
        (it only inspects 'active' mappings; a never-activated 'pending' one
        is invisible to every drift check). instant=True calls
        protocol.sendString() -- and therefore self.transport.write() --
        synchronously, in the SAME reactor turn whenConnected() confirms the
        protocol is connected, so there is no queue, no drain window, and
        nothing to die inside. This can only make the previously-silent
        "dropped but reported success" case into, at worst, "handed to a
        TCP transport that itself then fails" -- an ordinary connection-
        level failure, which is exactly what whenConnected()/_fail already
        handle correctly (ambiguous outcome -> SendNotAttempted -> retry,
        never a silent no-op). Only this method's call is changed to
        instant=True; send() (app/account auth, heartbeat, and every other
        request type) is untouched and still queues.
        """
        result: defer.Deferred = defer.Deferred()
        settled = [False]

        def _fail(failure: Failure) -> None:
            if not settled[0]:
                settled[0] = True
                result.errback(failure)

        def _on_connected(protocol) -> None:
            if settled[0]:
                # Already timed out and reported to the caller (see the
                # cancellation note above) -- do not send a message for an
                # operation the caller has already been told failed.
                return
            try:
                sent = protocol.send(msg, instant=True, clientMsgId=str(id(msg)))
            except BaseException:
                # A synchronous raise here (unreachable for the protobufs
                # Dispatcher builds today, but this is the live trade-send
                # path) must not strand `result` forever: settled[0] is
                # still False, so _fail is free to errback it, exactly as
                # any other failure mode does.
                _fail(Failure())
                return
            settled[0] = True
            result.callback(sent)

        def _on_timeout() -> None:
            _fail(Failure(defer.TimeoutError(
                f"send_no_reply: no connected transport within {timeout_s}s")))

        d = self._sdk.whenConnected(failAfterFailures=1)

        timeout_call = self._clock.callLater(timeout_s, _on_timeout)

        def _cancel_pending_timeout(_ignored=None) -> None:
            if timeout_call.active():
                timeout_call.cancel()

        d.addCallbacks(_on_connected, _fail)
        d.addBoth(_cancel_pending_timeout)
        return result

    def on_execution(self, cb) -> None: self._exec_cbs.append(cb)
    def on_account_disconnect(self, cb) -> None: self._disc_cbs.append(cb)
    def on_tokens_invalidated(self, cb) -> None: self._invalid_cbs.append(cb)
    def on_spot(self, cb) -> None: self._spot_cbs.append(cb)

    # ---- internals ----

    def _on_connected(self, _sdk) -> None:
        req = ProtoOAApplicationAuthReq()
        req.clientId = self._client_id
        req.clientSecret = self._client_secret
        d = self._sdk.send(req)
        d.addCallback(self._on_app_authed)
        d.addErrback(lambda f: log.error("app auth failed: %s", f))

    def _on_app_authed(self, _res) -> None:
        if not self._hb.running:
            self._hb.start(HEARTBEAT_INTERVAL_S, now=False)
        for account_id in list(self._accounts):
            self._send_account_auth(account_id)
        if not self.ready.called:
            self.ready.callback(self)

    def _send_account_auth(self, account_id: int) -> defer.Deferred:
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = account_id
        req.accessToken = self._accounts[account_id]
        d = self._sdk.send(req)
        d.addErrback(lambda f: log.error("account auth %s failed: %s", account_id, f))
        return d

    def _on_disconnected(self, _sdk, reason) -> None:
        log.warning("disconnected: %s", reason)
        if self._hb.running:
            self._hb.stop()

    def _heartbeat(self) -> None:
        d = self._sdk.send(ProtoHeartbeatEvent())
        d.addErrback(lambda _f: None)   # heartbeats have no response; ignore timeouts

    def _on_message(self, _sdk, message) -> None:
        payload = message
        if not isinstance(message, (ProtoOAExecutionEvent, ProtoOASpotEvent,
                                    ProtoOAAccountsTokenInvalidatedEvent,
                                    ProtoOAAccountDisconnectEvent)):
            try:
                payload = Protobuf.extract(message)
            except Exception:
                return
        if isinstance(payload, ProtoOAExecutionEvent):
            for cb in self._exec_cbs:
                try:
                    cb(payload.ctidTraderAccountId, payload)
                except Exception:
                    log.exception("execution callback raised")
        elif isinstance(payload, ProtoOASpotEvent):
            for cb in self._spot_cbs:
                try:
                    cb(payload)
                except Exception:
                    log.exception("spot callback raised")
        elif isinstance(payload, ProtoOAAccountDisconnectEvent):
            for cb in self._disc_cbs:
                try:
                    cb(payload.ctidTraderAccountId)
                except Exception:
                    log.exception("account disconnect callback raised")
        elif isinstance(payload, ProtoOAAccountsTokenInvalidatedEvent):
            for cb in self._invalid_cbs:
                try:
                    cb(list(payload.ctidTraderAccountIds))
                except Exception:
                    log.exception("tokens invalidated callback raised")
