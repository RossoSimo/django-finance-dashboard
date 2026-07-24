from django import forms

from common.forms import BootstrapFormMixin

from .models import CashAccount, Holding, Transaction


class CashAccountForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = CashAccount
        fields = ["name", "currency", "balance"]


class HoldingForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Holding
        fields = ["ticker", "name", "asset_type", "quantity", "avg_cost", "currency", "notes"]

    def clean_ticker(self):
        return self.cleaned_data["ticker"].upper().strip()


class TransactionForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Transaction
        fields = [
            "tx_type", "holding", "cash_account", "quantity", "price",
            "amount", "fees", "date", "notes",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
        }
