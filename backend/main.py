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
from data.ogn_client      import fetch_ogn_gliders, start_ogn_stream, fetch_glider_track, drain_beacon_buffers, beacon_cursor, is_thermal_evidence, flush_beacons, purge_old_beacons
from data import meteo_client, terrain_client, landcover_client
from data.meteo_client    import fetch_meteo_features
from data.landcover_client import fetch_landcover_fine
from data.terrain_client  import fetch_elevation_grid, fetch_elevation_point, fetch_elevation_batch, snap_grid_centre, cached_elevation
from pipeline.feature_engineering import build_feature_matrix
from models.thermal_model import ThermalModel
from config import (UPDATE_INTERVAL, WS_FRAME_INTERVAL, GRID_RES, GRID_RADIUS, CORS_ORIGINS, ADMIN_TOKEN,
                    STATIC_DIR, DATA_DIR, BEACON_RETENTION_DAYS, PREDICT_CONCURRENCY,
                    MIN_FREE_DISK_GB, MIN_RETENTION_DAYS, ENABLE_LANDCOVER)
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


def _free_disk_gb() -> float:
    return shutil.disk_usage(DATA_DIR).free / 2**30


# Two rounds, then stop and escalate.  Each round is a multi-million-row delete
# on a box that is already in trouble, and on a database created before
# auto_vacuum=INCREMENTAL the freed pages never return to the filesystem — so a
# loop that waits for free space to recover would grind indefinitely against a
# number that cannot move.  Two rounds is enough to cap growth; anything still
# wrong after that is not beacon history's fault.
_MAX_EMERGENCY_PURGES = 2


async def _disk_guard() -> None:
    """Shorten retention while free space is under the floor.

    The feed writes ~1.4 GB/day, which can close a gap faster than the weekly
    window reclaims it — a purge job that failed for a few cycles, a disk shared
    with Docker image layers, a /seed rebuild landing at the wrong moment.
    Beacon history is the cheapest thing on this box to give up, so it goes
    first, and loudly.  /seed no longer silently starves when the window moves:
    its default follows BEACON_RETENTION_DAYS and an explicit days_back past the
    window is clamped with a warning.  An emergency purge still shortens history
    under /seed's feet mid-run, which the warning will not catch.
    """
    free = await asyncio.to_thread(_free_disk_gb)
    if free >= MIN_FREE_DISK_GB:
        return

    days = BEACON_RETENTION_DAYS
    for _ in range(_MAX_EMERGENCY_PURGES):
        if free >= MIN_FREE_DISK_GB or days <= MIN_RETENTION_DAYS:
            break
        days = max(MIN_RETENTION_DAYS, days / 2)
        log.warning(
            f"[purge] {free:.1f} GB free, below the {MIN_FREE_DISK_GB:.1f} GB floor — "
            f"emergency purge to {days:.1f} days; /seed loses history beyond that"
        )
        try:
            deleted = await asyncio.to_thread(purge_old_beacons, days)
        except Exception as exc:
            log.error(f"[purge] emergency purge failed: {exc}")
            return
        free = await asyncio.to_thread(_free_disk_gb)
        log.warning(f"[purge] emergency purge removed {deleted:,} beacons — "
                    f"{free:.1f} GB free")

    if free < MIN_FREE_DISK_GB:
        log.error(
            f"[purge] still {free:.1f} GB free after shortening retention — this is "
            f"no longer something beacon retention can fix. Check Docker usage "
            f"(`docker system df`; build cache is usually the culprit), and whether "
            f"this DB predates auto_vacuum=INCREMENTAL, in which case deletes cap "
            f"growth but reclaim nothing — see DEPLOY.md for the one-time VACUUM."
        )


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
    try:
        await _disk_guard()
    except Exception as exc:
        log.warning(f"[purge] disk guard failed: {exc}")


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
    await landcover_client.aclose()

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


# Probabilities are drawn as a colour ramp, so nothing past the third decimal can
# reach a pixel.  Serialising raw float64 spent ~19 characters per cell on digits
# no one can see: 1.62 MB per response over the 201x201 grid, by far the largest
# payload the app produces.  Rounding is applied here rather than inside predict()
# because _apply_ogn_fusion still needs the full-precision values to add to.
_PROB_DP = 3


def _round_probs(values: list[float]) -> list[float]:
    return np.round(np.asarray(values, dtype=float), _PROB_DP).tolist()


# Admission control for the one genuinely expensive request.  Each prediction
# holds ~30 MB (a 50 x 40401 result array plus a per-iteration copy of the
# feature matrix) and saturates the cores it is given, so concurrent ones split
# the same CPU into slower pieces while multiplying peak memory on a 896 MB box.
# A queue is the honest response: everyone waits their turn and nobody is
# rejected, versus every request degrading together and the box swapping.
_predict_sem = asyncio.Semaphore(PREDICT_CONCURRENCY)


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
        # Kept as a name rather than inlined: it is both the basis for the
        # default altitude and the reference the response reports height
        # against, and the two must be the same number.
        terrain_mean = float(terrain.mean())
        alt_amsl = (
            float(alt) if alt is not None
            else terrain_mean + DEFAULT_WORKING_AGL
        )
        land_props = None
        if ENABLE_LANDCOVER:
            land_props = await fetch_landcover_fine(lat, lon, terrain.shape, radius)
        features = build_feature_matrix(
            meteo, terrain,
            dt=datetime.now(timezone.utc) + timedelta(hours=forecast_h),
            lat_bounds=(grid_lat - radius, grid_lat + radius),
            lon_bounds=(grid_lon - radius, grid_lon + radius),
            alt_amsl=alt_amsl,
            land_use_props=land_props,
        )
        grid_rows, grid_cols = terrain.shape
        # to_thread, not inline: predict() runs _MC_SAMPLES synchronous passes
        # over the whole grid (~2M rows), which pins the event loop for ~0.5 s on
        # the deployed VM.  The app is pinned to --workers 1, so that stalls the
        # /ws/live frames, /healthz and every other request behind it.  XGBoost
        # releases the GIL inside its OpenMP section, so a worker thread really
        # does run alongside the loop rather than just deferring the block.
        async with _predict_sem:
            heatmap, heatmap_std = await asyncio.to_thread(model.predict, features)
        live_gliders = await fetch_ogn_gliders()
        heatmap = _apply_ogn_fusion(heatmap, live_gliders, grid_lat, grid_lon,
                                    radius, grid_rows, grid_cols)
    except Exception as exc:
        log.error(f"[predict] failed for ({lat},{lon}): {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    return {
        "heatmap":      _round_probs(heatmap),
        "heatmap_std":  _round_probs(heatmap_std),
        "rows":         grid_rows,
        "cols":         grid_cols,
        "grid_lat":     grid_lat,
        "grid_lon":     grid_lon,
        # The altitude this heatmap actually answers for, and where it came
        # from.  A caller that omits alt gets a height derived from terrain it
        # never sees, so without this the same coordinates can return two
        # different maps with nothing on the wire to explain the difference.
        # alt_agl is measured against the grid's mean terrain, not the ground
        # under any single cell -- it is the reference DEFAULT_WORKING_AGL is
        # defined in, so the default case always reports exactly that value.
        "alt_amsl":     round(alt_amsl),
        "alt_agl":      round(alt_amsl - terrain_mean),
        "alt_source":   "observer" if alt is not None else "default",
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

# Ground elevation is cached on a ~1 km lattice, which is accurate enough at
# altitude and useless near the deck.  Measured on the live feed: NAV405393 sat
# parked at 517 m on a knoll south of Rieti, over ground the lattice sampled
# 700 m away in the valley at 468 m.  Its AGL therefore went out as +49 m, and
# the client's 10 m airborne test kept a stationary aircraft on the map as
# "flying" while the pinned card, which reads the exact point, said -1 m.
#
# So anything the coarse pass puts within _PRECISE_BAND_M of the ground is
# resampled at _PRECISE_DP.  4 dp (~11 m) rather than 3: at that same knoll 3 dp
# rounds to 42.403 and reads 504 m, still +13 m of phantom height and still past
# the 10 m threshold.  11 m is finer than SRTM's 30 m posts, so it is the exact
# ground under the aircraft — no more precision is available to buy.
#
# Both gates exist to bound what that costs.  OpenTopoData's free tier is 1000
# requests a day for the whole deployment, and an aircraft crosses a fresh 11 m
# cell several times a second, so "always precise" would spend the budget in
# minutes.  Height alone is not enough of a gate either: a ridge-soaring glider
# lives inside the band for an hour at 100 km/h.  Adding the speed gate narrows
# the fine sampling to aircraft sitting on or crawling around an airfield, which
# occupy a handful of cells that then stay cached — on disk, so across restarts.
# Fast traffic inside the band keeps the coarse reading, which costs at most a
# glider showing on the map through its twenty-second takeoff roll.
_PRECISE_DP            = 4
_PRECISE_BAND_M        = 150.0
_PRECISE_MAX_SPEED_KMH = 40.0


def _wants_precise_ground(g: dict, coarse_elev: int) -> bool:
    """Is this aircraft one whose agl the coarse lattice cannot be trusted on?"""
    return (g["alt"] - coarse_elev <= _PRECISE_BAND_M
            and (g.get("speed_kmh") or 0) <= _PRECISE_MAX_SPEED_KMH)


async def _add_agl(gliders: list[dict]) -> list[dict]:
    """Attach agl (metres above ground) to each glider dict in-place.

    Blocking twin of _attach_cached_agl, and it follows the same two-resolution
    rule so a caller cannot get one meaning of agl here and another over
    /ws/live: coarse everywhere, resampled at _PRECISE_DP for the aircraft
    _wants_precise_ground picks out.
    """
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
    near_ground = [
        pair for g, pair in zip(gliders, pairs)
        if elevs.get(pair) is not None and _wants_precise_ground(g, elevs[pair])
    ]
    fine: dict[tuple, int | None] = {}
    if near_ground:
        try:
            fine = await asyncio.wait_for(
                fetch_elevation_batch(near_ground, dp=_PRECISE_DP), timeout=8.0)
        except asyncio.TimeoutError:
            fine = {}
    for g, pair in zip(gliders, pairs):
        # Explicit None test, not `or`: a fine reading of 0 m is sea level, and
        # falling back to the coarse value there would undo the resample.
        elev = fine.get(pair)
        if elev is None:
            elev = elevs.get(pair)
        g["agl"] = (g["alt"] - elev) if elev is not None else None
    return gliders


# Only one warm runs at a time process-wide: every viewer sees the same misses
# over the same shared elevation cache, so N sockets must not mean N fetches.
_agl_warm_task: asyncio.Task | None = None


async def _warm_elevations(coarse: list[tuple[float, float]],
                           fine:   list[tuple[float, float]]) -> None:
    """Pull missing ground elevations into the cache, off the frame path."""
    try:
        if coarse:
            await fetch_elevation_batch(coarse)
        if fine:
            await fetch_elevation_batch(fine, dp=_PRECISE_DP)
    except Exception as exc:
        log.warning(f"[ws] elevation warm failed: {exc}")


def _attach_cached_agl(gliders: list[dict]) -> list[dict]:
    """Attach agl to each glider from the warm elevation cache, synchronously.

    /ws/live cannot use _add_agl: it sends a frame every 2 s per viewer, while a
    cold fetch_elevation_batch is rate-limited to 1 req/s and _add_agl waits up
    to 8 s for it.  Awaiting that inline would stall the stream behind
    OpenTopoData for every viewer at once.

    Without this the field was simply absent from the wire — _to_wire only
    copies keys that exist — so `agl` was undefined on every glider the map ever
    drew and the client's airborne filter silently fell through to its
    speed-only fallback, which passes anything on a takeoff roll or a winch
    launch.  Misses go to a background warm and resolve on a later frame; until
    then agl is explicitly null, which the client reads as "unknown".

    Aircraft _wants_precise_ground picks out are resampled at _PRECISE_DP,
    because down there the lattice's own sampling error is several times the
    threshold anyone tests agl against.  One awaiting that finer reading keeps
    the coarse one for a frame or two rather than going null: an approximate
    height is a better answer than none for everything except the
    is-it-on-the-ground question, and that one self-corrects on the next frame.
    """
    global _agl_warm_task
    coarse_misses: list[tuple[float, float]] = []
    fine_misses:   list[tuple[float, float]] = []
    for g in gliders:
        elev = cached_elevation(g["lat"], g["lon"])
        if elev is None:
            coarse_misses.append((g["lat"], g["lon"]))
        elif _wants_precise_ground(g, elev):
            fine = cached_elevation(g["lat"], g["lon"], _PRECISE_DP)
            if fine is None:
                fine_misses.append((g["lat"], g["lon"]))
            else:
                elev = fine
        g["agl"] = (g["alt"] - elev) if elev is not None else None
    if (coarse_misses or fine_misses) and (_agl_warm_task is None or _agl_warm_task.done()):
        # Test for the loop before building the coroutine: constructing it and
        # then failing to schedule it leaves an un-awaited coroutine behind.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop. The agl values computed above are still good;
            # only the background warm is skipped, so misses stay null this pass
            # and resolve on the next call that has one. Degrading here keeps
            # this callable from sync code rather than letting a cache miss
            # raise out of an otherwise fine request.
            pass
        else:
            _agl_warm_task = asyncio.create_task(
                _warm_elevations(coarse_misses, fine_misses))
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
    # to_thread, not inline: this is a synchronous sqlite3 query against a table
    # holding a week of the worldwide feed (4+ GB, tens of millions of rows), and
    # sqlite3 holds the GIL across it.  Run on the event loop it stalls /ws/live
    # for every viewer, which is what turned a handful of track requests into
    # minute-long gaps between live frames.
    return {"track": await asyncio.to_thread(fetch_glider_track, glider_id)}


@app.get("/ogn/live")
async def ogn_live(wait_agl: bool = False):
    """Snapshot of the live feed.

    agl comes from the warm elevation cache by default, which makes this return
    immediately.  It used to call _add_agl unconditionally and so paid for a
    rate-limited OpenTopoData batch on every hit — measured at 2.6 s against the
    deployed VM, against an 8 s timeout, for an endpoint the frontend does not
    even call.  Cache misses are warmed in the background and resolve on a later
    request; pass wait_agl=true to block for them instead, which is what a
    one-shot caller that needs agl on the first response wants.
    """
    gliders = await fetch_ogn_gliders()
    gliders = await _add_agl(gliders) if wait_agl else _attach_cached_agl(gliders)
    return {"gliders": _to_wire(gliders)}


@app.get("/ogn/search")
async def ogn_search(q: str, limit: int = 5):
    """Substring match on glider id across the whole live feed.

    The search box used to filter the client's own copy of the glider list,
    which only worked while that copy was the entire planet.  Now that /ws/live
    is scoped to the viewport, the lookup has to run where the full picture
    still exists — otherwise searching for a glider would only ever find one
    already visible on screen, which is the case where you don't need to search.
    """
    lq = q.strip().lower()
    if not lq:
        return {"gliders": []}
    gliders = await fetch_ogn_gliders()
    hits = [g for g in gliders if lq in g["id"].lower()]
    # Prefix matches first: typing a full registration should put it at the top
    # rather than behind whatever else happens to contain the string.
    hits.sort(key=lambda g: (not g["id"].lower().startswith(lq), g["id"]))
    return {"gliders": _to_wire(hits[:max(0, min(limit, 25))])}

@app.post("/train", dependencies=[Depends(_require_admin)])
async def trigger_train(background_tasks: BackgroundTasks):
    """Manually trigger model retraining in the background."""
    if _training_lock.locked():
        return {"status": "already_running"}
    background_tasks.add_task(_retrain_job)
    return {"status": "started"}


async def _seed_job(days_back: float | None, limit: int, reset: bool = False) -> None:
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
    days_back: float | None = None,
    limit: int = 5000,
    reset: bool = False,
):
    """
    Backfill the training buffer from historical beacon DB records.

    Fetches archived NWP meteo once per (0.5° × 0.5° × hour) bucket and
    builds feature vectors for every sampled beacon. Runs in the background;
    check server logs for progress. Triggers a retrain automatically when done
    if the buffer crosses the minimum sample threshold.

    - days_back: how many days of history to scan (default: the whole beacon
      retention window, BEACON_RETENTION_DAYS). Values past that window are
      clamped with a warning — the older beacons have already been purged.
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


Bounds = tuple[float, float, float, float]   # lat_min, lat_max, lon_min, lon_max


def _parse_bounds(raw: object) -> Bounds | None:
    """Validate a client viewport message.

    Returns None for anything malformed, and the caller reads None as "no
    filter" — a client that sends nonsense keeps the old worldwide behaviour
    instead of silently receiving an empty map.  A span of a full 360 degrees is
    also None: it selects everything, so filtering would only burn CPU.
    """
    if not isinstance(raw, dict):
        return None
    try:
        lat_min, lat_max = float(raw["lat_min"]), float(raw["lat_max"])
        lon_min, lon_max = float(raw["lon_min"]), float(raw["lon_max"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (lat_min <= lat_max and lon_min <= lon_max):
        return None
    if lon_max - lon_min >= 360.0:
        return None
    return lat_min, lat_max, lon_min, lon_max


def _in_bounds(g: dict, b: Bounds) -> bool:
    lat_min, lat_max, lon_min, lon_max = b
    if not (lat_min <= g["lat"] <= lat_max):
        return False
    # Leaflet reports longitudes outside +/-180 once the view crosses the
    # antimeridian, while a beacon's lon is always inside it.  Test the wrapped
    # copies too, else the Pacific edge of the map goes blank.
    lon = g["lon"]
    return any(lon_min <= lon + off <= lon_max for off in (0.0, 360.0, -360.0))


# How long the first frame waits for the client to say where it is looking.
#
# Without this wait the stream opens unfiltered, so frame 1 carries every glider
# on the planet and frame 2 — once the viewport has been read — carries only the
# handful on screen.  The client draws both, so the map fills with hundreds of
# aircraft and then empties itself seconds later, which reads as "all the
# gliders disappeared" rather than as the filter engaging.
#
# The client sends its bounds from onopen, so this is a round trip, not a poll.
# Expiring is not an error: a frontend that never sends bounds is an older one,
# and it still gets the full feed exactly as before — one frame later.
_BOUNDS_GRACE_S = 2.0


@app.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    """Live glider stream, scoped to the client's map viewport.

    The client sends {"bounds": {lat_min, lat_max, lon_min, lon_max}} on connect
    and on every map move; anything outside is dropped before serialisation.
    Without it each viewer downloaded every glider on the planet every 2 s in
    order to draw the handful actually on screen.  Bounds stay optional: a
    client that never sends any still gets the full feed, so an older frontend
    against a newer backend degrades to the previous behaviour rather than to a
    blank map.
    """
    await ws.accept()
    bounds: Bounds | None = None
    declared = asyncio.Event()          # client has told us where it is looking

    async def _read_client() -> None:
        """Apply viewport updates until the socket closes."""
        nonlocal bounds
        try:
            while True:
                try:
                    msg = json.loads(await ws.receive_text())
                except ValueError:
                    continue            # malformed frame — keep the current viewport
                if isinstance(msg, dict) and "bounds" in msg:
                    bounds = _parse_bounds(msg["bounds"])
                    declared.set()
        except (WebSocketDisconnect, RuntimeError):
            pass                        # the send loop notices and tears down

    reader = asyncio.create_task(_read_client())
    # Per-connection cursor: every viewer reads the same beacons at its own pace.
    cursor = beacon_cursor()
    try:
        try:
            await asyncio.wait_for(declared.wait(), timeout=_BOUNDS_GRACE_S)
        except asyncio.TimeoutError:
            pass                        # no viewport declared — send the full feed
        while True:
            try:
                gliders         = await fetch_ogn_gliders()
                new_pos, cursor = drain_beacon_buffers(cursor)
            except Exception as e:
                log.warning(f"[ws] glider fetch failed: {e}")
                gliders = []
                new_pos = {}
            if bounds is not None:
                gliders = [g for g in gliders if _in_bounds(g, bounds)]
                # Track updates follow the markers: a glider that is not being
                # drawn has no path to extend.
                visible = {g["id"] for g in gliders}
                new_pos = {k: v for k, v in new_pos.items() if k in visible}
            await ws.send_text(json.dumps({
                "gliders":       _to_wire(_attach_cached_agl(gliders)),
                "new_positions": _positions_to_wire(new_pos),
            }))
            await asyncio.sleep(WS_FRAME_INTERVAL)
    except (WebSocketDisconnect, RuntimeError):
        pass  # client navigated away — normal teardown
    finally:
        reader.cancel()


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
