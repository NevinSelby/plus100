"""FastAPI app: team search, head-to-head stats, predictions, logos, Reddit buzz."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .data_store import get_store, norm_key, slug
from .fpl import club_squad as fpl_squad
from .model import expected_goals, likely_scorers, likely_scorers_club, predict

ROOT = Path(__file__).resolve().parent.parent
LOGO_CACHE_FILE = ROOT / "data" / "logo_cache.json"
PLAYER_TEAM_CACHE_FILE = ROOT / "data" / "player_team_cache.json"
UA = {"User-Agent": "Mozilla/5.0 (FootballAnalytics; personal research tool)"}

app = FastAPI(title="Plus100 Football Predictor")

# Read-only public API: allow browser clients (Expo web debugging, the PWA on
# another origin) to call it. The native app is unaffected by CORS.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# The model store takes a couple of minutes to build on a cold machine (it
# downloads the full match history first). Build it in the background so the
# web server binds its port immediately — cloud hosts kill services that do
# not answer within a few minutes of starting.
store = None
warmup = {"ready": False, "error": None, "since": time.time()}


def _warm_up() -> None:
    global store
    try:
        store = get_store()
        warmup["ready"] = True
        from .refresher import start_background
        start_background()
    except Exception as e:  # noqa: BLE001
        warmup["error"] = str(e)[:300]


from .refresher import REFRESH_HOURS  # noqa: E402
from .refresher import state as refresher_state  # noqa: E402


@app.on_event("startup")
def _start_warmup():
    threading.Thread(target=_warm_up, daemon=True, name="plus100-warmup").start()


@app.get("/healthz")
def healthz():
    """Liveness probe: answers immediately, even while the model is building."""
    return {"ok": True, "model_ready": warmup["ready"],
            "warming_for_s": round(time.time() - warmup["since"]),
            "error": warmup["error"]}


def _require_store():
    if store is None:
        raise HTTPException(503, "Model is still starting up. First boot downloads the "
                                 "match history and takes a couple of minutes — try again shortly.")

_logo_lock = threading.Lock()
_logo_cache: dict[str, dict | str | None] = (
    json.loads(LOGO_CACHE_FILE.read_text()) if LOGO_CACHE_FILE.exists() else {}
)
_player_team_cache: dict[str, str | None] = (
    json.loads(PLAYER_TEAM_CACHE_FILE.read_text()) if PLAYER_TEAM_CACHE_FILE.exists() else {}
)
_news_cache: dict[str, tuple[float, list]] = {}


def _team_or_404(tid: str) -> dict:
    _require_store()
    t = store.registry.get(tid)
    if not t:
        raise HTTPException(404, f"unknown team id: {tid}")
    return t


@app.get("/api/teams")
def team_search(q: str = Query("", max_length=60), limit: int = 12):
    _require_store()
    key = norm_key(q)
    if len(key) < 2:
        return []
    scored = []
    for tid, r in store.registry.items():
        nk = norm_key(r["name"])
        if key not in nk:
            continue
        score = (0 if nk.startswith(key) else 1,
                 0 if r["active"] else 1,
                 -r["n"])
        scored.append((score, {
            "id": tid, "name": r["name"], "league": r["league_name"],
            "country": r["country"], "scope": r["scope"],
            "elo": r["elo_global"], "active": r["active"],
        }))
    scored.sort(key=lambda t: t[0])
    return [x for _, x in scored[:limit]]


def _form(tid: str, n: int = 8) -> list[dict]:
    tm = store.team_matches(tid).sort_values("date").tail(n)
    out = []
    for _, r in tm.iterrows():
        is_home = r.home_id == tid
        gf, ga = (r.hg, r.ag) if is_home else (r.ag, r.hg)
        out.append({
            "date": str(r.date.date()),
            "opponent": r.away if is_home else r.home,
            "venue": "H" if is_home else "A",
            "score": f"{int(gf)}-{int(ga)}",
            "result": "W" if gf > ga else ("L" if gf < ga else "D"),
            "competition": r.tournament if r.scope == "intl" else
                           store.registry.get(tid, {}).get("league_name", ""),
        })
    return out[::-1]


@app.get("/api/h2h")
def h2h(home: str, away: str):
    th, ta = _team_or_404(home), _team_or_404(away)
    hh = store.h2h(home, away).sort_values("date")

    wins_h = int((((hh.home_id == home) & (hh.hg > hh.ag)) |
                  ((hh.away_id == home) & (hh.ag > hh.hg))).sum())
    wins_a = int((((hh.home_id == away) & (hh.hg > hh.ag)) |
                  ((hh.away_id == away) & (hh.ag > hh.hg))).sum())
    draws = int((hh.hg == hh.ag).sum())

    goals_h = int((hh.hg.where(hh.home_id == home, hh.ag)).sum())
    goals_a = int((hh.hg.where(hh.home_id == away, hh.ag)).sum())

    meetings = [{
        "date": str(r.date.date()),
        "home": r.home, "away": r.away,
        "score": f"{int(r.hg)}-{int(r.ag)}",
        "competition": r.tournament if r.scope == "intl" else
                       f"{r.league} {r.season}",
    } for _, r in hh.tail(12).iloc[::-1].iterrows()]

    def elo_series(tid: str, points: int = 60) -> list:
        h = store.elo_hist.get(tid, [])
        step = max(1, len(h) // points)
        return [[str(pd.Timestamp(d).date()), round(e)] for d, e in h[::step]][-points:]

    return {
        "summary": {
            "played": len(hh), "wins_home": wins_h, "draws": draws, "wins_away": wins_a,
            "goals_home": goals_h, "goals_away": goals_a,
            "avg_goals_per_match": round((goals_h + goals_a) / len(hh), 2) if len(hh) else None,
            "first_meeting": str(hh.date.min().date()) if len(hh) else None,
        },
        "meetings": meetings,
        "form": {"home": _form(home), "away": _form(away)},
        "elo_history": {"home": elo_series(home), "away": elo_series(away)},
        "teams": {
            "home": {"name": th["name"], "league": th["league_name"], "elo": th["elo_global"],
                     "country": th["country"], "matches_in_data": th["n"], "last_match": th["last"]},
            "away": {"name": ta["name"], "league": ta["league_name"], "elo": ta["elo_global"],
                     "country": ta["country"], "matches_in_data": ta["n"], "last_match": ta["last"]},
        },
    }


_ABSENCE_PATTERNS = [re.compile(rx) for rx in (
    r"\binjur", r"\bruled out\b", r"\bout for\b", r"\bsidelined\b",
    r"\bdoubt(?:ful)?\b", r"\bsuspend", r"\bbanned\b", r"\bsurgery\b",
    r"\bhamstring\b", r"\bacl\b", r"\bfracture\b", r"\bmiss(?:es|ing)?\b")]
_DEPARTURE_PATTERNS = [re.compile(rx) for rx in (
    r"\bsigns? for\b", r"\bsold to\b", r"\bcompletes? (?:a )?(?:move|transfer)\b",
    r"\btransfer agreed\b", r"\bdeparts\b", r"\b(?:leaves|left) the club\b",
    r"\bloan move to\b")]
# headlines that mean the OPPOSITE of an absence: "Wood returns from injury",
# "no doubt over Saka", "Isak back in training after injury scare" — any of
# these vetoes the whole title so good news never flags a player out
_RECOVERY_PATTERNS = [re.compile(rx) for rx in (
    r"\breturn(?:s|ed|ing)?\b", r"\bback in\b", r"\bback from\b",
    r"\bfit\b", r"\bfitness boost\b", r"\bboost\b", r"\bno doubts?\b",
    r"\bavailable\b", r"\brecover(?:s|ed|ing|y)?\b", r"\bin contention\b",
    r"\bshakes? off\b", r"\bpasses? fit\b", r"\bwins? .{0,20}race\b",
    r"\bnew (?:deal|contract)\b", r"\bcontract extension\b", r"\bextends?\b")]


def _fold(s: str) -> str:
    """Lowercase, accent-stripped, spaces preserved — safe for word-boundary matching."""
    return re.sub(r"[^a-z0-9 ]", " ",
                  unicodedata.normalize("NFKD", str(s))
                  .encode("ascii", "ignore").decode().lower())


def _news_scan(tid: str, names: list[str], patterns: list,
               vetoes: list | None = None) -> list[str]:
    try:
        items = news(tid)["items"]
    except Exception:  # noqa: BLE001
        return []
    hits = []
    for n in names:
        parts = [w for w in str(n).split() if len(w) > 3]
        if not parts:
            continue
        last = _fold(parts[-1]).strip()
        if len(last) < 4:
            continue
        name_rx = re.compile(r"\b" + re.escape(last) + r"\b")
        for it in items:
            title = _fold(it.get("title") or "")
            if not name_rx.search(title):
                continue
            if vetoes and any(v.search(title) for v in vetoes):
                continue
            if any(p.search(title) for p in patterns):
                hits.append(n)
                break
    return hits


def _news_absences(tid: str, names: list[str]) -> list[str]:
    """Players from `names` the news says are injured/suspended OR have left the
    club — either way they should not appear in a line-up or count toward goals.
    Good-news headlines veto a hit; a transfer TO this club is an arrival, not a
    departure, so the club's own name after "to/joins" vetoes the departure side."""
    outs = _news_scan(tid, names, _ABSENCE_PATTERNS, _RECOVERY_PATTERNS)
    dep_veto = list(_RECOVERY_PATTERNS)
    team = _fold(store.registry.get(tid, {}).get("name", "")).strip()
    if len(team) >= 4:
        dep_veto.append(re.compile(
            r"\b(?:to|joins?|joining|for)\s+" + re.escape(team) + r"\b"))
    for n in _news_scan(tid, names, _DEPARTURE_PATTERNS, dep_veto):
        if n not in outs:
            outs.append(n)
    return outs


def _match_tracked(flag_name: str, tracked: list[str]) -> str | None:
    """Map an FPL web name like "M.Salah" onto a tracked full name."""
    parts = [w for w in re.split(r"[ .]", str(flag_name)) if len(w) > 2]
    if not parts:
        return None
    last = norm_key(parts[-1])
    if len(last) < 4:
        return None
    for t in tracked:
        if last in norm_key(t):
            return t
    return None


def _auto_absences(tid: str) -> list[str]:
    """Likely absentees among the players our model tracks, from two live
    sources: the official FPL availability flags (PL clubs, updated daily by the
    league) and team-news headlines (everyone else)."""
    reg = store.registry.get(tid, {})
    if reg.get("scope") == "club":
        names = [r["player"] for r in store.player_rates.get(tid, [])[:12]]
    else:
        sg = store.scorer_goals
        names = list(sg[sg.team == reg.get("name", "")].sort_values(
            "wgoals", ascending=False).scorer.head(10))
    # headlines are a noisy source, so cap them at the three most important
    # players; the official FPL availability flags below are authoritative
    # and are never capped
    outs = _news_absences(tid, names)[:3]
    if reg.get("scope") == "club":
        try:
            from .fpl import club_unavailable
            for f in club_unavailable(store, tid):
                hit = _match_tracked(f["name"], names)
                if hit and hit not in outs:
                    outs.append(hit)
        except Exception:  # noqa: BLE001
            pass
    return outs


def _effective_elo(tid: str, outs: list[str]) -> dict:
    """Today's usable strength: the learned rating, discounted for absent players
    via the fitted elo→goals curve (so the discount speaks the model's language)."""
    t = store.registry[tid]
    base = float(t["elo_global"])
    from .model import _absence_factor
    factor, applied = _absence_factor(store, tid, outs)
    fit = store.goal_fit_club if t["scope"] == "club" else store.goal_fit_intl
    b = (abs(fit["b"]) + abs(fit["e"])) / 2 or 1.0
    delta = 400.0 * math.log(max(factor, 0.6)) / b
    return {"elo": round(base), "elo_effective": round(base + delta),
            "elo_delta": round(delta),
            "outs_priced_in": [a["player"] for a in applied]}


@app.get("/api/teamstate")
def teamstate(team_id: str):
    """Live team condition for the pickers: dynamic rating + who is missing."""
    _team_or_404(team_id)
    outs = _auto_absences(team_id)
    eff = _effective_elo(team_id, outs)
    return {"id": team_id, **eff, "outs": outs,
            "ratings_updated": refresher_state.get("last_refresh"),
            "note": ("The rating re-learns from every result at each data refresh; "
                     "the effective number additionally discounts today's absentees.")}


@app.get("/api/predict")
def predict_endpoint(request: Request, home: str, away: str, neutral: bool = False,
                     out_home: str = "", out_away: str = "", context: str = "none"):
    _team_or_404(home)
    _team_or_404(away)
    if home == away:
        raise HTTPException(400, "pick two different teams")
    oh = [p.strip() for p in out_home.split("|") if p.strip()]
    oa = [p.strip() for p in out_away.split("|") if p.strip()]
    # No manual list given: scan team news for likely absentees so the numbers
    # reflect who can actually play, not just the two crests.
    auto_oh = [] if oh else _auto_absences(home)
    auto_oa = [] if oa else _auto_absences(away)
    p = predict(store, home, away, neutral, out_home=oh or auto_oh,
                out_away=oa or auto_oa, context=context)
    _verify_squads(p)
    applied = {a["player"] for side in ("home", "away")
               for a in (p.get("absences", {}).get(side) or [])}
    for team_name, outs in ((p["home"]["name"], auto_oh), (p["away"]["name"], auto_oa)):
        if not outs:
            continue
        reduced = [n for n in outs if n in applied]
        listed = [n for n in outs if n not in applied]
        if reduced:
            p.setdefault("caveats", []).append(
                f"Team news suggests {', '.join(reduced)} may be unavailable for {team_name}; "
                "the goal expectation was reduced for the minutes they usually provide.")
        if listed:
            p.setdefault("caveats", []).append(
                f"{', '.join(listed)} flagged as possibly unavailable for {team_name} "
                "(no per-player goal data for this team, so the numbers are unchanged).")
    try:
        p["home"] |= _effective_elo(home, (oh or auto_oh))
        p["away"] |= _effective_elo(away, (oa or auto_oa))
        p["elo_note"] = ("Ratings are re-learned from every new result at each data "
                         f"refresh (last: {refresher_state.get('last_refresh') or 'startup'}); "
                         "the effective numbers additionally discount players missing today.")
    except Exception:  # noqa: BLE001
        pass
    _log_prediction(request, p, context, neutral)
    return p


from pydantic import BaseModel


class ParlayReq(BaseModel):
    home: str
    away: str
    legs: list[str]
    neutral: bool = False
    price: float = 0.0
    context: str = "none"


def _outs_for_pair(home: str, away: str) -> tuple[list, list]:
    """Best-effort auto absences; never blocks the endpoint."""
    try:
        return _auto_absences(home), _auto_absences(away)
    except Exception:  # noqa: BLE001
        return [], []


@app.get("/api/parlay/suggest")
def parlay_suggest(home: str, away: str, neutral: bool = False, context: str = "none"):
    _require_store()
    from .model import suggest_parlays
    _team_or_404(home)
    _team_or_404(away)
    if home == away:
        raise HTTPException(400, "pick two different teams")
    oh, oa = _outs_for_pair(home, away)
    return suggest_parlays(store, home, away, neutral, context=context,
                           out_home=oh, out_away=oa)


@app.post("/api/parlay")
def parlay_endpoint(req: ParlayReq):
    _require_store()
    from .model import simulate_sgp
    _team_or_404(req.home)
    _team_or_404(req.away)
    if req.home == req.away:
        raise HTTPException(400, "pick two different teams")
    if not req.legs:
        raise HTTPException(400, "no legs given")
    oh, oa = _outs_for_pair(req.home, req.away)
    try:
        r = simulate_sgp(store, req.home, req.away, req.legs, req.neutral, req.context,
                         out_home=oh, out_away=oa)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if req.price and req.price > 1:
        p = r["joint_prob"]
        edge = p * req.price - 1
        b = req.price - 1
        r["book_price"] = req.price
        r["edge_pct"] = round(edge * 100, 2)
        r["quarter_kelly_pct"] = round(max(0.0, (p * b - (1 - p)) / b) * 25, 2)
    return r


@app.get("/api/bestbets")
def bestbets_endpoint(key: str = "", home: str = "", away: str = ""):
    _require_store()
    from .bestbets import best_bets
    k = _resolve_key(key)
    if not k:
        return {"error": "no_key", "detail": "No API key configured."}
    return best_bets(k, store, sel_home=home.strip(), sel_away=away.strip())


@app.get("/api/news")
def news(team_id: str):
    """Injury & team news headlines via Google News RSS. Context only."""
    t = _team_or_404(team_id)
    key = t["name"]
    now = time.time()
    if key in _news_cache and now - _news_cache[key][0] < 1800:
        return {"team": key, "items": _news_cache[key][1]}
    items, fetch_failed = [], False
    try:
        q = f'"{key}" football (injury OR "team news" OR lineup)'
        r = requests.get("https://news.google.com/rss/search",
                         params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                         headers=UA, timeout=8)
        root = ET.fromstring(r.content)
        for it in root.findall(".//item")[:8]:
            src = it.find("{https://news.google.com/rss}source")
            items.append({
                "title": it.findtext("title") or "",
                "link": it.findtext("link") or "",
                "date": (it.findtext("pubDate") or "")[:16],
                "source": src.text if src is not None else "",
            })
    except Exception:  # noqa: BLE001
        fetch_failed = True
    if not (fetch_failed and not items):    # a failed fetch shouldn't blank the
        _news_cache[key] = (now, items)     # team's news for the next 30 minutes
    return {"team": key, "items": items,
            "disclaimer": "Headlines are context only; they are not part of the statistical model."}


# football-data short names that TheSportsDB doesn't know under that spelling:
# search with the club's canonical name instead (keyed by our registry id)
TSDB_SEARCH_ALIASES = {
    "sociedad": "Real Sociedad", "betis": "Real Betis", "ath-madrid": "Atletico Madrid",
    "ath-bilbao": "Athletic Bilbao", "espanol": "Espanyol", "vallecano": "Rayo Vallecano",
    "celta": "Celta Vigo", "sp-gijon": "Sporting Gijon", "la-coruna": "Deportivo La Coruna",
    "nott-m-forest": "Nottingham Forest", "sheffield-weds": "Sheffield Wednesday",
    "qpr": "Queens Park Rangers", "west-brom": "West Bromwich Albion",
    "wolves": "Wolverhampton Wanderers", "man-united": "Manchester United",
    "man-city": "Manchester City", "spurs": "Tottenham Hotspur",
    "ein-frankfurt": "Eintracht Frankfurt", "m-gladbach": "Borussia Monchengladbach",
    "leverkusen": "Bayer Leverkusen", "dortmund": "Borussia Dortmund",
    "hertha": "Hertha Berlin", "milan": "AC Milan", "inter": "Inter Milan",
    "verona": "Hellas Verona", "paris-sg": "Paris Saint Germain",
    "st-etienne": "Saint-Etienne", "sp-lisbon": "Sporting CP",
    "sp-braga": "Sporting Braga", "guimaraes": "Vitoria Guimaraes",
    "for-sittard": "Fortuna Sittard", "psv-eindhoven": "PSV Eindhoven",
}

# clubs whose record their search never returns (shadowed by a same-name team in
# another sport): resolved by direct id lookup instead
TSDB_TEAM_IDS = {
    "nott-m-forest": "133720",     # the search only finds the netball club
}


def _tsdb_best_match(cands: list, t: dict):
    """The TSDB record that is genuinely this team, or None. Never settle for
    'first search hit': that's how 'Sociedad' once resolved to a village club
    instead of Real Sociedad. Exact name wins; otherwise our name's words must
    be contained in theirs, backed up by country/league agreement."""
    want = TSDB_SEARCH_ALIASES.get(t["id"], t["name"])
    nk = norm_key(want)
    toks = set(slug(want).split("-")) - {"fc", "cf", "ac", "sc", "de", "cd"}
    country = (t.get("country") or "").lower()
    lg = (t.get("league_name") or "").lower()

    def score(x):
        xname = x.get("strTeam") or ""
        s = 0
        if norm_key(xname) == nk:
            s += 100
        if toks and toks <= set(slug(xname).split("-")):
            s += 40
        if country and (x.get("strCountry") or "").lower() == country:
            s += 30
        if lg and lg in (x.get("strLeague") or "").lower():
            s += 20
        return s

    best = max(cands, key=score, default=None)
    return best if best is not None and score(best) >= 60 else None


def _tsdb_search_team(t: dict):
    """searchteams.php with the canonical name, filtered to a confident match.
    Their search misses some records unless the query is lowercase (e.g.
    'Nottingham Forest' finds nothing, 'nottingham forest' works), so retry."""
    known = TSDB_TEAM_IDS.get(t["id"])
    if known:
        r = requests.get("https://www.thesportsdb.com/api/v1/json/3/lookupteam.php",
                         params={"id": known}, headers=UA, timeout=8)
        rec = ((r.json() or {}).get("teams") or [None])[0]
        return rec if rec and rec.get("strSport") == "Soccer" else None
    q = TSDB_SEARCH_ALIASES.get(t["id"], t["name"])
    for query in dict.fromkeys((q, q.lower())):
        r = requests.get("https://www.thesportsdb.com/api/v1/json/3/searchteams.php",
                         params={"t": query}, headers=UA, timeout=8)
        soccer = [x for x in (r.json() or {}).get("teams") or []
                  if x.get("strSport") == "Soccer"]
        best = _tsdb_best_match(soccer, t)
        if best is not None:
            return best
    return None


def _tsdb_team(team_id: str) -> dict:
    """Resolve a team on TheSportsDB: badge URL + their canonical team name."""
    t = _team_or_404(team_id)
    with _logo_lock:
        cached = _logo_cache.get(team_id)
    if isinstance(cached, dict) and "colors" in cached:   # old entries lack fields: refetch
        return cached
    entry = {"badge": cached.get("badge") if isinstance(cached, dict) else
             cached if isinstance(cached, str) else None,
             "tsdb_name": None, "fanart": None, "stadium": None, "capacity": None,
             "colors": []}
    try:
        best = _tsdb_search_team(t)
        if best is not None:
            entry = {"badge": best.get("strBadge"), "tsdb_name": best.get("strTeam"),
                     "fanart": best.get("strFanart1") or best.get("strBanner"),
                     "stadium": best.get("strStadium"),
                     "capacity": best.get("intStadiumCapacity"),
                     "colors": [c for c in (best.get("strColour1"), best.get("strColour2"))
                                if c and c.startswith("#") and len(c) == 7]}
    except Exception:  # noqa: BLE001
        return entry            # transient failure: do not freeze an empty entry
    if not entry.get("tsdb_name"):
        return entry            # nothing found: retry next time rather than cache
    with _logo_lock:
        _logo_cache[team_id] = entry
        LOGO_CACHE_FILE.write_text(json.dumps(_logo_cache))
    return entry


@app.get("/api/logo")
def logo(team_id: str):
    e = _tsdb_team(team_id)
    return {"badge": e["badge"], "fanart": e.get("fanart"),
            "stadium": e.get("stadium"), "capacity": e.get("capacity"),
            "colors": e.get("colors") or []}


# ---------- probable lineups (squad data from TheSportsDB + our scorer model) ----------

LINEUP_CACHE_FILE = ROOT / "data" / "lineup_cache.json"
_lineup_lock = threading.Lock()
_lineup_cache: dict = (
    json.loads(LINEUP_CACHE_FILE.read_text()) if LINEUP_CACHE_FILE.exists() else {}
)
_SQUAD_TTL = 86400   # squads refresh daily
_STAFF_WORDS = ("manager", "coach", "assistant", "director", "physio", "analyst", "scout")


def _pos_bucket(pos: str) -> str | None:
    p = (pos or "").lower()
    if any(w in p for w in _STAFF_WORDS):
        return None
    if "goalkeeper" in p or p == "gk":
        return "GK"
    if "midfield" in p:
        return "MID"
    if "back" in p or "defen" in p or "sweeper" in p:
        return "DEF"
    if any(w in p for w in ("winger", "forward", "striker", "attack", "wing")):
        return "FWD"
    return None


def _pos_x_order(pos: str) -> int:
    p = (pos or "").lower()
    if "left" in p:
        return 0
    if "right" in p:
        return 2
    return 1


def _tsdb_squad(team_id: str) -> list[dict]:
    """Squad players with position + cutout photo, cached a few days on disk."""
    with _lineup_lock:
        hit = _lineup_cache.get(team_id)
    if hit and time.time() - hit["at"] < _SQUAD_TTL:
        return hit["players"]
    t = _team_or_404(team_id)
    players = []
    try:
        best = _tsdb_search_team(t)
        if best:
            r2 = requests.get("https://www.thesportsdb.com/api/v1/json/3/lookup_all_players.php",
                              params={"id": best["idTeam"]}, headers=UA, timeout=8)
            for p in (r2.json() or {}).get("player") or []:
                if (p.get("strStatus") or "").lower() in ("deceased", "retired"):
                    continue
                bucket = _pos_bucket(p.get("strPosition"))
                if bucket:
                    players.append({
                        "name": p.get("strPlayer"), "pos": p.get("strPosition"),
                        "bucket": bucket,
                        "img": p.get("strCutout") or p.get("strThumb") or None,
                    })
    except Exception:  # noqa: BLE001
        return players
    if not players:
        # rate-limited or unknown team: never cache an empty squad, or a momentary
        # throttle would freeze a keeper-less line-up in place for days
        return players
    with _lineup_lock:
        _lineup_cache[team_id] = {"at": time.time(), "players": players}
        try:
            LINEUP_CACHE_FILE.write_text(json.dumps(_lineup_cache))
        except OSError:
            pass
    return players


def _player_lookup(name: str, team_name: str, intl: bool) -> dict | None:
    """Position + photo for one player by name, verified against the team.

    Guards against namesakes: a non-exact match must also belong to the team
    (club: current club matches; international: nationality matches). An exact
    normalized name match is accepted as a last resort so short forms like
    "Messi" still resolve even though his club is not "Argentina"."""
    key = f"p2:{norm_key(team_name)}:{norm_key(name)}"
    with _lineup_lock:
        hit = _lineup_cache.get(key)
    if hit is not None:
        return hit or None
    qk = norm_key(name)
    tk = norm_key(team_name)
    gated, exact_only = None, None
    try:
        r = requests.get("https://www.thesportsdb.com/api/v1/json/3/searchplayers.php",
                         params={"p": name}, headers=UA, timeout=6)
        if r.status_code != 200:
            return None                       # rate limited: don't cache the miss
        for p in (r.json() or {}).get("player") or []:
            if p.get("strSport") != "Soccer":
                continue
            if (p.get("strStatus") or "").lower() in ("deceased", "retired"):
                continue
            bucket = _pos_bucket(p.get("strPosition"))
            if not bucket:
                continue
            pk = norm_key(p.get("strPlayer", ""))
            exact = pk == qk
            words = set(re.sub(r"[^a-z0-9 ]", "",
                               unicodedata.normalize("NFKD", p.get("strPlayer", ""))
                               .encode("ascii", "ignore").decode().lower()).split())
            word_hit = qk in words
            if not exact and not word_hit:
                continue
            team_ok = (norm_key(p.get("strNationality") or "") == tk) if intl \
                else (tk in norm_key(p.get("strTeam") or "") or
                      norm_key(p.get("strTeam") or "") in tk if p.get("strTeam") else False)
            info = {"name": p.get("strPlayer"), "pos": p.get("strPosition"),
                    "bucket": bucket,
                    "img": p.get("strCutout") or p.get("strThumb") or None}
            if team_ok and gated is None:
                gated = info
            # exact names may fall back only when nothing contradicts the club:
            # for club teams, a KNOWN different current club disqualifies the hit
            if exact and exact_only is None and (intl or team_ok or not p.get("strTeam")):
                exact_only = info
    except Exception:  # noqa: BLE001
        return None
    best = gated or exact_only
    with _lineup_lock:
        _lineup_cache[key] = best or False
        try:
            LINEUP_CACHE_FILE.write_text(json.dumps(_lineup_cache))
        except OSError:
            pass
    return best



# Real shapes managers actually pick, in rough order of how common they are.
# A line-up is only ever shown as one of these, so no 1-3-3 nonsense can appear.
FORMATIONS = [(4, 3, 3), (4, 4, 2), (4, 2, 3, 1), (3, 5, 2), (4, 5, 1),
              (3, 4, 3), (5, 3, 2), (5, 4, 1), (4, 1, 4, 1)]


def _pick_formation(n_def: int, n_mid: int, n_fwd: int) -> tuple[int, int, int] | None:
    """The most conventional shape the available players can actually fill."""
    for f in FORMATIONS:
        d, m, fw = f[0], sum(f[1:-1]), f[-1]
        if d <= n_def and m <= n_mid and fw <= n_fwd:
            return d, m, fw
    return None


def _team_lineup(tid: str, lam: float) -> dict:
    t = _team_or_404(tid)
    reg = store.registry[tid]
    if reg.get("scope") == "club":
        scorers = likely_scorers_club(store, tid, lam)
    else:
        scorers = likely_scorers(store, t["name"], lam)
    p_by_key = {norm_key(s["player"]): s["prob_to_score"] for s in scorers}

    # Premier League clubs: the FPL API gives the COMPLETE current squad with
    # positions, availability and minutes, so it beats the partial public feed.
    squad, source = [], "public squad data"
    if reg.get("scope") == "club":
        try:
            squad = [dict(p) for p in fpl_squad(store, tid)]
        except Exception:  # noqa: BLE001
            squad = []
        if squad:
            source = "the official Premier League squad list"
    if not squad:
        squad = [dict(p) for p in _tsdb_squad(tid)]
        # the public feed lags transfers: drop anyone whose CURRENT club, per a
        # per-player lookup (cached on disk), is verifiably somewhere else
        if reg.get("scope") == "club":
            verified = []
            for p in squad:
                cur = _player_current_team(p["name"])
                if cur and norm_key(cur) != norm_key(t["name"]) \
                        and norm_key(t["name"]) not in norm_key(cur):
                    continue
                verified.append(p)
            if len(verified) >= 4:      # keep the filter only when it leaves a core
                squad = verified
    have = {norm_key(p["name"]) for p in squad}

    def match_prob(name: str) -> float | None:
        k = norm_key(name)
        if k in p_by_key:
            return p_by_key[k]
        for sk, v in p_by_key.items():   # "Mac Allister" vs "Alexis Mac Allister"
            if len(sk) > 5 and (sk in k or k in sk):
                return v
        return None

    for p in squad:
        p["p_score"] = match_prob(p["name"])

    # top scorers missing from the squad list get looked up individually
    intl = reg.get("scope") != "club"
    extra_budget = 5
    for s in scorers:
        if extra_budget == 0:
            break
        k = norm_key(s["player"])
        if any(len(k) > 5 and (k in h or h in k) for h in have):
            continue
        info = _player_lookup(s["player"], t["name"], intl)
        extra_budget -= 1
        if info and norm_key(info["name"]) not in have:
            info["p_score"] = s["prob_to_score"]
            squad.append(info)
            have.add(norm_key(info["name"]))

    # drop players the news says are out — but never empty a position doing it,
    # or a single injury headline can leave a side with no keeper or no striker
    outs = _news_absences(tid, [p["name"] for p in squad])
    if reg.get("scope") == "club":
        try:
            from .fpl import club_unavailable
            squad_names = [p["name"] for p in squad]
            for fl in club_unavailable(store, tid):
                hit = _match_tracked(fl["name"], squad_names)
                if hit and hit not in outs:
                    outs.append(hit)
        except Exception:  # noqa: BLE001
            pass
    if outs:
        kept, dropped = [], []
        for p in squad:
            (dropped if p["name"] in outs else kept).append(p)
        for bucket in ("GK", "DEF", "MID", "FWD"):
            need = {"GK": 1, "DEF": 3, "MID": 2, "FWD": 1}[bucket]
            while sum(1 for p in kept if p["bucket"] == bucket) < need:
                back = next((p for p in dropped if p["bucket"] == bucket), None)
                if not back:
                    break
                dropped.remove(back)
                kept.append(back)
                outs = [n for n in outs if n != back["name"]]
        squad = kept

    # Rank each position by who is likeliest to start: scoring share first (that is
    # our model talking), then real playing time, then ownership as a tiebreak.
    pool: dict[str, list] = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for p in squad:
        pool[p["bucket"]].append(p)
    for b in pool:
        pool[b].sort(key=lambda p: (-(p.get("p_score") or 0), -(p.get("minutes") or 0),
                                    -(p.get("sel") or 0), p.get("img") is None, p["name"]))

    shape = _pick_formation(len(pool["DEF"]), len(pool["MID"]), len(pool["FWD"]))
    partial = shape is None or not pool["GK"]
    if shape is None:
        # not enough known players for a real shape: show whoever we do know,
        # capped per position, rather than inventing a formation
        counts = {"DEF": min(len(pool["DEF"]), 4), "MID": min(len(pool["MID"]), 3),
                  "FWD": min(len(pool["FWD"]), 3)}   # matches the 4-3-3 padding target
    else:
        counts = {"DEF": shape[0], "MID": shape[1], "FWD": shape[2]}

    rows = {"GK": pool["GK"][:1]}
    for b in ("DEF", "MID", "FWD"):
        rows[b] = pool[b][:counts[b]]
    for b in rows:
        rows[b].sort(key=lambda p: (_pos_x_order(p["pos"]), p["name"]))
    # pad to a full, honest eleven: unnamed spots become explicit "Unknown" chips
    target = {"GK": 1, "DEF": counts["DEF"] if shape else 4,
              "MID": counts["MID"] if shape else 3, "FWD": counts["FWD"] if shape else 3}
    n_known = sum(len(v) for v in rows.values())
    for b, want in target.items():
        while len(rows[b]) < want:
            rows[b].append({"name": "Unknown", "pos": "Not named in the public feed",
                            "bucket": b, "img": None, "p_score": None, "placeholder": True})

    players = []
    for ri, b in enumerate(("GK", "DEF", "MID", "FWD")):
        n = len(rows[b])
        for si, p in enumerate(rows[b]):
            players.append({**p, "row": ri, "slot": si, "n": n})
    return {
        "id": tid, "name": t["name"], "badge": _tsdb_team(tid)["badge"],
        "formation": ("-".join(str(len(rows[b])) for b in ("DEF", "MID", "FWD"))
                      if not partial else None),
        "players": players,
        "known": n_known,
        "complete": n_known == 11 and shape is not None,
        "gk_missing": not pool["GK"],
        "source": source,
        "outs": outs,
    }


@app.get("/api/lineup")
def lineup(home: str, away: str, neutral: bool = False):
    """Probable line-ups for a matchup: public squad data (TheSportsDB) ranked by
    our model's scoring shares. These are LIKELY players, not confirmed team sheets."""
    _require_store()
    _team_or_404(home)
    _team_or_404(away)
    eg = expected_goals(store, home, away, neutral=neutral)
    h = _team_lineup(home, eg["lambda_home"])
    a = _team_lineup(away, eg["lambda_away"])
    srcs = sorted({h["source"], a["source"]})
    return {
        "home": h, "away": a,
        "note": (f"Built from {' and '.join(srcs)}, ranked by playing time and each "
                 "player's share of his team's expected goals, then arranged in the "
                 "most likely formation those players can field. Real team sheets drop "
                 "about an hour before kickoff and can differ."),
    }


_PLAYER_TEAM_TTL = 5 * 86400   # transfers happen: re-verify a club every few days


def _player_current_team(player: str) -> str | None:
    """Player's current club per TheSportsDB, cached on disk WITH an expiry so a
    transfer is picked up within days (an eternal cache kept ghosts in line-ups)."""
    key = norm_key(player)
    hit = _player_team_cache.get(key)
    if isinstance(hit, str) or hit is None and key in _player_team_cache:
        hit = {"team": hit if isinstance(hit, str) else None, "ts": 0}   # legacy entry
    if isinstance(hit, dict) and time.time() - hit.get("ts", 0) < _PLAYER_TEAM_TTL:
        return hit.get("team")
    team = None
    try:
        r = requests.get("https://www.thesportsdb.com/api/v1/json/3/searchplayers.php",
                         params={"p": player}, headers=UA, timeout=6)
        if r.status_code == 200:
            players = (r.json() or {}).get("player") or []
            soccer = [x for x in players if x.get("strSport") == "Soccer"
                      and norm_key(x.get("strPlayer", "")) == key]
            if soccer:
                team = soccer[0].get("strTeam")
        else:
            # rate limited: keep whatever we knew, do not overwrite with unknown
            return hit.get("team") if isinstance(hit, dict) else None
    except Exception:  # noqa: BLE001
        return hit.get("team") if isinstance(hit, dict) else None
    with _logo_lock:                      # same lock family as the other disk caches
        _player_team_cache[key] = {"team": team, "ts": time.time()}
        try:
            PLAYER_TEAM_CACHE_FILE.write_text(json.dumps(_player_team_cache))
        except OSError:
            pass
    return team


def _verify_squads(pred: dict):
    """Drop predicted scorers who have verifiably left the club since the shot
    data was collected (best-effort; unknown players are kept)."""
    for side in ("home", "away"):
        team = pred[side]
        reg = store.registry.get(team["id"], {})
        if reg.get("scope") != "club":
            continue
        lst = pred["likely_scorers"].get(team["name"])
        if not lst:
            continue
        tsdb_name = _tsdb_team(team["id"]).get("tsdb_name")
        if not tsdb_name:
            continue
        kept = []
        for s in lst:
            current = _player_current_team(s["player"])
            if current and norm_key(current) != norm_key(tsdb_name):
                continue  # verifiably at a different club now
            kept.append(s)
        pred["likely_scorers"][team["name"]] = kept


@app.get("/api/buzz")
def buzz(home: str, away: str):
    """Recent Reddit chatter about the two teams (best-effort; may be rate limited)."""
    th, ta = _team_or_404(home), _team_or_404(away)
    q = f'"{th["name"]}" "{ta["name"]}"'
    posts, note = [], None
    try:
        r = requests.get("https://www.reddit.com/search.json",
                         params={"q": q, "sort": "new", "limit": 10, "t": "month"},
                         headers=UA, timeout=8)
        if r.status_code == 200:
            for c in r.json().get("data", {}).get("children", []):
                d = c["data"]
                posts.append({"title": d["title"], "subreddit": d["subreddit"],
                              "score": d["score"], "num_comments": d["num_comments"],
                              "url": "https://reddit.com" + d["permalink"]})
        else:
            note = f"Reddit returned HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        note = f"Reddit unreachable: {e}"
    if not posts and not note:
        note = "No recent Reddit posts mention both teams."
    return {"posts": posts, "note": note,
            "disclaimer": "Social buzz is shown for context only; it is not part of the statistical model."}


KEY_FILE = ROOT / "data" / "oddsapi_key.txt"


def _resolve_key(key: str = "") -> str:
    if key.strip():
        return key.strip()
    env = os.environ.get("ODDS_API_KEY", "").strip()   # cloud deployments (HF Space secret)
    if env:
        return env
    if KEY_FILE.exists():
        return KEY_FILE.read_text().strip()
    return ""


@app.get("/api/scan")
def scan_endpoint(key: str = "", sports: str = ""):
    """Cross-book arb scan via The Odds API (user-supplied free key)."""
    from .scanner import scan
    k = _resolve_key(key)
    if not k:
        return {"error": "no_key", "detail": "No API key configured."}
    sport_list = [s for s in sports.split(",") if s.strip()] or None
    return scan(k, sport_list)


@app.get("/api/scan/sports")
def scan_sports(key: str = ""):
    from .scanner import list_soccer_sports
    try:
        return list_soccer_sports(_resolve_key(key))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"could not list competitions: {e}")


# NOTE: football-only for now. Multi-sport support lives in backend/sports.py
# (events browser, market consensus, line shopping for NFL/NBA/MLB/NHL etc.) —
# re-add the /api/sports/* endpoints here when it's wanted again.


@app.get("/api/fpl/squad")
def fpl_model_squad():
    """The persistent model team: obeys real FPL rules, tracks its real score."""
    _require_store()
    from .fpl import model_squad
    try:
        return model_squad(store)
    except Exception as e:  # noqa: BLE001
        return {"error": "fpl_unavailable", "detail": str(e)[:200]}


@app.get("/api/fpl/gw")
def fpl_gameweek():
    _require_store()
    from .fpl import next_gameweek
    try:
        d = next_gameweek(store)
        # internal plumbing for the squad engines; not part of the public payload
        d.pop("xp_all", None)
        d.pop("teams_with_fixture", None)
        return d
    except Exception as e:  # noqa: BLE001
        return {"error": "fpl_unavailable", "detail": str(e)[:200]}


@app.get("/api/fpl/entry/{entry_id}")
def fpl_entry(entry_id: int):
    _require_store()
    from .fpl import entry_analysis
    try:
        return entry_analysis(store, entry_id)
    except Exception as e:  # noqa: BLE001
        return {"error": "fpl_unavailable", "detail": str(e)[:200]}


@app.get("/api/meta")
def meta():
    _require_store()
    m = store.matches
    return {
        "matches": int(len(m)),
        "teams": int(len(store.registry)),
        "leagues": int(m.league.nunique()),
        "data_from": str(m.date.min().date()),
        "data_to": str(m.date.max().date()),
        "xg_data_to": store.xg_data_to,
        "backtest": {
            "test_matches": 1827, "period": "Oct 2025 – Jan 2026",
            "model_accuracy": 0.504, "bookmaker_accuracy": 0.510,
            "model_brier": 0.6025, "bookmaker_brier": 0.5932,
            "note": "Full model (Elo + xG-blended strengths) under live conditions vs closing odds; "
                    "calibration verified per decile on a 14,432-match walk-forward test.",
        },
        "fantasy_eval": {
            "pairs": 734,
            "xg_only": 0.384, "record_only": 0.525, "blended": 0.543,
            "note": "Rank correlation with what players actually scored the FOLLOWING "
                    "season, measured on 734 real season-to-season pairs. Projecting from "
                    "shot quality alone ranks players at 0.38; a player's own scoring "
                    "record alone at 0.53; the shipped blend of both at 0.54.",
        },
        "blend": {
            "market_weight": 0.75,
            "note": "Validated on 4,227 unseen matches: accuracy improves monotonically toward "
                    "the market (optimum 1.0), but 0.75 costs only +0.0008 Brier, within noise. "
                    "0.75 is the maximum model weight the data defends.",
        },
        "live_eval": ({**store.live_eval,
                       "note": "Rolling window check of the Elo-driven core — the component "
                               "that decides match winners — against actual results and the "
                               "books' closing odds. The full blend layers xG on top for "
                               "totals and scorelines."}
                      if store.live_eval else None),
        "context_scales": {k: v for k, v in store.context_scales.items()},
        "refresh": {
            "auto": f"every {REFRESH_HOURS} hours",
            "last": refresher_state["last_refresh"],
            "refreshing": refresher_state["refreshing"],
            "last_error": refresher_state["last_error"],
        },
    }


# ---------- usage analytics + admin (no database: a JSON file on disk) ----------
# The free host wipes its disk on every deploy or restart, so this is honest
# "since the last restart" analytics, and the dashboard says so.

USAGE_FILE = ROOT / "data" / "usage.json"
_usage_lock = threading.Lock()
try:
    _usage = json.loads(USAGE_FILE.read_text())
    assert isinstance(_usage.get("daily"), dict)
except Exception:  # noqa: BLE001
    _usage = {"since": time.time(), "daily": {}, "recent": []}
_usage_dirty = 0

# ---------- durable analytics: Supabase (falls back to the local file) ----------
# Events stream into a `usage_events` table via PostgREST, batched off-thread so a
# slow or missing database can never slow a page down. The dashboard reads a
# `usage_stats()` SQL function; if Supabase is unconfigured or down, it serves
# the local on-disk aggregates instead.

SB_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SB_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
_sb_queue: list[dict] = []
_sb_lock = threading.Lock()
_sb_flusher_started = False


def _sb_headers() -> dict:
    return {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}",
            "Content-Type": "application/json", "Prefer": "return=minimal"}


def _sb_log(evt: dict) -> None:
    if not (SB_URL and SB_KEY):
        return
    global _sb_flusher_started
    with _sb_lock:
        _sb_queue.append(evt)
        if len(_sb_queue) > 2000:          # backstop if the database is unreachable
            del _sb_queue[:1000]
        if not _sb_flusher_started:
            _sb_flusher_started = True
            threading.Thread(target=_sb_flusher, daemon=True,
                             name="plus100-usage-flush").start()


def _sb_flusher() -> None:
    while True:
        time.sleep(15)
        with _sb_lock:
            batch, _sb_queue[:] = _sb_queue[:], []
        if not batch:
            continue
        # PostgREST bulk inserts demand identical keys on every row: pad the
        # short "request" rows so they can share a batch with prediction rows
        cols = ("kind", "path", "visitor", "ip", "home", "away", "context", "neutral")
        batch = [{c: e.get(c) for c in cols} for e in batch]
        try:
            r = requests.post(f"{SB_URL}/rest/v1/usage_events", json=batch,
                              headers=_sb_headers(), timeout=10)
            if r.status_code == 400:
                # most likely the ip column migration hasn't been run yet:
                # keep the analytics flowing without it rather than dropping
                slim = [{k: v for k, v in e.items() if k != "ip"} for e in batch]
                r = requests.post(f"{SB_URL}/rest/v1/usage_events", json=slim,
                                  headers=_sb_headers(), timeout=10)
            if r.status_code >= 500:          # transient server-side: retry once
                raise RuntimeError(f"supabase {r.status_code}")
        except Exception:  # noqa: BLE001 — re-queue once so a blip loses nothing
            with _sb_lock:
                _sb_queue[:0] = batch[-500:]


def _sb_stats() -> dict | None:
    if not (SB_URL and SB_KEY):
        return None
    try:
        r = requests.post(f"{SB_URL}/rest/v1/rpc/usage_stats", json={},
                          headers=_sb_headers(), timeout=12)
        if r.status_code != 200:
            return None
        d = r.json()
        if not isinstance(d, dict):
            return None
        d.setdefault("since", time.time())
        d["note"] = ("Stored durably in Supabase; survives every restart and deploy. "
                     "No third-party trackers; visitor IP addresses are recorded "
                     "for the site owner.")
        return d
    except Exception:  # noqa: BLE001
        return None


ADMIN_USER = "nevinselby"


def _admin_pass() -> str:
    # never in the (public) repo: comes from the PLUS100_ADMIN_PASS env var
    return os.environ.get("PLUS100_ADMIN_PASS", "")


def _admin_token() -> str | None:
    ap = _admin_pass()
    if not ap:
        return None
    return hmac.new(ap.encode(), b"plus100-admin-session", hashlib.sha256).hexdigest()


def _client_ip(request: Request) -> str:
    """Real client address: first hop of X-Forwarded-For (set by the host's
    proxy on Render), falling back to the socket peer when running locally."""
    return (request.headers.get("x-forwarded-for")
            or (request.client.host if request.client else "?")).split(",")[0].strip()[:45]


def _visitor_id(request: Request) -> str:
    ua = request.headers.get("user-agent", "")[:80]
    return hashlib.sha256(f"{_client_ip(request)}|{ua}".encode()).hexdigest()[:12]


def _day_bucket(day: str) -> dict:
    return _usage["daily"].setdefault(day, {"requests": 0, "visitors": {}, "predictions": 0})


def _flush_usage() -> None:
    try:
        USAGE_FILE.write_text(json.dumps(_usage))
    except OSError:
        pass


@app.middleware("http")
async def _track_usage(request: Request, call_next):
    response = await call_next(request)
    try:
        path = request.url.path
        if not (path.startswith("/static") or path.startswith("/api/admin")
                or path in ("/healthz", "/favicon.ico")):
            global _usage_dirty
            vid = _visitor_id(request)
            ip = _client_ip(request)
            _sb_log({"kind": "request", "path": path[:80], "visitor": vid, "ip": ip})
            with _usage_lock:
                d = _day_bucket(time.strftime("%Y-%m-%d"))
                d["requests"] += 1
                d["visitors"][vid] = d["visitors"].get(vid, 0) + 1
                v = _usage.setdefault("ips", {}).setdefault(vid, {"ip": ip, "n": 0})
                v.update(ip=ip, last=int(time.time()), n=v.get("n", 0) + 1)
                _usage_dirty += 1
                if _usage_dirty >= 25:
                    _usage_dirty = 0
                    _flush_usage()
    except Exception:  # noqa: BLE001 — analytics must never break the site
        pass
    return response


def _log_prediction(request: Request, p: dict, context: str, neutral: bool) -> None:
    try:
        _sb_log({"kind": "prediction", "path": "/api/predict",
                 "visitor": _visitor_id(request), "ip": _client_ip(request),
                 "home": p["home"]["name"], "away": p["away"]["name"],
                 "context": context if context not in ("", "none") else "regular",
                 "neutral": bool(neutral)})
        with _usage_lock:
            _day_bucket(time.strftime("%Y-%m-%d"))["predictions"] += 1
            _usage["recent"] = ([{
                "ts": int(time.time()), "home": p["home"]["name"], "away": p["away"]["name"],
                "context": context if context not in ("", "none") else "regular",
                "neutral": bool(neutral), "visitor": _visitor_id(request),
                "ip": _client_ip(request),
            }] + _usage["recent"])[:500]
            _flush_usage()
    except Exception:  # noqa: BLE001
        pass


class AdminLogin(BaseModel):
    username: str
    password: str


# brute-force throttle: after 5 straight failures the login sleeps for 15
# minutes. Global (there is exactly one admin), in-memory (resets on restart —
# fine, the free host restarts often and the window only needs to slow a bot).
_login_guard = {"fails": 0, "until": 0.0}


@app.post("/api/admin/login")
def admin_login(body: AdminLogin):
    ap = _admin_pass()
    if not ap:
        raise HTTPException(503, "Admin is not configured on this server "
                                 "(PLUS100_ADMIN_PASS is unset).")
    if time.time() < _login_guard["until"]:
        raise HTTPException(429, "Too many attempts. Try again in a few minutes.")
    if body.username != ADMIN_USER or not hmac.compare_digest(body.password, ap):
        _login_guard["fails"] += 1
        if _login_guard["fails"] >= 5:
            _login_guard["until"] = time.time() + 900
            _login_guard["fails"] = 0
        raise HTTPException(401, "Wrong username or password.")
    _login_guard["fails"] = 0
    return {"token": _admin_token()}


def _require_admin(request: Request) -> None:
    exp = _admin_token()
    tok = request.headers.get("x-admin-token", "")
    if not exp or not hmac.compare_digest(tok, exp):
        raise HTTPException(401, "admin login required")


@app.get("/api/admin/stats")
def admin_stats(request: Request):
    _require_admin(request)
    sb = _sb_stats()
    if sb is not None:
        return sb
    with _usage_lock:
        daily = [{"date": k, "requests": v["requests"], "visitors": len(v["visitors"]),
                  "predictions": v.get("predictions", 0)}
                 for k, v in sorted(_usage["daily"].items())]
        allv: set = set()
        for v in _usage["daily"].values():
            allv.update(v["visitors"].keys())
        matchups: dict[str, int] = {}
        for r in _usage["recent"]:
            key = f"{r['home']} v {r['away']}"
            matchups[key] = matchups.get(key, 0) + 1
        visitors = sorted(
            ({"visitor": vid, "ip": v.get("ip"), "n": v.get("n", 0),
              "last": v.get("last")} for vid, v in _usage.get("ips", {}).items()),
            key=lambda x: -(x["last"] or 0))[:20]
        return {
            "since": _usage["since"],
            "unique_visitors": len(allv),
            "total_requests": sum(d["requests"] for d in daily),
            "total_predictions": sum(d["predictions"] for d in daily),
            "daily": daily[-30:],
            "top_matchups": [{"matchup": k, "n": n} for k, n in
                             sorted(matchups.items(), key=lambda x: -x[1])[:10]],
            "recent": _usage["recent"][:100],
            "visitors": visitors,
            "note": ("Counted on the server itself, no third-party trackers; visitor "
                     "IP addresses are recorded for the site owner. The free host "
                     "wipes its disk on every deploy or restart, so history starts "
                     "over at that point."),
        }


@app.get("/admin")
def admin_page():
    return FileResponse(str(ROOT / "frontend" / "admin.html"))


app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


@app.get("/")
def index():
    # always revalidate the page itself; versioned ?v= query strings handle JS/CSS
    return FileResponse(ROOT / "frontend" / "index.html",
                        headers={"Cache-Control": "no-cache"})


# ---------- upcoming fixtures (same source as our match history) ----------

_fixtures_cache: dict = {}
_FIXTURES_TTL = 1800          # 30 min

# division codes in rough order of interest
_DIV_RANK = {"E0": 0, "SP1": 1, "I1": 2, "D1": 3, "F1": 4, "N1": 5, "P1": 6,
             "B1": 7, "T1": 8, "SC0": 9, "E1": 10}


@app.get("/api/fixtures/upcoming")
def upcoming_fixtures(days: int = 7, limit: int = 40):
    """Real upcoming matches from football-data.co.uk — the same feed our match
    history comes from, so every team name maps straight onto the model.
    Kick-offs are ISO-8601 WITH a UTC offset (UK wall time), so clients can show
    the viewer's local time with a plain Date parse; the first 16 characters
    still read as UK time for older clients that slice the string."""
    _require_store()
    days = max(1, min(days, 14))
    limit = max(1, min(limit, 60))
    key = f"up:{days}:{limit}"
    hit = _fixtures_cache.get(key)
    if hit and time.time() - hit[0] < _FIXTURES_TTL:
        return hit[1]

    import csv
    import io as _io
    from datetime import datetime, timedelta

    try:
        from zoneinfo import ZoneInfo
        _uk = ZoneInfo("Europe/London")
    except Exception:  # noqa: BLE001
        _uk = None

    def _iso_uk(naive_london: datetime) -> str:
        if _uk is not None:
            return naive_london.replace(tzinfo=_uk).isoformat(timespec="minutes")
        return naive_london.strftime("%Y-%m-%dT%H:%M")

    def _utc_to_london(naive_utc: datetime) -> datetime:
        if _uk is not None:
            from datetime import timezone as _tz
            return naive_utc.replace(tzinfo=_tz.utc).astimezone(_uk).replace(tzinfo=None)
        return naive_utc

    try:
        r = requests.get("https://www.football-data.co.uk/fixtures.csv",
                         headers=UA, timeout=12)
        r.raise_for_status()
        text = r.content.decode("utf-8-sig", errors="replace")
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "The fixtures feed is not answering right now; try again in a minute.")

    by_name = {norm_key(reg["name"]): tid for tid, reg in store.registry.items()
               if reg["scope"] == "club"}
    from .data_store import LEAGUE_NAMES

    now = (datetime.now(_uk).replace(tzinfo=None) if _uk is not None
           else datetime.utcnow())
    horizon = now + timedelta(days=days)
    out = []
    seen: set[tuple] = set()   # (home_id, away_id): a rescheduled match shows once

    def _push(hid, aid, ko_london, league, country, odds, rank):
        if (hid, aid) in seen:
            return
        seen.add((hid, aid))
        out.append({
            "home_id": hid, "away_id": aid,
            "home": store.registry[hid]["name"], "away": store.registry[aid]["name"],
            "home_elo": store.registry[hid]["elo_global"],
            "away_elo": store.registry[aid]["elo_global"],
            "league": league, "country": country,
            "kickoff": _iso_uk(ko_london),
            "kicked_off": ko_london <= now,
            "odds": odds, "rank": rank,
        })

    for row in csv.DictReader(_io.StringIO(text)):
        div, hn, an = row.get("Div"), row.get("HomeTeam"), row.get("AwayTeam")
        if not div or not hn or not an:
            continue
        try:
            ko = datetime.strptime(f"{row['Date']} {row.get('Time') or '15:00'}",
                                   "%d/%m/%Y %H:%M")
        except (ValueError, KeyError):
            continue
        if ko < now - timedelta(hours=3) or ko > horizon:
            continue
        hid, aid = by_name.get(norm_key(hn)), by_name.get(norm_key(an))
        if not hid or not aid:
            continue
        league, country = LEAGUE_NAMES.get(div, (div, ""))
        try:
            odds = {"home": float(row["AvgH"]), "draw": float(row["AvgD"]),
                    "away": float(row["AvgA"])}
        except (TypeError, ValueError, KeyError):
            odds = None
        _push(hid, aid, ko, league, country, odds, _DIV_RANK.get(div, 20))

    primary_n = len(out)
    have_pl = any(f["league"] == "Premier League" for f in out)
    note = "Confirmed fixtures from the leagues this model is built on."
    # Between rounds the main feed goes quiet (sometimes only partially: it can
    # hold a stray midweek game while missing the whole next PL round). Fall back
    # to the official Premier League schedule whenever no PL fixture surfaced,
    # and to the per-league schedule source when the rail is still thin.
    if not have_pl:
        try:
            from .fpl import BASE as FPL_BASE
            from .fpl import _get as fpl_get
            from .fpl import _team_map
            boot = fpl_get(f"{FPL_BASE}/bootstrap-static/")
            tmap = _team_map(store, boot["teams"])
            for f in fpl_get(f"{FPL_BASE}/fixtures/?future=1"):
                ko = f.get("kickoff_time")
                if not ko:
                    continue
                kod = _utc_to_london(datetime.strptime(ko, "%Y-%m-%dT%H:%M:%SZ"))
                if kod > now + timedelta(days=max(days, 12)):
                    continue
                th, ta = tmap.get(f["team_h"]), tmap.get(f["team_a"])
                if not th or not ta or not th["registry_id"] or not ta["registry_id"]:
                    continue
                _push(th["registry_id"], ta["registry_id"], kod,
                      "Premier League", "England", None, 1)
        except Exception:  # noqa: BLE001
            pass
    if primary_n < 5:
        # other big leagues: the free schedule source lists the next confirmed
        # match per league — thin, but keeps the rail worldwide between rounds
        _TSDB_LEAGUES = [("4335", "La Liga", "Spain"), ("4332", "Serie A", "Italy"),
                         ("4331", "Bundesliga", "Germany"), ("4334", "Ligue 1", "France"),
                         ("4337", "Eredivisie", "Netherlands"),
                         ("4344", "Primeira Liga", "Portugal"),
                         ("4329", "Championship", "England")]

        def _map_club(name: str):
            from .bestbets import resolve_team
            tid = resolve_team(store, name, "club")
            if not tid:
                for pref in ("AC ", "AS ", "FC ", "SS ", "SSC ", "CF ", "RC "):
                    if name.startswith(pref):
                        tid = resolve_team(store, name[len(pref):], "club")
                        if tid:
                            break
            return tid

        for lgid, lgname, country in _TSDB_LEAGUES:
            try:
                r2 = requests.get("https://www.thesportsdb.com/api/v1/json/3/eventsnextleague.php",
                                  params={"id": lgid}, headers=UA, timeout=8)
                for ev in (r2.json() or {}).get("events") or []:
                    ts = ev.get("strTimestamp")
                    if not ts:
                        continue
                    kod = _utc_to_london(datetime.strptime(ts[:16], "%Y-%m-%dT%H:%M"))
                    if kod < now - timedelta(hours=3) or kod > now + timedelta(days=max(days, 12)):
                        continue
                    hid = _map_club(ev.get("strHomeTeam") or "")
                    aid = _map_club(ev.get("strAwayTeam") or "")
                    if not hid or not aid:
                        continue
                    _push(hid, aid, kod, lgname, country, None, 2)
            except Exception:  # noqa: BLE001
                continue
    if len(out) > primary_n:
        note = ("The odds feed is between rounds, so confirmed league schedules "
                "fill the gaps.")
    out.sort(key=lambda f: (f["kickoff"], f["rank"]))   # soonest first, all leagues mixed
    payload = {"fixtures": out[:limit], "count": len(out), "note": note}
    _fixtures_cache[key] = (time.time(), payload)
    return payload
