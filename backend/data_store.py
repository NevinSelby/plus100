"""Unified data store: loads every data source, normalizes team names,
computes Elo ratings and time-weighted attack/defence strengths.

All heavy computation happens once at startup and is cached to a pickle.
"""
from __future__ import annotations

import gc
import math
import pickle
import re
import unicodedata
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd

import os

# Small cloud instances (512 MB) cannot hold the full history while building.
# LOW_MEM trims to recent seasons and skips the on-disk cache, which is useless
# there anyway because the filesystem is wiped on every restart.
LOW_MEM = os.environ.get("PLUS100_LOW_MEM") == "1"
MIN_YEAR = int(os.environ.get("PLUS100_MIN_YEAR", "0") or 0)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE = DATA / "store_cache.pkl"

LEAGUE_NAMES = {
    "E0": ("Premier League", "England"), "E1": ("Championship", "England"),
    "SC0": ("Scottish Premiership", "Scotland"),
    "SP1": ("La Liga", "Spain"), "SP2": ("La Liga 2", "Spain"),
    "D1": ("Bundesliga", "Germany"), "D2": ("2. Bundesliga", "Germany"),
    "I1": ("Serie A", "Italy"), "I2": ("Serie B", "Italy"),
    "F1": ("Ligue 1", "France"), "F2": ("Ligue 2", "France"),
    "N1": ("Eredivisie", "Netherlands"), "P1": ("Primeira Liga", "Portugal"),
    "B1": ("Pro League", "Belgium"), "T1": ("Süper Lig", "Turkey"),
    "G1": ("Super League", "Greece"),
    "USA": ("MLS", "USA"), "BRA": ("Série A", "Brazil"),
    "ARG": ("Primera División", "Argentina"), "MEX": ("Liga MX", "Mexico"),
    "JPN": ("J1 League", "Japan"), "CHN": ("Super League", "China"),
    "DNK": ("Superliga", "Denmark"), "NOR": ("Eliteserien", "Norway"),
    "SWE": ("Allsvenskan", "Sweden"), "FIN": ("Veikkausliiga", "Finland"),
    "IRL": ("Premier Division", "Ireland"), "POL": ("Ekstraklasa", "Poland"),
    "ROU": ("Liga I", "Romania"), "RUS": ("Premier League", "Russia"),
    "AUT": ("Bundesliga", "Austria"), "SWZ": ("Super League", "Switzerland"),
    "INTL": ("International", "World"),
}

# Absolute Elo anchors (ClubElo scale) for leagues ClubElo does not cover.
# Rough consensus estimates of average squad strength; documented approximations.
NON_EURO_LEAGUE_ANCHOR = {
    "BRA": 1720, "ARG": 1650, "MEX": 1600, "USA": 1530, "JPN": 1550, "CHN": 1420,
}

# Understat league file -> our league code
UNDERSTAT_LEAGUES = {
    "epl": "E0", "la_liga": "SP1", "bundesliga": "D1",
    "serie_a": "I1", "ligue_1": "F1", "rfpl": "RUS",
}

# Understat team name -> football-data.co.uk name, where normalization fails
UNDERSTAT_ALIASES = {
    "manchester united": "man united", "manchester city": "man city",
    "wolverhampton wanderers": "wolves", "newcastle united": "newcastle",
    "west bromwich albion": "west brom", "nottingham forest": "nott'm forest",
    "queens park rangers": "qpr", "sheffield united": "sheffield united",
    "atletico madrid": "ath madrid", "athletic club": "ath bilbao",
    "real sociedad": "sociedad", "rayo vallecano": "vallecano",
    "celta vigo": "celta", "espanyol": "espanol", "real betis": "betis",
    "deportivo la coruna": "la coruna", "sporting gijon": "sp gijon",
    "borussia m.gladbach": "m'gladbach", "borussia monchengladbach": "m'gladbach",
    "bayer leverkusen": "leverkusen", "eintracht frankfurt": "ein frankfurt",
    "fortuna duesseldorf": "fortuna dusseldorf", "vfb stuttgart": "stuttgart",
    "hertha berlin": "hertha", "schalke 04": "schalke 04", "fc cologne": "fc koln",
    "paris saint germain": "paris sg", "saint-etienne": "st etienne",
    "ac milan": "milan", "inter": "inter", "as roma": "roma",
    "parma calcio 1913": "parma", "spal 2013": "spal",
    "borussia dortmund": "dortmund", "hannover 96": "hannover",
    "hamburger sv": "hamburg", "mainz 05": "mainz",
    "rasenballsport leipzig": "rb leipzig", "nuernberg": "nurnberg",
    "arminia bielefeld": "bielefeld", "greuther fuerth": "greuther furth",
    "fc heidenheim": "heidenheim", "real valladolid": "valladolid",
    "sd huesca": "huesca", "real oviedo": "oviedo",
    "clermont foot": "clermont", "gfc ajaccio": "ajaccio gfco", "sc bastia": "bastia",
    "zenit st. petersburg": "zenit", "dinamo moscow": "dynamo moscow",
    "fc krasnodar": "krasnodar", "fc rostov": "rostov", "fc ufa": "ufa",
    "fc orenburg": "orenburg", "fk akhmat": "akhmat grozny",
    "krylya sovetov samara": "krylya sovetov",
    "anzhi makhachkala": "fk anzi makhackala", "pfc sochi": "sochi",
    "nizhny novgorod": "pari nn", "fc rostov": "fk rostov",
    "fakel": "fakel voronezh", "akron": "akron togliatti",
    "kuban krasnodar": "kuban", "tom tomsk": "tomsk",
    "fc tambov": "tambov", "mordovya": "m. saransk",
    "fc yenisey krasnoyarsk": "yenisey", "fc rotor volgograd": "r. volgograd",
}


# football-data.co.uk name -> ClubElo name, for teams where normalization fails
CLUBELO_ALIASES = {
    "man united": "manunited", "man city": "mancity",
    "nott'm forest": "forest", "nottm forest": "forest",
    "sheffield united": "sheffieldunited", "sheffield weds": "sheffieldweds",
    "ath madrid": "atletico", "ath bilbao": "bilbao", "espanol": "espanyol",
    "sociedad": "realsociedad", "betis": "betis", "vallecano": "rayovallecano",
    "ein frankfurt": "frankfurt", "leverkusen": "leverkusen",
    "m'gladbach": "gladbach", "mgladbach": "gladbach", "fc koln": "koeln",
    "st pauli": "stpauli", "mainz": "mainz",
    "paris sg": "parissg", "st etienne": "saintetienne",
    "sp lisbon": "sporting", "sp braga": "braga",
    "inter": "inter", "milan": "milan", "roma": "roma",
    "psv eindhoven": "psv", "for sittard": "fortunasittard",
    "waregem": "zultewaregem", "fenerbahce": "fenerbahce",
    "besiktas": "besiktas", "galatasaray": "galatasaray",
}


def norm_key(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _read_csv(path: Path, **kw) -> pd.DataFrame:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(path, encoding=enc, on_bad_lines="skip",
                               engine="python", **kw)
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"cannot read {path}")



# The raw match files carry a column per bookmaker (about 100 in recent seasons).
# Reading only what the model uses keeps peak memory small enough for modest hosts.
_CLUB_COLS = {
    "div", "date", "time", "hometeam", "awayteam", "fthg", "ftag",
    "psh", "psd", "psa", "b365h", "b365d", "b365a", "whh", "whd", "wha",
    "avgh", "avgd", "avga", "hc", "ac", "hy", "ay", "hr", "ar",
}
_EXTRA_COLS = {
    "country", "league", "season", "date", "time", "home", "away", "hg", "ag",
    "psch", "pscd", "psca", "avgch", "avgcd", "avgca",
}


def _wanted(cols: set):
    return lambda c: str(c).strip().lower() in cols

def load_club_matches() -> pd.DataFrame:
    frames = []
    for f in sorted((DATA / "club").glob("*.csv")):
        lg, season = f.stem.split("_")
        if MIN_YEAR and len(season) == 4 and season.isdigit():
            start = 2000 + int(season[:2])          # "1011" -> 2010/11 season
            if start < MIN_YEAR:
                continue                            # never read it at all
        df = _read_csv(f, usecols=_wanted(_CLUB_COLS))
        df.columns = [c.strip() for c in df.columns]
        need = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
        if not need.issubset(df.columns):
            continue
        odds_h = next((c for c in ("PSH", "B365H", "WHH", "AvgH") if c in df.columns), None)
        sub = pd.DataFrame({
            "date": pd.to_datetime(df["Date"], format="mixed", dayfirst=True, errors="coerce"),
            "home": df["HomeTeam"].astype(str).str.strip(),
            "away": df["AwayTeam"].astype(str).str.strip(),
            "hg": pd.to_numeric(df["FTHG"], errors="coerce"),
            "ag": pd.to_numeric(df["FTAG"], errors="coerce"),
        })
        if odds_h:
            base = odds_h[:-1]
            sub["oddsH"] = pd.to_numeric(df.get(base + "H"), errors="coerce")
            sub["oddsD"] = pd.to_numeric(df.get(base + "D"), errors="coerce")
            sub["oddsA"] = pd.to_numeric(df.get(base + "A"), errors="coerce")
        # corners and cards (main European league files only)
        for col, src in (("hc", "HC"), ("ac", "AC"), ("hy", "HY"), ("ay", "AY"),
                         ("hred", "HR"), ("ared", "AR")):
            sub[col] = pd.to_numeric(df.get(src), errors="coerce")
        sub["league"] = lg
        sub["season"] = season
        for c in ("home", "away"):
            sub[c] = sub[c].astype("category")
        for c in ("oddsH", "oddsD", "oddsA"):
            if c in sub.columns:
                sub[c] = sub[c].astype("float32")
        frames.append(sub)
        del df
    for f in sorted((DATA / "extra").glob("*.csv")):
        lg = f.stem
        df = _read_csv(f, usecols=_wanted(_EXTRA_COLS))
        df.columns = [c.strip() for c in df.columns]
        if not {"Date", "Home", "Away", "HG", "AG"}.issubset(df.columns):
            continue
        sub = pd.DataFrame({
            "date": pd.to_datetime(df["Date"], format="mixed", dayfirst=True, errors="coerce"),
            "home": df["Home"].astype(str).str.strip(),
            "away": df["Away"].astype(str).str.strip(),
            "hg": pd.to_numeric(df["HG"], errors="coerce"),
            "ag": pd.to_numeric(df["AG"], errors="coerce"),
            "oddsH": pd.to_numeric(df.get("PSCH", df.get("AvgCH")), errors="coerce"),
            "oddsD": pd.to_numeric(df.get("PSCD", df.get("AvgCD")), errors="coerce"),
            "oddsA": pd.to_numeric(df.get("PSCA", df.get("AvgCA")), errors="coerce"),
        })
        sub["league"] = lg
        sub["season"] = df.get("Season", "").astype(str)
        frames.append(sub)
    out = pd.concat(frames, ignore_index=True)
    out = out.dropna(subset=["date", "hg", "ag"])
    out = out[(out.home != "nan") & (out.away != "nan") & (out.home != "") & (out.away != "")]
    out["hg"] = out.hg.astype(int)
    out["ag"] = out.ag.astype(int)
    out["scope"] = "club"
    out["neutral"] = False
    out["tournament"] = ""
    return out.sort_values("date").reset_index(drop=True)


def load_intl_matches() -> pd.DataFrame:
    df = _read_csv(DATA / "international" / "results.csv")
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"], errors="coerce"),
        "home": df["home_team"].astype(str).str.strip(),
        "away": df["away_team"].astype(str).str.strip(),
        "hg": pd.to_numeric(df["home_score"], errors="coerce"),
        "ag": pd.to_numeric(df["away_score"], errors="coerce"),
        "tournament": df["tournament"].astype(str),
        "neutral": df["neutral"].astype(str).str.upper() == "TRUE",
    })
    out = out.dropna(subset=["date", "hg", "ag"])
    out["hg"] = out.hg.astype(int)
    out["ag"] = out.ag.astype(int)
    out["league"] = "INTL"
    out["season"] = out.date.dt.year.astype(str)
    out["scope"] = "intl"
    return out.sort_values("date").reset_index(drop=True)


def intl_k(tournament: str) -> float:
    t = tournament.lower()
    if "world cup" in t and "qualification" not in t:
        return 60.0
    if any(x in t for x in ("euro", "copa américa", "copa america", "afc asian cup",
                            "african cup", "africa cup", "gold cup")) and "qualification" not in t:
        return 50.0
    if "qualification" in t or "nations league" in t:
        return 40.0
    if "friendly" in t:
        return 20.0
    return 30.0


def compute_elo(matches: pd.DataFrame):
    """Chronological Elo pass. Clubs and national teams use separate pools.
    Returns final ratings dict and per-match pre-game elo diff arrays
    (used to fit the elo -> expected-goals mapping).
    """
    ratings: dict[str, float] = {}
    # only the recent tail is ever displayed; a bounded deque keeps this from
    # growing to millions of points on a full history rebuild
    hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=90))
    n = len(matches)
    pre_diff = np.zeros(n)
    dates = matches.date.to_numpy()
    homes = matches.home_id.to_numpy()
    aways = matches.away_id.to_numpy()
    hgs = matches.hg.to_numpy()
    ags = matches.ag.to_numpy()
    scopes = matches.scope.to_numpy()
    neutrals = matches.neutral.to_numpy()
    tours = matches.tournament.to_numpy()

    HA_CLUB, HA_INTL = 60.0, 55.0
    for i in range(n):
        h, a = homes[i], aways[i]
        rh = ratings.get(h, 1500.0)
        ra = ratings.get(a, 1500.0)
        ha = 0.0 if neutrals[i] else (HA_INTL if scopes[i] == "intl" else HA_CLUB)
        d = rh + ha - ra
        pre_diff[i] = d
        exp_h = 1.0 / (1.0 + 10 ** (-d / 400.0))
        gd = hgs[i] - ags[i]
        score_h = 1.0 if gd > 0 else (0.0 if gd < 0 else 0.5)
        k = intl_k(tours[i]) if scopes[i] == "intl" else 20.0
        k *= math.sqrt(max(abs(gd), 1))  # margin-of-victory multiplier
        delta = k * (score_h - exp_h)
        ratings[h] = rh + delta
        ratings[a] = ra - delta
        hist[h].append((dates[i], ratings[h]))
        hist[a].append((dates[i], ratings[a]))
    return ratings, pre_diff, {k: list(v) for k, v in hist.items()}


def fit_elo_goals(diff: np.ndarray, hg: np.ndarray, ag: np.ndarray):
    """Fit log-linear maps  log E[home goals] = a + b*d,  log E[away goals] = c - e*d
    where d = pre-match elo diff (incl. home advantage) / 400."""
    d = diff / 400.0
    qs = np.quantile(d, np.linspace(0.02, 0.98, 25))
    idx = np.digitize(d, qs)
    xs, yh, ya, w = [], [], [], []
    for b in range(idx.max() + 1):
        m = idx == b
        if m.sum() < 30:
            continue
        xs.append(d[m].mean())
        yh.append(max(hg[m].mean(), 0.05))
        ya.append(max(ag[m].mean(), 0.05))
        w.append(m.sum())
    xs, w = np.array(xs), np.array(w)
    bh = np.polyfit(xs, np.log(yh), 1, w=np.sqrt(w))  # [b, a]
    ba = np.polyfit(xs, np.log(ya), 1, w=np.sqrt(w))  # [-e, c]
    return {"a": bh[1], "b": bh[0], "c": ba[1], "e": -ba[0]}


def compute_strengths(m: pd.DataFrame, half_life_days: float = 420.0,
                      ref_date=None):
    """Time-weighted attack/defence strengths relative to league average.
    ref_date limits data to matches before it and anchors the decay weights
    (pass a cutoff to compute strengths "as of" that date, e.g. for backtests).
    """
    if ref_date is not None:
        m = m[m.date < ref_date]
        now = pd.Timestamp(ref_date)
    else:
        now = m.date.max()
    age = (now - m.date).dt.days.to_numpy()
    w = 0.5 ** (age / half_life_days)
    keep = w > 0.01
    mm = m[keep].copy()
    mm["w"] = w[keep]

    # league weighted average goals (home & away)
    lg_stats = {}
    for lg, g in mm.groupby("league", observed=True):
        sw = g.w.sum()
        lg_stats[lg] = {"avg_hg": float((g.hg * g.w).sum() / sw),
                        "avg_ag": float((g.ag * g.w).sum() / sw)}

    att: dict[str, float] = {}
    deff: dict[str, float] = {}
    rows = []
    for side, gcol, ccol in (("home_id", "hg", "ag"), ("away_id", "ag", "hg")):
        g = mm.groupby(side, observed=True).apply(
            lambda x: pd.Series({
                "gs": (x[gcol] * x.w).sum(), "gc": (x[ccol] * x.w).sum(),
                "sw": x.w.sum(), "league": x.league.iloc[-1]}),
            include_groups=False)
        rows.append(g)
    num = ["gs", "gc", "sw"]
    agg = rows[0][num].add(rows[1][num], fill_value=0.0)
    agg["league"] = rows[0].league.combine_first(rows[1].league)
    for tid, r in agg.iterrows():
        ls = lg_stats.get(r.league, {"avg_hg": 1.5, "avg_ag": 1.15})
        lam = (ls["avg_hg"] + ls["avg_ag"]) / 2.0
        sw = max(r.sw, 1e-9)
        # shrink toward league average for teams with little recent data
        prior_w = 8.0
        att[tid] = float(((r.gs / sw) * sw + lam * prior_w) / (sw + prior_w) / lam)
        deff[tid] = float(((r.gc / sw) * sw + lam * prior_w) / (sw + prior_w) / lam)
    return att, deff, lg_stats


class Store:
    def __init__(self):
        club = load_club_matches()
        intl = load_intl_matches()
        m = pd.concat([club, intl], ignore_index=True).sort_values("date").reset_index(drop=True)

        # team ids: clubs may share names across countries rarely; key by name+scope
        m["home_id"] = m.home.map(slug) + np.where(m.scope == "intl", "@intl", "")
        m["away_id"] = m.away.map(slug) + np.where(m.scope == "intl", "@intl", "")
        if MIN_YEAR:
            m = m[m.date.dt.year >= MIN_YEAR].reset_index(drop=True)
        for col in ("home", "away", "league", "season", "tournament",
                    "home_id", "away_id", "scope"):
            if col in m.columns:
                m[col] = m[col].astype("category")
        for col in ("hg", "ag", "hc", "ac", "hy", "ay", "hred", "ared"):
            if col in m.columns:
                m[col] = pd.to_numeric(m[col], errors="coerce").fillna(-1).astype("int16")
        for col in ("oddsH", "oddsD", "oddsA"):
            if col in m.columns:
                m[col] = m[col].astype("float32")
        self.matches = m

        self.ratings, pre_diff, self.elo_hist = compute_elo(m)
        m["pre_elo_diff"] = pre_diff

        recent = m[(m.date >= "2008-01-01") & (m.scope == "club")]
        self.goal_fit_club = fit_elo_goals(recent.pre_elo_diff.to_numpy(),
                                           recent.hg.to_numpy(), recent.ag.to_numpy())
        rintl = m[(m.date >= "1990-01-01") & (m.scope == "intl")]
        self.goal_fit_intl = fit_elo_goals(rintl.pre_elo_diff.to_numpy(),
                                           rintl.hg.to_numpy(), rintl.ag.to_numpy())

        self._build_registry()
        self._calibrate_leagues()
        self._compute_strengths()
        self._compute_extras()
        self._load_scorers()
        self._fit_contexts()
        self._live_eval()
        self._load_xg()

    # ---------- registry ----------
    def _build_registry(self):
        m = self.matches
        last_date = m.date.max()
        reg: dict[str, dict] = {}
        for side in ("home", "away"):
            grp = m.groupby(f"{side}_id", observed=True).agg(
                name=(side, "last"), league=("league", "last"),
                scope=("scope", "last"), last=("date", "max"), n=("hg", "size"))
            for tid, row in grp.iterrows():
                r = reg.setdefault(tid, {"id": tid, "name": row["name"], "n": 0,
                                         "league": row["league"], "scope": row["scope"],
                                         "last": row["last"]})
                r["n"] += int(row["n"])
                if row["last"] >= r["last"]:
                    r["last"], r["league"], r["name"] = row["last"], row["league"], row["name"]
        for tid, r in reg.items():
            lg, country = LEAGUE_NAMES.get(r["league"], (r["league"], ""))
            r["league_name"], r["country"] = lg, country
            r["elo"] = round(self.ratings.get(tid, 1500.0), 1)
            r["active"] = bool((last_date - r["last"]).days < 550)
            r["last"] = str(pd.Timestamp(r["last"]).date())
        self.registry = reg

    # ---------- cross-league calibration via ClubElo ----------
    def _calibrate_leagues(self):
        ce_path = DATA / "elo" / "clubelo.csv"
        if ce_path.exists() and ce_path.stat().st_size > 1000:
            ce = _read_csv(ce_path)
            ce_map = {norm_key(c): e for c, e in zip(ce.Club, ce.Elo)}
        else:
            # ClubElo unreachable: fall back to internal Elo only. Same-league
            # predictions are unaffected; European cross-league anchoring is
            # slightly weaker until the next successful refresh.
            print("WARNING: clubelo.csv missing/empty — skipping ClubElo anchoring")
            ce_map = {}
        offsets: dict[str, list] = defaultdict(list)
        self.clubelo: dict[str, float] = {}
        for tid, r in self.registry.items():
            if r["scope"] != "club" or not r["active"]:
                continue
            k = norm_key(r["name"])
            k = CLUBELO_ALIASES.get(k.replace("fc", "").strip(), CLUBELO_ALIASES.get(k, k))
            e = ce_map.get(k) or ce_map.get(norm_key(CLUBELO_ALIASES.get(r["name"].lower(), "")))
            if e:
                self.clubelo[tid] = float(e)
                offsets[r["league"]].append(float(e) - r["elo"])
        self.league_offset: dict[str, float] = {}
        for lg, vals in offsets.items():
            if len(vals) >= 4:
                self.league_offset[lg] = float(np.median(vals))
        # leagues ClubElo doesn't cover: anchor league mean to documented estimate
        for lg, anchor in NON_EURO_LEAGUE_ANCHOR.items():
            elos = [r["elo"] for r in self.registry.values()
                    if r["league"] == lg and r["active"]]
            if elos:
                self.league_offset[lg] = anchor - float(np.mean(elos))
        # remaining leagues (incl. INTL): no offset
        for tid, r in self.registry.items():
            base = self.clubelo.get(tid)
            r["elo_global"] = round(base if base else r["elo"] + self.league_offset.get(r["league"], 0.0), 1)

    # ---------- attack / defence strengths ----------
    def _compute_strengths(self, half_life_days: float = 420.0):
        self.attack, self.defence, self.league_stats = compute_strengths(
            self.matches, half_life_days=half_life_days)

    # ---------- corners & cards rates (for parlay simulation) ----------
    def _compute_extras(self, half_life_days: float = 420.0):
        m = self.matches
        mm = m[(m.scope == "club") & m.hc.notna()].copy()
        now = m.date.max()
        mm["w"] = 0.5 ** ((now - mm.date).dt.days / half_life_days)
        mm = mm[mm.w > 0.01]
        mm["h_cards"] = mm.hy.fillna(0) + mm.hred.fillna(0)
        mm["a_cards"] = mm.ay.fillna(0) + mm.ared.fillna(0)
        self.extras_league = {
            "corners_home": float((mm.hc * mm.w).sum() / mm.w.sum()),
            "corners_away": float((mm.ac * mm.w).sum() / mm.w.sum()),
            "cards_home": float((mm.h_cards * mm.w).sum() / mm.w.sum()),
            "cards_away": float((mm.a_cards * mm.w).sum() / mm.w.sum()),
        }
        ex: dict[str, dict] = {}
        for side, cf, ca, cards in (("home_id", "hc", "ac", "h_cards"),
                                    ("away_id", "ac", "hc", "a_cards")):
            for tid, g in mm.groupby(side, observed=True):
                sw = g.w.sum()
                if sw < 3:
                    continue
                r = ex.setdefault(tid, {"cf": 0.0, "ca": 0.0, "cards": 0.0, "n": 0})
                r["cf"] += float((g[cf] * g.w).sum() / sw)
                r["ca"] += float((g[ca] * g.w).sum() / sw)
                r["cards"] += float((g[cards] * g.w).sum() / sw)
                r["n"] += 1
        # average home-side and away-side rates where both present
        self.team_extras = {tid: {k: v / r["n"] for k, v in r.items() if k != "n"}
                            for tid, r in ex.items() if r["n"] > 0}

    # ---------- international goalscorers ----------
    def _load_scorers(self):
        """Recency-weighted goal records (half-life 18 months). Caps/appearance
        data does not exist in any free source for internationals, so the model
        uses weighted goal share; players whose last goal is >30 months old are
        treated as no longer in the squad picture."""
        df = _read_csv(DATA / "international" / "goalscorers.csv")
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date", "scorer"])
        now = df.date.max()
        df = df[df.date >= (now - pd.Timedelta(days=365 * 6))]
        df = df[(df.own_goal.astype(str).str.upper() != "TRUE")]
        df["w"] = 0.5 ** ((now - df.date).dt.days / 540.0)
        g = df.groupby(["team", "scorer"], observed=True).agg(
            goals=("w", "size"), wgoals=("w", "sum"), last=("date", "max"),
            goals_2y=("date", lambda d: int((d >= now - pd.Timedelta(days=730)).sum())),
        ).reset_index()
        g = g[g["last"] >= now - pd.Timedelta(days=913)]  # last goal within 30 months
        self.scorer_goals = g
        # matches played per team over same window (for rate denominators)
        m = self.matches
        recent = m[(m.scope == "intl") & (m.date >= (m.date.max() - pd.Timedelta(days=365 * 4)))]
        played = pd.concat([recent.home, recent.away]).value_counts()
        self.intl_matches_played = played.to_dict()

    # ---------- Understat shot-level xG ----------
    def _load_xg(self, half_life_days: float = 500.0):
        """Shot-level xG (Understat via worldfootballR, 2014 -> ~Sep 2025).

        Produces:
          xg_attack / xg_defence  — time-weighted xG for/against relative to league avg
          player_rates            — per team: recent players with their share of team xG
          xg_data_to              — freshness stamp shown in the UI
        """
        # Small hosts load a precomputed artifact instead of parsing 570k shots:
        # the inputs are static (the xG source updates a few times a year), so
        # this is the same numbers without the memory spike.
        pre = DATA / "xg_precomputed.json.gz"
        if LOW_MEM and pre.exists():
            import gzip, json as _json
            with gzip.open(pre, "rt") as fh:
                blob = _json.load(fh)
            self.xg_attack = blob["xg_attack"]
            self.xg_defence = blob["xg_defence"]
            self.player_rates = blob["player_rates"]
            self.xg_data_to = blob["xg_data_to"]
            print(f"[xg] loaded precomputed strengths ({len(self.player_rates)} squads)")
            return

        frames = []
        for us_lg, code in UNDERSTAT_LEAGUES.items():
            f = DATA / "understat" / f"{us_lg}_shots.csv"
            if not f.exists():                       # deployed images ship gzipped
                f = f.with_suffix(".csv.gz")
                if not f.exists():
                    continue
            # only the columns the strengths and player rates actually need:
            # the full shot files carry coordinates and metadata we never read,
            # and loading them all is what pushes peak memory over small hosts
            df = _read_csv(f, usecols=["date", "xG", "player", "h_a", "result",
                                       "match_id", "home_team", "away_team"],
                           dtype={"player": "category", "home_team": "category",
                                  "away_team": "category", "result": "category",
                                  "h_a": "category"})
            df["league"] = code
            frames.append(df)
        if not frames:
            self.xg_attack, self.xg_defence, self.player_rates = {}, {}, {}
            self.xg_data_to = None
            return
        shots = pd.concat(frames, ignore_index=True)
        del frames
        shots["date"] = pd.to_datetime(shots.date, errors="coerce")
        shots = shots.dropna(subset=["date", "xG"])
        self.xg_data_to = str(shots.date.max().date())

        # map Understat team names -> registry ids
        club_by_key = {norm_key(r["name"]): tid for tid, r in self.registry.items()
                       if r["scope"] == "club"}

        def to_tid(name: str):
            k = norm_key(name)
            k = norm_key(UNDERSTAT_ALIASES.get(str(name).lower(), "")) or k
            return club_by_key.get(k)

        team_names = pd.unique(pd.concat([shots.home_team, shots.away_team]))
        tid_map = {n: to_tid(n) for n in team_names}
        unmatched = [n for n, t in tid_map.items() if t is None]
        if unmatched:
            print(f"[xg] unmatched understat teams ({len(unmatched)}): {unmatched[:12]}")
        shots["home_id"] = shots.home_team.map(tid_map)
        shots["away_id"] = shots.away_team.map(tid_map)
        shots["team_id"] = np.where(shots.h_a == "h", shots.home_id, shots.away_id)

        # per-match team xG totals
        agg = shots.groupby(["match_id", "league", "home_id", "away_id"], dropna=True, observed=True).apply(
            lambda g: pd.Series({
                "date": g.date.iloc[0],
                "home_xg": g.xG[g.h_a == "h"].sum(),
                "away_xg": g.xG[g.h_a == "a"].sum(),
            }), include_groups=False).reset_index()

        now = self.matches.date.max()
        agg["w"] = 0.5 ** ((now - agg.date).dt.days.clip(lower=0) / half_life_days)
        agg = agg[agg.w > 0.01]

        xga, xgd = {}, {}
        for lg, g in agg.groupby("league", observed=True):
            sw = g.w.sum()
            avg_h, avg_a = (g.home_xg * g.w).sum() / sw, (g.away_xg * g.w).sum() / sw
            lam = (avg_h + avg_a) / 2
            for side, xg_for, xg_ag in (("home_id", "home_xg", "away_xg"),
                                        ("away_id", "away_xg", "home_xg")):
                for tid, tg in g.groupby(side, observed=True):
                    tw = tg.w.sum()
                    for_r = (tg[xg_for] * tg.w).sum()
                    ag_r = (tg[xg_ag] * tg.w).sum()
                    prior = 8.0
                    a = (for_r + lam * prior) / (tw + prior) / lam
                    d = (ag_r + lam * prior) / (tw + prior) / lam
                    xga[tid] = (xga.get(tid, 0) + a) / (2 if tid in xga else 1)
                    xgd[tid] = (xgd.get(tid, 0) + d) / (2 if tid in xgd else 1)
        self.xg_attack, self.xg_defence = xga, xgd

        # player scoring rates. Per-appearance xG rate (not raw share), shrunk
        # toward a low prior for small samples so a two-match hot streak cannot
        # outrank an established starter, then scaled by availability (how often
        # the player has featured in the team's recent matches).
        end = shots.date.max()
        recent = shots[shots.date >= end - pd.Timedelta(days=450)].copy()
        recent["w"] = 0.5 ** ((end - recent.date).dt.days / 300.0)
        recent = recent.dropna(subset=["team_id"])
        PRIOR_RATE, PRIOR_APPS = 0.08, 4.0   # xG/match prior, pseudo-appearances
        rates: dict[str, list] = {}
        for tid, g in recent.groupby("team_id", observed=True):
            # team appearance mass: one weight per distinct match
            match_w = g.groupby("match_id", observed=True).w.max()
            team_w_matches = float(match_w.sum())
            if team_w_matches <= 0:
                continue
            rows = []
            for player, pg in g.groupby("player", observed=True):
                if (end - pg.date.max()).days > 200:
                    continue  # not seen recently -> likely departed
                pxg = float((pg.xG * pg.w).sum())
                p_match_w = pg.groupby("match_id", observed=True).w.max()
                w_apps = float(p_match_w.sum())
                apps = int(pg.match_id.nunique())
                goals = int((pg.result == "Goal").sum())
                sot = float((pg.result.isin(("Goal", "SavedShot")) * pg.w).sum())
                # shrunk xG-per-appearance, then weight by how often they play
                rate = (pxg + PRIOR_RATE * PRIOR_APPS) / (w_apps + PRIOR_APPS)
                avail = min(1.0, w_apps / team_w_matches)
                rows.append({"player": player, "contrib": rate * avail,
                             "apps": apps, "recent_goals": goals,
                             "recent_xg": round(pxg, 2),
                             "xg_per_match": round(pxg / max(w_apps, 0.5), 2),
                             "sot_rate": round(sot / max(w_apps, 0.5), 3)})
            total = sum(r["contrib"] for r in rows)
            if total <= 0:
                continue
            for r in rows:
                r["xg_share"] = r.pop("contrib") / total
            rows.sort(key=lambda r: -r["xg_share"])
            rates[tid] = rows[:10]
        self.player_rates = rates
        del shots, recent
        gc.collect()

    # ---------- match-context scoring environments ----------
    def _fit_contexts(self):
        """Fitted multipliers for how the match context shifts expected goals.
        Fitted from history where possible; research-based estimates are labeled."""
        m = self.matches
        intl = m[(m.scope == "intl") & (m.date >= "1995-01-01")]
        pooled = float((intl.hg + intl.ag).mean())

        def env_of(t):
            tl = t.lower()
            if "friendly" in tl:
                return "friendly"
            if "qualification" in tl:
                return "qualifier"
            return "finals"

        envs = intl.tournament.map(env_of)
        scales = {}
        for e in ("friendly", "qualifier", "finals"):
            g = intl[envs == e]
            scales[e] = {"scale": round(float((g.hg + g.ag).mean()) / pooled, 3),
                         "fitted": True, "n": int(len(g))}

        # third-place playoffs + finals of major tournaments (heuristic labeling)
        tp_rows, fin_rows = [], []
        for stem in ("FIFA World Cup", "Copa Am", "UEFA Euro", "African Cup", "AFC Asian Cup"):
            tm = m[(m.tournament.str.contains(stem.split()[0], na=False)) &
                   (~m.tournament.str.contains("qual", case=False, na=False))]
            for _, g in tm.groupby(tm.date.dt.year, observed=True):
                if len(g) < 8:
                    continue
                gg = g.sort_values("date")
                final_day = gg.date.max()
                final_match = gg[gg.date == final_day].iloc[-1]
                finalists = {final_match.home, final_match.away}
                fin_rows.append(final_match)
                cand = gg[(gg.date >= final_day - pd.Timedelta(days=2)) & (gg.date < final_day)]
                cand = cand[~cand.home.isin(finalists) & ~cand.away.isin(finalists)]
                if len(cand) == 1:
                    tp_rows.append(cand.iloc[0])
        finals_scale = scales["finals"]["scale"]
        if tp_rows:
            tp = pd.DataFrame(tp_rows)
            scales["third_place"] = {
                "scale": round(float((tp.hg + tp.ag).mean()) / pooled, 3),
                "fitted": True, "n": len(tp)}
        else:
            scales["third_place"] = {"scale": round(finals_scale * 1.16, 3),
                                     "fitted": False, "n": 0}
        if fin_rows:
            fin = pd.DataFrame(fin_rows)
            scales["final"] = {
                "scale": round(float((fin.hg + fin.ag).mean()) / pooled, 3),
                "fitted": True, "n": len(fin)}
        # club contexts: research-based estimates (no clean labels in the data)
        scales["dead_rubber"] = {"scale": 1.08, "fitted": False, "n": 0}
        scales["derby"] = {"scale": 0.95, "fitted": False, "n": 0}
        self.context_scales = scales

    # ---------- continuously re-measured accuracy ----------
    def _live_eval(self, days: int = 180):
        """Quick walk-forward evaluation on the most recent matches with odds.
        Re-runs at every store rebuild so the About page is never stale.
        Uses the Elo-based estimate (inherently walk-forward)."""
        from .model import score_matrix  # lazy: avoids circular import at load
        m = self.matches
        club = m[m.scope == "club"].dropna(subset=["oddsH", "oddsD", "oddsA"])
        club = club[(club.oddsH > 1) & (club.oddsD > 1) & (club.oddsA > 1)]
        recent = club[club.date >= club.date.max() - pd.Timedelta(days=days)]
        if len(recent) < 200:
            self.live_eval = None
            return
        d = recent.pre_elo_diff.to_numpy() / 400.0
        fit = self.goal_fit_club
        lh = np.exp(fit["a"] + fit["b"] * d)
        la = np.exp(fit["c"] - fit["e"] * d)
        probs = np.zeros((len(recent), 3))
        for i in range(len(recent)):
            mat = score_matrix(float(lh[i]), float(la[i]))
            probs[i] = [np.tril(mat, -1).sum(), np.trace(mat), np.triu(mat, 1).sum()]
        inv = np.column_stack([1 / recent.oddsH, 1 / recent.oddsD, 1 / recent.oddsA])
        book = inv / inv.sum(axis=1, keepdims=True)
        gd = recent.hg.to_numpy() - recent.ag.to_numpy()
        outcome = np.where(gd > 0, 0, np.where(gd == 0, 1, 2))
        onehot = np.eye(3)[outcome]
        self.live_eval = {
            "matches": int(len(recent)),
            "from": str(recent.date.min().date()), "to": str(recent.date.max().date()),
            "model_accuracy": round(float((probs.argmax(1) == outcome).mean()), 4),
            "book_accuracy": round(float((book.argmax(1) == outcome).mean()), 4),
            "model_brier": round(float(((probs - onehot) ** 2).sum(1).mean()), 4),
            "book_brier": round(float(((book - onehot) ** 2).sum(1).mean()), 4),
        }

    # ---------- queries ----------
    def team_matches(self, tid: str) -> pd.DataFrame:
        m = self.matches
        return m[(m.home_id == tid) | (m.away_id == tid)]

    def h2h(self, a: str, b: str) -> pd.DataFrame:
        m = self.matches
        return m[((m.home_id == a) & (m.away_id == b)) |
                 ((m.home_id == b) & (m.away_id == a))]


STORE_CODE_VERSION = 3  # bump when Store gains new computed attributes


def _bootstrap_download() -> None:
    """First boot on a fresh deployment (empty data dir): fetch the full
    historical dataset. Mirrors scripts/download_data.py."""
    import concurrent.futures as cf
    import datetime as _dt

    import requests as _rq

    ua = {"User-Agent": "Mozilla/5.0 (Plus100 bootstrap)"}
    main_lg = ["E0", "E1", "SC0", "SP1", "SP2", "D1", "D2", "I1", "I2",
               "F1", "F2", "N1", "P1", "B1", "T1", "G1"]
    extra_lg = ["USA", "BRA", "ARG", "MEX", "JPN", "CHN", "DNK", "NOR",
                "SWE", "FIN", "IRL", "POL", "ROU", "RUS", "AUT", "SWZ"]
    today = _dt.date.today()
    last_start = today.year if today.month >= 7 else today.year - 1
    first = max(2000, MIN_YEAR) if MIN_YEAR else 2000
    seasons = [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(first, last_start + 1)]
    for sub in ("club", "extra", "international", "elo"):
        (DATA / sub).mkdir(parents=True, exist_ok=True)

    jobs = [(f"https://www.football-data.co.uk/mmz4281/{s}/{lg}.csv",
             DATA / "club" / f"{lg}_{s}.csv") for s in seasons for lg in main_lg]
    jobs += [(f"https://www.football-data.co.uk/new/{lg}.csv",
              DATA / "extra" / f"{lg}.csv") for lg in extra_lg]
    jobs += [(f"https://raw.githubusercontent.com/martj42/international_results/master/{f}",
              DATA / "international" / f)
             for f in ("results.csv", "goalscorers.csv", "shootouts.csv")]
    jobs += [(f"http://api.clubelo.com/{today.isoformat()}", DATA / "elo" / "clubelo.csv")]

    def fetch(url: str, dest: Path) -> None:
        if dest.exists() and dest.stat().st_size > 500:
            return
        try:
            r = _rq.get(url, headers=ua, timeout=60)
            if r.status_code == 200 and len(r.content) > 200:
                dest.write_bytes(r.content)
        except _rq.RequestException:
            pass

    print(f"[bootstrap] downloading full dataset ({len(jobs)} files)…")
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda j: fetch(*j), jobs))
    print("[bootstrap] done")


def get_store(force: bool = False) -> Store:
    if not any((DATA / "club").glob("*.csv")):
        _bootstrap_download()
    files = sorted(DATA.rglob("*.csv"))
    sig = (STORE_CODE_VERSION, len(files), sum(f.stat().st_size for f in files))
    if CACHE.exists() and not force:
        try:
            with open(CACHE, "rb") as fh:
                cached_sig, store = pickle.load(fh)
            if cached_sig == sig:
                return store
        except Exception:  # noqa: BLE001
            pass
    store = Store()
    if not LOW_MEM:
        with open(CACHE, "wb") as fh:
            pickle.dump((sig, store), fh)
    gc.collect()
    return store


if __name__ == "__main__":
    import time
    t0 = time.time()
    s = get_store(force=True)
    print(f"built in {time.time() - t0:.1f}s")
    print(f"matches: {len(s.matches):,}  teams: {len(s.registry):,}")
    print(f"clubelo matched: {len(s.clubelo)}  league offsets: {len(s.league_offset)}")
    print("goal fit club:", {k: round(v, 3) for k, v in s.goal_fit_club.items()})
    print("goal fit intl:", {k: round(v, 3) for k, v in s.goal_fit_intl.items()})
    print(f"xg teams: {len(s.xg_attack)}  player-rate teams: {len(s.player_rates)}  xg data to: {s.xg_data_to}")
    liv = s.player_rates.get("liverpool", [])
    print("Liverpool top scorers:", [(r['player'], round(r['xg_share'], 2)) for r in liv[:5]])
    for name in ("Man United", "Real Madrid", "Bayern Munich", "Brazil", "Argentina"):
        tid = slug(name) + ("@intl" if name in ("Brazil", "Argentina") else "")
        r = s.registry.get(tid)
        if r:
            print(f"{name:15s} elo={r['elo']:7.1f} global={r['elo_global']:7.1f} "
                  f"att={s.attack.get(tid, 0):.2f} def={s.defence.get(tid, 0):.2f}")
