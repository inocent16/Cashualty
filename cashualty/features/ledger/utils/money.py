from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from cashualty.features.ledger.models import Currency

_SYMBOLS = {Currency.EUR: "€", Currency.USD: "$"}


def to_minor(amount: float) -> int:
    """Convert a user-entered decimal amount (e.g. 19.99) to minor units (1999).
    Goes through Decimal(str(...)) to avoid binary-float rounding artifacts on
    ordinary two-decimal currency values."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_minor(amount_minor: int, currency: Currency) -> str:
    sign = "-" if amount_minor < 0 else ""
    major = abs(amount_minor) / 100
    symbol = _SYMBOLS.get(currency, "")
    return f"{sign}{symbol}{major:,.2f}"
