"""Dispatcher x MT5 (engine/dispatch.py): intents whose slave is an MT5
terminal are queued on the outbox BEFORE build_request; cTrader intents are
untouched."""

from unittest.mock import Mock

import psycopg
import pytest
from twisted.internet import defer
from twisted.internet.task import Clock

from copier.db.repo import Repo
from copier.domain.models import (
    AmendPending, AmendPositionSLTP, CancelPending, ClosePosition, OpenMarket, PendingType,
    PlacePending, Side)
from copier.engine.dispatch import Dispatcher
from copier.mt5.outbox import MT5Outbox

ORG_ID = 1
CTRADER_SLAVE = 101
EURUSD_ID = 12345          # the bridge's id for the MT5 account's EURUSD.r


@pytest.fixture
def world(db, seed_mt5_account):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO orgs (id, name) VALUES (%s, 'Org A')", (ORG_ID,))
        conn.execute(
            "INSERT INTO ctid_connections (org_id, access_token_enc, refresh_token_enc,"
            " granted_at, expires_at) VALUES (%s, 'a', 'b', now(), now() + interval '1 hour')",
            (ORG_ID,))
        conn.execute(
            "INSERT INTO accounts (ctid_trader_account_id, org_id, ctid_connection_id,"
            " trader_login, is_live, role, enabled, multiplier)"
            " VALUES (%s, %s, 1, 10001, false, 'slave', true, 1.0)", (CTRADER_SLAVE, ORG_ID))
    mt5_id = seed_mt5_account(ORG_ID)
    repo = Repo(db)
    sent = []

    def mock_send(account_id, msg):
        sent.append((account_id, msg))
        return defer.succeed(None)

    bucket = Mock()
    bucket.acquire.return_value = defer.succeed(None)

    def mt5_targets(account_id):
        return {EURUSD_ID: "EURUSD.r"} if account_id == mt5_id else None

    dispatcher = Dispatcher(mock_send, repo, bucket, clock=Clock(),
                            mt5_targets=mt5_targets, mt5_outbox=MT5Outbox(repo, clock=Clock()))
    return repo, mt5_id, dispatcher, sent


def _open(account_id, symbol_id=EURUSD_ID, master_position_id=42):
    return OpenMarket(slave_account_id=account_id, master_position_id=master_position_id,
                      symbol_id=symbol_id, side=Side.BUY, volume=100, stop_loss=1.09,
                      take_profit=1.12, label=f"copy:m{master_position_id}",
                      symbol_name="EURUSD", entry_price=1.1)


def _events(repo, action):
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, account_id, payload FROM events"
            " WHERE payload->>'action' = %s ORDER BY id", (action,)).fetchall()


def test_an_open_for_an_mt5_slave_is_queued_not_sent(world):
    repo, mt5_id, dispatcher, sent = world
    dispatcher.dispatch([_open(mt5_id)], org_id=ORG_ID)
    assert sent == []
    (row,) = repo.mt5_commands_open(mt5_id)
    assert row["kind"] == "open" and row["status"] == "queued"
    assert row["payload"] == {"symbol": "EURUSD.r", "side": "BUY", "lots": 1.0, "sl": 1.09,
                              "tp": 1.12, "comment": "copy:m42"}
    assert row["client_order_id"] == f"cm42.{mt5_id}"
    (mapping,) = repo.mapping_rows(org_id=ORG_ID)
    assert (mapping["master_position_id"], mapping["slave_account_id"], mapping["status"],
            mapping["symbol"]) == (42, mt5_id, "pending", "EURUSD")
    (event,) = _events(repo, "mt5_command_queued")
    assert event[1] == mt5_id and event[2]["command_id"] == row["id"]
    assert event[2]["intent_type"] == "OpenMarket"


def test_a_ctrader_slave_still_goes_to_the_wire(world):
    repo, mt5_id, dispatcher, sent = world
    dispatcher.dispatch([_open(CTRADER_SLAVE, symbol_id=1)], org_id=ORG_ID)
    assert [a for a, _m in sent] == [CTRADER_SLAVE]
    assert repo.mt5_commands_open(CTRADER_SLAVE) == [] and repo.mt5_commands_open(mt5_id) == []


def test_every_intent_kind_becomes_its_command(world):
    repo, mt5_id, dispatcher, sent = world
    dispatcher.dispatch([
        ClosePosition(mt5_id, 7001, 50),
        AmendPositionSLTP(mt5_id, 7001, 1.08, None),
        PlacePending(mt5_id, 9, EURUSD_ID, Side.SELL, PendingType.LIMIT, 100, 1.12, None, None,
                     None, "copy:o9", symbol_name="EURUSD"),
        AmendPending(mt5_id, 5551, PendingType.LIMIT, 100, 1.121, None, None),
        CancelPending(mt5_id, 5551),
    ], org_id=ORG_ID)
    assert sent == []
    rows = repo.mt5_commands_open(mt5_id)
    assert [r["kind"] for r in rows] == [
        "close", "amend", "place_pending", "amend_pending", "cancel_pending"]
    assert rows[2]["payload"]["symbol"] == "EURUSD.r"
    assert rows[2]["client_order_id"] == f"co9.{mt5_id}"
    assert [m["master_order_id"] for m in repo.mapping_rows(org_id=ORG_ID)] == [9]


def test_an_unmatched_symbol_is_an_alert_naming_the_details_panel(world):
    repo, mt5_id, dispatcher, sent = world
    dispatcher.dispatch([_open(mt5_id, symbol_id=999)], org_id=ORG_ID)
    assert sent == [] and repo.mt5_commands_open(mt5_id) == [] and repo.mapping_rows() == []
    (event,) = _events(repo, "mt5_symbol_unmatched")
    assert event[0] == "warning" and event[1] == mt5_id
    assert "'EURUSD'" in event[2]["message"] and "Details" in event[2]["message"]


def test_the_kill_switch_and_dry_run_gate_mt5_like_everything_else(world):
    repo, mt5_id, dispatcher, sent = world
    repo.set_org_setting(ORG_ID, "copying_enabled", False)
    dispatcher.dispatch([_open(mt5_id)], org_id=ORG_ID)
    assert repo.mt5_commands_open(mt5_id) == [] and repo.mapping_rows() == []
    repo.set_org_setting(ORG_ID, "copying_enabled", True)
    repo.set_org_setting(ORG_ID, "dry_run", True)
    dispatcher.dispatch([_open(mt5_id)], org_id=ORG_ID)
    assert repo.mt5_commands_open(mt5_id) == []                      # dry-run queues nothing
    assert [m["status"] for m in repo.mapping_rows()] == ["pending"]  # ...but records the would-be copy


def test_a_dispatcher_without_an_mt5_lane_behaves_as_before(world):
    repo, mt5_id, _dispatcher, _sent = world
    sent = []
    bucket = Mock()
    bucket.acquire.return_value = defer.succeed(None)
    plain = Dispatcher(lambda a, m: (sent.append(a), defer.succeed(None))[1], repo, bucket,
                       clock=Clock())
    plain.dispatch([_open(CTRADER_SLAVE, symbol_id=1)], org_id=ORG_ID)
    assert sent == [CTRADER_SLAVE]
