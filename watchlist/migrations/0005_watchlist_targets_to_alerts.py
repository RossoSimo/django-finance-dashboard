"""Watchlist target prices are replaced by price alerts: turn each existing
target into an equivalent "falls to or below" alert before the column goes."""

from django.db import migrations


def targets_to_alerts(apps, schema_editor):
    WatchlistItem = apps.get_model("watchlist", "WatchlistItem")
    PriceAlert = apps.get_model("alerts", "PriceAlert")
    for item in WatchlistItem.objects.filter(target_price__isnull=False):
        PriceAlert.objects.create(
            user_id=item.user_id,
            ticker=item.ticker,
            direction="below",
            target_price=item.target_price,
            note="From your watchlist target",
        )


class Migration(migrations.Migration):

    dependencies = [
        ("watchlist", "0004_user_required"),
        ("alerts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(targets_to_alerts, migrations.RunPython.noop),
    ]
