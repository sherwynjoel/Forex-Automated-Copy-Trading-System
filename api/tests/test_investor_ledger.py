"""Ledger rules with no I/O: what an amount may look like, how a summary is
computed, and which status changes are legal."""
from decimal import Decimal

import pytest

from api.investor_ledger import (
    DEPOSIT_TRANSITIONS, WITHDRAWAL_TRANSITIONS, LedgerError, Summary,
    can_transition, clean_text, parse_amount, summarise, summary_json)


class TestAmount:
    @pytest.mark.parametrize("raw, expected", [
        (100, Decimal("100")), ("250.5", Decimal("250.5")), (0.01, Decimal("0.01")),
        ("1000000.00", Decimal("1000000.00")),
    ])
    def test_accepts_positive_money(self, raw, expected):
        assert parse_amount(raw) == expected

    @pytest.mark.parametrize("raw", [0, -5, "0.001", "abc", None, "", True, "1e400",
                                     "1234567890123", float("nan")])
    def test_refuses_what_a_ledger_cannot_hold(self, raw):
        with pytest.raises(LedgerError):
            parse_amount(raw)

    def test_message_names_the_field(self):
        with pytest.raises(LedgerError, match="amount"):
            parse_amount("-1")


class TestText:
    def test_trims_and_keeps(self):
        assert clean_text("  abc123  ", "txid") == "abc123"

    def test_required_text_may_not_be_blank(self):
        with pytest.raises(LedgerError, match="txid"):
            clean_text("   ", "txid")

    def test_optional_text_returns_none_when_blank(self):
        assert clean_text("  ", "note", required=False) is None

    def test_too_long_is_refused(self):
        with pytest.raises(LedgerError, match="128"):
            clean_text("x" * 129, "destination")


class TestTransitions:
    def test_deposits_only_move_forward(self):
        assert can_transition(DEPOSIT_TRANSITIONS, "pending", "confirmed")
        assert can_transition(DEPOSIT_TRANSITIONS, "pending", "rejected")
        assert not can_transition(DEPOSIT_TRANSITIONS, "confirmed", "pending")
        assert not can_transition(DEPOSIT_TRANSITIONS, "rejected", "confirmed")

    def test_withdrawals_follow_the_spec_diagram(self):
        assert can_transition(WITHDRAWAL_TRANSITIONS, "requested", "approved")
        assert can_transition(WITHDRAWAL_TRANSITIONS, "requested", "rejected")
        assert can_transition(WITHDRAWAL_TRANSITIONS, "approved", "paid")
        assert can_transition(WITHDRAWAL_TRANSITIONS, "approved", "rejected")
        assert not can_transition(WITHDRAWAL_TRANSITIONS, "requested", "paid")
        assert not can_transition(WITHDRAWAL_TRANSITIONS, "paid", "rejected")
        assert not can_transition(WITHDRAWAL_TRANSITIONS, "rejected", "approved")


class TestSummary:
    def test_only_confirmed_and_paid_rows_count(self):
        s = summarise(
            deposits=[("confirmed", Decimal("5000")), ("pending", Decimal("1000")),
                      ("rejected", Decimal("9"))],
            withdrawals=[("paid", Decimal("500")), ("requested", Decimal("200")),
                         ("approved", Decimal("100")), ("rejected", Decimal("7"))],
            equity=Decimal("5120.50"))
        assert s == Summary(
            total_deposited=Decimal("5000"), total_withdrawn=Decimal("500"),
            net_deposits=Decimal("4500"), pending_withdrawn=Decimal("300"),
            equity=Decimal("5120.50"), profit=Decimal("620.50"),
            available=Decimal("4820.50"))

    def test_unknown_equity_leaves_profit_and_available_unknown(self):
        s = summarise([("confirmed", Decimal("100"))], [], equity=None)
        assert s.net_deposits == Decimal("100")
        assert s.equity is None and s.profit is None and s.available is None

    def test_json_is_two_decimal_floats(self):
        s = summarise([("confirmed", Decimal("100"))], [], equity=Decimal("133.333"))
        assert summary_json(s) == {
            "total_deposited": 100.0, "total_withdrawn": 0.0, "net_deposits": 100.0,
            "pending_withdrawn": 0.0, "equity": 133.33, "profit": 33.33, "available": 133.33}
