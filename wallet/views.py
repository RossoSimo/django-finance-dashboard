from decimal import Decimal
from urllib.parse import quote

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .csv_import import COLUMNS as CSV_COLUMNS
from .csv_import import MAX_BYTES, MAX_ROWS, SAMPLE_CSV, parse_csv, run_import
from .ledger import cash_effect
from .forms import CashAccountForm, HoldingForm, TransactionForm
from .models import CashAccount, Holding, Transaction
from .services import (
    delete_transaction,
    net_worth_summary,
    recalculate_cash_account,
    recalculate_holding,
    save_transaction,
)


def _decorate_transactions(transactions):
    """Attach display fields: target label, currency and a signed cash amount."""
    out = []
    for tx in transactions:
        tx.target = tx.holding.ticker if tx.holding else (tx.cash_account.name if tx.cash_account else None)
        if tx.cash_account:
            tx.currency = tx.cash_account.currency
        else:
            tx.currency = tx.holding.currency if tx.holding else ""
        tx.signed_amount = cash_effect(tx)  # exactly what the books moved, fees included
        tx.menu = [
            {"url": reverse("wallet:edit_transaction", args=[tx.pk]), "label": "Edit", "icon": "pencil"},
            {"url": reverse("wallet:delete_transaction", args=[tx.pk]), "label": "Delete", "icon": "trash",
             "danger": True},
        ]
        out.append(tx)
    return out


def wallet_home(request):
    summary = net_worth_summary(request.user)
    cash_accounts = CashAccount.objects.filter(user=request.user)
    recent_tx = _decorate_transactions(
        Transaction.objects.filter(user=request.user).select_related("holding", "cash_account")[:15]
    )
    gains = [row["gain"] for row in summary["rows"] if row["gain"] is not None]
    total_gain = sum(gains, Decimal("0")) if gains else None
    for row in summary["rows"]:
        holding = row["holding"]
        row["menu"] = [
            {"url": reverse("performance:holding", args=[holding.pk]), "label": "Chart", "icon": "graph-up"},
            {"url": f"{reverse('alerts:add')}?ticker={quote(holding.ticker)}", "label": "Add alert",
             "icon": "bell"},
            {"url": reverse("wallet:delete_holding", args=[holding.pk]), "label": "Remove", "icon": "trash",
             "post": True, "confirm": f"Remove {holding.ticker}?", "danger": True},
        ]
    header_menu = [
        {"url": reverse("wallet:add_holding"), "label": "Add holding", "icon": "plus-circle"},
        {"url": reverse("wallet:add_cash_account"), "label": "Add cash account", "icon": "bank"},
        {"url": reverse("wallet:csv_upload"), "label": "Import CSV", "icon": "filetype-csv"},
        {"url": reverse("assistant:import_upload"), "label": "Import screenshot", "icon": "camera"},
    ]
    return render(request, "wallet/wallet_home.html", {
        "summary": summary,
        "cash_accounts": cash_accounts,
        "recent_tx": recent_tx,
        "total_gain": total_gain,
        "header_menu": header_menu,
    })


def add_cash_account(request):
    if request.method == "POST":
        form = CashAccountForm(request.POST, user=request.user)
        if form.is_valid():
            recalculate_cash_account(form.save())
            messages.success(request, "Cash account added.")
            return redirect("wallet:home")
    else:
        form = CashAccountForm(user=request.user)
    return render(request, "wallet/form.html", {"form": form, "title": "Add cash account"})


def add_holding(request):
    if request.method == "POST":
        form = HoldingForm(request.POST, user=request.user)
        if form.is_valid():
            recalculate_holding(form.save())
            messages.success(request, "Holding added.")
            return redirect("wallet:home")
    else:
        form = HoldingForm(user=request.user)
    return render(request, "wallet/form.html", {"form": form, "title": "Add holding"})


def add_transaction(request):
    if request.method == "POST":
        form = TransactionForm(request.POST, user=request.user)
        if form.is_valid():
            save_transaction(form.save(commit=False))
            messages.success(request, "Transaction recorded.")
            return redirect("wallet:home")
    else:
        form = TransactionForm(user=request.user)
    return render(request, "wallet/form.html", {"form": form, "title": "Add transaction"})


def edit_transaction(request, pk):
    tx = get_object_or_404(Transaction, pk=pk, user=request.user)
    if request.method == "POST":
        form = TransactionForm(request.POST, instance=tx, user=request.user)
        if form.is_valid():
            save_transaction(form.save(commit=False))
            messages.success(request, "Transaction updated; balances recalculated.")
            return redirect("wallet:transaction_list")
    else:
        form = TransactionForm(instance=tx, user=request.user)
    return render(request, "wallet/form.html", {"form": form, "title": "Edit transaction"})


def remove_transaction(request, pk):
    tx = get_object_or_404(Transaction, pk=pk, user=request.user)
    if request.method == "POST":
        try:
            delete_transaction(tx)
        except ValidationError as exc:
            messages.error(request, f"Can't delete this transaction: {' '.join(exc.messages)}")
        else:
            messages.success(request, "Transaction deleted; balances recalculated.")
        return redirect("wallet:transaction_list")
    return render(request, "wallet/confirm_delete.html", {
        "object": tx, "cancel_url": reverse("wallet:transaction_list"),
        "consequence": "Balances and positions will be recalculated without it.",
    })


def transaction_list(request):
    tx = Transaction.objects.filter(user=request.user).select_related("holding", "cash_account")
    header_menu = [{"url": reverse("wallet:csv_upload"), "label": "Import CSV", "icon": "filetype-csv"}]
    return render(request, "wallet/transaction_list.html", {
        "transactions": _decorate_transactions(tx), "header_menu": header_menu,
    })


def delete_holding(request, pk):
    holding = get_object_or_404(Holding, pk=pk, user=request.user)
    tx_count = holding.transactions.count()
    if tx_count:
        messages.error(
            request,
            f"{holding.ticker} has {tx_count} transaction(s). Delete them first so your history stays consistent.",
        )
        return redirect("wallet:home")
    if request.method == "POST":
        holding.delete()
        messages.success(request, f"Removed {holding.ticker}.")
        return redirect("wallet:home")
    return render(request, "wallet/confirm_delete.html", {
        "object": holding, "cancel_url": reverse("wallet:home"),
        "consequence": "This can't be undone.",
    })


CSV_SESSION_KEY = "csv_import_rows"


def csv_sample(request):
    response = HttpResponse(SAMPLE_CSV, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="transactions-sample.csv"'
    return response


def csv_upload(request):
    if request.method == "POST":
        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, "Please choose a CSV file.")
            return redirect("wallet:csv_upload")
        try:
            rows = parse_csv(upload.read(MAX_BYTES + 1))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("wallet:csv_upload")
        request.session[CSV_SESSION_KEY] = rows
        return redirect("wallet:csv_preview")
    return render(request, "wallet/csv_upload.html", {"columns": CSV_COLUMNS, "max_rows": MAX_ROWS})


def csv_preview(request):
    rows = request.session.get(CSV_SESSION_KEY)
    if not rows:
        messages.error(request, "Nothing to import; upload a CSV file first.")
        return redirect("wallet:csv_upload")

    if request.method == "POST":
        result = run_import(request.user, rows, commit=True)
        if result.committed:
            request.session.pop(CSV_SESSION_KEY, None)
            skipped = f" Skipped {result.duplicate_count} duplicate(s)." if result.duplicate_count else ""
            messages.success(request, f"Imported {result.ok_count} transaction(s).{skipped}")
            return redirect("wallet:transaction_list")
        messages.error(request, "Nothing was imported; fix the rows marked as errors and upload the file again.")
    else:
        result = run_import(request.user, rows, commit=False)

    return render(request, "wallet/csv_preview.html", {"result": result})
