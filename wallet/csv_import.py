"""
CSV import of transactions.

parse_csv() turns an uploaded file into raw rows (strings only, so they can
be kept in the session between preview and confirm). run_import() converts
and saves them through the same TransactionForm + save_transaction path as
the UI, so every ledger rule applies. A preview is a real run that is rolled
back; the import itself is all-or-nothing.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import get_base_currency

from .forms import TransactionForm
from .models import CashAccount, Holding, Transaction
from .services import save_transaction

MAX_BYTES = 1_000_000
MAX_ROWS = 2000

COLUMNS = ["date", "type", "ticker", "account", "quantity", "price", "amount", "fees", "notes"]
HEADER_ALIASES = {
    "tx_type": "type", "transaction": "type", "tipo": "type", "data": "date",
    "symbol": "ticker", "titolo": "ticker",
    "cash_account": "account", "conto": "account",
    "qty": "quantity", "quantità": "quantity", "quantita": "quantity",
    "prezzo": "price", "importo": "amount", "commissioni": "fees", "fee": "fees", "note": "notes",
}
TYPE_ALIASES = {
    "buy": "buy", "acquisto": "buy",
    "sell": "sell", "vendita": "sell",
    "deposit": "deposit", "deposito": "deposit", "versamento": "deposit",
    "withdraw": "withdraw", "withdrawal": "withdraw", "prelievo": "withdraw",
    "dividend": "dividend", "dividendo": "dividend", "cedola": "dividend",
}
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"]

SAMPLE_CSV = (
    "date,type,ticker,account,quantity,price,amount,fees,notes\n"
    "2026-01-02,deposit,,Broker,,,1000.00,,Initial funding\n"
    "2026-01-05,buy,VWCE.DE,Broker,5,110.50,,2.00,\n"
    "2026-03-15,dividend,,Broker,,,4.20,,VWCE dividend\n"
    "2026-04-01,sell,VWCE.DE,Broker,2,118.00,,2.00,\n"
)


# --- Parsing ----------------------------------------------------------------


def parse_decimal(value: str) -> Decimal | None:
    """Parse '1234.56', '1.234,56', '1,234.56', '12,5', '€ 10' and similar.

    A single comma with no dot is read as a decimal comma (12,5 = 12.5).
    """
    s = (value or "").strip().replace(" ", "").replace(" ", "")
    s = s.lstrip("€$£").rstrip("€$£")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif s.count(",") == 1:
        s = s.replace(",", ".")
    elif s.count(",") > 1:
        s = s.replace(",", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        raise ValidationError(f"“{value}” is not a number.")


def parse_date(value: str) -> date:
    """ISO dates, or day-first dates (31/12/2026, 31-12-2026, 31.12.2026)."""
    value = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValidationError(f"“{value}” is not a date (use YYYY-MM-DD or DD/MM/YYYY).")


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")  # Excel on Windows


def parse_csv(data: bytes) -> list[dict]:
    """Return [{"line": n, "date": "...", "type": "...", ...}] with raw string
    values. Raises ValidationError for problems with the file as a whole."""
    if len(data) > MAX_BYTES:
        raise ValidationError(f"The file is too large (max {MAX_BYTES // 1_000_000} MB).")
    text = _decode(data)
    if not text.strip():
        raise ValidationError("The file is empty.")

    try:
        dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)

    header = [h.strip().lower() for h in next(reader)]
    header = [HEADER_ALIASES.get(h, h) for h in header]
    missing = [c for c in ("date", "type") if c not in header]
    if missing:
        raise ValidationError(
            f"Missing column(s): {', '.join(missing)}. Expected columns: {', '.join(COLUMNS)}."
        )
    unknown = [h for h in header if h and h not in COLUMNS]
    if unknown:
        raise ValidationError(f"Unknown column(s): {', '.join(unknown)}. Expected: {', '.join(COLUMNS)}.")

    rows = []
    for line, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        row = {"line": line, **{c: "" for c in COLUMNS}}
        for name, value in zip(header, values):
            if name:
                row[name] = value.strip()
        rows.append(row)
        if len(rows) > MAX_ROWS:
            raise ValidationError(f"Too many rows (max {MAX_ROWS}). Split the file and import it in parts.")
    if not rows:
        raise ValidationError("The file has a header but no rows.")
    return rows


# --- Importing ----------------------------------------------------------------


@dataclass
class RowResult:
    line: int
    raw: dict
    status: str = "ok"  # "ok", "duplicate" or "error"
    message: str = ""
    creates_holding: str | None = None


@dataclass
class ImportResult:
    rows: list[RowResult] = field(default_factory=list)
    committed: bool = False

    def count(self, status):
        return sum(1 for r in self.rows if r.status == status)

    @property
    def ok_count(self):
        return self.count("ok")

    @property
    def duplicate_count(self):
        return self.count("duplicate")

    @property
    def error_count(self):
        return self.count("error")


class _Rollback(Exception):
    pass


def _key(tx_date, tx_type, ticker, account_id, quantity, price, amount, fees):
    def n(v):
        return v.normalize() if isinstance(v, Decimal) else (v or None)
    return (tx_date, tx_type, (ticker or "").upper() or None, account_id, n(quantity), n(price), n(amount), n(fees or Decimal("0")))


def _existing_keys(user) -> Counter:
    txs = Transaction.objects.filter(user=user).select_related("holding")
    return Counter(
        _key(t.date, t.tx_type, t.holding.ticker if t.holding else None, t.cash_account_id,
             t.quantity, t.price, t.amount, t.fees)
        for t in txs
    )


def _convert(raw: dict) -> dict:
    tx_type = TYPE_ALIASES.get(raw["type"].strip().lower())
    if not tx_type:
        raise ValidationError(f"Unknown type “{raw['type']}” (use buy, sell, deposit, withdraw or dividend).")
    return {
        "date": parse_date(raw["date"]),
        "tx_type": tx_type,
        "ticker": raw["ticker"].strip().upper(),
        "account": raw["account"].strip(),
        "quantity": parse_decimal(raw["quantity"]),
        "price": parse_decimal(raw["price"]),
        "amount": parse_decimal(raw["amount"]),
        "fees": parse_decimal(raw["fees"]) or Decimal("0"),
        "notes": raw["notes"],
    }


def _find_account(user, name):
    if not name:
        return None
    matches = list(CashAccount.objects.filter(user=user, name__iexact=name)[:2])
    if not matches:
        raise ValidationError(f"No cash account named “{name}”. Create it first in the Wallet.")
    if len(matches) > 1:
        raise ValidationError(f"More than one cash account is named “{name}”; rename one of them.")
    return matches[0]


def _form_errors(form) -> str:
    parts = []
    for name, errors in form.errors.items():
        label = form.fields[name].label if name in form.fields else None
        parts.append(f"{label}: {' '.join(errors)}" if label else " ".join(errors))
    return " ".join(parts)


def _import_row(user, values, result, base_currency):
    account = _find_account(user, values["account"])
    holding = None
    if values["ticker"]:
        holding = Holding.objects.filter(user=user, ticker=values["ticker"]).first()
        if holding is None and values["tx_type"] == "buy":
            currency = account.currency if account else base_currency
            holding = Holding.objects.create(user=user, ticker=values["ticker"], currency=currency)
            result.creates_holding = values["ticker"]
        elif holding is None and values["tx_type"] == "sell":
            raise ValidationError(f"You don't hold {values['ticker']}, so it can't be sold.")

    def s(v):
        return "" if v is None else str(v)

    form = TransactionForm({
        "tx_type": values["tx_type"], "holding": holding.pk if holding else "",
        "cash_account": account.pk if account else "", "quantity": s(values["quantity"]),
        "price": s(values["price"]), "amount": s(values["amount"]), "fees": s(values["fees"]),
        "date": values["date"].isoformat(), "notes": values["notes"],
    }, user=user)
    if not form.is_valid():
        raise ValidationError(_form_errors(form))
    save_transaction(form.save(commit=False))


def run_import(user, raw_rows: list[dict], commit: bool) -> ImportResult:
    """Validate (commit=False) or import (commit=True) raw rows.

    Rows are applied in date order so a file may list a sale before the
    purchase it depends on. Rows identical to an existing transaction are
    skipped as duplicates. Nothing is saved unless commit is True and no row
    has an error.
    """
    result = ImportResult()
    base_currency = get_base_currency(user)
    existing = _existing_keys(user)

    converted = []
    for raw in raw_rows:
        row = RowResult(line=raw["line"], raw=raw)
        result.rows.append(row)
        try:
            converted.append((row, _convert(raw)))
        except ValidationError as exc:
            row.status, row.message = "error", " ".join(exc.messages)

    try:
        with transaction.atomic():
            for row, values in sorted(converted, key=lambda rv: rv[1]["date"]):
                account = None
                try:
                    account = _find_account(user, values["account"])
                except ValidationError:
                    pass  # reported by _import_row below
                key = _key(values["date"], values["tx_type"], values["ticker"], account.pk if account else None,
                           values["quantity"], values["price"], values["amount"], values["fees"])
                if existing[key]:
                    existing[key] -= 1
                    row.status, row.message = "duplicate", "Already recorded; will be skipped."
                    continue
                try:
                    with transaction.atomic():  # undo a half-done row (e.g. a created holding)
                        _import_row(user, values, row, base_currency)
                except ValidationError as exc:
                    row.status, row.message = "error", " ".join(exc.messages)
                    row.creates_holding = None

            if not commit or result.error_count:
                raise _Rollback
            result.committed = True
    except _Rollback:
        pass
    return result
