"""The VT bridge parser and gate refuse a wrong trade before it can become
one -- same discipline as tradingview_alerts.py, for the htf/ltf contract."""
from datetime import datetime, timedelta, timezone

import pytest

from api.tradingview_alerts import AlertError
from api.vt_bridge_alerts import (
    STALE_MULTIPLIER, TOL_PCT, GateResult, VTAlert, VTSnapshot, check_gate,
    parse_vt_alert)

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)


def _htf_body(**over):
    body = {"secret": "x", "role": "htf", "symbol": "XAUUSD", "tf": "60",
            "bias": "long", "entry": 4385.34, "stop": 4413.15, "target": 4301.91,
            "price": 4355.28, "valid": True, "bar_ms": 1757400000000}
    body.update(over)
    return body


def _ltf_body(**over):
    body = {"secret": "x", "role": "ltf", "symbol": "XAUUSD", "tf": "1",
            "bias": "short", "entry": 4380.63, "stop": 4390.10, "target": 4360.00,
            "price": 4380.63, "valid": True, "trigger": True, "lots": 0.01,
            "bar_ms": 1757400060000}
    body.update(over)
    return body


class TestParseVtAlert:
    def test_a_valid_htf_alert(self):
        alert = parse_vt_alert(_htf_body(), max_lots=1.0)
        assert alert == VTAlert("htf", "XAUUSD", "60", "long", 4385.34, 4413.15,
                                4301.91, 4355.28, True, None, None, 1757400000000)

    def test_a_valid_ltf_alert(self):
        alert = parse_vt_alert(_ltf_body(), max_lots=1.0)
        assert alert == VTAlert("ltf", "XAUUSD", "1", "short", 4380.63, 4390.10,
                                4360.00, 4380.63, True, True, 0.01, 1757400060000)

    def test_bar_ms_as_a_numeric_string_is_accepted(self):
        """Pine renders large timestamps as a quoted string to dodge
        scientific-notation formatting; int() must still parse it."""
        alert = parse_vt_alert(_htf_body(bar_ms="1757400000000"), max_lots=1.0)
        assert alert.bar_ms == 1757400000000

    @pytest.mark.parametrize("role", [None, "", "htf ", "HTF", "ltf2", 42])
    def test_role_must_be_htf_or_ltf(self, role):
        with pytest.raises(AlertError):
            parse_vt_alert(_htf_body(role=role), max_lots=1.0)

    @pytest.mark.parametrize("bias", [None, "", "up", "LONG", 1])
    def test_bias_must_be_long_or_short(self, bias):
        with pytest.raises(AlertError):
            parse_vt_alert(_htf_body(bias=bias), max_lots=1.0)

    @pytest.mark.parametrize("key", ["entry", "stop", "target", "price"])
    def test_prices_must_be_positive_finite_numbers(self, key):
        with pytest.raises(AlertError):
            parse_vt_alert(_htf_body(**{key: -1}), max_lots=1.0)
        with pytest.raises(AlertError):
            parse_vt_alert(_htf_body(**{key: None}), max_lots=1.0)
        with pytest.raises(AlertError):
            parse_vt_alert(_htf_body(**{key: float("nan")}), max_lots=1.0)

    def test_valid_must_be_a_real_boolean(self):
        with pytest.raises(AlertError):
            parse_vt_alert(_htf_body(valid="true"), max_lots=1.0)

    def test_bar_ms_is_required(self):
        with pytest.raises(AlertError):
            parse_vt_alert(_htf_body(bar_ms=None), max_lots=1.0)

    def test_htf_ignores_a_missing_lots(self):
        body = _htf_body()
        assert "lots" not in body
        alert = parse_vt_alert(body, max_lots=1.0)
        assert alert.lots is None

    def test_ltf_requires_trigger(self):
        with pytest.raises(AlertError):
            parse_vt_alert(_ltf_body(trigger=None), max_lots=1.0)

    def test_ltf_requires_lots(self):
        with pytest.raises(AlertError):
            parse_vt_alert(_ltf_body(lots=None), max_lots=1.0)

    def test_ltf_lots_is_capped_like_the_existing_contract(self):
        with pytest.raises(AlertError, match="above this workspace's cap"):
            parse_vt_alert(_ltf_body(lots=5.0), max_lots=1.0)

    def test_symbol_is_normalised_the_same_way(self):
        alert = parse_vt_alert(_htf_body(symbol="OANDA:XAUUSD"), max_lots=1.0)
        assert alert.symbol == "XAUUSD"


class TestCheckGate:
    def _htf(self, **over):
        base = dict(bias="long", stop=4413.15, target=4301.91, tf="60", received_at=NOW)
        base.update(over)
        return VTSnapshot(**base)

    def _ltf(self, **over):
        base = dict(bias="long", entry=4350.0)
        base.update(over)
        return parse_vt_alert(_ltf_body(**base), max_lots=1.0)

    def test_passes_when_everything_lines_up(self):
        result = check_gate(self._htf(), self._ltf(), {"1"}, NOW)
        assert result == GateResult(True, None)

    def test_gate_0_rejects_a_timeframe_not_on_the_allowlist(self):
        result = check_gate(self._htf(), self._ltf(), set(), NOW)
        assert not result.passed and "not enabled" in result.reason

    def test_gate_0_runs_before_looking_for_a_snapshot(self):
        """An empty allowlist rejects even when no htf snapshot exists either --
        gate 0 is cheapest-first and must not depend on htf being present."""
        result = check_gate(None, self._ltf(), set(), NOW)
        assert not result.passed and "not enabled" in result.reason

    def test_missing_snapshot_is_rejected(self):
        result = check_gate(None, self._ltf(), {"1"}, NOW)
        assert not result.passed and "no HTF snapshot" in result.reason

    def test_snapshot_exactly_at_the_staleness_boundary_still_passes(self):
        age = timedelta(seconds=STALE_MULTIPLIER * 60 * 60)  # 2 * 60min tf
        htf = self._htf(received_at=NOW - age)
        result = check_gate(htf, self._ltf(), {"1"}, NOW)
        assert result.passed

    def test_snapshot_one_second_past_the_boundary_is_stale(self):
        age = timedelta(seconds=STALE_MULTIPLIER * 60 * 60 + 1)
        htf = self._htf(received_at=NOW - age)
        result = check_gate(htf, self._ltf(), {"1"}, NOW)
        assert not result.passed and "old" in result.reason

    def test_bias_mismatch_is_rejected(self):
        htf = self._htf(bias="short")
        result = check_gate(htf, self._ltf(bias="long"), {"1"}, NOW)
        assert not result.passed and "does not match" in result.reason

    def test_entry_exactly_on_the_band_edge_passes(self):
        htf = self._htf(stop=4413.15, target=4301.91)  # band [4301.91, 4413.15]
        result = check_gate(htf, self._ltf(entry=4301.91), {"1"}, NOW)
        assert result.passed
        result = check_gate(htf, self._ltf(entry=4413.15), {"1"}, NOW)
        assert result.passed

    def test_entry_just_outside_the_tolerance_padded_band_is_rejected(self):
        htf = self._htf(stop=4413.15, target=4301.91)
        width = 4413.15 - 4301.91
        just_outside = 4301.91 - (TOL_PCT * width) - 0.01
        result = check_gate(htf, self._ltf(entry=just_outside), {"1"}, NOW)
        assert not result.passed and "outside the HTF band" in result.reason

    def test_entry_just_inside_the_tolerance_padding_passes(self):
        htf = self._htf(stop=4413.15, target=4301.91)
        width = 4413.15 - 4301.91
        just_inside = 4301.91 - (TOL_PCT * width) + 0.01
        result = check_gate(htf, self._ltf(entry=just_inside), {"1"}, NOW)
        assert result.passed

    def test_band_math_works_for_a_short_htf_too(self):
        """A short HTF has stop ABOVE target -- min/max must not assume order."""
        htf = self._htf(bias="short", stop=4413.15, target=4301.91)
        ltf = self._ltf(bias="short", entry=4380.63)
        result = check_gate(htf, ltf, {"1"}, NOW)
        assert result.passed
