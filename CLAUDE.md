# CLAUDE.md

Guidance for Claude Code working in this repository.

## What this project is

**ThermalSense** predicts thermal lift for glider pilots. An XGBoost model scores
a 201x201 grid (~50 m cells, ~11 km across) around a clicked point and returns a
climb-probability heatmap; a React/Leaflet frontend draws it over live glider
traffic. The model trains itself online from the Open Glider Network (OGN) APRS
feed — every circling, climbing glider is a positive label.

Live deployment: `https://thermalsense-tr.polandcentral.cloudapp.azure.com`
(Azure VM, 2 vCPU / 3.9 GB RAM, app container capped at 1.5 GB — see
[DEPLOY.md](DEPLOY.md); the limits below were first set for an 896 MB box).
**Merging to `main` deploys it:** cron on the VM runs
[deploy/auto-deploy.sh](deploy/auto-deploy.sh) every five minutes, with a
health-checked rollback. A merged PR is live within minutes.

The UI is **Turkish-first** with a TR/EN toggle. The primary operating area is
İnönü / Eskişehir; the glider feed itself is worldwide.

## Commands

All backend commands run from `backend/`. A Windows venv exists at
`backend/.venv` with every dependency installed — prefer it.

```bash
# Tests — ~500 tests, ~15 s (needs pyarrow, or the archive tests skip)
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
`test_meteo_client.py::test_hour_index_falls_back_when_stamp_absent` fails
whenever the suite runs between 00:00 and 03:00 UTC: it drops the first
three hours of a day's array and then looks for the current hour in it. That
is the test, not a regression.

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
| [backend/pipeline/feature_engineering.py](backend/pipeline/feature_engineering.py) | `build_feature_matrix` and `FEATURE_COUNT` (30 columns: the original 22, then 8 full-resolution terrain columns at 22–29) |
| [backend/pipeline/hires_terrain.py](backend/pipeline/hires_terrain.py) | The 8 full-resolution terrain columns (slope, northness, eastness, ruggedness, TPI at 150/400/1000/2500 m), vectorised. `evaluation/hires_terrain.py` stays the scalar reference they were measured with; a test pins the two together |
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
| [frontend/src/components/Thermal3D.jsx](frontend/src/components/Thermal3D.jsx), [frontend/src/services/thermal3d.js](frontend/src/services/thermal3d.js) | The 3D view of one scan (2B/3B switch, `?view=3d`): renderer, camera, picking and caption in the component; terrain, ground texture, thermal columns, ceiling, labels and aircraft as plain three.js builders in the service. Lazy-loaded, so three.js is its own chunk |
| [frontend/src/i18n/strings.js](frontend/src/i18n/strings.js) | TR/EN dictionaries — must stay in key parity |
| [frontend/about.html](frontend/about.html), [frontend/src/landing/](frontend/src/landing/) | Landing page at `/about`, written as a scrolling pre-seed pitch about low-altitude fixed-wing UAVs (civil / dual-use framing): a second page of the same Vite build, not a route in the app. Own stylesheet and own TR/EN dictionary (`landing/strings.js`); language and theme come from the app's two providers, so both are one choice across the two pages |
| [DEPLOY.md](DEPLOY.md) | Hosting rationale, sizing, TLS, operating runbook |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Data flow, feature pipeline, training loop, resource budget |

## Hard invariants

Breaking any of these has taken the app down or silently corrupted results before.

1. **`--workers 1` is mandatory.** The APRS daemon thread and the `_live_gliders`
   and beacon buffers are per-process module state. A second worker opens a
   duplicate upstream connection and serves divergent data. This also rules out
   serverless entirely.

2. **`FEATURE_COUNT` and the saved model must agree — or be a listed prefix.**
   New columns are *appended*, and the old width goes into `LEGACY_WIDTHS`
   (`feature_engineering.py`). Then a model of that width keeps serving on the
   leading columns (`_model_view`), and a buffer of that width is widened with
   NaN and filled in rather than set aside, so there is no physics-only dark
   period. Any other width: `load()` refuses the model (physics fallback until a
   retrain) and sets the buffer aside as `.vN`. Inserting a column in the
   middle breaks the prefix and puts you back in that case. **A rollback past a
   widening is not free:** the older code refuses the wider model and buffer,
   so back both up before deploying one.

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

10. **Anything that trains passes `strict=True` to all four fetchers**
    (`fetch_meteo_features`, `fetch_meteo_historical`, `fetch_elevation_grid`,
    `fetch_hires_grid`) and skips the sample on `None`. The non-strict return is
    a stand-in made for a display request; with a real label attached it is a
    fabrication. `fetch_hires_grid`'s stand-in is all-NaN, and so is its answer
    whenever the source is not Copernicus (nothing else reads at that
    resolution): unknown, never a number.

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
  and above the estimated thermal top + 300 m (that climb is wave, ridge or
  engine; the top is `meteo_client.thermal_top_agl` — see "The thermal top
  replaced `cape_base`" below). Both the AGL floor and the thermal top are
  applied at collection *and* again at fit time (`_fit_rows`, used by
  `fit_and_gate`, `training_summary` and `evaluation/evaluate.py`), so a buffer
  written before either existed is cleaned without a `/seed` rebuild. Rows are
  masked, not deleted. The thermal top joined it on 20 Nov 2026; until then it
  was collection-only, on the grounds that it would thin the frozen
  benchmark's groups mid-run. Measured on the live buffer that day: it
  excludes 7,440 of 78,416 rows (635 positives) — all collected or rebuilt
  before the switch; 0 of the 691 collected after it — and takes the
  benchmark from 695 to 655 mixed-label groups against the 30 the gate needs.
  The gate's decision is paired on whatever rows the benchmark holds, so only
  the logged "serving" score mixes populations, for one cycle.
  `evaluate.py --no-thermal-top` reproduces scores recorded before then.
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
- **The counter counts billed calls, not requests** (since 19 Nov 2026).
  Open-Meteo bills a request over 10 variables or two weeks as a fraction
  more (`_call_weight`): the live request carries 13 variables, so it is 1.3
  calls, and an archive hour 1.1. `live_usage()["total"]` — what every budget
  checks — used to add 1 per request and read up to 30% low. Add a variable to
  `HOURLY_VARS` or `DISPLAY_VARS` and every request costs another 0.1.
- **The solar floor is checked before the feature matrix is built.** At night
  every candidate is dropped there, and building first spent ~7 MB and a terrain
  read to discard the result.
- **A cycle that changed nothing does not fit** (since 20 Nov 2026,
  `_buffer_changed`). The fit is deterministic (`random_state=42`), so a cycle
  that added no rows — all night, every night — rebuilt the challenger the gate
  had already judged and rewrote the unchanged buffer. If that challenger was
  reliably worse, each repeat was rejected again and spent one of
  `_MAX_CONSECUTIVE_SKIPS`, so an hour of darkness promoted it by repetition
  alone; if it had won, each repeat re-saved the same model. The flag, not
  "added this cycle", decides it: `/seed` fills the buffer and then calls
  `retrain()` to fit it, at 02:00 UTC when the cycle itself adds nothing; and
  `_backfill_hires` sets it when it fills rows in or finishes, because it edits
  rows in place and holds every fit until it is done. That fill-in runs every
  cycle, ahead of the check — a dark cycle that skipped it would stall it, and
  the first wider model with it. The first cycle after a start always fits,
  and a fit that raised or timed out runs again.
- **Buffer eviction is day-stratified, not chronological** (`_evict_to_cap`).
  The `_MAX_BUFFER = 200_000` cap stays — it is a RAM limit on an 896 MB box —
  but keeping the *newest* N meant a full buffer held one stretch of weather, so
  the model could only know the season it stood in. Every distinct day now gets
  an equal quota; inside a day positives are kept first (4% of the feed, two
  rate-limited API calls each), then the newest negatives. Measured: subsampling
  within one 5-day stretch moves the score less than its own noise (0.635 at 25%
  of groups vs 0.664 at 100%), so spending the cap on breadth costs nothing and
  buys regimes.
- **The model is refitted from scratch every cycle that adds rows, deliberately.** There is no
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
- **Türkiye first, since 21 Nov 2026** (`config.PRIORITY_BBOX`, the same box
  as `evaluation/regions.py`'s Türkiye; a test pins the two together). The
  live buffer held 1 Turkish row of 78,428 because both ways in sampled the
  worldwide feed blind. The live sampler took the first `MAX_SAMPLE_GRIDS`
  soaring aircraft in feed order, and the seed took every n-th labelable row
  by ROWID.
  - **Live sampler:** `retrain()` now sorts Turkish aircraft to the front of
    the candidates. The sort is stable, so everyone else keeps feed order and
    the cap is unchanged.
  - **Seed:** `seed_from_history` first takes every labelable row inside the
    box, up to `PRIORITY_SEED_LIMIT` (2,000) per source. Its worldwide sample
    of `limit` then excludes the box, so no row is read twice. Those buckets
    go first in the loop.
  - **Weather plan:** reordered so the Turkish cells are bought first. It is
    otherwise biggest first, which put Türkiye's few hours last, where a
    spent budget would have deferred them.
  - **Backfill:** `POST /seed?priority_only=true` takes the box alone, the
    one-off backfill of what earlier seeds sampled past. `last_seed` reports
    `priority_rows` and `priority_added`.

  **This is not much data, and the feed cannot give more.** Measured on the
  52-day archive (17 Aug–5 Nov): OGN heard 143 soaring aircraft over Türkiye
  (129 paragliders, 8 gliders) but only 188 climbing-circling fixes, against
  1.06M in the Alps and 2.4M in Germany. That is about 65 labelable rows a
  day. Turkish aircraft reach OGN about once a minute through phone apps
  (ids `NAV…`, `FNT…`): median 58 s between fixes, against 1.7 s in the
  Alps. The OGN receiver list (edited March 2026) has no receiver in
  Türkiye, and no glider was ever seen within 25 km of İnönü. The labels
  are still valid: `circling` is the turn rate the tracker itself reports
  (`rot`, > 8°/s), not a difference between fixes.

  Eviction does not protect the region: at the 200k cap the day quota keeps
  positives first, Turkish or not. More data needs OGN receivers (İnönü,
  Babadağ) or flight logs by agreement (THK İnönü, ypforum, XContest,
  thermal.kk7.ch). The to-do list tracks both.
- **The seed buys weather by the fortnight, not by the hour** (since 20 Nov
  2026). Open-Meteo bills a request up to 14 days long the same as one day, and
  `fetch_meteo_historical` asks for a whole day to keep one hour. So before its
  bucket loop the seed plans one request per 0.5° cell per ≤14-day span
  (`plan_historical_ranges`), and `fetch_meteo_historical_range` caches each
  wanted hour under the same key, as the same record (`_hist_record`), that
  the single-hour fetch would write. Night buckets come free; each used to
  cost a call to learn it was dark. Rebuilding 28 Sep–6 Nov from the archive
  this way took 1,741 calls instead of ~17,000; real range answers matched
  single-day ones on 41 of 41 hours (20 Nov), and 477 of 479 earlier (the two
  misses were one hour Open-Meteo had revised). Whatever the prefetch does not
  reach is fetched hour by hour as before. The budget now holds back only
  *uncached* buckets (`meteo_deferred_buckets`): the loop used to stop at the
  first bucket once the budget was reached, and after a prefetch that would
  strand buckets already paid for. Tests cannot reach Open-Meteo or the real
  cache: `conftest.py` stubs `_get_client` and moves the cache to `tmp_path`.
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
  sunrise rather than instantaneous `cos_incidence`. **Superseded 17 Nov 2026,
  see the next entry: the terrain had not run out, it was never read.**
- **Full-resolution terrain is the first lever that clearly moves the score.**
  `TERRAIN_RES` samples elevation every 0.01° (~1.1 km) and bilinearly fills
  the 50 m grid, so every slope and aspect the model has seen is a plane between
  points a kilometre apart. That spacing existed to spare OpenTopoData's
  1,000/day; Copernicus has no quota. `evaluation/hires_terrain.py` reads the
  real ~33 m surface around each buffer row and adds slope, northness,
  eastness, ruggedness and TPI at 150/400/1000/2500 m (centre minus a ring
  mean: positive on hills and ridges). Measured 17 Nov 2026 on the 30 Oct
  export (28,090 rows, 1,105 mixed-label units, `--by-hour`):

  | | seed 0 | seed 1 | seed 2 |
  |---|---|---|---|
  | base served | 0.575 | 0.559 | 0.594 |
  | hires served | 0.607 | 0.605 | — |
  | paired delta, served | **+0.033 [+0.015, +0.050]** | **+0.047 [+0.028, +0.066]** | — |
  | hires raw − base raw | +0.043 [+0.014, +0.074] | +0.055 [+0.028, +0.083] | +0.030 [+0.000, +0.058] |

  `tpi_2500` *alone* scores 0.613 [0.580, 0.645], above the whole 22-column
  model. **The airfield leak was checked and is not it.** Sinking, straight
  gliders on final glide into valley airfields could teach "low ground = no
  lift" the way `alt_agl` once taught "on the ground = no lift". Against
  OurAirports (48k fields, `--airports`): TPI is *stronger* ≥3 km from any
  field (0.627) and null under 3 km; positives sit nearer fields than
  negatives (4.4 vs 5.1 km median); the served delta holds at ≥5 km
  (+0.026 [+0.005, +0.049], +0.041 [+0.016, +0.067]). It does fade above
  ~800 m AGL (297 units, interval spans chance) — fewer units, and thermals
  drift off their trigger with height.
- **Shipped to the live model 19 Nov 2026** as columns 22–29
  (`pipeline/hires_terrain.py`, `terrain_client.fetch_hires_grid`), with the
  live gate as the "fresh buffer" check. The switch: the 22-column model keeps
  serving on columns 0–21; the 22-column buffer is widened, and
  `_backfill_hires` reads each row's columns at its own cell (the cell the
  live grid computes them for), `HIRES_BACKFILL_BUDGET_S` (60 s) per retrain
  cycle, sorted by area so rows share blocks; **fits are held until it is
  done**, so the first 30-column model is judged on complete rows. The gate
  then scores it against the 22-column model on the same benchmark rows, and
  `/training/status` shows `model.full_resolution_terrain` (rows left, model
  width) and `last_fit.width / serving_width / delta` for that decision.
  Measured locally on real Copernicus: one grid 0.3–0.9 s warm, 0.9–2.3 s cold
  (1–4 blocks), event loop never blocked over 80 ms (the arithmetic runs in a
  thread on blocks `dem_client.load_window` fetched first), peak 33–50 MB.
  Filling in a 48k-row buffer is ~2,600 distinct blocks, ~16 min of reading
  at home-connection speed, so ~17 cycles. **The first version took 4.3 s a
  grid:** `np.unique(axis=0)` over (lat, lon) pairs sorted every one of 400k
  points per chunk; one integer key per tile fixed it.
- **Also measured 17 Nov 2026, same export and seeds:** `prior` from the
  export's 43-day Parquet archive (`evaluation/archive_prior.py`, `--prior-db`)
  +0.008 / +0.016 / +0.016 served — consistent sign, one interval clear of
  zero, and nothing on top of hires terrain. Wind-relative aspect, slope-weighted
  aspect sin/cos and insolation integrated over 14 h (`windaspect`,
  `aspectcyc`, `insolation`): −0.003, +0.001, −0.002, all spanning zero.
  **The fold seed alone moves the base score 0.557–0.594**, so a single run of
  this harness is not a measurement; `--seed` exists for that.
- **Drift-corrected terrain is ruled out, measured 17 Nov 2026** on the 6 Nov
  buffer (47,419 rows, 2,009 mixed-label units, by hour, seeds 0–2). A glider
  circling at height sits downwind of the ground that triggered its thermal,
  so `evaluation/drift_terrain.py` reads the hires columns at a point shifted
  by wind × AGL / w — for *every* row, positive and negative, with labels left
  where they were observed. Shifting positives alone would move the goalposts,
  and with lat/lon among the columns it would teach "upwind edge of the
  traffic = lift". Against terrain read under the glider, averaged over seeds:
  upwind at w = 3 / 1.5 / 0.75 m/s (median shift 431 m / 861 m / 1.7 km)
  −0.005 / −0.014 / −0.020, the last two with every interval below zero;
  placebos of 861 m downwind −0.017 and crosswind −0.018. `tpi_2500` alone:
  0.603 under, 0.565 upwind, 0.576 downwind. **Every shift loses, more with
  distance, and upwind is no better than the wrong direction.** Above 800 m
  AGL the shortest shift gained +0.012 / +0.003 / +0.009, each interval
  spanning zero; giving the model both positions, +0.002. Likely why: the
  median row is 483 m AGL, and the buffer holds only Open-Meteo's 10 m wind
  (km/h — no unit is requested), weaker than and often veered from the wind
  aloft. Revisit only with wind at height in the buffer.
- **The same run reproduced hires terrain** on the 6 Nov buffer: served 0.593
  / 0.596 / 0.590 against base 0.570 / 0.569 / 0.567, deltas +0.023 to
  +0.027 with every interval clear of zero. Its days largely overlap the
  30 Oct export, so this is more rows, not an independent check — the live
  buffer still is. **A failed DEM fetch does not fail the run:** the first
  attempt lost DNS halfway, left 52% of rows NaN and showed hires at only
  +0.005–0.010. `drift_terrain score` refuses above 2% missing, and `compute`
  retries dropped connections and keeps range reads on disk (`--block-cache`,
  ~2,900 blocks for this buffer). `hires_terrain.py` has neither guard.
- **A per-cell surface energy budget adds nothing, measured 17 Nov 2026**
  (same buffer, units and seeds; `evaluation/energy_budget.py`). Per row:
  sunlight on the 30 m slope (direct × cos incidence, unless terrain within
  4 km is above the sun; plus diffuse sky and ground reflection), then
  albedo, ground storage and evaporative fraction by WorldCover class at
  ~40 m (literature-typical constants in `_SURFACE`, not site measurements),
  giving the sensible heat handed to the air — now and weighted over the
  previous 3 h, and its excess over flat-ground rings at 400 m and 1 km.
  Alone: `h_3h` 0.555, `h_now` 0.552, excess over 1 km 0.553 — the same as
  `cos_incidence` alone (0.551). Land cover alone 0.490, terrain shadow
  0.499. On top of hires terrain: +0.004 / −0.002 / +0.001. **All of its
  skill is the sun angle on the slope.** Land cover barely varies under the
  aircraft (median and p90 of the heat factor both 0.40), and shadow is rare
  at the hours gliders fly. The budget says how hot the ground should be;
  what is still missing is a measurement of how hot it is (satellite LST).
- **Wind at height does not rescue drift either, measured 17 Nov 2026.**
  `energy_budget aloft` fetches 925/850/700 hPa wind plus direct and diffuse
  radiation for every row from the historical forecast API: 3,170 requests
  for this buffer (one per 0.25° cell per ≤14-day window), none failed,
  cached on disk. `layer_wind` averages the profile from the ground to the
  glider, and `drift_terrain compute --aloft` drives the shifts with it at a
  true rise rate w. Averaged over seeds, against terrain under the glider:
  upwind at w = 3 / 2 / 1.2 m/s (median 487 m / 731 m / 1.2 km) 0.000 /
  −0.011 / −0.017; placebos at 731 m downwind −0.019, crosswind −0.014.
  Still no shift beats reading under the glider, but upwind now loses less
  than downwind in all three seeds, where with 10 m wind they tied — the
  direction is right, the effect is below the label noise. The layer and
  850 hPa wind as model columns, with slope-weighted windwardness:
  +0.006 / −0.004 / +0.011, one interval clear of zero — unproven.
- **What a thermal-resolving simulation would cost (for the pitch).** A
  site at 50 m, 11 × 11 km × 3 km, is 2.9M cells; a 12 h flying day at a
  1–2 s step is 21,600–43,200 steps. Scaled from the DALES GPU port
  (arXiv:2502.20412, whole timestep, per node per million cells: 192-core
  Genoa 9,712 µs, 4×A100 2,444 µs, 4×H100 846 µs): one H100 4–7 min,
  $0.15–0.47; one A100 10–20 min, $0.22–0.85; CPU 32–65 core-hours. A
  100 × 100 km region: $0.76–2.44 a day at 100 m, $12–39 at 50 m (H100).
  Prices are 2026 specialist-cloud on-demand (H100 $2.50–4, A100
  $1.30–2.50 per GPU-hour; hyperscalers 2–4× more). Caveats that go with
  the number: the benchmark ran ~46M cells per GPU, so a 2.9M-cell site
  underuses one (budget up to 3×); its case carries clouds, rain and
  radiation, so a dry run is cheaper per cell; chaos needs an ensemble of
  ~10 (×10); it needs boundary conditions from a regional forecast.
  Whiffle has run LES forecasts operationally on GPUs since 2016 at ~100 m.
- **3D thermal blocks are the second lever that holds, measured 18 Nov 2026**
  (`evaluation/thermal_blocks.py`; 6 Nov buffer, 2,009 units, by hour,
  seeds 0–2, against hires terrain). thermal.kk7.ch's 3D method on our own
  OGN archive: ~100 × 100 × 100 m blocks, each counting the aircraft that
  passed through and those circling in a climb (≥ 1 m/s fully, weaker
  partly), once per aircraft per block per day; soaring types only, untowed.
  As columns it is read from other days only — `past` (days before the
  row's) and `loo` (all but its own) — summed over the 3 × 3 × 3 blocks
  around the row, with a 2D twin (3 × 3 columns, no height) beside it. From
  the local 52-day archive (17 Aug–5 Nov), 67% of rows have any history.
  Served deltas, past: 3D +0.014 / +0.009 / +0.009, 2D +0.011 / +0.002 /
  +0.011; loo: 3D +0.017 / +0.010 / +0.007. **Read the single column with
  care:** `b3_share` alone scores 0.649, but hires already scores 0.640 on
  the same rows — busy places are easier; the fair gain there is +0.009 to
  +0.017, and rows without history gain nothing. **The airfield leak was
  checked and is not it** (`--airports`, OurAirports): `b3_share` alone is
  0.662 at ≥ 3 and ≥ 5 km from any field; the 3D delta at ≥ 5 km is +0.016 /
  +0.011 / +0.011 and at ≥ 8 km +0.021 / +0.010 / +0.013 — holding or
  growing with distance, though only seed 0 stays clear of zero as units
  thin out. Away from fields 3D beats 2D in every seed (+0.013 vs +0.007
  averaged at ≥ 5 km); near them the two tie, so height earns its place
  where thermals lean, not where gliders fly circuits. **Coverage is the
  limit:** blocks with ≥ 20 aircraft-days cover ~120 km² each of the Alps
  and Germany, and Türkiye has 10. That archive saw 129 paragliders over
  Türkiye but 188 circling-and-climbing beacons in 52 days, against 7.3M
  worldwide; thermal.kk7.ch covers Türkiye from paraglider IGC logs
  (XContest, Leonardo, …) under CC BY-NC-SA, which needs a separate
  agreement for commercial use. **Not in production;** re-measure once the
  archive holds a flying season. `build --bbox` writes the layer itself as
  Parquet (Alps: 174,114 blocks with ≥ 5 aircraft-days, 1.2 MB).
- **Satellite land-surface temperature adds nothing, MODIS or MTG, measured
  19–20 Nov 2026** (`evaluation/lst.py`; 6 Nov buffer, 2,009 units, by hour,
  seeds 0–2, 1,500 resamples, against hires terrain from the drift cache).
  MOD11A1 / MYD11A1 v6.1 came from Planetary Computer: anonymous STAC and SAS
  token, COG range reads, 395 tile-days, none failed. QC-masked to an LST
  error ≤ 2 K, 1 km. Per row: LST at the pixel, its anomaly against the
  ±0.05° grid box, and LST − T2m. Coverage was 74% of rows on either platform
  (Aqua 45%, Terra 61%) and 85% of mixed-label units. Both orbits have
  drifted: Aqua's median pass is now **15:00** local solar time, not 13:30,
  and Terra's 09:18. **Raw LST ranks lift below chance** (0.432–0.471 alone)
  because it is an altimeter: skin temperature falls 5.6 K/km (Aqua) and
  4.4 K/km (Terra) inside a unit, and elevation alone scores 0.605 on the
  same rows. With that lapse taken out (`lapse_adjusted`, fitted without
  labels), Terra scores 0.505 and Aqua 0.544, about what the sun angle
  scores. Served deltas against hires: raw +0.001 / −0.004 / +0.004,
  lapse-adjusted −0.001 / −0.004 / −0.003, every interval spanning zero. On
  the 1,710 covered units only: +0.000 / −0.005 / +0.004. A daily pass hours
  from the row's own hour cannot say which ground is hot *now*. That is
  the case for the geostationary product. **MTG/FCI MTLST (LSA-007), the
  geostationary product, adds nothing either** (20 Nov). Every unit reads
  the HH:00 slot of its own hour, plus the hourly morning back to sunrise
  for `rise` / `rate`: 527 slots, 5.8 GB, none missing. Kept: clear sky by
  QFLAGS and LST_uncertainty ≤ 2 K; at the rows that cut removed 3 of
  17,647 clear pixels. The effective FCI pixel is 2.1 × 3.7 km in the Alps,
  2.2 × 4.3 Germany, 2.1 × 2.9 Iberia and 2.1 × 3.5 Türkiye, measured on
  the LATLON grid at the rows (3.70 × 2.17 km median). The 2 km figure
  holds only at nadir. **Coverage is the first problem:** 84% of rows are
  in view, but one hourly look meets Oct–Nov cloud. That leaves 36% of rows
  and 43% of mixed-label units (861 units scored on covered rows), against
  MODIS's 85% from two passes a day. Values are sound: within an hour of a
  MODIS pass r = 0.92 (Aqua) and 0.88 (Terra), MTG 1.5–2.6 K warmer. LST −
  T2m has a median of +3.9 K, the morning rise 11.5 K at 2.1 K/h. **It is
  still an altimeter:** raw 0.419, anomaly 0.421, rise 0.420 and rate 0.419
  alone, while elevation alone scores 0.641 on the same rows. The lapse
  inside a unit is only 2.1 K/km, because the pixel averages 8 km² of
  terrain the row's own height barely describes. So the label-free
  adjustment under-corrects and leaves 0.468, still below chance. Served
  deltas against hires: raw −0.000 / −0.004 / +0.007 [+0.000, +0.013],
  lapse-adjusted +0.003 / −0.002 / +0.006. Covered units only: raw −0.005
  / −0.000 / +0.010 [+0.002, +0.019]. Every LST column from both sources
  together: −0.002 / −0.002 / +0.005. The sign changes with the seed and
  one seed touches zero: unproven. **At 1–4 km, skin temperature mostly
  measures height, and inside an 11 km map there is little left once
  height is known.** The LSA SAF LATLON file is public; slot files need the
  free login (HTTP Basic). `lst need` writes the list, and a typo at
  curl's one password prompt fails all 527 silently, which is why the
  first two downloads fetched nothing. Oct–Nov has the weakest surface
  contrasts of the year, so a null here does not rule out summer.
  Re-measure on a summer buffer before calling satellite LST closed.
- **The thermal top replaced `cape_base`, 19 Nov 2026** — the "thermal base"
  on the map and the ceiling of the training filter. Now
  `meteo_client.thermal_top_agl`: **min(boundary layer height, condensation
  level)**, the LCL being 125 m per °C of dew-point spread (Magnus dew point
  from 2 m temperature and humidity). Dry thermals stop at the layer top;
  where the layer clears the LCL they stop at cloud base. The old
  `(t2m − 8) / max(1, (t2m − t850) / 1.5) × 1000` read no humidity and no
  layer: over the 3,567 cached daylight hours it was **0 m — filter off — on
  22.7%** (every hour under 8 °C), sat > 300 m above the new estimate on 42%,
  and reached 27,200 m wherever 2 m and 850 hPa were within 1.5 °C (high
  ground, inversions). At İnönü that noon it said 2,273 m against an 890 m
  cloud base. Measured three ways (`evaluation/thermal_top.py`):
  - **It predicts how high gliders climb** (`replay`): in the 978 (0.5°,
    hour) buckets with ≥ 3 thermalling aircraft, the p95 height of
    climbing-circling beacons (median 855 m) against each estimate — new
    Spearman **0.73**, median miss **229 m**, observed/estimate 0.90; old
    0.55, 715 m, 0.57, and no estimate at all in 11% of buckets. Layer
    height alone 0.71 / 290 m, LCL alone 0.66 / 375 m: the minimum beats both.
  - **What it admits** (`replay`): 10 archive days (17 Sep – 5 Nov) joined
    to the cached meteo, 2.35M labelled beacons ≥ 200 m AGL, terrain from the
    cached Copernicus lattice (95.1% known). Positives kept 89.8% → **96.8%**
    (per aircraft-hour 94.8% → 95.8%), negatives 87.6% → 89.5% (93.9% →
    92.2%). The old filter was throwing away real climbs: Germany positives
    74.9% → 96.3%: on cool October days just above 8 °C the old formula
    gave a median 132 m, and dropped climbs at a median 718 m AGL under a
    778 m new top.
    The Alps are the one place it keeps fewer climbing aircraft-hours (97.0%
    → 95.4%) — wave and high mountain thermals a coarse NWP layer
    under-reads. These rows are absent from any buffer, so only the replay
    sees them.
  - **The score does not move** (`buffer`, 6 Nov buffer, 47,419 rows): on
    top of the old filter the new one drops 250 positives (4.0%) and 4,151
    negatives (10.1%). Same folds, a model trained only on what it keeps
    against one trained on everything, served, by hour: **+0.003 / −0.001 /
    +0.000** (seeds 0–2) on the kept rows, every interval within ±0.012 of
    zero over 1,909 units; on all rows ±0.001. `evaluate.py --variant base
    --by-hour` on the filtered buffer: 0.569 / 0.570 / 0.559 against 0.570 /
    0.569 / 0.567 unfiltered — the seed-2 dip is the folds being re-cut on
    fewer rows; the paired run, on fixed folds, shows +0.000.
  So the switch is justified by the estimate, not by the model: the map shows
  a number that tracks reality, and the filter stops discarding real climbs
  on cool days and stops switching itself off — at no cost to the score.
  **Semantics changed with it:** only `None` is "no estimate"; 0 m (saturated
  air) is a real one. The old function treated 0 as missing because the old
  formula produced it below 8 °C. Historical-cache entries keep a
  `cape_base` key, now holding the new top (`_with_legacy_base`), so an older
  image's `/seed` still reads them after a rollback; nothing in this code
  reads the key, and old entries are recomputed from their own fields.
- **More days do not yet measurably help on an unseen day, measured 20 Nov
  2026** (`evaluation/learning_curve.py`, live buffer of 20 Nov: 71,034 rows
  after `_fit_rows`, 57 scorable days, 4,487 (day, cell, hour) units, seeds
  0–2). It replays the buffer: for each day D the production fit on every day
  before D against the same fit on days before D − 7, both blind to D, paired
  on D's units. Pooled: **+0.001 [−0.008, +0.011] / +0.001 [−0.005, +0.008] /
  +0.004 [−0.004, +0.012] — inconclusive.** A 14-day gap: +0.010 / +0.005 /
  +0.012, one interval clear of zero. Only September, with 2–14 days of
  history, gains from a week: +0.016 / +0.006 / +0.021, two of three clear;
  October and November ±0.004. The model does beat terrain on days it never
  saw: +0.022 to +0.025, every interval clear. Its out-of-day score is
  0.535–0.538, the ledger's 0.52–0.54; grouped CV on the same units 0.542–0.544
  (forward minus CV −0.004 to −0.008, one clear). The newer model has also seen
  the seven days nearest D, so a gain could not separate quantity from recency.
  Re-run as the buffer grows; `models/score_history.py` answers it forward.
- **Quality gate:** the challenger and the serving model are scored on identical
  rows from a *frozen, group-disjoint* benchmark (`evaluation/holdout.py`), and
  the challenger is **rejected only when the paired bootstrap CI
  (`paired_delta_ci`) lies entirely below zero** — reliably worse. One the
  benchmark cannot tell apart from the serving model is promoted. So the gate
  stops regressions; it does not pick improvements, and because every cycle
  refits from scratch, a fresh model can be promoted every cycle. **"Accepted"
  is not evidence that a change helped:** the 22 → 30 column switch was
  accepted on 19 Nov 2026 at −0.009 [−0.022, +0.009] over 199 groups. Judge a
  change by the paired delta in `last_fit`, the offline harness and the
  forecast ledger. Until 19 Nov 2026 this entry said a challenger needed the
  whole interval *above* zero; the code (`fit_and_gate`, `delta["hi"] < 0`)
  has never done that, from the gate's first commit on 16 Aug 2026. After
  `_MAX_CONSECUTIVE_SKIPS = 12` consecutive rejections it accepts and
  re-baselines, so a lucky score cannot freeze the model forever.
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
| `GET /terrain/relief?lat=&lon=` | The scan patch's elevation every `RELIEF_STEP` (0.001°, ~100 m), 101 × 101, same snapped centre and row order as `heatmap`, for the 3D view. `source` is `copernicus` or `lattice` (the model's interpolated grid, when Copernicus cannot answer); `filled` counts sea points taken from the lattice. 503 when neither answers. Display only |
| `GET /weather?lat=&lon=&forecast_h=` | Sky and local clock at one point, no model run — for the pinned glider card. Same cached meteo call as `/predict` |
| `GET /healthz` | `{status, model_loaded}` |
| `POST /train` | Admin. Background retrain |
| `POST /seed?days_back=&limit=&reset=&priority_only=` | Admin. Rebuild the buffer from beacon history. Türkiye's rows always go first; `priority_only=true` takes those alone (see "Türkiye first") |
| `GET /verification/summary?days=` | Forecast ledger scorecard per horizon: statuses, within-forecast score for served / model / terrain / persistence with bootstrap CIs, hotspot hit rate vs chance, pooled score, Brier |
| `GET /verification/examples?status=&horizon_min=&limit=` | Recent forecasts with one status, always with the issued/scored counts they were picked from |
| `GET /verification/forecast/{id}` | One recorded forecast, its outcome and its pooled grid, for replay |
| `WS /ws/live` | Frames every `WS_FRAME_INTERVAL` (1 s). Client sends `{"bounds":{lat_min,lat_max,lon_min,lon_max}}` on connect and on every map move |
| `GET /todo` | The team's shared to-do list (see "Housekeeping"). Open to read, `no-store`, `noindex` |
| `PUT /todo` | Admin. `{version, items}`: replaces the list if it is still at `version`, else 409 |
| `GET /todo/auth` | Admin. 200 when the key is right; the to-do page tests a key with it before offering editing |

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

**The 3D view draws one scan, and keeps measured and drawn apart** (added
26 Nov 2026; the 2B/3B switch on the map, or `?view=3d`). The scan patch is a
block of real relief (`/terrain/relief`; at İnönü 818–1,383 m, one DEM block,
0.58 s cold) under the same Esri imagery and the heatmap painted exactly as
in 2D (`services/thermalColor.js` is the one colour scale). Over it: a column
from each hotspot to the thermal top, a sheet at that top, and the live
aircraft at reported altitude with their trails. The Leaflet map stays
mounted underneath, so the viewport the socket streams and the 2D view a user
left are both kept.
- **The columns are an illustration, and the caption says so.** The model
  predicts a probability per cell, not a thermal's width or lean. Hotspots
  are 8-cell blocks that are local maxima, in the top 15% of blocks and at
  least half the peak, at least max(900 m, 0.8 × top) apart; half the peak
  alone admitted every bump of the flat 11–28% İnönü grid. Position is
  relative, colour and opacity are the absolute probability. The lean is the
  10 m wind at an assumed 2 m/s climb (`CLIMB_MS`).
- **Every height is drawn ×1.5** (`VERTICAL_SCALE`) — terrain, ceiling and
  aircraft alike, so nothing sinks into a raised hill. The caption states it.
- **The same rules as the 2D icons:** the icon may be dead-reckoned, the
  trail and the line to the ground never are. An aircraft is a flat
  silhouette (heading from above) plus a camera-facing dot, because a flat
  shape vanishes edge-on.
- **A click on the terrain scans there, a click on an aircraft pins it**, and
  in 3D the search box scans the place found (it only moved the hidden map).
  Turning on the comparison or the cloud layer returns to 2D: both are
  Leaflet-only. Choosing 3D turns the comparison off.
- **Without `/terrain/relief` it still works:** it draws the 121 samples
  `/predict` sent, filled to 51 × 51, and the caption says "interpolated
  between 1 km samples". Imagery that fails leaves shaded relief.
- Light is the sun at `target_at` (`sunPosition`), so the slopes it lights
  are the ones the model's sun-angle column rewards; at night the sky goes
  dark and no columns are drawn. The view carries the imagery and Copernicus
  credit itself, since it covers Leaflet's attribution control.
- `renderer.forceContextLoss()` on unmount is load-bearing: every 2D → 3D
  switch makes a new WebGL context, and browsers stop at about sixteen.

**The landing page (`/about`) is a second page, and it quotes this file.**
`vite.config.js` lists `index.html` and `about.html` as build inputs, and
`_static_file` in `main.py` is what makes `/about` answer with `about.html` —
before it, the catch-all served the map app for any path that was not an exact
file name. The map app stays at `/`, so `?compare=1` and `?panel=training`
links keep working; the landing page links to both.

- **It is a pre-seed pitch**, fourteen sections that scroll as one page,
  about **low-altitude fixed-wing UAVs** (below ~10,000 ft — the band thermals
  form in). Rewritten 17 Nov 2026 after the TUSAŞ HANGAR BİGG mentor review,
  whose wording rules sit at the top of `landing/strings.js` and bind every
  edit: never a bare "İHA"/"UAV" (it reads as a quadcopter), no "long/high
  endurance" class name (LALE is gone), "termal verisi" not "planör verisi",
  civil/dual-use rather than "defence is our main target", flight *duration*
  never range, no diverting from the mission for a thermal, no internet and
  no extra hardware on the aircraft — maps are made before flight and handed
  to the GCS. It carries three kinds of figure and must keep them apart:
  published third-party results (soaring flight tests, the market reports),
  each linked, with a bibliography as the last section; our own scores; and
  labelled assumptions (the 1 / (1 − f) calculator, the SOM line).
- **The horizon table is the ledger, copied.** `HORIZONS` in `Landing.jsx`
  is `/verification/summary?days=30` from the live node on 17 Nov 2026. It
  shows what the ledger shows: whether/when-there-is-lift (pooled AUC) holds
  at 0.67–0.71 out to 3 h, while within-map hotspot hits beat chance only at
  15 min. The 6 h row reads "being measured" until 360 (added to
  `LEDGER_HORIZONS_MIN` that day) has results — never fill it with a guess.
- **No figure on it claims how much ThermalSense extends endurance** — that
  is unmeasured, and the page says so. The calculator is `1 / (1 − f)` on a
  stated assumption; the map's effect on f is presented as the hypothesis the
  pilot project would measure. Keep it that way: a defence buyer will ask for
  the source of every number.
- **Its numbers are copies.** The scores (16 Nov 2026 run, below) are written
  out in `Landing.jsx` `SCORES` and in `landing/strings.js` (`scoreMeta`,
  `scoreHonest`); the horizon table in `HORIZONS`; market figures and prices
  in `strings.js` with their sources in its header. Re-measure one here and change it there. "Better than any
  single input" is a point-estimate statement — the sun-angle baseline's
  interval overlaps the model's.
- **Section 08 is a sourced chronology of the field** (added 20 Nov 2026):
  `TIMELINE` in `Landing.jsx`, 17 publications from 1980 to Aug 2026 in
  four tracks (physics, in-flight soaring control, maps from flight data, AI
  weather), then one ThermalSense row that links up to sections 05 and 07
  rather than repeating figures. Each source with a `cite` is appended to the
  bibliography (`BIBLIOGRAPHY`); one without must already be in `REFS`, and a
  dev build warns otherwise. The "frontier" lines (`fr*`) claim only what that
  survey did *not find* — never that nobody does it. Two dates were left out
  because no source would confirm them: when RASP/BLIPMAP started, and when
  thermal.kk7.ch launched. The 2026 paraglider paper's 123.6 m/°C is its
  warm-season, midday, low-mountain subset, not France as a whole.
- **Its copy lives in `landing/strings.js`, not `i18n/strings.js`**, so the
  map app's bundle does not carry a page of prose. Invariant 5 still holds: no
  string in a component, every key in both languages. A dev build warns in the
  console on a missing key.
- **`--mono` (B612 Mono) is Latin-only** — no ğ, ş or İ. It is for figures;
  translated text set in it falls back glyph by glyph. Decimals are set in the
  tabular sans instead, because a monospaced Turkish decimal comma reads as
  "0, 54".
- **The glider animation is a simulation and its caption says so.** No real
  beacon is drawn there. The one live number on the page is the count from
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

**Sky and local clock** (`weather.condition`, `is_day`, `utc_offset_s`,
`timezone` in `/predict`, and `/weather`): `weathercode` and `is_day` ride in
the live forecast request that is made anyway (`meteo_client.DISPLAY_VARS`),
never in the feature matrix. The request says `timezone=auto` to learn the
point's IANA zone and `timeformat=unixtime` so the time axis stays absolute —
the slot lookup reads the same UTC hour it read under `timezone=UTC`. The code
takes the nearer hour (a category does not interpolate); the offset is for the
target moment via `zoneinfo`, so a slider across a DST change reads the right
clock. The fallback carries none of these, and the map then shows neither.

`/predict` changed with it: live thermals are blended in with weight
`exp(-minutes / LIVE_FUSION_TAU_MIN)`, zero from `LIVE_FUSION_MAX_MIN` (they were
added at full strength at every horizon), and the response carries
`meteo_fallback`, `live_fusion_weight` and `target_at`. The ledger scores served
and model-only maps side by side, which is how the fusion constants should be
tuned.

**Cumulus outlook** (`weather.cumulus` in `/predict`, `meteo_client.cumulus_outlook`):
`cumulus` / `blue` / `none` and a cloud base from values the request already
carries — dew point by Magnus from 2 m temperature and humidity, base ≈ 125 m
per °C of spread (Lawrence 2005), cumulus where `boundary_layer_height` clears
it. Display only, no variable and no call added, `None` under the fallback.
It shares `lcl_agl` with the thermal top (see "The thermal top replaced
`cape_base`"), so the two agree by construction: under `cumulus` the "T-base"
chip and the cloud base are the same level; under `blue` the thermal base is
the layer top, below the cloud base this chip names. Before that switch they
disagreed — 900 m cloud base against a `cape_base` of 2,273 m AGL at İnönü
noon on 19 Nov — which is how the old formula was caught.

**Clouds now** (`components/CloudLayer.jsx`, toggle or `?clouds=1`): where
the clouds are, **drawn by us, not photographed.** It replaced a GeoColour
satellite image on 20 Nov 2026 — the photograph sat over the map at 80% and
did not look like part of it. Two of EUMETSAT's derived Meteosat products are
read as *data* by the browser (public WMS, no key, CORS open, no backend
memory, no Open-Meteo calls): the **cloud mask** `msg_fes:clm` (white cloud,
green land, blue sea, resampled — so a pixel's darkest channel is its cloud
fraction) and the **cloud-top height** `msg_fes:cth` (a colour ramp, decoded
back to metres by nearest stop in `CLOUD_TOP_RAMP`, read off the product's
legend). MSG at 0°, 15 min, ~3 km at nadir, infrared, so it works at night,
which GeoColour did not. Its disc ends near ±75° longitude: no clouds are
drawn over the Americas, which means unknown, not clear. The illustration is
a lattice of `CLOUD_PUFF_PX` anchored in world pixels (so it does not shimmer
on a pan): each cloudy node becomes a heap (a dome and six turrets) for low
tops, a soft translucent sheet with a faint dapple for mid tops, and a veil
with tapering cirrus fibres for high tops. Since 22 Nov 2026 they are drawn
as seen from above and lit from the north-west, the hillshade convention:
shaded lobe sprites, the heap's south-east edge darkened as a whole, feathered
edges and no outline, and a soft navy ground shadow that falls further and
fainter the higher the cloud. The first version was flat shapes with a thin
dark rim, like the map's other marks; on satellite imagery it read as
stickers. Colours are muted rather than paper-white so a deck does not
outshine the imagery, and are not themed. The sidebar key draws from the same
`COLOURS` and keeps a faint rim, or a white heap vanishes on the light
sidebar. Observed, never forecast; the sidebar says so when the slider is
ahead. Traps:
- **Always send `time=`.** Without it the server answers "latest" with
  `max-age=604800`, and a test request came back a sunlit scene during the
  Turkish night — a cached render, not the current frame. The newest time is
  the `default` of each product's own capabilities document; the two are not
  always on the same frame (cloud-top height was 15 min ahead on 20 Nov).
- **PNG only, `crossOrigin = 'anonymous'`.** The colours are the data, so a
  JPEG would corrupt them, and without the CORS flag the canvas is tainted and
  `getImageData` throws.
- **The ramp is copied, not fetched.** If EUMETSAT restyles `cth`, heights
  decode wrong (clouds still land in the right place — that is the mask).
  Re-read the legend's colour bar (GetLegendGraphic, x 155–634 = 320–16,000 m).
- **z-index 400, not below.** Leaflet's map pane is `z-index: 400` and opaque,
  so a canvas portalled into the container below that is invisible.
  `OgnHeatmapCanvas` (the "live activity layer", 398) is exactly that: a solid
  red test fill on it did not show on 19 Nov 2026. It has been hidden under the
  base imagery, and is left so until someone decides it should be seen.

## Resource budget

The deployment target is 2 vCPU / 896 MB, and most odd-looking choices come from it.

- `PREDICT_CONCURRENCY=1` — predictions are queued, not parallelised. Each is
  core-bound and holds ~30 MB; `asyncio.to_thread` would otherwise hand out
  `min(32, cpu_count+4)` threads, each spawning its own OpenMP pool.
- `XGB_FIT_THREADS=1` (the background job yields cores), `XGB_PREDICT_THREADS`
  takes all of them.
- `mem_limit: 1536m` on the app service — a blast radius, not a target. It
  was 700m from the 896 MB box until 19 Nov 2026; see the note at the end of
  this section.
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
  `terrain.cached` in `/training/status`, beside the 3D view's relief grids
  (`TERRAIN_RELIEF_CACHE`, 32 × 40 KB). The full-resolution grids
  (`_hires_cache`, `TERRAIN_HIRES_CACHE` 16 × ~1.3 MB) are bounded and
  reported there too, and memory-only on purpose: one per centre on disk would
  be ~2 GB a day. The old `points.json` is imported
  once and then left alone, because the previous image still reads it.
- **The 896 MB figures in this file no longer describe the node.** `free -m`
  there reports 3,905 MB total (15 and 19 Nov 2026), with
  `BEACON_RETENTION_DAYS` at 2 in `docker-compose.yml`. `mem_limit` stayed at
  700m (not the 600m the nightly-seed note above records) while the terrain
  caches leaked, because raising it then would have hidden the growth, not
  fixed it. **Raised to 1536m on 19 Nov 2026**, once the kernel log showed no
  OOM kill since 12:39 UTC on 16 Nov — every earlier one was this cgroup's
  limit (`CONSTRAINT_MEMCG`), never the host. Measured before the change, with
  the 30-column switch running: app 449 MB anon at rest (309 MB just after a
  restart), 593 MB cgroup peak during the terrain fill-in, Caddy 23 MB, the
  host's own processes ~750 MB, page cache 2.6 GB. At the new cap ≥ 1.6 GB of
  page cache is left for the beacon DB. Do not lift the cap entirely: the
  18 Sep outage was the host starving, not the container. The other limits
  (`_MAX_BUFFER`, retention, `PREDICT_CONCURRENCY`) have still not been
  re-derived for the larger box.

**Upstream ceilings:** Copernicus DEM has no key and no quota (the terrain
default); OpenTopoData 1000 calls/day and 1 req/s, now only the fallback — as
the primary source a training node spent the whole day's quota in its first 40
minutes; Open-Meteo 10k/day, billed in calls (see the counter note above); ESA
WorldCover needs no key and has no published limit; EUMETSAT's WMS lists no
fees and no access constraints.

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
- **The map opens on Türkiye and scans İnönü by itself** (`MAP_CENTER`,
  `HOME_SCAN`; the opening effect in `App.jsx`). It used to open on the
  European soaring belt, where the traffic is — the mentor review asked for a
  demo that opens on a filled Turkish map. At night (outside 09–16 TRT) the
  opening scan asks for the next local noon instead of now, because the night
  guard zeroes a night grid; the forecast slider shows the offset. The map
  app's aircraft wording is "süzülen hava aracı" / "soaring aircraft", and the
  compare toggle is labelled "Ham veri ↔ ThermalSense AI".
- **Every altitude on screen names its reference** (`metresMsl` /
  `metresAgl`). The prediction altitude is MSL (with its AGL beneath), the
  thermal base is AGL (`thermal_top_agl` is a height above the surface, in
  50 m steps) with its MSL equivalent over the grid's mean terrain. Two bare "m" figures side by side
  read as comparable and were not — the mentor review asked exactly this.
  The GeoJSON export's `thermal_base_m` is still AGL and still unlabelled.
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
- The 30-column feature list goes stale often. Read `FEATURE_COUNT` and the
  `np.column_stack` block in `feature_engineering.py` rather than trusting any
  written list, including the one in `docs/ARCHITECTURE.md`.
- **The team's to-do list is at `/to-do-list`** (added 20 Nov 2026): done, to
  do, to research, so the two of us and every Claude session work from one
  record. It is linked from nowhere, is `noindex` (meta tag and
  `X-Robots-Tag`), and a test fails if a public page or bundle ever names it.
  It is not secret, though: anyone with the address can read it, so nothing
  that must stay private belongs in it. Editing takes the admin key
  (`X-Admin-Token`), which the page asks for once and keeps in that browser.
  - **The live list is on the VM, not in the repo.** It is
    `/data/data/todo.json` on the data volume (`config.TODO_PATH`, written by
    `data/todo_store.py`), so edits survive every deploy.
    `backend/todo_seed.json` is only the first version, read while that file
    does not exist. Editing the seed after the first save changes nothing
    live. Change the list on the page.
  - Every save names the version it started from, and a stale one gets a 409.
    The page then reloads, so two people editing at once cannot erase each
    other's change.
  - The 30 most recent replaced versions are kept in `/data/data/todo_history/`.
    That is the undo for a wrong delete: copy one back over `todo.json` on the
    VM.
  - The page is `frontend/public/to-do-list.html`: plain HTML and JS, copied
    into the build as-is, outside both React bundles. It shares
    `thermalsense.lang` / `thermalsense.theme` with the other two pages. Every
    item string reaches the DOM as text, and only URLs and `#123` (a PR)
    become links.
