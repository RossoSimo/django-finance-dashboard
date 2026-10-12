from django import forms

from common.forms import BootstrapFormMixin
from wallet.models import ASSET_TYPES


class HoldingImportForm(BootstrapFormMixin, forms.Form):
    include = forms.BooleanField(required=False, initial=True, label="")
    ticker = forms.CharField(max_length=20, required=False)  # required only for ticked rows
    name = forms.CharField(max_length=200, required=False)
    asset_type = forms.ChoiceField(choices=ASSET_TYPES, initial="stock")
    quantity = forms.DecimalField(max_digits=18, decimal_places=6, required=False, initial=0)
    avg_cost = forms.DecimalField(max_digits=18, decimal_places=6, required=False, initial=0)
    currency = forms.CharField(max_length=10, required=False, initial="USD")

    def clean_ticker(self):
        return self.cleaned_data["ticker"].upper().strip()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("include"):
            if not cleaned.get("ticker"):
                self.add_error("ticker", "Enter the ticker, or untick this row.")
            if not cleaned.get("quantity") or cleaned["quantity"] <= 0:
                self.add_error("quantity", "Enter the quantity, or untick this row.")
        return cleaned


class CashImportForm(BootstrapFormMixin, forms.Form):
    include = forms.BooleanField(required=False, initial=True, label="")
    name = forms.CharField(max_length=100)
    currency = forms.CharField(max_length=10, required=False, initial="USD")
    balance = forms.DecimalField(max_digits=18, decimal_places=2, required=False, initial=0)


HoldingImportFormSet = forms.formset_factory(HoldingImportForm, extra=0)
CashImportFormSet = forms.formset_factory(CashImportForm, extra=0)


def holding_initial_from_checks(checks: list[dict], default_currency: str) -> list[dict]:
    """Form rows from MarketChecker results: the matched listing where found,
    otherwise what was read. Rows without a match start unticked."""
    valid_types = dict(ASSET_TYPES)
    initial = []
    for check in checks or []:
        read = check["read"]
        initial.append({
            "include": check["status"] != "not_found",
            "ticker": check["symbol"] or read.get("ticker") or "",
            "name": check["symbol_name"] or read.get("name") or "",
            "asset_type": check["asset_type"] if check["asset_type"] in valid_types else "stock",
            "quantity": check["quantity"] or 0,
            "avg_cost": check["avg_cost"] or 0,
            "currency": check["live_currency"] or read.get("currency") or default_currency,
        })
    return initial


def cash_initial_from_extracted(items: list[dict], default_currency: str) -> list[dict]:
    initial = []
    for item in items or []:
        initial.append({
            "include": True,
            "name": item.get("name") or "Cash account",
            "currency": item.get("currency") or default_currency,
            "balance": item.get("balance") or 0,
        })
    return initial
