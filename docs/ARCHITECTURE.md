# ThermalSense — Architecture

How a click on the map becomes a heatmap, and how the model that produces it
teaches itself from live glider traffic.

Companion documents: [../README.md](../README.md) for the overview and quick
start, [../DEPLOY.md](../DEPLOY.md) for hosting, [../CLAUDE.md](../CLAUDE.md)
for the invariants an editor must not break.

---

## 1. Process shape

The whole backend is **one Python process** with three concurrent actors:

| Actor | What it is | What it owns |
|---|---|---|
| **Event loop** | uvicorn / FastAPI | HTTP endpoints, the `/ws/live` sockets |
| **APRS thread** | daemon thread started in `lifespan` | the TCP socket to `aprs.glidernet.org`, the `_live_gliders` dict, the beacon ring buffers |
| **APScheduler** | asyncio scheduler on the same loop | retrain every 300 s, beacon purge, cache flush, disk guard |

The APRS thread and the event loop share **module-level mutable state** in
`data/ogn_client.py`. That is why `--workers 1` is not a default but a
constraint: a second worker would open a second upstream connection and serve a
different view of the world from the same URL. It is also why serverless is
impossible — the socket must stay open, the WebSocket must stay open, and the
model and beacon DB must survive on disk.

Two kinds of work are pushed off the loop with `asyncio.to_thread`, because both
hold the GIL long enough to stall every other request in a single-process app:

- **Inference** (`model.predict`) — XGBoost releases the GIL inside its OpenMP
  section, so a worker thread genuinely runs alongside the loop.
- **Track queries** (`fetch_glider_track`) — a synchronous SQLite read against a
  multi-million-row table.

## 2. The prediction path

```
GET /predict?lat&lon&alt&forecast_h
   │
   ├─ snap_grid_centre(lat, lon)          snap to the TERRAIN_RES lattice (0.01 deg)
   │
   ├─ gather:
   │    fetch_meteo_features()            Open-Meteo, one point, hourly index
   │    fetch_elevation_grid()            OpenTopoData SRTM, coarse then upsampled
   │  [ fetch_landcover_fine()            ESA WorldCover, only if ENABLE_LANDCOVER ]
   │
   ├─ build_feature_matrix()              -> (rows*cols, 22) float64
   │
   ├─ semaphore(PREDICT_CONCURRENCY=1)
   │    to_thread: model.predict()        50 MC passes -> mean, std
   │
   ├─ _apply_ogn_fusion()                 add Gaussian bumps at circling gliders
   │
   └─ round to 3 dp, return
```

### Why the grid is snapped

The grid centre is moved to the nearest terrain-lattice point, up to ~550 m from
the requested coordinates, and the response reports it as `grid_lat` / `grid_lon`.
Every bound is derived from that snapped centre rather than from the request, so
the coordinates a cell is labelled with are exactly the coordinates its
elevation, slope and aspect were sampled at. Neighbouring requests also share
cached terrain points instead of each fetching their own near-duplicate grid.

**A client converting a cell index back to a location must use `grid_lat` /
`grid_lon`, not the point it asked about.**

### Grid geometry

`GRID_RADIUS = 0.05` degrees, spanned inclusively on both axes at
`GRID_RES = 0.0005` — so `2 * 0.05 / 0.0005 + 1 = 201` points per side, 40,401
cells, about 11 x 7 km at mid latitudes. The frontend keeps its own copy of the
radius in `PREDICT_RADIUS`; the two must stay equal or training features and
served features describe different ground.

### Terrain fetching

Elevation is fetched at `TERRAIN_RES = 0.01` (about 1.1 km) and bilinearly
upsampled to the fine grid. This matters for the upstream budget: OpenTopoData
allows 1000 calls/day and 1 req/s for the entire deployment, so a naive
per-cell fetch would exhaust the day in seconds. Grids are cached on disk under
`DATA_DIR/cache/`, keyed by snapped centre and resolution, so they survive
restarts.

Point elevations (used for glider AGL) are cached on a coarser ~1 km lattice.
Aircraft close to the ground get a second, precise pass at 4 decimal places
(~11 m), gated on both height (`_PRECISE_BAND_M = 150`) and ground speed
(`_PRECISE_MAX_SPEED_KMH = 40`). The gates exist to bound cost: a ridge-soaring
glider lives inside the height band for an hour, but adding the speed gate
narrows fine sampling to aircraft parked or taxiing at an airfield, which occupy
a handful of cells that then stay cached.

### OGN fusion

After inference, gliders that are **soaring type, not under tow, circling, and
climbing faster than 1 m/s** are painted onto the heatmap as Gaussian bumps
(sigma = 6 cells, about 300 m) with strength proportional to climb rate, capped
at +0.50. This is observation, not prediction: the model says where lift should
be, the fusion says where lift *is* right now.

The `is_thermal_evidence` filter is used here, in the clustering endpoint, and
in the training labeller. Anything under power — including a glider on the end
of a rope, which climbs at 2–3 m/s — would otherwise paint lift onto the map
that does not exist.

## 3. The feature matrix

`pipeline/feature_engineering.py` builds one row per grid cell. `FEATURE_COUNT`
is the authoritative width; the model file must have been fitted on exactly that
many columns.

| # | Column | Varies per cell? | Source |
|---|---|---|---|
| 0–1 | lat, lon | yes | grid geometry |
| 2 | elevation | yes | SRTM |
| 3–4 | slope, aspect | yes | gradient of the elevation grid |
| 5–6 | temp_2m, humidity | no | Open-Meteo |
| 7–8 | wind_u, wind_v | no | Open-Meteo wind speed/direction decomposed |
| 9–10 | cape, cin | no | Open-Meteo |
| 11 | solar_ghi | no | Open-Meteo shortwave radiation |
| 12 | lapse_rate | no | (T850 − T500) / 3.5 |
| 13–14 | land_use_heat, land_use_albedo | only with `ENABLE_LANDCOVER` | ESA WorldCover, else placeholder constants |
| 15–16 | hour_sin, hour_cos | no | cyclic encoding of UTC hour |
| 17–18 | day_of_year_sin/cos | no | cyclic encoding |
| 19–20 | pbl_height, soil_temp | no | Open-Meteo |
| 21 | alt_agl | yes | observer altitude minus per-cell elevation, clipped at 0 |

> This table goes stale. Read `FEATURE_COUNT` and the `np.column_stack` block in
> the source before relying on it.

Three details are load-bearing:

**Aspect convention.** Row index increases north, column index increases east.
Aspect is the compass bearing the slope *faces* — the downhill direction —
computed as `atan2(-dx, -dy)`. Negating only `dx` would mirror the result
north–south and report shaded northern slopes as sun-facing.

**Longitude scaling.** A degree of longitude shrinks as `cos(latitude)`, so the
east–west cell size is scaled before taking the gradient. Without it, slope and
aspect are wrong everywhere except the equator.

**`alt_agl`, not altitude.** The observer's AMSL altitude is converted to height
above the ground under *each cell*, which is what makes the column vary spatially
and what makes the question "is there lift at my height here" answerable. A plain
map click, carrying no altitude, defaults to terrain mean + `DEFAULT_WORKING_AGL`
(1000 m) — a fixed AMSL default cannot work, because 500 m AMSL is underground
across the Anatolian plateau and would clip `alt_agl` to zero.

### Columns 13/14: the placeholder problem

Land use was a single string per grid mapped through a constants table, so across
5,149 collected samples those two columns held exactly one distinct value each —
two of twenty-two inputs carrying no information. `data/landcover_client.py`
fixes that with real per-cell ESA WorldCover values, read from Cloud-Optimized
GeoTIFFs on public S3 via HTTP range requests against internal overviews
(one or two requests of a few hundred KB, rather than a 73 MB tile).

It is still **off by default**: measured through the evaluation harness, enabling
it moves the within-group score by −0.04 to +0.02 depending only on the CV fold
seed, so the sign is not determined by the data yet. Turning it on requires a
retrain — the columns change meaning, not width.

## 4. Inference and uncertainty

`ThermalModel.predict()` runs `_MC_SAMPLES = 50` passes. Each pass perturbs the
**meteo columns only** with per-column noise calibrated to NWP error
(±1 °C temperature, ±15 % CAPE, ±0.5 K/km lapse rate, and so on); terrain
columns are never perturbed. The returned mean is the heatmap and the standard
deviation is the uncertainty band.

Two things sit around the model output:

**The terrain multiplier.** The classifier is fitted on grid-*centre* samples
only, so it carries no spatial signal of its own — without a correction the
heatmap would be uniform across every cell. `_terrain_multiplier` supplies it
from three hand-tuned terms: south-facing aspect (0.5–1.0), slope steepness
(1.0–1.4), and moderate elevation (1.0–1.25).

**The physics fallback.** With no model loaded, probability comes from CAPE,
solar GHI and CIN directly, times the same terrain multiplier. It yields
realistic 0.32–0.88 values with real weather — which is the diagnostic worth
remembering: **a blank map means an HTTP 500, not the fallback.** The usual cause
is a feature-width mismatch between `FEATURE_COUNT` and the saved model.

Cost, measured on the deployed 2-vCPU VM: 50 passes over 40,401 rows is ~2M row
scorings per request, 0.48–0.53 s warm and ~2.4 s cold. The cold case is a
terrain or meteo cache miss, not the model.

## 5. The live feed

```
aprs.glidernet.org:10152  ──▶  parse beacon  ──▶  _live_gliders (TTL dict)
        (full feed)                │
                                   ├──▶  ring buffers  ──▶  /ws/live frames
                                   └──▶  SQLite beacons table  ──▶  /seed, priors
```

**Port choice is derived from the bounding box.** Worldwide means port 10152 —
the full feed, which accepts a filter in the login line and then ignores it —
and an empty filter string. A narrower box switches to port 14580 with an
**area** filter `a/latN/lonW/latS/lonE`. Deliberately not a radius filter: no
circle centred in a box covers its corners, so `r/` silently drops the edges.

**Aircraft typing happens at the parse boundary.** Only gliders, hang gliders and
paragliders (plus tugs, for tow detection) enter the system at all — powered and
unclassifiable traffic is rejected in the parser, so every value downstream is
one of three known types.

**Aerotow detection** looks for a glider sitting ~60 m behind a tug at matched
altitude, with loose thresholds because OGN beacons are not time-synchronised. A
towed glider climbs steadily and would otherwise be a perfect false positive.

### SQLite

Every parsed beacon is appended to `beacons(ts, id, lat, lon, alt, vario, circling, is_tow)`.
Three pragmas set at creation carry real weight:

- `journal_mode=WAL` — the rollback journal it replaces cost several fsyncs per
  beacon, and `sqlite3` holds the GIL across them.
- `synchronous=NORMAL`.
- `auto_vacuum=INCREMENTAL` — only settable on a database with no tables, which
  is exactly when it is free. Without it, deleted pages go to the freelist and
  the file never shrinks, so retention caps growth but reclaims no disk.

Two indexes: `idx_ts` for retention, and the composite `idx_id_ts` whose column
order matters — track queries filter `id = ? AND ts >= ?`, and leading with `ts`
turned each track into a 4M-row scan holding the GIL for 1.5 s.

### WebSocket

`/ws/live` sends a frame every `WS_FRAME_INTERVAL` (1 s) containing visible gliders and their new
positions. The client declares its viewport on connect and on every map move
(`Leaflet getBounds().pad(0.25)`), and the server filters both lists before
serialising — measured 157 gliders down to 4 for a 1x1 degree box.

Design choices worth knowing:

- **Bounds are optional.** Absent or malformed means "send everything", so an
  older bundle against a newer backend degrades to the previous behaviour rather
  than to a blank map. A 2 s grace period after accept lets the client declare
  before the first frame.
- **No deltas.** After filtering, a frame is ~5 gliders and ~500 bytes. Delta
  bookkeeping plus resync-on-reconnect would risk more than it saves.
- **Trimming is a serialisation-boundary concern.** `_to_wire` strips internal
  TTL bookkeeping and rounds coordinates to 5 dp. Internal consumers need the
  untrimmed dicts, so trimming must never move upstream into `fetch_ogn_gliders`.
- **Search had to move server-side.** Once the socket carries only the viewport,
  the client no longer holds a full list, so `GET /ogn/search` exists to search
  the aircraft that are *not* on screen — the only case where searching helps.

## 6. Online learning

```
every 300 s
   ├─ fetch live gliders, keep soaring types not under tow
   ├─ take up to 20 candidates; for each, build the grid it sits in
   ├─ label:  circling AND vario > 1.5 m/s  ->  1     otherwise  0
   ├─ reject: solar_ghi < 50 W/m2  (no surface heating, nothing to learn)
   │          alt above thermal base + 300 m  (wave / ridge / engine, not thermal)
   ├─ append to rolling buffer (cap 200,000 rows)
   └─ if enough samples and positives:
         fit challenger  ->  score against frozen benchmark  ->  gate  ->  promote or discard
```

### Labels

A positive is a glider **circling and climbing faster than 1.5 m/s**. A negative
is a soaring aircraft present and not thermalling — straight cruising counts, and
there is deliberately no vario pre-filter beyond the tow exclusion.

The two rejection rules both exist because of observed corruption. The overnight
one is stark: without the solar floor, one night contributed 1,640 samples and a
single positive, drowning the daytime signal. The thermal-base rule follows from
physics — a thermal cannot exist above the convective condensation level, so an
aircraft circling higher is climbing on something else, and its position says
nothing about lift over the ground below.

Co-occupants of a candidate's grid are also sampled, with a `sampled` id set
preventing the same aircraft entering twice in a cycle — a duplicate row could
otherwise land on both sides of the train/holdout split.

### The benchmark split

`evaluation/holdout.py` assigns every sample permanently to train, benchmark or
holdout by **hashing a group key**: `(day-of-year, 0.5 deg lat cell, 0.5 deg lon cell)`
— roughly "one day, one local area, one weather regime".

This replaced a positional newest-20% split, which was broken in two ways at
once. Occupants of a single grid share 16 of their 22 feature values, so a
positional split routinely put near-copies on both sides. And each fit was
compared against the score the *saved* model had earned on its own, different
holdout — two numbers measured on different samples, which is why observed
validation AUC wandered across a 0.07 band with nothing real behind it.

### The gate

A challenger is promoted only when a **paired bootstrap confidence interval**
(`paired_delta_ci`) for its improvement over the serving model, scored on
identical benchmark rows, lies entirely above zero. Pairing means shared sampling
noise cancels, which is why the old fixed AUC tolerance could be deleted rather
than retuned.

The benchmark abstains below `_MIN_BENCH_GROUPS = 30` mixed-label groups —
measured across fold seeds the score moved by ±0.05 on about 20 groups, as much
as any improvement worth shipping.

After `_MAX_CONSECUTIVE_SKIPS = 12` rejections (about an hour) the current fit is
accepted and becomes the new baseline. Without that, one lucky score could freeze
the model forever and online learning would stop adapting to the season.

Models saved before this harness existed are marked by the absence of a
`bench_micro` attribute and treated as not comparable — they were fitted on a
positional split, so their training data overlaps today's benchmark groups and
they would score unfairly well.

### The XGBoost thread trap

`fit_and_gate` promotes its challenger *object* straight to `self.model`, and a
booster keeps the `nthread` it was fitted under. Since the fit deliberately runs
single-threaded to yield cores, every prediction after a successful retrain ran
single-threaded until restart — silently, because only latency changed.
`_for_serving()` retunes at both the promotion and save points. `save_model` does
not persist `n_jobs` (a reload comes back with all cores), so the reload path was
never affected; a test pins that in case a future XGBoost version changes it.

### Seeding from history

`POST /seed` (implemented in `scripts/seed_historical.py`) rebuilds the buffer
from the beacon DB, fetching archived NWP once per (0.5° × 0.5° × hour) bucket
rather than per beacon. `days_back` defaults to the retention window and is
clamped with a warning if it exceeds it — asking for a window the DB no longer
holds would return "no rows", which is the worst way for it to fail.

## 7. Offline evaluation

`python -m evaluation.evaluate` scores feature-set variants against the same
frozen benchmark the live gate uses, with bootstrap intervals per variant and a
paired interval for each difference. Variants: `base`, `solar`, `landcover`,
`prior`, `both`.

It answers questions the live gate structurally cannot:

- Scores are comparable **across runs, variants and days**, because every
  candidate meets identical rows.
- **Baselines are scored alongside the model.** If XGBoost cannot beat the
  terrain heuristic it is already multiplied by, it is not earning its place —
  and nothing in the codebase reported that before.

Two candidate features live here and are not yet in the served matrix:

**Solar incidence** (`pipeline/solar.py`) — `cos` of the angle between the sun
vector and the terrain normal, the physically correct per-cell insolation
fraction. The matrix already carries slope, aspect, hour and day-of-year but
never combines them, so a south-facing slope at 14:00 in August and a
north-facing one look identical on the solar axis. It can be retrofitted onto
existing buffer rows because every input is already there, so it A/Bs on samples
collected before the feature existed.

**Circling prior** (`data/circling_prior.py`) — how often gliders have actually
climbed over a given patch of ground, from this project's own beacon history. It
is a **rate, not a count**, because raw counts mostly encode where gliders fly at
all; and it is **strictly lagged**, enforced in the SQL rather than by
convention, because the beacon that produced a sample's label is in the same
database and any prior that can see it is reading the answer. Sparse cells are
shrunk toward the global rate.

## 8. Frontend

Vite + React 18 + Leaflet, built to `frontend/dist` and served by FastAPI at `/`.
The static mount is registered **last** in `main.py`, because a mount at `/`
swallows every path and all API routes must already be registered to keep
matching first.

| Piece | Responsibility |
|---|---|
| `hooks/useBackend.js` | Prediction fetch, WebSocket lifecycle and reconnection, viewport declaration, accumulated glider paths |
| `components/ThermalMap.jsx` | Leaflet map, heatmap canvas overlay, glider markers, tracks, thermal clusters |
| `components/InfoPanel.jsx`, `WeatherBar.jsx` | Prediction readout, weather summary, TR/EN toggle |
| `components/SearchBar.jsx` | Server-side glider search with fly-to |
| `components/PinnedGliderCard.jsx` | Follows one aircraft, reads exact-point elevation |
| `i18n/` | `strings.js` (TR + EN dictionaries in key parity, plus wind-direction labels) and `LanguageContext.jsx` (`useLang()`, persisted to localStorage, defaults to `tr`) |

Two frontend rules worth restating:

- **No hardcoded user-facing strings.** Add the key to both dictionaries and call
  `t('key', vars)`. Transient messages are stored in state as *keys*, not
  resolved text, so a language switch updates a banner already on screen.
- **`API_BASE` resolves to `''` in a production build**, so the bundle calls its
  own origin and the WebSocket URL upgrades `https` to `wss` automatically — a
  page served over HTTPS cannot open a plain `ws://` socket.

The prediction area (`LAT/LON_MIN/MAX` in `frontend/src/config.js`) is separate
from the glider-display box (`GLIDER_*`). The first is İnönü/Eskişehir and
widening it explodes the grid; the second is worldwide.

## 9. Resource model

Every unusual choice below traces to the deployment target: 2 vCPU, 896 MB
usable RAM, burstable CPU credits.

| Constraint | Response |
|---|---|
| Two concurrent predictions each saturate the cores and hold ~30 MB | `PREDICT_CONCURRENCY = 1` — a semaphore queues them. Everyone waits their turn; nobody is rejected and the box does not swap |
| A background fit could starve serving | `XGB_FIT_THREADS = 1`, `XGB_PREDICT_THREADS` = all cores |
| A leak or runaway `/seed` could take the box, including sshd | `mem_limit: 700m` — a blast radius, not a target (steady state ~135–190 MB); the restart policy brings the container back |
| A flood of cheap requests | uvicorn `--limit-concurrency 64` as a backstop |
| Beacon DB grows ~1.4 GB/day | `BEACON_RETENTION_DAYS = 2` |
| Disk could still fill | `_disk_guard()` shortens retention below 3 GB free, floored at 1 day, capped at 2 rounds |
| A 1.62 MB `/predict` payload | probabilities rounded to 3 dp — nothing finer can reach a pixel |
| Burstable CPU is scarcer than egress | Caddy `encode zstd gzip` at default (speed-tuned) levels; zstd is actually *larger* than gzip here, and that is the right trade |

**The 17 Aug 2026 outage is the clearest illustration.** Retention was 7 days,
the disk was only 40 % full, and the site went down anyway: a 4.26 GB database
against the ~165 MB of page cache left on the box meant nearly every SQLite page
hit was a physical read. The host sat at 96.6 % iowait, uvicorn wedged in D
state, and Caddy accepted connections it could never get an answer for — so the
browser saw a timeout rather than an error. Retention is the only knob that
shrinks the working set, and it is now sized to what fits in RAM, not to what
fits on disk.
