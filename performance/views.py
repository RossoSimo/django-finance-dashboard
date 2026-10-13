from django.db.models import Min
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render

from wallet.models import Holding, Transaction

from .payloads import holding_payload, portfolio_payload
from .services import DEFAULT_RANGE, RANGES, history_for_range


def resolve_range(request, default):
    key = request.GET.get("range", default)
    return key if key in RANGES else default


def _range_links(selected):
    return [{"key": k, "label": label, "active": k == selected} for k, (label, _) in RANGES.items()]


def _portfolio_history(user, key):
    earliest = Transaction.objects.filter(user=user).aggregate(d=Min("date"))["d"]
    return history_for_range(user, key, earliest=earliest)


def _holding_history(user, holding, key):
    earliest = holding.transactions.aggregate(d=Min("date"))["d"]
    return history_for_range(user, key, holding=holding, earliest=earliest)


def portfolio(request):
    key = resolve_range(request, DEFAULT_RANGE)
    history = _portfolio_history(request.user, key)
    return render(request, "performance/portfolio.html", {
        "history": history, "chart": portfolio_payload(history, key),
        "ranges": _range_links(key), "range_key": key,
        "has_data": any(history.net_worth) or any(history.invested),
    })


def holding_detail(request, pk):
    holding = get_object_or_404(Holding, pk=pk, user=request.user)
    key = resolve_range(request, DEFAULT_RANGE)
    history = _holding_history(request.user, holding, key)
    return render(request, "performance/holding.html", {
        "history": history, "holding": holding, "chart": holding_payload(history, key),
        "ranges": _range_links(key), "range_key": key,
        "has_prices": any(v is not None for v in history.price),
    })


def chart_data(request):
    key = resolve_range(request, DEFAULT_RANGE)
    holding_pk = request.GET.get("holding")
    if holding_pk is None:
        return JsonResponse(portfolio_payload(_portfolio_history(request.user, key), key))
    if not holding_pk.isdigit():
        raise Http404
    holding = get_object_or_404(Holding, pk=int(holding_pk), user=request.user)
    return JsonResponse(holding_payload(_holding_history(request.user, holding, key), key))
