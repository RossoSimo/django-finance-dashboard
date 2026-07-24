from django.contrib import admin

from .models import WatchlistItem


@admin.register(WatchlistItem)
class WatchlistItemAdmin(admin.ModelAdmin):
    list_display = ("ticker", "asset_type", "target_price", "added_at")
    list_filter = ("asset_type",)
    search_fields = ("ticker", "name")
