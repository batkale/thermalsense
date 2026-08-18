"""
Per-cell land cover from ESA WorldCover 10 m.

`land_use` was a single string for an entire grid, mapped through a table of
placeholder constants, so columns 13 and 14 of every feature row held the same
two numbers — 0.4 and 0.2 across all 5,149 buffer samples.  Two of the model's
twenty-two inputs were carrying no information at all.  Ground cover is one of
the few things that genuinely varies at the scale a thermal forms on: a
ploughed field, a lake and a pine forest under identical weather do completely
different things to the air above them.

WorldCover is distributed as Cloud-Optimized GeoTIFFs on public S3, which is
what makes this affordable on a 1 GiB box.  A COG carries internal overviews,
so instead of the 73 MB full-resolution 3-degree tile we read one overview
level at roughly the resolution the prediction grid needs and fetch only the
1024x1024 blocks the requested window touches — typically one or two HTTP range
requests of a few hundred KB, then cached to disk exactly like terrain.

The reader here is deliberately narrow: classic little-endian TIFF, tiled,
DEFLATE, 8-bit single band, which is what every WorldCover tile is.  Anything
else raises and the caller falls back to the neutral default rather than
guessing at a format it does not understand.  GDAL/rasterio would handle the
general case but costs more than this whole application.
"""

import asyncio
import logging
import math
import struct
import zlib

import httpx
import numpy as np

from config import GRID_RADIUS, TERRAIN_RES, DATA_DIR, WORLDCOVER_BASE

log = logging.getLogger(__name__)

# WorldCover class -> (heat, albedo), continuing the scale the placeholder table
# established: heat is relative sensible-heat yield, albedo is reflectance.
# Bare ground and built-up land drive the strongest thermals; water and snow
# produce none, for opposite reasons.
_CLASS_PROPS: dict[int, tuple[float, float]] = {
    10:  (0.20, 0.12),   # tree cover
    20:  (0.55, 0.20),   # shrubland
    30:  (0.45, 0.22),   # grassland
    40:  (0.50, 0.25),   # cropland
    50:  (1.00, 0.15),   # built-up
    60:  (0.90, 0.20),   # bare / sparse vegetation
    70:  (0.00, 0.80),   # snow and ice
    80:  (0.00, 0.06),   # permanent water
    90:  (0.10, 0.10),   # herbaceous wetland
    95:  (0.10, 0.10),   # mangroves
    100: (0.30, 0.18),   # moss and lichen
}
# Matches _LAND_USE["default"] in feature_engineering, so an unmapped or
# missing class lands exactly where the old constant did.
DEFAULT_PROPS = (0.40, 0.20)

_HEAT_LUT = np.full(256, DEFAULT_PROPS[0], dtype=float)
_ALBEDO_LUT = np.full(256, DEFAULT_PROPS[1], dtype=float)
for _cls, (_h, _a) in _CLASS_PROPS.items():
    _HEAT_LUT[_cls] = _h
    _ALBEDO_LUT[_cls] = _a

_TILE_DEG = 3           # WorldCover tiles are 3x3 degrees, named by SW corner
_HEADER_BYTES = 131072  # covers every IFD and tile-offset array (largest seen: ~28 KB)

_CACHE_DIR = DATA_DIR / "cache" / "landcover"

# Parsed COG directories, keyed by tile name. Small (a few KB) and reused by
# every grid inside the same 3-degree tile.
_cog_cache: dict[str, list[dict] | None] = {}
# Extracted class grids, keyed like the terrain grid cache.
_grid_cache: dict[tuple[float, float, float], np.ndarray] = {}

_client: httpx.AsyncClient | None = None
_TAG_NAMES = {254: "subfile", 256: "width", 257: "height", 258: "bits",
              259: "compression", 317: "predictor", 322: "tile_w", 323: "tile_h",
              324: "offsets", 325: "counts", 33550: "scale", 33922: "tiepoint"}


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=8),
        )
    return _client


async def aclose() -> None:
    """Release pooled connections. Called from the app lifespan on shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def tile_name(lat: float, lon: float) -> str:
    """WorldCover tile covering a point, named by its south-west corner."""
    lat_sw = math.floor(lat / _TILE_DEG) * _TILE_DEG
    lon_sw = math.floor(lon / _TILE_DEG) * _TILE_DEG
    ns = "N" if lat_sw >= 0 else "S"
    ew = "E" if lon_sw >= 0 else "W"
    return f"{ns}{abs(lat_sw):02d}{ew}{abs(lon_sw):03d}"


def tile_url(name: str) -> str:
    return f"{WORLDCOVER_BASE}/ESA_WorldCover_10m_2021_v200_{name}_Map.tif"


def _parse_ifds(buf: bytes) -> list[dict]:
    """
    Parse the IFD chain of a classic little-endian TIFF into level descriptors.

    Level 0 is full resolution; the rest are the COG's internal overviews, each
    roughly half the previous. Only the tags this reader needs are decoded.
    """
    if buf[:2] != b"II":
        raise ValueError("not a little-endian TIFF")
    if struct.unpack("<H", buf[2:4])[0] != 42:
        raise ValueError("BigTIFF is not supported by this reader")

    fmt_of = {1: "B", 3: "H", 4: "I", 12: "d"}
    levels: list[dict] = []
    off = struct.unpack("<I", buf[4:8])[0]

    while off and off + 2 <= len(buf):
        count = struct.unpack("<H", buf[off:off + 2])[0]
        if count == 0 or count > 200:
            break
        entry = {}
        for i in range(count):
            e = off + 2 + i * 12
            tag, typ, n = struct.unpack("<HHI", buf[e:e + 8])
            name = _TAG_NAMES.get(tag)
            if name is None:
                continue
            fmt = fmt_of.get(typ)
            if fmt is None:
                continue
            item = struct.calcsize(fmt)
            if item * n <= 4:
                entry[name] = struct.unpack(f"<{n}{fmt}", buf[e + 8:e + 8 + item * n])
            else:
                ptr = struct.unpack("<I", buf[e + 8:e + 12])[0]
                end = ptr + item * n
                if end > len(buf):
                    raise ValueError(f"tag {tag} array at {ptr}..{end} beyond header")
                entry[name] = struct.unpack(f"<{n}{fmt}", buf[ptr:end])
        levels.append(entry)
        off = struct.unpack("<I", buf[off + 2 + count * 12: off + 6 + count * 12])[0]

    if not levels:
        raise ValueError("no IFDs found")
    return levels


def _geo(levels: list[dict], level: int) -> tuple[float, float, float]:
    """(lon of west edge, lat of north edge, degrees per pixel) for a level."""
    scale = levels[0]["scale"][0]
    tp = levels[0]["tiepoint"]
    lon0, lat0 = tp[3], tp[4]
    ratio = levels[0]["width"][0] / levels[level]["width"][0]
    return lon0, lat0, scale * ratio


def _pick_level(levels: list[dict], target_deg: float) -> int:
    """
    Cheapest level still at least as fine as `target_deg` per pixel.

    Levels run fine to coarse, so the last one that clears the bar is the one
    that moves the fewest bytes while meeting it.  Reading full resolution and
    averaging it away instead would transfer ~70x more for the same answer.
    """
    best = 0
    for i in range(len(levels)):
        _, _, dpp = _geo(levels, i)
        if dpp <= target_deg:
            best = i
    return best


async def _fetch_header(name: str) -> list[dict] | None:
    """Parsed directory for a tile, or None when the tile does not exist."""
    if name in _cog_cache:
        return _cog_cache[name]
    try:
        r = await _get_client().get(
            tile_url(name), headers={"Range": f"bytes=0-{_HEADER_BYTES - 1}"}
        )
        if r.status_code in (403, 404):
            # Ocean and unmapped areas simply have no tile published.
            log.info(f"[landcover] no tile {name} — using defaults there")
            _cog_cache[name] = None
            return None
        r.raise_for_status()
        levels = _parse_ifds(r.content)
    except Exception as exc:
        log.warning(f"[landcover] header fetch/parse failed for {name} ({exc})")
        _cog_cache[name] = None
        return None
    _cog_cache[name] = levels
    return levels


async def _read_window(name: str, levels: list[dict], level: int,
                       px0: int, py0: int, px1: int, py1: int) -> np.ndarray | None:
    """
    Decode the pixel window [px0, px1) x [py0, py1) of one overview level.

    Only the 1024x1024 blocks the window intersects are fetched, each as its own
    range request over the already-open connection.
    """
    lv = levels[level]
    width, height = lv["width"][0], lv["height"][0]
    tw, th = lv["tile_w"][0], lv["tile_h"][0]
    if lv.get("compression", (0,))[0] != 8:
        raise ValueError(f"unexpected compression {lv.get('compression')}")
    if lv.get("bits", (8,))[0] != 8:
        raise ValueError(f"unexpected bit depth {lv.get('bits')}")

    px0, py0 = max(0, px0), max(0, py0)
    px1, py1 = min(width, px1), min(height, py1)
    if px1 <= px0 or py1 <= py0:
        return None

    tiles_across = -(-width // tw)
    offsets, counts = lv["offsets"], lv["counts"]
    out = np.full((py1 - py0, px1 - px0), 0, dtype=np.uint8)
    client = _get_client()

    for ty in range(py0 // th, (py1 - 1) // th + 1):
        for tx in range(px0 // tw, (px1 - 1) // tw + 1):
            idx = ty * tiles_across + tx
            if idx >= len(offsets):
                continue
            start, length = offsets[idx], counts[idx]
            if length == 0:
                continue
            r = await client.get(
                tile_url(name),
                headers={"Range": f"bytes={start}-{start + length - 1}"},
            )
            r.raise_for_status()
            block = np.frombuffer(zlib.decompress(r.content), dtype=np.uint8)
            if block.size != tw * th:
                raise ValueError(f"tile {idx} decoded to {block.size}, expected {tw * th}")
            block = block.reshape(th, tw)

            # Intersect this block with the requested window, in image pixels.
            bx0, by0 = tx * tw, ty * th
            ix0, iy0 = max(px0, bx0), max(py0, by0)
            ix1, iy1 = min(px1, bx0 + tw), min(py1, by0 + th)
            out[iy0 - py0:iy1 - py0, ix0 - px0:ix1 - px0] = \
                block[iy0 - by0:iy1 - by0, ix0 - bx0:ix1 - bx0]
    return out


def properties(classes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(heat, albedo) arrays for an array of WorldCover class codes."""
    idx = np.asarray(classes, dtype=np.uint8)
    return _HEAT_LUT[idx], _ALBEDO_LUT[idx]


# Finer pixels averaged into each output cell, per axis.  Taking a single class
# per ~1.1 km cell throws away most of what a 10 m product knows: one cell here
# routinely spans cropland, grassland and trees, and picking whichever class
# landed under the sample point turns a mixed surface into an arbitrary pure
# one.  Averaging heat/albedo over the constituent pixels keeps the mixture,
# which is also what a thermal integrates over.
_SUBSAMPLE = 8


def _grid_path(key: tuple[float, float, float]):
    lat, lon, radius = key
    return _CACHE_DIR / f"lc_{lat:+.2f}_{lon:+.2f}_{radius:.3f}.npy"


def _block_average(values: np.ndarray, py: np.ndarray, px: np.ndarray,
                   shape: tuple[int, int]) -> np.ndarray:
    """Mean of `values` grouped by the output cell each pixel falls in."""
    rows, cols = shape
    flat = (py[:, None] * cols + px[None, :]).ravel()
    total = np.bincount(flat, weights=values.ravel(), minlength=rows * cols)
    count = np.bincount(flat, minlength=rows * cols)
    return (total / np.maximum(count, 1)).reshape(rows, cols)


async def fetch_landcover_props(lat: float, lon: float,
                                radius: float = GRID_RADIUS,
                                shape: tuple[int, int] | None = None
                                ) -> tuple[np.ndarray, np.ndarray]:
    """
    (heat, albedo) grids over the same square fetch_elevation_grid covers.

    The centre is snapped with the terrain lattice so the two layers describe
    exactly the same ground — pairing land cover sampled at the raw point with
    terrain sampled at the snapped one would offset them by up to half a coarse
    cell.

    Returns arrays at `shape` (default: the coarse terrain lattice).  Any
    failure, including the missing tiles that are normal over sea, yields the
    neutral defaults rather than an error, so a caller always gets a usable
    grid.
    """
    from data.terrain_client import snap_grid_centre

    clat, clon = snap_grid_centre(lat, lon)
    if shape is None:
        n = round((2 * radius) / TERRAIN_RES) + 1
        shape = (n, n)
    key = (clat, clon, radius)
    if key in _grid_cache:
        stack = _grid_cache[key]
        return stack[0], stack[1]

    path = _grid_path(key)
    if path.exists():
        try:
            stack = np.load(path)
            if stack.shape == (2, *shape):
                _grid_cache[key] = stack
                return stack[0], stack[1]
        except Exception as exc:
            log.warning(f"[landcover] cached grid {path.name} unreadable ({exc})")

    stack = np.stack([
        np.full(shape, DEFAULT_PROPS[0]),
        np.full(shape, DEFAULT_PROPS[1]),
    ])
    name = tile_name(clat, clon)
    levels = await _fetch_header(name)
    if levels is not None:
        try:
            cell_deg = (2 * radius) / max(shape[0] - 1, 1)
            level = _pick_level(levels, cell_deg / _SUBSAMPLE)
            lon0, lat0, dpp = _geo(levels, level)

            # Pixel window spanning the whole grid, half a cell beyond each edge
            # so edge cells average the same amount of ground as interior ones.
            pad = cell_deg / 2
            px0 = int(np.floor((clon - radius - pad - lon0) / dpp))
            px1 = int(np.ceil((clon + radius + pad - lon0) / dpp)) + 1
            py0 = int(np.floor((lat0 - (clat + radius + pad)) / dpp))
            py1 = int(np.ceil((lat0 - (clat - radius - pad)) / dpp)) + 1

            window = await _read_window(name, levels, level, px0, py0, px1, py1)
            if window is not None:
                heat, albedo = properties(window)
                # Geographic centre of every pixel actually returned, then the
                # output cell it belongs to.  TIFF row 0 is the NORTH edge while
                # build_feature_matrix row 0 is the SOUTH edge, so latitude runs
                # backwards through the window and the mapping must be computed
                # from coordinates rather than assumed.
                h, w = window.shape
                plat = lat0 - (np.arange(py0, py0 + h) + 0.5) * dpp
                plon = lon0 + (np.arange(px0, px0 + w) + 0.5) * dpp
                iy = np.clip(np.round((plat - (clat - radius)) / cell_deg), 0, shape[0] - 1).astype(int)
                ix = np.clip(np.round((plon - (clon - radius)) / cell_deg), 0, shape[1] - 1).astype(int)
                stack = np.stack([
                    _block_average(heat, iy, ix, shape),
                    _block_average(albedo, iy, ix, shape),
                ])
        except Exception as exc:
            log.warning(f"[landcover] read failed for {name} ({exc}) — using defaults")

    _grid_cache[key] = stack
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        np.save(path, stack)
    except OSError as exc:
        log.warning(f"[landcover] could not persist grid {path.name} ({exc})")
    return stack[0], stack[1]


async def fetch_landcover_fine(lat: float, lon: float, fine_shape: tuple[int, int],
                               radius: float = GRID_RADIUS
                               ) -> tuple[np.ndarray, np.ndarray]:
    """
    (heat, albedo) upsampled to the prediction grid, mirroring terrain.

    Deliberately *not* read at the fine grid's own resolution.  A 201x201 grid
    spans ~55 m cells, which would select WorldCover's full-resolution level and
    pull four 1024x1024 blocks — several MB on every prediction — to produce a
    layer the model consumes alongside terrain that was itself interpolated from
    an 11x11 lattice.  Reading the same coarse lattice and interpolating costs
    one cached range request and keeps the two layers on identical footing.

    Interpolating is sound here because block averaging has already turned class
    codes into continuous heat/albedo: a cell that is half forest and half bare
    is genuinely intermediate, whereas interpolating the raw class codes would
    invent classes that do not exist.
    """
    from scipy.ndimage import zoom as ndimage_zoom

    heat, albedo = await fetch_landcover_props(lat, lon, radius=radius)
    if heat.shape == fine_shape:
        return heat, albedo

    factor = fine_shape[0] / heat.shape[0]
    out = []
    for layer in (heat, albedo):
        fine = ndimage_zoom(layer, factor, order=1)
        out.append(fine[: fine_shape[0], : fine_shape[1]])
    return out[0], out[1]


async def fetch_point_properties(lat: float, lon: float) -> tuple[float, float]:
    """(heat, albedo) at a single point, via the cached grid for its area."""
    from data.terrain_client import snap_grid_centre

    heat, albedo = await fetch_landcover_props(lat, lon)
    clat, clon = snap_grid_centre(lat, lon)
    rows, cols = heat.shape
    i = int(round((lat - (clat - GRID_RADIUS)) / (2 * GRID_RADIUS) * (rows - 1)))
    j = int(round((lon - (clon - GRID_RADIUS)) / (2 * GRID_RADIUS) * (cols - 1)))
    i, j = min(max(i, 0), rows - 1), min(max(j, 0), cols - 1)
    return float(heat[i, j]), float(albedo[i, j])
