"""Currency display.

The warehouse stores Brazilian Real. Showing pounds means converting, and the
failure that matters is silent: swap the symbol without applying the rate and
every figure on the dashboard overstates by roughly 4.5x, while still looking
entirely plausible. These tests exist to make that failure loud.
"""

from __future__ import annotations

import pytest

from src.viz import money


@pytest.fixture
def gbp(monkeypatch):
    monkeypatch.setenv("DISPLAY_CURRENCY", "GBP")
    return money.active()


@pytest.fixture
def brl(monkeypatch):
    monkeypatch.setenv("DISPLAY_CURRENCY", "BRL")
    return money.active()


def test_pounds_are_converted_not_just_relabelled(gbp):
    assert gbp.convert(100) == pytest.approx(22.0)
    assert gbp.format(100) != "£100"


def test_the_rate_is_in_the_plausible_historic_range():
    """GBP/BRL sat around 4.2-4.9 across Sep 2016 - Oct 2018."""
    assert 1 / 4.9 < money.BRL_TO_GBP < 1 / 4.0


def test_source_currency_is_left_alone(brl):
    assert brl.convert(100) == 100
    assert brl.format(100) == "R$100"
    assert brl.is_converted is False


def test_converted_currencies_carry_a_note(gbp):
    assert gbp.is_converted
    assert "Brazilian Real" in gbp.note


def test_formatting(gbp):
    assert gbp.format(1000) == "£220"
    assert gbp.format(1000, decimals=2) == "£220.00"
    # Small values keep their pence; a £4.62 order value shown as "£5" is wrong.
    assert gbp.format(21) == "£4.62"


def test_compact_form(gbp):
    assert gbp.compact(15_735_527) == "£3.46M"
    assert gbp.compact(1_000_000) == "£220k"
    assert gbp.compact(500) == "£110"


def test_none_is_not_rendered_as_zero(gbp):
    """A missing value and a value of zero mean different things."""
    assert gbp.convert(None) is None
    assert gbp.format(None) == "n/a"
    assert gbp.compact(None) == "n/a"


# --- prose rewriting -------------------------------------------------------
# The model is told to write R$ and never to convert, because models are
# unreliable at arithmetic. The conversion happens here, in Python.

def test_amounts_in_prose_are_converted(gbp):
    out = money.convert_brl_text("R$6,856,150 came from credit card orders.")
    assert out == "£1,508,353 came from credit card orders."


def test_prose_conversion_handles_several_amounts(gbp):
    out = money.convert_brl_text("R$1,000 in April and R$2,000 in May.")
    assert "£220" in out and "£440" in out
    assert "R$" not in out


def test_non_monetary_numbers_are_untouched(gbp):
    """Review scores, day counts and percentages are not money."""
    text = "Average review 4.07 out of 5 over 12.5 days, up 3.1%."
    assert money.convert_brl_text(text) == text


def test_prose_is_untouched_when_showing_the_source_currency(brl):
    text = "R$6,856,150 came from credit card orders."
    assert money.convert_brl_text(text) == text


def test_an_unknown_currency_code_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("DISPLAY_CURRENCY", "XYZ")
    assert money.active().code == "GBP"
