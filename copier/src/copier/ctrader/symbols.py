from ctrader_open_api import Protobuf
from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASymbolByIdReq, ProtoOASymbolsListReq
from twisted.internet import defer

from copier.domain.models import SymbolInfo


class SymbolFetchRefused(Exception):
    """The broker answered a symbol request with an error instead of symbols."""


def _symbols_or_raise(response, account_id: int, what: str):
    """Return the message's symbol list, or explain why the broker refused.

    A broker that will not serve an account -- disabled, unauthorised, a
    revoked grant -- answers with an error message carrying no `symbol`
    field at all. Reading it raised a bare AttributeError, and that is
    what reached the operator's log: "'ProtoOAErrorRes' object has no
    attribute 'symbol'", which names the Python type and not one thing
    about the account or the reason. Symbols are not optional -- without
    them nothing can be sized or copied on this account -- so this refuses
    loudly rather than degrading, but it refuses in the broker's words.
    """
    if hasattr(response, "symbol"):
        return response.symbol
    code = getattr(response, "errorCode", None)
    detail = getattr(response, "description", None)
    raise SymbolFetchRefused(
        f"broker refused the {what} for account {account_id}: "
        f"{code or type(response).__name__}"
        + (f" ({detail})" if detail else "")
    )


@defer.inlineCallbacks
def fetch_symbol_map(client, account_id: int):
    req = ProtoOASymbolsListReq()
    req.ctidTraderAccountId = account_id
    light = Protobuf.extract((yield client.send(req)))
    names = {s.symbolId: s.symbolName
             for s in _symbols_or_raise(light, account_id, "symbol list")}
    detail_req = ProtoOASymbolByIdReq()
    detail_req.ctidTraderAccountId = account_id
    detail_req.symbolId.extend(names.keys())
    full = Protobuf.extract((yield client.send(detail_req)))
    _symbols_or_raise(full, account_id, "symbol details")
    result: dict[str, SymbolInfo] = {}
    for sym in full.symbol:
        name = names[sym.symbolId]
        result[name] = SymbolInfo(symbol_id=sym.symbolId, name=name, digits=sym.digits,
                                  lot_size=sym.lotSize, min_volume=sym.minVolume,
                                  step_volume=sym.stepVolume)
    return result


def by_id(symbol_map: dict[str, SymbolInfo]) -> dict[int, SymbolInfo]:
    return {info.symbol_id: info for info in symbol_map.values()}
