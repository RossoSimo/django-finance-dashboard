import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from common.market_data import Quote
from wallet.models import CashAccount, Holding

from .market_check import MarketChecker, clean_cash_accounts, to_decimal
from .ollama_client import EXTRACTION_SCHEMA, OllamaError, extract_portfolio_from_screenshot

D = Decimal
User = get_user_model()

# A small fake market: Yahoo-style search results and live quotes.
LISTINGS = {
    "VWCE.DE": ("Vanguard FTSE All-World UCITS ETF", "XETRA", "ETF", 173.48, "EUR"),
    "VWRA.L": ("Vanguard FTSE All-World UCITS ETF", "London", "ETF", 202.10, "USD"),
    "IE00BK5BQT80.SG": ("Vanguard FTSE All-World UCITS E", "Stuttgart", "MUTUALFUND", 173.44, "EUR"),
    "ENEL.MI": ("Enel SpA", "Milan", "EQUITY", 8.918, "EUR"),
    "AAPL": ("Apple Inc.", "NASDAQ", "EQUITY", 340.97, "USD"),
    "VOD.L": ("Vodafone Group Plc", "London", "EQUITY", 116.7, "GBp"),
}
SEARCH = {
    "IE00BK5BQT80": ["VWRA.L", "IE00BK5BQT80.SG"],
    "VANGUARD FTSE ALL-WORLD": ["VWCE.DE", "VWRA.L"],
    "VANGUARD FTSE ALL-WORLD UCITS ETF": ["VWCE.DE", "VWRA.L"],
    "ENEL SPA": ["ENEL.MI"],
    "APPLE INC": ["AAPL"],
    "AAPL": ["AAPL"],
    "VODAFONE": ["VOD.L"],
}


def fake_search(query, limit=5):
    return [{"symbol": s, "name": LISTINGS[s][0], "exchange": LISTINGS[s][1], "quote_type": LISTINGS[s][2]}
            for s in SEARCH.get(query.upper(), [])][:limit]


def fake_quote(symbol):
    if symbol not in LISTINGS:
        return Quote(ticker=symbol, name=None, price=None, previous_close=None, currency=None,
                     change=None, change_pct=None)
    name, _, _, price, currency = LISTINGS[symbol]
    return Quote(ticker=symbol, name=name, price=price, previous_close=None, currency=currency,
                 change=None, change_pct=None)


def fake_fx(from_ccy, to_ccy):
    return {("USD", "EUR"): 0.9, ("EUR", "USD"): 1 / 0.9}.get((from_ccy, to_ccy))


def checker():
    return MarketChecker(search_fn=fake_search, quote_fn=fake_quote, fx_fn=fake_fx)


def row(**kwargs):
    base = {"name": "", "ticker": None, "isin": None, "quantity": None, "price": None,
            "market_value": None, "avg_cost": None, "currency": None}
    return {**base, **kwargs}


class MarketCheckTests(TestCase):
    def test_isin_row_prefers_the_listing_in_the_screenshot_currency(self):
        c = checker().check_holding(row(name="Vanguard FTSE All-World", isin="IE00BK5BQT80",
                                        quantity=12, price=173.48, market_value=2081.76, currency="EUR"))
        self.assertEqual(c["status"], "verified")
        self.assertEqual(c["symbol"], "VWCE.DE")  # not the USD listing, nor the ISIN-named mirror
        self.assertEqual((c["live_price"], c["live_currency"], c["asset_type"]), ("173.48", "EUR", "etf"))
        self.assertEqual(c["quantity"], "12")
        self.assertEqual(c["live_value"], "2081.76")
        self.assertEqual({x["symbol"] for x in c["candidates"]}, {"VWCE.DE", "VWRA.L", "IE00BK5BQT80.SG"})

    def test_price_shown_in_another_currency_is_converted_before_comparing(self):
        c = checker().check_holding(row(name="Apple Inc", quantity=5, price=306.87, currency="EUR"))
        self.assertEqual(c["status"], "verified")  # 340.97 USD * 0.9 = 306.87 EUR
        self.assertEqual(c["symbol"], "AAPL")

    def test_misread_price_is_flagged(self):
        c = checker().check_holding(row(name="Enel SpA", quantity=150, price=89.2, currency="EUR"))
        self.assertEqual(c["status"], "check")
        self.assertEqual(c["symbol"], "ENEL.MI")
        self.assertIn("differs from the screenshot by 90.0%", c["notes"][0])

    def test_inconsistent_quantity_is_flagged(self):
        c = checker().check_holding(row(name="Enel SpA", quantity=15, price=8.92, market_value=1338, currency="EUR"))
        self.assertTrue(any("check the quantity" in n for n in c["notes"]))

    def test_quantity_is_derived_from_value(self):
        c = checker().check_holding(row(name="Apple Inc", market_value=1704.85, currency="USD"))
        self.assertEqual(c["status"], "check")  # value alone can't confirm the listing
        self.assertEqual((c["quantity"], c["quantity_derived"]), ("5", True))
        self.assertTrue(any("Only the total value" in n for n in c["notes"]))

    def test_missing_quantity_without_value_needs_input(self):
        c = checker().check_holding(row(name="Apple Inc", price=340.97, currency="USD"))
        self.assertEqual(c["status"], "check")
        self.assertIsNone(c["quantity"])
        self.assertTrue(any("please fill it in" in n for n in c["notes"]))

    def test_average_cost_kept_when_shown_otherwise_todays_price(self):
        shown = checker().check_holding(row(name="Enel SpA", quantity=10, price=8.92, avg_cost="7,50", currency="EUR"))
        self.assertEqual((shown["avg_cost"], shown["avg_cost_from_market"]), ("7.5", False))
        missing = checker().check_holding(row(name="Enel SpA", quantity=10, price=8.92, currency="EUR"))
        self.assertEqual((missing["avg_cost"], missing["avg_cost_from_market"]), ("8.918", True))

    def test_average_cost_is_converted_to_the_listing_currency(self):
        c = checker().check_holding(row(name="Apple Inc", quantity=1, price=306.87, avg_cost=270, currency="EUR"))
        self.assertEqual(c["live_currency"], "USD")
        self.assertEqual(D(c["avg_cost"]).quantize(D("0.01")), D("300.00"))  # 270 EUR / 0.9

    def test_printed_ticker_is_used(self):
        c = checker().check_holding(row(name="", ticker="aapl", quantity=1, price=340.97, currency="USD"))
        self.assertEqual((c["symbol"], c["status"]), ("AAPL", "verified"))

    def test_pence_listing_compared_in_pounds(self):
        c = checker().check_holding(row(name="Vodafone", quantity=100, price=1.167, currency="GBP"))
        self.assertEqual((c["symbol"], c["live_price"], c["live_currency"], c["status"]),
                         ("VOD.L", "1.167", "GBP", "verified"))

    def test_unknown_security(self):
        c = checker().check_holding(row(name="Zorblax Imaginary Holdings", quantity=3, price=10, currency="EUR"))
        self.assertEqual(c["status"], "not_found")
        self.assertIsNone(c["symbol"])
        self.assertEqual(c["quantity"], "3")

    def test_result_is_json_safe(self):
        c = checker().check_holding(row(name="Enel SpA", quantity=150, price=8.92, currency="EUR"))
        json.dumps(c)

    def test_helpers(self):
        self.assertEqual(to_decimal("1.234,56"), D("1234.56"))
        self.assertEqual(to_decimal(12), D("12"))
        self.assertIsNone(to_decimal("n/a"))
        self.assertEqual(clean_cash_accounts([{"name": " Conto ", "balance": "2.345,67", "currency": "eur"}]),
                         [{"name": "Conto", "currency": "EUR", "balance": "2345.67"}])


class ExtractionRequestTests(TestCase):
    def response(self, content):
        resp = mock.Mock()
        resp.json.return_value = {"message": {"content": content}}
        resp.raise_for_status.return_value = None
        return resp

    @override_settings(OLLAMA_VISION_MODEL="qwen3-vl:8b")
    def test_sends_schema_image_and_zero_temperature(self):
        reply = json.dumps({"holdings": [row(name="ENEL SPA", quantity=150), row(name="")],
                            "cash_accounts": [{"name": "Conto", "balance": 5, "currency": "EUR"}]})
        with mock.patch("assistant.ollama_client.requests.post", return_value=self.response(reply)) as post:
            data = extract_portfolio_from_screenshot(b"fake-png")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "qwen3-vl:8b")
        self.assertEqual(payload["format"], EXTRACTION_SCHEMA)
        self.assertEqual(payload["options"], {"temperature": 0})
        self.assertEqual(len(payload["messages"][0]["images"]), 1)
        self.assertEqual([h["name"] for h in data["holdings"]], ["ENEL SPA"])  # empty rows dropped

    def test_invalid_json_raises_friendly_error(self):
        with mock.patch("assistant.ollama_client.requests.post", return_value=self.response("not json")):
            with self.assertRaises(OllamaError):
                extract_portfolio_from_screenshot(b"x")


@mock.patch("common.market_data.search_securities", fake_search)
@mock.patch("common.market_data.get_quote", fake_quote)
@mock.patch("common.market_data.get_fx_rate", fake_fx)
class ScreenshotImportFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.client.force_login(self.user)

    def upload(self, extracted):
        with mock.patch("assistant.views.extract_portfolio_from_screenshot", return_value=extracted):
            return self.client.post(reverse("assistant:import_upload"),
                                    {"screenshot": SimpleUploadedFile("s.png", b"png", "image/png")})

    def test_upload_review_and_import(self):
        resp = self.upload({
            "holdings": [row(name="ENEL SPA", isin="IT0003128367", quantity=150, price=8.92,
                             market_value=1338, currency="EUR"),
                         row(name="Zorblax", quantity=1, price=1, currency="EUR")],
            "cash_accounts": [{"name": "Conto", "balance": 2345.67, "currency": "EUR"}],
        })
        self.assertRedirects(resp, reverse("assistant:import_preview"), fetch_redirect_response=False)

        page = self.client.get(reverse("assistant:import_preview"))
        self.assertContains(page, "✓ Verified")
        self.assertContains(page, "✗ Not found")
        self.assertContains(page, "ENEL.MI — Enel SpA")
        self.assertContains(page, "1 of 2 verified")
        forms = page.context["holding_formset"].forms
        self.assertEqual(forms[0].initial["ticker"], "ENEL.MI")
        self.assertTrue(forms[0].initial["include"])
        self.assertFalse(forms[1].initial["include"])

        data = {
            "holdings-TOTAL_FORMS": "2", "holdings-INITIAL_FORMS": "0",
            "cash-TOTAL_FORMS": "1", "cash-INITIAL_FORMS": "0",
            "cash-0-include": "on", "cash-0-name": "Conto", "cash-0-currency": "EUR", "cash-0-balance": "2345.67",
        }
        for i, form in enumerate(forms):
            for field in ("ticker", "name", "asset_type", "quantity", "avg_cost", "currency"):
                data[f"holdings-{i}-{field}"] = form.initial[field]
            if form.initial["include"]:
                data[f"holdings-{i}-include"] = "on"
        resp = self.client.post(reverse("assistant:import_preview"), data)
        self.assertRedirects(resp, reverse("wallet:home"), fetch_redirect_response=False)

        enel = Holding.objects.get(user=self.user)
        self.assertEqual((enel.ticker, enel.quantity, enel.avg_cost, enel.currency), ("ENEL.MI", D("150"), D("8.918"), "EUR"))
        self.assertEqual(CashAccount.objects.get(user=self.user).balance, D("2345.67"))

    def test_nothing_found(self):
        resp = self.upload({"holdings": [], "cash_accounts": []})
        self.assertRedirects(resp, reverse("assistant:import_upload"), fetch_redirect_response=False)

    def test_ollama_down(self):
        with mock.patch("assistant.views.extract_portfolio_from_screenshot", side_effect=OllamaError("down")):
            resp = self.client.post(reverse("assistant:import_upload"),
                                    {"screenshot": SimpleUploadedFile("s.png", b"png", "image/png")}, follow=True)
        self.assertContains(resp, "down")


class HoldingImportFormTests(TestCase):
    def form(self, **data):
        from .forms import HoldingImportForm

        base = {"ticker": "", "name": "X", "asset_type": "stock", "quantity": "1", "avg_cost": "0", "currency": "EUR"}
        return HoldingImportForm({**base, **data})

    def test_unticked_rows_need_nothing(self):
        self.assertTrue(self.form(quantity="").is_valid())

    def test_ticked_rows_need_ticker_and_quantity(self):
        form = self.form(include="on", quantity="0")
        self.assertFalse(form.is_valid())
        self.assertEqual(set(form.errors), {"ticker", "quantity"})
        self.assertTrue(self.form(include="on", ticker="enel.mi").is_valid())

    def test_converted_average_cost_fits_the_form(self):
        c = checker().check_holding(row(name="Apple Inc", quantity=1, price=306.87, avg_cost=271.13, currency="EUR"))
        self.assertTrue(self.form(include="on", ticker="AAPL", avg_cost=c["avg_cost"]).is_valid())
