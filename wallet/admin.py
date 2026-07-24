from django.contrib import admin

from .models import CashAccount, Holding, NetWorthSnapshot, Transaction


@admin.register(CashAccount)
class CashAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "currency", "balance")


@admin.register(NetWorthSnapshot)
class NetWorthSnapshotAdmin(admin.ModelAdmin):
    list_display = ("date", "cash_total", "holdings_value", "net_worth")


@admin.register(Holding)
class HoldingAdmin(admin.ModelAdmin):
    list_display = ("ticker", "asset_type", "quantity", "avg_cost", "currency")
    list_filter = ("asset_type",)
    search_fields = ("ticker", "name")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "tx_type", "holding", "cash_account", "quantity", "price", "amount")
    list_filter = ("tx_type", "date")
