import asyncio
import logging
import math
import re
from collections import OrderedDict
import httpx
import numpy as np
from pathlib import Path
from scipy.ndimage import zoom as ndimage_zoom
from config import (SRTM_BASE, GRID_RES, TERRAIN_RES, GRID_RADIUS, DATA_DIR, TERRAIN_SOURCE,
                    TERRAIN_GRID_CACHE, TERRAIN_POINT_CACHE, TERRAIN_HIRES_CACHE)
from data import dem_client

log = logging.getLogger(__name__)

# Point elevations keyed by (lat, lon) rounded to DEFAULT_POINT_DP, least
# recently used first.  Bounded by TERRAIN_POINT_CACHE — see config.py for what
# the unbounded version cost.
#
# The key is not just a bucket: it is the coordinate the elevation was actually
# sampled at, because fetch_elevation_batch asks OpenTopoData about the rounded
# point rather than the raw one.  That is what lets entries fetched at different
# `dp` share one dict — a fine lookup of a point that happens to sit on the
# coarse lattice hits the existing entry, since both round to the same
# coordinate and that coordinate is where the elevation came from.
_elev_cache: "OrderedDict[tuple[float, float], int]" = OrderedDict()

# 2 dp = ~1 km cells.  Coarse on purpose: it matches TERRAIN_RES, so the points
# a grid fetch resolves serve point lookups nearby for free.  Callers that need
# the ground under a specific aircraft rather than the ground a kilometre-grid
# says is nearby must pass a finer dp — see _attach_cached_agl in main.py.
DEFAULT_POINT_DP = 2

# Grid cache keyed by (snapped lat, snapped lon, radius) — terrain grids are static.
# The key must be the centre the array was actually built around; see
# snap_grid_centre for why anything looser hands out the wrong patch of ground.
# Bounded by TERRAIN_GRID_CACHE; every grid is also on disk, so falling out of
# here costs a 1 KB read and an upsample, not a fetch.
_grid_cache: "OrderedDict[tuple[float, float, float], np.ndarray]" = OrderedDict()

# Full-resolution terrain columns per grid, same key.  Bounded by
# TERRAIN_HIRES_CACHE and memory-only — see config.py.
_hires_cache: "OrderedDict[tuple[float, float, float], np.ndarray]" = OrderedDict()


def _recall(cache: dict, key):
    """`cache[key]`, marked most recently used.  None on a miss.

    Reads count as use, not only writes: the points under an airfield are
    fetched once and then read on every frame, and a cache that aged them by
    fetch time would evict exactly the entries that are hit most.

    Pop and re-insert rather than OrderedDict.move_to_end, so the plain dicts
    tests put in place of these caches behave the same way.  No lock: both
    caches are only ever touched from the event loop.  Reaching one from a
    worker thread would need a lock here first — between the pop and the
    re-insert the key is absent, and eviction iterates.
    """
    try:
        value = cache.pop(key)
    except KeyError:
        return None
    cache[key] = value
    return value


def _remember(cache: dict, key, value, limit: int) -> None:
    """Store `key` as most recently used and drop the oldest entries past `limit`."""
    cache.pop(key, None)
    cache[key] = value
    while len(cache) > max(limit, 0):
        del cache[next(iter(cache))]

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
# One directory per source.  SRTM and Copernicus agree to a few metres but not
# exactly, and a grid assembled half from one and half from the other would put
# a seam through its slope and aspect.  Switching TERRAIN_SOURCE therefore starts
# a fresh cache rather than reading the other source's values.
_CACHE_DIR = DATA_DIR / "cache" / ("terrain-copernicus" if TERRAIN_SOURCE == "copernicus"
                                   else "terrain")

# --- Point journal ------------------------------------------------------------
# Points are appended as they are fetched, 20 bytes each, instead of the whole
# cache being rewritten as JSON every 50 points.  That rewrite was 16 MB, a
# second of event loop and a 58 MB spike at 770,770 points, and it grew with
# every point it saved.  An append costs what the new points cost.
#
# Fixed-width records are what make an interrupted write harmless: a file whose
# size is not a whole number of records ends in a torn one, the tail is ignored
# on load, and the next flush replaces the file instead of appending after it.
# A text line cut short can parse as a shorter number — 850 m read back as 8.
_POINT_REC = np.dtype([("lat", "<f8"), ("lon", "<f8"), ("elev", "<i4")])
_POINT_FLUSH_EVERY = 50
# The journal is rewritten from memory once it holds this many times the cache.
# Until then it is append-only, so on disk it stays under 2 x 200,000 x 20 B =
# 8 MB however long the process runs.
_JOURNAL_SLACK = 2
_point_unsaved: list[tuple[float, float, int]] = []   # fetched, not yet on disk
_journal_records = 0     # whole records in the journal, as far as this process knows
_journal_clean = True    # False: the file may end mid-record, so replace, never append

# The whole-cache file this replaces.  Read once, to carry a deployed cache
# over, and never written again.
_LEGACY_ENTRY = re.compile(r'"([-+0-9.eE]+),([-+0-9.eE]+)"\s*:\s*(-?[0-9]+)')


def _journal_path() -> Path:
    # Resolved per call, not bound at import: tests redirect _CACHE_DIR, and a
    # path computed up front would keep pointing at the real cache.
    return _CACHE_DIR / "points.bin"


def _legacy_points_path() -> Path:
    return _CACHE_DIR / "points.json"


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


def _i4(elev) -> int:
    """An elevation as the journal stores it.  Clamped so that no value an
    upstream can return — a nodata sentinel, say — is able to make a flush raise."""
    return max(-2**31, min(2**31 - 1, int(elev)))


def _cache_point(key: tuple[float, float], elev: int) -> None:
    """Record a freshly fetched elevation: in memory now, on disk at the next flush."""
    _remember(_elev_cache, key, elev, TERRAIN_POINT_CACHE)
    _point_unsaved.append((key[0], key[1], _i4(elev)))


def _restore_points(rec: np.ndarray) -> int:
    """Put journal records into the cache, oldest first.  Returns how many went in."""
    # The journal is in order of use as far as anything on disk can know it, so
    # the tail is the part worth having when it holds more than the cache does.
    rec = rec[len(rec) - min(len(rec), max(TERRAIN_POINT_CACHE, 0)):]
    # A record that names no place on Earth is not an elevation.  NaN fails both
    # comparisons, so it goes too.
    rec = rec[(np.abs(rec["lat"]) <= 90) & (np.abs(rec["lon"]) <= 180)]
    for la, lo, v in zip(rec["lat"].tolist(), rec["lon"].tolist(), rec["elev"].tolist()):
        _remember(_elev_cache, (la, lo), v, TERRAIN_POINT_CACHE)
    return len(rec)


def _import_legacy_points(path: Path) -> int:
    """Carry the old points.json over.  Returns how many entries it held.

    json.loads on that file is part of what is being removed: at 770,770 entries
    the load peaked 69 MB above the 196 MB it went on to keep.  The entries are
    matched one at a time instead and passed through the same bound as
    everything else, so the file's own text is all that is held beyond the
    cache.  It was written in fetch order, so what survives the bound is the
    most recently fetched TERRAIN_POINT_CACHE.
    """
    text = path.read_text(encoding="utf-8")
    seen = 0
    for m in _LEGACY_ENTRY.finditer(text):
        try:
            key = (float(m[1]), float(m[2]))
        except ValueError:
            continue
        _remember(_elev_cache, key, int(m[3]), TERRAIN_POINT_CACHE)
        seen += 1
    return seen


def load_caches() -> None:
    """Restore the point-elevation cache from disk. Called once from the lifespan."""
    # Broad excepts throughout, as before: this runs in the lifespan, and a
    # cache that cannot be read must cost a cold start, never the start itself.
    global _journal_records, _journal_clean
    path, legacy = _journal_path(), _legacy_points_path()
    if path.exists():
        try:
            size = path.stat().st_size
            whole = size // _POINT_REC.itemsize
            kept = _restore_points(np.fromfile(path, dtype=_POINT_REC, count=whole))
        except Exception as exc:
            # Whatever is there cannot be extended safely; the first flush
            # replaces it with what this process has learned by then.
            _journal_clean = False
            log.warning(f"[terrain] point journal unreadable ({exc}) — starting empty")
            return
        _journal_records = whole
        _journal_clean = size == whole * _POINT_REC.itemsize
        log.info(f"[terrain] restored {kept:,} of {whole:,} point elevations from disk"
                 + ("" if _journal_clean else " (last record torn, ignored)"))
    elif legacy.exists():
        try:
            seen = _import_legacy_points(legacy)
        except Exception as exc:
            log.warning(f"[terrain] old point cache unreadable ({exc}) — starting empty")
            return
        log.info(f"[terrain] imported {len(_elev_cache):,} of {seen:,} point elevations "
                 f"from {legacy.name}")
        # Write the journal now, so the import happens once.  The old file is
        # left where it is: the previous image reads it, and a rollback should
        # find its cache.  Nothing reads it once points.bin exists.
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _rewrite_journal()
        except Exception as exc:
            log.warning(f"[terrain] could not write the point journal ({exc}) — "
                        f"the import will repeat at the next start")


def _rewrite_journal() -> None:
    """Replace the journal with exactly what is cached, least recently used first."""
    global _journal_records, _journal_clean
    n = len(_elev_cache)
    rec = np.empty(n, dtype=_POINT_REC)
    if n:
        coords = np.fromiter((c for key in _elev_cache for c in key),
                             dtype="<f8", count=2 * n)
        rec["lat"], rec["lon"] = coords[0::2], coords[1::2]
        rec["elev"] = np.fromiter(map(_i4, _elev_cache.values()), dtype="<i4", count=n)
    path = _journal_path()
    tmp = path.with_suffix(".bin.tmp")
    tmp.write_bytes(rec.tobytes())
    tmp.replace(path)        # atomic — a kill mid-write leaves the old journal whole
    _journal_records, _journal_clean = n, True


def flush_point_cache(force: bool = False) -> None:
    """Append the points fetched since the last flush to the journal.

    Batched so a seed run is not writing on every fetch.  The cost is that of the
    new points alone, except when the journal has outgrown the cache or may end
    mid-record; then it is replaced by the cache as it stands — at most
    TERRAIN_POINT_CACHE records, a few megabytes, about once every two days at
    the rate the training node meets new ground.
    """
    global _journal_records, _journal_clean
    if not _point_unsaved or (not force and len(_point_unsaved) < _POINT_FLUSH_EVERY):
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        outgrown = (_journal_records + len(_point_unsaved)
                    > _JOURNAL_SLACK * max(TERRAIN_POINT_CACHE, 0))
        if outgrown or not _journal_clean:
            _rewrite_journal()
        else:
            _journal_clean = False      # until the append is known to be whole
            with open(_journal_path(), "ab") as fh:
                fh.write(np.array(_point_unsaved, dtype=_POINT_REC).tobytes())
            _journal_records += len(_point_unsaved)
            _journal_clean = True
        _point_unsaved.clear()
    except OSError as exc:
        # The points are still cached and still owed to disk; the next flush
        # retries.  Bounded all the same, or a full disk turns this list into
        # the leak the cache used to be.
        del _point_unsaved[: max(0, len(_point_unsaved) - max(TERRAIN_POINT_CACHE, 0))]
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
    await dem_client.aclose()


# Serialise all outbound requests: opentopodata.org free tier = 1 req/s
_api_lock: asyncio.Semaphore | None = None

def _get_lock() -> asyncio.Semaphore:
    global _api_lock
    if _api_lock is None:
        _api_lock = asyncio.Semaphore(1)
    return _api_lock


# Real upstream requests made today (UTC), cache hits excluded.  OpenTopoData
# allows 1,000 a day, and one 10,000-beacon /seed spent 2,197 of them, so the
# number worth watching is this one, not the cache size.  In memory only: it
# resets on restart, which undercounts a day with a restart in it — acceptable
# for a gauge, not for enforcement.
_usage = {"day": "", "requests": 0, "rate_limited": 0}
DAILY_QUOTA = 1000


def _count(status: int) -> None:
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    if _usage["day"] != today:
        _usage.update(day=today, requests=0, rate_limited=0)
    _usage["requests"] += 1
    if status == 429:
        _usage["rate_limited"] += 1


def api_usage() -> dict:
    """Today's upstream request count against the published daily quota."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    if _usage["day"] != today:
        return {"day": today, "requests": 0, "rate_limited": 0, "quota": DAILY_QUOTA}
    return {**_usage, "quota": DAILY_QUOTA}


def source_usage() -> dict:
    """Which source serves terrain, with today's work for each.

    `cached` is what this process holds against its bounds.  Nothing reported
    these while they had no bound, and the growth was found from kernel OOM
    records instead of from here.  `dem.raw_hits` counts block reads since start
    that the compressed tier answered — each one a download the old 8-block
    cache would have made again.
    """
    return {"source": TERRAIN_SOURCE, "api": api_usage(),
            "copernicus": dem_client.usage() if TERRAIN_SOURCE == "copernicus" else None,
            "cached": {"grids": len(_grid_cache), "grids_max": TERRAIN_GRID_CACHE,
                       "points": len(_elev_cache), "points_max": TERRAIN_POINT_CACHE,
                       "points_unsaved": len(_point_unsaved),
                       "hires_grids": len(_hires_cache), "hires_grids_max": TERRAIN_HIRES_CACHE,
                       "dem": dem_client.cached()}}


async def _get_with_retry(client: httpx.AsyncClient, url: str, max_retries: int = 3) -> httpx.Response:
    """GET with fixed back-off on 429 (max 3 retries, 2 s each = 6 s total)."""
    for attempt in range(max_retries):
        async with _get_lock():
            r = await client.get(url)
        _count(r.status_code)
        if r.status_code != 429:
            return r
        log.warning(f"[terrain] 429 rate-limited — retrying in 2s (attempt {attempt + 1}/{max_retries})")
        await asyncio.sleep(2.0)
    return r  # caller calls raise_for_status(); falls back to flat-zero on failure


async def fetch_elevation_point(lat: float, lon: float) -> int | None:
    """Return ground elevation in metres at a single point, or None on failure."""
    if TERRAIN_SOURCE == "copernicus":
        v = (await dem_client.fetch_elevations([(lat, lon)])).get((lat, lon))
        if v is not None:
            return int(round(v))
        # Unpublished tile or a read failure: the API still covers it.
    try:
        r = await _get_with_retry(_get_client(), f"{SRTM_BASE}?locations={lat:.5f},{lon:.5f}")
        r.raise_for_status()
        elev = r.json()["results"][0]["elevation"]
        return int(elev) if elev is not None else 0
    except Exception as exc:
        log.warning(f"[terrain] point elevation failed ({exc})")
        return None


def cached_elevation(lat: float, lon: float, dp: int = DEFAULT_POINT_DP) -> int | None:
    """Ground elevation from the warm cache only — never touches the network.

    For hot paths that cannot afford a rate-limited fetch (the 2 s /ws/live
    frame).  A miss returns None rather than blocking; the caller is expected to
    warm the cache out of band and pick the value up on a later pass.  The key is
    the same rounded cell fetch_elevation_batch caches under, so points a grid
    fetch already resolved are hits here too.

    `dp` must match the `dp` the point was fetched under — see
    fetch_elevation_batch for why the two are one and the same number.
    """
    return _recall(_elev_cache, (round(lat, dp), round(lon, dp)))


MAX_BATCH_POINTS = 100      # opentopodata.org caps a single request at 100 locations


async def fetch_elevation_batch(
    latlon_pairs: list[tuple[float, float]],
    max_requests: int = 2,
    dp: int = DEFAULT_POINT_DP,
) -> dict[tuple, int | None]:
    """
    Elevation for multiple points, split into MAX_BATCH_POINTS-sized requests
    (1 req/s limit).  Results cached by (lat, lon) rounded to `dp`, and the
    uncached points are deduplicated on that same key before fetching — a
    single request therefore covers up to 100 distinct cells.

    `dp` is a resolution knob, not just a cache-key detail: the request goes out
    for the *rounded* coordinate, so at the 2 dp default the elevation returned
    for a point can be the ground up to ~700 m away.  Raise it when the answer
    has to describe the ground under the point itself.

    At most `max_requests` chunks are fetched per call so a large input can't
    blow past the caller's timeout; points beyond that budget come back None
    and are resolved by later calls as the cache warms.

    Returns {(lat, lon): elevation_m | None} for every input pair.
    """
    out: dict[tuple, int | None] = {}
    pending: dict[tuple[float, float], list[tuple[float, float]]] = {}

    for lat, lon in latlon_pairs:
        key = (round(lat, dp), round(lon, dp))
        hit = _recall(_elev_cache, key)
        if hit is not None:             # not truthiness: 0 m is sea level
            out[(lat, lon)] = hit
        else:
            pending.setdefault(key, []).append((lat, lon))

    keys      = list(pending)

    # Copernicus first: no quota, so no budget.  Whatever it cannot answer —
    # typically a tile that is not published — drops through to the API below,
    # which keeps its request budget for exactly those points.
    if TERRAIN_SOURCE == "copernicus" and keys:
        try:
            got = await dem_client.fetch_elevations(keys)
        except Exception as exc:
            log.warning(f"[terrain] copernicus lookup failed ({exc}) — using the API")
            got = {}
        for key in keys:
            v = got.get(key)
            if v is None:
                continue
            elev = int(round(v))
            _cache_point(key, elev)
            for pair in pending[key]:
                out[pair] = elev
        keys = [k for k in keys if got.get(k) is None]
        flush_point_cache()

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
            _cache_point(key, elev)
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


def coarse_lattice(fine: np.ndarray) -> np.ndarray:
    """
    The measured samples a fine grid from fetch_elevation_grid was built on.

    Everything between them is interpolation: 121 elevations go in, 40,401 come
    out.  The upsample runs endpoint to endpoint, so coarse[i] sits at
    fine[i * step] and striding recovers the samples without keeping a second
    cache beside _grid_cache — to within the ~1e-12 m the interpolation's float
    arithmetic leaves behind, which is why callers round rather than compare.

    This is what the map's raw-data view draws.  It must stay the fetched
    values and never a decimation of something already smoothed, or the view
    stops showing what the model was actually given.
    """
    step = round(TERRAIN_RES / GRID_RES)
    return fine[::step, ::step]


async def fetch_elevation_grid(lat: float, lon: float,
                               radius: float = GRID_RADIUS,
                               strict: bool = False) -> np.ndarray | None:
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

    Pass strict=True from anything that *trains* on the result.  Flat zero is a
    tolerable degraded map for a display request, but as a training input it is
    a fabrication with a real label attached: elevation 0 and slope 0 everywhere,
    alt_agl equal to the aircraft's full AMSL altitude, so the row clears every
    filter and lands in the buffer looking normal.  That happens exactly when the
    1,000/day OpenTopoData quota runs out — which a training node reaches within
    hours of a cold start — so strict callers get None and skip the sample, the
    same contract fetch_meteo_historical(strict=True) already has.
    """
    clat, clon = snap_grid_centre(lat, lon)
    key = (clat, clon, radius)
    cached = _recall(_grid_cache, key)
    if cached is not None:
        return cached

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
            _remember(_grid_cache, key, fine, TERRAIN_GRID_CACHE)
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
        _remember(_grid_cache, key, fine, TERRAIN_GRID_CACHE)
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            np.save(path, coarse)
        except OSError as exc:
            log.warning(f"[terrain] could not persist grid {path.name} ({exc})")
        return fine
    except Exception as exc:
        if strict:
            log.warning(f"[terrain] elevation fetch failed ({exc}) — strict, no fallback")
            return None
        log.warning(f"[terrain] elevation fetch failed ({exc}) — using flat-zero fallback")
        return np.zeros(fine_shape)


_HIRES_CHUNK = 2048   # cells per read: ~200k sample points, ~6 MB of coordinates


async def hires_at_points(lats, lons) -> np.ndarray:
    """
    The eight full-resolution terrain columns at arbitrary cells, (n, 8) float32.

    Used for whole grids (fetch_hires_grid) and for buffer rows collected before
    the columns existed.  Read from Copernicus in chunks so the coordinate arrays
    stay small; raises if a read fails, NaN only where no tile is published.
    Without Copernicus there is nothing at this resolution to read, so every
    value is NaN — "unknown", which the model handles, never a stand-in number.
    """
    from pipeline import hires_terrain as ht
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    out = np.full((len(lats), len(ht.COLUMNS)), np.nan, dtype=np.float32)
    if TERRAIN_SOURCE != "copernicus" or not len(lats):
        return out
    # By 0.25 deg area, so each window is a few blocks however spread the
    # points are (buffer rows come from everywhere; a grid is one area).
    area = np.floor(lats * 4).astype(np.int64) * 10_000 + np.floor(lons * 4).astype(np.int64)
    reach = (max(ht.RINGS_M) + 4 * ht.STEP_M) / 111_320.0          # widest ring, plus a margin
    for key in np.unique(area):
        sel = np.flatnonzero(area == key)
        la, lo = lats[sel], lons[sel]
        reach_lon = reach / max(math.cos(math.radians(float(np.abs(la).max()) + reach)), 1e-3)
        window = await dem_client.load_window(la.min() - reach, la.max() + reach,
                                              lo.min() - reach_lon, lo.max() + reach_lon)
        out[sel] = await asyncio.to_thread(_hires_from_window, window, la, lo)
        dem_client._tick(points=len(sel) * ht.N_POINTS)
    return out


def _hires_from_window(window: dict, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """The CPU half of hires_at_points, in chunks so coordinates stay ~6 MB.  Thread-safe."""
    from pipeline import hires_terrain as ht
    out = np.empty((len(lats), len(ht.COLUMNS)), dtype=np.float32)
    for s in range(0, len(lats), _HIRES_CHUNK):
        plat, plon = ht.sample_points(lats[s:s + _HIRES_CHUNK], lons[s:s + _HIRES_CHUNK])
        out[s:s + len(plat)] = ht.features(dem_client.sample_window(window, plat, plon))
    return out


async def fetch_hires_grid(lat: float, lon: float, radius: float = GRID_RADIUS,
                           strict: bool = False,
                           shape: tuple[int, int] | None = None) -> np.ndarray | None:
    """
    Full-resolution terrain columns for every cell of the grid at (lat, lon).

    (rows * cols, 8) float32 in build_feature_matrix's cell order, for the same
    snapped centre fetch_elevation_grid uses — the two must describe one patch
    of ground.  Pass the elevation grid's own shape when it is in hand; the
    default is the shape fetch_elevation_grid returns.  strict follows
    fetch_elevation_grid: a failed read returns None to anything that trains (a
    grid with holes punched by a network error is not the ground), and an
    all-NaN grid to a display request, which the model reads as unknown terrain
    rather than as a fabricated one.
    """
    from pipeline import hires_terrain as ht
    shape = tuple(shape) if shape is not None else grid_shape(radius)
    clat, clon = snap_grid_centre(lat, lon)
    key = (clat, clon, radius, shape)
    cached = _recall(_hires_cache, key)
    if cached is not None:
        return cached
    try:
        out = await hires_at_points(*ht.grid_cells(clat, clon, radius, shape))
    except Exception as exc:
        if strict:
            log.warning(f"[terrain] full-resolution read failed ({exc}) — strict, no fallback")
            return None
        log.warning(f"[terrain] full-resolution read failed ({exc}) — columns left unknown")
        return np.full((shape[0] * shape[1], len(ht.COLUMNS)), np.nan, dtype=np.float32)
    _remember(_hires_cache, key, out, TERRAIN_HIRES_CACHE)
    return out
