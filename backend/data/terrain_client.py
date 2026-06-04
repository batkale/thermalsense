import asyncio
import logging
import httpx
import numpy as np
from scipy.ndimage import zoom as ndimage_zoom
from config import SRTM_BASE, GRID_RES, TERRAIN_RES

log = logging.getLogger(__name__)

# Session-level cache keyed by (lat, lon) rounded to 2 dp (~1 km resolution)
_elev_cache: dict[tuple[float, float], int] = {}

# Grid cache keyed by (lat, lon, radius) rounded to 2 dp — terrain grids are static
_grid_cache: dict[tuple[float, float, float], np.ndarray] = {}

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
        async with httpx.AsyncClient(timeout=10) as client:
            r = await _get_with_retry(client, f"{SRTM_BASE}?locations={lat:.5f},{lon:.5f}")
        r.raise_for_status()
        elev = r.json()["results"][0]["elevation"]
        return int(elev) if elev is not None else 0
    except Exception as exc:
        log.warning(f"[terrain] point elevation failed ({exc})")
        return None


async def fetch_elevation_batch(latlon_pairs: list[tuple[float, float]]) -> dict[tuple, int | None]:
    """
    Elevation for multiple points in one request (max 100 pts, 1 req/s limit).
    Results cached by (lat, lon) rounded to 2 dp to avoid redundant fetches.
    Returns {(lat, lon): elevation_m} for every input pair.
    """
    out: dict[tuple, int | None] = {}
    to_fetch: list[tuple[float, float]] = []

    for lat, lon in latlon_pairs:
        key = (round(lat, 2), round(lon, 2))
        if key in _elev_cache:
            out[(lat, lon)] = _elev_cache[key]
        else:
            to_fetch.append((lat, lon))

    if to_fetch:
        pts = "|".join(f"{la:.5f},{lo:.5f}" for la, lo in to_fetch)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await _get_with_retry(client, f"{SRTM_BASE}?locations={pts}")
            r.raise_for_status()
            items = r.json()["results"]
            for (lat, lon), item in zip(to_fetch, items):
                elev = int(item["elevation"] or 0)
                _elev_cache[(round(lat, 2), round(lon, 2))] = elev
                out[(lat, lon)] = elev
        except Exception as exc:
            log.warning(f"[terrain] batch elevation failed ({exc})")
            for pair in to_fetch:
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

    try:
        all_pts = [f"{la:.5f},{lo:.5f}" for la in lats_c for lo in lons_c]
        elevs: list[float] = []
        async with httpx.AsyncClient(timeout=20) as client:
            for i in range(0, len(all_pts), 100):
                if i > 0:
                    await asyncio.sleep(1.1)
                chunk = all_pts[i : i + 100]
                r = await _get_with_retry(client, f"{SRTM_BASE}?locations={'|'.join(chunk)}")
                r.raise_for_status()
                elevs.extend(p["elevation"] or 0 for p in r.json()["results"])
        coarse = np.array(elevs, dtype=float).reshape(n_lat, n_lon)
        fine = ndimage_zoom(coarse, zoom_factor, order=1)   # bilinear
        fine = fine[: fine_shape[0], : fine_shape[1]]
        _grid_cache[key] = fine
        return fine
    except Exception as exc:
        log.warning(f"[terrain] elevation fetch failed ({exc}) — using flat-zero fallback")
        return np.zeros(fine_shape)
