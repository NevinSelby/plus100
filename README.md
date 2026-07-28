# Plus100 — football prediction engine

FastAPI backend + web app. Deployed on Render (free web service).

- Website: served at `/`
- API: `/api/meta`, `/api/predict`, `/api/bestbets`, `/api/fpl/gw`, …

On first boot the service downloads its historical match dataset (~2 min), then
refreshes automatically every 6 hours while awake.

Set `ODDS_API_KEY` in the Render dashboard (Environment tab) to enable the
live-odds features.
