from django.core.management.base import BaseCommand

from alerts.services import check_alerts


class Command(BaseCommand):
    help = "Check every active price alert now (e.g. from a scheduled task)."

    def handle(self, *args, **options):
        triggered = check_alerts(user=None, force=True)
        for alert in triggered:
            self.stdout.write(f"Triggered: {alert.user} - {alert} (price {alert.triggered_price.normalize():f})")
        self.stdout.write(self.style.SUCCESS(f"{len(triggered)} alert(s) triggered."))
