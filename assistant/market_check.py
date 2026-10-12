"""
Cross-check holdings read from a screenshot against live market data.

The vision model only transcribes what is printed (name, ticker/ISIN if
shown, quantity, price, value, currency). Here each row is matched to a real
Yahoo listing: candidates come from searching the ticker, ISIN and name;
each candidate's live price is compared with the price (or value/quantity)
in the screenshot, preferring the listing in the screenshot's currency.

Each result says what was read, which listing was chosen and why, and
whether it is "verified" (live price within 5% of the screenshot), needs a
"check", or was "not_found". Everything is JSON-safe (Decimals as strings)
so it can be kept in the session until the user confirms.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from common.currency import normalize

TOLERANCE = Decimal("0.05")  # screenshots can be delayed; 5% still means "same thing"
LOOSE_TOLERANCE = Decimal("0.15")
MAX_CANDIDATES = 8

QUOTE_TYPE_TO_ASSET = {"EQUITY": "stock", "ETF": "etf", "MUTUALFUND": "etf", "CRYPTOCURRENCY": "crypto"}


def to_decimal(value) -> Decimal | None:
    """Model output is usually a number, but accept '1.234,56'-style strings too."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    from wallet.csv_import import parse_decimal

    try:
        return parse_decimal(str(value))
    except Exception:
        return None


def _s(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _rel_diff(a: Decimal, b: Decimal) -> Decimal:
    return abs(a - b) / b if b else Decimal("Infinity")


def _price(value: Decimal) -> Decimal:
    """Live prices arrive as floats (173.47999572753906); 4 decimals is plenty."""
    return value.quantize(Decimal("0.0001"))


def _pct(d: Decimal) -> str:
    return f"{(d * 100).quantize(Decimal('0.1'))}%"


class MarketChecker:
    def __init__(self, search_fn=None, quote_fn=None, fx_fn=None):
        from common import market_data

        self.search = search_fn or market_data.search_securities
        self.quote = quote_fn or market_data.get_quote
        self.fx = fx_fn or market_data.get_fx_rate

    def _convert(self, amount: Decimal, from_ccy: str, to_ccy: str | None) -> Decimal | None:
        if not to_ccy or from_ccy == to_ccy:
            return amount
        rate = self.fx(from_ccy, to_ccy)
        return amount * Decimal(str(rate)) if rate else None

    def _candidates(self, ticker, isin, name, currency=None) -> list[dict]:
        found: dict[str, dict] = {}

        def add(results):
            for r in results:
                found.setdefault(r["symbol"], r)

        if ticker:
            add([{"symbol": ticker.upper(), "name": "", "exchange": "", "quote_type": ""}])
            if currency:
                # Crypto is listed as pairs (BTC-EUR); a bare "BTC" is a US fund.
                add([{"symbol": f"{ticker.upper()}-{currency}", "name": "", "exchange": "",
                      "quote_type": "CRYPTOCURRENCY"}])
            add(self.search(ticker))
        if isin:
            add(self.search(isin))
        if name:
            add(self.search(name))
        # The same security trades on several exchanges/currencies; searching the
        # best match's full name finds its other listings (e.g. VWRA.L -> VWCE.DE).
        first_named = next((r for r in found.values() if r.get("name")), None)
        if first_named and first_named["name"].lower() != (name or "").lower():
            add(self.search(first_named["name"]))
        return list(found.values())[:MAX_CANDIDATES]

    def check_holding(self, row: dict) -> dict:
        name = (row.get("name") or "").strip()
        ticker = (row.get("ticker") or "").strip().upper() or None
        isin = (row.get("isin") or "").strip().upper() or None
        currency = (row.get("currency") or "").strip().upper() or None
        qty = to_decimal(row.get("quantity"))
        price = to_decimal(row.get("price"))
        value = to_decimal(row.get("market_value"))
        avg_cost = to_decimal(row.get("avg_cost"))

        result = {
            "read": {"name": name, "ticker": ticker, "isin": isin, "currency": currency,
                     "quantity": _s(qty), "price": _s(price), "market_value": _s(value), "avg_cost": _s(avg_cost)},
            "symbol": None, "symbol_name": None, "exchange": None, "asset_type": "stock",
            "live_price": None, "live_currency": None, "live_value": None,
            "quantity": _s(qty), "quantity_derived": False,
            "avg_cost": _s(avg_cost), "avg_cost_from_market": False,
            "candidates": [], "status": "not_found", "notes": [],
        }
        notes = result["notes"]

        # Price per unit according to the screenshot, if it can be known.
        ref_price = price or (value / qty if value is not None and qty else None)
        if price and qty and value and _rel_diff(qty * price, value) > TOLERANCE:
            notes.append(f"Quantity × price ({_s(qty * price)}) doesn't match the value shown ({_s(value)}); "
                         "check the quantity.")

        best, best_score, best_diff = None, None, None
        for rank, cand in enumerate(self._candidates(ticker, isin, name, currency)):
            quote = self.quote(cand["symbol"])
            if not quote or quote.price is None:
                continue
            live, live_ccy = normalize(Decimal(str(quote.price)), quote.currency)
            live = _price(live)
            cand = {**cand, "price": _s(live), "currency": live_ccy, "name": cand["name"] or quote.name or cand["symbol"]}
            result["candidates"].append(cand)

            score, diff = Decimal(1) - Decimal(rank) / 10, None
            if currency and live_ccy == currency:
                score += 3
            if ticker and (cand["symbol"] == ticker or cand["symbol"].startswith(ticker + ".")):
                score += 2
            if isin and cand["symbol"].startswith(isin):
                score -= 2  # "IE00...SG"-style listings are thinly traded mirrors
            if ref_price is not None:
                comparable = self._convert(live, live_ccy, currency)
                if comparable is not None:
                    diff = _rel_diff(comparable, ref_price)
                    if diff <= TOLERANCE:
                        score += 5 + (TOLERANCE - diff) * 40  # closer price wins between listings
                    else:
                        score += 1 if diff <= LOOSE_TOLERANCE else -3
            if best_score is None or score > best_score:
                best, best_score, best_diff = cand, score, diff

        if best is None:
            notes.append("No matching security found on the market. Enter the ticker yourself or untick the row.")
            return result

        live = Decimal(best["price"])
        result.update(symbol=best["symbol"], symbol_name=best["name"], exchange=best["exchange"],
                      asset_type=QUOTE_TYPE_TO_ASSET.get(best.get("quote_type"), "stock"),
                      live_price=best["price"], live_currency=best["currency"])

        if best_diff is not None and best_diff <= TOLERANCE:
            result["status"] = "verified"
        else:
            result["status"] = "check"
            if best_diff is None and value is not None:
                notes.append("Only the total value is shown (no price or quantity), so the match can't be confirmed.")
            elif best_diff is None:
                notes.append("The screenshot shows no price or value to compare with, so the match can't be confirmed.")
            else:
                notes.append(f"Live price differs from the screenshot by {_pct(best_diff)}: "
                             "maybe another listing, or a misread number.")

        # Quantity: derive it from the value if it wasn't readable.
        if qty is None and value is not None:
            live_in_row_ccy = self._convert(live, best["currency"], currency)
            if live_in_row_ccy:
                derived = value / live_in_row_ccy
                whole = derived.quantize(Decimal(1), rounding=ROUND_HALF_UP)
                qty = whole if whole and _rel_diff(derived, whole) <= Decimal("0.005") else derived.quantize(Decimal("0.000001"))
                result.update(quantity=_s(qty), quantity_derived=True)
                notes.append("Quantity wasn't visible; worked out from the value and today's price.")
        if qty is None:
            notes.append("Quantity wasn't visible; please fill it in.")
            result["status"] = "check"

        # Average cost, in the listing's currency (that's the holding's currency).
        if avg_cost is not None and currency and currency != best["currency"]:
            converted = self._convert(avg_cost, currency, best["currency"])
            converted = _price(converted) if converted is not None else None
            if converted is None:
                notes.append(f"Couldn't convert the average cost from {currency}; please check it.")
            avg_cost = converted
            result["avg_cost"] = _s(avg_cost)
        if avg_cost is None:
            result.update(avg_cost=best["price"], avg_cost_from_market=True)
            notes.append("Average cost isn't in the screenshot; using today's price (gain starts at zero). "
                         "Edit it if you know what you paid.")

        if qty is not None:
            result["live_value"] = _s((qty * live).quantize(Decimal("0.01")))
        return result

    def check_holdings(self, rows: list[dict]) -> list[dict]:
        return [self.check_holding(row) for row in rows]


def clean_cash_accounts(rows: list[dict]) -> list[dict]:
    cleaned = []
    for row in rows:
        balance = to_decimal(row.get("balance"))
        cleaned.append({
            "name": (row.get("name") or "").strip() or "Cash account",
            "currency": (row.get("currency") or "").strip().upper() or None,
            "balance": _s(balance),
        })
    return cleaned

