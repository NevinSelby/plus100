"""Automatic data refresh: keeps ratings current without manual steps.

Every REFRESH_HOURS (and once at startup if data is stale), volatile sources are
re-downloaded — current + previous season results for all leagues, the
international dataset, extra leagues, ClubElo — the model store is rebuilt in a
background thread and hot-swapped into the running app.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from pathlib import Path

import requests

from .data_store import CACHE, DATA, EXTRA_LEAGUES, MAIN_LEAGUES, get_store

REFRESH_HOURS = 3
UA = {"User-Agent": "Mozilla/5.0 (Plus100 refresher)"}

state = {
    "last_refresh": None,      # iso str
    "refreshing": False,
    "last_error": None,
}


def _season_codes(today: dt.date) -> list[str]:
    y = today.year if today.month >= 7 else today.year - 1
    cur = f"{y % 100:02d}{(y + 1) % 100:02d}"
    prev = f"{(y - 1) % 100:02d}{y % 100:02d}"
    return [prev, cur]


INTL_FILES = ["results.csv", "goalscorers.csv", "shootouts.csv"]
RECENT_FILE = DATA / "espn_recent.json"


def refresh_recent_results(store) -> bool:
    """The last ten days of finished matches in every covered league, straight
    from ESPN, so a result from this afternoon counts tonight even when the
    weekly history files lag. Written with OUR team names so the store can
    merge them like any other row. Returns True if the file changed."""
    import json
    from . import espn
    from .bestbets import resolve_team
    rows = []
    today = dt.date.today()
    for code in espn.SOCCER_SLUGS:
        if not any(r["league"] == code for r in store.registry.values()):
            continue
        for back in range(10):
            day = today - dt.timedelta(days=back)
            for g in espn.soccer_scores(code, day):
                hid = resolve_team(store, g["home"], "club", code)
                aid = resolve_team(store, g["away"], "club", code)
                if not hid or not aid or hid == aid:
                    continue
                rows.append({"date": g["date"], "league": code,
                             "home": store.registry[hid]["name"], "away": store.registry[aid]["name"],
                             "hg": g["hs"], "ag": g["as"]})
    payload = json.dumps(sorted(rows, key=lambda r: (r["date"], r["home"])))
    old = RECENT_FILE.read_text() if RECENT_FILE.exists() else ""
    if payload != old:
        RECENT_FILE.write_text(payload)
    return payload != old


def _fetch_replace(url: str, dest: Path, timeout: int = 45) -> bool:
    """Download to a temp file; replace dest only on a good response."""
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        if r.status_code == 200 and len(r.content) > 500:
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(r.content)
            changed = not dest.exists() or dest.read_bytes() != r.content
            tmp.replace(dest)
            return changed
    except requests.RequestException:
        pass
    return False


def refresh_files() -> bool:
    """Re-download everything that changes between matchdays. Returns True if
    any file actually changed."""
    changed = False
    today = dt.date.today()
    for season in _season_codes(today):
        for lg in MAIN_LEAGUES:
            changed |= _fetch_replace(
                f"https://www.football-data.co.uk/mmz4281/{season}/{lg}.csv",
                DATA / "club" / f"{lg}_{season}.csv")
    for lg in EXTRA_LEAGUES:
        changed |= _fetch_replace(
            f"https://www.football-data.co.uk/new/{lg}.csv",
            DATA / "extra" / f"{lg}.csv")
    for f in INTL_FILES:
        changed |= _fetch_replace(
            f"https://raw.githubusercontent.com/martj42/international_results/master/{f}",
            DATA / "international" / f)
    changed |= _fetch_replace(f"http://api.clubelo.com/{today.isoformat()}",
                              DATA / "elo" / "clubelo.csv", timeout=90)
    return changed


def refresh_now() -> None:
    """Download fresh data and hot-swap a rebuilt store into the app."""
    from . import app as app_module
    state["refreshing"] = True
    state["last_error"] = None
    try:
        changed = refresh_files()
        try:
            changed |= refresh_recent_results(app_module.store)
        except Exception as e:  # noqa: BLE001
            state["last_error"] = f"recent results: {e}"
        try:
            from .nba import refresh_games             # last night's NBA results
            refresh_games()
        except Exception as e:  # noqa: BLE001
            state["last_error"] = f"nba refresh: {e}"
        try:
            from .xg import refresh as refresh_xg      # live xG strengths + scorers
            changed |= refresh_xg(app_module.store.registry)
        except Exception as e:  # noqa: BLE001
            state["last_error"] = f"xg refresh: {e}"
        if changed:
            CACHE.unlink(missing_ok=True)
            app_module.store = get_store(force=True)
        state["last_refresh"] = dt.datetime.now().isoformat(timespec="seconds")
        state["last_ts"] = time.time()
    except Exception as e:  # noqa: BLE001 — a failed refresh must never kill the loop
        state["last_error"] = str(e)
    finally:
        state["refreshing"] = False
        try:
            from .app import _prewarm
            threading.Thread(target=_prewarm, daemon=True, name="plus100-prewarm").start()
        except Exception:  # noqa: BLE001
            pass


def maybe_refresh() -> None:
    """Called on every request: if the last refresh is older than the cadence
    (a free host sleeps, so timers rarely fire), start one in the background.
    Opening the app is what keeps the data current."""
    if state["refreshing"]:
        return
    if time.time() - state.get("last_ts", 0) < REFRESH_HOURS * 3600:
        return
    state["refreshing"] = True            # claim it before the thread starts
    threading.Thread(target=refresh_now, daemon=True, name="plus100-refresh").start()


def _data_age_days() -> float:
    """Age of the newest RESULT inside the built store. Content-based on
    purpose: file mtimes lie after a git clone (every committed file gets the
    deploy's timestamp), and a fresh deploy of an old data snapshot must still
    refresh itself immediately."""
    try:
        from . import app as app_module
        latest = app_module.store.matches.date.max()
        return (dt.date.today() - latest.date()).days
    except Exception:  # noqa: BLE001
        return 1e9


def start_background() -> None:
    def loop():
        if _data_age_days() > 1:
            refresh_now()
        else:
            state["last_refresh"] = dt.datetime.now().isoformat(timespec="seconds")
            state["last_ts"] = time.time()
        while True:
            time.sleep(REFRESH_HOURS * 3600)
            refresh_now()

    threading.Thread(target=loop, daemon=True, name="plus100-refresher").start()
