from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoHeartbeatEvent
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAAccountAuthReq, ProtoOAApplicationAuthReq, ProtoOAExecutionEvent)
from twisted.internet import defer
from twisted.internet.task import Clock

from copier.ctrader.client import HEARTBEAT_INTERVAL_S, CTraderClient


class StubSdk:
    def __init__(self):
        self.sent = []
        self.running = False
        self._connected_cb = self._disconnected_cb = self._message_cb = None

    def setConnectedCallback(self, cb): self._connected_cb = cb
    def setDisconnectedCallback(self, cb): self._disconnected_cb = cb
    def setMessageReceivedCallback(self, cb): self._message_cb = cb
    def startService(self): self.running = True
    def stopService(self): self.running = False

    def send(self, msg, **kwargs):
        self.sent.append(msg)
        return defer.succeed(None)

    # test helpers
    def connect(self): self._connected_cb(self)
    def disconnect(self): self._disconnected_cb(self, "lost")
    def deliver(self, payload): self._message_cb(self, payload)


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
