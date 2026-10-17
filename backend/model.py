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

from .data_store import ELO_HOME_ADV_CLUB, ELO_HOME_ADV_INTL, Store

MAX_GOALS = 10
_FACT = np.array([math.factorial(i) for i in range(MAX_GOALS + 1)], dtype=float)
DC_RHO = -0.10          # Dixon-Coles low-score correlation


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
    ph = np.exp(-lh) * lh ** g / _FACT
    pa = np.exp(-la) * la ** g / _FACT
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
    # goals data is fresher, and the mix captures both.
    def strength(team: str, goals_map: dict, xg_map: dict) -> float:
        g = goals_map.get(team, 1.0)
        x = xg_map.get(team)
        return 0.55 * x + 0.45 * g if x is not None else g

    ls_h = store.league_stats[rh["league"]]
    ls_a = store.league_stats[ra["league"]]
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
    # teams with no result inside the strengths window fall back to 1.0 above
    unrated = [r["name"] for t, r in ((home, rh), (away, ra)) if t not in store.attack]

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
    valid = INTL_CONTEXTS if intl else CLUB_CONTEXTS
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
            "context": ctx_info, "unrated": unrated,
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
    dc = {"1X": p_home + p_draw, "X2": p_away + p_draw, "12": p_home + p_away}
    cs = {"home": float(mat[:, 0].sum()), "away": float(mat[0, :].sum())}
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
        "double_chance": {**{k: round(p, 4) for k, p in dc.items()},
                          "fair_odds": {k: fair(p) for k, p in dc.items()}},
        "draw_no_bet": {
            "home": round(p_home / (p_home + p_away), 4),
            "away": round(p_away / (p_home + p_away), 4),
        },
        "totals": over,
        "btts": {"yes": round(btts, 4), "no": round(1 - btts, 4),
                 "fair_yes": fair(btts), "fair_no": fair(1 - btts)},
        "clean_sheet": {**{k: round(p, 4) for k, p in cs.items()},
                        "fair_odds": {k: fair(p) for k, p in cs.items()}},
        "handicaps": hcap,
        "margins": margins,
        "correct_scores": top_scores,
    }


# ---------- player shares: one computation for scorers, absences and parlay legs ----------
# Some goals go to players outside the tracked list, so a list's shares are thinned.
CLUB_SHARE_SCALE = 0.92     # shot-data list (top 10 by xG share)
INTL_SHARE_SCALE = 0.85     # recent-scorer list for national teams


def _team_shares(store: Store, tid: str, reg: dict) -> list[tuple[str, float]]:
    """Each tracked player's share of his team's goals, largest first: xG share
    where shot data exists, recency-weighted goal share for national teams."""
    rows = store.player_rates.get(tid)
    if rows:
        return [(r["player"], r["xg_share"] * CLUB_SHARE_SCALE) for r in rows]
    if reg["scope"] == "intl":
        sg = store.scorer_goals
        t = sg[sg.team == reg["name"]].sort_values("wgoals", ascending=False)
        total = float(t.wgoals.sum())
        if total > 0:
            return [(p, float(g) / total * INTL_SHARE_SCALE) for p, g in zip(t.scorer, t.wgoals)]
    return []


def _player_share(store: Store, tid: str, reg: dict, player: str) -> float | None:
    """A player's share of his team's goals, or None if unknown."""
    return next((s for p, s in _team_shares(store, tid, reg) if p == player), None)


def likely_scorers(store: Store, tid: str, reg: dict, team_lambda: float) -> list[dict]:
    """Allocate the team's expected goals across its tracked players."""
    out = []
    for player, share in _team_shares(store, tid, reg)[:8]:
        p = 1 - math.exp(-team_lambda * share)
        out.append({"player": player, "prob_to_score": round(p, 3), "fair_odds": fair(p)})
    return out


def likely_assisters(store: Store, tid: str, team_lambda: float) -> list[dict]:
    """Clubs with shot data: a player's expected assists are his share of the
    team's xA times the team's expected goals times the league-wide measured
    assists per goal (xA per xG). Empty where the data is missing."""
    shares = store.player_xa.get(tid)
    if not shares or not store.xa_per_xg:
        return []
    out = []
    for player, share in sorted(shares.items(), key=lambda kv: -kv[1])[:5]:
        p = 1 - math.exp(-share * team_lambda * store.xa_per_xg)
        out.append({"player": player, "prob_to_assist": round(p, 3), "fair_odds": fair(p)})
    return out


ABSENCE_LOSS = 0.65      # assumed share of a missing player's goals the team loses


def _absence_factor(store: Store, tid: str, out_players: list[str]) -> tuple[float, list]:
    """Reduction in team expected goals when named players are unavailable.
    A missing player is assumed to take ABSENCE_LOSS of his goal share with him
    (his replacement and shot redistribution recover the rest). That loss rate
    is an assumption, not a value fitted on this data. Clubs use shot-based xG
    shares; national teams fall back to weighted goal shares so marking an
    international's star out is not a silent no-op."""
    reg = store.registry.get(tid, {})
    rates = {r["player"]: r["xg_share"] for r in store.player_rates.get(tid, [])}
    factor, applied = 1.0, []
    for p in out_players:
        share = rates.get(p)
        if share is None and reg.get("scope") == "intl":
            share = _player_share(store, tid, reg, p)
        if share:
            factor *= (1.0 - ABSENCE_LOSS * share)
            applied.append({"player": p, "xg_share": round(share, 3)})
    # even a decimated side keeps ~60% of its attacking output (replacements
    # play); this floor also bounds the damage of any false-positive news hit
    return max(factor, 0.6), applied


# --- same-game parlay legs: each is a boolean condition over (home goals, away goals)
FIRST_HALF_GOAL_SHARE = 0.456   # fallback only: the store measures its own share
N_SIMS = 150_000
MIN_PARLAY_PROB = 0.015         # suggestions below this joint probability are dropped


class _MatchSim:
    """Monte Carlo scoreline draws for one fixture, taken once from the calibrated
    Dixon-Coles matrix (exact). Half-time splits and scorer legs are layered on
    lazily with documented approximations (goals split by the measured
    first-half share, player goals thinned from team goals by share). Every
    parlay for the fixture is evaluated on the same draws."""

    def __init__(self, store: Store, home: str, away: str, neutral: bool = False,
                 context: str = "none", out_home: list[str] | None = None,
                 out_away: list[str] | None = None, n: int = N_SIMS):
        self.store, self.n = store, n
        self.rng = np.random.default_rng(20260715)
        self.ids = {"home": home, "away": away}
        self.regs = {"home": store.registry[home], "away": store.registry[away]}
        self.names = {s: r["name"] for s, r in self.regs.items()}
        eg = expected_goals(store, home, away, neutral, context)
        lh, la = eg["lambda_home"], eg["lambda_away"]
        # the same absentees that shape the main prediction shape every parlay leg
        if out_home:
            lh *= _absence_factor(store, home, out_home)[0]
        if out_away:
            la *= _absence_factor(store, away, out_away)[0]
        self.lh, self.la = lh, la
        self.outs = set((out_home or []) + (out_away or []))
        mat = score_matrix(lh, la)
        flat = mat.ravel()
        cells = self.rng.choice(len(flat), size=n, p=flat / flat.sum())
        self.hg = cells // (MAX_GOALS + 1)
        self.ag = cells % (MAX_GOALS + 1)
        self.layers: dict[str, np.ndarray] = {}

    def _half_time(self) -> tuple[np.ndarray, np.ndarray]:
        if "h1_h" not in self.layers:
            share = self.store.first_half_share or FIRST_HALF_GOAL_SHARE
            self.layers["h1_h"] = self.rng.binomial(self.hg, share)
            self.layers["h1_a"] = self.rng.binomial(self.ag, share)
        return self.layers["h1_h"], self.layers["h1_a"]

    def _first_goal(self) -> tuple[np.ndarray, np.ndarray]:
        if "first_home" not in self.layers:
            tot = self.hg + self.ag
            p_home_first = np.where(tot > 0, self.hg / np.maximum(tot, 1), 0.0)
            r = self.rng.random(self.n)
            self.layers["first_home"] = (tot > 0) & (r < p_home_first)
            self.layers["first_away"] = (tot > 0) & ~self.layers["first_home"]
        return self.layers["first_home"], self.layers["first_away"]

    def _player_goals(self, side: str, player: str) -> np.ndarray:
        key = f"pg:{side}:{player}"
        if key not in self.layers:
            if player in self.outs:
                raise ValueError(f"{player} is flagged out of this match")
            sh = _player_share(self.store, self.ids[side], self.regs[side], player)
            if sh is None:
                raise ValueError(f"no data for player: {player}")
            self.layers[key] = self.rng.binomial(self.hg if side == "home" else self.ag,
                                                 min(sh, 0.95))
        return self.layers[key]

    def leg(self, leg: str) -> tuple[str, np.ndarray]:
        h, a, tot = self.hg, self.ag, self.hg + self.ag
        nm = self.names
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
        }
        if leg in simple:
            return simple[leg]
        if leg == "both_halves_goal":
            h1h, h1a = self._half_time()
            return "Goal in each half", (h1h + h1a > 0) & ((h - h1h) + (a - h1a) > 0)
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
            hh, ha = self._half_time()
            m = {"home": hh > ha, "draw": hh == ha, "away": hh < ha}[p[1]]
            lbl = {"home": nm["home"], "draw": "Draw", "away": nm["away"]}[p[1]]
            return f"HT: {lbl}", m
        if p[0] == "first":                         # first:home / first:away / first:none
            if p[1] == "none":
                return "No goals", tot == 0
            first_home, first_away = self._first_goal()
            return (f"{nm[p[1]]} scores first", first_home if p[1] == "home" else first_away)
        if p[0] == "scorer":                        # scorer:home:Mohamed Salah
            side, player = p[1], ":".join(p[2:])
            return f"{player} to score", self._player_goals(side, player) > 0
        raise ValueError(f"unknown leg: {leg}")

    def parlay(self, legs: list[str]) -> dict:
        """Joint probability of all legs on the shared draws, next to the naive
        product of the marginals, so the correlation between legs is visible."""
        out_legs = []
        mask = np.ones(self.n, dtype=bool)
        naive = 1.0
        for leg in legs:
            label, m = self.leg(leg)
            pm = float(m.mean())
            naive *= pm
            out_legs.append({"leg": leg, "label": label, "marginal_prob": round(pm, 4)})
            mask &= m
        joint = float(mask.mean())
        # worst-case binomial sampling error at two standard errors
        err = 2 * math.sqrt(0.25 / self.n)
        return {
            "legs": out_legs,
            "joint_prob": round(joint, 4),
            "fair_odds": fair(joint),
            "naive_prob": round(naive, 4),
            "naive_odds": fair(naive),
            "correlation_boost": round(joint / naive, 3) if naive > 1e-9 else None,
            "expected_goals": {"home": round(self.lh, 2), "away": round(self.la, 2)},
            "notes": [f"Simulated over {self.n:,} match runs (sampling error within about {err:.1%})."],
        }


def suggest_parlays(store: Store, home: str, away: str,
                    neutral: bool = False, context: str = "none",
                    out_home: list[str] | None = None,
                    out_away: list[str] | None = None) -> dict:
    """Auto-build same-game parlay candidates around the model's read of the
    match. Each comes with true fair odds and the minimum book quote worth
    taking (fair +5% safety margin for model uncertainty). Players flagged
    out never headline a suggestion, and every leg is priced with the same
    absentee-reduced goal expectations as the main prediction."""
    sim = _MatchSim(store, home, away, neutral, context, out_home, out_away)
    favside = "home" if sim.lh >= sim.la else "away"
    tid, reg = sim.ids[favside], sim.regs[favside]
    outs_fav = set((out_home or []) if favside == "home" else (out_away or []))
    top = next((p for p, _ in _team_shares(store, tid, reg) if p not in outs_fav), None)

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
            r = sim.parlay(legs)
        except (ValueError, KeyError):
            continue
        p = r["joint_prob"]
        if p < MIN_PARLAY_PROB or not r["fair_odds"]:
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
    return {"parlays": out, "n_sims": sim.n, "min_prob": MIN_PARLAY_PROB}


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

    # --- how the game opens: first goal + the half-time picture ---
    # First scorer: both sides' scoring follow the same in-game time profile
    # (the goal-flow weighting), so the profile cancels and the race is decided
    # by the intensity ratio. "Neither scores" is the matrix's own 0-0 cell, so
    # this agrees exactly with the displayed scoreline probabilities.
    p_00 = float(mat[0, 0])
    rate = lh / max(lh + la, 1e-9)
    mk["first_goal"] = {
        "home": round((1 - p_00) * rate, 4),
        "away": round((1 - p_00) * (1 - rate), 4),
        "none": round(p_00, 4),
    }
    # Half-time: goals cluster late, so the first half carries the measured
    # first-half share of scoring; the parlay legs use the same number, so both
    # endpoints price half-time identically.
    h1 = store.first_half_share or FIRST_HALF_GOAL_SHARE
    ht_mat = score_matrix(lh * h1, la * h1)
    hi, hj = np.unravel_index(int(ht_mat.argmax()), ht_mat.shape)
    mk["half_time"] = {
        "home": round(float(np.tril(ht_mat, -1).sum()), 4),
        "draw": round(float(np.trace(ht_mat)), 4),
        "away": round(float(np.triu(ht_mat, 1).sum()), 4),
        "score": f"{int(hi)}-{int(hj)}",
        "score_prob": round(float(ht_mat[hi, hj]), 4),
        "over_05": round(1 - float(ht_mat[0, 0]), 4),
        "over_15": round(float(ht_mat[(np.arange(ht_mat.shape[0])[:, None]
                                       + np.arange(ht_mat.shape[1])[None, :]) > 1.5].sum()), 4),
    }

    rh, ra = store.registry[home], store.registry[away]
    scorers, assisters = {}, {}
    for tid, reg, lam in ((home, rh, lh), (away, ra, la)):
        scorers[reg["name"]] = likely_scorers(store, tid, reg, lam)
        assisters[reg["name"]] = likely_assisters(store, tid, lam)
    scorers_from_xg = home in store.player_rates or away in store.player_rates

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
        assisters = {t: [x for x in ps if x["player"] not in out_all] for t, ps in assisters.items()}

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
            caveats.append(f"Adjusted for {a['player']} OUT: {reg['name']}'s goal numbers "
                           "were reduced for the absence.")
        priced = {a["player"] for a in side_adj}
        missed = [p for p in side_out if p not in priced]
        if missed:
            caveats.append(f"No per-player goal data for {', '.join(missed)} "
                           f"({reg['name']}): their absence is noted but the goal "
                           "numbers are unchanged.")
    if scorers_from_xg and store.xg_data_to:
        caveats.append(f"Club scorer probabilities use shot data up to {store.xg_data_to}; "
                       "players verifiably absent this season are removed and flagged "
                       "absentees are discounted.")
    for name in eg["unrated"]:
        caveats.append(f"No recent results for {name}: league-average attack and defence assumed.")
    if eg["scope_mix"]:
        caveats.append("Club vs national team: Elo scales are not directly comparable, so treat this prediction as a rough guide only.")
    if not eg["same_league"] and not eg["scope_mix"]:
        caveats.append("Cross-league matchup: prediction relies mainly on Elo ratings; these teams rarely or never meet in the data.")
    if not eg["same_league"]:
        for reg in (rh, ra):
            if reg.get("elo_basis") == "estimate":
                caveats.append(f"{reg['name']}'s rating sits on the European scale by a documented "
                               f"estimate for {reg['league_name']}, not a measured value.")
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

    def team_block(tid: str, reg: dict) -> dict:
        return {"id": tid, "name": reg["name"], "elo": reg["elo_global"],
                "elo_basis": reg["elo_basis"],
                "league": reg["league_name"], "country": reg["country"]}

    return {
        "home": team_block(home, rh),
        "away": team_block(away, ra),
        "neutral_venue": neutral,
        "expected_goals": {"home": round(lh, 2), "away": round(la, 2)},
        "model_detail": eg["components"] | {"elo_diff": eg["elo_diff"], "uses_xg": eg["uses_xg"],
                                            "first_half_share": h1},
        "context_applied": eg.get("context"),
        "verdict": {"call": verdict, "confidence": round(max(n1x2["home"], n1x2["draw"], n1x2["away"]), 3),
                    "predicted_score": mk["correct_scores"][0]["score"]},
        "margin_note": margin_note,
        "markets": mk,
        "score_matrix": [[round(float(mat[i, j]), 5) for j in range(7)] for i in range(7)],
        "likely_scorers": scorers,
        "likely_assisters": assisters,
        "absences": {"home": adj_home, "away": adj_away},
        "caveats": caveats,
    }
