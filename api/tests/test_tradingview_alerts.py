"""The alert parser refuses a wrong trade before it can become one."""
import pytest

from api.tradingview_alerts import (
    Alert, AlertError, HARD_MAX_LOTS, find_master_positions, normalise_ticker,
    parse_alert)


class TestTicker:
    @pytest.mark.parametrize("raw, expected", [
        ("XAUUSD", "XAUUSD"),
        ("OANDA:XAUUSD", "XAUUSD"),          # the common case
        ("FX:EURUSD", "EURUSD"),
        ("fx:eurusd", "EURUSD"),
        ("  OANDA:XAUUSD ", "XAUUSD"),
        ("BINANCE:BTCUSDT.P", "BTCUSDT"),    # perpetual suffix
        ("OANDA:XAUUSD1!", "XAUUSD"),        # continuous-contract suffix
        ("XAUUSD.CFD", "XAUUSD"),
    ])
    def test_strips_exchange_and_decorations(self, raw, expected):
        assert normalise_ticker(raw) == expected

    def test_never_translates_to_a_lookalike(self):
        """BTCUSDT is not BTCUSD. Rejecting later beats trading the neighbour."""
        assert normalise_ticker("BINANCE:BTCUSDT") == "BTCUSDT"

    def test_two_letter_futures_roots_are_refused_on_purpose(self):
        """cTrader names are 3+ chars; "ES" is a futures root this platform
        does not trade. Short names are far more often garbage than real."""
        with pytest.raises(AlertError):
            normalise_ticker("CME_MINI:ES1!")

    @pytest.mark.parametrize("raw", ["", "  ", "OANDA:", "XA", "XAU USD", "xau-usd", 42, None, "A" * 30])
    def test_refuses_anything_that_is_not_a_plain_name(self, raw):
        with pytest.raises(AlertError):
            normalise_ticker(raw)


class TestParse:
    def test_the_template_the_operator_will_paste(self):
        alert = parse_alert(
            {"action": "buy", "symbol": "OANDA:XAUUSD", "lots": 0.01,
             "stop_loss": 4570.0, "take_profit": 4600.0, "id": "abc"},
            max_lots=1.0)
        assert alert == Alert("buy", "XAUUSD", 0.01, 4570.0, 4600.0, "abc")

    def test_ticker_is_accepted_as_an_alias_for_symbol(self):
        """{{ticker}} is the placeholder TradingView actually offers."""
        alert = parse_alert({"action": "sell", "ticker": "FX:EURUSD", "lots": 0.5, "id": "1"}, 1.0)
        assert alert.symbol == "EURUSD"

    def test_close_needs_no_size_and_ignores_one_it_is_given(self):
        """A shared template can carry lots harmlessly on a close."""
        alert = parse_alert({"action": "close", "symbol": "XAUUSD", "lots": 0.01, "id": "7"}, 1.0)
        assert alert == Alert("close", "XAUUSD", None, None, None, "7")

    def test_strings_from_placeholders_are_accepted(self):
        """TradingView substitutes placeholders as text; "0.01" must work."""
        alert = parse_alert({"action": "BUY", "symbol": "XAUUSD", "lots": "0.01", "id": "1"}, 1.0)
        assert alert.action == "buy" and alert.lots == 0.01

    # ---- the refusals that matter ----

    def test_not_json_is_refused_with_the_template_shown(self):
        with pytest.raises(AlertError, match="must be JSON"):
            parse_alert("buy XAUUSD", 1.0)

    def test_unknown_action_is_refused(self):
        with pytest.raises(AlertError, match="action must be one of"):
            parse_alert({"action": "long", "symbol": "XAUUSD", "lots": 0.01, "id": "1"}, 1.0)

    def test_missing_lots_on_a_buy_is_refused(self):
        with pytest.raises(AlertError, match="lots is required"):
            parse_alert({"action": "buy", "symbol": "XAUUSD", "id": "1"}, 1.0)

    @pytest.mark.parametrize("lots", [0, -0.01, "abc", float("nan"), float("inf")])
    def test_bad_lots_are_refused(self, lots):
        with pytest.raises(AlertError):
            parse_alert({"action": "buy", "symbol": "XAUUSD", "lots": lots, "id": "1"}, 1.0)

    def test_the_per_org_cap_stops_a_template_typo(self):
        """0.10 meant, 10 sent. The cap is what stands between them."""
        with pytest.raises(AlertError, match="above this workspace's cap of 1"):
            parse_alert({"action": "buy", "symbol": "XAUUSD", "lots": 10, "id": "1"}, max_lots=1.0)

    def test_the_hard_ceiling_holds_even_if_the_org_cap_is_absurd(self):
        """A corrupted or malicious cap must not unlock a hundred lots."""
        with pytest.raises(AlertError, match=f"cap of {HARD_MAX_LOTS:g}"):
            parse_alert({"action": "buy", "symbol": "XAUUSD", "lots": 60, "id": "1"},
                        max_lots=1_000_000)

    @pytest.mark.parametrize("cap", [0, None, -1, float("nan")])
    def test_a_missing_or_zero_cap_is_a_configuration_error_not_permission(self, cap):
        """Falling back to the fifty-lot ceiling would be the worst default."""
        with pytest.raises(AlertError, match="no lot cap configured"):
            parse_alert({"action": "buy", "symbol": "XAUUSD", "lots": 0.01, "id": "1"}, cap)

    @pytest.mark.parametrize("field", ["stop_loss", "take_profit"])
    @pytest.mark.parametrize("value", [0, -1, "x", float("nan")])
    def test_bad_protection_prices_are_refused(self, field, value):
        with pytest.raises(AlertError, match=field):
            parse_alert({"action": "buy", "symbol": "XAUUSD", "lots": 0.01, field: value, "id": "1"}, 1.0)

    def test_empty_protection_fields_mean_none(self):
        """A template with an unfilled {{...}} slot sends "" -- not an error."""
        alert = parse_alert(
            {"action": "buy", "symbol": "XAUUSD", "lots": 0.01, "stop_loss": "", "take_profit": None, "id": "1"},
            1.0)
        assert alert.stop_loss is None and alert.take_profit is None

    def test_alert_id_is_bounded(self):
        alert = parse_alert({"action": "buy", "symbol": "XAUUSD", "lots": 0.01, "id": "x" * 500}, 1.0)
        assert len(alert.alert_id) == 128


class TestFindMasterPositions:
    STATE = {"master_positions": [
        {"position_id": 11, "symbol": "XAUUSD", "volume": 100},
        {"position_id": 12, "symbol": "XAUUSD", "volume": 200},   # scaled in
        {"position_id": 13, "symbol": "EURUSD", "volume": 100000},
    ]}

    def test_returns_every_position_on_the_symbol(self):
        found = find_master_positions(self.STATE, "XAUUSD")
        assert [p["position_id"] for p in found] == [11, 12]

    def test_case_insensitive(self):
        assert len(find_master_positions(self.STATE, "xauusd")) == 2

    def test_nothing_open_is_an_empty_list_not_an_error(self):
        """A strategy that fires close on every exit must not be told it is
        broken because the position already went."""
        assert find_master_positions(self.STATE, "GBPUSD") == []

    @pytest.mark.parametrize("state", [None, "x", {}, {"master_positions": None},
                                       {"master_positions": [None, "bad", {"symbol": "XAUUSD"}]}])
    def test_malformed_state_is_treated_as_empty(self, state):
        assert find_master_positions(state, "XAUUSD") == []


class TestHardening:
    """What the adversarial review demanded of the parser."""

    def test_id_is_required(self):
        """Without it, a re-entry after a stop is silently a 'duplicate'."""
        with pytest.raises(AlertError, match="id is required"):
            parse_alert({"action": "buy", "symbol": "XAUUSD", "lots": 0.01}, 1.0)

    @pytest.mark.parametrize("bad", ["{{timenow}}", "{{time}}", "x{{timenow}}y"])
    def test_an_unexpanded_placeholder_is_refused_with_the_cause(self, bad):
        """Placeholders inside strategy.entry(alert_message=...) never render.
        The operator must be told that, not have every signal deduplicated."""
        with pytest.raises(AlertError, match="did not expand"):
            parse_alert({"action": "buy", "symbol": "XAUUSD", "lots": 0.01, "id": bad}, 1.0)

    @pytest.mark.parametrize("field", ["lots", "stop_loss", "take_profit"])
    def test_booleans_are_not_numbers(self, field):
        """float(True) is 1.0 -- one lot, or a stop at price 1."""
        body = {"action": "buy", "symbol": "XAUUSD", "lots": 0.01, "id": "1", field: True}
        with pytest.raises(AlertError, match=field):
            parse_alert(body, 1.0)

    def test_an_absurdly_long_number_is_refused_before_parsing(self):
        """float('1'*4000) is legal Python and a slow way to say nothing."""
        with pytest.raises(AlertError, match="lots"):
            parse_alert({"action": "buy", "symbol": "XAUUSD", "lots": "1" * 4000, "id": "1"}, 1.0)
