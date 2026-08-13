from ctrader_open_api import Protobuf
from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOASymbolByIdReq, ProtoOASymbolsListReq
from twisted.internet import defer

from copier.domain.models import SymbolInfo


@defer.inlineCallbacks
def fetch_symbol_map(client, account_id: int):
    req = ProtoOASymbolsListReq()
    req.ctidTraderAccountId = account_id
    light = Protobuf.extract((yield client.send(req)))
    names = {s.symbolId: s.symbolName for s in light.symbol}
    detail_req = ProtoOASymbolByIdReq()
    detail_req.ctidTraderAccountId = account_id
    detail_req.symbolId.extend(names.keys())
    full = Protobuf.extract((yield client.send(detail_req)))
    result: dict[str, SymbolInfo] = {}
    for sym in full.symbol:
        name = names[sym.symbolId]
        result[name] = SymbolInfo(symbol_id=sym.symbolId, name=name, digits=sym.digits,
                                  lot_size=sym.lotSize, min_volume=sym.minVolume,
                                  step_volume=sym.stepVolume)
    return result


def by_id(symbol_map: dict[str, SymbolInfo]) -> dict[int, SymbolInfo]:
    return {info.symbol_id: info for info in symbol_map.values()}
