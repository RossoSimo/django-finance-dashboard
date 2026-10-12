from django import forms

from common.forms import BootstrapFormMixin, UserOwnedFormMixin

from .models import PriceAlert


class PriceAlertForm(UserOwnedFormMixin, BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = PriceAlert
        fields = ["ticker", "direction", "target_price", "note"]
        labels = {"direction": "Alert me when the price"}

    def clean_ticker(self):
        # Several alerts per ticker are fine, so skip the per-user uniqueness check.
        return self.cleaned_data["ticker"].upper().strip()

    def clean_target_price(self):
        price = self.cleaned_data["target_price"]
        if price <= 0:
            raise forms.ValidationError("Must be greater than zero.")
        return price
