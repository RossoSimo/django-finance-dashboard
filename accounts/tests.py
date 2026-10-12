from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from wallet.models import CashAccount, Holding, NetWorthSnapshot
from watchlist.forms import WatchlistItemForm
from watchlist.models import WatchlistItem

User = get_user_model()

PROTECTED_URLS = [
    "dashboard:home", "wallet:home", "wallet:add_cash_account", "wallet:add_holding",
    "wallet:add_transaction", "wallet:transaction_list", "watchlist:home", "watchlist:add",
    "newsfeed:home", "assistant:chat", "assistant:import_upload", "password_change",
]


class LoginRequiredTests(TestCase):
    def test_anonymous_users_are_redirected_to_login(self):
        for name in PROTECTED_URLS:
            with self.subTest(name):
                url = reverse(name)
                resp = self.client.get(url)
                self.assertRedirects(resp, f"{reverse('login')}?next={url}", fetch_redirect_response=False)

    def test_login_and_signup_pages_are_public(self):
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)
        self.assertEqual(self.client.get(reverse("signup")).status_code, 200)


class AuthFlowTests(TestCase):
    def test_signup_creates_user_and_logs_in(self):
        resp = self.client.post(reverse("signup"), {
            "username": "alice", "password1": "a-Strong-pass-42", "password2": "a-Strong-pass-42",
        })
        self.assertRedirects(resp, reverse("dashboard:home"), fetch_redirect_response=False)
        user = User.objects.get(username="alice")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_signup_rejects_mismatched_passwords(self):
        resp = self.client.post(reverse("signup"), {
            "username": "alice", "password1": "a-Strong-pass-42", "password2": "different-42",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.exists())

    @override_settings(SIGNUP_ENABLED=False)
    def test_signup_can_be_disabled(self):
        self.assertEqual(self.client.get(reverse("signup")).status_code, 404)
        self.client.post(reverse("signup"), {
            "username": "alice", "password1": "a-Strong-pass-42", "password2": "a-Strong-pass-42",
        })
        self.assertFalse(User.objects.exists())

    def test_login_then_logout(self):
        User.objects.create_user("alice", password="a-Strong-pass-42")
        resp = self.client.post(reverse("login"), {"username": "alice", "password": "a-Strong-pass-42"})
        self.assertRedirects(resp, reverse("dashboard:home"), fetch_redirect_response=False)
        resp = self.client.post(reverse("logout"))
        self.assertRedirects(resp, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_wrong_password_is_rejected(self):
        User.objects.create_user("alice", password="a-Strong-pass-42")
        resp = self.client.post(reverse("login"), {"username": "alice", "password": "nope"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)


@mock.patch("newsfeed.views.get_news_for_tickers", return_value=[])
@mock.patch("dashboard.views.get_news_for_tickers", return_value=[])
@mock.patch("common.market_data.get_quotes", return_value={})
@mock.patch("dashboard.views.get_quotes", return_value={})
@mock.patch("watchlist.views.get_quotes", return_value={})
class DataIsolationTests(TestCase):
    """Alice must never see or touch Bob's data."""

    def setUp(self):
        self.alice = User.objects.create_user("alice")
        self.bob = User.objects.create_user("bob")
        CashAccount.objects.create(user=self.alice, name="Alice bank", opening_balance=Decimal("10"))
        CashAccount.objects.create(user=self.bob, name="Bob bank", opening_balance=Decimal("999"))
        self.alice_holding = Holding.objects.create(user=self.alice, ticker="AAPL", opening_quantity=1)
        self.bob_holding = Holding.objects.create(user=self.bob, ticker="TSLA", opening_quantity=1)
        WatchlistItem.objects.create(user=self.alice, ticker="MSFT")
        self.bob_watch = WatchlistItem.objects.create(user=self.bob, ticker="NVDA")
        self.client.force_login(self.alice)

    def test_pages_only_show_own_data(self, *mocks):
        for name in ["dashboard:home", "wallet:home", "watchlist:home"]:
            with self.subTest(name):
                content = self.client.get(reverse(name)).content.decode()
                self.assertNotIn("TSLA", content)
                self.assertNotIn("NVDA", content)
                self.assertNotIn("Bob bank", content)

    def test_dashboard_snapshot_and_news_use_own_tickers(self, watch_quotes, dash_quotes, wallet_quotes,
                                                         dash_news, news_news):
        self.client.get(reverse("dashboard:home"))
        self.assertEqual(NetWorthSnapshot.objects.get().user, self.alice)
        self.assertCountEqual(dash_news.call_args.args[0], ["AAPL", "MSFT"])

        self.client.get(reverse("newsfeed:home"))
        self.assertEqual(news_news.call_args.args[0], ["AAPL", "MSFT"])

    def test_cannot_delete_another_users_holding(self, *mocks):
        url = reverse("wallet:delete_holding", args=[self.bob_holding.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertTrue(Holding.objects.filter(pk=self.bob_holding.pk).exists())

    def test_cannot_remove_another_users_watchlist_item(self, *mocks):
        url = reverse("watchlist:remove", args=[self.bob_watch.pk])
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertTrue(WatchlistItem.objects.filter(pk=self.bob_watch.pk).exists())

    def test_new_objects_belong_to_the_current_user(self, *mocks):
        self.client.post(reverse("wallet:add_holding"), {
            "ticker": "voo", "asset_type": "etf", "opening_quantity": "1", "opening_avg_cost": "1", "currency": "USD",
        })
        self.assertEqual(Holding.objects.get(ticker="VOO").user, self.alice)
        self.client.post(reverse("wallet:add_cash_account"), {"name": "New", "currency": "EUR", "opening_balance": "5"})
        self.assertEqual(CashAccount.objects.get(name="New").user, self.alice)

    def test_screenshot_import_saves_into_current_user(self, *mocks):
        session = self.client.session
        session["import_extracted"] = {"holdings": [], "cash_accounts": []}
        session.save()
        self.client.post(reverse("assistant:import_preview"), {
            "holdings-TOTAL_FORMS": "1", "holdings-INITIAL_FORMS": "0",
            "holdings-0-include": "on", "holdings-0-ticker": "TSLA", "holdings-0-asset_type": "stock",
            "holdings-0-quantity": "5", "holdings-0-avg_cost": "2", "holdings-0-currency": "USD",
            "cash-TOTAL_FORMS": "0", "cash-INITIAL_FORMS": "0",
        })
        # Alice gets her own TSLA; Bob's TSLA is untouched.
        self.assertEqual(Holding.objects.get(user=self.alice, ticker="TSLA").quantity, 5)
        self.assertEqual(Holding.objects.get(pk=self.bob_holding.pk).quantity, 1)

    def test_assistant_context_is_scoped(self, *mocks):
        from assistant.context import build_portfolio_context
        context = build_portfolio_context(self.alice)
        self.assertIn("AAPL", context)
        self.assertNotIn("TSLA", context)
        self.assertNotIn("NVDA", context)


class PerUserUniquenessTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice")
        self.bob = User.objects.create_user("bob")

    def test_two_users_can_track_the_same_ticker(self):
        WatchlistItem.objects.create(user=self.bob, ticker="AAPL")
        form = WatchlistItemForm({"ticker": "aapl", "asset_type": "stock"}, user=self.alice)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().user, self.alice)

    def test_same_user_cannot_add_a_ticker_twice(self):
        WatchlistItem.objects.create(user=self.alice, ticker="AAPL")
        form = WatchlistItemForm({"ticker": " aapl ", "asset_type": "stock"}, user=self.alice)
        self.assertFalse(form.is_valid())
        self.assertIn("ticker", form.errors)
