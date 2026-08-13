import pytest_twisted

from copier.ctrader.symbols import fetch_symbol_map, by_id

ACCOUNT_ID = 1001


@pytest_twisted.inlineCallbacks
def test_fetch_symbol_map_builds_name_keyed_infos(server_and_client):
    """Fetch symbol map through real SDK client against FakeCTraderServer.

    Verifies:
    - Two-stage fetch: ProtoOASymbolsListReq → ProtoOASymbolByIdReq
    - Results keyed by symbol name (EURUSD → SymbolInfo)
    - Symbol details correct: symbol_id, lot_size, step_volume, min_volume, digits
    - by_id() index works
    """
    server, client = server_and_client
    # Wait for client to be ready (app auth done)
    yield client.ready
    # Authorize the account
    yield client.authorize_account(ACCOUNT_ID, "tok-1001")

    # Fetch symbol map
    symbol_map = yield fetch_symbol_map(client, ACCOUNT_ID)

    # Verify the map contains EURUSD
    info = symbol_map["EURUSD"]
    assert (info.symbol_id, info.lot_size, info.step_volume, info.min_volume, info.digits) == \
           (1, 10_000_000, 100_000, 100_000, 5)

    # Verify by_id() index works and returns same object instance
    assert by_id(symbol_map)[1] is info
