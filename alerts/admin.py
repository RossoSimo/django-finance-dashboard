from django.contrib import admin

from .models import PriceAlert


@admin.register(PriceAlert)
class PriceAlertAdmin(admin.ModelAdmin):
    list_display = ("ticker", "user", "direction", "target_price", "is_active", "last_price", "triggered_at")
    list_filter = ("user", "is_active", "direction")
    search_fields = ("ticker",)
