from django.contrib import admin

from .models import CashAccount, Holding, NetWorthSnapshot, Transaction


@admin.register(CashAccount)
class CashAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "currency", "balance")
    list_filter = ("user",)


@admin.register(NetWorthSnapshot)
class NetWorthSnapshotAdmin(admin.ModelAdmin):
    list_display = ("date", "user", "cash_total", "holdings_value", "net_worth")
    list_filter = ("user",)


@admin.register(Holding)
class HoldingAdmin(admin.ModelAdmin):
    list_display = ("ticker", "user", "asset_type", "quantity", "avg_cost", "currency")
    list_filter = ("user", "asset_type")
    search_fields = ("ticker", "name")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "user", "tx_type", "holding", "cash_account", "quantity", "price", "amount")
    list_filter = ("user", "tx_type", "date")
