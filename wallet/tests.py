from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from common.market_data import Quote

from .models import CashAccount, Holding, NetWorthSnapshot, Transaction
from .services import apply_transaction, net_worth_summary, record_snapshot

D = Decimal
User = get_user_model()


def make_quote(ticker, price):
    return Quote(ticker=ticker, name=None, price=price, previous_close=None,
                 currency="USD", change=None, change_pct=None)


class ApplyTransactionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.cash = CashAccount.objects.create(user=self.user, name="Broker", balance=D("1000.00"))
        self.holding = Holding.objects.create(user=self.user, ticker="AAPL", quantity=D("0"), avg_cost=D("0"))

    def apply(self, **kwargs):
        kwargs.setdefault("date", date(2026, 1, 1))
        kwargs.setdefault("user", self.user)
        tx = Transaction.objects.create(**kwargs)
        apply_transaction(tx)
        self.cash.refresh_from_db()
        self.holding.refresh_from_db()
        return tx

    def test_buy_into_empty_position(self):
        self.apply(tx_type="buy", holding=self.holding, cash_account=self.cash,
                   quantity=D("10"), price=D("50"), fees=D("5"))
        self.assertEqual(self.holding.quantity, D("10"))
        # Fees are capitalised into the average cost: (10*50 + 5) / 10
        self.assertEqual(self.holding.avg_cost, D("50.5"))
        self.assertEqual(self.cash.balance, D("495.00"))

    def test_buy_averages_with_existing_position(self):
        self.holding.quantity = D("10")
        self.holding.avg_cost = D("100")
        self.holding.save()
        self.apply(tx_type="buy", holding=self.holding, quantity=D("10"), price=D("200"))
        self.assertEqual(self.holding.quantity, D("20"))
        self.assertEqual(self.holding.avg_cost, D("150"))

    def test_buy_without_cash_account_leaves_cash_untouched(self):
        self.apply(tx_type="buy", holding=self.holding, quantity=D("1"), price=D("10"))
        self.assertEqual(self.cash.balance, D("1000.00"))
        self.assertEqual(self.holding.quantity, D("1"))

    def test_sell_reduces_quantity_keeps_avg_cost_and_credits_cash(self):
        self.holding.quantity = D("10")
        self.holding.avg_cost = D("40")
        self.holding.save()
        self.apply(tx_type="sell", holding=self.holding, cash_account=self.cash,
                   quantity=D("4"), price=D("60"), fees=D("2"))
        self.assertEqual(self.holding.quantity, D("6"))
        self.assertEqual(self.holding.avg_cost, D("40"))
        self.assertEqual(self.cash.balance, D("1238.00"))  # 1000 + 4*60 - 2

    def test_oversell_is_clamped_to_zero(self):
        # Current behaviour: selling more than held floors quantity at 0
        # but still credits the full proceeds. See README "next steps".
        self.holding.quantity = D("2")
        self.holding.save()
        self.apply(tx_type="sell", holding=self.holding, cash_account=self.cash,
                   quantity=D("5"), price=D("10"))
        self.assertEqual(self.holding.quantity, D("0"))
        self.assertEqual(self.cash.balance, D("1050.00"))

    def test_deposit_withdraw_dividend(self):
        self.apply(tx_type="deposit", cash_account=self.cash, amount=D("250.50"))
        self.assertEqual(self.cash.balance, D("1250.50"))
        self.apply(tx_type="withdraw", cash_account=self.cash, amount=D("100.25"))
        self.assertEqual(self.cash.balance, D("1150.25"))
        self.apply(tx_type="dividend", cash_account=self.cash, amount=D("9.75"))
        self.assertEqual(self.cash.balance, D("1160.00"))

    def test_cash_tx_without_amount_is_a_noop(self):
        self.apply(tx_type="deposit", cash_account=self.cash)
        self.assertEqual(self.cash.balance, D("1000.00"))

    def test_holding_tx_without_holding_is_a_noop(self):
        self.apply(tx_type="buy", cash_account=self.cash, quantity=D("1"), price=D("10"))
        self.assertEqual(self.cash.balance, D("1000.00"))

    def test_rolls_back_when_save_fails(self):
        self.holding.quantity = D("1")
        self.holding.save()
        tx = Transaction.objects.create(user=self.user, tx_type="sell", holding=self.holding, cash_account=self.cash,
                                        quantity=D("1"), price=D("10"), date=date(2026, 1, 1))
        with mock.patch.object(CashAccount, "save", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                apply_transaction(tx)
        self.holding.refresh_from_db()
        self.assertEqual(self.holding.quantity, D("1"))

    def test_rejects_another_users_cash_account(self):
        bob = User.objects.create_user("bob")
        bobs_cash = CashAccount.objects.create(user=bob, name="Bob", balance=D("10.00"))
        tx = Transaction.objects.create(user=self.user, tx_type="deposit", cash_account=bobs_cash,
                                        amount=D("5"), date=date(2026, 1, 1))
        with self.assertRaises(ValueError):
            apply_transaction(tx)
        bobs_cash.refresh_from_db()
        self.assertEqual(bobs_cash.balance, D("10.00"))


@mock.patch("common.market_data.get_quotes")
class NetWorthSummaryTests(TestCase):
    def setUp(self):
        self.user = u = User.objects.create_user("alice")
        CashAccount.objects.create(user=u, name="Bank", balance=D("500.00"))
        CashAccount.objects.create(user=u, name="Broker", balance=D("250.00"))
        self.aapl = Holding.objects.create(user=u, ticker="AAPL", asset_type="stock",
                                           quantity=D("10"), avg_cost=D("100"))
        self.voo = Holding.objects.create(user=u, ticker="VOO", asset_type="etf",
                                          quantity=D("2"), avg_cost=D("400"))
        Holding.objects.create(user=u, ticker="SOLD", quantity=D("0"), avg_cost=D("10"))
        # Another user's data must never leak into alice's numbers.
        bob = User.objects.create_user("bob")
        CashAccount.objects.create(user=bob, name="Bob bank", balance=D("99999.00"))
        Holding.objects.create(user=bob, ticker="TSLA", quantity=D("100"), avg_cost=D("1"))

    def test_uses_live_prices(self, get_quotes):
        get_quotes.return_value = {"AAPL": make_quote("AAPL", 120.0), "VOO": make_quote("VOO", 500.0)}
        s = net_worth_summary(self.user)

        get_quotes.assert_called_once()
        self.assertCountEqual(get_quotes.call_args.args[0], ["AAPL", "VOO"])  # zero qty skipped
        self.assertEqual(s["cash_total"], D("750.00"))
        self.assertEqual(s["holdings_value"], D("2200"))
        self.assertEqual(s["net_worth"], D("2950.00"))
        self.assertEqual(s["allocation"], {"stock": D("1200"), "etf": D("1000"), "cash": D("750.00")})

        aapl = next(r for r in s["rows"] if r["holding"] == self.aapl)
        self.assertEqual(aapl["gain"], D("200"))
        self.assertEqual(aapl["gain_pct"], D("20"))

    def test_falls_back_to_cost_basis_without_quote(self, get_quotes):
        get_quotes.return_value = {"AAPL": make_quote("AAPL", None)}
        s = net_worth_summary(self.user)
        self.assertEqual(s["holdings_value"], D("1800"))
        for row in s["rows"]:
            self.assertEqual(row["gain"], D("0"))

    def test_zero_cost_basis_has_no_gain_pct(self, get_quotes):
        self.aapl.avg_cost = D("0")
        self.aapl.save()
        get_quotes.return_value = {"AAPL": make_quote("AAPL", 10.0)}
        s = net_worth_summary(self.user)
        aapl = next(r for r in s["rows"] if r["holding"] == self.aapl)
        self.assertIsNone(aapl["gain_pct"])

    def test_cash_excluded_from_allocation_when_not_positive(self, get_quotes):
        get_quotes.return_value = {}
        CashAccount.objects.filter(user=self.user).update(balance=D("0"))
        self.assertNotIn("cash", net_worth_summary(self.user)["allocation"])

    def test_empty_wallet(self, get_quotes):
        get_quotes.return_value = {}
        s = net_worth_summary(User.objects.create_user("carol"))
        self.assertEqual(s["net_worth"], D("0"))
        self.assertEqual(s["rows"], [])
        self.assertEqual(s["allocation"], {})


class RecordSnapshotTests(TestCase):
    summary = {"cash_total": D("10"), "holdings_value": D("20"), "net_worth": D("30")}

    def setUp(self):
        self.user = User.objects.create_user("alice")

    def test_creates_then_updates_todays_snapshot(self):
        record_snapshot(self.user, self.summary)
        record_snapshot(self.user, {**self.summary, "net_worth": D("99")})
        self.assertEqual(NetWorthSnapshot.objects.count(), 1)
        snap = NetWorthSnapshot.objects.get()
        self.assertEqual(snap.date, date.today())
        self.assertEqual(snap.net_worth, D("99"))

    def test_each_user_gets_their_own_daily_snapshot(self):
        bob = User.objects.create_user("bob")
        record_snapshot(self.user, self.summary)
        record_snapshot(bob, {**self.summary, "net_worth": D("5")})
        self.assertEqual(NetWorthSnapshot.objects.count(), 2)
        self.assertEqual(NetWorthSnapshot.objects.get(user=self.user).net_worth, D("30"))

    @mock.patch("wallet.services.net_worth_summary")
    def test_computes_summary_when_not_given(self, summary_fn):
        summary_fn.return_value = self.summary
        self.assertEqual(record_snapshot(self.user).net_worth, D("30"))
        summary_fn.assert_called_once_with(self.user)


class AddTransactionViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.client.force_login(self.user)

    def test_valid_post_records_and_applies_transaction(self):
        cash = CashAccount.objects.create(user=self.user, name="Bank", balance=D("100.00"))
        resp = self.client.post(reverse("wallet:add_transaction"), {
            "tx_type": "deposit", "cash_account": cash.pk, "amount": "50.00",
            "fees": "0", "date": "2026-01-01",
        })
        self.assertRedirects(resp, reverse("wallet:home"), fetch_redirect_response=False)
        self.assertEqual(Transaction.objects.get().user, self.user)
        cash.refresh_from_db()
        self.assertEqual(cash.balance, D("150.00"))

    def test_invalid_post_does_not_touch_balances(self):
        cash = CashAccount.objects.create(user=self.user, name="Bank", balance=D("100.00"))
        resp = self.client.post(reverse("wallet:add_transaction"), {
            "tx_type": "deposit", "cash_account": cash.pk, "amount": "50.00",
        })  # missing date
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Transaction.objects.count(), 0)
        cash.refresh_from_db()
        self.assertEqual(cash.balance, D("100.00"))

    def test_cannot_post_to_another_users_cash_account(self):
        bob = User.objects.create_user("bob")
        bobs_cash = CashAccount.objects.create(user=bob, name="Bob", balance=D("100.00"))
        resp = self.client.post(reverse("wallet:add_transaction"), {
            "tx_type": "deposit", "cash_account": bobs_cash.pk, "amount": "50.00",
            "fees": "0", "date": "2026-01-01",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn("cash_account", resp.context["form"].errors)
        self.assertEqual(Transaction.objects.count(), 0)
        bobs_cash.refresh_from_db()
        self.assertEqual(bobs_cash.balance, D("100.00"))

    def test_form_only_offers_own_holdings_and_accounts(self):
        bob = User.objects.create_user("bob")
        CashAccount.objects.create(user=bob, name="Bob", balance=D("1"))
        Holding.objects.create(user=bob, ticker="TSLA")
        mine = Holding.objects.create(user=self.user, ticker="AAPL")
        form = self.client.get(reverse("wallet:add_transaction")).context["form"]
        self.assertEqual(list(form.fields["holding"].queryset), [mine])
        self.assertEqual(list(form.fields["cash_account"].queryset), [])
