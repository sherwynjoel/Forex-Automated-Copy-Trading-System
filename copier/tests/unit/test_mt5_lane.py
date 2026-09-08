"""MT5Lane (copier/src/copier/mt5/lane.py): operator actions become outbox
commands; the read models come from Postgres and the registry."""

import psycopg
import pytest
from twisted.internet.task import Clock

import copier.main as main_module
from copier.db.repo import Repo
from copier.mt5.lane import MT5Lane
from copier.mt5.outbox import MT5Outbox
from copier.mt5.protocol import HelloReport, HelloSymbol
from copier.mt5.registry import MT5Registry
from copier.testing.mt5_fixtures import order, position, report

MAP_DEAL_KEYS = {"deal_id", "order_id", "position_id", "symbol_id", "symbol", "side", "volume",
                 "filled_volume", "volume_lots", "execution_price", "status", "commission",
                 "create_timestamp", "execution_timestamp", "close"}
CLOSE_KEYS = {"entry_price", "gross_profit", "swap", "commission", "balance", "closed_volume",
              "closed_volume_lots"}


class _StubApp:
    """Just the attributes MT5Lane reads off CopierApp."""

    def __init__(self, repo, clock):
        self.repo = repo
        self.mt5_registry = MT5Registry(repo, clock=clock)
        self.mt5_outbox = MT5Outbox(repo, clock=clock)
        self.clock = clock

    def _org_for_account(self, account_id):
        return self.repo.org_for_account(account_id)


@pytest.fixture
def world(db, seed_mt5_account):
    with psycopg.connect(db, autocommit=True) as conn:
        (org_id,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('MT5 Org') RETURNING id").fetchone()
    mt5_id = seed_mt5_account(org_id)
    repo = Repo(db)
    clock = Clock()
    app = _StubApp(repo, clock)
    app.mt5_registry.update_from_hello(mt5_id, org_id, HelloReport(
        "1.0.0", 4400, 12345678, "XYZ Ltd", "XYZ-Live3", "USD", True, "demo", 500,
        [HelloSymbol("EURUSD.r", 5, 100000.0, 0.01, 0.01, 100.0, 4),
         HelloSymbol("XAUUSD.r", 2, 100.0, 0.01, 0.01, 50.0, 4)], 1, 1), now=0.0)
    repo.upsert_mt5_link_hello(
        mt5_id, login=12345678, broker="XYZ Ltd", server="XYZ-Live3", currency="USD",
        hedging=True, trade_mode="demo", leverage=500, ea_version="1.0.0", ea_build=4400)
    app.mt5_registry.update_from_sync(mt5_id, org_id, report(
        positions=[position(7001, volume=100, sl=1.09),
                   position(7002, symbol="XAUUSD.r", side="SELL", volume=5, open_price=2400.0)],
        orders=[order(5551)], balance=10000.0, equity=10003.0), now=0.0)
    return repo, org_id, mt5_id, app, MT5Lane(app)


def _commands(repo, mt5_id):
    return repo.mt5_commands_open(mt5_id)


def _events(repo, action):
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            return cur.execute(
                "SELECT severity, payload, actor_email, account_id FROM events"
                " WHERE payload->>'action' = %s ORDER BY id", (action,)).fetchall()


class TestOperatorActions:
    def test_market_order_queues_an_open_with_rounded_protection(self, world):
        repo, org_id, mt5_id, app, lane = world
        result = lane.place_order(mt5_id, org_id, "XAUUSD.r", "BUY", "MARKET", 0.5, None, None,
                                  2390.123, 2420.456, "ada@example.com")
        (row,) = _commands(repo, mt5_id)
        assert row["kind"] == "open" and row["client_order_id"] is None
        assert row["payload"] == {"symbol": "XAUUSD.r", "side": "BUY", "lots": 0.5,
                                  "sl": 2390.12, "tp": 2420.46, "comment": "manual"}
        assert result == {"status": "submitted", "account_id": mt5_id, "symbol": "XAUUSD.r",
                          "side": "BUY", "order_type": "MARKET", "volume": 50,
                          "volume_lots": "0.50", "command_id": row["id"]}
        (event,) = _events(repo, "manual_order")
        assert event["actor_email"] == "ada@example.com"
        assert event["payload"]["protection"] == {"stop_loss": 2390.12, "take_profit": 2420.46}

    def test_pending_order_queues_a_place_pending(self, world):
        repo, org_id, mt5_id, app, lane = world
        lane.place_order(mt5_id, org_id, "EURUSD.r", "SELL", "STOP", 1.0, None, 1.08123456,
                         None, None, None)
        (row,) = _commands(repo, mt5_id)
        assert row["kind"] == "place_pending"
        assert row["payload"] == {"symbol": "EURUSD.r", "type": "SELL_STOP", "lots": 1.0,
                                  "price": 1.08123, "sl": 0.0, "tp": 0.0, "expiry_ms": 0,
                                  "comment": "manual"}

    def test_volume_is_stepped_and_checked_against_the_minimum(self, world):
        repo, org_id, mt5_id, app, lane = world
        with pytest.raises(ValueError, match="below the minimum"):
            lane.place_order(mt5_id, org_id, "EURUSD.r", "BUY", "MARKET", 0.004, None, None,
                             None, None, None)
        with pytest.raises(ValueError, match="unknown symbol"):
            lane.place_order(mt5_id, org_id, "GBPJPY.r", "BUY", "MARKET", 1.0, None, None,
                             None, None, None)
        assert _commands(repo, mt5_id) == []

    def test_close_full_partial_and_clamped(self, world):
        repo, org_id, mt5_id, app, lane = world
        full = lane.close_position(mt5_id, 7001, None, None)
        partial = lane.close_position(mt5_id, 7001, 0.3, "ada@example.com")
        clamped = lane.close_position(mt5_id, 7001, 5.0, None)
        assert (full["volume"], partial["volume"], clamped["volume"]) == (100, 30, 100)
        assert full["status"] == "submitted" and full["position_id"] == 7001
        rows = _commands(repo, mt5_id)
        assert [r["payload"] for r in rows] == [
            {"position": 7001, "lots": 0.0}, {"position": 7001, "lots": 0.3},
            {"position": 7001, "lots": 0.0}]
        assert full["command_id"] == rows[0]["id"]
        with pytest.raises(ValueError, match="not found"):
            lane.close_position(mt5_id, 424242, None, None)
        assert len(_events(repo, "manual_close")) == 3

    def test_amend_rounds_to_the_symbols_digits(self, world):
        repo, org_id, mt5_id, app, lane = world
        result = lane.amend_position_sltp(mt5_id, 7002, "2390.123", None, None)
        (row,) = _commands(repo, mt5_id)
        assert row["kind"] == "amend" and row["payload"] == {"position": 7002, "sl": 2390.12,
                                                             "tp": 0.0}
        assert result["stop_loss"] == 2390.12 and result["take_profit"] is None
        with pytest.raises(ValueError):
            lane.amend_position_sltp(mt5_id, 7002, "-1", None, None)

    def test_cancel(self, world):
        repo, org_id, mt5_id, app, lane = world
        assert lane.cancel_order(mt5_id, 5551, None)["order_id"] == 5551
        (row,) = _commands(repo, mt5_id)
        assert row["kind"] == "cancel_pending" and row["payload"] == {"order": 5551}
        assert _events(repo, "manual_cancel")


class TestFlatten:
    def test_queues_closes_and_cancels_then_verifies_from_the_reports(self, world, monkeypatch):
        repo, org_id, mt5_id, app, lane = world
        monkeypatch.setattr(main_module, "FLATTEN_SETTLE_S", 0.5)
        results = []
        lane.flatten(mt5_id).addCallback(results.append)
        rows = _commands(repo, mt5_id)
        assert [(r["kind"], r["payload"]) for r in rows] == [
            ("close", {"position": 7001, "lots": 0.0}), ("close", {"position": 7002, "lots": 0.0}),
            ("cancel_pending", {"order": 5551})]
        assert results == []                                           # waiting for the terminal
        app.mt5_registry.update_from_sync(mt5_id, org_id, report(seq=2), now=0.4)  # it reports flat
        app.clock.advance(0.5)
        (summary,) = results
        assert summary == {"account_id": mt5_id, "positions_closed": 2, "orders_cancelled": 1,
                           "positions_remaining": [], "orders_remaining": [], "rounds": 1,
                           "error": None}
        (event,) = _events(repo, "kill_switch_flatten")
        assert event["severity"] == "warning" and event["payload"]["positions_closed"] == 2

    def test_a_later_round_never_requeues_a_close_still_in_flight(self, world, monkeypatch):
        repo, org_id, mt5_id, app, lane = world
        monkeypatch.setattr(main_module, "FLATTEN_SETTLE_S", 0.5)
        results = []
        lane.flatten(mt5_id).addCallback(results.append)
        app.clock.advance(0.5)                       # round 1 settled: the terminal said nothing
        assert len(_commands(repo, mt5_id)) == 3     # nothing re-queued
        app.mt5_registry.update_from_sync(mt5_id, org_id, report(
            positions=[position(7002, symbol="XAUUSD.r", side="SELL", volume=5)], seq=2), now=1.0)
        app.clock.advance(1.0)                       # round 2 settled
        app.clock.advance(2.0)                       # round 3 settled
        (summary,) = results
        assert (summary["positions_closed"], summary["orders_cancelled"],
                summary["positions_remaining"], summary["rounds"]) == (1, 1, [7002], 3)
        assert summary["error"] == "1 position(s) and 0 order(s) still open after 3 attempt(s)"
        assert _events(repo, "kill_switch_flatten")[0]["severity"] == "error"

    def test_a_terminal_that_never_reported_is_an_error_not_a_verified_flat(self, world, seed_mt5_account):
        repo, org_id, mt5_id, app, lane = world
        silent = seed_mt5_account(org_id)
        failures = []
        lane.flatten(silent).addErrback(lambda f: failures.append(str(f.value)))
        assert failures and "has not reported" in failures[0]


class TestQueries:
    def _seed_deals(self, repo, org_id, mt5_id):
        repo.upsert_mt5_deals(mt5_id, org_id, [
            {"deal_id": 1, "order_id": 10, "position_id": 7001, "symbol_id": 77,
             "symbol": "EURUSD.r", "side": "BUY", "volume": 100, "filled_volume": 100,
             "execution_price": 1.1, "status": "FILLED", "commission": -0.03,
             "create_timestamp": 1000, "execution_timestamp": 1000, "close": None,
             "balance_after": 9994.97},
            {"deal_id": 2, "order_id": 11, "position_id": 7001, "symbol_id": 77,
             "symbol": "EURUSD.r", "side": "SELL", "volume": 100, "filled_volume": 100,
             "execution_price": 1.105, "status": "FILLED", "commission": -0.03,
             "create_timestamp": 2000, "execution_timestamp": 2000,
             "close": {"entry_price": 1.1, "gross_profit": 5.0, "swap": 0.0,
                       "commission": -0.03, "balance": 10000.0, "closed_volume": 100}},
            {"deal_id": 3, "order_id": 0, "position_id": 0, "symbol_id": None, "symbol": None,
             "side": "BALANCE", "volume": 0, "filled_volume": 0, "execution_price": None,
             "status": "FILLED", "commission": None, "create_timestamp": 3000,
             "execution_timestamp": 3000, "close": None, "gross_profit": 500.0,
             "balance_after": 10500.0},
        ])

    def test_deal_history_has_map_deals_shape_plus_the_estimate_flag(self, world):
        repo, org_id, mt5_id, app, lane = world
        self._seed_deals(repo, org_id, mt5_id)
        history = lane.deal_history(mt5_id, 0, 2500)
        assert history["has_more"] is False
        assert [d["deal_id"] for d in history["deals"]] == [1, 2]      # balance ops are not deals
        opened, closed = history["deals"]
        assert set(opened) == MAP_DEAL_KEYS | {"balance_after_estimated"}
        assert opened["close"] is None and opened["volume_lots"] == "1.00"
        assert opened["balance_after_estimated"] is True
        assert set(closed["close"]) == CLOSE_KEYS
        assert closed["close"]["closed_volume_lots"] == "1.00"
        assert closed["close"]["balance"] == 10000.0

    def test_order_history_is_one_filled_order_per_trade_deal(self, world):
        repo, org_id, mt5_id, app, lane = world
        self._seed_deals(repo, org_id, mt5_id)
        orders = lane.order_history(mt5_id, 0, 5000)
        assert orders["has_more"] is False
        assert orders["orders"][0] == {
            "order_id": 10, "symbol_id": 77, "symbol": "EURUSD.r", "side": "BUY", "volume": 100,
            "volume_lots": "1.00", "order_type": "MARKET", "status": "FILLED",
            "limit_price": None, "stop_price": None, "execution_price": 1.1,
            "executed_volume": 100, "position_id": 7001, "label": "", "open_timestamp": 1000,
            "update_timestamp": 1000, "stop_loss": None, "take_profit": None}
        assert [o["order_id"] for o in orders["orders"]] == [10, 11]

    def test_order_history_carries_the_copy_label_the_fleet_view_groups_by(self, world):
        repo, org_id, mt5_id, app, lane = world
        repo.upsert_mt5_deals(mt5_id, org_id, [
            {"deal_id": 1, "order_id": 10, "position_id": 7001, "symbol_id": 77,
             "symbol": "EURUSD.r", "side": "BUY", "volume": 100, "filled_volume": 100,
             "execution_price": 1.1, "status": "FILLED", "commission": -0.03,
             "create_timestamp": 1000, "execution_timestamp": 1000, "close": None,
             "label": "copy:m670345546", "balance_after": 9994.97},
        ])
        (order,) = lane.order_history(mt5_id, 0, 5000)["orders"]
        assert order["label"] == "copy:m670345546"
        # deal_history stays exactly _map_deal-shaped -- a cTrader deal
        # never carries a label, only its order does.
        (deal,) = lane.deal_history(mt5_id, 0, 5000)["deals"]
        assert "label" not in deal

    def test_cash_flow(self, world):
        repo, org_id, mt5_id, app, lane = world
        self._seed_deals(repo, org_id, mt5_id)
        assert lane.cash_flow(mt5_id, 0, 5000) == {"entries": [
            {"id": 3, "type": "DEPOSIT", "amount": 500.0, "balance_after": 10500.0,
             "timestamp": 3000, "note": None}]}

    def test_position_deals(self, world):
        repo, org_id, mt5_id, app, lane = world
        self._seed_deals(repo, org_id, mt5_id)
        deals = lane.position_deals(mt5_id, 7001, 0, 5000)["deals"]
        assert [d["deal_id"] for d in deals] == [1, 2]
        assert lane.position_deals(mt5_id, 9999, 0, 5000) == {"deals": [], "has_more": False}

    def test_details(self, world):
        repo, org_id, mt5_id, app, lane = world
        details = lane.details(mt5_id)
        assert details["platform"] == "mt5" and details["account_id"] == mt5_id
        assert (details["trader_login"], details["balance"], details["deposit_currency"],
                details["leverage"], details["broker_name"], details["account_type"]) == (
            12345678, 10000.0, "USD", 500, "XYZ Ltd", "HEDGED")
        assert details["mt5"]["login"] == 12345678 and details["mt5"]["connected"] is True
        assert details["mt5"]["ea_version"] == "1.0.0"
        positions = {p["position_id"]: p for p in details["open_positions"]}
        assert positions[7001]["volume_lots"] == "1.00" and positions[7001]["stop_loss"] == 1.09
        assert positions[7001]["symbol"] == "EURUSD.r"
        assert positions[7002]["side"] == "SELL" and positions[7002]["volume_lots"] == "0.05"
        (pending,) = details["pending_orders"]
        assert (pending["order_id"], pending["order_type"], pending["side"],
                pending["limit_price"], pending["stop_price"]) == (5551, "LIMIT", "BUY", 1.09, None)
