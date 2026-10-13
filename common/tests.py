from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from common import market_data


class NewsTests(TestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("common.market_data.yf")
    def test_falls_back_to_search_when_ticker_news_is_empty(self, yf):
        yf.Ticker.return_value.news = []
        yf.Search.return_value.news = [
            {"title": "Apple up", "publisher": "IBD", "link": "https://x/1", "providerPublishTime": 1791909732},
        ]
        items = market_data.get_news_for_ticker("aapl")
        self.assertEqual([i.title for i in items], ["Apple up"])
        self.assertEqual(items[0].ticker, "AAPL")
        self.assertIsNotNone(items[0].published.tzinfo)

    @mock.patch("common.market_data.yf")
    def test_mixed_date_formats_sort_newest_first(self, yf):
        nested = {"content": {"title": "Nested", "canonicalUrl": {"url": "https://x/2"},
                              "pubDate": "2026-10-13T10:00:00Z"}}
        flat = {"title": "Flat", "link": "https://x/1", "providerPublishTime": 1700000000}
        undated = {"title": "Undated", "link": "https://x/3"}
        yf.Ticker.return_value.news = [flat, nested, undated]
        items = market_data.get_news_for_tickers(["AAPL"], limit_per_ticker=5)
        self.assertEqual([i.title for i in items], ["Nested", "Flat", "Undated"])
        yf.Search.assert_not_called()
