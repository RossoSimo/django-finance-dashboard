from django.conf import settings
from django.db import models

ASSET_TYPES = [
    ("stock", "Stock"),
    ("etf", "ETF"),
    ("bond", "Bond"),
    ("crypto", "Crypto"),
    ("other", "Other"),
]


class CashAccount(models.Model):
    """A pool of liquidity, e.g. 'Broker cash', 'Bank account', 'Savings'."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cash_accounts"
    )
    name = models.CharField(max_length=100)
    currency = models.CharField(max_length=10, default="USD")
    opening_balance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    allow_negative_balance = models.BooleanField(
        default=False,
        help_text="Allow this account to go below zero (overdraft, margin, credit card).",
    )
    # Derived: opening_balance plus every transaction. Maintained by
    # wallet.services; never edit it directly.
    balance = models.DecimalField(max_digits=18, decimal_places=2, default=0, editable=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.currency})"

    def save(self, *args, **kwargs):
        if self._state.adding:  # no transactions yet
            self.balance = self.opening_balance
        super().save(*args, **kwargs)


class Holding(models.Model):
    """A position you own in a given ticker."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="holdings"
    )
    ticker = models.CharField(max_length=20)
    name = models.CharField(max_length=200, blank=True)
    asset_type = models.CharField(max_length=10, choices=ASSET_TYPES, default="stock")
    opening_quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    opening_avg_cost = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    # Derived: the opening position plus every buy/sell. Maintained by
    # wallet.services; never edit them directly.
    quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0, editable=False)
    avg_cost = models.DecimalField(max_digits=18, decimal_places=6, default=0, editable=False)
    currency = models.CharField(max_length=10, default="USD")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ticker"]
        constraints = [
            models.UniqueConstraint(fields=["user", "ticker"], name="unique_holding_ticker_per_user"),
        ]

    def __str__(self):
        return self.ticker

    def save(self, *args, **kwargs):
        if self._state.adding:  # no transactions yet
            self.quantity, self.avg_cost = self.opening_quantity, self.opening_avg_cost
        super().save(*args, **kwargs)

    @property
    def cost_basis(self):
        return self.quantity * self.avg_cost


class NetWorthSnapshot(models.Model):
    """A daily snapshot of net worth, used to draw the trend chart on the
    dashboard. One row per user per calendar date; recorded automatically whenever
    the dashboard is viewed."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="net_worth_snapshots"
    )
    date = models.DateField()
    cash_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    holdings_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    net_worth = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(fields=["user", "date"], name="unique_snapshot_date_per_user"),
        ]

    def __str__(self):
        return f"{self.date}: {self.net_worth}"


class Transaction(models.Model):
    """A single event: buying/selling a holding, or moving cash.

    Transactions are the source of truth for balances and positions; create,
    edit and delete them through wallet.services so derived values stay in sync.
    """

    TX_TYPES = [
        ("buy", "Buy"),
        ("sell", "Sell"),
        ("deposit", "Deposit"),
        ("withdraw", "Withdraw"),
        ("dividend", "Dividend"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transactions"
    )
    tx_type = models.CharField(max_length=10, choices=TX_TYPES)
    holding = models.ForeignKey(
        Holding, null=True, blank=True, on_delete=models.PROTECT, related_name="transactions"
    )
    cash_account = models.ForeignKey(
        CashAccount, null=True, blank=True, on_delete=models.PROTECT, related_name="transactions"
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    price = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    fees = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    date = models.DateField()
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        target = self.holding.ticker if self.holding else (self.cash_account.name if self.cash_account else "?")
        return f"{self.get_tx_type_display()} {target} on {self.date}"
