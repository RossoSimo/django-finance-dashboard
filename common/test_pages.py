from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from alerts.models import PriceAlert
from wallet.models import CashAccount, Holding, Transaction
from wallet.services import save_transaction
from watchlist.models import WatchlistItem


@mock.patch("common.market_data.yf", None)
@mock.patch("requests.post", side_effect=AssertionError("network"))
class PageSmokeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("alice", password="pw-12345-xyz")
        self.client.force_login(self.user)
        self.account = CashAccount.objects.create(
            user=self.user, name="Main", currency="EUR", opening_balance=Decimal("1000")
        )
        self.holding = Holding.objects.create(user=self.user, ticker="AAPL", currency="USD")
        self.tx = save_transaction(
            Transaction(
                user=self.user,
                tx_type="buy",
                holding=self.holding,
                cash_account=self.account,
                quantity=Decimal("2"),
                price=Decimal("10"),
                date=date(2024, 1, 2),
            )
        )
        WatchlistItem.objects.create(user=self.user, ticker="MSFT")
        PriceAlert.objects.create(
            user=self.user, ticker="AAPL", direction="above", target_price=Decimal("500")
        )

    def test_every_page_renders(self, *_):
        pages = [
            reverse("dashboard:home"),
            reverse("wallet:home"),
            reverse("wallet:add_cash_account"),
            reverse("wallet:add_holding"),
            reverse("wallet:transaction_list"),
            reverse("wallet:add_transaction"),
            reverse("wallet:edit_transaction", args=[self.tx.pk]),
            reverse("wallet:delete_transaction", args=[self.tx.pk]),
            reverse("wallet:csv_upload"),
            reverse("watchlist:home"),
            reverse("watchlist:add"),
            reverse("newsfeed:home"),
            reverse("alerts:home"),
            reverse("alerts:add"),
            reverse("performance:portfolio"),
            reverse("performance:holding", args=[self.holding.pk]),
            reverse("assistant:chat"),
            reverse("assistant:import_upload"),
            reverse("settings"),
            reverse("password_change"),
            reverse("password_change_done"),
        ]
        for url in pages:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_active_nav_item(self, *_):
        resp = self.client.get(reverse("wallet:home"))
        self.assertContains(resp, 'href="/wallet/" aria-current="page"', count=2)

    def test_more_sheet_lists_only_non_tab_items(self, *_):
        html = self.client.get(reverse("wallet:home")).content.decode()
        sheet = html.split('id="more-sheet"', 1)[1]
        self.assertIn("/watchlist/", sheet)
        self.assertIn("Log out", sheet)
        self.assertNotIn('href="/wallet/"', sheet)

    def test_shell_structure(self, *_):
        resp = self.client.get(reverse("dashboard:home"))
        for marker in ('id="app-sidebar"', 'class="tabbar', 'id="more-sheet"', "toast-container"):
            self.assertContains(resp, marker)
        self.assertNotContains(resp, "<style>")

    def test_auth_pages_have_no_app_navigation(self, *_):
        self.client.logout()
        for name in ("login", "signup"):
            self.assertNotContains(self.client.get(reverse(name)), 'id="app-sidebar"')

    def test_messages_render_as_toasts(self, *_):
        resp = self.client.post(
            reverse("watchlist:add"),
            {"ticker": "NVDA", "name": "", "asset_type": "stock", "notes": ""},
            follow=True,
        )
        self.assertContains(resp, 'class="toast')


class WatchlistNewsPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user("alice", password="pw-12345-xyz")
        self.client.force_login(self.user)

    def test_news_items_show_relative_time(self):
        from datetime import timedelta

        from django.utils import timezone

        from common.market_data import NewsItem
        items = [
            NewsItem("AAPL", "Apple up", "Wire", "https://example.com/a", timezone.now() - timedelta(hours=2, seconds=30)),
            NewsItem("AAPL", "Undated", None, "https://example.com/b", None),
        ]
        with mock.patch("newsfeed.views.get_news_for_tickers", return_value=items):
            page = self.client.get(reverse("newsfeed:home"))
        self.assertContains(page, "2 hours ago")
        self.assertContains(page, 'rel="noopener"')
        self.assertContains(page, "Unknown source")

    def test_watchlist_rows_show_formatted_price_and_menu(self):
        from common.market_data import Quote
        WatchlistItem.objects.create(user=self.user, ticker="MSFT")
        q = {"MSFT": Quote("MSFT", "Microsoft", 1234.5, 1200.0, "USD", 34.5, 2.875)}
        with mock.patch("watchlist.views.get_quotes", return_value=q):
            page = self.client.get(reverse("watchlist:home"))
        self.assertContains(page, "1.234,50 $")
        self.assertContains(page, "Actions for MSFT")
        self.assertContains(page, 'data-confirm="Remove MSFT?"')
        self.assertContains(page, "table-responsive")
