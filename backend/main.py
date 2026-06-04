from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio, json, logging
import numpy as np
from datetime import datetime, timezone, timedelta
from data.ogn_client      import fetch_ogn_gliders, start_ogn_stream, fetch_glider_track, drain_beacon_buffers
from data.meteo_client    import fetch_meteo_features
from data.terrain_client  import fetch_elevation_grid, fetch_elevation_point, fetch_elevation_batch
from pipeline.feature_engineering import build_feature_matrix
from models.thermal_model import ThermalModel
from config import UPDATE_INTERVAL, GRID_RES

log = logging.getLogger(__name__)
model = ThermalModel()
scheduler = AsyncIOScheduler()
_training_lock = asyncio.Lock()

async def _retrain_job():
    """Scheduled job: fetch fresh OGN data and retrain the model."""
    if _training_lock.locked():
        log.info("[train] already running, skipping")
        return
    async with _training_lock:
        log.info("[train] starting scheduled retrain")
        try:
            # Hard cap: must finish before the next scheduled fire
            await asyncio.wait_for(model.retrain(), timeout=UPDATE_INTERVAL - 30)
        except asyncio.TimeoutError:
            log.warning("[train] retrain timed out — will retry next cycle")
        except Exception as e:
            log.error(f"[train] failed: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_ogn_stream()
    await model.load()
    scheduler.add_job(
        _retrain_job, "interval", seconds=UPDATE_INTERVAL, id="retrain",
        misfire_grace_time=UPDATE_INTERVAL // 2,  # tolerate up to 150s lateness
        coalesce=True,                             # merge stacked missed runs into one
    )
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def _apply_ogn_fusion(
    heatmap: list[float],
    gliders: list[dict],
    lat: float, lon: float, radius: float,
    rows: int, cols: int,
) -> list[float]:
    """Blend live confirmed thermals (circling + vario > 1 m/s) into the ML heatmap."""
    circling = [g for g in gliders if g.get("circling") and g.get("vario", 0) > 1.0]
    if not circling:
        return heatmap
    arr    = np.array(heatmap, dtype=float).reshape(rows, cols)
    sigma  = 6.0                                    # grid cells ≈ 300 m radius
    window = int(3 * sigma)
    for g in circling:
        ri = (g["lat"] - (lat - radius)) / GRID_RES
        ci = (g["lon"] - (lon - radius)) / GRID_RES
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


@app.get("/predict")
async def predict(lat: float, lon: float, alt: int = 500, forecast_h: int = 0):
    try:
        meteo, terrain = await asyncio.gather(
            fetch_meteo_features(lat, lon, forecast_h),
            fetch_elevation_grid(lat, lon),
        )
        radius   = 0.05  # matches fetch_elevation_grid default
        features = build_feature_matrix(
            meteo, terrain,
            dt=datetime.now(timezone.utc) + timedelta(hours=forecast_h),
            lat_bounds=(lat - radius, lat + radius),
            lon_bounds=(lon - radius, lon + radius),
        )
        grid_rows, grid_cols = terrain.shape
        heatmap, heatmap_std = model.predict(features)
        live_gliders = await fetch_ogn_gliders()
        heatmap = _apply_ogn_fusion(heatmap, live_gliders, lat, lon, radius, grid_rows, grid_cols)
    except Exception as exc:
        log.error(f"[predict] failed for ({lat},{lon}): {exc}")
        raise HTTPException(status_code=500, detail=str(exc))
    return {
        "heatmap":      heatmap,
        "heatmap_std":  heatmap_std,
        "rows":         grid_rows,
        "cols":         grid_cols,
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
        elevs = await asyncio.wait_for(fetch_elevation_batch(pairs), timeout=4.0)
    except asyncio.TimeoutError:
        elevs = {}
    for g, pair in zip(gliders, pairs):
        elev = elevs.get(pair)
        g["agl"] = (g["alt"] - elev) if elev is not None else None
    return gliders


def _cluster_circling(gliders: list[dict], radius: float = 0.012) -> list[dict]:
    """Greedy proximity clustering of circling gliders (~1.3 km radius at UK latitudes)."""
    candidates = [g for g in gliders if g.get("circling") and g.get("vario", 0) > 0.5]
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
    return {"gliders": gliders}

@app.post("/train")
async def trigger_train(background_tasks: BackgroundTasks):
    """Manually trigger model retraining in the background."""
    if _training_lock.locked():
        return {"status": "already_running"}
    background_tasks.add_task(_retrain_job)
    return {"status": "started"}


async def _seed_job(days_back: int, limit: int) -> None:
    async with _training_lock:
        log.info(f"[seed] starting — days_back={days_back}, limit={limit}")
        try:
            await asyncio.wait_for(
                model.seed_from_history(days_back=days_back, limit=limit),
                timeout=3600,
            )
        except asyncio.TimeoutError:
            log.warning("[seed] timed out after 1 hour")
        except Exception as exc:
            log.error(f"[seed] failed: {exc}")


@app.post("/seed")
async def seed_from_history(
    background_tasks: BackgroundTasks,
    days_back: int = 3,
    limit: int = 5000,
):
    """
    Backfill the training buffer from historical beacon DB records.

    Fetches archived NWP meteo once per (0.5° × 0.5° × hour) bucket and
    builds feature vectors for every sampled beacon. Runs in the background;
    check server logs for progress. Triggers a retrain automatically when done
    if the buffer crosses the minimum sample threshold.

    - days_back: how many days of history to scan (default 3)
    - limit: max beacon rows to process (default 5000; sampled uniformly across the window)
    """
    if _training_lock.locked():
        return {"status": "already_running"}
    background_tasks.add_task(_seed_job, days_back, limit)
    return {"status": "started", "days_back": days_back, "limit": limit}

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
            await ws.send_text(json.dumps({"gliders": gliders, "new_positions": new_pos}))
            await asyncio.sleep(2)
    except (WebSocketDisconnect, RuntimeError):
        pass  # client navigated away — normal teardown
