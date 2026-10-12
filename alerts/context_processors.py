from .services import unseen_alerts


def triggered_alerts(request):
    """Triggered-but-not-dismissed alerts for the banner. One cheap query; no
    price fetching happens here."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    alerts = list(unseen_alerts(user).order_by("-triggered_at")[:6])
    return {"unseen_alerts": alerts[:5], "unseen_alerts_more": len(alerts) > 5}
