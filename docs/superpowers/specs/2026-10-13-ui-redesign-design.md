# UI/UX redesign — design

Date: 2026-10-13 · Branch: `feature/ui-redesign`

## Goal

Make every page look polished and consistent, and make every page fully usable
on a phone (checking, quick entry, and also the wallet tables, imports,
performance and the assistant). Big visual changes are welcome.

Alongside the redesign, charts gain short ranges (1D, 7D) and always end at
the live price, and the dashboard's net-worth chart is rebuilt from market
history instead of daily snapshots.

## Constraints

- Stack stays: Django templates, Bootstrap 5.3 (CDN), Chart.js (CDN), no build
  step, no JS framework. New CDN dependency: Bootstrap Icons (pinned version).
- All AGENTS.md invariants hold: per-user scoping, ledger as source of truth,
  `Decimal` money, missing FX amounts excluded and reported (never summed 1:1),
  market data never crashes a page, imports reviewable and all-or-nothing.
- No model changes. Theme preference stays in `localStorage`.
- Templates keep the AGENTS.md rules: no colour-only signals, labels on inputs,
  `json_script` for chart data, tables usable on mobile.

## 1. Foundations

### Number formatting

New template library `common/templatetags/fmt.py` (plain Python on `Decimal`,
independent of Django locale settings, so output is deterministic):

| Filter | Input | Output |
|---|---|---|
| `money:currency` | `1234.5`, `"EUR"` | `1.234,50 €` |
| `money:currency` | `-1234.5`, `"USD"` | `−1.234,50 $` |
| `signed_money:currency` | `12.3`, `"EUR"` | `+12,30 €` |
| `num` | `Decimal("0.0500")` | `0,05` (trailing zeros trimmed) |
| `num:2` | `1234.5` | `1.234,50` |
| `pct` | `2.345` | `+2,35 %` (always signed, 2 decimals) |

- Thousands separator `.`, decimal separator `,`, symbol after the number with
  a non-breaking space. Minus is U+2212.
- Symbols: EUR €, USD $, GBP £, CHF CHF, JPY ¥; other codes print the code
  (`1.234,50 SEK`). Minor-unit codes (`GBp`) go through `common.currency.normalize`
  first, so they display as GBP.
- `None` → `—` for every filter.
- Gains and losses: colour **plus** ▲/▼ **plus** sign; a `_change.html`
  component renders this so it is consistent everywhere.
- Figures use `font-variant-numeric: tabular-nums`.
- Charts format axes and tooltips with `Intl.NumberFormat("it-IT", …)` in
  `charts.js`, matching the filters.

### Theme and assets

- `static/css/app.css`: design tokens as CSS variables (surface, text, muted,
  border, accent, positive, negative, warning, radius, spacing) with light and
  dark values keyed on Bootstrap's `[data-bs-theme]`. Replaces the inline
  `<style>` in `base.html`, the dark-mode overrides and per-template `<style>`
  blocks.
- `static/js/app.js`: theme toggle (no page reload), toasts, confirm dialog,
  submit spinners, table/row action menus.
- `static/js/charts.js`: shared Chart.js defaults (colours from CSS variables,
  `it-IT` formatting, touch tooltips, fixed heights) and a `recolor()` hook the
  theme toggle calls so charts update live.
- Theme: system preference by default; toggle in the header and an Appearance
  choice (System / Light / Dark) on the Settings page.

### Layout and navigation

- **Desktop (≥ 992 px):** fixed left sidebar: logo; groups *Overview*
  (Dashboard, Performance), *Money* (Wallet, Import), *Markets* (Watchlist,
  Alerts, News), *Tools* (Assistant); bottom: user menu (Settings, Admin for
  staff, Log out) and theme toggle. Active page highlighted (`aria-current`).
  Alerts shows the unseen count as a numbered badge.
- **Mobile:** compact top bar (logo, page title, theme toggle) and a fixed
  bottom tab bar: Dashboard, Wallet, Performance, Alerts, More. *More* opens a
  Bootstrap offcanvas sheet with Watchlist, News, Assistant, Import, Settings,
  Log out. Content gets bottom padding so nothing hides behind the bar.
- Active nav item: each view's template sets `{% block nav %}` to a key; no
  view changes needed.
- Content width capped (~1200 px) for readability.
- Auth pages (login, signup, logged-out, password reset/change done) use
  `base_auth.html`: centred card with logo, no navigation.

## 2. Components (`templates/components/`)

- **`_page_header.html`** — title, optional subtitle, primary action, secondary
  actions. On mobile secondary actions collapse into a "⋯" dropdown.
- **`_stat.html`** — label, value, optional change line and footnote. Used by
  dashboard, wallet, performance. On mobile, stat rows scroll horizontally with
  snap points.
- **Stacked tables** — `.table-stack` class: on desktop a normal table
  (numeric columns right-aligned, sticky header in long lists, row hover);
  under 768 px each row becomes a card using `data-label` attributes on cells
  (`.cell-primary` and `.cell-figure` cells get prominent placement). Same HTML
  for both. All tables also keep a `table-responsive` wrapper.
- **Row actions** — a "⋯" dropdown per row replaces multiple buttons. Each item
  carries an accessible label ("Remove AAPL").
- **Confirm dialog** — forms with `data-confirm="…"` open a Bootstrap modal
  instead of `window.confirm()`; without JS the form still submits.
- **`_empty.html`** — icon, one sentence, and the action(s) that fix it.
- **`_form_fields.html`** — single shared field renderer (merges
  `registration/_form_fields.html`): label, help text, inline errors with
  `is-invalid`, checkboxes as switches, error summary focused on load.
- **`BootstrapFormMixin`** additionally sets `inputmode="decimal"` on decimal
  fields and `type="date"` on date fields.
- **Sticky form footer** on mobile (Save / Cancel).
- **`_confirm_delete.html`** — one template for delete pages, stating what is
  deleted and what will be recalculated.
- **Toasts** — Django messages render as auto-dismissing toasts (top-right on
  desktop, above the tab bar on mobile); `error` messages stay until dismissed.
- **Busy buttons** — `data-busy="Reading screenshot…"` on long-running forms:
  disables the button, shows a spinner and the text, prevents double submit.
- **Notice bar** — slim alert style for FX / missing-price warnings
  (`wallet/_fx_warning.html` restyled).
- **Range control** — `_ranges.html` becomes a segmented control, horizontally
  scrollable on mobile.

## 3. Chart data changes

### Ranges

`performance.services.RANGES` becomes **1D · 7D · 1M · 3M · 6M · YTD · 1Y ·
Max**. Default stays 6M on Performance; the dashboard defaults to 1M.

### Intraday history (1D, 7D)

- New `common.market_data.get_intraday_history(symbol, range_key)` →
  `[(aware datetime, float), …]`, oldest first. 1D: 5-minute bars of the most
  recent trading session (`period="5d"`, last session kept, so weekends and
  holidays show the last session). 7D: 60-minute bars over 7 days. Cached (1D:
  5 min, 7D: 15 min via new settings `INTRADAY_CACHE_SECONDS`); fails soft
  (`[]`, logged, failures not cached).
- `performance.services.portfolio_history` / `holding_history` gain an
  intraday path selected by the range key:
  - **Common time grid** (portfolio): bucket timestamps (5 min / 60 min) in
    UTC, take the union across holdings, forward-fill each holding's last
    price. Before a holding's first bar in the range, use its last daily close
    before the range (from `get_price_history`); if none, it is reported in
    `missing_prices` and valued at average cost (as daily ranges do today).
  - **Positions**: quantity/avg cost per point = ledger replay up to that
    point's local date (transactions have dates only).
  - **FX**: one rate per currency for the whole intraday range — the latest
    available (`CurrencyConverter`); missing → excluded and reported
    (`missing_currencies`), never 1:1.
  - **Cash**: per-day balance, constant within a day.
- Labels: times in the project `TIME_ZONE`; 1D shows the session date in the
  chart subtitle ("Session of Fri 9 Oct").

### Live last point

For every range, the final point uses the **live quote** (`get_quote(s)`) for
each holding that has one — the same numbers as the stat cards. Holdings
without a live quote keep their last historical price. FX for that point: the
current rate. The holding page's price line and "last price" use the quote too.

### Dashboard net-worth chart

- Built with `portfolio_history()` (same as Performance) over the selected
  range, with the range control; defaults to 1M.
- Stat card "Net worth" shows change over the selected range.
- `record_snapshot` is still called (data kept), but the dashboard no longer
  reads `NetWorthSnapshot`.

### Range switching without reload

New view `performance:chart_data` (`/performance/chart-data/?range=…[&holding=pk]`)
returns the chart JSON, scoped to `request.user` (other users' holding → 404).
Range links keep real `href`s (full page reload without JS); `charts.js`
intercepts them, fetches the JSON and updates the chart and the range-dependent
stat values in place, updating the URL with `history.replaceState`.

## 4. Pages

- **Dashboard** — greeting with today's date; stat cards (Net worth large,
  with range change; Cash; Holdings); net-worth chart with range control;
  allocation doughnut (total in centre, legend below on mobile); today's movers
  as a ranked ▲/▼ list (replaces the bar chart); compact watchlist and news
  lists with "View all". Stacks in that order on mobile.
- **Wallet** — header: primary "+ Transaction", "Add ▾" (holding, cash account,
  CSV import, screenshot import); stat cards; holdings stacked table (ticker +
  name, qty, avg cost, price, value, gain/loss with %, totals row; row menu:
  Chart, Add alert, Remove with confirm); cash accounts as a compact list;
  recent transactions with text type badges and signed amounts.
- **Transactions** — stacked table, date shown once per day group, signed
  amounts, Edit/Delete in the row menu.
- **Performance (portfolio, holding)** — range control, stat cards, full-width
  fixed-height chart, holdings breakdown as a stacked table; notices in the
  notice style; trade markers kept on the holding chart.
- **Watchlist** — rows: ticker + name, price, day-change pill; row menu: News,
  Alert, Remove. On mobile, tapping a row opens that ticker's news.
- **Alerts** — tabs *Watching* / *Triggered (n)*; each alert as a sentence
  ("AAPL above 230,00 $ · last 228,10 $ · checked 3 min ago"); New/Seen text
  badges; Dismiss and Re-arm visible, Delete in the menu; help text in a
  collapsible "How alerts work".
- **News** — horizontally scrollable ticker chips; items: headline, source ·
  ticker · relative time; whole item is one tap target (opens in new tab).
- **Screenshot import** — upload: drop zone (drag & drop on desktop; on mobile
  "Choose photo" with `accept="image/*"` so camera/gallery are offered); busy
  state "Reading your screenshot…". Review: one card per position with text
  status badge (Verified / Check / Not found), read values vs market match side
  by side, field grid that stacks on mobile, sticky bottom bar "Import N
  items". Formsets, field names and behaviour unchanged.
- **CSV import** — upload: same drop zone, collapsible "Expected format"
  example. Preview: summary pills (to import / duplicates / errors), stacked
  table, error rows sorted first with the message prominent.
- **Assistant** — chat layout: scrolling message area, composer pinned to the
  bottom (above the tab bar on mobile); suggested prompts as chips when there
  is no history; busy state "Thinking…"; Enter sends, Shift+Enter newline.
- **Forms** (wallet add/edit, watchlist, alerts, settings, password change) —
  shared field renderer, single column, max ~560 px on desktop, full width with
  sticky footer on mobile. Settings gains the Appearance section.
- **Auth pages** — `base_auth.html`.

## 5. Testing

- Existing suite keeps passing (it asserts content and ownership, not markup).
- `common/tests.py`: formatting filters — rounding, negatives, zero, `None`,
  unknown currency code, `GBp`, large numbers, `num` trimming.
- `performance/tests.py`:
  - intraday grid alignment and forward-fill across two holdings with
    different bar times; seed from last daily close; missing prices reported;
  - missing FX in an intraday range excluded and reported;
  - live quote replaces the last point; holding without quote keeps history;
  - `chart_data` view: JSON shape per range, other user's holding → 404,
    unknown range → default.
- Dashboard test: net-worth chart data comes from `portfolio_history`, snapshot
  still recorded.
- Smoke test: every page (GET) renders 200 for a logged-in user with all
  `common.market_data.*` mocked.
- Suite verified with `yf` and `requests.post` patched to raise (no network).
- Manual check at 375 px and desktop widths, light and dark.

## 6. Delivery

Branch `feature/ui-redesign`, commits that each leave the app working:

1. Foundations: `fmt` filters + tests, `app.css`, `app.js`, `charts.js`, new
   `base.html` / `base_auth.html` with sidebar + tab bar.
2. Components.
3. Chart data: ranges, intraday history, live last point, `chart_data` view,
   dashboard on `portfolio_history` (+ tests).
4. Pages: dashboard + performance; wallet + transactions + forms; watchlist +
   alerts + news; imports (CSV, screenshot); assistant + settings + auth.
5. AGENTS.md: update map, conventions (components, filters), progress.

## Out of scope

New data features (tags, notifications), model changes, a JS build step,
server-side theme preference, adjusting quantities intraday (transactions have
no time).
