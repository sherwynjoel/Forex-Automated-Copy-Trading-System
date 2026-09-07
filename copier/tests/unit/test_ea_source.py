"""mt5/MirrorFleet.mq5 cannot be compiled here (there is no MetaEditor), so
this pins the parts of it the rest of the bridge relies on: every JSON key
protocol.parse_hello/parse_sync read, every enum spelling and command kind,
the contract's endpoints, header, inputs and constants, and the file's basic
well-formedness (ASCII, balanced brackets, no raw tabs). It is not a
compiler -- the owner's MetaEditor build is the second gate (plan 04,
Task 6) -- but it fails the moment either side drifts from the other."""

import re
from pathlib import Path

import pytest

from copier.mt5 import protocol as p

# tests/unit/test_ea_source.py -> unit -> tests -> copier -> the repo root
EA_PATH = Path(__file__).resolve().parents[3] / "mt5" / "MirrorFleet.mq5"

# The keys parse_hello reads (plan 01 Task 2), top level and per symbol.
HELLO_KEYS = ["v", "ea", "build", "login", "broker", "server", "currency", "hedging",
              "trade_mode", "leverage", "symbols", "chunk", "chunks"]
HELLO_SYMBOL_KEYS = ["n", "d", "cs", "vmin", "vstep", "vmax", "tm"]
# The keys parse_sync reads, top level and per position / order / deal / ack.
SYNC_KEYS = ["v", "seq", "ts", "balance", "equity", "margin", "margin_free",
             "positions", "orders", "deals", "acks"]
POSITION_KEYS = ["t", "s", "side", "lots", "open", "sl", "tp", "price", "pnl", "swap",
                 "comment", "magic", "time"]
ORDER_KEYS = ["t", "s", "type", "lots", "price", "sl", "tp", "comment", "magic"]
DEAL_KEYS = ["t", "pos", "order", "s", "type", "entry", "lots", "price", "profit", "swap",
             "commission", "time", "comment", "magic"]
ACK_KEYS = ["id", "ok", "retcode", "msg", "pos", "deal", "order", "price", "lots"]
ENUM_LITERALS = sorted(set(p.SIDES + p.PENDING_TYPES + p.DEAL_TYPES
                           + tuple(e for e in p.DEAL_ENTRIES if e)))


@pytest.fixture(scope="module")
def source() -> str:
    assert EA_PATH.is_file(), f"{EA_PATH} does not exist"
    return EA_PATH.read_text(encoding="utf-8")


def strip_strings_and_comments(src: str) -> str:
    """The code with every string/char literal and comment removed, so a
    bracket inside a literal or a comment does not count."""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        if two == "//":
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif two == "/*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def define(source: str, name: str) -> str:
    """The value of `#define NAME value` (the first token after the name)."""
    m = re.search(rf"^#define\s+{name}\s+(\S+)", source, re.M)
    assert m, f"#define {name} is missing"
    return m.group(1)


def input_default(source: str, name: str) -> str:
    """The default of `input <type> NAME = <default>;`."""
    m = re.search(rf"^input\s+\w+\s+{name}\s*=\s*([^;]+);", source, re.M)
    assert m, f"input {name} is missing"
    return m.group(1).strip()


class TestIdentity:
    def test_version_and_the_only_include(self, source):
        assert re.search(r'^#property\s+version\s+"1\.00"', source, re.M)
        assert define(source, "EA_VERSION") == '"1.0.0"'
        assert re.findall(r"^#include\s+<(.+)>", source, re.M) == ["Trade/Trade.mqh"]
        assert "#property strict" not in source          # an MQL4 directive

    def test_inputs_and_their_defaults(self, source):
        assert input_default(source, "InpKey") == '""'
        assert input_default(source, "InpServer") == '"https://mirrorfleet.com"'
        assert input_default(source, "InpPollMs") == str(p.DEFAULT_POLL_MS)
        assert input_default(source, "InpMagic") == "20260907"
        assert input_default(source, "InpDeviationPoints") == "50"

    def test_event_handlers(self, source):
        for handler in ("int OnInit()", "void OnDeinit(const int reason)", "void OnTimer()",
                        "void OnTradeTransaction("):
            assert handler in source, handler


class TestTransport:
    def test_endpoints_and_header(self, source):
        assert define(source, "HELLO_PATH") == '"/api/mt5/hello"'
        assert define(source, "SYNC_PATH") == '"/api/mt5/sync"'
        assert define(source, "KEY_HEADER") == '"X-MirrorFleet-Key"'
        assert define(source, "KEY_PREFIX") == f'"{p.MT5_KEY_PREFIX}"'
        assert "Content-Type: application/json" in source

    def test_constants_match_protocol(self, source):
        assert int(define(source, "PROTOCOL_VERSION")) == p.PROTOCOL_VERSION
        assert int(define(source, "RETRY_POLL_MS")) == p.RETRY_POLL_MS
        assert int(define(source, "MAX_DEALS_PER_SYNC")) == p.MAX_DEALS_PER_SYNC
        assert int(define(source, "SYMBOLS_PER_HELLO")) == 300
        assert int(define(source, "MIN_POLL_MS")) == 100
        assert int(define(source, "HTTP_TIMEOUT_MS")) == 3000
        assert int(define(source, "MAX_SYNC_BODY_BYTES")) < 262144    # the api's sync cap

    def test_the_ack_file(self, source):
        assert define(source, "ACK_DIR") == '"MirrorFleet"'
        assert define(source, "ACK_EXT") == '".acks"'
        assert int(define(source, "ACK_FILE_LIMIT")) == 500


class TestHello:
    @pytest.mark.parametrize("key", HELLO_KEYS + HELLO_SYMBOL_KEYS)
    def test_every_key_parse_hello_reads_is_written(self, source, key):
        assert f'"{key}"' in source

    def test_the_reply_is_json_with_the_watermark(self, source):
        assert '"last_deal_ticket"' in source
        assert '"reason"' in source                        # the api's 401/503 bodies


class TestSync:
    @pytest.mark.parametrize("key", SYNC_KEYS + POSITION_KEYS + ORDER_KEYS + DEAL_KEYS)
    def test_every_key_parse_sync_reads_is_written(self, source, key):
        assert f'"{key}"' in source

    @pytest.mark.parametrize("literal", ENUM_LITERALS)
    def test_every_enum_value_is_spelled_as_the_copier_expects(self, source, literal):
        assert f'"{literal}"' in source

    def test_the_status_lines(self, source):
        for literal in ('"OK"', '"RETRY"', '"STOP"'):
            assert literal in source, literal


class TestCommands:
    def test_the_command_line_prefix(self, source):
        assert '"CMD"' in source

    @pytest.mark.parametrize("kind", p.COMMAND_KINDS)
    def test_every_command_kind_is_handled(self, source, kind):
        assert f'"{kind}"' in source

    @pytest.mark.parametrize("key", ACK_KEYS)
    def test_every_ack_key_parse_sync_reads_is_written(self, source, key):
        assert f'"{key}"' in source

    def test_the_comment_is_read_at_the_index_command_line_writes_it(self, source):
        # DoOpen and DoPlacePending read the comment by field index (CMD, id
        # and kind come first). Derive each index from protocol.command_line
        # so a slip in the EA's transcription of the field order cannot pass.
        cases = [
            ("DoOpen", p.Command(1, "open", {"symbol": "X", "side": "BUY", "lots": 0.01,
                                              "comment": "CMT"}, "c1")),
            ("DoPlacePending", p.Command(2, "place_pending", {"symbol": "X", "type": "BUY_LIMIT",
                                                              "lots": 0.01, "price": 1.0,
                                                              "comment": "CMT"}, "c2")),
        ]
        for function, cmd in cases:
            index = p.command_line(cmd).split("\t").index("CMT")
            body = re.search(rf"^string {function}\(.*?^\}}", source, re.M | re.S)
            assert body, f"{function} is missing"
            assert f'comment = (ArraySize(f) > {index}) ? f[{index}] : "";' in body.group(0), function


class TestWellFormed:
    def test_ascii_only(self, source):
        bad = [(i + 1, line) for i, line in enumerate(source.splitlines()) if not line.isascii()]
        assert bad == []

    def test_no_raw_tab_characters(self, source):
        # A tab is the wire separator; in the source it would be invisible
        # in a diff and is only ever written as the escape '\t'.
        assert "\t" not in source

    def test_brackets_balance(self, source):
        code = strip_strings_and_comments(source)
        for open_, close in ("{}", "()", "[]"):
            assert code.count(open_) == code.count(close), f"{open_}{close} unbalanced"
