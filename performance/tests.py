from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import get_profile
from common.currency import CurrencyConverter
from common.market_data import Quote
from wallet.models import CashAccount, Holding, Transaction
from wallet.services import save_transaction

from .services import holding_history, portfolio_history, range_start, sample_days

D = Decimal
User = get_user_model()

# A week starting on a Monday; the 10th-11th is a weekend with no closes.
MON = date(2026, 1, 5)


def day(n):
    return MON + timedelta(days=n)


def quote(ticker, currency="USD"):
    return Quote(ticker=ticker, name=None, price=1.0, previous_close=None,
                 currency=currency, change=None, change_pct=None)


def fake_history(series):
    """history_fn returning fixed closes per symbol (ignores `start`)."""
    return lambda symbol, start: series.get(symbol.upper(), [])


def no_current_rates(from_ccy, to_ccy):
    return None


class RangeTests(TestCase):
    def test_range_start(self):
        today = date(2026, 10, 12)
        self.assertEqual(range_start("1m", today, None), date(2026, 9, 12))
        self.assertEqual(range_start("ytd", today, None), date(2026, 1, 1))
        self.assertEqual(range_start("max", today, date(2024, 3, 1)), date(2024, 3, 1))
        self.assertEqual(range_start("max", today, None), today - timedelta(days=365))
        self.assertEqual(range_start("max", today, date(1990, 1, 1)), today - timedelta(days=3650))
        self.assertEqual(range_start("bogus", today, None), today - timedelta(days=182))

    def test_sample_days(self):
        days = sample_days(day(0), day(6))
        self.assertEqual(len(days), 7)
        long = sample_days(date(2020, 1, 1), date(2026, 1, 3))
        self.assertEqual(long[1] - long[0], timedelta(days=7))  # weekly beyond ~400 days
        self.assertEqual(long[-1], date(2026, 1, 3))  # always ends on the end date


class PortfolioHistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        profile = get_profile(self.user)
        profile.base_currency = "EUR"
        profile.save()
        self.broker = CashAccount.objects.create(user=self.user, name="Broker", currency="EUR",
                                                 opening_balance=D("1000"), allow_negative_balance=True)
        self.aapl = Holding.objects.create(user=self.user, ticker="AAPL", currency="EUR")

    def record(self, **kwargs):
        kwargs.setdefault("user", self.user)
        return save_transaction(Transaction(**kwargs))

    def history(self, series, quotes=None, rates=no_current_rates, start=day(0), end=day(6)):
        return portfolio_history(self.user, start, end, history_fn=fake_history(series),
                                 quotes=quotes if quotes is not None else {"AAPL": quote("AAPL", "EUR")},
                                 converter=CurrencyConverter("EUR", rates))

    def test_position_and_cash_follow_the_transactions(self):
        self.record(tx_type="buy", holding=self.aapl, cash_account=self.broker,
                    quantity=D("2"), price=D("100"), date=day(2))
        h = self.history({"AAPL": [(day(0), 90.0), (day(1), 95.0), (day(2), 100.0), (day(3), 110.0), (day(4), 120.0)]})

        self.assertEqual(h.holdings_value[:2], [D("0"), D("0")])  # not held yet
        self.assertEqual(h.holdings_value[2:5], [D("200.0"), D("220.0"), D("240.0")])
        self.assertEqual(h.holdings_value[5:], [D("240.0"), D("240.0")])  # weekend: last close carried
        self.assertEqual(h.invested[1:3], [D("0"), D("200")])
        self.assertEqual(h.cash[1:3], [D("1000"), D("800")])
        self.assertEqual(h.net_worth[0], D("1000"))
        self.assertEqual(h.net_worth[-1], D("1040.0"))
        self.assertEqual(h.change, D("40.0"))
        self.assertEqual(h.unrealized_gain, D("40.0"))

        (perf,) = h.holdings
        self.assertEqual(perf.price_change_pct, (D("120") / D("90") - 1) * 100)
        self.assertEqual(perf.gain, D("40.0"))

    def test_opening_position_counts_from_the_start(self):
        self.aapl.delete()
        aapl = Holding.objects.create(user=self.user, ticker="AAPL", currency="EUR",
                                      opening_quantity=D("1"), opening_avg_cost=D("50"))
        h = self.history({"AAPL": [(day(0), 60.0)]})
        self.assertTrue(all(v == D("60.0") for v in h.holdings_value))
        self.assertEqual(h.holdings[0].holding, aapl)

    def test_falls_back_to_average_cost_before_the_first_close(self):
        self.record(tx_type="buy", holding=self.aapl, quantity=D("1"), price=D("100"), date=day(0))
        h = self.history({"AAPL": [(day(3), 130.0)]})
        self.assertEqual(h.holdings_value[:3], [D("100"), D("100"), D("100")])
        self.assertEqual(h.holdings_value[3], D("130.0"))

    def test_no_history_at_all_is_reported_and_valued_at_cost(self):
        self.record(tx_type="buy", holding=self.aapl, quantity=D("1"), price=D("100"), date=day(0))
        h = self.history({})
        self.assertEqual(h.missing_prices, ["AAPL"])
        self.assertEqual(h.holdings_value[-1], D("100"))

    def test_uses_each_days_fx_rate(self):
        self.aapl.currency = "USD"
        self.aapl.save()
        self.record(tx_type="buy", holding=self.aapl, quantity=D("1"), price=D("100"), date=day(0))
        h = self.history(
            {"AAPL": [(day(0), 100.0)], "USDEUR=X": [(day(0), 0.9), (day(3), 0.8)]},
            quotes={"AAPL": quote("AAPL", "USD")},
        )
        self.assertEqual(h.holdings_value[0], D("90.0"))
        self.assertEqual(h.holdings_value[3], D("80.0"))
        self.assertEqual(h.invested[3], D("80.0"))

    def test_fx_falls_back_to_todays_rate_then_reports_missing(self):
        usd = CashAccount.objects.create(user=self.user, name="USD", currency="USD", opening_balance=D("10"))
        h = self.history({}, rates=lambda f, t: 0.5)
        self.assertEqual(h.cash[0], D("1005.0"))
        self.assertEqual(h.missing_currencies, [])

        h = self.history({})  # no history and no current rate
        self.assertEqual(h.cash[0], D("1000"))
        self.assertEqual(h.missing_currencies, ["USD"])
        usd.delete()

    def test_pence_prices_are_converted_to_pounds(self):
        vod = Holding.objects.create(user=self.user, ticker="VOD.L", currency="GBP",
                                     opening_quantity=D("100"), opening_avg_cost=D("1"))
        h = self.history({"VOD.L": [(day(0), 120.0)], "GBPEUR=X": [(day(0), 1.2)]},
                         quotes={"VOD.L": quote("VOD.L", "GBp")})
        perf = next(p for p in h.holdings if p.holding == vod)
        self.assertEqual(perf.price_currency, "GBP")
        self.assertEqual(perf.last_price, D("1.2"))
        self.assertEqual(h.holdings_value[0], D("144.00"))  # 100 * 1.20 GBP * 1.2

    def test_holdings_sold_before_the_range_are_left_out(self):
        self.record(tx_type="buy", holding=self.aapl, quantity=D("1"), price=D("1"), date=day(-30))
        self.record(tx_type="sell", holding=self.aapl, quantity=D("1"), price=D("1"), date=day(-20))
        h = self.history({"AAPL": [(day(0), 5.0)]})
        self.assertEqual(h.holdings, [])
        self.assertTrue(all(v == 0 for v in h.holdings_value))

    def test_only_the_users_own_data(self):
        bob = User.objects.create_user("bob")
        CashAccount.objects.create(user=bob, name="Bob", currency="EUR", opening_balance=D("99999"))
        Holding.objects.create(user=bob, ticker="AAPL", currency="EUR", opening_quantity=D("100"))
        h = self.history({"AAPL": [(day(0), 1.0)]})
        self.assertEqual(h.net_worth[0], D("1000"))
        self.assertEqual(h.holdings, [])


class HoldingHistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.aapl = Holding.objects.create(user=self.user, ticker="AAPL", currency="USD")

    def test_price_average_cost_and_trades(self):
        save_transaction(Transaction(user=self.user, tx_type="buy", holding=self.aapl, quantity=D("2"),
                                     price=D("100"), date=day(1)))
        save_transaction(Transaction(user=self.user, tx_type="buy", holding=self.aapl, quantity=D("2"),
                                     price=D("200"), date=day(3)))
        save_transaction(Transaction(user=self.user, tx_type="buy", holding=self.aapl, quantity=D("1"),
                                     price=D("1"), date=day(-10)))  # before the range
        h = holding_history(self.aapl, day(0), day(4), history_fn=fake_history({"AAPL": [(day(0), 50.0)]}),
                            quote=quote("AAPL", "USD"))
        self.assertEqual(h.price, [D("50.0")] * 5)
        self.assertEqual(h.quantity, [D("1"), D("3"), D("3"), D("5"), D("5")])
        self.assertEqual(h.avg_cost[1], (D("1") + D("200")) / 3)
        self.assertEqual(len(h.trades), 2)  # only those inside the range
        self.assertTrue(h.avg_cost_comparable)

    def test_average_cost_not_comparable_across_currencies(self):
        h = holding_history(self.aapl, day(0), day(1), history_fn=fake_history({}), quote=quote("AAPL", "EUR"))
        self.assertFalse(h.avg_cost_comparable)


@mock.patch("common.market_data.get_quote", return_value=quote("AAPL", "EUR"))
@mock.patch("common.market_data.get_quotes", return_value={})
@mock.patch("common.market_data.get_price_history", return_value=[])
@mock.patch("common.market_data.get_fx_rate", return_value=None)
class PerformanceViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.client.force_login(self.user)

    def test_empty_state(self, *mocks):
        resp = self.client.get(reverse("performance:portfolio"))
        self.assertContains(resp, "Nothing to chart yet")

    def test_portfolio_page_with_data_and_range(self, *mocks):
        CashAccount.objects.create(user=self.user, name="Bank", currency="EUR", opening_balance=D("500"))
        resp = self.client.get(reverse("performance:portfolio") + "?range=1m")
        self.assertContains(resp, 'id="portfolio-data"')
        self.assertContains(resp, "500.00 EUR")
        self.assertContains(resp, 'aria-current="page">1M<')
        self.assertEqual(resp.context["history"].days[-1], date.today())
        self.assertEqual(len(resp.context["history"].days), 31)

    def test_unknown_range_uses_default(self, *mocks):
        CashAccount.objects.create(user=self.user, name="Bank", currency="EUR", opening_balance=D("1"))
        resp = self.client.get(reverse("performance:portfolio") + "?range=<script>")
        self.assertEqual(resp.context["range_key"], "6m")
        self.assertNotContains(resp, "<script>alert")

    def test_holding_page(self, _fx_rate, history, *mocks):  # mocks arrive bottom decorator first
        history.return_value = [(date.today() - timedelta(days=3), 10.0)]
        holding = Holding.objects.create(user=self.user, ticker="AAPL", currency="EUR")
        save_transaction(Transaction(user=self.user, tx_type="buy", holding=holding, quantity=D("1"),
                                     price=D("9"), date=date.today() - timedelta(days=2)))
        resp = self.client.get(reverse("performance:holding", args=[holding.pk]) + "?range=1m")
        self.assertContains(resp, 'id="holding-data"')
        trades = resp.context["chart"]["trades"]
        self.assertEqual(trades, [{"x": (date.today() - timedelta(days=2)).isoformat(), "y": 9.0,
                                   "type": "buy", "quantity": 1.0}])

    def test_trade_markers_snap_to_plotted_days(self, *mocks):
        holding = Holding.objects.create(user=self.user, ticker="AAPL", currency="EUR")
        old = date.today() - timedelta(days=900)
        save_transaction(Transaction(user=self.user, tx_type="buy", holding=holding, quantity=D("1"),
                                     price=D("9"), date=old))
        resp = self.client.get(reverse("performance:holding", args=[holding.pk]) + "?range=max")
        days = [d.isoformat() for d in resp.context["history"].days]
        self.assertIn(resp.context["chart"]["trades"][0]["x"], days)

    def test_other_users_holding_is_not_found(self, *mocks):
        bob = User.objects.create_user("bob")
        holding = Holding.objects.create(user=bob, ticker="TSLA")
        self.assertEqual(self.client.get(reverse("performance:holding", args=[holding.pk])).status_code, 404)

    def test_requires_login(self, *mocks):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("performance:portfolio")).status_code, 302)
