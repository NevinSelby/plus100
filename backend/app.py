"""FastAPI app: team search, head-to-head stats, predictions, logos, Reddit buzz."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .data_store import get_store, norm_key
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


_ABSENCE_WORDS = ("injur", "ruled out", "out for", "sidelined", "doubt", "suspended",
                  "banned", "surgery", "hamstring", "acl", "fracture", "misses", "miss ")


def _news_absences(tid: str, names: list[str]) -> list[str]:
    """Players from `names` who appear in recent team-news headlines next to an
    absence word (injury, suspension, ruled out …). Best-effort, cached with news."""
    try:
        items = news(tid)["items"]
    except Exception:  # noqa: BLE001
        return []
    outs = []
    for n in names:
        parts = [w for w in str(n).split() if len(w) > 3]
        if not parts:
            continue
        last = norm_key(parts[-1])
        for it in items:
            title = (it.get("title") or "").lower()
            if last in norm_key(title) and any(w in title for w in _ABSENCE_WORDS):
                outs.append(n)
                break
    return outs


def _auto_absences(tid: str) -> list[str]:
    """News-detected likely absentees among the players our model tracks."""
    reg = store.registry.get(tid, {})
    if reg.get("scope") == "club":
        names = [r["player"] for r in store.player_rates.get(tid, [])[:12]]
    else:
        sg = store.scorer_goals
        names = list(sg[sg.team == reg.get("name", "")].sort_values(
            "wgoals", ascending=False).scorer.head(10))
    return _news_absences(tid, names)


@app.get("/api/predict")
def predict_endpoint(home: str, away: str, neutral: bool = False,
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
    for team_name, outs in ((p["home"]["name"], auto_oh), (p["away"]["name"], auto_oa)):
        if outs:
            p.setdefault("caveats", []).append(
                f"Team news suggests {', '.join(outs)} may be unavailable for {team_name}; "
                "the goal expectation was reduced for the minutes they usually provide.")
    return p


from pydantic import BaseModel


class ParlayReq(BaseModel):
    home: str
    away: str
    legs: list[str]
    neutral: bool = False
    price: float = 0.0
    context: str = "none"


@app.get("/api/parlay/suggest")
def parlay_suggest(home: str, away: str, neutral: bool = False, context: str = "none"):
    _require_store()
    from .model import suggest_parlays
    _team_or_404(home)
    _team_or_404(away)
    return suggest_parlays(store, home, away, neutral, context=context)


@app.post("/api/parlay")
def parlay_endpoint(req: ParlayReq):
    _require_store()
    from .model import simulate_sgp
    _team_or_404(req.home)
    _team_or_404(req.away)
    if not req.legs:
        raise HTTPException(400, "no legs given")
    try:
        r = simulate_sgp(store, req.home, req.away, req.legs, req.neutral, req.context)
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
    items = []
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
        pass
    _news_cache[key] = (now, items)
    return {"team": key, "items": items,
            "disclaimer": "Headlines are context only; they are not part of the statistical model."}


def _tsdb_team(team_id: str) -> dict:
    """Resolve a team on TheSportsDB: badge URL + their canonical team name."""
    t = _team_or_404(team_id)
    with _logo_lock:
        cached = _logo_cache.get(team_id)
    if isinstance(cached, dict) and "colors" in cached:   # old entries lack fields: refetch
        return cached
    name = t["name"]
    entry = {"badge": cached.get("badge") if isinstance(cached, dict) else
             cached if isinstance(cached, str) else None,
             "tsdb_name": None, "fanart": None, "stadium": None, "capacity": None,
             "colors": []}
    try:
        r = requests.get("https://www.thesportsdb.com/api/v1/json/3/searchteams.php",
                         params={"t": name}, headers=UA, timeout=8)
        teams = (r.json() or {}).get("teams") or []
        soccer = [x for x in teams if x.get("strSport") == "Soccer"]
        if soccer:
            exact = [x for x in soccer if norm_key(x.get("strTeam", "")) == norm_key(name)]
            best = (exact or soccer)[0]
            entry = {"badge": best.get("strBadge"), "tsdb_name": best.get("strTeam"),
                     "fanart": best.get("strFanart1") or best.get("strBanner"),
                     "stadium": best.get("strStadium"),
                     "capacity": best.get("intStadiumCapacity"),
                     "colors": [c for c in (best.get("strColour1"), best.get("strColour2"))
                                if c and c.startswith("#") and len(c) == 7]}
    except Exception:  # noqa: BLE001
        pass
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
_SQUAD_TTL = 3 * 86400
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
        r = requests.get("https://www.thesportsdb.com/api/v1/json/3/searchteams.php",
                         params={"t": t["name"]}, headers=UA, timeout=8)
        teams = [x for x in (r.json() or {}).get("teams") or [] if x.get("strSport") == "Soccer"]
        exact = [x for x in teams if norm_key(x.get("strTeam", "")) == norm_key(t["name"])]
        best = (exact or teams)[0] if teams else None
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
            if exact and exact_only is None:
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


_ROW_CAPS = {"GK": 1, "DEF": 5, "MID": 5, "FWD": 3}


def _team_lineup(tid: str, lam: float) -> dict:
    t = _team_or_404(tid)
    reg = store.registry[tid]
    if reg.get("scope") == "club":
        scorers = likely_scorers_club(store, tid, lam)
    else:
        scorers = likely_scorers(store, t["name"], lam)
    p_by_key = {norm_key(s["player"]): s["prob_to_score"] for s in scorers}

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

    # drop players the news says are out (injury, suspension, transfer talk aside)
    outs = _news_absences(tid, [p["name"] for p in squad])
    squad = [p for p in squad if p["name"] not in outs]

    rows: dict[str, list] = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for p in squad:
        rows[p["bucket"]].append(p)
    for b, cap in _ROW_CAPS.items():
        rows[b].sort(key=lambda p: (-(p.get("p_score") or 0), p.get("img") is None,
                                    _pos_x_order(p["pos"]), p["name"]))
        rows[b] = rows[b][:cap]
        rows[b].sort(key=lambda p: (_pos_x_order(p["pos"]), p["name"]))
    # trim to 11 by dropping from the largest outfield rows first
    while sum(len(v) for v in rows.values()) > 11:
        biggest = max(("DEF", "MID", "FWD"), key=lambda b: len(rows[b]))
        rows[biggest].pop()

    players = []
    for ri, b in enumerate(("GK", "DEF", "MID", "FWD")):
        n = len(rows[b])
        for si, p in enumerate(rows[b]):
            players.append({**p, "row": ri, "slot": si, "n": n})
    return {
        "id": tid, "name": t["name"], "badge": _tsdb_team(tid)["badge"],
        "formation": "-".join(str(len(rows[b])) for b in ("DEF", "MID", "FWD")),
        "players": players,
        "known": sum(len(v) for v in rows.values()),
        "gk_missing": len(rows["GK"]) == 0,
        "outs": outs,
    }


@app.get("/api/lineup")
def lineup(home: str, away: str, neutral: bool = False):
    """Probable line-ups for a matchup: public squad data (TheSportsDB) ranked by
    our model's scoring shares. These are LIKELY players, not confirmed team sheets."""
    _require_store()
    eg = expected_goals(store, home, away, neutral=neutral)
    return {
        "home": _team_lineup(home, eg["lambda_home"]),
        "away": _team_lineup(away, eg["lambda_away"]),
        "note": ("Built from public squad data ranked by each player's share of his "
                 "team's expected goals. Real team sheets drop about an hour before "
                 "kickoff and can differ."),
    }


def _player_current_team(player: str) -> str | None:
    """Player's current club per TheSportsDB (cached on disk). None = unknown."""
    key = norm_key(player)
    if key in _player_team_cache:
        return _player_team_cache[key]
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
            return None  # rate limited: unknown, don't cache
    except Exception:  # noqa: BLE001
        return None
    _player_team_cache[key] = team
    PLAYER_TEAM_CACHE_FILE.write_text(json.dumps(_player_team_cache))
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


@app.get("/api/fpl/gw")
def fpl_gameweek():
    _require_store()
    from .fpl import next_gameweek
    try:
        return next_gameweek(store)
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
        "blend": {
            "market_weight": 0.75,
            "note": "Validated on 4,227 unseen matches: accuracy improves monotonically toward "
                    "the market (optimum 1.0), but 0.75 costs only +0.0008 Brier, within noise. "
                    "0.75 is the maximum model weight the data defends.",
        },
        "live_eval": store.live_eval,
        "context_scales": {k: v for k, v in store.context_scales.items()},
        "refresh": {
            "auto": f"every {REFRESH_HOURS} hours",
            "last": refresher_state["last_refresh"],
            "refreshing": refresher_state["refreshing"],
            "last_error": refresher_state["last_error"],
        },
    }


app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")


@app.get("/")
def index():
    # always revalidate the page itself; versioned ?v= query strings handle JS/CSS
    return FileResponse(ROOT / "frontend" / "index.html",
                        headers={"Cache-Control": "no-cache"})
