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

import json
import os
import threading
import time
from pathlib import Path

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


def club_unavailable(store: Store, registry_id: str) -> list[dict]:
    """Players flagged unavailable or doubtful by the OFFICIAL live FPL feed for a
    Premier League club: injured/suspended/unavailable status, or a stated chance
    of playing at 50% or less. Updated daily by the league itself."""
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
        chance = e.get("chance_of_playing_next_round")
        if e["status"] in ("i", "s", "u") or (chance is not None and chance <= 50):
            why = {"i": "injured", "s": "suspended", "u": "unavailable"}.get(
                e["status"], f"{chance}% chance of playing")
            out.append({"name": e["web_name"], "why": why,
                        "news": (e.get("news") or "")[:80]})
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

    # 2025/26 rule: 2 pts for reaching the defensive-contribution threshold
    # (10 actions for defenders, 12 for everyone else). Probability of reaching
    # it approximated from the player's own per-90 rate.
    dc90 = _num(e.get("defensive_contribution_per_90"))
    thr = 10.0 if pos == 2 else 12.0
    p_dc = min(max((dc90 - 0.55 * thr) / (0.9 * thr), 0.0), 0.92) if pos != 1 else 0.0
    dc_pts = 2.0 * p_dc * p60

    total = (appearance + goals_pts + assist_pts + cs_pts + conceded_pen + bonus
             + saves + cards + dc_pts)

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
            "defending": round(dc_pts * (1 - w), 2),
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


# ---------------- the persistent model team ----------------
# One shared squad that PLAYS BY THE RULES: seeded once, then at most the banked
# free transfers each gameweek (never a hit), selling-price simplification noted.
# State lives in Supabase (fpl_state) so it survives restarts; local file fallback.

_MODEL_LOCK = threading.Lock()
STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "fpl_state.json"
GAIN_BAR_MODEL = 0.7

SEED = [  # the squad the app recommended and the user actually built (GW1, £99.5m)
    ("Kelleher", "GK"), ("Virgil", "DEF"), ("O'Reilly", "DEF"), ("Tarkowski", "DEF"),
    ("Guéhi", "DEF"), ("Gibbs-White", "MID"), ("Dewsbury-Hall", "MID"), ("Bruno G.", "MID"),
    ("Haaland", "FWD"), ("Thiago", "FWD"), ("João Pedro", "FWD"),
    ("Dubravka", "GK"), ("van Ewijk", "DEF"), ("Reed", "MID"), ("Hughes", "MID"),
]
SEED_XI = ["Kelleher", "Virgil", "O'Reilly", "Tarkowski", "Guéhi",
           "Gibbs-White", "Dewsbury-Hall", "Bruno G.", "Haaland", "Thiago", "João Pedro"]
SEED_CAPTAIN, SEED_VICE = "Haaland", "Kelleher"
FORMATIONS_XI = [(3, 4, 3), (3, 5, 2), (4, 3, 3), (4, 4, 2), (4, 5, 1), (5, 3, 2), (5, 4, 1)]


def _sb_conf():
    u = os.environ.get("SUPABASE_URL", "").rstrip("/")
    k = os.environ.get("SUPABASE_SERVICE_KEY", "")
    return (u, k) if u and k else None


def _sb_h(k):
    return {"apikey": k, "Authorization": f"Bearer {k}", "Content-Type": "application/json"}


def state_get(key: str):
    conf = _sb_conf()
    if conf:
        u, k = conf
        try:
            r = requests.get(f"{u}/rest/v1/fpl_state", headers=_sb_h(k), timeout=10,
                             params={"key": f"eq.{key}", "select": "value"})
            if r.status_code == 200:
                rows = r.json()
                return rows[0]["value"] if rows else None
        except Exception:  # noqa: BLE001
            pass
    try:
        return json.loads(STATE_FILE.read_text()).get(key)
    except Exception:  # noqa: BLE001
        return None


def state_put(key: str, value) -> bool:
    saved = False
    conf = _sb_conf()
    if conf:
        u, k = conf
        try:
            r = requests.post(f"{u}/rest/v1/fpl_state", json=[{"key": key, "value": value}],
                              headers=_sb_h(k) | {"Prefer": "resolution=merge-duplicates,return=minimal"},
                              timeout=10)
            saved = r.status_code in (200, 201, 204)
        except Exception:  # noqa: BLE001
            pass
    try:
        d = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
        d[key] = value
        STATE_FILE.write_text(json.dumps(d))
    except Exception:  # noqa: BLE001
        pass
    return saved


def _round_points(pid: int, rnd: int):
    """(points, minutes) one player scored in one finished round, or None."""
    try:
        hist = _get(f"{BASE}/element-summary/{pid}/").get("history") or []
    except Exception:  # noqa: BLE001
        return None
    rows = [h for h in hist if h.get("round") == rnd]
    if not rows:
        return (0, 0)
    return (sum(h.get("total_points", 0) for h in rows),
            sum(h.get("minutes", 0) for h in rows))


def _score_round(xi_ids: list, captain: int, vice: int, rnd: int):
    total, cap_played = 0, False
    details = {}
    for pid in xi_ids:
        rp = _round_points(pid, rnd)
        if rp is None:
            return None
        pts, mins = rp
        details[pid] = pts
        total += pts
        if pid == captain and mins > 0:
            cap_played = True
    if cap_played:
        total += details.get(captain, 0)
    elif vice in details:
        total += details[vice]                    # armband passes to the vice
    return total


def _best_xi(squad: list, xp: dict):
    """Best legal XI (+captain/vice) from the 15 by expected points."""
    by = {"GK": [], "DEF": [], "MID": [], "FWD": []}
    for p in squad:
        by[p["pos"]].append(p)
    for b in by:
        by[b].sort(key=lambda p: -xp.get(p["id"], 0))
    best = None
    for d, m, f in FORMATIONS_XI:
        if len(by["DEF"]) < d or len(by["MID"]) < m or len(by["FWD"]) < f or not by["GK"]:
            continue
        xi = by["GK"][:1] + by["DEF"][:d] + by["MID"][:m] + by["FWD"][:f]
        tot = sum(xp.get(p["id"], 0) for p in xi)
        if best is None or tot > best[0]:
            best = (tot, xi)
    if best is None:
        return squad[:11]
    xi = best[1]
    ranked = sorted(xi, key=lambda p: -xp.get(p["id"], 0))
    return xi, ranked[0]["id"], ranked[1]["id"] if len(ranked) > 1 else ranked[0]["id"]


def model_squad(store: Store) -> dict:
    with _MODEL_LOCK:
        return _model_squad_locked(store)


def _model_squad_locked(store: Store) -> dict:
    gw_data = next_gameweek(store)
    if gw_data.get("error"):
        return gw_data
    gw = gw_data["gameweek"]
    boot = _get(f"{BASE}/bootstrap-static/")
    els = {e["id"]: e for e in boot["elements"]}
    finished = {e["id"] for e in boot["events"] if e.get("finished")}
    xp = {p["id"]: p["xpts"] for p in gw_data["players"]}
    by_pid = {p["id"]: p for p in gw_data["players"]}

    st = state_get("model_squad")
    if not st:
        squad = []
        for name, pos in SEED:
            cands = [e for e in boot["elements"]
                     if e["web_name"] == name and POS[e["element_type"]] == pos]
            if not cands:
                return {"error": "seed_failed", "detail": f"couldn't resolve {name}"}
            e = min(cands, key=lambda e: e["now_cost"])
            squad.append({"id": e["id"], "name": e["web_name"], "pos": pos,
                          "team": e["team"], "buy": e["now_cost"] / 10})
        xi_ids = []
        for n in SEED_XI:
            xi_ids.append(next(p["id"] for p in squad if p["name"] == n))
        cap = next(p["id"] for p in squad if p["name"] == SEED_CAPTAIN)
        vice = next(p["id"] for p in squad if p["name"] == SEED_VICE)
        st = {"created_gw": gw, "gw": gw, "evaluated_gw": gw - 1, "banked": 1,
              "bank": round(100.0 - sum(p["buy"] for p in squad), 1),
              "squad": squad, "xi": xi_ids, "captain": cap, "vice": vice,
              "xi_history": {}, "transfers": [], "scores": []}
        # the squad was held from GW1, so earlier rounds use this exact snapshot
        for g in range(1, gw + 1):
            st["xi_history"][str(g)] = {"xi": xi_ids, "captain": cap, "vice": vice,
                                        "squad": [{"id": p["id"], "pos": p["pos"]}
                                                  for p in squad]}
        state_put("model_squad", st)

    st.setdefault("xi_history", {})
    dirty = False
    if not st["xi_history"]:
        # state predates snapshots: rounds before the first transfer were played
        # with the deterministic seed squad — reconstruct those snapshots
        try:
            seed_ids = {}
            for name, pos in SEED:
                cands = [e for e in boot["elements"]
                         if e["web_name"] == name and POS[e["element_type"]] == pos]
                if cands:
                    seed_ids[name] = min(cands, key=lambda e: e["now_cost"])["id"]
            sq = [{"id": seed_ids[n], "pos": p} for n, p in SEED if n in seed_ids]
            xi_ids = [seed_ids[n] for n in SEED_XI if n in seed_ids]
            first_tr = min((t["gw"] for t in st["transfers"]), default=st["gw"] + 1)
            for g in range(1, first_tr):
                st["xi_history"][str(g)] = {
                    "xi": xi_ids, "captain": seed_ids.get(SEED_CAPTAIN),
                    "vice": seed_ids.get(SEED_VICE), "squad": sq}
            dirty = True
        except Exception:  # noqa: BLE001
            pass

    # ---- score every finished round not yet on the board, each with the XI and
    # squad that were actually held that week (snapshotted at evaluation time)
    for g in sorted(finished):
        if str(g) not in st["xi_history"] and g < st["created_gw"]:
            continue
        if any(s["gw"] == g for s in st["scores"]):
            continue
        snap = st["xi_history"].get(str(g)) or {
            "xi": st["xi"], "captain": st["captain"], "vice": st["vice"],
            "squad": [{"id": p["id"], "pos": p["pos"]} for p in st["squad"]]}
        pts = _score_round(snap["squad"], snap["xi"], snap["captain"], snap["vice"], g)
        if pts is not None:
            st["scores"].append({"gw": g, "points": pts})
            dirty = True

    # ---- a new gameweek arrived: bank the earned transfer(s)
    if st["gw"] < gw:
        st["banked"] = min(5, st["banked"] + (gw - st["gw"]))
        st["gw"] = gw
        dirty = True

    # ---- evaluate this gameweek's transfer window once (before its deadline),
    # spending banked free transfers only on swaps that clearly pay
    if st.get("evaluated_gw", st["gw"] - 1) < gw:
        st["evaluated_gw"] = gw
        while st["banked"] > 0:
            ids = {p["id"] for p in st["squad"]}
            best = None
            for out_p in st["squad"]:
                for cand in gw_data["players"]:
                    if cand["pos"] != out_p["pos"] or cand["id"] in ids:
                        continue
                    if cand["price"] > out_p["buy"] + st["bank"] + 1e-9:
                        continue
                    others = [q for q in st["squad"] if q["id"] != out_p["id"]]
                    club = els.get(cand["id"], {}).get("team")
                    if sum(1 for q in others if q["team"] == club) >= 3:
                        continue
                    gain = cand["xpts"] - xp.get(out_p["id"], 1.0)
                    if best is None or gain > best["gain"]:
                        best = {"out": out_p, "cand": cand, "gain": round(gain, 2)}
            if not best or best["gain"] < GAIN_BAR_MODEL:
                break
            o, c = best["out"], best["cand"]
            st["bank"] = round(st["bank"] + o["buy"] - c["price"], 1)
            st["squad"] = [{"id": c["id"], "name": c["name"], "pos": c["pos"],
                            "team": els[c["id"]]["team"], "buy": c["price"]}
                           if p["id"] == o["id"] else p for p in st["squad"]]
            st["banked"] -= 1
            st["transfers"].append({"gw": gw, "out": o["name"], "in": c["name"],
                                    "gain": best["gain"]})
        xi, cap, vice = _best_xi(st["squad"], xp)
        st["xi"] = [p["id"] for p in xi]
        st["captain"], st["vice"] = cap, vice
        st["xi_history"][str(gw)] = {"xi": st["xi"], "captain": cap, "vice": vice,
                                     "squad": [{"id": p["id"], "pos": p["pos"]}
                                               for p in st["squad"]]}
        state_put("model_squad", st)
    elif dirty:
        state_put("model_squad", st)

    # ---- build the live view
    def enrich(p):
        e = els.get(p["id"], {})
        live = by_pid.get(p["id"])
        return {**p,
                "price": e.get("now_cost", 0) / 10,
                "photo": f"https://resources.premierleague.com/premierleague/photos/players/250x250/p{e.get('code')}.png" if e.get("code") else None,
                "xpts": round(xp.get(p["id"], 1.6), 2),
                "status": e.get("status", "a"),
                "opp": live.get("opp") if live else None,
                "home": live.get("home") if live else None,
                "event_points": e.get("event_points", 0)}
    squad_v = [enrich(p) for p in st["squad"]]
    xi_set = set(st["xi"])
    xi_v = [p for p in squad_v if p["id"] in xi_set]
    bench_v = [p for p in squad_v if p["id"] not in xi_set]
    projected = round(sum(p["xpts"] for p in xi_v)
                      + next((p["xpts"] for p in xi_v if p["id"] == st["captain"]), 0), 1)
    live_gw = sum(p["event_points"] for p in xi_v)         + next((p["event_points"] for p in xi_v if p["id"] == st["captain"]), 0)
    this_week = [t for t in st["transfers"] if t["gw"] == gw]
    return {
        "gameweek": gw, "squad": squad_v, "xi": st["xi"],
        "captain": st["captain"], "vice": st["vice"],
        "bank": st["bank"], "banked_transfers": st["banked"],
        "projected_points": projected,
        "live_gw_points": live_gw,
        "season_points": sum(s["points"] for s in st["scores"]),
        "scores": st["scores"][-10:],
        "this_week": this_week,
        "held": not this_week,
        "durable": _sb_conf() is not None,
        "note": ("This team obeys the real rules: it changes only through free "
                 "transfers (one earned per gameweek, up to five banked, never a "
                 "points hit), inside the budget, max three per club. Selling prices "
                 "are approximated by purchase price, which can differ by up to "
                 "£0.5m in the official app."),
    }
