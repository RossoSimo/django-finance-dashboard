from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .csv_import import SAMPLE_CSV, parse_csv, parse_date, parse_decimal, run_import
from .models import CashAccount, Holding, Transaction

D = Decimal
User = get_user_model()


class ParsingTests(TestCase):
    def test_parse_decimal_formats(self):
        cases = {
            "1234.56": D("1234.56"), "1.234,56": D("1234.56"), "1,234.56": D("1234.56"),
            "12,5": D("12.5"), "1,234,567": D("1234567"), " € 10 ": D("10"), "-3": D("-3"), "": None,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_decimal(raw), expected)
        with self.assertRaises(ValidationError):
            parse_decimal("abc")

    def test_parse_date_formats(self):
        for raw in ("2026-01-31", "31/01/2026", "31-01-2026", "31.01.2026"):
            with self.subTest(raw=raw):
                self.assertEqual(parse_date(raw), date(2026, 1, 31))
        with self.assertRaises(ValidationError):
            parse_date("01/31/2026")  # month-first is not accepted

    def test_comma_separated_with_bom(self):
        rows = parse_csv(("﻿" + SAMPLE_CSV).encode("utf-8"))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[1]["ticker"], "VWCE.DE")
        self.assertEqual(rows[1]["line"], 3)

    def test_semicolon_separated_italian_headers(self):
        data = "Data;Tipo;Titolo;Conto;Quantità;Prezzo;Importo;Commissioni\n31/01/2026;acquisto;ENEL.MI;Broker;10;6,50;;1,00\n"
        rows = parse_csv(data.encode("cp1252"))
        self.assertEqual(rows[0]["type"], "acquisto")
        self.assertEqual(rows[0]["price"], "6,50")
        self.assertEqual(rows[0]["quantity"], "10")

    def test_missing_and_unknown_columns(self):
        with self.assertRaisesMessage(ValidationError, "Missing column(s): type"):
            parse_csv(b"date,ticker\n2026-01-01,AAPL\n")
        with self.assertRaisesMessage(ValidationError, "Unknown column(s): colour"):
            parse_csv(b"date,type,colour\n2026-01-01,buy,red\n")

    def test_empty_and_blank_rows(self):
        with self.assertRaises(ValidationError):
            parse_csv(b"")
        with self.assertRaises(ValidationError):
            parse_csv(b"date,type\n\n,\n")

    def test_limits(self):
        with mock.patch("wallet.csv_import.MAX_ROWS", 2):
            with self.assertRaisesMessage(ValidationError, "Too many rows"):
                parse_csv(b"date,type\n" + b"2026-01-01,deposit\n" * 3)
        with mock.patch("wallet.csv_import.MAX_BYTES", 10):
            with self.assertRaisesMessage(ValidationError, "too large"):
                parse_csv(b"date,type\n2026-01-01,deposit\n")


def csv_rows(*lines, header="date,type,ticker,account,quantity,price,amount,fees,notes"):
    return parse_csv(("\n".join([header, *lines]) + "\n").encode())


class RunImportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.broker = CashAccount.objects.create(user=self.user, name="Broker", currency="EUR",
                                                 opening_balance=D("1000.00"))

    def test_preview_saves_nothing(self):
        result = run_import(self.user, csv_rows(*SAMPLE_CSV.splitlines()[1:]), commit=False)
        self.assertEqual((result.ok_count, result.error_count), (4, 0), [r.message for r in result.rows])
        self.assertFalse(result.committed)
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Holding.objects.exists())
        self.assertEqual(result.rows[1].creates_holding, "VWCE.DE")

    def test_commit_saves_through_the_ledger(self):
        result = run_import(self.user, csv_rows(*SAMPLE_CSV.splitlines()[1:]), commit=True)
        self.assertTrue(result.committed)
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 4)
        holding = Holding.objects.get(user=self.user, ticker="VWCE.DE")
        self.assertEqual(holding.quantity, D("3"))
        self.assertEqual(holding.currency, "EUR")  # taken from the paying account
        self.broker.refresh_from_db()
        # 1000 + 1000 - (5*110.50 + 2) + 4.20 + (2*118 - 2)
        self.assertEqual(self.broker.balance, D("1683.70"))

    def test_rows_are_applied_in_date_order(self):
        rows = csv_rows(
            "2026-02-01,sell,AAPL,Broker,2,15,,,",  # listed first, dated after the buy
            "2026-01-01,buy,AAPL,Broker,5,10,,,",
        )
        result = run_import(self.user, rows, commit=True)
        self.assertTrue(result.committed, [r.message for r in result.rows])
        self.assertEqual(Holding.objects.get(ticker="AAPL").quantity, D("3"))

    def test_any_error_blocks_the_whole_import(self):
        rows = csv_rows(
            "2026-01-01,deposit,,Broker,,,50,,",
            "2026-01-02,sell,AAPL,Broker,1,10,,,",  # nothing held
            "2026-01-03,withdraw,,Broker,,,5000,,",  # overdraft
            "2026-01-04,teleport,,Broker,,,1,,",
            "not-a-date,deposit,,Broker,,,1,,",
            "2026-01-05,deposit,,Nope,,,1,,",
        )
        result = run_import(self.user, rows, commit=True)
        self.assertFalse(result.committed)
        self.assertEqual([r.status for r in result.rows], ["ok", "error", "error", "error", "error", "error"])
        messages = [r.message for r in result.rows]
        self.assertIn("don't hold AAPL", messages[1])
        self.assertIn("allow negative balance", messages[2])
        self.assertIn("Unknown type", messages[3])
        self.assertIn("not a date", messages[4])
        self.assertIn("No cash account named", messages[5])
        self.assertFalse(Transaction.objects.exists())
        self.broker.refresh_from_db()
        self.assertEqual(self.broker.balance, D("1000.00"))

    def test_missing_required_fields_are_reported(self):
        result = run_import(self.user, csv_rows("2026-01-01,buy,AAPL,Broker,,10,,,"), commit=False)
        self.assertEqual(result.rows[0].status, "error")
        self.assertIn("Quantity", result.rows[0].message)
        self.assertFalse(Holding.objects.exists())  # holding created for the row was rolled back

    def test_reimporting_the_same_file_skips_duplicates(self):
        rows = csv_rows(*SAMPLE_CSV.splitlines()[1:])
        run_import(self.user, rows, commit=True)
        again = run_import(self.user, rows, commit=True)
        self.assertEqual((again.ok_count, again.duplicate_count), (0, 4))
        self.assertEqual(Transaction.objects.count(), 4)

    def test_identical_rows_within_one_file_are_both_imported(self):
        rows = csv_rows("2026-01-01,deposit,,Broker,,,10,,", "2026-01-01,deposit,,Broker,,,10,,")
        result = run_import(self.user, rows, commit=True)
        self.assertEqual(result.ok_count, 2)
        # ...and re-importing skips exactly those two.
        again = run_import(self.user, rows + csv_rows("2026-01-01,deposit,,Broker,,,10,,"), commit=False)
        self.assertEqual((again.duplicate_count, again.ok_count), (2, 1))

    def test_italian_values(self):
        rows = parse_csv("data;tipo;titolo;conto;quantità;prezzo;importo;commissioni\n"
                         "05/01/2026;acquisto;ENEL.MI;broker;10;6,50;;1,00\n".encode("utf-8"))
        result = run_import(self.user, rows, commit=True)
        self.assertTrue(result.committed, [r.message for r in result.rows])
        tx = Transaction.objects.get()
        self.assertEqual((tx.tx_type, tx.price, tx.fees), ("buy", D("6.5"), D("1")))
        self.assertEqual(tx.cash_account, self.broker)  # account names match case-insensitively

    def test_other_users_accounts_and_holdings_are_never_used(self):
        bob = User.objects.create_user("bob")
        CashAccount.objects.create(user=bob, name="Bob cash", opening_balance=D("100"))
        Holding.objects.create(user=bob, ticker="TSLA", opening_quantity=D("10"))
        rows = csv_rows("2026-01-01,deposit,,Bob cash,,,10,,", "2026-01-02,sell,TSLA,Broker,1,1,,,")
        result = run_import(self.user, rows, commit=True)
        self.assertEqual([r.status for r in result.rows], ["error", "error"])
        self.assertEqual(Holding.objects.get(user=bob).quantity, D("10"))


class CsvViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.client.force_login(self.user)
        CashAccount.objects.create(user=self.user, name="Broker", opening_balance=D("1000.00"))

    def upload(self, content: str):
        return self.client.post(reverse("wallet:csv_upload"),
                                {"file": SimpleUploadedFile("tx.csv", content.encode(), "text/csv")})

    def test_upload_preview_confirm(self):
        resp = self.upload(SAMPLE_CSV)
        self.assertRedirects(resp, reverse("wallet:csv_preview"), fetch_redirect_response=False)
        preview = self.client.get(reverse("wallet:csv_preview"))
        self.assertContains(preview, "Import 4 transaction(s)")
        self.assertContains(preview, "New holding VWCE.DE will be created")
        self.assertFalse(Transaction.objects.exists())

        resp = self.client.post(reverse("wallet:csv_preview"))
        self.assertRedirects(resp, reverse("wallet:transaction_list"), fetch_redirect_response=False)
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), 4)
        self.assertNotIn("csv_import_rows", self.client.session)

    def test_preview_with_errors_has_no_import_button(self):
        self.upload("date,type,account,amount\n2026-01-01,withdraw,Broker,99999\n")
        preview = self.client.get(reverse("wallet:csv_preview"))
        self.assertContains(preview, "Error")
        self.assertNotContains(preview, "Import 0")
        self.assertNotContains(preview, "Import 1 transaction")
        self.client.post(reverse("wallet:csv_preview"))  # forced confirm still saves nothing
        self.assertFalse(Transaction.objects.exists())

    def test_bad_file_shows_message(self):
        resp = self.upload("hello,world\n1,2\n")
        self.assertRedirects(resp, reverse("wallet:csv_upload"), fetch_redirect_response=False)
        page = self.client.get(reverse("wallet:csv_upload"))
        self.assertContains(page, "Missing column(s)")

    def test_preview_without_upload_redirects(self):
        resp = self.client.get(reverse("wallet:csv_preview"))
        self.assertRedirects(resp, reverse("wallet:csv_upload"), fetch_redirect_response=False)

    def test_sample_download(self):
        resp = self.client.get(reverse("wallet:csv_sample"))
        self.assertEqual(resp["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertEqual(resp.content.decode(), SAMPLE_CSV)

    def test_requires_login(self):
        self.client.logout()
        for name in ("wallet:csv_upload", "wallet:csv_preview", "wallet:csv_sample"):
            with self.subTest(name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 302)
