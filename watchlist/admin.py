from django.contrib import admin

from .models import WatchlistItem


@admin.register(WatchlistItem)
class WatchlistItemAdmin(admin.ModelAdmin):
    list_display = ("ticker", "user", "asset_type", "added_at")
    list_filter = ("user", "asset_type")
    search_fields = ("ticker", "name")
