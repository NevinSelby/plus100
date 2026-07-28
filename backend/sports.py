"""Multi-sport support via The Odds API.

Football (soccer) has the full statistical model. Every other sport gets:
  - upcoming events (free endpoint)
  - de-vigged market consensus probabilities ("what the market believes")
  - best price per outcome across books (line shopping)
  - value flags where one book's price beats the consensus
  - arbitrage check
The hedge / parlay / odds-checker math in the app is sport-agnostic already.
"""
from __future__ import annotations

import statistics

import requests

from .scanner import BASE, _window

# curated, stable sport keys (The Odds API). two_way = no draw price.
SPORTS = [
    {"key": "soccer", "title": "Soccer", "emoji": "⚽", "two_way": False,
     "modeled": True, "note": "full statistical model + market blend"},
    {"key": "americanfootball_nfl", "title": "NFL", "emoji": "🏈", "two_way": True},
    {"key": "basketball_nba", "title": "NBA", "emoji": "🏀", "two_way": True},
    {"key": "baseball_mlb", "title": "MLB", "emoji": "⚾", "two_way": True},
    {"key": "icehockey_nhl", "title": "NHL", "emoji": "🏒", "two_way": True},
    {"key": "americanfootball_ncaaf", "title": "NCAAF", "emoji": "🎓", "two_way": True},
    {"key": "basketball_wnba", "title": "WNBA", "emoji": "🏀", "two_way": True},
    {"key": "mma_mixed_martial_arts", "title": "MMA", "emoji": "🥊", "two_way": True},
    {"key": "boxing_boxing", "title": "Boxing", "emoji": "🥊", "two_way": True},
]


def list_events(key: str, sport: str) -> dict:
    """Upcoming events in the next 2 days — free endpoint, no credits."""
    t_from, t_to = _window()
    r = requests.get(f"{BASE}/sports/{sport}/events",
                     params={"apiKey": key, "commenceTimeFrom": t_from,
                             "commenceTimeTo": t_to}, timeout=15)
    if r.status_code == 401:
        return {"error": "invalid_key"}
    if r.status_code == 429:
        return {"error": "quota"}
    if r.status_code != 200:
        return {"events": [], "note": f"no events listed (HTTP {r.status_code})"}
    events = [{"id": ev["id"], "home": ev.get("home_team"),
               "away": ev.get("away_team"), "commence": ev.get("commence_time")}
              for ev in r.json()]
    return {"events": events,
            "remaining_credits": r.headers.get("x-requests-remaining")}


def _half_line(x) -> bool:
    return x is not None and abs(float(x) * 2 - round(float(x) * 2)) < 1e-9 \
        and abs(float(x) - round(float(x))) > 0.4


def market_view(key: str, sport: str, event_id: str) -> dict:
    """One paid call: consensus + best prices + value + arb for one event.
    Markets: moneyline, spreads, totals (≈6 credits)."""
    r = requests.get(f"{BASE}/sports/{sport}/events/{event_id}/odds",
                     params={"apiKey": key, "regions": "us,us2",
                             "markets": "h2h,spreads,totals",
                             "oddsFormat": "decimal"}, timeout=20)
    if r.status_code == 401:
        return {"error": "invalid_key"}
    if r.status_code == 429:
        return {"error": "quota"}
    if r.status_code != 200:
        return {"error": "unavailable", "detail": f"HTTP {r.status_code}"}
    ev = r.json()
    home, away = ev.get("home_team"), ev.get("away_team")

    groups: dict = {}
    for bk in ev.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            mkey = mkt["key"]
            if mkey not in ("h2h", "spreads", "totals"):
                continue
            per_line: dict = {}
            for oc in mkt.get("outcomes", []):
                point = oc.get("point")
                gk = (abs(float(point)) if mkey == "spreads" else point) \
                    if point is not None else None
                per_line.setdefault(gk, {})[oc["name"]] = (float(oc["price"]), point)
            for gk, prices in per_line.items():
                groups.setdefault((mkey, gk), {})[(bk["key"], bk["title"])] = prices

    markets_out = []
    best_arb = None
    for (mkey, gk), books in groups.items():
        names = set()
        for prices in books.values():
            names |= set(prices)
        n_expected = max(len(p) for p in books.values())
        devig: dict[str, list] = {}
        best: dict[str, tuple] = {}
        for (bkey, btitle), prices in books.items():
            if len(prices) != n_expected:
                continue
            if mkey in ("spreads", "totals") and \
                    any(not _half_line(pt) for _, (_, pt) in prices.items()):
                continue
            inv = {n: 1 / pr for n, (pr, _) in prices.items()}
            s = sum(inv.values())
            for n, (pr, pt) in prices.items():
                devig.setdefault(n, []).append(inv[n] / s)
                if n not in best or pr > best[n][0]:
                    best[n] = (pr, btitle, bkey == "hardrockbet", pt)
        if not devig or len(devig) < n_expected:
            continue
        rows = []
        inv_best = 0.0
        for n, plist in devig.items():
            pr, btitle, hr, pt = best[n]
            p = float(statistics.median(plist))
            inv_best += 1 / pr
            label = n
            if mkey == "totals":
                label = f"{n} {pt}"
            elif mkey == "spreads":
                label = f"{n} {float(pt):+g}"
            rows.append({"outcome": label, "consensus_prob": round(p, 4),
                         "best_odds": round(pr, 2), "book": btitle,
                         "at_hardrock": hr,
                         "value_pct": round((p * pr - 1) * 100, 2)})
        rows.sort(key=lambda x: -x["value_pct"])
        arb = inv_best < 1.0
        if arb and (best_arb is None or inv_best < best_arb["coverage"]):
            best_arb = {"market": mkey, "line": gk, "coverage": round(inv_best, 4),
                        "profit_pct": round((1 / inv_best - 1) * 100, 2)}
        markets_out.append({"market": mkey, "line": gk, "outcomes": rows,
                            "books_count": len(books), "arb": arb})

    order = {"h2h": 0, "spreads": 1, "totals": 2}
    markets_out.sort(key=lambda m: (order.get(m["market"], 9),
                                    m["line"] if m["line"] is not None else 0))
    return {"match": f"{home} vs {away}" if home else event_id,
            "home": home, "away": away, "commence": ev.get("commence_time"),
            "markets": markets_out, "arb": best_arb,
            "remaining_credits": r.headers.get("x-requests-remaining"),
            "note": ("Probabilities are the combined view of all sportsbooks with the "
                     "bookmaker margin removed — the strongest available estimate for "
                     "this sport. Green value = one book pays above consensus.")}
