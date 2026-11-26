"""
Ground elevation from Copernicus GLO-30, read straight from public S3.

Replaces OpenTopoData as the primary terrain source.  That API allows 1,000
requests a day and a training node spent them in its first 40 minutes, after
which sampling stopped for the rest of the day.  Copernicus publishes the same
kind of 30 m DEM as 1x1 degree Cloud-Optimized GeoTIFFs: no key, no quota,
commercial use allowed with attribution — and copyable onto a closed network,
which an API is not.

Each tile is ~40 MB but is never downloaded whole.  A COG is split into
1024x1024 compressed blocks with their byte offsets in the header, so a lookup
costs one header read per tile (cached) and one range request per block it
touches.  Blocks are kept in two tiers: a small LRU of decoded arrays (4 MB
each) and behind it the compressed bodies as served (~2.2 MB), bounded by
DEM_RAW_CACHE_MB, which turn a repeat visit into an ~18 ms decode instead of a
download.  A 0.1 degree prediction grid at 0.01 degree spacing usually lands
in one or two blocks.

Full resolution is read on purpose, not an overview.  Overview pixels are
averages, which would smooth slopes relative to the SRTM point samples the
model was measured against; at full resolution the two agree to 4.4 m of
elevation and 0.26° of slope.

A point this module cannot answer — tile not published (open ocean, and a few
withheld areas), or any read failure — comes back None.  The caller decides what
that means; terrain_client falls back to OpenTopoData for just those points.
"""

import asyncio
import logging
import math
from collections import OrderedDict
from datetime import datetime, timezone

import httpx
import numpy as np

from config import COPERNICUS_DEM_BASE, DEM_RAW_CACHE_MB
from data import cog

log = logging.getLogger(__name__)

_HEADER_BYTES = 65536        # all four IFDs plus their offset arrays fit in ~2 KB
_BLOCK_CACHE_SIZE = 8        # decoded blocks are 4 MB each — the arrays one grid is working on


class _ByteLRU:
    """
    Compressed block bodies, least recently used out first, bounded by bytes.

    The second tier behind the decoded LRU (see DEM_RAW_CACHE_MB).  Bytes, not
    a count, because a block's size swings with the ground: 0.7 to 2.8 MB
    (5th to 95th percentile of 2,592 blocks read for the evaluation runs),
    median 2.3 MB — flat ground compresses, mountains do not.
    """

    def __init__(self, limit_bytes: int):
        self.limit = limit_bytes
        self.size = 0
        self.hits = 0
        self._d: "OrderedDict[tuple[str, int], bytes]" = OrderedDict()

    def __len__(self) -> int:
        return len(self._d)

    def get(self, key):
        body = self._d.get(key)
        if body is not None:
            self._d.move_to_end(key)
            self.hits += 1
        return body

    def put(self, key, body: bytes) -> None:
        if len(body) > self.limit:
            return
        old = self._d.pop(key, None)
        if old is not None:
            self.size -= len(old)
        self._d[key] = body
        self.size += len(body)
        while self.size > self.limit:
            _, dropped = self._d.popitem(last=False)
            self.size -= len(dropped)


_headers: dict[str, dict | None] = {}
_blocks: "OrderedDict[tuple[str, int], np.ndarray]" = OrderedDict()
_raw = _ByteLRU(DEM_RAW_CACHE_MB * 1024 * 1024)
_lock: asyncio.Lock | None = None
_client: httpx.AsyncClient | None = None

# Today's work, for /training/status.  Not a quota — there is none — but the
# number that shows the source is actually carrying the load.
_usage = {"day": "", "points": 0, "blocks": 0, "bytes": 0, "missing": 0, "errors": 0}


def cached() -> dict:
    """What the two block tiers hold now, for /training/status."""
    return {"blocks_decoded": len(_blocks), "blocks_decoded_max": _BLOCK_CACHE_SIZE,
            "blocks_raw": len(_raw), "raw_mb": round(_raw.size / 2**20, 1),
            "raw_mb_max": round(_raw.limit / 2**20), "raw_hits": _raw.hits}


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _tick(**inc) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    if _usage["day"] != today:
        _usage.update(day=today, points=0, blocks=0, bytes=0, missing=0, errors=0)
    for k, v in inc.items():
        _usage[k] += v


def usage() -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    if _usage["day"] != today:
        return {"day": today, "points": 0, "blocks": 0, "bytes": 0, "missing": 0, "errors": 0}
    return dict(_usage)


def tile_origin(lat: float, lon: float) -> tuple[int, int]:
    """
    (latitude, longitude) naming the tile that contains a point.

    Tiles are named by their south-west corner and hold rows from the north
    edge down.  A latitude exactly on an integer degree is the *top row* of the
    tile below it, not beyond the bottom of the tile floor() would pick — so the
    latitude is ceil - 1.  This is the edge case that silently returned 0 m for
    a whole row of an 11x11 lattice when first measured.
    """
    return math.ceil(lat) - 1, math.floor(lon)


def tile_name(tile_lat: int, tile_lon: int) -> str:
    ns = "N" if tile_lat >= 0 else "S"
    ew = "E" if tile_lon >= 0 else "W"
    return (f"Copernicus_DSM_COG_10_{ns}{abs(tile_lat):02d}_00_"
            f"{ew}{abs(tile_lon):03d}_00_DEM")


def tile_url(name: str) -> str:
    return f"{COPERNICUS_DEM_BASE}/{name}/{name}.tif"


def pixel_index(meta: dict, lat: float, lon: float) -> tuple[int, int]:
    """
    (row, col) of the pixel holding a point, clamped into the tile.

    The x and y pixel sizes are read separately on purpose: Copernicus narrows
    the column count above 50° (2400 wide, then fewer), so assuming square
    pixels would sample the wrong ground across most of Germany and all of
    Scandinavia.
    """
    lon0, lat0, dx, dy = meta["lon0"], meta["lat0"], meta["dx"], meta["dy"]
    fx, fy = (lon - lon0) / dx, (lat0 - lat) / dy
    if meta["point"]:
        col, row = round(fx), round(fy)
    else:
        col, row = math.floor(fx), math.floor(fy)
    return (min(max(row, 0), meta["height"] - 1),
            min(max(col, 0), meta["width"] - 1))


async def _header(name: str) -> dict | None:
    if name in _headers:
        return _headers[name]
    r = await _get_client().get(tile_url(name), headers={"Range": f"bytes=0-{_HEADER_BYTES - 1}"})
    if r.status_code in (403, 404):
        # S3 answers 403 for a key that does not exist when listing is not public.
        log.info(f"[dem] no tile {name}")
        _headers[name] = None
        return None
    r.raise_for_status()
    levels = cog.parse_ifds(r.content)
    lv = levels[0]
    if lv.get("compression", (0,))[0] != 8:
        raise ValueError(f"{name}: unexpected compression {lv.get('compression')}")
    meta = {
        "width": lv["width"][0], "height": lv["height"][0],
        "tile_w": lv["tile_w"][0], "tile_h": lv["tile_h"][0],
        "bits": lv["bits"][0],
        "sample_format": lv.get("sample_format", (1,))[0],
        "predictor": lv.get("predictor", (1,))[0],
        "offsets": lv["offsets"], "counts": lv["counts"],
        "lon0": lv["tiepoint"][3], "lat0": lv["tiepoint"][4],
        "dx": lv["scale"][0], "dy": lv["scale"][1],
        "point": cog.pixel_is_point(levels),
    }
    _headers[name] = meta
    return meta


async def _block(name: str, meta: dict, idx: int) -> np.ndarray:
    key = (name, idx)
    if key in _blocks:
        _blocks.move_to_end(key)
        return _blocks[key]
    body = _raw.get(key)
    fetched = body is None
    if fetched:
        start, length = meta["offsets"][idx], meta["counts"][idx]
        r = await _get_client().get(tile_url(name),
                                    headers={"Range": f"bytes={start}-{start + length - 1}"})
        r.raise_for_status()
        body = r.content
    arr = cog.decode_block(body, meta["tile_w"], meta["tile_h"], meta["bits"],
                           meta["sample_format"], meta["predictor"])
    if fetched:
        # Kept only once it decodes: a truncated body cached here would fail
        # every later read of the block instead of being fetched again.
        _tick(blocks=1, bytes=len(body))
        _raw.put(key, body)
    _blocks[key] = arr
    while len(_blocks) > _BLOCK_CACHE_SIZE:
        _blocks.popitem(last=False)
    return arr


def _pixels(meta: dict, lat: np.ndarray, lon: np.ndarray):
    """pixel_index over arrays: same rounding, same clamping."""
    fx = (lon - meta["lon0"]) / meta["dx"]
    fy = (meta["lat0"] - lat) / meta["dy"]
    if meta["point"]:
        col, row = np.round(fx), np.round(fy)
    else:
        col, row = np.floor(fx), np.floor(fy)
    return (np.clip(row, 0, meta["height"] - 1).astype(np.int64),
            np.clip(col, 0, meta["width"] - 1).astype(np.int64))


async def load_window(south: float, north: float, west: float, east: float) -> dict:
    """
    Every block covering a box, read through the usual cache: {tile name: (meta, {block: array})}.

    The network half of an array read.  The other half, sample_window, is pure
    numpy over what this returns, so callers can run it in a worker thread —
    a full-resolution grid is ~4M sample points, and that much arithmetic on
    the event loop stalls every live frame behind it.  meta is None for a tile
    that is not published.  A read that fails raises.
    """
    window: dict = {}
    async with _get_lock():
        for tl in range(math.ceil(south) - 1, math.ceil(north)):
            for tn in range(math.floor(west), math.floor(east) + 1):
                name = tile_name(tl, tn)
                meta = await _header(name)
                if meta is None:
                    window[name] = (None, {})
                    continue
                (r0, r1), (c0, c1) = zip(*(_pixels(meta, np.array([la]), np.array([lo]))
                                           for la, lo in ((north, west), (south, east))))
                tw, th = meta["tile_w"], meta["tile_h"]
                across = -(-meta["width"] // tw)
                blocks = {}
                for br in range(int(r0[0]) // th, int(r1[0]) // th + 1):
                    for bc in range(int(c0[0]) // tw, int(c1[0]) // tw + 1):
                        idx = br * across + bc
                        blocks[idx] = await _block(name, meta, idx)
                window[name] = (meta, blocks)
    return window


def sample_window(window: dict, lats, lons) -> np.ndarray:
    """
    Elevation at every (lat, lon) from a load_window result; NaN where unknown.

    No awaits and no shared state, so it is safe in a worker thread.  Same tile
    naming and pixel rule as fetch_elevations — a test pins the two together.
    Tiles are told apart by one integer each, not np.unique over (lat, lon)
    pairs: that sorted every point and cost ~4 s of a 4.5 s grid.
    """
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    flat_lat, flat_lon = lats.ravel(), lons.ravel()
    out = np.full(flat_lat.shape, np.nan, dtype=np.float32)
    if not len(flat_lat):
        return out.reshape(lats.shape)
    tile_key = (np.ceil(flat_lat).astype(np.int64) - 1) * 1000 + np.floor(flat_lon).astype(np.int64)
    one_tile = bool((tile_key == tile_key[0]).all())
    for key in ([tile_key[0]] if one_tile else np.unique(tile_key)):
        sel = np.arange(len(tile_key)) if one_tile else np.flatnonzero(tile_key == key)
        name = tile_name(int(np.ceil(flat_lat[sel[0]])) - 1, int(np.floor(flat_lon[sel[0]])))
        meta, blocks = window.get(name, (None, {}))
        if meta is None:
            continue
        row, col = _pixels(meta, flat_lat[sel], flat_lon[sel])
        tw, th = meta["tile_w"], meta["tile_h"]
        blk = (row // th) * (-(-meta["width"] // tw)) + (col // tw)
        present = np.unique(blk)
        for idx in present:
            if int(idx) not in blocks:
                raise KeyError(f"block {idx} of {name} is outside the loaded window")
            k = slice(None) if len(present) == 1 else blk == idx
            v = blocks[int(idx)][row[k] % th, col[k] % tw].astype(np.float32)
            v[~(np.isfinite(v) & (v > -1000))] = np.nan
            out[sel[k]] = v
    return out.reshape(lats.shape)


async def sample_elevations(lats, lons) -> np.ndarray:
    """
    Elevation at every (lat, lon) in two same-shaped arrays; NaN where unknown.

    load_window over the points' own box, then sample_window — on the event
    loop, so for small reads.  The full-resolution columns call the two halves
    separately and run the second in a thread.  An unpublished tile is NaN, as
    it is None in fetch_elevations; a read that fails raises.
    """
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)
    if lats.size == 0:
        return np.full(lats.shape, np.nan, dtype=np.float32)
    window = await load_window(lats.min(), lats.max(), lons.min(), lons.max())
    out = sample_window(window, lats, lons)
    _tick(points=int(np.isfinite(out).sum()), missing=int(np.isnan(out).sum()))
    return out


async def fetch_elevations(points: list[tuple[float, float]]) -> dict[tuple[float, float], float | None]:
    """
    Elevation in metres for each (lat, lon), or None where it cannot be read.

    Points are grouped by tile and block so each block is fetched once however
    many points fall in it.  Serialised behind one lock: concurrent callers for
    the same area would otherwise each download the same blocks.
    """
    out: dict[tuple[float, float], float | None] = {}
    by_tile: dict[str, list[tuple[float, float]]] = {}
    for la, lo in points:
        by_tile.setdefault(tile_name(*tile_origin(la, lo)), []).append((la, lo))

    async with _get_lock():
        for name, pts in by_tile.items():
            try:
                meta = await _header(name)
            except Exception as exc:
                log.warning(f"[dem] header read failed for {name} ({exc})")
                _tick(errors=len(pts))
                out.update({p: None for p in pts})
                continue
            if meta is None:
                _tick(missing=len(pts))
                out.update({p: None for p in pts})
                continue

            tw, th = meta["tile_w"], meta["tile_h"]
            across = -(-meta["width"] // tw)
            by_block: dict[int, list[tuple[tuple[float, float], int, int]]] = {}
            for p in pts:
                row, col = pixel_index(meta, *p)
                idx = (row // th) * across + (col // tw)
                by_block.setdefault(idx, []).append((p, row % th, col % tw))

            for idx, items in by_block.items():
                try:
                    block = await _block(name, meta, idx)
                except Exception as exc:
                    log.warning(f"[dem] block {idx} of {name} failed ({exc})")
                    _tick(errors=len(items))
                    out.update({p: None for p, _, _ in items})
                    continue
                for p, r_, c_ in items:
                    v = float(block[r_, c_])
                    out[p] = v if math.isfinite(v) and v > -1000 else None
                _tick(points=len(items))
    return out
