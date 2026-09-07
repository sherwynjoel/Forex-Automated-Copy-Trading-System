"""Symbol naming for the MT5 bridge (copier/src/copier/mt5/symbols.py):
broker names -> canonical names, the crc32 ids the rest of the copier keys
on, and the auto-matcher the hello runs."""

import zlib

import pytest

from copier.domain.models import SymbolInfo
from copier.mt5.protocol import HelloSymbol
from copier.mt5.symbols import (
    SYNONYMS, auto_match, normalise_symbol, symbol_id_for, symbol_infos_from_hello)


@pytest.mark.parametrize("name, canonical", [
    ("XAUUSD.r", "XAUUSD"), ("GOLDm", "XAUUSD"), ("GOLD", "XAUUSD"), ("gold.pro", "XAUUSD"),
    ("XAUUSDM", "XAUUSD"),
    ("SILVER", "XAGUSD"), ("SILVERc", "XAGUSD"),
    ("USTEC", "NAS100"), ("NAS100.i", "NAS100"),
    ("DE40", "GER40"), ("GER40.ecn", "GER40"),
    ("US30", "US30"), ("DJ30", "US30"), ("US30.m", "US30"),
    ("US500", "US500"), ("SPX500", "US500"),
    ("UK100", "UK100"),
    ("USOIL", "USOIL"), ("WTI", "USOIL"), ("OIL", "USOIL"),
    ("BRENT", "UKOIL"), ("UKOIL", "UKOIL"),
    ("BTCUSD", "BTCUSD"), ("BITCOIN", "BTCUSD"),
    ("ETHUSD.x", "ETHUSD"),
    ("EURUSD", "EURUSD"), ("EURUSD.r", "EURUSD"), ("EURUSDm", "EURUSD"), ("eurusd_i", "EURUSD"),
    ("GBPJPY.i", "GBPJPY"), ("AUDCADc", "AUDCAD"),
    ("USTEC.cash", "USTECCASH"),      # no rule fits: the operator maps it by hand
])
def test_normalise_symbol(name, canonical):
    assert normalise_symbol(name) == canonical


def test_the_synonym_table_from_the_spec():
    expected = [
        ("GOLD", "XAUUSD"), ("XAUUSD", "XAUUSD"), ("SILVER", "XAGUSD"), ("XAGUSD", "XAGUSD"),
        ("USTEC", "NAS100"), ("NAS100", "NAS100"), ("DE40", "GER40"), ("GER40", "GER40"),
        ("US30", "US30"), ("DJ30", "US30"), ("US500", "US500"), ("SPX500", "US500"),
        ("UK100", "UK100"), ("USOIL", "USOIL"), ("WTI", "USOIL"), ("OIL", "USOIL"),
        ("BRENT", "UKOIL"), ("UKOIL", "UKOIL"), ("BTCUSD", "BTCUSD"), ("BITCOIN", "BTCUSD"),
        ("ETHUSD", "ETHUSD"),
    ]
    for stem, canonical in expected:
        assert SYNONYMS[stem] == canonical


class TestSymbolIds:
    def test_crc32_masked_to_31_bits(self):
        assert symbol_id_for("XAUUSD.r", set()) == zlib.crc32(b"XAUUSD.r") & 0x7FFFFFFF

    def test_a_collision_probes_upwards(self):
        base = symbol_id_for("EURUSD", set())
        assert symbol_id_for("EURUSD", {base}) == base + 1
        assert symbol_id_for("EURUSD", {base, base + 1}) == base + 2

    def test_hello_symbols_become_centilot_symbol_infos(self):
        infos = symbol_infos_from_hello([
            HelloSymbol("XAUUSD.r", 2, 100.0, 0.01, 0.01, 50.0, 4),
            HelloSymbol("EURUSD.r", 5, 100000.0, 0.01, 0.01, 100.0, 4),
            HelloSymbol("BTCUSD", 2, 1.0, 0.1, 0.1, 10.0, 4),
        ])
        assert infos[0] == SymbolInfo(
            symbol_id=zlib.crc32(b"XAUUSD.r") & 0x7FFFFFFF, name="XAUUSD.r", digits=2,
            lot_size=100, min_volume=1, step_volume=1)
        assert (infos[2].min_volume, infos[2].step_volume) == (10, 10)
        assert len({i.symbol_id for i in infos}) == 3

    def test_a_step_below_one_centilot_is_clamped_to_one(self):
        (info,) = symbol_infos_from_hello([HelloSymbol("TINY", 2, 1.0, 0.001, 0.001, 1.0, 4)])
        assert info.step_volume == 1 and info.min_volume == 0


class TestAutoMatch:
    def test_exact_then_normalised_then_synonym(self):
        matched = auto_match(
            ["EURUSD", "XAUUSD", "GBPJPY", "NAS100", "US30"],
            ["EURUSD.r", "GOLD.r", "XAUUSD", "USTEC", "DJ30.pro"],
        )
        assert matched == {"EURUSD": "EURUSD.r", "XAUUSD": "XAUUSD",
                           "NAS100": "USTEC", "US30": "DJ30.pro"}
        assert "GBPJPY" not in matched

    def test_the_first_broker_symbol_wins_a_normalised_tie(self):
        assert auto_match(["XAUUSD"], ["GOLD.r", "GOLDm"]) == {"XAUUSD": "GOLD.r"}

    def test_a_canonical_name_that_is_itself_a_synonym_matches(self):
        # A cTrader master that names gold "GOLD" copies to a broker naming it "XAUUSD.r".
        assert auto_match(["GOLD"], ["XAUUSD.r"]) == {"GOLD": "XAUUSD.r"}

    def test_empty_inputs(self):
        assert auto_match([], ["EURUSD"]) == {}
        assert auto_match(["EURUSD"], []) == {}
