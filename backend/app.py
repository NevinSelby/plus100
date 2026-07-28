"""FastAPI app: team search, head-to-head stats, predictions, logos, Reddit buzz."""
from __future__ import annotations

import json
import os
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .data_store import get_store, norm_key
from .model import predict

ROOT = Path(__file__).resolve().parent.parent
LOGO_CACHE_FILE = ROOT / "data" / "logo_cache.json"
PLAYER_TEAM_CACHE_FILE = ROOT / "data" / "player_team_cache.json"
UA = {"User-Agent": "Mozilla/5.0 (FootballAnalytics; personal research tool)"}

app = FastAPI(title="Plus100 Football Predictor")
store = get_store()


from .refresher import REFRESH_HOURS  # noqa: E402
from .refresher import state as refresher_state  # noqa: E402


@app.on_event("startup")
def _start_refresher():
    from .refresher import start_background
    start_background()

_logo_lock = threading.Lock()
_logo_cache: dict[str, dict | str | None] = (
    json.loads(LOGO_CACHE_FILE.read_text()) if LOGO_CACHE_FILE.exists() else {}
)
_player_team_cache: dict[str, str | None] = (
    json.loads(PLAYER_TEAM_CACHE_FILE.read_text()) if PLAYER_TEAM_CACHE_FILE.exists() else {}
)
_news_cache: dict[str, tuple[float, list]] = {}


def _team_or_404(tid: str) -> dict:
    t = store.registry.get(tid)
    if not t:
        raise HTTPException(404, f"unknown team id: {tid}")
    return t


@app.get("/api/teams")
def team_search(q: str = Query("", max_length=60), limit: int = 12):
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


@app.get("/api/predict")
def predict_endpoint(home: str, away: str, neutral: bool = False,
                     out_home: str = "", out_away: str = "", context: str = "none"):
    _team_or_404(home)
    _team_or_404(away)
    if home == away:
        raise HTTPException(400, "pick two different teams")
    oh = [p.strip() for p in out_home.split("|") if p.strip()]
    oa = [p.strip() for p in out_away.split("|") if p.strip()]
    p = predict(store, home, away, neutral, out_home=oh, out_away=oa, context=context)
    _verify_squads(p)
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
    from .model import suggest_parlays
    _team_or_404(home)
    _team_or_404(away)
    return suggest_parlays(store, home, away, neutral, context=context)


@app.post("/api/parlay")
def parlay_endpoint(req: ParlayReq):
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
    if isinstance(cached, dict):
        return cached
    name = t["name"]
    entry = {"badge": cached if isinstance(cached, str) else None, "tsdb_name": None}
    try:
        r = requests.get("https://www.thesportsdb.com/api/v1/json/3/searchteams.php",
                         params={"t": name}, headers=UA, timeout=8)
        teams = (r.json() or {}).get("teams") or []
        soccer = [x for x in teams if x.get("strSport") == "Soccer"]
        if soccer:
            exact = [x for x in soccer if norm_key(x.get("strTeam", "")) == norm_key(name)]
            best = (exact or soccer)[0]
            entry = {"badge": best.get("strBadge"), "tsdb_name": best.get("strTeam")}
    except Exception:  # noqa: BLE001
        pass
    with _logo_lock:
        _logo_cache[team_id] = entry
        LOGO_CACHE_FILE.write_text(json.dumps(_logo_cache))
    return entry


@app.get("/api/logo")
def logo(team_id: str):
    return {"badge": _tsdb_team(team_id)["badge"]}


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
    from .fpl import next_gameweek
    try:
        return next_gameweek(store)
    except Exception as e:  # noqa: BLE001
        return {"error": "fpl_unavailable", "detail": str(e)[:200]}


@app.get("/api/fpl/entry/{entry_id}")
def fpl_entry(entry_id: int):
    from .fpl import entry_analysis
    try:
        return entry_analysis(store, entry_id)
    except Exception as e:  # noqa: BLE001
        return {"error": "fpl_unavailable", "detail": str(e)[:200]}


@app.get("/api/meta")
def meta():
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
