from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseRedirect

from .forms import LedgerCheckMixin
from .models import CashAccount, Holding, NetWorthSnapshot, Transaction
from .services import (
    check_cash_account,
    check_holding,
    check_transaction,
    delete_transaction,
    recalculate_cash_account,
    recalculate_holding,
    save_transaction,
)


class CashAccountAdminForm(LedgerCheckMixin, forms.ModelForm):
    ledger_check = check_cash_account

    class Meta:
        model = CashAccount
        fields = "__all__"


class HoldingAdminForm(LedgerCheckMixin, forms.ModelForm):
    ledger_check = check_holding

    class Meta:
        model = Holding
        fields = "__all__"


class TransactionAdminForm(LedgerCheckMixin, forms.ModelForm):
    ledger_check = check_transaction

    class Meta:
        model = Transaction
        fields = "__all__"


@admin.register(CashAccount)
class CashAccountAdmin(admin.ModelAdmin):
    form = CashAccountAdminForm
    list_display = ("name", "user", "currency", "opening_balance", "balance", "allow_negative_balance")
    list_filter = ("user",)
    readonly_fields = ("balance",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        recalculate_cash_account(obj)


@admin.register(NetWorthSnapshot)
class NetWorthSnapshotAdmin(admin.ModelAdmin):
    list_display = ("date", "user", "cash_total", "holdings_value", "net_worth")
    list_filter = ("user",)


@admin.register(Holding)
class HoldingAdmin(admin.ModelAdmin):
    form = HoldingAdminForm
    list_display = ("ticker", "user", "asset_type", "quantity", "avg_cost", "currency")
    list_filter = ("user", "asset_type")
    search_fields = ("ticker", "name")
    readonly_fields = ("quantity", "avg_cost")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        recalculate_holding(obj)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    """Saves and deletes go through wallet.services so balances stay in sync
    and the history stays consistent."""

    form = TransactionAdminForm
    list_display = ("date", "user", "tx_type", "holding", "cash_account", "quantity", "price", "amount")
    list_filter = ("user", "tx_type", "date")

    def save_model(self, request, obj, form, change):
        save_transaction(obj)

    def delete_model(self, request, obj):
        delete_transaction(obj)

    def delete_queryset(self, request, queryset):
        # Newest first, so deleting a buy together with the sells that
        # depend on it succeeds; all-or-nothing.
        with transaction.atomic():
            for tx in queryset.order_by("-date", "-created_at", "-pk"):
                delete_transaction(tx)

    def _refuse(self, request, exc):
        self.message_user(request, f"Not deleted: {' '.join(exc.messages)}", level=messages.ERROR)
        return HttpResponseRedirect(request.get_full_path())

    def delete_view(self, request, object_id, extra_context=None):
        try:
            return super().delete_view(request, object_id, extra_context)
        except ValidationError as exc:
            return self._refuse(request, exc)

    def changelist_view(self, request, extra_context=None):
        try:
            return super().changelist_view(request, extra_context)
        except ValidationError as exc:
            return self._refuse(request, exc)
