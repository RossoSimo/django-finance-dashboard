"""
Thin wrapper around yfinance so the rest of the app never talks to the
network/API directly. Everything is cached briefly with Django's cache
framework to keep pages fast and avoid rate limits.

If yfinance/network isn't available, functions fail soft and return
None / empty results rather than crashing a page.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None


@dataclass
class Quote:
    ticker: str
    name: str | None
    price: float | None
    previous_close: float | None
    currency: str | None
    change: float | None
    change_pct: float | None


def _empty_quote(ticker: str) -> Quote:
    return Quote(ticker=ticker, name=None, price=None, previous_close=None,
                 currency=None, change=None, change_pct=None)


def get_quote(ticker: str) -> Quote:
    """Return a lightweight live quote for a single ticker, cached briefly."""
    if not ticker:
        return _empty_quote(ticker)

    cache_key = f"quote:{ticker.upper()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    if yf is None:
        return _empty_quote(ticker)

    try:
        t = yf.Ticker(ticker)
        info = t.fast_info  # lightweight, fast endpoint
        price = getattr(info, "last_price", None)
        prev_close = getattr(info, "previous_close", None)
        currency = getattr(info, "currency", None)

        name = ticker
        try:
            # get_info() is heavier; only used to fetch a friendly name
            full_info = t.info
            name = full_info.get("shortName") or full_info.get("longName") or ticker
        except Exception:
            pass

        change = None
        change_pct = None
        if price is not None and prev_close:
            change = price - prev_close
            change_pct = (change / prev_close) * 100 if prev_close else None

        quote = Quote(
            ticker=ticker.upper(),
            name=name,
            price=price,
            previous_close=prev_close,
            currency=currency,
            change=change,
            change_pct=change_pct,
        )
    except Exception:
        logger.exception("Failed to fetch quote for %s", ticker)
        quote = _empty_quote(ticker)

    cache.set(cache_key, quote, settings.MARKET_DATA_CACHE_SECONDS)
    return quote


def get_quotes(tickers: list[str]) -> dict[str, Quote]:
    """Batch helper: returns {ticker: Quote}."""
    return {t.upper(): get_quote(t) for t in tickers if t}


@dataclass
class NewsItem:
    ticker: str | None
    title: str
    publisher: str | None
    link: str
    published: datetime | None


def get_news_for_ticker(ticker: str, limit: int = 8) -> list[NewsItem]:
    """Return recent news items for a ticker, cached briefly."""
    cache_key = f"news:{ticker.upper()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached[:limit]

    if yf is None:
        return []

    items: list[NewsItem] = []
    try:
        t = yf.Ticker(ticker)
        raw_news = t.news or []
        for entry in raw_news:
            # yfinance news items can be nested under "content" in newer versions
            content = entry.get("content", entry)
            title = content.get("title") or entry.get("title")
            link = (
                content.get("canonicalUrl", {}).get("url")
                if isinstance(content.get("canonicalUrl"), dict)
                else content.get("link") or entry.get("link")
            )
            publisher = None
            if isinstance(content.get("provider"), dict):
                publisher = content["provider"].get("displayName")
            publisher = publisher or entry.get("publisher")

            pub_date = None
            pub_raw = content.get("pubDate") or entry.get("providerPublishTime")
            if isinstance(pub_raw, (int, float)):
                try:
                    pub_date = datetime.fromtimestamp(pub_raw)
                except Exception:
                    pub_date = None
            elif isinstance(pub_raw, str):
                try:
                    pub_date = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
                except Exception:
                    pub_date = None

            if title and link:
                items.append(NewsItem(
                    ticker=ticker.upper(),
                    title=title,
                    publisher=publisher,
                    link=link,
                    published=pub_date,
                ))
    except Exception:
        logger.exception("Failed to fetch news for %s", ticker)

    cache.set(cache_key, items, settings.NEWS_CACHE_SECONDS)
    return items[:limit]


def get_news_for_tickers(tickers: list[str], limit_per_ticker: int = 5) -> list[NewsItem]:
    """Aggregate + dedupe news across multiple tickers, newest first."""
    seen_links = set()
    all_items: list[NewsItem] = []
    for ticker in tickers:
        for item in get_news_for_ticker(ticker, limit=limit_per_ticker):
            if item.link not in seen_links:
                seen_links.add(item.link)
                all_items.append(item)

    all_items.sort(key=lambda i: i.published or datetime.min, reverse=True)
    return all_items


def get_fx_rate(from_currency: str, to_currency: str) -> float | None:
    """Units of `to_currency` per 1 `from_currency`, from Yahoo's
    "<FROM><TO>=X" pairs, cached. Returns None if it can't be fetched."""
    from_currency, to_currency = (from_currency or "").upper(), (to_currency or "").upper()
    if not from_currency or not to_currency:
        return None
    if from_currency == to_currency:
        return 1.0

    cache_key = f"fx:{from_currency}{to_currency}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    rate = None
    if yf is not None:
        try:
            rate = getattr(yf.Ticker(f"{from_currency}{to_currency}=X").fast_info, "last_price", None)
        except Exception:
            logger.exception("Failed to fetch FX rate %s->%s", from_currency, to_currency)
    if rate is not None and rate > 0:
        cache.set(cache_key, float(rate), settings.FX_CACHE_SECONDS)
        return float(rate)
    return None


def get_price_history(symbol: str, start) -> list[tuple]:
    """Daily closes since `start` (a date) as [(date, float), ...], oldest
    first. Works for tickers and FX pairs ("USDEUR=X"). Prices are in the
    instrument's quote currency (split-adjusted, not dividend-adjusted).
    Cached; returns [] if the data can't be fetched."""
    symbol = (symbol or "").upper()
    if not symbol:
        return []
    cache_key = f"history:{symbol}:{start.isoformat()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    if yf is None:
        return []

    series: list[tuple] = []
    try:
        frame = yf.Ticker(symbol).history(start=start.isoformat(), interval="1d", auto_adjust=False)
        for stamp, close in frame["Close"].items():
            if close == close and close is not None:  # skip NaN
                series.append((stamp.date(), float(close)))
    except Exception:
        logger.exception("Failed to fetch price history for %s", symbol)
        return []  # don't cache failures; retry on the next request

    cache.set(cache_key, series, settings.PRICE_HISTORY_CACHE_SECONDS)
    return series
