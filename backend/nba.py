"""Basketball (NBA) prediction engine.

Same philosophy as the football model, different mathematics. Basketball
scores are high and nearly continuous, so a game is a point MARGIN and a point
TOTAL, each close to normally distributed:

  margin ~ Normal(mu_m, sd_m)   mu_m from an Elo gap, home court and rest
  total  ~ Normal(mu_t, sd_t)   mu_t from the two sides' recent scoring and
                                 defending rates against the league average

Elo uses a margin-of-victory multiplier and a between-season regression to the
mean (the approach popularised by FiveThirtyEight). Everything that turns a
rating gap into points is FITTED on the stored games, never assumed: the
points-per-Elo slope, home court (the intercept of margin on the raw rating
gap), both standard deviations and the first-half share of points. The Elo
home bonus used inside the rating update is that fitted home court divided by
the fitted slope, so the two never disagree. Win probability, every spread and
total line, the half-time picture and the projected score all come from the
same two distributions.

Honest evaluation: the last complete season is predicted with parameters
fitted only on the seasons before it (true walk-forward), then everything is
refitted on all games for live use.

Data: data/nba/games.csv (ESPN scoreboard, five seasons, appended every refresh).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests

from . import espn
from .bestbets import (BASE as ODDS_BASE, FITTED_MARKET_WEIGHT, _is_half_line, _window,
                       collect_groups, count_books, grade_groups, odds_error)

DATA = Path(__file__).resolve().parent.parent / "data" / "nba"
GAMES = DATA / "games.csv"
TEAMS_FILE = DATA / "teams.json"          # last good ESPN franchise list (fallback if ESPN is down)
SCAN_FILE = DATA / "scanned_to.txt"       # last US calendar day the scoreboard scan has finished
COLS = ["date", "season", "stype", "espn_id", "home", "home_abbr", "away", "away_abbr",
        "hs", "as_", "neutral", "h_q1", "h_q2", "h_q3", "h_q4", "a_q1", "a_q2", "a_q3", "a_q4", "ot"]

K = 20.0
SEASON_REGRESS = 0.25          # pull a quarter of the way back to 1505 each summer
B2B_PTS = 1.4                  # tired side on the second night of a back-to-back (assumed, not fitted)
RECENT_HALF_LIFE = 22.0        # games, for team scoring/defending rates
PROP_HALF_LIFE = 15.0          # games, for player per-game stats
ROTATION = 8                   # players priced per side
PRIOR_GAMES = 12.0             # last season counts as this many games against this season's log
FACTOR_CAP = 0.10              # matchup scaling of a player's averages is held within 10 percent
MIN_PROP_MEAN = 0.5            # below this weighted average a stat line is not offered
MIN_FIT_GAMES = 200
# injury pricing: assumptions, listed in every prediction's caveats
INJURY_SHARE = {"out": 0.55, "doubtful": 0.25, "day": 0.12}
INJURY_CAP_PTS = 10.0
INJURY_TOTAL_FACTOR = 0.6
MARKET_NOTE = (f"the {int(FITTED_MARKET_WEIGHT * 100)}% market weight was fitted on football "
               f"(bestbets.FITTED_MARKET_WEIGHT) and is borrowed for the NBA, not refitted")

_lock = threading.Lock()
_model: dict | None = None
_built_at = 0.0
_TEAM_INFO: dict | None = None


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def season_of(day: dt.date) -> int:
    """ESPN convention: a season is named by the year it ends (Oct 2026 -> 2027)."""
    return day.year + 1 if day.month >= 9 else day.year


def season_label(season: int) -> str:
    return f"{season - 1}-{str(season)[2:]}"


# ------------------------------------------------------------------ teams
def _team_info() -> dict:
    """name -> ESPN team record for the 30 franchises. Fetched from ESPN and
    mirrored to disk; the disk copy serves when ESPN is down. An empty answer
    is never cached."""
    global _TEAM_INFO
    if _TEAM_INFO:
        return _TEAM_INFO
    teams = espn.nba_teams()
    if len(teams) >= 30:
        try:
            TEAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
            TEAMS_FILE.write_text(json.dumps(teams))
        except OSError:
            pass
    else:
        try:
            teams = json.loads(TEAMS_FILE.read_text())
        except (OSError, ValueError):
            teams = []
    info = {t["name"]: t for t in teams}
    if info:
        _TEAM_INFO = info
    return info


def _team_card(name: str, elo: float | None) -> dict:
    info = _team_info().get(name, {})
    return {"id": name, "name": name, "abbr": info.get("abbr"), "logo": info.get("logo"),
            "color": ("#" + info["color"]) if info.get("color") else None,
            "alt": ("#" + info["alt"]) if info.get("alt") else None,
            "elo": round(elo) if elo is not None else None}


def teams() -> list[dict]:
    """Registry: name, abbreviation, colours, logo, current Elo."""
    m = model()
    out = [_team_card(name, m["elo"][name]) for name in m["teams"]]
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


# ------------------------------------------------------------------ games file
def _read_csv() -> list[dict]:
    if not GAMES.exists():
        return []
    with GAMES.open() as fh:
        return list(csv.DictReader(fh))


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_games(raw: list[dict] | None = None) -> list[dict]:
    """Stored games with franchise names canonicalised to ESPN's current display
    names (ESPN renamed e.g. the Clippers mid-history). Sides that are not one
    of the 30 franchises (All-Star exhibitions) are dropped."""
    raw = _read_csv() if raw is None else raw
    current = set(_team_info())
    if not current:
        # neither ESPN nor the disk mirror: a franchise is a side that plays a
        # season's worth of games, an exhibition side plays one or two
        n: dict[str, int] = {}
        for r in raw:
            for k in ("home", "away"):
                n[r[k]] = n.get(r[k], 0) + 1
        current = {t for t, c in n.items() if c >= 50}
    by_nick: dict[str, list] = {}
    for t in current:
        by_nick.setdefault(t.split()[-1].lower(), []).append(t)

    def canon(name: str) -> str | None:
        if name in current:
            return name
        hits = by_nick.get(name.split()[-1].lower()) or []
        return hits[0] if len(hits) == 1 else None

    rows = []
    for r in raw:
        home, away = canon(r["home"]), canon(r["away"])
        if not home or not away:
            continue
        try:
            hq = [_num(r.get(k)) for k in ("h_q1", "h_q2")]
            aq = [_num(r.get(k)) for k in ("a_q1", "a_q2")]
            rows.append({"date": dt.date.fromisoformat(r["date"]), "season": int(r["season"]),
                         "stype": int(r["stype"]), "home": home, "away": away,
                         "hs": int(float(r["hs"])), "as": int(float(r["as_"])),
                         "neutral": r.get("neutral") == "1",
                         "h1": sum(hq) if None not in hq else None,
                         "a1": sum(aq) if None not in aq else None})
        except (KeyError, ValueError):
            continue
    rows.sort(key=lambda r: r["date"])
    return rows


# ------------------------------------------------------------------ Elo and fits
def _elo_pass(games: list[dict], home_elo: float) -> dict:
    """The one Elo implementation: run every game in order with `home_elo`
    rating points of home court inside the expected-score step. Returns the
    final state plus, per game, the PRE-game inputs the fits and the
    evaluation need (raw rating gap, back-to-back correction, outcome)."""
    elo: dict[str, float] = {}
    last_season: dict[str, int] = {}
    last_played: dict[str, dt.date] = {}
    hist: dict[str, list] = {}
    recs = []
    for g in games:
        h, a = g["home"], g["away"]
        for t in (h, a):
            elo.setdefault(t, 1500.0)
            if t in last_season and last_season[t] != g["season"]:
                elo[t] += SEASON_REGRESS * (1505.0 - elo[t])
            last_season[t] = g["season"]
        gap = elo[h] - elo[a]
        tired_h = h in last_played and (g["date"] - last_played[h]).days == 1
        tired_a = a in last_played and (g["date"] - last_played[a]).days == 1
        margin = g["hs"] - g["as"]
        # the correction that turns the observed margin into a rested one
        b2b = (B2B_PTS if tired_h else 0.0) - (B2B_PTS if tired_a else 0.0)
        recs.append({"season": g["season"], "neutral": g["neutral"], "gap": gap, "b2b": b2b,
                     "margin": margin, "total": g["hs"] + g["as"], "h1": g["h1"], "a1": g["a1"]})
        d = gap + (0.0 if g["neutral"] else home_elo)
        exp_h = 1.0 / (1.0 + 10 ** (-d / 400.0))
        winner_d = d if margin > 0 else -d
        mult = math.log(abs(margin) + 1.0) * (2.2 / (2.2 + 0.001 * winner_d))
        delta = K * mult * ((1.0 if margin > 0 else 0.0) - exp_h)
        elo[h] += delta
        elo[a] -= delta
        for t in (h, a):
            hist.setdefault(t, []).append((g["date"].isoformat(), round(elo[t])))
        last_played[h] = g["date"]
        last_played[a] = g["date"]
    return {"elo": elo, "last_season": last_season, "last_played": last_played,
            "hist": hist, "recs": recs}


def _fit_margin(recs: list[dict]) -> tuple[float, float, float] | None:
    """rested margin = slope * raw Elo gap + intercept, on non-neutral games.
    The intercept IS home court in points; the slope is points per Elo point."""
    pts = [(r["gap"], r["margin"] + r["b2b"]) for r in recs if not r["neutral"]]
    if len(pts) < MIN_FIT_GAMES:
        return None
    x = np.array([p[0] for p in pts]); y = np.array([p[1] for p in pts])
    slope, intercept = np.polyfit(x, y, 1)
    sd = float(np.std(y - (slope * x + intercept)))
    return float(slope), float(intercept), sd


def _fit(games: list[dict]) -> dict | None:
    """Two passes: the first (no home bonus in the ratings) fits home court in
    points, which fixes the Elo home bonus; the second runs Elo with that bonus
    and refits. None with too few games to fit."""
    first = _fit_margin(_elo_pass(games, 0.0)["recs"])
    if first is None:
        return None
    slope, intercept, _ = first
    home_elo = intercept / slope
    state = _elo_pass(games, home_elo)
    slope, intercept, sd_m = _fit_margin(state["recs"])
    return {"slope": slope, "hca_pts": intercept, "home_elo": home_elo, "sd_m": sd_m,
            "state": state}


def _evaluate(games: list[dict], cur_season: int) -> dict:
    """Walk-forward check: the last complete season (or the current one once it
    holds 400 games) predicted with slope, home court and spread fitted only on
    the seasons before it. Honest numbers for the About page."""
    seasons = sorted({g["season"] for g in games})
    cur_n = sum(1 for g in games if g["season"] == cur_season)
    earlier = [s for s in seasons if s < cur_season]
    target = cur_season if cur_n >= 400 else (earlier[-1] if earlier else None)
    out = {"season": season_label(target) if target else None, "games": 0,
           "accuracy": None, "brier": None, "spread_mae": None,
           "method": "parameters fitted on the seasons before the evaluated season"}
    if target is None:
        return out
    train = [g for g in games if g["season"] < target]
    fit = _fit(train)
    if fit is None:
        return out
    recs = [r for r in _elo_pass([g for g in games if g["season"] <= target], fit["home_elo"])["recs"]
            if r["season"] == target]
    mu = np.array([fit["slope"] * r["gap"] + (0.0 if r["neutral"] else fit["hca_pts"]) - r["b2b"]
                   for r in recs])
    actual = np.array([r["margin"] for r in recs], dtype=float)
    p = np.array([1 - _ncdf(-m / fit["sd_m"]) for m in mu])
    won = (actual > 0).astype(float)
    out.update({"games": int(len(recs)),
                "accuracy": round(float(((p > 0.5) == (won > 0.5)).mean()), 3),
                "brier": round(float(((p - won) ** 2).mean()), 4),
                "spread_mae": round(float(np.abs(mu - actual).mean()), 2)})
    return out


def build() -> dict:
    """Fit everything on the stored games and assemble the live model."""
    raw = _read_csv()
    games = load_games(raw)
    cur_season = season_of(dt.date.today())
    seasons = sorted({g["season"] for g in games})
    fit = _fit(games)
    if fit is None:
        # fewer than MIN_FIT_GAMES stored games: nothing can be measured yet
        state = _elo_pass(games, 0.0)
        fit = {"slope": 1 / 28.0, "hca_pts": 0.0, "home_elo": 0.0, "sd_m": 12.5, "state": state,
               "fitted": False}
    state = fit.pop("state")
    recs = state["recs"]
    elo = state["elo"]
    # a side that has not played yet this season gets its summer regression now
    # rather than at its first game, so pre-season predictions use rested ratings
    for t, s in state["last_season"].items():
        if s < cur_season:
            elo[t] += SEASON_REGRESS * (1505.0 - elo[t])

    # ---- totals: league scoring and spread over the latest two REGULAR seasons
    #      (playoff games are slower and lower scoring; recs align 1:1 with games)
    recent = [r for r, g in zip(recs, games) if seasons and r["season"] >= seasons[-1] - 1 and g["stype"] == 2]
    tot = np.array([r["total"] for r in recent], dtype=float)
    lg_total = float(tot.mean()) if len(tot) else 228.0
    sd_t = float(tot.std()) if len(tot) > 50 else 19.0

    # ---- first half: share of points scored before the break and how much of
    # the final margin is usually in place by then
    with_half = [r for r in recs if r["h1"] is not None and r["a1"] is not None]
    half_share, half_ratio, sd_half = 0.5, 0.5, fit["sd_m"] * 0.72
    if len(with_half) >= MIN_FIT_GAMES:
        half_share = float(sum(r["h1"] + r["a1"] for r in with_half) / sum(r["total"] for r in with_half))
        hm = np.array([r["h1"] - r["a1"] for r in with_half]); fm = np.array([r["margin"] for r in with_half], dtype=float)
        half_ratio = float(np.clip(np.polyfit(fm, hm, 1)[0], 0.3, 0.7))
        sd_half = float(np.std(hm - half_ratio * fm))

    # ---- recent scoring / defending rates (exponentially weighted, per team)
    pf: dict[str, list] = {}
    pa: dict[str, list] = {}
    games_this_season: dict[str, int] = {}
    for g in games:
        if g["season"] == cur_season:
            for t in (g["home"], g["away"]):
                games_this_season[t] = games_this_season.get(t, 0) + 1
        if g["stype"] != 2:
            continue                      # scoring rates: regular season only
        pf.setdefault(g["home"], []).append(g["hs"]); pa.setdefault(g["home"], []).append(g["as"])
        pf.setdefault(g["away"], []).append(g["as"]); pa.setdefault(g["away"], []).append(g["hs"])

    def ew(vals):
        v = np.array(vals[-60:], dtype=float)
        w = 0.5 ** ((len(v) - 1 - np.arange(len(v))) / RECENT_HALF_LIFE)
        return float((v * w).sum() / w.sum()) if len(v) else lg_total / 2

    fit_out = {"points_per_elo": round(fit["slope"], 4), "home_court_pts": round(fit["hca_pts"], 2),
               "home_elo": round(fit["home_elo"], 1),
               "sd_margin": round(fit["sd_m"], 2), "sd_total": round(sd_t, 2),
               "half_share": round(half_share, 4), "league_total": round(lg_total, 1),
               "b2b_pts": B2B_PTS, "seasons": seasons, "games": len(games),
               "method": ("margin regressed on the raw Elo gap over every stored non-neutral game: "
                          "slope = points per Elo point, intercept = home court; the Elo home bonus is "
                          "intercept / slope. League total and its spread are from the latest two seasons. "
                          f"Back-to-back cost ({B2B_PTS} points) is assumed, not fitted.")}
    if fit.get("fitted") is False:
        fit_out["method"] = f"fewer than {MIN_FIT_GAMES} stored games: placeholders until the games file is populated"

    return {"elo": elo, "hist": state["hist"], "last_played": state["last_played"],
            "last_season": state["last_season"], "games_this_season": games_this_season,
            "off": {t: ew(v) for t, v in pf.items()}, "def": {t: ew(v) for t, v in pa.items()},
            "slope": fit["slope"], "hca_pts": fit["hca_pts"], "home_elo": fit["home_elo"],
            "sd_m": fit["sd_m"], "sd_t": sd_t, "lg_total": lg_total,
            "half_share": half_share, "half_ratio": half_ratio, "sd_half": sd_half,
            "games": games, "n_games": len(games), "seasons": seasons,
            "season_year": cur_season, "season": season_label(cur_season),
            "data_from": games[0]["date"].isoformat() if games else None,
            "data_to": games[-1]["date"].isoformat() if games else None,
            "eval": _evaluate(games, cur_season), "fit": fit_out, "teams": sorted(elo)}


def model(force: bool = False) -> dict:
    global _model, _built_at, _TEAM_INFO
    with _lock:
        if _model is None or force or time.time() - _built_at > 6 * 3600:
            if force:
                _TEAM_INFO = None
            _model = build()
            _built_at = time.time()
        return _model


def meta() -> dict:
    """What /api/nba/meta shows: data span, the fitted parameters and the
    walk-forward evaluation."""
    m = model()
    return {"games": m["n_games"], "data_from": m["data_from"], "data_to": m["data_to"],
            "season": m["season"], "eval": m["eval"], "fit": m["fit"]}


# ------------------------------------------------------------------ refresh
def _read_scanned_to() -> dt.date | None:
    try:
        return dt.date.fromisoformat(SCAN_FILE.read_text().strip())
    except (OSError, ValueError):
        return None


def refresh_games() -> bool:
    """Append every finished regular-season/playoff game ESPN lists since the
    last scanned day, with per-quarter scores. The scan position is persisted
    so a refresh re-reads only the last two days (a game older than that is
    final), not the whole off-season. Returns True if anything was added."""
    raw = _read_csv()
    have = {r["espn_id"] for r in raw}
    today = dt.date.today()
    scanned = _read_scanned_to()
    if scanned:
        start = scanned + dt.timedelta(days=1)
    elif raw:
        # ESPN's scoreboard day is a US date and our stored date is UTC, so
        # overlap by a day; espn_id de-duplicates
        start = max(dt.date.fromisoformat(r["date"]) for r in raw) - dt.timedelta(days=1)
    else:
        start = dt.date(2021, 10, 1)
    if start > today:
        return False
    franchises = set(_team_info())
    new = []
    day, ok_through = start, None
    while day <= today:
        board = espn.nba_scoreboard(day, ttl=600)
        if board is None:
            break                             # ESPN unreachable: resume from this day next time
        ok_through = day
        for g in board:
            if not g["final"] or g["stype"] not in (2, 3) or g["id"] in have:
                continue
            if franchises and (g["home"] not in franchises or g["away"] not in franchises):
                continue                      # All-Star and other exhibition sides
            d = dt.date.fromisoformat(g["date"][:10])
            hq, aq = (g["h_periods"] + [None] * 4)[:4], (g["a_periods"] + [None] * 4)[:4]
            row = {"date": d.isoformat(), "season": g["season_year"] or season_of(d),
                   "stype": g["stype"], "espn_id": g["id"],
                   "home": g["home"], "home_abbr": g["home_abbr"], "away": g["away"], "away_abbr": g["away_abbr"],
                   "hs": g["hs"], "as_": g["as"], "neutral": int(g["neutral"]),
                   "ot": max(0, len(g["h_periods"]) - 4)}
            for i in range(4):
                row[f"h_q{i + 1}"] = "" if hq[i] is None else hq[i]
                row[f"a_q{i + 1}"] = "" if aq[i] is None else aq[i]
            new.append(row)
            have.add(g["id"])
        day += dt.timedelta(days=1)
    if ok_through:
        # today and yesterday are re-read next time (late tip-offs may not be final yet)
        try:
            DATA.mkdir(parents=True, exist_ok=True)
            SCAN_FILE.write_text(min(ok_through, today - dt.timedelta(days=2)).isoformat())
        except OSError:
            pass
    if not new:
        return False
    write_header = not GAMES.exists()
    with GAMES.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        if write_header:
            w.writeheader()
        w.writerows(new)
    model(force=True)
    return True


# ------------------------------------------------------------------ injuries
def _team_id(team: str) -> str | None:
    return (_team_info().get(team) or {}).get("id")


def _leaders(team: str, stat: str) -> dict | None:
    """athlete id -> per-game value, this season if it has stats, else last.
    None when ESPN could not be reached for either."""
    tid = _team_id(team)
    if not tid:
        return None
    season = model()["season_year"]
    failed = True
    for s in (season, season - 1):
        lead = espn.nba_leaders(tid, s, stat)
        if lead:
            return lead
        failed &= lead is None
    return None if failed else {}


def _injury_impact(team: str) -> tuple[float, list, bool]:
    """(points lost, listed players, unavailable). Each absent player costs a
    share of his scoring average (the replacement recovers the rest), capped.
    `unavailable` is True when ESPN's injury report or the scoring averages
    could not be fetched, so the UI can say so instead of 'nobody listed'."""
    report = espn.nba_injuries()
    if report is None:
        return 0.0, [], True
    inj = report.get(team) or []
    if not inj:
        return 0.0, [], False
    tid = _team_id(team)
    roster = espn.nba_roster(tid) if tid else None
    lead = _leaders(team, "pointsPerGame")
    if roster is None or lead is None:
        return 0.0, [{**i, "ppg": None} for i in inj], True
    names = {p["id"]: p["name"] for p in roster}
    ppg = {names.get(aid, aid): v for aid, v in lead.items()}
    cost, out = 0.0, []
    for i in inj:
        st = (i.get("status") or "").lower()
        detail = (i.get("detail") or "").lower()
        p = ppg.get(i["player"], 0.0)
        # "rest" on a day-to-day tag is load management, not an absence from
        # the next game; only genuine outs and doubtfuls move the number
        if st == "out":
            cost += INJURY_SHARE["out"] * p
        elif ("doubtful" in st or "questionable" in st) and "rest" not in detail:
            cost += INJURY_SHARE["doubtful"] * p
        elif "day" in st and "rest" not in detail:
            cost += INJURY_SHARE["day"] * p
        out.append({**i, "ppg": round(p, 1) if p else None})
    return min(cost, INJURY_CAP_PTS), out, False


# ------------------------------------------------------------------ prediction
def market_line(home: str, away: str, day: dt.date | None) -> dict | None:
    """The consensus line ESPN publishes on its scoreboard for this game, as the
    home margin (home minus away) and the total. None when the game is not
    listed or carries no line yet. Live and free; shown beside our number."""
    if day is None:
        return None
    for g in espn.nba_scoreboard(day, ttl=1800) or []:
        if resolve(g["home"]) != home or resolve(g["away"]) != away:
            continue
        text, total = g.get("odds"), g.get("ou")
        line = None
        if text and " " in text:
            fav, num = text.rsplit(" ", 1)
            try:
                v = abs(float(num))
                line = v if fav == _team_card(home, None)["abbr"] else -v
            except ValueError:
                line = None
        if line is None and total is None:
            return None
        return {"line": line, "total": total, "text": text, "provider": "ESPN consensus line"}
    return None


def _as_date(date) -> dt.date | None:
    if date is None or date == "":
        return None
    if isinstance(date, dt.datetime):
        return date.date()
    if isinstance(date, dt.date):
        return date
    return dt.date.fromisoformat(str(date)[:10])


def predict(home: str, away: str, neutral: bool = False, date=None) -> dict:
    """`date` is the fixture date (ISO string or date); rest days are measured
    to it. Without a date the schedule is unknown: rest is None and no
    back-to-back adjustment is applied."""
    m = model()
    if home not in m["elo"] or away not in m["elo"]:
        raise KeyError("unknown team")
    day = _as_date(date)

    def rest(team):
        if day is None or team not in m["last_played"]:
            return None
        return (day - m["last_played"][team]).days

    rest_h, rest_a = rest(home), rest(away)
    b2b_h = rest_h is not None and rest_h <= 1
    b2b_a = rest_a is not None and rest_a <= 1
    cost_h, inj_h, unav_h = _injury_impact(home)
    cost_a, inj_a, unav_a = _injury_impact(away)
    gap = m["elo"][home] - m["elo"][away]
    rating_pts = m["slope"] * gap
    hca = 0.0 if neutral else m["hca_pts"]
    rest_adj = (-B2B_PTS if b2b_h else 0.0) + (B2B_PTS if b2b_a else 0.0)
    mu = rating_pts + hca + rest_adj - cost_h + cost_a
    sd = m["sd_m"]
    p_home = 1 - _ncdf(-mu / sd)
    # totals: each side's recent scoring vs the other's recent defending
    lg = m["lg_total"] / 2
    exp_h = (m["off"].get(home, lg) + m["def"].get(away, lg)) / 2 - cost_h * INJURY_TOTAL_FACTOR
    exp_a = (m["off"].get(away, lg) + m["def"].get(home, lg)) / 2 - cost_a * INJURY_TOTAL_FACTOR
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

    def bucket(lo, hi):
        return round(_ncdf((hi - mu) / sd) - _ncdf((lo - mu) / sd), 4)

    margins = {"home_by_11_plus": round(1 - _ncdf((10.5 - mu) / sd), 4),
               "home_by_6_10": bucket(5.5, 10.5), "home_by_1_5": bucket(0.0, 5.5),
               "away_by_1_5": bucket(-5.5, 0.0), "away_by_6_10": bucket(-10.5, -5.5),
               "away_by_11_plus": round(_ncdf((-10.5 - mu) / sd), 4)}
    mu_h = m["half_ratio"] * mu
    half = {"home": round(1 - _ncdf(-mu_h / m["sd_half"]), 4),
            "away": round(_ncdf(-mu_h / m["sd_half"]), 4),
            "proj_home": round(proj_h * m["half_share"], 1), "proj_away": round(proj_a * m["half_share"], 1)}
    tight = bucket(-5.5, 5.5)
    fav = home if mu >= 0 else away
    if abs(mu) >= 9:
        note = f"{fav} by double digits is the single likeliest shape ({max(margins['home_by_11_plus'], margins['away_by_11_plus']):.0%})."
    elif tight >= 0.38:
        note = f"Tough to call: {tight:.0%} of the time this is decided by five points or fewer."
    else:
        note = f"A {fav} lean by about {abs(mu):.0f}; {tight:.0%} of the time it stays within five."
    caveats = []
    if b2b_h:
        caveats.append(f"{home} play the second night of a back-to-back: priced at {B2B_PTS} points (an assumption, not fitted).")
    if b2b_a:
        caveats.append(f"{away} play the second night of a back-to-back: priced at {B2B_PTS} points (an assumption, not fitted).")
    if day is None:
        caveats.append("No fixture date given, so rest days are unknown and no back-to-back adjustment is applied.")
    if inj_h or inj_a:
        caveats.append(f"Injury pricing is an assumption, not a fit: a player listed Out costs {INJURY_SHARE['out']:.0%} of his "
                       f"scoring average, Doubtful or Questionable {INJURY_SHARE['doubtful']:.0%}, Day-To-Day "
                       f"{INJURY_SHARE['day']:.0%}, capped at {INJURY_CAP_PTS:.0f} points a side; "
                       f"{INJURY_TOTAL_FACTOR:.0%} of that also comes off the side's projected total.")
    for team, unav in ((home, unav_h), (away, unav_a)):
        if unav:
            caveats.append(f"ESPN's injury report for {team} could not be fetched; no absences are priced.")
    for team in (home, away):
        n = m["games_this_season"].get(team, 0)
        if n < 5:
            caveats.append(f"{team} have played {n} game{'s' if n != 1 else ''} this season: the rating is last "
                           f"season's, regressed {SEASON_REGRESS:.0%} of the way to the mean.")
    if day and m["data_to"] and (day - dt.date.fromisoformat(m["data_to"])).days > 3 and day.month not in (7, 8, 9):
        caveats.append(f"Stored results run to {m['data_to']}; newer games are being fetched and the ratings "
                       "and rest days will update within a few minutes.")
    mk = market_line(home, away, day)
    if mk:
        if mk["line"] is not None and abs(mk["line"] - mu) >= 6:
            caveats.append(f"The books have this at {mk['text']}; a gap that wide from our number usually means a "
                           "roster change the ratings have not caught up with yet.")
        if mk["total"] is not None and abs(mk["total"] - tot) >= 8:
            caveats.append(f"The books total this game at {mk['total']}; our scoring rates come from the last "
                           f"{int(RECENT_HALF_LIFE)} or so regular-season games and lag a changed roster or pace.")
    return {
        "sport": "basketball", "market": mk,
        "home": {"id": home, "name": home, "elo": round(m["elo"][home]), "rest_days": rest_h,
                 "injuries": inj_h, "injuries_unavailable": unav_h},
        "away": {"id": away, "name": away, "elo": round(m["elo"][away]), "rest_days": rest_a,
                 "injuries": inj_a, "injuries_unavailable": unav_a},
        "neutral_venue": neutral, "date": day.isoformat() if day else None,
        "win_prob": {"home": round(p_home, 4), "away": round(1 - p_home, 4),
                     "fair_odds": {"home": round(1 / p_home, 2), "away": round(1 / (1 - p_home), 2)}},
        "projected": {"home": round(proj_h, 1), "away": round(proj_a, 1), "total": round(tot, 1),
                      "margin": round(mu, 1), "spread": f"{home if mu >= 0 else away} -{abs(mu):.1f}"},
        "spreads": spreads, "totals": totals, "margins": margins, "half_time": half,
        "margin_note": note,
        "inputs": {"elo_gap": round(gap, 1), "points_per_elo": round(m["slope"], 4),
                   "rating_pts": round(rating_pts, 1), "home_court_pts": round(hca, 2),
                   "rest_adjust": round(rest_adj, 1), "injury_adjust": round(-cost_h + cost_a, 1),
                   "sd_margin": round(sd, 1), "sd_total": round(sd_t, 1),
                   "half_share": round(m["half_share"], 4), "b2b_pts": B2B_PTS,
                   "seasons": len(m["seasons"]), "games": m["n_games"]},
        "elo_history": {"home": m["hist"].get(home, [])[-40:], "away": m["hist"].get(away, [])[-40:]},
        "caveats": caveats,
    }


def upcoming(days: int = 7) -> list[dict]:
    """Scheduled games from ESPN over the next days, with our names."""
    m = model()
    out = []
    today = dt.date.today()
    for k in range(days + 1):
        day = today + dt.timedelta(days=k)
        for g in espn.nba_scoreboard(day, ttl=1800) or []:
            if g["final"] or g["stype"] not in (2, 3):
                continue
            h, a = resolve(g["home"]), resolve(g["away"])
            if not h or not a:
                continue
            out.append({"home_id": h, "away_id": a, "home": h, "away": a,
                        "home_team": _team_card(h, m["elo"].get(h)), "away_team": _team_card(a, m["elo"].get(a)),
                        "kickoff": g["date"], "date": g["date"][:10],
                        "live": g["live"], "neutral": bool(g["neutral"]),
                        "book_line": g["odds"], "book_total": g["ou"],
                        "league": "NBA" if g["stype"] == 2 else "NBA Playoffs"})
    return out


def h2h(home: str, away: str, n: int = 8) -> list[dict]:
    games = [g for g in model()["games"] if {g["home"], g["away"]} == {home, away}]
    return [{"date": g["date"].isoformat(), "home": g["home"], "away": g["away"],
             "score": f"{g['hs']}-{g['as']}"} for g in games[-n:]][::-1]


# ------------------------------------------------------------------ odds grading
def _odds_get(url: str, params: dict, timeout: int, errors: list):
    """One Odds API call. (json or None, status, remaining credits header).
    A key or quota problem is reported through odds_error in `errors`."""
    try:
        r = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as e:
        errors.append(str(e))
        return None, None, None
    remaining = r.headers.get("x-requests-remaining")
    if r.status_code != 200:
        errors.append(f"odds api {r.status_code}: {odds_error(r.status_code)}")
        return None, r.status_code, remaining
    try:
        return r.json(), 200, remaining
    except ValueError as e:
        errors.append(f"odds api body was not JSON: {e}")
        return None, 200, remaining


def grade_odds(key: str, market_weight: float = FITTED_MARKET_WEIGHT) -> dict:
    """Every priced NBA market (moneyline, spreads, totals) against the model:
    bookmaker margin removed per book, median across books, blended with the
    model, edge and quarter-Kelly stake. One odds call (three markets)."""
    frm, to = _window()
    errors: list = []
    events, status, remaining = _odds_get(
        f"{ODDS_BASE}/sports/basketball_nba/odds", timeout=20,
        params={"apiKey": key, "regions": "us,us2", "markets": "h2h,spreads,totals",
                "oddsFormat": "decimal", "commenceTimeFrom": frm, "commenceTimeTo": to},
        errors=errors)
    if events is None:
        out = {"bets": [], "fixtures": 0, "n_books": 0, "errors": errors, "remaining_credits": remaining}
        if status in (401, 403, 429):
            out["error"] = odds_error(status)
        return out
    rows, skipped = [], []
    for ev in events:
        h, a = resolve(ev.get("home_team") or ""), resolve(ev.get("away_team") or "")
        if not h or not a or h == a:
            skipped.append(f"{ev.get('home_team')} vs {ev.get('away_team')}")
            continue
        p = predict(h, a, neutral=False, date=(ev.get("commence_time") or "")[:10] or None)
        mu, sd = p["projected"]["margin"], p["inputs"]["sd_margin"]
        tot, sd_t = p["projected"]["total"], p["inputs"]["sd_total"]
        home_name, away_name = ev["home_team"], ev["away_team"]

        def model_prob(mkey, _gk, name, point):
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

        def label(mkey, _gk, name, point):
            if mkey == "h2h":
                return f"{name} win"
            if mkey == "spreads":
                return f"{name} {float(point):+g}"
            return f"{name} {point} points"

        for r in grade_groups(collect_groups(ev, ("h2h", "spreads", "totals"), home_name),
                              model_prob, label, market_weight):
            r.update({"home_id": h, "away_id": a, "match": f"{home_name} vs {away_name}",
                      "commence": ev.get("commence_time"), "sport": "basketball_nba"})
            rows.append(r)
    rows.sort(key=lambda x: -x["edge_pct"])
    return {"bets": [x for x in rows if x["value"]][:15], "all_evaluated": len(rows), "fixtures": len(events),
            "n_books": count_books(events), "skipped": skipped[:8], "errors": errors,
            "remaining_credits": remaining,
            "method": f"probabilities = {int(market_weight * 100)}% de-vigged market consensus + "
                      f"{int((1 - market_weight) * 100)}% model (Elo margin and totals distributions); "
                      f"edges use the best price across books; {MARKET_NOTE}"}


# ------------------------------------------------------------------ player props
STATS = ("points", "rebounds", "assists", "threes")
PROP_MARKETS = {"player_points": "points", "player_rebounds": "rebounds",
                "player_assists": "assists", "player_threes": "threes"}


def _ew_stats(logs: list[dict]) -> dict:
    """Exponentially weighted mean and sd (half-life PROP_HALF_LIFE games) of
    each stat and of minutes, most recent game weighted 1."""
    n = len(logs)
    w = 0.5 ** ((n - 1 - np.arange(n)) / PROP_HALF_LIFE)
    out = {}
    for k in STATS + ("minutes",):
        v = np.array([g[k] for g in logs], dtype=float)
        mean = float((w * v).sum() / w.sum())
        var = float((w * (v - mean) ** 2).sum() / w.sum())
        out[k] = (mean, math.sqrt(var))
    return out


def _rotation(team: str) -> tuple[list[dict], str | None]:
    """The ROTATION current-roster players with the most minutes per game (this
    season if it has stats, else last). (players, problem) where problem names
    what could not be fetched."""
    tid = _team_id(team)
    if not tid:
        return [], "team not known to ESPN"
    roster = espn.nba_roster(tid)
    if roster is None:
        return [], "roster could not be fetched"
    mpg = _leaders(team, "minutesPerGame")
    if mpg is None:
        return [], "minutes leaders could not be fetched"
    if not mpg:
        return [], "no minutes on record for this franchise in the last two seasons"
    players = [p for p in roster if p["id"] in mpg]
    players.sort(key=lambda p: -mpg[p["id"]])
    return players[:ROTATION], None


def _player_logs(pid: str, season: int) -> tuple[list[dict], list[dict]] | None:
    """(this season's logs, last season's logs), games actually played only.
    Last season is fetched while this season is short of 40 games. None when
    ESPN could not be reached for either."""
    cur = espn.nba_gamelog(pid, season)
    if cur is None:
        return None
    prev = []
    if len(cur) < 40:
        prev = espn.nba_gamelog(pid, season - 1)
        if prev is None:
            if not cur:
                return None
            prev = []
    played = lambda logs: [g for g in logs if g["minutes"] > 0]
    return played(cur), played(prev)


def _blend_stats(cur: list[dict], prev: list[dict]) -> dict:
    """This season's weighted averages shrunk toward last season's, with last
    season worth PRIOR_GAMES games: after 3 games this season carries 20% of the
    mean, after 12 games half, after 36 games three quarters. Spreads come from
    the pooled log so a short season cannot look falsely steady."""
    n = len(cur)
    sc = _ew_stats(cur) if cur else None
    sp = _ew_stats(prev) if prev else None
    pooled = _ew_stats(prev + cur)
    w = n / (n + PRIOR_GAMES)
    out = {}
    for k in STATS + ("minutes",):
        if sc and sp:
            mean = w * sc[k][0] + (1 - w) * sp[k][0]
        else:
            mean = (sc or sp)[k][0]
        out[k] = (mean, pooled[k][1])
    return out


def player_props(home: str, away: str, date=None) -> dict:
    """Per-player projections for the two rotations: an exponentially weighted
    mean and spread of points, rebounds, assists and threes over the last two
    seasons of game logs, scaled to this matchup by the team model, with the
    half-point line nearest the mean and the normal over/under at it."""
    m = model()
    p = predict(home, away, date=date)
    season = m["season_year"]
    report = espn.nba_injuries()
    caveats = [
        f"This season's games lead: a player's averages are the exponentially weighted mean of this season's logs "
        f"(half-life {PROP_HALF_LIFE:.0f} games) shrunk toward last season's, with last season worth {PRIOR_GAMES:.0f} games. "
        "Three games in, this season already carries a fifth of the number; by game twelve, half. Minutes follow the same rule.",
        "Points, threes and assists are scaled by the model's projected points for the player's team in this "
        "matchup relative to the team's own recent scoring rate (this is where the opponent's defence enters); "
        f"rebounds by the projected total relative to the league average as a pace proxy. Either factor is held within "
        f"{int(FACTOR_CAP * 100)} percent, and the spread scales with the mean.",
        f"Rotation = the {ROTATION} current-roster players with the most minutes per game (this season, else last). "
        "New signings and rookies without an NBA record are not listed. Starters resting late in blowouts are not modelled.",
        "A normal curve is used for every stat; it is coarse for low counts such as threes.",
        "Players listed Out are excluded. Questionable, Doubtful and Day-To-Day players are shown with their status "
        "and no change to their minutes.",
        "Each spread is floored at the median spread for that stat across the two rotations here, "
        "so a short or unusually steady log cannot produce a false certainty.",
        f"A stat is left out when the player's weighted average is below {MIN_PROP_MEAN}: books do not list "
        "those lines and a normal curve says nothing useful there.",
    ]
    if report is None:
        caveats.append("ESPN's injury report could not be fetched: nobody has been excluded for injury.")

    def side(team: str, proj_pts: float) -> dict:
        players, problem = _rotation(team)
        statuses = {}
        for i in (report or {}).get(team) or []:
            statuses[i["player"]] = i.get("status") or ""
        kept = [pl for pl in players if (statuses.get(pl["name"]) or "").lower() != "out"]
        with ThreadPoolExecutor(max_workers=8) as ex:
            logs = list(ex.map(lambda pl: _player_logs(pl["id"], season), kept))
        off = m["off"].get(team) or m["lg_total"] / 2
        clamp = lambda f: max(1 - FACTOR_CAP, min(1 + FACTOR_CAP, f))
        f_team = clamp(proj_pts / off)
        f_pace = clamp(p["projected"]["total"] / m["lg_total"])
        rows = []
        for pl, lg in zip(kept, logs):
            if lg is None:
                continue                     # ESPN could not be reached for this player
            cur, prev = lg
            if len(cur) + len(prev) < 5:
                continue                     # too few games to measure anything
            st = _blend_stats(cur, prev)
            rows.append({"name": pl["name"], "pos": pl["pos"], "headshot": pl["img"], "id": pl["id"],
                         "games": len(cur) + len(prev), "games_this_season": len(cur),
                         "minutes": round(st["minutes"][0], 1),
                         "status": statuses.get(pl["name"]),
                         "_raw": {k: (st[k][0] * (f_pace if k == "rebounds" else f_team),
                                      st[k][1] * (f_pace if k == "rebounds" else f_team)) for k in STATS}})
        return {"name": team, "players": rows, "problem": problem,
                "factors": {"team_points": round(f_team, 3), "pace": round(f_pace, 3)}}

    sides = {"home": side(home, p["projected"]["home"]), "away": side(away, p["projected"]["away"])}
    floors = {}
    for k in STATS:
        sds = [pl["_raw"][k][1] for s in sides.values() for pl in s["players"]]
        floors[k] = float(np.median(sds)) if sds else 0.0
    for s in sides.values():
        for pl in s["players"]:
            raw = pl.pop("_raw")
            pl.pop("id")
            pl["stats"] = {}
            for k in STATS:
                mean, sd = raw[k]
                if mean < MIN_PROP_MEAN:
                    continue
                sd = max(sd, floors[k], 1e-6)
                line = math.floor(mean) + 0.5
                over = 1 - _ncdf((line - mean) / sd)
                pl["stats"][k] = {"mean": round(mean, 2), "sd": round(sd, 2), "line": line,
                                  "over": round(over, 4), "under": round(1 - over, 4)}
    return {"home": sides["home"], "away": sides["away"], "date": p["date"],
            "sd_floor": {k: round(v, 2) for k, v in floors.items()},
            "note": ("Each line is the half-point nearest the player's matchup-adjusted mean; over and under are "
                     "the normal tail probabilities at that line. These are projections from box scores, not "
                     "bookmaker lines."),
            "caveats": caveats,
            "sources": ["ESPN game logs (per athlete, this season and last)", "ESPN roster and minutes leaders",
                        "ESPN injury report", "data/nba/games.csv for team scoring rates and the league total"]}


def _props_index(props: dict) -> dict:
    from .data_store import norm_key
    return {norm_key(pl["name"]): pl for s in (props["home"], props["away"]) for pl in s["players"]}


def grade_prop_event(ev: dict, props: dict, market_weight: float = FITTED_MARKET_WEIGHT) -> list[dict]:
    """Price every player-prop line in one Odds API event payload with the
    normal model behind player_props (pure: no network)."""
    from .data_store import norm_key
    idx = _props_index(props)

    def model_prob(mkey, gk, name, point):
        stat = PROP_MARKETS.get(mkey)
        pl = idx.get(norm_key(gk[0])) if isinstance(gk, tuple) else None
        if not stat or not pl or not _is_half_line(point):
            return None
        s = pl["stats"].get(stat)
        if not s:
            return None
        over = 1 - _ncdf((float(point) - s["mean"]) / s["sd"])
        return over if name == "Over" else (1 - over) if name == "Under" else None

    def label(mkey, gk, name, point):
        return f"{gk[0]} {name.lower()} {float(point):g} {PROP_MARKETS[mkey]}"

    rows = grade_groups(collect_groups(ev, tuple(PROP_MARKETS), ev.get("home_team") or ""),
                        model_prob, label, market_weight)
    for r in rows:
        r.update({"home_id": props["home"]["name"], "away_id": props["away"]["name"],
                  "match": f"{ev.get('home_team')} vs {ev.get('away_team')}",
                  "commence": ev.get("commence_time"), "sport": "basketball_nba"})
    return rows


def grade_props(home: str, away: str, key: str, market_weight: float = FITTED_MARKET_WEIGHT) -> dict:
    """Player props for one game against the bookmakers: a free events call to
    find the game, then ONE per-event odds call for player_points,
    player_rebounds, player_assists and player_threes (regions us: 4 credits)."""
    errors: list = []
    events, status, remaining = _odds_get(f"{ODDS_BASE}/sports/basketball_nba/events", timeout=15,
                                          params={"apiKey": key}, errors=errors)
    out = {"bets": [], "all_evaluated": 0, "fixtures": 0, "n_books": 0, "errors": errors,
           "remaining_credits": remaining,
           "method": (f"one per-event odds call (player points, rebounds, assists, threes; 4 credits); "
                      f"probabilities = {int(market_weight * 100)}% de-vigged two-way market + "
                      f"{int((1 - market_weight) * 100)}% normal model from the player's weighted game logs; "
                      f"{MARKET_NOTE}")}
    if events is None:
        if status in (401, 403, 429):
            out["error"] = odds_error(status)
        return out
    hit, hh, ha = None, home, away
    for ev in sorted(events, key=lambda e: e.get("commence_time") or ""):
        h, a = resolve(ev.get("home_team") or ""), resolve(ev.get("away_team") or "")
        if h and a and {h, a} == {home, away}:
            hit, hh, ha = ev, h, a
            break
    if not hit:
        out["method"] += "; the game is not listed by any book yet (no paid credits were spent)"
        return out
    ev, status, remaining2 = _odds_get(
        f"{ODDS_BASE}/sports/basketball_nba/events/{hit['id']}/odds", timeout=20,
        params={"apiKey": key, "regions": "us", "markets": ",".join(PROP_MARKETS), "oddsFormat": "decimal"},
        errors=errors)
    out["remaining_credits"] = remaining2 or remaining
    if ev is None:
        if status in (401, 403, 429):
            out["error"] = odds_error(status)
        return out
    props = player_props(hh, ha, date=(hit.get("commence_time") or "")[:10] or None)
    rows = grade_prop_event(ev, props, market_weight)
    out.update({"bets": rows, "all_evaluated": len(rows), "fixtures": 1, "n_books": count_books([ev]),
                "commence": hit.get("commence_time"), "match": f"{hit['home_team']} vs {hit['away_team']}",
                "caveats": props["caveats"]})
    return out
