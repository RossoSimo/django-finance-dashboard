"""Accounts whose existing history already goes below zero keep working:
switch on allow_negative_balance for them instead of rejecting later edits."""

from django.db import migrations

from wallet.ledger import first_negative_balance

ORDER = ("date", "created_at", "pk")


def allow_existing_overdrafts(apps, schema_editor):
    CashAccount = apps.get_model("wallet", "CashAccount")
    Transaction = apps.get_model("wallet", "Transaction")
    for account in CashAccount.objects.filter(allow_negative_balance=False):
        txs = Transaction.objects.filter(cash_account=account).order_by(*ORDER)
        if first_negative_balance(account.opening_balance, txs) is not None:
            account.allow_negative_balance = True
            account.save(update_fields=["allow_negative_balance"])


class Migration(migrations.Migration):

    dependencies = [
        ("wallet", "0008_cash_allow_negative_balance"),
    ]

    operations = [
        migrations.RunPython(allow_existing_overdrafts, migrations.RunPython.noop),
    ]
