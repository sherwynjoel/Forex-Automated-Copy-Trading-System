"""A cap on request bodies, enforced before routing.

FastAPI parses a whole multipart form (spooling file parts to the
container's temp space) before a route's dependencies -- auth included --
run, and Starlette puts no ceiling on a file part. Without this layer an
anonymous client could fill the disk with one request.

This is pure ASGI and sits outside every other middleware (registered last
in main.create_app). For an HTTP request:

  * a Content-Length over the limit is answered 413 at once, without
    reading a byte of the body;
  * otherwise `receive` is wrapped and the streamed bytes counted (a
    chunked body declares no length); the moment the count passes the
    limit the 413 goes out, the app sees `http.disconnect` in place of the
    rest of the body, and anything the app then sends is dropped.

The limits: UPLOAD_MAX_BODY_BYTES (6 MiB) for the investor file upload,
whose own cap on the file is 5 MB (uploads.MAX_UPLOAD_BYTES) plus multipart
framing; DEFAULT_MAX_BODY_BYTES (1 MiB) for every other request. The MT5
door (hello 512 KiB, sync 256 KiB) and the TradingView receiver (4 KiB)
keep their own smaller caps and their own 413 body underneath this one --
the default must never drop below them (tests/test_body_limit.py holds it
to that).
"""
from __future__ import annotations

import json
import re

MIB = 1024 * 1024
UPLOAD_MAX_BODY_BYTES = 6 * MIB
DEFAULT_MAX_BODY_BYTES = 1 * MIB

_UPLOAD_PATH = re.compile(r"/api/orgs/[^/]+/investor/files/?")
_TOO_LARGE = json.dumps({"detail": "request body too large"}).encode()


def body_limit_for(method: str, path: str) -> int:
    if method == "POST" and _UPLOAD_PATH.fullmatch(path):
        return UPLOAD_MAX_BODY_BYTES
    return DEFAULT_MAX_BODY_BYTES


def _declared_length(scope) -> int | None:
    for name, value in scope.get("headers") or ():
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None  # malformed: the server refuses it; count instead
    return None


async def _reject(send) -> None:
    await send({"type": "http.response.start", "status": 413, "headers": [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(_TOO_LARGE)).encode()),
        # The unread rest of the body makes the connection unusable.
        (b"connection", b"close"),
    ]})
    await send({"type": "http.response.body", "body": _TOO_LARGE})


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = body_limit_for(scope["method"], scope["path"])
        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            await _reject(send)
            return

        received = 0
        started = False   # the app has begun its own response
        rejected = False  # the body passed the limit

        async def counted_receive():
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    rejected = True
                    if not started:
                        await _reject(send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            nonlocal started
            if rejected:
                return  # the 413 already answered; the app's reply is moot
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, guarded_send)
        except Exception:
            # The app's reaction to a body cut off mid-read (ClientDisconnect,
            # a parse error it could not answer) is not an error to report:
            # the client already has its 413.
            if not rejected:
                raise
