"""Symbol naming for the MT5 bridge.

The engine matches symbols across accounts by NAME (that is how a cTrader
master's "XAUUSD" finds each slave's "XAUUSD"). MT5 brokers decorate names
("XAUUSD.r", "GOLDm", "USTEC") and have no numeric symbol id, so this
module does two things: it guesses which broker name is which canonical
instrument, and it mints the stable integer id every symbol_id-keyed path
in the copier expects.
"""

import re
import zlib
from typing import Iterable

from copier.domain.models import SymbolInfo
from copier.mt5.protocol import CENTILOTS, HelloSymbol

# Normalised stem -> canonical name. Both sides of every synonym are keys
# so that a canonical name normalises to itself.
SYNONYMS: dict[str, str] = {
    "GOLD": "XAUUSD", "XAUUSD": "XAUUSD",
    "SILVER": "XAGUSD", "XAGUSD": "XAGUSD",
    "USTEC": "NAS100", "NAS100": "NAS100",
    "DE40": "GER40", "GER40": "GER40",
    "US30": "US30", "DJ30": "US30",
    "US500": "US500", "SPX500": "US500",
    "UK100": "UK100",
    "USOIL": "USOIL", "WTI": "USOIL", "OIL": "USOIL",
    "BRENT": "UKOIL", "UKOIL": "UKOIL",
    "BTCUSD": "BTCUSD", "BITCOIN": "BTCUSD",
    "ETHUSD": "ETHUSD",
}

# Broker decorations, checked (upper-cased) at the end of the raw name.
SUFFIXES = (".ECN", ".PRO", ".R", ".X", ".M", ".I", "_I")
# A trailing account-type letter ("EURUSDm", "GOLDc") is stripped only when
# what remains is a known instrument, so a real 7-letter name is never cut.
TRAILING_LETTERS = ("M", "C", "I")

_NOT_ALNUM = re.compile(r"[^A-Z0-9]")
_FX_PAIR = re.compile(r"[A-Z]{6}")


def _is_known(stem: str) -> bool:
    return stem in SYNONYMS or _FX_PAIR.fullmatch(stem) is not None


def normalise_symbol(name: str) -> str:
    """"XAUUSD.r" -> "XAUUSD", "GOLDm" -> "XAUUSD", "eurusd_i" -> "EURUSD"."""
    upper = name.strip().upper()
    for suffix in SUFFIXES:
        if upper.endswith(suffix) and len(upper) > len(suffix):
            upper = upper[: -len(suffix)]
            break
    stem = _NOT_ALNUM.sub("", upper)
    if stem in SYNONYMS:
        return SYNONYMS[stem]
    if len(stem) > 1 and stem[-1] in TRAILING_LETTERS and _is_known(stem[:-1]):
        return SYNONYMS.get(stem[:-1], stem[:-1])
    return stem


def symbol_id_for(name: str, taken: set[int]) -> int:
    """crc32 of the broker name, masked positive; +1 probing within one
    account's list keeps ids unique there."""
    symbol_id = zlib.crc32(name.encode("utf-8")) & 0x7FFFFFFF
    while symbol_id in taken:
        symbol_id += 1
    return symbol_id


def symbol_infos_from_hello(symbols: list[HelloSymbol]) -> list[SymbolInfo]:
    """The hello's symbol list as the engine's SymbolInfo rows: lot_size is
    always CENTILOTS, volumes are centilots, ids are crc32 with probing."""
    out: list[SymbolInfo] = []
    taken: set[int] = set()
    for s in symbols:
        symbol_id = symbol_id_for(s.name, taken)
        taken.add(symbol_id)
        out.append(SymbolInfo(
            symbol_id=symbol_id, name=s.name, digits=s.digits, lot_size=CENTILOTS,
            min_volume=round(s.volume_min * CENTILOTS),
            step_volume=max(1, round(s.volume_step * CENTILOTS)),
        ))
    return out


def auto_match(canonical_names: Iterable[str], broker_names: Iterable[str]) -> dict[str, str]:
    """canonical -> broker_name. Exact name first, then the same normalised
    stem (which already folds synonyms). Unmatched canonicals are omitted;
    a manual alias set by the operator always wins over this guess (see
    Repo.save_symbol_aliases)."""
    brokers = list(dict.fromkeys(broker_names))
    by_exact = {b: b for b in brokers}
    by_stem: dict[str, str] = {}
    for b in brokers:
        by_stem.setdefault(normalise_symbol(b), b)
    out: dict[str, str] = {}
    for canonical in canonical_names:
        if canonical in by_exact:
            out[canonical] = canonical
            continue
        stem = normalise_symbol(canonical)
        if stem in by_stem:
            out[canonical] = by_stem[stem]
    return out
