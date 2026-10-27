"""FastAPI app: team search, head-to-head stats, predictions, lineups, fantasy, odds."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import math
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import fpl as _fpl
from .data_store import DIV_RANK, LEAGUE_NAMES, get_store, norm_key, season_start, slug
from .fpl import club_squad as fpl_squad
from .model import expected_goals, likely_scorers, predict

ROOT = Path(__file__).resolve().parent.parent
LOGO_CACHE_FILE = ROOT / "data" / "logo_cache.json"
UA = {"User-Agent": "Mozilla/5.0 (FootballAnalytics; personal research tool)"}
# one pool for the per-request fan-out to upstream feeds (two teams at a time)
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="plus100-io")

app = FastAPI(title="Plus100 Football Predictor")

# Read-only public API: allow browser clients (Expo web debugging, the PWA on
# another origin) to call it. The native app is unaffected by CORS.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
app.add_middleware(GZipMiddleware, minimum_size=1024)   # player lists shrink ~8x

# The model store takes a couple of minutes to build on a cold machine (it
# downloads the full match history first). Build it in the background so the
# web server binds its port immediately; cloud hosts kill services that do
# not answer within a few minutes of starting.
store = None
warmup = {"ready": False, "error": None, "since": time.time()}


def _warm_up() -> None:
    global store
    # A failed build (a data source down mid-boot, a transient network hole)
    # must not leave the service dead until someone restarts it: keep retrying
    # with growing pauses. The bootstrap downloader skips files it already has,
    # so each retry only re-fetches what is still missing.
    delay = 60
    while True:
        try:
            store = get_store()
            warmup["ready"] = True
            warmup["error"] = None
            from .refresher import start_background
            start_background()          # also starts the cache prewarm
            threading.Thread(target=_keepalive, daemon=True, name="plus100-keepalive").start()
            return
        except Exception as e:  # noqa: BLE001
            warmup["error"] = f"{str(e)[:250]} (retrying in {delay}s)"
            time.sleep(delay)
            delay = min(delay * 2, 900)


from .refresher import REFRESH_HOURS  # noqa: E402
from .refresher import state as refresher_state  # noqa: E402


def _keepalive() -> None:
    """The free host sleeps after 15 idle minutes and takes a minute to wake,
    which users feel as 'the app is slow'. Touching our own public URL every
    ten minutes counts as traffic and keeps it awake while anyone used it today."""
    url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not url:
        return
    while True:
        time.sleep(600)
        try:
            requests.get(f"{url}/healthz", timeout=20)
        except requests.RequestException:
            pass


def _prewarm() -> None:
    """Fill the per-team caches (news, availability, crest, squad) for every
    team in the next week's fixture rail, so the first user to open a match
    gets an instant answer instead of paying for six upstream fetches."""
    try:
        time.sleep(5)
        fx = upcoming_fixtures(days=8, limit=60)["fixtures"]
    except Exception:  # noqa: BLE001
        return
    seen = []
    for f in fx:
        for tid in (f["home_id"], f["away_id"]):
            if tid not in seen:
                seen.append(tid)
    for tid in seen[:48]:
        try:
            _auto_absences(tid)
            _tsdb_team(tid)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    for f in fx[:16]:
        try:
            eg = expected_goals(store, f["home_id"], f["away_id"], neutral=False)
            _team_lineup(f["home_id"], eg["lambda_home"])
            _team_lineup(f["away_id"], eg["lambda_away"])
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)


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
                                 "match history and takes a couple of minutes. Try again shortly.")

_logo_lock = threading.Lock()
_logo_cache: dict[str, dict | str | None] = (
    json.loads(LOGO_CACHE_FILE.read_text()) if LOGO_CACHE_FILE.exists() else {}
)


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
        nk = r["nk"]
        if key not in nk:
            continue
        score = (0 if nk.startswith(key) else 1,
                 0 if r["active"] else 1,
                 -r["n"])
        scored.append((score, {
            "id": tid, "name": r["name"], "league": r["league_name"],
            "country": r["country"], "scope": r["scope"],
            "elo": r["elo_global"], "elo_basis": r["elo_basis"], "active": r["active"],
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
    } for _, r in hh.tail(12).iloc[::-1].iterrows()]

    def elo_series(tid: str, points: int = 60) -> list:
        h = store.elo_hist.get(tid, [])
        return [[str(pd.Timestamp(d).date()), round(e)] for d, e in h[-points:]]

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
    """Likely absentees among the players our model tracks, from official live
    sources only: the FPL availability flags (PL clubs, published daily by the
    league) and the injury flags on ESPN's current squad list. No headline
    guessing: a side with no official feed simply gets no absences."""
    reg = store.registry.get(tid, {})
    if reg.get("scope") != "club":
        return []
    names = [r["player"] for r in store.player_rates.get(tid, [])[:12]]
    outs = _espn_flagged_outs(tid, names, [])
    return _fpl_flagged_outs(tid, names, outs)


def _espn_flagged_outs(tid: str, names: list[str], outs: list[str]) -> list[str]:
    """Append tracked players ESPN's squad list marks as injured. Official feed,
    never capped; no-op when the roster is unavailable."""
    try:
        from . import espn
        for p in espn.soccer_roster(store, tid) or []:
            if p.get("injured"):
                hit = _match_tracked(p["name"], names)
                if hit and hit not in outs:
                    outs.append(hit)
    except Exception:  # noqa: BLE001
        pass
    return outs


def _fpl_flagged_outs(tid: str, names: list[str], outs: list[str]) -> list[str]:
    """Append players the OFFICIAL FPL availability feed flags as out/doubtful.
    Authoritative, so never capped; no-op for non-PL clubs and on any error."""
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
    via the fitted elo-to-goals curve (so the discount speaks the model's language)."""
    t = store.registry[tid]
    base = float(t["elo_global"])
    from .model import _absence_factor
    factor, applied = _absence_factor(store, tid, outs)
    fit = store.goal_fit_club if t["scope"] == "club" else store.goal_fit_intl
    b = (abs(fit["b"]) + abs(fit["e"])) / 2 or 1.0
    delta = 400.0 * math.log(max(factor, 0.6)) / b
    return {"elo": round(base), "elo_effective": round(base + delta), "elo_delta": round(delta)}


@app.get("/api/teamstate")
def teamstate(team_id: str):
    """Live team condition for the pickers: dynamic rating + who is missing."""
    _team_or_404(team_id)
    outs = _auto_absences(team_id)
    return {"id": team_id, **_effective_elo(team_id, outs), "outs": outs}


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
    # reflect who can actually play, not just the two crests. predict() writes
    # the caveat for every absentee it prices or cannot price.
    f_h = _pool.submit(lambda: [] if oh else _auto_absences(home))
    f_a = _pool.submit(lambda: [] if oa else _auto_absences(away))
    auto_oh, auto_oa = f_h.result(), f_a.result()
    p = predict(store, home, away, neutral, out_home=oh or auto_oh,
                out_away=oa or auto_oa, context=context)
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


@app.get("/api/bestbets")
def bestbets_endpoint(key: str = "", home: str = "", away: str = ""):
    _require_store()
    from .bestbets import best_bets
    k = _resolve_key(key)
    if not k:
        return {"error": "no_key", "detail": "No API key configured."}
    return best_bets(k, store, sel_home=home.strip(), sel_away=away.strip())


# football-data short names that TheSportsDB doesn't know under that spelling:
# search with the club's canonical name instead (keyed by our registry id)
TSDB_SEARCH_ALIASES = {
    "sociedad": "Real Sociedad", "betis": "Real Betis", "ath-madrid": "Atletico Madrid",
    "ath-bilbao": "Athletic Bilbao", "espanol": "Espanyol", "vallecano": "Rayo Vallecano",
    "celta": "Celta Vigo", "sp-gijon": "Sporting Gijon", "la-coruna": "Deportivo La Coruna",
    "nott-m-forest": "Nottingham Forest", "sheffield-weds": "Sheffield Wednesday",
    "qpr": "Queens Park Rangers", "west-brom": "West Bromwich Albion",
    "wolves": "Wolverhampton Wanderers", "man-united": "Manchester United",
    "man-city": "Manchester City", "tottenham": "Tottenham Hotspur",
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
        r = requests.get("https://www.thesportsdb.com/api/v1/json/123/lookupteam.php",
                         params={"id": known}, headers=UA, timeout=8)
        rec = ((r.json() or {}).get("teams") or [None])[0]
        return rec if rec and rec.get("strSport") == "Soccer" else None
    q = TSDB_SEARCH_ALIASES.get(t["id"], t["name"])
    for query in dict.fromkeys((q, q.lower())):
        r = requests.get("https://www.thesportsdb.com/api/v1/json/123/searchteams.php",
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
    if (isinstance(cached, dict) and "colors" in cached
            and time.time() - cached.get("at", 0) < 30 * 86400):   # refetch monthly
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
    entry["at"] = time.time()
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
_SQUAD_TTL = 86400           # squads refresh daily
_PLAYER_TTL = 5 * 86400      # a player's record (position, photo) is re-checked every few days
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
    """Squad players with position + cutout photo, cached one day on disk."""
    with _lineup_lock:
        hit = _lineup_cache.get(team_id)
    if hit and time.time() - hit["at"] < _SQUAD_TTL:
        return hit["players"]
    t = _team_or_404(team_id)
    players = []
    try:
        best = _tsdb_search_team(t)
        if best:
            r2 = requests.get("https://www.thesportsdb.com/api/v1/json/123/lookup_all_players.php",
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
    # entries are {"at": ts, "player": dict | None}; older shapes are simply refetched.
    # Callers get a copy so the p_score they attach never lands in the cache file.
    if isinstance(hit, dict) and "at" in hit and time.time() - hit["at"] < _PLAYER_TTL:
        return dict(hit["player"]) if hit["player"] else None
    qk = norm_key(name)
    tk = norm_key(team_name)
    gated, exact_only = None, None
    try:
        r = requests.get("https://www.thesportsdb.com/api/v1/json/123/searchplayers.php",
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
        _lineup_cache[key] = {"at": time.time(), "player": best}
        try:
            LINEUP_CACHE_FILE.write_text(json.dumps(_lineup_cache))
        except OSError:
            pass
    return dict(best) if best else None


# Standard shapes (defenders, midfielders, forwards), most common first. A
# line-up is only ever shown as one of these, so no 1-3-3 nonsense can appear.
FORMATIONS = [(4, 3, 3), (4, 4, 2), (4, 5, 1), (3, 5, 2), (3, 4, 3), (5, 3, 2), (5, 4, 1)]


def _pick_formation(n_def: int, n_mid: int, n_fwd: int) -> tuple[int, int, int] | None:
    """The most common standard shape the available players can fill."""
    for d, m, fw in FORMATIONS:
        if d <= n_def and m <= n_mid and fw <= n_fwd:
            return d, m, fw
    return None


def _team_lineup(tid: str, lam: float) -> dict:
    t = _team_or_404(tid)
    club = t["scope"] == "club"
    scorers = likely_scorers(store, tid, t, lam)
    p_by_key = {norm_key(s["player"]): s["prob_to_score"] for s in scorers}

    # Premier League clubs: the FPL API gives the COMPLETE current squad with
    # positions, availability and minutes, so it beats the partial public feed.
    squad, source = [], "public squad data"
    if club:
        try:
            squad = [dict(p) for p in fpl_squad(store, tid)]
        except Exception:  # noqa: BLE001
            squad = []
        if squad:
            source = "the official Premier League squad list"
    espn_outs: list[str] = []
    if not squad:
        try:
            from . import espn
            roster = espn.soccer_roster(store, tid) if club else espn.intl_roster(t["name"])
        except Exception:  # noqa: BLE001
            roster = []
        if len(roster) >= 11:
            squad = [dict(p) for p in roster]
            espn_outs = [p["name"] for p in roster if p.get("injured")]
            source = "the current squad list"
    if not squad:
        squad = [dict(p) for p in _tsdb_squad(tid)]
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

    # playing time decides who starts. PL squads carry FPL minutes; for the other
    # big leagues understat's per-player minutes fill the same role.
    umins = store.player_minutes.get(tid) or {}
    if umins and not any(p.get("minutes") for p in squad):
        by_key = {norm_key(n): m for n, m in umins.items()}
        for p in squad:
            k = norm_key(p["name"])
            m = by_key.get(k)
            if m is None:
                last = k[-6:] if len(k) > 6 else k
                cands = [v for kk, v in by_key.items() if len(last) >= 5 and kk.endswith(last)]
                m = max(cands) if len(cands) == 1 else None
            p["minutes"] = m or 0

    # top scorers missing from the squad list get looked up individually
    intl = not club
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

    # drop players the official feeds flag as out (ESPN squad injury flags, FPL
    # availability), but never empty a position doing it
    outs = list(espn_outs)
    if club:
        outs = _fpl_flagged_outs(tid, [p["name"] for p in squad], outs)
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
        def _jersey(p):
            try:
                return int(p.get("jersey") or 99)
            except (TypeError, ValueError):
                return 99
        pool[b].sort(key=lambda p: (-(p.get("minutes") or 0), -(p.get("p_score") or 0),
                                    -(p.get("sel") or 0), _jersey(p), p.get("img") is None, p["name"]))

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
    f_h = _pool.submit(_team_lineup, home, eg["lambda_home"])
    f_a = _pool.submit(_team_lineup, away, eg["lambda_away"])
    h, a = f_h.result(), f_a.result()
    srcs = sorted({h["source"], a["source"]})
    return {
        "home": h, "away": a,
        "note": (f"Built from {' and '.join(srcs)}, ranked by playing time and each "
                 "player's share of his team's expected goals, then arranged in a "
                 "standard formation those players can fill. Real team sheets drop "
                 "about an hour before kickoff and can differ."),
    }


KEY_FILE = ROOT / "data" / "oddsapi_key.txt"


def _resolve_key(key: str = "") -> str:
    if key.strip():
        return key.strip()
    env = os.environ.get("ODDS_API_KEY", "").strip()   # cloud deployments
    if env:
        return env
    if KEY_FILE.exists():
        return KEY_FILE.read_text().strip()
    return ""


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
    y = season_start(m.date.max())
    return {
        "matches": int(len(m)),
        "teams": int(len(store.registry)),
        "leagues": int(m.league.nunique()),
        "data_from": str(m.date.min().date()),
        "data_to": str(m.date.max().date()),
        "season": f"{y}-{(y + 1) % 100:02d}",
        "xg_data_to": store.xg_data_to,
        "fantasy_eval": _fpl.FANTASY_EVAL,     # measured figures live next to the code that made them
        "live_eval": ({**store.live_eval,
                       "note": "Rolling window check of the Elo-driven core (the component "
                               "that decides match winners) against actual results and the "
                               "books' closing odds. The full blend layers xG on top for "
                               "totals and scorelines."}
                      if store.live_eval else None),
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

from .fpl import _sb_conf, _sb_h  # noqa: E402 (one Supabase config, shared with fpl_state)
SB_URL, SB_KEY = _sb_conf() or ("", "")
_sb_queue: list[dict] = []
_sb_lock = threading.Lock()
_sb_flusher_started = False


def _sb_headers() -> dict:
    return _sb_h(SB_KEY) | {"Prefer": "return=minimal"}


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
        except Exception:  # noqa: BLE001 (re-queue once so a blip loses nothing)
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


def _mark_dirty() -> None:
    """Call with _usage_lock held: write the file on every 25th change."""
    global _usage_dirty
    _usage_dirty += 1
    if _usage_dirty >= 25:
        _usage_dirty = 0
        _flush_usage()


@app.middleware("http")
async def _track_usage(request: Request, call_next):
    if warmup["ready"]:
        try:
            from .refresher import maybe_refresh
            maybe_refresh()
        except Exception:  # noqa: BLE001
            pass
    response = await call_next(request)
    try:
        path = request.url.path
        if not (path.startswith("/static") or path.startswith("/api/admin")
                or path in ("/healthz", "/favicon.ico")):
            vid = _visitor_id(request)
            ip = _client_ip(request)
            _sb_log({"kind": "request", "path": path[:80], "visitor": vid, "ip": ip})
            with _usage_lock:
                d = _day_bucket(time.strftime("%Y-%m-%d"))
                d["requests"] += 1
                d["visitors"][vid] = d["visitors"].get(vid, 0) + 1
                ips = _usage.setdefault("ips", {})
                v = ips.setdefault(vid, {"ip": ip, "n": 0})
                v.update(ip=ip, last=int(time.time()), n=v.get("n", 0) + 1)
                if len(ips) > 500:              # keep only the newest 500 visitors
                    for k in sorted(ips, key=lambda k: ips[k].get("last", 0))[:len(ips) - 500]:
                        del ips[k]
                _mark_dirty()
    except Exception:  # noqa: BLE001 (analytics must never break the site)
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
            _mark_dirty()
    except Exception:  # noqa: BLE001
        pass


class AdminLogin(BaseModel):
    username: str
    password: str


# brute-force throttle: after 5 straight failures the login sleeps for 15
# minutes. Global (there is exactly one admin), in-memory (resets on restart;
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


class StateBody(BaseModel):
    key: str
    value: dict


@app.get("/api/admin/fpl/state")
def admin_fpl_state(request: Request, key: str = "model_squad"):
    """Read one stored fantasy record (the model squad or an archive) for inspection."""
    _require_admin(request)
    v = _fpl.state_get(key)
    return {"key": key, "unavailable": v is _fpl.UNAVAILABLE, "value": None if v is _fpl.UNAVAILABLE else v}


@app.post("/api/admin/fpl/state")
def admin_fpl_state_put(body: StateBody, request: Request):
    """Restore or correct a stored fantasy record. Whatever was there is kept under a dated key first."""
    _require_admin(request)
    prev = _fpl.state_get(body.key)
    if isinstance(prev, dict):
        _fpl.state_put(f"{body.key}_before_{int(time.time())}", prev)
    return {"saved": _fpl.state_put(body.key, body.value)}


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


# ---------- basketball (NBA): same product, different sport ----------
from . import nba as _nba  # noqa: E402


@app.get("/api/nba/teams")
def nba_teams():
    try:
        return _nba.teams()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"basketball data unavailable: {str(e)[:80]}")


def _nba_call(fn, *args, **kw):
    """Run a basketball model call; an unknown team id is a 404, like /api/nba/predict."""
    try:
        return fn(*args, **kw)
    except KeyError:
        raise HTTPException(404, "unknown team")


@app.get("/api/nba/predict")
def nba_predict(request: Request, home: str, away: str, neutral: bool = False,
                date: str | None = None):
    """date: the fixture's kickoff date (ISO) so rest days are counted to the game, not to today."""
    if home == away:
        raise HTTPException(400, "pick two different teams")
    p = _nba_call(_nba.predict, home, away, neutral, date=date)
    try:
        _sb_log({"kind": "prediction", "path": "/api/nba/predict", "visitor": _visitor_id(request),
                 "ip": _client_ip(request), "home": home, "away": away, "context": "nba",
                 "neutral": bool(neutral)})
    except Exception:  # noqa: BLE001
        pass
    p["h2h"] = _nba.h2h(home, away)
    return p


@app.get("/api/nba/fixtures")
def nba_fixtures(days: int = 7):
    return {"fixtures": _nba.upcoming(max(1, min(days, 14))),
            "note": "Official schedule, refreshed every half hour. Lines shown are the books' current numbers."}


@app.get("/api/nba/bestbets")
def nba_bestbets(key: str = ""):
    k = _resolve_key(key)
    if not k:
        return {"error": "no_key", "detail": "No odds API key configured on this server."}
    return _nba.grade_odds(k)


@app.get("/api/nba/props")
def nba_props(home: str, away: str, date: str | None = None):
    """Model player props (points, rebounds, assists) for a matchup."""
    if home == away:
        raise HTTPException(400, "pick two different teams")
    return _nba_call(_nba.player_props, home, away, date=date)


@app.get("/api/nba/props/market")
def nba_props_market(home: str, away: str, key: str = ""):
    """The model's props graded against the books' current prop lines."""
    if home == away:
        raise HTTPException(400, "pick two different teams")
    k = _resolve_key(key)
    if not k:
        return {"error": "no_key", "detail": "No odds API key configured on this server."}
    return _nba_call(_nba.grade_props, home, away, k)


@app.get("/api/nba/meta")
def nba_meta():
    return _nba.meta() | {
        "note": ("Elo with a margin-of-victory multiplier, re-fitted on every refresh. The "
                 "slope from rating gap to points and both spreads are measured on the stored "
                 "games, not assumed. Walk-forward accuracy is on the most recent full season.")}


@app.get("/admin")
def admin_page():
    return FileResponse(ROOT / "frontend" / "admin.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


@app.get("/")
def index():
    # always revalidate the page itself; versioned ?v= query strings handle JS/CSS
    return FileResponse(ROOT / "frontend" / "index.html",
                        headers={"Cache-Control": "no-cache"})


# ---------- upcoming fixtures (same source as our match history) ----------

_fixtures_cache: dict[int, tuple[float, list, str]] = {}   # days -> (built_at, fixtures, note)
_FIXTURES_TTL = 1800          # 30 min

try:
    from zoneinfo import ZoneInfo
    _UK = ZoneInfo("Europe/London")
except Exception:  # noqa: BLE001
    _UK = None


def _iso_uk(naive_london: datetime) -> str:
    if _UK is not None:
        return naive_london.replace(tzinfo=_UK).isoformat(timespec="minutes")
    return naive_london.strftime("%Y-%m-%dT%H:%M")


def _utc_to_london(naive_utc: datetime) -> datetime:
    if _UK is not None:
        return naive_utc.replace(tzinfo=timezone.utc).astimezone(_UK).replace(tzinfo=None)
    return naive_utc


@app.get("/api/fixtures/upcoming")
def upcoming_fixtures(days: int = 7, limit: int = 40):
    """Real upcoming matches from football-data.co.uk, the same feed our match
    history comes from, so every team name maps straight onto the model.
    Kick-offs are ISO-8601 WITH a UTC offset (UK wall time), so clients can show
    the viewer's local time with a plain Date parse; the first 16 characters
    still read as UK time for older clients that slice the string. A match whose
    kick-off time is not published yet carries the date at 00:00 and
    time_tbc: true; no time is ever invented."""
    _require_store()
    days = max(1, min(days, 14))
    limit = max(1, min(limit, 60))
    hit = _fixtures_cache.get(days)
    if hit and time.time() - hit[0] < _FIXTURES_TTL:
        _, fixtures, note = hit
    else:
        fixtures, note = _build_fixtures(days)
        if fixtures:   # a total source blackout shouldn't pin an empty rail for the TTL
            _fixtures_cache[days] = (time.time(), fixtures, note)
    now_utc = datetime.now(timezone.utc)
    for f in fixtures:         # the in-play flag must not go stale with the cache
        if f["time_tbc"]:
            continue
        try:
            f["kicked_off"] = datetime.fromisoformat(f["kickoff"]) <= now_utc
        except (TypeError, ValueError):
            pass
    return {"fixtures": fixtures[:limit], "count": len(fixtures), "note": note}


def _build_fixtures(days: int) -> tuple[list[dict], str]:
    try:
        r = requests.get("https://www.football-data.co.uk/fixtures.csv",
                         headers=UA, timeout=12)
        r.raise_for_status()
        text = r.content.decode("utf-8-sig", errors="replace")
    except Exception:  # noqa: BLE001
        # the primary feed being down must not empty the rail: treat it like a
        # quiet week and let the FPL + league-schedule fallbacks fill in
        text = ""

    by_name = {reg["nk"]: tid for tid, reg in store.registry.items() if reg["scope"] == "club"}
    now = (datetime.now(_UK).replace(tzinfo=None) if _UK is not None
           else datetime.now(timezone.utc).replace(tzinfo=None))
    horizon = now + timedelta(days=days)
    out: list[dict] = []
    by_pair: dict[tuple, dict] = {}   # (home_id, away_id): a rescheduled match shows once

    def _push(hid, aid, ko_london, league, country, odds, rank, time_tbc=False):
        cur = by_pair.get((hid, aid))
        if cur is not None:
            if odds and not cur.get("odds"):
                cur["odds"] = odds                       # a later source adds prices only
            if cur["time_tbc"] and not time_tbc:         # ...or the kick-off time
                cur.update(kickoff=_iso_uk(ko_london), time_tbc=False,
                           kicked_off=ko_london <= now)
            return
        by_pair[(hid, aid)] = {
            "home_id": hid, "away_id": aid,
            "home": store.registry[hid]["name"], "away": store.registry[aid]["name"],
            "home_elo": store.registry[hid]["elo_global"],
            "away_elo": store.registry[aid]["elo_global"],
            "league": league, "country": country,
            "kickoff": _iso_uk(ko_london),
            "time_tbc": time_tbc,
            "kicked_off": (not time_tbc) and ko_london <= now,
            "odds": odds, "rank": rank,
        }
        out.append(by_pair[(hid, aid)])

    # ---- live layer: the odds service's events listing. Bookmaker-grade,
    # continuously updated, and its events endpoint costs ZERO quota credits,
    # so the rail is genuinely live even when the historical feed is quiet/down.
    live_n = 0
    odds_key = _resolve_key("")
    if odds_key:
        from .bestbets import LEAGUE_TO_SPORT, resolve_team
        from .bestbets import BASE as ODDS_BASE
        # the single league table: code -> (sport_key, league_name, country, rank)
        for code, (sport, lgname, country, rank) in LEAGUE_TO_SPORT.items():
            try:
                r2 = requests.get(f"{ODDS_BASE}/sports/{sport}/events",
                                  params={"apiKey": odds_key}, timeout=8)
                if r2.status_code != 200:
                    continue
                for ev in r2.json() or []:
                    ct = ev.get("commence_time")
                    if not ct:
                        continue
                    kod = _utc_to_london(datetime.strptime(ct[:16], "%Y-%m-%dT%H:%M"))
                    if kod < now - timedelta(hours=2) or kod > horizon:
                        continue
                    hid = resolve_team(store, ev.get("home_team") or "", "club", code)
                    aid = resolve_team(store, ev.get("away_team") or "", "club", code)
                    if not hid or not aid or hid == aid:
                        continue
                    # bookmakers file cup ties under the league key: if either side
                    # is not one of this league's clubs, say so instead of lying
                    in_league = all(store.registry[t]["league"] == code for t in (hid, aid))
                    before = len(out)
                    if in_league:
                        _push(hid, aid, kod, lgname, country, None, rank)
                    else:
                        _push(hid, aid, kod, "Other competition", country, None, 15)
                    live_n += len(out) - before
            except Exception:  # noqa: BLE001
                continue

    # ---- the schedule file: confirmed fixtures, with the market's average odds
    n_before_schedule = len(out)
    for row in csv.DictReader(io.StringIO(text)):
        div, hn, an = row.get("Div"), row.get("HomeTeam"), row.get("AwayTeam")
        if not div or not hn or not an:
            continue
        hhmm = (row.get("Time") or "").strip()
        tbc = not hhmm
        try:
            ko = datetime.strptime(f"{row['Date']} {hhmm or '00:00'}", "%d/%m/%Y %H:%M")
        except (ValueError, KeyError):
            continue
        # a match with no published time stays listed for the whole of its day
        cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0) if tbc \
            else now - timedelta(hours=2)
        if ko < cutoff or ko > horizon:
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
        _push(hid, aid, ko, league, country, odds, DIV_RANK.get(div, 20), time_tbc=tbc)
    primary_n = len(out) - n_before_schedule

    have_pl = any(f["league"] == "Premier League" for f in out)
    note = ("Live listings straight from the sportsbooks, refreshed every half hour."
            if live_n else "Confirmed fixtures from the leagues this model is built on.")
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
                if kod > horizon:
                    continue
                th, ta = tmap.get(f["team_h"]), tmap.get(f["team_a"])
                if not th or not ta or not th["registry_id"] or not ta["registry_id"]:
                    continue
                _push(th["registry_id"], ta["registry_id"], kod,
                      "Premier League", "England", None, 1)
        except Exception:  # noqa: BLE001
            pass
    if len(out) < 5:
        # other big leagues: the free schedule source lists the next confirmed
        # match per league: thin, but keeps the rail worldwide between rounds
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
                r2 = requests.get("https://www.thesportsdb.com/api/v1/json/123/eventsnextleague.php",
                                  params={"id": lgid}, headers=UA, timeout=8)
                for ev in (r2.json() or {}).get("events") or []:
                    ts = ev.get("strTimestamp")
                    if not ts:
                        continue
                    kod = _utc_to_london(datetime.strptime(ts[:16], "%Y-%m-%dT%H:%M"))
                    if kod < now - timedelta(hours=2) or kod > horizon:
                        continue
                    hid = _map_club(ev.get("strHomeTeam") or "")
                    aid = _map_club(ev.get("strAwayTeam") or "")
                    if not hid or not aid:
                        continue
                    _push(hid, aid, kod, lgname, country, None, 2)
            except Exception:  # noqa: BLE001
                continue
    if len(out) > primary_n + live_n:
        note += " Confirmed league schedules fill the remaining gaps."
    if any(f["time_tbc"] for f in out):
        note += " Matches marked TBC have no published kick-off time yet."
    out.sort(key=lambda f: (f["kickoff"], f["rank"]))   # soonest first, all leagues mixed
    return out, note
