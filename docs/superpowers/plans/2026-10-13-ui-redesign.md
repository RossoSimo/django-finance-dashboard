# UI/UX Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every page polished, consistent and fully usable on a phone; add 1D/7D chart ranges, live-price last points, and a dashboard net-worth chart built from market history.

**Architecture:** A shared front-end layer (`static/css/app.css`, `static/js/app.js`, `static/js/charts.js`, `templates/components/`, `fmt` template filters) on Bootstrap 5.3, with a new `base.html` (sidebar on desktop, bottom tab bar on mobile). Chart data comes from `performance.services` (daily path unchanged in shape, new intraday path) through one payload builder used by pages and a JSON endpoint.

**Tech Stack:** Python 3.14, Django 6.1, yfinance 1.7, Bootstrap 5.3.3 + Bootstrap Icons 1.11.3 (jsDelivr), Chart.js 4.4.4.

**Spec:** `docs/superpowers/specs/2026-10-13-ui-redesign-design.md` (read it first; this plan does not repeat page-level visual detail).

## Global Constraints

- No build step, no JS framework, no model changes, no migrations. Theme preference only in `localStorage` (key `financedash-theme`, values `light`/`dark`; absent = system).
- CDN pins: `bootstrap@5.3.3`, `bootstrap-icons@1.11.3`, `chart.js@4.4.4`, all from `cdn.jsdelivr.net`.
- Every query scoped to `request.user`; other users' objects → 404.
- Money is `Decimal` in Python; floats only in chart JSON.
- Missing FX → amount excluded and reported in `missing_currencies`, never summed 1:1.
- Market data never crashes a page (wrappers return `[]`/`None` and log).
- Number format: thousands `.`, decimals `,`, symbol after the number with U+00A0, minus is U+2212, `None` → `—`.
- No colour-only signals: gains/losses carry ▲/▼ and a sign; status badges carry text.
- Inputs have labels; chart data via `json_script`; all tables inside `table-responsive`.
- Tests never hit the network: mock `common.market_data.*` or patch `common.market_data.yf` / `requests.post`.
- Windows: run tools as `.venv/Scripts/python.exe`; write files with `encoding="utf-8"`.
- User-facing strings currently asserted by tests must survive (e.g. "Nothing to chart yet", "✓ Verified", "✗ Not found", "1 of 2 verified", "Price alert triggered", "hit 101 USD", "left out of the totals", "Delete them first", "delete this transaction", "only hold 1 AAPL", "allow negative balance"); only number-format assertions change (Task 7 and Task 9 list them).
- Commit messages explain *why* and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A holding with no intraday bars on 1D/7D** (many European funds only have daily NAVs): it should appear as a flat line at its last daily close, not be dropped or valued at cost. → Task 5, `test_intraday_holding_without_bars_is_flat_at_last_close`.
2. **Mixed sessions on a weekend** (crypto traded today, stocks last on Friday): 1D covers the most recent session date across holdings; holdings whose session ended earlier stay flat at their last price. → Task 5, `test_1d_uses_latest_session_and_holds_older_sessions_flat`.
3. **Cash-only portfolio on 1D/7D**: chart still has points (flat cash line), no crash or empty chart. → Task 5, `test_intraday_cash_only_portfolio_has_points`.
4. **Live quote in pence (`GBp`)**: the live last point is normalised to GBP like the history, not 100× too big. → Task 5, `test_live_last_point_normalises_pence`.
5. **Rounding to zero and negative zero**: `money(Decimal("-0.004"), "EUR")` shows `0,00 €`, never `−0,00 €`; `pct(-0.001)` shows `0,00 %` without sign. → Task 1, `test_negative_zero_has_no_sign`.

---

### Task 1: Number formatting filters

**Files:**
- Create: `common/templatetags/__init__.py`, `common/templatetags/fmt.py`
- Modify: `financedash/settings.py` (TEMPLATES `OPTIONS["builtins"] = ["common.templatetags.fmt"]` so no template needs `{% load fmt %}`)
- Test: `common/tests.py` (new class `FormatTests`)

**Interfaces:**
- Produces (all in `common/templatetags/fmt.py`):
  - `CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF", "JPY": "¥"}`
  - `format_number(value, decimals: int | None = 2, signed: bool = False) -> str` — `None` → `"—"`; `decimals=None` trims trailing zeros (max 8 places); accepts `Decimal`, `int`, `float` (floats via `Decimal(str(x))`); rounds `ROUND_HALF_UP`; a value that rounds to zero never gets a sign.
  - filters `money(value, currency)`, `signed_money(value, currency)`, `num(value, decimals=None)`, `pct(value)` (always signed, 2 decimals, `" %"` suffix), `currency_symbol(code)`.
  - `money`/`signed_money` run `common.currency.normalize(value, currency)` first (so `GBp` pence become GBP pounds).

- [ ] **Step 1: Write the failing tests** in `FormatTests`:

```python
def test_money(self):
    self.assertEqual(money(Decimal("1234.5"), "EUR"), "1.234,50 €")
    self.assertEqual(money(-1234.5, "USD"), "−1.234,50 $")
    self.assertEqual(money(Decimal("1234567.891"), "SEK"), "1.234.567,89 SEK")
    self.assertEqual(money(Decimal("1250"), "GBp"), "12,50 £")
    self.assertEqual(money(None, "EUR"), "—")
def test_signed_money(self):
    self.assertEqual(signed_money(Decimal("12.3"), "EUR"), "+12,30 €")
    self.assertEqual(signed_money(Decimal("-12.3"), "EUR"), "−12,30 €")
def test_num(self):
    self.assertEqual(num(Decimal("0.0500")), "0,05")
    self.assertEqual(num(Decimal("12")), "12")
    self.assertEqual(num(1234.5, 2), "1.234,50")
def test_pct(self):
    self.assertEqual(pct(2.345), "+2,35 %")
    self.assertEqual(pct(Decimal("-1.1")), "−1,10 %")
def test_negative_zero_has_no_sign(self):
    self.assertEqual(money(Decimal("-0.004"), "EUR"), "0,00 €")
    self.assertEqual(pct(-0.001), "0,00 %")
def test_template_builtin(self):
    self.assertEqual(Template("{{ v|money:'EUR' }}").render(Context({"v": 5})), "5,00 €")
```

- [ ] **Step 2: Run** `.venv/Scripts/python.exe manage.py test common.tests.FormatTests` — expected: ImportError / FAIL.
- [ ] **Step 3: Implement** `fmt.py` per the Interfaces block and register the builtin in settings.
- [ ] **Step 4: Run** the same command — expected: OK. Then the whole suite — expected: OK.
- [ ] **Step 5: Commit** `common/templatetags/ financedash/settings.py common/tests.py` — "Add Italian-style number formatting filters".

---

### Task 2: App shell — assets, base layouts, navigation

**Files:**
- Create: `static/css/app.css`, `static/js/app.js`, `templates/base_auth.html`, `templates/components/_nav_items.html` (one list rendered by both sidebar and More sheet), `common/context_processors.py`, `common/test_pages.py`
- Modify: `templates/base.html` (rewrite), `financedash/settings.py` (add `common.context_processors.nav`), `templates/registration/login.html`, `signup.html`, `password_change_done.html` (extend `base_auth.html`), `templates/alerts/_banner.html` (restyle as notice; keep its text)

**Interfaces:**
- Produces:
  - `common.context_processors.nav(request) -> {"nav_active": str}` — derived from `request.resolver_match`: namespace `dashboard`→`"dashboard"`, `wallet`→`"wallet"`, `performance`→`"performance"`, `watchlist`→`"watchlist"`, `alerts`→`"alerts"`, `newsfeed`→`"news"`, `assistant` + url_name starting `import`→`"import"`, other `assistant`→`"assistant"`, url names `settings`/`password_change*`→`"settings"`; else `""`. (Deviation from the spec's per-template `{% block nav %}`: same result, no per-template edits.)
  - `base.html` blocks: `title`, `page_title` (mobile top bar text), `content`, `scripts` (page JS, after `app.js`/`charts.js`), `sticky_footer` (optional mobile action bar).
  - `app.css` tokens on `:root` / `[data-bs-theme="dark"]`: `--fd-surface`, `--fd-surface-2`, `--fd-text`, `--fd-muted`, `--fd-border`, `--fd-accent`, `--fd-positive`, `--fd-negative`, `--fd-warning`, `--fd-radius`; utility classes `.figure` (tabular-nums), `.text-up`, `.text-down`.
  - `app.js` globals: `window.FD.setTheme(mode)` (`"light"|"dark"|"system"`; sets `data-bs-theme`, stores/clears localStorage, dispatches `fd:themechange` on `document`); auto-inits toasts on `.toast` elements (non-`error` ones `autohide`, 5 s).
  - Nav item element ids/labels: links carry `aria-current="page"` when `nav_active` matches; Alerts link shows a `badge` with `unseen_alerts|length` plus visually-hidden " triggered".
- Layout per spec §1 "Layout and navigation" (sidebar ≥ 992 px; mobile top bar + bottom tab bar Dashboard/Wallet/Performance/Alerts/More; More = Bootstrap offcanvas with Watchlist, News, Assistant, Import, Settings, Admin (staff), Log out form). Messages render as Bootstrap toasts in a `.toast-container`; `message.tags == "error"` → no autohide. The pre-paint theme script stays in `<head>`.

- [ ] **Step 1: Write failing tests** in `common/test_pages.py`:

```python
@mock.patch("common.market_data.yf", None)
@mock.patch("requests.post", side_effect=AssertionError("network"))
class PageSmokeTests(TestCase):
    # setUp: user alice logged in; a CashAccount, a Holding AAPL, a buy Transaction, a WatchlistItem, a PriceAlert
    def test_every_page_renders(self, *_):
        # GET each and assert 200: dashboard:home, wallet:home, wallet:add_cash_account, wallet:add_holding,
        # wallet:transaction_list, wallet:add_transaction, wallet:edit_transaction(tx), wallet:delete_transaction(tx),
        # wallet:csv_upload, watchlist:home, watchlist:add, newsfeed:home, alerts:home, alerts:add,
        # performance:portfolio, performance:holding(holding), assistant:chat, assistant:import_upload,
        # settings, password_change, password_change_done
    def test_active_nav_item(self, *_):
        resp = self.client.get(reverse("wallet:home"))
        self.assertContains(resp, 'href="/wallet/" aria-current="page"', count=2)  # sidebar + tab bar
    def test_auth_pages_have_no_app_navigation(self, *_):
        self.client.logout()
        for name in ("login", "signup"):
            self.assertNotContains(self.client.get(reverse(name)), 'id="app-sidebar"')
    def test_messages_render_as_toasts(self, *_):
        # POST a valid watchlist add (follow=True) → assertContains 'class="toast'
```

- [ ] **Step 2: Run** `.venv/Scripts/python.exe manage.py test common.test_pages` — expected: FAIL on nav/toast/auth assertions.
- [ ] **Step 3: Implement** the context processor, `app.css`, `app.js`, `base.html`, `base_auth.html`, `_nav_items.html`; move existing `.badge-change`/`.positive`/`.negative`/chat styles into `app.css` (keep class names working until pages are migrated); point auth templates at `base_auth.html`.
- [ ] **Step 4: Run** `common.test_pages` then the full suite — expected: OK.
- [ ] **Step 5: Manual check** with `runserver`: 375 px and desktop, light/dark, theme toggle switches without reload, More sheet opens, nothing hidden behind the tab bar.
- [ ] **Step 6: Commit** — "New app shell: sidebar and mobile tab bar, shared stylesheet, toasts".

---

### Task 3: Shared components and form rendering

**Files:**
- Create: `templates/components/_page_header.html`, `_stat.html`, `_change.html`, `_empty.html`, `_row_actions.html`, `_notice.html`, `_form_fields.html`, `_form_card.html`, `_confirm_delete.html`
- Modify: `static/css/app.css` (`.table-stack`, `.stat-row`, `.chip-row`, `.sticky-actions`, `.drop-zone`), `static/js/app.js` (confirm dialog, busy buttons, drop zone), `common/forms.py` (`BootstrapFormMixin`), `templates/base.html` (one shared confirm `<div class="modal" id="confirm-modal">`)
- Delete: `templates/registration/_form_fields.html` (callers switch to `components/_form_fields.html`)
- Test: `common/tests.py` (`FormMixinTests`, `ComponentTests`)

**Interfaces:**
- Produces (include parameters, used by Tasks 7–12):
  - `_page_header.html`: `title`, `subtitle` (opt), `primary_url` + `primary_label` + `primary_icon` (opt), and `menu` = a list of `{"url", "label", "icon"}` dicts built in the view (or `None`). On ≥ 768 px `menu` items render as outline buttons; below, as a "⋯" dropdown.
  - `_stat.html`: `label`, `value` (pre-formatted string), `change` (Decimal|None), `change_pct` (Decimal|None), `currency`, `footnote`, `size` (`"lg"` or default), `stat_id` (opt; `id` used by charts.js to update in place).
  - `_change.html`: `value`, `pct`, `currency` → `<span class="change change-up|down|flat">▲ +12,30 € (+2,35 %)</span>`; either of `value`/`pct` may be `None`.
  - `_empty.html`: `icon` (Bootstrap Icons name), `text`, `action_url`/`action_label`, `action2_url`/`action2_label` (opt).
  - `_row_actions.html`: `label` (accessible, e.g. "Actions for AAPL"); items via the `items` list of `{"url", "label", "icon", "post": bool, "confirm": str|None, "danger": bool}`; `post` items render a `<form method="post">` with `{% csrf_token %}` and `data-confirm`.
  - `_notice.html`: `level` (`warning|info|danger`), `text` (safe HTML not allowed; plain text).
  - `_form_fields.html`: `form`; renders non-field errors (with `tabindex="-1"` and `data-autofocus`), every visible field (checkboxes as `form-switch`), hidden fields, `is-invalid` + `invalid-feedback` on errors.
  - `_form_card.html`: `form`, `submit_label`, `cancel_url`; wraps fields + sticky footer.
  - `_confirm_delete.html`: `object_label`, `consequence` (text), `cancel_url`; posts to the current URL.
  - `app.js`: forms with `data-confirm` open `#confirm-modal` (text = attribute value) and submit on confirm; forms with `data-busy="…"` disable their submit button, show a spinner + the text, and ignore further submits; `.drop-zone` wraps an `<input type=file>`, highlights on dragover, sets the input's files on drop, shows the chosen file name.
  - `BootstrapFormMixin`: additionally sets `inputmode="decimal"` on `DecimalField`/`FloatField` widgets and `type="date"` on `DateField` widgets (keeps the existing class logic).

- [ ] **Step 1: Write failing tests:**

```python
class FormMixinTests(TestCase):
    def test_decimal_and_date_widgets(self):
        form = TransactionForm(user=User.objects.create_user("u"))   # wallet.forms
        self.assertEqual(form.fields["amount"].widget.attrs["inputmode"], "decimal")
        self.assertEqual(form.fields["date"].widget.input_type, "date")

class ComponentTests(TestCase):
    def test_change_has_arrow_sign_and_text(self):
        html = render_to_string("components/_change.html", {"value": D("-12.3"), "pct": D("-1.1"), "currency": "EUR"})
        self.assertIn("▼", html); self.assertIn("−12,30 €", html); self.assertIn("change-down", html)
    def test_row_actions_post_items_have_csrf_and_confirm(self):
        # render with a RequestFactory request; items=[{"url": "/x/", "label": "Remove AAPL", "post": True, "confirm": "Remove AAPL?", "danger": True}]
        # assert 'name="csrfmiddlewaretoken"', 'data-confirm="Remove AAPL?"', 'aria-label="Actions for AAPL"'
    def test_form_fields_marks_errors(self):
        # bound invalid form → 'is-invalid' and 'invalid-feedback' in render
```

- [ ] **Step 2: Run** `manage.py test common.tests` — expected: FAIL.
- [ ] **Step 3: Implement** the components, CSS, JS and mixin change; switch `registration/*` and `wallet/form.html`, `watchlist/form.html` to `_form_card.html` / `_form_fields.html`.
- [ ] **Step 4: Run** full suite — expected: OK.
- [ ] **Step 5: Commit** — "Add shared UI components and consistent form rendering".

---

### Task 4: Intraday price history and new ranges

**Files:**
- Modify: `common/market_data.py`, `financedash/settings.py`, `performance/services.py` (`RANGES`, `INTRADAY_RANGES`, `range_start`)
- Test: `common/tests.py` (`IntradayHistoryTests`), `performance/tests.py` (`RangeTests`)

**Interfaces:**
- Produces:
  - `settings.INTRADAY_CACHE_SECONDS = {"1d": 300, "7d": 900}`
  - `common.market_data.INTRADAY_PARAMS = {"1d": {"interval": "5m", "period": "5d"}, "7d": {"interval": "60m", "period": "7d"}}`
  - `get_intraday_history(symbol: str, range_key: str) -> list[tuple[datetime, float]]` — aware UTC datetimes, oldest first, NaN skipped. For `"1d"` keep only bars whose date in `settings.TIME_ZONE` equals the date of the last bar (last session). Cache key `intraday:{SYMBOL}:{range_key}`; failures logged, return `[]`, not cached. Unknown `range_key` → `[]`.
  - `performance.services.RANGES` (ordered): `"1d": ("1D", 1)`, `"7d": ("7D", 7)`, then existing `1m, 3m, 6m, ytd, 1y, max`. `INTRADAY_RANGES = {"1d", "7d"}`. `DEFAULT_RANGE = "6m"`, new `DASHBOARD_DEFAULT_RANGE = "1m"`.

- [ ] **Step 1: Write failing tests:**

```python
@mock.patch("common.market_data.yf")
def test_1d_keeps_last_session_only(self, yf):
    # history() returns a DataFrame with Close at 2026-10-08 15:00Z, 2026-10-09 13:30Z, 2026-10-09 13:35Z
    # → result has the two 2026-10-09 points, tz-aware, oldest first; yf.Ticker().history called with interval="5m", period="5d"
def test_failure_returns_empty_and_is_not_cached(self, yf): ...  # history raises → [] ; second call calls yf again
def test_unknown_range(self, yf): self.assertEqual(get_intraday_history("AAPL", "1m"), [])
# RangeTests:
def test_short_ranges(self):
    today = date(2026, 10, 12)
    self.assertEqual(range_start("1d", today, None), date(2026, 10, 11))
    self.assertEqual(range_start("7d", today, None), date(2026, 10, 5))
    self.assertEqual(list(RANGES)[:3], ["1d", "7d", "1m"])
```

Build the fake frame with `pandas.DataFrame({"Close": [...]}, index=pandas.DatetimeIndex([...], tz="UTC"))` (pandas ships with yfinance).

- [ ] **Step 2: Run** `manage.py test common.tests.IntradayHistoryTests performance.tests.RangeTests` — expected: FAIL.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** full suite — expected: OK (existing `?range=` links now also show 1D/7D; the 1D/7D pages are wired in Task 5).
- [ ] **Step 5: Commit** — "Add intraday price history and 1D/7D ranges".

---

### Task 5: Intraday portfolio/holding history and live last point

**Files:**
- Modify: `performance/services.py`
- Test: `performance/tests.py` (`IntradayPortfolioTests`, `LiveLastPointTests`, `HoldingHistoryTests`)

**Interfaces:**
- Consumes: `get_intraday_history`, `get_price_history`, `get_quotes`/`get_quote`, `CurrencyConverter`, `INTRADAY_RANGES` (Task 4).
- Produces:
  - `PortfolioHistory` and `HoldingHistory` gain `intraday: bool = False` and `session_date: date | None = None`. For intraday results `days` holds aware `datetime`s (template/JSON code must call `.isoformat()` only).
  - `portfolio_history(user, start, end, history_fn=None, quotes=None, converter=None, *, today=None)` — unchanged signature plus `today` (defaults to `timezone.localdate()`): when `end == today`, the last point uses live quotes (below).
  - `portfolio_history_intraday(user, range_key, intraday_fn=None, history_fn=None, quotes=None, converter=None, *, now=None) -> PortfolioHistory`
  - `holding_history(holding, start, end, history_fn=None, quote=None, *, today=None)` — same live-last-point rule.
  - `holding_history_intraday(holding, range_key, intraday_fn=None, history_fn=None, quote=None, *, now=None) -> HoldingHistory`
  - `history_for_range(user, key, *, holding=None, earliest=None) -> PortfolioHistory | HoldingHistory` — dispatches daily vs intraday (views use only this).

**Live last point (daily and intraday):** for each holding with `quote.price is not None`, the price at the final point is `normalize(Decimal(str(quote.price)), quote.currency)`; FX for the final point uses `converter.rate()` (current rate), not FX history. Holdings without a live price keep the historical value. `HoldingPerformance.last_price` reflects it.

**Intraday algorithm** (portfolio):

```
bucket = 5 min for "1d", 60 min for "7d"; floor every bar time to its bucket (UTC)
bars[h] = intraday_fn(h.ticker, key) per held ticker
1D: session_date = max(local date of last bar across holdings) or today if none;
    keep only bars on session_date (local)
7D: window start = now - 7 days
grid = sorted(union of bucketed times); if empty → [start_of_session_local, now]
       always append `now` as the final point (live point)
seed[h] = last daily close strictly before the window (history_fn(h.ticker, window_start_date - 10 days))
value of h at t = last bar ≤ t, else seed[h]; if neither → avg cost (h in missing_prices)
qty/avg at t = ledger replay up to t's local date
FX: converter.rate(currency) for every point (missing → excluded + missing_currencies)
cash at t = ledger replay up to t's local date, converted with converter
```

`holding_history_intraday` uses the same grid rules for one holding (`trades` = buy/sell on dates inside the window).

- [ ] **Step 1: Write failing tests** (helpers: `fake_intraday(series)` like `fake_history`; fixed `now = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)` (a Sunday)):

```python
def test_grid_aligns_and_forward_fills_two_holdings(self):
    # AAPL bars at 13:30, 13:40; MSFT at 13:35 (same session) → grid has 13:30, 13:35, 13:40 + now;
    # at 13:35 AAPL value uses its 13:30 price
def test_intraday_holding_without_bars_is_flat_at_last_close(self):
    # FUND has no bars, daily close 50 on previous day → every point values FUND at qty*50; FUND not in missing_prices
def test_1d_uses_latest_session_and_holds_older_sessions_flat(self):
    # BTC-EUR bars on Sun 2026-10-11, AAPL bars on Fri 2026-10-09 → session_date == 2026-10-11;
    # grid only Sunday times; AAPL flat at its last Friday bar
def test_intraday_cash_only_portfolio_has_points(self):
    # no holdings, cash 1000 EUR → len(days) >= 2, net_worth all 1000
def test_intraday_missing_fx_is_excluded_and_reported(self):
    # USD holding, converter rate None → holdings_value 0, missing_currencies == ["USD"]
def test_live_last_point_uses_quote(self):        # daily, end == today == day(6): last value uses quote price 12 not close 10
def test_live_quote_ignored_when_range_ends_in_past(self):  # today=day(7), end=day(6) → close used
def test_live_last_point_normalises_pence(self):  # quote price 1250 GBp, qty 2 → last holdings value 25 GBP-converted
def test_holding_intraday_trades_in_window(self): ...
```

Existing daily tests keep passing unchanged (they pass `end=day(6)`; give `history()` a default `today=day(30)` so live quotes don't apply).

- [ ] **Step 2: Run** `manage.py test performance` — expected: new tests FAIL.
- [ ] **Step 3: Implement** per Interfaces and algorithm; update the module docstring (intraday + live last point).
- [ ] **Step 4: Run** full suite — expected: OK.
- [ ] **Step 5: Commit** — "Chart history: intraday ranges and a live-price last point".

---

### Task 6: Chart payloads, JSON endpoint and charts.js

**Files:**
- Create: `performance/payloads.py`, `static/js/charts.js`
- Modify: `performance/views.py`, `performance/urls.py`
- Test: `performance/tests.py` (`ChartDataViewTests`)

**Interfaces:**
- Consumes: `history_for_range` (Task 5), `fmt.money`/`signed_money`/`pct` (Task 1).
- Produces:
  - `performance.payloads.portfolio_payload(history) -> dict` = `{"range", "intraday", "session_date" (iso|None), "currency", "labels": [iso], "series": {"net_worth", "holdings_value", "invested"}, "stats": {"net_worth": str, "change": str, "change_pct": str, "direction": "up|down|flat", "holdings_value": str, "invested": str, "unrealized_gain": str}}` — `stats` strings pre-formatted with the `fmt` functions so JS never formats money.
  - `performance.payloads.holding_payload(history) -> dict` = same envelope with `series: {"price", "avg_cost"}`, `trades` (existing shape, x snapped to plotted points), `stats: {"last_price", "change_pct", "direction"}`.
  - `performance.views.resolve_range(request, default) -> str` (unknown → default).
  - URL `performance:chart_data` → `/performance/chart-data/?range=<key>[&holding=<pk>]` → `JsonResponse(payload)`; holding not owned → 404; login required (same as other views).
  - `charts.js` globals: `FD.charts.line(canvas, payload, seriesConfig)`, `FD.charts.doughnut(canvas, labels, values, colors)`, `FD.charts.bindRanges(navEl, canvasChart, endpointUrl, onStats)`; reads colours from `--fd-*` CSS variables; re-reads them on `fd:themechange`; formats with `Intl.NumberFormat("it-IT", {style: "currency", currency})`; intraday labels shown as `HH:mm` (1D) / `ddd HH:mm` (7D) in the browser's time zone; chart containers use fixed CSS heights (`.chart-box` 280 px, 220 px under 576 px) with `maintainAspectRatio: false`.
  - `bindRanges` intercepts clicks on range links, fetches `endpointUrl + "?range=" + key` (+ `&holding=`), updates the chart, updates elements with `data-stat="<name>"` from `payload.stats`, marks the active link (`aria-current`), and calls `history.replaceState`. Without JS the links reload the page.

- [ ] **Step 1: Write failing tests:**

```python
def test_portfolio_json(self, *mocks):
    CashAccount.objects.create(user=self.user, name="Bank", currency="EUR", opening_balance=D("500"))
    data = self.client.get(reverse("performance:chart_data") + "?range=1m").json()
    self.assertEqual(data["range"], "1m"); self.assertFalse(data["intraday"])
    self.assertEqual(len(data["labels"]), 31); self.assertEqual(data["stats"]["net_worth"], "500,00 €")
def test_intraday_json(self, *mocks): ...        # ?range=1d with get_intraday_history mocked → intraday True, session_date set
def test_holding_json_and_other_users_holding_404(self, *mocks): ...
def test_unknown_range_falls_back(self, *mocks): ...  # ?range=bogus → "6m"
def test_requires_login(self, *mocks): ...
```

(Add `@mock.patch("common.market_data.get_intraday_history", return_value=[])` to the `PerformanceViewTests`-style decorator stack.)

- [ ] **Step 2: Run** `manage.py test performance.tests.ChartDataViewTests` — expected: FAIL.
- [ ] **Step 3: Implement** payloads, view, URL and `charts.js`; make `portfolio`/`holding_detail` views build their context from the same payload functions and `history_for_range`.
- [ ] **Step 4: Run** full suite — expected: OK.
- [ ] **Step 5: Commit** — "Serve chart data as JSON and switch ranges without reloading".

---

### Task 7: Dashboard and Performance pages

**Files:**
- Modify: `dashboard/views.py`, `templates/dashboard/home.html`, `templates/performance/portfolio.html`, `templates/performance/holding.html`, `templates/performance/_ranges.html`
- Test: `wallet/tests.py:770,786`, `performance/tests.py:209`, `dashboard` tests in `accounts/tests.py` / `wallet/tests.py` / `alerts/tests.py` (decorators), new `DashboardChartTests` in `performance/tests.py`

**Interfaces:**
- Consumes: `history_for_range`, `portfolio_payload`, `DASHBOARD_DEFAULT_RANGE`, components (Task 3), `charts.js` (Task 6).
- Produces: `dashboard/views.py` imports `history_for_range` by name (so tests patch `dashboard.views.history_for_range`). Dashboard context keys `chart` (portfolio payload via `json_script` id `networth-data`), `range_key`, `ranges`, `movers` (list of `{"ticker", "change_pct"}` sorted desc, top 5 + bottom 5 deduped), `allocation` (labels/values/colors via `json_script` id `allocation-data`). `chart_data_json`, `has_snapshots` and the snapshot query are removed; `record_snapshot` stays.

- [ ] **Step 1: Write failing tests:**

```python
@mock.patch("dashboard.views.history_for_range")
def test_dashboard_chart_uses_portfolio_history(self, hist, *mocks):
    hist.return_value = <PortfolioHistory with 2 days, net_worth [100, 110], base EUR>
    resp = self.client.get(reverse("dashboard:home"))
    hist.assert_called_once_with(self.user, "1m", earliest=mock.ANY)
    self.assertContains(resp, 'id="networth-data"')
    self.assertEqual(NetWorthSnapshot.objects.filter(user=self.user).count(), 1)  # still recorded
def test_dashboard_range_param(self, hist, *mocks): ...  # ?range=7d → called with "7d"
```

Update number-format assertions: `"500.00 EUR"` → `"500,00 €"`; `"0.00 EUR"` → `"0,00 €"`; `"0.00 USD"` → `"0,00 $"`. Existing dashboard tests that patch `dashboard.views.get_news_for_tickers` etc. also need `common.market_data.get_intraday_history` patched if they render 1D (they don't by default).

- [ ] **Step 2: Run** `manage.py test performance dashboard wallet accounts alerts` — expected: FAIL on new/updated assertions.
- [ ] **Step 3: Implement** the view change and both pages per spec §4 (Dashboard, Performance), using `_page_header`, `_stat` (with `stat_id`/`data-stat`), `_notice`, `_empty`, segmented `_ranges.html`, `charts.js`. Movers render as a ranked list with `_change.html`. Keep "Nothing to chart yet". Keep `id="portfolio-data"` / `id="holding-data"` json_script ids.
- [ ] **Step 4: Run** full suite — expected: OK. Manual: 375 px + desktop, light/dark, range switching on both pages without reload, 1D on a weekend shows the session date.
- [ ] **Step 5: Commit** — "Redesign dashboard and performance; dashboard chart from market history".

---

### Task 8: Wallet, transactions and forms

**Files:**
- Modify: `wallet/views.py` (header `menu` lists, row action items if built in view), `templates/wallet/wallet_home.html`, `transaction_list.html`, `form.html`, `confirm_delete.html`, `templates/watchlist/form.html`, `templates/watchlist/confirm_delete.html`
- Test: `wallet/tests.py` (`WalletPageTests`)

**Interfaces:**
- Consumes: components (Task 3), `fmt` filters (Task 1).
- Produces: `.table-stack` markup convention used again in Tasks 9–10: `<td data-label="Value" class="cell-figure">`, first cell `class="cell-primary"`. Transactions are grouped with Django `{% ifchanged tx.date %}` (date row once per day). Holdings table has a totals row (`summary.holdings_value`, total gain).

- [ ] **Step 1: Write failing tests:**

```python
def test_holdings_rows_are_stackable_and_signed(self): # wallet:home contains 'class="table table-stack', 'data-label="Gain/Loss"', '▲' for a gaining holding
def test_remove_holding_uses_confirm_dialog(self):    # contains 'data-confirm="Remove AAPL?"' and no 'onclick="return confirm('
def test_transaction_list_groups_by_date(self):        # two tx same date → date label appears once
```

- [ ] **Step 2: Run** `manage.py test wallet` — expected: FAIL.
- [ ] **Step 3: Implement** per spec §4 (Wallet, Transactions, Forms). Keep all existing copy asserted by tests.
- [ ] **Step 4: Run** full suite — expected: OK. Manual 375 px check of holdings card layout.
- [ ] **Step 5: Commit** — "Redesign wallet, transactions and forms for mobile".

---

### Task 9: Watchlist, alerts and news

**Files:**
- Modify: `templates/watchlist/watchlist_home.html`, `templates/alerts/alerts_home.html`, `templates/newsfeed/news_home.html`, `templates/alerts/_banner.html` (numbers via `money`). The alert form already renders through `wallet/form.html` (Task 8).
- Test: `alerts/tests.py`, `accounts/tests.py` (watchlist/news pages are covered there), `common/test_pages.py`

**Interfaces:**
- Consumes: components, `.table-stack`, `fmt`.
- Produces: alerts page tabs use Bootstrap nav-tabs with ids `tab-watching` / `tab-triggered`; `?tab=triggered` opens the second tab (server-side `active` class, no JS needed). News items show relative time in English via `{{ item.published|timesince }} ago`.

- [ ] **Step 1: Write failing tests:**

```python
def test_alert_reads_as_sentence(self):   # alerts:home contains "AAPL</strong> above <strong>230,00" (target 230, USD)
def test_triggered_tab_count(self):       # one triggered → 'Triggered <span class="badge' ... '>1<'
def test_news_items_show_relative_time(self):  # get_news_for_tickers mocked with published=now-2h → "2 hours ago"
```

Update `"hit 101 USD"` assertion in `alerts/tests.py` to the new formatted text (`"hit 101,00 $"`) — the banner now uses `money`.

- [ ] **Step 2: Run** `manage.py test alerts watchlist common` — expected: FAIL.
- [ ] **Step 3: Implement** per spec §4 (Watchlist, Alerts, News).
- [ ] **Step 4: Run** full suite — expected: OK.
- [ ] **Step 5: Commit** — "Redesign watchlist, alerts and news".

---

### Task 10: CSV and screenshot import

**Files:**
- Modify: `templates/wallet/csv_upload.html`, `csv_preview.html`, `wallet/views.py` (`csv_preview`: sort rows errors-first for display only), `templates/assistant/import_upload.html`, `import_preview.html`
- Test: `wallet/tests.py`, `assistant/tests.py`

**Interfaces:**
- Consumes: `.drop-zone` + `data-busy` (Task 3), components.
- Produces: screenshot upload form has `data-busy="Reading your screenshot…"`; CSV preview import form `data-busy="Importing…"`; screenshot review has a `sticky-actions` bar with the submit button labelled "Import {{ n }} items" where `n` = number of holding + cash forms; formset field names, management forms and POST handling unchanged.

- [ ] **Step 1: Write failing tests:**

```python
def test_csv_preview_lists_errors_first(self):      # file with ok row then error row → error row's line number appears before the ok row's in the HTML
def test_screenshot_upload_has_busy_state(self):    # import_upload contains 'data-busy="Reading your screenshot…"'
```

Existing assistant tests asserting "✓ Verified", "✗ Not found", "1 of 2 verified", "ENEL.MI — Enel SpA" must keep passing untouched.

- [ ] **Step 2: Run** `manage.py test wallet assistant` — expected: FAIL.
- [ ] **Step 3: Implement** per spec §4 (Screenshot import, CSV import). The CSV *import* order is unchanged (only display order).
- [ ] **Step 4: Run** full suite — expected: OK. Manual: drag-and-drop on desktop; file picker on a 375 px viewport.
- [ ] **Step 5: Commit** — "Redesign CSV and screenshot import for mobile".

---

### Task 11: Assistant, settings and auth pages

**Files:**
- Modify: `templates/assistant/chat.html`, `templates/registration/settings.html`, `password_change_form.html`, `login.html`, `signup.html`, `static/js/app.js` (chat: Enter sends, Shift+Enter newline; scroll message list to bottom on load)
- Test: `assistant/tests.py`, `accounts/tests.py`

**Interfaces:**
- Consumes: `FD.setTheme` (Task 2), components.
- Produces: settings page Appearance control = three radio buttons `name="theme"` values `system|light|dark`, not submitted to the server (`form="none"`-style: outside the profile form), wired to `FD.setTheme`. Chat form has `data-busy="Thinking…"`; the inline `<style>` and `<script>` blocks in `chat.html` are removed (moved to `app.css` / `app.js`).

- [ ] **Step 1: Write failing tests:**

```python
def test_settings_has_appearance_choice(self):  # contains 'name="theme" value="system"', 'value="light"', 'value="dark"'
def test_chat_has_no_inline_style(self):        # assistant:chat NotContains "<style>"
def test_login_uses_auth_layout(self):          # login contains 'class="auth-card' and NotContains 'id="app-sidebar"'
```

- [ ] **Step 2: Run** `manage.py test assistant accounts` — expected: FAIL.
- [ ] **Step 3: Implement** per spec §4 (Assistant, Forms, Auth pages).
- [ ] **Step 4: Run** full suite — expected: OK.
- [ ] **Step 5: Commit** — "Redesign assistant, settings and sign-in pages".

---

### Task 12: Final verification and AGENTS.md

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: No-network run:** run the full suite with `yf` and `requests.post` patched to raise (e.g. a scratch runner script that starts `mock.patch("common.market_data.yf", side_effect=…)` — follow the existing project practice) — expected: OK, 0 errors.
- [ ] **Step 2: Grep checks:** `grep -rn "floatformat" templates/` — expected: no money/price uses left (quantities via `num`); `grep -rn "<style>" templates/` — expected: none; `grep -rn "onclick=\"return confirm" templates/` — expected: none.
- [ ] **Step 3: Manual pass** with `runserver` on every page at 375 px and desktop, light and dark: no horizontal page scroll, nothing hidden behind the tab bar, charts recolour on theme toggle, 1D/7D/1M switch in place.
- [ ] **Step 4: Update AGENTS.md:** map rows for `common/templatetags/fmt.py`, `common/context_processors.py`, `templates/components/`, `static/`, `performance/payloads.py`; conventions: "use `money`/`num`/`pct`, never `floatformat` for money", "tables use `.table-stack` + `data-label`", "destructive POSTs use `data-confirm`, long ones `data-busy`"; known limitation: "intraday FX uses the current rate"; Progress item 10: UI redesign.
- [ ] **Step 5: Commit** — "Document the UI conventions and intraday charts in AGENTS.md".
