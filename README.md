# ThermalSense

Thermal lift prediction for glider pilots. Click anywhere on the map and get a
probability heatmap of where a sailplane will find a climb — computed from
terrain, numerical weather, and the live Open Glider Network feed.

**Live:** https://thermalsense-tr.polandcentral.cloudapp.azure.com

The model is not trained once and frozen. Every glider on the OGN network that
starts circling and climbing is a labelled example, so the model retrains itself
from real soaring behaviour every five minutes.

---

## What it does

- **Heatmap prediction** — a 201x201 grid (~50 m cells, roughly 11 x 7 km) around
  the clicked point, scored by an XGBoost classifier and returned with a
  per-cell uncertainty band from 50 Monte Carlo passes over perturbed weather.
- **Altitude-aware** — clicking a glider sends that glider's own altitude, so the
  answer is "is there lift *at this height*", not just "is this a good field".
  A thermal that tops out at 900 m is no use to someone at 1500 m.
- **Live traffic** — gliders, paragliders and hang gliders stream in over a
  WebSocket at 2 s intervals, with tracks, climb rates, tow detection and height
  above ground. Confirmed thermals (clusters of circling gliders) are drawn on
  top of the prediction.
- **Forecast** — the same prediction, shifted up to several hours ahead using
  Open-Meteo forecast fields.
- **Turkish-first UI** with a TR/EN toggle.

## Architecture at a glance

```
  OGN APRS TCP  ─┐
 (aprs.glidernet)│   ┌──────────────────────────────────────┐
                 ├──▶│  FastAPI (single process, workers=1) │
  Open-Meteo  ───┤   │                                      │
  (NWP forecast) │   │  • /predict   grid inference         │──▶ React + Leaflet
                 │   │  • /ws/live   viewport-scoped stream │      (Vite build,
  OpenTopoData ──┤   │  • APScheduler: retrain every 300 s  │       served by
  (SRTM 30 m)    │   │  • SQLite beacon history             │       FastAPI)
                 │   └──────────────────────────────────────┘
  ESA WorldCover ┘              │
  (10 m COG, off)               ▼
                        XGBoost model + training buffer
                        (persisted to a mounted volume)
```

Everything runs in **one process** — the APRS client is a daemon thread sharing
module state with the event loop, so the app cannot be scaled horizontally
without redesign. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full
picture and [DEPLOY.md](DEPLOY.md) for why serverless is impossible here.

## Quick start

### Backend

```bash
cd backend
python -m venv .venv                          # first time only
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # Linux/macOS

.venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000
```

The API comes up on http://localhost:8000. `GET /healthz` reports liveness and
whether a trained model loaded. On first run there is no model, so predictions
come from the physics fallback until the buffer fills or you seed it.

### Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to :8000
```

### Tests

```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/ -q
```

227 tests, about 7 seconds. `pytest.ini` sets `asyncio_mode = auto`.

### Docker (production shape)

```bash
cp .env.example .env      # set SITE_ADDRESS and ADMIN_TOKEN
docker compose up -d --build
```

This builds the Vite bundle, bakes it into a Python image, and puts Caddy in
front for automatic TLS. The app port is deliberately not published on the host
— Caddy is the only route in.

## Configuration

Every tunable lives in [backend/config.py](backend/config.py), overridable by
environment variable. The ones that matter most:

| Variable | Default | Purpose |
|---|---|---|
| `THERMALSENSE_DATA_DIR` | backend dir | Root for the model, training buffer and beacon DB. **Point at a mounted volume in production.** |
| `ADMIN_TOKEN` | *(unset)* | When set, `POST /train` and `POST /seed` require an `X-Admin-Token` header |
| `BEACON_RETENTION_DAYS` | `2` | Beacon history window. The feed writes ~1.4 GB/day |
| `MIN_FREE_DISK_GB` | `3` | Below this the purge job shortens retention rather than fill the disk |
| `PREDICT_CONCURRENCY` | `1` | Simultaneous `/predict` runs. Each saturates its cores and holds ~30 MB |
| `XGB_FIT_THREADS` | `1` | Threads for the retrain fit, kept low so a background job cannot starve serving |
| `XGB_PREDICT_THREADS` | all cores | Threads for inference |
| `ENABLE_LANDCOVER` | `0` | Feed real ESA WorldCover values into feature columns 13/14 (requires a retrain) |
| `OGN_LAT_MIN` etc. | worldwide | Glider feed bounding box. Narrowing it switches the APRS port and adds an area filter |
| `CORS_ORIGINS` | `*` | Fine for the single-container deploy where API and UI share an origin |
| `STATIC_DIR` | `../frontend/dist` | Built frontend, served at `/` when present |

Frontend build-time variables are in `frontend/.env.example`. The
single-container deploy needs none of them — the bundle calls its own origin and
upgrades `https` to `wss` automatically.

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/predict?lat=&lon=&alt=&forecast_h=` | Heatmap + uncertainty + weather summary for a grid around the point |
| `GET` | `/ogn/live?wait_agl=` | Snapshot of the live feed |
| `GET` | `/ogn/search?q=&limit=` | Substring search on glider id across the whole feed |
| `GET` | `/ogn/track/{glider_id}` | Last 8 hours of positions for one aircraft |
| `GET` | `/thermals/active` | Clusters of circling gliders — confirmed thermals |
| `GET` | `/elevation?lat=&lon=` | SRTM elevation at a point |
| `GET` | `/healthz` | `{"status":"ok","model_loaded":true}` |
| `POST` | `/train` | Trigger a retrain in the background (admin) |
| `POST` | `/seed?days_back=&limit=&reset=` | Rebuild the training buffer from beacon history (admin) |
| `WS` | `/ws/live` | 2 s glider frames, scoped to the viewport the client declares |

`/predict` returns the heatmap as a flat `rows * cols` array plus `grid_lat` /
`grid_lon` — the terrain-lattice point the grid was actually snapped to, up to
~550 m from the requested coordinates. Use those, not the request point, when
mapping an index back to a location.

## Project layout

```
backend/
  main.py                    FastAPI app, endpoints, scheduler, WebSocket
  config.py                  all tunables, heavily commented with rationale
  data/                      external feeds: OGN APRS, Open-Meteo, SRTM, WorldCover
  pipeline/                  feature matrix construction, solar geometry
  models/                    ThermalModel: predict, retrain, gate, persistence
  evaluation/                frozen benchmark split, grouped AUC, offline A/B harness
  scripts/seed_historical.py backfill training data from the beacon DB
  tests/                     227 pytest tests
frontend/
  src/components/            Leaflet map, info panel, weather bar, search, export
  src/hooks/useBackend.js    prediction + WebSocket state
  src/i18n/                  TR/EN dictionaries and language context
docs/ARCHITECTURE.md         data flow, feature pipeline, training loop
DEPLOY.md                    hosting, sizing, TLS, operating runbook
CLAUDE.md                    working notes and invariants for AI-assisted development
notebooks/train_model.ipynb  exploratory training notebook
```

## Data sources

| Source | Used for | Limit |
|---|---|---|
| [Open Glider Network](http://wiki.glidernet.org/) APRS | Live positions, training labels | None, but a persistent TCP socket |
| [Open-Meteo](https://open-meteo.com/) | Temperature, humidity, wind, CAPE/CIN, GHI, lapse rate, PBL height, soil temperature | 10k calls/day, no key |
| [OpenTopoData](https://www.opentopodata.org/) SRTM 30 m | Elevation, slope, aspect | 1000 calls/day, 1 req/s |
| [ESA WorldCover](https://esa-worldcover.org/) 10 m | Per-cell land cover (optional) | None, public S3 |

## Status and known limitations

- **Single process only.** Horizontal scaling would need the APRS client and
  glider state extracted into a separate service.
- **Land cover is plumbed but disabled.** Measured through the evaluation
  harness the change is within fold-seed noise, so it waits for a larger buffer.
- **Solar incidence and the circling prior** are implemented and testable
  through `evaluation/evaluate.py`, but are not yet in the served feature matrix.
- **Retention is RAM-bound, not disk-bound.** Two days of worldwide beacons is
  what fits in page cache on the current VM.

## License

No license file is present; treat as all-rights-reserved unless one is added.
