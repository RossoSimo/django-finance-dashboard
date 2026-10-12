from django import forms

from common.forms import BootstrapFormMixin, UserOwnedFormMixin

from .models import CashAccount, Holding, Transaction


class CashAccountForm(UserOwnedFormMixin, BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = CashAccount
        fields = ["name", "currency", "balance"]


class HoldingForm(UserOwnedFormMixin, BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Holding
        fields = ["ticker", "name", "asset_type", "quantity", "avg_cost", "currency", "notes"]


class TransactionForm(UserOwnedFormMixin, BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Transaction
        fields = [
            "tx_type", "holding", "cash_account", "quantity", "price",
            "amount", "fees", "date", "notes",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }
