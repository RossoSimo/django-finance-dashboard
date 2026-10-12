from django import forms
from django.core.exceptions import ValidationError

from common.forms import BootstrapFormMixin, UserOwnedFormMixin

from .models import CashAccount, Holding, Transaction
from .services import check_cash_account, check_holding, check_transaction


class LedgerCheckMixin:
    """Runs a wallet.services consistency check on the would-be instance once
    the form is otherwise valid, showing problems as form errors."""

    ledger_check = None  # set to a check_* function from wallet.services

    def _post_clean(self):
        super()._post_clean()
        if not self.errors:
            try:
                type(self).ledger_check(self.instance)
            except ValidationError as exc:
                self.add_error(None, exc)


class CashAccountForm(LedgerCheckMixin, UserOwnedFormMixin, BootstrapFormMixin, forms.ModelForm):
    ledger_check = check_cash_account

    class Meta:
        model = CashAccount
        fields = ["name", "currency", "opening_balance", "allow_negative_balance"]
        help_texts = {
            "opening_balance": "Balance before any transaction you record here.",
        }


class HoldingForm(LedgerCheckMixin, UserOwnedFormMixin, BootstrapFormMixin, forms.ModelForm):
    ledger_check = check_holding

    class Meta:
        model = Holding
        fields = ["ticker", "name", "asset_type", "opening_quantity", "opening_avg_cost", "currency", "notes"]
        labels = {
            "opening_quantity": "Quantity already held",
            "opening_avg_cost": "Average cost of that quantity",
        }
        help_texts = {
            "opening_quantity": "Leave at 0 if you'll record the purchases as transactions.",
        }


class TransactionForm(LedgerCheckMixin, UserOwnedFormMixin, BootstrapFormMixin, forms.ModelForm):
    ledger_check = check_transaction

    # Fields each transaction type needs to have a well-defined effect.
    REQUIRED_BY_TYPE = {
        "buy": ("holding", "quantity", "price"),
        "sell": ("holding", "quantity", "price"),
        "deposit": ("cash_account", "amount"),
        "withdraw": ("cash_account", "amount"),
        "dividend": ("cash_account", "amount"),
    }

    class Meta:
        model = Transaction
        fields = [
            "tx_type", "holding", "cash_account", "quantity", "price",
            "amount", "fees", "date", "notes",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        cleaned = super().clean()
        for name in self.REQUIRED_BY_TYPE.get(cleaned.get("tx_type"), ()):
            if cleaned.get(name) in (None, "") and name not in self.errors:
                label = self.fields[name].label
                self.add_error(name, f"{label} is required for a {self.cleaned_data['tx_type']}.")
        for name in ("quantity", "price", "amount", "fees"):
            value = cleaned.get(name)
            if value is not None and value < 0:
                self.add_error(name, "Must not be negative.")
        return cleaned
