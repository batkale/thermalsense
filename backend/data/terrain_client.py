import asyncio
import json
import logging
import httpx
import numpy as np
from pathlib import Path
from scipy.ndimage import zoom as ndimage_zoom
from config import SRTM_BASE, GRID_RES, TERRAIN_RES, GRID_RADIUS, DATA_DIR

log = logging.getLogger(__name__)

# Session-level cache keyed by (lat, lon) rounded to 2 dp (~1 km resolution)
_elev_cache: dict[tuple[float, float], int] = {}

# Grid cache keyed by (snapped lat, snapped lon, radius) — terrain grids are static.
# The key must be the centre the array was actually built around; see
# snap_grid_centre for why anything looser hands out the wrong patch of ground.
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
    # 2 dp, matching the lattice snap_grid_centre rounds to. Files written by the
    # old 1 dp scheme carry a different name, so they are simply never read again
    # rather than being mistaken for grids centred where their name claims.
    return _CACHE_DIR / f"g_{lat:+.2f}_{lon:+.2f}_{radius:.3f}.npy"


def snap_grid_centre(lat: float, lon: float) -> tuple[float, float]:
    """
    Snap a requested centre onto the terrain lattice.

    Callers that build a feature matrix from fetch_elevation_grid MUST derive
    their lat/lon bounds from this rather than from the raw request point. The
    grid is fetched, cached and returned for the snapped centre; pairing it with
    unsnapped bounds labels every cell with coordinates it does not describe,
    which is how two gliders a kilometre apart used to get maps of different
    ground while claiming to cover the same zone.

    Snapping moves the centre by at most half a coarse cell (~550 m at
    TERRAIN_RES), which is finer than the terrain is sampled, and it puts every
    sample on an exact multiple of TERRAIN_RES so neighbouring grids share
    coarse points through the persistent point cache instead of refetching them.
    """
    return (round(round(lat / TERRAIN_RES) * TERRAIN_RES, 6),
            round(round(lon / TERRAIN_RES) * TERRAIN_RES, 6))


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


def cached_elevation(lat: float, lon: float) -> int | None:
    """Ground elevation from the warm cache only — never touches the network.

    For hot paths that cannot afford a rate-limited fetch (the 2 s /ws/live
    frame).  A miss returns None rather than blocking; the caller is expected to
    warm the cache out of band and pick the value up on a later pass.  The key is
    the same 2 dp (~1 km) cell fetch_elevation_batch caches under, so points a
    grid fetch already resolved are hits here too.
    """
    return _elev_cache.get((round(lat, 2), round(lon, 2)))


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


def grid_shape(radius: float = GRID_RADIUS) -> tuple[int, int]:
    """Shape of the fine grid fetch_elevation_grid returns for a given radius."""
    n = round((2 * radius) / GRID_RES) + 1
    return (n, n)


async def fetch_elevation_grid(lat: float, lon: float,
                               radius: float = GRID_RADIUS) -> np.ndarray:
    """
    Fetch coarse SRTM 30m elevation at TERRAIN_RES, then bilinearly upsample to GRID_RES.

    The grid is centred on snap_grid_centre(lat, lon) — NOT on the point passed in —
    and spans exactly [centre - radius, centre + radius] on both axes, inclusive of
    both edges.  Callers must build their lat/lon bounds from the same snapped
    centre, or the cells carry coordinates the elevations underneath them do not
    belong to.

    Both edges being inclusive is load-bearing.  Sampling with endpoint=False
    covered 2*radius - TERRAIN_RES of ground while build_feature_matrix labelled
    the result as the full 2*radius, stretching the terrain ~11% across the grid
    and displacing the far edge by a whole coarse cell.

    Coarse grid: 2*radius/TERRAIN_RES + 1 points per axis → 11×11 = 121 pts.
    Fine grid:   2*radius/GRID_RES + 1 per axis → 201×201 cells at exactly GRID_RES.
    Points are fetched through fetch_elevation_batch so overlapping grids share
    them via the persistent point cache.  Falls back to flat-zero on any failure.
    """
    clat, clon = snap_grid_centre(lat, lon)
    key = (clat, clon, radius)
    if key in _grid_cache:
        return _grid_cache[key]

    n_coarse   = round((2 * radius) / TERRAIN_RES) + 1   # 11 for default params
    fine_shape = grid_shape(radius)                      # (201, 201)
    lats_c = np.linspace(clat - radius, clat + radius, n_coarse)
    lons_c = np.linspace(clon - radius, clon + radius, n_coarse)

    def _upsample(coarse: np.ndarray) -> np.ndarray:
        if coarse.shape != (n_coarse, n_coarse):
            raise ValueError(f"coarse grid is {coarse.shape}, expected "
                             f"{(n_coarse, n_coarse)}")
        # Endpoint-to-endpoint: coarse[0] lands on fine[0] and coarse[-1] on
        # fine[-1], so the fine lattice spans the same ground at exactly GRID_RES.
        fine = ndimage_zoom(coarse, fine_shape[0] / n_coarse, order=1)  # bilinear
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

    pts = [(float(la), float(lo)) for la in lats_c for lo in lons_c]
    try:
        # Every lattice point is an exact multiple of TERRAIN_RES = the batch
        # cache's 2 dp key, so a neighbouring grid one lattice step away reuses
        # all but one row of these instead of refetching the whole window.
        budget = -(-len(pts) // MAX_BATCH_POINTS)   # ceil: never truncate the grid
        elevs = await fetch_elevation_batch(pts, max_requests=budget)
        missing = sum(elevs.get(p) is None for p in pts)
        if missing:
            raise RuntimeError(f"{missing}/{len(pts)} points unresolved")
        coarse = np.array([elevs[p] for p in pts], dtype=float).reshape(n_coarse, n_coarse)
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
