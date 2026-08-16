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
MODEL_PATH        = str(DATA_DIR / "models" / "thermal_xgb.json")
BUFFER_PATH       = str(DATA_DIR / "models" / "training_buffer.npz")
DB_PATH           = DATA_DIR / "data" / "ogn_history.db"

# How long beacons are kept before the retention job deletes them.  Without a cap
# the DB grows without bound and fills the disk in weeks.
#
# Measured on the live worldwide feed (16 Aug 2026): 23.2M rows in 4.1 GB over
# 3 days — ~7.7M rows and ~1.4 GB per day, not the ~6M/800 MB this comment used
# to estimate.  At 7 days that is a steady state near 9.6 GB, so the file wants
# roughly 10 GB of headroom rather than the 5-6 GB previously assumed.
#
# The floor is set by readers, not by disk: seed_from_history() defaults to
# days_back=3, so anything below that silently starves /seed of training data —
# it would return "no rows" rather than fail, which is the worst way to break.
# 7 days keeps the default seed working with margin.  Raise it only with the disk
# headroom to match, and never set it below the largest days_back you intend to
# pass to /seed.
BEACON_RETENTION_DAYS = float(os.getenv("BEACON_RETENTION_DAYS", "7"))

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
