import pytest_twisted
from copier.ctrader.symbols import fetch_symbol_map, by_id


@pytest_twisted.inlineCallbacks
def test_fetch_symbol_map_builds_name_keyed_infos(server_and_client):
    server, client = server_and_client          # authorized account 1001
    symbol_map = yield fetch_symbol_map(client, 1001)
    info = symbol_map["EURUSD"]
    assert (info.symbol_id, info.lot_size, info.step_volume, info.min_volume, info.digits) == \
           (1, 10_000_000, 100_000, 100_000, 5)
    assert by_id(symbol_map)[1] is info
