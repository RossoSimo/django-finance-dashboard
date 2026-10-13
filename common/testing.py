"""Helpers shared by tests that render the dashboard."""

from datetime import date

from accounts.models import get_base_currency
from performance.services import PortfolioHistory


def empty_dashboard_history(user, key, *, holding=None, earliest=None):
    """Stand-in for history_for_range: an empty history, so no market calls.

    Use as `@mock.patch("dashboard.views.history_for_range", new=empty_dashboard_history)`
    (`new=` keeps the decorated test's positional arguments unchanged).
    """
    today = date.today()
    return PortfolioHistory(base_currency=get_base_currency(user), start=today, end=today)
