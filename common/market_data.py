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
