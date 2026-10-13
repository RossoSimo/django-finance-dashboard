from django.db.models import Min
from django.shortcuts import render
from django.utils import timezone

from alerts.services import check_alerts
from common.market_data import get_news_for_tickers, get_quotes
from performance.payloads import portfolio_payload
from performance.services import DASHBOARD_DEFAULT_RANGE, history_for_range
from performance.views import _range_links, resolve_range
from wallet.models import Holding, Transaction
from wallet.services import net_worth_summary, record_snapshot
from watchlist.models import WatchlistItem

ASSET_TYPE_LABELS = {
    "stock": "Stocks",
    "etf": "ETFs",
    "bond": "Bonds",
    "crypto": "Crypto",
    "other": "Other",
    "cash": "Cash",
}

ASSET_TYPE_COLORS = {
    "stock": "#4f8ef7",
    "etf": "#8b5cf6",
    "bond": "#f59e0b",
    "crypto": "#f472b6",
    "other": "#94a3b8",
    "cash": "#22c55e",
}


def _movers(watch_rows, holding_rows):
    """Top 5 and bottom 5 by day change among watchlist + holdings, best first."""
    seen, unique = set(), []
    for ticker, quote in [(r["item"].ticker, r["quote"]) for r in watch_rows] +                          [(r["holding"].ticker, r["quote"]) for r in holding_rows]:
        if quote and quote.change_pct is not None and ticker not in seen:
            seen.add(ticker)
            unique.append({"ticker": ticker, "change_pct": quote.change_pct})
    unique.sort(key=lambda m: m["change_pct"], reverse=True)
    picked = unique[:5] + [m for m in unique[-5:] if m not in unique[:5]]
    return picked


def _greeting(now):
    hour = timezone.localtime(now).hour
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"


def home(request):
    check_alerts(request.user)
    summary = net_worth_summary(request.user)
    record_snapshot(request.user, summary)

    range_key = resolve_range(request, DASHBOARD_DEFAULT_RANGE)
    earliest = Transaction.objects.filter(user=request.user).aggregate(d=Min("date"))["d"]
    history = history_for_range(request.user, range_key, earliest=earliest)

    watchlist_items = list(WatchlistItem.objects.filter(user=request.user)[:8])
    watch_quotes = get_quotes([i.ticker for i in watchlist_items])
    watch_rows = [{"item": i, "quote": watch_quotes.get(i.ticker.upper())} for i in watchlist_items]

    holding_tickers = list(Holding.objects.filter(user=request.user).values_list("ticker", flat=True))
    watch_tickers = list(WatchlistItem.objects.filter(user=request.user).values_list("ticker", flat=True))
    news_items = get_news_for_tickers(list(set(holding_tickers) | set(watch_tickers)), limit_per_ticker=3)[:8]

    total = sum(summary["allocation"].values()) or 1
    allocation_rows = [
        {"label": ASSET_TYPE_LABELS.get(k, k.title()), "value": float(v), "share": float(v / total * 100),
         "color": ASSET_TYPE_COLORS.get(k, "#cbd5e1")}
        for k, v in summary["allocation"].items()
    ]
    allocation = {
        "labels": [a["label"] for a in allocation_rows],
        "values": [a["value"] for a in allocation_rows],
        "colors": [a["color"] for a in allocation_rows],
    }
    movers = _movers(watch_rows, summary["rows"])

    return render(request, "dashboard/home.html", {
        "summary": summary,
        "today": timezone.localdate(),
        "greeting": _greeting(timezone.now()),
        "history": history,
        "chart": portfolio_payload(history, range_key),
        "has_chart": any(history.net_worth),
        "range_key": range_key,
        "ranges": _range_links(range_key),
        "watch_rows": watch_rows,
        "news_items": news_items,
        "allocation": allocation,
        "allocation_rows": allocation_rows,
        "movers": movers,
    })
