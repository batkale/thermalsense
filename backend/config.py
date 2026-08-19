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
BEACON_RETENTION_DAYS = float(os.getenv("BEACON_RETENTION_DAYS", "2"))

# Free-space floor for the emergency purge (see _disk_guard in main.py).  Losing
# beacon history is recoverable; filling the disk takes the whole service down
# with it — SQLite writes start failing, the container cannot log, and the box
# needs a human on SSH to rescue it.  History is the cheapest thing here to give
# up, so it is what gets given up.
MIN_FREE_DISK_GB   = float(os.getenv("MIN_FREE_DISK_GB", "3"))
# However tight the disk gets, never purge below this: past it the DB holds
# nothing worth training on and is being churned for no gain.
MIN_RETENTION_DAYS = float(os.getenv("MIN_RETENTION_DAYS", "1"))

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
