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
# Tests — 374 tests, ~15 s (needs pyarrow, or the archive tests skip)
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

`tests/conftest.py` points the model, buffer, beacon DB, archive and terrain
cache paths at each test's `tmp_path` (autouse). Keep it that way: before it, a
run replaced the real `training_buffer.npz` with 3 stub rows — the buffer
committed from 17 Sep to 30 Sep 2026 was that file — and wrote into a local
`ogn_history.db`. The terrain cache was the same story until 16 Nov 2026: the
local `cache/terrain-copernicus/points.json` held 200 entries, all
`(50.x, 1.x) -> 100`, beside 16,443 real grids.

## Layout

| Path | Role |
|---|---|
| [backend/main.py](backend/main.py) | FastAPI app: all endpoints, WebSocket, APScheduler jobs, disk guard, static SPA mount |
| [backend/config.py](backend/config.py) | Every tunable. Read this before changing behaviour — the comments carry the reasoning |
| [backend/pipeline/feature_engineering.py](backend/pipeline/feature_engineering.py) | `build_feature_matrix` and `FEATURE_COUNT` (22 columns) |
| [backend/pipeline/solar.py](backend/pipeline/solar.py) | Sun position, per-cell cos(incidence). Candidate feature, evaluation-only so far |
| [backend/models/thermal_model.py](backend/models/thermal_model.py) | `ThermalModel`: load/save, MC-dropout predict, online retrain + quality gate, physics fallback |
| [backend/data/ogn_client.py](backend/data/ogn_client.py) | APRS TCP client, beacon parser, plausibility gate (`_admit`), SQLite history, retention purge |
| [backend/data/meteo_client.py](backend/data/meteo_client.py) | Open-Meteo forecast + historical NWP (no API key). Archived hours are cached to disk forever, keyed on the same (0.5°, hour) bucket `/seed` groups on — 10k calls/day against ~1M buckets a year means an uncached rebuild is months, not an afternoon, and Open-Meteo revises its archive so an uncached rebuild would not even reproduce the same buffer |
| [backend/data/terrain_client.py](backend/data/terrain_client.py) | Elevation lookups, grid snapping, disk cache. `TERRAIN_SOURCE=copernicus` (default) reads Copernicus first and sends only the points it cannot answer to OpenTopoData; each source has its own cache directory so the two are never stitched into one grid. Both in-memory caches are bounded LRUs, and points persist as an append-only journal (`points.bin`) — see "Resource budget" |
| [backend/data/dem_client.py](backend/data/dem_client.py) | Copernicus GLO-30 COG reader: range requests on public S3, no key, no quota. Verified against GDAL on 65 points (Türkiye, Alps, >50°N, both hemispheres, integer-degree edges): max difference 0.0000 m. A missing tile is `None`, never 0 m |
| [backend/data/cog.py](backend/data/cog.py) | Minimal COG parsing shared by land cover and elevation: little-endian tiled DEFLATE, integer and float samples, horizontal and floating-point predictors |
| [backend/data/landcover_client.py](backend/data/landcover_client.py) | ESA WorldCover 10 m via COG range reads. Off by default (`ENABLE_LANDCOVER`) |
| [backend/data/circling_prior.py](backend/data/circling_prior.py) | Lagged per-cell climb rate from own beacon history. `update_index()` accumulates hourly and is wired into the scheduler; the *feature* is still evaluation-only |
| [backend/data/archive.py](backend/data/archive.py) | Cold beacon archive: completed days as date-partitioned Parquet, kept forever. `purge_old_beacons` will not delete a day this has not written |
| [backend/evaluation/](backend/evaluation/) | Frozen group-disjoint split, grouped AUC, bootstrap CIs, offline A/B harness. `--by-hour` scores within (day, cell, hour) — the unit one heatmap ranks over — while still cutting folds on the coarse key |
| [backend/scripts/seed_historical.py](backend/scripts/seed_historical.py) | **Legacy, refuses to run without `--legacy`.** Not what `POST /seed` calls — that is `ThermalModel.seed_from_history`. Its rows have `alt_agl = 0`, v1 labels and a smaller meteo set, so the app sets them aside |
| [frontend/src/hooks/useBackend.js](frontend/src/hooks/useBackend.js) | Prediction fetch, WebSocket lifecycle, viewport declaration, glider paths |
| [frontend/src/components/ThermalMap.jsx](frontend/src/components/ThermalMap.jsx) | Leaflet map, heatmap canvas, glider markers and tracks |
| [frontend/src/components/CompareLayer.jsx](frontend/src/components/CompareLayer.jsx) | Raw data / product comparison: the before/after divider, the raw-inputs canvas and the two caption cards |
| [frontend/src/i18n/strings.js](frontend/src/i18n/strings.js) | TR/EN dictionaries — must stay in key parity |
| [frontend/about.html](frontend/about.html), [frontend/src/landing/](frontend/src/landing/) | Landing page at `/about`: a second page of the same Vite build, not a route in the app. Own stylesheet and own TR/EN dictionary (`landing/strings.js`); language and theme come from the app's two providers, so both are one choice across the two pages |
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

9. **Every beacon passes `_admit` before anything else sees it.** The type
   allow-list trusts what a beacon says it is; the gate checks what it does.
   Bypass it and shared ids come back as one icon crossing three countries
   every few seconds — see "The feed carries ids that are not one aircraft".

10. **Anything that trains passes `strict=True` to all three fetchers**
    (`fetch_meteo_features`, `fetch_meteo_historical`, `fetch_elevation_grid`)
    and skips the sample on `None`. The non-strict return is a stand-in made
    for a display request; with a real label attached it is a fabrication.

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
- **Cadence:** APScheduler every `UPDATE_INTERVAL` (300 s). A cycle samples up to
  `MAX_SAMPLE_GRIDS` (60) candidate grids and stops early on whichever of three
  bounds arrives first: that count, `SAMPLE_TIME_BUDGET_S` (150 s, against the
  cycle's own `UPDATE_INTERVAL - 30` deadline), or Open-Meteo's 10,000 forecast
  calls for the day. The fit runs via `asyncio.to_thread`.
- **Why the old fixed 20 is gone:** it existed to spare OpenTopoData's
  1,000/day, which Copernicus removed, and it held collection to ~200 samples a
  day — weeks from the 30 mixed-label groups the gate needs. The 1.1 s pacing
  sleep goes with it on the Copernicus path; at 60 candidates it alone would eat
  two thirds of a cycle.
- **Live meteo is cached per 0.1° cell and hour** with a daily counter
  (`meteo_client.live_usage`). Every candidate grid used to be its own request,
  night included, so Open-Meteo was the real ceiling and nothing reported it.
  0.1° is not a compromise: `build_feature_matrix` already spreads one point
  sample across the whole 11 km grid. The synthetic fallback is never cached —
  pinning invented weather for an hour would feed it straight into the buffer.
- **The solar floor is checked before the feature matrix is built.** At night
  every candidate is dropped there, and building first spent ~7 MB and a terrain
  read to discard the result.
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
- **The nightly seed is bounded by the clock, not only by quota.** It stopped
  only at `SEED_METEO_BUDGET`, and on 18 Sep 2026 a run of 1,430 buckets never
  came near 6,000 calls, so nothing stopped it: it built one 201x201x22 feature
  matrix (~7 MB) per beacon to keep a single row, 20,000 of them, against the
  live sampler's 60 grids per cycle. The 3-minute full scan of the 2.1 GB DB
  that preceded it took the page cache, the host's daemons went to swap on the
  OS disk and did not come back, and the box was unreachable for 36 h — sshd
  accepted TCP but never sent a banner, the Azure guest agent went Not Ready,
  and only an Azure-side restart recovered it. Azure's hypervisor metrics show
  it exactly: OS disk queue depth 0.01 at 01:00, 35.7 at 02:00, climbing to 91
  by the time it was restarted, while data disk queue depth sat at 0 — no
  progress, pure swap churn. The container never died, because `mem_limit` kept
  its reservation while everything around it starved. Hence `SEED_TIME_BUDGET_S`
  (1800), `NIGHTLY_SEED_LIMIT` at 2,000 rather than 20,000, one elevation grid
  per snapped centre per bucket instead of per beacon, and `mem_limit` 700m →
  600m so an overrun becomes an OOM kill that restarts rather than a host-wide
  thrash. **A budget on what a job costs an upstream API is not a budget on what
  it costs the machine.**
- **Nightly seed** (`_nightly_seed_job`, 02:00 UTC): rebuilds training rows from
  the whole of yesterday via `seed_from_history(days_back=1)`, reading the hot
  DB while retention still covers the day and the Parquet archive once it does
  not. This is what makes the archive part of training rather than a backup
  nobody opens — the live sampler only ever sees the aircraft airborne during
  its 150 s slice of each five minutes. It stops at `SEED_METEO_BUDGET` (6,000)
  rather than the 10,000 quota, because the morning's live sampling draws on the
  same allowance; `meteo_client.live_usage()["total"]` counts both endpoints.
  Overlap with rows the live sampler already took from the same day is possible
  and expected to be small; the panel shows the run's `added` count.
- **What the score actually is, measured 27 Sep 2026** on the first buffer big
  enough to measure anything (11,774 rows, 1,714 positives, 2,571 scoring units,
  77 benchmark groups — the gate is open and fits are being accepted). Scored
  within (day, cell, hour), which is what one heatmap ranks over:

  | ranker | micro | 95% CI |
  |---|---|---|
  | `terrain_multiplier` | 0.519 | [0.484, 0.556] — at chance |
  | `alt_agl` alone | 0.473 | [0.440, 0.512] — **below** chance |
  | `cos_incidence` alone | 0.532 | [0.503, 0.562] |
  | served model (22 cols) | 0.542 | [0.519, 0.567] |

  `alt_agl` alone being below chance is the ground-row leak confirmed gone; it
  used to score 0.631 and carry almost the whole headline.
- **Re-measured 16 Nov 2026** on the 6 Nov buffer, same command
  (`--variant base --by-hour`), after dropping its 862 invented-weather rows
  the way `load()` does: 47,419 rows, 6,259 positives, 2,009 mixed-label units.
  Served model **0.570 [0.539, 0.601]**; `cos_incidence` alone 0.551
  [0.520, 0.580]; `terrain_multiplier` 0.525 [0.493, 0.557]; physics fallback
  0.502; `alt_agl` alone 0.501. By region (served vs terrain): Alps 0.612,
  +0.041 [+0.013, +0.072]; Iberia 0.611, +0.070 [+0.045, +0.096]; Germany
  0.575, +0.043 [−0.003, +0.091]; other 0.523, +0.037 [+0.019, +0.052];
  Türkiye 55 rows, 1 mixed group — not measurable. The rise from 0.542 sits
  inside both intervals, so it is not yet evidence that more data helps.
- **Nothing has yet been proven to raise it, and four things have been ruled
  out.** Paired deltas against the shipped setup, same groups, 1,500 bootstrap
  resamples: `solar` as a 23rd column −0.019 [−0.051, +0.010]; a pairwise
  **ranking objective** grouped by the scoring unit −0.013 [−0.046, +0.015];
  training on only the six columns that vary inside a group +0.010
  [−0.009, +0.027]; `prior` **+0.014 [−0.002, +0.029]**. All inconclusive.
  `evaluation/objective.py` holds the ranker experiment — the hypothesis was
  that 16 constant-within-group columns let a global classifier memorise which
  hours are thermic while we score where inside an hour the lift sits. It is
  wrong, and the file exists so nobody re-derives it.
- **`prior` is the leading candidate and it is clean.** Nearest to significance,
  covers 99.4% of buffer rows, and `LAG_HOURS = 24` is enforced in the SQL with
  a test asserting a cell's own beacons cannot move its own prior — so the gain
  is not the label leaking back. Its index lives only on the training node;
  measuring it locally means extracting `prior_cells` (103 MB, vs 6.4 GB for the
  DB around it) and pointing `config.DB_PATH` at the extract with
  `evaluate._prior_ready = True`.
- **~0.55 is where terrain and altitude run out.** That is the honest reading of
  the table: the six columns that vary within a group are position, the terrain
  derived from it, and the aircraft's height, and none of them measures whether
  *this* ground is hot right now. The untried lever is a per-cell column that
  does — satellite land surface temperature, or insolation integrated since
  sunrise rather than instantaneous `cos_incidence`.
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
- **The live sampler trained on invented weather until 15 Nov 2026.**
  `fetch_meteo_features` returns `_FALLBACK` (18 °C, 500 W/m², CAPE 600) on any
  failure, and `retrain()` took it for a forecast. Measured on the buffer of
  6 Nov 2026: 862 of 48,281 rows (1.8%), 70 of them positives, over 16 days —
  and the invented 500 W/m² carried night rows past `_MIN_SOLAR_GHI`. The
  sampler is strict now, and `load()` drops rows matching
  `meteo_client.is_fallback_row` (seven columns at once; no genuine row matched
  more than two). **`/predict` still shows the fallback as if it were a
  forecast** — deliberate for an offline demo, but nothing on screen says so.

## API

| Endpoint | Notes |
|---|---|
| `GET /predict?lat=&lon=&alt=&forecast_h=` | Heatmap, std and weather. `alt` is observer AMSL; omitting it means terrain mean + 1000 m. `forecast_h` is hours ahead, 0–24, rounded to the quarter hour (the slider steps every 15 min to +3 h, then hourly to +23 h); a fractional offset interpolates between the two hourly Open-Meteo values either side, and 0 still reads the current hour exactly as the live sampler does. `inputs` carries what the grid was built from — the fetched elevation samples and the feature width — for the comparison view |
| `GET /ogn/live?wait_agl=` | Live snapshot. AGL from the warm cache unless `wait_agl=true` |
| `GET /ogn/search?q=&limit=` | Server-side id search — the client no longer holds a full glider list |
| `GET /ogn/track/{id}` | Last 8 h of positions (runs in a thread; the table holds millions of rows) |
| `GET /ogn/glider/{id}` | One aircraft by **exact** id, ignoring the viewport. `{"glider": null}` when it is gone — not a 404 |
| `GET /thermals/active` | Clusters of circling gliders |
| `GET /elevation?lat=&lon=` | Single SRTM point |
| `GET /healthz` | `{status, model_loaded}` |
| `POST /train` | Admin. Background retrain |
| `POST /seed?days_back=&limit=&reset=` | Admin. Rebuild the buffer from beacon history |
| `GET /verification/summary?days=` | Forecast ledger scorecard per horizon: statuses, within-forecast score for served / model / terrain / persistence with bootstrap CIs, hotspot hit rate vs chance, pooled score, Brier |
| `GET /verification/examples?status=&horizon_min=&limit=` | Recent forecasts with one status, always with the issued/scored counts they were picked from |
| `GET /verification/forecast/{id}` | One recorded forecast, its outcome and its pooled grid, for replay |
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

**The feed carries ids that are not one aircraft.** Measured on the beacon DB
(5 Nov 2026, 10:00–14:00 UTC, 2,013 ids): 134 ids made a leg no glider can fly
and 17 jumped more than 100 km. `FLR1900FF`, typed "tow plane", moved up to
628 km 253 times in four hours at 10,700 m — several airliners under one
address. `FLR18008D`, a "hang glider", reports 537–1,333 km/h. Three ids
reported altitudes above 64,000 m (a negative value through an unsigned field).
`_admit` in `ogn_client.py` sits between the parser and everything else:

- above `_MAX_ALT_M` (15,000 m) the beacon is dropped;
- above `_MAX_SPEED_KMH` (400; the feed's 99th percentile is 163) it is dropped
  and the id takes a strike;
- a fix taken *before* the one on the track is dropped — a second receiver
  running 2–3 s behind, or a tracker uploading its backlog, otherwise walks the
  icon backwards (0.55% of consecutive beacons);
- a position the track cannot reach at 400 km/h is held as a candidate. A lone
  outlier never shows. The track moves only when a second, *different* fix
  agrees (17% of consecutive beacons are one fix relayed twice, which confirms
  nothing) — and an established track still reporting does not move even then.
  A relocation is a strike.

Three strikes in 30 min quarantines the id for 30 min, extended while it keeps
earning them. Replaying those four hours: impossible legs shown fell from 822
to 34 for 0.39% of beacons rejected, and that understates it, because the DB
keeps no speed and the speed check could not take part. Counters are under
`feed.implausible` in `/training/status`. **Two aircraft genuinely sharing an
id show as one steady track** — the first to report; the other is refused
until the first has been silent for `_HANDOVER_S` (60 s).

**A scan has to be visible to count as an answer.** "Clicking bare map does
nothing" was three separate causes, all measured on the live site on
15 Nov 2026, and the backend was answering correctly through every one:

- The map opens at zoom 5, where the 0.1° grid is under 3 px wide. A bare-map
  click that would draw narrower than `MIN_SCAN_PX` (160) now flies in to fit
  it (`ScanFocusController`). A glider click does not move the map.
- At night the grid is zeroed and no cell is painted. The footprint outline is
  drawn regardless of content — dashed while the request is in flight — and a
  note over the map says why it is empty.
- By day the model returns mostly 3–25% (no grid of ten peaked above 56%), and
  the renderer skipped cells under 5% and drew the rest at ~15% opacity. One
  grid drew 932 of 40,401 cells. Only an exact zero is skipped now.

The forecast slider re-runs the scan on the map; it used to change only what
the *next* click would ask for. Predictions carry a sequence number, so a slow
earlier response can no longer overwrite a later click's heatmap.

**The raw data comparison splits the map by clipping, not by drawing.** The
sidebar toggle (or `?compare=1`) puts a draggable divider across the map: left
of it the inputs as they arrive, right of it the map as normally drawn. Every
canvas still spans the whole map and carries `LAYER_BUILT` or `LAYER_RAW`;
`index.css` clips each to its side of `--cmp-split` on the Leaflet container, so
dragging redraws nothing — which matters when one layer is a 40,401-cell grid.

- **A new map layer must carry one of the two classes**, or it draws across
  both halves and the comparison stops meaning anything.
- **The raw half shows only what arrived.** Reported positions, never
  `displayPosition`; every aircraft the same dot, because circling, tow and
  thermal are conclusions; and the elevation squares are the 121 values
  `/predict` actually fetched (`terrain_client.coarse_lattice`), not the
  interpolated grid thinned out. That last one holds only because the upsample
  runs endpoint to endpoint — change the interpolation and
  `test_coarse_lattice_is_the_samples_that_were_fetched` is what says so.
- **The divider uses native listeners**, not React props. It sits inside
  Leaflet's container, whose handlers run before the event reaches React's
  root, so a synthetic handler stops a press only after the map has started
  panning and read the release as a click asking for a prediction.
- Weather has no mark on the raw half on purpose. It is one sample cached per
  0.1° cell, so a pin at any exact point would claim a precision it lacks; the
  dashed outline of the patch it is spread across is the honest picture.
- The scan note (`.map-note`) is not a map layer: it sits outside the Leaflet
  container and is never clipped. It drops to the bottom edge while the
  comparison is on, because the caption cards hang from the top of the divider
  and the divider starts at the centre — where the note otherwise sits.

**The landing page (`/about`) is a second page, and it quotes this file.**
`vite.config.js` lists `index.html` and `about.html` as build inputs, and
`_static_file` in `main.py` is what makes `/about` answer with `about.html` —
before it, the catch-all served the map app for any path that was not an exact
file name. The map app stays at `/`, so `?compare=1` and `?panel=training`
links keep working; the landing page links to both.

- **Its numbers are copies.** The score and region tables (16 Nov 2026 run,
  below), the 27 Sep 0.542 comparison, the label thresholds and the 27%
  pseudo-absence figure are written out in `landing/strings.js` and
  `Landing.jsx`. Re-measure one here and change it there — that page is the
  only place a visitor reads them.
- **It is written for investors as well as pilots.** Confident register, but
  no claim past what was measured: "stronger than any single signal" is a
  point-estimate statement (the sun-angle baseline's interval overlaps the
  model's), and the 0.542 → 0.570 line is two measurements, not a proven
  trend — their intervals overlap too.
- **Its copy lives in `landing/strings.js`, not `i18n/strings.js`**, so the
  map app's bundle does not carry a page of prose. Invariant 5 still holds: no
  string in a component, every key in both languages. A dev build warns in the
  console on a missing key.
- **`--mono` (B612 Mono) is Latin-only** — no ğ, ş or İ. It is for figures;
  translated text set in it falls back glyph by glyph. Decimals are set in the
  tabular sans instead, because a monospaced Turkish decimal comma reads as
  "0, 54".
- **The hero is a simulation and its caption says so.** No real beacon is
  drawn there. The one live number on the page is the count from
  `/thermals/active`, and the line is simply absent when that request fails.

## Forecast ledger (`data/forecast_ledger.py`)

The offline benchmark scores the model **given the weather that happened**.
Meteo is constant across a grid, so a wrong weather forecast barely moves the
within-grid ranking while making the map wrong about whether there is lift at
all. That score therefore cannot say how skill falls with the horizon. The
ledger does, by recording forecasts before their time and checking them after.

- **Issue** (`_ledger_issue_job`, every `LEDGER_ISSUE_EVERY_MIN`): for each site
  (`LEDGER_FIXED_SITES` + the `LEDGER_DYNAMIC_SITES` busiest 0.1° cells at issue
  time) and each horizon in `LEDGER_HORIZONS_MIN`, run `compute_prediction` —
  the same function `/predict` uses — and store the served map, the model-only
  map, terrain alone and elevation, pooled 4×4 (~200 m, ~2.6 KB per forecast),
  plus model hash, meteo, the fallback flag, fusion weight and the circling
  clusters at issue time (the persistence baseline). Night targets are skipped
  before the model runs. Skips entirely past `LEDGER_METEO_CEILING` calls.
- **Score** (`_ledger_score_job`, :07/:22/:37/:52): beacons within
  ±`MATCH_WINDOW_MIN` of the target, soaring, untowed, labelled by `_label_for`,
  200–3000 m AGL, collapsed to one unit per (aircraft, pooled cell). Status:
  `supported` (≥ half the climbs in the top 20% of cells), `contradicted`
  (climbs mostly outside, or only sink and ≥2 sink units inside), `unverifiable`
  (no usable traffic — never counted as wrong), `excluded` (synthetic meteo,
  feed outage, or the window is no longer in the hot DB). Rules live in the
  module and are versioned by `RULES_VERSION`; change one, bump it.
- **Integrity:** SQLite triggers refuse UPDATE and DELETE on `forecasts`.
  Outcomes are a separate table. Only grids are pruned (`LEDGER_GRID_KEEP_DAYS`),
  never those of `supported` forecasts — they are the gallery.
- **Read a single `supported` with care:** one climb lands in the top 20% by
  chance one time in five. The claim is the per-horizon summary, not examples.
- **Known gap:** scoring reads the hot DB only. If the app is down past
  `BEACON_RETENTION_DAYS`, those forecasts score `no_observation_data`; reading
  the Parquet archive instead is the obvious next step.

`/predict` changed with it: live thermals are blended in with weight
`exp(-minutes / LIVE_FUSION_TAU_MIN)`, zero from `LIVE_FUSION_MAX_MIN` (they were
added at full strength at every horizon), and the response carries
`meteo_fallback`, `live_fusion_weight` and `target_at`. The ledger scores served
and model-only maps side by side, which is how the fusion constants should be
tuned.

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
  `BEACON_RETENTION_DAYS` defaults to **1**, lowered from 7 after the 17 Aug 2026
  outage and from 2 after the 18 Sep 2026 one: the binding constraint is RAM, not
  disk. A 4.26 GB DB against ~165 MB of page cache put the host at 96.6% iowait
  with uvicorn wedged in D state; a 2.1 GB one was still enough for a single full
  scan to evict every host daemon to swap. Day two is not lost — it is in the
  Parquet archive, which the nightly seed reads. `MIN_RETENTION_DAYS` went to 0.5
  with it, or the disk guard would have had no room to shorten anything.
- `_disk_guard()` shortens retention below `MIN_FREE_DISK_GB=3`, capped at 2
  rounds — a DB created before `auto_vacuum=INCREMENTAL` never returns freed
  pages to the OS, so waiting for free space to recover would spin forever.
- **Every module-level cache needs a bound, and a number that reports it.** The
  two in `terrain_client.py` had neither, and between 7 and 15 Nov 2026 the app
  container was OOM-killed 28 times — ten on the last day, under an hour apart.
  `_grid_cache` kept a 316 KB array for every grid centre ever asked about
  (~2,900 new a day, 300 an hour at a weekend midday). `_elev_cache` restored
  770,770 points at every start (196 MB, 267 B each), and `flush_point_cache`
  re-serialised all of them every 50 new points: 16 MB of JSON, a second of
  event loop and a 58 MB spike, three times inside one retrain cycle. All 27
  kills that could be placed fell inside a retrain cycle (22) or the nightly
  seed (5), and the baseline rose ~30 MB a day. Now `TERRAIN_GRID_CACHE` (64)
  and `TERRAIN_POINT_CACHE` (200,000) bound them, least recently *used* first —
  a read counts, or the points under an airfield would age out by fetch time
  while being hit every frame. Points are appended to `points.bin` as 20-byte
  records and the file is rewritten only when it passes twice the bound.
  Measured on a copy of the node's cache: point cache 196 → 56 MB, a flush
  1.0 s → 0.13 ms, start-up load 0.95 → 0.18 s. Sizes are under
  `terrain.cached` in `/training/status`. The old `points.json` is imported
  once and then left alone, because the previous image still reads it.
- **The 896 MB figures in this file no longer describe the node.** `free -m`
  there reported 3,905 MB total on 15 Nov 2026, with `mem_limit` at 700m (not
  the 600m the nightly-seed note above records) and `BEACON_RETENTION_DAYS`
  at 2 in `docker-compose.yml`. Nobody has re-derived the limits for the
  larger box; raising `mem_limit` would have hidden the cache growth, not
  fixed it.

**Upstream ceilings:** Copernicus DEM has no key and no quota (the terrain
default); OpenTopoData 1000 calls/day and 1 req/s, now only the fallback — as
the primary source a training node spent the whole day's quota in its first 40
minutes; Open-Meteo 10k/day; ESA WorldCover needs no key and has no published
limit.

**Copernicus vs SRTM** (36 grids, after the production upsample): elevation
4.4 m mean difference, slope 0.26° (r = 0.92), aspect 3.2°. The features barely
move, but a buffer collected under one source should not be silently extended
under the other — reset or set it aside when switching. Copernicus requires
attribution; the credit is on the map's attribution control. Its tiles above
50°N are narrower (2400 columns, then fewer), which is why the reader keeps
separate x and y pixel sizes.

## Config that surprises people

- The bounding box in `backend/config.py` is **worldwide**. That derives
  `OGN_APRS_PORT = 10152` (the full feed — port 14580 is the filtered one) and an
  empty `OGN_APRS_FILTER`. Narrow the box and the filter becomes an *area* filter
  `a/latN/lonW/latS/lonE` — deliberately not a radius, since no circle inside a
  box covers its corners.
- `frontend/src/config.js` `LAT/LON_MIN/MAX` is the **prediction grid** area
  (İnönü/Eskişehir), not the glider feed. Widening it explodes the grid; live
  glider bounds are the separate `GLIDER_*` constants.
- Map labels come from Esri's `Reference/World_Boundaries_and_Places` layer,
  not CARTO. Requested without a key, CARTO's `voyager_only_labels` tiles now
  return no labels and "API KEY REQUIRED" across each one (seen 15 Nov 2026),
  which tiled a watermark over every view. The ArcGIS tile path is
  `{z}/{y}/{x}` — row first. The Copernicus terrain credit rides on this
  layer's attribution string; carry it along if the layer is swapped again.
- `ENABLE_LANDCOVER` is off by default. The plumbing is complete and tested, but
  measured through `evaluation/` the change moves the within-group score by −0.04
  to +0.02 depending only on the CV fold seed. Flipping it on requires a retrain
  — the columns change meaning, not width. **Bound its cache first:**
  `landcover_client._grid_cache` is still a plain dict that keeps one stack per
  grid centre forever, the same shape as the terrain leak of Nov 2026.

## Housekeeping

- `.claude/` is gitignored; `CLAUDE.md` at the repo root is not, so durable
  project knowledge belongs here.
- **`*.pem`, `*.key` and `.env` are gitignored** (`.env.example` is not).
  That rule was added on 2 Oct 2026; before it this file claimed the
  protection existed when it did not. Nothing had leaked — verified across
  the whole history — but the VM's SSH key and the `ADMIN_TOKEN` env file
  live in the working tree, so it had been luck.
- The 22-column feature list goes stale often. Read `FEATURE_COUNT` and the
  `np.column_stack` block in `feature_engineering.py` rather than trusting any
  written list, including the one in `docs/ARCHITECTURE.md`.
