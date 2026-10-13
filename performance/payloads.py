"""Chart payloads: the dict the pages embed and the JSON endpoint returns.

Stats are pre-formatted with the `fmt` filters so JavaScript never formats
money itself. Series values are floats (chart-only), never used for totals.
"""

from bisect import bisect_left
from datetime import datetime

from django.utils import timezone

from common.templatetags.fmt import money, pct, signed_money

from .services import DEFAULT_RANGE


def _num(value):
    return float(value) if value is not None else None


def _direction(value):
    """up/down/flat from the sign after rounding to cents (so -0.004 is flat)."""
    if value is None:
        return "flat"
    rounded = round(value, 2)
    return "up" if rounded > 0 else "down" if rounded < 0 else "flat"


def _envelope(history, range_key, currency):
    return {
        "range": range_key or DEFAULT_RANGE,
        "intraday": history.intraday,
        "session_date": history.session_date.isoformat() if history.session_date else None,
        "currency": currency,
        "labels": [d.isoformat() for d in history.days],
    }


def _snap(history, day):
    """The plotted point a trade dated `day` sits on: the first one on/after it
    (by local date for intraday points), else the last."""
    points = history.days
    if history.intraday:
        local = [timezone.localtime(p).date() for p in points]
        index = bisect_left(local, day)
    else:
        index = bisect_left(points, day)
    return points[min(index, len(points) - 1)]


def portfolio_payload(history, range_key=None):
    unrealized = history.unrealized_gain
    last = lambda values: values[-1] if values else None  # noqa: E731
    payload = _envelope(history, range_key, history.base_currency)
    payload["series"] = {
        "net_worth": [_num(v) for v in history.net_worth],
        "holdings_value": [_num(v) for v in history.holdings_value],
        "invested": [_num(v) for v in history.invested],
    }
    payload["stats"] = {
        "net_worth": money(last(history.net_worth), history.base_currency),
        "change": signed_money(history.change, history.base_currency),
        "change_pct": pct(history.change_pct),
        "direction": _direction(history.change),
        "holdings_value": money(last(history.holdings_value), history.base_currency),
        "invested": money(last(history.invested), history.base_currency),
        "unrealized_gain": signed_money(unrealized, history.base_currency),
        "unrealized_direction": _direction(unrealized),
    }
    return payload


def holding_payload(history, range_key=None):
    payload = _envelope(history, range_key, history.price_currency)
    known = [p for p in history.price if p is not None]
    change_pct = (known[-1] / known[0] - 1) * 100 if known and known[0] else None
    payload["series"] = {
        "price": [_num(v) for v in history.price],
        "avg_cost": [_num(v) for v in history.avg_cost] if history.avg_cost_comparable else [],
    }
    payload["trades"] = [
        {"x": _snap(history, t.date).isoformat(), "y": _num(t.price), "type": t.tx_type,
         "quantity": _num(t.quantity)}
        for t in history.trades
    ] if history.days else []
    payload["stats"] = {
        "last_price": money(known[-1] if known else None, history.price_currency),
        "change_pct": pct(change_pct),
        "direction": _direction(change_pct),
    }
    return payload
