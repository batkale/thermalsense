# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this project is

**ThermalSense** predicts thermal lift for glider pilots. An XGBoost model scores
a 201x201 grid (~50 m cells, ~11 km across) around a clicked point and returns a
climb-probability heatmap; a React/Leaflet frontend draws it over live glider
traffic. The model trains itself online from the Open Glider Network (OGN) APRS
feed — every circling, climbing glider is a positive label.

Live deployment: `https://thermalsense-tr.polandcentral.cloudapp.azure.com`
(Azure VM, 2 vCPU / 896 MB usable RAM — see [DEPLOY.md](DEPLOY.md)).

The UI is **Turkish-first** with a TR/EN toggle. The primary operating area is
İnönü / Eskişehir; the glider feed itself is worldwide.

## Commands

All backend commands run from `backend/`. A Windows venv exists at
`backend/.venv` with every dependency installed — prefer it.

```bash
# Tests — 271 tests, ~5 s
cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -q

# Single test file
cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_thermal_model.py -v

# Dev API on :8000
cd backend && ./.venv/Scripts/python.exe -m uvicorn main:app --reload --port 8000

# Offline model evaluation (frozen benchmark, bootstrap CIs)
cd backend && ./.venv/Scripts/python.exe -m evaluation.evaluate --variant base --variant solar
```

PowerShell equivalent: `backend\.venv\Scripts\Activate.ps1`, then plain `pytest tests/`.

If the venv is ever lost, `uv` (`C:\Users\sbatu\.local\bin\uv.exe`) reproduces it:
`uv run --with pytest --with pytest-asyncio --with xgboost --with scikit-learn --with numpy --with httpx --with python-dotenv pytest tests/ -v`

```bash
# Frontend dev on :5173 (proxies /api to :8000)
cd frontend && npm run dev
cd frontend && npm run build     # -> frontend/dist, served by FastAPI in prod

# Full stack in Docker (app + Caddy TLS)
docker compose up -d --build
```

There is no lint or typecheck step configured. `pytest.ini` sets
`asyncio_mode = auto`, so async tests need no decorator.

## Layout

| Path | Role |
|---|---|
| [backend/main.py](backend/main.py) | FastAPI app: all endpoints, WebSocket, APScheduler jobs, disk guard, static SPA mount |
| [backend/config.py](backend/config.py) | Every tunable. Read this before changing behaviour — the comments carry the reasoning |
| [backend/pipeline/feature_engineering.py](backend/pipeline/feature_engineering.py) | `build_feature_matrix` and `FEATURE_COUNT` (22 columns) |
| [backend/pipeline/solar.py](backend/pipeline/solar.py) | Sun position, per-cell cos(incidence). Candidate feature, evaluation-only so far |
| [backend/models/thermal_model.py](backend/models/thermal_model.py) | `ThermalModel`: load/save, MC-dropout predict, online retrain + quality gate, physics fallback |
| [backend/data/ogn_client.py](backend/data/ogn_client.py) | APRS TCP client, beacon parser, SQLite history, retention purge |
| [backend/data/meteo_client.py](backend/data/meteo_client.py) | Open-Meteo forecast + historical NWP (no API key). Archived hours are cached to disk forever, keyed on the same (0.5°, hour) bucket `/seed` groups on — 10k calls/day against ~1M buckets a year means an uncached rebuild is months, not an afternoon, and Open-Meteo revises its archive so an uncached rebuild would not even reproduce the same buffer |
| [backend/data/terrain_client.py](backend/data/terrain_client.py) | OpenTopoData SRTM elevation, grid snapping, disk cache |
| [backend/data/landcover_client.py](backend/data/landcover_client.py) | ESA WorldCover 10 m via COG range reads. Off by default (`ENABLE_LANDCOVER`) |
| [backend/data/circling_prior.py](backend/data/circling_prior.py) | Lagged per-cell climb rate from own beacon history. `update_index()` accumulates hourly and is wired into the scheduler; the *feature* is still evaluation-only |
| [backend/data/archive.py](backend/data/archive.py) | Cold beacon archive: completed days as date-partitioned Parquet, kept forever. `purge_old_beacons` will not delete a day this has not written |
| [backend/evaluation/](backend/evaluation/) | Frozen group-disjoint split, grouped AUC, bootstrap CIs, offline A/B harness. `--by-hour` scores within (day, cell, hour) — the unit one heatmap ranks over — while still cutting folds on the coarse key |
| [backend/scripts/seed_historical.py](backend/scripts/seed_historical.py) | **Legacy, refuses to run without `--legacy`.** Not what `POST /seed` calls — that is `ThermalModel.seed_from_history`. Its rows have `alt_agl = 0`, v1 labels and a smaller meteo set, so the app sets them aside |
| [frontend/src/hooks/useBackend.js](frontend/src/hooks/useBackend.js) | Prediction fetch, WebSocket lifecycle, viewport declaration, glider paths |
| [frontend/src/components/ThermalMap.jsx](frontend/src/components/ThermalMap.jsx) | Leaflet map, heatmap canvas, glider markers and tracks |
| [frontend/src/i18n/strings.js](frontend/src/i18n/strings.js) | TR/EN dictionaries — must stay in key parity |
| [DEPLOY.md](DEPLOY.md) | Hosting rationale, sizing, TLS, operating runbook |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Data flow, feature pipeline, training loop, resource budget |

## Hard invariants

Breaking any of these has taken the app down or silently corrupted results before.

1. **`--workers 1` is mandatory.** The APRS daemon thread and the `_live_gliders`
   and beacon buffers are per-process module state. A second worker opens a
   duplicate upstream connection and serves divergent data. This also rules out
   serverless entirely.

2. **`FEATURE_COUNT` and the saved model must agree.** Change the width of the
   feature matrix and you must retrain (`POST /train` or `POST /seed`), or the
   app runs physics-only. `ThermalModel.load()` refuses a stale model rather than
   crashing, and the buffer is set aside as `.vN`.

3. **`GRID_RADIUS` (backend config) and `PREDICT_RADIUS` (frontend config) are
   separate copies of one number.** They must stay equal — training features and
   served features have to describe the same patch of ground.

4. **`_to_wire` / `_positions_to_wire` apply only at the serialisation boundary.**
   Internal consumers (`_apply_ogn_fusion`, `_cluster_circling`, retrain) need the
   untrimmed dicts. Never trim inside `fetch_ogn_gliders`.

5. **Never hardcode a user-facing string in a component.** Add the key to *both*
   `tr` and `en` in `strings.js` and call `t('key', vars)`. Transient messages go
   into state as translation keys, not resolved text, so they follow a language
   switch.

6. **`--forwarded-allow-ips *` is only safe while port 8000 stays `expose`.**
   Publishing it in docker-compose would make `X-Forwarded-For` spoofable.

7. **Any `VITE_` variable is compiled into the public bundle.** `VITE_ADMIN_TOKEN`
   deters drive-by abuse; it is not a secret. Leave it unset in production.

8. **Driving the VM over ssh: always pass `-T` to `docker compose run/exec`.**
   Without it the command attaches stdin and eats the rest of a heredoc — this
   swallowed an `up -d app` line and caused a real outage on 15 Aug 2026.

## Model and training loop

- **Label** (`_label_for`, used by both the live loop and `/seed`): circling
  *and* climbing above `_POSITIVE_MIN_VARIO` (1.5 m/s) is a positive; flying
  straight *and* sinking below `_NEGATIVE_MAX_VARIO` (−0.5 m/s) is a negative;
  **everything between is ambiguous and discarded.**
- **Why the middle band is discarded:** "present and not thermalling" is a
  *pseudo*-absence. It labels the glider dolphining through lift without
  circling, and the one running a ridge, as evidence of *no* lift — teaching the
  model that lift is absent exactly where it is present. Straight-and-sinking is
  a *true* absence: still-air sink is −0.6 to −1.0 m/s, so a straight glider
  below −0.5 is in air that is neutral at best. Measured on 24 min of live feed:
  of 55,856 rows the old rule called negative, only 14,940 (27%) are true
  absences. This is the presence-only → presence-absence move from the SDM
  literature, and the feed already carries the missing half for free.
  `/seed` filters on the same thresholds **in SQL**, so the rate-limited terrain
  and meteo budget is not spent on rows that get discarded afterwards.
- **Rejected samples:** below `_MIN_SOLAR_GHI` (50 W/m², no surface heating),
  below `_MIN_SAMPLE_AGL` (200 m AGL — on the field, on the roll or on approach)
  and above the estimated thermal base + 300 m (that climb is wave, ridge or
  engine). The AGL floor is applied at collection *and* again in `fit_and_gate`,
  so a buffer written before it existed is cleaned without a `/seed` rebuild.
- **Why the AGL floor exists:** without it 18% of the buffer sat at exactly 0 m
  AGL, all negative, and the model learned "aircraft on the ground are not
  thermalling". Measured: `alt_agl` alone scored 0.631 within-group against 0.664
  for all 22 columns, and removing `alt_agl` collapsed the model to 0.530.
  Restricted to 500–2500 m AGL the model did not beat `terrain_multiplier` at all
  (−0.008 [−0.034, +0.039]). The 200 m floor costs 4 of 249 positives, removes
  38% of rows, and leaves the frozen benchmark at 31 mixed-label groups — one
  above `_MIN_BENCH_GROUPS`. **Tighter altitude matching starves the gate:
  ±250 m per-positive matching drops it to 28 groups and switches it off.**
  Revisit once the buffer holds more days. `alt_agl alone` is now a scored
  baseline in `evaluation/evaluate.py` so this class of leak shows up in the
  table instead of hiding in the headline.
- **Cadence:** APScheduler every `UPDATE_INTERVAL` (300 s), at most 20 candidates
  per cycle. The fit runs via `asyncio.to_thread`.
- **Buffer eviction is day-stratified, not chronological** (`_evict_to_cap`).
  The `_MAX_BUFFER = 200_000` cap stays — it is a RAM limit on an 896 MB box —
  but keeping the *newest* N meant a full buffer held one stretch of weather, so
  the model could only know the season it stood in. Every distinct day now gets
  an equal quota; inside a day positives are kept first (4% of the feed, two
  rate-limited API calls each), then the newest negatives. Measured: subsampling
  within one 5-day stretch moves the score less than its own noise (0.635 at 25%
  of groups vs 0.664 at 100%), so spending the cap on breadth costs nothing and
  buys regimes.
- **The model is refitted from scratch every cycle, deliberately.** There is no
  `xgb_model=` warm start and there should not be: appending trees to the
  serving booster would make the challenger and the incumbent share structure,
  which destroys the paired gate; stale trees would have no eviction path; and
  `best_iteration` stops meaning anything. The cure for "no memory" is a buffer
  that spans regimes, not a booster that never forgets.
- **Quality gate:** the challenger and the serving model are scored on identical
  rows from a *frozen, group-disjoint* benchmark (`evaluation/holdout.py`), and
  the challenger is rejected unless a paired bootstrap CI (`paired_delta_ci`)
  lies entirely above zero. After `_MAX_CONSECUTIVE_SKIPS = 12` rejections it
  accepts and re-baselines, so a lucky score cannot freeze the model forever.
  **Do not report the split or a missing gate as bugs — both were fixed.**
- **XGBoost thread trap:** `fit_and_gate` promotes the challenger object
  directly, and a booster keeps the `nthread` it was *fitted* under.
  `_for_serving()` retunes at the promotion and save points. `save_model` does
  not persist `n_jobs`, so the reload path was never affected; a test pins this.
- **Uncertainty:** `predict()` runs `_MC_SAMPLES = 50` passes with per-column
  noise on the meteo features only (terrain columns are never perturbed) and
  returns mean plus std. This is the CPU bottleneck: ~2M rows per request,
  0.48–0.53 s warm on the VM, ~2.4 s cold (terrain/meteo cache miss, not the
  model).
- **Physics fallback** runs when no model is loaded and yields realistic
  0.32–0.88 probabilities. A *blank* map therefore means an HTTP 500 or
  `no_insolation`, not the fallback. Note the fallback ranks cells no better
  than chance within a group (0.515 [0.478, 0.565]) — it produces plausible
  absolute numbers, not a usable heatmap.
- **Night guard:** `/predict` zeroes the grid and sets `no_insolation: true`
  when `solar_ghi < _MIN_SOLAR_GHI`. Training drops those rows, so the model has
  never seen the regime and was extrapolating — measured on the live site at
  0 W/m² it still returned a 54% peak. The flag is additive: an older bundle
  gets a dark map, which is right, just unexplained.

## API

| Endpoint | Notes |
|---|---|
| `GET /predict?lat=&lon=&alt=&forecast_h=` | Heatmap, std and weather. `alt` is observer AMSL; omitting it means terrain mean + 1000 m |
| `GET /ogn/live?wait_agl=` | Live snapshot. AGL from the warm cache unless `wait_agl=true` |
| `GET /ogn/search?q=&limit=` | Server-side id search — the client no longer holds a full glider list |
| `GET /ogn/track/{id}` | Last 8 h of positions (runs in a thread; the table holds millions of rows) |
| `GET /ogn/glider/{id}` | One aircraft by **exact** id, ignoring the viewport. `{"glider": null}` when it is gone — not a 404 |
| `GET /thermals/active` | Clusters of circling gliders |
| `GET /elevation?lat=&lon=` | Single SRTM point |
| `GET /healthz` | `{status, model_loaded}` |
| `POST /train` | Admin. Background retrain |
| `POST /seed?days_back=&limit=&reset=` | Admin. Rebuild the buffer from beacon history |
| `WS /ws/live` | Frames every `WS_FRAME_INTERVAL` (1 s). Client sends `{"bounds":{lat_min,lat_max,lon_min,lon_max}}` on connect and on every map move |

Admin endpoints require an `X-Admin-Token` header when `ADMIN_TOKEN` is set.

Viewport filtering on `/ws/live` shipped in commit `8dd1cc2` — measured 157
gliders down to 4 for a 1x1 degree box. Bounds are optional: absent or malformed
means "send everything", so an old bundle degrades to the previous behaviour
rather than to a blank map. Deltas were deliberately not implemented (a filtered
frame is ~500 bytes; resync bookkeeping would risk more than it saves).

**A marker's position is not always a reported position.** Measured on the live
feed (19 Aug 2026, 1548 aircraft / 10 min): the feed delivers a beacon every
1.0 s median, but *per aircraft* the median gap is 3.9 s and the slowest tenth
report only every 45 s. No server setting changes that — it is the aircraft's
transmit rate and its receiver coverage. So `services/deadReckon.js` projects
the gap client-side, advancing the icon along its heading at its ground speed.

Three rules keep the synthesis contained, and all three matter:
- **Circling aircraft are never projected.** A straight line through a
  thermalling turn walks the icon out of the very thermal it is climbing in.
- **It stops after `MAX_EXTRAPOLATE_MS` (15 s)**, after which an honestly stale
  icon beats a confident guess.
- **It reaches the drawn icon and the click hit-test, nothing else.** The pinned
  card's coordinates and the flight path must only ever show reported
  positions — check this before wiring `displayPosition` anywhere new.

`rx` is stamped client-side when a position is observed to *change*, not when a
frame arrives: an aircraft that has not beaconed is re-sent unchanged every
frame, so stamping on arrival would reset the clock and freeze every icon. It
stays null until the first observed change, because the wire carries no beacon
timestamp and a first sighting could be a minute old.

Icons live on their own canvas (`GliderCanvas`, ~10 fps) precisely so animating
them does not drag a 40k-cell heatmap repaint along at the same rate.

**A pinned glider is polled as well as streamed.** Viewport scoping means the
socket stops carrying a followed aircraft once it leaves the padded bounds, and
the card would then hold its last frame while looking perfectly live. So
`useBackend(pinnedId)` fetches `/ogn/glider/{id}` every `PIN_POLL_MS` (5 s) for
as long as something is pinned. The streamed copy always wins when it exists —
it arrives every `WS_FRAME_INTERVAL`, so preferring the poll would make the
followed glider the least current thing on the map. Note the pinned *track*
still only extends from `new_positions`, which stays viewport-filtered, so a
path gains a gap while its aircraft is off-screen.

## Resource budget

The deployment target is 2 vCPU / 896 MB, and most odd-looking choices come from it.

- `PREDICT_CONCURRENCY=1` — predictions are queued, not parallelised. Each is
  core-bound and holds ~30 MB; `asyncio.to_thread` would otherwise hand out
  `min(32, cpu_count+4)` threads, each spawning its own OpenMP pool.
- `XGB_FIT_THREADS=1` (the background job yields cores), `XGB_PREDICT_THREADS`
  takes all of them.
- `mem_limit: 700m` on the app service — a blast radius, not a target (steady
  state is ~135–190 MB).
- **Retention is now eviction, not deletion.** `_archive_job` writes every
  completed day to `ARCHIVE_DIR` as Parquet before `_purge_job` deletes it, and
  `purge_old_beacons` refuses to delete past the unbroken archived prefix — an
  export failure stops the delete rather than being outrun by it. Measured on
  this feed: 10.0 B/row archived against 244 B/row in SQLite (18–24×), so a year
  of the worldwide feed is ~28 GB rather than ~0.7 TB. `_prior_index_job` runs
  in the same slot and folds completed hours into `prior_cells`, which is ~35×
  smaller again (~2.6 GB/year) and so outlives retention entirely — the
  climatology is no longer bounded by `BEACON_RETENTION_DAYS`.
- **The beacon DB grows ~7.7M rows / ~1.4 GB per day** on the worldwide feed.
  `BEACON_RETENTION_DAYS` defaults to **2**, lowered from 7 after the 17 Aug 2026
  outage: the binding constraint is RAM, not disk. A 4.26 GB DB against ~165 MB
  of page cache put the host at 96.6% iowait with uvicorn wedged in D state.
- `_disk_guard()` shortens retention below `MIN_FREE_DISK_GB=3`, capped at 2
  rounds — a DB created before `auto_vacuum=INCREMENTAL` never returns freed
  pages to the OS, so waiting for free space to recover would spin forever.

**Upstream ceilings:** OpenTopoData 1000 calls/day and 1 req/s (one call per
prediction, cached); Open-Meteo 10k/day; ESA WorldCover needs no key and has no
published limit.

## Config that surprises people

- The bounding box in `backend/config.py` is **worldwide**. That derives
  `OGN_APRS_PORT = 10152` (the full feed — port 14580 is the filtered one) and an
  empty `OGN_APRS_FILTER`. Narrow the box and the filter becomes an *area* filter
  `a/latN/lonW/latS/lonE` — deliberately not a radius, since no circle inside a
  box covers its corners.
- `frontend/src/config.js` `LAT/LON_MIN/MAX` is the **prediction grid** area
  (İnönü/Eskişehir), not the glider feed. Widening it explodes the grid; live
  glider bounds are the separate `GLIDER_*` constants.
- `ENABLE_LANDCOVER` is off by default. The plumbing is complete and tested, but
  measured through `evaluation/` the change moves the within-group score by −0.04
  to +0.02 depending only on the CV fold seed. Flipping it on requires a retrain
  — the columns change meaning, not width.

## Housekeeping

- `.claude/` is gitignored; `CLAUDE.md` at the repo root is not, so durable
  project knowledge belongs here.
- `myVm_key.pem` sits in the repo root and is covered by the `*.pem` gitignore
  rule.
- The 22-column feature list goes stale often. Read `FEATURE_COUNT` and the
  `np.column_stack` block in `feature_engineering.py` rather than trusting any
  written list, including the one in `docs/ARCHITECTURE.md`.
