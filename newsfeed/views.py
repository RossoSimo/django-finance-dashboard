from django.shortcuts import render

from common.market_data import get_news_for_ticker, get_news_for_tickers
from wallet.models import Holding
from watchlist.models import WatchlistItem


def news_home(request):
    ticker_filter = request.GET.get("ticker", "").strip().upper()

    if ticker_filter:
        items = get_news_for_ticker(ticker_filter, limit=25)
        tickers = [ticker_filter]
    else:
        holding_tickers = list(Holding.objects.values_list("ticker", flat=True))
        watch_tickers = list(WatchlistItem.objects.values_list("ticker", flat=True))
        tickers = sorted(set(holding_tickers) | set(watch_tickers))
        items = get_news_for_tickers(tickers, limit_per_ticker=5)

    return render(request, "newsfeed/news_home.html", {
        "items": items,
        "tickers": tickers,
        "active_ticker": ticker_filter,
    })
