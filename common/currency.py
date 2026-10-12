"""Currency normalisation and conversion into a user's base currency."""

from __future__ import annotations

from decimal import Decimal

# Yahoo quotes some exchanges in minor units: London in pence (GBp/GBX),
# Johannesburg in cents (ZAc), Tel Aviv in agorot (ILA).
MINOR_UNITS = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}


def normalize(amount: Decimal | None, currency: str | None) -> tuple[Decimal | None, str | None]:
    """Turn a minor-unit amount (e.g. 1234 GBp) into its major unit (12.34 GBP)."""
    if currency in MINOR_UNITS:
        major, divisor = MINOR_UNITS[currency]
        return (amount / divisor if amount is not None else None), major
    return amount, (currency.upper() if currency else None)


class CurrencyConverter:
    """Converts amounts into `base`, fetching each rate once.

    Currencies whose rate can't be fetched are recorded in `missing` and
    convert() returns None for them, so callers can leave those amounts out
    of totals (and say so) instead of adding them up as if 1:1.
    """

    def __init__(self, base: str, rate_fn=None):
        if rate_fn is None:
            from common.market_data import get_fx_rate as rate_fn
        self.base = base.upper()
        self.missing: set[str] = set()
        self._rate_fn = rate_fn
        self._rates: dict[str, Decimal | None] = {}

    def rate(self, currency: str | None) -> Decimal | None:
        currency = (currency or self.base).upper()
        if currency == self.base:
            return Decimal("1")
        if currency not in self._rates:
            raw = self._rate_fn(currency, self.base)
            self._rates[currency] = Decimal(str(raw)) if raw else None
            if self._rates[currency] is None:
                self.missing.add(currency)
        return self._rates[currency]

    def convert(self, amount: Decimal | None, currency: str | None) -> Decimal | None:
        if amount is None:
            return None
        amount, currency = normalize(Decimal(amount), currency)
        rate = self.rate(currency)
        return amount * rate if rate is not None else None
