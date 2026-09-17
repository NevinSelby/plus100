"""Best-bets engine: fetch live odds for the next 2 days, blend model
probabilities with the de-vigged market consensus, and rank every available
price by expected value.

The blend (75% market / 25% model) exists because the market consensus is the
strongest public predictor available; anchoring to it and deviating only where
the model disagrees is how professional bettors use models. Edges reported are
therefore conservative — a bet only surfaces when its price beats a
market-respecting probability estimate.
"""
from __future__ import annotations

import datetime as dt
import difflib
import re
import statistics

import requests

import numpy as np

from .data_store import Store, norm_key
from .model import MAX_GOALS, expected_goals, score_matrix

# The Odds API sport key -> (scope, is_neutral_venue)
SPORT_META = {
    "soccer_epl": ("club", False), "soccer_uefa_champs_league": ("club", False),
    "soccer_spain_la_liga": ("club", False), "soccer_germany_bundesliga": ("club", False),
    "soccer_italy_serie_a": ("club", False), "soccer_france_ligue_one": ("club", False),
    "soccer_usa_mls": ("club", False), "soccer_mexico_ligamx": ("club", False),
    "soccer_brazil_campeonato": ("club", False),
    "soccer_fifa_world_cup": ("intl", True),
    "soccer_uefa_european_championship": ("intl", True),
}

# registry league code -> Odds API sport key (for targeted single-match queries)
LEAGUE_TO_SPORT = {
    "E0": "soccer_epl", "SP1": "soccer_spain_la_liga",
    "D1": "soccer_germany_bundesliga", "I1": "soccer_italy_serie_a",
    "F1": "soccer_france_ligue_one", "USA": "soccer_usa_mls",
    "MEX": "soccer_mexico_ligamx", "BRA": "soccer_brazil_campeonato",
}


def candidate_sports(store: Store, sel_home: str, sel_away: str) -> list[str]:
    """Narrow the query to the competition(s) this matchup could appear in."""
    rh = store.registry.get(sel_home)
    ra = store.registry.get(sel_away)
    if not rh or not ra:
        return list(SPORT_META)
    if rh["scope"] == "intl" and ra["scope"] == "intl":
        return ["soccer_fifa_world_cup", "soccer_uefa_european_championship"]
    if rh["scope"] == "club" and ra["scope"] == "club":
        lh, la = rh["league"], ra["league"]
        if lh == la and lh in LEAGUE_TO_SPORT:
            return [LEAGUE_TO_SPORT[lh], "soccer_uefa_champs_league"]
        keys = {LEAGUE_TO_SPORT.get(lh), LEAGUE_TO_SPORT.get(la)} - {None}
        return ["soccer_uefa_champs_league", *sorted(keys)]
    return list(SPORT_META)


BASE = "https://api.the-odds-api.com/v4"


def _window() -> tuple[str, str]:
    """Now through the end of tomorrow (local), in the UTC ISO format the API wants."""
    now = dt.datetime.now(dt.timezone.utc)
    local_tomorrow_end = (dt.datetime.now().astimezone() + dt.timedelta(days=1)) \
        .replace(hour=23, minute=59, second=59)
    end = local_tomorrow_end.astimezone(dt.timezone.utc)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return now.strftime(fmt), end.strftime(fmt)



ODDSAPI_ALIASES = {
    "manchesterunited": "man-united", "manchestercity": "man-city",
    "hullcity": "hull", "ipswichtown": "ipswich", "leicestercity": "leicester",
    "norwichcity": "norwich", "coventrycity": "coventry", "derbycounty": "derby",
    "stokecity": "stoke", "swanseacity": "swansea", "cardiffcity": "cardiff",
    "westbromwichalbion": "west-brom", "sheffieldwednesday": "sheffield-weds",
    "queensparkrangers": "qpr", "prestonnorthend": "preston",
    "blackburnrovers": "blackburn", "bristolcity": "bristol-city",
    "newcastleunited": "newcastle", "wolverhamptonwanderers": "wolves",
    "tottenhamhotspur": "tottenham", "westhamunited": "west-ham",
    "brightonandhovealbion": "brighton", "nottinghamforest": "nott-m-forest",
    "leedsunited": "leeds", "parissaintgermain": "paris-sg",
    "atleticomadrid": "ath-madrid", "athleticbilbao": "ath-bilbao",
    "realsociedad": "sociedad", "realbetis": "betis",
    "bayernmunich": "bayern-munich", "borussiadortmund": "dortmund",
    "borussiamonchengladbach": "m-gladbach", "bayerleverkusen": "leverkusen",
    "eintrachtfrankfurt": "ein-frankfurt", "internazionale": "inter",
    "intermilan": "inter", "acmilan": "milan", "asroma": "roma",
    "sportinglisbon": "sp-lisbon", "psveindhoven": "psv-eindhoven",
    "losangelesgalaxy": "los-angeles-galaxy", "lagalaxy": "los-angeles-galaxy",
    "losangelesfc": "los-angeles-fc", "atlantaunited": "atlanta-utd",
    "stlouiscity": "st-louis-city", "intermiamicf": "inter-miami",
    "atleticosanluis": "atl-san-luis", "atleticomineiro": "atletico-mg",
    "atleticoparanaense": "athletico-pr", "fortunasittard": "for-sittard",
    "vitoriasc": "guimaraes", "vitoriaguimaraes": "guimaraes", "tigres": "tigres-uanl",
    "amedsk": "amedspor", "erzurumbb": "erzurumspor", "basaksehir": "buyuksehyr",
    "istanbulbasaksehir": "buyuksehyr", "atlantaunitedfc": "atlanta-utd",
}


_CLUB_TOKENS = {"fc", "cf", "sc", "ac", "as", "rc", "ca", "cs", "cp", "bc", "jk", "sk", "afc",
                "fsv", "vfb", "sv", "tsg", "ud", "sd", "us", "fk", "club", "cd", "de", "deportivo",
                "1", "04", "05", "09", "1899", "enschede"}


def _bare(name: str) -> str:
    """A club name without its legal-form furniture: '1. FC Köln' -> 'koln'."""
    toks = [t for t in re.split(r"[\s.\-]+", str(name).lower()) if t and t not in _CLUB_TOKENS]
    return norm_key(" ".join(toks)) or norm_key(name)


def _match_in_pool(pool: dict, name: str) -> str | None:
    """Exact, then bare-name exact, then containment, then a close fuzzy match —
    all inside ONE pool (a scope, optionally narrowed to one league)."""
    k = norm_key(name)
    for tid, (nk, _bk) in pool.items():
        if nk == k:
            return tid
    bk = _bare(name)
    hits = [tid for tid, (_nk, pb) in pool.items() if pb == bk]
    if len(hits) == 1:
        return hits[0]
    if len(bk) >= 5:
        cont = [(tid, pb) for tid, (_nk, pb) in pool.items()
                if len(pb) >= 5 and (bk in pb or pb in bk)]
        if len(cont) == 1:
            return cont[0][0]
        if len(cont) > 1:          # 'atletico' is in many names: keep the tightest fit
            cont.sort(key=lambda t: abs(len(t[1]) - len(bk)))
            if abs(len(cont[0][1]) - len(bk)) < abs(len(cont[1][1]) - len(bk)):
                return cont[0][0]
    close = difflib.get_close_matches(k, [nk for nk, _ in pool.values()], n=1, cutoff=0.86)
    if close:
        return next(tid for tid, (nk, _) in pool.items() if nk == close[0])
    return None


def resolve_team(store: Store, name: str, scope: str, league: str | None = None) -> str | None:
    """Map a bookmaker/schedule team name onto our registry id. When the source
    says which league the match belongs to, resolution is confined to that
    league's clubs first, and never falls back to a fuzzy guess outside it:
    that is how Liga MX 'Tigres' once became Argentina's Tigre."""
    k = norm_key(name)
    if k in ODDSAPI_ALIASES:
        tid = ODDSAPI_ALIASES[k]
        return tid if tid in store.registry else None
    pools = store.__dict__.setdefault("_name_pools", {})

    def pool_for(lg):
        key = (scope, lg)
        if key not in pools:       # built once per store; rebuilt with each refresh
            pools[key] = {tid: (norm_key(r["name"]), _bare(r["name"]))
                          for tid, r in store.registry.items()
                          if r["scope"] == scope and r["active"]
                          and (lg is None or r["league"] == lg)}
        return pools[key]

    if league:
        hit = _match_in_pool(pool_for(league), name)
        if hit:
            return hit
        return next((tid for tid, (nk, _) in pool_for(None).items() if nk == k), None)
    hit = _match_in_pool(pool_for(None), name)
    if hit:
        return hit
    if scope == "intl" and (slugged := name.lower().replace(" ", "-") + "@intl") in store.registry:
        return slugged
    return None


def _is_half_line(x) -> bool:
    return x is not None and abs(float(x) * 2 - round(float(x) * 2)) < 1e-9 \
        and abs(float(x) - round(float(x))) > 0.4


def _grade_event(store: Store, ev: dict, sport: str, scope: str, neutral: bool,
                 market_weight: float):
    """Grade every priced market of one event (1X2, totals, BTTS, spreads)
    against the model's score matrix. Returns (rows, skip_reason)."""
    home_name, away_name = ev.get("home_team"), ev.get("away_team")
    if not home_name or not away_name:
        return None, None
    hid = resolve_team(store, home_name, scope)
    aid = resolve_team(store, away_name, scope)
    if not hid or not aid or hid == aid:
        return None, f"{home_name} vs {away_name} (no team match)"

    eg = expected_goals(store, hid, aid, neutral)
    mat = score_matrix(eg["lambda_home"], eg["lambda_away"])
    g = np.arange(MAX_GOALS + 1)
    Hm, Am = np.meshgrid(g, g, indexing="ij")

    def model_prob(mkey: str, name: str, point) -> float | None:
        if mkey == "h2h":
            return {home_name: float(mat[Hm > Am].sum()),
                    "Draw": float(np.trace(mat)),
                    away_name: float(mat[Hm < Am].sum())}.get(name)
        if mkey == "totals" and _is_half_line(point):
            p_over = float(mat[(Hm + Am) > float(point)].sum())
            return p_over if name == "Over" else (1 - p_over) if name == "Under" else None
        if mkey == "btts":
            yes = float(mat[(Hm > 0) & (Am > 0)].sum())
            return {"Yes": yes, "No": 1 - yes}.get(name)
        if mkey == "spreads" and _is_half_line(point):
            if name == home_name:
                return float(mat[Hm + float(point) > Am].sum())
            if name == away_name:
                return float(mat[Am + float(point) > Hm].sum())
        return None

    def label(mkey: str, name: str, point) -> str:
        if mkey == "h2h":
            return name if name == "Draw" else f"{name} win"
        if mkey == "totals":
            return f"{name} {point} goals"
        if mkey == "btts":
            return f"Both teams score: {name}"
        return f"{name} {float(point):+g}"

    # group prices: (market, line) -> {book: {outcome name: (price, point)}}
    groups: dict = {}
    for bk in ev.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            mkey = mkt["key"]
            if mkey not in ("h2h", "totals", "btts", "spreads"):
                continue
            per_line: dict = {}
            for oc in mkt.get("outcomes", []):
                point = oc.get("point")
                # group spreads by the HOME team's signed line: Home -1.5 and
                # Home +1.5 are different propositions and must never be pooled
                if point is not None and mkey == "spreads":
                    gk = float(point) if oc["name"] == ev.get("home_team") else -float(point)
                elif point is not None:
                    gk = point
                else:
                    gk = None
                per_line.setdefault(gk, {})[oc["name"]] = (float(oc["price"]), point)
            for gk, prices in per_line.items():
                groups.setdefault((mkey, gk), {})[(bk["key"], bk["title"])] = prices

    rows = []
    for (mkey, gk), books in groups.items():
        need_n = 3 if mkey == "h2h" else 2
        devig: dict[str, list] = {}
        best: dict[str, tuple] = {}
        for (bkey, btitle), prices in books.items():
            if len(prices) != need_n:
                continue
            if any(model_prob(mkey, n, pt) is None for n, (_, pt) in prices.items()):
                continue  # quarter/integer lines or unknown outcomes
            inv = {n: 1 / pr for n, (pr, _) in prices.items()}
            s = sum(inv.values())
            for n, (pr, pt) in prices.items():
                devig.setdefault(n, []).append(inv[n] / s)
                if n not in best or pr > best[n][0]:
                    best[n] = (pr, btitle, bkey == "hardrockbet", pt)
        for n, plist in devig.items():
            pr, btitle, hr, pt = best[n]
            p_mkt = float(statistics.median(plist))
            p_mod = model_prob(mkey, n, pt)
            p = market_weight * p_mkt + (1 - market_weight) * p_mod
            edge = p * pr - 1
            b_frac = pr - 1
            kelly = max(0.0, (p * b_frac - (1 - p)) / b_frac) * 0.25
            rows.append({
                "home_id": hid, "away_id": aid,
                "match": f"{home_name} vs {away_name}",
                "commence": ev.get("commence_time"), "sport": sport,
                "market": mkey,
                "outcome": label(mkey, n, pt),
                "odds": round(pr, 2), "book": btitle, "at_hardrock": hr,
                "p_market": round(p_mkt, 4), "p_model": round(p_mod, 4),
                "p_blend": round(p, 4),
                "edge_pct": round(edge * 100, 2),
                "quarter_kelly_pct": round(kelly * 100, 2),
            })
    if not rows:
        return None, f"{home_name} vs {away_name} (no gradeable markets)"
    rows.sort(key=lambda b: -b["edge_pct"])
    return rows, None


def _targeted(key: str, store: Store, market_weight: float,
              sel_home: str, sel_away: str) -> dict:
    """Selected-match mode: free events calls to locate the fixture, then ONE
    paid per-event odds call. Costs ~8 credits (four markets) (0 if the fixture isn't listed)."""
    t_from, t_to = _window()
    remaining, errors = None, []
    for sport in candidate_sports(store, sel_home, sel_away):
        scope, neutral = SPORT_META.get(sport, ("club", False))
        try:
            ev_r = requests.get(f"{BASE}/sports/{sport}/events",
                                params={"apiKey": key, "commenceTimeFrom": t_from,
                                        "commenceTimeTo": t_to}, timeout=15)
            remaining = ev_r.headers.get("x-requests-remaining", remaining)
            if ev_r.status_code == 401:
                return {"error": "invalid_key"}
            if ev_r.status_code == 429:
                return {"error": "quota"}
            if ev_r.status_code != 200:
                continue
            hit = None
            for ev in ev_r.json():
                hid = resolve_team(store, ev.get("home_team", ""), scope)
                aid = resolve_team(store, ev.get("away_team", ""), scope)
                if hid and aid and {hid, aid} == {sel_home, sel_away}:
                    hit = ev
                    break
            if not hit:
                continue
            odds_r = requests.get(f"{BASE}/sports/{sport}/events/{hit['id']}/odds",
                                  params={"apiKey": key, "regions": "us,us2",
                                          "markets": "h2h,totals,btts,spreads",
                                          "oddsFormat": "decimal"},
                                  timeout=20)
            remaining = odds_r.headers.get("x-requests-remaining", remaining)
            if odds_r.status_code != 200:
                errors.append(f"{sport}: odds HTTP {odds_r.status_code}")
                continue
            rows, reason = _grade_event(store, odds_r.json(), sport, scope,
                                        neutral, market_weight)
            selected = ({"match": rows[0]["match"], "commence": rows[0]["commence"],
                         "bets": rows} if rows else None)
            return {"bets": [], "selected": selected, "all_evaluated": len(rows or []),
                    "fixtures": 1,
                    "skipped": [reason] if reason else [], "errors": errors,
                    "remaining_credits": remaining, "targeted": sport,
                    "method": f"targeted query: one odds call for this fixture only "
                              f"({sport}; 1X2, totals, BTTS, spreads ≈ 8 credits); "
                              f"probabilities = {int(market_weight*100)}% de-vigged "
                              f"market consensus + {int((1-market_weight)*100)}% model"}
        except requests.RequestException as e:
            errors.append(f"{sport}: {e}")
    return {"bets": [], "selected": None, "all_evaluated": 0, "fixtures": 0, "skipped": [], "errors": errors,
            "remaining_credits": remaining, "targeted": None,
            "method": "targeted query: fixture not listed by any book in the window "
                      "(no paid credits were spent)"}


# Fitted on 10,205 matches with odds (Jul 2024–Jun 2025), validated on 4,227
# unseen (Jul 2025–Jan 2026): vs the market the optimal weight is 1.0, but the
# Brier cost curve is nearly flat near the top — 0.75 costs only +0.0008,
# within measurement noise. A 25% model share is the most the data defends;
# it also grades earlier, softer prices where the model plausibly adds more.
FITTED_MARKET_WEIGHT = 0.75


def best_bets(key: str, store: Store, market_weight: float = FITTED_MARKET_WEIGHT,
              sel_home: str = "", sel_away: str = "") -> dict:
    if sel_home and sel_away:
        return _targeted(key, store, market_weight, sel_home, sel_away)

    t_from, t_to = _window()
    bets, skipped, errors = [], [], []
    remaining = None
    fixtures = 0

    for sport, (scope, neutral) in SPORT_META.items():
        try:
            ev_r = requests.get(f"{BASE}/sports/{sport}/events",
                                params={"apiKey": key, "commenceTimeFrom": t_from,
                                        "commenceTimeTo": t_to}, timeout=15)
            remaining = ev_r.headers.get("x-requests-remaining", remaining)
            if ev_r.status_code == 401:
                return {"error": "invalid_key"}
            if ev_r.status_code == 429:
                return {"error": "quota"}
            if ev_r.status_code != 200 or not ev_r.json():
                continue
            r = requests.get(f"{BASE}/sports/{sport}/odds",
                             params={"apiKey": key, "regions": "us,us2",
                                     "markets": "h2h", "oddsFormat": "decimal",
                                     "commenceTimeFrom": t_from, "commenceTimeTo": t_to},
                             timeout=20)
            remaining = r.headers.get("x-requests-remaining", remaining)
            if r.status_code != 200:
                continue
        except requests.RequestException as e:
            errors.append(f"{sport}: {e}")
            continue

        for ev in r.json():
            fixtures += 1
            rows, reason = _grade_event(store, ev, sport, scope, neutral, market_weight)
            if rows is None:
                if reason:
                    skipped.append(reason)
                continue
            bets.extend(rows)

    bets.sort(key=lambda b: -b["edge_pct"])
    # basic proportional de-vigging overstates longshot probability (books load
    # extra margin there), so long-priced outcomes must clear a higher bar
    # before being called value
    positive = [b for b in bets
                if b["edge_pct"] > (2.5 if b["p_blend"] < 0.25 else 1.0)]

    return {"bets": positive[:15], "selected": None,
            "all_evaluated": len(bets), "fixtures": fixtures,
            "skipped": skipped[:8], "errors": errors,
            "remaining_credits": remaining,
            "method": f"probabilities = {int(market_weight*100)}% de-vigged market consensus "
                      f"+ {int((1-market_weight)*100)}% model; edges use best price across books"}
