"""A broker that refuses a symbol request must say so in its own words."""
import pytest
from copier.ctrader.symbols import _symbols_or_raise, SymbolFetchRefused


class FakeSymbolsRes:
    """What a healthy account answers with."""
    symbol = ["EURUSD", "XAUUSD"]


class FakeErrorRes:
    """What a disabled or unauthorised account answers with: no `symbol`."""
    errorCode = "ACCOUNT_DISABLED"
    description = "account is disabled"


def test_a_healthy_response_passes_its_symbols_through():
    assert _symbols_or_raise(FakeSymbolsRes(), 123, "symbol list") == ["EURUSD", "XAUUSD"]


def test_a_refusal_names_the_broker_code_and_the_account():
    # The operator used to get "'ProtoOAErrorRes' object has no attribute
    # 'symbol'", which names a Python type and nothing about the account or
    # the reason. It ran 33 times in three days and explained nothing.
    with pytest.raises(SymbolFetchRefused) as err:
        _symbols_or_raise(FakeErrorRes(), 48375975, "symbol list")

    message = str(err.value)
    assert "ACCOUNT_DISABLED" in message
    assert "48375975" in message
    assert "symbol list" in message
    assert "AttributeError" not in message


def test_a_refusal_without_detail_still_says_something_useful():
    class Bare:
        pass

    with pytest.raises(SymbolFetchRefused) as err:
        _symbols_or_raise(Bare(), 55, "symbol details")
    # Falls back to the message type rather than an empty accusation.
    assert "Bare" in str(err.value) and "55" in str(err.value)
