from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from alerts.services import check_alerts
from common.market_data import get_quotes

from .forms import WatchlistItemForm
from .models import WatchlistItem


def watchlist_home(request):
    check_alerts(request.user)
    items = list(WatchlistItem.objects.filter(user=request.user))
    quotes = get_quotes([i.ticker for i in items])
    rows = []
    for item in items:
        quote = quotes.get(item.ticker.upper())
        rows.append({"item": item, "quote": quote})
    return render(request, "watchlist/watchlist_home.html", {"rows": rows})


def add_watchlist_item(request):
    if request.method == "POST":
        form = WatchlistItemForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Added to watchlist.")
            return redirect("watchlist:home")
    else:
        form = WatchlistItemForm(user=request.user)
    return render(request, "watchlist/form.html", {"form": form, "title": "Add to watchlist"})


def remove_watchlist_item(request, pk):
    item = get_object_or_404(WatchlistItem, pk=pk, user=request.user)
    if request.method == "POST":
        item.delete()
        messages.success(request, f"Removed {item.ticker} from watchlist.")
        return redirect("watchlist:home")
    return render(request, "watchlist/confirm_delete.html", {"object": item})
