from decimal import Decimal

from wallet.test_migrations import MigrationTestCase


class WatchlistTargetsToAlertsMigrationTests(MigrationTestCase):
    def test_targets_become_below_alerts_and_column_is_dropped(self):
        apps = self.migrate([("watchlist", "0004_user_required"), ("alerts", "0001_initial")])
        User = apps.get_model("auth", "User")
        WatchlistItem = apps.get_model("watchlist", "WatchlistItem")
        user = User.objects.create(username="alice")
        WatchlistItem.objects.create(user=user, ticker="MSFT", target_price=Decimal("300.5"))
        WatchlistItem.objects.create(user=user, ticker="NVDA")

        apps = self.migrate([("watchlist", "0006_remove_target_price")])
        PriceAlert = apps.get_model("alerts", "PriceAlert")
        alert = PriceAlert.objects.get()
        self.assertEqual((alert.user_id, alert.ticker, alert.direction), (user.pk, "MSFT", "below"))
        self.assertEqual(alert.target_price, Decimal("300.5"))
        self.assertTrue(alert.is_active)
        fields = {f.name for f in apps.get_model("watchlist", "WatchlistItem")._meta.get_fields()}
        self.assertNotIn("target_price", fields)
