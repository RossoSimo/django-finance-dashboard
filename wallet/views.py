from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import CashAccountForm, HoldingForm, TransactionForm
from .models import CashAccount, Holding, Transaction
from .services import apply_transaction, net_worth_summary


def wallet_home(request):
    summary = net_worth_summary()
    cash_accounts = CashAccount.objects.all()
    recent_tx = Transaction.objects.select_related("holding", "cash_account")[:15]
    return render(request, "wallet/wallet_home.html", {
        "summary": summary,
        "cash_accounts": cash_accounts,
        "recent_tx": recent_tx,
    })


def add_cash_account(request):
    if request.method == "POST":
        form = CashAccountForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Cash account added.")
            return redirect("wallet:home")
    else:
        form = CashAccountForm()
    return render(request, "wallet/form.html", {"form": form, "title": "Add cash account"})


def add_holding(request):
    if request.method == "POST":
        form = HoldingForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Holding added.")
            return redirect("wallet:home")
    else:
        form = HoldingForm()
    return render(request, "wallet/form.html", {"form": form, "title": "Add holding"})


def add_transaction(request):
    if request.method == "POST":
        form = TransactionForm(request.POST)
        if form.is_valid():
            tx = form.save()
            apply_transaction(tx)
            messages.success(request, "Transaction recorded.")
            return redirect("wallet:home")
    else:
        form = TransactionForm()
    return render(request, "wallet/form.html", {"form": form, "title": "Add transaction"})


def transaction_list(request):
    tx = Transaction.objects.select_related("holding", "cash_account")
    return render(request, "wallet/transaction_list.html", {"transactions": tx})


def delete_holding(request, pk):
    holding = get_object_or_404(Holding, pk=pk)
    if request.method == "POST":
        holding.delete()
        messages.success(request, f"Removed {holding.ticker}.")
        return redirect("wallet:home")
    return render(request, "wallet/confirm_delete.html", {"object": holding})
