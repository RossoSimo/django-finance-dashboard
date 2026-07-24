"""
Business logic for turning a Transaction into updated Holding/CashAccount
state. Kept separate from views so it's easy to test and reuse (e.g. from
the admin, a management command, or an API later).
"""

from datetime import date as date_cls
from decimal import Decimal

from django.db import transaction as db_transaction

from .models import CashAccount, Holding, NetWorthSnapshot, Transaction


@db_transaction.atomic
def apply_transaction(tx: Transaction) -> None:
    """Update the related Holding and/or CashAccount balances for a tx.

    Call this once, right after creating a Transaction, e.g.:
        tx = Transaction.objects.create(...)
        apply_transaction(tx)
    """
    if tx.tx_type == "buy" and tx.holding:
        holding = tx.holding
        qty = tx.quantity or Decimal("0")
        price = tx.price or Decimal("0")
        old_qty = holding.quantity
        new_qty = old_qty + qty
        if new_qty > 0:
            total_cost = (holding.avg_cost * old_qty) + (price * qty) + (tx.fees or 0)
            holding.avg_cost = total_cost / new_qty
        holding.quantity = new_qty
        holding.save()

        if tx.cash_account:
            cost = (qty * price) + (tx.fees or 0)
            tx.cash_account.balance -= cost
            tx.cash_account.save()

    elif tx.tx_type == "sell" and tx.holding:
        holding = tx.holding
        qty = tx.quantity or Decimal("0")
        price = tx.price or Decimal("0")
        holding.quantity = max(holding.quantity - qty, Decimal("0"))
        holding.save()

        if tx.cash_account:
            proceeds = (qty * price) - (tx.fees or 0)
            tx.cash_account.balance += proceeds
            tx.cash_account.save()

    elif tx.tx_type == "deposit" and tx.cash_account:
        tx.cash_account.balance += tx.amount or Decimal("0")
        tx.cash_account.save()

    elif tx.tx_type == "withdraw" and tx.cash_account:
        tx.cash_account.balance -= tx.amount or Decimal("0")
        tx.cash_account.save()

    elif tx.tx_type == "dividend" and tx.cash_account:
        tx.cash_account.balance += tx.amount or Decimal("0")
        tx.cash_account.save()


def net_worth_summary():
    """Return a dict summarizing total liquidity, holdings value, and net worth.

    Uses live prices where available; falls back to cost basis if a quote
    can't be fetched.
    """
    from common.market_data import get_quotes  # local import to avoid app-loading issues

    cash_total = sum((a.balance for a in CashAccount.objects.all()), Decimal("0"))

    holdings = list(Holding.objects.filter(quantity__gt=0))
    quotes = get_quotes([h.ticker for h in holdings])

    holdings_value = Decimal("0")
    rows = []
    allocation = {}  # asset_type -> value, for the dashboard donut chart
    for h in holdings:
        quote = quotes.get(h.ticker.upper())
        price = Decimal(str(quote.price)) if quote and quote.price is not None else h.avg_cost
        market_value = h.quantity * price
        holdings_value += market_value
        gain = market_value - h.cost_basis
        gain_pct = (gain / h.cost_basis * 100) if h.cost_basis else None
        rows.append({
            "holding": h,
            "quote": quote,
            "market_value": market_value,
            "gain": gain,
            "gain_pct": gain_pct,
        })
        allocation[h.asset_type] = allocation.get(h.asset_type, Decimal("0")) + market_value

    if cash_total > 0:
        allocation["cash"] = allocation.get("cash", Decimal("0")) + cash_total

    return {
        "cash_total": cash_total,
        "holdings_value": holdings_value,
        "net_worth": cash_total + holdings_value,
        "rows": rows,
        "allocation": allocation,
    }


def record_snapshot(summary: dict | None = None) -> NetWorthSnapshot:
    """Upsert today's NetWorthSnapshot from the current net worth summary.

    Called from the dashboard view on every visit so the trend chart
    naturally fills in over time without needing a scheduled job.
    """
    summary = summary or net_worth_summary()
    snapshot, _created = NetWorthSnapshot.objects.update_or_create(
        date=date_cls.today(),
        defaults={
            "cash_total": summary["cash_total"],
            "holdings_value": summary["holdings_value"],
            "net_worth": summary["net_worth"],
        },
    )
    return snapshot
