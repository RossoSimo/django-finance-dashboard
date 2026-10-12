from django import forms

from common.forms import BootstrapFormMixin
from wallet.models import ASSET_TYPES


class HoldingImportForm(BootstrapFormMixin, forms.Form):
    include = forms.BooleanField(required=False, initial=True, label="")
    ticker = forms.CharField(max_length=20)
    name = forms.CharField(max_length=200, required=False)
    asset_type = forms.ChoiceField(choices=ASSET_TYPES, initial="stock")
    quantity = forms.DecimalField(max_digits=18, decimal_places=6, required=False, initial=0)
    avg_cost = forms.DecimalField(max_digits=18, decimal_places=6, required=False, initial=0)
    currency = forms.CharField(max_length=10, required=False, initial="USD")

    def clean_ticker(self):
        return self.cleaned_data["ticker"].upper().strip()


class CashImportForm(BootstrapFormMixin, forms.Form):
    include = forms.BooleanField(required=False, initial=True, label="")
    name = forms.CharField(max_length=100)
    currency = forms.CharField(max_length=10, required=False, initial="USD")
    balance = forms.DecimalField(max_digits=18, decimal_places=2, required=False, initial=0)


HoldingImportFormSet = forms.formset_factory(HoldingImportForm, extra=0)
CashImportFormSet = forms.formset_factory(CashImportForm, extra=0)


def holding_initial_from_extracted(items: list[dict]) -> list[dict]:
    valid_types = dict(ASSET_TYPES)
    initial = []
    for item in items or []:
        asset_type = item.get("asset_type") if item.get("asset_type") in valid_types else "stock"
        initial.append({
            "include": True,
            "ticker": (item.get("ticker") or "").upper(),
            "name": item.get("name") or "",
            "asset_type": asset_type,
            "quantity": item.get("quantity") or 0,
            "avg_cost": item.get("avg_cost") or 0,
            "currency": item.get("currency") or "USD",
        })
    return initial


def cash_initial_from_extracted(items: list[dict]) -> list[dict]:
    initial = []
    for item in items or []:
        initial.append({
            "include": True,
            "name": item.get("name") or "Cash account",
            "currency": item.get("currency") or "USD",
            "balance": item.get("balance") or 0,
        })
    return initial
