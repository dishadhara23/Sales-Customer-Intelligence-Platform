"""Currency display, in one place.

The source data is Brazilian Real: Olist is a Brazilian marketplace and every
value in the warehouse is BRL. Showing pounds therefore means **converting**,
not relabelling, and the distinction matters. Swapping the symbol without
applying a rate would overstate every figure by roughly 4.5x — and anyone who
knows the dataset would spot it immediately.

So: the warehouse always stores BRL, conversion happens only at display time,
and converted figures are always labelled as approximate.

The rate is a fixed constant rather than a live lookup, deliberately:

* the data is historical (Sep 2016 – Oct 2018), so today's rate is the wrong
  one anyway;
* a live rate would make every rebuild produce different numbers, breaking
  reproducibility and the test suite;
* a documented constant can be checked by a reader, and changed in one place.

Set ``DISPLAY_CURRENCY`` in ``.env`` to ``BRL`` to switch conversion off.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Approximate average over the dataset's window (Sep 2016 – Oct 2018). GBP/BRL
# ranged roughly 4.2–4.9 across that period, so 1 BRL ≈ £0.22. Rounded on
# purpose: a figure like 0.2183 would imply a precision this does not have.
BRL_TO_GBP = 0.22
BRL_TO_USD = 0.27
BRL_TO_EUR = 0.24


@dataclass(frozen=True)
class Currency:
    code: str
    symbol: str
    rate_from_brl: float
    note: str

    @property
    def is_converted(self) -> bool:
        return self.code != "BRL"

    def convert(self, brl: float | int | None) -> float | None:
        if brl is None:
            return None
        return float(brl) * self.rate_from_brl

    def format(self, brl: float | int | None, decimals: int | None = None) -> str:
        """Format a BRL amount in the display currency."""
        value = self.convert(brl)
        if value is None:
            return "n/a"
        if decimals is None:
            decimals = 0 if abs(value) >= 100 else 2
        return f"{self.symbol}{value:,.{decimals}f}"

    def compact(self, brl: float | int | None) -> str:
        """Short form for tiles and axes: £1.2M, £340k."""
        value = self.convert(brl)
        if value is None:
            return "n/a"
        for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
            if abs(value) >= threshold:
                return f"{self.symbol}{value / threshold:,.2f}{suffix}".replace(".00", "")
        return f"{self.symbol}{value:,.0f}"


CURRENCIES = {
    "GBP": Currency("GBP", "£", BRL_TO_GBP,
                    f"converted from Brazilian Real at £{BRL_TO_GBP:.2f} per R$1 "
                    f"(approximate 2016–18 average)"),
    "USD": Currency("USD", "$", BRL_TO_USD,
                    f"converted from Brazilian Real at ${BRL_TO_USD:.2f} per R$1 "
                    f"(approximate 2016–18 average)"),
    "EUR": Currency("EUR", "€", BRL_TO_EUR,
                    f"converted from Brazilian Real at €{BRL_TO_EUR:.2f} per R$1 "
                    f"(approximate 2016–18 average)"),
    "BRL": Currency("BRL", "R$", 1.0, "source currency, unconverted"),
}


def active() -> Currency:
    code = os.getenv("DISPLAY_CURRENCY", "GBP").strip().upper()
    return CURRENCIES.get(code, CURRENCIES["GBP"])


_BRL_IN_TEXT = __import__("re").compile(r"R\$\s?([\d,]+(?:\.\d+)?)")


def convert_brl_text(text: str) -> str:
    """Rewrite ``R$1,234`` amounts inside prose into the display currency.

    The model is told to write amounts in R$ and is never asked to convert.
    Language models are unreliable at arithmetic, and a silently mis-converted
    figure is the worst possible failure here — so the conversion is done in
    Python, deterministically, after the text comes back.
    """
    cur = active()
    if not cur.is_converted:
        return text

    def swap(match):
        try:
            brl = float(match.group(1).replace(",", ""))
        except ValueError:
            return match.group(0)
        return cur.format(brl)

    return _BRL_IN_TEXT.sub(swap, text)


# Convenience wrappers so call sites stay short.
def fmt(brl, decimals: int | None = None) -> str:
    return active().format(brl, decimals)


def compact(brl) -> str:
    return active().compact(brl)


def symbol() -> str:
    return active().symbol


def note() -> str:
    return active().note
