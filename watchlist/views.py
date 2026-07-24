from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from common.market_data import get_quotes

from .forms import WatchlistItemForm
from .models import WatchlistItem


def watchlist_home(request):
    items = list(WatchlistItem.objects.all())
    quotes = get_quotes([i.ticker for i in items])
    rows = []
    for item in items:
        quote = quotes.get(item.ticker.upper())
        hit_target = None
        if item.target_price and quote and quote.price is not None:
            hit_target = quote.price <= float(item.target_price)
        rows.append({"item": item, "quote": quote, "hit_target": hit_target})
    return render(request, "watchlist/watchlist_home.html", {"rows": rows})


def add_watchlist_item(request):
    if request.method == "POST":
        form = WatchlistItemForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Added to watchlist.")
            return redirect("watchlist:home")
    else:
        form = WatchlistItemForm()
    return render(request, "watchlist/form.html", {"form": form, "title": "Add to watchlist"})


def remove_watchlist_item(request, pk):
    item = get_object_or_404(WatchlistItem, pk=pk)
    if request.method == "POST":
        item.delete()
        messages.success(request, f"Removed {item.ticker} from watchlist.")
        return redirect("watchlist:home")
    return render(request, "watchlist/confirm_delete.html", {"object": item})
