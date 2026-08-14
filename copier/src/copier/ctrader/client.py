"""Thin wrapper around the Spotware SDK client.

Owns: app auth on every (re)connect, per-account re-auth registry, 8 s heartbeat,
typed routing of pushed events. Reconnect scheduling itself is Twisted
ClientService's retryPolicy (see make_sdk_client).
"""
import logging
from typing import Callable

from ctrader_open_api import Client, Protobuf, TcpProtocol
from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoHeartbeatEvent
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq, ProtoOAAccountDisconnectEvent,
    ProtoOAAccountsTokenInvalidatedEvent, ProtoOAApplicationAuthReq,
    ProtoOAExecutionEvent, ProtoOASpotEvent)
from twisted.application.internet import backoffPolicy
from twisted.internet import defer, task

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S = 8.0   # server requires <= 10 s


def make_sdk_client(host: str, port: int) -> Client:
    return Client(host, port, TcpProtocol,
                  retryPolicy=backoffPolicy(initialDelay=1.0, maxDelay=60.0, factor=2.0))


class CTraderClient:
    def __init__(self, sdk, client_id: str, client_secret: str, clock=None):
        if clock is None:
            from twisted.internet import reactor as clock  # pragma: no cover
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

    def send_no_reply(self, msg) -> defer.Deferred:
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

        The returned Deferred fires once the message has been handed to a
        connected transport; a connection-level failure (e.g. not currently
        connected) still propagates as a failure, exactly as it does for
        send() -- both go through the same whenConnected() gate.
        """
        d = self._sdk.whenConnected(failAfterFailures=1)
        d.addCallback(lambda protocol: protocol.send(msg, clientMsgId=str(id(msg))))
        return d

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
