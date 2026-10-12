from django.conf import settings
from django.db import models


class PriceAlert(models.Model):
    """"Tell me when TICKER goes above/below PRICE". One-shot: once triggered
    it stops checking and shows in the alerts banner until dismissed; it can
    be re-armed."""

    ABOVE, BELOW = "above", "below"
    DIRECTIONS = [(ABOVE, "rises to or above"), (BELOW, "falls to or below")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="price_alerts")
    ticker = models.CharField(max_length=20)
    direction = models.CharField(max_length=5, choices=DIRECTIONS)
    target_price = models.DecimalField(
        max_digits=18, decimal_places=6,
        help_text="In the currency the ticker trades in (pence-quoted London stocks: use pounds).",
    )
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    is_active = models.BooleanField(default=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_price = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    currency = models.CharField(max_length=10, blank=True)
    triggered_at = models.DateTimeField(null=True, blank=True)
    triggered_price = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-triggered_at", "ticker"]
        indexes = [models.Index(fields=["user", "is_active"])]

    def __str__(self):
        return f"{self.ticker} {self.get_direction_display()} {self.target_price.normalize():f}"

    @property
    def is_unseen(self):
        return self.triggered_at is not None and self.dismissed_at is None

    def condition_met(self, price) -> bool:
        return price >= self.target_price if self.direction == self.ABOVE else price <= self.target_price
