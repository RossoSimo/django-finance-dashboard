"""Builds a compact JSON snapshot of the user's portfolio to hand to the LLM."""

import json

from wallet.services import net_worth_summary
from alerts.models import PriceAlert
from watchlist.models import WatchlistItem


def _num(value):
    return float(value) if value is not None else None


def build_portfolio_context(user) -> str:
    summary = net_worth_summary(user)

    holdings = []
    for row in summary["rows"]:
        h = row["holding"]
        holdings.append({
            "ticker": h.ticker,
            "asset_type": h.asset_type,
            "quantity": float(h.quantity),
            "avg_cost": float(h.avg_cost),
            "avg_cost_currency": h.currency,
            "market_value": _num(row["market_value"]),  # in base currency; None if no FX rate
            "gain_pct": _num(row["gain_pct"]),
        })

    watchlist = [
        {"ticker": w.ticker, "asset_type": w.asset_type}
        for w in WatchlistItem.objects.filter(user=user)
    ]
    price_alerts = [
        {"ticker": a.ticker, "direction": a.direction, "target_price": float(a.target_price)}
        for a in PriceAlert.objects.filter(user=user, is_active=True)
    ]

    context = {
        "base_currency": summary["base_currency"],
        "currencies_without_fx_rate": summary["missing_currencies"],
        "cash_total": float(summary["cash_total"]),
        "holdings_value": float(summary["holdings_value"]),
        "net_worth": float(summary["net_worth"]),
        "holdings": holdings,
        "watchlist": watchlist,
        "active_price_alerts": price_alerts,
    }
    return json.dumps(context)
