# FinanceDash

A local-first personal finance dashboard built with Django:

- **Wallet & liquidity** — track cash accounts and holdings (stocks, ETFs,
  bonds, crypto), record buy/sell/deposit/withdraw/dividend transactions,
  and see live net worth.
- **Watchlist** — track tickers you're watching, with live prices and
  optional target-price alerts.
- **News** — aggregated news for everything in your wallet + watchlist,
  pulled from Yahoo Finance.
- **AI Assistant** — ask questions about your portfolio (diversification,
  concentration risk, winners/losers), answered by a local LLM via
  [Ollama](https://ollama.com). Nothing leaves your machine.
- **Screenshot import** — upload a screenshot of your broker/bank app; a
  local vision model reads out cash balances and holdings, then you review
  and edit before anything is saved.

Prices and news come from [yfinance](https://github.com/ranaroussi/yfinance),
a free wrapper around Yahoo Finance's public data. No API key needed.

## Setting up Ollama (for the Assistant + screenshot import)

These two features are optional; the rest of the app works without Ollama.

1. Install Ollama: https://ollama.com/download
2. Pull a chat model and a vision model:
   ```bash
   ollama pull llama3.1        # portfolio Q&A / analytics
   ollama pull qwen3-vl:8b     # screenshot -> holdings (fits a 12 GB GPU)
   ```
   `qwen2.5vl:7b` is a good alternative vision model. Older models such as
   `llava` are not reliable at reading numbers and tables. Models can be
   changed with `OLLAMA_CHAT_MODEL` / `OLLAMA_VISION_MODEL` in `.env`.
3. Make sure Ollama is running (`ollama serve`, or the background service).
4. Visit `/assistant/chat/` or click "🤖 Assistant" / "📸 Import" in the navbar.

**How the screenshot import works:** the vision model only transcribes what
is printed (name, ticker/ISIN if shown, quantity, price, value, currency),
forced into a fixed JSON schema. Each position is then matched to a real
Yahoo Finance listing by searching its ticker, ISIN and name, preferring the
listing in the screenshot's currency whose live price matches. The review
page marks each row ✓ Verified (live price within 5%), ⚠ Check (with the
reason) or ✗ Not found. Missing quantities are worked out from the value;
a missing average cost defaults to today's price (editable).

If Ollama isn't running, both features show a friendly error message
instead of crashing.

## Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy .env.example to .env and set SECRET_KEY (see the comment inside)

# 4. Create the database
python manage.py migrate

# 5. Create your user (also gives you access to the admin at /admin/)
python manage.py createsuperuser

# 6. Run the dev server
python manage.py runserver
```

Then open http://127.0.0.1:8000/ in your browser and log in.

Every page requires login, and each user only sees their own wallet,
watchlist and news. Others can register at `/accounts/signup/`; set
`SIGNUP_ENABLED=False` in `.env` to turn that off.

## Adding data

You can add cash accounts, holdings, transactions, and watchlist items
either through the web UI (buttons on each page) or through the Django
admin at `/admin/` (faster for bulk entry, if you created a superuser).

**Typical first steps:**
1. Add a cash account (e.g. "Broker cash", starting balance).
2. Add a holding for something you already own (ticker, quantity, avg cost).
3. Record any new buy/sell transactions going forward — the app will
   automatically update quantities, average cost, and cash balances.
4. Add a few tickers to your watchlist.
5. Check the News tab — it pulls recent articles for every ticker across
   your wallet and watchlist.

## Project layout

```
financedash/       Django project settings/urls
common/            Shared yfinance wrapper (common/market_data.py) + form styling
wallet/            Cash accounts, holdings, transactions
watchlist/         Watchlist items
newsfeed/          News aggregation view
dashboard/         Home page combining everything
templates/         All HTML templates (Bootstrap 5 via CDN)
```

## Notes & next steps

- **Ticker symbols**: use the same symbols Yahoo Finance uses (e.g. `AAPL`,
  `VOO`, `BTC-USD`). For non-US tickers, Yahoo often needs a suffix
  (e.g. `ENEL.MI` for Milan-listed stocks).
- **Caching**: live quotes are cached for 60s and news for 5 minutes
  (see `MARKET_DATA_CACHE_SECONDS` / `NEWS_CACHE_SECONDS` in
  `financedash/settings.py`) to avoid hammering Yahoo Finance.
- **Switching to Postgres later**: only `DATABASES` in `settings.py` needs
  to change (plus `pip install psycopg2-binary`), since all the app code
  goes through Django's ORM.
- **This was scaffolded without a live network connection**, so it hasn't
  been run end-to-end. If you hit an error on first run, it's most likely
  a small typo — check the traceback, it'll point straight at the line.
- **Currencies**: totals, net worth and charts are shown in your base
  currency (EUR by default; change it under 👤 → Settings). Other currencies
  are converted with live Yahoo Finance FX rates (cached 15 min). If a rate
  can't be fetched, those amounts are left out of the totals with a warning
  rather than added 1:1. Cost basis is converted at today's rate, so gains
  don't include currency movements.
- **CSV import**: Wallet → Transactions → Import CSV. Upload, review a
  preview (a full trial run through the same rules as manual entry), then
  confirm. All-or-nothing; rows already recorded are skipped; `;`
  separators, decimal commas and DD/MM/YYYY dates are accepted. A sample
  file is downloadable from the import page.
- **Price alerts**: 🔔 Alerts in the navbar, or the 🔔 buttons on the
  watchlist and wallet. An alert fires once when a ticker rises to/above or
  falls to/below a price (in the currency it trades in) and stays in a banner
  until dismissed; re-arm it to watch again. Alerts are checked when you open
  the dashboard, watchlist or alerts page (at most once a minute). To check
  on a schedule instead, run `python manage.py check_alerts` from Windows
  Task Scheduler or cron.
- **Performance**: net worth, holdings value and amount invested over
  1M/3M/6M/YTD/1Y/Max, rebuilt from your transactions and Yahoo daily
  closes (cached 1 h) with each day's FX rate; per-holding price charts with
  your average cost and buy/sell markers (click a ticker in the Wallet).
  Opening quantities/balances count as held from the start of the chart;
  stock splits aren't adjusted for.
- Ideas for later: email alerts, tags/categories per holding (e.g. with Chart.js) for portfolio
  performance over time, tags/categories per holding.
