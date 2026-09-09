"""Cross-book arbitrage scanner backed by The Odds API (the-odds-api.com).

Pulls current odds for soccer competitions across US bookmakers (Hard Rock Bet
included where it prices the fixture), finds the best price per outcome across
books, and flags fixtures where best-price coverage sums under 100% (a true
arbitrage) or close to it (worth watching).
"""
from __future__ import annotations

import datetime as dt

import requests

BASE = "https://api.the-odds-api.com/v4"

# soccer competitions worth scanning by default (keys per The Odds API)
def _window() -> tuple[str, str]:
    """Now through the end of tomorrow (local), in the UTC ISO format the API wants."""
    now = dt.datetime.now(dt.timezone.utc)
    local_tomorrow_end = (dt.datetime.now().astimezone() + dt.timedelta(days=1)) \
        .replace(hour=23, minute=59, second=59)
    end = local_tomorrow_end.astimezone(dt.timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return now.strftime(fmt), end.strftime(fmt)
