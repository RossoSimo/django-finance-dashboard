"""
Historical performance, rebuilt from the transaction ledger and market prices.

Daily ranges (1M ... Max): for each sampled day, what was held (opening
position + transactions up to that day, using the same ledger rules as the
wallet) times that day's close, converted with that day's FX rate. Cash
accounts are replayed the same way.

Intraday ranges (1D, 7D): the same on a time grid built from intraday bars,
bucketed to 5 minutes (1D) or 1 hour (7D) in UTC and merged across holdings.
1D shows the latest session (local date of the newest bar of anything held);
7D the last 7 days. Each holding's price is carried forward from its last bar
at or before a point; without one (e.g. funds, which have no intraday bars)
it is the last daily close strictly before the point's local date (a
same-day daily row is partial, so using it would look ahead). Positions and
cash follow the ledger up to the point's local date (transactions carry
dates, not times). Every point uses the current FX rate. The grid ends at
`now`, except for a 1D session that isn't today's (e.g. at the weekend): that
chart ends on the session's last bucket instead of jumping days ahead.

Live last point: when a range ends today (and always for intraday ranges),
the final point values each holding at its live quote and converts at the
current FX rate (daily ranges fall back to the latest FX history), so the
chart ends on the same numbers as the wallet. Holdings without a live price
keep their last historical price.

Fallbacks, mirroring net_worth_summary():
- no price yet for a point (e.g. before the first available close): value
  the position at its average cost; no price history at all: also report
  the ticker in `missing_prices`;
- no FX history for a day: use today's rate; no rate at all: leave those
  amounts out and report the currency in `missing_currencies`.

Opening quantities/balances have no date, so they count as held from the
start of the chart.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from accounts.models import get_base_currency
from common.currency import CurrencyConverter, normalize
from wallet import ledger
from wallet.models import CashAccount, Holding

ZERO = Decimal("0")
ONE_DAY = timedelta(days=1)

RANGES = {  # key -> (label, days back; None = computed)
    "1d": ("1D", 1),
    "7d": ("7D", 7),
    "1m": ("1M", 30),
    "3m": ("3M", 91),
    "6m": ("6M", 182),
    "ytd": ("YTD", None),
    "1y": ("1Y", 365),
    "max": ("Max", None),
}
DEFAULT_RANGE = "6m"
DASHBOARD_DEFAULT_RANGE = "1m"
INTRADAY_RANGES = {"1d", "7d"}  # drawn from intraday bars, not daily closes
INTRADAY_BUCKET_MINUTES = {"1d": 5, "7d": 60}
INTRADAY_SEED_LOOKBACK = timedelta(days=10)  # daily closes fetched from this long before the window
MAX_YEARS = 10
DAILY_POINTS_LIMIT = 400  # beyond this many days, sample weekly


def range_start(key: str, today: date, earliest: date | None) -> date:
    if key == "ytd":
        return date(today.year, 1, 1)
    if key == "max":
        floor = today - timedelta(days=365 * MAX_YEARS)
        return max(earliest, floor) if earliest else today - timedelta(days=365)
    return today - timedelta(days=RANGES.get(key, RANGES[DEFAULT_RANGE])[1])


def sample_days(start: date, end: date) -> list[date]:
    step = 1 if (end - start).days <= DAILY_POINTS_LIMIT else 7
    days = [start + timedelta(days=i) for i in range(0, (end - start).days + 1, step)]
    if days[-1] != end:
        days.append(end)
    return days


class _Series:
    """Forward-filled lookups into a [(date or datetime, value), ...] series."""

    def __init__(self, points):
        points = sorted(points)
        self.dates = [d for d, _ in points]
        self.values = [v for _, v in points]

    def __bool__(self):
        return bool(self.dates)

    def at(self, day):
        i = bisect_right(self.dates, day)
        return self.values[i - 1] if i else None

    def first_on_or_after(self, day):
        i = bisect_right(self.dates, day - ONE_DAY)
        return self.values[i] if i < len(self.values) else None


class _Replay:
    """Walks a chronological transaction list forward one day at a time."""

    def __init__(self, txs):
        self.txs = sorted(txs, key=lambda t: (t.date, t.created_at, t.pk))
        self.i = 0

    def upto(self, day):
        batch = []
        while self.i < len(self.txs) and self.txs[self.i].date <= day:
            batch.append(self.txs[self.i])
            self.i += 1
        return batch


class _FX:
    """Per-day conversion into the base currency, from FX history with
    today's rate as fallback. `day=None` asks for the current rate only;
    `current_first` prefers the current rate, falling back to history."""

    def __init__(self, base, start, history_fn, converter):
        self.base, self.start, self.history_fn, self.now = base, start, history_fn, converter
        self.series: dict[str, _Series] = {}
        self.missing: set[str] = set()

    def _history(self, currency):
        if currency not in self.series:
            self.series[currency] = _Series(
                (d, Decimal(str(v))) for d, v in self.history_fn(f"{currency}{self.base}=X", self.start)
            )
        return self.series[currency]

    def rate(self, currency, day=None, current_first=False):
        if currency == self.base:
            return Decimal("1")
        if day is None:
            rate = self.now.rate(currency)
        elif current_first:
            rate = self.now.rate(currency) or self._history(currency).at(day)
        else:
            rate = self._history(currency).at(day) or self.now.rate(currency)
        if rate is None:
            self.missing.add(currency)
        return rate

    def convert(self, amount, currency, day=None, current_first=False):
        rate = self.rate(currency, day, current_first)
        return amount * rate if rate is not None else None


@dataclass
class HoldingPerformance:
    holding: Holding
    price_currency: str
    first_price: Decimal | None
    last_price: Decimal | None
    end_value: Decimal | None
    end_cost: Decimal | None
    has_history: bool

    @property
    def price_change_pct(self):
        if self.first_price and self.last_price is not None:
            return (self.last_price / self.first_price - 1) * 100
        return None

    @property
    def gain(self):
        if self.end_value is None or self.end_cost is None:
            return None
        return self.end_value - self.end_cost


@dataclass
class PortfolioHistory:
    base_currency: str
    start: date
    end: date
    days: list[date] = field(default_factory=list)  # aware datetimes when `intraday`
    net_worth: list[Decimal] = field(default_factory=list)
    holdings_value: list[Decimal] = field(default_factory=list)
    invested: list[Decimal] = field(default_factory=list)
    cash: list[Decimal] = field(default_factory=list)
    holdings: list[HoldingPerformance] = field(default_factory=list)
    missing_currencies: list[str] = field(default_factory=list)
    missing_prices: list[str] = field(default_factory=list)
    intraday: bool = False
    session_date: date | None = None  # 1D: the session shown

    @property
    def change(self):
        return self.net_worth[-1] - self.net_worth[0] if self.net_worth else None

    @property
    def change_pct(self):
        if self.net_worth and self.net_worth[0]:
            return self.change / self.net_worth[0] * 100
        return None

    @property
    def unrealized_gain(self):
        return self.holdings_value[-1] - self.invested[-1] if self.holdings_value else None


def _price_currency(holding, quote):
    _, currency = normalize(None, (quote.currency if quote and quote.currency else holding.currency))
    return currency, (quote.currency if quote and quote.currency else holding.currency)


def _live_price(quote):
    """The quote's price in major units (pence -> pounds), or None."""
    if quote is None or quote.price is None:
        return None
    return normalize(Decimal(str(quote.price)), quote.currency)[0]


def _load_portfolio(user):
    holdings = [
        h for h in Holding.objects.filter(user=user).prefetch_related("transactions")
        if h.opening_quantity != 0 or h.transactions.all()
    ]
    accounts = list(CashAccount.objects.filter(user=user).prefetch_related("transactions"))
    return holdings, accounts


def _value_portfolio(result, holdings, accounts, quotes, *, points, day_of, load_prices, fx, fx_days, live):
    """Fill `result` with holdings value, cost and cash at every point.

    day_of(point) -> the date the ledger is replayed up to;
    load_prices(holding, raw_currency) -> (prices, first_price), prices.at(point)
        giving the historical price (or None);
    fx_days[i] -> the day whose FX rate converts point i (None: current rate);
    live -> the final point uses each holding's live quote when it has one,
        and the current FX rate before that day's FX history.
    """
    last = len(points) - 1
    holdings_value = [ZERO] * len(points)
    invested = [ZERO] * len(points)
    cash = [ZERO] * len(points)
    before_start = day_of(points[0]) - ONE_DAY

    def convert(amount, currency, i):
        return fx.convert(amount, currency, fx_days[i], current_first=live and i == last)

    for h in holdings:
        quote = quotes.get(h.ticker.upper())
        price_currency, raw_currency = _price_currency(h, quote)
        prices, first_price = load_prices(h, raw_currency)
        live_price = _live_price(quote) if live else None
        if not prices:
            result.missing_prices.append(h.ticker)

        replay = _Replay(h.transactions.all())
        qty, avg = ledger.replay_position(h.opening_quantity, h.opening_avg_cost, replay.upto(before_start))
        held_in_range = qty > 0
        end_value = end_cost = None
        for i, point in enumerate(points):
            qty, avg = ledger.replay_position(qty, avg, replay.upto(day_of(point)))
            if qty <= 0:
                continue
            held_in_range = True
            price = live_price if i == last and live_price is not None else prices.at(point)
            if price is not None:
                value = convert(qty * price, price_currency, i)
            else:
                value = convert(qty * avg, h.currency, i)
            cost = convert(qty * avg, h.currency, i)
            if value is not None:
                holdings_value[i] += value
            if cost is not None:
                invested[i] += cost
            if i == last:
                end_value, end_cost = value, cost
        if held_in_range:
            result.holdings.append(HoldingPerformance(
                holding=h, price_currency=price_currency, first_price=first_price,
                last_price=live_price if live_price is not None else prices.at(points[-1]),
                end_value=end_value, end_cost=end_cost, has_history=bool(prices),
            ))

    for account in accounts:
        replay = _Replay(account.transactions.all())
        balance = ledger.replay_cash(account.opening_balance, replay.upto(before_start))
        for i, point in enumerate(points):
            balance = ledger.replay_cash(balance, replay.upto(day_of(point)))
            converted = convert(balance, account.currency, i)
            if converted is not None:
                cash[i] += converted

    result.holdings_value, result.invested, result.cash = holdings_value, invested, cash
    result.net_worth = [h + c for h, c in zip(holdings_value, cash)]
    result.holdings.sort(key=lambda p: p.end_value or ZERO, reverse=True)
    result.missing_currencies = sorted(fx.missing)


def _same_day(day):
    return day


def portfolio_history(user, start: date, end: date, history_fn=None, quotes=None, converter=None,
                      *, today: date | None = None) -> PortfolioHistory:
    """Rebuild the user's portfolio value, cost basis and cash for each sampled day.
    If the range ends today, the last point uses live quotes and current FX."""
    from common.market_data import get_price_history, get_quotes

    history_fn = history_fn or get_price_history
    today = today if today is not None else timezone.localdate()
    base = get_base_currency(user)
    fx = _FX(base, start, history_fn, converter or CurrencyConverter(base))
    days = sample_days(start, end)
    live = end == today
    result = PortfolioHistory(base_currency=base, start=start, end=end, days=days)

    holdings, accounts = _load_portfolio(user)
    if quotes is None:
        quotes = get_quotes([h.ticker for h in holdings])

    def load_prices(h, raw_currency):
        prices = _Series((d, normalize(Decimal(str(v)), raw_currency)[0]) for d, v in history_fn(h.ticker, start))
        return prices, prices.first_on_or_after(start)

    _value_portfolio(result, holdings, accounts, quotes, points=days, day_of=_same_day,
                     load_prices=load_prices, fx=fx, fx_days=days, live=live)
    return result


# --- Intraday ranges --------------------------------------------------------

def _local_date(moment: datetime) -> date:
    return timezone.localtime(moment).date()


def _bucket(moment: datetime, minutes: int) -> datetime:
    """Floor `moment` to its bucket, in UTC."""
    seconds = int(moment.timestamp())
    return datetime.fromtimestamp(seconds - seconds % (minutes * 60), tz=UTC)


@dataclass
class _Window:
    key: str
    now: datetime
    start: datetime
    session_date: date | None  # 1D only

    @property
    def minutes(self):
        return INTRADAY_BUCKET_MINUTES[self.key]

    @property
    def start_date(self):
        return _local_date(self.start)

    @property
    def reaches_now(self):
        """7D always ends at `now`; 1D only when the session is today's."""
        return self.session_date is None or self.session_date == _local_date(self.now)

    def keeps(self, moment):
        if self.session_date is not None:
            return _local_date(moment) == self.session_date
        return self.start <= moment <= self.now


def _intraday_window(key: str, now: datetime, last_bar_times: list[datetime]) -> _Window:
    """1D: the latest session (local date of the newest bar, or today if there
    are none), from its local midnight. 7D: the last 7 days."""
    if key == "1d":
        session = max((_local_date(t) for t in last_bar_times), default=_local_date(now))
        start = timezone.make_aware(datetime.combine(session, time.min)).astimezone(UTC)
        return _Window(key, now, start, session)
    return _Window(key, now, now - timedelta(days=RANGES[key][1]), None)


def _grid(window: _Window, bar_lists) -> list[datetime]:
    """Union of the bucketed bar times in the window, ending at `now` when
    the window reaches it."""
    grid = sorted({_bucket(t, window.minutes) for bars in bar_lists for t, _ in bars if window.keeps(t)})
    if not grid:
        grid = [window.start]
    if window.reaches_now and grid[-1] < window.now:
        grid.append(window.now)
    return grid


class _IntradayPrices:
    """Bar closes carried forward on bucketed times; where there's no bar
    yet (or none at all, e.g. funds), the last daily close strictly before the
    point's local date: a same-day daily row is partial, close to the live price."""

    def __init__(self, bars, closes, minutes, raw_currency):
        by_bucket = {}
        for moment, value in bars:  # oldest first: the last bar in a bucket wins
            by_bucket[_bucket(moment, minutes)] = normalize(Decimal(str(value)), raw_currency)[0]
        self.bars = _Series(by_bucket.items())
        self.closes = _Series((d, normalize(Decimal(str(v)), raw_currency)[0]) for d, v in closes)

    def __bool__(self):
        return bool(self.bars) or bool(self.closes)

    def at(self, moment):
        price = self.bars.at(moment)
        return price if price is not None else self.closes.at(_local_date(moment) - ONE_DAY)


def _intraday_prices(bars, history_fn, ticker, window, raw_currency):
    closes = history_fn(ticker, window.start_date - INTRADAY_SEED_LOOKBACK)
    return _IntradayPrices(bars, closes, window.minutes, raw_currency)


def _bars(intraday_fn, ticker, range_key, now):
    return sorted(b for b in intraday_fn(ticker, range_key) if b[0] <= now)


def _held_during(holding, start: date, end: date) -> bool:
    """Whether the position is open on any date from `start` to `end`."""
    replay = _Replay(holding.transactions.all())
    qty, avg = ledger.replay_position(holding.opening_quantity, holding.opening_avg_cost, replay.upto(start))
    if qty > 0:
        return True
    for tx in replay.upto(end):
        qty, avg = ledger.replay_position(qty, avg, [tx])
        if qty > 0:
            return True
    return False


def portfolio_history_intraday(user, range_key: str, intraday_fn=None, history_fn=None, quotes=None,
                               converter=None, *, now: datetime | None = None) -> PortfolioHistory:
    """Portfolio value, cost basis and cash on an intraday grid ("1d" or "7d")."""
    from common.market_data import get_intraday_history, get_price_history, get_quotes

    intraday_fn = intraday_fn or get_intraday_history
    history_fn = history_fn or get_price_history
    now = now or timezone.now()
    today = _local_date(now)
    base = get_base_currency(user)
    fx = _FX(base, None, history_fn, converter or CurrencyConverter(base))

    holdings, accounts = _load_portfolio(user)
    bars = {h.pk: _bars(intraday_fn, h.ticker, range_key, now) for h in holdings}
    # The 1D session is the latest one of something actually held then.
    window = _intraday_window(range_key, now, [
        bars[h.pk][-1][0] for h in holdings
        if bars[h.pk] and _held_during(h, _local_date(bars[h.pk][-1][0]), today)
    ])
    holdings = [h for h in holdings if _held_during(h, window.start_date, today)]
    points = _grid(window, [bars[h.pk] for h in holdings])
    if quotes is None:
        quotes = get_quotes([h.ticker for h in holdings])

    def load_prices(h, raw_currency):
        prices = _intraday_prices(bars[h.pk], history_fn, h.ticker, window, raw_currency)
        return prices, prices.at(points[0])

    result = PortfolioHistory(base_currency=base, start=window.start_date, end=_local_date(points[-1]), days=points,
                              intraday=True, session_date=window.session_date)
    _value_portfolio(result, holdings, accounts, quotes, points=points, day_of=_local_date,
                     load_prices=load_prices, fx=fx, fx_days=[None] * len(points), live=True)
    return result


# --- Single holding ---------------------------------------------------------

@dataclass
class HoldingHistory:
    holding: Holding
    price_currency: str
    days: list[date] = field(default_factory=list)  # aware datetimes when `intraday`
    price: list[Decimal | None] = field(default_factory=list)
    avg_cost: list[Decimal | None] = field(default_factory=list)
    quantity: list[Decimal] = field(default_factory=list)
    trades: list = field(default_factory=list)  # transactions in range
    intraday: bool = False
    session_date: date | None = None  # 1D: the session shown

    @property
    def avg_cost_comparable(self):
        """Average cost can only be drawn on the price chart in the same currency."""
        return self.price_currency == normalize(None, self.holding.currency)[1]


def _fill_holding_history(result, prices, live_price, *, day_of, first_day, last_day):
    """Price, average cost and quantity at each of result.days; the final
    point uses `live_price` when given. Trades are the buys/sells dated
    first_day..last_day."""
    holding = result.holding
    txs = list(holding.transactions.all())
    replay = _Replay(txs)
    qty, avg = ledger.replay_position(holding.opening_quantity, holding.opening_avg_cost,
                                      replay.upto(day_of(result.days[0]) - ONE_DAY))
    last = len(result.days) - 1
    for i, point in enumerate(result.days):
        qty, avg = ledger.replay_position(qty, avg, replay.upto(day_of(point)))
        result.price.append(live_price if i == last and live_price is not None else prices.at(point))
        result.avg_cost.append(avg if qty > 0 else None)
        result.quantity.append(qty)
    result.trades = sorted((t for t in txs if first_day <= t.date <= last_day and t.tx_type in ("buy", "sell")),
                           key=lambda t: (t.date, t.created_at, t.pk))


def holding_history(holding: Holding, start: date, end: date, history_fn=None, quote=None,
                    *, today: date | None = None) -> HoldingHistory:
    """Daily price/cost history; if the range ends today, the last price is live."""
    from common.market_data import get_price_history, get_quote

    history_fn = history_fn or get_price_history
    today = today if today is not None else timezone.localdate()
    quote = quote if quote is not None else get_quote(holding.ticker)
    price_currency, raw_currency = _price_currency(holding, quote)
    prices = _Series((d, normalize(Decimal(str(v)), raw_currency)[0]) for d, v in history_fn(holding.ticker, start))

    result = HoldingHistory(holding=holding, price_currency=price_currency, days=sample_days(start, end))
    _fill_holding_history(result, prices, _live_price(quote) if end == today else None,
                          day_of=_same_day, first_day=start, last_day=end)
    return result


def holding_history_intraday(holding: Holding, range_key: str, intraday_fn=None, history_fn=None, quote=None,
                             *, now: datetime | None = None) -> HoldingHistory:
    """Price/cost history on an intraday grid ("1d" or "7d"), ending on the live price."""
    from common.market_data import get_intraday_history, get_price_history, get_quote

    intraday_fn = intraday_fn or get_intraday_history
    history_fn = history_fn or get_price_history
    now = now or timezone.now()
    quote = quote if quote is not None else get_quote(holding.ticker)
    price_currency, raw_currency = _price_currency(holding, quote)

    bars = _bars(intraday_fn, holding.ticker, range_key, now)
    window = _intraday_window(range_key, now, [bars[-1][0]] if bars else [])
    prices = _intraday_prices(bars, history_fn, holding.ticker, window, raw_currency)

    result = HoldingHistory(holding=holding, price_currency=price_currency, days=_grid(window, [bars]),
                            intraday=True, session_date=window.session_date)
    _fill_holding_history(result, prices, _live_price(quote),
                          day_of=_local_date, first_day=window.start_date,
                          last_day=_local_date(result.days[-1]))
    return result


# --- Dispatch ---------------------------------------------------------------

def history_for_range(user, key: str, *, holding: Holding | None = None,
                      earliest: date | None = None) -> PortfolioHistory | HoldingHistory:
    """The chart history for a range key: intraday for 1D/7D, daily (ending
    today) otherwise. With `holding` (already checked to belong to `user`),
    that holding's history; else the whole portfolio."""
    if key not in RANGES:
        key = DEFAULT_RANGE
    if key in INTRADAY_RANGES:
        if holding is not None:
            return holding_history_intraday(holding, key)
        return portfolio_history_intraday(user, key)
    today = timezone.localdate()
    start = range_start(key, today, earliest)
    if holding is not None:
        return holding_history(holding, start, today, today=today)
    return portfolio_history(user, start, today, today=today)
