# AGENTS.md — FinanceDash

Guide for AI coding agents (and humans) working on this repo. Read this first;
keep the **Progress** section up to date when you finish a piece of work.

## What this is

A local-first personal finance dashboard (Django monolith, server-rendered
templates + Bootstrap 5 + Chart.js). Single owner in practice, but fully
multi-user: every record belongs to a user.

Features: wallet (cash accounts, holdings, transactions), watchlist, news,
price alerts, performance charts, CSV import of transactions, screenshot
import via a local vision model, and a portfolio Q&A assistant (Ollama).
Market data (quotes, FX, history, search, news) comes from Yahoo Finance via
`yfinance`; no API keys.

## Stack and running it

- Python 3.14, Django 6.1, yfinance 1.7, requests, python-dotenv (`requirements.txt`, pinned to minor ranges).
- SQLite for local use; all access goes through the ORM.
- Windows dev machine: venv at `.venv`, run tools as `.venv/Scripts/python.exe`.

```bash
cp .env.example .env            # set SECRET_KEY; see comments inside
.venv/Scripts/python.exe manage.py migrate
.venv/Scripts/python.exe manage.py createsuperuser
.venv/Scripts/python.exe manage.py runserver
.venv/Scripts/python.exe manage.py test          # whole suite, ~3 s, no network
.venv/Scripts/python.exe manage.py check_alerts  # check all price alerts (for schedulers)
```

`.env` is read only at startup: **restart `runserver` after editing it.**
Settings from env: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `SIGNUP_ENABLED`,
`DEFAULT_BASE_CURRENCY` (EUR), `OLLAMA_BASE_URL`, `OLLAMA_CHAT_MODEL`,
`OLLAMA_VISION_MODEL`.

## Map of the code

| App / module | Responsibility |
|---|---|
| `financedash/` | settings, root URLs |
| `accounts/` | signup/login/logout/password, `Profile.base_currency`, Settings page; `get_base_currency(user)` |
| `wallet/models.py` | `CashAccount`, `Holding`, `Transaction`, `NetWorthSnapshot` |
| `wallet/ledger.py` | **pure** replay/validation functions (no ORM; also used by migrations) |
| `wallet/services.py` | the only place that changes balances: `save_transaction`, `delete_transaction`, `set_position`, `set_cash_balance`, checks, `net_worth_summary` |
| `wallet/csv_import.py` | CSV parse → dry run → all-or-nothing import |
| `watchlist/` | tickers to watch (no targets; use alerts) |
| `alerts/` | `PriceAlert`, `check_alerts()` (throttled), banner context processor, `check_alerts` command |
| `performance/` | portfolio/holding history rebuilt from ledger × daily closes × daily FX |
| `dashboard/`, `newsfeed/` | home page, news aggregation |
| `assistant/` | Ollama client, screenshot extraction, `market_check.py` (match rows to real listings), chat |
| `common/market_data.py` | every Yahoo call: `get_quote(s)`, `get_fx_rate`, `get_price_history`, `search_securities`, news. Cached; fails soft (returns None/[]) |
| `common/currency.py` | `normalize()` for minor units (GBp→GBP), `CurrencyConverter` |
| `common/forms.py` | `BootstrapFormMixin`, `UserOwnedFormMixin` (sets user, scopes choices, per-user ticker uniqueness) |

URL prefixes: `/` dashboard, `/wallet/`, `/watchlist/`, `/news/`, `/alerts/`,
`/performance/`, `/assistant/`, `/accounts/`, `/admin/`.

## Invariants (do not break)

1. **Every query is scoped to `request.user`.** Other users' objects → 404. Forms
   take `user=` (`UserOwnedFormMixin`). Tests in `accounts/tests.py` guard this.
2. **Transactions are the source of truth.** `CashAccount.balance`,
   `Holding.quantity`/`avg_cost` are derived (`editable=False`) from opening values
   + transactions replayed in date order. Never write them directly; go through
   `wallet.services`. New objects copy opening values in `save()`.
3. **Consistency rules are enforced server-side:** no sell below zero at any point in
   history; no negative cash unless `allow_negative_balance`. Checks run on create,
   edit, delete, import and in the admin.
4. **Money is `Decimal`**, never float (floats from yfinance are converted via
   `Decimal(str(x))`).
5. **Currency:** totals are in the user's base currency. If an FX rate is missing, the
   amount is **excluded and reported** (`missing_currencies`), never summed 1:1.
   Snapshots record their currency; incomplete totals aren't snapshotted.
6. **Market data never crashes a page**: wrappers return None/[] and log.
7. **Imports are reviewable and all-or-nothing** (CSV and screenshot).

## Conventions

- Business logic lives in services/pure modules, not views or templates.
- Tests are mandatory for anything touching money, ownership or imports. Tests must
  not hit the network: mock `common.market_data.*` / `requests.post`. To verify,
  run the suite with `yf` and `requests.post` patched to raise.
- Locmem cache persists across tests: call `cache.clear()` in `setUp` when a test
  depends on throttling or cached quotes.
- Migrations: data migrations must be reversible or `noop`-reversed and must
  preserve data (e.g. watchlist targets → alerts). **Back up `db.sqlite3` before
  migrating the real database.**
- Templates: no colour-only signals (badges carry text), labels on inputs,
  `json_script` for chart data, mobile-friendly tables (`table-responsive`).
- Commits: one feature per commit on a `feature/*` branch, fast-forward into
  `main`, then push. Commit messages explain *why*.

## Gotchas (learned the hard way)

- **Windows encoding:** Python's default `read_text()` is cp1252 here; always pass
  `encoding="utf-8"` when editing files programmatically.
- **Bash heredocs with nested quotes** sometimes fail to parse in the agent shell;
  write a script file and run it instead.
- **Ollama vision model must be an *instruct* edition.** Plain `qwen3-vl:8b` is the
  *thinking* edition: it reasons until the context (4096 tokens) is full and returns
  an empty answer. Use `qwen3-vl:8b-instruct` (default). `llava` is too weak for tables.
- **Ask the model for amounts as printed text**, not JSON numbers; the app parses them
  (`wallet.csv_import.parse_decimal` handles `1.234,56` and `1,234.56`).
- Yahoo quotes London in pence (`GBp`) and crypto as pairs (`BTC-EUR`); a bare `BTC`
  is a US fund. Some funds have ISIN-named mirror listings (`IE00….SG`); prefer real ones.
- Ollama's server log (`%LOCALAPPDATA%\Ollama\server.log`) shows context size,
  token counts and truncation; check it first when extraction fails.

## Progress

Done (in git order):
1. Dependencies upgraded (Django 6.1 etc.), first tests.
2. User accounts; all data per user; `ALLOWED_HOSTS` from env.
3. Ledger: balances derived from transactions; editable/deletable transactions.
4. Consistency rules (no overselling, negative cash opt-in).
5. Base-currency conversion with live FX.
6. CSV import of transactions.
7. In-app price alerts (replaced watchlist targets).
8. Performance charts (portfolio + per holding).
9. Screenshot import rebuilt: transcribe-only schema + market cross-check;
   fixed thinking-model failure (branch `feature/better-screenshot-import`, pending merge).

Known limitations:
- Cost basis is converted at today's FX rate (gains exclude currency moves).
- Opening quantities/balances have no date (charts treat them as held from the start).
- Stock splits are not adjusted for.
- A single comma in CSV/screenshot numbers is read as a decimal comma (`1,234` = 1.234).
- Alerts are checked only when pages are opened (or via the command); no email.

Ideas / next steps:
- Email or push notifications for alerts.
- Tags/categories per holding.
- Split handling; dated opening positions.
- Remove unused `llava` model from the machine.
