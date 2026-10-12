from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse

from common.market_data import Quote

from . import ledger
from .forms import CashAccountForm, HoldingForm, TransactionForm
from .models import CashAccount, Holding, NetWorthSnapshot, Transaction
from .services import (
    delete_transaction,
    net_worth_summary,
    recalculate_holding,
    record_snapshot,
    save_transaction,
    set_cash_balance,
    set_position,
)

D = Decimal
User = get_user_model()


def make_quote(ticker, price):
    return Quote(ticker=ticker, name=None, price=price, previous_close=None,
                 currency="USD", change=None, change_pct=None)


def tx(tx_type, quantity=None, price=None, amount=None, fees=D("0")):
    return SimpleNamespace(tx_type=tx_type, quantity=quantity, price=price, amount=amount, fees=fees)


class LedgerTests(TestCase):
    """The pure replay/solve functions, independent of the database."""

    HISTORY = [
        tx("buy", D("10"), D("100"), fees=D("10")),
        tx("sell", D("4"), D("120"), fees=D("2")),
        tx("buy", D("6"), D("90")),
        tx("dividend", amount=D("15")),
        tx("deposit", amount=D("500")),
        tx("withdraw", amount=D("200")),
    ]

    def test_cash_effects(self):
        self.assertEqual(ledger.cash_effect(tx("buy", D("2"), D("10"), fees=D("1"))), D("-21"))
        self.assertEqual(ledger.cash_effect(tx("sell", D("2"), D("10"), fees=D("1"))), D("19"))
        self.assertEqual(ledger.cash_effect(tx("deposit", amount=D("5"))), D("5"))
        self.assertEqual(ledger.cash_effect(tx("dividend", amount=D("5"))), D("5"))
        self.assertEqual(ledger.cash_effect(tx("withdraw", amount=D("5"))), D("-5"))
        self.assertEqual(ledger.cash_effect(tx("deposit")), D("0"))

    def test_replay_position(self):
        qty, avg = ledger.replay_position(D("0"), D("0"), self.HISTORY)
        # buy 10 @ 101 effective, sell 4 (avg stays 101), buy 6 @ 90 -> (6*101 + 540) / 12
        self.assertEqual(qty, D("12"))
        self.assertEqual(avg, (D("6") * D("101") + D("540")) / D("12"))

    def test_replay_cash(self):
        # -1010 + 478 - 540 + 15 + 500 - 200
        self.assertEqual(ledger.replay_cash(D("1000"), self.HISTORY), D("243"))

    def test_oversell_is_clamped_to_zero(self):
        # Current behaviour, to be replaced by validation in a later step.
        qty, _ = ledger.replay_position(D("2"), D("10"), [tx("sell", D("5"), D("1"))])
        self.assertEqual(qty, D("0"))

    def test_solve_opening_balance_round_trips(self):
        opening = ledger.solve_opening_balance(D("243"), self.HISTORY)
        self.assertEqual(opening, D("1000"))

    def test_solve_opening_position_round_trips(self):
        for opening_qty, opening_avg in [(D("5"), D("80")), (D("1"), D("0.5")), (D("100"), D("3.25"))]:
            with self.subTest(opening_qty=opening_qty, opening_avg=opening_avg):
                qty, avg = ledger.replay_position(opening_qty, opening_avg, self.HISTORY)
                solved_qty, solved_avg = ledger.solve_opening_position(qty, avg, self.HISTORY)
                self.assertEqual(solved_qty, opening_qty)
                self.assertAlmostEqual(solved_avg, opening_avg, places=12)

    def test_solve_opening_position_without_history(self):
        self.assertEqual(ledger.solve_opening_position(D("3"), D("7"), []), (D("3"), D("7")))

    def test_solve_opening_position_when_history_explains_everything(self):
        qty, avg = ledger.replay_position(D("0"), D("0"), self.HISTORY)
        self.assertEqual(ledger.solve_opening_position(qty, avg, self.HISTORY), (D("0"), D("0")))


class TransactionServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.cash = CashAccount.objects.create(user=self.user, name="Broker", opening_balance=D("1000.00"))
        self.holding = Holding.objects.create(user=self.user, ticker="AAPL")
        self.refresh()

    def refresh(self):
        for obj in (self.cash, self.holding):
            obj.refresh_from_db()

    def record(self, **kwargs):
        kwargs.setdefault("date", date(2026, 1, 1))
        kwargs.setdefault("user", self.user)
        t = save_transaction(Transaction(**kwargs))
        self.refresh()
        return t

    def set_opening_position(self, quantity, avg_cost):
        self.holding.opening_quantity, self.holding.opening_avg_cost = quantity, avg_cost
        self.holding.save()
        recalculate_holding(self.holding)

    def test_buy_into_empty_position(self):
        self.record(tx_type="buy", holding=self.holding, cash_account=self.cash,
                    quantity=D("10"), price=D("50"), fees=D("5"))
        self.assertEqual(self.holding.quantity, D("10"))
        # Fees are capitalised into the average cost: (10*50 + 5) / 10
        self.assertEqual(self.holding.avg_cost, D("50.5"))
        self.assertEqual(self.cash.balance, D("495.00"))

    def test_buy_averages_with_opening_position(self):
        self.set_opening_position(D("10"), D("100"))
        self.record(tx_type="buy", holding=self.holding, quantity=D("10"), price=D("200"))
        self.assertEqual(self.holding.quantity, D("20"))
        self.assertEqual(self.holding.avg_cost, D("150"))

    def test_buy_without_cash_account_leaves_cash_untouched(self):
        self.record(tx_type="buy", holding=self.holding, quantity=D("1"), price=D("10"))
        self.assertEqual(self.cash.balance, D("1000.00"))
        self.assertEqual(self.holding.quantity, D("1"))

    def test_sell_reduces_quantity_keeps_avg_cost_and_credits_cash(self):
        self.set_opening_position(D("10"), D("40"))
        self.record(tx_type="sell", holding=self.holding, cash_account=self.cash,
                    quantity=D("4"), price=D("60"), fees=D("2"))
        self.assertEqual(self.holding.quantity, D("6"))
        self.assertEqual(self.holding.avg_cost, D("40"))
        self.assertEqual(self.cash.balance, D("1238.00"))  # 1000 + 4*60 - 2

    def test_deposit_withdraw_dividend(self):
        self.record(tx_type="deposit", cash_account=self.cash, amount=D("250.50"))
        self.assertEqual(self.cash.balance, D("1250.50"))
        self.record(tx_type="withdraw", cash_account=self.cash, amount=D("100.25"))
        self.assertEqual(self.cash.balance, D("1150.25"))
        self.record(tx_type="dividend", cash_account=self.cash, amount=D("9.75"))
        self.assertEqual(self.cash.balance, D("1160.00"))

    def test_editing_a_transaction_recalculates(self):
        t = self.record(tx_type="buy", holding=self.holding, cash_account=self.cash,
                        quantity=D("10"), price=D("50"))
        t.quantity = D("4")
        save_transaction(t)
        self.refresh()
        self.assertEqual(self.holding.quantity, D("4"))
        self.assertEqual(self.cash.balance, D("800.00"))

    def test_moving_a_transaction_to_another_account_fixes_both(self):
        savings = CashAccount.objects.create(user=self.user, name="Savings", opening_balance=D("0"))
        t = self.record(tx_type="deposit", cash_account=self.cash, amount=D("100"))
        t.cash_account = savings
        save_transaction(t)
        self.refresh()
        savings.refresh_from_db()
        self.assertEqual(self.cash.balance, D("1000.00"))
        self.assertEqual(savings.balance, D("100.00"))

    def test_deleting_a_transaction_reverts_its_effect(self):
        self.record(tx_type="buy", holding=self.holding, cash_account=self.cash, quantity=D("2"), price=D("10"))
        t = self.record(tx_type="buy", holding=self.holding, cash_account=self.cash, quantity=D("3"), price=D("20"))
        delete_transaction(t)
        self.refresh()
        self.assertEqual(self.holding.quantity, D("2"))
        self.assertEqual(self.holding.avg_cost, D("10"))
        self.assertEqual(self.cash.balance, D("980.00"))

    def test_backdated_transaction_is_replayed_in_date_order(self):
        self.record(tx_type="buy", holding=self.holding, quantity=D("8"), price=D("10"), date=date(2026, 2, 1))
        self.record(tx_type="sell", holding=self.holding, quantity=D("5"), price=D("30"), date=date(2026, 3, 1))
        # Recorded last but dated first: it must be replayed before the others.
        self.record(tx_type="buy", holding=self.holding, quantity=D("2"), price=D("20"), date=date(2026, 1, 1))
        self.assertEqual(self.holding.quantity, D("5"))
        self.assertEqual(self.holding.avg_cost, D("12"))  # (2*20 + 8*10) / 10

    def test_changing_opening_balance_keeps_transactions(self):
        self.record(tx_type="deposit", cash_account=self.cash, amount=D("50"))
        set_cash_balance(self.cash, D("2000"))
        self.refresh()
        self.assertEqual(self.cash.balance, D("2000.00"))
        self.assertEqual(self.cash.opening_balance, D("1950.00"))
        self.assertEqual(self.cash.transactions.count(), 1)

    def test_set_position_keeps_history(self):
        self.record(tx_type="buy", holding=self.holding, quantity=D("2"), price=D("10"))
        set_position(self.holding, D("5"), D("16"))
        self.refresh()
        self.assertEqual(self.holding.quantity, D("5"))
        self.assertAlmostEqual(self.holding.avg_cost, D("16"), places=6)
        self.assertEqual(self.holding.opening_quantity, D("3"))
        self.assertEqual(self.holding.transactions.count(), 1)

    def test_rolls_back_when_recalculation_fails(self):
        with mock.patch.object(CashAccount, "save", side_effect=RuntimeError("db down")):
            with self.assertRaises(RuntimeError):
                self.record(tx_type="buy", holding=self.holding, cash_account=self.cash,
                            quantity=D("1"), price=D("10"))
        self.refresh()
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(self.holding.quantity, D("0"))

    def test_rejects_another_users_cash_account(self):
        bob = User.objects.create_user("bob")
        bobs_cash = CashAccount.objects.create(user=bob, name="Bob", opening_balance=D("10.00"))
        with self.assertRaises(ValueError):
            save_transaction(Transaction(user=self.user, tx_type="deposit", cash_account=bobs_cash,
                                         amount=D("5"), date=date(2026, 1, 1)))
        self.assertFalse(Transaction.objects.exists())

    def test_holding_with_transactions_cannot_be_deleted(self):
        self.record(tx_type="buy", holding=self.holding, quantity=D("1"), price=D("1"))
        with self.assertRaises(ProtectedError):
            self.holding.delete()

    def test_cash_account_with_transactions_cannot_be_deleted(self):
        self.record(tx_type="deposit", cash_account=self.cash, amount=D("1"))
        with self.assertRaises(ProtectedError):
            self.cash.delete()


class TransactionFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.cash = CashAccount.objects.create(user=self.user, name="Broker")
        self.holding = Holding.objects.create(user=self.user, ticker="AAPL")

    def form(self, **data):
        data.setdefault("date", "2026-01-01")
        data.setdefault("fees", "0")
        return TransactionForm(data, user=self.user)

    def test_buy_requires_holding_quantity_and_price(self):
        form = self.form(tx_type="buy", cash_account=self.cash.pk)
        self.assertFalse(form.is_valid())
        self.assertEqual(set(form.errors), {"holding", "quantity", "price"})

    def test_cash_movements_require_account_and_amount(self):
        for tx_type in ("deposit", "withdraw", "dividend"):
            with self.subTest(tx_type):
                form = self.form(tx_type=tx_type)
                self.assertFalse(form.is_valid())
                self.assertEqual(set(form.errors), {"cash_account", "amount"})

    def test_negative_values_are_rejected(self):
        form = self.form(tx_type="deposit", cash_account=self.cash.pk, amount="-5")
        self.assertFalse(form.is_valid())
        self.assertIn("amount", form.errors)

    def test_valid_buy(self):
        form = self.form(tx_type="buy", holding=self.holding.pk, quantity="1", price="10")
        self.assertTrue(form.is_valid(), form.errors)


@mock.patch("common.market_data.get_quotes")
class NetWorthSummaryTests(TestCase):
    def setUp(self):
        self.user = u = User.objects.create_user("alice")
        CashAccount.objects.create(user=u, name="Bank", opening_balance=D("500.00"))
        CashAccount.objects.create(user=u, name="Broker", opening_balance=D("250.00"))
        self.aapl = Holding.objects.create(user=u, ticker="AAPL", asset_type="stock",
                                           opening_quantity=D("10"), opening_avg_cost=D("100"))
        self.voo = Holding.objects.create(user=u, ticker="VOO", asset_type="etf",
                                          opening_quantity=D("2"), opening_avg_cost=D("400"))
        Holding.objects.create(user=u, ticker="SOLD", opening_quantity=D("0"), opening_avg_cost=D("10"))
        # Another user's data must never leak into alice's numbers.
        bob = User.objects.create_user("bob")
        CashAccount.objects.create(user=bob, name="Bob bank", opening_balance=D("99999.00"))
        Holding.objects.create(user=bob, ticker="TSLA", opening_quantity=D("100"), opening_avg_cost=D("1"))

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
        cash = CashAccount.objects.create(user=self.user, name="Bank", opening_balance=D("100.00"))
        resp = self.client.post(reverse("wallet:add_transaction"), {
            "tx_type": "deposit", "cash_account": cash.pk, "amount": "50.00",
            "fees": "0", "date": "2026-01-01",
        })
        self.assertRedirects(resp, reverse("wallet:home"), fetch_redirect_response=False)
        self.assertEqual(Transaction.objects.get().user, self.user)
        cash.refresh_from_db()
        self.assertEqual(cash.balance, D("150.00"))

    def test_invalid_post_does_not_touch_balances(self):
        cash = CashAccount.objects.create(user=self.user, name="Bank", opening_balance=D("100.00"))
        resp = self.client.post(reverse("wallet:add_transaction"), {
            "tx_type": "deposit", "cash_account": cash.pk, "amount": "50.00",
        })  # missing date
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Transaction.objects.count(), 0)
        cash.refresh_from_db()
        self.assertEqual(cash.balance, D("100.00"))

    def test_cannot_post_to_another_users_cash_account(self):
        bob = User.objects.create_user("bob")
        bobs_cash = CashAccount.objects.create(user=bob, name="Bob", opening_balance=D("100.00"))
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
        CashAccount.objects.create(user=bob, name="Bob", opening_balance=D("1"))
        Holding.objects.create(user=bob, ticker="TSLA")
        mine = Holding.objects.create(user=self.user, ticker="AAPL")
        form = self.client.get(reverse("wallet:add_transaction")).context["form"]
        self.assertEqual(list(form.fields["holding"].queryset), [mine])
        self.assertEqual(list(form.fields["cash_account"].queryset), [])


class EditDeleteTransactionViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.client.force_login(self.user)
        self.cash = CashAccount.objects.create(user=self.user, name="Bank", opening_balance=D("100.00"))
        self.tx = save_transaction(Transaction(user=self.user, tx_type="deposit", cash_account=self.cash,
                                               amount=D("50"), date=date(2026, 1, 1)))

    def test_edit_recalculates_balance(self):
        resp = self.client.post(reverse("wallet:edit_transaction", args=[self.tx.pk]), {
            "tx_type": "withdraw", "cash_account": self.cash.pk, "amount": "30.00",
            "fees": "0", "date": "2026-01-02",
        })
        self.assertRedirects(resp, reverse("wallet:transaction_list"), fetch_redirect_response=False)
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance, D("70.00"))
        self.assertEqual(Transaction.objects.count(), 1)

    def test_delete_recalculates_balance(self):
        url = reverse("wallet:delete_transaction", args=[self.tx.pk])
        self.assertEqual(self.client.get(url).status_code, 200)  # confirmation page
        self.client.post(url)
        self.assertFalse(Transaction.objects.exists())
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance, D("100.00"))

    def test_other_users_transactions_are_not_found(self):
        bob = User.objects.create_user("bob")
        self.client.force_login(bob)
        for name in ("wallet:edit_transaction", "wallet:delete_transaction"):
            with self.subTest(name):
                url = reverse(name, args=[self.tx.pk])
                self.assertEqual(self.client.get(url).status_code, 404)
                self.assertEqual(self.client.post(url).status_code, 404)
        self.assertTrue(Transaction.objects.filter(pk=self.tx.pk).exists())

    def test_holding_with_history_is_not_removed(self):
        holding = Holding.objects.create(user=self.user, ticker="AAPL")
        save_transaction(Transaction(user=self.user, tx_type="buy", holding=holding,
                                     quantity=D("1"), price=D("1"), date=date(2026, 1, 1)))
        resp = self.client.post(reverse("wallet:delete_holding", args=[holding.pk]), follow=True)
        self.assertTrue(Holding.objects.filter(pk=holding.pk).exists())
        self.assertContains(resp, "Delete them first")
        # A buy with no cash account used to crash both transaction tables.
        self.assertContains(self.client.get(reverse("wallet:transaction_list")), "AAPL")


class ConsistencyRuleTests(TestCase):
    """Step 5: no overselling, and no negative cash unless the account allows it."""

    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.cash = CashAccount.objects.create(user=self.user, name="Broker", opening_balance=D("100.00"))
        self.holding = Holding.objects.create(user=self.user, ticker="AAPL")

    def record(self, **kwargs):
        kwargs.setdefault("date", date(2026, 1, 1))
        kwargs.setdefault("user", self.user)
        return save_transaction(Transaction(**kwargs))

    def assertRejected(self, code, fn, *args, **kwargs):
        with self.assertRaises(ValidationError) as ctx:
            fn(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_cannot_sell_more_than_held(self):
        self.record(tx_type="buy", holding=self.holding, quantity=D("3"), price=D("1"))
        exc = self.assertRejected("oversell", self.record, tx_type="sell", holding=self.holding,
                                  quantity=D("5"), price=D("1"), date=date(2026, 1, 2))
        self.assertIn("only hold 3 AAPL", exc.messages[0])
        self.assertEqual(Transaction.objects.count(), 1)

    def test_can_sell_everything(self):
        self.record(tx_type="buy", holding=self.holding, quantity=D("3"), price=D("1"))
        self.record(tx_type="sell", holding=self.holding, quantity=D("3"), price=D("2"), date=date(2026, 1, 2))
        self.holding.refresh_from_db()
        self.assertEqual(self.holding.quantity, D("0"))

    def test_cannot_sell_before_the_purchase_date(self):
        self.record(tx_type="buy", holding=self.holding, quantity=D("3"), price=D("1"), date=date(2026, 2, 1))
        self.assertRejected("oversell", self.record, tx_type="sell", holding=self.holding,
                            quantity=D("1"), price=D("1"), date=date(2026, 1, 1))

    def test_opening_quantity_counts_towards_sells(self):
        holding = Holding.objects.create(user=self.user, ticker="VOO", opening_quantity=D("4"))
        self.record(tx_type="sell", holding=holding, quantity=D("4"), price=D("1"))
        holding.refresh_from_db()
        self.assertEqual(holding.quantity, D("0"))

    def test_cannot_delete_a_buy_a_later_sell_depends_on(self):
        buy = self.record(tx_type="buy", holding=self.holding, quantity=D("3"), price=D("1"))
        self.record(tx_type="sell", holding=self.holding, quantity=D("2"), price=D("1"), date=date(2026, 1, 2))
        self.assertRejected("oversell", delete_transaction, buy)
        self.assertTrue(Transaction.objects.filter(pk=buy.pk).exists())
        self.holding.refresh_from_db()
        self.assertEqual(self.holding.quantity, D("1"))

    def test_cannot_shrink_a_buy_below_what_was_later_sold(self):
        buy = self.record(tx_type="buy", holding=self.holding, quantity=D("3"), price=D("1"))
        self.record(tx_type="sell", holding=self.holding, quantity=D("2"), price=D("1"), date=date(2026, 1, 2))
        buy.quantity = D("1")
        self.assertRejected("oversell", save_transaction, buy)
        buy.refresh_from_db()
        self.assertEqual(buy.quantity, D("3"))

    def test_moving_a_buy_to_another_holding_checks_the_old_one(self):
        other = Holding.objects.create(user=self.user, ticker="MSFT")
        buy = self.record(tx_type="buy", holding=self.holding, quantity=D("3"), price=D("1"))
        self.record(tx_type="sell", holding=self.holding, quantity=D("2"), price=D("1"), date=date(2026, 1, 2))
        buy.holding = other
        self.assertRejected("oversell", save_transaction, buy)

    def test_cash_cannot_go_negative_by_default(self):
        exc = self.assertRejected("negative_balance", self.record, tx_type="withdraw",
                                  cash_account=self.cash, amount=D("150"))
        self.assertIn("-50.00", exc.messages[0])
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance, D("100.00"))

    def test_buy_cannot_overdraw_the_paying_account(self):
        self.assertRejected("negative_balance", self.record, tx_type="buy", holding=self.holding,
                            cash_account=self.cash, quantity=D("2"), price=D("60"))
        self.assertFalse(Transaction.objects.exists())

    def test_negative_balance_allowed_when_account_opts_in(self):
        self.cash.allow_negative_balance = True
        self.cash.save()
        self.record(tx_type="withdraw", cash_account=self.cash, amount=D("150"))
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance, D("-50.00"))

    def test_cannot_delete_a_deposit_that_later_spending_relies_on(self):
        deposit = self.record(tx_type="deposit", cash_account=self.cash, amount=D("100"))
        self.record(tx_type="withdraw", cash_account=self.cash, amount=D("150"), date=date(2026, 1, 2))
        self.assertRejected("negative_balance", delete_transaction, deposit)

    def test_cannot_switch_off_allow_negative_while_history_needs_it(self):
        self.cash.allow_negative_balance = True
        self.cash.save()
        self.record(tx_type="withdraw", cash_account=self.cash, amount=D("150"))
        form = CashAccountForm({"name": "Broker", "currency": "USD", "opening_balance": "100"},
                               instance=self.cash, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("allow negative balance", form.non_field_errors()[0])

    def test_negative_opening_values_are_rejected(self):
        form = CashAccountForm({"name": "X", "currency": "USD", "opening_balance": "-1"}, user=self.user)
        self.assertFalse(form.is_valid())
        form = CashAccountForm({"name": "X", "currency": "USD", "opening_balance": "-1",
                                "allow_negative_balance": "on"}, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        form = HoldingForm({"ticker": "X", "asset_type": "stock", "opening_quantity": "-1",
                            "opening_avg_cost": "0", "currency": "USD"}, user=self.user)
        self.assertFalse(form.is_valid())

    def test_set_position_refuses_irreconcilable_history(self):
        self.record(tx_type="buy", holding=self.holding, quantity=D("10"), price=D("1"))
        self.assertRejected("irreconcilable", set_position, self.holding, D("3"), D("1"))
        self.holding.refresh_from_db()
        self.assertEqual(self.holding.quantity, D("10"))

    def test_set_cash_balance_respects_the_negative_rule(self):
        self.assertRejected("negative_balance", set_cash_balance, self.cash, D("-5"))


class ConsistencyViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.client.force_login(self.user)
        self.cash = CashAccount.objects.create(user=self.user, name="Bank", opening_balance=D("10.00"))
        self.holding = Holding.objects.create(user=self.user, ticker="AAPL", opening_quantity=D("1"))

    def test_add_form_shows_oversell_error(self):
        resp = self.client.post(reverse("wallet:add_transaction"), {
            "tx_type": "sell", "holding": self.holding.pk, "quantity": "2", "price": "1",
            "fees": "0", "date": "2026-01-01",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "only hold 1 AAPL")
        self.assertFalse(Transaction.objects.exists())

    def test_add_form_shows_overdraft_error(self):
        resp = self.client.post(reverse("wallet:add_transaction"), {
            "tx_type": "withdraw", "cash_account": self.cash.pk, "amount": "20",
            "fees": "0", "date": "2026-01-01",
        })
        self.assertContains(resp, "allow negative balance")
        self.assertFalse(Transaction.objects.exists())

    def test_delete_refused_with_message(self):
        buy = save_transaction(Transaction(user=self.user, tx_type="buy", holding=self.holding,
                                           quantity=D("1"), price=D("1"), date=date(2026, 1, 1)))
        save_transaction(Transaction(user=self.user, tx_type="sell", holding=self.holding,
                                     quantity=D("2"), price=D("1"), date=date(2026, 1, 2)))
        resp = self.client.post(reverse("wallet:delete_transaction", args=[buy.pk]), follow=True)
        self.assertContains(resp, "delete this transaction")
        self.assertTrue(Transaction.objects.filter(pk=buy.pk).exists())

    def test_admin_rejects_oversell(self):
        admin_user = User.objects.create_superuser("root", password="x")
        self.client.force_login(admin_user)
        resp = self.client.post(reverse("admin:wallet_transaction_add"), {
            "user": self.user.pk, "tx_type": "sell", "holding": self.holding.pk, "quantity": "5",
            "price": "1", "fees": "0", "date": "2026-01-01", "notes": "",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "only hold 1 AAPL")
        self.assertFalse(Transaction.objects.exists())
