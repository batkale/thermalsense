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
TERRAIN_RES       = 0.01          # coarse terrain fetch resolution (upsampled to GRID_RES)
# Half-width of a prediction/training grid. Must match fetch_elevation_grid's
# default radius and the frontend's PREDICT_RADIUS — training features and
# served features have to describe the same patch of ground.
GRID_RADIUS       = 0.05          # degrees (~11 x 7 km at UK latitudes)
UPDATE_INTERVAL   = 300           # seconds between OGN polls
MODEL_PATH        = str(DATA_DIR / "models" / "thermal_xgb.json")
BUFFER_PATH       = str(DATA_DIR / "models" / "training_buffer.npz")
DB_PATH           = DATA_DIR / "data" / "ogn_history.db"

# How long beacons are kept before the retention job deletes them.  The feed
# writes ~6M rows/day worldwide (~800 MB), so without a cap the DB grows without
# bound and fills the disk in weeks.
#
# The floor is set by readers, not by disk: seed_from_history() defaults to
# days_back=3, so anything below that silently starves /seed of training data —
# it would return "no rows" rather than fail, which is the worst way to break.
# 7 days keeps the default seed working with margin and still bounds the file at
# roughly 5-6 GB.  Raise it only with the disk headroom to match, and never set
# it below the largest days_back you intend to pass to /seed.
BEACON_RETENTION_DAYS = float(os.getenv("BEACON_RETENTION_DAYS", "7"))

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
