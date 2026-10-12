"""Builds a compact JSON snapshot of the user's portfolio to hand to the LLM."""

import json

from wallet.services import net_worth_summary
from watchlist.models import WatchlistItem


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
            "market_value": float(row["market_value"]),
            "gain_pct": float(row["gain_pct"]) if row["gain_pct"] is not None else None,
        })

    watchlist = [
        {"ticker": w.ticker, "asset_type": w.asset_type,
         "target_price": float(w.target_price) if w.target_price else None}
        for w in WatchlistItem.objects.filter(user=user)
    ]

    context = {
        "cash_total": float(summary["cash_total"]),
        "holdings_value": float(summary["holdings_value"]),
        "net_worth": float(summary["net_worth"]),
        "holdings": holdings,
        "watchlist": watchlist,
    }
    return json.dumps(context)
