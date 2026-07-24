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

    name = models.CharField(max_length=100)
    currency = models.CharField(max_length=10, default="USD")
    balance = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.currency})"


class Holding(models.Model):
    """A position you own in a given ticker."""

    ticker = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200, blank=True)
    asset_type = models.CharField(max_length=10, choices=ASSET_TYPES, default="stock")
    quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    avg_cost = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    currency = models.CharField(max_length=10, default="USD")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ticker"]

    def __str__(self):
        return self.ticker

    @property
    def cost_basis(self):
        return self.quantity * self.avg_cost


class NetWorthSnapshot(models.Model):
    """A daily snapshot of net worth, used to draw the trend chart on the
    dashboard. One row per calendar date; recorded automatically whenever
    the dashboard is viewed."""

    date = models.DateField(unique=True)
    cash_total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    holdings_value = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    net_worth = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    class Meta:
        ordering = ["date"]

    def __str__(self):
        return f"{self.date}: {self.net_worth}"


class Transaction(models.Model):
    """A single event: buying/selling a holding, or moving cash."""

    TX_TYPES = [
        ("buy", "Buy"),
        ("sell", "Sell"),
        ("deposit", "Deposit"),
        ("withdraw", "Withdraw"),
        ("dividend", "Dividend"),
    ]

    tx_type = models.CharField(max_length=10, choices=TX_TYPES)
    holding = models.ForeignKey(
        Holding, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions"
    )
    cash_account = models.ForeignKey(
        CashAccount, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions"
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
