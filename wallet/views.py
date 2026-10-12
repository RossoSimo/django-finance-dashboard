from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import CashAccountForm, HoldingForm, TransactionForm
from .models import CashAccount, Holding, Transaction
from .services import (
    delete_transaction,
    net_worth_summary,
    recalculate_cash_account,
    recalculate_holding,
    save_transaction,
)


def wallet_home(request):
    summary = net_worth_summary(request.user)
    cash_accounts = CashAccount.objects.filter(user=request.user)
    recent_tx = Transaction.objects.filter(user=request.user).select_related("holding", "cash_account")[:15]
    return render(request, "wallet/wallet_home.html", {
        "summary": summary,
        "cash_accounts": cash_accounts,
        "recent_tx": recent_tx,
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
    })


def transaction_list(request):
    tx = Transaction.objects.filter(user=request.user).select_related("holding", "cash_account")
    return render(request, "wallet/transaction_list.html", {"transactions": tx})


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
    return render(request, "wallet/confirm_delete.html", {"object": holding})
