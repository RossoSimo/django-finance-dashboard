from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.template import Context, Template
from django.test import TestCase

from common import market_data
from common.templatetags.fmt import money, num, pct, signed_money


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


class FormatTests(TestCase):
    def test_money(self):
        self.assertEqual(money(Decimal("1234.5"), "EUR"), "1.234,50\u00a0€")
        self.assertEqual(money(-1234.5, "USD"), "−1.234,50\u00a0$")
        self.assertEqual(money(Decimal("1234567.891"), "SEK"), "1.234.567,89\u00a0SEK")
        self.assertEqual(money(Decimal("1250"), "GBp"), "12,50\u00a0£")
        self.assertEqual(money(None, "EUR"), "—")

    def test_symbol_uses_non_breaking_space(self):
        self.assertIn("\u00a0", money(Decimal("1"), "EUR"))
        self.assertNotIn(" ", money(Decimal("1"), "EUR"))
        self.assertIn("\u00a0", pct(1))
        self.assertNotIn(" ", pct(1))

    def test_signed_money(self):
        self.assertEqual(signed_money(Decimal("12.3"), "EUR"), "+12,30\u00a0€")
        self.assertEqual(signed_money(Decimal("-12.3"), "EUR"), "−12,30\u00a0€")

    def test_num(self):
        self.assertEqual(num(Decimal("0.0500")), "0,05")
        self.assertEqual(num(Decimal("12")), "12")
        self.assertEqual(num(1234.5, 2), "1.234,50")

    def test_pct(self):
        self.assertEqual(pct(2.345), "+2,35\u00a0%")
        self.assertEqual(pct(Decimal("-1.1")), "−1,10\u00a0%")

    def test_negative_zero_has_no_sign(self):
        self.assertEqual(money(Decimal("-0.004"), "EUR"), "0,00\u00a0€")
        self.assertEqual(pct(-0.001), "0,00\u00a0%")

    def test_template_builtin(self):
        self.assertEqual(Template("{{ v|money:'EUR' }}").render(Context({"v": 5})), "5,00\u00a0€")
