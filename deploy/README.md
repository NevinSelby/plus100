# Deploying Plus100

FastAPI backend plus the web app in `frontend/`, served by one process:

```
pip install -r deploy/requirements.txt
uvicorn backend.app:app --host 0.0.0.0 --port $PORT
```

The port answers `/healthz` immediately. On first boot with an empty `data/`
directory the app downloads the full match history (a couple of minutes) and
builds the model (about a minute); predictions return 503 until then. After
that it refreshes itself every `REFRESH_HOURS` (3) hours while it is receiving
traffic: each request checks the age of the last refresh, and the keepalive
ping counts as traffic.

Environment variables:

- `ODDS_API_KEY`: The Odds API key for Best Bets, the odds scanner and the live
  fixture listing. Without it those features report `no_key`.
- `PLUS100_ADMIN_PASS`: password for `/admin`. Unset means admin is disabled.
- `RENDER_EXTERNAL_URL`: the public URL; when set, the app pings its own
  `/healthz` every ten minutes so a free host does not put it to sleep.
- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`: durable usage analytics and the
  fantasy team state. Without them both fall back to files in `data/`, which a
  free host wipes on every restart.
- `PLUS100_MIN_YEAR`: skip match files for seasons that started before this
  year (smaller hosts). Default: read everything from 2000.

`deploy/supabase_setup.sql` and `deploy/supabase_add_ip.sql` create the tables
the analytics expect.
