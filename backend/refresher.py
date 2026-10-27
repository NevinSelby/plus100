"""Automatic data refresh: keeps ratings current without manual steps.

Once at startup (if the stored results are more than a day old) and then
whenever a request arrives more than REFRESH_HOURS after the last refresh
(see maybe_refresh; the keepalive ping counts as a request), the volatile
sources are re-downloaded: current + previous season results for all leagues,
the international dataset, extra leagues, ClubElo. The model store is rebuilt
in a background thread and hot-swapped into the running app.
"""
from __future__ import annotations

import datetime as dt
import threading
import time

from .data_store import CACHE, DATA, fetch_file, get_store, season_code, season_start, source_jobs

REFRESH_HOURS = 3

state = {
    "last_refresh": None,      # iso str
    "refreshing": False,
    "last_error": None,
}

RECENT_FILE = DATA / "espn_recent.json"


def refresh_recent_results(store) -> bool:
    """Finished matches in every covered league from the day after the newest
    stored result up to today, straight from ESPN, so the model is current to
    the day even when the weekly history files lag or their site is down.
    Days already fetched are remembered (data/espn_recent.json keeps a ledger),
    so a refresh only costs the last two days plus anything new. Rows carry OUR
    team names so the store can merge them like any other result."""
    import json
    from . import espn
    from .bestbets import resolve_team
    today = dt.date.today()
    try:
        latest = app_store_latest(store)
    except Exception:  # noqa: BLE001
        latest = today - dt.timedelta(days=10)
    start = max(latest - dt.timedelta(days=1), today - dt.timedelta(days=75))
    ledger = {"done": {}, "rows": []}
    if RECENT_FILE.exists():
        try:
            ledger = json.loads(RECENT_FILE.read_text())
            if isinstance(ledger, list):            # pre-ledger format
                ledger = {"done": {}, "rows": ledger}
        except (OSError, ValueError):
            pass
    # the ledger only needs to remember the window it still fetches from
    cutoff = start.isoformat()
    done = {k: {d for d in v if d >= cutoff} for k, v in ledger.get("done", {}).items()}
    rows = [x for x in ledger.get("rows", []) if x.get("date", "") >= cutoff]
    codes = [c for c in espn.SOCCER_SLUGS if any(r["league"] == c for r in store.registry.values())]
    changed = False
    day = start
    while day <= today:
        key = day.isoformat()
        fresh = (today - day).days <= 1          # today/yesterday can still gain results
        for code in codes:
            if not fresh and key in done.get(code, set()):
                continue
            got = espn.soccer_scores(code, day)
            if got is None:
                continue
            new_rows = []
            for g in got:
                hid = resolve_team(store, g["home"], "club", code)
                aid = resolve_team(store, g["away"], "club", code)
                if not hid or not aid or hid == aid:
                    continue
                new_rows.append({"date": g["date"], "league": code,
                                 "home": store.registry[hid]["name"], "away": store.registry[aid]["name"],
                                 "hg": g["hs"], "ag": g["as"]})
            before = {(x["date"], x["league"], x["home"], x["away"]) for x in rows}
            for x in new_rows:
                if (x["date"], x["league"], x["home"], x["away"]) not in before:
                    rows.append(x); changed = True
            if not fresh:
                done.setdefault(code, set()).add(key)
        day += dt.timedelta(days=1)
    payload = {"done": {k: sorted(v) for k, v in done.items() if v},
               "rows": sorted(rows, key=lambda x: (x["date"], x["league"], x["home"]))}
    text = json.dumps(payload)
    if changed or not RECENT_FILE.exists():
        RECENT_FILE.write_text(text)
    return changed


def app_store_latest(store) -> dt.date:
    """Newest result date in the built store (history files + merged recents)."""
    return store.matches.date.max().date()


def refresh_files() -> bool:
    """Re-download everything that changes between matchdays (the current and
    previous season of every league, plus the shared sources). Returns True if
    any file actually changed."""
    y = season_start(dt.date.today())
    changed = False
    for url, dest, timeout in source_jobs([season_code(y - 1), season_code(y)]):
        changed |= fetch_file(url, dest, timeout)
    return changed


def _start_prewarm() -> None:
    """Fill the per-team caches for the coming week's fixtures off-thread."""
    try:
        from .app import _prewarm
        threading.Thread(target=_prewarm, daemon=True, name="plus100-prewarm").start()
    except Exception:  # noqa: BLE001
        pass


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
    except Exception as e:  # noqa: BLE001 (a failed refresh must never kill the app)
        state["last_error"] = str(e)
    finally:
        state["refreshing"] = False
        _start_prewarm()


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
    """Startup check: refresh now if the stored results are stale, otherwise
    just stamp the cadence clock and warm the caches. Later refreshes are
    driven by maybe_refresh() from the request path."""
    def once():
        if _data_age_days() > 1:
            refresh_now()                 # its finally block starts the prewarm
        else:
            state["last_refresh"] = dt.datetime.now().isoformat(timespec="seconds")
            state["last_ts"] = time.time()
            _start_prewarm()

    def nba_first():
        # the NBA append is a handful of scoreboard calls; it must not wait behind
        # the football downloads, or a fresh instance serves last season's ratings
        try:
            from .nba import refresh_games
            refresh_games()
        except Exception as e:  # noqa: BLE001
            state["last_error"] = f"nba boot refresh: {e}"

    threading.Thread(target=nba_first, daemon=True, name="plus100-nba-boot").start()
    threading.Thread(target=once, daemon=True, name="plus100-refresher").start()
