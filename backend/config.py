from dotenv import load_dotenv
from pathlib import Path
import os

load_dotenv()

# Writable state root. Defaults to the backend package dir so local runs behave
# the same from any CWD; in a container point this at a mounted volume so the
# trained model, training buffer and beacon history survive redeploys.
DATA_DIR = Path(os.getenv("THERMALSENSE_DATA_DIR", Path(__file__).parent)).resolve()

OPEN_METEO_BASE      = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_HIST_BASE = "https://historical-forecast-api.open-meteo.com/v1/forecast"
SRTM_BASE         = "https://api.opentopodata.org/v1/srtm30m"

# Where ground elevation comes from.
#
# "copernicus" reads Copernicus GLO-30 Cloud-Optimized GeoTIFFs straight from
# public S3 with range requests — no API, no quota, commercial use allowed.
# "opentopodata" is the original free API: 1,000 requests a day, which a
# training node spent in its first 40 minutes, after which collection stops.
#
# Measured on 36 grids against the cached SRTM values, after the upsample the
# model actually sees: elevation differs by 4.4 m on average, slope by 0.26°
# (correlation 0.92), aspect by 3.2° — the features barely move.  Points
# Copernicus cannot answer (a tile not published, a read failure) fall back to
# OpenTopoData, so the two sources are cached separately and never mixed.
TERRAIN_SOURCE    = os.getenv("TERRAIN_SOURCE", "copernicus").strip().lower()

# Candidate grids one retrain cycle may sample, and how long it may spend doing
# it.  The old fixed 20 was chosen to spare the terrain API's 1,000/day — which
# Copernicus removed — and it held collection to ~200 samples a day, weeks away
# from the 30 mixed-label groups the quality gate needs.
#
# A count alone is the wrong control now.  What actually binds is Open-Meteo's
# 10,000 forecast calls a day and the cycle's own deadline (UPDATE_INTERVAL - 30),
# so the loop stops on whichever comes first: the count, the time budget, or the
# meteo quota.  Each grid also builds a 201x201x22 feature matrix (~7 MB), so an
# unbounded loop is a memory question on a 1 GiB box, not only an API one.
MAX_SAMPLE_GRIDS  = int(os.getenv("MAX_SAMPLE_GRIDS", "60"))
SAMPLE_TIME_BUDGET_S = float(os.getenv("SAMPLE_TIME_BUDGET_S", "150"))
COPERNICUS_DEM_BASE = os.getenv(
    "COPERNICUS_DEM_BASE", "https://copernicus-dem-30m.s3.amazonaws.com")
# ESA WorldCover 10 m, public S3. Cloud-Optimized GeoTIFFs, so the reader takes
# HTTP range requests over internal overviews instead of the 73 MB full tile.
# No key and no rate limit, unlike OpenTopoData.
WORLDCOVER_BASE   = os.getenv(
    "WORLDCOVER_BASE",
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map",
)

# Feed real per-cell land cover into feature columns 13/14 instead of the two
# placeholder constants.  Off by default because the evidence does not yet
# support it: measured through evaluation/, the change moves the within-group
# score by -0.04 to +0.02 depending only on the CV fold seed, i.e. the sign is
# not determined by the data.  The plumbing is complete and tested, so this
# becomes a one-line change once the buffer is large enough to decide.
# Flipping it on requires a retrain — the columns change meaning, not width.
ENABLE_LANDCOVER  = os.getenv("ENABLE_LANDCOVER", "0") not in ("0", "", "false", "False")
# OGN gliders come from the APRS TCP stream (aprs.glidernet.org), not HTTP

# Bounding box for live traffic — the whole world by default.
#
# Overridable via env for anyone who wants to run a regional instance; the APRS
# filter and the client-side checks are both derived from these four numbers, so
# narrowing them here narrows the whole pipeline.  Note these are the *glider
# feed* bounds — the prediction grid is a separate, much smaller area chosen per
# request (see GRID_RADIUS), and is unaffected by widening this box.
def _bound(name: str, default: float) -> float:
    return float(os.getenv(name, default))

LAT_MIN, LAT_MAX  = _bound("OGN_LAT_MIN", -90.0), _bound("OGN_LAT_MAX", 90.0)
LON_MIN, LON_MAX  = _bound("OGN_LON_MIN", -180.0), _bound("OGN_LON_MAX", 180.0)

WORLDWIDE         = (LAT_MIN, LAT_MAX, LON_MIN, LON_MAX) == (-90.0, 90.0, -180.0, 180.0)

# Port 10152 is OGN's *full feed*: it accepts a filter in the login line and then
# ignores it.  That made the old r/53.5/15.0/2500 filter decorative — every beacon
# on the planet arrived anyway, to be discarded client-side.  14580 is the port
# that honours filters.  Worldwide wants the full feed, so pick the port to match
# rather than asking 14580 for an a/90/-180/-90/180 filter that excludes nothing.
OGN_APRS_PORT     = 10152 if WORLDWIDE else 14580

# Area filter (a/latN/lonW/latS/lonE) derived from the box, so the two can't drift.
# Deliberately not a radius filter: no circle centred in a box covers its corners,
# so r/ silently drops the edges.  Empty when worldwide — the full feed needs none.
OGN_APRS_FILTER   = "" if WORLDWIDE else f"a/{LAT_MAX}/{LON_MIN}/{LAT_MIN}/{LON_MAX}"

GRID_RES          = 0.0005        # degrees per cell (~50 m)
# Coarse terrain fetch resolution (bilinearly upsampled to GRID_RES).  Doubles as
# the lattice that grid centres snap to, so every terrain sample lands on an exact
# multiple of it and neighbouring grids share cached points.
TERRAIN_RES       = 0.01
# Half-width of a prediction/training grid, spanned inclusively on both axes:
# 2*GRID_RADIUS/GRID_RES + 1 = 201 points per side at exactly GRID_RES apart.
# fetch_elevation_grid takes its default radius from here, but the frontend's
# PREDICT_RADIUS is a separate copy that must be kept equal — training features
# and served features have to describe the same patch of ground.
GRID_RADIUS       = 0.05          # degrees (~11 x 7 km at UK latitudes)
UPDATE_INTERVAL   = 300           # seconds between OGN polls
# Seconds between /ws/live frames.  Measured on the live feed (19 Aug 2026,
# 1548 aircraft over 10 min): the feed as a whole delivers a beacon every 1.0 s
# median, but per aircraft the median gap is 3.9 s and the slowest tenth report
# only every 45 s.  So this interval is not what makes a marker sit still --
# that is the aircraft's own transmit rate and its receiver coverage, which no
# setting here can change.  What it does fix is the fastest quarter of the fleet:
# their per-aircraft median gap is ~0.9 s, which a 2 s frame plainly undersamples.
# The cost is linear in viewers x gliders on screen, and the default view is now
# Europe-wide, so treat this as the knob to raise first if the VM gets busy.
WS_FRAME_INTERVAL = float(os.getenv("WS_FRAME_INTERVAL", "1.0"))
MODEL_PATH        = str(DATA_DIR / "models" / "thermal_xgb.json")
BUFFER_PATH       = str(DATA_DIR / "models" / "training_buffer.npz")
DB_PATH           = DATA_DIR / "data" / "ogn_history.db"

# Cold archive: every completed day of the beacon feed, as date-partitioned
# Parquet, kept forever.  DB_PATH stays a two-day hot cache — that limit is a
# RAM and iowait constraint, not a data policy, and conflating the two is why
# the project currently has no climatology and no seasonal coverage.  Measured
# on this feed: ~10 bytes/row here against ~244 in SQLite, so three months of
# the worldwide feed is ~7 GB rather than ~169 GB.  Mount it on a volume that
# is not the container's writable layer.
ARCHIVE_DIR       = Path(os.getenv("THERMALSENSE_ARCHIVE_DIR",
                                   DATA_DIR / "archive" / "beacons")).resolve()

# How long beacons are kept before the retention job deletes them.  Without a cap
# the DB grows without bound.
#
# Measured on the live worldwide feed (16 Aug 2026): 23.2M rows in 4.1 GB over
# 3 days — ~7.7M rows and ~1.4 GB per day.
#
# Lowered 7 -> 2 after the 17 Aug 2026 outage, which proved the binding
# constraint is RAM, not disk.  The disk was only 40% full; what took the site
# down was the 4.26 GB DB against the ~165 MB of page cache left on a 896 MB
# box, so nearly every SQLite page hit was a physical read.  The host sat at
# 96.6% iowait with io pressure full=90%, uvicorn wedged in D state, and Caddy
# accepted connections it could never get an upstream answer for — the browser
# saw a timeout, not an error.  Retention is the only knob that shrinks the
# working set, so it is sized to what fits in cache now, not to what fits on
# disk.
#
# The floor is set by readers, not by disk: seed_from_history() must not be
# asked for a window the DB no longer holds, or it returns "no rows" rather
# than failing — the worst way to break.  Its default now derives from this
# value and it clamps + warns instead of silently seeding nothing, so the two
# can no longer drift apart.  Raise this only with the RAM to match.
#
# Lowered from 2 to 1 after the 18 Sep 2026 outage.  The binding constraint has
# never been disk, it is that anything scanning this file pulls it through a
# page cache the box does not have: the nightly seed's full scan of a 2.1 GB DB
# evicted sshd, systemd-resolved and the Azure guest agent to swap, and the host
# thrashed for 36 h with the container still holding its 700 MB reservation.
# Halving the file halves that scan.  Nothing is lost by it — _archive_job
# writes each completed day to Parquet before _purge_job may delete it, and
# _nightly_seed_job reads the archive, so day two now lives in the archive
# rather than in the hot file.
BEACON_RETENTION_DAYS = float(os.getenv("BEACON_RETENTION_DAYS", "1"))

# Free-space floor for the emergency purge (see _disk_guard in main.py).  Losing
# beacon history is recoverable; filling the disk takes the whole service down
# with it — SQLite writes start failing, the container cannot log, and the box
# needs a human on SSH to rescue it.  History is the cheapest thing here to give
# up, so it is what gets given up.
MIN_FREE_DISK_GB   = float(os.getenv("MIN_FREE_DISK_GB", "3"))
# However tight the disk gets, never purge below this.
#
# It used to be one day, on the reasoning that a shorter window leaves nothing
# worth training on.  The archive changed what the hot DB is for: completed days
# live in Parquet forever and _nightly_seed_job trains from them, so this file
# is now a buffer for the day in progress, not the record.  With
# BEACON_RETENTION_DAYS itself at 1 a floor of 1 also left the disk guard no
# room to move at all — it would log that it had shortened retention and delete
# nothing.  Half a day still covers the live loop's own window several times
# over, and this is the emergency path regardless.
MIN_RETENTION_DAYS = float(os.getenv("MIN_RETENTION_DAYS", "0.5"))

# --- CPU budget ---------------------------------------------------------------
# Sized for the 2-vCPU deployment target.  predict() and the retrain fit both run
# in worker threads now, so nothing stops two XGBoost OpenMP pools from each
# claiming every core and spending the difference on contention.
#
# The fit is a background job on a 300 s cycle whose latency nobody observes, so
# it yields: one thread, leaving a core for whatever request lands mid-cycle.
#
# Predictions are serialised rather than run in parallel.  Each is ~2M rows of
# already core-bound work, so two at once take twice as long apiece and finish no
# sooner together — and asyncio.to_thread would otherwise hand out up to
# min(32, cpu_count + 4) threads, each spawning its own OpenMP pool on 2 cores.
XGB_FIT_THREADS     = int(os.getenv("XGB_FIT_THREADS", "1"))
XGB_PREDICT_THREADS = int(os.getenv("XGB_PREDICT_THREADS", "0")) or (os.cpu_count() or 2)
PREDICT_CONCURRENCY = int(os.getenv("PREDICT_CONCURRENCY", "1"))

# --- Deployment ---------------------------------------------------------------
# Comma-separated allowed origins. "*" is fine when the API and UI share an
# origin (single-container deploy); set it explicitly for a split deploy.
CORS_ORIGINS      = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

# When set, POST /train and POST /seed require a matching X-Admin-Token header.
# Unset (the default) leaves them open — fine locally, not for a public URL.
ADMIN_TOKEN       = os.getenv("ADMIN_TOKEN", "")

# Directory holding the built frontend (Vite `dist`). Served at / when present.
_static_default   = Path(__file__).parent.parent / "frontend" / "dist"
STATIC_DIR        = Path(os.getenv("STATIC_DIR", _static_default))

# Open-Meteo calls the nightly seed may spend, out of the 10,000/day shared with
# the live sampler.  Deliberately below the quota: the seed runs at 02:00 UTC and
# must leave the morning's collection able to work.  Buckets past the budget are
# simply not fetched — the next run picks them up, and the historical cache means
# nothing already paid for is paid for twice.
SEED_METEO_BUDGET = int(os.getenv("SEED_METEO_BUDGET", "6000"))

# What the nightly seed pulls from one day of beacons.  Yields roughly a third
# as many training rows (measured: 10,000 beacons -> 3,100 rows), the rest being
# dark hours, ground traffic and the ambiguous vario band.
#
# Lowered from 20,000 after the 18 Sep 2026 outage.  The number was chosen for
# how many rows were wanted, with no thought for what producing them costs: the
# seed builds one 201x201x22 feature matrix (~7 MB) per beacon to keep a single
# row of it, so 20,000 beacons meant 20,000 such matrices back to back, against
# the live sampler's MAX_SAMPLE_GRIDS = 60 per cycle.  2,000 is the same order
# as a day of live sampling rather than three orders above it.
NIGHTLY_SEED_LIMIT = int(os.getenv("NIGHTLY_SEED_LIMIT", "2000"))

# Wall-clock ceiling for one seed run.  The live sampler has had
# SAMPLE_TIME_BUDGET_S since the quota work; the seed had no deadline at all,
# which is the asymmetry that took the box down on 18 Sep 2026 — it stopped only
# at SEED_METEO_BUDGET, and 1,430 buckets never came near 6,000 calls.  A run
# that runs out of time is not a failure: buckets it did not reach are picked up
# by the next run, and the historical meteo cache is permanent, so nothing
# already paid for is paid for twice.
#
# 30 min at 02:00 UTC leaves the morning's live sampling untouched.
SEED_TIME_BUDGET_S = float(os.getenv("SEED_TIME_BUDGET_S", "1800"))
