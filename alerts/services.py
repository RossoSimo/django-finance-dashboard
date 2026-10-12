"""Checking and managing price alerts. Reusable from views, the
check_alerts management command, or a future background job."""

from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from common.currency import normalize

from .models import PriceAlert


def check_alerts(user=None, force: bool = False) -> list[PriceAlert]:
    """Fetch prices for active alerts and trigger those whose condition is met.

    For a single user, runs at most once per MARKET_DATA_CACHE_SECONDS unless
    `force`. Pass user=None to check everyone's (management command).
    Returns the alerts triggered by this run.
    """
    from common.market_data import get_quotes  # local import keeps app loading simple

    if user is not None and not force:
        throttle_key = f"alerts-checked:{user.pk}"
        if not cache.add(throttle_key, True, settings.MARKET_DATA_CACHE_SECONDS):
            return []

    alerts = PriceAlert.objects.filter(is_active=True)
    if user is not None:
        alerts = alerts.filter(user=user)
    alerts = list(alerts)
    if not alerts:
        return []

    quotes = get_quotes(sorted({a.ticker for a in alerts}))
    now = timezone.now()
    triggered = []
    for alert in alerts:
        quote = quotes.get(alert.ticker.upper())
        if not quote or quote.price is None:
            continue  # no data right now; try again next time
        price, currency = normalize(Decimal(str(quote.price)), quote.currency)
        alert.last_checked_at, alert.last_price, alert.currency = now, price, currency or ""
        fields = ["last_checked_at", "last_price", "currency"]
        if alert.condition_met(price):
            alert.is_active = False
            alert.triggered_at, alert.triggered_price, alert.dismissed_at = now, price, None
            fields += ["is_active", "triggered_at", "triggered_price", "dismissed_at"]
            triggered.append(alert)
        alert.save(update_fields=fields)
    return triggered


def unseen_alerts(user):
    return PriceAlert.objects.filter(user=user, triggered_at__isnull=False, dismissed_at__isnull=True)


def dismiss(alerts) -> int:
    return alerts.filter(triggered_at__isnull=False, dismissed_at__isnull=True).update(dismissed_at=timezone.now())


def rearm(alert: PriceAlert) -> PriceAlert:
    alert.is_active = True
    alert.triggered_at = alert.triggered_price = alert.dismissed_at = None
    alert.save(update_fields=["is_active", "triggered_at", "triggered_price", "dismissed_at"])
    return alert
