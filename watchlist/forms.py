from django import forms

from common.forms import BootstrapFormMixin

from .models import WatchlistItem


class WatchlistItemForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = WatchlistItem
        fields = ["ticker", "name", "asset_type", "target_price", "notes"]

    def clean_ticker(self):
        return self.cleaned_data["ticker"].upper().strip()
