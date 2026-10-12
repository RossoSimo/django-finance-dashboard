"""
Business logic for wallet state. Transactions are the source of truth:
a CashAccount's balance and a Holding's quantity/avg_cost are derived by
replaying their transactions on top of the opening values (see ledger.py).

Always create, edit or delete transactions through save_transaction /
delete_transaction so the derived values stay in sync and the history stays
consistent (no overselling, no unexpected negative cash). Kept separate from
views so it's reusable from the admin, a management command, or an API.
"""

from datetime import date as date_cls
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.utils import timezone

from . import ledger
from .models import CashAccount, Holding, NetWorthSnapshot, Transaction

CHRONOLOGICAL = ("date", "created_at", "pk")


def _fmt(value: Decimal) -> str:
    return f"{value.normalize():f}"


def _money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):,}"


# --- Consistency checks -----------------------------------------------------
# Each check looks at the full history as it *would* be after a change and
# raises ValidationError if it doesn't hold up, before anything is saved.


def _history(related, tx=None, include_tx=False):
    """Chronological transactions of a holding/account, with `tx` taken out
    and, if include_tx, put back in with its (possibly unsaved) new values."""
    txs = list(related.transactions.all()) if related.pk else []
    if tx is not None:
        txs = [t for t in txs if t.pk != tx.pk or tx.pk is None]
        if include_tx:
            txs.append(tx)
    now = timezone.now()
    txs.sort(key=lambda t: (t.date, t.created_at or now, t.pk if t.pk is not None else float("inf")))
    return txs


def _check_position(holding: Holding, txs) -> None:
    if holding.opening_quantity < 0 or holding.opening_avg_cost < 0:
        raise ValidationError("Opening quantity and average cost can't be negative.", code="negative_opening")
    found = ledger.first_negative_position(holding.opening_quantity, txs)
    if found:
        bad, qty_after = found
        held = qty_after + (bad.quantity or 0)
        raise ValidationError(
            f"You'd only hold {_fmt(held)} {holding.ticker} on {bad.date}, so you can't sell "
            f"{_fmt(bad.quantity)} then. Record the purchase first or fix the dates.",
            code="oversell",
        )


def _check_cash(account: CashAccount, txs) -> None:
    if account.allow_negative_balance:
        return
    found = ledger.first_negative_balance(account.opening_balance, txs)
    if found:
        bad, balance = found
        when = f"on {bad.date}" if bad is not None else "from the start"
        raise ValidationError(
            f"This would take {account.name} to {_money(balance)} {account.currency} {when}. "
            f"Tick \"allow negative balance\" on the account if that's expected.",
            code="negative_balance",
        )


def check_holding(holding: Holding) -> None:
    """Validate a holding's (possibly edited) opening position against its history."""
    _check_position(holding, _history(holding))


def check_cash_account(account: CashAccount) -> None:
    """Validate an account's (possibly edited) opening balance/settings against its history."""
    _check_cash(account, _history(account))


def check_transaction(tx: Transaction, deleting: bool = False) -> None:
    """Validate creating/editing (or deleting) `tx` against every holding and
    account it touches now or touched before an edit."""
    for related in (tx.holding, tx.cash_account):
        if related is not None and related.user_id != tx.user_id:
            raise ValidationError("That holding or cash account belongs to another user.", code="owner")

    previous = Transaction.objects.filter(pk=tx.pk).select_related("holding", "cash_account").first() if tx.pk else None
    holdings = {h.pk: h for h in (tx.holding, previous and previous.holding) if h is not None}
    accounts = {a.pk: a for a in (tx.cash_account, previous and previous.cash_account) if a is not None}

    for holding in holdings.values():
        include = not deleting and tx.holding_id == holding.pk
        _check_position(holding, _history(holding, tx, include))
    for account in accounts.values():
        include = not deleting and tx.cash_account_id == account.pk
        _check_cash(account, _history(account, tx, include))


# --- Recalculation ------------------------------------------------------------


def recalculate_cash_account(account: CashAccount) -> CashAccount:
    txs = account.transactions.order_by(*CHRONOLOGICAL)
    account.balance = ledger.replay_cash(account.opening_balance, txs)
    account.save(update_fields=["balance"])
    return account


def recalculate_holding(holding: Holding) -> Holding:
    txs = holding.transactions.order_by(*CHRONOLOGICAL)
    holding.quantity, holding.avg_cost = ledger.replay_position(
        holding.opening_quantity, holding.opening_avg_cost, txs
    )
    holding.save(update_fields=["quantity", "avg_cost"])
    return holding


def _recalculate(holding_ids, cash_account_ids) -> None:
    for holding in Holding.objects.select_for_update().filter(pk__in=set(holding_ids) - {None}):
        recalculate_holding(holding)
    for account in CashAccount.objects.select_for_update().filter(pk__in=set(cash_account_ids) - {None}):
        recalculate_cash_account(account)


# --- Operations -----------------------------------------------------------------


@db_transaction.atomic
def save_transaction(tx: Transaction) -> Transaction:
    """Create or update a transaction and recalculate everything it touches,
    including the holding/account it pointed at before an edit.

    Raises ValueError if the holding or cash account belongs to a different
    user than the transaction, and ValidationError if the resulting history
    would oversell a holding or overdraw an account that doesn't allow it.
    """
    for related in (tx.holding, tx.cash_account):
        if related is not None and related.user_id != tx.user_id:
            raise ValueError("Transaction references another user's holding or cash account.")
    check_transaction(tx)

    previous = None
    if tx.pk:
        previous = Transaction.objects.filter(pk=tx.pk).values("holding_id", "cash_account_id").first()
    tx.save()

    holding_ids = [tx.holding_id] + ([previous["holding_id"]] if previous else [])
    cash_ids = [tx.cash_account_id] + ([previous["cash_account_id"]] if previous else [])
    _recalculate(holding_ids, cash_ids)
    return tx


@db_transaction.atomic
def delete_transaction(tx: Transaction) -> None:
    """Delete a transaction and recalculate. Raises ValidationError if later
    transactions depend on it (e.g. deleting a buy that a sell relies on)."""
    check_transaction(tx, deleting=True)
    holding_id, cash_id = tx.holding_id, tx.cash_account_id
    tx.delete()
    _recalculate([holding_id], [cash_id])


@db_transaction.atomic
def set_cash_balance(account: CashAccount, balance) -> CashAccount:
    """Make the account's current balance equal `balance` (e.g. from a
    screenshot import) by adjusting its opening balance; history is kept."""
    txs = account.transactions.order_by(*CHRONOLOGICAL)
    account.opening_balance = ledger.solve_opening_balance(balance, txs)
    check_cash_account(account)
    account.save()
    return recalculate_cash_account(account)


@db_transaction.atomic
def set_position(holding: Holding, quantity, avg_cost) -> Holding:
    """Make the holding's current position equal (quantity, avg_cost) by
    adjusting its opening position; history is kept. Raises ValidationError
    if the recorded transactions can't be reconciled with that position."""
    txs = list(holding.transactions.order_by(*CHRONOLOGICAL))
    holding.opening_quantity, holding.opening_avg_cost = ledger.solve_opening_position(quantity, avg_cost, txs)
    final_qty, _ = ledger.replay_position(holding.opening_quantity, holding.opening_avg_cost, txs)
    if final_qty != Decimal(quantity):
        raise ValidationError(
            f"Your recorded {holding.ticker} transactions already add up to {_fmt(final_qty)}, "
            f"so the position can't be set to {_fmt(Decimal(quantity))}. Edit those transactions instead.",
            code="irreconcilable",
        )
    check_holding(holding)
    holding.save()
    return recalculate_holding(holding)


def net_worth_summary(user):
    """Return a dict summarizing a user's total liquidity, holdings value, and net worth.

    Uses live prices where available; falls back to cost basis if a quote
    can't be fetched.
    """
    from common.market_data import get_quotes  # local import to avoid app-loading issues

    cash_total = sum((a.balance for a in CashAccount.objects.filter(user=user)), Decimal("0"))

    holdings = list(Holding.objects.filter(user=user, quantity__gt=0))
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


def record_snapshot(user, summary: dict | None = None) -> NetWorthSnapshot:
    """Upsert the user's NetWorthSnapshot for today from their net worth summary.

    Called from the dashboard view on every visit so the trend chart
    naturally fills in over time without needing a scheduled job.
    """
    summary = summary or net_worth_summary(user)
    snapshot, _created = NetWorthSnapshot.objects.update_or_create(
        user=user,
        date=date_cls.today(),
        defaults={
            "cash_total": summary["cash_total"],
            "holdings_value": summary["holdings_value"],
            "net_worth": summary["net_worth"],
        },
    )
    return snapshot
