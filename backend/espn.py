"""ESPN's public site API: the one live source for squads, injuries, scores and
schedules across football and basketball. No key, no quota, current to the day.

Everything here is a thin cached fetch; the callers decide what to do with it.
"""
from __future__ import annotations

import datetime as dt
import threading
import time

import requests

SITE = "https://site.api.espn.com/apis/site/v2/sports"
CORE = "https://sports.core.api.espn.com/v2/sports"
UA = {"User-Agent": "Mozilla/5.0 (Plus100)"}

_cache: dict = {}
_lock = threading.Lock()


def get(url: str, ttl: float, params: dict | None = None, timeout: int = 12):
    """GET + JSON with a TTL cache. Failures are not cached, so a blip retries."""
    key = (url, tuple(sorted((params or {}).items())))
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        r = requests.get(url, params=params, headers=UA, timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
    except (requests.RequestException, ValueError):
        return None
    with _lock:
        _cache[key] = (time.time(), data)
    return data


# ---------------------------------------------------------------- football
# our league codes -> ESPN league slugs
SOCCER_SLUGS = {
    "E0": "eng.1", "E1": "eng.2", "SP1": "esp.1", "I1": "ita.1", "D1": "ger.1",
    "F1": "fra.1", "N1": "ned.1", "P1": "por.1", "T1": "tur.1", "SC0": "sco.1",
    "B1": "bel.1", "G1": "gre.1", "USA": "usa.1", "BRA": "bra.1", "MEX": "mex.1",
}
_POS = {"goalkeeper": "GK", "defender": "DEF", "midfielder": "MID",
        "forward": "FWD", "attacker": "FWD"}


def _team_list(slug: str) -> list[dict]:
    d = get(f"{SITE}/soccer/{slug}/teams", ttl=86400)
    try:
        return [t["team"] for t in d["sports"][0]["leagues"][0]["teams"]]
    except (KeyError, IndexError, TypeError):
        return []


def soccer_team_index(store, code: str) -> dict:
    """registry id -> ESPN team id for one of our league codes (daily cache)."""
    slug = SOCCER_SLUGS.get(code)
    if not slug:
        return {}
    key = ("idx", code)
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < 86400:
        return hit[1]
    from .bestbets import resolve_team     # league-aware name matching
    idx = {}
    for t in _team_list(slug):
        tid = resolve_team(store, t.get("displayName") or t.get("name") or "", "club", code)
        if tid and tid not in idx:
            idx[tid] = t["id"]
    if idx:
        with _lock:
            _cache[key] = (time.time(), idx)
    return idx


def _athletes(d) -> list[dict]:
    out = []
    for a in (d or {}).get("athletes") or []:
        pos = ((a.get("position") or {}).get("name") or "").lower()
        bucket = next((b for k, b in _POS.items() if k in pos), None)
        if not bucket:
            continue
        out.append({
            "name": a.get("displayName"), "pos": (a.get("position") or {}).get("name"),
            "bucket": bucket,
            "img": (a.get("headshot") or {}).get("href") if isinstance(a.get("headshot"), dict) else None,
            "injured": bool(a.get("injuries")) or (a.get("status") or {}).get("name") not in (None, "Active"),
            "jersey": a.get("jersey"),
        })
    return out


def soccer_roster(store, tid: str) -> list[dict]:
    """Current squad for a club in a covered league, with positions, photos
    and the player's own injury flag."""
    reg = store.registry.get(tid) or {}
    code = reg.get("league")
    espn_id = soccer_team_index(store, code).get(tid)
    if not espn_id:
        return []
    return _athletes(get(f"{SITE}/soccer/{SOCCER_SLUGS[code]}/teams/{espn_id}/roster", ttl=6 * 3600))


def intl_roster(team_name: str) -> list[dict]:
    """Current national squad (World Cup-cycle teams) by country name."""
    from .data_store import norm_key
    teams = {norm_key(t.get("displayName") or ""): t["id"]
             for t in _team_list("fifa.world")}
    espn_id = teams.get(norm_key(team_name))
    if not espn_id:
        return []
    return _athletes(get(f"{SITE}/soccer/fifa.world/teams/{espn_id}/roster", ttl=6 * 3600))


def soccer_scores(code: str, day: dt.date) -> list[dict]:
    """Finished matches on one day: [{home, away, hs, as}] with ESPN names."""
    slug = SOCCER_SLUGS.get(code)
    if not slug:
        return []
    d = get(f"{SITE}/soccer/{slug}/scoreboard", ttl=1800, params={"dates": day.strftime("%Y%m%d")})
    out = []
    for e in (d or {}).get("events") or []:
        c = e["competitions"][0]
        if not c["status"]["type"].get("completed"):
            continue
        try:
            h = next(x for x in c["competitors"] if x["homeAway"] == "home")
            a = next(x for x in c["competitors"] if x["homeAway"] == "away")
            out.append({"date": e["date"][:10], "home": h["team"]["displayName"],
                        "away": a["team"]["displayName"],
                        "hs": int(h["score"]), "as": int(a["score"])})
        except (StopIteration, KeyError, ValueError, TypeError):
            continue
    return out


# ---------------------------------------------------------------- basketball
NBA = f"{SITE}/basketball/nba"


def nba_teams() -> list[dict]:
    d = get(f"{NBA}/teams", ttl=86400)
    out = []
    try:
        for t in d["sports"][0]["leagues"][0]["teams"]:
            t = t["team"]
            out.append({"id": t["id"], "abbr": t["abbreviation"], "name": t["displayName"],
                        "short": t.get("shortDisplayName"), "color": t.get("color"),
                        "alt": t.get("alternateColor"),
                        "logo": (t.get("logos") or [{}])[0].get("href")})
    except (KeyError, IndexError, TypeError):
        pass
    return out


def nba_scoreboard(day: dt.date, ttl: float = 1800) -> list[dict]:
    """All games on a day (scheduled, live or final)."""
    d = get(f"{NBA}/scoreboard", ttl=ttl, params={"dates": day.strftime("%Y%m%d")})
    out = []
    for e in (d or {}).get("events") or []:
        c = e["competitions"][0]
        try:
            h = next(x for x in c["competitors"] if x["homeAway"] == "home")
            a = next(x for x in c["competitors"] if x["homeAway"] == "away")
        except StopIteration:
            continue
        st = c["status"]["type"]
        out.append({
            "id": e["id"], "date": e["date"], "stype": (e.get("season") or {}).get("type"),
            "home": h["team"]["displayName"], "home_abbr": h["team"].get("abbreviation"),
            "away": a["team"]["displayName"], "away_abbr": a["team"].get("abbreviation"),
            "hs": int(h["score"]) if st.get("completed") else None,
            "as": int(a["score"]) if st.get("completed") else None,
            "final": bool(st.get("completed")), "live": st.get("state") == "in",
            "neutral": bool(c.get("neutralSite")),
            "odds": (c.get("odds") or [{}])[0].get("details"),
            "ou": (c.get("odds") or [{}])[0].get("overUnder"),
        })
    return out


def nba_injuries() -> dict:
    """team display name -> [{player, status, detail}] from ESPN's injury report."""
    d = get(f"{NBA}/injuries", ttl=1800)
    out = {}
    for t in (d or {}).get("injuries") or []:
        out[t.get("displayName")] = [
            {"player": i["athlete"]["displayName"], "status": i.get("status"),
             "detail": (i.get("details") or {}).get("type") or (i.get("shortComment") or "")[:60]}
            for i in t.get("injuries") or [] if i.get("athlete")]
    return out


def nba_roster(team_id: str) -> list[dict]:
    d = get(f"{NBA}/teams/{team_id}/roster", ttl=6 * 3600)
    return [{"id": a.get("id"), "name": a.get("displayName"),
             "pos": (a.get("position") or {}).get("abbreviation"),
             "img": (a.get("headshot") or {}).get("href") if isinstance(a.get("headshot"), dict) else None,
             "status": (a.get("status") or {}).get("name"),
             "injured": bool(a.get("injuries"))}
            for a in (d or {}).get("athletes") or []]


def nba_leaders(team_id: str, season: int) -> dict:
    """athlete id -> points per game for a season (regular season)."""
    d = get(f"{CORE}/basketball/leagues/nba/seasons/{season}/types/2/teams/{team_id}/leaders",
            ttl=6 * 3600)
    out = {}
    for c in (d or {}).get("categories") or []:
        if c.get("name") != "pointsPerGame":
            continue
        for l in c.get("leaders") or []:
            ref = (l.get("athlete") or {}).get("$ref", "")
            aid = ref.rsplit("/", 1)[-1].split("?")[0]
            if aid:
                out[aid] = float(l.get("value") or 0)
    return out


def nba_schedule(team_id: str) -> list[dict]:
    d = get(f"{NBA}/teams/{team_id}/schedule", ttl=6 * 3600)
    return [{"id": e["id"], "date": e["date"], "name": e.get("shortName"),
             "stype": (e.get("seasonType") or {}).get("type"),
             "done": e["competitions"][0]["status"]["type"].get("completed")}
            for e in (d or {}).get("events") or []]
