"""Fantasy Premier League integration.

Expected FPL points (xPts) per player for the next gameweek, built on the same
match model as everything else:
  - our Dixon-Coles model supplies each fixture's expected goals and clean-sheet
    probability against the SPECIFIC opponent
  - the FPL API supplies each player's price, role, availability, minutes and
    per-90 expected stats (xG, xA)
  - a player's expected output = his share of his team's attacking pool, scaled
    by what our model expects his team to produce in this exact fixture

Honest limits: player-vs-player marking data does not exist publicly; xPts is
opponent-adjusted, not man-marking-adjusted. Early-season numbers lean on last
season's rates until fresh minutes arrive.
"""
from __future__ import annotations

import time

import numpy as np
import requests

from .data_store import Store, norm_key
from .model import expected_goals, score_matrix

BASE = "https://fantasy.premierleague.com/api"
UA = {"User-Agent": "Mozilla/5.0 (Plus100)"}

_cache: dict = {}
TTL = 1800  # 30 min

# FPL short names -> our registry ids where normalization differs
FPL_TEAM_ALIASES = {
    "manutd": "man-united", "mancity": "man-city", "spurs": "tottenham",
    "nottmforest": "nott-m-forest", "sheffieldutd": "sheffield-united",
}
POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
GOAL_PTS = {1: 10, 2: 6, 3: 5, 4: 4}
CS_PTS = {1: 4, 2: 4, 3: 1, 4: 0}


def _get(url: str):
    key = ("http", url)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    data = r.json()
    _cache[key] = (time.time(), data)
    return data


def _team_map(store: Store, teams: list) -> dict:
    out = {}
    for t in teams:
        k = norm_key(t["name"])
        tid = FPL_TEAM_ALIASES.get(k)
        if not tid:
            for rid, r in store.registry.items():
                if r["scope"] == "club" and r["active"] and norm_key(r["name"]) == k:
                    tid = rid
                    break
        out[t["id"]] = {"fpl": t, "registry_id": tid}
    return out


def _fixture_model(store: Store, hid: str, aid: str) -> dict:
    eg = expected_goals(store, hid, aid, neutral=False)
    mat = score_matrix(eg["lambda_home"], eg["lambda_away"])
    return {
        "xg_home": round(eg["lambda_home"], 2), "xg_away": round(eg["lambda_away"], 2),
        "cs_home": round(float(mat[:, 0].sum()), 3),   # away scores 0
        "cs_away": round(float(mat[0, :].sum()), 3),   # home scores 0
    }


def next_gameweek(store: Store) -> dict:
    boot = _get(f"{BASE}/bootstrap-static/")
    events = boot["events"]
    nxt = next((e for e in events if e.get("is_next")), None) or \
          next((e for e in events if not e.get("finished")), None)
    if not nxt:
        return {"error": "season_over", "detail": "No upcoming gameweek found."}
    gw = nxt["id"]
    fixtures = [f for f in _get(f"{BASE}/fixtures/?event={gw}") if not f.get("finished")]
    tmap = _team_map(store, boot["teams"])

    # per-fixture model numbers (team goals + clean sheets vs the actual opponent)
    fixture_ctx: dict[int, dict] = {}   # fpl team id -> context
    fixtures_out = []
    for f in fixtures:
        th, ta = tmap.get(f["team_h"]), tmap.get(f["team_a"])
        if not th or not ta or not th["registry_id"] or not ta["registry_id"]:
            continue
        fm = _fixture_model(store, th["registry_id"], ta["registry_id"])
        fixture_ctx[f["team_h"]] = {"team_xg": fm["xg_home"], "opp_xg": fm["xg_away"],
                                    "p_cs": fm["cs_home"], "opp": ta["fpl"]["short_name"],
                                    "home": True}
        fixture_ctx[f["team_a"]] = {"team_xg": fm["xg_away"], "opp_xg": fm["xg_home"],
                                    "p_cs": fm["cs_away"], "opp": th["fpl"]["short_name"],
                                    "home": False}
        fixtures_out.append({
            "home": th["fpl"]["name"], "away": ta["fpl"]["name"],
            "kickoff": f.get("kickoff_time"), **fm,
        })

    # attacking pools per team (top players by minutes define the XI-ish pool).
    # Early in a season everyone's rates are one game old, so shares also lean on
    # a price prior (what the market thinks of the player) that fades out as real
    # minutes arrive — about five full gameweeks to fully trust the rates.
    els = boot["elements"]
    gws_played = max(0, (gw or 1) - 1)
    max_mins = max((e["minutes"] for e in els), default=0)
    maturity = min(max_mins / (90.0 * 5), 1.0)
    pools: dict[int, dict] = {}
    for team_id in fixture_ctx:
        squad = [e for e in els if e["team"] == team_id]
        squad.sort(key=lambda e: (-e["minutes"], -e["now_cost"]))
        xi = [e for e in squad[:14] if e["minutes"] > 0] or squad[:11]
        pool_g = sum(_per90(e, "expected_goals") for e in xi) or 1e-9
        pool_a = sum(_per90(e, "expected_assists") for e in xi) or 1e-9
        outf = [e for e in xi if e["element_type"] != 1]
        minp = min((e["now_cost"] for e in outf), default=40)
        pr = {e["id"]: max(e["now_cost"] - minp, 0) ** 1.3 for e in outf}
        pools[team_id] = {"g": pool_g, "a": pool_a, "pr": pr,
                          "prsum": sum(pr.values()) or 1e-9,
                          "maturity": maturity, "gws": gws_played}

    players = []
    for e in els:
        ctx = fixture_ctx.get(e["team"])
        if not ctx:
            continue
        xp = _xpts(e, ctx, pools[e["team"]])
        if xp is None:
            continue
        players.append({
            "id": e["id"], "name": e["web_name"],
            "photo": f"https://resources.premierleague.com/premierleague/photos/players/250x250/p{e['code']}.png",
            "team": tmap[e["team"]]["fpl"]["short_name"], "opp": ctx["opp"],
            "home": ctx["home"], "pos": POS[e["element_type"]],
            "price": e["now_cost"] / 10, "owned_pct": float(e["selected_by_percent"] or 0),
            "status": e["status"], "news": (e.get("news") or "")[:90],
            "xpts": xp["total"], "breakdown": xp["parts"],
            "value": round(xp["total"] / (e["now_cost"] / 10), 3),
        })
    players.sort(key=lambda p: -p["xpts"])

    return {
        "gameweek": gw, "name": nxt["name"], "deadline": nxt["deadline_time"],
        "fixtures": fixtures_out,
        "players": players[:250],
        "preseason": all(e["minutes"] == 0 for e in els[:50]) is False and gw == 1,
        "note": ("Expected points for the next gameweek, adjusted for each player's actual "
                 "opponent using the match model. Early in the season these lean on last "
                 "season's playing time and output; they sharpen as minutes accumulate."),
    }


def club_squad(store: Store, registry_id: str) -> list[dict]:
    """Full CURRENT squad for a Premier League club from the FPL API — complete,
    transfer-aware, with availability flags and photos. Empty list for non-PL teams."""
    try:
        boot = _get(f"{BASE}/bootstrap-static/")
    except Exception:  # noqa: BLE001
        return []
    tmap = _team_map(store, boot["teams"])
    fpl_id = next((fid for fid, v in tmap.items() if v["registry_id"] == registry_id), None)
    if fpl_id is None:
        return []
    out = []
    for e in boot["elements"]:
        if e["team"] != fpl_id:
            continue
        if e["status"] in ("i", "s", "u", "n"):     # injured / suspended / gone
            continue
        out.append({
            "name": e["web_name"],
            "bucket": POS[e["element_type"]],
            "pos": {"GK": "Goalkeeper", "DEF": "Defender",
                    "MID": "Midfielder", "FWD": "Forward"}[POS[e["element_type"]]],
            "minutes": e["minutes"], "price": e["now_cost"] / 10,
            "sel": float(e["selected_by_percent"] or 0),
            "img": f"https://resources.premierleague.com/premierleague/photos/players/250x250/p{e['code']}.png",
        })
    return out


def _per90(e: dict, field: str) -> float:
    """Per-90 rate, shrunk toward zero on thin samples instead of zeroed out —
    vital in the first weeks of a season when everyone has one game played."""
    mins = e["minutes"]
    if mins <= 0:
        return 0.0
    try:
        v = float(e.get(field) or 0) / mins * 90
    except (TypeError, ValueError):
        return 0.0
    return v * min(mins / 400.0, 1.0)


def _num(x, d: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


# Fitted on 734 real season-to-season player pairs (scripts/fpl_quality2.py):
# predicting a player's NEXT season from the one before, the xG-driven projection
# alone ranks players at 0.38, his realised points rate alone at 0.53, and this
# blend at 0.54 — the best of the three. Weights swept, not guessed.
FORM_WEIGHT = 0.60
FORM_CONF_MINUTES = 900.0         # minutes before the realised rate is fully trusted


def _xpts(e: dict, ctx: dict, pool: dict):
    """Expected FPL points for one player in one fixture."""
    mins = e["minutes"]
    status = e["status"]
    if status in ("i", "s", "u", "n"):        # injured / suspended / unavailable
        return None
    # chance of featuring: starts matter more than raw minutes (a regular starter
    # who missed a month still starts; a super-sub with many minutes may not)
    starts = e.get("starts") or 0
    gws = max(pool.get("gws", 1), 1)
    if mins > 0:
        p_play = min(0.95, max(0.15, 0.7 * min(starts / gws, 1.0)
                               + 0.3 * min(mins / (90.0 * gws), 1.0)))
    else:
        p_play = 0.2
    chance = e.get("chance_of_playing_next_round")
    if chance is not None:                    # respect flags on every status, not just "doubtful"
        p_play *= chance / 100
    p60 = p_play * 0.85

    pos = e["element_type"]
    mat = pool.get("maturity", 1.0)
    prior = min(pool.get("pr", {}).get(e["id"], 0.0) / pool.get("prsum", 1e-9), 0.45)
    share_g = mat * min(_per90(e, "expected_goals") / pool["g"], 0.45) + (1 - mat) * prior
    share_a = mat * min(_per90(e, "expected_assists") / pool["a"], 0.45) + (1 - mat) * prior
    e_goals = ctx["team_xg"] * share_g * p_play
    e_assists = ctx["team_xg"] * 0.72 * share_a * p_play   # ~72% of goals are assisted

    appearance = p_play + p60
    goals_pts = e_goals * GOAL_PTS[pos]
    assist_pts = e_assists * 3
    cs_pts = CS_PTS[pos] * ctx["p_cs"] * p60
    conceded_pen = -(ctx["opp_xg"] / 2) * p60 if pos in (1, 2) else 0.0
    games = max(mins / 90, 1)
    bonus = (e.get("bonus", 0) / games) * p_play
    saves = (e.get("saves", 0) / games) / 3 * p60 if pos == 1 else 0.0
    cards = -0.12 * p_play

    total = appearance + goals_pts + assist_pts + cs_pts + conceded_pen + bonus + saves + cards

    # Anchor on what the player has actually scored, adjusted for this fixture.
    # Attackers ride their team's expected goals; keepers and defenders ride the
    # clean-sheet chance. Both are expressed relative to an average fixture.
    ppg = _num(e.get("points_per_game"))
    form_pts = 0.0
    if ppg > 0 and mins > 0:
        if pos in (1, 2):
            fixture = 0.55 + 0.45 * (ctx["p_cs"] / 0.26)
        else:
            fixture = 0.55 + 0.45 * (ctx["team_xg"] / 1.45)
        fixture = max(0.55, min(1.6, fixture))
        form_pts = ppg * p_play * fixture
        w = FORM_WEIGHT * min(mins / FORM_CONF_MINUTES, 1.0)
        blended = (1 - w) * total + w * form_pts
    else:
        w, blended = 0.0, total

    return {
        "total": round(blended, 2),
        "parts": {
            "appearance": round(appearance * (1 - w), 2),
            "goals": round(goals_pts * (1 - w), 2),
            "assists": round(assist_pts * (1 - w), 2),
            "clean_sheet": round(cs_pts * (1 - w), 2),
            "bonus": round((bonus + saves) * (1 - w), 2),
            "other": round((conceded_pen + cards) * (1 - w), 2),
            "his_scoring_record": round(form_pts * w, 2),
        },
    }


def entry_analysis(store: Store, entry_id: int) -> dict:
    """Analyze a user's actual FPL squad (works once the season has started)."""
    gw_data = next_gameweek(store)
    if gw_data.get("error"):
        return gw_data
    gw = gw_data["gameweek"]
    try:
        entry = _get(f"{BASE}/entry/{entry_id}/")
    except requests.HTTPError:
        return {"error": "entry_not_found",
                "detail": "Couldn't find that FPL team ID. It's the number in your team's "
                          "URL on fantasy.premierleague.com."}
    picks = None
    for g in (gw - 1, gw):     # last saved squad
        try:
            picks = _get(f"{BASE}/entry/{entry_id}/event/{g}/picks/")
            break
        except requests.HTTPError:
            continue
    if not picks:
        return {"error": "no_picks",
                "detail": "Your squad isn't visible yet. FPL publishes squads once the "
                          "season starts, so check back after Gameweek 1 kicks off.",
                "entry_name": entry.get("name")}

    by_id = {p["id"]: p for p in gw_data["players"]}
    squad = []
    for pk in picks["picks"]:
        pl = by_id.get(pk["element"])
        if pl:
            squad.append({**pl, "is_captain": pk["is_captain"],
                          "multiplier": pk["multiplier"]})
    squad.sort(key=lambda p: -p["xpts"])
    best_captain = squad[0] if squad else None
    starters = sorted(squad, key=lambda p: -p["xpts"])[:11]
    xi_total = round(sum(p["xpts"] for p in starters) +
                     (best_captain["xpts"] if best_captain else 0), 1)
    weakest = min(starters, key=lambda p: p["xpts"]) if starters else None
    upgrades = []
    if weakest:
        upgrades = [p for p in gw_data["players"]
                    if p["pos"] == weakest["pos"] and p["price"] <= weakest["price"] + 0.5
                    and p["xpts"] > weakest["xpts"] + 0.5
                    and p["id"] not in {q["id"] for q in squad}][:3]
    # One free transfer per gameweek is the rule; extras cost 4 points. So the
    # advice engine scans every legal, affordable swap and recommends AT MOST one,
    # and only when the projected gain clearly beats doing nothing.
    bank = (picks.get("entry_history") or {}).get("bank", 0) / 10.0
    squad_ids = {p["id"] for p in squad}
    best = None
    for out_p in squad:
        for cand in gw_data["players"]:
            if cand["pos"] != out_p["pos"] or cand["id"] in squad_ids:
                continue
            if cand["price"] > out_p["price"] + bank + 1e-9:     # money restriction
                continue
            others = [q for q in squad if q["id"] != out_p["id"]]
            if sum(1 for q in others if q["team"] == cand["team"]) >= 3:
                continue                                          # 3-per-club rule
            gain = cand["xpts"] - out_p["xpts"]
            if best is None or gain > best["gain"]:
                best = {"out": out_p["name"], "out_xpts": out_p["xpts"],
                        "in": cand["name"], "in_xpts": cand["xpts"],
                        "gain": round(gain, 2),
                        "cost_delta": round(cand["price"] - out_p["price"], 1)}
    GAIN_BAR = 0.7      # below this, projection noise; keep the free transfer banked
    if best and best["gain"] >= GAIN_BAR:
        advice = {"action": "transfer", **best,
                  "reason": (f"Use your free transfer: {best['out']} "
                             f"({best['out_xpts']} xPts) out, {best['in']} "
                             f"({best['in_xpts']} xPts) in — {best['gain']} more projected "
                             f"points this week, within your budget (£{bank:.1f}m banked). "
                             "Only one transfer is free; extras cost 4 points, so no "
                             "second change is ever suggested.")}
    else:
        advice = {"action": "hold",
                  "reason": ((f"The best affordable swap gains only "
                              f"{best['gain']:.1f} projected points — inside projection "
                              "noise. ") if best else
                             "No affordable, rule-legal upgrade exists this week. ")
                            + "Bank the free transfer; it rolls over."}
    return {
        "entry_name": entry.get("name"), "manager": f'{entry.get("player_first_name", "")} {entry.get("player_last_name", "")}'.strip(),
        "gameweek": gw, "squad": squad,
        "best_captain": best_captain["name"] if best_captain else None,
        "projected_points": xi_total,
        "weakest_starter": weakest["name"] if weakest else None,
        "upgrade_ideas": upgrades,
        "bank": bank,
        "transfer_advice": advice,
        "advice_note": ("Prices here are current buy prices; your personal selling "
                        "prices can differ by up to £0.5m, so double-check in the "
                        "official app before confirming."),
    }
