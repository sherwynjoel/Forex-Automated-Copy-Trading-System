"""MT5Outbox (copier/src/copier/mt5/outbox.py): intents become queued
commands; delivery, re-delivery, expiry and ack settlement."""

import time
from datetime import datetime, timezone

import psycopg
import pytest
from twisted.internet.task import Clock

from copier.db.repo import Repo
from copier.domain.models import (
    Alert, AmendPending, AmendPositionSLTP, CancelPending, ClosePosition, OpenMarket,
    PendingType, PlacePending, Side)
from copier.mt5.outbox import (
    CLOSE_SUFFIX, MAX_ATTEMPTS, NO_ACK_MESSAGE, OPEN_COMMAND_TTL_S, REDELIVER_AFTER_S, AckOutcome,
    MT5Outbox, command_for_intent)
from copier.mt5.protocol import Command
from copier.testing.mt5_fixtures import ack


@pytest.fixture
def world(db, seed_mt5_account):
    with psycopg.connect(db, autocommit=True) as conn:
        (org_id,) = conn.execute(
            "INSERT INTO orgs (name) VALUES ('MT5 Org') RETURNING id").fetchone()
    mt5_id = seed_mt5_account(org_id)
    repo = Repo(db)
    return repo, org_id, mt5_id, MT5Outbox(repo, clock=Clock())


def _events(repo, action):
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        return conn.execute(
            "SELECT severity, payload FROM events WHERE payload->>'action' = %s ORDER BY id",
            (action,)).fetchall()


def _row(repo, command_id):
    with psycopg.connect(repo.dsn, autocommit=True) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            return cur.execute(
                "SELECT * FROM mt5_commands WHERE id = %s", (command_id,)).fetchone()


class TestCommandForIntent:
    def test_open(self):
        intent = OpenMarket(slave_account_id=5, master_position_id=42, symbol_id=7, side=Side.BUY,
                            volume=100, stop_loss=1.09, take_profit=1.12, label="copy:m42",
                            symbol_name="EURUSD", entry_price=1.1)
        assert command_for_intent(intent, "EURUSD.r") == ("open", {
            "symbol": "EURUSD.r", "side": "BUY", "lots": 1.0, "sl": 1.09, "tp": 1.12,
            "comment": "copy:m42"})

    def test_open_without_protection_carries_zeros(self):
        intent = OpenMarket(slave_account_id=5, master_position_id=42, symbol_id=7, side=Side.SELL,
                            volume=1, stop_loss=None, take_profit=None, label="copy:m42")
        assert command_for_intent(intent, "XAUUSD.r")[1] == {
            "symbol": "XAUUSD.r", "side": "SELL", "lots": 0.01, "sl": 0.0, "tp": 0.0,
            "comment": "copy:m42"}

    def test_close_amend_and_pending_kinds(self):
        assert command_for_intent(ClosePosition(5, 7001, 50), "") == (
            "close", {"position": 7001, "lots": 0.5})
        assert command_for_intent(AmendPositionSLTP(5, 7001, 1.09, None), "") == (
            "amend", {"position": 7001, "sl": 1.09, "tp": 0.0})
        assert command_for_intent(
            PlacePending(5, 9, 7, Side.SELL, PendingType.STOP, 200, 1.08, None, 1.05,
                         1_760_000_000_000, "copy:o9", symbol_name="EURUSD"), "EURUSD.r") == (
            "place_pending", {"symbol": "EURUSD.r", "type": "SELL_STOP", "lots": 2.0,
                              "price": 1.08, "sl": 0.0, "tp": 1.05,
                              "expiry_ms": 1_760_000_000_000, "comment": "copy:o9"})
        assert command_for_intent(
            AmendPending(5, 5551, PendingType.LIMIT, 100, 1.091, 1.08, None), "") == (
            "amend_pending", {"order": 5551, "lots": 1.0, "price": 1.091, "sl": 1.08, "tp": 0.0})
        assert command_for_intent(CancelPending(5, 5551), "") == (
            "cancel_pending", {"order": 5551})

    def test_other_intents_have_no_command(self):
        with pytest.raises(ValueError):
            command_for_intent(Alert(5, "x"), "")


class TestEnqueue:
    def test_enqueue_intent_writes_the_row_with_its_client_order_id(self, world):
        repo, org_id, mt5_id, outbox = world
        intent = OpenMarket(slave_account_id=mt5_id, master_position_id=42, symbol_id=7,
                            side=Side.BUY, volume=100, stop_loss=None, take_profit=None,
                            label="copy:m42", symbol_name="EURUSD")
        command_id = outbox.enqueue_intent(intent, org_id, "EURUSD.r")
        (row,) = repo.mt5_commands_open(mt5_id)
        assert row["id"] == command_id and row["kind"] == "open" and row["org_id"] == org_id
        assert row["payload"]["symbol"] == "EURUSD.r" and row["payload"]["lots"] == 1.0
        assert row["client_order_id"] == f"cm42.{mt5_id}"
        assert outbox.pending_count(mt5_id) == 1

    def test_enqueue_takes_a_raw_command(self, world):
        repo, org_id, mt5_id, outbox = world
        outbox.enqueue(mt5_id, org_id, "close", {"position": 7001, "lots": 0.0})
        (row,) = repo.mt5_commands_open(mt5_id)
        assert (row["kind"], row["payload"], row["client_order_id"]) == (
            "close", {"position": 7001, "lots": 0.0}, None)


class TestDeliverable:
    def test_returns_queued_commands_in_id_order_and_marks_them_sent(self, world):
        repo, org_id, mt5_id, outbox = world
        first = outbox.enqueue(mt5_id, org_id, "close", {"position": 1, "lots": 0.0})
        second = outbox.enqueue(mt5_id, org_id, "amend", {"position": 1, "sl": 1.0, "tp": 0.0})
        now = time.time()
        commands = outbox.deliverable(mt5_id, now)
        assert commands == [
            Command(first, "close", {"position": 1, "lots": 0.0}, None),
            Command(second, "amend", {"position": 1, "sl": 1.0, "tp": 0.0}, None)]
        rows = repo.mt5_commands_open(mt5_id)
        assert [(r["status"], r["attempts"]) for r in rows] == [("sent", 1), ("sent", 1)]
        assert rows[0]["sent_at"] == datetime.fromtimestamp(now, tz=timezone.utc)
        assert outbox.deliverable(mt5_id, now + 1.0) == []      # sent, still waiting for the ack

    def test_redelivers_after_ten_seconds_up_to_three_attempts_then_fails(self, world):
        repo, org_id, mt5_id, outbox = world
        command_id = outbox.enqueue(mt5_id, org_id, "close", {"position": 1, "lots": 0.0})
        now = time.time()
        assert [c.id for c in outbox.deliverable(mt5_id, now)] == [command_id]
        assert outbox.deliverable(mt5_id, now + REDELIVER_AFTER_S - 0.5) == []
        assert [c.id for c in outbox.deliverable(mt5_id, now + REDELIVER_AFTER_S + 1)] == [command_id]
        assert [c.id for c in outbox.deliverable(mt5_id, now + 2 * (REDELIVER_AFTER_S + 1))] == [command_id]
        assert _row(repo, command_id)["attempts"] == MAX_ATTEMPTS
        assert outbox.deliverable(mt5_id, now + 3 * (REDELIVER_AFTER_S + 1)) == []
        row = _row(repo, command_id)
        assert (row["status"], row["result"]["message"], row["result"]["ok"]) == (
            "failed", NO_ACK_MESSAGE, False)
        (event,) = _events(repo, "mt5_command_unacked")
        assert event[0] == "error" and event[1]["command_id"] == command_id
        assert event[1]["attempts"] == MAX_ATTEMPTS

    def test_giving_up_on_a_mapped_command_fails_its_mapping(self, world):
        """A close is used (opens expire after 30 s, before three
        re-deliveries are up); the mapping half is the same code path."""
        repo, org_id, mt5_id, outbox = world
        repo.create_position_mapping(42, mt5_id, f"cm42.{mt5_id}", org_id=org_id)
        outbox.enqueue(mt5_id, org_id, "close", {"position": 1, "lots": 0.0}, f"cm42.{mt5_id}")
        now = time.time()
        for n in range(MAX_ATTEMPTS + 1):
            outbox.deliverable(mt5_id, now + n * (REDELIVER_AFTER_S + 1))
        (mapping,) = repo.mapping_rows(org_id=org_id)
        assert (mapping["status"], mapping["error"]) == ("failed", NO_ACK_MESSAGE)

    def test_a_stale_open_expires_with_its_mapping_but_a_close_never_does(self, world):
        repo, org_id, mt5_id, outbox = world
        repo.create_position_mapping(42, mt5_id, f"cm42.{mt5_id}", org_id=org_id)
        open_id = outbox.enqueue(
            mt5_id, org_id, "open",
            {"symbol": "EURUSD.r", "side": "BUY", "lots": 1.0, "sl": 0.0, "tp": 0.0,
             "comment": "copy:m42"}, f"cm42.{mt5_id}")
        close_id = outbox.enqueue(mt5_id, org_id, "close", {"position": 1, "lots": 0.0})
        now = time.time()
        # The terminal comes back OPEN_COMMAND_TTL_S + 1 later.
        commands = outbox.deliverable(mt5_id, now + OPEN_COMMAND_TTL_S + 1)
        assert [c.id for c in commands] == [close_id]
        row = _row(repo, open_id)
        assert (row["status"], row["result"]["message"]) == ("failed", "terminal offline")
        (mapping,) = repo.mapping_rows(org_id=org_id)
        assert (mapping["status"], mapping["error"]) == ("failed", "terminal offline")
        (event,) = _events(repo, "mt5_command_expired")
        assert event[0] == "warning" and event[1]["command_id"] == open_id

    def test_a_fresh_open_is_delivered(self, world):
        repo, org_id, mt5_id, outbox = world
        open_id = outbox.enqueue(
            mt5_id, org_id, "open",
            {"symbol": "EURUSD.r", "side": "BUY", "lots": 1.0, "sl": 0.0, "tp": 0.0, "comment": ""})
        assert [c.id for c in outbox.deliverable(mt5_id, time.time())] == [open_id]


class TestApplyAcks:
    def test_an_ack_settles_the_command_and_reports_the_outcome(self, world):
        repo, org_id, mt5_id, outbox = world
        command_id = outbox.enqueue(
            mt5_id, org_id, "open",
            {"symbol": "EURUSD.r", "side": "BUY", "lots": 1.0, "sl": 0.0, "tp": 0.0,
             "comment": "copy:m42"}, f"cm42.{mt5_id}")
        outbox.deliverable(mt5_id, time.time())
        outcomes = outbox.apply_acks(mt5_id, [
            ack(command_id, position=7001, deal=8, order=9, price=1.1001, volume=100)])
        assert outcomes == [AckOutcome(
            command_id=command_id, kind="open", client_order_id=f"cm42.{mt5_id}", ok=True,
            message="done", position=7001, order=9, price=1.1001, volume=100, deal=8)]
        row = _row(repo, command_id)
        assert row["status"] == "done" and row["done_at"] is not None
        assert row["result"] == {"ok": True, "retcode": 10009, "message": "done",
                                 "position": 7001, "deal": 8, "order": 9, "price": 1.1001,
                                 "lots": 1.0}
        assert outbox.pending_count(mt5_id) == 0

    def test_a_failed_ack(self, world):
        repo, org_id, mt5_id, outbox = world
        command_id = outbox.enqueue(mt5_id, org_id, "close", {"position": 7001, "lots": 0.0})
        (outcome,) = outbox.apply_acks(
            mt5_id, [ack(command_id, ok=False, retcode=10036, message="position closed")])
        assert (outcome.ok, outcome.message, outcome.kind, outcome.position) == (
            False, "position closed", "close", 7001)
        assert _row(repo, command_id)["status"] == "failed"

    def test_position_and_order_fall_back_to_the_payload(self, world):
        repo, org_id, mt5_id, outbox = world
        cancel_id = outbox.enqueue(mt5_id, org_id, "cancel_pending", {"order": 5551})
        (outcome,) = outbox.apply_acks(mt5_id, [ack(cancel_id)])
        assert outcome.order == 5551 and outcome.position is None

    def test_duplicate_unknown_and_foreign_acks_are_ignored(self, world, seed_mt5_account):
        repo, org_id, mt5_id, outbox = world
        other = seed_mt5_account(org_id)
        command_id = outbox.enqueue(mt5_id, org_id, "close", {"position": 7001, "lots": 0.0})
        theirs = outbox.enqueue(other, org_id, "close", {"position": 8001, "lots": 0.0})
        assert len(outbox.apply_acks(mt5_id, [ack(command_id)])) == 1
        assert outbox.apply_acks(mt5_id, [ack(command_id)]) == []      # duplicate
        assert outbox.apply_acks(mt5_id, [ack(424242)]) == []          # unknown
        assert outbox.apply_acks(mt5_id, [ack(theirs)]) == []          # another account's
        assert _row(repo, theirs)["status"] == "queued"

    def test_a_volume_the_ack_omits_falls_back_to_the_payloads_lots(self, world):
        repo, org_id, mt5_id, outbox = world
        command_id = outbox.enqueue(mt5_id, org_id, "close", {"position": 7001, "lots": 0.4})
        (outcome,) = outbox.apply_acks(mt5_id, [ack(command_id, deal=8)])
        assert (outcome.volume, outcome.deal) == (40, 8)


def _open(mt5_id, master_position_id=42, side=Side.BUY):
    return OpenMarket(slave_account_id=mt5_id, master_position_id=master_position_id, symbol_id=7,
                      side=side, volume=100, stop_loss=1.09, take_profit=1.12,
                      label=f"copy:m{master_position_id}", symbol_name="EURUSD")


class TestNetting:
    """A netting follower: every copy on a symbol shares one net position,
    so a mapping's close is an opposite-side open that names the mapping."""

    @pytest.fixture
    def netting(self, world):
        repo, org_id, mt5_id, _outbox = world
        return repo, org_id, mt5_id, MT5Outbox(repo, clock=Clock(), margin_mode=lambda _a: "netting")

    def test_a_close_becomes_an_opposite_open_named_after_its_mapping(self, netting):
        repo, org_id, mt5_id, outbox = netting
        outbox.enqueue_intent(_open(mt5_id), org_id, "EURUSD.r")
        close_id = outbox.enqueue_intent(
            ClosePosition(mt5_id, 9001, 40, master_position_id=42), org_id, "")
        row = _row(repo, close_id)
        assert (row["kind"], row["client_order_id"]) == ("open", f"cm42.{mt5_id}{CLOSE_SUFFIX}")
        assert row["payload"] == {"symbol": "EURUSD.r", "side": "SELL", "lots": 0.4, "sl": 0.0,
                                  "tp": 0.0, "comment": "close:m42"}

    def test_a_sell_copy_closes_with_a_buy(self, netting):
        repo, org_id, mt5_id, outbox = netting
        outbox.enqueue_intent(_open(mt5_id, side=Side.SELL), org_id, "XAUUSD.r")
        close_id = outbox.enqueue_intent(
            ClosePosition(mt5_id, 9001, 100, master_position_id=42), org_id, "")
        assert _row(repo, close_id)["payload"]["side"] == "BUY"
        assert _row(repo, close_id)["payload"]["symbol"] == "XAUUSD.r"

    def test_the_close_ack_is_reported_under_the_mappings_own_coid(self, netting):
        repo, org_id, mt5_id, outbox = netting
        outbox.enqueue_intent(_open(mt5_id), org_id, "EURUSD.r")
        close_id = outbox.enqueue_intent(
            ClosePosition(mt5_id, 9001, 40, master_position_id=42), org_id, "")
        outbox.deliverable(mt5_id, time.time())
        (outcome,) = outbox.apply_acks(
            mt5_id, [ack(close_id, position=9001, deal=700005, order=700004, price=1.101, volume=40)])
        assert outcome == AckOutcome(
            command_id=close_id, kind="close", client_order_id=f"cm42.{mt5_id}", ok=True,
            message="done", position=9001, order=700004, price=1.101, volume=40, deal=700005)
        assert _row(repo, close_id)["status"] == "done"

    def test_a_close_without_a_master_id_is_a_plain_close_on_the_net_ticket(self, netting):
        """Trade page, Close all and the reconciler's orphan close act on the
        net position on both modes."""
        repo, org_id, mt5_id, outbox = netting
        close_id = outbox.enqueue_intent(ClosePosition(mt5_id, 9001, 40), org_id, "")
        row = _row(repo, close_id)
        assert (row["kind"], row["payload"], row["client_order_id"]) == (
            "close", {"position": 9001, "lots": 0.4}, None)

    def test_a_mapping_without_an_open_command_falls_back_to_a_plain_close(self, netting):
        """An adopted mapping has no open command to read a side from."""
        repo, org_id, mt5_id, outbox = netting
        close_id = outbox.enqueue_intent(
            ClosePosition(mt5_id, 9001, 40, master_position_id=77), org_id, "")
        assert _row(repo, close_id)["kind"] == "close"
        (event,) = _events(repo, "mt5_netting_close_unsided")
        assert event[0] == "warning" and event[1]["client_order_id"] == f"cm77.{mt5_id}"

    def test_an_amend_goes_to_the_net_ticket(self, netting):
        repo, org_id, mt5_id, outbox = netting
        amend_id = outbox.enqueue_intent(AmendPositionSLTP(mt5_id, 9001, 1.095, None), org_id, "")
        row = _row(repo, amend_id)
        assert (row["kind"], row["payload"]) == ("amend", {"position": 9001, "sl": 1.095, "tp": 0.0})

    def test_a_netting_close_never_expires(self, netting):
        repo, org_id, mt5_id, outbox = netting
        outbox.enqueue_intent(_open(mt5_id), org_id, "EURUSD.r")
        close_id = outbox.enqueue_intent(
            ClosePosition(mt5_id, 9001, 100, master_position_id=42), org_id, "")
        with psycopg.connect(repo.dsn, autocommit=True) as conn:
            conn.execute("UPDATE mt5_commands SET created_at = now() - interval '60 seconds'")
        delivered = outbox.deliverable(mt5_id, time.time())
        assert [c.id for c in delivered] == [close_id]        # the stale open expired, the close did not
        assert _row(repo, close_id)["status"] == "sent"

    def test_hedging_followers_keep_a_plain_close(self, world):
        repo, org_id, mt5_id, _outbox = world
        outbox = MT5Outbox(repo, clock=Clock(), margin_mode=lambda _a: "hedging")
        close_id = outbox.enqueue_intent(
            ClosePosition(mt5_id, 7001, 50, master_position_id=42), org_id, "")
        assert _row(repo, close_id)["kind"] == "close"
        assert _row(repo, close_id)["payload"] == {"position": 7001, "lots": 0.5}
