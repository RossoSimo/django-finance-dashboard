from datetime import date
from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

D = Decimal


class MigrationTestCase(TransactionTestCase):
    """Migrates to a given state and returns historical apps; restores the
    latest schema afterwards."""

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())


class BackfillOpeningValuesMigrationTests(MigrationTestCase):
    """0007 must pick opening values so replaying existing history reproduces
    the balances and positions that were stored before the ledger change."""

    before = [("wallet", "0005_user_required")]
    after = [("wallet", "0007_backfill_opening_values")]

    def test_existing_state_is_preserved(self):
        apps = self.migrate(self.before)
        User = apps.get_model("auth", "User")
        CashAccount = apps.get_model("wallet", "CashAccount")
        Holding = apps.get_model("wallet", "Holding")
        Transaction = apps.get_model("wallet", "Transaction")

        user = User.objects.create(username="alice")
        # State as the old in-place code would have left it: 1000 opening cash,
        # 5 shares @ 80 held, then buy 10 @ 100 (+10 fees) and sell 4 @ 120 (-2 fees).
        cash = CashAccount.objects.create(user=user, name="Broker", balance=D("468.00"))
        holding = Holding.objects.create(user=user, ticker="AAPL", quantity=D("11"),
                                         avg_cost=(D("5") * 80 + D("1010")) / 15)
        Transaction.objects.create(user=user, tx_type="buy", holding=holding, cash_account=cash,
                                   quantity=D("10"), price=D("100"), fees=D("10"), date=date(2026, 1, 1))
        Transaction.objects.create(user=user, tx_type="sell", holding=holding, cash_account=cash,
                                   quantity=D("4"), price=D("120"), fees=D("2"), date=date(2026, 1, 2))
        untouched = CashAccount.objects.create(user=user, name="Savings", balance=D("42.00"))

        apps = self.migrate(self.after)
        CashAccount = apps.get_model("wallet", "CashAccount")
        Holding = apps.get_model("wallet", "Holding")

        cash = CashAccount.objects.get(pk=cash.pk)
        self.assertEqual(cash.opening_balance, D("1000.00"))
        self.assertEqual(cash.balance, D("468.00"))
        self.assertEqual(CashAccount.objects.get(pk=untouched.pk).opening_balance, D("42.00"))

        holding = Holding.objects.get(pk=holding.pk)
        self.assertEqual(holding.opening_quantity, D("5"))
        self.assertAlmostEqual(holding.opening_avg_cost, D("80"), places=4)
        self.assertEqual(holding.quantity, D("11"))


class AllowExistingOverdraftsMigrationTests(MigrationTestCase):
    """0009 must keep already-overdrawn accounts valid under the new rule."""

    def test_only_overdrawn_accounts_are_allowed_negative(self):
        apps = self.migrate([("wallet", "0008_cash_allow_negative_balance")])
        User = apps.get_model("auth", "User")
        CashAccount = apps.get_model("wallet", "CashAccount")
        Transaction = apps.get_model("wallet", "Transaction")
        user = User.objects.create(username="alice")
        overdrawn = CashAccount.objects.create(user=user, name="Card", opening_balance=D("10"), balance=D("-40"))
        Transaction.objects.create(user=user, tx_type="withdraw", cash_account=overdrawn,
                                   amount=D("50"), date=date(2026, 1, 1))
        healthy = CashAccount.objects.create(user=user, name="Bank", opening_balance=D("10"), balance=D("10"))

        apps = self.migrate([("wallet", "0009_allow_negative_for_existing_overdrafts")])
        CashAccount = apps.get_model("wallet", "CashAccount")
        self.assertTrue(CashAccount.objects.get(pk=overdrawn.pk).allow_negative_balance)
        self.assertFalse(CashAccount.objects.get(pk=healthy.pk).allow_negative_balance)
