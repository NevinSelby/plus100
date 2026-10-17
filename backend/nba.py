"""Basketball (NBA) prediction engine.

Same philosophy as the football model, different mathematics. Basketball
scores are high and nearly continuous, so a game is a point MARGIN and a point
TOTAL, each close to normally distributed:

  margin ~ Normal(mu_m, sd_m)   mu_m from an Elo gap, home court and rest
  total  ~ Normal(mu_t, sd_t)   mu_t from the two sides' recent scoring and
                                 defending rates against the league average

Elo uses a margin-of-victory multiplier and a between-season regression to the
mean (the approach popularised by FiveThirtyEight); the slope from Elo gap to
points and both standard deviations are FITTED on the stored games, not
assumed, and re-fitted every time new results arrive. Win probability, every
spread line, every total line, the half-time picture and the projected score
all come from those two distributions, so they can never disagree with each
other.

Data: data/nba/games.csv (ESPN scoreboard, five seasons, appended daily).
"""
from __future__ import annotations

import csv
import datetime as dt
import math
import threading
import time
from pathlib import Path

import numpy as np

from . import espn

DATA = Path(__file__).resolve().parent.parent / "data" / "nba"
GAMES = DATA / "games.csv"

K = 20.0
HOME_ELO = 90.0                # ≈ 3 points of home court
SEASON_REGRESS = 0.25          # pull a quarter of the way back to 1505 each summer
B2B_PTS = 1.4                  # tired side on the second night of a back-to-back
RECENT_HALF_LIFE = 22.0        # games, for scoring/defending rates

_lock = threading.Lock()
_model: dict | None = None
_built_at = 0.0


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


_canon_cache: dict | None = None


def _canon(name: str) -> str | None:
    """Current ESPN display name for a franchise (ESPN renamed e.g. the Clippers
    mid-history); None for exhibition sides like 'Team LeBron'."""
    global _canon_cache
    if _canon_cache is None:
        current = {t["name"] for t in espn.nba_teams()} or set()
        by_nick = {}
        for n in current:
            by_nick.setdefault(n.split()[-1].lower(), []).append(n)
        _canon_cache = {"current": current, "nick": by_nick}
    if name.startswith("Team "):
        return None
    cur = _canon_cache["current"]
    if not cur or name in cur:
        return name
    hits = _canon_cache["nick"].get(name.split()[-1].lower()) or []
    return hits[0] if len(hits) == 1 else name


def load_games() -> list[dict]:
    if not GAMES.exists():
        return []
    rows = []
    with GAMES.open() as fh:
        for r in csv.DictReader(fh):
            try:
                home, away = _canon(r["home"]), _canon(r["away"])
                if not home or not away:
                    continue
                r = {**r, "home": home, "away": away}
                rows.append({"date": dt.date.fromisoformat(r["date"]), "season": int(r["season"]),
                             "stype": int(r["stype"]), "home": r["home"], "away": r["away"],
                             "hs": int(float(r["hs"])), "as": int(float(r["as_"])),
                             "neutral": r.get("neutral") == "1",
                             "h1": sum(float(r[k] or 0) for k in ("h_q1", "h_q2")) if r.get("h_q1") else None,
                             "a1": sum(float(r[k] or 0) for k in ("a_q1", "a_q2")) if r.get("a_q1") else None})
            except (KeyError, ValueError):
                continue
    rows.sort(key=lambda r: r["date"])
    return rows


def build() -> dict:
    """Run Elo through every game, fit the Elo→points slope and the two
    standard deviations, compute recent scoring/defending rates and rest."""
    games = load_games()
    elo: dict[str, float] = {}
    last_season: dict[str, int] = {}
    last_played: dict[str, dt.date] = {}
    hist: dict[str, list] = {}
    gap, margin, total, half = [], [], [], []
    pf: dict[str, list] = {}
    pa: dict[str, list] = {}
    cur_season = games[-1]["season"] if games else None
    for g in games:
        h, a = g["home"], g["away"]
        for t in (h, a):
            elo.setdefault(t, 1500.0)
            if last_season.get(t) is not None and last_season[t] != g["season"]:
                elo[t] = elo[t] + SEASON_REGRESS * (1505.0 - elo[t])
            last_season[t] = g["season"]
        hca = 0.0 if g["neutral"] else HOME_ELO
        rest_h = (g["date"] - last_played[h]).days if h in last_played else 3
        rest_a = (g["date"] - last_played[a]).days if a in last_played else 3
        d = elo[h] + hca - elo[a]
        if g["season"] >= (cur_season or 0) - 2:        # fit on the recent, modern-pace seasons
            gap.append(d)
            margin.append(g["hs"] - g["as"] + (B2B_PTS if rest_h == 1 else 0) - (B2B_PTS if rest_a == 1 else 0))
            total.append(g["hs"] + g["as"])
            if g["h1"] is not None and g["a1"] is not None:
                half.append((g["h1"] - g["a1"], g["hs"] - g["as"]))
        exp_h = 1.0 / (1.0 + 10 ** (-d / 400.0))
        mov = abs(g["hs"] - g["as"])
        winner_d = d if g["hs"] > g["as"] else -d
        mult = math.log(mov + 1.0) * (2.2 / (2.2 + 0.001 * winner_d))
        res = 1.0 if g["hs"] > g["as"] else 0.0
        delta = K * mult * (res - exp_h)
        elo[h] += delta
        elo[a] -= delta
        for t in (h, a):
            hist.setdefault(t, []).append((g["date"].isoformat(), round(elo[t])))
        last_played[h] = g["date"]
        last_played[a] = g["date"]
        pf.setdefault(h, []).append(g["hs"]); pa.setdefault(h, []).append(g["as"])
        pf.setdefault(a, []).append(g["as"]); pa.setdefault(a, []).append(g["hs"])

    # ---- fit: points per Elo point, residual spread, total spread
    x = np.array(gap); y = np.array(margin)
    if len(x) > 200:
        slope, intercept = np.polyfit(x, y, 1)
        sd_m = float(np.std(y - (slope * x + intercept)))
    else:
        slope, intercept, sd_m = 1 / 28.0, 0.0, 12.5
    t = np.array(total)
    sd_t = float(np.std(t)) if len(t) > 50 else 19.0
    lg_total = float(np.mean(t)) if len(t) else 228.0
    half_ratio = 0.5
    if len(half) > 200:
        hm = np.array([h for h, _ in half]); fm = np.array([f for _, f in half])
        half_ratio = float(np.clip(np.polyfit(fm, hm, 1)[0], 0.3, 0.7))
        sd_half = float(np.std(hm - half_ratio * fm))
    else:
        sd_half = sd_m * 0.72

    # ---- recent scoring / defending rates (exponentially weighted, per team)
    def ew(vals):
        if not vals:
            return lg_total / 2
        w = np.array([0.5 ** ((len(vals) - 1 - i) / RECENT_HALF_LIFE) for i in range(len(vals))])
        v = np.array(vals[-60:], dtype=float); w = w[-60:]
        return float((v * w).sum() / w.sum())
    off = {t: ew(v) for t, v in pf.items()}
    dfc = {t: ew(v) for t, v in pa.items()}
    lg_half = lg_total / 2

    # ---- walk-forward check on the last complete season: pick accuracy, Brier,
    # spread error. Honest numbers for the About page.
    ev = {"games": 0, "accuracy": None, "brier": None, "spread_mae": None}
    if cur_season:
        test = [g for g in games if g["season"] == cur_season - 1 or (g["season"] == cur_season and g["stype"] == 2)]
        test = [g for g in test if g["season"] == max(gg["season"] for gg in games if gg["stype"] == 2 and gg["season"] < cur_season or gg["season"] == cur_season)][-900:]
        # replay: reset to the ratings as they stood is expensive; use the sequential
        # pass above by re-running Elo on everything before each test game is overkill,
        # so approximate with a second sequential pass that records pre-game states.
        pre = []
        e2: dict[str, float] = {}; ls2: dict[str, int] = {}; lp2: dict[str, dt.date] = {}
        for g in games:
            h, a = g["home"], g["away"]
            for tm in (h, a):
                e2.setdefault(tm, 1500.0)
                if ls2.get(tm) is not None and ls2[tm] != g["season"]:
                    e2[tm] += SEASON_REGRESS * (1505.0 - e2[tm])
                ls2[tm] = g["season"]
            hca2 = 0.0 if g["neutral"] else HOME_ELO
            d2 = e2[h] + hca2 - e2[a]
            d2 -= (B2B_PTS if lp2.get(h) and (g["date"] - lp2[h]).days == 1 else 0) / slope
            d2 += (B2B_PTS if lp2.get(a) and (g["date"] - lp2[a]).days == 1 else 0) / slope
            if g in test:
                mu = slope * d2 + intercept
                p = 1 - _ncdf(-mu / sd_m)
                pre.append((p, 1.0 if g["hs"] > g["as"] else 0.0, mu, g["hs"] - g["as"]))
            exp2 = 1.0 / (1.0 + 10 ** (-(e2[h] + hca2 - e2[a]) / 400.0))
            mov = abs(g["hs"] - g["as"]); wd = (e2[h] + hca2 - e2[a]) if g["hs"] > g["as"] else -(e2[h] + hca2 - e2[a])
            delta = K * math.log(mov + 1.0) * (2.2 / (2.2 + 0.001 * wd)) * ((1.0 if g["hs"] > g["as"] else 0.0) - exp2)
            e2[h] += delta; e2[a] -= delta
            lp2[h] = g["date"]; lp2[a] = g["date"]
        if pre:
            ps = np.array([p for p, *_ in pre]); ys = np.array([y for _, y, *_ in pre])
            mus = np.array([m for *_, m, _ in pre]); ms = np.array([m for *_, m in pre])
            ev = {"games": int(len(pre)), "accuracy": round(float(((ps > 0.5) == (ys > 0.5)).mean()), 3),
                  "brier": round(float(((ps - ys) ** 2).mean()), 4),
                  "spread_mae": round(float(np.abs(mus - ms).mean()), 2)}

    return {"elo": elo, "hist": hist, "last_played": last_played, "off": off, "def": dfc,
            "slope": float(slope), "intercept": float(intercept), "sd_m": sd_m, "sd_t": sd_t,
            "lg_total": lg_total, "lg_half": lg_half, "half_ratio": half_ratio, "sd_half": sd_half,
            "n_games": len(games), "data_to": games[-1]["date"].isoformat() if games else None,
            "data_from": games[0]["date"].isoformat() if games else None,
            "season": cur_season, "eval": ev, "teams": sorted(elo)}


def model(force: bool = False) -> dict:
    global _model, _built_at
    with _lock:
        if _model is None or force or time.time() - _built_at > 6 * 3600:
            _model = build()
            _built_at = time.time()
        return _model


def refresh_games() -> bool:
    """Append every finished regular-season/playoff game since the last stored
    date (ESPN scoreboard, day by day). Returns True if anything was added."""
    games = load_games()
    start = (games[-1]["date"] + dt.timedelta(days=1)) if games else dt.date(2021, 10, 1)
    today = dt.date.today()
    if start > today:
        return False
    have = set()
    if GAMES.exists():
        with GAMES.open() as fh:
            have = {r["espn_id"] for r in csv.DictReader(fh)}
    new = []
    day = start
    while day <= today:
        for g in espn.nba_scoreboard(day, ttl=600):
            if not g["final"] or g["stype"] not in (2, 3) or g["id"] in have:
                continue
            new.append({"date": g["date"][:10], "season": None, "stype": g["stype"], "espn_id": g["id"],
                        "home": g["home"], "home_abbr": g["home_abbr"], "away": g["away"], "away_abbr": g["away_abbr"],
                        "hs": g["hs"], "as_": g["as"], "neutral": int(g["neutral"]),
                        "h_q1": "", "h_q2": "", "h_q3": "", "h_q4": "", "a_q1": "", "a_q2": "", "a_q3": "", "a_q4": "", "ot": 0})
        day += dt.timedelta(days=1)
    if not new:
        return False
    # season year = the year the season ENDS (ESPN convention); from the date
    for r in new:
        d = dt.date.fromisoformat(r["date"])
        r["season"] = d.year + 1 if d.month >= 9 else d.year
    DATA.mkdir(parents=True, exist_ok=True)
    cols = ["date", "season", "stype", "espn_id", "home", "home_abbr", "away", "away_abbr",
            "hs", "as_", "neutral", "h_q1", "h_q2", "h_q3", "h_q4", "a_q1", "a_q2", "a_q3", "a_q4", "ot"]
    write_header = not GAMES.exists()
    with GAMES.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        if write_header:
            w.writeheader()
        w.writerows(new)
    model(force=True)
    return True


# ------------------------------------------------------------------ teams
_TEAM_INFO: dict | None = None


def teams() -> list[dict]:
    """Registry: name, abbreviation, colours, logo, current Elo."""
    global _TEAM_INFO
    m = model()
    if _TEAM_INFO is None:
        _TEAM_INFO = {t["name"]: t for t in espn.nba_teams()}
    out = []
    for name in m["teams"]:
        info = _TEAM_INFO.get(name, {})
        out.append({"id": name, "name": name, "abbr": info.get("abbr"), "logo": info.get("logo"),
                    "color": ("#" + info["color"]) if info.get("color") else None,
                    "alt": ("#" + info["alt"]) if info.get("alt") else None,
                    "elo": round(m["elo"][name])})
    out.sort(key=lambda t: -t["elo"])
    return out


def resolve(name: str) -> str | None:
    """Bookmaker/ESPN name -> our team key (ESPN display name)."""
    from .data_store import norm_key
    m = model()
    k = norm_key(name)
    for t in m["teams"]:
        if norm_key(t) == k:
            return t
    for t in m["teams"]:
        tk = norm_key(t)
        if k in tk or tk in k or norm_key(t.split()[-1]) == norm_key(name.split()[-1]):
            return t
    return None


# ------------------------------------------------------------------ prediction
def _injury_impact(team: str) -> tuple[float, list]:
    """Points the side loses to listed absences: each player OUT costs a share of
    his scoring average (the replacement recovers the rest), capped sensibly."""
    try:
        inj = espn.nba_injuries().get(team) or []
    except Exception:  # noqa: BLE001
        return 0.0, []
    if not inj:
        return 0.0, []
    info = next((t for t in espn.nba_teams() if t["name"] == team), None)
    ppg = {}
    if info:
        season = model()["season"] or dt.date.today().year
        roster = {p["id"]: p["name"] for p in espn.nba_roster(info["id"])}
        for s in (season, season - 1):
            lead = espn.nba_leaders(info["id"], s)
            if lead:
                ppg = {roster.get(aid, aid): v for aid, v in lead.items()}
                break
    cost, out = 0.0, []
    for i in inj:
        st = (i.get("status") or "").lower()
        detail = (i.get("detail") or "").lower()
        p = ppg.get(i["player"], 0.0)
        # "rest" on a day-to-day tag is load management, not an absence from
        # the next game; only genuine outs and doubtfuls move the number
        if st == "out":
            cost += 0.55 * p
        elif ("doubtful" in st or "questionable" in st) and "rest" not in detail:
            cost += 0.25 * p
        elif "day" in st and "rest" not in detail:
            cost += 0.12 * p
        out.append({**i, "ppg": round(p, 1) if p else None})
    return min(cost, 10.0), out


def predict(home: str, away: str, neutral: bool = False) -> dict:
    m = model()
    if home not in m["elo"] or away not in m["elo"]:
        raise KeyError("unknown team")
    today = dt.date.today()
    rest_h = (today - m["last_played"][home]).days if home in m["last_played"] else 3
    rest_a = (today - m["last_played"][away]).days if away in m["last_played"] else 3
    b2b_h, b2b_a = rest_h <= 1, rest_a <= 1
    cost_h, inj_h = _injury_impact(home)
    cost_a, inj_a = _injury_impact(away)
    hca = 0.0 if neutral else HOME_ELO
    gap = m["elo"][home] + hca - m["elo"][away]
    mu = m["slope"] * gap + m["intercept"]
    mu += (-B2B_PTS if b2b_h else 0) + (B2B_PTS if b2b_a else 0)
    mu += -cost_h + cost_a
    sd = m["sd_m"]
    p_home = 1 - _ncdf(-mu / sd)
    # totals: each side's recent scoring vs the other's recent defending
    lg = m["lg_total"] / 2
    exp_h = (m["off"].get(home, lg) + m["def"].get(away, lg)) / 2 - cost_h * 0.6
    exp_a = (m["off"].get(away, lg) + m["def"].get(home, lg)) / 2 - cost_a * 0.6
    # reconcile the two views: split the projected total by the margin model
    tot = exp_h + exp_a
    proj_h, proj_a = (tot + mu) / 2, (tot - mu) / 2
    sd_t = m["sd_t"]

    def spread_prob(line: float) -> float:
        """P(home margin + line > 0): home covers at 'line' (negative = favourite)."""
        return 1 - _ncdf(-(mu + line) / sd)

    lines = sorted({round(x * 2) / 2 for x in (-mu - 6, -mu - 3, -mu, -mu + 3, -mu + 6)})
    spreads = [{"line": l, "home_cover": round(spread_prob(l), 4), "away_cover": round(1 - spread_prob(l), 4)}
               for l in lines]
    tlines = sorted({round(x * 2) / 2 for x in (tot - 8, tot - 4, tot, tot + 4, tot + 8)})
    totals = [{"line": l, "over": round(1 - _ncdf((l - tot) / sd_t), 4),
               "under": round(_ncdf((l - tot) / sd_t), 4)} for l in tlines]
    # margin buckets (home perspective)
    def bucket(lo, hi):
        return round(_ncdf((hi - mu) / sd) - _ncdf((lo - mu) / sd), 4)
    margins = {"home_by_11_plus": round(1 - _ncdf((10.5 - mu) / sd), 4),
               "home_by_6_10": bucket(5.5, 10.5), "home_by_1_5": bucket(0.0, 5.5),
               "away_by_1_5": bucket(-5.5, 0.0), "away_by_6_10": bucket(-10.5, -5.5),
               "away_by_11_plus": round(_ncdf((-10.5 - mu) / sd), 4)}
    mu_h = m["half_ratio"] * mu
    half = {"home": round(1 - _ncdf(-mu_h / m["sd_half"]), 4),
            "away": round(_ncdf(-mu_h / m["sd_half"]), 4),
            "proj_home": round(proj_h * 0.49, 1), "proj_away": round(proj_a * 0.49, 1)}
    tight = bucket(-5.5, 5.5)
    fav = home if mu >= 0 else away
    if abs(mu) >= 9:
        note = f"{fav} by double digits is the single likeliest shape ({max(margins['home_by_11_plus'], margins['away_by_11_plus']):.0%})."
    elif tight >= 0.38:
        note = f"Tough to call: {tight:.0%} of the time this is decided by five points or fewer."
    else:
        note = f"A {fav} lean by about {abs(mu):.0f}; {tight:.0%} of the time it stays within five."
    caveats = []
    if b2b_h: caveats.append(f"{home} play the second night of a back-to-back: priced at {B2B_PTS} points.")
    if b2b_a: caveats.append(f"{away} play the second night of a back-to-back: priced at {B2B_PTS} points.")
    if inj_h or inj_a:
        caveats.append("Listed injuries are priced by each absent player's scoring average; questionable players count at a quarter.")
    if m["n_games"] and m["season"] and sum(1 for _ in m["hist"].get(home, [])) < 5:
        caveats.append("Very early in the season: ratings carry last season's strength, regressed toward the mean.")
    return {
        "sport": "basketball",
        "home": {"id": home, "name": home, "elo": round(m["elo"][home]), "rest_days": rest_h, "injuries": inj_h},
        "away": {"id": away, "name": away, "elo": round(m["elo"][away]), "rest_days": rest_a, "injuries": inj_a},
        "neutral_venue": neutral,
        "win_prob": {"home": round(p_home, 4), "away": round(1 - p_home, 4),
                     "fair_odds": {"home": round(1 / p_home, 2), "away": round(1 / (1 - p_home), 2)}},
        "projected": {"home": round(proj_h, 1), "away": round(proj_a, 1), "total": round(tot, 1),
                      "margin": round(mu, 1), "spread": f"{home if mu >= 0 else away} -{abs(mu):.1f}"},
        "spreads": spreads, "totals": totals, "margins": margins, "half_time": half,
        "margin_note": note,
        "inputs": {"elo_gap": round(gap, 1), "home_court_pts": round(m["slope"] * hca, 1),
                   "rest_adjust": round((-B2B_PTS if b2b_h else 0) + (B2B_PTS if b2b_a else 0), 1),
                   "injury_adjust": round(-cost_h + cost_a, 1), "sd_margin": round(sd, 1), "sd_total": round(sd_t, 1)},
        "elo_history": {"home": m["hist"].get(home, [])[-40:], "away": m["hist"].get(away, [])[-40:]},
        "caveats": caveats,
    }


def upcoming(days: int = 7) -> list[dict]:
    """Scheduled games from ESPN over the next days, with our names."""
    out = []
    today = dt.date.today()
    for k in range(days + 1):
        day = today + dt.timedelta(days=k)
        for g in espn.nba_scoreboard(day, ttl=1800):
            if g["final"] or g["stype"] not in (2, 3):
                continue
            h, a = resolve(g["home"]), resolve(g["away"])
            if not h or not a:
                continue
            out.append({"home_id": h, "away_id": a, "home": h, "away": a, "kickoff": g["date"],
                        "live": g["live"], "neutral": g["neutral"], "book_line": g["odds"], "book_total": g["ou"],
                        "league": "NBA" if g["stype"] == 2 else "NBA Playoffs"})
    return out


def h2h(home: str, away: str, n: int = 8) -> list[dict]:
    games = [g for g in load_games() if {g["home"], g["away"]} == {home, away}]
    return [{"date": g["date"].isoformat(), "home": g["home"], "away": g["away"],
             "score": f"{g['hs']}-{g['as']}"} for g in games[-n:]][::-1]


# ------------------------------------------------------------------ odds grading
def grade_odds(key: str, market_weight: float = 0.75) -> dict:
    """Every priced NBA market (moneyline, spreads, totals) against the model:
    bookmaker margin removed per book, median across books, blended with the
    model, edge and quarter-Kelly stake. One odds call (three markets)."""
    import statistics
    import requests as _rq
    from .bestbets import BASE, _window
    frm, to = _window()
    try:
        r = _rq.get(f"{BASE}/sports/basketball_nba/odds", timeout=20,
                    params={"apiKey": key, "regions": "us,us2", "markets": "h2h,spreads,totals",
                            "oddsFormat": "decimal", "commenceTimeFrom": frm, "commenceTimeTo": to})
    except _rq.RequestException as e:
        return {"bets": [], "fixtures": 0, "errors": [str(e)]}
    if r.status_code == 401:
        return {"error": "quota", "bets": [], "fixtures": 0}
    if r.status_code != 200:
        return {"bets": [], "fixtures": 0, "errors": [f"odds api {r.status_code}"]}
    remaining = r.headers.get("x-requests-remaining")
    events = r.json() or []
    m = model()
    rows, skipped = [], []
    for ev in events:
        h, a = resolve(ev.get("home_team") or ""), resolve(ev.get("away_team") or "")
        if not h or not a or h == a:
            skipped.append(f"{ev.get('home_team')} vs {ev.get('away_team')}")
            continue
        p = predict(h, a, neutral=False)
        mu, sd = p["projected"]["margin"], p["inputs"]["sd_margin"]
        tot, sd_t = p["projected"]["total"], p["inputs"]["sd_total"]
        home_name, away_name = ev["home_team"], ev["away_team"]

        def model_prob(mkey, name, point):
            if mkey == "h2h":
                return {home_name: p["win_prob"]["home"], away_name: p["win_prob"]["away"]}.get(name)
            if mkey == "spreads" and point is not None:
                if name == home_name:
                    return 1 - _ncdf(-(mu + float(point)) / sd)
                if name == away_name:
                    return _ncdf(-(mu - float(point)) / sd)
            if mkey == "totals" and point is not None:
                over = 1 - _ncdf((float(point) - tot) / sd_t)
                return over if name == "Over" else (1 - over) if name == "Under" else None
            return None

        groups: dict = {}
        for bk in ev.get("bookmakers", []):
            for mkt in bk.get("markets", []):
                if mkt["key"] not in ("h2h", "spreads", "totals"):
                    continue
                per_line: dict = {}
                for oc in mkt.get("outcomes", []):
                    pt = oc.get("point")
                    gk = (float(pt) if oc["name"] == home_name else -float(pt)) if (pt is not None and mkt["key"] == "spreads") else pt
                    per_line.setdefault(gk, {})[oc["name"]] = (float(oc["price"]), pt)
                for gk, prices in per_line.items():
                    groups.setdefault((mkt["key"], gk), {})[(bk["key"], bk["title"])] = prices
        for (mkey, gk), books in groups.items():
            devig: dict = {}; best: dict = {}
            for (bkey, btitle), prices in books.items():
                if len(prices) != 2 or any(model_prob(mkey, n, pt) is None for n, (_, pt) in prices.items()):
                    continue
                inv = {n: 1 / pr for n, (pr, _) in prices.items()}
                s = sum(inv.values())
                for n, (pr, pt) in prices.items():
                    devig.setdefault(n, []).append(inv[n] / s)
                    if n not in best or pr > best[n][0]:
                        best[n] = (pr, btitle, bkey == "hardrockbet", pt)
            for n, plist in devig.items():
                pr, btitle, hr, pt = best[n]
                p_mkt = float(statistics.median(plist)); p_mod = model_prob(mkey, n, pt)
                pb = market_weight * p_mkt + (1 - market_weight) * p_mod
                edge = pb * pr - 1; b = pr - 1
                label = (f"{n} win" if mkey == "h2h" else f"{n} {float(pt):+g}" if mkey == "spreads"
                         else f"{n} {pt} points")
                rows.append({"home_id": h, "away_id": a, "match": f"{home_name} vs {away_name}",
                             "commence": ev.get("commence_time"), "sport": "basketball_nba",
                             "market": mkey, "outcome": label,
                             "p_market": round(p_mkt, 4), "p_model": round(p_mod, 4), "p_blend": round(pb, 4),
                             "odds": pr, "book": btitle, "at_hardrock": hr,
                             "edge_pct": round(edge * 100, 2),
                             "quarter_kelly_pct": round(max(0.0, (pb * b - (1 - pb)) / b) * 25, 2),
                             "books": len(plist)})
    rows.sort(key=lambda x: -x["edge_pct"])
    positive = [x for x in rows if x["edge_pct"] > (2.5 if x["p_blend"] < 0.25 else 1.0)]
    return {"bets": positive[:15], "all_evaluated": len(rows), "fixtures": len(events),
            "skipped": skipped[:8], "remaining_credits": remaining,
            "method": f"probabilities = {int(market_weight*100)}% de-vigged market consensus + "
                      f"{int((1-market_weight)*100)}% model (Elo margin and totals distributions); "
                      "edges use the best price across books"}
