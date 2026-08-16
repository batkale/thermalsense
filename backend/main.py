from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import secrets
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio, json, logging
import numpy as np
from datetime import datetime, timezone, timedelta
from data.ogn_client      import fetch_ogn_gliders, start_ogn_stream, fetch_glider_track, drain_beacon_buffers, is_thermal_evidence, flush_beacons, purge_old_beacons
from data import meteo_client, terrain_client
from data.meteo_client    import fetch_meteo_features
from data.terrain_client  import fetch_elevation_grid, fetch_elevation_point, fetch_elevation_batch, snap_grid_centre
from pipeline.feature_engineering import build_feature_matrix
from models.thermal_model import ThermalModel
from config import UPDATE_INTERVAL, GRID_RES, GRID_RADIUS, CORS_ORIGINS, ADMIN_TOKEN, STATIC_DIR, DATA_DIR, BEACON_RETENTION_DAYS
from pathlib import Path
import shutil

# Without this the app's own loggers inherit root's WARNING level, so every
# progress line from the long-running seed/retrain jobs is silently dropped and
# a multi-minute rebuild looks indistinguishable from a hang.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

SEED_TIMEOUT = 4 * 3600   # seconds; see _seed_job for why this is generous

log = logging.getLogger(__name__)
model = ThermalModel()
scheduler = AsyncIOScheduler()
_training_lock = asyncio.Lock()

async def _retrain_job():
    """Scheduled job: fetch fresh OGN data and retrain the model."""
    # locked() is only a hint — acquire without blocking so a seed holding the
    # lock for hours makes this skip the cycle rather than queue up behind it.
    try:
        await asyncio.wait_for(_training_lock.acquire(), timeout=0.1)
    except asyncio.TimeoutError:
        log.info("[train] already running, skipping")
        return
    try:
        log.info("[train] starting scheduled retrain")
        # Hard cap: must finish before the next scheduled fire
        await asyncio.wait_for(model.retrain(), timeout=UPDATE_INTERVAL - 30)
    except asyncio.TimeoutError:
        log.warning("[train] retrain timed out — will retry next cycle")
    except Exception as e:
        log.error(f"[train] failed: {e}")
    finally:
        _training_lock.release()

def _seed_data_dir() -> None:
    """Copy the image's bundled model + buffer into DATA_DIR on first run.

    In the container DATA_DIR is a mounted volume, which starts empty and
    shadows whatever the image baked in at that path. Without this the app
    silently drops to physics-only predictions on every fresh deploy even
    though a trained model shipped in the image. Existing files are never
    overwritten, so a volume that has already learned something is left alone.
    """
    bundled = Path(__file__).parent / "models"
    target  = DATA_DIR / "models"
    if bundled.resolve() == target.resolve():
        return  # running straight from the source tree — nothing to copy
    target.mkdir(parents=True, exist_ok=True)
    for name in ("thermal_xgb.json", "training_buffer.npz"):
        src, dst = bundled / name, target / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
            log.info(f"[init] seeded {name} -> {dst}")


async def _purge_job() -> None:
    """Drop beacons past the retention window so the DB stops growing forever."""
    try:
        # to_thread, not inline: the delete is a multi-million-row write and
        # running it on the event loop would stall every request behind it.
        deleted = await asyncio.to_thread(purge_old_beacons)
        if deleted:
            log.info(f"[purge] removed {deleted:,} beacons older than "
                     f"{BEACON_RETENTION_DAYS} days")
    except Exception as exc:
        log.warning(f"[purge] failed: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_ogn_stream()
    _seed_data_dir()
    terrain_client.load_caches()
    await model.load()
    scheduler.add_job(
        _retrain_job, "interval", seconds=UPDATE_INTERVAL, id="retrain",
        misfire_grace_time=UPDATE_INTERVAL // 2,  # tolerate up to 150s lateness
        coalesce=True,                             # merge stacked missed runs into one
    )
    # Hourly, not daily: an hourly pass deletes at most an hour of feed, whereas
    # a daily one wakes up to several million rows at once.  Nothing is due until
    # the DB is older than the retention window, so early runs are no-ops.
    scheduler.add_job(
        _purge_job, "interval", hours=1, id="purge",
        misfire_grace_time=1800,
        coalesce=True,
    )
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)
    terrain_client.flush_point_cache(force=True)   # don't lose the last partial batch
    flush_beacons()                                # same, for queued OGN beacons
    await meteo_client.aclose()
    await terrain_client.aclose()

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """Guard the endpoints that kick off expensive background work.

    No-op when ADMIN_TOKEN is unset (local dev). Uses a constant-time compare so
    the token can't be recovered by timing the responses.
    """
    if not ADMIN_TOKEN:
        return
    if not x_admin_token or not secrets.compare_digest(x_admin_token, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="invalid or missing X-Admin-Token")


@app.get("/healthz")
async def healthz():
    """Liveness probe for the host's health checks."""
    return {"status": "ok", "model_loaded": model.is_loaded}

def _apply_ogn_fusion(
    heatmap: list[float],
    gliders: list[dict],
    grid_lat: float, grid_lon: float, radius: float,
    rows: int, cols: int,
) -> list[float]:
    """Blend live confirmed thermals (circling + vario > 1 m/s) into the ML heatmap.

    grid_lat/grid_lon are the grid's own centre (see snap_grid_centre), not the
    point the caller asked about — a glider is placed relative to the cells that
    exist, not to where the request happened to land.
    """
    # Soaring aircraft only, and not on aerotow: anything under power can circle
    # and climb without a thermal underneath it — including a glider on the end
    # of a rope — which would paint lift onto the map that isn't there.
    circling = [
        g for g in gliders
        if is_thermal_evidence(g) and g.get("circling") and g.get("vario", 0) > 1.0
    ]
    if not circling:
        return heatmap
    arr    = np.array(heatmap, dtype=float).reshape(rows, cols)
    sigma  = 6.0                                    # grid cells ≈ 300 m radius
    window = int(3 * sigma)
    for g in circling:
        # GRID_RES is the exact spacing of the fine lattice, so this is a direct
        # index rather than an approximation.
        ri = (g["lat"] - (grid_lat - radius)) / GRID_RES
        ci = (g["lon"] - (grid_lon - radius)) / GRID_RES
        if not (0 <= ri < rows and 0 <= ci < cols):
            continue
        strength = min(g["vario"] / 5.0, 1.0) * 0.50
        r0, r1 = max(0, int(ri) - window), min(rows, int(ri) + window + 1)
        c0, c1 = max(0, int(ci) - window), min(cols, int(ci) + window + 1)
        ys = np.arange(r0, r1) - ri
        xs = np.arange(c0, c1) - ci
        yy, xx = np.meshgrid(ys, xs, indexing="ij")
        arr[r0:r1, c0:c1] += np.exp(-(yy**2 + xx**2) / (2 * sigma**2)) * strength
    return np.clip(arr, 0, 1).ravel().tolist()


# Height above ground assumed when the caller doesn't supply an altitude — a
# plain map click, as opposed to clicking a specific glider. A fixed AMSL default
# cannot work: 500 m AMSL is underground across the Anatolian plateau, which
# clips alt_agl to 0 and asks the model about lift at ground level. Training rows
# carry real glider altitudes (typically 500-2000 m AGL), so this keeps served
# features inside the distribution the model was fitted on.
DEFAULT_WORKING_AGL = 1000.0


@app.get("/predict")
async def predict(lat: float, lon: float, alt: int | None = None, forecast_h: int = 0):
    """
    Predicted thermal strength over a grid centred on (lat, lon).

    alt is the pilot's altitude in metres above sea level; the heatmap answers
    "is there lift here at that height", since a thermal that tops out at 900 m
    is no use to someone at 1500 m.  It is converted to height above ground per
    cell, matching how training samples record the glider's altitude.

    The grid is centred on the terrain lattice point nearest (lat, lon), up to
    ~550 m away, and grid_lat/grid_lon in the response say where that is.  Every
    bound here is derived from it rather than from the request point, so the
    coordinates a cell is labelled with are the coordinates its elevation, slope
    and aspect were sampled at.
    """
    try:
        grid_lat, grid_lon = snap_grid_centre(lat, lon)
        meteo, terrain = await asyncio.gather(
            fetch_meteo_features(lat, lon, forecast_h),
            fetch_elevation_grid(lat, lon),
        )
        radius   = GRID_RADIUS  # matches fetch_elevation_grid default
        alt_amsl = (
            float(alt) if alt is not None
            else float(terrain.mean()) + DEFAULT_WORKING_AGL
        )
        features = build_feature_matrix(
            meteo, terrain,
            dt=datetime.now(timezone.utc) + timedelta(hours=forecast_h),
            lat_bounds=(grid_lat - radius, grid_lat + radius),
            lon_bounds=(grid_lon - radius, grid_lon + radius),
            alt_amsl=alt_amsl,
        )
        grid_rows, grid_cols = terrain.shape
        heatmap, heatmap_std = model.predict(features)
        live_gliders = await fetch_ogn_gliders()
        heatmap = _apply_ogn_fusion(heatmap, live_gliders, grid_lat, grid_lon,
                                    radius, grid_rows, grid_cols)
    except Exception as exc:
        log.error(f"[predict] failed for ({lat},{lon}): {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    return {
        "heatmap":      heatmap,
        "heatmap_std":  heatmap_std,
        "rows":         grid_rows,
        "cols":         grid_cols,
        "grid_lat":     grid_lat,
        "grid_lon":     grid_lon,
        "thermal_base": meteo["cape_base"],
        "cape":         meteo["cape"],
        "weather": {
            "temp_2m":     round(meteo["temp_2m"], 1),
            "humidity":    round(meteo["humidity"] * 100),
            "wind_speed":  round(meteo["wind_speed"], 1),
            "wind_dir":    round(meteo["wind_dir"]),
            "cape":        round(meteo["cape"]),
            "cin":         round(meteo["cin"]),
            "solar_ghi":   round(meteo["solar_ghi"]),
            "lapse_rate":  round(meteo["lapse_rate"], 1),
            "thermal_base": meteo["cape_base"],
            "pbl_height":  round(meteo["pbl_height"]),
            "soil_temp":   round(meteo["soil_temp"], 1),
        },
    }

async def _add_agl(gliders: list[dict]) -> list[dict]:
    """Attach agl (metres above ground) to each glider dict in-place."""
    if not gliders:
        return gliders
    pairs = [(g["lat"], g["lon"]) for g in gliders]
    try:
        # Two chunked requests + the 1 s rate-limit gap between them need ~5 s
        # on a cold cache; warm calls return immediately.  Work completed before
        # a timeout still lands in the elevation cache for the next call.
        elevs = await asyncio.wait_for(fetch_elevation_batch(pairs), timeout=8.0)
    except asyncio.TimeoutError:
        elevs = {}
    for g, pair in zip(gliders, pairs):
        elev = elevs.get(pair)
        g["agl"] = (g["alt"] - elev) if elev is not None else None
    return gliders


def _cluster_circling(gliders: list[dict], radius: float = 0.012) -> list[dict]:
    """Greedy proximity clustering of circling gliders (~1.3 km radius at UK latitudes)."""
    # Only unaided soaring aircraft confirm a thermal — see _apply_ogn_fusion.
    candidates = [
        g for g in gliders
        if is_thermal_evidence(g) and g.get("circling") and g.get("vario", 0) > 0.5
    ]
    unclustered = list(candidates)
    clusters = []
    while unclustered:
        seed = unclustered.pop(0)
        members = [seed]
        remaining = []
        for g in unclustered:
            if abs(g["lat"] - seed["lat"]) < radius and abs(g["lon"] - seed["lon"]) < radius:
                members.append(g)
            else:
                remaining.append(g)
        unclustered = remaining
        n = len(members)
        clusters.append({
            "lat":       round(sum(m["lat"]   for m in members) / n, 5),
            "lon":       round(sum(m["lon"]   for m in members) / n, 5),
            "avg_vario": round(sum(m["vario"] for m in members) / n, 2),
            "count":     n,
            "est_alt_m": round(sum(m["alt"]   for m in members) / n),
        })
    return clusters


@app.get("/thermals/active")
async def active_thermals():
    """Return clusters of actively circling gliders as confirmed thermal locations."""
    gliders = await fetch_ogn_gliders()
    return {"thermals": _cluster_circling(gliders)}


@app.get("/elevation")
async def point_elevation(lat: float, lon: float):
    """Ground elevation at a single point (SRTM 30 m).  Returns null if API unavailable."""
    elev = await fetch_elevation_point(lat, lon)
    return {"elevation": elev}


@app.get("/ogn/track/{glider_id}")
async def glider_track(glider_id: str):
    """Return the last 8 hours of positions for a single glider."""
    return {"track": fetch_glider_track(glider_id)}


@app.get("/ogn/live")
async def ogn_live():
    gliders = await _add_agl(await fetch_ogn_gliders())
    return {"gliders": _to_wire(gliders)}

@app.post("/train", dependencies=[Depends(_require_admin)])
async def trigger_train(background_tasks: BackgroundTasks):
    """Manually trigger model retraining in the background."""
    if _training_lock.locked():
        return {"status": "already_running"}
    background_tasks.add_task(_retrain_job)
    return {"status": "started"}


async def _seed_job(days_back: int, limit: int, reset: bool = False) -> None:
    async with _training_lock:
        log.info(f"[seed] starting — days_back={days_back}, limit={limit}, reset={reset}")
        try:
            # Terrain is the bottleneck: ~1 unique 0.1-degree cell per beacon at the
            # SRTM API's 1 req/s cap, so a few thousand beacons runs well past an
            # hour.  On timeout the whole run is discarded unsaved, so keep this
            # generous — it is a background job with a lock, not a request.
            result = await asyncio.wait_for(
                model.seed_from_history(days_back=days_back, limit=limit, reset=reset),
                timeout=SEED_TIMEOUT,
            )
        except asyncio.TimeoutError:
            log.warning(f"[seed] timed out after {SEED_TIMEOUT}s — nothing saved")
            return
        except Exception as exc:
            log.error(f"[seed] failed: {exc}")
            return

        # seed_from_history swallows per-beacon errors, so a run can "succeed"
        # having added nothing at all.  Surface that instead of fitting on nothing.
        if not result or result.get("added", 0) == 0:
            log.error(f"[seed] added no samples — not fitting. result={result}")
            return
        log.info(f"[seed] {result}")

        # Seeding only fills the buffer; fit it so /seed leaves a usable model.
        # Call retrain() directly — _retrain_job would deadlock on the lock held here.
        try:
            log.info("[seed] buffer filled — fitting model")
            await model.retrain()
        except Exception as exc:
            log.error(f"[seed] post-seed retrain failed: {exc}")


@app.post("/seed", dependencies=[Depends(_require_admin)])
async def seed_from_history(
    background_tasks: BackgroundTasks,
    days_back: int = 3,
    limit: int = 5000,
    reset: bool = False,
):
    """
    Backfill the training buffer from historical beacon DB records.

    Fetches archived NWP meteo once per (0.5° × 0.5° × hour) bucket and
    builds feature vectors for every sampled beacon. Runs in the background;
    check server logs for progress. Triggers a retrain automatically when done
    if the buffer crosses the minimum sample threshold.

    - days_back: how many days of history to scan (default 3)
    - limit: max beacon rows to process (default 5000; sampled uniformly across the window)
    - reset: discard the existing buffer first so the rebuild replaces it rather
      than appending (use after a feature-pipeline correction)
    """
    if _training_lock.locked():
        return {"status": "already_running"}
    background_tasks.add_task(_seed_job, days_back, limit, reset)
    return {"status": "started", "days_back": days_back, "limit": limit, "reset": reset}

# Fields the frontend actually reads. `seen_at` is deliberately excluded: it is
# internal TTL bookkeeping, was being sent 43,200x/day per viewer, and leaked
# server state. Coordinates are rounded to 5 dp (~1 m) — far beyond what a
# 50 m grid or a map pixel can resolve, and it compresses much better.
# ac_type/ac_type_name let the client distinguish a sailplane from a paraglider
# instead of labelling everything "glider".  Unclassifiable traffic is no longer
# among the possibilities: DISPLAY_AC_TYPES rejects it at the parse boundary, so
# every value on the wire is one of glider / hang glider / paraglider.
_WIRE_FIELDS = ("id", "lat", "lon", "alt", "vario", "heading",
                "speed_kmh", "circling", "is_tow", "under_tow", "agl",
                "ac_type", "ac_type_name")
_COORD_DP = 5


def _to_wire(gliders: list[dict]) -> list[dict]:
    """Trim gliders to the fields the client uses, at sane precision."""
    out = []
    for g in gliders:
        w = {k: g[k] for k in _WIRE_FIELDS if k in g}
        if "lat" in w:
            w["lat"] = round(w["lat"], _COORD_DP)
        if "lon" in w:
            w["lon"] = round(w["lon"], _COORD_DP)
        out.append(w)
    return out


def _positions_to_wire(new_pos: dict[str, list[dict]]) -> dict[str, list[dict]]:
    return {
        gid: [
            {"lat": round(p["lat"], _COORD_DP),
             "lon": round(p["lon"], _COORD_DP),
             "alt": p["alt"]}
            for p in positions
        ]
        for gid, positions in new_pos.items()
    }


@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            try:
                gliders    = await fetch_ogn_gliders()
                new_pos    = drain_beacon_buffers()   # all positions since last frame
            except Exception as e:
                log.warning(f"[ws] glider fetch failed: {e}")
                gliders = []
                new_pos = {}
            await ws.send_text(json.dumps({
                "gliders":       _to_wire(gliders),
                "new_positions": _positions_to_wire(new_pos),
            }))
            await asyncio.sleep(2)
    except (WebSocketDisconnect, RuntimeError):
        pass  # client navigated away — normal teardown


# --- Static frontend ----------------------------------------------------------
# Registered last on purpose: a mount at "/" swallows every path, so all API
# routes above must already be registered to keep matching first.
if STATIC_DIR.is_dir() and (STATIC_DIR / "index.html").exists():
    _index = STATIC_DIR / "index.html"

    # Hashed Vite bundles are immutable — let the browser cache them hard.
    # Guarded: StaticFiles raises at import time if the directory is missing, which
    # would take down the API too rather than degrading to the SPA fallback.
    if (STATIC_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
    else:
        log.warning(f"[static] no assets/ under {STATIC_DIR} — serving via SPA fallback only")

    @app.get("/", include_in_schema=False)
    async def _spa_root():
        return FileResponse(_index)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        """Serve real files when they exist, else fall back to index.html."""
        candidate = (STATIC_DIR / full_path).resolve()
        # Guard against ../ traversal escaping the static root
        if candidate.is_file() and candidate.is_relative_to(STATIC_DIR.resolve()):
            return FileResponse(candidate)
        return FileResponse(_index)

    log.info(f"[static] serving frontend from {STATIC_DIR}")
else:
    log.info(f"[static] no frontend build at {STATIC_DIR} — API only")
