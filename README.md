# FinanceDash

A personal finance dashboard built with Django:

- **Wallet & liquidity** — track cash accounts and holdings (stocks, ETFs,
  bonds, crypto), record buy/sell/deposit/withdraw/dividend transactions,
  and see live net worth.
- **Watchlist** — track tickers you're watching, with live prices and
  optional target-price alerts.
- **News** — aggregated news for everything in your wallet + watchlist,
  pulled from Yahoo Finance.

Prices and news come from [yfinance](https://github.com/ranaroussi/yfinance),
a free wrapper around Yahoo Finance's public data. No API key needed.

## Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create the database
python manage.py migrate

# 4. (Optional) create an admin user, useful for quickly adding data
#    via Django's built-in admin at /admin/
python manage.py createsuperuser

# 5. Run the dev server
python manage.py runserver
```

Then open http://127.0.0.1:8000/ in your browser.

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
- Ideas for later: multi-currency FX conversion, price alerts via email,
  CSV import of transactions, charts (e.g. with Chart.js) for portfolio
  performance over time, tags/categories per holding.
