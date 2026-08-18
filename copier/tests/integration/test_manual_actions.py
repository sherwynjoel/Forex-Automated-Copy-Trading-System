"""End-to-end tests for operator-initiated trade actions: the kill switch
(close_all) and manual order placement / position close / order cancel.

Same harness as test_copier_e2e: the REAL CopierApp (build_app) against a
scripted FakeCTraderServer over TLS, outcomes asserted via the recorded wire
traffic (server.requests) and real DB state -- never internal mocks.
"""

import pytest
import pytest_twisted
from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOACancelOrderReq,
    ProtoOAClosePositionReq,
    ProtoOANewOrderReq,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAOrderType,
    ProtoOATradeSide,
)

from integration.test_copier_e2e import (
    MASTER_ID, SLAVE1_ID, SLAVE2_ID, SYMBOL_ID, ONE_LOT,
    _setup, _teardown, _wait_until,
)


def _reqs_of(server, req_type, account_id=None):
    return [
        r for r in server.requests
        if isinstance(r, req_type)
        and (account_id is None or r.ctidTraderAccountId == account_id)
    ]


def _seed_position(server, account_id, position_id, volume,
                   trade_side=ProtoOATradeSide.BUY, label=""):
    server.open_positions.setdefault(account_id, []).append({
        "position_id": position_id, "symbol_id": SYMBOL_ID,
        "volume": volume, "trade_side": trade_side, "label": label,
    })
    server._position_volumes[position_id] = volume


def _seed_pending(server, account_id, order_id, volume=100_000):
    server.pending_orders.setdefault(account_id, []).append({
        "order_id": order_id, "symbol_id": SYMBOL_ID, "volume": volume,
        "trade_side": ProtoOATradeSide.BUY,
        "order_type": ProtoOAOrderType.LIMIT,
    })


# ---------- kill switch ----------

@pytest_twisted.inlineCallbacks
def test_close_all_one_account_closes_positions_and_cancels_orders(db):
    server, repo, app = _setup(db)
    _seed_position(server, SLAVE1_ID, 7001, 200_000)
    _seed_position(server, SLAVE1_ID, 7002, 300_000)
    _seed_pending(server, SLAVE1_ID, 9101)
    try:
        yield app.startup()

        result = yield app.close_all(SLAVE1_ID)

        assert result["paused"] is False
        summary = result["accounts"][0]
        assert summary["account_id"] == SLAVE1_ID
        assert summary["positions_closed"] == 2
        assert summary["orders_cancelled"] == 1

        yield _wait_until(
            lambda: len(_reqs_of(server, ProtoOAClosePositionReq, SLAVE1_ID)) == 2
            and len(_reqs_of(server, ProtoOACancelOrderReq, SLAVE1_ID)) == 1)

        closes = _reqs_of(server, ProtoOAClosePositionReq, SLAVE1_ID)
        assert {(c.positionId, c.volume) for c in closes} == {
            (7001, 200_000), (7002, 300_000)}
        cancels = _reqs_of(server, ProtoOACancelOrderReq, SLAVE1_ID)
        assert cancels[0].orderId == 9101

        # A single-account kill switch must NOT touch global copying.
        assert repo.get_settings().copying_enabled is True
    finally:
        _teardown(app, server)


@pytest_twisted.inlineCallbacks
def test_close_all_global_pauses_copying_and_flattens_every_account(db):
    server, repo, app = _setup(db)
    _seed_position(server, MASTER_ID, 7001, 100_000)
    _seed_position(server, SLAVE1_ID, 7002, 100_000)
    _seed_position(server, SLAVE2_ID, 7003, 50_000)
    try:
        yield app.startup()

        result = yield app.close_all(None)

        assert result["paused"] is True
        assert repo.get_settings().copying_enabled is False
        by_account = {s["account_id"]: s for s in result["accounts"]}
        assert by_account[MASTER_ID]["positions_closed"] == 1
        assert by_account[SLAVE1_ID]["positions_closed"] == 1
        assert by_account[SLAVE2_ID]["positions_closed"] == 1

        yield _wait_until(
            lambda: all(
                len(_reqs_of(server, ProtoOAClosePositionReq, acct)) == 1
                for acct in (MASTER_ID, SLAVE1_ID, SLAVE2_ID)))
    finally:
        _teardown(app, server)


@pytest_twisted.inlineCallbacks
def test_close_all_works_while_copying_disabled_and_dry_run(db):
    """The kill switch must flatten even when dispatch gates (pause/dry-run)
    are engaged -- it is an operator emergency action, not a copy."""
    server, repo, app = _setup(db)
    _seed_position(server, SLAVE1_ID, 7001, 200_000)
    try:
        yield app.startup()
        repo.set_setting("copying_enabled", False)
        repo.set_setting("dry_run", True)

        yield app.close_all(SLAVE1_ID)

        yield _wait_until(
            lambda: len(_reqs_of(server, ProtoOAClosePositionReq, SLAVE1_ID)) == 1)
    finally:
        _teardown(app, server)


# ---------- manual order placement ----------

@pytest_twisted.inlineCallbacks
def test_manual_market_order_reaches_wire_with_manual_label(db):
    server, repo, app = _setup(db)
    try:
        yield app.startup()

        result = yield app.place_order({
            "account_id": SLAVE2_ID, "symbol": "EURUSD", "side": "SELL",
            "order_type": "MARKET", "volume_lots": 0.5,
        })

        assert result["status"] == "submitted"
        assert result["account_id"] == SLAVE2_ID
        assert result["volume"] == 5_000_000

        yield _wait_until(
            lambda: len(_reqs_of(server, ProtoOANewOrderReq, SLAVE2_ID)) == 1)
        req = _reqs_of(server, ProtoOANewOrderReq, SLAVE2_ID)[0]
        assert req.symbolId == SYMBOL_ID
        assert req.orderType == ProtoOAOrderType.MARKET
        assert req.tradeSide == ProtoOATradeSide.SELL
        assert req.volume == 5_000_000
        assert req.label == "manual"
    finally:
        _teardown(app, server)


@pytest_twisted.inlineCallbacks
def test_manual_limit_order_carries_price_sl_tp(db):
    server, repo, app = _setup(db)
    try:
        yield app.startup()

        yield app.place_order({
            "account_id": SLAVE1_ID, "symbol": "EURUSD", "side": "BUY",
            "order_type": "LIMIT", "volume_lots": 1.0,
            "limit_price": 1.0950, "stop_loss": 1.0900, "take_profit": 1.1100,
        })

        yield _wait_until(
            lambda: len(_reqs_of(server, ProtoOANewOrderReq, SLAVE1_ID)) == 1)
        req = _reqs_of(server, ProtoOANewOrderReq, SLAVE1_ID)[0]
        assert req.orderType == ProtoOAOrderType.LIMIT
        assert req.volume == ONE_LOT
        assert req.limitPrice == 1.0950
        assert req.stopLoss == 1.0900
        assert req.takeProfit == 1.1100
    finally:
        _teardown(app, server)


@pytest_twisted.inlineCallbacks
def test_manual_master_order_fans_out_to_slaves(db):
    """A manual order on the MASTER fills and replicates through the normal
    copy pipeline: both slaves receive copy NewOrderReqs sized by their
    multipliers, exactly as if the master had traded in cTrader."""
    server, repo, app = _setup(db)
    try:
        yield app.startup()

        yield app.place_order({
            "account_id": MASTER_ID, "symbol": "EURUSD", "side": "BUY",
            "order_type": "MARKET", "volume_lots": 1.0,
        })

        def slaves_got_copies():
            s1 = _reqs_of(server, ProtoOANewOrderReq, SLAVE1_ID)
            s2 = _reqs_of(server, ProtoOANewOrderReq, SLAVE2_ID)
            return len(s1) == 1 and len(s2) == 1

        yield _wait_until(slaves_got_copies)

        s1_req = _reqs_of(server, ProtoOANewOrderReq, SLAVE1_ID)[0]
        s2_req = _reqs_of(server, ProtoOANewOrderReq, SLAVE2_ID)[0]
        assert s1_req.volume == ONE_LOT           # 1.0x multiplier
        assert s2_req.volume == ONE_LOT // 2      # 0.5x multiplier
        assert s1_req.clientOrderId.startswith("cm")
    finally:
        _teardown(app, server)


@pytest_twisted.inlineCallbacks
def test_place_order_validation_rejects_bad_input(db):
    server, repo, app = _setup(db)
    try:
        yield app.startup()

        with pytest.raises(ValueError):
            yield app.place_order({
                "account_id": SLAVE1_ID, "symbol": "XAUUSD",  # not in symbol cache
                "side": "BUY", "order_type": "MARKET", "volume_lots": 1.0,
            })

        with pytest.raises(ValueError):
            yield app.place_order({
                "account_id": SLAVE1_ID, "symbol": "EURUSD", "side": "BUY",
                "order_type": "MARKET", "volume_lots": 0.001,  # below min_volume
            })

        with pytest.raises(ValueError):
            yield app.place_order({
                "account_id": SLAVE1_ID, "symbol": "EURUSD", "side": "BUY",
                "order_type": "LIMIT", "volume_lots": 1.0,  # no limit_price
            })

        with pytest.raises(ValueError):
            yield app.place_order({
                "account_id": SLAVE1_ID, "symbol": "EURUSD", "side": "HOLD",
                "order_type": "MARKET", "volume_lots": 1.0,
            })

        assert _reqs_of(server, ProtoOANewOrderReq) == []
    finally:
        _teardown(app, server)


# ---------- manual close / cancel ----------

@pytest_twisted.inlineCallbacks
def test_manual_close_position_full_and_partial(db):
    server, repo, app = _setup(db)
    _seed_position(server, SLAVE1_ID, 7001, 300_000)
    try:
        yield app.startup()

        # Partial: 0.01 lots of a 0.03-lot position.
        yield app.close_position(SLAVE1_ID, 7001, volume_lots=0.01)
        yield _wait_until(
            lambda: len(_reqs_of(server, ProtoOAClosePositionReq, SLAVE1_ID)) == 1)
        partial = _reqs_of(server, ProtoOAClosePositionReq, SLAVE1_ID)[0]
        assert partial.positionId == 7001
        assert partial.volume == 100_000

        # Full: no volume given closes the whole remaining position --
        # 200_000 after the partial close above, per the broker's own
        # snapshot at request time.
        yield app.close_position(SLAVE1_ID, 7001)
        yield _wait_until(
            lambda: len(_reqs_of(server, ProtoOAClosePositionReq, SLAVE1_ID)) == 2)
        full = _reqs_of(server, ProtoOAClosePositionReq, SLAVE1_ID)[1]
        assert full.volume == 200_000

        with pytest.raises(ValueError):
            yield app.close_position(SLAVE1_ID, 424242)  # unknown position
    finally:
        _teardown(app, server)


@pytest_twisted.inlineCallbacks
def test_manual_cancel_order(db):
    server, repo, app = _setup(db)
    _seed_pending(server, SLAVE1_ID, 9101)
    try:
        yield app.startup()

        result = yield app.cancel_order(SLAVE1_ID, 9101)
        assert result["status"] == "submitted"

        yield _wait_until(
            lambda: len(_reqs_of(server, ProtoOACancelOrderReq, SLAVE1_ID)) == 1)
        assert _reqs_of(server, ProtoOACancelOrderReq, SLAVE1_ID)[0].orderId == 9101
    finally:
        _teardown(app, server)
