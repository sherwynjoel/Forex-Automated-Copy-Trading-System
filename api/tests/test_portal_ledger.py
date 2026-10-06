# api/tests/test_portal_ledger.py
"""Ledger rules with no I/O: what an amount may look like, balances and
holds per wallet, the floored available figure, fees, the four transition
tables and the transfer pair rule."""
from decimal import Decimal

import pytest

from api.portal_ledger import (
    CENT, DEPOSIT_TRANSITIONS, DESTINATION_TRANSITIONS, INVESTOR_MOVES, TRANSFER_PAIRS,
    TRANSFER_TRANSITIONS, WALLETS, WITHDRAWAL_TRANSITIONS, LedgerError, available, balance,
    can_transition, clean_text, deposit_bonus, fee_for, floor_cents, holds, money, parse_amount,
    round_cents, transfer_pair)

D = Decimal


class TestAmount:
    @pytest.mark.parametrize("raw, expected", [
        (100, D("100")), ("250.5", D("250.5")), (0.01, D("0.01")),
        ("1000000.00", D("1000000.00")),
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
        assert clean_text("  abc123  ", "reference") == "abc123"

    def test_required_text_may_not_be_blank(self):
        with pytest.raises(LedgerError, match="reference"):
            clean_text("   ", "reference")

    def test_optional_text_returns_none_when_blank(self):
        assert clean_text("  ", "note", required=False) is None

    def test_too_long_is_refused(self):
        with pytest.raises(LedgerError, match="128"):
            clean_text("x" * 129, "nickname")


class TestCents:
    def test_floor_cents_never_rounds_up(self):
        assert floor_cents(D("5120.506")) == D("5120.50")
        assert floor_cents(D("5120.509")) == D("5120.50")
        assert floor_cents(D("5120.5")) == D("5120.50")

    def test_floor_and_round_disagree_on_a_half_cent(self):
        assert floor_cents(D("0.005")) == D("0.00")
        assert round_cents(D("0.005")) == D("0.01")
        assert round_cents(D("133.333")) == D("133.33")

    def test_floor_cents_of_a_negative_moves_toward_zero(self):
        assert floor_cents(D("-10.009")) == D("-10.00")

    def test_available_is_the_floored_difference(self):
        # The parked bug of the previous design: 5120.506 - 100 must give
        # 5020.50, the figure "Use max" fills, never 5020.51.
        assert available(D("5120.506"), D("100")) == D("5020.50")
        assert available(D("100"), D("0")) == D("100.00")

    def test_available_may_be_negative_after_an_adjustment(self):
        assert available(D("10"), D("25")) == D("-15.00")

    def test_cent_is_the_quantum(self):
        assert CENT == D("0.01")


class TestFees:
    @pytest.mark.parametrize("amount, pct, fee", [
        ("250", "1.5", "3.75"), ("100", "0.333", "0.33"), ("100", "0", "0.00"),
        ("1", "0.5", "0.01"), ("1000000", "99.999", "999990.00"),
    ])
    def test_fee_is_rounded_half_up_to_cents(self, amount, pct, fee):
        assert fee_for(D(amount), D(pct)) == D(fee)


class TestBalances:
    def test_every_wallet_is_present_even_when_empty(self):
        assert balance([]) == {w: D("0") for w in WALLETS}
        assert WALLETS == ("main", "credit", "pamm", "social")

    def test_entries_sum_per_wallet(self):
        b = balance([("main", D("5000")), ("main", D("-500")), ("main", D("620.50")),
                     ("pamm", D("12.25")), ("credit", D("100")), ("credit", D("-100"))])
        assert b == {"main": D("5120.50"), "credit": D("0"), "pamm": D("12.25"), "social": D("0")}


class TestHolds:
    def test_withdrawals_hold_on_main_and_transfers_on_their_source(self):
        h = holds([D("60"), D("40")], [("main", D("25")), ("pamm", D("5"))])
        assert h == {"main": D("125"), "credit": D("0"), "pamm": D("5"), "social": D("0")}

    def test_nothing_open_holds_nothing(self):
        assert holds([], []) == {w: D("0") for w in WALLETS}


class TestTransitions:
    def test_deposits(self):
        assert DEPOSIT_TRANSITIONS == {"pending": {"confirmed", "rejected", "cancelled"}}
        for new in ("confirmed", "rejected", "cancelled"):
            assert can_transition("deposits", "pending", new)
        assert not can_transition("deposits", "confirmed", "pending")
        assert not can_transition("deposits", "confirmed", "rejected")
        assert not can_transition("deposits", "cancelled", "confirmed")

    def test_withdrawals(self):
        assert WITHDRAWAL_TRANSITIONS == {
            "requested": {"approved", "rejected", "cancelled"}, "approved": {"paid", "rejected"}}
        assert can_transition("withdrawals", "requested", "approved")
        assert can_transition("withdrawals", "requested", "cancelled")
        assert can_transition("withdrawals", "approved", "paid")
        assert can_transition("withdrawals", "approved", "rejected")
        assert not can_transition("withdrawals", "requested", "paid")
        assert not can_transition("withdrawals", "approved", "cancelled")
        assert not can_transition("withdrawals", "paid", "rejected")

    def test_transfers(self):
        assert TRANSFER_TRANSITIONS == {
            "requested": {"approved", "done", "rejected", "cancelled"},
            "approved": {"done", "rejected"}}
        assert can_transition("transfers", "requested", "done")
        assert can_transition("transfers", "approved", "done")
        assert not can_transition("transfers", "approved", "cancelled")
        assert not can_transition("transfers", "done", "rejected")

    def test_destinations(self):
        assert DESTINATION_TRANSITIONS == {
            "pending": {"approved", "rejected", "removed"}, "approved": {"removed"}}
        assert can_transition("payout_destinations", "approved", "removed")
        assert not can_transition("payout_destinations", "rejected", "approved")
        assert not can_transition("payout_destinations", "removed", "approved")

    def test_the_investor_moves_are_cancel_and_remove(self):
        assert INVESTOR_MOVES == {"cancelled", "removed"}


class TestTransferPairs:
    @pytest.mark.parametrize("source, target, pair", [
        ({"kind": "wallet", "wallet": "main"}, {"kind": "account", "account_id": 1001},
         ("main", "account")),
        ({"kind": "account", "account_id": 1001}, {"kind": "wallet", "wallet": "main"},
         ("account", "main")),
        ({"kind": "wallet", "wallet": "pamm"}, {"kind": "wallet", "wallet": "main"},
         ("pamm", "main")),
        ({"kind": "wallet", "wallet": "social"}, {"kind": "wallet", "wallet": "main"},
         ("social", "main")),
        ({"kind": "wallet", "wallet": "credit"}, {"kind": "account", "account_id": 1001},
         ("credit", "account")),
    ])
    def test_the_allowed_pairs(self, source, target, pair):
        assert transfer_pair(source, target) == pair
        assert pair in TRANSFER_PAIRS

    @pytest.mark.parametrize("source, target", [
        ({"kind": "wallet", "wallet": "main"}, {"kind": "wallet", "wallet": "pamm"}),
        ({"kind": "wallet", "wallet": "credit"}, {"kind": "wallet", "wallet": "main"}),
        ({"kind": "wallet", "wallet": "main"}, {"kind": "wallet", "wallet": "main"}),
        ({"kind": "account", "account_id": 1}, {"kind": "account", "account_id": 2}),
        ({"kind": "wallet", "wallet": "savings"}, {"kind": "wallet", "wallet": "main"}),
        ({"kind": "wallet"}, {"kind": "wallet", "wallet": "main"}),
        ({"kind": "account"}, {"kind": "wallet", "wallet": "main"}),
        ({"kind": "cash", "wallet": "main"}, {"kind": "wallet", "wallet": "main"}),
        ({"kind": "wallet", "wallet": "credit"}, {"kind": "wallet", "wallet": "pamm"}),
        ({"kind": "account", "account_id": 1001}, {"kind": "wallet", "wallet": "credit"}),
    ])
    def test_everything_else_is_refused_with_one_message(self, source, target):
        with pytest.raises(LedgerError, match="that transfer is not allowed"):
            transfer_pair(source, target)


class TestMoney:
    def test_money_is_a_two_decimal_float_or_none(self):
        assert money(D("133.333")) == 133.33
        assert money(D("0.005")) == 0.01
        assert money(D("-40")) == -40.0
        assert money(None) is None


class TestDepositBonus:
    def test_pct_rounds_half_up_to_the_cent_and_the_cap_wins(self):
        assert deposit_bonus(D("1000"), D("10"), None) == D("100.00")
        assert deposit_bonus(D("33.33"), D("12.5"), None) == D("4.17")   # 4.16625
        assert deposit_bonus(D("1000"), D("10"), D("50")) == D("50")
        assert deposit_bonus(D("0.03"), D("12.5"), None) == D("0.00")
        assert deposit_bonus(D("200"), D("100"), None) == D("200.00")
