"""Match prediction engine.

Two independent estimates of expected goals are blended:
  1. Dixon-Coles style: league average goals x time-weighted attack/defence strengths
  2. Elo-based: log-linear map from pre-match Elo difference to expected goals,
     fitted on the historical data itself

Same-league matches lean on (1); cross-league and international matches lean on (2)
because attack/defence strengths are only meaningful relative to a team's own league.
The blended expected goals feed a Dixon-Coles-corrected Poisson score matrix from
which every market is derived.
"""
from __future__ import annotations

import math

import numpy as np

from .data_store import Store

MAX_GOALS = 10
DC_RHO = -0.10          # Dixon-Coles low-score correlation
ELO_HOME_ADV_CLUB = 60.0   # matches the HA used when pre_elo_diff was built
ELO_HOME_ADV_INTL = 55.0


def dc_tau(x: int, y: int, lh: float, la: float, rho: float) -> float:
    if x == 0 and y == 0:
        return 1 - lh * la * rho
    if x == 0 and y == 1:
        return 1 + lh * rho
    if x == 1 and y == 0:
        return 1 + la * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def score_matrix(lh: float, la: float, rho: float = DC_RHO) -> np.ndarray:
    g = np.arange(MAX_GOALS + 1)
    ph = np.exp(-lh) * lh ** g / np.array([math.factorial(i) for i in g])
    pa = np.exp(-la) * la ** g / np.array([math.factorial(i) for i in g])
    mat = np.outer(ph, pa)
    for x in (0, 1):
        for y in (0, 1):
            mat[x, y] *= dc_tau(x, y, lh, la, rho)
    return mat / mat.sum()


# contexts valid for each scope; "none" = pooled baseline (no adjustment)
INTL_CONTEXTS = ("friendly", "qualifier", "finals", "third_place", "final")
CLUB_CONTEXTS = ("dead_rubber", "derby")


def expected_goals(store: Store, home: str, away: str, neutral: bool,
                   context: str = "none") -> dict:
    rh = store.registry[home]
    ra = store.registry[away]
    scope_mix = rh["scope"] != ra["scope"]
    intl = rh["scope"] == "intl" and ra["scope"] == "intl"
    same_league = rh["league"] == ra["league"] and not scope_mix

    # --- estimate 1: attack/defence strengths against blended league baseline
    # Where shot-level xG strengths exist (top-5 leagues + RFPL) they are blended
    # with goals-based strengths: xG is the better signal of underlying quality,
    # goals data is fresher — the mix captures both.
    def strength(team: str, goals_map: dict, xg_map: dict) -> float:
        g = goals_map.get(team, 1.0)
        x = xg_map.get(team)
        return 0.55 * x + 0.45 * g if x is not None else g

    ls_h = store.league_stats.get(rh["league"], {"avg_hg": 1.5, "avg_ag": 1.15})
    ls_a = store.league_stats.get(ra["league"], {"avg_hg": 1.5, "avg_ag": 1.15})
    base_hg = (ls_h["avg_hg"] + ls_a["avg_hg"]) / 2
    base_ag = (ls_h["avg_ag"] + ls_a["avg_ag"]) / 2
    if neutral:
        base_hg = base_ag = (base_hg + base_ag) / 2
    att_h = strength(home, store.attack, store.xg_attack)
    att_a = strength(away, store.attack, store.xg_attack)
    def_h = strength(home, store.defence, store.xg_defence)
    def_a = strength(away, store.defence, store.xg_defence)
    dc_lh = base_hg * att_h * def_a
    dc_la = base_ag * att_a * def_h
    uses_xg = home in store.xg_attack or away in store.xg_attack

    # --- estimate 2: Elo difference -> goals (global, cross-league comparable)
    fit = store.goal_fit_intl if intl else store.goal_fit_club
    eh, ea = rh["elo_global"], ra["elo_global"]
    d = (eh + (0.0 if neutral else (ELO_HOME_ADV_INTL if intl else ELO_HOME_ADV_CLUB)) - ea) / 400.0
    a, c = fit["a"], fit["c"]
    if neutral:
        a = c = (fit["a"] + fit["c"]) / 2
    elo_lh = math.exp(a + fit["b"] * d)
    elo_la = math.exp(c - fit["e"] * d)

    # --- blend (geometric). Validation (10k matches, Jul 2024 - Jul 2025) showed
    # Elo is the stronger 1X2 signal at every weight tested; the strengths part
    # is kept at 25% because it carries xG and team-specific goal totals, which
    # differentiate the totals/BTTS/correct-score markets.
    if same_league:
        w = 0.75
    elif scope_mix:
        w = 0.90
    else:
        w = 0.85
    lh = dc_lh ** (1 - w) * elo_lh ** w
    la = dc_la ** (1 - w) * elo_la ** w
    # match-context scoring environment (fitted from history where possible)
    ctx_info = None
    valid = INTL_CONTEXTS if (rh["scope"] == "intl" and ra["scope"] == "intl") else CLUB_CONTEXTS
    if context in valid:
        cs = store.context_scales.get(context)
        if cs:
            lh *= cs["scale"]
            la *= cs["scale"]
            ctx_info = {"context": context, "goals_multiplier": cs["scale"],
                        "fitted": cs["fitted"], "sample": cs["n"], "applied": True}
    elif context and context != "none":
        # asked for a match type that does not exist for this kind of fixture
        # (tournament stages are international-only, derbies are club-only)
        ctx_info = {"context": context, "goals_multiplier": 1.0, "applied": False,
                    "valid_for_this_match": list(valid)}

    lh = float(np.clip(lh, 0.15, 4.5))
    la = float(np.clip(la, 0.15, 4.5))
    return {"lambda_home": lh, "lambda_away": la, "elo_diff": round(eh - ea, 1),
            "context": ctx_info,
            "same_league": same_league, "scope_mix": scope_mix, "uses_xg": uses_xg,
            "components": {"dc": [round(dc_lh, 2), round(dc_la, 2)],
                           "elo": [round(elo_lh, 2), round(elo_la, 2)],
                           "elo_weight": w}}


def fair(p: float) -> float | None:
    return round(1.0 / p, 2) if p > 0.005 else None


def markets_from_matrix(mat: np.ndarray) -> dict:
    idx = np.arange(MAX_GOALS + 1)
    p_home = float(np.tril(mat, -1).sum())
    p_draw = float(np.trace(mat))
    p_away = float(np.triu(mat, 1).sum())

    totals = idx[:, None] + idx[None, :]
    over = {}
    for line in (0.5, 1.5, 2.5, 3.5, 4.5):
        p = float(mat[totals > line].sum())
        over[str(line)] = {"over": round(p, 4), "under": round(1 - p, 4),
                           "fair_over": fair(p), "fair_under": fair(1 - p)}

    btts = float(mat[1:, 1:].sum())
    flat = [(int(i), int(j), float(mat[i, j]))
            for i in idx for j in idx if mat[i, j] > 0.001]
    flat.sort(key=lambda t: -t[2])
    top_scores = [{"score": f"{i}-{j}", "prob": round(p, 4), "fair_odds": fair(p)}
                  for i, j, p in flat[:8]]

    hcap = {}
    for h in (-2.5, -1.5, 1.5, 2.5):  # home handicap lines
        p = float(mat[(idx[:, None] + h) > idx[None, :]].sum())
        hcap[f"home {h:+g}"] = round(p, 4)

    # winning margin: the same matrix folded into goal-difference buckets,
    # which reads far more naturally than a wall of exact scorelines
    diff = idx[:, None] - idx[None, :]
    margins = {
        "home_by_2_plus": round(float(mat[diff >= 2].sum()), 4),
        "home_by_1": round(float(mat[diff == 1].sum()), 4),
        "draw": round(p_draw, 4),
        "away_by_1": round(float(mat[diff == -1].sum()), 4),
        "away_by_2_plus": round(float(mat[diff <= -2].sum()), 4),
    }

    return {
        "one_x_two": {
            "home": round(p_home, 4), "draw": round(p_draw, 4), "away": round(p_away, 4),
            "fair_odds": {"home": fair(p_home), "draw": fair(p_draw), "away": fair(p_away)},
        },
        "double_chance": {
            "1X": round(p_home + p_draw, 4), "X2": round(p_away + p_draw, 4),
            "12": round(p_home + p_away, 4),
        },
        "draw_no_bet": {
            "home": round(p_home / (p_home + p_away), 4),
            "away": round(p_away / (p_home + p_away), 4),
        },
        "totals": over,
        "btts": {"yes": round(btts, 4), "no": round(1 - btts, 4),
                 "fair_yes": fair(btts), "fair_no": fair(1 - btts)},
        "clean_sheet": {"home": round(float(mat[:, 0].sum()), 4),
                        "away": round(float(mat[0, :].sum()), 4)},
        "handicaps": hcap,
        "margins": margins,
        "correct_scores": top_scores,
    }


def likely_scorers(store: Store, team_name: str, team_lambda: float) -> list[dict]:
    """International teams: allocate expected goals by recency-weighted goal share."""
    sg = store.scorer_goals
    rows = sg[sg.team == team_name].sort_values("wgoals", ascending=False)
    if rows.empty:
        return []
    total = rows.wgoals.sum()
    out = []
    for _, r in rows.head(8).iterrows():
        share = r.wgoals / total
        share *= 0.85  # some goals go to players outside the recent-scorer list
        p = 1 - math.exp(-team_lambda * share)
        out.append({"player": r.scorer, "recent_goals": int(r.goals_2y),
                    "prob_to_score": round(p, 3), "fair_odds": fair(p)})
    return out


def likely_scorers_club(store: Store, tid: str, team_lambda: float) -> list[dict]:
    """Clubs with shot-level data: allocate expected goals by each player's
    recency-weighted share of the team's xG."""
    out = []
    for r in store.player_rates.get(tid, [])[:8]:
        p = 1 - math.exp(-team_lambda * r["xg_share"] * 0.92)
        out.append({"player": r["player"], "recent_goals": r["recent_goals"],
                    "recent_xg": r["recent_xg"], "apps": r.get("apps"),
                    "xg_per_match": r.get("xg_per_match"),
                    "sot_rate": r.get("sot_rate", 0),
                    "prob_to_score": round(p, 3), "fair_odds": fair(p)})
    return out


def _absence_factor(store: Store, tid: str, out_players: list[str]) -> tuple[float, list]:
    """Reduction in team expected goals when named players are unavailable.
    A missing player removes ~65% of his xG share (his replacement and shot
    redistribution recover the rest) — in line with published squad-strength studies.
    Clubs use shot-based xG shares; national teams fall back to weighted goal
    shares so marking an international's star out is not a silent no-op."""
    reg = store.registry.get(tid, {})
    rates = {r["player"]: r["xg_share"] for r in store.player_rates.get(tid, [])}
    factor, applied = 1.0, []
    for p in out_players:
        share = rates.get(p)
        if share is None and reg.get("scope") == "intl":
            sg = store.scorer_goals
            rows = sg[sg.team == reg.get("name", "")]
            if len(rows) and p in set(rows.scorer):
                total = float(rows.wgoals.sum()) or 1.0
                share = float(rows[rows.scorer == p].wgoals.iloc[0]) / total * 0.85
        if share:
            factor *= (1.0 - 0.65 * share)
            applied.append({"player": p, "xg_share": round(share, 3)})
    # even a decimated side keeps ~60% of its attacking output (replacements
    # play); this floor also bounds the damage of any false-positive news hit
    return max(factor, 0.6), applied


# --- same-game parlay legs: each is a boolean condition over (home goals, away goals)
_G = np.arange(MAX_GOALS + 1)
_H, _A = np.meshgrid(_G, _G, indexing="ij")

FIRST_HALF_GOAL_SHARE = 0.456   # empirical share of goals scored before HT
N_SIMS = 150_000


def _team_extras(store: Store, tid: str, side: str) -> dict:
    lg = store.extras_league
    base = store.team_extras.get(tid)
    if base:
        return base
    return {"cf": lg[f"corners_{side}"], "ca": lg[f"corners_{'away' if side == 'home' else 'home'}"],
            "cards": lg[f"cards_{side}"]}


def _player_share(store: Store, tid: str, reg: dict, player: str) -> tuple[float, float] | None:
    """(goal share of team goals, shots-on-target per match) for a player."""
    for r in store.player_rates.get(tid, []):
        if r["player"] == player:
            return r["xg_share"] * 0.92, r.get("sot_rate") or 0.0
    if reg["scope"] == "intl":
        sg = store.scorer_goals
        rows = sg[sg.team == reg["name"]]
        if not rows.empty and player in set(rows.scorer):
            total = rows.wgoals.sum()
            share = float(rows[rows.scorer == player].wgoals.iloc[0]) / total * 0.85
            return share, 0.0
    return None


def simulate_sgp(store: Store, home: str, away: str, legs: list[str],
                 neutral: bool = False, context: str = "none", n: int = N_SIMS,
                 out_home: list[str] | None = None,
                 out_away: list[str] | None = None) -> dict:
    """Same-game parlay joint probability via Monte Carlo over the score matrix.

    Scorelines are drawn from the calibrated Dixon-Coles matrix (exact);
    goal timing, corners, cards and player events are layered on with
    documented approximations (timing uniform w/ empirical half split,
    corners/cards Poisson from team rates, player goals thinned from team
    goals by xG share, player SoT Poisson scaled with match expectation).
    """
    rng = np.random.default_rng(20260715)
    rh, ra = store.registry[home], store.registry[away]
    eg = expected_goals(store, home, away, neutral, context)
    lh, la = eg["lambda_home"], eg["lambda_away"]
    # the same absentees that shape the main prediction shape every parlay leg
    if out_home:
        lh *= _absence_factor(store, home, out_home)[0]
    if out_away:
        la *= _absence_factor(store, away, out_away)[0]
    _outs = set((out_home or []) + (out_away or []))
    mat = score_matrix(lh, la)
    flat = mat.ravel()
    cells = rng.choice(len(flat), size=n, p=flat / flat.sum())
    hg = cells // (MAX_GOALS + 1)
    ag = cells % (MAX_GOALS + 1)

    sims: dict[str, np.ndarray] = {"hg": hg, "ag": ag}

    def need(kind: str):
        """Lazily simulate optional layers only when a leg needs them."""
        if kind in sims:
            return
        if kind == "h1":
            sims["h1_h"] = rng.binomial(hg, FIRST_HALF_GOAL_SHARE)
            sims["h1_a"] = rng.binomial(ag, FIRST_HALF_GOAL_SHARE)
            sims["h1"] = True
        elif kind == "first":
            tot = hg + ag
            p_home_first = np.where(tot > 0, hg / np.maximum(tot, 1), 0.0)
            r = rng.random(n)
            sims["first_home"] = (tot > 0) & (r < p_home_first)
            sims["first_away"] = (tot > 0) & ~sims["first_home"]
            sims["first"] = True
        elif kind == "corners":
            exh = _team_extras(store, home, "home")
            exa = _team_extras(store, away, "away")
            lg = store.extras_league
            # attacking tilt nudges corner expectation toward the stronger side.
            # Home sides win ~54% of corners empirically; the old 0.4+0.55*tilt
            # curve handed them ~67%+ and inflated every home-corners-over leg.
            tilt = lh / (lh + la)
            base_total = (exh["cf"] + exa["ca"]) / 2 + (exa["cf"] + exh["ca"]) / 2
            home_share = (0.5 if neutral else 0.54) + 0.35 * (tilt - 0.5)
            mu_h = base_total * min(max(home_share, 0.30), 0.75)
            mu_a = base_total - mu_h
            sims["ch"] = rng.poisson(max(mu_h, 0.5), n)
            sims["ca_"] = rng.poisson(max(mu_a, 0.5), n)
            sims["corners"] = True
        elif kind == "cards":
            exh = _team_extras(store, home, "home")
            exa = _team_extras(store, away, "away")
            sims["cards_h"] = rng.poisson(max(exh["cards"], 0.3), n)
            sims["cards_a"] = rng.poisson(max(exa["cards"], 0.3), n)
            sims["cards"] = True

    def player_goals(side: str, player: str) -> np.ndarray:
        key = f"pg:{side}:{player}"
        if key not in sims:
            if player in _outs:
                raise ValueError(f"{player} is flagged out of this match")
            tid, reg = (home, rh) if side == "home" else (away, ra)
            sh = _player_share(store, tid, reg, player)
            if sh is None:
                raise ValueError(f"no data for player: {player}")
            sims[key] = rng.binomial(hg if side == "home" else ag, min(sh[0], 0.95))
        return sims[key]

    def player_sot(side: str, player: str) -> np.ndarray:
        key = f"ps:{side}:{player}"
        if key not in sims:
            if player in _outs:
                raise ValueError(f"{player} is flagged out of this match")
            tid, reg = (home, rh) if side == "home" else (away, ra)
            sh = _player_share(store, tid, reg, player)
            if sh is None or sh[1] <= 0:
                raise ValueError(f"no shots-on-target data for: {player}")
            lam_typ = 1.35
            scale = (lh if side == "home" else la) / lam_typ
            sims[key] = rng.poisson(sh[1] * scale, n)
        return sims[key]

    def eval_leg(leg: str) -> tuple[str, np.ndarray]:
        h, a, tot = hg, ag, hg + ag
        nm = {"home": rh["name"], "away": ra["name"]}
        simple = {
            "home": (f"{nm['home']} win", h > a), "draw": ("Draw", h == a),
            "away": (f"{nm['away']} win", h < a),
            "1x": (f"{nm['home']} or draw", h >= a), "x2": (f"{nm['away']} or draw", h <= a),
            "12": ("No draw", h != a),
            "btts": ("Both teams score", (h > 0) & (a > 0)),
            "no_btts": ("Not both score", (h == 0) | (a == 0)),
            "odd": ("Odd total goals", tot % 2 == 1), "even": ("Even total goals", tot % 2 == 0),
            "home_cs": (f"{nm['home']} clean sheet", a == 0),
            "away_cs": (f"{nm['away']} clean sheet", h == 0),
            "both_halves_goal": ("Goal in each half", None),  # filled below
        }
        if leg in simple:
            if leg == "both_halves_goal":
                need("h1")
                m = (sims["h1_h"] + sims["h1_a"] > 0) & \
                    ((h - sims["h1_h"]) + (a - sims["h1_a"]) > 0)
                return "Goal in each half", m
            return simple[leg]
        p = leg.split(":")
        if p[0] in ("o", "u"):                      # o:2.5 / u:3.5 total goals
            line = float(p[1])
            return (f"{'Over' if p[0] == 'o' else 'Under'} {line} goals",
                    tot > line if p[0] == "o" else tot < line)
        if p[0] in ("home_o", "away_o"):            # home_o:1.5 team goals
            line = float(p[1])
            side = "home" if p[0] == "home_o" else "away"
            arr = h if side == "home" else a
            return f"{nm[side]} over {line} goals", arr > line
        if p[0] == "cs":                            # cs:2-1 correct score
            x, y = p[1].split("-")
            return f"Correct score {x}-{y}", (h == int(x)) & (a == int(y))
        if p[0] == "margin":                        # margin:home:2 -> win by 2+
            side, k = p[1], int(p[2])
            d = h - a if side == "home" else a - h
            return f"{nm[side]} win by {k}+", d >= k
        if p[0] == "ht":                            # ht:home half-time result
            need("h1")
            hh, ha = sims["h1_h"], sims["h1_a"]
            m = {"home": hh > ha, "draw": hh == ha, "away": hh < ha}[p[1]]
            lbl = {"home": nm["home"], "draw": "Draw", "away": nm["away"]}[p[1]]
            return f"HT: {lbl}", m
        if p[0] == "first":                         # first:home / first:away / first:none
            need("first")
            if p[1] == "none":
                return "No goals", hg + ag == 0
            return (f"{nm[p[1]]} scores first",
                    sims["first_home"] if p[1] == "home" else sims["first_away"])
        if p[0] in ("corners_o", "corners_u"):      # corners_o:9.5
            need("corners")
            line = float(p[1])
            ctot = sims["ch"] + sims["ca_"]
            return (f"{'Over' if p[0] == 'corners_o' else 'Under'} {line} corners",
                    ctot > line if p[0] == "corners_o" else ctot < line)
        if p[0] in ("cards_o", "cards_u"):          # cards_o:4.5
            need("cards")
            line = float(p[1])
            ctot = sims["cards_h"] + sims["cards_a"]
            return (f"{'Over' if p[0] == 'cards_o' else 'Under'} {line} cards",
                    ctot > line if p[0] == "cards_o" else ctot < line)
        if p[0] == "scorer":                        # scorer:home:Mohamed Salah
            side, player = p[1], ":".join(p[2:])
            return f"{player} to score", player_goals(side, player) > 0
        if p[0] == "sot":                           # sot:home:Salah:2 -> 2+ SoT
            side, player, k = p[1], ":".join(p[2:-1]), int(p[-1])
            return f"{player} {k}+ shots on target", player_sot(side, player) >= k
        raise ValueError(f"unknown leg: {leg}")

    out_legs = []
    mask = np.ones(n, dtype=bool)
    naive = 1.0
    approx = False
    for leg in legs:
        label, m = eval_leg(leg)
        pm = float(m.mean())
        naive *= pm
        out_legs.append({"leg": leg, "label": label, "marginal_prob": round(pm, 4)})
        mask &= m
        if leg.split(":")[0] in ("corners_o", "corners_u", "cards_o", "cards_u", "sot"):
            approx = True
    joint = float(mask.mean())
    notes = ["Simulated over 150,000 match runs (sampling error ≈ ±0.3%)."]
    if approx:
        notes.append("Corners/cards/shots-on-target legs use team-rate models that are "
                     "approximately independent of the scoreline, so treat those combos as estimates.")
    return {
        "legs": out_legs,
        "joint_prob": round(joint, 4),
        "fair_odds": fair(joint),
        "naive_prob": round(naive, 4),
        "naive_odds": fair(naive),
        "correlation_boost": round(joint / naive, 3) if naive > 1e-9 else None,
        "expected_goals": {"home": round(lh, 2), "away": round(la, 2)},
        "notes": notes,
    }


def suggest_parlays(store: Store, home: str, away: str,
                    neutral: bool = False, context: str = "none",
                    out_home: list[str] | None = None,
                    out_away: list[str] | None = None) -> list[dict]:
    """Auto-build same-game parlay candidates around the model's read of the
    match. Each comes with true fair odds and the minimum book quote worth
    taking (fair +5% safety margin for model uncertainty). Players flagged
    out never headline a suggestion, and every leg is priced with the same
    absentee-reduced goal expectations as the main prediction."""
    eg = expected_goals(store, home, away, neutral, context)
    favside = "home" if eg["lambda_home"] >= eg["lambda_away"] else "away"
    tid = home if favside == "home" else away
    reg = store.registry[tid]
    outs_fav = set((out_home or []) if favside == "home" else (out_away or []))
    top = None
    for r in store.player_rates.get(tid, []):
        if r["player"] not in outs_fav:
            top = r["player"]
            break
    if top is None and reg["scope"] == "intl":
        rows = store.scorer_goals[store.scorer_goals.team == reg["name"]] \
            .sort_values("wgoals", ascending=False)
        for _, row in rows.iterrows():
            if row.scorer not in outs_fav:
                top = row.scorer
                break

    T = [
        ("Banker build", [favside, "o:1.5"]),
        ("Win & both score", [favside, "btts"]),
        ("Win & goals", [favside, "o:2.5"]),
        ("Team firepower", [favside, f"{favside}_o:1.5"]),
        ("Statement win", [f"margin:{favside}:2", "o:2.5"]),
        ("Win to nil", [favside, f"{favside}_cs"]),
        ("Fast start", [f"first:{favside}", favside]),
        ("Cagey affair", ["draw", "u:2.5"]),
        ("Goal fest", ["btts", "o:2.5"]),
    ]
    if top:
        T.insert(4, ("Star delivers", [favside, f"scorer:{favside}:{top}"]))
        T.insert(5, ("Star & goals", [f"scorer:{favside}:{top}", "o:2.5"]))
        T.insert(6, ("Full script", [favside, f"scorer:{favside}:{top}", "o:2.5"]))
        T.append(("Boost qualifier (4 legs)",
                  [favside, "o:1.5", f"scorer:{favside}:{top}", f"first:{favside}"]))

    out = []
    for name, legs in T:
        try:
            r = simulate_sgp(store, home, away, legs, neutral, context,
                             out_home=out_home, out_away=out_away)
        except (ValueError, KeyError):
            continue
        p = r["joint_prob"]
        if p < 0.015 or not r["fair_odds"]:
            continue
        out.append({
            "name": name, "legs": legs,
            "labels": [l["label"] for l in r["legs"]],
            "joint_prob": p, "fair_odds": r["fair_odds"],
            "min_quote": round(r["fair_odds"] * 1.05, 2),
            "correlation_boost": r["correlation_boost"],
            "n_legs": len(legs),
        })
    out.sort(key=lambda x: -x["joint_prob"])
    return out


def predict(store: Store, home: str, away: str, neutral: bool = False,
            out_home: list[str] | None = None, out_away: list[str] | None = None,
            context: str = "none") -> dict:
    eg = expected_goals(store, home, away, neutral, context)
    lh, la = eg["lambda_home"], eg["lambda_away"]
    adj_home, adj_away = [], []
    if out_home:
        f, adj_home = _absence_factor(store, home, out_home)
        lh *= f
    if out_away:
        f, adj_away = _absence_factor(store, away, out_away)
        la *= f
    mat = score_matrix(lh, la)
    mk = markets_from_matrix(mat)

    rh, ra = store.registry[home], store.registry[away]
    scorers = {}
    scorers_from_xg = False
    for tid, reg, lam in ((home, rh, lh), (away, ra, la)):
        if reg["scope"] == "intl":
            scorers[reg["name"]] = likely_scorers(store, reg["name"], lam)
        elif tid in store.player_rates:
            scorers[reg["name"]] = likely_scorers_club(store, tid, lam)
            scorers_from_xg = True

    # drop players marked OUT from scorer lists, and hand their remaining share
    # of the (already reduced) team goals to the players still on the pitch
    out_all = set((out_home or []) + (out_away or []))
    if out_all:
        removed = {rh["name"]: sum(a["xg_share"] for a in adj_home),
                   ra["name"]: sum(a["xg_share"] for a in adj_away)}
        rescored = {}
        for t, ps in scorers.items():
            kept = [x for x in ps if x["player"] not in out_all]
            s = min(removed.get(t, 0.0), 0.6)
            if s > 0:
                for x in kept:
                    p = min(max(x.get("prob_to_score", 0.0), 0.0), 0.999)
                    p2 = 1.0 - (1.0 - p) ** (1.0 / (1.0 - s))
                    x["prob_to_score"] = round(p2, 3)
                    x["fair_odds"] = fair(p2)
            rescored[t] = kept
        scorers = rescored

    caveats = []
    if eg.get("context"):
        ci = eg["context"]
        label = ci["context"].replace("_", " ")
        if ci.get("applied"):
            basis = (f"fitted on {ci['sample']} historical matches" if ci["fitted"]
                     else "research-based estimate, not fitted")
            caveats.append(f"Match context '{label}': expected goals scaled "
                           f"x{ci['goals_multiplier']} ({basis}).")
        else:
            kind = ("a club" if rh["scope"] != "intl" or ra["scope"] != "intl"
                    else "an international")
            caveats.append(f"'{label.title()}' does not apply to {kind} fixture, so no goal "
                           "adjustment was made. The numbers are the same as a regular match.")
    for side_adj, side_out, reg in ((adj_home, out_home or [], rh),
                                    (adj_away, out_away or [], ra)):
        for a in side_adj:
            caveats.append(f"Adjusted for {a['player']} OUT: {reg['name']}'s expected goals "
                           f"reduced by {a['xg_share']*65:.0f}% of team output.")
        priced = {a["player"] for a in side_adj}
        missed = [p for p in side_out if p not in priced]
        if missed:
            caveats.append(f"No per-player goal data for {', '.join(missed)} "
                           f"({reg['name']}): their absence is noted but the goal "
                           "numbers are unchanged.")
    if scorers_from_xg and store.xg_data_to:
        caveats.append(f"Club scorer probabilities use shot data up to {store.xg_data_to}; "
                       "they do not reflect transfers or injuries since then.")
    if eg["scope_mix"]:
        caveats.append("Club vs national team: Elo scales are not directly comparable, so treat this prediction as a rough guide only.")
    if not eg["same_league"] and not eg["scope_mix"]:
        caveats.append("Cross-league matchup: prediction relies mainly on Elo ratings; these teams rarely or never meet in the data.")
    if not rh.get("active") or not ra.get("active"):
        caveats.append("At least one team has not played recently in our data; ratings may be stale.")
    for reg in (rh, ra):
        if reg.get("n", 999) < 15:
            caveats.append(f"{reg['name']} has very little history in our data "
                           f"({reg.get('n', 0)} matches), so its numbers lean on a "
                           "league-average prior. Treat this one loosely.")

    n1x2 = mk["one_x_two"]
    verdict_key = max(("home", "draw", "away"), key=lambda k: n1x2[k])
    verdict = {"home": f"{rh['name']} win", "draw": "Draw", "away": f"{ra['name']} win"}[verdict_key]

    # one honest sentence about the SHAPE of the game, from the margin buckets
    mg = mk["margins"]
    tight = mg["draw"] + mg["home_by_1"] + mg["away_by_1"]
    if mg["home_by_2_plus"] >= 0.40:
        margin_note = (f"{rh['name']} by two or more goals is the likeliest shape "
                       f"({mg['home_by_2_plus']:.0%}), though {tight:.0%} of the time "
                       "this still stays within a single goal.")
    elif mg["away_by_2_plus"] >= 0.40:
        margin_note = (f"{ra['name']} by two or more goals is the likeliest shape "
                       f"({mg['away_by_2_plus']:.0%}), though {tight:.0%} of the time "
                       "this still stays within a single goal.")
    elif tight >= 0.55:
        margin_note = (f"Tough to call: {tight:.0%} of the time this ends level or is "
                       "settled by a single goal, so expect it tight either way.")
    else:
        lead = rh["name"] if n1x2["home"] >= n1x2["away"] else ra["name"]
        margin_note = (f"A {lead} lean, but no margin dominates: the game is about as "
                       f"likely to stay within one goal ({tight:.0%}) as to open up.")

    return {
        "home": {"id": home, "name": rh["name"], "elo": rh["elo_global"],
                 "league": rh["league_name"], "country": rh["country"]},
        "away": {"id": away, "name": ra["name"], "elo": ra["elo_global"],
                 "league": ra["league_name"], "country": ra["country"]},
        "neutral_venue": neutral,
        "expected_goals": {"home": round(lh, 2), "away": round(la, 2)},
        "model_detail": eg["components"] | {"elo_diff": eg["elo_diff"], "uses_xg": eg["uses_xg"]},
        "context_applied": eg.get("context"),
        "verdict": {"call": verdict, "confidence": round(max(n1x2["home"], n1x2["draw"], n1x2["away"]), 3),
                    "predicted_score": mk["correct_scores"][0]["score"]},
        "margin_note": margin_note,
        "markets": mk,
        "score_matrix": [[round(float(mat[i, j]), 5) for j in range(7)] for i in range(7)],
        "likely_scorers": scorers,
        "absences": {"home": adj_home, "away": adj_away},
        "caveats": caveats,
    }
