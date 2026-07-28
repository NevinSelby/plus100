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
DEFAULT_SPORTS = [
    "soccer_epl", "soccer_uefa_champs_league", "soccer_spain_la_liga",
    "soccer_germany_bundesliga", "soccer_italy_serie_a", "soccer_france_ligue_one",
    "soccer_usa_mls", "soccer_fifa_world_cup", "soccer_uefa_european_championship",
    "soccer_mexico_ligamx", "soccer_brazil_campeonato",
]


def list_soccer_sports(key: str) -> list[dict]:
    """All in-season soccer competitions (this endpoint costs no credits)."""
    r = requests.get(f"{BASE}/sports", params={"apiKey": key}, timeout=15)
    r.raise_for_status()
    return [{"key": s["key"], "title": s["title"]}
            for s in r.json() if s.get("group") == "Soccer" and s.get("active")]


def _window() -> tuple[str, str]:
    """Now through the end of tomorrow (local), in the UTC ISO format the API wants."""
    now = dt.datetime.now(dt.timezone.utc)
    local_tomorrow_end = (dt.datetime.now().astimezone() + dt.timedelta(days=1)) \
        .replace(hour=23, minute=59, second=59)
    end = local_tomorrow_end.astimezone(dt.timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return now.strftime(fmt), end.strftime(fmt)


def scan(key: str, sports: list[str] | None = None, region: str = "us,us2") -> dict:
    sports = sports or DEFAULT_SPORTS
    t_from, t_to = _window()
    events, errors = [], []
    remaining = None
    credits_spent = 0

    # step 1 (FREE): find which competitions actually have fixtures in the window,
    # so paid odds requests are only made where there is something to price
    active_sports = []
    for sport in sports:
        try:
            r = requests.get(f"{BASE}/sports/{sport}/events",
                             params={"apiKey": key, "commenceTimeFrom": t_from,
                                     "commenceTimeTo": t_to},
                             timeout=15)
            remaining = r.headers.get("x-requests-remaining", remaining)
            if r.status_code == 401:
                return {"error": "invalid_key", "detail": "The Odds API rejected this key."}
            if r.status_code == 429:
                return {"error": "quota", "detail": "Monthly request quota exhausted.",
                        "remaining": remaining}
            if r.status_code == 200 and r.json():
                active_sports.append(sport)
        except requests.RequestException as e:
            errors.append(f"{sport} (events): {e}")

    for sport in active_sports:
        try:
            r = requests.get(f"{BASE}/sports/{sport}/odds",
                             params={"apiKey": key, "regions": region,
                                     "markets": "h2h", "oddsFormat": "decimal",
                                     "commenceTimeFrom": t_from, "commenceTimeTo": t_to},
                             timeout=20)
            remaining = r.headers.get("x-requests-remaining", remaining)
            credits_spent += int(r.headers.get("x-requests-last", 0) or 0)
            if r.status_code == 401:
                return {"error": "invalid_key", "detail": "The Odds API rejected this key."}
            if r.status_code == 429:
                return {"error": "quota", "detail": "Monthly request quota exhausted.",
                        "remaining": remaining}
            if r.status_code == 404:
                continue  # competition not currently offered
            r.raise_for_status()
        except requests.RequestException as e:
            errors.append(f"{sport}: {e}")
            continue

        for ev in r.json():
            outcomes: dict[str, dict] = {}   # name -> {odds, book}
            hr_prices: dict[str, float] = {}
            for bk in ev.get("bookmakers", []):
                for mkt in bk.get("markets", []):
                    if mkt["key"] != "h2h":
                        continue
                    for oc in mkt.get("outcomes", []):
                        name, price = oc["name"], float(oc["price"])
                        if bk["key"] == "hardrockbet":
                            hr_prices[name] = price
                        cur = outcomes.get(name)
                        if cur is None or price > cur["odds"]:
                            outcomes[name] = {"odds": price, "book": bk["title"],
                                              "book_key": bk["key"]}
            if len(outcomes) < 2:
                continue
            inv = sum(1 / o["odds"] for o in outcomes.values())
            legs = [{"outcome": n, "odds": round(o["odds"], 3), "book": o["book"],
                     "stake_per_100": round(100 * (1 / o["odds"]) / inv, 2),
                     "at_hardrock": o["book_key"] == "hardrockbet"}
                    for n, o in outcomes.items()]
            events.append({
                "sport": sport,
                "match": f"{ev.get('home_team')} vs {ev.get('away_team')}",
                "commence": ev.get("commence_time"),
                "books_count": len(ev.get("bookmakers", [])),
                "coverage_pct": round(inv * 100, 2),
                "arb": inv < 1.0,
                "profit_pct": round((1 / inv - 1) * 100, 2) if inv < 1 else None,
                "legs": legs,
                "hardrock_listed": bool(hr_prices),
            })

    events.sort(key=lambda e: e["coverage_pct"])
    arbs = [e for e in events if e["arb"]]
    near = [e for e in events if not e["arb"] and e["coverage_pct"] <= 102.0]
    return {"arbs": arbs, "near_misses": near[:10], "scanned": len(events),
            "window": {"from": t_from, "to": t_to},
            "competitions_with_fixtures": active_sports,
            "credits_spent": credits_spent,
            "remaining_credits": remaining, "errors": errors}
