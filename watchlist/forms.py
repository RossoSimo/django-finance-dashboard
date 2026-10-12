from django import forms

from common.forms import BootstrapFormMixin, UserOwnedFormMixin

from .models import WatchlistItem


class WatchlistItemForm(UserOwnedFormMixin, BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = WatchlistItem
        fields = ["ticker", "name", "asset_type", "target_price", "notes"]
