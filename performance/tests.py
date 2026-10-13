from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import get_profile
from common.currency import CurrencyConverter
from common.market_data import Quote
from wallet.models import CashAccount, Holding, NetWorthSnapshot, Transaction
from wallet.services import save_transaction
from watchlist.models import WatchlistItem

from .services import (
    RANGES,
    HoldingHistory,
    PortfolioHistory,
    history_for_range,
    holding_history,
    holding_history_intraday,
    portfolio_history,
    portfolio_history_intraday,
    range_start,
    sample_days,
)

D = Decimal
User = get_user_model()

# A week starting on a Monday; the 10th-11th is a weekend with no closes.
MON = date(2026, 1, 5)


def day(n):
    return MON + timedelta(days=n)


def quote(ticker, currency="USD", price=1.0):
    return Quote(ticker=ticker, name=None, price=price, previous_close=None,
                 currency=currency, change=None, change_pct=None)


def fake_history(series):
    """history_fn returning fixed closes per symbol (ignores `start`)."""
    return lambda symbol, start: series.get(symbol.upper(), [])


def fake_intraday(series):
    """intraday_fn returning fixed bars per symbol (ignores the range key)."""
    return lambda symbol, key: series.get(symbol.upper(), [])


def utc(month_day, hour, minute=0):
    """An aware UTC datetime in October 2026."""
    return datetime(2026, 10, month_day, hour, minute, tzinfo=UTC)


NOW = utc(11, 12)  # Sunday 11 Oct 2026, 14:00 in Rome


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

    def test_short_ranges(self):
        today = date(2026, 10, 12)
        self.assertEqual(range_start("1d", today, None), date(2026, 10, 11))
        self.assertEqual(range_start("7d", today, None), date(2026, 10, 5))
        self.assertEqual(list(RANGES)[:3], ["1d", "7d", "1m"])

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

    def history(self, series, quotes=None, rates=no_current_rates, start=day(0), end=day(6), today=day(30)):
        return portfolio_history(self.user, start, end, history_fn=fake_history(series),
                                 quotes=quotes if quotes is not None else {"AAPL": quote("AAPL", "EUR")},
                                 converter=CurrencyConverter("EUR", rates), today=today)

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


class LiveLastPointTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        profile = get_profile(self.user)
        profile.base_currency = "EUR"
        profile.save()
        self.aapl = Holding.objects.create(user=self.user, ticker="AAPL", currency="EUR",
                                           opening_quantity=D("2"), opening_avg_cost=D("5"))

    def history(self, series, quotes, today, rates=no_current_rates):
        return portfolio_history(self.user, day(0), day(6), history_fn=fake_history(series), quotes=quotes,
                                 converter=CurrencyConverter("EUR", rates), today=today)

    def test_live_last_point_uses_quote(self):
        h = self.history({"AAPL": [(day(0), 10.0)]}, {"AAPL": quote("AAPL", "EUR", price=12.0)}, today=day(6))
        self.assertEqual(h.holdings_value[:-1], [D("20.0")] * 6)
        self.assertEqual(h.holdings_value[-1], D("24.0"))
        self.assertEqual(h.holdings[0].last_price, D("12.0"))
        self.assertEqual(h.holdings[0].end_value, D("24.0"))

    def test_live_quote_ignored_when_range_ends_in_past(self):
        h = self.history({"AAPL": [(day(0), 10.0)]}, {"AAPL": quote("AAPL", "EUR", price=12.0)}, today=day(7))
        self.assertEqual(h.holdings_value[-1], D("20.0"))
        self.assertEqual(h.holdings[0].last_price, D("10.0"))

    def test_holding_without_live_price_keeps_its_close(self):
        h = self.history({"AAPL": [(day(0), 10.0)]}, {"AAPL": quote("AAPL", "EUR", price=None)}, today=day(6))
        self.assertEqual(h.holdings_value[-1], D("20.0"))
        self.assertEqual(h.holdings[0].last_price, D("10.0"))

    def test_live_last_point_normalises_pence(self):
        self.aapl.delete()
        Holding.objects.create(user=self.user, ticker="VOD.L", currency="GBP",
                               opening_quantity=D("2"), opening_avg_cost=D("10"))
        h = self.history({"VOD.L": [(day(0), 1000.0)], "GBPEUR=X": [(day(0), 1.1)]},
                         {"VOD.L": quote("VOD.L", "GBp", price=1250.0)}, today=day(6),
                         rates=lambda f, t: 1.2)
        self.assertEqual(h.holdings_value[0], D("22.0"))  # 2 * 10.00 GBP * 1.1 (FX history)
        self.assertEqual(h.holdings_value[-1], D("30.0"))  # 2 * 12.50 GBP * 1.2 (current rate)
        self.assertEqual(h.holdings[0].last_price, D("12.5"))

    def test_live_point_falls_back_to_fx_history(self):
        CashAccount.objects.create(user=self.user, name="USD", currency="USD", opening_balance=D("10"))
        h = self.history({"AAPL": [(day(0), 10.0)], "USDEUR=X": [(day(0), 0.5), (day(5), 0.6)]}, {},
                         today=day(6))
        self.assertEqual(h.cash[0], D("5.0"))
        self.assertEqual(h.cash[-1], D("6.0"))  # no current rate: latest history rate on/before today
        self.assertEqual(h.missing_currencies, [])

    def test_live_point_without_any_rate_is_excluded_and_reported(self):
        CashAccount.objects.create(user=self.user, name="USD", currency="USD", opening_balance=D("10"))
        h = self.history({"AAPL": [(day(0), 10.0)]}, {}, today=day(6))
        self.assertEqual(h.cash[-1], D("0"))  # left out, not counted 1:1
        self.assertEqual(h.missing_currencies, ["USD"])


class IntradayPortfolioTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        profile = get_profile(self.user)
        profile.base_currency = "EUR"
        profile.save()

    def hold(self, ticker, qty, currency="EUR", avg="1"):
        return Holding.objects.create(user=self.user, ticker=ticker, currency=currency,
                                      opening_quantity=D(qty), opening_avg_cost=D(avg))

    def history(self, bars, closes=None, key="1d", quotes=None, rates=no_current_rates):
        return portfolio_history_intraday(self.user, key, intraday_fn=fake_intraday(bars),
                                          history_fn=fake_history(closes or {}), quotes=quotes or {},
                                          converter=CurrencyConverter("EUR", rates), now=NOW)

    def test_grid_aligns_and_forward_fills_two_holdings(self):
        self.hold("AAPL", "1")
        self.hold("MSFT", "1")
        h = self.history({"AAPL": [(utc(9, 13, 30), 100.0), (utc(9, 13, 40), 102.0)],
                          "MSFT": [(utc(9, 13, 35), 50.0)]},
                         closes={"MSFT": [(date(2026, 10, 8), 49.0)]})
        self.assertTrue(h.intraday)
        self.assertEqual(h.session_date, date(2026, 10, 9))
        self.assertEqual(h.days, [utc(9, 13, 30), utc(9, 13, 35), utc(9, 13, 40)])  # Friday: no `now` point
        # 13:30: MSFT has no bar yet -> previous close; 13:35: AAPL carries its 13:30 price
        self.assertEqual(h.holdings_value, [D("149.0"), D("150.0"), D("152.0")])
        self.assertEqual(h.missing_prices, [])

    def test_bars_are_bucketed(self):
        self.hold("AAPL", "1")
        h = self.history({"AAPL": [(utc(9, 13, 31), 100.0), (utc(9, 13, 33), 101.0)]})
        self.assertEqual(h.days, [utc(9, 13, 30)])
        self.assertEqual(h.holdings_value[0], D("101.0"))  # last bar in the bucket wins

    def test_intraday_holding_without_bars_is_flat_at_last_close(self):
        self.hold("FUND", "3")
        h = self.history({}, closes={"FUND": [(date(2026, 10, 9), 48.0), (date(2026, 10, 10), 50.0)]})
        self.assertEqual(h.session_date, date(2026, 10, 11))
        self.assertGreaterEqual(len(h.days), 2)
        self.assertTrue(all(v == D("150.0") for v in h.holdings_value))
        self.assertEqual(h.missing_prices, [])

    def test_1d_uses_latest_session_and_holds_older_sessions_flat(self):
        self.hold("BTC-EUR", "2")
        self.hold("AAPL", "1")
        h = self.history({"BTC-EUR": [(utc(11, 8, 0), 60000.0), (utc(11, 8, 5), 60100.0)],
                          "AAPL": [(utc(9, 19, 50), 99.0), (utc(9, 19, 55), 100.0)]},
                         closes={"AAPL": [(date(2026, 10, 8), 95.0)]})
        self.assertEqual(h.session_date, date(2026, 10, 11))
        self.assertEqual(h.days, [utc(11, 8, 0), utc(11, 8, 5), NOW])
        self.assertEqual(h.holdings_value, [D("120100.0"), D("120300.0"), D("120300.0")])

    def test_7d_keeps_the_week_with_hourly_buckets(self):
        self.hold("AAPL", "1")
        h = self.history({"AAPL": [(utc(3, 14, 30), 90.0), (utc(5, 14, 30), 95.0), (utc(9, 19, 30), 100.0)]},
                         key="7d")
        self.assertIsNone(h.session_date)
        self.assertEqual(h.days, [utc(5, 14), utc(9, 19), NOW])  # 3 Oct is before now - 7 days
        self.assertEqual(h.holdings_value, [D("95.0"), D("100.0"), D("100.0")])

    def test_intraday_cash_only_portfolio_has_points(self):
        CashAccount.objects.create(user=self.user, name="Bank", currency="EUR", opening_balance=D("1000"))
        h = self.history({})
        self.assertGreaterEqual(len(h.days), 2)
        self.assertEqual(h.days[-1], NOW)
        self.assertTrue(all(v == D("1000") for v in h.net_worth))

    def test_cash_follows_the_ledger_by_local_date(self):
        bank = CashAccount.objects.create(user=self.user, name="Bank", currency="EUR", opening_balance=D("1000"))
        save_transaction(Transaction(user=self.user, tx_type="withdraw", cash_account=bank, amount=D("100"),
                                     date=date(2026, 10, 7)))
        h = self.history({}, key="7d")
        self.assertEqual(h.cash, [D("1000"), D("900")])  # window start (4 Oct), now

    def test_intraday_missing_fx_is_excluded_and_reported(self):
        self.hold("AAPL", "1", currency="USD")
        h = self.history({"AAPL": [(utc(9, 13, 30), 100.0)]}, quotes={"AAPL": quote("AAPL", "USD", price=None)})
        self.assertTrue(all(v == 0 for v in h.holdings_value))
        self.assertEqual(h.missing_currencies, ["USD"])

    def test_intraday_uses_current_rate(self):
        self.hold("AAPL", "1", currency="USD")
        h = self.history({"AAPL": [(utc(9, 13, 30), 100.0)]}, quotes={"AAPL": quote("AAPL", "USD", price=None)},
                         rates=lambda f, t: 0.9)
        self.assertEqual(h.holdings_value, [D("90.0")])

    def test_intraday_last_point_is_live(self):
        self.hold("BTC-EUR", "2")
        h = self.history({"BTC-EUR": [(utc(11, 8), 100.0)]}, quotes={"BTC-EUR": quote("BTC-EUR", "EUR", price=110.0)})
        self.assertEqual(h.days, [utc(11, 8), NOW])  # today's session reaches `now`
        self.assertEqual(h.holdings_value, [D("200.0"), D("220.0")])
        self.assertEqual(h.holdings[0].last_price, D("110.0"))

    def test_1d_weekend_ends_on_last_session_bucket_at_live_quote(self):
        self.hold("AAPL", "2")
        h = self.history({"AAPL": [(utc(9, 19, 50), 99.0), (utc(9, 19, 55), 100.0)]},
                         quotes={"AAPL": quote("AAPL", "EUR", price=101.0)})
        self.assertEqual(h.session_date, date(2026, 10, 9))
        self.assertEqual(h.days, [utc(9, 19, 50), utc(9, 19, 55)])  # no point on Sunday
        self.assertEqual(h.holdings_value, [D("198.0"), D("202.0")])  # last Friday bucket at the live quote
        self.assertEqual(h.end, date(2026, 10, 9))

    def test_1d_before_first_bar_uses_previous_close_not_same_day(self):
        self.hold("BTC-EUR", "1")
        self.hold("ETH-EUR", "1")
        h = self.history({"BTC-EUR": [(utc(11, 8), 100.0), (utc(11, 10), 100.0)],
                          "ETH-EUR": [(utc(11, 10), 30.0)]},
                         closes={"ETH-EUR": [(date(2026, 10, 10), 20.0),
                                             (date(2026, 10, 11), 29.0)]})  # today's partial daily row
        self.assertEqual(h.days, [utc(11, 8), utc(11, 10), NOW])
        # 08:00: ETH has no bar yet -> Saturday's close (20), not today's row (29)
        self.assertEqual(h.holdings_value, [D("120.0"), D("130.0"), D("130.0")])

    def test_7d_fund_without_bars_steps_with_daily_closes(self):
        self.hold("AAPL", "1")
        self.hold("FUND", "2")
        h = self.history({"AAPL": [(utc(5, 14, 30), 100.0), (utc(7, 14, 30), 100.0), (utc(10, 14, 30), 100.0)]},
                         closes={"FUND": [(date(2026, 10, 3), 40.0), (date(2026, 10, 6), 45.0),
                                          (date(2026, 10, 9), 50.0)]}, key="7d")
        self.assertEqual(h.days, [utc(5, 14), utc(7, 14), utc(10, 14), NOW])
        self.assertEqual(h.holdings_value, [D("180.0"), D("190.0"), D("200.0"), D("200.0")])
        self.assertEqual(h.missing_prices, [])

    def test_no_price_at_all_is_valued_at_cost_and_reported(self):
        self.hold("AAPL", "2", avg="40")
        h = self.history({})
        self.assertEqual(h.missing_prices, ["AAPL"])
        self.assertTrue(all(v == D("80") for v in h.holdings_value))

    def test_holding_not_held_in_the_window_is_left_out(self):
        aapl = self.hold("AAPL", "0")
        save_transaction(Transaction(user=self.user, tx_type="buy", holding=aapl, quantity=D("1"),
                                     price=D("1"), date=date(2026, 9, 1)))
        save_transaction(Transaction(user=self.user, tx_type="sell", holding=aapl, quantity=D("1"),
                                     price=D("1"), date=date(2026, 9, 2)))
        h = self.history({"AAPL": [(utc(9, 13, 30), 100.0)]})
        self.assertEqual(h.holdings, [])
        self.assertEqual(h.session_date, date(2026, 10, 11))  # AAPL's Friday session doesn't count
        self.assertEqual(h.days, [utc(10, 22), NOW])  # no bars: local midnight (UTC+2) to now

    def test_only_the_users_own_data(self):
        bob = User.objects.create_user("bob")
        Holding.objects.create(user=bob, ticker="AAPL", currency="EUR", opening_quantity=D("100"))
        h = self.history({"AAPL": [(utc(9, 13, 30), 100.0)]})
        self.assertEqual(h.holdings, [])
        self.assertTrue(all(v == 0 for v in h.net_worth))


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

    def test_live_last_price(self):
        self.aapl.opening_quantity = D("1")
        self.aapl.save()
        hist = fake_history({"AAPL": [(day(0), 50.0)]})
        h = holding_history(self.aapl, day(0), day(2), history_fn=hist, quote=quote("AAPL", "USD", 55.0),
                            today=day(2))
        self.assertEqual(h.price, [D("50.0"), D("50.0"), D("55.0")])
        h = holding_history(self.aapl, day(0), day(2), history_fn=hist, quote=quote("AAPL", "USD", 55.0),
                            today=day(3))
        self.assertEqual(h.price[-1], D("50.0"))

    def test_holding_intraday_trades_in_window(self):
        save_transaction(Transaction(user=self.user, tx_type="buy", holding=self.aapl, quantity=D("1"),
                                     price=D("90"), date=date(2026, 10, 1)))  # before the 7-day window
        save_transaction(Transaction(user=self.user, tx_type="buy", holding=self.aapl, quantity=D("1"),
                                     price=D("110"), date=date(2026, 10, 8)))
        h = holding_history_intraday(
            self.aapl, "7d", now=NOW, quote=quote("AAPL", "USD", 120.0),
            intraday_fn=fake_intraday({"AAPL": [(utc(6, 14, 30), 100.0), (utc(9, 19, 30), 105.0)]}),
            history_fn=fake_history({}))
        self.assertTrue(h.intraday)
        self.assertEqual(h.days, [utc(6, 14), utc(9, 19), NOW])
        self.assertEqual(h.price, [D("100.0"), D("105.0"), D("120.0")])
        self.assertEqual(h.quantity, [D("1"), D("2"), D("2")])
        self.assertEqual(h.avg_cost, [D("90"), D("100"), D("100")])
        self.assertEqual([t.date for t in h.trades], [date(2026, 10, 8)])

    def test_holding_intraday_1d_past_session(self):
        h = holding_history_intraday(
            self.aapl, "1d", now=NOW, quote=quote("AAPL", "USD", None),
            intraday_fn=fake_intraday({"AAPL": [(utc(9, 13, 35), 100.0)]}),
            history_fn=fake_history({"AAPL": [(date(2026, 10, 8), 98.0)]}))
        self.assertEqual(h.session_date, date(2026, 10, 9))
        self.assertEqual(h.days, [utc(9, 13, 35)])  # Friday session viewed on Sunday: no `now` point
        self.assertEqual(h.price, [D("100.0")])
        self.assertEqual(h.avg_cost, [None])  # nothing held

    def test_holding_intraday_without_bars_uses_daily_closes(self):
        vod = Holding.objects.create(user=self.user, ticker="VOD.L", currency="GBP", opening_quantity=D("1"))
        h = holding_history_intraday(
            vod, "7d", now=NOW, quote=quote("VOD.L", "GBp", None),
            intraday_fn=fake_intraday({}),
            history_fn=fake_history({"VOD.L": [(date(2026, 10, 3), 9800.0), (date(2026, 10, 9), 10100.0)]}))
        self.assertEqual(h.price_currency, "GBP")
        # window start (4 Oct): close before the window; now: the 9 Oct close inside it (pence -> pounds)
        self.assertEqual(h.price, [D("98.0"), D("101.0")])

    def test_average_cost_not_comparable_across_currencies(self):
        h = holding_history(self.aapl, day(0), day(1), history_fn=fake_history({}), quote=quote("AAPL", "EUR"))
        self.assertFalse(h.avg_cost_comparable)


@mock.patch("common.market_data.get_quote", return_value=quote("AAPL", "EUR"))
@mock.patch("common.market_data.get_quotes", return_value={})
@mock.patch("common.market_data.get_intraday_history", return_value=[])
@mock.patch("common.market_data.get_price_history", return_value=[])
@mock.patch("common.market_data.get_fx_rate", return_value=None)
class HistoryForRangeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        CashAccount.objects.create(user=self.user, name="Bank", currency="EUR", opening_balance=D("5"))

    def test_daily_range_ends_today(self, *mocks):
        h = history_for_range(self.user, "1m")
        self.assertFalse(h.intraday)
        self.assertEqual(h.end, timezone.localdate())
        self.assertEqual(h.days[-1], h.end)
        self.assertEqual(len(h.days), 31)

    def test_unknown_key_uses_default(self, *mocks):
        h = history_for_range(self.user, "bogus")
        self.assertEqual(len(h.days), 183)

    def test_intraday_keys(self, _fx, _history, intraday, *mocks):
        h = history_for_range(self.user, "1d")
        self.assertTrue(h.intraday)
        self.assertEqual(h.net_worth[-1], D("5"))
        holding = Holding.objects.create(user=self.user, ticker="AAPL", currency="EUR", opening_quantity=D("1"))
        h = history_for_range(self.user, "7d", holding=holding)
        self.assertTrue(h.intraday)
        self.assertIsInstance(h, HoldingHistory)
        intraday.assert_called_with("AAPL", "7d")
        h = history_for_range(self.user, "3m", holding=holding, earliest=date(2020, 1, 1))
        self.assertFalse(h.intraday)
        self.assertIsInstance(h, HoldingHistory)


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
        self.assertContains(resp, "500,00 €")
        self.assertContains(resp, 'aria-current="page">1M<')
        self.assertContains(resp, 'data-stat="net_worth"')
        self.assertContains(resp, "js/charts.js")
        self.assertNotContains(resp, "new Chart(")
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
        self.assertContains(resp, f'data-holding="{holding.pk}"')
        self.assertContains(resp, 'data-stat="change_pct"')
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


@mock.patch("common.market_data.get_intraday_history", return_value=[])
@mock.patch("common.market_data.get_quote", return_value=quote("AAPL", "EUR"))
@mock.patch("common.market_data.get_quotes", return_value={})
@mock.patch("common.market_data.get_price_history", return_value=[])
@mock.patch("common.market_data.get_fx_rate", return_value=None)
class ChartDataViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.client.force_login(self.user)

    def url(self, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return reverse("performance:chart_data") + "?" + query

    def test_portfolio_json(self, *mocks):
        CashAccount.objects.create(user=self.user, name="Bank", currency="EUR", opening_balance=D("500"))
        data = self.client.get(self.url(range="1m")).json()
        self.assertEqual(data["range"], "1m")
        self.assertFalse(data["intraday"])
        self.assertIsNone(data["session_date"])
        self.assertEqual(data["currency"], "EUR")
        self.assertEqual(len(data["labels"]), 31)
        self.assertEqual(data["series"]["net_worth"][-1], 500.0)
        self.assertEqual(data["stats"]["net_worth"], "500,00 €")
        self.assertEqual(data["stats"]["direction"], "flat")
        self.assertEqual(set(data["stats"]), {"net_worth", "change", "change_pct", "direction",
                                              "holdings_value", "invested", "unrealized_gain"})

    def test_intraday_json(self, _fx, _history, _quotes, _quote, intraday):
        holding = Holding.objects.create(user=self.user, ticker="AAPL", currency="EUR", opening_quantity=D("2"))
        now = timezone.now()
        intraday.return_value = [(now - timedelta(minutes=30), 10.0), (now - timedelta(minutes=10), 11.0)]
        data = self.client.get(self.url(range="1d")).json()
        self.assertTrue(data["intraday"])
        self.assertIsNotNone(data["session_date"])
        self.assertEqual(data["range"], "1d")
        self.assertGreaterEqual(len(data["labels"]), 2)
        self.assertIn("T", data["labels"][0])
        self.assertEqual(data["stats"]["direction"], "up")
        self.assertEqual(holding.ticker, "AAPL")

    def test_holding_json_and_other_users_holding_404(self, _fx, history, *mocks):
        history.return_value = [(date.today() - timedelta(days=3), 10.0)]
        holding = Holding.objects.create(user=self.user, ticker="AAPL", currency="EUR")
        save_transaction(Transaction(user=self.user, tx_type="buy", holding=holding, quantity=D("1"),
                                     price=D("9"), date=date.today() - timedelta(days=2)))
        data = self.client.get(self.url(range="1m", holding=holding.pk)).json()
        self.assertEqual(set(data["series"]), {"price", "avg_cost"})
        self.assertEqual(data["trades"], [{"x": (date.today() - timedelta(days=2)).isoformat(), "y": 9.0,
                                           "type": "buy", "quantity": 1.0}])
        self.assertEqual(set(data["stats"]), {"last_price", "change_pct", "direction"})
        bob = User.objects.create_user("bob")
        theirs = Holding.objects.create(user=bob, ticker="TSLA")
        self.assertEqual(self.client.get(self.url(range="1m", holding=theirs.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url(range="1m", holding="abc")).status_code, 404)

    def test_intraday_trade_markers_snap_to_local_date(self, _fx, _history, _quotes, _quote, intraday):
        holding = Holding.objects.create(user=self.user, ticker="AAPL", currency="EUR")
        now = timezone.now()
        intraday.return_value = [(now - timedelta(minutes=30), 10.0)]
        save_transaction(Transaction(user=self.user, tx_type="buy", holding=holding, quantity=D("1"),
                                     price=D("9"), date=timezone.localdate()))
        data = self.client.get(self.url(range="1d", holding=holding.pk)).json()
        self.assertEqual(len(data["trades"]), 1)
        self.assertIn(data["trades"][0]["x"], data["labels"])

    def test_unknown_range_falls_back(self, *mocks):
        CashAccount.objects.create(user=self.user, name="Bank", currency="EUR", opening_balance=D("1"))
        self.assertEqual(self.client.get(self.url(range="bogus")).json()["range"], "6m")

    def test_requires_login(self, *mocks):
        self.client.logout()
        self.assertEqual(self.client.get(self.url(range="1m")).status_code, 302)


@mock.patch("common.market_data.get_intraday_history", return_value=[])
@mock.patch("common.market_data.get_quotes", return_value={})
@mock.patch("dashboard.views.get_news_for_tickers", return_value=[])
@mock.patch("dashboard.views.get_quotes", return_value={})
@mock.patch("dashboard.views.history_for_range")
class DashboardChartTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("alice")
        get_profile(self.user)
        self.client.force_login(self.user)
        CashAccount.objects.create(user=self.user, name="Bank", currency="EUR", opening_balance=D("100"))

    def history(self):
        return PortfolioHistory(
            base_currency="EUR", start=day(0), end=day(1), days=[day(0), day(1)],
            net_worth=[D("100"), D("110")], holdings_value=[D("0"), D("0")],
            invested=[D("0"), D("0")], cash=[D("100"), D("110")])

    def test_dashboard_chart_uses_portfolio_history(self, hist, *mocks):
        hist.return_value = self.history()
        resp = self.client.get(reverse("dashboard:home"))
        hist.assert_called_once_with(self.user, "1m", earliest=mock.ANY)
        self.assertContains(resp, 'id="networth-data"')
        self.assertEqual(resp.context["chart"]["series"]["net_worth"], [100.0, 110.0])
        self.assertContains(resp, "+10,00")  # change over the range, from history stats
        self.assertContains(resp, 'data-stat="change"')
        self.assertContains(resp, 'id="allocation-data"')
        self.assertContains(resp, "js/charts.js")
        self.assertEqual(NetWorthSnapshot.objects.filter(user=self.user).count(), 1)  # still recorded

    def test_dashboard_range_param(self, hist, *mocks):
        hist.return_value = self.history()
        resp = self.client.get(reverse("dashboard:home") + "?range=7d")
        hist.assert_called_once_with(self.user, "7d", earliest=mock.ANY)
        self.assertContains(resp, 'aria-current="page">7D<')
        self.assertEqual(resp.context["range_key"], "7d")

    def test_dashboard_earliest_is_first_transaction(self, hist, *mocks):
        hist.return_value = self.history()
        holding = Holding.objects.create(user=self.user, ticker="AAPL", currency="EUR")
        save_transaction(Transaction(user=self.user, tx_type="buy", holding=holding,
                                     cash_account=CashAccount.objects.get(), quantity=D("1"),
                                     price=D("5"), date=date(2024, 3, 4)))
        self.client.get(reverse("dashboard:home"))
        self.assertEqual(hist.call_args.kwargs["earliest"], date(2024, 3, 4))

    def test_movers_ranked_and_deduped(self, hist, _dq, _news, _mq, _intraday):
        hist.return_value = self.history()
        WatchlistItem.objects.create(user=self.user, ticker="AAA")
        WatchlistItem.objects.create(user=self.user, ticker="BBB")
        q = lambda t, p: Quote(ticker=t, name=None, price=1.0, previous_close=None,  # noqa: E731
                               currency="EUR", change=None, change_pct=p)
        with mock.patch("dashboard.views.get_quotes", return_value={"AAA": q("AAA", 2.0), "BBB": q("BBB", -3.0)}):
            resp = self.client.get(reverse("dashboard:home"))
        self.assertEqual([m["ticker"] for m in resp.context["movers"]], ["AAA", "BBB"])
        self.assertContains(resp, "BBB")

    def test_empty_history_renders_empty_state(self, hist, *mocks):
        hist.return_value = PortfolioHistory(base_currency="EUR", start=day(0), end=day(0))
        resp = self.client.get(reverse("dashboard:home"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Nothing to chart yet")
