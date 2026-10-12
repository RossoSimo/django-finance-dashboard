"""
Pure functions that replay a transaction history into a cash balance or a
position. Nothing here touches the ORM, so the same rules are used by the
services, the admin and data migrations.

A "transaction" is anything with tx_type, quantity, price, amount and fees
attributes (a Transaction instance, or a historical model in a migration).
Transactions must be passed in chronological order.
"""

from decimal import Decimal

ZERO = Decimal("0")


def _d(value):
    return value if value is not None else ZERO


def cash_effect(tx) -> Decimal:
    """Signed change a transaction makes to its cash account."""
    qty, price, fees = _d(tx.quantity), _d(tx.price), _d(tx.fees)
    if tx.tx_type == "buy":
        return -(qty * price + fees)
    if tx.tx_type == "sell":
        return qty * price - fees
    if tx.tx_type in ("deposit", "dividend"):
        return _d(tx.amount)
    if tx.tx_type == "withdraw":
        return -_d(tx.amount)
    return ZERO


def replay_cash(opening_balance, txs) -> Decimal:
    return _d(opening_balance) + sum((cash_effect(tx) for tx in txs), ZERO)


def replay_position(opening_quantity, opening_avg_cost, txs) -> tuple[Decimal, Decimal]:
    """Return (quantity, avg_cost) after applying buys and sells.

    Buys re-weight the average cost (fees included); sells reduce quantity
    and leave the average cost unchanged. Histories that oversell are
    rejected by first_negative_position() before they're saved; the floor
    at zero only guards legacy data.
    """
    qty, avg = _d(opening_quantity), _d(opening_avg_cost)
    for tx in txs:
        if tx.tx_type == "buy":
            bought = _d(tx.quantity)
            new_qty = qty + bought
            if new_qty > 0:
                avg = (avg * qty + bought * _d(tx.price) + _d(tx.fees)) / new_qty
            qty = new_qty
        elif tx.tx_type == "sell":
            qty = max(qty - _d(tx.quantity), ZERO)
    return qty, avg


def solve_opening_balance(current_balance, txs) -> Decimal:
    """Opening balance that makes the history end at `current_balance`."""
    return _d(current_balance) - replay_cash(ZERO, txs)


def solve_opening_position(current_quantity, current_avg_cost, txs) -> tuple[Decimal, Decimal]:
    """Opening (quantity, avg_cost) that makes the history end at the given position.

    The final average cost is an affine function of the opening one, so two
    replays are enough to solve for it. If the history sold more than it
    could have held, the opening quantity is floored at zero.
    """
    txs = list(txs)
    net_bought = sum(
        (_d(tx.quantity) if tx.tx_type == "buy" else -_d(tx.quantity)
         for tx in txs if tx.tx_type in ("buy", "sell")),
        ZERO,
    )
    opening_qty = max(_d(current_quantity) - net_bought, ZERO)
    if opening_qty == 0:
        return ZERO, ZERO

    _, avg_if_zero = replay_position(opening_qty, ZERO, txs)
    _, avg_if_one = replay_position(opening_qty, Decimal("1"), txs)
    slope = avg_if_one - avg_if_zero
    if slope == 0:  # opening cost no longer influences the result
        return opening_qty, ZERO
    return opening_qty, (_d(current_avg_cost) - avg_if_zero) / slope


def first_negative_position(opening_quantity, txs):
    """Return (tx, quantity_after) for the first transaction that would take
    the position below zero, or None if the history is consistent."""
    qty = _d(opening_quantity)
    for tx in txs:
        if tx.tx_type == "buy":
            qty += _d(tx.quantity)
        elif tx.tx_type == "sell":
            qty -= _d(tx.quantity)
        if qty < 0:
            return tx, qty
    return None


def first_negative_balance(opening_balance, txs):
    """Return (tx, balance_after) for the first transaction that would take the
    balance below zero (None for tx means the opening balance itself is
    negative), or None if the balance never goes negative."""
    balance = _d(opening_balance)
    if balance < 0:
        return None, balance
    for tx in txs:
        balance += cash_effect(tx)
        if balance < 0:
            return tx, balance
    return None
