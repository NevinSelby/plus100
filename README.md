# Plus100 — Football Match Predictor

A statistical football match predictor: pick any two teams (clubs or national sides,
across 33 competitions) and get calibrated probabilities for every major market,
plus full head-to-head history.

## Run it

```sh
.venv/bin/uvicorn backend.app:app --port 8710
# then open http://127.0.0.1:8710
```

First startup after a data refresh takes ~10s (model rebuild); afterwards it loads
from `data/store_cache.pkl` in ~2s.

## Refresh data (do this weekly)

```sh
.venv/bin/python scripts/download_data.py   # fetches only new/changed files
rm data/store_cache.pkl                     # force model rebuild
```

## What's inside

| Piece | What it does |
|---|---|
| `scripts/download_data.py` | Downloads all sources (football-data.co.uk, international results, ClubElo) |
| `backend/data_store.py` | Unifies 250k+ matches, computes Elo + time-weighted attack/defence strengths |
| `backend/model.py` | Blends Elo and strength estimates into a Dixon-Coles Poisson score matrix; derives all markets |
| `backend/app.py` | FastAPI: team search, predictions, H2H, logos (TheSportsDB), Reddit buzz |
| `frontend/` | The website |
| `scripts/backtest.py` | Walk-forward accuracy test vs bookmaker closing odds |

## Data sources (xG layer)

- **Understat shot data** (via the worldfootballR data releases on GitHub) — 570k
  shots with xG values, player, and situation for the top-5 European leagues + RFPL,
  2014 → Sep 2025. Powers the xG team strengths and club scorer probabilities.
  Refresh: automatic. `backend/xg.py` pulls each league-season from understat's
  JSON endpoint on every data refresh and rewrites `data/xg_precomputed.json.gz`
  (team strengths + scorer shares); no shot files or R step are needed anymore.
- **TheSportsDB player search** — used at prediction time to verify each predicted
  scorer still plays for the club (drops verifiably departed players; cached in
  `data/player_team_cache.json`).
- **Google News RSS** — injury & team-news headlines per team (display only,
  not part of the model).

## Data sources

- **football-data.co.uk** — 16 European divisions, 26 seasons each, plus MLS, Brazil,
  Argentina, Mexico, Japan, China and 10 more leagues (includes bookmaker odds).
- **github.com/martj42/international_results** — every international since 1872,
  with goalscorers (powers the "likely scorers" panel for national teams).
- **api.clubelo.com** — puts all European clubs on one Elo scale so cross-league
  matchups (e.g. Real Madrid vs Bayern) are meaningful.
- **TheSportsDB** — team badges/logos.
- Non-European league strength anchors (MLS ≈ 1530, Brazil ≈ 1720, …) are documented
  estimates in `data_store.py`, not fitted values.

## Model

1. **Elo ratings**: chronological pass over all matches; K scaled by margin of victory
   and (for internationals) tournament importance; home advantage ≈ 60 points.
2. **Elo → goals**: log-linear mapping fitted on the data itself
   (`log E[goals] = a ± b·elodiff/400`).
3. **Attack/defence strengths**: exponentially time-weighted (420-day half-life)
   goals for/against relative to league average, shrunk toward the mean for thin
   data. Where shot data exists, strengths are a 55/45 mix of xG-based and
   goals-based values — xG is the better signal of underlying quality.
4. **Blend**: Elo carries 75% of the weight within a league (validated on 10k
   matches — Elo is the stronger 1X2 signal), 85% cross-league, 90% club-vs-nation
   (flagged as indicative). The strengths part differentiates the totals /
   BTTS / correct-score markets.
5. **Score matrix**: Poisson grid with the Dixon-Coles low-score correction
   (ρ = −0.10, confirmed optimal on a validation grid). Every market (1X2,
   correct score, over/unders, BTTS, handicaps, clean sheets, draw-no-bet) is
   read off this matrix.
6. **Scorers**: expected team goals allocated by each player's recency-weighted
   share of team xG (clubs) or of recent international goals (national teams),
   then filtered against TheSportsDB current-club data to drop departed players.

## Honest accuracy

Live-conditions test (Oct 2025 – Jan 2026, 1,827 matches, all information frozen
before the window — `scripts/eval_blend.py`):

| | accuracy | Brier |
|---|---|---|
| full model (Elo + xG blend) | 50.4% | 0.6025 |
| Elo backbone only | 50.1% | 0.6028 |
| bookmaker closing odds | 51.0% | 0.5932 |

Calibration verified on a separate 14,432-match walk-forward test (predicted vs
realized frequencies match within ~1% in every decile). The model is ~0.6pt
behind the bookmakers and does **not** beat them. Football is a high-variance
sport: even the best models in the world sit around 50–53% on 1X2. Use the
fair-odds output to spot prices that look generous, never as a guarantee.

**Bet responsibly. Never stake money you cannot afford to lose.**
