from bisect import bisect_left
from datetime import date

from django.db.models import Min
from django.shortcuts import get_object_or_404, render

from wallet.models import Holding, Transaction

from .services import DEFAULT_RANGE, RANGES, holding_history, portfolio_history, range_start


def _num(value):
    return float(value) if value is not None else None


def _snap(days, day):
    return days[min(bisect_left(days, day), len(days) - 1)]


def _selected_range(request, earliest):
    key = request.GET.get("range", DEFAULT_RANGE)
    if key not in RANGES:
        key = DEFAULT_RANGE
    today = date.today()
    return key, range_start(key, today, earliest), today


def _range_links(selected):
    return [{"key": k, "label": label, "active": k == selected} for k, (label, _) in RANGES.items()]


def portfolio(request):
    earliest = Transaction.objects.filter(user=request.user).aggregate(d=Min("date"))["d"]
    key, start, end = _selected_range(request, earliest)
    history = portfolio_history(request.user, start, end)
    chart = {
        "labels": [d.isoformat() for d in history.days],
        "net_worth": [_num(v) for v in history.net_worth],
        "holdings_value": [_num(v) for v in history.holdings_value],
        "invested": [_num(v) for v in history.invested],
        "currency": history.base_currency,
    }
    return render(request, "performance/portfolio.html", {
        "history": history, "chart": chart, "ranges": _range_links(key), "range_key": key,
        "has_data": any(history.net_worth) or any(history.invested),
    })


def holding_detail(request, pk):
    holding = get_object_or_404(Holding, pk=pk, user=request.user)
    earliest = holding.transactions.aggregate(d=Min("date"))["d"]
    key, start, end = _selected_range(request, earliest)
    history = holding_history(holding, start, end)
    chart = {
        "labels": [d.isoformat() for d in history.days],
        "price": [_num(v) for v in history.price],
        "avg_cost": [_num(v) for v in history.avg_cost] if history.avg_cost_comparable else [],
        # Markers sit on the first plotted day on/after the trade (ranges over
        # ~a year are sampled weekly, so not every date is on the axis).
        "trades": [
            {"x": _snap(history.days, t.date).isoformat(), "y": _num(t.price), "type": t.tx_type,
             "quantity": _num(t.quantity)}
            for t in history.trades
        ],
        "currency": history.price_currency,
    }
    return render(request, "performance/holding.html", {
        "history": history, "holding": holding, "chart": chart, "ranges": _range_links(key), "range_key": key,
        "has_prices": any(v is not None for v in history.price),
    })
