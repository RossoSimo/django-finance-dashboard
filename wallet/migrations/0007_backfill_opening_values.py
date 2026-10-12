"""Back-fill opening values so that replaying each account's / holding's
existing transactions reproduces the balance and position stored today."""

from django.db import migrations

from wallet.ledger import solve_opening_balance, solve_opening_position

ORDER = ("date", "created_at", "pk")


def backfill(apps, schema_editor):
    CashAccount = apps.get_model("wallet", "CashAccount")
    Holding = apps.get_model("wallet", "Holding")
    Transaction = apps.get_model("wallet", "Transaction")

    for account in CashAccount.objects.all():
        txs = Transaction.objects.filter(cash_account=account).order_by(*ORDER)
        account.opening_balance = solve_opening_balance(account.balance, txs)
        account.save(update_fields=["opening_balance"])

    for holding in Holding.objects.all():
        txs = Transaction.objects.filter(holding=holding).order_by(*ORDER)
        holding.opening_quantity, holding.opening_avg_cost = solve_opening_position(
            holding.quantity, holding.avg_cost, txs
        )
        holding.save(update_fields=["opening_quantity", "opening_avg_cost"])


class Migration(migrations.Migration):

    dependencies = [
        ("wallet", "0006_ledger_opening_values"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
