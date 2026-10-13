from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.template import Context, Template
from django.test import TestCase

from common import market_data
from common.templatetags.fmt import money, num, pct, signed_money


class NewsTests(TestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("common.market_data.yf")
    def test_falls_back_to_search_when_ticker_news_is_empty(self, yf):
        yf.Ticker.return_value.news = []
        yf.Search.return_value.news = [
            {"title": "Apple up", "publisher": "IBD", "link": "https://x/1", "providerPublishTime": 1791909732},
        ]
        items = market_data.get_news_for_ticker("aapl")
        self.assertEqual([i.title for i in items], ["Apple up"])
        self.assertEqual(items[0].ticker, "AAPL")
        self.assertIsNotNone(items[0].published.tzinfo)

    @mock.patch("common.market_data.yf")
    def test_mixed_date_formats_sort_newest_first(self, yf):
        nested = {"content": {"title": "Nested", "canonicalUrl": {"url": "https://x/2"},
                              "pubDate": "2026-10-13T10:00:00Z"}}
        flat = {"title": "Flat", "link": "https://x/1", "providerPublishTime": 1700000000}
        undated = {"title": "Undated", "link": "https://x/3"}
        yf.Ticker.return_value.news = [flat, nested, undated]
        items = market_data.get_news_for_tickers(["AAPL"], limit_per_ticker=5)
        self.assertEqual([i.title for i in items], ["Nested", "Flat", "Undated"])
        yf.Search.assert_not_called()


class IntradayHistoryTests(TestCase):
    def setUp(self):
        cache.clear()

    @staticmethod
    def _frame(stamps, closes):
        import pandas
        return pandas.DataFrame({"Close": closes}, index=pandas.DatetimeIndex(stamps, tz="UTC"))

    @mock.patch("common.market_data.yf")
    def test_1d_keeps_last_session_only(self, yf):
        yf.Ticker.return_value.history.return_value = self._frame(
            ["2026-10-08 15:00", "2026-10-09 13:30", "2026-10-09 13:35", "2026-10-09 13:40"],
            [1.0, 2.0, 3.0, float("nan")])
        result = market_data.get_intraday_history("aapl", "1d")
        self.assertEqual([c for _, c in result], [2.0, 3.0])
        self.assertTrue(all(t.tzinfo is not None for t, _ in result))
        self.assertEqual(result[0][0].isoformat(), "2026-10-09T13:30:00+00:00")
        yf.Ticker.return_value.history.assert_called_once()
        kwargs = yf.Ticker.return_value.history.call_args.kwargs
        self.assertEqual((kwargs["interval"], kwargs["period"]), ("5m", "5d"))

    @mock.patch("common.market_data.yf")
    def test_7d_keeps_all_bars_and_is_cached(self, yf):
        yf.Ticker.return_value.history.return_value = self._frame(
            ["2026-10-08 15:00", "2026-10-09 13:30"], [1.0, 2.0])
        self.assertEqual(len(market_data.get_intraday_history("AAPL", "7d")), 2)
        market_data.get_intraday_history("AAPL", "7d")
        self.assertEqual(yf.Ticker.return_value.history.call_count, 1)
        kwargs = yf.Ticker.return_value.history.call_args.kwargs
        self.assertEqual((kwargs["interval"], kwargs["period"]), ("60m", "7d"))

    @mock.patch("common.market_data.yf")
    def test_failure_returns_empty_and_is_not_cached(self, yf):
        yf.Ticker.return_value.history.side_effect = RuntimeError("boom")
        with self.assertLogs("common.market_data", level="ERROR"):
            self.assertEqual(market_data.get_intraday_history("AAPL", "1d"), [])
        with self.assertLogs("common.market_data", level="ERROR"):
            market_data.get_intraday_history("AAPL", "1d")
        self.assertEqual(yf.Ticker.return_value.history.call_count, 2)

    @mock.patch("common.market_data.yf")
    def test_unknown_range(self, yf):
        self.assertEqual(market_data.get_intraday_history("AAPL", "1m"), [])
        yf.Ticker.assert_not_called()


class FormatTests(TestCase):
    def test_money(self):
        self.assertEqual(money(Decimal("1234.5"), "EUR"), "1.234,50\u00a0€")
        self.assertEqual(money(-1234.5, "USD"), "−1.234,50\u00a0$")
        self.assertEqual(money(Decimal("1234567.891"), "SEK"), "1.234.567,89\u00a0SEK")
        self.assertEqual(money(Decimal("1250"), "GBp"), "12,50\u00a0£")
        self.assertEqual(money(None, "EUR"), "—")

    def test_symbol_uses_non_breaking_space(self):
        self.assertIn("\u00a0", money(Decimal("1"), "EUR"))
        self.assertNotIn(" ", money(Decimal("1"), "EUR"))
        self.assertIn("\u00a0", pct(1))
        self.assertNotIn(" ", pct(1))

    def test_signed_money(self):
        self.assertEqual(signed_money(Decimal("12.3"), "EUR"), "+12,30\u00a0€")
        self.assertEqual(signed_money(Decimal("-12.3"), "EUR"), "−12,30\u00a0€")

    def test_num(self):
        self.assertEqual(num(Decimal("0.0500")), "0,05")
        self.assertEqual(num(Decimal("12")), "12")
        self.assertEqual(num(1234.5, 2), "1.234,50")

    def test_pct(self):
        self.assertEqual(pct(2.345), "+2,35\u00a0%")
        self.assertEqual(pct(Decimal("-1.1")), "−1,10\u00a0%")

    def test_negative_zero_has_no_sign(self):
        self.assertEqual(money(Decimal("-0.004"), "EUR"), "0,00\u00a0€")
        self.assertEqual(pct(-0.001), "0,00\u00a0%")

    def test_template_builtin(self):
        self.assertEqual(Template("{{ v|money:'EUR' }}").render(Context({"v": 5})), "5,00\u00a0€")


class FormMixinTests(TestCase):
    def test_decimal_and_date_widgets(self):
        from django.contrib.auth.models import User

        from wallet.forms import TransactionForm

        form = TransactionForm(user=User.objects.create_user("u"))
        self.assertEqual(form.fields["amount"].widget.attrs["inputmode"], "decimal")
        self.assertEqual(form.fields["date"].widget.input_type, "date")
        self.assertEqual(form.fields["date"].widget.format, "%Y-%m-%d")


class ComponentTests(TestCase):
    def test_change_has_arrow_sign_and_text(self):
        from django.template.loader import render_to_string

        html = render_to_string(
            "components/_change.html", {"value": Decimal("-12.3"), "pct": Decimal("-1.1"), "currency": "EUR"}
        )
        self.assertIn("▼", html)
        self.assertIn("−12,30 €", html)
        self.assertIn("change-down", html)
        up = render_to_string("components/_change.html", {"value": Decimal("12.3"), "pct": Decimal("2.35"), "currency": "EUR"})
        self.assertIn("▲", up)
        self.assertIn("change-up", up)
        flat = render_to_string("components/_change.html", {"value": Decimal("0"), "pct": None, "currency": "EUR"})
        self.assertIn("change-flat", flat)
        self.assertNotIn("▲", flat)
        self.assertNotIn("▼", flat)

    def test_row_actions_post_items_have_csrf_and_confirm(self):
        from django.template.loader import render_to_string
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        html = render_to_string(
            "components/_row_actions.html",
            {
                "label": "Actions for AAPL",
                "items": [
                    {"url": "/edit/", "label": "Edit AAPL", "icon": "pencil"},
                    {"url": "/x/", "label": "Remove AAPL", "post": True, "confirm": "Remove AAPL?", "danger": True},
                ],
            },
            request=request,
        )
        self.assertIn('name="csrfmiddlewaretoken"', html)
        self.assertIn('data-confirm="Remove AAPL?"', html)
        self.assertIn('aria-label="Actions for AAPL"', html)
        self.assertIn('<a class="dropdown-item"', html)
        self.assertIn("text-danger", html)
        self.assertIn("data-bs-popper-config='{&quot;strategy&quot;:&quot;fixed&quot;}'".replace("&quot;", '"'), html)

    def test_form_fields_marks_errors(self):
        from django import forms
        from django.template.loader import render_to_string

        class F(BootstrapFormMixinForTest):
            name = forms.CharField()
            agree = forms.BooleanField()

        html = render_to_string("components/_form_fields.html", {"form": F(data={})})
        self.assertIn("is-invalid", html)
        self.assertIn("invalid-feedback", html)
        self.assertIn("form-switch", html)

    def test_form_fields_non_field_errors_focusable(self):
        from django import forms
        from django.template.loader import render_to_string

        class F(forms.Form):
            def clean(self):
                raise forms.ValidationError("Broken")

        html = render_to_string("components/_form_fields.html", {"form": F(data={})})
        self.assertIn('tabindex="-1"', html)
        self.assertIn("data-autofocus", html)
        self.assertIn("Broken", html)

    def test_simple_components_render(self):
        from django.template.loader import render_to_string

        header = render_to_string(
            "components/_page_header.html",
            {"title": "Wallet", "primary_url": "/new/", "primary_label": "Add", "menu": [{"url": "/i/", "label": "Import", "icon": "upload"}]},
        )
        self.assertIn("Wallet", header)
        self.assertIn("Import", header)
        stat = render_to_string(
            "components/_stat.html",
            {"label": "Net worth", "value": "1.000,00 €", "change": Decimal("5"), "change_pct": Decimal("1"), "currency": "EUR", "stat_id": "nw"},
        )
        self.assertIn('id="nw"', stat)
        self.assertIn("change-up", stat)
        empty = render_to_string("components/_empty.html", {"icon": "wallet2", "text": "Nothing yet", "action_url": "/a/", "action_label": "Add one"})
        self.assertIn("Add one", empty)
        notice = render_to_string("components/_notice.html", {"level": "warning", "text": "<b>x</b>"})
        self.assertIn("alert-warning", notice)
        self.assertIn("&lt;b&gt;", notice)


from django import forms as _forms  # noqa: E402

from common.forms import BootstrapFormMixin  # noqa: E402


class BootstrapFormMixinForTest(BootstrapFormMixin, _forms.Form):
    pass
