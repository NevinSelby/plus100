"""Live xG strengths and player scoring rates, straight from understat.

Understat serves each league-season as one JSON document (the same data its
own pages render): every team's per-match xG for and against, plus every
player's season totals. Two small requests per league replace the old
export-shot-files-by-hand pipeline, and because this runs inside the regular
data refresh the numbers move with the season instead of freezing at whenever
someone last regenerated an artifact.

The output artifact keeps the exact shape the model already consumes:
  xg_attack / xg_defence  team multipliers relative to league average
  player_rates            per team: recent scorers with their share of team xG
  player_xa               per team: {player: share of team xA}, same shrinkage
  xa_per_xg               measured league-wide xA per xG over the loaded leagues
  xg_data_to              freshness stamp shown in the UI

A partial understat answer (any league-season missing) keeps the previous
artifact rather than publishing strengths with holes in them.
"""
from __future__ import annotations

import datetime as dt
import gzip
import html
import json

import requests

from .data_store import DATA, UNDERSTAT_ALIASES, norm_key

ENDPOINT = "https://understat.com/getLeagueData/{league}/{season}"
HEADERS = {"X-Requested-With": "XMLHttpRequest",       # without it: 404
           "User-Agent": "Mozilla/5.0 (Plus100)"}
LEAGUES = {"EPL": "E0", "La_liga": "SP1", "Bundesliga": "D1",
           "Serie_A": "I1", "Ligue_1": "F1", "RFPL": "RUS"}
ARTIFACT = DATA / "xg_precomputed.json.gz"

HALF_LIFE_DAYS = 500.0          # how fast old matches fade from the strengths
PRIOR_MATCHES = 8.0             # shrink thin samples toward league average
PRIOR_RATE, PRIOR_APPS = 0.08, 4.0   # xG per match prior for scorer rates
PREV_SEASON_WEIGHT = 0.4        # last season's player totals vs this season's
SETTLED_GAMES = 5               # after this many games, absent players are gone


def _season_start(today: dt.date) -> int:
    return today.year if today.month >= 7 else today.year - 1


def _fetch(league: str, season: int) -> dict | None:
    try:
        r = requests.get(ENDPOINT.format(league=league, season=season),
                         headers=HEADERS, timeout=25)
        return r.json() if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def build(registry: dict, today: dt.date | None = None) -> dict | None:
    """Compute the artifact from live understat data. None if nothing loaded."""
    today = today or dt.date.today()
    cur = _season_start(today)
    club_by_key = {norm_key(r["name"]): tid for tid, r in registry.items()
                   if r["scope"] == "club"}

    def to_tid(name: str):
        k = norm_key(UNDERSTAT_ALIASES.get(str(name).lower(), "")) or norm_key(name)
        return club_by_key.get(k)

    records = []        # (league, tid, side, date, xg_for, xg_against)
    team_games = {}     # (tid, season) -> matches played
    players = {}        # tid -> {name: {pxg, pxa, w_apps, apps, current}}
    minutes = {}        # tid -> {name: weighted minutes}: who actually starts
    unmatched, missing = set(), []
    xg_total, xa_total = 0.0, 0.0      # league-wide, unweighted, for xa_per_xg
    for us_lg, code in LEAGUES.items():
        for season in (cur, cur - 1):
            d = _fetch(us_lg, season)
            if not d:
                missing.append(f"{us_lg} {season}")
                continue
            sw = 1.0 if season == cur else PREV_SEASON_WEIGHT
            for t in (d.get("teams") or {}).values():
                hist = t.get("history") or []
                xg_total += sum(_num(h.get("xG")) for h in hist)
                tid = to_tid(t.get("title", ""))
                if not tid:
                    unmatched.add(t.get("title"))
                    continue
                team_games[(tid, season)] = len(hist)
                for h in hist:
                    records.append((code, tid, h.get("h_a"), h["date"][:10],
                                    _num(h.get("xG")), _num(h.get("xGA"))))
            for p in d.get("players") or []:
                xa_total += _num(p.get("xA"))
                tid = to_tid(p.get("team_title", ""))
                if not tid:
                    continue
                name = html.unescape(p.get("player_name") or "")     # understat escapes apostrophes
                row = players.setdefault(tid, {}).setdefault(
                    name, {"pxg": 0.0, "pxa": 0.0, "w_apps": 0.0, "apps": 0, "current": False})
                games = _num(p.get("games"))
                mins = minutes.setdefault(tid, {})
                mins[name] = mins.get(name, 0.0) + sw * _num(p.get("time"))
                row["pxg"] += sw * _num(p.get("xG"))
                row["pxa"] += sw * _num(p.get("xA"))
                row["w_apps"] += sw * games
                row["apps"] += int(games)
                row["current"] |= season == cur
    if missing or not records:
        # every (league, season) pair must load, or the strengths would have holes
        print(f"[xg] understat feed incomplete, keeping the previous artifact: {missing or 'no records'}")
        return None
    xa_per_xg = xa_total / xg_total if xg_total > 0 else 0.0
    if unmatched:
        print(f"[xg] unmatched understat teams ({len(unmatched)}): {sorted(unmatched)[:12]}")

    # ---- team strengths: time-weighted xG for/against vs league average,
    # shrunk toward average on thin samples, home and away sides averaged
    latest = max(r[3] for r in records)
    end = dt.date.fromisoformat(latest)
    weighted = []
    for code, tid, side, date, xf, xa in records:
        w = 0.5 ** ((end - dt.date.fromisoformat(date)).days / HALF_LIFE_DAYS)
        if w > 0.01:
            weighted.append((code, tid, side, w, xf, xa))
    lam = {}
    for code in {r[0] for r in weighted}:
        rows = [r for r in weighted if r[0] == code]
        home = [(w, xf) for _, _, s, w, xf, _ in rows if s == "h"]
        away = [(w, xf) for _, _, s, w, xf, _ in rows if s == "a"]
        avg_h = sum(w * x for w, x in home) / max(sum(w for w, _ in home), 1e-9)
        avg_a = sum(w * x for w, x in away) / max(sum(w for w, _ in away), 1e-9)
        lam[code] = (avg_h + avg_a) / 2
    acc = {}
    for code, tid, side, w, xf, xa in weighted:
        a = acc.setdefault((tid, side), [0.0, 0.0, 0.0, code])
        a[0] += w; a[1] += w * xf; a[2] += w * xa
    att, dfc = {}, {}
    for (tid, _side), (tw, for_r, ag_r, code) in acc.items():
        l = lam[code]
        a = (for_r + l * PRIOR_MATCHES) / (tw + PRIOR_MATCHES) / l
        d = (ag_r + l * PRIOR_MATCHES) / (tw + PRIOR_MATCHES) / l
        att[tid] = (att[tid] + a) / 2 if tid in att else a
        dfc[tid] = (dfc[tid] + d) / 2 if tid in dfc else d

    # ---- player shares: shrunk xG (or xA) per appearance, scaled by
    # availability, normalised within the team. Once a season has settled,
    # players missing from this season's list are treated as departed rather
    # than carried over from last year.
    def shares(field: str, prior_rate: float) -> dict:
        out_all = {}
        for tid, rows in players.items():
            team_w = team_games.get((tid, cur), 0) + PREV_SEASON_WEIGHT * team_games.get((tid, cur - 1), 0)
            if team_w <= 0:
                continue
            settled = team_games.get((tid, cur), 0) >= SETTLED_GAMES
            out = []
            for name, r in rows.items():
                if settled and not r["current"]:
                    continue
                rate = (r[field] + prior_rate * PRIOR_APPS) / (r["w_apps"] + PRIOR_APPS)
                avail = min(1.0, r["w_apps"] / team_w)
                out.append((name, rate * avail))
            total = sum(c for _, c in out)
            if total <= 0:
                continue
            out.sort(key=lambda x: -x[1])
            out_all[tid] = [(n, round(c / total, 4)) for n, c in out[:10]]
        return out_all

    rates = {tid: [{"player": n, "xg_share": c} for n, c in v]
             for tid, v in shares("pxg", PRIOR_RATE).items()}
    # the assist prior is the scorer prior scaled by the measured xA per xG
    player_xa = {tid: dict(v) for tid, v in shares("pxa", PRIOR_RATE * xa_per_xg).items()}

    return {"xg_attack": {k: round(v, 4) for k, v in att.items()},
            "xg_defence": {k: round(v, 4) for k, v in dfc.items()},
            "player_rates": rates, "player_xa": player_xa,
            "xa_per_xg": round(xa_per_xg, 4),
            "player_minutes": {tid: {n: int(m) for n, m in rows.items() if m > 0}
                               for tid, rows in minutes.items()},
            "xg_data_to": latest, "built_at": today.isoformat()}


def refresh(registry: dict) -> bool:
    """Rebuild the artifact from live data. True if it was rewritten."""
    blob = build(registry)
    if blob is None:
        return False
    old = None
    if ARTIFACT.exists():
        with gzip.open(ARTIFACT, "rt") as fh:
            old = json.load(fh).get("xg_data_to")
    with gzip.open(ARTIFACT, "wt") as fh:
        json.dump(blob, fh)
    print(f"[xg] refreshed from understat: data to {blob['xg_data_to']} "
          f"({len(blob['player_rates'])} squads)")
    return old != blob["xg_data_to"]
