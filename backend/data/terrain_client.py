import asyncio
import json
import logging
import httpx
import numpy as np
from pathlib import Path
from scipy.ndimage import zoom as ndimage_zoom
from config import SRTM_BASE, GRID_RES, TERRAIN_RES, DATA_DIR

log = logging.getLogger(__name__)

# Session-level cache keyed by (lat, lon) rounded to 2 dp (~1 km resolution)
_elev_cache: dict[tuple[float, float], int] = {}

# Grid cache keyed by (lat, lon, radius) rounded to 2 dp — terrain grids are static
_grid_cache: dict[tuple[float, float, float], np.ndarray] = {}

# --- Disk cache ---------------------------------------------------------------
# Terrain does not change, so these are worth keeping across restarts. Measured
# on the deployed VM: a cold process pays ~18 s for the first request in an area
# versus ~1.2 s warm, almost all of it rate-limited OpenTopoData calls — and the
# free tier only allows 1000/day, so re-fetching after every redeploy is wasteful
# as well as slow.
#
# Only the *coarse* grid is stored (10x10 = 100 floats). The upsample back to
# 200x200 costs microseconds, and storing the fine grid instead would be 400x
# larger on disk for no gain.
_CACHE_DIR = DATA_DIR / "cache" / "terrain"
_POINT_CACHE_PATH = _CACHE_DIR / "points.json"
_point_writes_since_flush = 0
_POINT_FLUSH_EVERY = 50


def _grid_path(key: tuple[float, float, float]) -> Path:
    lat, lon, radius = key
    return _CACHE_DIR / f"g_{lat:+.1f}_{lon:+.1f}_{radius:.3f}.npy"


def load_caches() -> None:
    """Restore the point-elevation cache from disk. Called once from the lifespan."""
    if not _POINT_CACHE_PATH.exists():
        return
    try:
        raw = json.loads(_POINT_CACHE_PATH.read_text(encoding="utf-8"))
        for k, v in raw.items():
            lat_s, lon_s = k.split(",")
            _elev_cache[(float(lat_s), float(lon_s))] = v
        log.info(f"[terrain] restored {len(_elev_cache)} point elevations from disk")
    except Exception as exc:
        log.warning(f"[terrain] point cache unreadable ({exc}) — starting empty")


def flush_point_cache(force: bool = False) -> None:
    """Persist the point cache, batched so a seed run isn't writing on every hit."""
    global _point_writes_since_flush
    if not force and _point_writes_since_flush < _POINT_FLUSH_EVERY:
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _POINT_CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({f"{la},{lo}": v for (la, lo), v in _elev_cache.items()}),
            encoding="utf-8",
        )
        tmp.replace(_POINT_CACHE_PATH)   # atomic — a crash mid-write can't corrupt it
        _point_writes_since_flush = 0
    except OSError as exc:
        log.warning(f"[terrain] could not persist point cache ({exc})")

# One pooled client — a seed run makes thousands of calls, and a fresh
# AsyncClient per call exhausts the ephemeral port range (see meteo_client).
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=20,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
        )
    return _client


async def aclose() -> None:
    """Release the pooled connections. Called from the app lifespan on shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


# Serialise all outbound requests: opentopodata.org free tier = 1 req/s
_api_lock: asyncio.Semaphore | None = None

def _get_lock() -> asyncio.Semaphore:
    global _api_lock
    if _api_lock is None:
        _api_lock = asyncio.Semaphore(1)
    return _api_lock


async def _get_with_retry(client: httpx.AsyncClient, url: str, max_retries: int = 3) -> httpx.Response:
    """GET with fixed back-off on 429 (max 3 retries, 2 s each = 6 s total)."""
    for attempt in range(max_retries):
        async with _get_lock():
            r = await client.get(url)
        if r.status_code != 429:
            return r
        log.warning(f"[terrain] 429 rate-limited — retrying in 2s (attempt {attempt + 1}/{max_retries})")
        await asyncio.sleep(2.0)
    return r  # caller calls raise_for_status(); falls back to flat-zero on failure


async def fetch_elevation_point(lat: float, lon: float) -> int | None:
    """Return ground elevation in metres at a single point, or None on failure."""
    try:
        r = await _get_with_retry(_get_client(), f"{SRTM_BASE}?locations={lat:.5f},{lon:.5f}")
        r.raise_for_status()
        elev = r.json()["results"][0]["elevation"]
        return int(elev) if elev is not None else 0
    except Exception as exc:
        log.warning(f"[terrain] point elevation failed ({exc})")
        return None


MAX_BATCH_POINTS = 100      # opentopodata.org caps a single request at 100 locations


async def fetch_elevation_batch(
    latlon_pairs: list[tuple[float, float]],
    max_requests: int = 2,
) -> dict[tuple, int | None]:
    """
    Elevation for multiple points, split into MAX_BATCH_POINTS-sized requests
    (1 req/s limit).  Results cached by (lat, lon) rounded to 2 dp, and the
    uncached points are deduplicated on that same key before fetching — a
    single request therefore covers up to 100 distinct ~1 km cells.

    At most `max_requests` chunks are fetched per call so a large input can't
    blow past the caller's timeout; points beyond that budget come back None
    and are resolved by later calls as the cache warms.

    Returns {(lat, lon): elevation_m | None} for every input pair.
    """
    global _point_writes_since_flush

    out: dict[tuple, int | None] = {}
    pending: dict[tuple[float, float], list[tuple[float, float]]] = {}

    for lat, lon in latlon_pairs:
        key = (round(lat, 2), round(lon, 2))
        if key in _elev_cache:
            out[(lat, lon)] = _elev_cache[key]
        else:
            pending.setdefault(key, []).append((lat, lon))

    keys      = list(pending)
    budgeted  = keys[: max_requests * MAX_BATCH_POINTS]
    for key in keys[len(budgeted):]:
        for pair in pending[key]:
            out[pair] = None

    client = _get_client()
    for i in range(0, len(budgeted), MAX_BATCH_POINTS):
        if i > 0:
            await asyncio.sleep(1.1)
        chunk = budgeted[i : i + MAX_BATCH_POINTS]
        pts   = "|".join(f"{la:.5f},{lo:.5f}" for la, lo in chunk)
        try:
            r = await _get_with_retry(client, f"{SRTM_BASE}?locations={pts}")
            r.raise_for_status()
            items = r.json()["results"]
        except Exception as exc:
            log.warning(f"[terrain] batch elevation failed ({exc})")
            items = []
        for key, item in zip(chunk, items):
            elev = int(item["elevation"] or 0)
            _elev_cache[key] = elev
            _point_writes_since_flush += 1
            for pair in pending[key]:
                out[pair] = elev
        flush_point_cache()
        for key in chunk[len(items):]:          # short/failed response
            for pair in pending[key]:
                out[pair] = None

    return out


async def fetch_elevation_grid(lat: float, lon: float, radius: float = 0.05) -> np.ndarray:
    """
    Fetch coarse SRTM 30m elevation at TERRAIN_RES, then bilinearly upsample to GRID_RES.

    Coarse grid: radius/TERRAIN_RES points per axis → ≤10×10 = 100 pts → 1 API batch.
    Fine grid:   zoom factor TERRAIN_RES/GRID_RES → 200×200 = 40,000 cells at ~50 m.
    Rate limit: 1 req/s between chunks.  Falls back to flat-zero on any failure.
    """
    # Round to 1 dp (~10 km) so the cache survives glider movement within the grid area
    key = (round(lat, 1), round(lon, 1), radius)
    if key in _grid_cache:
        return _grid_cache[key]

    n_lat = n_lon = round((2 * radius) / TERRAIN_RES)   # always 10 for default params
    lats_c = np.linspace(lat - radius, lat + radius, n_lat, endpoint=False)
    lons_c = np.linspace(lon - radius, lon + radius, n_lon, endpoint=False)

    zoom_factor = TERRAIN_RES / GRID_RES
    fine_shape = (n_lat * round(zoom_factor), n_lon * round(zoom_factor))

    def _upsample(coarse: np.ndarray) -> np.ndarray:
        fine = ndimage_zoom(coarse, zoom_factor, order=1)   # bilinear
        return fine[: fine_shape[0], : fine_shape[1]]

    # Disk cache — the reason a redeployed container doesn't re-pay the
    # rate-limited fetch for every area anyone has already looked at.
    path = _grid_path(key)
    if path.exists():
        try:
            fine = _upsample(np.load(path))
            _grid_cache[key] = fine
            return fine
        except Exception as exc:
            log.warning(f"[terrain] cached grid {path.name} unreadable ({exc}) — refetching")

    try:
        all_pts = [f"{la:.5f},{lo:.5f}" for la in lats_c for lo in lons_c]
        elevs: list[float] = []
        client = _get_client()
        for i in range(0, len(all_pts), 100):
            if i > 0:
                await asyncio.sleep(1.1)
            chunk = all_pts[i : i + 100]
            r = await _get_with_retry(client, f"{SRTM_BASE}?locations={'|'.join(chunk)}")
            r.raise_for_status()
            elevs.extend(p["elevation"] or 0 for p in r.json()["results"])
        coarse = np.array(elevs, dtype=float).reshape(n_lat, n_lon)
        fine = _upsample(coarse)
        _grid_cache[key] = fine
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            np.save(path, coarse)
        except OSError as exc:
            log.warning(f"[terrain] could not persist grid {path.name} ({exc})")
        return fine
    except Exception as exc:
        log.warning(f"[terrain] elevation fetch failed ({exc}) — using flat-zero fallback")
        return np.zeros(fine_shape)
