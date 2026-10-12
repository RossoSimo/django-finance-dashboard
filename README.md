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

These two features are optional — the rest of the app works fine without
Ollama. To enable them:

1. Install Ollama: https://ollama.com/download
2. Pull a chat model and a vision model:
   ```bash
   ollama pull llama3.1        # portfolio Q&A / analytics
   ollama pull llava           # screenshot -> structured data
   ```
   (`qwen2.5vl` or `llama3.2-vision` also work well as vision models if you
   have the hardware for them — just update `OLLAMA_VISION_MODEL` in
   `financedash/settings.py` to match.)
3. Make sure Ollama is running (`ollama serve`, or it may already be running
   as a background service after install).
4. Visit `/assistant/chat/` or click "🤖 Assistant" / "📸 Import" in the navbar.

If Ollama isn't running, both features will show a friendly error message
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
- Ideas for later: multi-currency FX conversion, price alerts via email,
  CSV import of transactions, charts (e.g. with Chart.js) for portfolio
  performance over time, tags/categories per holding.
