import json
from decimal import Decimal

from django.shortcuts import render

from common.market_data import get_news_for_tickers, get_quotes
from wallet.models import Holding, NetWorthSnapshot
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


def _decimal_default(o):
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError


def home(request):
    summary = net_worth_summary()
    record_snapshot(summary)

    watchlist_items = list(WatchlistItem.objects.all()[:8])
    watch_quotes = get_quotes([i.ticker for i in watchlist_items])
    watch_rows = [{"item": i, "quote": watch_quotes.get(i.ticker.upper())} for i in watchlist_items]

    holding_tickers = list(Holding.objects.values_list("ticker", flat=True))
    watch_tickers = list(WatchlistItem.objects.values_list("ticker", flat=True))
    news_items = get_news_for_tickers(list(set(holding_tickers) | set(watch_tickers)), limit_per_ticker=3)[:8]

    # --- Chart data ---
    snapshots = list(NetWorthSnapshot.objects.order_by("date")[:365])
    trend_labels = [s.date.strftime("%b %d") for s in snapshots]
    trend_values = [float(s.net_worth) for s in snapshots]

    allocation = summary["allocation"]
    allocation_labels = [ASSET_TYPE_LABELS.get(k, k.title()) for k in allocation.keys()]
    allocation_values = [float(v) for v in allocation.values()]
    allocation_colors = [ASSET_TYPE_COLORS.get(k, "#cbd5e1") for k in allocation.keys()]

    # Top/bottom movers among watchlist + holdings, by % change, for a small bar chart
    movers = []
    for row in watch_rows:
        if row["quote"] and row["quote"].change_pct is not None:
            movers.append((row["item"].ticker, row["quote"].change_pct))
    for row in summary["rows"]:
        if row["quote"] and row["quote"].change_pct is not None:
            movers.append((row["holding"].ticker, row["quote"].change_pct))
    seen = set()
    unique_movers = []
    for ticker, pct in movers:
        if ticker not in seen:
            seen.add(ticker)
            unique_movers.append((ticker, pct))
    unique_movers.sort(key=lambda x: x[1], reverse=True)
    top_movers = unique_movers[:5]
    bottom_movers = sorted(unique_movers, key=lambda x: x[1])[:5]

    dedup_labels, dedup_values, seen2 = [], [], set()
    for lbl, val in top_movers + bottom_movers[::-1]:
        if lbl not in seen2:
            seen2.add(lbl)
            dedup_labels.append(lbl)
            dedup_values.append(val)

    chart_data = {
        "trend_labels": trend_labels,
        "trend_values": trend_values,
        "allocation_labels": allocation_labels,
        "allocation_values": allocation_values,
        "allocation_colors": allocation_colors,
        "mover_labels": dedup_labels,
        "mover_values": dedup_values,
    }

    return render(request, "dashboard/home.html", {
        "summary": summary,
        "watch_rows": watch_rows,
        "news_items": news_items,
        "chart_data_json": json.dumps(chart_data, default=_decimal_default),
        "has_snapshots": len(snapshots) > 1,
        "has_allocation": len(allocation) > 0,
        "has_movers": len(dedup_labels) > 0,
    })
