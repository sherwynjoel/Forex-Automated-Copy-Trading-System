"""Thin wrapper around the Spotware SDK client.

Owns: app auth on every (re)connect, per-account re-auth registry, 8 s heartbeat,
typed routing of pushed events, and the TLS endpoint the SDK connects over
(see make_sdk_client / build_tls_endpoint). Reconnect scheduling itself is
Twisted ClientService's retryPolicy (see make_sdk_client).
"""
import logging
import os
from collections import deque
from pathlib import Path
from typing import Callable

from ctrader_open_api import Client, Protobuf, TcpProtocol
from ctrader_open_api.factory import Factory
from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
    ProtoErrorRes, ProtoHeartbeatEvent)
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq, ProtoOAAccountDisconnectEvent,
    ProtoOAAccountsTokenInvalidatedEvent, ProtoOAApplicationAuthReq,
    ProtoOAErrorRes, ProtoOAExecutionEvent, ProtoOAMarginCallTriggerEvent,
    ProtoOAOrderErrorEvent, ProtoOASpotEvent, ProtoOATraderUpdatedEvent)
from twisted.application.internet import ClientService, backoffPolicy
from twisted.internet import defer, ssl, task
from twisted.internet.endpoints import HostnameEndpoint, wrapClientTLS
from twisted.protocols.basic import Int32StringReceiver
from twisted.python.failure import Failure

log = logging.getLogger(__name__)

# ---- TLS knobs (see build_tls_endpoint) --------------------------------
#
# Both are OFF unless the corresponding environment variable is explicitly
# set, so the DEFAULT path -- the one every real demo/live cTrader
# connection takes -- always verifies the server certificate chain against
# the platform trust store AND checks the hostname. Nothing here can weaken
# that by accident: an unset/empty variable is inert.
TLS_INSECURE_ENV = "CTRADER_TLS_INSECURE"   # "1"/"true"/"yes" -> NO verification (tests only)
TLS_CA_FILE_ENV = "CTRADER_TLS_CA_FILE"     # path to a PEM CA to trust INSTEAD of platformTrust

HEARTBEAT_INTERVAL_S = 8.0   # server requires <= 10 s

# Max time send_no_reply() will wait for a connected transport before
# failing. Deliberately short: whenConnected() resolves with "the currently
# connected protocol, OR THE NEXT ONE TO CONNECT", so during a reconnect it
# can otherwise stay pending through the whole backoff (up to 60s, see
# make_sdk_client) and then hand a now-stale trade request to the wire at a
# since-moved price. A trade send must never wait out a reconnect backoff.
SEND_HANDOFF_TIMEOUT_S = 2.0

# The vendored TcpProtocol drains its outbound queue on a ONCE-PER-SECOND
# LoopingCall (`self._send_task.start(1)`), sending at most
# numberOfMessagesToSendPerSecond (SDK default 5) per tick. The 1s CADENCE
# -- not the 5-message budget -- meant every queued broker round trip paid
# up to a full second of pure queue wait: a 3-round-trip query (/details)
# had a deterministic ~2.4s floor and /analytics ~4s (measured on prod,
# 2026-08-20). Draining every DRAIN_TICK_S with DRAIN_BUDGET_PER_TICK
# messages per tick cuts that wait to <=100ms while keeping a hard
# budget/tick = 10 msg/s wire ceiling for the queued path: Spotware's
# server cap is 50 req/s, and the trade path (instant=True, bypasses this
# queue) is separately bucketed at 40/s by the Dispatcher's TokenBucket,
# so 10/s here keeps even the combined worst case at the cap.
DRAIN_TICK_S = 0.1
DRAIN_BUDGET_PER_TICK = 1


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

    _drain_clock = None  # tests inject a twisted Clock; None = global reactor

    def connectionMade(self):
        self._send_queue = deque()
        # Replicates TcpProtocol.connectionMade (small, pinned SDK 0.9.2)
        # instead of calling it: the SDK hardcodes .start(1) on its drain
        # LoopingCall, and the faster DRAIN_TICK_S cadence is the whole
        # point -- see the constant's comment for the latency/cap math.
        Int32StringReceiver.connectionMade(self)
        if not self._send_task:
            self._send_task = task.LoopingCall(self._sendStrings)
        if self._drain_clock is not None:
            self._send_task.clock = self._drain_clock
        self._send_task.start(DRAIN_TICK_S)
        self.factory.connected(self)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def client_tls_options(host: str):
    """Build the TLS connection creator used for every cTrader connection.

    DEFAULT (nothing set in the environment): `optionsForClientTLS(host,
    trustRoot=platformTrust())` -- the server's certificate chain must
    validate against the operating system's CA store AND its identity must
    match `host`. This is what the vendored SDK does NOT do: it builds its
    endpoint from the string `f"ssl:{host}:{port}"`, and Twisted's parser
    for that form (`_parseClientSSL`/`_parseClientSSLOptions`) only enables
    verification when a `hostname=` or `caCertsDir=` parameter is present in
    the string -- neither ever is -- so it produces
    `CertificateOptions(trustRoot=None)`, i.e. VERIFY_NONE with no hostname
    check. Real-money OAuth access tokens and trade traffic were therefore
    being handed to whatever answered on the far end of an unauthenticated
    TLS handshake.

    Two escape hatches exist, both strictly opt-in via environment variable
    and both inert when unset (see TLS_INSECURE_ENV / TLS_CA_FILE_ENV):

    - CTRADER_TLS_INSECURE=1 -> no verification at all. This exists ONLY so
      the test rigs that speak to `FakeCTraderServer`'s in-process,
      per-run self-signed certificate (copier/tests/integration and the
      `fake-ctrader` service in docker-compose.test.yml) can connect. It is
      set in exactly those two places, never in docker-compose.yml, and it
      logs a WARNING every time it is honoured so an accidental production
      set is visible in the copier's logs.
    - CTRADER_TLS_CA_FILE=/path/ca.pem -> verify against that CA instead of
      the platform store (hostname checking still applies). For a private
      CA / pinned test CA; unused by this repo's own test rigs but the
      non-blunt alternative to the insecure knob.
    """
    if _env_flag(TLS_INSECURE_ENV):
        log.warning(
            "%s is set: TLS certificate and hostname verification are DISABLED "
            "for cTrader connections to %s. This must only ever be set by the "
            "test rigs; unset it for any real broker connection.",
            TLS_INSECURE_ENV, host,
        )
        # trustRoot=None -> OpenSSL VERIFY_NONE and no hostname check; this
        # reproduces the vendored SDK's old behaviour verbatim, on purpose,
        # so the fake server's self-signed cert is accepted.
        return ssl.CertificateOptions()

    ca_file = os.environ.get(TLS_CA_FILE_ENV, "").strip()
    if ca_file:
        trust_root = ssl.Certificate.loadPEM(Path(ca_file).read_bytes())
        log.info("using custom CA %s for cTrader TLS (hostname checking still on)", ca_file)
        return ssl.optionsForClientTLS(host, trustRoot=trust_root)

    return ssl.optionsForClientTLS(host, trustRoot=ssl.platformTrust())


def build_tls_endpoint(reactor_, host: str, port: int):
    """A verifying TLS client endpoint for (host, port).

    `wrapClientTLS` over a plain `HostnameEndpoint` rather than
    `clientFromString("ssl:...")`: it is the only form that lets us hand in
    our own connection creator (see client_tls_options) instead of the
    string parser's verify-nothing default.
    """
    return wrapClientTLS(client_tls_options(host), HostnameEndpoint(reactor_, host, port))


class _VerifiedTLSClient(Client):
    """`ctrader_open_api.Client` with a caller-supplied endpoint.

    The vendored `Client.__init__` (SDK 0.9.2) hardcodes
    `clientFromString(reactor, f"ssl:{host}:{port}")` -- the verify-nothing
    endpoint T1 exists to remove -- and there is no hook to override it: the
    endpoint is passed straight into `ClientService.__init__`, which buries
    it in a private state machine (`self._machine._endpoint`). So this
    bypasses `Client.__init__` entirely and re-does the (small, pinned:
    ctrader-open-api==0.9.2) remainder of its body verbatim against
    `ClientService.__init__`, with `endpoint` supplied by the caller. Every
    other Client method is inherited unchanged.
    """

    def __init__(self, host, port, protocol, endpoint, retryPolicy=None, clock=None,
                 prepareConnection=None, numberOfMessagesToSendPerSecond=5):
        from twisted.internet import reactor as _reactor
        # Verbatim from Client.__init__, minus the clientFromString endpoint.
        self._runningReactor = _reactor
        self.numberOfMessagesToSendPerSecond = numberOfMessagesToSendPerSecond
        factory = Factory.forProtocol(protocol, client=self)
        ClientService.__init__(self, endpoint, factory, retryPolicy=retryPolicy,
                               clock=clock, prepareConnection=prepareConnection)
        self._events = dict()
        self._responseDeferreds = dict()
        self.isConnected = False


def make_sdk_client(host: str, port: int, reactor_=None) -> Client:
    """Build the SDK client for (host, port) over a VERIFYING TLS endpoint.

    See build_tls_endpoint/client_tls_options for the verification contract
    and the two opt-in test knobs.
    """
    if reactor_ is None:
        from twisted.internet import reactor as reactor_
    return _VerifiedTLSClient(
        host, port, _PerConnectionTcpProtocol,
        endpoint=build_tls_endpoint(reactor_, host, port),
        retryPolicy=backoffPolicy(initialDelay=1.0, maxDelay=60.0, factor=2.0),
        # Read by _sendStrings as the PER-TICK allowance (see DRAIN_TICK_S).
        numberOfMessagesToSendPerSecond=DRAIN_BUDGET_PER_TICK,
    )


class CTraderClient:
    def __init__(self, sdk, client_id: str, client_secret: str, clock=None):
        if clock is None:
            from twisted.internet import reactor as clock  # pragma: no cover
        self._clock = clock
        self._sdk = sdk
        self._client_id = client_id
        self._client_secret = client_secret
        self._accounts: dict[int, str] = {}          # account_id -> access token
        # account_id -> confirmed account-authed on the CURRENT connection
        # (cleared on every disconnect, repopulated by re-auth). Distinct
        # from _accounts, which is just a registry of "authorize_account()
        # was ever called" -- not proof of auth on this connection. See
        # is_account_authed() / NEW-1.
        self._authed_accounts: set[int] = set()
        self._exec_cbs: list[Callable] = []
        self._disc_cbs: list[Callable] = []
        self._invalid_cbs: list[Callable] = []
        self._spot_cbs: list[Callable] = []
        self._trader_updated_cbs: list[Callable] = []
        self._margin_call_cbs: list[Callable] = []
        self._order_error_cbs: list[Callable] = []
        self._conn_lost_cbs: list[Callable] = []
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
        self._authed_accounts.discard(account_id)

    def is_account_authed(self, account_id: int) -> bool:
        """True once a (non-error) response to this account's
        ProtoOAAccountAuthReq has been received on the CURRENT connection.

        NEW-1: send_no_reply's instant=True write reaches the wire in the
        same reactor turn whenConnected() confirms a connection -- there is
        no longer a FIFO drain queue serializing it behind this connection's
        (still-queued) account-auth request the way there incidentally was
        before instant=True. Gating a trade send on this (see
        main.py:send_for_account) instead of on _accounts registry
        membership means a send attempted before re-auth completes raises
        SendNotAttempted -- provably nothing reached the wire -- so the
        dispatcher's normal retry ladder (1s/2s/4s) applies and the retry
        lands once auth actually completes, rather than racing the wire
        ahead of it and being silently rejected server-side.
        """
        return account_id in self._authed_accounts

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
        only *enqueues* onto _send_queue, drained by a DRAIN_TICK_S-cadence
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
    def on_disconnected(self, cb) -> None:
        """cb() fires when the CONNECTION drops (vs on_account_disconnect,
        which is the broker's per-account event). Connection-scoped broker
        state (spot subscriptions, account auth) dies with the socket, so
        holders of such state register here to reset it."""
        self._conn_lost_cbs.append(cb)
    def on_tokens_invalidated(self, cb) -> None: self._invalid_cbs.append(cb)
    def on_spot(self, cb) -> None: self._spot_cbs.append(cb)
    def on_trader_updated(self, cb) -> None: self._trader_updated_cbs.append(cb)
    def on_margin_call(self, cb) -> None: self._margin_call_cbs.append(cb)
    def on_order_error(self, cb) -> None: self._order_error_cbs.append(cb)

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
        d = self._reauth_all()
        d.addErrback(lambda f: log.error("bulk re-auth failed: %s", f))
        d.addBoth(self._fire_ready)

    def _fire_ready(self, _ignored=None) -> None:
        """Fire `ready` once the bulk re-auth for this connection is done.

        Ordering matters and is unchanged from before T2: `ready` fires
        AFTER the account-auth work for this connection has been handed off,
        never before it. On a FIRST connect `_accounts` is still empty (the
        only callers of authorize_account -- CopierApp._connect_and_authorize
        and reload() -- wait on `ready` first), so `_reauth_all` completes
        synchronously and this fires in the same turn, exactly as it used
        to. Firing it EARLIER would let a waiter's authorize_account() run
        in the middle of `_reauth_all`, so the account it just registered
        would be auth'd twice on the same connection.
        """
        if not self.ready.called:
            self.ready.callback(self)

    @defer.inlineCallbacks
    def _reauth_all(self):
        """Re-authorize every registered account, ONE AT A TIME.

        T2: the previous version fired `_send_account_auth` for all accounts
        in a single reactor turn. Each one goes through the vendored SDK's
        `send()`, which arms its 5 s response timeout AT SEND TIME while the
        messages themselves drain out of TcpProtocol's queue at only
        `numberOfMessagesToSendPerSecond` per drain tick (5 per 1s tick when
        this was written; DRAIN_BUDGET_PER_TICK per DRAIN_TICK_S now -- the
        serialization below is what matters, not the rate). So on a reconnect
        with N accounts, everything at queue index >= ~25 timed out before
        it was ever written; the SDK's `isCanceled` check then DROPPED it
        from the queue instead of sending it, and nothing ever retried --
        those accounts stayed un-authed for the whole life of that
        connection. At the ~50-account target with SHARDS=1, every
        reconnect blip permanently silenced half the fleet (every send for
        them raising SendNotAttempted via main.send_for_account's
        is_account_authed gate) until a manual reload.

        Awaiting each auth before sending the next is exactly what
        CopierApp._connect_and_authorize already does for the initial
        authorization pass, and it means a request's 5 s timer only starts
        once its predecessor has been answered -- the queue never builds a
        backlog deeper than one message, so the timeout can never expire on
        a message that has not been written yet. See the README's SHARDS
        note for sizing beyond one connection.
        """
        for account_id in list(self._accounts):
            # _send_account_auth already attaches an errback that logs and
            # swallows, so this never raises and one bad account can never
            # abandon the accounts queued behind it.
            yield self._send_account_auth(account_id)

    def _send_account_auth(self, account_id: int) -> defer.Deferred:
        req = ProtoOAAccountAuthReq()
        req.ctidTraderAccountId = account_id
        req.accessToken = self._accounts[account_id]
        d = self._sdk.send(req)
        d.addCallback(self._on_account_auth_response, account_id)
        d.addErrback(lambda f: log.error("account auth %s failed: %s", account_id, f))
        return d

    def _on_account_auth_response(self, response, account_id: int):
        """Mark `account_id` authed on the current connection ONLY on a
        response this method can positively read as non-rejection.

        `response` is send()'s raw ProtoMessage envelope; unwrap it to
        inspect it. T3 -- this gate guards a real-money send path
        (main.send_for_account refuses to put a trade on the wire for an
        account that is not authed here), so both of the previously
        fail-OPEN outcomes are now fail-CLOSED, and both are logged rather
        than passing silently:

        - `ProtoErrorRes` (common protocol, payloadType 50) is a rejection
          just as much as `ProtoOAErrorRes` (2142) is; treating only the
          latter as failure meant a common-protocol error left the account
          marked authed and every subsequent trade for it going to the wire
          on a connection the server does not consider authorized -- where
          it is rejected untagged, i.e. silently.
        - A response `Protobuf.extract` cannot decode at all is not
          evidence of success either. Leaving the account un-authed costs
          only a SendNotAttempted (which Dispatcher retries at 1s/2s/4s,
          and which the next reconnect's re-auth clears) instead of a
          silently-rejected live order.
        """
        try:
            payload = Protobuf.extract(response)
        except Exception as e:
            log.warning(
                "account auth %s: response could not be decoded (%s); "
                "treating account as NOT authed on this connection", account_id, e,
            )
            return response
        if isinstance(payload, (ProtoOAErrorRes, ProtoErrorRes)):
            log.error("account auth %s rejected: %s", account_id, payload.errorCode)
            return response
        self._authed_accounts.add(account_id)
        return response

    def _on_disconnected(self, _sdk, reason) -> None:
        log.warning("disconnected: %s", reason)
        if self._hb.running:
            self._hb.stop()
        self._authed_accounts.clear()
        for cb in list(self._conn_lost_cbs):
            try:
                cb()
            except Exception:
                log.exception("on_disconnected callback raised")

    def _heartbeat(self) -> None:
        d = self._sdk.send(ProtoHeartbeatEvent())
        d.addErrback(lambda _f: None)   # heartbeats have no response; ignore timeouts

    def _on_message(self, _sdk, message) -> None:
        payload = message
        if not isinstance(message, (ProtoOAExecutionEvent, ProtoOASpotEvent,
                                    ProtoOAAccountsTokenInvalidatedEvent,
                                    ProtoOAAccountDisconnectEvent,
                                    ProtoOATraderUpdatedEvent,
                                    ProtoOAMarginCallTriggerEvent)):
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
        elif isinstance(payload, ProtoOATraderUpdatedEvent):
            for cb in self._trader_updated_cbs:
                try:
                    cb(payload)
                except Exception:
                    log.exception("trader updated callback raised")
        elif isinstance(payload, ProtoOAMarginCallTriggerEvent):
            for cb in self._margin_call_cbs:
                try:
                    cb(payload)
                except Exception:
                    log.exception("margin call callback raised")
        elif isinstance(payload, (ProtoOAErrorRes, ProtoOAOrderErrorEvent)):
            # An untagged rejection of something send_no_reply already
            # wrote to the wire (no clientMsgId round trip exists for
            # these): there is no registered response Deferred to fail and
            # no typed callback list for it, so without this it was
            # silently dropped -- exactly the observability gap NEW-1's
            # gate exists to keep out of reach in the common case, but this
            # still logs any rejection that reaches the wire regardless of
            # cause (e.g. a rejected trade for reasons other than auth).
            log.error("server rejected a request: %s (account %s)",
                      payload.errorCode, getattr(payload, 'ctidTraderAccountId', None))
            # Order rejections specifically (MARKET_CLOSED on a weekend,
            # NOT_ENOUGH_MONEY...) get a typed callback so the dashboard can
            # tell the trader; generic ProtoOAErrorRes stays log-only (a
            # closed-market margin-estimate poll must not flood the events).
            if isinstance(payload, ProtoOAOrderErrorEvent):
                for cb in self._order_error_cbs:
                    try:
                        cb(payload)
                    except Exception:
                        log.exception("order error callback raised")
