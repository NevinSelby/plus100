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

from .data_store import CACHE, DATA, get_store

REFRESH_HOURS = 6
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


MAIN_LEAGUES = ["E0", "E1", "SC0", "SP1", "SP2", "D1", "D2", "I1", "I2",
                "F1", "F2", "N1", "P1", "B1", "T1", "G1"]
EXTRA_LEAGUES = ["USA", "BRA", "ARG", "MEX", "JPN", "CHN", "DNK", "NOR",
                 "SWE", "FIN", "IRL", "POL", "ROU", "RUS", "AUT", "SWZ"]
INTL_FILES = ["results.csv", "goalscorers.csv", "shootouts.csv"]


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
        if changed or not CACHE.exists():
            CACHE.unlink(missing_ok=True)
            new_store = get_store(force=True)
            app_module.store = new_store
        state["last_refresh"] = dt.datetime.now().isoformat(timespec="seconds")
    except Exception as e:  # noqa: BLE001 — a failed refresh must never kill the loop
        state["last_error"] = str(e)
    finally:
        state["refreshing"] = False


def _newest_data_age_hours() -> float:
    files = list((DATA / "international").glob("*.csv"))
    if not files:
        return 1e9
    newest = max(f.stat().st_mtime for f in files)
    return (time.time() - newest) / 3600


def start_background() -> None:
    def loop():
        if _newest_data_age_hours() > REFRESH_HOURS:
            refresh_now()
        else:
            state["last_refresh"] = dt.datetime.fromtimestamp(
                max(f.stat().st_mtime for f in (DATA / "international").glob("*.csv"))
            ).isoformat(timespec="seconds")
        while True:
            time.sleep(REFRESH_HOURS * 3600)
            refresh_now()

    threading.Thread(target=loop, daemon=True, name="plus100-refresher").start()
