"""
Historical performance, rebuilt from the transaction ledger and daily prices.

For each sampled day: what was held (opening position + transactions up to
that day, using the same ledger rules as the wallet) times that day's close,
converted with that day's FX rate. Cash accounts are replayed the same way.

Fallbacks, mirroring net_worth_summary():
- no close yet for a day (e.g. before the first available price): value the
  position at its average cost;
- no FX history for a day: use today's rate; no rate at all: leave those
  amounts out and report the currency in `missing_currencies`.

Opening quantities/balances have no date, so they count as held from the
start of the chart.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from accounts.models import get_base_currency
from common.currency import CurrencyConverter, normalize
from wallet import ledger
from wallet.models import CashAccount, Holding

ZERO = Decimal("0")

RANGES = {  # key -> (label, days back; None = computed)
    "1m": ("1M", 30),
    "3m": ("3M", 91),
    "6m": ("6M", 182),
    "ytd": ("YTD", None),
    "1y": ("1Y", 365),
    "max": ("Max", None),
}
DEFAULT_RANGE = "6m"
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
    """Forward-filled lookups into a [(date, value), ...] series."""

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
        i = bisect_right(self.dates, day - timedelta(days=1))
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
    today's rate as fallback."""

    def __init__(self, base, start, history_fn, converter):
        self.base, self.start, self.history_fn, self.now = base, start, history_fn, converter
        self.series: dict[str, _Series] = {}
        self.missing: set[str] = set()

    def rate(self, currency, day):
        if currency == self.base:
            return Decimal("1")
        if currency not in self.series:
            self.series[currency] = _Series(
                (d, Decimal(str(v))) for d, v in self.history_fn(f"{currency}{self.base}=X", self.start)
            )
        rate = self.series[currency].at(day) or self.now.rate(currency)
        if rate is None:
            self.missing.add(currency)
        return rate

    def convert(self, amount, currency, day):
        rate = self.rate(currency, day)
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
    days: list[date] = field(default_factory=list)
    net_worth: list[Decimal] = field(default_factory=list)
    holdings_value: list[Decimal] = field(default_factory=list)
    invested: list[Decimal] = field(default_factory=list)
    cash: list[Decimal] = field(default_factory=list)
    holdings: list[HoldingPerformance] = field(default_factory=list)
    missing_currencies: list[str] = field(default_factory=list)
    missing_prices: list[str] = field(default_factory=list)

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


def portfolio_history(user, start: date, end: date, history_fn=None, quotes=None, converter=None) -> PortfolioHistory:
    """Rebuild the user's portfolio value, cost basis and cash for each sampled day."""
    from common.market_data import get_price_history, get_quotes

    history_fn = history_fn or get_price_history
    base = get_base_currency(user)
    fx = _FX(base, start, history_fn, converter or CurrencyConverter(base))
    days = sample_days(start, end)
    result = PortfolioHistory(base_currency=base, start=start, end=end, days=days)

    holdings = list(Holding.objects.filter(user=user).prefetch_related("transactions"))
    accounts = list(CashAccount.objects.filter(user=user).prefetch_related("transactions"))
    if quotes is None:
        quotes = get_quotes([h.ticker for h in holdings])

    holdings_value = [ZERO] * len(days)
    invested = [ZERO] * len(days)
    cash = [ZERO] * len(days)

    for h in holdings:
        txs = list(h.transactions.all())
        if not txs and h.opening_quantity == 0:
            continue
        quote = quotes.get(h.ticker.upper())
        price_currency, raw_currency = _price_currency(h, quote)
        prices = _Series((d, normalize(Decimal(str(v)), raw_currency)[0]) for d, v in history_fn(h.ticker, start))
        if not prices:
            result.missing_prices.append(h.ticker)

        replay = _Replay(txs)
        qty, avg = h.opening_quantity, h.opening_avg_cost
        qty, avg = ledger.replay_position(qty, avg, replay.upto(start - timedelta(days=1)))
        held_in_range = qty > 0
        end_value = end_cost = None
        for i, day in enumerate(days):
            qty, avg = ledger.replay_position(qty, avg, replay.upto(day))
            if qty <= 0:
                continue
            held_in_range = True
            price = prices.at(day)
            value = fx.convert(qty * price, price_currency, day) if price is not None else fx.convert(qty * avg, h.currency, day)
            cost = fx.convert(qty * avg, h.currency, day)
            if value is not None:
                holdings_value[i] += value
            if cost is not None:
                invested[i] += cost
            if day == end:
                end_value, end_cost = value, cost
        if held_in_range:
            result.holdings.append(HoldingPerformance(
                holding=h, price_currency=price_currency,
                first_price=prices.first_on_or_after(start), last_price=prices.at(end),
                end_value=end_value, end_cost=end_cost, has_history=bool(prices),
            ))

    for account in accounts:
        replay = _Replay(account.transactions.all())
        balance = ledger.replay_cash(account.opening_balance, replay.upto(start - timedelta(days=1)))
        for i, day in enumerate(days):
            balance = ledger.replay_cash(balance, replay.upto(day))
            converted = fx.convert(balance, account.currency, day)
            if converted is not None:
                cash[i] += converted

    result.holdings_value, result.invested, result.cash = holdings_value, invested, cash
    result.net_worth = [h + c for h, c in zip(holdings_value, cash)]
    result.holdings.sort(key=lambda p: p.end_value or ZERO, reverse=True)
    result.missing_currencies = sorted(fx.missing)
    return result


@dataclass
class HoldingHistory:
    holding: Holding
    price_currency: str
    days: list[date] = field(default_factory=list)
    price: list[Decimal | None] = field(default_factory=list)
    avg_cost: list[Decimal | None] = field(default_factory=list)
    quantity: list[Decimal] = field(default_factory=list)
    trades: list = field(default_factory=list)  # transactions in range

    @property
    def avg_cost_comparable(self):
        """Average cost can only be drawn on the price chart in the same currency."""
        return self.price_currency == normalize(None, self.holding.currency)[1]


def holding_history(holding: Holding, start: date, end: date, history_fn=None, quote=None) -> HoldingHistory:
    from common.market_data import get_price_history, get_quote

    history_fn = history_fn or get_price_history
    quote = quote if quote is not None else get_quote(holding.ticker)
    price_currency, raw_currency = _price_currency(holding, quote)
    prices = _Series((d, normalize(Decimal(str(v)), raw_currency)[0]) for d, v in history_fn(holding.ticker, start))

    txs = list(holding.transactions.all())
    replay = _Replay(txs)
    qty, avg = ledger.replay_position(holding.opening_quantity, holding.opening_avg_cost,
                                      replay.upto(start - timedelta(days=1)))
    result = HoldingHistory(holding=holding, price_currency=price_currency, days=sample_days(start, end))
    for day in result.days:
        qty, avg = ledger.replay_position(qty, avg, replay.upto(day))
        result.price.append(prices.at(day))
        result.avg_cost.append(avg if qty > 0 else None)
        result.quantity.append(qty)
    result.trades = sorted((t for t in txs if start <= t.date <= end and t.tx_type in ("buy", "sell")),
                           key=lambda t: (t.date, t.created_at, t.pk))
    return result
