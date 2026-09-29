# api/tests/test_body_limit.py
"""The request body cap (api/body_limit.py): a pure ASGI layer outside
every other middleware that answers 413 before routing -- from the
Content-Length header without reading a byte, or by counting a streamed
(chunked) body -- so no route ever parses an unbounded body. 6 MiB for the
investor file upload, 1 MiB for everything else; the machine doors keep
their own smaller caps underneath it."""
import asyncio
import json

import pytest

from api import body_limit as bl
from api.routes import mt5 as m5
from api.routes import webhooks as wh

MIB = 1024 * 1024
TOO_LARGE = {"detail": "request body too large"}


# ---------------------------------------------------------------- the limits


@pytest.mark.parametrize("method, path, limit", [
    ("POST", "/api/orgs/7/investor/files", 6 * MIB),
    ("POST", "/api/orgs/7/investor/files/", 6 * MIB),
    ("GET", "/api/orgs/7/investor/files", 1 * MIB),
    ("POST", "/api/orgs/7/investor/deposits", 1 * MIB),
    ("POST", "/api/orgs/7/investor/files/extra", 1 * MIB),
    ("POST", "/api/mt5/sync", 1 * MIB),
    ("POST", "/api/webhooks/tradingview/abc", 1 * MIB),
    ("POST", "/api/login", 1 * MIB),
])
def test_the_limit_for_each_route(method, path, limit):
    assert bl.body_limit_for(method, path) == limit


def test_the_default_never_undercuts_a_routes_own_larger_cap():
    """The MT5 door and the TradingView receiver answer their own 413
    (their own body shape) below this layer; the default must sit at or
    above every such cap or it would silently shrink them."""
    for cap in (m5.HELLO_MAX_BODY_BYTES, m5.SYNC_MAX_BODY_BYTES, wh.MAX_BODY_BYTES):
        assert bl.DEFAULT_MAX_BODY_BYTES >= cap
    assert bl.DEFAULT_MAX_BODY_BYTES == 1 * MIB
    assert bl.UPLOAD_MAX_BODY_BYTES == 6 * MIB


# ------------------------------------------------ the middleware, pure ASGI


class _Inner:
    """An app that reads the whole body, then answers 200 -- recording
    what it saw, so a test can prove what reached it."""

    def __init__(self):
        self.called = False
        self.messages = []

    async def __call__(self, scope, receive, send):
        self.called = True
        while True:
            message = await receive()
            self.messages.append(message["type"])
            if message["type"] == "http.disconnect" or not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _run(app, *, path="/api/orgs/1/settings", method="PUT", headers=(), chunks=()):
    scope = {"type": "http", "method": method, "path": path,
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers]}
    queue = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
             for i, c in enumerate(chunks)] or [{"type": "http.request", "body": b""}]
    pulled = []
    sent = []

    async def receive():
        pulled.append(len(pulled))
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent, pulled


def _status(sent):
    return sent[0]["status"], json.loads(b"".join(m.get("body", b"") for m in sent[1:]))


def test_a_declared_length_over_the_limit_is_413_without_reading_the_body():
    inner = _Inner()
    sent, pulled = _run(bl.BodyLimitMiddleware(inner),
                        headers=[("Content-Length", str(MIB + 1))], chunks=[b"x"])
    assert _status(sent) == (413, TOO_LARGE)
    assert not inner.called and pulled == []


def test_a_streamed_body_is_counted_and_cut_at_the_limit():
    """Chunked: no Content-Length, 400 KiB at a time. The third chunk
    crosses 1 MiB; the 413 goes out then, the app sees a disconnect in
    place of the body, anything it answers is dropped, and the fourth chunk
    is never pulled from the client."""
    inner = _Inner()
    chunk = b"x" * (400 * 1024)
    sent, pulled = _run(bl.BodyLimitMiddleware(inner), chunks=[chunk] * 4)
    assert _status(sent) == (413, TOO_LARGE)
    assert len(sent) == 2
    assert inner.messages == ["http.request", "http.request", "http.disconnect"]
    assert len(pulled) == 3


def test_a_body_at_the_limit_passes_through_untouched():
    inner = _Inner()
    sent, _ = _run(bl.BodyLimitMiddleware(inner),
                   headers=[("Content-Length", str(MIB))], chunks=[b"x" * (MIB // 2)] * 2)
    assert sent[0]["status"] == 200 and inner.messages == ["http.request", "http.request"]


def test_the_upload_route_gets_six_mebibytes():
    inner = _Inner()
    sent, _ = _run(bl.BodyLimitMiddleware(inner), method="POST",
                   path="/api/orgs/3/investor/files",
                   chunks=[b"x" * MIB] * 6)
    assert sent[0]["status"] == 200
    sent, _ = _run(bl.BodyLimitMiddleware(_Inner()), method="POST",
                   path="/api/orgs/3/investor/files",
                   headers=[("Content-Length", str(6 * MIB + 1))], chunks=[b"x"])
    assert _status(sent) == (413, TOO_LARGE)


def test_websockets_and_lifespan_pass_straight_through():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    for kind in ("websocket", "lifespan"):
        asyncio.run(bl.BodyLimitMiddleware(inner)({"type": kind, "path": "/api/ws"},
                                                   None, None))
    assert seen == ["websocket", "lifespan"]


# ----------------------------------------------------- through the real app


def test_a_two_mebibyte_json_body_to_a_normal_route_is_413(app_client):
    big = json.dumps({"email": "a@example.com", "password": "x" * (2 * MIB)})
    r = app_client.post("/api/login", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json() == TOO_LARGE


def test_a_chunked_two_mebibyte_body_is_413_too(app_client):
    """No Content-Length: the body arrives as a stream and is counted."""
    big = json.dumps({"email": "a@example.com", "password": "x" * (2 * MIB)}).encode()
    r = app_client.post("/api/login", content=iter([big]),
                        headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json() == TOO_LARGE


def test_a_normal_sized_body_still_reaches_the_route(app_client):
    r = app_client.post("/api/login", json={"email": "nobody@example.com", "password": "nope"})
    assert r.status_code == 401


WRONG = "mt5_not-the-key-0123456789abcdefghijklmnopq"


def _door(client, path, size):
    return client.post(path, content=b"x" * size,
                       headers={m5.KEY_HEADER: WRONG, "Content-Type": "application/json"})


def test_the_mt5_door_still_accepts_its_maximum_bodies(org_client):
    """A body exactly at the door's own cap gets past both size gates and
    is refused only for its (wrong) key; one byte over is the door's own
    413 shape; past 1 MiB this layer answers first."""
    client, _, _ = org_client
    assert _door(client, "/api/mt5/sync", m5.SYNC_MAX_BODY_BYTES).status_code == 401
    assert _door(client, "/api/mt5/hello", m5.HELLO_MAX_BODY_BYTES).status_code == 401
    r = _door(client, "/api/mt5/sync", m5.SYNC_MAX_BODY_BYTES + 1)
    assert r.status_code == 413 and r.json() == {"status": "rejected", "reason": "body too large"}
    r = _door(client, "/api/mt5/sync", MIB + 1)
    assert r.status_code == 413 and r.json() == TOO_LARGE


def test_the_tradingview_receiver_keeps_its_own_cap(app_client):
    r = app_client.post("/api/webhooks/tradingview/nohook", content=b"x" * (wh.MAX_BODY_BYTES + 1),
                        headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json() == {"status": "rejected", "reason": "body too large"}
