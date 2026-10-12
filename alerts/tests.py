from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from common.market_data import Quote

from .forms import PriceAlertForm
from .models import PriceAlert
from .services import check_alerts, dismiss, rearm, unseen_alerts

D = Decimal
User = get_user_model()


def quote(ticker, price, currency="USD"):
    return Quote(ticker=ticker, name=None, price=price, previous_close=None,
                 currency=currency, change=None, change_pct=None)


def quotes(**prices):
    return {t.replace("_", "."): quote(t.replace("_", "."), p) for t, p in prices.items()}


class CheckAlertsTests(TestCase):
    def setUp(self):
        cache.clear()  # the per-user throttle lives in the cache
        self.user = User.objects.create_user("alice")

    def alert(self, ticker="AAPL", direction=PriceAlert.ABOVE, target="200", user=None):
        return PriceAlert.objects.create(user=user or self.user, ticker=ticker, direction=direction,
                                         target_price=D(target))

    def check(self, prices, user="self", force=True):
        with mock.patch("common.market_data.get_quotes", return_value=prices) as get_quotes:
            triggered = check_alerts(self.user if user == "self" else user, force=force)
        return triggered, get_quotes

    def test_above_and_below(self):
        up = self.alert(direction=PriceAlert.ABOVE, target="200")
        down = self.alert(direction=PriceAlert.BELOW, target="150")
        triggered, _ = self.check(quotes(AAPL=180.0))
        self.assertEqual(triggered, [])
        triggered, _ = self.check(quotes(AAPL=200.0))  # "to or above" includes the target
        self.assertEqual(triggered, [up])
        triggered, _ = self.check(quotes(AAPL=149.99))
        self.assertEqual(triggered, [down])

    def test_trigger_records_price_and_stops_watching(self):
        alert = self.alert()
        self.check(quotes(AAPL=201.5))
        alert.refresh_from_db()
        self.assertFalse(alert.is_active)
        self.assertEqual(alert.triggered_price, D("201.5"))
        self.assertEqual(alert.currency, "USD")
        self.assertTrue(alert.is_unseen)
        # One-shot: later checks don't touch it again.
        triggered, get_quotes = self.check(quotes(AAPL=300.0))
        self.assertEqual(triggered, [])
        get_quotes.assert_not_called()

    def test_untriggered_alert_records_last_price(self):
        alert = self.alert()
        self.check(quotes(AAPL=123.0))
        alert.refresh_from_db()
        self.assertTrue(alert.is_active)
        self.assertEqual(alert.last_price, D("123"))
        self.assertIsNotNone(alert.last_checked_at)

    def test_missing_quote_is_skipped(self):
        alert = self.alert()
        self.check({"AAPL": quote("AAPL", None)})
        self.check({})
        alert.refresh_from_db()
        self.assertTrue(alert.is_active)
        self.assertIsNone(alert.last_checked_at)

    def test_pence_quotes_are_compared_in_pounds(self):
        alert = self.alert(ticker="VOD.L", target="0.80")
        self.check({"VOD.L": quote("VOD.L", 81.0, "GBp")})
        alert.refresh_from_db()
        self.assertEqual((alert.triggered_price, alert.currency), (D("0.81"), "GBP"))

    def test_fetches_each_ticker_once(self):
        self.alert(target="500")
        self.alert(direction=PriceAlert.BELOW, target="1")
        self.alert(ticker="MSFT", target="999")
        _, get_quotes = self.check(quotes(AAPL=10.0, MSFT=10.0))
        get_quotes.assert_called_once_with(["AAPL", "MSFT"])

    def test_throttled_per_user(self):
        self.alert(target="500")
        _, first = self.check(quotes(AAPL=10.0), force=False)
        _, second = self.check(quotes(AAPL=10.0), force=False)
        first.assert_called_once()
        second.assert_not_called()

    def test_only_checks_the_given_users_alerts(self):
        bob = User.objects.create_user("bob")
        bobs = self.alert(user=bob, target="1")
        self.check(quotes(AAPL=10.0))
        bobs.refresh_from_db()
        self.assertTrue(bobs.is_active)
        # Checking everyone (management command) does include it.
        self.check(quotes(AAPL=10.0), user=None)
        bobs.refresh_from_db()
        self.assertFalse(bobs.is_active)

    def test_dismiss_and_rearm(self):
        alert = self.alert(target="1")
        self.check(quotes(AAPL=10.0))
        self.assertEqual(list(unseen_alerts(self.user)), [alert])
        self.assertEqual(dismiss(PriceAlert.objects.filter(user=self.user)), 1)
        self.assertEqual(list(unseen_alerts(self.user)), [])
        alert.refresh_from_db()
        rearm(alert)
        alert.refresh_from_db()
        self.assertTrue(alert.is_active)
        self.assertIsNone(alert.triggered_at)
        triggered, _ = self.check(quotes(AAPL=10.0))
        self.assertEqual(triggered, [alert])

    def test_management_command(self):
        self.alert(target="1")
        out = StringIO()
        with mock.patch("common.market_data.get_quotes", return_value=quotes(AAPL=10.0)):
            call_command("check_alerts", stdout=out)
        self.assertIn("1 alert(s) triggered", out.getvalue())
        self.assertIn("AAPL", out.getvalue())


class PriceAlertFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")

    def test_valid_and_normalised(self):
        form = PriceAlertForm({"ticker": " aapl ", "direction": "above", "target_price": "200"}, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        alert = form.save()
        self.assertEqual((alert.ticker, alert.user), ("AAPL", self.user))

    def test_several_alerts_per_ticker_are_allowed(self):
        PriceAlert.objects.create(user=self.user, ticker="AAPL", direction="above", target_price=D("1"))
        form = PriceAlertForm({"ticker": "AAPL", "direction": "below", "target_price": "1"}, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)

    def test_target_must_be_positive(self):
        form = PriceAlertForm({"ticker": "AAPL", "direction": "above", "target_price": "0"}, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("target_price", form.errors)


@mock.patch("dashboard.views.get_news_for_tickers", return_value=[])
@mock.patch("dashboard.views.get_quotes", return_value={})
@mock.patch("watchlist.views.get_quotes", return_value={})
class AlertViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("alice")
        self.client.force_login(self.user)

    def triggered_alert(self, user=None, ticker="AAPL"):
        return PriceAlert.objects.create(
            user=user or self.user, ticker=ticker, direction="above", target_price=D("100"),
            is_active=False, triggered_at="2026-10-12T10:00:00Z", triggered_price=D("101"), currency="USD",
        )

    def test_add_alert_prefilled_from_ticker(self, *mocks):
        page = self.client.get(reverse("alerts:add") + "?ticker=msft")
        self.assertEqual(page.context["form"]["ticker"].value(), "MSFT")
        resp = self.client.post(reverse("alerts:add"), {"ticker": "MSFT", "direction": "below", "target_price": "300"})
        self.assertRedirects(resp, reverse("alerts:home"), fetch_redirect_response=False)
        self.assertEqual(PriceAlert.objects.get().user, self.user)

    def test_opening_the_dashboard_triggers_alerts(self, *mocks):
        alert = PriceAlert.objects.create(user=self.user, ticker="AAPL", direction="above", target_price=D("100"))
        with mock.patch("common.market_data.get_quotes", return_value=quotes(AAPL=150.0)):
            resp = self.client.get(reverse("dashboard:home"))
        alert.refresh_from_db()
        self.assertFalse(alert.is_active)
        self.assertContains(resp, "Price alert triggered")  # banner on the same page load

    def test_watchlist_page_checks_alerts(self, *mocks):
        PriceAlert.objects.create(user=self.user, ticker="AAPL", direction="above", target_price=D("100"))
        with mock.patch("common.market_data.get_quotes", return_value=quotes(AAPL=150.0)):
            self.client.get(reverse("watchlist:home"))
        self.assertTrue(unseen_alerts(self.user).exists())

    def test_banner_and_badge_on_every_page_until_dismissed(self, *mocks):
        self.triggered_alert()
        with mock.patch("common.market_data.get_quotes", return_value={}):
            page = self.client.get(reverse("wallet:transaction_list"))
        self.assertContains(page, "Price alert triggered")
        self.assertContains(page, "hit 101 USD")
        self.assertContains(page, '<span class="visually-hidden"> triggered</span>', html=False)

        resp = self.client.post(reverse("alerts:dismiss_all"), {"next": reverse("wallet:transaction_list")})
        self.assertRedirects(resp, reverse("wallet:transaction_list"), fetch_redirect_response=False)
        page = self.client.get(reverse("wallet:transaction_list"))
        self.assertNotContains(page, "Price alert triggered")

    def test_dismiss_ignores_unsafe_next_url(self, *mocks):
        self.triggered_alert()
        resp = self.client.post(reverse("alerts:dismiss_all"), {"next": "https://evil.example/"})
        self.assertRedirects(resp, reverse("alerts:home"), fetch_redirect_response=False)

    def test_rearm_and_delete(self, *mocks):
        alert = self.triggered_alert()
        self.client.post(reverse("alerts:rearm", args=[alert.pk]))
        alert.refresh_from_db()
        self.assertTrue(alert.is_active)
        self.client.post(reverse("alerts:delete", args=[alert.pk]))
        self.assertFalse(PriceAlert.objects.exists())

    def test_actions_require_post(self, *mocks):
        alert = self.triggered_alert()
        for name in ("alerts:dismiss", "alerts:rearm", "alerts:delete"):
            with self.subTest(name):
                self.assertEqual(self.client.get(reverse(name, args=[alert.pk])).status_code, 405)

    def test_other_users_alerts_are_private(self, *mocks):
        bob = User.objects.create_user("bob")
        bobs = self.triggered_alert(user=bob, ticker="TSLA")
        with mock.patch("common.market_data.get_quotes", return_value={}):
            page = self.client.get(reverse("alerts:home"))
        self.assertNotContains(page, "TSLA")
        for name in ("alerts:rearm", "alerts:delete"):
            self.assertEqual(self.client.post(reverse(name, args=[bobs.pk])).status_code, 404)
        self.client.post(reverse("alerts:dismiss", args=[bobs.pk]))
        bobs.refresh_from_db()
        self.assertTrue(bobs.is_unseen)

    def test_requires_login(self, *mocks):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("alerts:home")).status_code, 302)


class AssistantContextTests(TestCase):
    def test_active_alerts_are_shared_with_the_assistant(self):
        import json

        from assistant.context import build_portfolio_context

        user = User.objects.create_user("alice")
        PriceAlert.objects.create(user=user, ticker="MSFT", direction="below", target_price=D("300"))
        PriceAlert.objects.create(user=user, ticker="OLD", direction="above", target_price=D("1"), is_active=False)
        with mock.patch("common.market_data.get_quotes", return_value={}):
            context = json.loads(build_portfolio_context(user))
        self.assertEqual(context["active_price_alerts"],
                         [{"ticker": "MSFT", "direction": "below", "target_price": 300.0}])
