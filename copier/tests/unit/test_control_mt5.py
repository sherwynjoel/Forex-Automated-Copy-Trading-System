"""The /mt5/* control routes (engine/control.py), driven with DummyRequest
against a fake app whose method signatures mirror CopierApp's."""

import json
from io import BytesIO

from twisted.web.test.requesthelper import DummyRequest

from copier.engine.control import (
    Mt5HelloResource, Mt5StatusResource, Mt5SyncResource, RootResource)


class _Mt5App:
    def __init__(self):
        self.calls = []

    def mt5_hello(self, account_id, body):
        self.calls.append(("hello", account_id, body))
        return {"last_deal_ticket": 700001}

    def mt5_sync(self, account_id, body):
        self.calls.append(("sync", account_id, body))
        if body.get("seq") == "bad":
            raise ValueError("sync: field 'seq' is not a int")
        return "OK\t1\t250\nCMD\t88\tclose\t669607966\t0\n"

    def mt5_status(self, account_id):
        self.calls.append(("status", account_id))
        return {"online": True, "last_seen_at": "2026-09-07T10:00:00+00:00",
                "pending_commands": 1, "hedging": True}


def _post(resource, path, body):
    request = DummyRequest(path)
    request.method = b"POST"
    request.content = BytesIO(json.dumps(body).encode())
    resource.render_POST(request)
    return request


def _written(request):
    return b"".join(request.written)


def test_hello_route_forwards_the_report_and_answers_json():
    app = _Mt5App()
    request = _post(Mt5HelloResource(app), [b"mt5", b"hello"],
                    {"account_id": 1000000000001, "org_id": 1, "report": {"login": 5}})
    assert app.calls == [("hello", 1000000000001, {"login": 5})]
    assert json.loads(_written(request)) == {"last_deal_ticket": 700001}
    assert request.responseHeaders.getRawHeaders(b"Content-Type") == [b"application/json"]


def test_sync_route_answers_the_ea_text_verbatim():
    app = _Mt5App()
    request = _post(Mt5SyncResource(app), [b"mt5", b"sync"],
                    {"account_id": 7, "org_id": 1, "report": {"seq": 1}})
    assert app.calls == [("sync", 7, {"seq": 1})]
    assert _written(request) == b"OK\t1\t250\nCMD\t88\tclose\t669607966\t0\n"
    assert request.responseHeaders.getRawHeaders(b"Content-Type") == [b"text/plain; charset=utf-8"]
    assert request.responseCode in (None, 200)


def test_sync_route_maps_a_bad_report_to_400_json():
    app = _Mt5App()
    request = _post(Mt5SyncResource(app), [b"mt5", b"sync"],
                    {"account_id": 7, "report": {"seq": "bad"}})
    assert request.responseCode == 400
    assert "seq" in json.loads(_written(request))["error"]


def test_routes_require_account_id_and_an_object_report():
    bodies = [{"report": {}}, {"account_id": "x", "report": {}},
              {"account_id": 7, "report": "nope"}, {"account_id": 7}]
    for body in bodies:
        request = _post(Mt5SyncResource(_Mt5App()), [b"mt5", b"sync"], body)
        assert request.responseCode == 400, body
        request = _post(Mt5HelloResource(_Mt5App()), [b"mt5", b"hello"], body)
        assert request.responseCode == 400, body


def test_status_route_parses_the_query_string():
    app = _Mt5App()
    request = DummyRequest([b"mt5", b"status"])
    request.method = b"GET"
    request.addArg(b"account_id", b"7")
    Mt5StatusResource(app).render_GET(request)
    assert app.calls == [("status", 7)]
    assert json.loads(_written(request))["pending_commands"] == 1

    missing = DummyRequest([b"mt5", b"status"])
    missing.method = b"GET"
    Mt5StatusResource(app).render_GET(missing)
    assert missing.responseCode == 400


def test_root_mounts_the_mt5_tree():
    root = RootResource(_Mt5App())
    mt5 = root.children[b"mt5"]
    assert set(mt5.children) == {b"hello", b"sync", b"status"}
