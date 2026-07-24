from django.db import models

from wallet.models import ASSET_TYPES


class WatchlistItem(models.Model):
    ticker = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200, blank=True)
    asset_type = models.CharField(max_length=10, choices=ASSET_TYPES, default="stock")
    target_price = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    notes = models.TextField(blank=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ticker"]

    def __str__(self):
        return self.ticker
