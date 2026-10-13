"""Italian-style number formatting (1.234,56) as template filters.

Registered as a template builtin in settings, so templates need no {% load %}.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from django import template

from common.currency import normalize

register = template.Library()

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF", "JPY": "¥"}
MINUS = "−"
DASH = "—"
MAX_PLACES = 8


def _to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    return Decimal(value)


def format_number(value, decimals: int | None = 2, signed: bool = False) -> str:
    """Format as 1.234,56. decimals=None trims trailing zeros (max 8 places)."""
    if value is None:
        return DASH
    places = MAX_PLACES if decimals is None else decimals
    rounded = _to_decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    text = f"{abs(rounded):,.{places}f}"
    if decimals is None and "." in text:
        text = text.rstrip("0").rstrip(".")
    text = text.replace(",", "\0").replace(".", ",").replace("\0", ".")
    if rounded < 0:
        return MINUS + text
    if signed and rounded > 0:
        return "+" + text
    return text


@register.filter
def currency_symbol(code) -> str:
    code = (code or "").upper()
    return CURRENCY_SYMBOLS.get(code, code)


def _money(value, currency, signed: bool) -> str:
    if value is None:
        return DASH
    amount, major = normalize(_to_decimal(value), currency)
    text = format_number(amount, 2, signed)
    symbol = currency_symbol(major)
    return f"{text}\u00a0{symbol}" if symbol else text


@register.filter
def money(value, currency) -> str:
    return _money(value, currency, False)


@register.filter
def signed_money(value, currency) -> str:
    return _money(value, currency, True)


@register.filter
def num(value, decimals=None) -> str:
    return format_number(value, None if decimals in (None, "") else int(decimals))


@register.filter
def pct(value) -> str:
    if value is None:
        return DASH
    return format_number(value, 2, signed=True) + "\u00a0%"
