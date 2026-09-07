"""The MT5 wire format (copier/src/copier/mt5/protocol.py): JSON reports in,
tab-separated command lines out. Pure -- no database, no reactor."""

import pytest

from copier.mt5 import protocol as p

HELLO = {
    "v": 1, "ea": "1.0.0", "build": 4400, "login": 12345678, "broker": "XYZ Ltd",
    "server": "XYZ-Live3", "currency": "USD", "hedging": True, "trade_mode": "real",
    "leverage": 500,
    "symbols": [{"n": "XAUUSD.r", "d": 2, "cs": 100, "vmin": 0.01, "vstep": 0.01,
                 "vmax": 50, "tm": 4}],
    "chunk": 1, "chunks": 3,
}

SYNC = {
    "v": 1, "seq": 1042, "ts": 1757203200123, "balance": 9784.04, "equity": 9790.10,
    "margin": 120.5, "margin_free": 9669.6,
    "positions": [{"t": 669607966, "s": "XAUUSD.r", "side": "BUY", "lots": 0.01,
                   "open": 4468.49, "sl": 4460.0, "tp": 4480.0, "price": 4470.1,
                   "pnl": 1.61, "swap": 0, "comment": "copy:m669607900",
                   "magic": 20260907, "time": 1757203100}],
    "orders": [{"t": 5551, "s": "XAUUSD.r", "type": "BUY_LIMIT", "lots": 0.01,
                "price": 4450.0, "sl": 0, "tp": 0, "comment": "", "magic": 20260907}],
    "deals": [{"t": 700001, "pos": 669607966, "order": 700000, "s": "XAUUSD.r",
               "type": "BUY", "entry": "IN", "lots": 0.01, "price": 4468.49,
               "profit": 0, "swap": 0, "commission": -0.03, "time": 1757203100456,
               "comment": "copy:m669607900", "magic": 20260907}],
    "acks": [{"id": 88, "ok": True, "retcode": 10009, "msg": "done", "pos": 669607966,
              "deal": 700001, "order": 700000, "price": 4468.49, "lots": 0.01}],
}


class TestUnits:
    def test_lots_to_centilots_and_back(self):
        assert p.centilots(0.01) == 1
        assert p.centilots(1.0) == 100
        assert p.centilots(0.37) == 37
        assert p.lots(150) == 1.5
        assert p.lots(1) == 0.01

    def test_constants(self):
        assert (p.MT5_KEY_PREFIX, p.PROTOCOL_VERSION, p.CENTILOTS) == ("mt5_", 1, 100)
        assert (p.DEFAULT_POLL_MS, p.RETRY_POLL_MS, p.MAX_DEALS_PER_SYNC) == (250, 2000, 200)


class TestParseHello:
    def test_spec_example(self):
        hello = p.parse_hello(HELLO)
        assert (hello.login, hello.broker, hello.server, hello.currency) == (
            12345678, "XYZ Ltd", "XYZ-Live3", "USD")
        assert hello.hedging is True and hello.trade_mode == "real" and hello.leverage == 500
        assert (hello.ea_version, hello.ea_build, hello.chunk, hello.chunks) == (
            "1.0.0", 4400, 1, 3)
        assert hello.symbols == [p.HelloSymbol(
            name="XAUUSD.r", digits=2, contract_size=100.0, volume_min=0.01,
            volume_step=0.01, volume_max=50.0, trade_mode=4)]

    def test_missing_optionals_default(self):
        hello = p.parse_hello({"login": 1, "broker": "B", "hedging": False})
        assert hello.symbols == [] and hello.chunk == 1 and hello.chunks == 1
        assert hello.ea_version == "" and hello.leverage == 0 and hello.server == ""
        assert hello.hedging is False

    @pytest.mark.parametrize("body", [
        {"broker": "B", "hedging": True},                                        # no login
        {"login": "abc", "broker": "B", "hedging": True},                        # login not an int
        {"login": 1, "broker": "B", "hedging": True, "symbols": [{"n": "X"}]},   # symbol without digits
        {"login": 1, "broker": "B", "hedging": True, "symbols": "XAUUSD"},       # not a list
        [],
    ])
    def test_shape_errors_raise_protocol_error(self, body):
        with pytest.raises(p.ProtocolError):
            p.parse_hello(body)


class TestParseSync:
    def test_spec_example_converts_lots_to_centilots(self):
        report = p.parse_sync(SYNC)
        assert (report.seq, report.ts_ms, report.balance, report.equity) == (
            1042, 1757203200123, 9784.04, 9790.10)
        assert (report.margin, report.margin_free) == (120.5, 9669.6)
        assert report.positions[0] == p.ReportPosition(
            ticket=669607966, symbol="XAUUSD.r", side="BUY", volume=1, open_price=4468.49,
            stop_loss=4460.0, take_profit=4480.0, current_price=4470.1, pnl=1.61, swap=0.0,
            comment="copy:m669607900", magic=20260907, opened_at_ms=1757203100000)
        assert report.orders[0] == p.ReportOrder(
            ticket=5551, symbol="XAUUSD.r", order_type="BUY_LIMIT", volume=1, price=4450.0,
            stop_loss=None, take_profit=None, comment="", magic=20260907)
        assert report.deals[0] == p.ReportDeal(
            ticket=700001, position=669607966, order=700000, symbol="XAUUSD.r",
            deal_type="BUY", entry="IN", volume=1, price=4468.49, profit=0.0, swap=0.0,
            commission=-0.03, time_ms=1757203100456, comment="copy:m669607900",
            magic=20260907)
        assert report.acks[0] == p.Ack(
            command_id=88, ok=True, retcode=10009, message="done", position=669607966,
            deal=700001, order=700000, price=4468.49, volume=1)

    def test_missing_optional_fields_become_none_or_zero(self):
        report = p.parse_sync({
            "seq": 1, "ts": 2, "balance": 1.0, "equity": 1.0,
            "positions": [{"t": 1, "s": "EURUSD", "side": "SELL", "lots": 0.5, "open": 1.1}],
            "deals": [{"t": 9, "time": 5}],
            "acks": [{"id": 3, "ok": False}],
        })
        pos = report.positions[0]
        assert (pos.stop_loss, pos.take_profit, pos.current_price, pos.pnl, pos.comment,
                pos.magic, pos.opened_at_ms) == (None, None, None, 0.0, "", 0, 0)
        assert pos.volume == 50 and pos.side == "SELL"
        assert report.orders == [] and report.margin == 0.0 and report.margin_free == 0.0
        deal = report.deals[0]
        assert (deal.position, deal.order, deal.symbol, deal.deal_type, deal.entry,
                deal.volume, deal.price, deal.commission) == (0, 0, "", "OTHER", "", 0, 0.0, 0.0)
        ack = report.acks[0]
        assert (ack.retcode, ack.message, ack.position, ack.deal, ack.order, ack.price,
                ack.volume) == (0, "", None, None, None, None, None)

    def test_a_zero_price_means_none(self):
        report = p.parse_sync({
            "seq": 1, "ts": 2, "balance": 1.0, "equity": 1.0,
            "positions": [{"t": 1, "s": "EURUSD", "side": "BUY", "lots": 0.01, "open": 1.1,
                           "sl": 0, "tp": 0.0, "price": 0}],
        })
        pos = report.positions[0]
        assert (pos.stop_loss, pos.take_profit, pos.current_price) == (None, None, None)

    @pytest.mark.parametrize("body", [
        {"ts": 2, "balance": 1.0, "equity": 1.0},                                    # no seq
        {"seq": 1, "ts": 2, "balance": 1.0, "equity": 1.0,
         "positions": [{"t": 1, "s": "X", "side": "LONG", "lots": 1, "open": 1}]},   # bad side
        {"seq": 1, "ts": 2, "balance": 1.0, "equity": 1.0, "acks": [{"ok": True}]},  # ack without id
        {"seq": 1, "ts": 2, "balance": 1.0, "equity": 1.0,
         "deals": [{"t": 1, "time": 1, "entry": "SIDEWAYS"}]},                        # bad entry
        "not an object",
    ])
    def test_shape_errors_raise_protocol_error(self, body):
        with pytest.raises(p.ProtocolError):
            p.parse_sync(body)


OPEN = p.Command(88, "open", {"symbol": "XAUUSD.r", "side": "BUY", "lots": 0.01,
                              "sl": 4460.0, "tp": 4480.0, "comment": "copy:m669607900"},
                 "cm669607900.1000000000001")
CLOSE = p.Command(89, "close", {"position": 669607966, "lots": 0.01})
AMEND = p.Command(90, "amend", {"position": 669607966, "sl": 4462.0, "tp": 4482.0})
PLACE = p.Command(91, "place_pending", {"symbol": "XAUUSD.r", "type": "BUY_LIMIT",
                                        "lots": 0.01, "price": 4450.0, "sl": 0, "tp": 0,
                                        "expiry_ms": 0, "comment": "copy:o5551"},
                  "co5551.1000000000001")
AMEND_PENDING = p.Command(92, "amend_pending", {"order": 5551, "lots": 0.01,
                                                "price": 4451.0, "sl": 0, "tp": 0})
CANCEL = p.Command(93, "cancel_pending", {"order": 5551})
ALL = [OPEN, CLOSE, AMEND, PLACE, AMEND_PENDING, CANCEL]


class TestCommandLine:
    def test_every_kind_in_the_contract_field_order(self):
        assert p.command_line(OPEN) == (
            "CMD\t88\topen\tXAUUSD.r\tBUY\t0.01\t4460.0\t4480.0\tcopy:m669607900"
            "\tcm669607900.1000000000001")
        assert p.command_line(CLOSE) == "CMD\t89\tclose\t669607966\t0.01"
        assert p.command_line(AMEND) == "CMD\t90\tamend\t669607966\t4462.0\t4482.0"
        assert p.command_line(PLACE) == (
            "CMD\t91\tplace_pending\tXAUUSD.r\tBUY_LIMIT\t0.01\t4450.0\t0\t0\t0\tcopy:o5551"
            "\tco5551.1000000000001")
        assert p.command_line(AMEND_PENDING) == "CMD\t92\tamend_pending\t5551\t0.01\t4451.0\t0\t0"
        assert p.command_line(CANCEL) == "CMD\t93\tcancel_pending\t5551"

    def test_absent_protection_encodes_as_zero_and_a_full_close_as_zero_lots(self):
        line = p.command_line(p.Command(
            1, "open", {"symbol": "EURUSD", "side": "SELL", "lots": 1.0, "comment": ""}, None))
        assert line == "CMD\t1\topen\tEURUSD\tSELL\t1.0\t0\t0\t\t"
        assert p.command_line(p.Command(2, "close", {"position": 5})) == "CMD\t2\tclose\t5\t0"

    def test_a_tab_or_newline_in_a_field_is_refused(self):
        with pytest.raises(p.ProtocolError):
            p.command_line(p.Command(
                1, "open", {"symbol": "EUR\tUSD", "side": "BUY", "lots": 1.0, "comment": ""}))
        with pytest.raises(p.ProtocolError):
            p.command_line(p.Command(
                1, "open", {"symbol": "EURUSD", "side": "BUY", "lots": 1.0,
                            "comment": "line\nbreak"}))

    def test_unknown_kind_is_refused(self):
        with pytest.raises(p.ProtocolError):
            p.command_line(p.Command(1, "teleport", {}))


class TestEncodeResponse:
    def test_ok_status_line_then_one_line_per_command(self):
        text = p.encode_response("OK", 1757203200400, 250, [OPEN, CLOSE])
        lines = text.split("\n")
        assert lines[0] == "OK\t1757203200400\t250"
        assert lines[1] == p.command_line(OPEN) and lines[2] == p.command_line(CLOSE)
        assert text.endswith("\n") and lines[-1] == ""

    def test_hello_adds_the_watermark_as_a_fourth_field(self):
        assert p.encode_response("OK", 5, 250, [], last_deal_ticket=700001) == "OK\t5\t250\t700001\n"

    def test_retry_and_stop(self):
        assert p.encode_response("RETRY", 5, 2000, []) == "RETRY\t5\t2000\n"
        assert p.encode_response("STOP", 5, 0, [], reason="netting account not supported") == (
            "STOP\t5\tnetting account not supported\n")

    def test_unknown_status_is_refused(self):
        with pytest.raises(p.ProtocolError):
            p.encode_response("MAYBE", 5, 250, [])


class TestParseResponse:
    @pytest.mark.parametrize("cmd", ALL, ids=[c.kind for c in ALL])
    def test_round_trip(self, cmd):
        status, commands = p.parse_response(p.encode_response("OK", 1, 250, [cmd]))
        assert status == ["OK", "1", "250"]
        assert commands == [cmd]

    def test_status_only(self):
        assert p.parse_response("STOP\t1\tkey revoked\n") == (["STOP", "1", "key revoked"], [])

    def test_garbage_is_a_protocol_error(self):
        with pytest.raises(p.ProtocolError):
            p.parse_response("")
        with pytest.raises(p.ProtocolError):
            p.parse_response("OK\t1\t250\nCMD\t1\topen\tonly-two-fields\n")
        with pytest.raises(p.ProtocolError):
            p.parse_response("OK\t1\t250\nNOT-A-COMMAND\n")
